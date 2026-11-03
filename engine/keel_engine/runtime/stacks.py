"""Stack packs: which of keel's stacks a project uses (the engine's twin of the api's StackService).

    defs(root)       every stack keel knows: content/stacks/*.yml, content/packs/<name>/stack.yml, then the project's
                     own <root>/.keel/stacks (a project stack with the same name wins)
    matched(root)    the ones this project uses: a project stack always, a shipped one when its `detect` block matches
                     (exclude_files first, then files / package_deps / extensions, in the root and one folder down);
                     never one the project turned off (keel 0.18.9: `stacks: {ignore: [name]}` in .keel/config.yml)
    turned_off(root) those names
    validate_setup(block)  what is wrong with a stack's `setup:` block (keel 0.18.22, runtime/stack_setup.py); a stack read
                     here with a broken one keeps everything else, its setup is left out and _setup_errors says why

Detection walks the tree for `extensions`, so the answer is kept for a minute per root.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import yaml

from .. import config

log = logging.getLogger(__name__)

SKIP = {"node_modules", "build", ".git", ".venv", "venv", "dist", "target", ".gradle", "vendor", "__pycache__"}
TTL = 60.0
_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _read(f: Path, source: str) -> dict | None:
    try:
        doc = yaml.safe_load(f.read_text())
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(doc, dict) or not doc.get("name"):
        return None
    out = {**doc, "_source": source, "_file": str(f)}
    if "setup" in out:
        errors = validate_setup(out["setup"])
        if errors:
            log.warning("stack %s (%s): its setup block is left out: %s", doc.get("name"), f, "; ".join(errors))
            out.pop("setup")
            out["_setup_errors"] = errors
    return out


# ------------------------------------------------------------------ setup: (keel 0.18.22)

SETUP_KEYS = ("apt", "run", "each_worktree")
APT_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,99}$")
SETUP_CMD_MAX = 2000


def validate_setup(block) -> list[str]:
    """The errors of a stack's `setup:` block (empty = fine; None = no block). apt: Debian package names; run and
    each_worktree: shell commands, one per entry."""
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["setup is not a mapping: give apt, run or each_worktree, each a list"]
    errors = [f"setup.{k} is not something keel reads (apt, run, each_worktree)" for k in block if k not in SETUP_KEYS]
    for k in SETUP_KEYS:
        v = block.get(k)
        if v is None:
            continue
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            errors.append(f"setup.{k} is not a list of text")
            continue
        for x in v:
            if k == "apt" and not APT_NAME.match(x.strip()):
                errors.append(f"setup.apt has {x!r}: a package name is lower-case letters, digits, + . and -")
            elif k != "apt" and len(x) > SETUP_CMD_MAX:
                errors.append(f"setup.{k} has a command longer than {SETUP_CMD_MAX} characters")
    return errors


# ------------------------------------------------------------------ languages: (keel 0.18.28)

LANG_NAME = re.compile(r"^[a-z][a-z0-9+#-]{0,31}$")
EXT_NAME = re.compile(r"^\.?[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def highlight_table() -> dict:
    """content/highlight-languages.json: the highlight.js names a stack may name, and the default per file ending."""
    try:
        doc = json.loads((config.content_dir() / "highlight-languages.json").read_text())
    except (OSError, ValueError):
        doc = {}
    return {"names": list(doc.get("names") or []), "extensions": dict(doc.get("extensions") or {})}


def validate_languages(block) -> tuple[list[str], list[str]]:
    """(errors, warnings) of a stack's `languages:` list: [{name: <highlight.js name>, ext: [<file ending>]}]. A name
    keel's dashboard cannot load is a warning (its files show plain)."""
    if block is None:
        return [], []
    if not isinstance(block, list):
        return ["languages is not a list of {name, ext}: like [{name: php, ext: [php, phtml]}]"], []
    errors, warnings = [], []
    known = set(highlight_table()["names"])
    for i, item in enumerate(block):
        if not isinstance(item, dict):
            errors.append(f"languages[{i}] is not {{name, ext}}")
            continue
        extra = sorted(k for k in item if k not in ("name", "ext"))
        if extra:
            errors.append(f"languages[{i}] has {', '.join(map(str, extra))}: only name and ext")
        name = str(item.get("name") or "")
        if not LANG_NAME.match(name):
            errors.append(f"languages[{i}] has the name {item.get('name')!r}: a highlight.js language, like php or xml")
        elif known and name not in known:
            warnings.append(f"languages: keel's dashboard has no highlight for {name} ({', '.join(sorted(known))}); its files show plain")
        ext = item.get("ext")
        if not isinstance(ext, list) or not ext or not all(isinstance(x, str) and EXT_NAME.match(x) for x in ext):
            errors.append(f"languages[{i}] ({name or '?'}): ext is a list of file endings, like [php, phtml]")
    return errors, warnings


