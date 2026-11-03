"""The CI/CD plugin's tools in keel's own MCP server (keel2 mcp, keel_engine/mcp_server.py): through keel's api, under
keel's rules; the re-run waits for the person's answer in keel's Inbox."""

from __future__ import annotations

import time

from mcp.server.fastmcp import Context
from keel_engine.mcp_server import KeelApi, _ask_person, _plugin_on, _project, inbox_refusal


def ci_runs(api: KeelApi, project: str | None = None, branch: str | None = None) -> str:
    from .tools import runs_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "ci")
    return runs_text(api.get(f"/projects/{pid}/ci/runs", branch=branch))


def ci_failure(api: KeelApi, project: str | None = None, run_id: int | None = None) -> str:
    from .tools import failure_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "ci")
    if not run_id:
        failed = [r for r in api.get(f"/projects/{pid}/ci/runs") or [] if r.get("failed")]
        if not failed:
            return "No failed pipeline: they pass, or none ran yet."
        run_id = failed[0]["id"]
    return failure_text(api.get(f"/projects/{pid}/ci/runs/{int(run_id)}"))


def ci_rerun(api: KeelApi, run_id: int, project: str | None = None, sleep=time.sleep) -> str:
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "ci")
    ok, why = _ask_person(api, pid, f"Claude Code: run the failed jobs of run #{int(run_id)} again?", f"gh run rerun {int(run_id)} --failed", sleep)
    if not ok:
        return inbox_refusal(why)
    api.post(f"/projects/{pid}/ci/runs/{int(run_id)}/rerun", {})
    return f"The failed jobs of run #{int(run_id)} run again."



def _acting(srv, guard):
    """keel's way to run an acting tool (keel 0.16.1: who acts, and with `confirm` the person's OK through the client
    first; PartServer.act in keel_engine/mcp_server.py). A keel before 0.16.1 has none: then the tool runs as it did."""
    act = getattr(srv, "act", None)

    async def run(ctx, name: str, what: str, fn, *a, confirm: bool = False, **kw) -> str:
        if act is None:
            return guard(fn, *a, **kw)
        return await act(ctx, name, what, fn, *a, confirm=confirm, **kw)

    return run


def register(srv, api: KeelApi, guard, write: bool) -> None:
    """The read tools, or (write) the acting one."""
    from mcp.types import ToolAnnotations

    if not write:
        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_ci_runs(project: str | None = None, branch: str | None = None) -> str:
            """CI/CD plugin: the project's newest pipeline runs on GitHub Actions (workflow, branch, result, link)."""
            return guard(ci_runs, api, project, branch)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_ci_failure(project: str | None = None, run_id: int | None = None) -> str:
            """CI/CD plugin: why a run failed (failed jobs, steps and the end of the log); default the newest failed run."""
            return guard(ci_failure, api, project, run_id)

        return
    rw = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    act = _acting(srv, guard)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_ci_rerun(run_id: int, project: str | None = None, ctx: Context | None = None) -> str:
        """CI/CD plugin: run the failed jobs of a pipeline again, after the person's OK in keel's Inbox."""
        return await act(ctx, "keel_ci_rerun", "rerun the pipeline", ci_rerun, api, run_id, project)
