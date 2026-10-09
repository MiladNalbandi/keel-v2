"""Install, update, roll back, remove, turn on or off, and what waits for a restart (docs/plugins/13-step4-contract.md §5).

Install (all or nothing; nothing runs at install time):
    1. choose the newest version that fits (plugin SDK 1, keel's version, the plugins it needs)
    2. refuse: a revoked version; an old (expired) catalog; an unverified publisher's web or code plugin while
       "allow unverified publishers" is off; a needed plugin that is not in the same catalog; Python libraries
    3. download each package to downloads/ (never above the catalog's size, at most 200 MB), check its sha256 and
       its .minisig with the publisher's keys
    4. check each manifest's name, version and permissions against the catalog, then store.install it
       (source "marketplace"); the store keeps the current and the previous version, older ones go
    5. events plugin.install.started, plugin.install.done, plugin.install.failed {name, version, why}

Update is an install of a newer version, refused when it asks for more permissions (a person approves that first).
Roll back goes to the kept version. Remove refuses an image plugin (turn it off instead) and a plugin another one
needs; data=delete removes $KEEL_DATA/plugins/data/<name>/ only (its tables stay).
"""

from __future__ import annotations

import fcntl
import json
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .. import config
from ..addons import NAME, satisfies
from ..pluginhost import PluginError, resolver, state, store
from ..pluginhost.manifest import Manifest, file_sha256
from . import MarketError, catalog, local, sources
from .catalog import Entry, Index, Version, newer
from .signing import SignatureError, verify

Notify = Callable[[str, dict], None]
KEEP, DELETE = "keep", "delete"
PARTS = ("engine", "api", "web", "content", "migrations")
LEVELS = {"workspace": ["none", "read", "write"], "agent_tools": ["none", "read", "act"]}
CHECKS = [
    "the catalog's signature matches the source's key",
    "the version is not revoked and the catalog is not old",
    "the download is not bigger than the catalog says, and its sha256 is the catalog's",
    "the file's signature matches a key of its publisher",
    "the manifest's name, version and permissions are the catalog's",
    "every file is inside the plugin folder: no links, no absolute paths, no '..'",
    "every file matches files.sha256 (again at every start)",
    "nothing runs at install: the plugin loads at the next start",
]

_lock = threading.RLock()
_held = threading.local()


