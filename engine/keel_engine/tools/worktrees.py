"""keel's git worktrees: a side session of the Helper (and, later, a flow of its own) works in a copy of the project on
its own branch, under <root>/.keel/worktrees/<name>. The main folder's checkout, its files and a flow running there are
never touched; the folder is excluded from the main repo's git status (.git/info/exclude), and the Repo page does not
walk into it (it has its own .git file).

    add       a new worktree on a new branch from the main folder's HEAD
    remove    the worktree away (its branch too, unless it is kept for a hand-over)
    changes   every file that differs from the commit the worktree started from: committed on its branch or not
    undo      put one file (or all of them) back as it was at that commit
    commits   the commits on its branch since that commit
"""

from __future__ import annotations

import difflib
import re
import shutil
from pathlib import Path

from . import git

DIR = ".keel/worktrees"
DIFF_MAX = 60_000
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}$")


class WorktreeError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def path_of(root: str, name: str) -> Path:
    if not NAME.match(name):
        raise WorktreeError(f"{name!r} is not a worktree name keel makes.")
    return Path(root) / DIR / name


def add(root: str, name: str, branch: str) -> dict:
    """{path, branch, base}: a worktree of `root` on a new branch from its HEAD."""
    if not git.is_repo(root):
        raise WorktreeError("This project is not a git repository, so it cannot have a worktree.",
                            "A side session needs git: run `git init` and commit once, or use Ask.")
    base = git.head(root)
    if not base:
        raise WorktreeError("This repository has no commit yet, so a worktree has nothing to start from.", "Commit once first.")
    git.exclude(root, [f"{DIR}/"], "keel v2 worktrees (side sessions)")
    path = path_of(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    r = git.git(root, "worktree", "add", "-q", "-b", branch, str(path), base)
    if r.returncode != 0:
        raise WorktreeError(f"git could not make the worktree {name}.", (r.stderr or r.stdout).strip()[-600:])
    return {"path": str(path), "branch": branch, "base": base}


def remove(root: str, path: str, branch: str | None = None) -> None:
    """The worktree away (also when its files changed), then its branch unless `branch` is None (kept for a hand-over)."""
    p = Path(path)
    if p.exists():
        r = git.git(root, "worktree", "remove", "--force", str(p))
        if r.returncode != 0 and p.is_relative_to(Path(root) / DIR):
            shutil.rmtree(p, ignore_errors=True)
    git.git(root, "worktree", "prune")
    if branch:
        git.git(root, "branch", "-D", branch)


def _text(root: str, base: str, rel: str) -> str | None:
    r = git.git(root, "show", f"{base}:{rel}")
    return r.stdout if r.returncode == 0 else None


def _changed(path: str, base: str) -> list[str]:
    tracked = git.git(path, "diff", "--name-only", "--no-renames", base, "--").stdout.splitlines()
    untracked = git.git(path, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return sorted({f for f in tracked + untracked if f.strip() and not git.is_engine_file(f)})


def changes(path: str, base: str) -> list[dict]:
    """{path, status, added, removed, diff} for every file that differs from `base` (the worktree's start)."""
    out = []
    for rel in _changed(path, base):
        f = Path(path) / rel
        before = _text(path, base, rel)
        try:
            now = f.read_text(errors="replace") if f.is_file() else None
        except OSError:
            now = None
        if now == before:
            continue
        status = "added" if before is None else "deleted" if now is None else "modified"
        diff = "".join(difflib.unified_diff((before or "").splitlines(keepends=True), (now or "").splitlines(keepends=True),
                                            fromfile=f"a/{rel}" if before is not None else "/dev/null",
                                            tofile=f"b/{rel}" if now is not None else "/dev/null"))
        plus = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        minus = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        out.append({"path": rel, "status": status, "added": plus, "removed": minus, "diff": diff[:DIFF_MAX]})
    return out


def undo(path: str, base: str, rel: str | None = None) -> list[dict]:
    """Put one file (or every changed one) back as it was at `base`; the next commit on the branch records it."""
    targets = [rel] if rel else _changed(path, base)
    root = Path(path).resolve()
    for t in targets:
        f = (root / t).resolve()
        if not f.is_relative_to(root):
            raise WorktreeError(f"{t} is outside this worktree.")
        if git.git(path, "cat-file", "-e", f"{base}:{t}").returncode == 0:
            git.git(path, "checkout", base, "--", t)
        elif f.is_file():
            f.unlink()
    return changes(path, base)


def commits(path: str, base: str) -> list[dict]:
    """[{sha, subject}] on the worktree's branch since `base`, oldest first."""
    r = git.git(path, "log", "--reverse", "--format=%H%x1f%s", f"{base}..HEAD")
    return [dict(zip(("sha", "subject"), ln.split("\x1f", 1))) for ln in r.stdout.splitlines() if "\x1f" in ln]
