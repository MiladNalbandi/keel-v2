"""0.18.64 a flow's base and the project's remotes (field report bugs 4 and 6).

A flow read stale code: it branched from a local base that was behind a second remote, and a fetch that timed out was
never mentioned. And once a flow ran, nothing could bring its branch up to its base: `git reset --hard` and a plain
`git commit` are refused during a flow (right), and nothing else did it.

    fetch_all(root, keys)          fetch every remote at once (FETCH_EACH s each): [{remote, ok, error?}]
    report(root, fetched, start)   how far the base branch is behind each remote's copy, in lines a person reads
    sync(root, onto, mode, ...)    keel merges (default) or rebases the checkout's branch onto a base; a conflict stops
                                   it with the files; action=continue commits the resolution as keel, abort goes back

keel runs these git commands itself (never an agent), so the guards that refuse an agent's merge or commit do not apply.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import rules
from ..tools import git, git_auth


class SyncError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status, self.message, self.hint = status, message, hint


# ------------------------------------------------------------------ fetch

def _first(text: str, n: int = 200) -> str:
    return ((text or "").strip().splitlines() or [""])[0][:n]


def fetch_one(root: str, name: str, keys=None, timeout: float = git.FETCH_EACH) -> dict:
    """`git fetch` of one remote's branches into refs/remotes/<name>/ (no tags), with keel's GitHub login when the
    remote is on its host. Never asks for a password or an ssh host key (BatchMode) and never waits longer than
    `timeout`. {remote, ok, error?}; the error is git's first line, the token masked."""
    login = git_auth.as_login(keys or {})
    if not git.plain_name(name):
        # 0.18.64 (security audit F14) a remote name from the repo is never read as an option (`--upload-pack=...`)
        return {"remote": name, "ok": False, "error": "not a remote name keel fetches"}
    try:
        with git_auth.remote(root, login, name) as r:
            env = dict(r.env)
            env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=10")
            f = git.git(root, *r.args, "fetch", "-q", "--no-tags", "--end-of-options", r.target,
                        f"+refs/heads/*:refs/remotes/{name}/*", env=env, timeout=timeout)
    except git_auth.UnsafeRemote as exc:
        return {"remote": name, "ok": False, "error": str(exc)}
    if f.returncode == 0:
        return {"remote": name, "ok": True}
    why = f"timed out after {int(timeout)} s" if f.returncode == 124 else \
        git_auth.mask(_first(f.stderr or f.stdout), login) or f"git fetch exited with {f.returncode}"
    return {"remote": name, "ok": False, "error": why}


def fetch_all(root: str, keys=None, timeout: float = git.FETCH_EACH) -> list[dict]:
    """Every remote of the repo fetched at the same time, so the whole fetch takes at most about `timeout` seconds
    however many remotes there are. [] for a repo with no remote (nothing is run)."""
    names = git.remotes(root) if git.is_repo(root) else []
    if not names:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
        return list(pool.map(lambda n: fetch_one(root, n, keys, timeout), names))


# ------------------------------------------------------------------ report

def base_name(root: str, cfg: dict | None = None) -> str | None:
    """The local base branch: base_branch from .keel/config.yml (origin/main counts as main), else main, else master;
    None when the repo has none of them."""
    from . import bases

    cfg = rules.load_config(root) if cfg is None else cfg
    named = bases.configured(cfg)
    # 0.18.65 only a real remote's name is cut off (github/main is main; a local feat/x stays feat/x)
    wanted = [named, bases.split(named, git.remotes(root))[1], "main", "master"]
    for b in wanted:
        if b and git.git(root, "show-ref", "--verify", "-q", f"refs/heads/{b}").returncode == 0:
            return b
    return None


def _n(k: int) -> str:
    return f"{k} commit{'' if k == 1 else 's'}"


