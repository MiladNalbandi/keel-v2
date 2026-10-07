"""The CI/CD plugin's core: the project's pipelines on GitHub Actions (gh, with the GitHub token of Connections › GitHub).

    runs(root, token, branch?)    the newest workflow runs: {id, workflow, branch, sha, event, status, conclusion, url, ...}
    run(root, token, id)          one run with its jobs; for a failed run the failed steps and the tail of their log
    rerun(root, token, id)        run the failed jobs again (gh run rerun --failed)
    for_head(root, token)         the runs of the current HEAD commit (what a fix waits for)
    wait(root, token, ...)        until the runs of HEAD are done: {ok, runs, failed}

Reading is free. Re-running is the only action here; a fix is a flow (content/plugins/ci/workflows/ci-fix.yaml) that
commits and pushes under keel's rules.
"""

from __future__ import annotations

import json
import re
import time

from ...tools import git, testcmd
from ...tools.agent_tools import command_env

LOG_TAIL = 8000
FAILED = {"failure", "timed_out", "cancelled", "startup_failure", "action_required"}
RUN_FIELDS = "databaseId,headBranch,headSha,status,conclusion,workflowName,event,createdAt,updatedAt,url,displayTitle,attempt"


class CiError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


def _gh(root: str, token: str | None, cmd: str, timeout: int = 60) -> str:
    if not token:
        raise CiError(400, "There is no GitHub token.", "Add one in Connections › GitHub (it needs repo and actions access).")
    if not git.is_repo(root):
        raise CiError(400, "This project is not a git repository.")
    code, out = testcmd.run(root, cmd, timeout, {**command_env(), "GH_TOKEN": token, "GH_PROMPT_DISABLED": "1"})
    if code != 0:
        low = out.lower()
        if "no default remote" in low or "not a git repository" in low or "could not determine" in low:
            raise CiError(400, "This project has no GitHub remote.", "keel reads pipelines from the repository's origin on GitHub.")
        raise CiError(502, f"gh failed: {out.strip()[-400:]}")
    return out


def _json(out: str):
    start = min([i for i in (out.find("["), out.find("{")) if i >= 0] or [0])
    try:
        return json.loads(out[start:])
    except json.JSONDecodeError as exc:
        raise CiError(502, "gh gave an answer keel cannot read.") from exc


def _run(d: dict) -> dict:
    return {"id": d.get("databaseId"), "workflow": d.get("workflowName"), "title": d.get("displayTitle"),
            "branch": d.get("headBranch"), "sha": (d.get("headSha") or "")[:12], "event": d.get("event"),
            "status": d.get("status"), "conclusion": d.get("conclusion") or None, "url": d.get("url"),
            "attempt": d.get("attempt"), "created_at": d.get("createdAt"), "updated_at": d.get("updatedAt"),
            "failed": (d.get("conclusion") or "") in FAILED}


def runs(root: str, token: str | None, branch: str | None = None, limit: int = 20) -> list[dict]:
    b = f" --branch {re.sub(r'[^A-Za-z0-9._/-]', '', branch)}" if branch else ""
    return [_run(d) for d in _json(_gh(root, token, f"gh run list --limit {max(1, min(limit, 50))}{b} --json {RUN_FIELDS}"))]


def run(root: str, token: str | None, run_id: int) -> dict:
    """One run with its jobs; a failed job carries its failed steps and, for the run, the tail of the failed log."""
    rid = int(run_id)
    d = _json(_gh(root, token, f"gh run view {rid} --json {RUN_FIELDS},jobs"))
    out = _run(d)
    jobs = []
    for j in d.get("jobs") or []:
        failed_steps = [s.get("name") for s in j.get("steps") or [] if (s.get("conclusion") or "") in FAILED]
        jobs.append({"id": j.get("databaseId"), "name": j.get("name"), "status": j.get("status"),
                     "conclusion": j.get("conclusion") or None, "url": j.get("url"), "failed_steps": failed_steps})
    out["jobs"] = jobs
    out["log"] = ""
    if out["failed"]:
        try:
            log = _gh(root, token, f"gh run view {rid} --log-failed", timeout=120)
            out["log"] = _clean_log(log)
        except CiError as exc:
            out["log"] = f"(keel could not read the log: {exc})"
    return out


def _clean_log(text: str) -> str:
    """`gh run view --log-failed` lines are `job\tstep\ttimestamp line`: keep job · step and the line, the tail only."""
    lines = []
    for raw in text.splitlines():
        parts = raw.split("\t", 2)
        if len(parts) == 3:
            msg = re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?", "", parts[2])
            lines.append(f"{parts[0]} · {parts[1]} | {msg}")
        else:
            lines.append(raw)
    text = "\n".join(lines)
    return text if len(text) <= LOG_TAIL else "…\n" + text[-LOG_TAIL:]


def rerun(root: str, token: str | None, run_id: int) -> dict:
    _gh(root, token, f"gh run rerun {int(run_id)} --failed")
    return {"id": int(run_id), "rerun": True}


def for_head(root: str, token: str | None) -> list[dict]:
    head = (git.head(root) or "")[:12]
    br = git.branch(root)
    return [r for r in runs(root, token, br, 30) if r["sha"] and head.startswith(r["sha"][:7])]


def wait(root: str, token: str | None, timeout_s: int = 1800, every_s: int = 30, sleep=time.sleep, grace_s: int = 180) -> dict:
    """Until every run of HEAD is done. No run within `grace_s` (a push takes a moment to start CI) is an error."""
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    while True:
        rs = for_head(root, token)
        if rs and all(r["status"] == "completed" for r in rs):
            failed = [r for r in rs if r["failed"]]
            return {"ok": not failed, "runs": rs, "failed": failed}
        if not rs and time.monotonic() - started > grace_s:
            raise CiError(404, "No pipeline ran for this commit.", "Is the branch pushed, and does a workflow run on it?")
        if time.monotonic() > deadline:
            raise CiError(408, f"The pipelines were not done after {timeout_s // 60} minutes.")
        sleep(every_s)
