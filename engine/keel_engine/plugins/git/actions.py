"""The Git plugin's workflow steps. Their settings sit in the step's `with:`.

    git:branch     {name? | pattern?}   switch to the flow's branch, creating it: `name`, else the pattern (default the
                                        branch_pattern setting) with {slug} = the flow's title
    git:sync       {}                   merge the base branch in; a conflict fails the step with the files
    git:push       {}                   push the branch: never with force, never to main or master
    git:pr         {title?, draft?}     open the pull request with keel's PR body, or update its title and body
    git:pr-checks  {minutes?: 30}       wait for the pull request's CI; a failed check fails the step
    git:cleanup    {}                   delete local branches already merged into the base; prune worktrees

git:push and git:pr follow Settings › Push and open PR at ship: `never` skips them, `ask` needs the gate before the
step approved (or asks here), `auto` goes on; the Auto run mode never pushes, whatever the setting.
"""

from __future__ import annotations

import asyncio
import re

from ...runtime import run_mode as run_modes
from .. import github_token
from . import core


def _result(ok: bool, note: str, detail: str = "", update: dict | None = None, ask: dict | None = None):
    from ...runtime.actions import ActionResult

    return ActionResult(ok, note, detail, update or {}, ask=ask)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48] or "work"


def _fail(exc: core.GitError):
    return _result(False, str(exc), exc.hint or "")


def _branch(a):
    p = a.params or {}
    name = str(p.get("name") or "").strip()
    if not name:
        pattern = str(p.get("pattern") or (a.settings or {}).get("branch_pattern") or "feat/{slug}")
        name = pattern.replace("{slug}", _slug(a.title)).replace("{flow}", a.flow or "flow").replace("{user}", "keel")
    try:
        if (core.status(a.root)["branch"] or "") == name:
            return _result(True, f"Already on {name}.")
        exists = name in [b["name"] for b in core.branches(a.root)]
        core.switch(a.root, name, create=not exists)
    except core.GitError as exc:
        return _fail(exc)
    return _result(True, f"{'Switched to' if exists else 'Created'} the branch {name}.")


def _sync(a):
    try:
        r = core.sync(a.root, github_token(a.keys))
    except core.GitError as exc:
        return _fail(exc)
    return _result(True, f"Merged {r['from']} into {r['branch']}." if r["merged"] else f"{r['branch']} already has {r['from']}.")


def _may_send(a, what: str):
    """None when the step may push or open the PR now; else the result that skips it or asks the person."""
    setting = str((a.settings or {}).get("push_pr") or "ask")
    if run_modes.normalize((a.settings or {}).get("run_mode")) == "auto":
        return _result(True, f"Run mode auto: keel does not {what} by itself.", "Do it from Code › Git, or change the run mode.")
    if setting == "never":
        return _result(True, f"Settings › Push and open PR at ship is never: keel does not {what}.")
    if setting == "auto" or (a.state or {}).get("gate_approved"):
        return None
    if ((a.state or {}).get("plugin_ok") or {}).get(a.step) == what:
        return None
    detail = f"This step would {what} for the branch {core.status(a.root)['branch']}. Approve to do it; reject to stop the flow."
    return _result(False, f"keel asks before it may {what}.", detail, ask={
        "type": "plugin", "kind": "gate", "title": f"May keel {what}?", "detail": detail, "fingerprint": what,
        "labels": {"approve": "Yes", "reject": "Stop the flow"}})


def _push(a):
    wait = _may_send(a, "push the branch")
    if wait:
        return wait
    try:
        r = core.push(a.root, github_token(a.keys))
    except core.GitError as exc:
        return _fail(exc)
    return _result(True, f"Pushed {r['branch']} ({r['sha'][:7]}).")


def _pr(a):
    wait = _may_send(a, "open the pull request")
    if wait:
        return wait
    p = a.params or {}
    body = (a.state or {}).get("pr_body") or ""
    if not body:
        from ...runtime.verdict_actions import pr_body

        st = core.status(a.root)
        body = pr_body(a.root, a.key, a.state or {}, a.title, st["base"], getattr(a, "request", "") or "",
                       run_modes.normalize((a.settings or {}).get("run_mode")), a.thread_id)
    try:
        r = core.pr(a.root, github_token(a.keys), str(p.get("title") or a.title or ""), body, bool(p.get("draft")))
    except core.GitError as exc:
        return _fail(exc)
    data = {**(a.data or {}), "pr_url": r.get("url")}
    return _result(True, f"{'Updated' if r['updated'] else 'Opened'} the pull request: {r.get('url')}", "", {"data": data})


def _checks(a):
    minutes = int((a.params or {}).get("minutes") or 30)
    try:
        r = core.wait_checks(a.root, github_token(a.keys), timeout_s=max(1, min(minutes, 120)) * 60)
    except core.GitError as exc:
        return _fail(exc)
    lines = "\n".join(f"- {c['name']}: {c['state']}" for c in r["checks"])
    if r["ok"]:
        return _result(True, f"All {len(r['checks'])} check(s) passed.", lines)
    names = ", ".join(c["name"] for c in r["failed"])
    return _result(False, f"{len(r['failed'])} check(s) failed: {names}.", f"{lines}\n\n{r['url']}")


def _cleanup(a):
    try:
        r = core.cleanup(a.root)
    except core.GitError as exc:
        return _fail(exc)
    return _result(True, f"Deleted {len(r['deleted'])} merged branch(es)." if r["deleted"] else "No merged branch to delete.",
                   "\n".join(r["deleted"]))


async def git_branch(a):
    return await asyncio.to_thread(_branch, a)


async def git_sync(a):
    return await asyncio.to_thread(_sync, a)


async def git_push(a):
    return await asyncio.to_thread(_push, a)


async def git_pr(a):
    return await asyncio.to_thread(_pr, a)


async def git_pr_checks(a):
    return await asyncio.to_thread(_checks, a)


async def git_cleanup(a):
    return await asyncio.to_thread(_cleanup, a)


ACTIONS = {"git:branch": git_branch, "git:sync": git_sync, "git:push": git_push, "git:pr": git_pr,
           "git:pr-checks": git_pr_checks, "git:cleanup": git_cleanup}
PARAMS = {
    "git:branch": {"name": "optional", "pattern": "optional"},
    "git:sync": {},
    "git:push": {},
    "git:pr": {"title": "optional", "draft": "optional"},
    "git:pr-checks": {"minutes": "optional"},
    "git:cleanup": {},
}
