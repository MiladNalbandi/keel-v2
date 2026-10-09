"""keel-plugin.yml, schema 1 (contract section 2), and files.sha256, the list of file hashes next to it.
Step 4 also reads `permissions` (what the marketplace shows and compares) and the Python libraries a manifest asks
for (docs/plugins/13-step4-contract.md)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from ..addons import NAME
from . import PluginError

MANIFEST = "keel-plugin.yml"
SUMS = "files.sha256"
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
PACKAGE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SPEC_PART = re.compile(r"^(>=|<=|==|>|<)\s*[0-9][0-9.]*$")
SUM_LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")


class ManifestError(PluginError):
    """keel-plugin.yml or files.sha256 is broken: the message says what is wrong."""


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    title: str
    sdk: int
    keel: str = ""
    plugins: dict[str, str] = field(default_factory=dict)
    engine: dict | None = None   # {"path": "engine", "package": "keel_x"}
    api: dict | None = None      # {"jars": ["api/x.jar"], "lib": "api/lib" | None}
    web: dict | None = None      # {"entry": "web/index.js", "css": ["web/style.css"]}
    content: str | None = None
    migrations: str | None = None
    per_project: bool = False
    publisher: str = ""
    summary: str = ""
    permissions: dict = field(default_factory=dict)   # what it may do (shown before install, docs/plugins/03-security.md)
    python: tuple[str, ...] = ()                      # extra Python libraries it asks for (requires.python,
                                                      # parts.engine.requirements); keel does not install them yet

    def requires(self) -> dict:
        """requires as resolved.json shows it: sdk, then keel and plugins when the manifest has them."""
        out: dict = {"sdk": self.sdk}
        if self.keel:
            out["keel"] = self.keel
        if self.plugins:
            out["plugins"] = dict(self.plugins)
        return out


def valid_spec(spec: str) -> bool:
    """Whether a version range has the syntax keel_engine.addons.satisfies reads ('>=0.15.0,<1.0.0'; empty is fine)."""
    return all(SPEC_PART.match(p.strip()) for p in spec.split(",") if p.strip())


def rel_path(value, where: str) -> str:
    """A path inside the plugin folder: relative, with no '..'. Returned in a clean form ('./a//b' → 'a/b')."""
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where} must be a path inside the plugin folder")
    p = PurePosixPath(value.strip())
    if p.is_absolute() or ".." in p.parts or "\\" in value or not p.parts:
        raise ManifestError(f"{where} '{value}' must be a relative path inside the plugin folder, with no '..'")
    return p.as_posix()


def _text(data: dict, key: str, required: bool = False) -> str:
    v = data.get(key)
    if v is None and not required:
        return ""
    if not isinstance(v, str) or not v.strip():
        raise ManifestError(f"{key} must be text" + (" and is required" if required else ""))
    return v.strip()


def _mapping(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestError(f"{where} must be a mapping")
    return value


def _python(data: dict) -> tuple[str, ...]:
    """The extra Python libraries a manifest asks for: requires.python (a list or a mapping) and the
    parts.engine.requirements file. keel reads them only to refuse them for now (docs/plugins/13-step4-contract.md §0)."""
    req = data.get("requires") if isinstance(data.get("requires"), dict) else {}
    asked = req.get("python")
    if isinstance(asked, dict):
        out = [f"{k} {v}".strip() if v else str(k) for k, v in asked.items()]
    else:
        out = [str(x) for x in (asked if isinstance(asked, list) else [asked] if asked else [])]
    engine = (data.get("parts") or {}).get("engine") if isinstance(data.get("parts"), dict) else None
    if isinstance(engine, dict) and engine.get("requirements"):
        out.append(str(engine["requirements"]))
    return tuple(out)


def _permissions(data: dict) -> dict:
    perms = data.get("permissions")
    if perms is None:
        return {}
    if not isinstance(perms, dict) or not all(isinstance(k, str) for k in perms):
        raise ManifestError("permissions must be a mapping (secrets, network, workspace, ...)")
    return perms


def _requires(data: dict) -> tuple[int, str, dict[str, str]]:
    req = _mapping(data.get("requires"), "requires") if "requires" in data else {}
    sdk = req.get("sdk")
    if isinstance(sdk, bool) or not isinstance(sdk, int):
        raise ManifestError("requires.sdk must be a whole number (the plugin SDK major, 1 today) and is required")
    keel = req.get("keel") or ""
    if not isinstance(keel, str) or not valid_spec(keel):
        raise ManifestError(f"requires.keel '{keel}' is not a version range like '>=0.15.0,<1.0.0'")
    plugins: dict[str, str] = {}
    for dep, spec in _mapping(req.get("plugins") or {}, "requires.plugins").items():
        spec = "" if spec is None else spec
        if not isinstance(dep, str) or not NAME.match(dep):
            raise ManifestError(f"requires.plugins: '{dep}' is not a plugin name")
        if not isinstance(spec, str) or not valid_spec(spec):
            raise ManifestError(f"requires.plugins.{dep} '{spec}' is not a version range like '>=0.1.0'")
        plugins[dep] = spec.strip()
    return sdk, keel.strip(), plugins


def _paths(value, where: str) -> list[str]:
    if not isinstance(value, list):
        raise ManifestError(f"{where} must be a list of paths")
    return [rel_path(v, f"{where}[{i}]") for i, v in enumerate(value)]


def _engine(part) -> dict:
    part = _mapping(part, "parts.engine")
    package = part.get("package")
    if not isinstance(package, str) or not PACKAGE.match(package):
        raise ManifestError(f"parts.engine.package '{package}' is not a Python package name (like keel_plugin_db)")
    return {"path": rel_path(part.get("path"), "parts.engine.path"), "package": package}


def _api(part) -> dict:
    part = _mapping(part, "parts.api")
    jars = _paths(part.get("jars"), "parts.api.jars")
    if not jars:
        raise ManifestError("parts.api.jars must name at least one jar")
    lib = part.get("lib")
    return {"jars": jars, "lib": rel_path(lib, "parts.api.lib") if lib is not None else None}


def _web(part) -> dict:
    part = _mapping(part, "parts.web")
    return {"entry": rel_path(part.get("entry"), "parts.web.entry"), "css": _paths(part.get("css") or [], "parts.web.css")}


def _parts(data: dict) -> dict:
    parts = _mapping(data.get("parts") or {}, "parts")
    return {"engine": _engine(parts["engine"]) if parts.get("engine") is not None else None,
            "api": _api(parts["api"]) if parts.get("api") is not None else None,
            "web": _web(parts["web"]) if parts.get("web") is not None else None,
            "content": rel_path(parts["content"], "parts.content") if parts.get("content") is not None else None,
            "migrations": rel_path(parts["migrations"], "parts.migrations") if parts.get("migrations") is not None else None}


def _check(data) -> Manifest:
    if not isinstance(data, dict):
        raise ManifestError("must be a mapping of fields (schema, name, version, requires, parts)")
    if data.get("schema") != 1 or isinstance(data.get("schema"), bool):
        raise ManifestError(f"schema must be 1, not {data.get('schema')!r}")
    name = data.get("name")
    if not isinstance(name, str) or not NAME.match(name):
        raise ManifestError(f"name {name!r} is not valid: a-z, 0-9 and '-', starts with a letter, 32 at most")
    version = data.get("version")
    if not isinstance(version, str) or not VERSION.match(version):
        raise ManifestError(f"version {version!r} is not a version like 1.0.0 or 0.1.0-beta.1 (write it in quotes)")
    per_project = data.get("per_project", False)
    if not isinstance(per_project, bool):
        raise ManifestError("per_project must be true or false")
    sdk, keel, plugins = _requires(data)
    return Manifest(name=name, version=version, title=_text(data, "title") or name, sdk=sdk, keel=keel, plugins=plugins,
                    per_project=per_project, publisher=_text(data, "publisher"), summary=_text(data, "summary"),
                    permissions=_permissions(data), python=_python(data), **_parts(data))


def parse(text: str) -> Manifest:
    """A manifest from its YAML text; ManifestError with a clear message when it is not schema 1."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{MANIFEST} is not valid YAML: {str(exc)[:300]}") from exc
    try:
        return _check(data)
    except ManifestError as exc:
        raise ManifestError(f"{MANIFEST}: {exc}") from None