def report(root: str, fetched: list[dict] | None, start: str | None = None, cfg: dict | None = None) -> dict:
    """{base, remotes: [{remote, ok, error?, branch?, ahead?, behind?, default?, default_ahead?, default_behind?}],
    lines, note}: for each remote, how far the local base branch is behind (and ahead of) the remote's branch of the
    same name, and the remote's default branch when its name differs (local master, another remote's main). A failed
    fetch is said plainly; the comparison then uses what the last fetch left. With `start` (the commit the flow starts
    at): which remote branch it starts from, or the way to start from the newer one."""
    from . import bases

    fetched = list(fetched or [])
    cfg = rules.load_config(root) if cfg is None else cfg
    base = base_name(root, cfg) if git.is_repo(root) else None
    rows, lines, newer = [], [], []
    for item in fetched:
        name = str(item.get("remote") or "")
        row = {"remote": name, "ok": bool(item.get("ok"))}
        if not row["ok"]:
            row["error"] = str(item.get("error") or "git fetch failed")
            lines.append(f"fetch from {name} failed: {row['error']}; the base may be stale")
        if base:
            same = f"{name}/{base}"
            ab = git.ahead_behind(root, base, same) if git.rev(root, same) else None
            if ab:
                row.update(branch=same, ahead=ab[0], behind=ab[1])
                if ab[1]:
                    newer.append((ab[1], same))
                    lines.append(f"{base} is {_n(ab[1])} behind {same}" +
                                 (f" and {ab[0]} ahead (they went their own ways)" if ab[0] else ""))
            default = git.remote_default(root, name)
            dab = git.ahead_behind(root, base, default) if default and default != same else None
            if dab:
                row.update(default=default, default_ahead=dab[0], default_behind=dab[1])
                if dab[1]:
                    newer.append((dab[1], default))
                    lines.append(f"{base} is {_n(dab[1])} behind {default} ({name}'s default branch)")
        rows.append(row)
    gone = bases.missing(root, cfg) if git.is_repo(root) else None
    if gone:
        # 0.18.65 (R5) base_branch names no local branch: said, with the choices the person makes (never an agent)
        lines.append(gone["text"] + " Choose in Settings › General › Base branch (or keel_base_branch): "
                     + "; ".join(c["label"] for c in gone["choices"]))
    compared = [r.get(k) for r in rows for k in ("branch", "default") if r.get(k)]
    if base and compared and not newer:
        lines.append(f"{base} is up to date with {', '.join(compared)}")
    if start and compared:
        local = git.rev(root, base or "")
        from_ref = next((ref for ref in compared if git.rev(root, ref) == start), None) if start != local else None
        if from_ref:
            lines.append(f"the flow's branch starts from {from_ref}")
        elif newer and local == start:
            # 0.18.65 the canonical remote's copy first (runtime/bases.py), else the one that is furthest ahead
            canonical = bases.resolve(root, cfg).get("remote")
            ref = canonical if any(r == canonical for _k, r in newer) else max(newer)[1]
            lines.append(f"the flow starts from the local {base}: to start from the newer code, start it with base {ref}")
    return {"base": base, "remotes": rows, "lines": lines, "note": "; ".join(lines)}


# ------------------------------------------------------------------ sync

def default_onto(root: str, cfg: dict) -> str | None:
    """The base a flow's branch syncs onto when none is named: the base branch's remote copy (origin/main) when there
    is one, else the local base branch (verdict_actions.base_ref)."""
    from . import verdict_actions

    return verdict_actions.base_ref(root, cfg)


def _changed_tracked(root: str) -> list[str]:
    r = git.git(root, "status", "--porcelain", "--untracked-files=no")
    out = [line[3:].strip().strip('"') for line in r.stdout.splitlines() if len(line) > 3]
    return [p for p in out if not git.is_engine_file(p)]


def _git_file(root: str, name: str) -> Path:
    return Path(root) / git.git(root, "rev-parse", "--git-path", name).stdout.strip()


def _merge_message(root: str) -> str:
    """The message the waiting merge will be committed with (.git/MERGE_MSG), without git's # comment lines."""
    try:
        text = _git_file(root, "MERGE_MSG").read_text(errors="replace")
    except OSError:
        return ""
    return "\n".join(x for x in text.splitlines() if not x.startswith("#")).strip()


