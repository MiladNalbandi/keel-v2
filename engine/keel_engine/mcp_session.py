"""0.29 connected sessions in keel's MCP server: the person's own Claude Code or OpenCode does the harness flow's plan
and build for keel (runtime/sessions.py). keel never starts that session and never touches its login.

    keel_session_open   register this MCP server's client with keel (its name from the MCP initialize)
    keel_session_next   wait for the next work order (a long poll: it calls keel every few seconds, which keeps the
                        session "connected"), then return it
    keel_session_plan   finish a plan step with the plan (Markdown, ending with PLAN: ready | PLAN: blocked <why>)
    keel_session_todo   the step's live todo list (shown on keel's Flow page)
    keel_session_done   finish a build or fix step; keel then runs its tests, its checks, and makes the commit

One session id per MCP server process (one process per client: `keel2 mcp`), kept in `state`.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .mcp_api import ApiError, KeelApi, _project, client_folder

NEXT_WAIT = 240          # keel_session_next's default wait: it answers before the client's tool timeout (~5 min)
NEXT_WAIT_MAX = 600
POLL = 3.0               # seconds between two calls to keel while it waits (each one is a heartbeat)

PROTOCOL = """How it works (you work for keel; keel stays the control room):
1. Call keel_session_next. It waits up to a few minutes for a work order and returns it, or "nothing yet": then call it again.
2. Do the work order in the project folder it names, with your own tools (read, edit, run the tests). Follow its
   instructions: they are what keel's own harness agent would get (the request, the approved plan, the person's notes,
   keel's failing test output).
3. While you work, send your todo list with keel_session_todo(flow, todos): keel shows it live on the Flow page.
4. Report: a plan step with keel_session_plan(flow, plan, todos) (the whole plan in Markdown, last line "PLAN: ready" or
   "PLAN: blocked <why>"); a build or fix step with keel_session_done(flow, summary, status) (what you changed, the test
   command you ran and its result; status blocked when you could not finish).
5. Never commit, push, switch branches or change .keel/: keel checks the change after each step against the project's
   rules (it puts back what the step may not change), runs its own tests and makes the commits.