@contextmanager
def _locked():
    """One change at a time, in the engine and in a `keel-engine plugins` command beside it (a file lock). The thread
    that holds it may take it again."""
    with _lock:
        if getattr(_held, "depth", 0):
            _held.depth += 1
            try:
                yield
            finally:
                _held.depth -= 1
            return
        state.plugins_dir().mkdir(parents=True, exist_ok=True)
        with open(state.plugins_dir() / ".lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            _held.depth = 1
            try:
                yield
            finally:
                _held.depth = 0
                fcntl.flock(fh, fcntl.LOCK_UN)


def _plain(exc: Exception) -> MarketError:
    """A plugin host or file error as a refusal in plain words."""
    return exc if isinstance(exc, MarketError) else MarketError(400 if isinstance(exc, PluginError) else 500, str(exc))


# ------------------------------------------------------------------ the plan

@dataclass
class Step:
    index: Index
    entry: Entry
    version: Version
    needed_by: str | None = None

    @property
    def name(self) -> str:
        return self.entry.name

    def view(self) -> dict:
        pub = self.index.publishers[self.entry.publisher]
        return {"name": self.name, "version": self.version.version, "title": self.entry.title, "trust": self.entry.trust,
                "publisher": self.entry.publisher, "publisher_title": pub.title, "verified": pub.verified,
                "permissions": self.version.permissions, "size": self.version.size, "needed_by": self.needed_by,
                "source": self.index.source}


def _refuse_unverified(index: Index, entry: Entry) -> None:
    pub = index.publishers[entry.publisher]
    if pub.verified or entry.trust == "content" or sources.read_rules()["allow_unverified"]:
        return
    what = "adds pages to keel" if entry.trust == "web" else "runs code inside keel"
    raise MarketError(409, f"{entry.title} is from {pub.title}, an unverified publisher, and it {what}.",
                      "keel installs it only when the rule 'Allow unverified publishers' is on (Control › Plugins › "
                      "Sources and rules).")


def _pick(index: Index, entry: Entry, have: dict, version: str | None, spec: str = "") -> Version:
    if version:
        v = entry.version(version)
        if v is None:
            raise MarketError(404, f"{entry.title} has no version {version} in the catalog {index.source}.",
                              f"Its versions: {', '.join(x.version for x in entry.versions)}.")
        why = catalog.why_not(index, entry, v, have)
        if why:
            raise MarketError(409, f"{entry.title} {version} cannot be installed: {why}.")
        if not satisfies(v.version, spec):
            raise MarketError(409, f"{entry.name} {spec} is needed, and {version} is not in that range.")
        return v
    v = catalog.newest_fitting(index, entry, have, spec)
    if v is None:
        newest = next((x for x in entry.versions if satisfies(x.version, spec)), entry.versions[0])
        raise MarketError(409, f"No version of {entry.title} fits this keel: {catalog.why_not(index, entry, newest, have)}.")
    return v


def _steps(name: str, version: str | None, have: dict) -> tuple[list[Step], list[str]]:
    """What to install for this plugin, needed plugins first (all from the same catalog), and the needed plugins keel
    has but are off (they are turned on)."""
    if not isinstance(name, str) or not NAME.match(name):
        raise MarketError(400, f"'{name}' is not a plugin name.")
    found = catalog.lookup(name)
    if found is None:
        raise MarketError(404, f"There is no plugin {name} in the catalogs keel reads.",
                          "Refresh the catalogs (Control › Plugins › Sources and rules), or check the name.")
    index, entry = found
    if index.expired():
        raise MarketError(409, f"The catalog {index.source} is old: it expired at {index.expires}.",
                          "Refresh it (Control › Plugins › Sources and rules › Refresh). keel installs nothing from an "
                          "old catalog.")
    steps: list[Step] = []
    turn_on: list[str] = []

    def add(e: Entry, ver: str | None, spec: str, needed_by: str | None, path: tuple[str, ...]) -> None:
        _refuse_unverified(index, e)
        v = _pick(index, e, have, ver, spec)
        for dep, dep_spec in sorted(v.plugins.items()):
            mine = have.get(dep)
            if mine and satisfies(mine.version, dep_spec):
                if not mine.on and dep not in turn_on:
                    turn_on.append(dep)
                continue
            if dep in path or dep == e.name:
                raise MarketError(409, f"The needs go round in a cycle: {' → '.join(path + (e.name, dep))}.")
            planned = next((s for s in steps if s.name == dep), None)
            if planned:
                if not satisfies(planned.version.version, dep_spec):
                    raise MarketError(409, f"Two plugins need different versions of {dep}: {dep_spec} and "
                                           f"{planned.version.version}.")
                continue
            dep_entry = index.plugins.get(dep)
            if dep_entry is None:
                raise MarketError(409, f"{e.title} needs {dep} {dep_spec}".rstrip() +
                                  f", which is not in the catalog {index.source}.")
            add(dep_entry, None, dep_spec, e.name, path + (e.name,))
        steps.append(Step(index, e, v, needed_by))

    add(entry, version, "", None, ())
    return steps, turn_on


def plan(name: str, version: str | None = None) -> dict:
    """What an install of this plugin does: {name, version, title, source, install: [step], turn_on: [name], checks}.
    Raises MarketError when keel would refuse it."""
    have = local.have()
    steps, turn_on = _steps(name, version, have)
    _refuse_had(steps[-1], have)
    return _plan_view(steps, turn_on)


def _plan_view(steps: list[Step], turn_on: list[str]) -> dict:
    main = steps[-1]
    return {"name": main.name, "version": main.version.version, "title": main.entry.title, "source": main.index.source,
            "install": [s.view() for s in steps], "turn_on": list(turn_on), "checks": list(CHECKS)}


def _refuse_had(main: Step, have: dict) -> None:
    mine = have.get(main.name)
    if mine is None:
        return
    if mine.version == main.version.version:
        where = "comes with keel's image" if mine.source == local.IMAGE else "is installed already"
        raise MarketError(409, f"{main.entry.title} {mine.version} {where}.")
    raise MarketError(409, f"{main.entry.title} {mine.version} is installed already.",
                      "Update it instead (Control › Plugins › Installed).", installed=mine.version)


# ------------------------------------------------------------------ download and check

def downloads_dir() -> Path:
    return state.plugins_dir() / "downloads"


def _fetch(step: Step, folder: Path) -> Path:
    v = step.version
    file = folder / f"{step.name}-{v.version}.kplug"
    catalog.download(v.url, limit=min(v.size, catalog.MAX_PACKAGE), to=file)
    if file_sha256(file) != v.sha256:
        raise MarketError(502, f"The download of {step.name} {v.version} does not match the catalog's sha256.",
                          "keel deleted it. The file may have been changed: try again later, or tell the publisher.")
    sig = catalog.download(v.url + ".minisig", limit=catalog.MAX_SIG)
    try:
        verify(file, sig.decode("utf-8"), step.index.publishers[step.entry.publisher].keys)
    except (SignatureError, UnicodeDecodeError) as exc:
        raise MarketError(502, f"The signature of {step.name} {v.version} does not match its publisher's key: {exc}.",
                          "keel deleted the download and installed nothing.") from None
    return file


def python_libraries(m: Manifest, files: list[str]) -> list[str]:
    """The extra Python libraries a package asks for: in its manifest, or an engine/requirements.lock (or .txt)."""
    out = list(m.python)
    if m.engine:
        folder = PurePosixPath(m.engine["path"])
        out += [f for f in files if PurePosixPath(f).parent == folder
                and PurePosixPath(f).name in ("requirements.lock", "requirements.txt")]
    return out


def _refuse_python(m: Manifest, files: list[str]) -> None:
    libs = python_libraries(m, files)
    if libs:
        raise MarketError(409, f"{m.title} {m.version} asks for extra Python libraries ({', '.join(libs)}), and this "
                               "keel does not install Python libraries for plugins yet.",
                          "Ask its publisher for a version without them.", libraries=libs)


def _norm(value):
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return sorted((_norm(v) for v in value), key=lambda x: json.dumps(x, sort_keys=True))
    return value


def _check_package(file: Path, step: Step) -> Manifest:
    try:
        m, files = store.read_package(file)
    except PluginError as exc:
        raise MarketError(502, f"The package of {step.name} is refused: {exc}.") from None
    v = step.version
    if (m.name, m.version) != (step.name, v.version):
        raise MarketError(502, f"The package says it is {m.name} {m.version}, the catalog says {step.name} {v.version}.",
                          "keel installed nothing. Tell the publisher.")
    if _norm(m.permissions) != _norm(v.permissions):
        raise MarketError(502, f"{m.title} {m.version} asks for other permissions than the catalog shows.",
                          "keel installs only what you were shown. Tell the publisher.",
                          manifest=m.permissions, catalog=v.permissions)
    _refuse_python(m, files)
    return m


# ------------------------------------------------------------------ permissions

def _permission_list(value) -> list:
    return value if isinstance(value, list) else [] if value in (None, False, "", {}) else [value]


def more_permissions(old: dict, new: dict) -> list[str]:
    """What the new permissions add, in words ("+ secrets: gitlab", "+ workspace: write (was read)")."""
    out: list[str] = []
    for key, value in sorted((new or {}).items()):
        before = (old or {}).get(key)
        if isinstance(value, list):
            out += [f"+ {key}: {x}" for x in value if x not in _permission_list(before)]
        elif key in LEVELS and value in LEVELS[key] and (before or "none") in LEVELS[key]:
            if LEVELS[key].index(value) > LEVELS[key].index(before or "none"):
                out.append(f"+ {key}: {value}" + (f" (was {before})" if before else ""))
        elif value not in (None, False, "", [], {}) and value != before:
            out.append(f"+ {key}: {value}" + (f" (was {before})" if before not in (None, False, "") else ""))
    return out


def _permissions_of(h: local.Had) -> dict:
    perms = h.entry.get("permissions") if h.source != local.IMAGE else None
    if isinstance(perms, dict):
        return perms
    return h.manifest.permissions if h.manifest else {}


# ------------------------------------------------------------------ changing the store

def _previous(h: local.Had | None) -> dict | None:
    """What the installed.json entry keeps for roll back: the version before this one."""
    if h is None:
        return None
    if h.source == local.IMAGE:
        return {"version": h.version, "source": local.IMAGE}
    return {k: v for k, v in h.entry.items() if k not in ("previous", "on")}


def _prune(name: str, keep: set[str]) -> None:
    """Keep the current and the previous version of a plugin in the store; older ones go."""
    for d in sorted((state.store_dir() / name).glob("*")):
        if d.is_dir() and not d.name.startswith(".") and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)


