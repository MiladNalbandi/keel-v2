"""Small git helpers. Synchronous: callers in async code wrap them in asyncio.to_thread."""

from __future__ import annotations

import subprocess
from pathlib import Path

# keel's own engine files: written by the mirror, never staged or guarded.
ENGINE_FILES = (".keel/state.json", ".keel/logs/", ".keel/.state.json")


def git(root: str, *args: str, check: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check, env=env)


def is_repo(root: str | None) -> bool:
    if not root or not Path(root).is_dir():
        return False
    return git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def head(root: str) -> str | None:
    r = git(root, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def branch(root: str) -> str | None:
    r = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def is_engine_file(rel: str) -> bool:
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in ENGINE_FILES)


def dirty(root: str) -> dict[str, str]:
    """{path: XY status} for every changed or untracked file (renames report the new path)."""
    r = git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    out: dict[str, str] = {}
    parts = r.stdout.split("\0")
    i = 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if len(item) < 4:
            continue
        xy, path = item[:2], item[3:]
        if "R" in xy or "C" in xy:
            i += 1  # the original path follows; skip it
        if not is_engine_file(path):
            out[path] = xy
    return out


def tracked_in_head(root: str, rel: str) -> bool:
    return git(root, "cat-file", "-e", f"HEAD:{rel}").returncode == 0