6. Then call keel_session_next again. The person approves the plan and the change in keel; their notes come back to you
   as a new work order."""

KIND_SAID = {"plan": "keel → session: plan this", "build": "keel → session: build the plan",
             "fix": "keel → session: fix the failing tests"}


def _open(api: KeelApi, project: str | None, client: str | None, state: dict) -> dict:
    p = _project(api, project)
    got = api.post("/sessions/open", {"project": p["id"], "client": client or "an MCP client",
                                      "folder": client_folder() or ""}) or {}
    state.update(id=got.get("id"), project=p["id"], client=got.get("client"))
    return got


def open_session(api: KeelApi, project: str | None, client: str | None, state: dict) -> str:
    got = _open(api, project, client, state)
    waiting = (api.get("/sessions", project=state["project"]) or {}).get("waiting") or []
    head = (f"keel knows this session now: {got.get('client')} on project {state['project']} (session {got.get('id')}). "
            "It counts as connected while it calls keel_session_next (seen within the last 2 minutes).")
    now = (f"{len(waiting)} work order(s) wait now: call keel_session_next." if waiting else
           "No work order waits yet: the person starts a flow with \"Run with: Connected session\" in keel's Start a flow "
           "(or keel_start_flow with engine='session'). Call keel_session_next to wait for it.")
    return f"{head}\n\n{PROTOCOL}\n\n{now}"


def _session(api: KeelApi, project: str | None, client: str | None, state: dict) -> str:
    if not state.get("id") or (project and project not in (state.get("project"),)):
        _open(api, project, client, state)
    return state["id"]


def poll_once(api: KeelApi, project: str | None, client: str | None, state: dict) -> dict | None:
    """One call: the heartbeat and the next work order (None: nothing yet). keel restarted: open again, once."""
    sid = _session(api, project, client, state)
    try:
        got = api.post(f"/sessions/{sid}/next", {}) or {}
    except ApiError as exc:
        if exc.status != 404:
            raise
        _open(api, state.get("project") or project, client, state)
        got = api.post(f"/sessions/{state['id']}/next", {}) or {}
    return got.get("order")


def next_order(api: KeelApi, project: str | None, client: str | None, state: dict, wait: float = NEXT_WAIT,
               sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> str:
    """The long poll, for a caller without an event loop (tests); the MCP tool runs poll_once in its own loop."""
    deadline = clock() + max(0.0, min(float(wait), NEXT_WAIT_MAX))
    while True:
        order = poll_once(api, project, client, state)
        if order:
            return order_text(order)
        if clock() >= deadline:
            return nothing_yet(wait)
        sleep(POLL)


def nothing_yet(wait: float) -> str:
    return (f"Nothing yet: no work order came in {int(wait)} s. Call keel_session_next again (you stay connected while "
            "you do).")


def order_text(o: dict) -> str:
    kind = str(o.get("kind") or "")
    flow = o.get("flow")
    where = f"{o.get('root')}" + (f" (branch {o.get('branch')})" if o.get("branch") else "")
    cwd = client_folder()
    root = str(o.get("root") or "")
    lines = [f"# Work order: {KIND_SAID.get(kind, kind)}",
             f"Flow {flow} · \"{o.get('title')}\" · step {o.get('step_name') or o.get('step')} · project {o.get('project')}",
             f"Work in: {where}"]
    if cwd and root and not (cwd == root or cwd.startswith(root.rstrip("/") + "/")):
        lines.append(f"keel sees your folder as {cwd}, not the flow's folder: work in {root} (or, if you cannot, report "
                     "with status blocked and say so).")
    if kind == "plan":
        lines.append(f'Report with: keel_session_plan(flow="{flow}", plan="<the whole plan in Markdown>", todos=[...]). '
                     "Plan only: do not change any file in this step (keel puts back what a plan step changes).")
    else:
        lines.append(f'Report with: keel_session_done(flow="{flow}", summary="<what you changed, the test command and its '
                     'result>", status="done" | "blocked").')
    lines.append(f'While you work: keel_session_todo(flow="{flow}", todos=[{{"text": "...", "status": "pending" | '
                 '"in_progress" | "done"}}]). Never commit or push: keel commits after its checks.')
    lines.append("\n## Instructions (what keel's own harness agent gets for this step)\n" + str(o.get("instructions") or ""))
    if o.get("plan"):
        lines.append("\n## The approved plan\n" + str(o["plan"]))
    if o.get("notes"):
        lines.append("\n## The person's notes\n" + str(o["notes"]))
    if o.get("failure"):
        lines.append("\n## keel's test run failed\n" + str(o["failure"]))
    return "\n".join(lines)


def _flow_path(flow: str, what: str) -> str:
    flow = str(flow or "").strip()
    if not flow:
        raise ApiError("flow is missing: the work order's flow (its thread id).")
    return f"/sessions/flows/{flow}/{what}"


def plan(api: KeelApi, project: str | None, client: str | None, state: dict, flow: str, text: str,
         todos: list[Any] | None) -> str:
    sid = _session(api, project, client, state)
    api.post(_flow_path(flow, "plan"), {"session": sid, "plan": text, "todos": todos})
    return ("keel has the plan: the person approves it (or sends a note back) on keel's plan gate. Call keel_session_next "
            "now: the build comes as the next work order once the plan is approved.")


def todo(api: KeelApi, project: str | None, client: str | None, state: dict, flow: str, todos: list[Any]) -> str:
    sid = _session(api, project, client, state)
    got = api.post(_flow_path(flow, "todo"), {"session": sid, "todos": todos}) or {}
    items = got.get("todos") or []
    done = sum(1 for t in items if t.get("status") == "done")
    return f"keel shows your list: {done} of {len(items)} done."


def done(api: KeelApi, project: str | None, client: str | None, state: dict, flow: str, summary: str, status: str,
         todos: list[Any] | None) -> str:
    sid = _session(api, project, client, state)
    api.post(_flow_path(flow, "done"), {"session": sid, "summary": summary, "status": status, "todos": todos})
    then = ("keel now runs the project's tests and its checks on your change; a failure comes back to you as a fix work "
            "order, else the person looks at the change and keel commits it." if status != "blocked" else
            "keel shows the person why you are blocked at its done gate; their note comes back as a new work order.")
    return f"keel has your report. {then} Call keel_session_next now."