def _commit(checked: list[tuple[Step, Path]], turn_on: list[str], by: str) -> list[dict]:
    """store.install each package (needed plugins first) and turn on the needed ones that are off. On any failure
    installed.json and the store are put back as they were."""
    f = state.installed_path()
    before = f.read_text(encoding="utf-8") if f.is_file() else None
    have = local.have()
    created: list[Path] = []
    done: list[dict] = []
    try:
        for step, file in checked:
            old = have.get(step.name)
            target = state.store_dir() / step.name / step.version.version
            existed = target.exists()
            prev = _previous(old)
            meta = {"by": by, "catalog": step.index.source, "publisher": step.entry.publisher, "trust": step.entry.trust,
                    "permissions": step.version.permissions, "previous": prev}
            store.install(file, on=old.on if old else True, force=True, source=local.MARKETPLACE, meta=meta)
            if not existed:
                created.append(target)
            kept = prev["version"] if prev and prev.get("source") != local.IMAGE else None
            done.append({"name": step.name, "version": step.version.version, "from": old.version if old else None,
                         "keep": kept})
        for n in turn_on:
            store.set_on(n, True)
    except BaseException:
        if before is None:
            f.unlink(missing_ok=True)
        else:
            state.write_atomic(f, before)
        for d in created:
            shutil.rmtree(d, ignore_errors=True)
        raise
    for d in done:
        _prune(d["name"], {d["version"]} | ({d.pop("keep")} if d.get("keep") else set()))
        d.pop("keep", None)
    return done


