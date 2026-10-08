"""The resolver (contract section 4): which plugins load at the next start, in what order, and why the others do not.

    1. one folder per name: the image's $KEEL_PLUGINS_IMAGE/<name>/<version>/, or the installed version in
       $KEEL_DATA/plugins/store/<name>/<version>/ (installed.json, KEEL_PLUGINS and --only decide)
    2. checks 1–3 per plugin: the manifest and the parts it names, files.sha256, the SDK and the keel range
    3. checks 4–5 together: needs (version, no cycles) and engine packages, until nothing more is left out
    4. dependency order, then run/resolved.json and run/env
A plugin that fails a check is left out with the reason; the plugins that need it are left out too.
"""

from __future__ import annotations

import heapq
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..addons import NAME, satisfies
from . import SDK, PluginError, manifest, state
from .manifest import Manifest, ManifestError

ONLY_LEFT_OUT = "left out: keel did not start with it last time"
OFF, LEFT_OUT = "off", "left out"


@dataclass(frozen=True)
class Found:
    """A plugin folder chosen for a name, before it is checked."""
    name: str
    version: str
    dir: Path
    source: str


@dataclass
class Plugin:
    manifest: Manifest
    dir: Path
    source: str   # "image", or the installed.json source ("file")

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def version(self) -> str:
        return self.manifest.version

    def entry(self) -> dict:
        """Its run/resolved.json entry: folders and jars as absolute paths; web paths stay relative (they become urls)."""
        m, d = self.manifest, self.dir
        lib = d / m.api["lib"] if m.api and m.api["lib"] else None
        return {
            "name": m.name, "title": m.title, "version": m.version, "source": self.source, "dir": str(d),
            "engine": {"path": str(d / m.engine["path"]), "package": m.engine["package"]} if m.engine else None,
            "api": {"jars": [str(d / j) for j in m.api["jars"]],
                    "lib": str(lib) if lib and lib.is_dir() else None} if m.api else None,
            "web": {"entry": m.web["entry"], "css": list(m.web["css"])} if m.web else None,
            "content": str(d / m.content) if m.content else None,
            "migrations": str(d / m.migrations) if m.migrations else None,
            "requires": m.requires(),
        }


@dataclass
class Resolution:
    mode: str
    plugins: list[Plugin] = field(default_factory=list)   # in dependency order
    problems: list[dict] = field(default_factory=list)
    off: list[dict] = field(default_factory=list)         # turned off by the person (list shows them)

    def problem(self, name: str, version: str, folder: Path | str, error: str) -> None:
        self.problems.append({"name": name, "version": version, "dir": str(folder), "error": error})

    def document(self) -> dict:
        """run/resolved.json."""
        return {"sdk": SDK, "keel": config.VERSION, "mode": self.mode, "resolved_at": state.now(),
                "plugins": [p.entry() for p in self.plugins], "problems": list(self.problems)}

    def env(self) -> dict[str, str]:
        """run/env: engine folders for sys.path, engine packages to load, and jars for the api's loader.path."""
        paths, packages, loader = [], [], []
        for e in (p.entry() for p in self.plugins):
            if e["engine"]:
                paths.append(e["engine"]["path"])
                packages.append(e["engine"]["package"])
            if e["api"]:
                loader += e["api"]["jars"] + ([e["api"]["lib"]] if e["api"]["lib"] else [])
        return {"KEEL_PLUGIN_PATHS": ":".join(paths), "KEEL_PLUGIN_ADDONS": ",".join(packages),
                "KEEL_PLUGIN_LOADER_PATH": ",".join(loader)}


# ------------------------------------------------------------------ finding the folders

def version_key(version: str) -> tuple:
    """Sort key for version folders: 1.2.0 > 1.2.0-beta.1 > 1.1.9."""
    core, _, pre = version.split("+", 1)[0].partition("-")
    return tuple(int(p) if p.isdigit() else 0 for p in core.split(".")), pre == "", pre


def _folders(path: Path) -> list[Path]:
    """Sub-folders, without hidden ones (an install in progress is hidden)."""
    try:
        return sorted(p for p in path.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def _image(res: Resolution) -> dict[str, Path]:
    """name → its folder in the image. With several versions the newest is used and the others are listed."""
    out: dict[str, Path] = {}
    for name_dir in _folders(state.image_root()):
        versions = sorted(_folders(name_dir), key=lambda p: version_key(p.name))
        if versions:
            out[name_dir.name] = versions[-1]
        for older in versions[:-1]:
            res.problem(name_dir.name, older.name, older, f"left out: version {versions[-1].name} is used")
    return out


def _entry_error(entry) -> str | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("on", True), bool):
        return "its installed.json entry is not an object with \"on\": true or false"
    v = entry.get("version")
    if v is not None and (not isinstance(v, str) or not manifest.VERSION.match(v)):
        return f"its installed.json version {v!r} is not a version"
    return None


