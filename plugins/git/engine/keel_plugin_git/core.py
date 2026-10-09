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
import tempfile
import time
from pathlib import Path

from keel_engine import rules
from keel_engine.rules import checks
from keel_engine.tools import git, testcmd
from keel_engine.tools.agent_tools import command_env

DIFF_MAX = 40_000
PROTECTED = ("main", "master")
SHA = re.compile(r"^[0-9a-fA-F]{4,64}$")


def github_token(keys: dict | None) -> str | None:
    """The GitHub token a call carries (keys["github"]), read as keel's own ship step reads it."""
    from keel_engine.runtime.verdict_actions import github_token as token

    return token(keys or {})


class GitError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


def _need_repo(root: str):
    if not git.is_repo(root):
        raise GitError(400, "This project is not a git repository.", "Run git init in the project folder first.")


def base(root: str) -> str | None:
    """The branch work is compared with: base_branch in .keel/config.yml, else main, else master."""
    cfg = rules.load_config(root)
    for ref in [cfg.get("base_branch"), "main", "master"]:
        if ref and git.git(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0:
            return ref
    return None


def _count(root: str, rng: str) -> int:
    r = git.git(root, "rev-list", "--count", rng)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else 0


def _tail(text: str, n: int = 1500) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


# ------------------------------------------------------------------ reading

def status(root: str) -> dict:
    """{branch, base, upstream, ahead, behind (the upstream), base_ahead, base_behind, changes: [{path, status}]}."""
    _need_repo(root)
    br = git.branch(root)
    b = base(root)
    up = git.git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    upstream = up.stdout.strip() if up.returncode == 0 else None
    changes = []
    for line in git.git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines():
        if len(line) > 3 and not git.is_engine_file(line[3:].strip()):
            changes.append({"path": line[3:].strip().strip('"'), "status": line[:2].strip() or "?"})
    return {"branch": br, "base": b, "upstream": upstream,
            "ahead": _count(root, "@{u}..HEAD") if upstream else _count(root, f"{b}..HEAD") if b else 0,
            "behind": _count(root, "HEAD..@{u}") if upstream else 0,
            "base_ahead": _count(root, f"{b}..HEAD") if b else 0, "base_behind": _count(root, f"HEAD..{b}") if b else 0,
            "pushed": bool(upstream), "changes": changes[:200]}


def branches(root: str) -> list[dict]:
    """Local branches, newest first: {name, current, ahead, behind (against the base), date, subject}."""
    _need_repo(root)
    b = base(root)
    cur = git.branch(root)
    out = []
    fmt = "%(refname:short)%09%(committerdate:iso-strict)%09%(subject)"
    for line in git.git(root, "for-each-ref", "--sort=-committerdate", f"--format={fmt}", "refs/heads").stdout.splitlines()[:60]:
        name, date, subject = (line.split("\t") + ["", ""])[:3]
        out.append({"name": name, "current": name == cur, "date": date, "subject": subject,
                    "ahead": _count(root, f"{b}..{name}") if b and name != b else 0,
                    "behind": _count(root, f"{name}..{b}") if b and name != b else 0})
    return out


def log(root: str, n: int = 20, path: str | None = None, rng: str | None = None) -> list[dict]:
    _need_repo(root)
    args = ["log", f"-n{max(1, min(n, 100))}", "--format=%h%x09%an%x09%ad%x09%s", "--date=short"]
    if rng:
        args.append(rng)
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

def _gh_env(token: str | None) -> dict:
    if not token:
        raise GitError(400, "There is no GitHub token.", "Add one in Connections › GitHub (it needs repo access).")
    return {**command_env(), "GH_TOKEN": token, "GH_PROMPT_DISABLED": "1"}


def _gh(root: str, token: str | None, cmd: str, timeout: int = 60) -> tuple[int, str]:
    return testcmd.run(root, cmd, timeout, _gh_env(token))


def pr_status(root: str, token: str | None) -> dict | None:
    """The branch's pull request: {number, title, url, state, draft, review, checks: [{name, state}], comments: [...]},
    or None when the branch has none."""
    _need_repo(root)
    fields = "number,title,url,state,isDraft,reviewDecision,statusCheckRollup,comments,baseRefName,headRefName"
    code, out = _gh(root, token, f"gh pr view --json {fields}")
    if code != 0:
        if "no pull requests found" in out.lower() or "no open pull requests" in out.lower():
            return None
        raise GitError(502, f"gh could not read the pull request: {_tail(out, 300)}")
    try:
        d = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        raise GitError(502, "gh gave an answer keel cannot read.")
    checks_ = []
    for c in d.get("statusCheckRollup") or []:
        state = (c.get("conclusion") or c.get("state") or c.get("status") or "").lower()
        checks_.append({"name": c.get("name") or c.get("context") or "check", "state": state or "pending",
                        "url": c.get("detailsUrl") or c.get("targetUrl") or ""})
    comments = [{"author": (c.get("author") or {}).get("login", ""), "body": (c.get("body") or "")[:2000], "path": None, "line": None}
                for c in d.get("comments") or []]
    code2, out2 = _gh(root, token, f"gh api repos/{{owner}}/{{repo}}/pulls/{d.get('number')}/comments --paginate")
    if code2 == 0:
        try:
            for c in json.loads(out2[out2.index("["):]) if "[" in out2 else []:
                comments.append({"author": (c.get("user") or {}).get("login", ""), "body": (c.get("body") or "")[:2000],
                                 "path": c.get("path"), "line": c.get("line") or c.get("original_line")})
        except (ValueError, json.JSONDecodeError):
            pass
    done = [c for c in checks_ if c["state"] not in ("pending", "queued", "in_progress", "expected", "waiting", "")]
    failed = [c for c in checks_ if c["state"] in ("failure", "failed", "error", "cancelled", "timed_out", "action_required")]
    return {"number": d.get("number"), "title": d.get("title"), "url": d.get("url"), "state": d.get("state"),
            "draft": d.get("isDraft"), "review": d.get("reviewDecision"), "base": d.get("baseRefName"),
            "branch": d.get("headRefName"), "checks": checks_, "checks_done": len(done), "checks_failed": len(failed),
            "comments": comments[-50:]}


# ------------------------------------------------------------------ acting

def switch(root: str, branch: str, create: bool = False) -> dict:
    _need_repo(root)
    name = (branch or "").strip()
    if not name or git.git(root, "check-ref-format", "--branch", name).returncode != 0 or name.startswith("-"):
        raise GitError(400, f"{name or 'That'} is not a branch name git accepts.", "For example feat/euro-prices.")
    exists = git.git(root, "rev-parse", "--verify", "-q", f"refs/heads/{name}").returncode == 0
    if create and exists:
        raise GitError(409, f"The branch {name} exists already.", "Switch to it instead.")
    if not create and not exists:
        raise GitError(404, f"There is no branch {name}.", "Create it instead.")
    r = git.git(root, "switch", *(["-c"] if create else []), name)
    if r.returncode != 0:
        raise GitError(409, f"git could not switch to {name}: {_tail(r.stderr, 400)}",
                       "Commit or stash the changes git names first.")
    return {"branch": name, "created": create}


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
        raise GitError(400, f"git commit failed: {_tail(r.stderr or r.stdout, 400)}")
    return {"sha": (git.head(root) or "")[:12], "subject": first.strip(), "files": staged}


def sync(root: str, token: str | None = None) -> dict:
    """Merge the base branch (the remote's when there is one) into the current branch. A conflict is undone."""
    _need_repo(root)
    b = base(root)
    br = git.branch(root)
    if not b:
        raise GitError(400, "keel found no base branch (main or master).", "Set base_branch in .keel/config.yml.")
    if br == b:
        raise GitError(400, f"You are on the base branch ({b}).", "Switch to a feature branch first.")
    if git.dirty(root):
        raise GitError(409, "The branch has uncommitted changes.", "Commit or stash them first, so a merge cannot mix with them.")
    ref = b
    if git.git(root, "remote").stdout.strip():
        _fetch(root, b, token)
        if git.git(root, "rev-parse", "--verify", "-q", f"origin/{b}").returncode == 0:
            ref = f"origin/{b}"
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


def _https(url: str) -> str | None:
    """A GitHub remote as https (an ssh remote has no key in keel's container)."""
    m = re.match(r"^(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return f"https://github.com/{m.group(1)}.git" if m else None


def _auth_args(token: str | None) -> list[str]:
    if not token:
        return []
    helper = "!f() { echo username=x-access-token; echo \"password=$KEEL_GH_TOKEN\"; }; f"
    return ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]


def _fetch(root: str, ref: str, token: str | None):
    url = _remote_url(root)
    https = _https(url) if token else None
    env = {**command_env(), "KEEL_GH_TOKEN": token or "", "GIT_TERMINAL_PROMPT": "0"}
    if https:
        git.git(root, *_auth_args(token), "fetch", "-q", https, f"+refs/heads/{ref}:refs/remotes/origin/{ref}", env=env)
    else:
        git.git(root, "fetch", "-q", "origin", ref, env=env)


def push(root: str, token: str | None = None) -> dict:
    """`git push` of the current branch to origin, never with force and never to the base branch, main or master."""
    _need_repo(root)
    br = git.branch(root)
    b = base(root)
    if not br:
        raise GitError(400, "HEAD is not on a branch.", "Switch to a branch first.")
    if br in PROTECTED or br == b:
        raise GitError(403, f"keel never pushes to {br}.", "Push a feature branch and open a pull request.")
    url = _remote_url(root)
    if not url:
        raise GitError(400, "This repository has no remote called origin.")
    env = {**command_env(), "KEEL_GH_TOKEN": token or "", "GIT_TERMINAL_PROMPT": "0"}
    https = _https(url) if token else None
    target = https or "origin"
    r = git.git(root, *_auth_args(token if https else None), "push", "--porcelain", target, f"HEAD:refs/heads/{br}", env=env)
    if r.returncode != 0:
        out = _tail(r.stderr or r.stdout, 500)
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
    b = base(root)
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
            code, out = _gh(root, token, f"gh pr edit {current['number']} --title {shlex.quote(title)} --body-file {shlex.quote(path)}")
            if code != 0:
                raise GitError(502, f"gh pr edit failed: {_tail(out, 400)}")
            return {"number": current["number"], "url": current["url"], "updated": True}
        cmd = (f"gh pr create --title {shlex.quote(title)} --body-file {shlex.quote(path)} --head {shlex.quote(br)}"
               + (f" --base {shlex.quote(b)}" if b else "") + (" --draft" if draft else ""))
        code, out = _gh(root, token, cmd, 120)
        if code != 0:
            raise GitError(502, f"gh pr create failed: {_tail(out, 400)}")
        url = next((line for line in out.splitlines() if line.startswith("http")), "").strip()
        return {"url": url, "updated": False}
    finally:
        Path(path).unlink(missing_ok=True)


def wait_checks(root: str, token: str | None, timeout_s: int = 1800, every_s: int = 30, sleep=time.sleep) -> dict:
    """Wait until the pull request's checks are done: {ok, checks, failed}."""
    deadline = time.monotonic() + timeout_s
    while True:
        st = pr_status(root, token)
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
        if not name or name in (b, cur, *PROTECTED):
            continue
        if git.git(root, "worktree", "list", "--porcelain").stdout.count(f"branch refs/heads/{name}\n"):
            continue
        if git.git(root, "branch", "-d", name).returncode == 0:
            gone.append(name)
    return {"deleted": gone, "base": b}
