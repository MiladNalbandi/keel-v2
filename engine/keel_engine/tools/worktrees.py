"""keel's git worktrees: a side session of KeelBot, or a flow next to other flows, works in a copy of the project on
its own branch, under <root>/.keel/worktrees/<name>. The main folder's checkout, its files and a flow running there are
never touched; the folder is excluded from the main repo's git status (.git/info/exclude), and the Repo page does not
walk into it (it has its own .git file).

    add       a new worktree on a new branch from the main folder's HEAD (or another start, e.g. the base branch)
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


def add(root: str, name: str, branch: str, start: str | None = None) -> dict:
    """{path, branch, base}: a worktree of `root` on a new branch from `start` (default: its HEAD)."""
    if not git.is_repo(root):
        raise WorktreeError("This project is not a git repository, so it cannot have a worktree.",
                            "It needs git: run `git init` and commit once.")
    r = git.git(root, "rev-parse", "--verify", "--quiet", f"{start or 'HEAD'}^{{commit}}")
    base = r.stdout.strip() if r.returncode == 0 else None
    if not base:
        raise WorktreeError(f"There is no commit {start or 'HEAD'} to start the worktree from.", "Commit once first.")
    if git.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        raise WorktreeError(f"The branch {branch} exists already.", "Pick another name.")
    git.exclude(root, [f"{DIR}/"], "keel v2 worktrees (side sessions)")
    path = path_of(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    r = git.git(root, "worktree", "add", "-q", "-b", branch, str(path), base)
    if r.returncode != 0:
        raise WorktreeError(f"git could not make the worktree {name}.", (r.stderr or r.stdout).strip()[-600:])
    seed_keel_files(root, path)
    return {"path": str(path), "branch": branch, "base": base}


def seed_keel_files(root: str, wt: Path) -> list[str]:
    """layout keel / external: .keel/ is outside git, so a new worktree would have no config, specs, knowledge, ADRs
    or runbook, and a flow there would plan against nothing. Copy them in, with their times kept: keep_keel_files
    brings back only what the flow changed. Symlinked files and keel's own worktrees are left out.
    0.18.64 layout repo too, for the files the checkout lacks: a branch whose .gitignore ignores .keel/ (a `memory.dir`
    of .keel/docs/knowledge) has none of them, and the knowledge check there said every section was missing."""
    from .. import layout

    lay = layout.resolve(root)
    r = Path(root)
    copied = []
    cfg = r / ".keel" / "config.yml"
    names = [".keel/config.yml"] if cfg.is_file() and not cfg.is_symlink() else []
    for d in (lay.specs, lay.knowledge, lay.adr):
        if (r / d).is_dir():
            names += [f"{d}/{p.name}" for p in sorted((r / d).glob("*.md")) if p.is_file() and not p.is_symlink()]
    if (r / lay.runbook).is_file() and not (r / lay.runbook).is_symlink():
        names.append(lay.runbook)
    for rel in names:
        dst = wt / rel
        if not lay.hidden and (dst.exists() or dst.is_symlink()
                               or git.git(str(wt), "check-ignore", "-q", "--", rel).returncode != 0):
            continue                     # layout repo: only what the branch ignores; git checked out the rest
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(r / rel, dst)
        copied.append(rel)
    return copied


def remove(root: str, path: str, branch: str | None = None) -> None:
    """The worktree away (also when its files changed), then its branch unless `branch` is None (kept for a hand-over).
    keel's own files there (a hidden layout keeps them out of git) are copied back to the project folder first."""
    p = Path(path)
    if p.exists():
        keep_keel_files(root, p)
        from ..runtime import container   # 0.18.57 what docker compose made there goes with it

        container.cleanup_worktree(str(p))
        r = git.git(root, "worktree", "remove", "--force", str(p))
        if r.returncode != 0 and p.is_relative_to(Path(root) / DIR):
            shutil.rmtree(p, ignore_errors=True)
    git.git(root, "worktree", "prune")
    if branch:
        git.git(root, "branch", "-D", branch)


def keep_keel_files(root: str, wt: Path) -> list[str]:
    """layout keel / external: specs, knowledge, ADRs and the runbook a flow wrote in its worktree are not in git, so
    they would go with the worktree. Copy each into the project folder, unless the file there is newer.
    0.18.64 layout repo: only the ones git ignores there (the branch's .gitignore); the rest belong to the branch."""
    from .. import layout

    lay = layout.resolve(root)

    def outside_git(rel: str) -> bool:
        return lay.hidden or git.git(str(wt), "check-ignore", "-q", "--", rel).returncode == 0

    copied = []
    for d in (lay.specs, lay.knowledge, lay.adr):
        for f in sorted((wt / d).glob("*.md")) if (wt / d).is_dir() else []:
            rel = f"{d}/{f.name}"
            if f.is_symlink() or not outside_git(rel):
                continue
            if _copy_newer(f, Path(root) / rel):
                copied.append(rel)
    book = wt / lay.runbook
    if book.is_file() and not book.is_symlink() and outside_git(lay.runbook) and _copy_newer(book, Path(root) / lay.runbook):
        copied.append(lay.runbook)
    return copied


def _copy_newer(src: Path, dst: Path) -> bool:
    if dst.is_symlink():
        return False
    if dst.is_file() and (dst.stat().st_mtime > src.stat().st_mtime or dst.read_bytes() == src.read_bytes()):
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


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
