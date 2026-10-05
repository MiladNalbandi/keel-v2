"""Small git helpers. Synchronous: callers in async code wrap them in asyncio.to_thread."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# keel's own engine files in a project: written by the engine, never staged or guarded.
ENGINE_FILES = (".keel/ladder.json", ".keel/agents/")


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


def fingerprint(root: str, rel: str) -> str:
    """A cheap content id for a working-tree path ("-" when it does not exist)."""
    p = Path(root) / rel
    if not p.exists():
        return "-"
    if p.is_dir():
        return "dir"
    import hashlib
    return hashlib.sha1(p.read_bytes()).hexdigest()


def snapshot(root: str) -> dict[str, str]:
    """{path: fingerprint} of every file that is already changed before a flow starts (the user's own work)."""
    return {rel: fingerprint(root, rel) for rel in dirty(root)}


def exclude_engine_files(root: str) -> None:
    """keel's own state files never show up as changes: add them to .git/info/exclude (local, not committed)."""
    exclude(root, list(ENGINE_FILES), "keel v2 engine files")


def exclude(root: str, patterns: list[str], comment: str) -> bool:
    """Add patterns to the repo's .git/info/exclude (local, never committed). False when root is not a repo."""
    r = git(root, "rev-parse", "--git-path", "info/exclude")
    if r.returncode != 0 or not r.stdout.strip():
        return False
    f = Path(root) / r.stdout.strip()
    f.parent.mkdir(parents=True, exist_ok=True)
    have = f.read_text().splitlines() if f.exists() else []
    add = [p for p in patterns if p not in have]
    if add:
        with f.open("a") as out:
            out.write(("\n" if have and have[-1] else "") + f"# {comment}\n" + "\n".join(add) + "\n")
    return True


def worktree_tree(root: str) -> str | None:
    """The git tree id of the working files as `git add -A` would commit them (keel's engine files left out), without
    touching the real index: a check that ran on uncommitted files is about the commit made from them."""
    r = git(root, "rev-parse", "--git-path", "index")
    if r.returncode != 0:
        return None
    index = Path(root) / r.stdout.strip()
    with tempfile.TemporaryDirectory(prefix="keel-tree-") as d:
        tmp = os.path.join(d, "index")
        if index.is_file():
            shutil.copyfile(index, tmp)          # keeps git's stat cache, so a big repo is not hashed again
        env = {**os.environ, "GIT_INDEX_FILE": tmp}
        excludes = [f":!{p.rstrip('/')}" for p in ENGINE_FILES]
        if git(root, "add", "-A", "--", ".", *excludes, env=env).returncode != 0:
            return None
        out = git(root, "write-tree", env=env)
    return out.stdout.strip() or None if out.returncode == 0 else None


def head_tree(root: str) -> str | None:
    r = git(root, "rev-parse", "HEAD^{tree}")
    return r.stdout.strip() if r.returncode == 0 else None


def tracked_in_head(root: str, rel: str) -> bool:
    return git(root, "cat-file", "-e", f"HEAD:{rel}").returncode == 0
