"""The .kplug file: a tar.gz of a plugin folder's content, with no top folder, and files.sha256 inside
(docs/plugins/02-plugin-package.md, 2.2; the package step of scripts/build-plugin.sh).

pack() makes one from an already-built plugin folder; unpack() opens one with keel's member checks (no absolute
path, no '..', no link, no device) into a folder, for lint and index.
"""

from __future__ import annotations

import gzip
import io
import os
import shutil
import tarfile
import zlib
from pathlib import Path, PurePosixPath

from . import PluginToolError
from .manifest import MANIFEST, SUMS, Manifest, missing_parts, read, sums_text

MAX_UNPACKED = 1 << 30   # 1 GB: a package bigger than this unpacked is refused


class PackageError(PluginToolError):
    """A plugin folder cannot be packed, or a .kplug cannot be opened safely."""


def skipped(name: str) -> bool:
    """Never packed: hidden files and folders (.git, .gitignore, .DS_Store …), caches, packages and signatures."""
    return name.startswith(".") or name == "__pycache__" or name.endswith((".pyc", ".kplug", ".kplug.minisig"))


def walk(folder: Path, skip: Path | None = None, everything: bool = False) -> tuple[list[str], list[str], list[str]]:
    """(folders, files, links) under folder as relative POSIX paths, sorted; caches are left out unless everything."""
    dirs, files, links = [], [], []
    for root, dirnames, filenames in os.walk(folder, followlinks=False):
        here = Path(root)
        keep = []
        for d in sorted(dirnames):
            p = here / d
            if skipped(d) and not everything:
                continue
            rel = p.relative_to(folder).as_posix()
            if p.is_symlink():
                links.append(rel)
            else:
                dirs.append(rel)
                keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            p = here / f
            if (skipped(f) and not everything) or (skip is not None and p.resolve() == skip):
                continue
            rel = p.relative_to(folder).as_posix()
            if p.is_symlink():
                links.append(rel)
            elif p.is_file():
                files.append(rel)
            else:
                links.append(rel)   # a device, a pipe: not a plain file either
    key = lambda s: s.encode("utf-8")
    return sorted(dirs, key=key), sorted(files, key=key), sorted(links, key=key)


def _info(name: str, size: int, is_dir: bool, mtime: int) -> tarfile.TarInfo:
    ti = tarfile.TarInfo(name)
    ti.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
    ti.mode = 0o755 if is_dir else 0o644
    ti.size = 0 if is_dir else size
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mtime = mtime
    return ti


def pack(folder: Path, out_dir: Path) -> tuple[Path, Manifest, int]:
    """Pack an already-built plugin folder into <out_dir>/<name>-<version>.kplug: (file, manifest, file count).

    The folder is not changed: files.sha256 is made fresh for the package. The same files give the same bytes
    (sorted members, owner 0, fixed modes, the time SOURCE_DATE_EPOCH or 0)."""
    folder = folder.resolve()
    if not folder.is_dir():
        raise PackageError(f"{folder} is not a folder")
    if not (folder / MANIFEST).is_file():
        raise PackageError(f"there is no {MANIFEST} in {folder}: pack the built plugin folder (its top level)")
    m = read(folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = (out_dir / f"{m.name}-{m.version}.kplug").resolve()
    dirs, files, links = walk(folder, skip=target)
    if links:
        raise PackageError(f"a plugin may not hold links or special files: {', '.join(links[:3])}")
    missing = missing_parts(m, folder)
    if missing:
        raise PackageError("; ".join(missing))
    files = [f for f in files if f != SUMS]
    sums = sums_text(folder, files).encode("utf-8")
    mtime = int(os.environ.get("SOURCE_DATE_EPOCH") or 0)
    members = sorted(dirs + files + [SUMS], key=lambda s: s.encode("utf-8"))
    tmp = target.with_name(f".{target.name}.part")
    try:
        with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, \
                tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            dirset = set(dirs)
            for name in members:
                if name == SUMS:
                    tar.addfile(_info(name, len(sums), False, mtime), io.BytesIO(sums))
                elif name in dirset:
                    tar.addfile(_info(name, 0, True, mtime))
                else:
                    p = folder / name
                    with open(p, "rb") as fh:
                        tar.addfile(_info(name, p.stat().st_size, False, mtime), fh)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target, m, len(files) + 1


def member_problem(m: tarfile.TarInfo) -> str | None:
    """Why keel would refuse this member, or None."""
    p = PurePosixPath(m.name)
    if p.is_absolute() or m.name.startswith("\\"):
        return f"'{m.name}' is an absolute path"
    if ".." in p.parts:
        return f"'{m.name}' goes outside the plugin folder ('..')"
    if m.issym() or m.islnk():
        return f"'{m.name}' is a link"
    if not (m.isfile() or m.isdir()):
        return f"'{m.name}' is a device or another special file"
    return None


def unpack(file: Path, into: Path) -> list[str]:
    """Unpack a .kplug into an empty folder with keel's checks. PackageError when keel would refuse the file."""
    try:
        with tarfile.open(file, "r:gz") as tar:
            members = tar.getmembers()
            problems = [p for m in members if (p := member_problem(m))]
            if problems:
                raise PackageError("keel refuses this package: " + "; ".join(problems[:5]))
            if sum(m.size for m in members) > MAX_UNPACKED:
                raise PackageError("the package is bigger than 1 GB unpacked")
            names = {PurePosixPath(m.name).as_posix() for m in members if m.isfile()}
            if MANIFEST not in names:
                tops = sorted({PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts})
                hint = f" (it has a top folder: {tops[0]}/)" if len(tops) == 1 and members else ""
                raise PackageError(f"there is no {MANIFEST} at the top of the package{hint}: pack the plugin "
                                   "folder's content, without a top folder")
            into.mkdir(parents=True, exist_ok=True)
            if hasattr(tarfile, "data_filter"):
                tar.extractall(into, members=members, filter="data")
            else:   # Python without extraction filters: the members were checked above
                tar.extractall(into, members=members)
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise PackageError(f"{file.name} is not a .kplug (a tar.gz of the plugin folder): {exc}") from None
    return sorted(names)


def remove(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)