def _apply(steps: list[Step], turn_on: list[str], *, by: str, notify: Notify | None, update: bool) -> dict:
    main = steps[-1]
    head = {"name": main.name, "version": main.version.version}
    if notify:
        notify("plugin.install.started", {**head, "update": update, "plugins": [s.name for s in steps]})
    downloads_dir().mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="get-", dir=downloads_dir()))
    try:
        files = [(s, _fetch(s, folder)) for s in steps]
        for s, file in files:
            _check_package(file, s)
        installed = _commit(files, turn_on, by)
    except (MarketError, PluginError, OSError) as exc:
        err = _plain(exc)
        if notify:
            notify("plugin.install.failed", {**head, "why": str(err)})
        raise err from None
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if notify:
        notify("plugin.install.done", {**head, "update": update, "installed": installed, "turned_on": list(turn_on)})
    return {**head, "title": main.entry.title, "installed": installed, "turned_on": list(turn_on),
            "pending_restart": pending_restart()}


def install(name: str, version: str | None = None, *, by: str = "person", notify: Notify | None = None) -> dict:
    """Install a plugin from the catalogs (and the plugins it needs). It loads at the next start."""
    with _locked():
        have = local.have()
        try:
            steps, turn_on = _steps(name, version, have)
            _refuse_had(steps[-1], have)
        except MarketError as exc:
            if notify:
                notify("plugin.install.failed", {"name": name, "version": version, "why": str(exc)})
            raise
        return _apply(steps, turn_on, by=by, notify=notify, update=False)


def update(name: str, version: str | None = None, *, allow_more_permissions: bool = False, by: str = "person",
           notify: Notify | None = None) -> dict:
    """Install a newer version. Refused when it asks for more permissions, unless a person approved them
    (allow_more_permissions). The version before stays for roll back."""
    with _locked():
        have = local.have()
        mine = have.get(name)
        try:
            if mine is None:
                raise MarketError(404, f"{name} is not installed.", "Install it first.")
            steps, turn_on = _steps(name, version, have)
            main = steps[-1]
            if not newer(main.version.version, mine.version):
                raise MarketError(409, f"{main.entry.title} {mine.version} is the newest version that fits this keel."
                                  if not version else f"{version} is not newer than {mine.version}.",
                                  "" if not version else "Roll back goes to the version before.")
            more = more_permissions(_permissions_of(mine), main.version.permissions)
            if more and not allow_more_permissions:
                raise MarketError(409, f"{main.entry.title} {main.version.version} asks for more permissions than "
                                       f"{mine.version}: {'; '.join(more)}.",
                                  "A person approves the new permissions first (an install request in keel's Inbox).",
                                  more=more, installed=mine.version, version=main.version.version)
        except MarketError as exc:
            if notify:
                notify("plugin.install.failed", {"name": name, "version": version, "why": str(exc)})
            raise
        return _apply(steps, turn_on, by=by, notify=notify, update=True)


