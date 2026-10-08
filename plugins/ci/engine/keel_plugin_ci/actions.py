"""The CI/CD plugin's workflow steps. Their settings sit in the step's `with:`.

    ci:status   {branch?}        the newest runs of HEAD (or a branch): passes when all are done and green
    ci:wait     {minutes?: 30}   wait for the runs of HEAD (after a push); a failed run fails the step and its log goes to
                                 data.ci_failure
    ci:logs     {run?}           why the newest failed run of this branch (or run `run`) failed, into data.ci_failure
    ci:rerun    {run?}           run the failed jobs of that run again
"""

from __future__ import annotations

import asyncio

from keel_engine.tools import git

from . import core, tools
from .core import github_token


def _result(ok: bool, note: str, detail: str = "", update: dict | None = None):
    from keel_engine.runtime.actions import ActionResult

    return ActionResult(ok, note, detail, update or {})


def _fail(exc: core.CiError):
    return _result(False, str(exc), exc.hint or "")


def _status(a):
    p = a.params or {}
    try:
        branch = str(p.get("branch") or "")
        rs = core.runs(a.root, github_token(a.keys), branch, 20) if branch else core.for_head(a.root, github_token(a.keys))
    except core.CiError as exc:
        return _fail(exc)
    if branch:
        latest: dict[str, dict] = {}
        for r in rs:
            latest.setdefault(r["workflow"], r)
        rs = list(latest.values())
    if not rs:
        return _result(False, "No pipeline ran for this commit yet.", "Push the branch, or wait a moment for CI to start.")
    open_ = [r for r in rs if r["status"] != "completed"]
    failed = [r for r in rs if r["failed"]]
    text = tools.runs_text(rs)
    if failed:
        return _result(False, f"{len(failed)} pipeline(s) failed: {', '.join(r['workflow'] for r in failed)}.", text)
    if open_:
        return _result(False, f"{len(open_)} pipeline(s) still running.", text)
    return _result(True, f"All {len(rs)} pipeline(s) passed.", text)


def _with_failure(a, r: dict) -> dict:
    full = core.run(a.root, github_token(a.keys), r["id"])
    return {"data": {**(a.data or {}), "ci_failure": tools.failure_text(full)}}


def _wait(a):
    minutes = int((a.params or {}).get("minutes") or 30)
    try:
        r = core.wait(a.root, github_token(a.keys), timeout_s=max(1, min(minutes, 120)) * 60)
        text = tools.runs_text(r["runs"])
        if r["ok"]:
            return _result(True, f"CI passed: {len(r['runs'])} pipeline(s).", text)
        upd = _with_failure(a, r["failed"][0])
    except core.CiError as exc:
        return _fail(exc)
    return _result(False, f"CI failed: {', '.join(x['workflow'] for x in r['failed'])}.", upd["data"]["ci_failure"], upd)


def _logs(a):
    p = a.params or {}
    try:
        if p.get("run"):
            r = {"id": int(p["run"])}
        else:
            r = tools.newest_failed(a.root, github_token(a.keys), git.branch(a.root))
            if not r:
                return _result(False, f"No failed pipeline on {git.branch(a.root)}: nothing to fix.",
                               "Its pipelines pass, or none ran yet.")
        upd = _with_failure(a, r)
    except core.CiError as exc:
        return _fail(exc)
    first = upd["data"]["ci_failure"].splitlines()[0]
    return _result(True, f"Read the failure: {first}", upd["data"]["ci_failure"], upd)


def _rerun(a):
    p = a.params or {}
    try:
        rid = int(p.get("run") or 0)
        if not rid:
            r = tools.newest_failed(a.root, github_token(a.keys), git.branch(a.root))
            if not r:
                return _result(True, "No failed pipeline to run again.")
            rid = r["id"]
        core.rerun(a.root, github_token(a.keys), rid)
    except core.CiError as exc:
        return _fail(exc)
    return _result(True, f"The failed jobs of run #{rid} run again.")


async def ci_status(a):
    return await asyncio.to_thread(_status, a)


async def ci_wait(a):
    return await asyncio.to_thread(_wait, a)


async def ci_logs(a):
    return await asyncio.to_thread(_logs, a)


async def ci_rerun(a):
    return await asyncio.to_thread(_rerun, a)


ACTIONS = {"ci:status": ci_status, "ci:wait": ci_wait, "ci:logs": ci_logs, "ci:rerun": ci_rerun}
# what each needs in `with:` is PARAMS in this package's PART (__init__.py)
