"""Installing a plugin from a .kplug file into the store, and turning plugins on or off (contract section 4).

    <name>-<version>.kplug   a tar.gz of the plugin folder, with no top folder
      1. every member: no absolute path, no '..', no link, no device
      2. keel-plugin.yml at the top is a valid schema 1 manifest
      3. unpack into store/.install-<random>/ (tarfile's "data" filter too); check the parts and files.sha256
      4. rename it to store/<name>/<version>/, then write the installed.json entry
Installing only unpacks files: no plugin code runs until keel starts again.
"""

from __future__ import annotations

import gzip
import os
import shutil
import tarfile
import tempfile
import uuid
import zlib
from pathlib import Path, PurePosixPath

from ..addons import NAME
from . import PluginError, manifest, resolver, state
from .manifest import Manifest

MAX_MANIFEST = 1 << 20


def _member_path(m: tarfile.TarInfo) -> PurePosixPath:
    """A member's path inside the plugin folder, after the checks: refuses absolute paths, '..', links and devices."""
    p = PurePosixPath(m.name)
    if p.is_absolute() or m.name.startswith("\\"):
        raise PluginError(f"refused: '{m.name}' is an absolute path")
    if ".." in p.parts:
        raise PluginError(f"refused: '{m.name}' goes outside the plugin folder ('..')")
    if m.issym() or m.islnk():
        raise PluginError(f"refused: '{m.name}' is a link")
    if not (m.isfile() or m.isdir()):
        raise PluginError(f"refused: '{m.name}' is a device or another special file")
    return p


def _read_manifest(tar: tarfile.TarFile, members: list[tarfile.TarInfo]) -> Manifest:
    top = [m for m in members if m.isfile() and _member_path(m).as_posix() == manifest.MANIFEST]
    if not top:
        raise PluginError(f"there is no {manifest.MANIFEST} at the top of the package (pack the plugin folder's "
                          "content, without a top folder)")
    if top[-1].size > MAX_MANIFEST:
        raise PluginError(f"{manifest.MANIFEST} is too big")
    f = tar.extractfile(top[-1])
    try:
        return manifest.parse(f.read().decode("utf-8") if f else "")
    except UnicodeDecodeError as exc:
        raise PluginError(f"{manifest.MANIFEST} is not UTF-8 text") from exc


def _check_unpacked(folder: Path, m: Manifest) -> None:
    """The unpacked files: the same manifest, every part it names, every files.sha256 hash."""
    if manifest.read(folder) != m:
        raise PluginError(f"{manifest.MANIFEST} changed while unpacking")
    for why in (manifest.missing_part(m, folder), manifest.check_sums(folder)):
        if why:
            raise PluginError(f"refused: {why}")


def _move_into_place(tmp: Path, target: Path) -> None:
    """Rename the unpacked folder to store/<name>/<version>/; an old copy (--force) is moved aside, then removed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    old = None
    if target.exists():
        old = state.store_dir() / f".replaced-{uuid.uuid4().hex}"
        os.rename(target, old)
    os.rename(tmp, target)
    if old:
        shutil.rmtree(old, ignore_errors=True)


def _unpack(tar: tarfile.TarFile, members: list[tarfile.TarInfo], m: Manifest, target: Path) -> None:
    store = state.store_dir()
    store.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".install-", dir=store))
    try:
        tar.extractall(tmp, members=members, filter="data")
        os.chmod(tmp, 0o755)
        _check_unpacked(tmp, m)
        _move_into_place(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def install(file: str | Path, *, on: bool = True, force: bool = False) -> resolver.Plugin:
    """Unpack a .kplug into store/<name>/<version>/ and add its installed.json entry (source "file")."""
    path = Path(file)
    if not path.is_file():
        raise PluginError(f"{file} is not a file")
    data = state.read_installed()   # a broken installed.json stops the install before anything is unpacked
    sha = manifest.file_sha256(path)
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                _member_path(member)
            m = _read_manifest(tar, members)
            target = state.store_dir() / m.name / m.version
            if target.exists() and not force:
                raise PluginError(f"{m.name} {m.version} is already installed in {target} (--force replaces it)")
            _unpack(tar, members, m, target)
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise PluginError(f"{path.name} is not a .kplug (a tar.gz of the plugin folder): {exc}") from exc
    data["plugins"][m.name] = {"version": m.version, "on": on, "source": "file", "sha256": sha,
                               "installed_at": state.now(), "by": "cli"}
    state.write_installed(data)
    return resolver.Plugin(m, target, "file")


def set_on(name: str, on: bool) -> None:
    """Turn a plugin on or off in installed.json (it takes effect at the next start)."""
    if not NAME.match(name):
        raise PluginError(f"'{name}' is not a plugin name")
    data = state.read_installed()
    if name not in data["plugins"] and not (state.image_root() / name).is_dir():
        raise PluginError(f"there is no plugin {name}, neither installed nor in the image")
    entry = data["plugins"].setdefault(name, {})
    if not isinstance(entry, dict):
        raise PluginError(f"the installed.json entry of {name} is not an object")
    entry["on"] = on
    state.write_installed(data)
