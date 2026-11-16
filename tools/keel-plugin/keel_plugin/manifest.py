"""keel-plugin.yml, schema 1, and files.sha256: the same rules as keel's engine (keel_engine/pluginhost/manifest.py,
docs/plugins/02-plugin-package.md), written again here because this tool never imports keel.

check() lists every problem it finds (keel stops at the first one); read() gives the fields keel uses.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import PluginToolError, miniyaml

MANIFEST = "keel-plugin.yml"
SUMS = "files.sha256"
NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
PLAIN_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
PACKAGE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SPEC_PART = re.compile(r"^(>=|<=|==|>|<)\s*[0-9][0-9.]*$")
SUM_LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")
TOP_KEYS = {"schema", "name", "title", "version", "publisher", "summary", "repo", "license", "requires", "optional",
            "parts", "per_project", "starts_off", "contributes", "permissions"}
PERMISSIONS = {"secrets", "network", "workspace", "agent_tools", "flows", "tables", "pages"}


class ManifestError(PluginToolError):
    """keel-plugin.yml cannot be read at all."""


@dataclass
class Manifest:
    name: str
    version: str
    title: str
    publisher: str
    summary: str
    sdk: int
    keel: str = ""
    plugins: dict[str, str] = field(default_factory=dict)
    parts: dict = field(default_factory=dict)        # engine, api, web, content, migrations (only those it has)
    permissions: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)         # the whole manifest

    def requires(self) -> dict:
        """requires as keel's catalog lists it: sdk, then keel and plugins when the manifest has them."""
        out: dict = {"sdk": self.sdk}
        if self.keel:
            out["keel"] = self.keel
        if self.plugins:
            out["plugins"] = dict(self.plugins)
        return out

    def trust(self) -> str:
        """content, web or code (03-security.md, 3.1): engine or api code runs inside keel."""
        if {"engine", "api"} & set(self.parts):
            return "code"
        return "web" if "web" in self.parts else "content"


def valid_spec(spec: str) -> bool:
    """A version range keel reads ('>=0.15.0,<1.0.0'; empty means any)."""
    return all(SPEC_PART.match(p.strip()) for p in spec.split(",") if p.strip())


def bad_path(value) -> str | None:
    """Why value is not a relative path inside the plugin folder, or None."""
    if not isinstance(value, str) or not value.strip():
        return "is not a path"
    p = PurePosixPath(value.strip())
    if p.is_absolute() or value.startswith("~"):
        return f"'{value}' is an absolute path"
    if ".." in p.parts:
        return f"'{value}' goes outside the plugin folder ('..')"
    if "\\" in value:
        return f"'{value}' has a backslash"
    return None


def load(text: str) -> dict:
    try:
        data = miniyaml.loads(text)
    except miniyaml.YamlError as exc:
        raise ManifestError(f"{MANIFEST} is not YAML this tool can read: {exc}") from None
    if not isinstance(data, dict):
        raise ManifestError(f"{MANIFEST} must be a mapping of fields (schema, name, version, requires, parts)")
    return data


class _Check:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, text: str) -> None:
        self.errors.append(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)


def _text(c: _Check, data: dict, key: str, why: str = "") -> str:
    v = data.get(key)
    if v is None:
        if why:
            c.warn(f"{key} is missing: {why}")
        return ""
    if not isinstance(v, str) or not v.strip():
        c.error(f"{key} must be text")
        return ""
    return v.strip()


