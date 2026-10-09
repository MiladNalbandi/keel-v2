"""keel's parts: what each built-in part and each loaded add-on or plugin adds to the engine, in one registry.

Core asks this registry and never imports a part (tests/test_fence.py, docs/plugins/09-step2-contract.md §3). The
built-in parts are named in one place, builtins.py (each module's PART dict); add-ons and plugins come from addons.py
(KEEL_PLUGIN_ADDONS, KEEL_ADDONS: each package's ADDON dict). A part's dict may have these keys (only name is needed):

    name, title   its id ("db") and name ("Database"); its actions are "<name>:*"
    order         its place in the registry: lower first, default 100; parts with the same order keep the order they
                  load in (built-ins, then add-ons). Database 10, Git 20: KeelBot, validation and keel2 mcp name them
                  before CI/CD, as keel 0.15.1 did, whether they are built in or plugins
    per_project   True: a project turns it on (Tools › Plugins, the flow's settings["plugins"]); while it is off its
                  actions and its MCP server are refused. Without it the part is on for every project.
    actions       {"<name>:x": fn}: workflow code steps, fn(ActionInput) -> ActionResult, plain or async
    params        {"<name>:x": {"sql": "required", "connection": "optional"}}: what each takes in `with:`
    docs          {"<name>:x": {"summary": str, "steps": [str]}}: each action in plain words (explain a step)
    read_tools    the tools of its MCP server; agents may call them without asking (the guard's hook)
    mcp           its MCP server for agents: {"server": "keel-db", "module": "keel_engine.plugins.server", "args":
                  ["db"], "call": fn(call, tool, args) -> str} runs with one key per agent call (open_call below);
                  a server the part hands out itself (hook mcp_specs) has no module. "prompt": what an agent that has
                  this server is told (runtime/agent_knowledge.py)
    router        a FastAPI router: its engine routes, mounted after keel's own
    errors        exception types (with .status and .hint) its routes raise; the app answers them as 4xx
    keelbot       {"prompt": str, "actions": [str]}: what KeelBot is told while the part is on (runtime/keelbot.py)
    keel_mcp      fn(server, api, guard, write): adds its tools to keel's own MCP server (keel2 mcp, mcp_server.py)
    hooks         {name: fn}, called by core:
                    on_scan(root, pid, rebuild=False) -> dict   a project scan (runtime/scan.py): fields for its index row
                    on_commit(root)                             after each keel commit (runtime/actions.py)
                    on_thread_start(root, thread)               a flow starts (runtime/service.py)
                    prompt_context(agent, root, pid, query) -> str   more for an agent's or KeelBot's prompt
                    mcp_specs(agent, root) -> [spec]            MCP servers it hands to an agent call or KeelBot turn
                    pr_body_sections(thread_id) -> [line]       lines for the PR body and the final review
                    index_available() -> bool                   it can build the project's code index (scan status)

The value of actions, params, docs, router, errors or keelbot may be a function without arguments that returns it: the
registry calls it once, on first use. So a part's module stays light (the guard's hook reads every part's read_tools
on each tool call) and its code loads only when it is used.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import secrets
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import addons, builtins, config

log = logging.getLogger("keel.extensions")
LAZY = ("actions", "params", "docs", "router", "errors", "keelbot")
CALL_TTL = 4 * 3600


class PartError(Exception):
    """A refusal from a part's route or tool call: the app answers {error, hint} with this status."""

    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


def _resolve(key: str, value):
    """A lazy value (a function without arguments) becomes what it returns; errors become a tuple of types."""
    if value is None or key not in LAZY:
        return value
    if key == "router":
        return value if hasattr(value, "routes") or not callable(value) else value()
    if key == "errors":
        if callable(value) and not isinstance(value, type):
            value = value()
        return tuple(value) if isinstance(value, (tuple, list)) else (value,)
    return value() if callable(value) else value


