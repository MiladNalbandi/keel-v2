"""The harness flow's agent steps when a connected session does the work (data.runner == "session").

No model is called. The step publishes a work order (what keel's harness agent would be told: the request, the intent
note, the approved plan, the person's notes, the failing test output) as a pause of kind "session", and waits. The
session's report (keel_session_plan, keel_session_done) resumes it; the person can instead keep waiting, run this one
step on keel's harness (API key) or stop the flow (the Flow page, or the Inbox once the session is gone).

    plan  ──► work order "plan this" ─── keel_session_plan ──► data.plan_text, PLAN marker ──► plan_show, plan_gate
    build ──► work order "build the plan" ─ keel_session_done ─► keel's checks on the change ──► check (keel's tests)
    check fails ──► build again: work order "fix the failing tests" (the output in it), as for keel's own harness

The session runs its own tools, so keel cannot check them before they run. After each step keel checks the change: its
diff guard puts back what the phase does not allow (a plan step changes no file; .keel/, secrets, keel's never-touch
list), the project's edit tools run on the changed files, and a moved HEAD (the session committed) is said. All of it
goes to data.session_findings, which the done gate shows. keel makes the commits.

One interrupt per node run (compiler.py): "keep waiting" and "run it on keel's harness" run the step again from its top.
"""

from __future__ import annotations

import asyncio

from langgraph.graph import END

from .. import rules
from ..models.base import AgentResult
from ..tools import git, guard
from . import markers, prompts, sessions

TITLES = {"plan": "Waits for the connected session: plan this",
          "build": "Waits for the connected session: build the plan",
          "fix": "Waits for the connected session: fix the failing tests"}
SAID = {"plan": "keel → session: plan this", "build": "keel → session: build the plan",
        "fix": "keel → session: fix the failing tests"}
CHOICES = ["wait", "harness", "stop"]
CHOICE_INFO = [
    {"name": "wait", "label": "Keep waiting", "description": "the work order stays; the session can still pick it up",
     "why": "none", "default": True},
    {"name": "harness", "label": "Run this step on keel's harness", "description":
        "keel's own harness does this one step on the model's API (your API key, tokens are used); the next step "
        "goes back to the session", "why": "none", "default": False},
    {"name": "stop", "label": "Stop the flow", "description": "end the flow here; nothing is committed",
     "why": "optional", "default": False},
]
INSTRUCTIONS_MAX = 40_000
FINDINGS_MAX = 60


def applies(step, state) -> bool:
    """A harness agent step of a flow that runs with a connected session."""
    data = state.get("data") or {}
    return (data.get("runner") == "session" and step.kind == "agent" and step.session == "thread"
            and (step.agent or "") in sessions.SESSION_AGENTS)


def kind_of(step, state) -> str:
    if step.phase == "triage" or "PLAN" in [m.upper() for m in step.markers or []]:
        return "plan"
    last, fb = str(state.get("last_failure") or ""), str(state.get("feedback") or "")
    return "fix" if last and fb.startswith(last[:60]) else "build"


def order_key(step, state) -> str:
    """The same key on every run of this step until its report is used (the snapshot's name on disk)."""
    n = len((state.get("data") or {}).get("session_log") or [])
    return f"{step.id}-{n}"


def instructions(comp, step, state) -> str:
    """What keel's harness agent would be told for this step (prompts.task_prompt + the step's own instructions)."""
    ctx = comp.ctx
    text = prompts.task_prompt(agent=step.agent or "", phase=state.get("phase") or step.phase or "none",
                               step_name=step.name, title=ctx.title, root=ctx.root, ac=None,
                               acs=state.get("acs") or [], feedback=state.get("feedback"), request=ctx.request,
                               unlocks=state.get("unlocks") or [], pid=ctx.project_id or "",
                               writes_spec=prompts.spec_writer(step))
    asks = prompts.step_asks(step, lambda path: comp._seed_value("$" + path, state, None))
    return (f"{text}\n{asks}" if asks else text)[:INSTRUCTIONS_MAX]


def work_order(comp, step, state, kind: str, key: str, meta: dict) -> dict:
    ctx = comp.ctx
    data = state.get("data") or {}
    fb = str(state.get("feedback") or "").strip()
    order = {
        "id": f"{ctx.thread_id}:{key}", "flow": ctx.thread_id, "title": ctx.title, "project": ctx.project_id,
        "step": step.id, "step_name": step.name, "kind": kind, "said": SAID[kind], "phase": state.get("phase"),
        "root": ctx.root, "branch": state.get("branch") or None, "head": meta.get("head"),
        "published_at": meta.get("at"), "request": (ctx.request or "")[:prompts.REQUEST_MAX],
        "intent": data.get("intent"), "intent_note": data.get("intent_note"),
        "instructions": instructions(comp, step, state),
    }
    if kind != "plan" and data.get("plan_text"):
        order["plan"] = str(data["plan_text"])[:20_000]
    if kind == "fix":
        order["failure"] = fb[:12_000]
    elif fb:
        order["notes"] = fb[:6_000]
    return order


