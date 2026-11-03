"""The Git plugin's tools in keel's own MCP server (keel2 mcp, keel_engine/mcp_server.py): through keel's api, under
keel's rules; the acting ones wait for the person's answer in keel's Inbox."""

from __future__ import annotations

import time

from mcp.server.fastmcp import Context
from keel_engine.mcp_server import KeelApi, _ask_person, _plugin_on, _project, inbox_refusal


def git_status(api: KeelApi, project: str | None = None) -> str:
    from .tools import status_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "git")
    return status_text(api.get(f"/projects/{pid}/git/status"))


def pr_status(api: KeelApi, project: str | None = None) -> str:
    from .tools import pr_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "git")
    return pr_text((api.get(f"/projects/{pid}/git/pr") or {}).get("pr"))


def git_act(api: KeelApi, op: str, project: str | None = None, sleep=time.sleep, **body) -> str:
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "git")
    what = {"commit": f"commit: {body.get('message', '')}", "push": "push the branch",
            "pr": f"open the pull request: {body.get('title', '')}"}[op]
    ok, why = _ask_person(api, pid, f"Claude Code: {what.split(':')[0]}?", what, sleep)
    if not ok:
        return inbox_refusal(why)
    r = api.post(f"/projects/{pid}/git/{op}", body)
    if op == "commit":
        return f"Committed {r['sha'][:7]} {r['subject']} ({len(r['files'])} file(s))."
    if op == "push":
        return f"Pushed {r['branch']} ({r['sha'][:7]})."
    return f"{'Updated' if r.get('updated') else 'Opened'} the pull request: {r.get('url')}"



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
    """The read tools, or (write) the acting ones."""
    from mcp.types import ToolAnnotations

    if not write:
        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_git_status(project: str | None = None) -> str:
            """Git plugin: the project's branch, how far it is from the base branch and its remote, and what changed."""
            return guard(git_status, api, project)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_pr_status(project: str | None = None) -> str:
            """Git plugin: the branch's pull request: state, review, CI checks and review comments."""
            return guard(pr_status, api, project)

        return
    rw = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    rx = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
    act = _acting(srv, guard)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_git_commit(message: str, project: str | None = None, ctx: Context | None = None) -> str:
        """Git plugin: commit every change (keel's secret check, the author from keel's settings), after the person's
        OK in keel's Inbox."""
        return await act(ctx, "keel_git_commit", "commit", git_act, api, "commit", project, message=message)

    @srv.tool(annotations=rx, structured_output=False)
    async def keel_git_push(project: str | None = None, ctx: Context | None = None) -> str:
        """Git plugin: push the branch (never with force, never to main or master), after the person's OK."""
        return await act(ctx, "keel_git_push", "push the branch", git_act, api, "push", project, confirm=True)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_pr_create(title: str, body: str = "", project: str | None = None, ctx: Context | None = None) -> str:
        """Git plugin: open the branch's pull request (or update its title and body), after the person's OK."""
        return await act(ctx, "keel_pr_create", "open the pull request", git_act, api, "pr", project, title=title, body=body)