class Part:
    """One part: its dict, with the lazy values resolved on first use."""

    def __init__(self, spec: dict, source: str, builtin: bool):
        self.spec = spec
        self.name = str(spec["name"])
        self.title = str(spec.get("title") or self.name)
        self.per_project = bool(spec.get("per_project"))
        self.order = int(spec.get("order", 100))
        self.source = source            # the module or package it comes from
        self.builtin = builtin
        self._got: dict = {}

    def get(self, key: str, default=None):
        if key not in self._got:
            self._got[key] = _resolve(key, self.spec.get(key))
        value = self._got[key]
        return default if value is None else value

    @property
    def actions(self) -> dict:
        return self.get("actions", {})

    @property
    def params(self) -> dict:
        return self.get("params", {})

    @property
    def mcp(self) -> dict:
        return self.spec.get("mcp") or {}

    @property
    def read_tools(self) -> tuple[str, ...]:
        return tuple(self.spec.get("read_tools") or ())

    def hook(self, name: str):
        fn = (self.spec.get("hooks") or {}).get(name)
        return fn if callable(fn) else None

    def __repr__(self) -> str:
        return f"Part({self.name!r} from {self.source})"


# ------------------------------------------------------------------ loading

@lru_cache(maxsize=1)
def _builtins() -> tuple[Part, ...]:
    out: list[Part] = []
    for module in builtins.BUILTINS:
        try:
            spec = importlib.import_module(module).PART
        except Exception as exc:  # noqa: BLE001 - a part that does not load is left out and logged, keel still starts
            log.warning("built-in part %s left out: %s", module, str(exc)[:300])
            continue
        out.append(Part(spec, module, True))
    return tuple(out)


_addon_parts: dict[str, tuple[object, Part]] = {}


def _addon_part(a) -> Part:
    """An add-on as a part: its PART dict (a part moved out of core keeps the keys it had as a built-in), then its
    ADDON dict, with the actions and router addons.py checked. A router or actions only in PART are used as they are."""
    have = _addon_parts.get(a.package)
    if have and have[0] is a:
        return have[1]
    mod = sys.modules.get(a.package) or importlib.import_module(a.package)
    part_spec = getattr(mod, "PART", None) or {}
    spec = {**part_spec, **(getattr(mod, "ADDON", None) or {}), "name": a.name,
            "actions": a.actions or part_spec.get("actions") or {},
            "router": a.router if a.router is not None else part_spec.get("router")}
    part_ = Part(spec, a.package, False)
    _addon_parts[a.package] = (a, part_)
    return part_


def parts() -> tuple[Part, ...]:
    """The built-in parts, then the loaded add-ons and plugins; each name once (the first one wins); sorted by their
    order (stable: the same order keeps this one)."""
    out = list(_builtins())
    seen = {p.name for p in out}
    for a in addons.loaded():
        if a.name not in seen:
            out.append(_addon_part(a))
            seen.add(a.name)
    return tuple(sorted(out, key=lambda p: p.order))


def reload() -> tuple[Part, ...]:
    """Load the built-in parts and the add-ons again (tests)."""
    _builtins.cache_clear()
    _addon_parts.clear()
    addons.reload()
    return parts()


def part(name: str) -> Part | None:
    return next((p for p in parts() if p.name == name), None)


def title(name: str) -> str:
    p = part(name)
    return p.title if p else name


# ------------------------------------------------------------------ on per project

def enabled(settings: dict | None) -> list[str]:
    """The per-project parts these settings turn on (settings["plugins"], in that order)."""
    names = {p.name for p in parts() if p.per_project}
    return [n for n in (settings or {}).get("plugins") or [] if n in names]


def on(settings: dict | None, name: str) -> bool:
    """Is this part on for the flow or turn: a per-project one when the settings name it, any other always."""
    p = part(name)
    return bool(p) and (not p.per_project or name in enabled(settings))


# ------------------------------------------------------------------ actions

def owner(action: str) -> Part | None:
    """The part whose action this is ("<name>:..."); None for keel's own actions."""
    head, colon, _ = action.partition(":")
    return part(head) if colon else None


def has_action(action: str) -> bool:
    p = owner(action)
    return bool(p and action in p.actions)


def action_names() -> list[str]:
    return [name for p in parts() for name in p.actions]


def action_params() -> dict[str, dict]:
    """Every part's step action that declares what it takes in `with:`."""
    return {k: v for p in parts() for k, v in p.params.items()}


def param_prefixes() -> list[str]:
    """The parts whose steps take `with:` ("db", "git", "ci")."""
    return [p.name for p in parts() if p.params]