def languages_of(doc: dict) -> list[dict]:
    """A stack's languages: its own `languages:` list, else one per detect.extensions ending keel knows."""
    own = doc.get("languages")
    if isinstance(own, list) and own and not validate_languages(own)[0]:
        return [{"name": str(x["name"]), "ext": [str(e).lstrip(".").lower() for e in x["ext"]]} for x in own]
    table = highlight_table()["extensions"]
    by: dict[str, list[str]] = {}
    for e in _strings((doc.get("detect") or {}).get("extensions") if isinstance(doc.get("detect"), dict) else None):
        e = e.lstrip(".").lower()
        if table.get(e):
            by.setdefault(table[e], []).append(e)
    return [{"name": n, "ext": x} for n, x in by.items()]


def project_languages(root: str) -> list[dict]:
    """The languages of the stacks this project uses (matched), merged by name: [{name, ext, stacks}]."""
    out: dict[str, dict] = {}
    for d in matched(root):
        for lang in languages_of(d):
            cur = out.setdefault(lang["name"], {"name": lang["name"], "ext": [], "stacks": []})
            cur["ext"] += [e for e in lang["ext"] if e not in cur["ext"]]
            if d["name"] not in cur["stacks"]:
                cur["stacks"].append(d["name"])
    return list(out.values())


def shipped() -> list[dict]:
    """keel's own stacks (source "keel") and packs (source "keel pack")."""
    base = config.content_dir()
    out = [d for f in sorted((base / "stacks").glob("*.yml")) if (d := _read(f, "keel"))]
    packs = base / "packs"
    if packs.is_dir():
        out += [d for p in sorted(packs.iterdir()) if (p / "stack.yml").is_file() and (d := _read(p / "stack.yml", "keel pack"))]
    return out


def project_defs(root: str) -> list[dict]:
    """<root>/.keel/stacks: a <name>.yml, a <dir>/stack.yml or yml files in <dir> (what installing a pack leaves)."""
    d = Path(root) / ".keel" / "stacks"
    if not d.is_dir():
        return []
    files = sorted(f for f in d.rglob("*.yml") if f.is_file() and len(f.relative_to(d).parts) <= 3)
    seen: set[str] = set()
    out = []
    for f in files:
        doc = _read(f, "project")
        if doc and doc["name"] not in seen:
            seen.add(doc["name"])
            out.append(doc)
    return out


def defs(root: str) -> list[dict]:
    project = project_defs(root)
    names = {d["name"] for d in project}
    return [d for d in shipped() if d["name"] not in names] + project


def _strings(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


def _dirs(root: Path) -> list[Path]:
    try:
        kids = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP)
    except OSError:
        kids = []
    return [root, *kids]


def _package_deps(d: Path) -> set[str]:
    f = d / "package.json"
    if not f.is_file():
        return set()
    try:
        doc = json.loads(f.read_text())
    except (OSError, ValueError):
        return set()
    return {k for key in ("dependencies", "devDependencies") for k in (doc.get(key) or {})}


def _has_extension(d: Path, exts: list[str], budget: list[int]) -> bool:
    stack = [d]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except OSError:
            continue
        for e in entries:
            budget[0] -= 1
            if budget[0] <= 0:
                return False
            if e.is_dir():
                if e.name not in SKIP and not e.name.startswith(".") and len(e.relative_to(d).parts) < 6:
                    stack.append(e)
            elif any(e.name.endswith(x) for x in exts):
                return True
    return False


