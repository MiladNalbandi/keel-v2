"""The engine's side of connected sessions (runtime/sessions.py): presence, the waiting work orders, the reports.

    GET  /sessions?project=                     the project's sessions (connected or not) and its waiting work orders
    POST /sessions/open        {project, client, folder}   a session registers (keel_session_open)
    POST /sessions/{id}/next   {}               heartbeat + the next work order or none (keel_session_next polls it)
    POST /sessions/flows/{tid}/plan  {session, plan, todos}            finishes a plan step
    POST /sessions/flows/{tid}/todo  {session, todos}                  live progress of the step
    POST /sessions/flows/{tid}/done  {session, summary, status, todos} finishes a build or fix step

A report is refused, with a plain reason, when the flow does not wait for a connected session now, when it waits for
another kind of report (a plan, not a build), or when another connected session took the work order.
"""

from __future__ import annotations

import asyncio
import logging

from . import markers, sessions
from .service import EngineError

log = logging.getLogger(__name__)

WATCH_EVERY = 30.0


def hub(engine) -> sessions.Hub:
    h = getattr(engine, "sessions", None)
    if h is None:
        h = engine.sessions = sessions.Hub()
    return h


# ------------------------------------------------------------------ the flow pauses for a session (Engine._drive)

def paused(engine, ctx, waiting: dict) -> None:
    """The flow waits for a connected session: remember the work order (the next poll hands it out) and say so. Not a
    gate.waiting: nobody is asked yet, so the Inbox and the notifications stay quiet until the session is gone."""
    order = dict(waiting.get("order") or {})
    h = hub(engine)
    old = h.waits.get(ctx.thread_id)
    w = sessions.Wait(tid=ctx.thread_id, project=ctx.project_id, step=str(waiting.get("step") or ""),
                      kind=str(order.get("kind") or ""), order=order, qid=waiting.get("id"), since=sessions.now())
    if old and old.order.get("id") == order.get("id"):
        w.claimed = old.claimed         # the same work order again ("keep waiting"): the session that took it keeps it
    h.waits[ctx.thread_id] = w
    h.not_session.pop(ctx.thread_id, None)
    ctx.emit("session.waiting", step=w.step, data={"kind": w.kind, "said": order.get("said"), "order": order.get("id"),
                                                   "step_name": order.get("step_name")})


def ended(engine, tid: str) -> None:
    """The flow no longer waits there (answered, stopped, rewound)."""
    h = hub(engine)
    h.waits.pop(tid, None)


async def _rebuild(engine, project: str | None) -> None:
    """After an engine restart the hub is empty while flows still wait for a session: find them again (cheap: a flow
    whose pause is not a session's is remembered by its updated_at)."""
    h = hub(engine)
    sql = "select thread_id, project_id, updated_at from keel_threads where status = 'waiting'"
    args: tuple = ()
    if project:
        sql += " and project_id = ?"
        args = (project,)
    async with engine.conn.execute(sql, args) as cur:
        rows = await cur.fetchall()
    for tid, pid, updated in rows:
        if tid in h.waits or h.not_session.get(tid) == updated or tid in engine.tasks:
            continue
        try:
            graph = await engine._graph(tid)
            snap = await graph.aget_state(engine._cfg(tid))
        except Exception:  # noqa: BLE001 - a flow that cannot be read is no work order
            h.not_session[tid] = updated
            continue
        w = engine._waiting(snap)
        if w and w.get("kind") == "session":
            order = dict(w.get("order") or {})
            h.waits[tid] = sessions.Wait(tid=tid, project=pid, step=str(w.get("step") or ""), kind=str(order.get("kind") or ""),
                                         order=order, qid=w.get("id"), since=sessions.now())
        else:
            h.not_session[tid] = updated


async def orders(engine, project: str | None) -> list[sessions.Wait]:
    await _rebuild(engine, project)
    h = hub(engine)
    return sorted((w for w in h.waits.values() if not project or w.project == project), key=lambda w: w.since)


def order_view(w: sessions.Wait, full: bool = True) -> dict:
    o = dict(w.order)
    if not full:
        o.pop("instructions", None)
        o.pop("plan", None)
        o.pop("failure", None)
        o.pop("request", None)
    return {**o, "waiting_since": sessions.iso(w.since), "claimed_by": w.claimed}


# ------------------------------------------------------------------ the api's routes