def docs() -> dict[str, dict]:
    return {k: v for p in parts() for k, v in p.get("docs", {}).items()}


async def run_action(action: str, a):
    """A part's code step: a per-project part must be on for the project."""
    import asyncio

    from .runtime.actions import ActionResult

    p = owner(action)
    if p is None:
        return ActionResult(False, f"Unknown action {action}.")
    if p.per_project and not on(a.settings, p.name):
        return ActionResult(False, f"The {p.title} plugin is off for this project, so {action} cannot run.",
                            "Turn it on in Tools › Plugins, then retry the step.")
    fn = p.actions.get(action)
    if not fn:
        return ActionResult(False, f"Unknown action {action}.")
    if inspect.iscoroutinefunction(fn):
        return await fn(a)
    return await asyncio.to_thread(fn, a)


# ------------------------------------------------------------------ MCP servers, and one agent call's key

def read_tools() -> dict[str, set[str]]:
    """{MCP server: the tools agents may call there without asking}."""
    out: dict[str, set[str]] = {}
    for p in parts():
        if p.mcp.get("server") and p.read_tools:
            out.setdefault(p.mcp["server"], set()).update(p.read_tools)
    return out


def servers() -> dict[str, str]:
    """{part: its MCP server} for the parts whose server keel runs with an agent call's key."""
    return {p.name: p.mcp["server"] for p in parts() if p.mcp.get("server") and p.mcp.get("module")}


def server_prompt(server: str) -> str:
    """What an agent that has this MCP server is told about it ("" when no part says)."""
    return next((str(p.mcp.get("prompt") or "") for p in parts() if p.mcp.get("server") == server), "")


_calls: dict[str, dict] = {}


def _sweep():
    now = time.time()
    for k in [k for k, c in _calls.items() if c["until"] < now]:
        _calls.pop(k, None)


def open_call(*, project: str, root: str, keys: dict | None, plugins: list[str], who: str) -> str:
    """A key for one agent call or KeelBot turn (memory only, at most 4 hours). The parts' MCP servers send it back with
    each tool call (POST /plugins/call), so the engine knows the project, its folder and its keys without writing any of
    them to disk."""
    _sweep()
    key = "pk_" + secrets.token_urlsafe(24)
    _calls[key] = {"project": project, "root": root, "keys": dict(keys or {}), "plugins": list(plugins), "who": who,
                   "until": time.time() + CALL_TTL}
    return key


def close_call(key: str | None):
    if key:
        _calls.pop(key, None)


def _package_folder(p: Part) -> str | None:
    """The folder that holds a plugin part's package (plugins/ci/engine), so the server it starts can import it; None for
    a built-in part (keel's own Python finds keel_engine)."""
    if p.builtin:
        return None
    top = sys.modules.get(p.source.split(".", 1)[0])
    paths = list(getattr(top, "__path__", None) or [])
    return str(Path(paths[0]).resolve().parent) if paths else None


def server_specs(names: list[str], key: str) -> list[dict]:
    """The MCP servers of these parts for one call: each runs its module and asks the engine back with the key. A
    plugin's server gets its package's folder on PYTHONPATH (that process only: a project's commands never see it)."""
    url = f"http://127.0.0.1:{config.port()}/plugins/call"
    out = []
    for n in names:
        p = part(n)
        if p and p.mcp.get("server") and p.mcp.get("module"):
            env = {"KEEL_PLUGIN_URL": url, "KEEL_PLUGIN_KEY": key}
            if folder := _package_folder(p):
                env["PYTHONPATH"] = folder
            out.append({"name": p.mcp["server"], "command": sys.executable,
                        "args": ["-m", p.mcp["module"], *[str(x) for x in p.mcp.get("args") or []]], "env": env})
    return out


def allow_entries(names: list[str]) -> list[str]:
    have = servers()
    return [f"mcp:{have[n]}:*" for n in names if n in have]


def call(key: str, tool: str, args: dict) -> str:
    """One tool call from a part's MCP server: the answer as text for the model."""
    c = _calls.get(key or "")
    if not c or c["until"] < time.time():
        raise PartError(401, "This tool call has no valid key: the agent call it belonged to has ended.")
    p = next((p for p in parts() if tool in p.read_tools and callable(p.mcp.get("call"))), None)
    if p is None or p.name not in c["plugins"]:
        raise PartError(403, f"The {p.name if p else '?'} plugin is off for this project.", "Turn it on in Tools › Plugins.")
    return p.mcp["call"](c, tool, args or {})


