"""Small git helpers. Synchronous: callers in async code wrap them in asyncio.to_thread."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# keel's own engine files in a project: written by the engine, never staged or guarded.
ENGINE_FILES = (".keel/ladder.json", ".keel/agents/")


GIT_TIMEOUT = 120      # seconds: a stuck git (a lock, a hung network remote) must not block a flow for ever


def git(root: str, *args: str, check: bool = False, env: dict | None = None, timeout: float | None = GIT_TIMEOUT,
        text: bool = True) -> subprocess.CompletedProcess:
    """Runs git in `root`. Never waits for a password (GIT_TERMINAL_PROMPT=0) and never for ever (`timeout`; None = no
    limit). Output that is not UTF-8 (a file name in another encoding) is decoded with replacement characters instead of
    raising; `text=False` gives bytes. A timeout is a failed command (returncode 124), like any other failed git call."""
    run_env = {**(os.environ if env is None else env), "GIT_TERMINAL_PROMPT": "0"}
    kw: dict = {"encoding": "utf-8", "errors": "replace"} if text else {}
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, check=check, env=run_env, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as exc:
        empty = "" if text else b""
        note = f"git {' '.join(args[:3])} timed out after {timeout}s"
        r = subprocess.CompletedProcess(["git", *args], 124, empty, note if text else note.encode())
        if check:
            raise subprocess.CalledProcessError(124, r.args, output=empty, stderr=r.stderr) from exc
        return r


def is_repo(root: str | None) -> bool:
    if not root or not Path(root).is_dir():
        return False
    return git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def unseen_gitdir(root: str | None) -> str | None:
    """0.18.17 the repository a .git FILE points at ("gitdir: /path/main/.git/worktrees/x", a git worktree or a
    submodule) when keel cannot see it, else None. keel's container mounts only the project folder, so every git call
    there failed with "fatal: not a git repository" while .git was right there."""
    if not root:
        return None
    f = Path(root) / ".git"
    if not f.is_file():
        return None
    try:
        first = (f.read_text(errors="replace").strip().splitlines() or [""])[0]
    except OSError:
        return None
    if not first.startswith("gitdir:"):
        return None
    d = first[len("gitdir:"):].strip()
    p = Path(d) if os.path.isabs(d) else Path(root) / d
    return None if not d or p.exists() else os.path.normpath(str(p))


def unseen_gitdir_hint(root: str | None) -> str | None:
    """What to say when git fails in a worktree whose repository keel cannot see (None when that is not the cause)."""
    d = unseen_gitdir(root)
    return None if d is None else (f"this folder is a git worktree; keel cannot see its repository at {d}; "
                                   "run keel2 add <main repo> or keel2 restart with keel2 0.18.17")


def head(root: str) -> str | None:
    r = git(root, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def branch(root: str) -> str | None:
    r = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def tracked(root: str) -> list[str]:
    """Every file git tracks in the repo (`git ls-files`), in git's order; [] outside a repo."""
    r = git(root, "ls-files", "-z")
    return [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else []


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


# ------------------------------------------------------------------ 0.18.64 remotes and branch sync

FETCH_EACH = 20        # seconds per remote: a dead or slow remote never holds a flow's start for long


def remotes(root: str) -> list[str]:
    """The repo's remotes by name (`git remote`), in git's order; [] outside a repo or with none."""
    r = git(root, "remote")
    return [x.strip() for x in r.stdout.splitlines() if x.strip()] if r.returncode == 0 else []


def rev(root: str, ref: str) -> str | None:
    """The commit `ref` names (a branch, origin/main, a tag, a sha), or None."""
    if not ref:
        return None
    r = git(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def ahead_behind(root: str, ref: str, other: str) -> tuple[int, int] | None:
    """(commits only on `ref`, commits only on `other`): how far `ref` is ahead of and behind `other`; None when git
    cannot compare them (a ref that does not exist)."""
    r = git(root, "rev-list", "--left-right", "--count", f"{ref}...{other}")
    try:
        ahead, behind = (int(x) for x in r.stdout.split())
    except ValueError:
        return None
    return ahead, behind


def remote_default(root: str, name: str) -> str | None:
    """A remote's default branch as the last fetch left it ("upstream/main"): refs/remotes/<name>/HEAD, else main or
    master when the remote has one. Nothing is fetched."""
    r = git(root, "symbolic-ref", "-q", "--short", f"refs/remotes/{name}/HEAD")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    for b in ("main", "master"):
        if git(root, "show-ref", "--verify", "-q", f"refs/remotes/{name}/{b}").returncode == 0:
            return f"{name}/{b}"
    return None


def in_progress(root: str) -> str | None:
    """"merge" or "rebase" while one waits for its conflicts to be resolved, else None."""
    if git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
        return "merge"
    for d in ("rebase-merge", "rebase-apply"):
        p = git(root, "rev-parse", "--git-path", d).stdout.strip()
        if p and (Path(root) / p).is_dir():
            return "rebase"
    return None


def unmerged(root: str) -> list[str]:
    """The files a merge or rebase left with conflicts (`git diff --diff-filter=U`), sorted."""
    r = git(root, "diff", "--name-only", "--diff-filter=U", "-z")
    return sorted({p for p in r.stdout.split("\0") if p}) if r.returncode == 0 else []


def has_markers(root: str, rel: str) -> bool:
    """True when a working file still holds a conflict block (a `<<<<<<< ` line and a `>>>>>>> ` line)."""
    p = Path(root) / rel
    if not p.is_file():
        return False
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return False
    return any(x.startswith("<<<<<<< ") for x in lines) and any(x.startswith(">>>>>>> ") for x in lines)