def _choose(res: Resolution, entries: dict, only: set[tuple[str, str]] | None) -> tuple[list[Found], dict[str, str]]:
    """One folder per name: an installed version wins over the image's (mode on only); "on": false turns a name off.
    Also returns why other names are not there ("off" or "left out")."""
    image = _image(res)
    found: list[Found] = []
    absent: dict[str, str] = {}
    for name in sorted(set(image) | set(entries)):
        entry = entries.get(name, {})
        why = None if name not in entries else ("its installed.json name is not valid" if not NAME.match(name)
                                                 else _entry_error(entry))
        if why:
            res.problem(name, "", "", why)
            absent[name] = LEFT_OUT
            continue
        if entry.get("on", True) is False:
            res.off.append({"name": name, "version": entry.get("version") or (image[name].name if name in image else "")})
            absent[name] = OFF
            continue
        if res.mode == "on" and entry.get("version"):
            f = Found(name, entry["version"], state.store_dir() / name / entry["version"], str(entry.get("source") or "file"))
            if not f.dir.is_dir():
                res.problem(name, f.version, f.dir, "installed, but its folder in the store is missing")
                absent[name] = LEFT_OUT
                continue
        elif name in image:
            f = Found(name, image[name].name, image[name], "image")
        else:
            continue
        if only is not None and (name, f.version) not in only:
            # a plugin keel could never load keeps its own reason (needs SDK 2, a bad sha256, …)
            try:
                check(f)
                why = ONLY_LEFT_OUT
            except ManifestError as exc:
                why = str(exc)
            res.problem(name, f.version, f.dir, why)
            absent[name] = LEFT_OUT
            continue
        found.append(f)
    return found, absent


# ------------------------------------------------------------------ checks

def check(f: Found) -> Manifest:
    """Checks 1–3 for one plugin folder: the manifest (and the parts it names), files.sha256, the SDK and keel range."""
    m = manifest.read(f.dir)
    if m.name != f.name:
        raise ManifestError(f"name '{m.name}' does not match its folder '{f.name}'")
    if m.version != f.version:
        raise ManifestError(f"version '{m.version}' does not match its folder '{f.version}'")
    for why in (manifest.missing_part(m, f.dir), manifest.check_sums(f.dir)):
        if why:
            raise ManifestError(why)
    if m.sdk != SDK:
        raise ManifestError(f"needs plugin SDK {m.sdk}, this keel has {SDK}")
    if not satisfies(config.VERSION, m.keel):
        raise ManifestError(f"needs keel {m.keel}, this is keel {config.VERSION}")
    return m


def _unmet(p: Plugin, chosen: dict[str, Plugin], absent: dict[str, str]) -> str | None:
    for dep, spec in sorted(p.manifest.plugins.items()):
        if dep in chosen:
            if not satisfies(chosen[dep].version, spec):
                return f"needs {dep} {spec}, found {chosen[dep].version}"
        elif absent.get(dep) == OFF:
            return f"needs {dep}, which is off"
        elif dep in absent:
            return f"needs {dep}, which was left out"
        else:
            return f"needs {dep}, which is not installed"
    return None


def _leave_out(p: Plugin, why: str, chosen: dict[str, Plugin], absent: dict[str, str], res: Resolution) -> None:
    chosen.pop(p.name, None)
    absent[p.name] = LEFT_OUT
    res.problem(p.name, p.version, p.dir, why)


def _drop_unmet(chosen: dict[str, Plugin], absent: dict[str, str], res: Resolution) -> bool:
    dropped = False
    for name in sorted(chosen):
        why = _unmet(chosen[name], chosen, absent)
        if why:
            _leave_out(chosen[name], why, chosen, absent, res)
            dropped = True
    return dropped


def _cycle(chosen: dict[str, Plugin]) -> list[str] | None:
    """One cycle in the needs as a path (a → b → a), or None."""
    done: set[str] = set()

    def visit(name: str, path: list[str]) -> list[str] | None:
        if name in path:
            return path[path.index(name):] + [name]
        if name in done:
            return None
        for dep in sorted(chosen[name].manifest.plugins):
            if dep in chosen and (found := visit(dep, path + [name])):
                return found
        done.add(name)
        return None

    for name in sorted(chosen):
        if found := visit(name, []):
            return found
    return None


def _drop_cycle(chosen: dict[str, Plugin], absent: dict[str, str], res: Resolution) -> bool:
    cycle = _cycle(chosen)
    for name in sorted(set(cycle or [])):
        _leave_out(chosen[name], f"its needs go round in a cycle: {' → '.join(cycle)}", chosen, absent, res)
    return bool(cycle)


