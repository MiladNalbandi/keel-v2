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
            "parts", "per_project", "contributes", "permissions"}
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
    mcp = contributes.get("mcp")
    if isinstance(mcp, dict):
        for mode in ("read", "act"):
            for tool in each(mcp.get(mode), f"contributes.mcp.{mode}"):
                if not isinstance(tool, str) or not tool.startswith((f"{under}_", f"{name}_")):
                    c.error(f"contributes.mcp.{mode}: the agent tool '{tool}' must start with '{under}_'")
    elif mcp is not None:
        c.error("contributes.mcp must be a mapping")


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