def _rebase_onto(root: str) -> str | None:
    for d in ("rebase-merge", "rebase-apply"):
        try:
            return (_git_file(root, d) / "onto").read_text().strip() or None
        except OSError:
            continue
    return None


def _merge_base(root: str, ref: str | None) -> str | None:
    if not ref:
        return None
    r = git.git(root, "merge-base", "HEAD", ref)
    return r.stdout.strip() or None if r.returncode == 0 else None


def _done(root: str, mode: str, onto: str, onto_sha: str | None, branch: str, before: str | None, behind: int | None) -> dict:
    head = git.head(root)
    parents = git.git(root, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    merged = mode == "merge" and len(parents) > 2
    n = f"{_n(behind)} of {onto}" if behind else onto
    note = (f"merged {n} into {branch}" + (f" (merge commit {head[:7]})" if merged and head else " (fast-forward)")
            if mode == "merge" else
            f"rebased {branch} onto {onto}: its commits have new ids, so a push of a branch pushed before needs "
            "--force-with-lease")
    return {"ok": True, "synced": head != before, "mode": mode, "onto": onto, "branch": branch, "before": before,
            "head": head, "base_head": _merge_base(root, onto_sha or onto), "commit": head if merged else None,
            "behind": behind, "conflicts": [], "note": note}


def _stopped(root: str, mode: str, onto: str, branch: str, before: str | None) -> dict:
    files = git.unmerged(root)
    shown = ", ".join(files[:10]) + (", …" if len(files) > 10 else "")
    return {"ok": False, "synced": False, "mode": mode, "onto": onto, "branch": branch, "before": before,
            "head": git.head(root), "state": git.in_progress(root) or mode, "conflicts": files,
            "note": f"the {mode} of {onto} into {branch} stopped: {len(files)} file(s) have conflicts: {shown}",
            "next": "Resolve each file (keep what both sides need and remove the <<<<<<< ======= >>>>>>> lines), then "
                    "sync again with action=continue: keel stages those files and commits the resolution. "
                    "action=abort puts the branch back as it was."}


def sync(root: str, onto: str | None = None, mode: str = "merge", action: str = "start",
         settings: dict | None = None, cfg: dict | None = None) -> dict:
    """keel brings the checkout's branch (a flow's branch, in the project folder or the flow's worktree) up to a base.

    action start: merge (default) or rebase `onto` (default: default_onto) into the branch. Refused on a detached HEAD,
    with changed tracked files (commit them first: keel never merges over uncommitted work), or while a merge or rebase
    waits. Nothing to bring in: ok, synced False. A conflict leaves the merge or rebase waiting and answers ok False
    with the files (`conflicts`). action continue: the files must be free of conflict markers; keel stages exactly them
    and commits (merge) or continues the rebase with keel's commit identity (actions.commit_args: the person's name,
    KeelBot co-authors). action abort: `git merge --abort` / `git rebase --abort`.
    The answer's base_head is where the branch's own work starts now (the merge base with `onto`)."""
    from .actions import commit_args

    if not git.is_repo(root):
        raise SyncError(400, "Not a git repository: there is no branch to sync.")
    cfg = rules.load_config(root) if cfg is None else cfg
    going = git.in_progress(root)
    branch = git.branch(root) or ""
    ident, trailer = commit_args(root, settings, cfg)
    editor = {**git.scrubbed(), "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"}
    if action == "abort":
        if not going:
            raise SyncError(409, "No merge or rebase waits here: nothing to abort.")
        r = git.git(root, going, "--abort")
        if r.returncode != 0:
            raise SyncError(409, f"git {going} --abort failed: {_first(r.stderr or r.stdout)}")
        return {"ok": True, "synced": False, "mode": going, "branch": git.branch(root), "head": git.head(root),
                "conflicts": [], "note": f"the {going} was aborted: {git.branch(root)} is back as it was"}
    if action == "continue":
        if not going:
            raise SyncError(409, "No merge or rebase waits here: nothing to continue.", "Start one with action=start.")
        files = git.unmerged(root)
        marked = [f for f in files if git.has_markers(root, f)]
        if marked:
            out = _stopped(root, going, "the base", branch, git.head(root))
            out.update(conflicts=marked, note=f"{len(marked)} file(s) still hold conflict markers: {', '.join(marked[:10])}")
            return out
        if files:
            git.git(root, "add", "-A", "--", *files)
        before = git.head(root)
        if going == "merge":
            onto_sha = git.rev(root, "MERGE_HEAD")
            msg = _merge_message(root) or f"Merge into {branch}"
            r = git.git(root, *ident, "commit", "-q", "-m", msg, *([] if "Co-Authored-By:" in msg else trailer))
        else:
            onto_sha = _rebase_onto(root)
            r = git.git(root, *ident, "-c", "core.editor=true", "rebase", "--continue", env=editor)
        if r.returncode != 0:
            if git.unmerged(root):
                return _stopped(root, going, onto_sha[:7] if onto_sha else "the base", git.branch(root) or branch, before)
            raise SyncError(409, f"git {going} could not go on: {_first(r.stderr or r.stdout)}",
                            "Stage the resolved files only; changes to other files belong in a commit of their own.")
        return _done(root, going, onto_sha[:7] if onto_sha else "the base", onto_sha, git.branch(root) or branch, before, None)
    if action != "start":
        raise SyncError(400, f'action is start, continue or abort, not "{action}".')
    if mode not in ("merge", "rebase"):
        raise SyncError(400, f'mode is merge or rebase, not "{mode}".')
    if going:
        files = git.unmerged(root)
        raise SyncError(409, f"A {going} waits here with {len(files)} conflicted file(s): {', '.join(files[:10])}",
                        "Resolve them and sync with action=continue, or action=abort to go back.")
    if not branch or branch == "HEAD":
        raise SyncError(409, "The checkout is not on a branch (detached HEAD): there is no branch to sync.")
    target = (onto or "").strip() or default_onto(root, cfg)
    if not target:
        raise SyncError(400, "No base to sync onto: this repo has no main or master.", "Name one: onto=<remote>/<branch> or a local branch.")
    if not git.plain_name(target):
        raise SyncError(400, f"{target!r} is not a branch name.", "Name a local branch or <remote>/<branch>.")
    target_sha = git.rev(root, target)
    if not target_sha:
        raise SyncError(400, f"There is no branch {target} here.",
                        "Fetch first (keel_fetch_remotes), or name a local branch or <remote>/<branch>.")
    if target in (branch, f"refs/heads/{branch}"):
        raise SyncError(400, f"{branch} cannot be synced onto itself.", "Name its base: main, or <remote>/main.")
    changed = _changed_tracked(root)
    if changed:
        raise SyncError(409, f"{len(changed)} tracked file(s) have uncommitted changes: {', '.join(changed[:10])}",
                        "keel never merges over uncommitted work: let the flow commit them (or commit them yourself), then sync.")
    ab = git.ahead_behind(root, "HEAD", target) or (0, 0)
    before = git.head(root)
    if not ab[1]:
        return {"ok": True, "synced": False, "mode": mode, "onto": target, "branch": branch, "before": before,
                "head": before, "base_head": _merge_base(root, target), "commit": None, "behind": 0, "conflicts": [],
                "note": f"{branch} already has every commit of {target}: nothing to bring in"}
    if mode == "merge":
        r = git.git(root, *ident, "merge", "--no-edit", "-m", f"Merge {target} into {branch}", *trailer, target, env=editor)
    else:
        r = git.git(root, *ident, "rebase", target, env=editor)
    if r.returncode == 0:
        return _done(root, mode, target, target_sha, branch, before, ab[1])
    if git.in_progress(root):
        return _stopped(root, mode, target, branch, before)
    raise SyncError(409, f"git {mode} {target} failed: {_first(r.stderr or r.stdout)}",
                    "Nothing changed. An untracked file in the way is a common cause: move it, then sync again.")
