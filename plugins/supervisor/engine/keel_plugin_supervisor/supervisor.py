"""The Supervisor's watcher: it hears keel's events, looks at each gate a supervised flow waits at, and answers it or
calls the person.

    event (EventBus.listen) -> queue -> look(thread)        one look per pause; one turn per flow at a time;
                                                             at most MAX_PARALLEL turns in the whole keel
    look: policy -> repeat limits -> budget -> brief -> model turn (read-only) -> verdict -> checks -> answer | escalate

Switches (switch.py; all off until the person turns them on):

    keel-wide off  -> nothing at all: no event listener, no model call, no Inbox card; turning it off cancels every look
    flow on        =  keel-wide on AND the plugin on for the project (Control › Plugins)
                      AND (the flow's own switch when it has one, else the project's)

Turning a project or a flow off cancels its running look at once (the model call stops; nothing is answered after it).
The flow's settings.supervisor = {on, mode, model, effort, cap_tokens, max_answers, policy, estimate} (routes.py). Mode "shadow" (the default) never answers: each verdict is a
suggestion for the person. Mode "act" answers through Engine.resume(by="supervisor"), as a person would; a 409 there
means the person answered first, and that is final.

Events (thread_id = the flow's, step = the waiting step): supervisor.looking, supervisor.decided, supervisor.escalated,
supervisor.paused, and supervisor.started / supervisor.finished around each model turn (call_id; the api's agent call).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import uuid

from keel_engine import extensions, models
from keel_engine.models.base import AgentRequest, ModelError
from keel_engine.runtime import side_usage
from keel_engine.runtime.compiler import budget_tokens, stronger
from keel_engine.runtime.service import EngineError
from keel_engine.tools.agent_tools import ToolBox

from . import brief, ledger, policy, switch, verdict

log = logging.getLogger("keel.supervisor")

MODES = ("shadow", "act")
DEFAULTS = {"on": None, "mode": "shadow", "effort": "high", "cap_tokens": 150_000, "max_answers": 20, "estimate": 30_000,
            "day_cap_tokens": 500_000}
MAX_SAME_QUESTION = 2       # answers to the very same question in one flow, then it asks
MAX_SAME_TITLE = 3          # answers to the same step + criterion + title (a loop whose question id changes)
MAX_PARALLEL = 2
WAKE = ("gate.waiting", "thread.done", "thread.failed")
SOURCE = "supervisor"


def settings_of(raw: dict | None) -> dict:
    s = {**DEFAULTS, **{k: v for k, v in (raw or {}).items() if v is not None}}
    s["mode"] = s["mode"] if s["mode"] in MODES else "shadow"
    return s


def model_for(sset: dict, project_models: dict | None) -> dict:
    """The model of a look: the flow's own (settings.supervisor.model), else the project's supervisor model, else one step
    above the project's default (0.18.64: Opus for Claude, high effort for Codex and Copilot). It judges a gate the other
    agents could not settle, so it should not be the same model that wrote the work."""
    pm = project_models or {}
    if sset.get("model"):
        return dict(sset["model"])
    if pm.get("supervisor"):
        return dict(pm["supervisor"])
    return stronger(dict(pm.get("default") or {}))


class Supervisor:
    def __init__(self, bus, engine):
        self.bus = bus
        self.engine = engine
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.sem = asyncio.Semaphore(MAX_PARALLEL)
        self.locks: dict[str, asyncio.Lock] = {}
        self.seen: set[tuple] = set()          # (thread, question id, checkpoints): one look per pause
        self.tasks: set[asyncio.Task] = set()
        self.looks: dict[asyncio.Task, tuple[str, str]] = {}   # running look -> (thread, project): what an off cancels
        self.worker: asyncio.Task | None = None
        self.active = False                    # the keel-wide switch: listening or not

    # ------------------------------------------------------------ life

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.worker = asyncio.create_task(self._work())
        if switch.get_keel():
            self._activate()                   # a restart: listen again, and look at the flows that wait now

    async def close(self) -> None:
        self.active = False
        self.bus.unlisten(self._hear)
        for t in [self.worker, *self.tasks]:
            if t:
                t.cancel()
        await asyncio.gather(*[t for t in [self.worker, *self.tasks] if t], return_exceptions=True)

    def _activate(self) -> None:
        self.active = True
        self.bus.listen(self._hear)
        self._spawn(self._waiting_now())

    def _deactivate(self) -> None:
        """Keel-wide off: stop listening, forget what waited in the queue, cancel every look."""
        self.active = False
        self.bus.unlisten(self._hear)
        while not self.queue.empty():
            self.queue.get_nowait()
        self.seen.clear()
        for t in list(self.looks):
            t.cancel()

    def stop_where(self, thread: str | None = None, project: str | None = None) -> int:
        """Cancels the running looks of one flow or one project (its switch went off). Returns how many."""
        n = 0
        for t, (tid, pid) in list(self.looks.items()):
            if (thread and tid == thread) or (project and pid == project):
                t.cancel()
                n += 1
        return n

    async def set_switch(self, keel: bool | None = None, projects: dict | None = None) -> dict:
        """The api's switches changed (routes.py POST /supervisor/switch)."""
        switch.put(keel, projects)
        now = switch.get_keel()
        if now and not self.active:
            self._activate()
        elif not now and self.active:
            self._deactivate()
        elif now:
            for pid in projects or {}:
                p = switch.project(pid) or {}
                if not p.get("on") or p.get("plugin") is False:
                    self.stop_where(project=pid)
            self._spawn(self._waiting_now())   # a project that went on: its waiting flow is looked at now
        return self.switch_view()

    def switch_view(self, pid: str | None = None) -> dict:
        out: dict = {"keel": switch.get_keel(), "active": self.active,
                     "looking": sorted({tid for tid, _ in self.looks.values()})}
        if pid:
            out["project"] = switch.project(pid)
        return out

    async def idle(self, timeout: float = 30.0) -> None:
        """Waits until no look runs or waits in the queue (tests)."""
        end = time.monotonic() + timeout
        while (self.tasks or not self.queue.empty()) and time.monotonic() < end:
            await asyncio.sleep(0.02)

    def _hear(self, ev: dict) -> None:
        if self.active and ev.get("type") in WAKE and self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.queue.put_nowait, ev)

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def _work(self) -> None:
        while True:
            ev = await self.queue.get()
            if ev["type"] == "gate.waiting":
                self._spawn(self.look(ev["thread_id"]))

    async def _waiting_now(self) -> None:
        if not self.active:
            return
        try:
            async with self.engine.conn.execute("select thread_id from keel_threads where status = 'waiting'") as cur:
                tids = [r[0] for r in await cur.fetchall()]
        except Exception:
            log.exception("could not list the waiting flows")
            return
        for tid in tids:
            self._spawn(self.look(tid, trigger="restart"))

    # ------------------------------------------------------------ settings

    async def settings(self, tid: str) -> dict:
        """The flow's Supervisor settings, `on` = whether it supervises this flow now (see the switches above), and
        `switches` = each switch and, when it is off, the first one that is off (`off_by`)."""
        ctx = await self.engine._context(tid)
        s = settings_of((ctx.settings or {}).get("supervisor"))
        proj = switch.project(ctx.project_id) or {}
        plugin = proj.get("plugin")
        if plugin is None:                     # the api has not said: the plugins the flow started with
            plugin = extensions.on(ctx.settings, "supervisor")
        sw = {"keel": switch.get_keel(), "plugin": bool(plugin), "project": bool(proj.get("on")), "flow": s["on"]}
        own = sw["flow"] if sw["flow"] is not None else sw["project"]
        s["on"] = bool(sw["keel"] and sw["plugin"] and own)
        sw["off_by"] = None if s["on"] else ("keel" if not sw["keel"] else "plugin" if not sw["plugin"]
                                             else "flow" if sw["flow"] is False else "project")
        s["plugin_on"] = sw["plugin"]
        s["switches"] = sw
        return s

    # ------------------------------------------------------------ one look

    async def look(self, tid: str, trigger: str = "gate.waiting") -> dict | None:
        if not self.active:
            return None
        me = asyncio.current_task()
        try:
            ctx = await self.engine._context(tid)
            if me:
                self.looks[me] = (tid, ctx.project_id)
            async with self.locks.setdefault(tid, asyncio.Lock()):
                return await self._look(tid, trigger)
        except EngineError:
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("supervisor look at %s failed", tid)
            return None
        finally:
            if me:
                self.looks.pop(me, None)

    async def _look(self, tid: str, trigger: str) -> dict | None:
        try:
            st = await self.engine.state(tid)
        except EngineError:
            return None
        q = st.get("waiting")
        if st.get("status") != "waiting" or not q:
            return None
        sset = await self.settings(tid)
        if not sset["on"]:
            return None
        key = (tid, q.get("id"), st.get("checkpoints"))
        if key in self.seen:
            return None
        self.seen.add(key)
        kind = await self._kind(tid, st, q)
        rule = policy.rule(kind, q.get("title") or "", sset)
        ac = st.get("ac")
        ac_id = ac.get("id") if isinstance(ac, dict) else ac
        row = {"project_id": st["project_id"], "thread_id": tid, "question_id": q.get("id"), "step": q.get("step"),
               "ac": ac_id, "kind": kind, "title_key": f"{q.get('step')}|{ac_id}|{q.get('title')}", "trigger": trigger}

        if rule == "never":
            return self._escalate(row, "never-kind", summary=f"{q.get('title')}: only you can answer this.")
        if policy.CAP_TITLE.search(q.get("title") or ""):        # rule() says never already; kept as a second lock
            return self._escalate(row, "never-kind")
        if ledger.answered(tid, q.get("id") or "") >= MAX_SAME_QUESTION or \
                ledger.answered_title(tid, row["title_key"]) >= MAX_SAME_TITLE:
            return self._escalate(row, "repeat", summary=f"I answered \"{q.get('title')}\" before and it came back. Please look.")
        if ledger.answers(tid) >= int(sset["max_answers"]):
            self._emit(row, "supervisor.paused", {"code": "too-many", "answers": ledger.answers(tid)})
            return self._escalate(row, "too-many")

        own = side_usage.of_source(tid, SOURCE)["tokens"]
        usage = st.get("usage") or {}
        flow_cap = int(usage.get("cap_tokens") or 0)
        flow_used = budget_tokens(usage) + side_usage.total(tid)["tokens"]
        money = {"flow_used": flow_used, "flow_cap": flow_cap, "own_used": own, "own_cap": int(sset["cap_tokens"])}
        today = ledger.tokens_today(st["project_id"])
        if today >= int(sset["day_cap_tokens"]):
            return self._escalate(row, "budget-day", summary=f"My budget for this project today is used up ({today:,} of {int(sset['day_cap_tokens']):,} tokens).")
        if own >= int(sset["cap_tokens"]):
            return self._escalate(row, "budget-own", summary=f"My budget for this flow is used up ({own:,} of {int(sset['cap_tokens']):,} tokens).")
        if flow_cap and flow_used + int(sset["estimate"]) > flow_cap:
            return self._escalate(row, "budget-flow", summary=f"Looking would pass the flow's cap ({flow_used:,} of {flow_cap:,} tokens).")

        earlier = ledger.list_rows(thread_id=tid, limit=8)
        text = brief.build(st, q, kind, rule, (await self.engine._context(tid)).root, money, earlier)
        self._emit(row, "supervisor.looking", {"kind": kind, "rule": rule, "mode": sset["mode"]})
        call_id = uuid.uuid4().hex
        try:
            v, spent, model, ms, err = await self._turn(tid, st, sset, text, row, call_id)
        except asyncio.CancelledError:
            # a switch went off while it thought: nothing is answered; the turn's tokens are booked by _turn
            self._record({**row, "action": "escalate", "summary": "Turned off while it was looking."}, "cancelled", "turned-off")
            raise
        row.update(tokens=spent["tokens"], cost_usd=spent["cost_usd"], ms=ms, model=model.get("model"),
                   effort=model.get("effort"))
        if err:
            return self._escalate(row, "model-error", summary=err)
        if v is None:
            return self._escalate(row, "no-verdict")
        row.update(action=v["action"], why=v["why"], confidence=v["confidence"], evidence=v["evidence"],
                   summary=v["summary"], choice=v["choice"], answers=v["answers"], commands=v["commands"])
        decision, why, payload = verdict.to_answer(v)
        row["decision"] = decision
        code = None
        if v["action"] == "escalate":
            code = "model-escalated"
        elif rule != "may":
            code = "policy-escalate"
        elif v["confidence"] != "high":
            code = "low-confidence"
        else:
            code = policy.evidence_problem(v["evidence"], (await self.engine._context(tid)).root, st.get("acs") or [])
        if not code and policy.offered_problem(q, decision, why, payload):
            code = "not-offered"
        if not code and sset["mode"] != "act":
            code = "shadow"
        if code:
            return self._escalate(row, code, suggestion={"decision": decision, "why": why, "payload": payload}
                                  if v["action"] != "escalate" else None)
        if not (await self.settings(tid))["on"]:      # switched off while it thought
            return self._record({**row, "summary": "Turned off before it answered."}, "cancelled", "turned-off")
        return await self._apply(row, st, q, decision, why, payload)

    async def _kind(self, tid: str, st: dict, q: dict) -> str:
        ctx = await self.engine._context(tid)
        wf = ctx.workflow
        sid = str(q.get("step") or "")
        step = wf.step(sid[: -len("__fix")] if sid.endswith("__fix") else sid)
        if sid.endswith("__fix") or step is None:
            step_for = None
        else:
            step_for = step
        prev = None
        if step_for is not None:
            i = next((n for n, s in enumerate(wf.steps) if s.id == step_for.id), 0)
            prev = wf.steps[i - 1] if i else None
        per_ac = bool(step_for is not None and step_for.per_ac and st.get("ac"))
        return policy.kind_of(q, step_for, wf.flow, prev.actions() if prev else (), per_ac)

    async def _turn(self, tid: str, st: dict, sset: dict, text: str, row: dict, call_id: str):
        """The model's look: read-only tools, the thread's keys. Returns (verdict, spent, model, ms, error). The api
        stores it as an agent call (supervisor.started / supervisor.finished), so the Budget page counts it."""
        ctx = await self.engine._context(tid)
        model = model_for(sset, ctx.models)
        if sset.get("effort"):
            model["effort"] = sset["effort"]
        model = models.effective(model)
        tmp = tempfile.mkdtemp(prefix="keel-supervisor-")
        spent = {"tokens": 0, "cost_usd": 0.0}
        used: dict = {}
        status = "cancelled"
        t0 = time.monotonic()
        try:
            async with self.sem:
                self.bus.emit("supervisor.started", tid, row["project_id"], step=row.get("step"), call_id=call_id, data={
                    "agent": "supervisor", "provider": model.get("provider"), "model": model.get("model"),
                    "mode": model.get("mode"), "effort": model.get("effort"), "phase": st.get("phase")})
                req = AgentRequest(agent="supervisor", system=brief.SYSTEM, prompt=text, root=ctx.root, phase=st.get("phase") or "none",
                                   model=model, toolbox=ToolBox(ctx.root, st.get("phase") or "none", readonly=True, agent="supervisor"),
                                   key=models.key_for(model.get("provider", ""), ctx.keys), workdir=tmp, keys=dict(ctx.keys or {}),
                                   thread=tid, title=st.get("title") or "", timeout=900)
                try:
                    res = await models.runner_for(model).run(req, lambda *a, **k: None)
                except ModelError as exc:
                    status = "failed"
                    used = dict(exc.usage or {})
                    spent = {"tokens": side_usage.record(tid, SOURCE, used), "cost_usd": float(used.get("cost_usd") or 0)}
                    return None, spent, model, int((time.monotonic() - t0) * 1000), f"{exc} {exc.hint}".strip()
            used = {"tokens_in": res.tokens_in, "tokens_out": res.tokens_out, "tokens_cached": res.tokens_cached,
                    "cost_usd": res.cost_usd}
            spent = {"tokens": side_usage.record(tid, SOURCE, used), "cost_usd": float(res.cost_usd or 0)}
            status = "done"
            return verdict.parse(res.text), spent, model, int((time.monotonic() - t0) * 1000), None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            self.bus.emit("supervisor.finished", tid, row["project_id"], step=row.get("step"), call_id=call_id, data={
                "agent": "supervisor", "status": status, "tokens_in": int(used.get("tokens_in") or 0),
                "tokens_out": int(used.get("tokens_out") or 0), "tokens_cached": int(used.get("tokens_cached") or 0),
                "cost_usd": float(used.get("cost_usd") or 0.0)})

    async def _apply(self, row: dict, st: dict, q: dict, decision: str, why: str, payload: dict) -> dict:
        tid = row["thread_id"]
        try:
            await self.engine.resume(tid, decision, why, payload or None, question_id=q.get("id"), by="supervisor")
        except EngineError as exc:
            if exc.status == 409:
                return self._record(row, "stale", "stale")
            return self._escalate(row, "not-offered", summary=f"keel refused the answer: {exc}")
        out = self._record(row, "applied", None)
        self._emit(row, "supervisor.decided", {"decision": decision, "choice": payload.get("choice"), "why": why,
                                               "confidence": row.get("confidence"), "evidence": row.get("evidence")})
        return out

    # ------------------------------------------------------------ records

    def _record(self, row: dict, outcome: str, code: str | None) -> dict:
        row.setdefault("action", "escalate" if outcome == "escalated" else "answer")
        return ledger.record(**row, outcome=outcome, code=code)

    def _escalate(self, row: dict, code: str, summary: str = "", suggestion: dict | None = None) -> dict:
        row = {**row, "summary": row.get("summary") or summary or policy.CODES.get(code, code)}
        out = self._record(row, "suggested" if code == "shadow" else "escalated", code)
        self._emit(row, "supervisor.escalated", {"code": code, "reason": policy.CODES.get(code, code),
                                                 "summary": row["summary"], "suggestion": suggestion,
                                                 "confidence": row.get("confidence"), "evidence": row.get("evidence") or []})
        return out

    def _emit(self, row: dict, type_: str, data: dict) -> None:
        self.bus.emit(type_, row["thread_id"], row["project_id"], step=row.get("step"),
                      data={"question_id": row.get("question_id"), "kind": row.get("kind"), **data})
