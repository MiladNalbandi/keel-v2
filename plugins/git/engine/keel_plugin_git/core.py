"""The Git plugin's core: git and the pull request, for KeelBot, workflows, the Code page and keel2 mcp.

Reading is free: status, branches, log, diff, show, blame, pr_status. Acting has rules that no caller can change:

    switch     a branch name git accepts; carries the person's changes along (git refuses when they would be lost)
    commit     keel's commit: every change except keel's own files, the secret check, the author from the settings
               and the KeelBot line (runtime/actions.py commit_args)
    sync       merges the base branch in; a conflict is undone at once and listed, nothing half-merged stays
    push       never with force, never to the base branch, main or master; a GitHub https remote gets the token
    pr         opens the pull request for the branch, or updates its title and body (gh, with the GitHub token)
    cleanup    deletes local branches already merged into the base (git branch -d refuses anything else)

Never, for anyone: force push, reset --hard, rewriting pushed history, deleting a remote branch or a tag.

    github_token(keys)  the GitHub token an agent call or an api call carries (Connections › GitHub, keel's core)

It uses keel's core as it did inside it: tools/git.py, the rules and the secret check, testcmd for gh, and keel's
commit rules (runtime/actions.py).
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from keel_engine import rules
from keel_engine.rules import checks
from keel_engine.tools import git, git_auth, testcmd

DIFF_MAX = 40_000
PROTECTED = ("main", "master")
SHA = re.compile(r"^[0-9a-fA-F]{4,64}$")


def github_token(keys: dict | None) -> str | None:
    """The GitHub token a call carries (keys["github"]), read as keel's own ship step reads it."""
    from keel_engine.runtime.verdict_actions import github_token as token

    return token(keys or {})


class GitError(Exception):
    """`transient`: GitHub did not answer (a network error that passes); the branch and its commits are fine and the
    same call may work in a moment."""

    def __init__(self, status: int, message: str, hint: str = "", transient: bool = False):
        super().__init__(message)
        self.status = status
        self.hint = hint
        self.transient = transient


def _need_repo(root: str):
    if not git.is_repo(root):
        # 0.18.15 say which folder keel checked and what git said: "not a repository" was also shown for a folder keel
        # cannot see (another path in its container) or one git refuses (dubious ownership), with .git right there
        from pathlib import Path
        if not root or not Path(root).is_dir():
            raise GitError(400, f"keel cannot see the project folder {root or '(none)'}.",
                           "keel runs in its own container: add the folder with keel2 add <folder> (or check the project's path).")
        # 0.18.17 a worktree (or submodule) whose repository is outside the folder keel's container mounts
        hint = git.unseen_gitdir_hint(root)
        if hint:
            raise GitError(400, f"{root} is not a git repository for keel: {hint}.",
                           "keel2 0.18.17 mounts a worktree's repository too: keel2 restart, or keel2 add the main repository.")
        why = (git.git(root, "rev-parse", "--is-inside-work-tree").stderr or "").strip().splitlines()
        raise GitError(400, f"{root} is not a git repository for keel" + (f": {why[-1]}" if why else "."),
                       "dubious ownership: the folder belongs to another user; run git config --global --add safe.directory "
                       f"{root}" if why and "dubious ownership" in why[-1] else "Run git init in the project folder first.")