def rollback(name: str, *, by: str = "person") -> dict:
    """Go back to the kept version (the image's, when the newer one came from the marketplace over it)."""
    with _locked():
        have = local.have()
        mine = have.get(name)
        data = state.read_installed()
        entry = data["plugins"].get(name) if isinstance(data["plugins"].get(name), dict) else None
        prev = (entry or {}).get("previous")
        if mine is None:
            raise MarketError(404, f"{name} is not installed.")
        if not isinstance(prev, dict) or not prev.get("version"):
            raise MarketError(409, f"{name} has no earlier version to go back to.")
        found = catalog.lookup(name)
        why = found[0].why_revoked(name, prev["version"]) if found else None
        if why:
            raise MarketError(409, f"{mine.title} {prev['version']} is revoked: {why}.", "keel does not go back to it.")
        current = _previous(mine)
        if prev.get("source") == local.IMAGE:
            if mine.image_version != prev["version"]:
                raise MarketError(409, f"keel's image no longer has {name} {prev['version']}.")
            new = {"on": mine.on, "previous": current}
        else:
            if not (state.store_dir() / name / prev["version"]).is_dir():
                raise MarketError(409, f"The kept version {prev['version']} of {name} is missing from the store.")
            new = {**prev, "on": mine.on, "by": by, "previous": current}
        data["plugins"][name] = new
        state.write_installed(data)
        return {"name": name, "version": prev["version"], "from": mine.version, "pending_restart": pending_restart()}


def _dependents(have: dict, names: set[str]) -> list[str]:
    """The plugins that are on and need one of these names, and the ones that need those, and so on."""
    out: list[str] = []
    grow = True
    while grow:
        grow = False
        for n, h in sorted(have.items()):
            if n not in names and n not in out and h.on and set(h.needs) & (names | set(out)):
                out.append(n)
                grow = True
    return out


def remove(name: str, data: str = KEEP, *, by: str = "person") -> dict:
    """Remove an installed plugin (a marketplace copy over an image plugin goes back to the image's version)."""
    if data not in (KEEP, DELETE):
        raise MarketError(400, f"data must be {KEEP} or {DELETE}, not {data!r}.")
    with _locked():
        have = local.have()
        mine = have.get(name)
        if mine is None:
            raise MarketError(404, f"{name} is not installed.")
        if mine.source == local.IMAGE:
            raise MarketError(409, f"{mine.title} came with keel's image: it cannot be removed, only turned off.",
                              "Turn it off in Control › Plugins › Installed.")
        back = mine.image_version
        needed_by = [n for n, h in sorted(have.items()) if n != name and h.on and name in h.needs
                     and not (back and satisfies(back, h.needs[name]))]
        if needed_by:
            titles = ", ".join(have[n].title for n in needed_by)
            raise MarketError(409, f"{mine.title} is needed by {titles}.", "Remove or turn off those first.",
                              needed_by=needed_by)
        entries = state.read_installed()
        if back:
            entries["plugins"][name] = {"on": mine.on}
        else:
            entries["plugins"].pop(name, None)
        state.write_installed(entries)
        shutil.rmtree(state.store_dir() / name, ignore_errors=True)
        if data == DELETE:
            shutil.rmtree(state.plugins_dir() / "data" / name, ignore_errors=True)
        return {"name": name, "removed": mine.version, "data": data, "back_to_image": back,
                "pending_restart": pending_restart()}


def set_on(name: str, on: bool, *, by: str = "person") -> dict:
    """Turn a plugin on or off for the next start. Off: the plugins that need it go off too. On: the plugins it
    needs that are off go on too. `also` lists them."""
    with _locked():
        have = local.have()
        mine = have.get(name)
        if mine is None:
            raise MarketError(404, f"There is no plugin {name}, neither installed nor in the image.")
        if on:
            also, todo = [], list(mine.needs)
            while todo:
                dep = todo.pop(0)
                if dep in have and not have[dep].on and dep not in also:
                    also.append(dep)
                    todo += list(have[dep].needs)
        else:
            also = _dependents(have, {name})
        try:
            for n in [name, *also]:
                store.set_on(n, on)
        except PluginError as exc:
            raise _plain(exc) from None
        return {"name": name, "on": on, "also": also, "pending_restart": pending_restart()}