async def list_view(engine, project: str) -> dict:
    h = hub(engine)
    h.forget_old()
    at = sessions.now()
    waiting = await orders(engine, project)
    live = h.connected(project)
    return {"project": project, "connected_s": sessions.CONNECTED_S, "gone_s": sessions.GONE_S,
            "connected": live.view(at) if live else None,
            "sessions": [s.view(at) for s in h.of_project(project)][:20],
            "waiting": [order_view(w, full=False) for w in waiting]}


def open_session(engine, project: str, client: str | None, folder: str | None) -> dict:
    if not project:
        raise EngineError(400, "project is missing.", "Pass the keel project this session works for.")
    s = hub(engine).open(project, client, folder)
    return s.view()


def _session(engine, sid: str) -> sessions.Session:
    s = hub(engine).seen(sid)
    if not s:
        raise EngineError(404, "keel does not know this session (keel restarted, or it was never opened).",
                          "Call keel_session_open again.")
    return s


async def next_order(engine, sid: str) -> dict:
    """The heartbeat of a waiting session, and its next work order: the one it took already, else the oldest one of
    its project that no other connected session took."""
    s = _session(engine, sid)
    h = hub(engine)
    found = None
    for w in await orders(engine, s.project):
        other = h.sessions.get(w.claimed) if w.claimed and w.claimed != s.id else None
        if other and sessions.now() - other.seen <= sessions.CONNECTED_S:
            continue
        found = w
        break
    if found:
        found.claimed = s.id
        return {"session": s.view(), "order": order_view(found)}
    return {"session": s.view(), "order": None}


async def _waiting_for(engine, tid: str, sid: str, want: tuple[str, ...]) -> tuple[sessions.Session, sessions.Wait, dict]:
    s = _session(engine, sid)
    row = await engine._row(tid)
    if row["project_id"] != s.project:
        raise EngineError(409, f"Flow {tid} belongs to project {row['project_id']}, not to {s.project} (this session's).")
    if tid in engine.tasks:
        raise EngineError(409, "This flow is running, not waiting for the connected session.",
                          "Call keel_session_next: it hands out a work order when one waits.")
    graph = await engine._graph(tid)
    snap = await graph.aget_state(engine._cfg(tid))
    waiting = engine._waiting(snap)
    if row["status"] in ("stopped", "failed", "done") or not waiting:
        raise EngineError(409, f"This flow does not wait for anything now (it is {row['status']}).",
                          "Call keel_session_next for the next work order.")
    if waiting.get("kind") != "session":
        raise EngineError(409, f"This flow waits for the person (\"{waiting.get('title')}\"), not for the connected session.",
                          "Nothing to report here; call keel_session_next.")
    order = dict(waiting.get("order") or {})
    if order.get("kind") not in want:
        tool = "keel_session_plan" if order.get("kind") == "plan" else "keel_session_done"
        raise EngineError(409, f"This flow waits for a {order.get('kind')} report, not this one.", f"Use {tool}.")
    h = hub(engine)
    w = h.waits.get(tid)
    if w and w.claimed and w.claimed != s.id:
        other = h.sessions.get(w.claimed)
        if other and sessions.now() - other.seen <= sessions.CONNECTED_S:
            raise EngineError(409, f"Another connected session ({other.client}) took this work order.",
                              "Call keel_session_next: it hands out the work orders no other session took.")
    return s, w, {**waiting, "order": order}


async def todo(engine, tid: str, sid: str, raw) -> dict:
    s, _w, waiting = await _waiting_for(engine, tid, sid, ("plan", "build", "fix"))
    try:
        items = sessions.todos_of(raw)
    except ValueError as exc:
        raise EngineError(400, str(exc)) from exc
    hub(engine).todos[tid] = {"step": waiting.get("step"), "todos": items, "at": sessions.iso(sessions.now()),
                              "client": s.client}
    ctx = await engine._context(tid)
    ctx.emit("session.todo", step=waiting.get("step"), data={"client": s.client, "todos": items})
    return {"ok": True, "todos": items}