# ------------------------------------------------------------------ hooks

@dataclass
class Agent:
    """Who a hook works for: one agent call of a flow, or one KeelBot turn."""
    name: str
    knowledge: dict = field(default_factory=dict)     # its knowledge setting (runtime/agent_knowledge.py)
    mentions: list[dict] | None = None                # KeelBot: what the person pointed at
    open_file: str | None = None
    selection: dict | None = None


def hooks(name: str) -> list[tuple[str, object]]:
    """(part, fn) for each part that has this hook, in the registry's order."""
    return [(p.name, fn) for p in parts() if (fn := p.hook(name))]


def call_hook(fn, *args, **optional):
    """fn(*args) with those optional keyword arguments it takes (a hook written without them still works)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **optional)
    return fn(*args, **{k: v for k, v in optional.items() if k in params})


def _notify(name: str, *args) -> None:
    """An event hook in every part: best effort, a failing part never stops a flow."""
    for who, fn in hooks(name):
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s of the %s part failed: %s", name, who, exc)


def on_commit(root: str) -> None:
    _notify("on_commit", root)


def on_thread_start(root: str, thread: str) -> None:
    _notify("on_thread_start", root, thread)


def mcp_specs(agent: Agent, root: str) -> list[dict]:
    """The MCP servers the parts hand to this agent call or KeelBot turn (the code graph's, once its index is ready)."""
    return [s for _who, fn in hooks("mcp_specs") for s in (fn(agent, root) or []) if s]


def with_part_servers(specs: list[dict], allow: list[str], agent: Agent, root: str) -> tuple[list[dict], list[str]]:
    """An agent call's MCP servers and allow entries, plus the parts' servers (mcp_specs): each of those is added
    unless one of the same name is configured, and all its tools are allowed."""
    out_specs, out_allow = list(specs), list(allow)
    for spec in mcp_specs(agent, root):
        if not any(s.get("name") == spec["name"] for s in specs):
            out_specs.append(spec)
        out_allow.append(f"mcp:{spec['name']}:*")
    return out_specs, out_allow


def prompt_context(agent: Agent, root: str, pid: str, query: str) -> list[str]:
    """What the parts add to the prompt for this agent and question, one text each ("" left out)."""
    return [text for _who, fn in hooks("prompt_context") if (text := fn(agent, root, pid, query))]


def pr_body_sections(thread_id: str) -> list[str]:
    return [line for _who, fn in hooks("pr_body_sections") for line in (fn(thread_id) or [])]


def index_available() -> bool:
    return any(fn() for _who, fn in hooks("index_available"))


# ------------------------------------------------------------------ routes, KeelBot, keel's own MCP server

def errors() -> tuple[type, ...]:
    """PartError and every part's error types, each once."""
    out: list[type] = [PartError]
    for p in parts():
        out += [e for e in p.get("errors", ()) if e not in out]
    return tuple(out)


def mount(app) -> None:
    """Each part's routes, built-in parts first. The app mounts them after keel's own, so none can shadow one."""
    for p in parts():
        router = p.get("router")
        if router is not None:
            app.include_router(router)


def keelbot(names: list[str] | None) -> list[dict]:
    """What KeelBot is told about the parts on for this turn (per-project ones in `names`), in the registry's order."""
    on_ = set(names or [])
    return [kb for p in parts() if (kb := p.get("keelbot")) and (not p.per_project or p.name in on_)]


def keelbot_actions(names: list[str] | None) -> list[str]:
    """The step lines KeelBot gets for these parts (when it helps write a workflow), in the order of `names`."""
    return [line for n in names or [] if (p := part(n)) for line in (p.get("keelbot") or {}).get("actions") or []]


def keel_mcp(server, api, guard, write: bool) -> None:
    """Each part adds its tools to keel's own MCP server: the read ones, or (write) the acting ones."""
    for p in parts():
        fn = p.spec.get("keel_mcp")
        if callable(fn):
            fn(server, api, guard, write)