def question(step, kind: str, order: dict) -> dict:
    what = {"plan": "plan the change (read only)", "build": "build the approved plan, tests first",
            "fix": "fix what made keel's test run fail"}[kind]
    return {"step": step.id, "kind": "session", "title": TITLES[kind],
            "detail": (f"keel sent this step to the connected session: {what}. The flow goes on when the session "
                       "reports (keel_session_plan or keel_session_done). No model is called and no tokens are used "
                       "while it waits.\n\nYou can keep waiting, run this one step on keel's harness instead (your API "
                       "key), or stop the flow."),
            "options": ["approve", "reject"], "choices": list(CHOICES), "choice_info": [dict(c) for c in CHOICE_INFO],
            "labels": {"approve": "Keep waiting", "reject": "Stop the flow"},
            "summary": [f"⏳ {SAID[kind]}", "◆ no tokens used while it waits"], "order": order}


async def run(comp, i: int, step, state):
    """The step with a connected session. Returns (update, goto) like every step kind."""
    ctx = comp.ctx
    data = dict(state.get("data") or {})
    if data.get("session_harness") == step.id:
        # the person chose "run this step on keel's harness": this one run is keel's own agent step
        data.pop("session_harness", None)
        upd, goto = await comp._agent_step_now(i, step, {**state, "data": data})
        out = dict(upd.get("data") or data)
        out.pop("session_harness", None)
        log = list(out.get("session_log") or [])
        log.append({"step": step.id, "kind": kind_of(step, state), "by": "keel's harness", "at": _at(),
                    "status": "done", "summary": "This step ran on keel's harness (the person chose it)."})
        out["session_log"] = log[-40:]
        return {**upd, "data": out}, goto
    kind = kind_of(step, state)
    key = order_key(step, state)
    snap, meta = await asyncio.to_thread(sessions.take_or_load, ctx.root, ctx.thread_id, key)
    order = work_order(comp, step, state, kind, key, meta)
    answer, extra = comp._ask(state, question(step, kind, order), gate="session")
    answer = answer or {}
    payload = answer.get("payload") or {}
    choice = payload.get("choice")
    report = payload.get("report") if isinstance(payload.get("report"), dict) else None
    by = answer.get("by") or ("the connected session" if report else "you")
    if report is None and (answer.get("decision") == "reject" or choice == "stop"):
        _decided(ctx, step, "stop", answer.get("why"))
        await asyncio.to_thread(sessions.drop_snapshot, ctx.thread_id, key)
        why = (answer.get("why") or "").strip()
        note = "stopped while it waited for the connected session" + (f": {why}" if why else "")
        return {**extra, "status": "stopped", "note": note[:300], "error": note[:500]}, END
    if report is None and choice == "harness":
        _decided(ctx, step, "harness", answer.get("why"))
        await asyncio.to_thread(sessions.drop_snapshot, ctx.thread_id, key)
        return {**extra, "data": {**data, "session_harness": step.id},
                "note": f"{step.name}: runs on keel's harness this once (the person chose it)"}, step.id
    if report is None:
        # "keep waiting" (or a plain approve without a report): the same work order again, in a new run of this node
        _decided(ctx, step, "wait", answer.get("why"))
        return {**extra, "note": f"{step.name}: keeps waiting for the connected session"}, step.id
    if payload.get("gone"):
        _decided(ctx, step, "report", None)       # the Inbox asked about a gone session: it is answered now
    return await _finish(comp, i, step, state, kind, key, snap, meta, report, extra)