def _ranges(c: _Check, value, where: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        c.error(f"{where} must be a mapping of plugin names to version ranges")
        return {}
    out = {}
    for dep, spec in value.items():
        spec = "" if spec is None else spec
        if not isinstance(dep, str) or not NAME.match(dep):
            c.error(f"{where}: '{dep}' is not a plugin name")
        elif not isinstance(spec, str) or not valid_spec(spec):
            c.error(f"{where}.{dep} '{spec}' is not a version range like '>=1.0.0'")
        else:
            out[dep] = spec.strip()
    return out


def _path(c: _Check, value, where: str) -> str | None:
    why = bad_path(value)
    if why:
        c.error(f"{where} {why}: it must be a relative path inside the plugin folder")
        return None
    return PurePosixPath(value.strip()).as_posix()


def _paths(c: _Check, value, where: str) -> list[str]:
    if not isinstance(value, list):
        c.error(f"{where} must be a list of paths")
        return []
    return [p for i, v in enumerate(value) if (p := _path(c, v, f"{where}[{i}]"))]


def _parts(c: _Check, data: dict) -> dict:
    parts = data.get("parts")
    if parts is None:
        c.warn("parts is missing: the plugin has nothing to load")
        return {}
    if not isinstance(parts, dict):
        c.error("parts must be a mapping")
        return {}
    for key in parts:
        if key not in ("engine", "api", "web", "content", "migrations"):
            c.warn(f"parts.{key} is not a part keel knows (engine, api, web, content, migrations)")
    out: dict = {}
    if parts.get("engine") is not None:
        e = parts["engine"]
        if not isinstance(e, dict):
            c.error("parts.engine must be a mapping like { path: engine, package: keel_plugin_x }")
        else:
            package = e.get("package")
            if not isinstance(package, str) or not PACKAGE.match(package):
                c.error(f"parts.engine.package '{package}' is not a Python package name (like keel_plugin_db)")
            path = _path(c, e.get("path"), "parts.engine.path")
            if path and isinstance(package, str) and PACKAGE.match(package):
                out["engine"] = {"path": path, "package": package}
    if parts.get("api") is not None:
        a = parts["api"]
        if not isinstance(a, dict):
            c.error("parts.api must be a mapping like { jars: [api/keel-plugin-x.jar] }")
        else:
            jars = _paths(c, a.get("jars"), "parts.api.jars")
            if isinstance(a.get("jars"), list) and not a.get("jars"):
                c.error("parts.api.jars must name at least one jar")
            lib = _path(c, a["lib"], "parts.api.lib") if a.get("lib") is not None else None
            if jars:
                out["api"] = {"jars": jars, "lib": lib}
    if parts.get("web") is not None:
        w = parts["web"]
        if not isinstance(w, dict):
            c.error("parts.web must be a mapping like { entry: web/index.js }")
        else:
            entry = _path(c, w.get("entry"), "parts.web.entry")
            css = _paths(c, w.get("css") or [], "parts.web.css")
            if entry:
                out["web"] = {"entry": entry, "css": css}
    for key in ("content", "migrations"):
        if parts.get(key) is not None:
            p = _path(c, parts[key], f"parts.{key}")
            if p:
                out[key] = p
    return out


def _names(c: _Check, name: str, data: dict) -> None:
    """Names a plugin adds start with its own name (02-plugin-package.md, 2.7)."""
    contributes = data.get("contributes")
    if contributes is None:
        return
    if not isinstance(contributes, dict):
        c.error("contributes must be a mapping")
        return
    under = name.replace("-", "_")

    def each(value, where: str):
        if value is None:
            return []
        if not isinstance(value, list):
            c.error(f"{where} must be a list")
            return []
        return value

    for action in each(contributes.get("actions"), "contributes.actions"):
        if not isinstance(action, str) or not action.startswith(f"{name}:"):
            c.error(f"contributes.actions: '{action}' must start with '{name}:'")
    events = contributes.get("events") or {}
    if not isinstance(events, dict):
        c.error("contributes.events must be a mapping like { emits: [...], listens: [...] }")
    else:
        for event in each(events.get("emits"), "contributes.events.emits"):
            if not isinstance(event, str) or not event.startswith(f"{name}."):
                c.error(f"contributes.events.emits: '{event}' must start with '{name}.'")
    _tui(c, name, contributes.get("tui"))
    mcp = contributes.get("mcp")
    if isinstance(mcp, dict):
        for mode in ("read", "act"):
            for tool in each(mcp.get(mode), f"contributes.mcp.{mode}"):
                if not isinstance(tool, str) or not tool.startswith((f"{under}_", f"{name}_")):
                    c.error(f"contributes.mcp.{mode}: the agent tool '{tool}' must start with '{under}_'")
    elif mcp is not None:
        c.error("contributes.mcp must be a mapping")


TUI_NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
TUI_WORD = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
TUI_TAKEN_KEYS = {"a", "b", "c", "f", "u"}
# 0.18.6 a mode's own keys (besides arrows, Enter, Esc, PgUp/PgDn, Tab), the names templates use, a note's tones
TUI_MODE_KEYS = {"j", "k", "q"}
TUI_VARS = {"pid", "arg", "item", "line", "side", "text", "choice", "answer"}
TUI_TONES = {"accent", "dim", "warn", "bad", "ok"}


def _tui(c: _Check, name: str, tui) -> None:
    """contributes.tui (docs/plugins/25-tui-plugins.md): keel TUI's commands and panel, as data. The api checks the same
    (keel.api.tui.TuiPluginService): the names, only the plugin's own routes, and a question before a POST."""
    if tui is None:
        return
    if not isinstance(tui, dict):
        c.error("contributes.tui must be a mapping like { commands: [...], panel: {...} }")
        return

    def call(where: str, x, confirm) -> None:
        if not isinstance(x, dict) or not isinstance(x.get("path"), str):
            c.error(f"contributes.tui {where}: call needs a path")
            return
        method = x.get("method", "GET")
        path = x["path"].split("?", 1)[0]
        if method not in ("GET", "POST"):
            c.error(f"contributes.tui {where}: the method is GET or POST")
        if method == "POST" and not confirm:
            c.error(f"contributes.tui {where} changes something (POST): it needs a confirm question")
        if not (path.startswith(f"/projects/{{pid}}/{name}/") or path == f"/projects/{{pid}}/{name}" or path.startswith(f"/{name}/")) \
                or ".." in x["path"] or "://" in x["path"] or "//" in x["path"]:
            c.error(f"contributes.tui {where} may only call the plugin's own routes (/projects/{{pid}}/{name}/...), not {x['path']}")

    commands = tui.get("commands") or []
    if not isinstance(commands, list):
        c.error("contributes.tui.commands must be a list")
        commands = []
    for cmd in commands:
        if not isinstance(cmd, dict):
            c.error("contributes.tui.commands: each command is a mapping")
            continue
        cname = cmd.get("name")
        if not isinstance(cname, str) or not TUI_NAME.match(cname) or not (cname == name or cname.startswith(f"{name}-")):
            c.error(f"contributes.tui.commands: '{cname}' must be named {name} or {name}-<word>")
            continue
        if not str(cmd.get("summary") or "").strip():
            c.error(f"contributes.tui /{cname} needs a summary")
        if ("sub" in cmd) == ("call" in cmd):
            c.error(f"contributes.tui /{cname} has either sub (subcommands) or one call")
        if "call" in cmd:
            call(f"/{cname}", cmd["call"], cmd.get("confirm"))
        for word, a in (cmd.get("sub") or {}).items():
            call(f"/{cname} {word}", (a or {}).get("call"), (a or {}).get("confirm"))
    panel = tui.get("panel")
    if panel is not None:
        if not isinstance(panel, dict) or not str(panel.get("title") or "").strip():
            c.error("contributes.tui.panel needs a title")
            return
        if panel.get("key") is not None and (not re.match(r"^[a-z]$", str(panel["key"])) or panel["key"] in TUI_TAKEN_KEYS):
            c.error(f"contributes.tui.panel: the key is one letter, not one of keel TUI's own ({', '.join(sorted(TUI_TAKEN_KEYS))})")
        if (panel.get("call") or {}).get("method", "GET") != "GET":
            c.error("contributes.tui.panel only reads (GET)")
        call("panel", panel.get("call"), None)
    modes = tui.get("modes") or []
    if not isinstance(modes, list):
        c.error("contributes.tui.modes must be a list")
        modes = []
    if len(modes) > 4:
        c.error("contributes.tui.modes: at most 4 modes")
    for m in modes:
        _tui_mode(c, name, m, commands, call)


def _tui_mode(c: _Check, name: str, m, commands: list, call) -> None:
    """One of contributes.tui.modes (0.18.6): a full-screen view in keel TUI, as data. The api checks the same
    (TuiPluginService.mode): GET reads for its state, list and detail; a key that changes something asks first."""
    if not isinstance(m, dict):
        c.error("contributes.tui.modes: each mode is a mapping")
        return
    mname = m.get("name")
    if not isinstance(mname, str) or not TUI_NAME.match(mname) or not (mname == name or mname.startswith(f"{name}-")):
        c.error(f"contributes.tui.modes: '{mname}' must be named {name} or {name}-<word>")
        return
    at = f"contributes.tui mode /{mname}"

    def read(where: str, x) -> None:
        if isinstance(x, dict) and x.get("method", "GET") != "GET":
            c.error(f"{at} {where} only reads (GET)")
        call(f"mode /{mname} {where}", x, None)

    if not str(m.get("title") or "").strip():
        c.error(f"{at} needs a title")
    if not str(m.get("summary") or "").strip():
        c.error(f"{at} needs a summary")
    same = [x for x in commands if isinstance(x, dict) and x.get("name") == mname]
    if same and "sub" not in same[0]:
        c.error(f"/{mname} is a mode and a command: the command has sub (its words run outside the mode)")
    state = m.get("state")
    if not isinstance(state, dict) or not 1 <= len(state) <= 5:
        c.error(f"{at} reads its state with 1 to 5 calls (state: {{ name: {{ path }} }})")
        state = {}
    for k, x in state.items():
        if not isinstance(k, str) or not TUI_WORD.match(k) or k in TUI_VARS:
            c.error(f"{at}: the state's name '{k}' is one word, not one of {', '.join(sorted(TUI_VARS))}")
        read(f"state {k}", x)
    lst = m.get("list")
    if not isinstance(lst, dict) or not lst.get("items") or not lst.get("line"):
        c.error(f"{at}: its list needs items and line")
    detail = m.get("detail")
    if detail is not None:
        if not isinstance(detail, dict):
            c.error(f"{at}: its detail is a mapping")
        else:
            read("detail", detail.get("call"))
            if ("diff" in detail) == ("text" in detail):
                c.error(f"{at}: its detail shows either a diff or a text")
            for n in detail.get("notes") or []:
                if not isinstance(n, dict) or not n.get("items") or not n.get("text"):
                    c.error(f"{at}: a note needs items and text")
                elif n.get("tone") is not None and n["tone"] not in TUI_TONES:
                    c.error(f"{at}: a note's tone is one of {', '.join(sorted(TUI_TONES))}")
    keys = m.get("keys") or []
    if not isinstance(keys, list) or len(keys) > 20:
        c.error(f"{at}: keys is a list of at most 20")
        keys = []
    seen: set = set()
    for k in keys:
        if not isinstance(k, dict):
            c.error(f"{at}: each key is a mapping")
            continue
        key = str(k.get("key", ""))
        w = f"{at} key {key}"
        if not re.match(r"^[a-zA-Z0-9]$", key) or key in TUI_MODE_KEYS:
            c.error(f"{at}: a key is one letter or digit, not one of the mode's own ({', '.join(sorted(TUI_MODE_KEYS))})")
        if key in seen:
            c.error(f"{at}: the key {key} is used twice")
        seen.add(key)
        if not str(k.get("label") or "").strip():
            c.error(f"{w} needs a label")
        if k.get("view") not in (None, "list", "detail", "both"):
            c.error(f"{w}: view is list, detail or both")
        if not any(k.get(x) for x in ("call", "show", "reload", "open")):
            c.error(f"{w} does nothing: it needs a call, show, reload or open")
        pick = k.get("pick")
        if pick is not None:
            if not isinstance(pick, dict) or not str(pick.get("title") or "").strip():
                c.error(f"{w}: its pick needs a title")
            elif ("choices" in pick) == ("call" in pick or "items" in pick):
                c.error(f"{w}: its pick reads items (from its call's answer, or from the state) or has choices")
            elif "call" in pick:
                read(f"key {key} pick", pick["call"])
        x = k.get("call")
        if x is not None:
            if isinstance(x, dict) and x.get("method", "GET") != "GET" and not (k.get("confirm") or k.get("ask")):
                c.error(f"{w} changes something (POST): it needs confirm (a yes/no question) or ask (the person's own words)")
            call(f"mode /{mname} key {key}", x, "asks first")


def _permissions(c: _Check, name: str, data: dict) -> dict:
    perms = data.get("permissions")
    if perms is None:
        return {}
    if not isinstance(perms, dict):
        c.error("permissions must be a mapping")
        return {}
    under = name.replace("-", "_")
    for key, value in perms.items():
        if key not in PERMISSIONS:
            c.warn(f"permissions.{key} is not a permission keel knows ({', '.join(sorted(PERMISSIONS))})")
        elif key in ("secrets", "network", "tables"):
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                c.error(f"permissions.{key} must be a list of names")
            elif key == "tables":
                for t in value:
                    if not t.startswith(f"{under}_"):
                        c.error(f"permissions.tables: the table '{t}' must start with '{under}_'")
        elif key == "workspace" and value not in ("read", "write"):
            c.error("permissions.workspace must be read or write")
        elif key == "agent_tools" and value not in ("read", "act"):
            c.error("permissions.agent_tools must be read or act")
        elif key == "flows" and value != "start":
            c.error("permissions.flows can only be start")
        elif key == "pages" and not isinstance(value, bool):
            c.error("permissions.pages must be true or false")
    return perms


def check(data: dict) -> tuple[Manifest | None, list[str], list[str]]:
    """The manifest (None when its name or version cannot be used), its errors and its warnings."""
    c = _Check()
    if data.get("schema") != 1 or isinstance(data.get("schema"), bool):
        c.error(f"schema must be 1, not {data.get('schema')!r}")
    for key in data:
        if key not in TOP_KEYS:
            c.warn(f"'{key}' is not a field of keel-plugin.yml schema 1 (a typo?)")
    name = data.get("name")
    name_ok = isinstance(name, str) and bool(NAME.match(name))
    if not name_ok:
        c.error(f"name {name!r} is not valid: a-z, 0-9 and '-', starts with a letter, 32 at most")
    version = data.get("version")
    version_ok = isinstance(version, str) and bool(VERSION.match(version))
    if not version_ok:
        c.error(f"version {version!r} is not a version like 1.0.0 (write it in quotes when YAML reads it as a number)")
    elif not PLAIN_VERSION.match(version):
        c.warn(f"version {version} is a pre-release: the catalog lists x.y.z versions")
    if not isinstance(data.get("per_project", False), bool):
        c.error("per_project must be true or false")
    if not isinstance(data.get("starts_off", False), bool):
        c.error("starts_off must be true or false")
    title = _text(c, data, "title")
    publisher = _text(c, data, "publisher", "the catalog needs it (it must match the key that signs the file)")
    summary = _text(c, data, "summary", "the catalog shows it")
    req = data.get("requires")
    sdk, keel, plugins = None, "", {}
    if not isinstance(req, dict):
        c.error("requires must be a mapping with sdk: 1 (the plugin SDK major)")
    else:
        sdk = req.get("sdk")
        if isinstance(sdk, bool) or not isinstance(sdk, int):
            c.error("requires.sdk must be a whole number (the plugin SDK major, 1 today)")
            sdk = None
        keel = req.get("keel") or ""
        if not isinstance(keel, str) or not valid_spec(keel):
            c.error(f"requires.keel '{keel}' is not a version range like '>=0.15.0,<1.0.0'")
            keel = ""
        plugins = _ranges(c, req.get("plugins"), "requires.plugins")
        if name_ok and name in plugins:
            c.error("requires.plugins: a plugin cannot need itself")
    _ranges(c, data.get("optional"), "optional")
    parts = _parts(c, data)
    perms: dict = {}
    if name_ok:
        perms = _permissions(c, name, data)
        _names(c, name, data)
    if not (name_ok and version_ok and sdk is not None):
        return None, c.errors, c.warnings
    m = Manifest(name=name, version=version, title=title or name, publisher=publisher, summary=summary, sdk=sdk,
                 keel=keel.strip(), plugins=plugins, parts=parts, permissions=perms, data=data)
    return m, c.errors, c.warnings


def read(folder: Path) -> Manifest:
    """The manifest of a plugin folder; ManifestError when it has errors."""
    f = folder / MANIFEST
    if not f.is_file():
        raise ManifestError(f"{MANIFEST} is missing")
    m, errors, _ = check(load(f.read_text(encoding="utf-8")))
    if errors or m is None:
        raise ManifestError(f"{MANIFEST}: " + "; ".join(errors))
    return m


def missing_parts(m: Manifest, folder: Path) -> list[str]:
    """Every file or folder the manifest names that the plugin folder does not have."""
    out = []
    need: list[tuple[str, str, bool]] = []   # (field, path, is a folder)
    if "engine" in m.parts:
        need.append(("parts.engine.path", m.parts["engine"]["path"], True))
    if "api" in m.parts:
        need += [("parts.api.jars", j, False) for j in m.parts["api"]["jars"]]
    if "web" in m.parts:
        need += [("parts.web.entry", m.parts["web"]["entry"], False)]
        need += [("parts.web.css", css, False) for css in m.parts["web"]["css"]]
    need += [(f"parts.{k}", m.parts[k], True) for k in ("content", "migrations") if k in m.parts]
    for where, rel, is_dir in need:
        p = folder / rel
        if not (p.is_dir() if is_dir else p.is_file()):
            out.append(f"{where} '{rel}' is missing")
    if "engine" in m.parts:
        e = m.parts["engine"]
        pkg = folder / e["path"] / e["package"]
        if (folder / e["path"]).is_dir() and not (pkg.is_dir() or pkg.with_suffix(".py").is_file()):
            out.append(f"parts.engine: there is no package {e['package']} in '{e['path']}'")
    return out


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sums_text(folder: Path, files: list[str]) -> str:
    """files.sha256 for these files: '<sha256>  <path>' lines, sorted by path in byte order (like LC_ALL=C sort)."""
    rows = sorted(f for f in files if f != SUMS)
    return "".join(f"{file_sha256(folder / f)}  {f}\n" for f in sorted(rows, key=lambda s: s.encode("utf-8")))


def read_sums(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(sha256, path) for each line of files.sha256, and the problems with its lines."""
    rows, problems = [], []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        m = SUM_LINE.match(line.rstrip("\r"))
        if not m:
            problems.append(f"{SUMS} line {n} is not '<sha256>  <path>'")
            continue
        why = bad_path(m.group(2))
        if why:
            problems.append(f"{SUMS} line {n}: {why}")
            continue
        rows.append((m.group(1).lower(), PurePosixPath(m.group(2)).as_posix()))
    return rows, problems


def check_sums(folder: Path, files: list[str]) -> list[str]:
    """Problems with files.sha256: a listed file missing or changed, a file not listed. files: every file's path."""
    f = folder / SUMS
    if not f.is_file():
        return [f"{SUMS} is missing (keel checks every file against it at each start; pack writes it)"]
    rows, problems = read_sums(f.read_text(encoding="utf-8"))
    listed = set()
    for want, rel in rows:
        if rel == SUMS:
            continue
        listed.add(rel)
        p = folder / rel
        if p.is_symlink() or not p.is_file():
            problems.append(f"{SUMS}: '{rel}' is listed but missing")
        elif file_sha256(p) != want:
            problems.append(f"{SUMS}: '{rel}' does not match its sha256 (the file was changed after packing)")
    for rel in sorted(set(files) - listed - {SUMS}):
        problems.append(f"{SUMS}: '{rel}' is not listed (keel would not notice a change to it)")
    return problems
