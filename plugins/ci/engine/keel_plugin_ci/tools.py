"""The CI/CD plugin's read tools for KeelBot and agents (the MCP server keel-ci, server.py). Every answer is
text for a model; a refusal says why instead of failing the call."""

from __future__ import annotations

from keel_engine.tools import git

from . import core
from .core import PluginError, github_token


def runs_text(rs: list[dict]) -> str:
    if not rs:
        return "No pipeline runs."
    return "\n".join(f"- #{r['id']} {r['workflow']} on {r['branch']} ({r['sha'][:7]}): "
                     f"{r['conclusion'] or r['status']} · {r['created_at']} · {r['url']}" for r in rs)


def failure_text(r: dict) -> str:
    lines = [f"Run #{r['id']} {r['workflow']} on {r['branch']} ({r['sha'][:7]}): {r['conclusion'] or r['status']} · {r['url']}"]
    for j in r.get("jobs") or []:
        if (j.get("conclusion") or "") in core.FAILED:
            lines.append(f"- job {j['name']} failed" + (f" at: {', '.join(j['failed_steps'])}" if j["failed_steps"] else ""))
    if r.get("log"):
        lines += ["The failed steps' log (the end):", r["log"]]
    return "\n".join(lines)


def newest_failed(root: str, token: str | None, branch: str | None = None) -> dict | None:
    for r in core.runs(root, token, branch or git.branch(root), 20):
        if r["failed"]:
            return r
    return None


def call(c: dict, tool: str, args: dict) -> str:
    root, token = c["root"], github_token(c["keys"])
    try:
        if tool == "ci_runs":
            return runs_text(core.runs(root, token, str(args.get("branch") or "") or None, int(args.get("limit") or 15)))
        if tool == "ci_failure":
            rid = int(args.get("run_id") or 0)
            if not rid:
                r = newest_failed(root, token, str(args.get("branch") or "") or None)
                if not r:
                    return "No failed run on this branch: its pipelines pass (or none ran yet)."
                rid = r["id"]
            return failure_text(core.run(root, token, rid))
        return f"Unknown tool {tool}."
    except (core.CiError, PluginError) as exc:
        return f"Refused: {exc}" + (f" {exc.hint}" if getattr(exc, "hint", "") else "")