def _order(chosen: dict[str, Plugin]) -> list[Plugin]:
    """Dependency order: a plugin comes after the plugins it needs; otherwise name order."""
    waiting = {n: {d for d in p.manifest.plugins if d in chosen} for n, p in chosen.items()}
    ready = [n for n, deps in waiting.items() if not deps]
    heapq.heapify(ready)
    out: list[Plugin] = []
    while ready:
        n = heapq.heappop(ready)
        out.append(chosen[n])
        for other, deps in waiting.items():
            if n in deps:
                deps.discard(n)
                if not deps:
                    heapq.heappush(ready, other)
    return out


def _package_error(package: str, owner: dict[str, str]) -> str | None:
    if package == "keel_engine" or package in sys.stdlib_module_names:
        return f"engine package {package} is a name keel or Python already uses"
    if package in owner:
        return f"engine package {package} is already used by {owner[package]}"
    return None


def _drop_twice_used(order: list[Plugin], chosen: dict[str, Plugin], absent: dict[str, str], res: Resolution) -> bool:
    """Check 5: no two plugins bring the same engine package (the first in dependency order keeps it)."""
    owner: dict[str, str] = {}
    for p in order:
        if not p.manifest.engine:
            continue
        package = p.manifest.engine["package"]
        why = _package_error(package, owner)
        if why:
            _leave_out(p, why, chosen, absent, res)
            return True
        owner[package] = p.name
    return False


def _settle(chosen: dict[str, Plugin], absent: dict[str, str], res: Resolution) -> list[Plugin]:
    """Checks 4 and 5, again and again until nothing more is left out; the rest in dependency order."""
    while True:
        if _drop_unmet(chosen, absent, res) or _drop_cycle(chosen, absent, res):
            continue
        order = _order(chosen)
        if not _drop_twice_used(order, chosen, absent, res):
            return order


# ------------------------------------------------------------------ the whole run

def resolve(only: set[tuple[str, str]] | None = None) -> Resolution:
    """What loads at the next start. `only`: (name, version) pairs to keep; every other plugin is left out.
    Raises PluginError only for a bad KEEL_PLUGINS or installed.json."""
    res = Resolution(mode=state.mode())
    if res.mode == "off":
        return res
    found, absent = _choose(res, state.read_installed()["plugins"], only)
    chosen: dict[str, Plugin] = {}
    for f in found:
        try:
            chosen[f.name] = Plugin(check(f), f.dir, f.source)
        except ManifestError as exc:
            absent[f.name] = LEFT_OUT
            res.problem(f.name, f.version, f.dir, str(exc))
    res.plugins = _settle(chosen, absent, res)
    return res


def read_only(file: str) -> set[tuple[str, str]]:
    """(name, version) pairs from last-good.json, {"plugins": [{"name", "version"}]} (a resolved.json works too).
    A relative name that is not in the current folder is looked for in $KEEL_DATA/plugins."""
    path = Path(file)
    if not path.is_file() and not path.is_absolute() and (state.plugins_dir() / file).is_file():
        path = state.plugins_dir() / file
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {(str(p["name"]), str(p["version"])) for p in data["plugins"]}
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise PluginError(f"--only {file}: not a readable {{\"plugins\": [{{\"name\", \"version\"}}]}} file ({exc})") from exc


def quote(value: str) -> str:
    """POSIX sh single quotes: every character stays as it is; a ' inside becomes '\\''."""
    return "'" + value.replace("'", "'\\''") + "'"


def env_text(env: dict[str, str]) -> str:
    return "".join(f"{k}={quote(v)}\n" for k, v in env.items())


def read_env(folder: Path | None = None) -> dict[str, str]:
    """run/env as {name: value} (what env_text wrote); {} when there is none or it does not read."""
    import shlex

    f = (folder or state.run_dir()) / "env"
    out: dict[str, str] = {}
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            for word in shlex.split(line):
                name, eq, value = word.partition("=")
                if eq and name.isidentifier():
                    out[name] = value
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return out


def write(res: Resolution, folder: Path | None = None) -> Path:
    """Write run/resolved.json and run/env (each whole or not at all)."""
    folder = folder or state.run_dir()
    state.write_atomic(folder / "resolved.json", json.dumps(res.document(), indent=2) + "\n")
    state.write_atomic(folder / "env", env_text(res.env()))
    return folder


def clear(folder: Path | None = None) -> None:
    """Remove run/resolved.json and run/env, so a failed resolve leaves no old plugin set behind."""
    folder = folder or state.run_dir()
    for name in ("resolved.json", "env"):
        (folder / name).unlink(missing_ok=True)
