"""The Goals plugin's tools in keel's own MCP server (keel2 mcp, keel_engine/mcp_server.py), issue #98, through keel's
api (/api/projects/{pid}/goals, the Goals api part):

    read    keel_goals_list        a project's goals: status, how far, tokens and cost
            keel_goal              one goal: done when, limits, the plan that waits, its steps and what each flow says
    write   keel_goal_create       a new goal with its limits (nothing starts)
            keel_goal_plan         an agent splits the goal into steps (the planning flow; its plan waits for the person)
            keel_goal_start_step   start one step's flow (by its id or its number)
            keel_goal_skip_step    leave a step out (asks the person first)
            keel_goal_status       mark the goal done or stopped (asks first)

Nothing starts by itself: a person (or their client) starts each step, and the plan becomes steps only when a person
approves the planning flow's gate.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from keel_engine.mcp_api import ApiError, KeelApi, _project
from mcp.server.fastmcp import Context


def _q(s: str) -> str:
    return quote(str(s), safe="")


def _base(api: KeelApi, project: str | None) -> tuple[str, str]:
    pid = _project(api, project)["id"]
    return pid, f"/projects/{_q(pid)}/goals"


def _k(n) -> str:
    n = int(n or 0)
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def _progress(p: dict, g: dict) -> str:
    cap = f" of {_k(g['cap_tokens'])}" if g.get("cap_tokens") else ""
    flows = f" of {g['max_flows']}" if g.get("max_flows") else ""
    return (f"{p.get('done', 0)}/{p.get('total', 0)} steps · {_k(p.get('tokens'))} tokens{cap} · "
            f"${float(p.get('cost_usd') or 0):.2f} · {p.get('flows', 0)} flows{flows}")


def goals_list(api: KeelApi, project: str | None = None) -> str:
    pid, base = _base(api, project)
    rows = api.get(base) or []
    if not rows:
        return f"{pid} has no goal yet. keel_goal_create adds one."
    out = [f"{pid}'s goals:"]
    for r in rows:
        g, p = r["goal"], r["progress"]
        out.append(f"- {g['id']}: {g['title']} · {g['status']} · {_progress(p, g)}")
    return "\n".join(out)


def goal_view(api: KeelApi, goal: str, project: str | None = None) -> str:
    pid, base = _base(api, project)
    v = api.get(f"{base}/{_q(goal)}")
    g, p = v["goal"], v["progress"]
    out = [f"Goal {g['id']} on {pid}: {g['title']} ({g['status']})", f"Done when: {g['done_when']}"]
    if g.get("why"):
        out.append(f"Why: {g['why']}")
    out.append(f"How far: {_progress(p, g)}")
    if p.get("blocked"):
        out.append(f"No step may start now: {p['blocked']}")
    if g["status"] == "new":
        out.append("Not planned yet: keel_goal_plan lets an agent split it into steps.")
    if v.get("draft"):
        out.append("The proposed plan (it waits for the person at the planning flow's gate):")
        out += [f"  {i}. {s['title']} ({s['workflow']})" for i, s in enumerate(v["draft"], 1)]
    if g.get("plan_thread") and g["status"] == "planning":
        out.append(f"Planning flow: thread {g['plan_thread']} (keel_read_gate, then keel_resume to approve or reject).")
    if v.get("steps"):
        out.append("Steps:")
        for s in v["steps"]:
            flow = f" · thread {s['thread_id']} · {_k(s.get('tokens'))} tokens, ${float(s.get('cost_usd') or 0):.2f}" \
                if s.get("thread_id") else ""
            out.append(f"  {s['n']}. [{s['id']}] {s['title']} · {s['workflow']} · {s['state']}{flow}")
        nxt = next((s for s in v["steps"] if s["state"] == "todo"), None)
        if nxt and g["status"] not in ("done", "stopped") and not p.get("blocked"):
            out.append(f"Next: keel_goal_start_step step={nxt['n']}.")
    if v.get("limits"):
        out += ["What to know:", *[f"- {line}" for line in v["limits"]]]
    return "\n".join(out)


def goal_create(api: KeelApi, title: str, done_when: str, why: str | None = None, cap_tokens: int | None = None,
                max_flows: int | None = None, project: str | None = None) -> str:
    pid, base = _base(api, project)
    body = {"title": title, "done_when": done_when, "why": why or None, "cap_tokens": cap_tokens, "max_flows": max_flows}
    g = api.post(base, {k: v for k, v in body.items() if v is not None})
    return (f"Goal {g['id']} added on {pid}: {g['title']} ({g['status']}). Nothing started. keel_goal_plan lets an agent "
            "split it into steps.")


def goal_plan(api: KeelApi, goal: str, project: str | None = None) -> str:
    pid, base = _base(api, project)
    g = api.post(f"{base}/{_q(goal)}/plan", {})
    where = f" (thread {g['plan_thread']})" if g.get("plan_thread") else ""
    return (f"Planning {g['id']}: an agent reads {pid} and proposes steps{where}. Nothing changes and nothing starts; the "
            "plan waits for the person's OK at its gate.")


def _step_id(api: KeelApi, base: str, goal: str, step: str) -> str:
    """A step by its id, or by its number in the plan (1, 2 …)."""
    s = str(step).strip()
    if not s.isdigit():
        return s
    steps = api.get(f"{base}/{_q(goal)}").get("steps") or []
    hit = next((x for x in steps if int(x.get("n") or 0) == int(s)), None)
    if hit is None:
        raise ApiError(f"Goal {goal} has no step {s}: it has {len(steps)}.")
    return hit["id"]


def goal_start_step(api: KeelApi, goal: str, step: str, project: str | None = None) -> str:
    pid, base = _base(api, project)
    sid = _step_id(api, base, goal, step)
    v = api.post(f"{base}/{_q(goal)}/steps/{_q(sid)}/start", {})
    s = next((x for x in v.get("steps") or [] if x["id"] == sid), {})
    return (f"Started step {s.get('n', sid)} of {goal}: {s.get('title', '')} ({s.get('workflow', '')}), thread "
            f"{s.get('thread_id')}. Follow it with keel_status or keel_goal.")


def goal_skip_step(api: KeelApi, goal: str, step: str, project: str | None = None) -> str:
    pid, base = _base(api, project)
    sid = _step_id(api, base, goal, step)
    api.post(f"{base}/{_q(goal)}/steps/{_q(sid)}/skip", {})
    return f"Step {step} of {goal} is left out."


def goal_status(api: KeelApi, goal: str, status: str, project: str | None = None) -> str:
    if status not in ("done", "stopped"):
        raise ApiError("A goal is marked done or stopped.")
    pid, base = _base(api, project)
    g = api.post(f"{base}/{_q(goal)}/status/{status}", {})
    return f"Goal {g['id']} is {g['status']}."


def _acting(srv, guard):
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
        def keel_goals_list(project: str | None = None) -> str:
            """Goals plugin: a project's goals, with their status, how many steps are done, tokens and cost."""
            return guard(goals_list, api, project)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_goal(goal: str, project: str | None = None) -> str:
            """Goals plugin: one goal: done when, its limits, the plan that waits for approval, its steps and what each
            step's flow says, and what may start next."""
            return guard(goal_view, api, goal, project)

        return

    rw = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    rx = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
    act = _acting(srv, guard)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_goal_create(title: str, done_when: str, why: str | None = None, cap_tokens: int | None = None,
                               max_flows: int | None = None, project: str | None = None, ctx: Context | None = None) -> str:
        """Goals plugin: add a goal (what "done" means, and optional limits: most tokens for all its flows together,
        most flows). Nothing starts."""
        return await act(ctx, "keel_goal_create", f"add the goal {title!r}", goal_create, api, title, done_when, why,
                         cap_tokens, max_flows, project)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_goal_plan(goal: str, project: str | None = None, ctx: Context | None = None) -> str:
        """Goals plugin: an agent reads the project and splits the goal into steps (a planning flow). The plan becomes
        steps only when the person approves its gate."""
        return await act(ctx, "keel_goal_plan", f"plan the goal {goal}", goal_plan, api, goal, project)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_goal_start_step(goal: str, step: str, project: str | None = None, ctx: Context | None = None) -> str:
        """Goals plugin: start one step's flow (step: its id, or its number in the plan). It gets what is left of the
        goal's tokens as its cap."""
        return await act(ctx, "keel_goal_start_step", f"start step {step} of the goal {goal}", goal_start_step, api,
                         goal, step, project)

    @srv.tool(annotations=rx, structured_output=False)
    async def keel_goal_skip_step(goal: str, step: str, project: str | None = None, ctx: Context | None = None) -> str:
        """Goals plugin: leave a step out of the goal. The person is asked first."""
        return await act(ctx, "keel_goal_skip_step", f"leave out step {step} of the goal {goal}", goal_skip_step, api,
                         goal, step, project, confirm=True)

    @srv.tool(annotations=rx, structured_output=False)
    async def keel_goal_status(goal: str, status: Literal["done", "stopped"], project: str | None = None,
                               ctx: Context | None = None) -> str:
        """Goals plugin: mark the goal done (only the person knows it is reached) or stopped. The person is asked first."""
        return await act(ctx, "keel_goal_status", f"mark the goal {goal} {status}", goal_status, api, goal, status,
                         project, confirm=True)