def install_file(path: str, *, by: str = "person", force: bool = False) -> dict:
    """Install a .kplug from keel's data folder (unsigned: source "file"). It loads at the next start."""
    home = config.data_dir().resolve()
    file = Path(path if Path(path).is_absolute() else home / path).resolve()
    if not file.is_relative_to(home):
        raise MarketError(400, f"keel installs a file from its data folder only ({home}).",
                          f"Copy the .kplug into {home} first.")
    if not file.is_file():
        raise MarketError(404, f"There is no file {file}.")
    with _locked():
        try:
            m, files = store.read_package(file)
            _refuse_python(m, files)
            have = local.have()
            old = have.get(m.name)
            if old and old.version == m.version and not force:
                raise MarketError(409, f"{m.title} {m.version} is installed already.",
                                  "Install it again with force to replace it.")
            prev = _previous(old) if old and old.version != m.version else (old.entry.get("previous") if old else None)
            store.install(file, on=old.on if old else True, force=True, source=local.FILE,
                          meta={"by": by, "permissions": m.permissions, "previous": prev})
        except (PluginError, OSError) as exc:
            raise _plain(exc) from None
        if prev and prev.get("source") != local.IMAGE:
            _prune(m.name, {m.version, prev["version"]})
        return {"name": m.name, "version": m.version, "title": m.title,
                "installed": [{"name": m.name, "version": m.version, "from": old.version if old else None}],
                "source": local.FILE, "pending_restart": pending_restart()}


# ------------------------------------------------------------------ what waits for a restart, and the installed list

def _resolve() -> resolver.Resolution | None:
    try:
        return resolver.resolve()
    except PluginError:
        return None


def pending_restart(res: resolver.Resolution | None = None) -> dict:
    """{pending, changes: [{name, now, next}]}: what loads at the next start differs from what keel's last start
    loaded (run/resolved.json): another plugin or another version."""
    res = res or _resolve()
    if res is None:
        return {"pending": False, "changes": [], "problem": "the plugin choices (installed.json) do not read"}
    now = local.loaded() or {}
    nxt = {p.name: p.version for p in res.plugins}
    changes = [{"name": n, "now": now.get(n), "next": nxt.get(n)} for n in sorted(set(now) | set(nxt))
               if now.get(n) != nxt.get(n)]
    return {"pending": bool(changes), "changes": changes}


def installed_view() -> dict:
    """The installed and image plugins for Control › Plugins › Installed, plus what waits for a restart."""
    have = local.have()
    res = _resolve()
    loaded = local.loaded() or {}
    nxt = {p.name: p.version for p in res.plugins} if res else {}
    problems: dict[str, list[str]] = {}
    for p in res.problems if res else []:
        problems.setdefault(p["name"], []).append(p["error"])
    if res and res.mode != "on":
        for name, h in have.items():
            if h.on and name not in nxt and not problems.get(name):
                problems[name] = [f"keel runs with KEEL_PLUGINS={res.mode}, so this plugin waits"]
    out = []
    for name, h in have.items():
        m = h.manifest
        found = catalog.lookup(name)
        hit = catalog.hit(*found, have) if found else {}
        if not h.on:
            status = "off"
        elif nxt.get(name) != h.version:
            status = "left out"          # the next start leaves it out: its problems say why
        elif loaded.get(name) == h.version:
            status = "loaded"
        else:
            status = "restart"           # it loads at the next start
        needed_by = [n for n, x in sorted(have.items()) if n != name and x.on and name in x.needs]
        prev = h.entry.get("previous") if isinstance(h.entry.get("previous"), dict) else None
        out.append({
            "name": name, "title": h.title, "version": h.version, "loaded": loaded.get(name),
            "parts": [k for k in PARTS if m and getattr(m, k)], "from": h.source, "on": h.on, "status": status,
            "problems": problems.get(name, []), "revoked": hit.get("revoked"), "update": hit.get("update"),
            "previous": prev.get("version") if prev else None, "image_version": h.image_version,
            "can_remove": h.source != local.IMAGE and not needed_by, "needed_by": needed_by, "needs": h.needs,
            "trust": h.entry.get("trust") or hit.get("trust"), "publisher": h.entry.get("publisher") or (m.publisher if m else ""),
            "catalog": h.entry.get("catalog"), "permissions": _permissions_of(h), "per_project": bool(m and m.per_project),
            "installed_at": h.entry.get("installed_at"),
        })
    for name, version in sorted(loaded.items()):
        if name not in have:
            out.append({"name": name, "title": name, "version": version, "loaded": version, "parts": [], "from": None,
                        "on": False, "status": "removed", "problems": [], "revoked": None, "update": None,
                        "previous": None, "image_version": None, "can_remove": False, "needed_by": [], "needs": {},
                        "trust": None, "publisher": "", "catalog": None, "permissions": {}, "per_project": False,
                        "installed_at": None})
    return {"plugins": out, "pending_restart": pending_restart(res), "problems": list(res.problems) if res else [],
            "mode": res.mode if res else None}