def base(root: str) -> str | None:
    """The branch work is compared with: base_branch in .keel/config.yml, else main, else master. 0.18.65 the
    canonical remote's copy (github/main, runtime/bases.py) when the local base is behind it, went its own way, or does
    not exist here; base_branch may be <remote>/<branch>."""
    from keel_engine.runtime import bases

    r = bases.resolve(root)
    if r.get("local") and r.get("remote") and not r.get("behind"):
        return r["local"]
    if r.get("ref"):
        return r["ref"]
    cfg = rules.load_config(root)
    for ref in [cfg.get("base_branch"), "main", "master"]:
        if ref and git.git(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0:
            return ref
    return None


def base_name(root: str) -> str | None:
    """0.18.65 the base branch's name (main for github/main): the branch a pull request goes into, the branch keel never
    pushes to."""
    from keel_engine.runtime import bases

    return bases.resolve(root).get("name") or base(root)


def _count(root: str, rng: str) -> int:
    r = git.git(root, "rev-list", "--count", rng)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else 0


def _tail(text: str, n: int = 1500) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


# ------------------------------------------------------------------ reading

def _numstat(root: str) -> dict[str, tuple[int, int]]:
    """{path: (lines added, lines deleted)} of the tracked changes against HEAD (staged and not); a binary file and an
    untracked one are not in it. Empty when HEAD has no commit yet."""
    r = git.git(root, "diff", "--numstat", "-z", "HEAD")
    if r.returncode != 0:
        return {}
    out: dict[str, tuple[int, int]] = {}
    parts = r.stdout.split("\0")
    i = 0
    while i < len(parts):
        item = parts[i]
        i += 1
        fields = item.split("\t")
        if len(fields) != 3:
            continue
        add, dele, path = fields
        if not path:                     # a rename: the old path and the new one follow as their own items
            path = parts[i + 1] if i + 1 < len(parts) else ""
            i += 2
        if path and add.isdigit() and dele.isdigit():
            out[path] = (int(add), int(dele))
    return out


def status(root: str) -> dict:
    """{branch, base, upstream, ahead, behind (the upstream), base_ahead, base_behind, changes: [{path, status,
    added?, deleted?}]}: `added` and `deleted` are the lines of a tracked file against HEAD (not for a binary or an
    untracked file)."""
    _need_repo(root)
    br = git.branch(root)
    b = base(root)
    up = git.git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    upstream = up.stdout.strip() if up.returncode == 0 else None
    # `status --porcelain -z` (git.dirty): paths are never quoted, and a rename reports its new path
    dirty = git.dirty(root)
    stats = _numstat(root) if dirty else {}
    changes = []
    for path, xy in dirty.items():
        c = {"path": path, "status": xy.strip() or "?"}
        if path in stats:
            c["added"], c["deleted"] = stats[path]
        changes.append(c)
    return {"branch": br, "base": b, "upstream": upstream,
            "ahead": _count(root, "@{u}..HEAD") if upstream else _count(root, f"{b}..HEAD") if b else 0,
            "behind": _count(root, "HEAD..@{u}") if upstream else 0,
            "base_ahead": _count(root, f"{b}..HEAD") if b else 0, "base_behind": _count(root, f"HEAD..{b}") if b else 0,
            "pushed": bool(upstream), "changes": changes[:200]}


def branches(root: str) -> list[dict]:
    """Local branches, newest first: {name, current, ahead, behind (against the base), date, subject, author, mine?}.
    `mine` (the last commit's author is the repository's git user.email) only when git has a user.email."""
    _need_repo(root)
    b = base(root)
    cur = git.branch(root)
    me = git.git(root, "config", "user.email").stdout.strip().lower()
    out = []
    fmt = "%(refname:short)%09%(committerdate:iso-strict)%09%(authorname)%09%(authoremail)%09%(subject)"
    for line in git.git(root, "for-each-ref", "--sort=-committerdate", f"--format={fmt}", "refs/heads").stdout.splitlines()[:60]:
        name, date, author, email, subject = (line.split("\t", 4) + ["", "", "", ""])[:5]
        item = {"name": name, "current": name == cur, "date": date, "subject": subject, "author": author,
                "ahead": _count(root, f"{b}..{name}") if b and name != b else 0,
                "behind": _count(root, f"{name}..{b}") if b and name != b else 0}
        if me:
            item["mine"] = email.strip("<>").lower() == me
        out.append(item)
    return out


def log(root: str, n: int = 20, path: str | None = None, rng: str | None = None) -> list[dict]:
    _need_repo(root)
    rng = (rng or "").strip() or None
    if rng and (rng.startswith("-") or any(ch.isspace() for ch in rng)):
        raise GitError(400, "A range is like main..HEAD.", "It cannot start with - or hold spaces.")
    args = ["log", f"-n{max(1, min(n, 100))}", "--format=%h%x09%an%x09%ad%x09%s", "--date=short"]
    if rng:
        args += ["--end-of-options", rng]
    if path:
        args += ["--", path]
    out = []
    for line in git.git(root, *args).stdout.splitlines():
        sha, author, date, subject = (line.split("\t") + ["", "", ""])[:4]
        out.append({"sha": sha, "author": author, "date": date, "subject": subject})
    return out


def diff(root: str, path: str | None = None, staged: bool = False, against: str | None = None) -> str:
    """The diff of the uncommitted changes (or the staged ones, or the branch against `base`), at most 40k characters."""
    _need_repo(root)
    args = ["diff", "--stat", "--patch"]
    if staged:
        args.append("--cached")
    if against == "base":
        b = base(root)
        if b:
            args.append(f"{b}...HEAD")
    if path:
        args += ["--", path]
    text = git.git(root, *args).stdout
    return text if len(text) <= DIFF_MAX else text[:DIFF_MAX] + f"\n… ({len(text) - DIFF_MAX} more characters)"


def show(root: str, sha: str) -> str:
    _need_repo(root)
    if not SHA.match(sha or ""):
        raise GitError(400, "That is not a commit id.")
    text = git.git(root, "show", "--stat", "--patch", sha).stdout
    return text if len(text) <= DIFF_MAX else text[:DIFF_MAX] + "\n…"


def blame(root: str, path: str, start: int = 1, end: int | None = None) -> str:
    _need_repo(root)
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise GitError(400, "Name a file inside the project.")
    rng = f"-L{max(1, start)},{end}" if end else f"-L{max(1, start)},+40"
    r = git.git(root, "blame", "--date=short", rng, "--", path)
    if r.returncode != 0:
        raise GitError(400, f"git blame failed: {_tail(r.stderr, 300)}")
    return r.stdout[:DIFF_MAX]


# ------------------------------------------------------------------ GitHub

def _gh_env(token: str | None, root: str | None = None) -> dict:
    """gh's environment: the login for its own host only (github.com, or the Enterprise origin of that host)."""
    if not token:
        raise GitError(400, "There is no GitHub token.", "Add one in Connections › GitHub (it needs repo access).")
    return git_auth.gh_env(root, token)


# What gh prints when GitHub did not answer, and not because of what was asked: worth one more try.
TRANSIENT = re.compile(
    r"tls handshake timeout|connection reset|connection refused|i/o timeout|timeout awaiting response headers"
    r"|unexpected eof|http 50[234]\b|\b50[234] (?:bad gateway|service unavailable|gateway timeout)",
    re.IGNORECASE)
RETRY_AFTER_S = 2.0
_sleep = time.sleep                      # the tests wait no time


def transient(code: int, out: str) -> bool:
    """A failed gh call that a moment later may work: a network error on the way to GitHub, or GitHub's 502/503/504."""
    return code != 0 and bool(TRANSIENT.search(out or ""))


def _gh(root: str, token: str | None, cmd: str, timeout: int = 60, retry: bool = True) -> tuple[int, str]:
    """gh with the login (for its own host only). A read that failed on the network (`transient`) runs once more after
    ~2 s; a call that changes something (retry=False: pr create) never runs twice, it may have reached GitHub. (0,
    stdout: the whole answer, never cut) or (code, what gh said: stderr first, upgrade notices out, secrets masked)."""
    env = _gh_env(token, root)
    code, out, err = testcmd.run_split(root, cmd, timeout, env)
    if retry and transient(code, err + out):
        _sleep(RETRY_AFTER_S)
        code, out, err = testcmd.run_split(root, cmd, timeout, env)
    return (code, out) if code == 0 else (code, git_auth.gh_message(code, out, err, token))


def _json_arrays(text: str) -> list:
    """The items of what `gh api --paginate` prints (one JSON array per page, one after the other, or --slurp's pages);
    no output is no items. A ValueError when the text is not arrays."""
    return git_auth.json_pages(text)


def _unreadable(what: str, out: str, token) -> GitError:
    said = git_auth.gh_message(0, out, "", token)
    return GitError(502, f"gh's answer for {what} is not what keel expects: {said}", git_auth.gh_fix(said, token))


def pr_status(root: str, token: str | None) -> dict | None:
    """The branch's pull request: {number, title, url, state, draft, review, checks: [{name, state}], comments: [...]},
    or None when the branch has none."""
    _need_repo(root)
    fields = ("number,title,url,state,isDraft,reviewDecision,statusCheckRollup,comments,baseRefName,headRefName,"
              "reviewRequests,updatedAt")
    code, out = _gh(root, token, f"gh pr view --json {fields}")
    if code != 0:
        if "no pull requests found" in out.lower() or "no open pull requests" in out.lower():
            return None
        if transient(code, out):
            raise GitError(504, f"GitHub did not answer in time: {_tail(out, 300)}",
                           "The branch and its commits are fine; keel tried twice. Try again in a moment.", transient=True)
        raise GitError(502, f"gh could not read the pull request: {out}", git_auth.gh_fix(out, token))
    try:
        d = json.loads(out)
        if not isinstance(d, dict):
            raise ValueError("not an object")
    except ValueError:
        raise _unreadable("the pull request", out, token) from None
    checks_ = []
    for c in d.get("statusCheckRollup") or []:
        state = (c.get("conclusion") or c.get("state") or c.get("status") or "").lower()
        checks_.append({"name": c.get("name") or c.get("context") or "check", "state": state or "pending",
                        "url": c.get("detailsUrl") or c.get("targetUrl") or ""})
    comments = [{"author": (c.get("author") or {}).get("login", ""), "body": (c.get("body") or "")[:2000], "path": None, "line": None}
                for c in d.get("comments") or []]
    code2, out2 = _gh(root, token, f"gh api repos/{{owner}}/{{repo}}/pulls/{d.get('number')}/comments --paginate")
    if transient(code2, out2):
        raise GitError(504, f"GitHub did not answer in time: {_tail(out2, 300)}",
                       "The branch and its commits are fine; keel tried twice. Try again in a moment.", transient=True)
    if code2 != 0:
        raise GitError(502, f"gh could not read the review comments: {out2}", git_auth.gh_fix(out2, token))
    try:
        inline = _json_arrays(out2)
    except ValueError:
        raise _unreadable("the review comments", out2, token) from None
    for c in inline:
        if isinstance(c, dict):
            comments.append({"author": (c.get("user") or {}).get("login", ""), "body": (c.get("body") or "")[:2000],
                             "path": c.get("path"), "line": c.get("line") or c.get("original_line")})
    done = [c for c in checks_ if c["state"] not in ("pending", "queued", "in_progress", "expected", "waiting", "")]
    failed = [c for c in checks_ if c["state"] in ("failure", "failed", "error", "cancelled", "timed_out", "action_required")]
    requests = [(r.get("login") or r.get("name") or r.get("slug") or "") for r in d.get("reviewRequests") or []
                if isinstance(r, dict)]
    return {"number": d.get("number"), "title": d.get("title"), "url": d.get("url"), "state": d.get("state"),
            "draft": d.get("isDraft"), "review": d.get("reviewDecision"), "base": d.get("baseRefName"),
            "branch": d.get("headRefName"), "checks": checks_, "checks_done": len(done), "checks_failed": len(failed),
            "comments": comments[-50:], "comments_total": len(comments), "review_requests": [r for r in requests if r],
            "updated_at": d.get("updatedAt")}


# ------------------------------------------------------------------ acting

def _remote_twins(root: str, name: str) -> list[str]:
    """The remote-tracking branches named `name` (origin/<name>, …): with exactly one, `git switch <name>` checks it out
    as a new local branch that tracks it (a teammate's branch from Source control's remote list)."""
    out = git.git(root, "for-each-ref", "--format=%(refname)", "refs/remotes").stdout.splitlines()
    return [r for r in out if r.count("/") >= 3 and r.split("/", 3)[3] == name]


def switch(root: str, branch: str, create: bool = False, stash: bool = False) -> dict:
    """Switch (or create and switch). A name only one remote has (origin/<name>) becomes a local branch that tracks it
    (0.18.38, Check out in Source control). keel 0.18.9 `stash`: the folder's uncommitted changes go into git's stash first
    (with a message that names the branch they were on), so a switch git refuses because of them can go on; `git stash
    pop` on that branch brings them back."""
    _need_repo(root)
    name = (branch or "").strip()
    if not name or git.git(root, "check-ref-format", "--branch", name).returncode != 0 or name.startswith("-"):
        raise GitError(400, f"{name or 'That'} is not a branch name git accepts.", "For example feat/euro-prices.")
    exists = git.git(root, "rev-parse", "--verify", "-q", f"refs/heads/{name}").returncode == 0
    if create and exists:
        raise GitError(409, f"The branch {name} exists already.", "Switch to it instead.")
    if not create and not exists and len(_remote_twins(root, name)) != 1:
        raise GitError(404, f"There is no branch {name}.", "Create it instead.")
    stashed = None
    if stash and git.git(root, "status", "--porcelain").stdout.strip():
        was = git.git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "?"
        msg = f"keel: changes on {was} before switching to {name}"
        s = git.git(root, "stash", "push", "--include-untracked", "-m", msg)
        if s.returncode != 0:
            raise GitError(409, f"git could not stash the changes: {_tail(s.stderr, 400)}", "Commit them instead.")
        stashed = {"from": was, "message": msg}
    r = git.git(root, "switch", *(["-c"] if create else []), name)
    if r.returncode != 0:
        if stashed:                                     # put them back where they were: nothing is lost
            git.git(root, "stash", "pop")
        local = "would be overwritten" in r.stderr or "local changes" in r.stderr
        raise GitError(409, f"git could not switch to {name}: {_tail(r.stderr, 400)}",
                       "Stash them and switch (keel keeps them in git's stash; `git stash pop` on your branch brings them "
                       "back), or commit them first." if local else "Commit or stash the changes git names first.")
    return {"branch": name, "created": create, **({"stashed": stashed} if stashed else {})}


def commit(root: str, message: str, settings: dict | None = None) -> dict:
    """keel's commit of every change but keel's own files, as the settings' author, with the secret check."""
    from keel_engine.runtime.actions import COMMIT_EXCLUDES, commit_args

    _need_repo(root)
    text = (message or "").strip()
    if not text:
        raise GitError(400, "The commit message is empty.")
    git.git(root, "add", "-A", "--", ".", *COMMIT_EXCLUDES)
    staged = [f for f in git.git(root, "diff", "--cached", "--name-only").stdout.splitlines() if f.strip()]
    if not staged:
        raise GitError(409, "There is nothing to commit.")
    found = checks.secrets_in_diff(git.git(root, "diff", "--cached", "-U0").stdout)
    if found:
        git.git(root, "reset", "-q")
        raise GitError(409, "The changes hold what looks like a secret; keel did not commit.",
                       "; ".join(f"{f['file']}: {f['why']}" for f in found)[:600])
    ident, trailer = commit_args(root, settings or {}, rules.load_config(root))
    first, _, rest = text.partition("\n")
    r = git.git(root, *ident, "commit", "-q", "-m", first.strip(), *(["-m", rest.strip()] if rest.strip() else []), *trailer)
    if r.returncode != 0:
        git.git(root, "reset", "-q")
        from keel_engine.runtime import v1_state

        # 0.18.64 keel v1's hooks refuse commits while its state file names a flow: the hint names it
        raise GitError(400, f"git commit failed: {_tail(r.stderr or r.stdout, 400)}", v1_state.leftover(root))
    return {"sha": (git.head(root) or "")[:12], "subject": first.strip(), "files": staged}


def sync(root: str, token: str | None = None) -> dict:
    """Merge the base branch (the remote's when there is one) into the current branch. A conflict is undone."""
    from keel_engine.runtime import bases

    _need_repo(root)
    found = bases.resolve(root)
    b = found.get("name") or base(root)
    br = git.branch(root)
    if not b:
        raise GitError(400, "keel found no base branch (main or master).", "Set base_branch in .keel/config.yml.")
    if br == b:
        raise GitError(400, f"You are on the base branch ({b}).", "Switch to a feature branch first.")
    if git.dirty(root):
        raise GitError(409, "The branch has uncommitted changes.", "Commit or stash them first, so a merge cannot mix with them.")
    ref = found.get("local") or b
    # 0.18.65 the canonical remote's copy of the base (github/main), not always origin's
    remote = found.get("remote") or (f"{found['canonical']}/{b}" if found.get("canonical") else None)
    if remote:
        where, _, branch = remote.partition("/")
        fetched = _fetch(root, branch, token, where)
        if fetched.returncode != 0:
            # a stale <remote>/<base> would be merged as if it were current: say the fetch failed instead
            raise GitError(502, f"git could not fetch {branch} from {where}: {_tail(fetched.stderr or fetched.stdout, 300)}",
                           "Check the network and the GitHub token; nothing was merged.")
        if git.git(root, "rev-parse", "--verify", "-q", f"refs/remotes/{remote}").returncode == 0:
            ref = remote
    from keel_engine.runtime.actions import commit_args

    ident, _ = commit_args(root, {}, rules.load_config(root))
    r = git.git(root, *ident, "merge", "--no-edit", ref)
    if r.returncode != 0:
        conflicts = [f for f in git.git(root, "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if f.strip()]
        git.git(root, "merge", "--abort")
        if conflicts:
            raise GitError(409, f"Merging {ref} conflicts in {len(conflicts)} file(s); nothing was changed.", ", ".join(conflicts[:20]))
        raise GitError(400, f"git merge failed: {_tail(r.stderr or r.stdout, 400)}")
    merged = "Already up to date" not in (r.stdout + r.stderr)
    return {"merged": merged, "from": ref, "branch": br}


def _remote_url(root: str) -> str:
    return git.git(root, "remote", "get-url", "origin").stdout.strip()


def _fetch(root: str, ref: str, token: str | None, name: str = "origin"):
    """A remote's `ref` into <remote>/<ref> (origin's by default); with keel's GitHub login only when the remote is on
    its host (git_auth)."""
    if not (git.plain_name(ref) and git.plain_name(name)):
        return subprocess.CompletedProcess(["git", "fetch"], 1, "", f"{name}/{ref} is not a remote branch name")
    try:
        with git_auth.remote(root, token, name) as r:
            out = git.git(root, *r.args, "fetch", "-q", "--end-of-options", r.target,
                          f"+refs/heads/{ref}:refs/remotes/{name}/{ref}", env=r.env)
    except git_auth.UnsafeRemote as exc:
        return subprocess.CompletedProcess(["git", "fetch"], 1, "", str(exc))
    out.stderr = git_auth.mask(out.stderr, token)
    out.stdout = git_auth.mask(out.stdout, token)
    return out


def push(root: str, token: str | None = None) -> dict:
    """`git push` of the current branch to origin, never with force and never to the base branch, main or master."""
    _need_repo(root)
    br = git.branch(root)
    b = base_name(root)
    if not br:
        raise GitError(400, "HEAD is not on a branch.", "Switch to a branch first.")
    if br in PROTECTED or br == b:
        raise GitError(403, f"keel never pushes to {br}.", "Push a feature branch and open a pull request.")
    url = _remote_url(root)
    if not url:
        raise GitError(400, "This repository has no remote called origin.")
    try:
        with git_auth.remote(root, token) as rem:
            r = git.git(root, *rem.args, "push", "--porcelain", "--end-of-options", rem.target, f"HEAD:refs/heads/{br}",
                        env=rem.env)
            https = rem.login is not None
    except git_auth.UnsafeRemote as exc:
        raise GitError(400, f"keel did not push: {exc}") from None
    if r.returncode != 0:
        out = git_auth.mask(_tail(r.stderr or r.stdout, 500), token)
        if "non-fast-forward" in out or "rejected" in out:
            raise GitError(409, f"The remote branch {br} has commits this one lacks; keel does not force.",
                           "Bring them in first (Update from main does not; pull the branch), then push again.")
        raise GitError(502, f"git push failed: {out}", "Check the GitHub token in Connections › GitHub." if token else
                       "Add a GitHub token in Connections › GitHub.")
    if https:
        git.git(root, "update-ref", f"refs/remotes/origin/{br}", "HEAD")
    git.git(root, "branch", f"--set-upstream-to=origin/{br}", br)
    return {"branch": br, "remote": "origin", "sha": (git.head(root) or "")[:12]}


def pr(root: str, token: str | None, title: str, body: str, draft: bool = False) -> dict:
    """Open the branch's pull request, or update its title and body when it has one."""
    _need_repo(root)
    title = " ".join((title or "").split())[:200]
    if not title:
        raise GitError(400, "The pull request needs a title.")
    br = git.branch(root)
    b = base_name(root)
    if not br or br in PROTECTED or br == b:
        raise GitError(400, "Open a pull request from a feature branch.")
    up = git.git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up.returncode != 0 or _count(root, "@{u}..HEAD"):
        raise GitError(409, f"The branch {br} has commits that are not pushed.", "Push it first.")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body or "")
        path = f.name
    try:
        current = None
        try:
            current = pr_status(root, token)
        except GitError:
            pass
        if current and current.get("state") == "OPEN":
            code, out = _gh(root, token, f"gh pr edit {current['number']} --title {shlex.quote(title)} --body-file {shlex.quote(path)}",
                            retry=False)
            if code != 0:
                raise GitError(502, f"gh pr edit failed: {out}", git_auth.gh_fix(out, token))
            return {"number": current["number"], "url": current["url"], "updated": True}
        cmd = (f"gh pr create --title {shlex.quote(title)} --body-file {shlex.quote(path)} --head {shlex.quote(br)}"
               + (f" --base {shlex.quote(b)}" if b else "") + (" --draft" if draft else ""))
        code, out = _gh(root, token, cmd, 120, retry=False)
        if code != 0:
            raise GitError(502, f"gh pr create failed: {out}", git_auth.gh_fix(out, token))
        url = next((line for line in out.splitlines() if line.startswith("http")), "").strip()
        return {"url": url, "updated": False}
    finally:
        Path(path).unlink(missing_ok=True)


def wait_checks(root: str, token: str | None, timeout_s: int = 1800, every_s: int = 30, sleep=time.sleep) -> dict:
    """Wait until the pull request's checks are done: {ok, checks, failed}."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            st = pr_status(root, token)
        except GitError as exc:
            if not exc.transient or time.monotonic() > deadline:
                raise
            sleep(every_s)               # GitHub did not answer: the checks are still there, ask again
            continue
        if not st:
            raise GitError(404, "This branch has no pull request.", "Open it first (git:pr).")
        cks = st["checks"]
        if cks and st["checks_done"] == len(cks):
            failed = [c for c in cks if c["state"] in ("failure", "failed", "error", "cancelled", "timed_out", "action_required")]
            return {"ok": not failed, "checks": cks, "failed": failed, "url": st["url"]}
        if time.monotonic() > deadline:
            raise GitError(408, f"The checks were not done after {timeout_s // 60} minutes.",
                           f"{st['checks_done']} of {len(cks)} done; see {st['url']}.")
        sleep(every_s)


def cleanup(root: str) -> dict:
    """Delete local branches merged into the base (git branch -d, which keeps anything unmerged) and prune worktrees."""
    _need_repo(root)
    b = base(root)
    cur = git.branch(root)
    if not b:
        raise GitError(400, "keel found no base branch (main or master).")
    git.git(root, "worktree", "prune")
    gone = []
    for line in git.git(root, "branch", "--merged", b, "--format=%(refname:short)").stdout.splitlines():
        name = line.strip()
        if not name or name in (b, cur, base_name(root), *PROTECTED):
            continue
        if git.git(root, "worktree", "list", "--porcelain").stdout.count(f"branch refs/heads/{name}\n"):
            continue
        if git.git(root, "branch", "-d", name).returncode == 0:
            gone.append(name)
    return {"deleted": gone, "base": b}