def detects(detect: dict, root: str) -> bool:
    """The api's StackService.detects: `exclude_files` first; then any of files / package_deps / extensions."""
    detect = detect if isinstance(detect, dict) else {}
    exclude, wanted = _strings(detect.get("exclude_files")), _strings(detect.get("files"))
    deps, exts = _strings(detect.get("package_deps")), _strings(detect.get("extensions"))
    if not (wanted or deps or exts):
        return False
    budget = [20_000]
    for d in _dirs(Path(root)):
        if any((d / x).exists() for x in exclude):
            continue
        if any((d / x).exists() for x in wanted):
            return True
        if deps and _package_deps(d) & set(deps):
            return True
        if exts and _has_extension(d, exts, budget):
            return True
    return False


LOCAL_STACKS = "keel/stacks.json"


def local_file(root: str) -> Path | None:
    """0.22.0 this clone's own stack switches: <git common dir>/keel/stacks.json, shared by its worktrees, never in a
    commit and out of the agents' reach (they never write inside .git). None outside git."""
    import subprocess

    try:
        r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return (Path(root) / r.stdout.strip()).resolve() / LOCAL_STACKS


def local_switches(root: str) -> dict:
    """{off: [...], on: [...]} of this clone (local_file); empty when there are none."""
    f = local_file(root)
    try:
        doc = json.loads(f.read_text()) if f and f.is_file() else {}
    except (OSError, ValueError):
        return {"off": [], "on": []}
    doc = doc if isinstance(doc, dict) else {}
    return {"off": _strings(doc.get("off")), "on": _strings(doc.get("on"))}


def turned_off(root: str) -> set[str]:
    """The stacks this project turned off (keel 0.18.9): `stacks: {ignore: [name, ...]}` in .keel/config.yml (not `off`:
    YAML 1.1 reads that key as false). keel's own stack that detection matched wrongly is turned off here; it stays in
    keel, only this project does not use it. 0.22.0 plus this clone's own switches (local_switches): a tracked
    config.yml is not changed by a switch, so the tree stays clean and the next flow can start."""
    f = Path(root) / ".keel" / "config.yml"
    try:
        doc = yaml.safe_load(f.read_text()) if f.is_file() else None
    except (OSError, yaml.YAMLError):
        doc = None
    block = doc.get("stacks") if isinstance(doc, dict) else None
    shared = set(_strings(block.get("ignore"))) if isinstance(block, dict) else set()
    mine = local_switches(root)
    return (shared | set(mine["off"])) - set(mine["on"])


def matched(root: str) -> list[dict]:
    """The stacks this project uses, keel's first, then the project's own; none it turned off."""
    key = str(Path(root).resolve())
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    off = turned_off(root)
    out = [d for d in defs(root) if d["name"] not in off and (d["_source"] == "project" or detects(d.get("detect") or {}, root))]
    _CACHE[key] = (time.monotonic(), out)
    return out


def forget(root: str | None = None):
    """Drop the cached detection (tests, or after the project's files changed shape)."""
    if root is None:
        _CACHE.clear()
    else:
        _CACHE.pop(str(Path(root).resolve()), None)


WEB_TEST_KEYS = ("web_test_ac", "web_test_module", "web_test_paths")


def has_web_layer(root: str) -> bool:
    """Does the project test a web layer of its own (0.18.12)? Yes with a web test command (commands.web_test_* in
    .keel/config.yml), a frontend folder that exists (frontend.dir), or a stack in the web lane. A server-rendered app
    (Spring MVC with Thymeleaf, Django templates) has none: its pages are tested by backend tests in the api lane, so a
    [WEB] criterion there could never get a test past the guard, and the guard counts its templates as the backend's
    (rules.server_page)."""
    from .. import rules

    return web_layer(root, rules.load_config(root))


def web_layer(root: str, cfg: dict) -> bool:
    """has_web_layer with the project's config already read (rules.classify calls it with its own)."""
    cmds = cfg.get("commands") or {}
    if any(str(cmds.get(k) or "").strip() for k in WEB_TEST_KEYS):
        return True
    front = str(((cfg.get("frontend") or {}).get("dir")) or "").strip().strip("/")
    while front.startswith("./"):
        front = front[2:]
    if front not in ("", ".") and ".." not in Path(front).parts and (Path(root) / front).is_dir():
        return True
    return any(str(d.get("lane") or "") == "web" or any(k in (d.get("commands") or {}) for k in WEB_TEST_KEYS)
               for d in matched(root))
