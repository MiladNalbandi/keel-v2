"""keel add-ons: optional packages that add workflows, agents, code actions, fake-model answers and routes to the engine
without changing it (keel Product is one). Nothing loads unless KEEL_ADDONS names a package.

    KEEL_ADDONS=keel_product            a comma-separated list of importable Python packages

An add-on package has a module-level ADDON dict:

    name      "product"                 its id: its actions are "<name>:*", the events it emits "<name>.*"
    version   "0.1.0-beta.1"
    requires  ">=0.13.0,<0.15.0"        the keel versions it works with; another keel refuses to load it
    content   Path                      a folder with workflows/ and agents/ (both optional)
    actions   {"product:save-doc": fn}  code actions: fn(ActionInput) -> ActionResult, plain or async
    fake      fn(AgentRequest)          the fake model's answer for its agents: (path, content, answer, data) or None
    router    fastapi.APIRouter         engine routes, mounted as they are

An add-on that fails to import, or needs another keel, is left out and listed under problems (GET /addons).
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import config

log = logging.getLogger("keel.addons")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


@dataclass
class Addon:
    name: str
    version: str
    package: str
    requires: str = ""
    content: Path | None = None
    actions: dict = field(default_factory=dict)
    fake: object = None
    router: object = None

    def folder(self, kind: str) -> Path | None:
        d = self.content / kind if self.content else None
        return d if d and d.is_dir() else None

    def info(self) -> dict:
        return {"name": self.name, "version": self.version, "package": self.package, "requires": self.requires,
                "workflows": sorted(f.stem for f in (self.folder("workflows") or Path("/nonexistent")).glob("*.yaml")),
                "agents": sorted(f.stem for f in (self.folder("agents") or Path("/nonexistent")).glob("*.md")),
                "actions": sorted(self.actions)}


def _numbers(version: str) -> tuple[int, ...]:
    """'0.13.2-beta.1' → (0, 13, 2): the release numbers only."""
    head = re.split(r"[-+]", version.strip(), maxsplit=1)[0]
    return tuple(int(p) for p in head.split(".") if p.isdigit())


def satisfies(version: str, spec: str) -> bool:
    """Whether a keel version meets a spec like '>=0.13.0,<0.15.0' (an empty spec: any version)."""
    have = _numbers(version)
    for part in [p.strip() for p in (spec or "").split(",") if p.strip()]:
        m = re.match(r"^(>=|<=|==|>|<)\s*([0-9][0-9.]*)$", part)
        if not m:
            return False
        op, want = m.group(1), _numbers(m.group(2))
        ok = {">=": have >= want, "<=": have <= want, "==": have == want, ">": have > want, "<": have < want}[op]
        if not ok:
            return False
    return True


_problems: list[dict] = []


def _load_one(package: str) -> Addon | None:
    try:
        mod = importlib.import_module(package)
    except Exception as exc:  # noqa: BLE001 - an add-on that does not import is a problem to show, not a crash
        _problems.append({"package": package, "error": f"does not import: {str(exc)[:300]}"})
        return None
    spec = getattr(mod, "ADDON", None)
    if not isinstance(spec, dict) or not NAME.match(str(spec.get("name") or "")):
        _problems.append({"package": package, "error": "has no ADDON with a valid name"})
        return None
    a = Addon(name=spec["name"], version=str(spec.get("version") or "0"), package=package, requires=str(spec.get("requires") or ""),
              content=Path(spec["content"]) if spec.get("content") else None, actions=dict(spec.get("actions") or {}),
              fake=spec.get("fake"), router=spec.get("router"))
    if not satisfies(config.VERSION, a.requires):
        _problems.append({"package": package, "name": a.name, "error": f"needs keel {a.requires}, this is keel {config.VERSION}"})
        return None
    wrong = [k for k in a.actions if not k.startswith(f"{a.name}:")]
    if wrong:
        _problems.append({"package": package, "name": a.name, "error": f"actions must start with '{a.name}:': {', '.join(wrong)}"})
        return None
    return a


@lru_cache(maxsize=1)
def loaded() -> tuple[Addon, ...]:
    _problems.clear()
    out: list[Addon] = []
    for package in [p.strip() for p in os.environ.get("KEEL_ADDONS", "").split(",") if p.strip()]:
        a = _load_one(package)
        if a and not any(x.name == a.name for x in out):
            out.append(a)
            log.info("add-on %s %s loaded from %s", a.name, a.version, package)
    for p in _problems:
        log.warning("add-on %s left out: %s", p.get("package"), p["error"])
    return tuple(out)


def reload() -> tuple[Addon, ...]:
    """Read KEEL_ADDONS again (tests)."""
    loaded.cache_clear()
    return loaded()


def info() -> dict:
    return {"addons": [a.info() for a in loaded()], "problems": list(_problems)}


def names() -> set[str]:
    return {a.name for a in loaded()}


# ------------------------------------------------------------------ content

def folders(kind: str) -> list[tuple[str, Path]]:
    """(add-on name, folder) for each loaded add-on that has content of this kind (workflows, agents)."""
    return [(a.name, d) for a in loaded() if (d := a.folder(kind))]


def agent_file(agent: str) -> Path | None:
    for _name, d in folders("agents"):
        f = d / f"{agent}.md"
        if f.is_file():
            return f
    return None


def agent_path(agent: str) -> Path:
    """keel's own agent file, else a loaded add-on's (the path may not exist: an agent with no file)."""
    core = config.content_dir() / "agents" / f"{agent}.md"
    return core if core.is_file() else (agent_file(agent) or core)


def agent_files() -> list[Path]:
    """Every agent file: keel's own, then the add-ons' (an add-on cannot replace one of keel's)."""
    core = sorted((config.content_dir() / "agents").glob("*.md")) if (config.content_dir() / "agents").is_dir() else []
    seen = {f.stem for f in core}
    more = [f for _n, d in folders("agents") for f in sorted(d.glob("*.md")) if f.stem not in seen]
    return core + more


# ------------------------------------------------------------------ actions, fake answers, routes, events

def has_action(action: str) -> bool:
    return any(action in a.actions for a in loaded())


async def run_action(action: str, inp):
    for a in loaded():
        fn = a.actions.get(action)
        if fn is None:
            continue
        if inspect.iscoroutinefunction(fn):
            return await fn(inp)
        import asyncio

        return await asyncio.to_thread(fn, inp)
    raise KeyError(action)


def fake_answer(req):
    """The first add-on answer for this agent request in fake mode, or None."""
    for a in loaded():
        if callable(a.fake):
            got = a.fake(req)
            if got:
                return got
    return None


def mount(app) -> None:
    for a in loaded():
        if a.router is not None:
            app.include_router(a.router)


def may_emit(kind: str) -> bool:
    """An add-on may emit its own event kinds only: '<name>.<something>'."""
    head = kind.split(".", 1)[0]
    return "." in kind and head in names()