def read(folder: Path) -> Manifest:
    """The manifest of a plugin folder."""
    f = folder / MANIFEST
    if not f.is_file():
        raise ManifestError(f"{MANIFEST} is missing")
    try:
        text = f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{MANIFEST} cannot be read: {exc}") from exc
    return parse(text)


def missing_part(m: Manifest, folder: Path) -> str | None:
    """The first file or folder the manifest names that the plugin folder does not have (api.lib is optional)."""
    need: list[tuple[str, str, bool]] = []   # (field, path, is a folder)
    if m.engine:
        need.append(("parts.engine.path", m.engine["path"], True))
    if m.api:
        need += [("parts.api.jars", j, False) for j in m.api["jars"]]
    if m.web:
        need += [("parts.web.entry", m.web["entry"], False)] + [("parts.web.css", c, False) for c in m.web["css"]]
    need += [(f"parts.{k}", getattr(m, k), True) for k in ("content", "migrations") if getattr(m, k)]
    for where, rel, is_dir in need:
        p = folder / rel
        if not (p.is_dir() if is_dir else p.is_file()):
            return f"{where} '{rel}' is missing"
    if m.engine:
        pkg = folder / m.engine["path"] / m.engine["package"]
        if not (pkg.is_dir() or pkg.with_suffix(".py").is_file()):
            return f"parts.engine: there is no package {m.engine['package']} in '{m.engine['path']}'"
    return None


def file_sha256(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def read_sums(folder: Path) -> list[tuple[str, str]]:
    """(sha256, relative path) for each line of files.sha256; [] when the plugin has none."""
    f = folder / SUMS
    if not f.is_file():
        return []
    out: list[tuple[str, str]] = []
    for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        m = SUM_LINE.match(line.rstrip("\r"))
        if not m:
            raise ManifestError(f"{SUMS} line {n} is not '<sha256>  <path>'")
        out.append((m.group(1).lower(), rel_path(m.group(2), f"{SUMS} line {n}")))
    return out


def check_sums(folder: Path) -> str | None:
    """None when every file files.sha256 lists is there with that hash (or there is no files.sha256), else why not.
    A line for files.sha256 itself is skipped: no file can hold its own hash."""
    try:
        for want, rel in read_sums(folder):
            if rel == SUMS:
                continue
            p = folder / rel
            if p.is_symlink() or not p.is_file():
                return f"{SUMS}: '{rel}' is missing"
            if file_sha256(p) != want:
                return f"{SUMS}: '{rel}' does not match (the file was changed)"
    except (ManifestError, OSError, UnicodeDecodeError) as exc:
        return str(exc)
    return None