async def report(engine, tid: str, sid: str, kind: str, body: dict) -> dict:
    """keel_session_plan (kind plan) or keel_session_done (kind done): checked here, then the waiting step resumes."""
    want = ("plan",) if kind == "plan" else ("build", "fix")
    s, w, waiting = await _waiting_for(engine, tid, sid, want)
    try:
        todos = sessions.todos_of(body.get("todos")) if body.get("todos") is not None else \
            (hub(engine).todos.get(tid) or {}).get("todos") or []
    except ValueError as exc:
        raise EngineError(400, str(exc)) from exc
    rep: dict = {"kind": kind, "client": s.client, "session": s.id, "todos": todos}
    if kind == "plan":
        plan = str(body.get("plan") or "").strip()
        if not plan:
            raise EngineError(400, "The plan is empty.", "Send the whole plan in Markdown, ending with PLAN: ready or PLAN: blocked <why>.")
        if not markers.parse(plan, ["PLAN"]).get("PLAN"):
            raise EngineError(400, "The plan does not end with its result line.",
                              "End the plan with one line: PLAN: ready, or PLAN: blocked <why>.")
        rep["plan"] = plan[:60_000]
    else:
        summary = str(body.get("summary") or "").strip()
        status = str(body.get("status") or "done").strip().lower()
        if status not in ("done", "blocked"):
            raise EngineError(400, f'status "{status}" is not done or blocked.')
        if not summary:
            raise EngineError(400, "The summary is empty.",
                              "Say in a few lines what you changed, the test command you ran and its result.")
        rep.update(summary=summary[:20_000], status=status)
    gone = bool(w and w.flagged)
    state = await engine.resume(tid, "approve", None, {"report": rep, **({"gone": True} if gone else {})},
                                question_id=waiting.get("id"))
    hub(engine).todos.pop(tid, None)
    ended(engine, tid)
    return {"ok": True, "thread_id": tid, "status": state.get("status"), "step": waiting.get("step")}


# ------------------------------------------------------------------ the flow's view (Engine.state)

def view(engine, tid: str, project: str, data: dict, waiting: dict | None) -> dict:
    """What the Flow page shows of the connected session: who it is, the work order that waits, the live todo list,
    and what the session did so far."""
    h = hub(engine)
    at = sessions.now()
    w = h.waits.get(tid) if waiting and waiting.get("kind") == "session" else None
    s = (h.sessions.get(w.claimed) if w and w.claimed else None) or (h.of_project(project) or [None])[0]
    out: dict = {"runner": "session", "client": (s.client if s else data.get("session_client")) or None,
                 "session": s.view(at) if s else None, "connected": bool(s and at - s.seen <= sessions.CONNECTED_S),
                 "log": list(data.get("session_log") or [])[-40:],
                 "findings": list(data.get("session_findings") or [])[-sessions.TODO_MAX:]}
    live = h.todos.get(tid)
    if waiting and waiting.get("kind") == "session":
        order = dict(waiting.get("order") or {})
        out["order"] = order
        out["todos"] = (live or {}).get("todos") or []
        out["gone"] = is_gone(h, project, w, at)
        if w:
            out["waiting_since"] = sessions.iso(w.since)
    else:
        last = data.get("session_todos") or {}
        out["todos"] = last.get("todos") or []
    return out


def is_gone(h: sessions.Hub, project: str, w: sessions.Wait | None, at: float | None = None) -> bool:
    at = sessions.now() if at is None else at
    if not w:
        return False
    if w.flagged:
        return True
    return at - w.since >= sessions.GONE_S and not h.connected(project, within=sessions.GONE_S)


# ------------------------------------------------------------------ a session that is gone: the Inbox asks

async def check_gone(engine) -> list[str]:
    """Every step that waits for a session while none was seen for GONE_S: once per work order, a gate.waiting (the
    api puts the flow in the Inbox and notifies) whose answers are keep waiting, keel's harness, stop."""
    h = hub(engine)
    flagged = []
    at = sessions.now()
    for tid, w in list(h.waits.items()):
        if w.flagged or not is_gone(h, w.project, w, at):
            continue
        w.flagged = True
        flagged.append(tid)
        try:
            ctx = await engine._context(tid)
        except EngineError:
            continue
        mins = int((at - w.since) // 60)
        ctx.emit("session.gone", step=w.step, data={"kind": w.kind, "minutes": mins})
        ctx.emit("gate.waiting", step=w.step, data={
            "kind": "session", "title": GONE_TITLE, "detail": gone_detail(mins), "flow": ctx.workflow.flow,
            "step_name": w.order.get("step_name"), "phase": w.order.get("phase"),
            "summary": [f"✕ No connected session for {mins} minutes", "◆ Keep waiting, run this step on keel's harness, or stop"]})
    return flagged


GONE_TITLE = "The connected session is gone"


def gone_detail(mins: int) -> str:
    return (f"This step waits for the connected session, and no session was seen for {mins} minutes. Keep waiting (the "
            "work order stays for the session), run this one step on keel's harness (your API key, tokens are used), or "
            "stop the flow.")


async def watch(engine) -> None:
    while True:
        await asyncio.sleep(WATCH_EVERY)
        try:
            await check_gone(engine)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the watcher never stops for one bad flow
            log.warning("the connected-session watcher failed once", exc_info=True)