async def _finish(comp, i, step, state, kind, key, snap, meta, report: dict, extra: dict):
    ctx = comp.ctx
    data = dict(state.get("data") or {})
    want = "plan" if kind == "plan" else "done"
    if report.get("kind") not in (want, None):
        # never: the api checks it first; a report for another step goes back to waiting
        return {**extra, "note": f"{step.name}: the report was for another step; it waits"}, step.id
    text = str(report.get("plan") if kind == "plan" else report.get("summary") or "").strip()
    status = str(report.get("status") or "done")
    todos = sessions.todos_of(report.get("todos")) if report.get("todos") is not None else []
    client = str(report.get("client") or "the connected session")[:sessions.CLIENT_MAX]
    upd: dict = {**extra, "feedback": None, "last_answer": text[:8000]}
    state = {**state, "data": data}
    outs = comp._outputs(step, state, [AgentResult(text=text)], {})
    upd.update({k: v for k, v in outs.items() if k != "data"})
    data = dict(outs.get("data") or data)
    if kind == "plan":
        data["plan_text"] = text[:60_000]
    # keel's checks after the step: the diff guard (puts back what the phase does not allow), the project's edit tools
    findings = list(data.get("session_findings") or [])
    phase = state.get("phase") or step.phase or "none"
    refused = await asyncio.to_thread(guard.guard_diff, ctx.root, phase, snap, None, None, state.get("unlocks") or [],
                                      comp._readonly())
    for r in refused:
        ctx.emit("guard.refused", step=step.id, data={"tool": "diff-guard", "phase": phase, "source": "session", **r})
        findings.append({"step": step.id, "kind": "put-back", "path": r.get("path"),
                         "text": f"keel put back {r.get('path')}: {r.get('reason') or 'not allowed in this phase'}"})
    tools_note, lint = await comp._after_edit(step, {**state, "data": data}, snap, refused)
    if lint is not None:
        data["lint_notes"] = lint.get("lint_notes") or []
        findings += [{"step": step.id, "kind": "lint", "text": n} for n in data["lint_notes"]]
    moved = await asyncio.to_thread(_head_moved, ctx.root, meta.get("head"), phase, state.get("unlocks") or [])
    findings += [{"step": step.id, **f} for f in moved]
    data["session_findings"] = findings[-FINDINGS_MAX:]
    data["session_todos"] = {"step": step.id, "kind": kind, "todos": todos}
    log = list(data.get("session_log") or [])
    log.append({"step": step.id, "kind": kind, "by": client, "at": _at(), "published_at": meta.get("at"),
                "status": "blocked" if status == "blocked" or markers.blocked(text or "") else "done",
                "summary": _first_lines(text), "todos": todos,
                "findings": len(refused) + len(moved) + (len(data.get("lint_notes") or []) if lint is not None else 0)})
    data["session_log"] = log[-40:]
    data.pop("session_harness", None)
    upd["data"] = data
    note = f"connected session · {client}"
    said = (upd.get("markers") or {}).get(step.id) or {}
    if said:
        note += " · " + ", ".join(f"{k}: {v}" for k, v in said.items())
    if refused:
        note += f" · guard put back {len(refused)} file(s)"
    if tools_note:
        note += f" · {tools_note}"
    if moved:
        note += " · HEAD moved"
    upd["note"] = note[:300]
    ctx.emit("session.reported", step=step.id, data={"kind": kind, "client": client, "status": log[-1]["status"],
                                                     "summary": log[-1]["summary"], "todos": todos,
                                                     "findings": data["session_findings"][-10:]})
    await asyncio.to_thread(sessions.drop_snapshot, ctx.thread_id, key)
    if kind != "plan" and log[-1]["status"] == "blocked" and "done_show" in comp.nav.index:
        # a build the session could not finish: no test run; the person reads why at the done gate
        upd["note"] += " · blocked"
        return upd, comp.nav.jump("done_show", i)
    return upd, comp.nav.after(i)


def _head_moved(root: str, before: str | None, phase: str, unlocks: list) -> list[dict]:
    """A HEAD that moved while the session worked: it committed (or checked out something). keel makes the commits, so
    this is said at the gate, with the committed files the phase would not allow."""
    if not before or not git.is_repo(root):
        return []
    now = git.head(root)
    if not now or now == before:
        return []
    out = [{"kind": "head", "text": f"HEAD moved from {before[:7]} to {now[:7]} while the session worked: it made a "
                                     "commit or checked out another branch. keel makes the commits; look at "
                                     f"`git log {before[:7]}..{now[:7]}` before you commit."}]
    r = git.git(root, "diff", "--name-only", f"{before}..{now}")
    files = [f for f in (r.stdout or "").splitlines() if f.strip()] if r.returncode == 0 else []
    cfg = rules.load_config(root)
    for rel in files[:200]:
        v = rules.check_edit(phase, rel, cfg, exists=git.tracked_in_head(root, rel), unlocks=unlocks)
        if not v.ok:
            out.append({"kind": "committed", "path": rel,
                        "text": f"{rel} was committed by the session, but {v.reason or 'this phase does not allow it'}"})
    return out


def _decided(ctx, step, decision: str, why) -> None:
    """The Inbox and the api's notifications see the pause answered (the api sets the flow running again)."""
    ctx.emit("gate.decided", step=step.id, data={"gate": "connected session", "decision": decision,
                                                 **({"why": str(why)[:500]} if why else {})})


def _first_lines(text: str, n: int = 6) -> str:
    lines = [x for x in (text or "").strip().splitlines() if x.strip()]
    out = "\n".join(lines[:n])
    return (out[:1200] + ("…" if len(out) > 1200 or len(lines) > n else "")).strip()


def _at() -> str:
    from ..clock import utc_iso

    return utc_iso()

