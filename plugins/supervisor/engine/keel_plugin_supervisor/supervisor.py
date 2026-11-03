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
The flow's settings.supervisor = {on, mode, model, effort, cap_tokens, max_answers, policy, estimate} (routes.py), over
the keel-wide mode and stall time (switch.py). What each mode may do, each one with all the one before it can do:

    shadow   nothing but words: each verdict is a suggestion for the person (the default)
    retry    tries a stuck step again with a note, when the gate offers that
    act      answers gates and sends findings back when it is sure. Engine.resume(by="supervisor"), as a person would;
             a 409 there means the person answered first, and that is final
    fix      also makes one small fix when it is sure: limits in code (fix.py), then keel's own check runs again

In every mode it also writes a diagnosis of a failed flow and of review findings, and asks the person about a flow that
has been quiet for the stall time (stall_minutes). A running step is never stopped.

Events (thread_id = the flow's, step = the waiting step): supervisor.looking, supervisor.decided, supervisor.escalated,
supervisor.paused, supervisor.diagnosis, supervisor.fixed, and supervisor.started / supervisor.finished around each model
turn (call_id; the api's agent call).
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

from . import brief, fix, ledger, past, policy, switch, verdict

log = logging.getLogger("keel.supervisor")

MODES = ("shadow", "retry", "act", "fix")     # 0.18.75 retry: acts only to try a stuck step again; 0.21.0 fix: small fixes
DEFAULTS = {"on": None, "mode": "shadow", "effort": "high", "cap_tokens": 150_000, "max_answers": 20, "estimate": 30_000,
            "day_cap_tokens": 500_000, "stall_minutes": 30, "fix_max_files": 3, "fix_max_lines": 40}
MAX_SAME_QUESTION = 2       # answers to the very same question in one flow, then it asks
MAX_SAME_TITLE = 3          # answers to the same step + criterion + title (a loop whose question id changes)
MAX_PARALLEL = 2
STALL_TICK = 30.0           # how often, in seconds, it checks for a quiet flow
# looks at these kinds of gate also write a diagnosis, in every mode
DIAGNOSED = ("failure", "findings")
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
        self.last: dict[str, float] = {}       # thread -> when its last event came (monotonic): the stall time reads it
        self.stalled: dict[str, float] = {}    # thread -> the `last` it asked the person about: one look per stall
        self.staller: asyncio.Task | None = None

    # ------------------------------------------------------------ life

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.worker = asyncio.create_task(self._work())
        self.staller = asyncio.create_task(self._watch_stalls())
        if switch.get_keel():
            self._activate()                   # a restart: listen again, and look at the flows that wait now

    async def close(self) -> None:
        self.active = False
        self.bus.unlisten(self._hear)
        for t in [self.worker, self.staller, *self.tasks]:
            if t:
                t.cancel()
        await asyncio.gather(*[t for t in [self.worker, self.staller, *self.tasks] if t], return_exceptions=True)

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
        self.last.clear()
        self.stalled.clear()
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

    async def set_switch(self, keel: bool | None = None, projects: dict | None = None, defaults: dict | None = None) -> dict:
        """The api's switches changed (routes.py POST /supervisor/switch)."""
        switch.put(keel, projects)
        if defaults:
            switch.put_defaults(defaults)
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
        out: dict = {"keel": switch.get_keel(), "active": self.active, "defaults": switch.defaults(),
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
        if not self.active:
            return
        kind, tid = str(ev.get("type") or ""), ev.get("thread_id")
        if tid and not kind.startswith("supervisor."):      # its own looks are no sign of life of the flow
            self.last[tid] = time.monotonic()
        if kind in WAKE and self.loop and not self.loop.is_closed():
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
            elif ev["type"] == "thread.failed":
                self._spawn(self.diagnose(ev["thread_id"]))

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

    # ------------------------------------------------------------ the stall time

    async def _watch_stalls(self) -> None:
        while True:
            await asyncio.sleep(STALL_TICK)
            if not self.active:
                continue
            try:
                await self.check_stalls()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("could not check for quiet flows")

    async def check_stalls(self, now: float | None = None) -> list[str]:
        """Flows that run with no event for their stall time: one look each, once per stall. Returns their ids."""
        now = time.monotonic() if now is None else now
        async with self.engine.conn.execute("select thread_id from keel_threads where status = 'running'") as cur:
            running = [r[0] for r in await cur.fetchall()]
        for tid in [t for t in self.last if t not in running]:     # it ended or waits: forget it
            self.last.pop(tid, None)
            self.stalled.pop(tid, None)
        out = []
        for tid in running:
            seen_at = self.last.setdefault(tid, now)        # after a restart it counts from now
            if self.stalled.get(tid) == seen_at:
                continue
            try:
                sset = await self.settings(tid)
            except EngineError:
                continue
            minutes = max(1, int(sset.get("stall_minutes") or DEFAULTS["stall_minutes"]))
            if sset["on"] and now - seen_at >= minutes * 60:
                self.stalled[tid] = seen_at
                self._spawn(self.stall_look(tid, minutes))
                out.append(tid)
        return out

    # ------------------------------------------------------------ settings

    async def settings(self, tid: str) -> dict:
        """The flow's Supervisor settings, `on` = whether it supervises this flow now (see the switches above), and
        `switches` = each switch and, when it is off, the first one that is off (`off_by`)."""
        ctx = await self.engine._context(tid)
        # 0.21.0 keel-wide mode and stall time (Settings › General), under the flow's own
        own_settings = {k: v for k, v in ((ctx.settings or {}).get("supervisor") or {}).items() if v is not None}
        s = settings_of({**switch.defaults(), **own_settings, "on": ((ctx.settings or {}).get("supervisor") or {}).get("on")})
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
        return await self._locked(tid, lambda: self._look(tid, trigger))

    async def diagnose(self, tid: str) -> dict | None:
        return await self._locked(tid, lambda: self._diagnose(tid))

    async def stall_look(self, tid: str, minutes: int) -> dict | None:
        return await self._locked(tid, lambda: self._stall_look(tid, minutes))

    async def _locked(self, tid: str, work) -> dict | None:
        """One look per flow at a time, and an off switch cancels it."""
        if not self.active:
            return None
        me = asyncio.current_task()
        try:
            ctx = await self.engine._context(tid)
            if me:
                self.looks[me] = (tid, ctx.project_id)
            async with self.locks.setdefault(tid, asyncio.Lock()):
                return await work()
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

        money, broke, words = self._money(tid, st, sset)
        if broke:
            return self._escalate(row, broke, summary=words)

        earlier = ledger.list_rows(thread_id=tid, limit=8)
        before = await asyncio.to_thread(past.fetch, st["project_id"], kind, q.get("step"), tid)
        limits = (int(sset["fix_max_files"]), int(sset["fix_max_lines"])) if sset["mode"] == "fix" else None
        text = brief.build(st, q, kind, rule, (await self.engine._context(tid)).root, money, earlier, before, limits)
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
        wants_fix = v["action"] == "fix"
        if wants_fix:
            v["choice"] = _fix_choice(q)           # a fix is checked again by the gate's own "try again"
        row.update(action=v["action"], why=v["why"], confidence=v["confidence"], evidence=v["evidence"],
                   summary=v["summary"], choice=v["choice"], answers=v["answers"], commands=v["commands"])
        decision, why, payload = verdict.to_answer(v)
        row["decision"] = decision
        if kind in DIAGNOSED and (v["summary"] or v["why"]):      # every mode, shadow too: it only writes
            self._emit(row, "supervisor.diagnosis", {"source": kind, "summary": v["summary"] or v["why"],
                                                     "confidence": v["confidence"], "evidence": v["evidence"]})
        code = None
        if v["action"] == "escalate":
            code = "model-escalated"
        elif rule != "may":
            code = "policy-escalate"
        elif v["confidence"] != "high" and not (sset["mode"] in ("retry", "act", "fix") and v["confidence"] == "medium"
                                                and _is_retry(q, payload) and not wants_fix):
            # 0.18.75 mode retry and up takes a medium-confidence retry note: a retry is cheap and the step's checks run again
            code = "low-confidence"
        else:
            code = policy.evidence_problem(v["evidence"], (await self.engine._context(tid)).root, st.get("acs") or [])
        if wants_fix and not code:
            code = "fix-off" if sset["mode"] != "fix" else None if v["choice"] else "fix-no-check"
        if not code and policy.offered_problem(q, decision, why, payload):
            code = "not-offered"
        if not code and sset["mode"] == "retry" and not _is_retry(q, payload):
            code = "shadow"           # mode retry answers nothing but "try again with this note"
        elif not code and sset["mode"] not in ("act", "retry", "fix"):
            code = "shadow"
        if code:
            return self._escalate(row, code, suggestion={"decision": decision, "why": why, "payload": payload}
                                  if v["action"] != "escalate" and not wants_fix else None)
        if not (await self.settings(tid))["on"]:      # switched off while it thought
            return self._record({**row, "summary": "Turned off before it answered."}, "cancelled", "turned-off")
        if wants_fix:
            return await self._fix(row, st, q, sset, v, decision, why, payload)
        return await self._apply(row, st, q, decision, why, payload)

    def _money(self, tid: str, st: dict, sset: dict) -> tuple[dict, str | None, str]:
        """The numbers for the brief, and the cap one more turn would break: (money, code or None, words)."""
        own = side_usage.of_source(tid, SOURCE)["tokens"]
        usage = st.get("usage") or {}
        flow_cap = int(usage.get("cap_tokens") or 0)
        flow_used = budget_tokens(usage) + side_usage.total(tid)["tokens"]
        money = {"flow_used": flow_used, "flow_cap": flow_cap, "own_used": own, "own_cap": int(sset["cap_tokens"])}
        today = ledger.tokens_today(st["project_id"])
        if today >= int(sset["day_cap_tokens"]):
            return money, "budget-day", f"My budget for this project today is used up ({today:,} of {int(sset['day_cap_tokens']):,} tokens)."
        if own >= int(sset["cap_tokens"]):
            return money, "budget-own", f"My budget for this flow is used up ({own:,} of {int(sset['cap_tokens']):,} tokens)."
        if flow_cap and flow_used + int(sset["estimate"]) > flow_cap:
            return money, "budget-flow", f"Looking would pass the flow's cap ({flow_used:,} of {flow_cap:,} tokens)."
        return money, None, ""

    async def _fix(self, row: dict, st: dict, q: dict, sset: dict, v: dict, decision: str, why: str, payload: dict) -> dict:
        """Mode fix: one writing turn on the flow's own branch, then the limits in code (fix.py). When they hold, the gate's
        own "try again" runs keel's check, and keel's commit step commits it later; when not, only what this turn changed is
        put back and the person gets the patch. The Supervisor never commits, pushes or merges."""
        tid = row["thread_id"]
        root = (await self.engine._context(tid)).root
        where = await asyncio.to_thread(fix.where_problem, root, st)
        if where:
            return self._escalate(row, where)
        _, broke, words = self._money(tid, st, sset)
        if broke:
            return self._escalate(row, broke, summary=words)
        before = await asyncio.to_thread(fix.Before.take, root)
        limits = int(sset["fix_max_files"]), int(sset["fix_max_lines"])
        text = brief.fix_prompt(st, q, v.get("plan") or "", v["why"])
        system = brief.FIX_SYSTEM.format(max_files=limits[0], max_lines=limits[1])
        try:
            _, spent, _, ms, err = await self._turn(tid, st, sset, text, row, uuid.uuid4().hex, system=system,
                                                    agent="supervisor-fix", writes=True)
        except asyncio.CancelledError:
            await asyncio.to_thread(fix.put_back, root, before)
            self._record({**row, "summary": "Turned off while it was fixing; its changes are put back."}, "cancelled", "turned-off")
            raise
        row.update(tokens=int(row.get("tokens") or 0) + spent["tokens"], cost_usd=float(row.get("cost_usd") or 0) + spent["cost_usd"],
                   ms=int(row.get("ms") or 0) + ms)
        report = await asyncio.to_thread(fix.check, root, before, *limits)
        if err or report.code or not (await self.settings(tid))["on"]:
            if report.code != "fix-git-moved":
                await asyncio.to_thread(fix.put_back, root, before)
            row["patch"] = report.patch or None
            if err:
                return self._escalate(row, "model-error", summary=err)
            if not report.code:
                return self._record({**row, "summary": "Turned off before it answered; its changes are put back."}, "cancelled", "turned-off")
            words = f"{policy.CODES[report.code]}: {report.why}." if report.why else ""
            return self._escalate({**row, "summary": None}, report.code, summary=words or policy.CODES[report.code])
        self._emit(row, "supervisor.fixed", {"files": report.paths, "lines": report.lines, "why": v["why"]})
        row["patch"] = report.patch or None
        out = await self._apply(row, st, q, decision, f"{why} (small fix: {', '.join(report.paths)})", payload)
        if out.get("outcome") != "applied":       # the person answered first, or keel refused: nothing will check this fix
            await asyncio.to_thread(fix.put_back, root, before)
        return out

    async def _diagnose(self, tid: str) -> dict | None:
        """A failed flow: what went wrong, what it suggests, in the flow's log. Every mode; it only writes."""
        try:
            st = await self.engine.state(tid)
        except EngineError:
            return None
        if st.get("status") != "failed":
            return None
        sset = await self.settings(tid)
        key = (tid, "failed", st.get("checkpoints"))
        if not sset["on"] or key in self.seen:
            return None
        self.seen.add(key)
        return await self._note_look(tid, st, sset, "failed", "thread.failed", f"The flow failed: {st.get('error') or 'no message'}")

    async def _stall_look(self, tid: str, minutes: int) -> dict | None:
        """A flow with no event for its stall time: one read-only look, then it asks the person. It stops nothing: a long
        step may be fine, and only the person knows. Over a budget it still asks, without the look."""
        try:
            st = await self.engine.state(tid)
        except EngineError:
            return None
        sset = await self.settings(tid)
        if st.get("status") != "running" or not sset["on"]:
            return None
        said = f"No event from this flow for {minutes} minutes. The step {st.get('current') or 'now'} may still be working; it was not stopped."
        got = await self._note_look(tid, st, sset, "stall", "stall", said, emit=False)
        summary = (got or {}).get("summary") if (got or {}).get("outcome") == "diagnosed" else None
        summary = summary or said
        row = {"project_id": st["project_id"], "thread_id": tid, "step": st.get("current"), "kind": "stall", "trigger": "stall"}
        return self._escalate(row, "stalled", summary=summary)

    async def _note_look(self, tid: str, st: dict, sset: dict, what: str, trigger: str, detail: str, emit: bool = True) -> dict | None:
        """A look that answers nothing: budget checks, one read-only turn, a row, and (emit) a supervisor.diagnosis event."""
        row = {"project_id": st["project_id"], "thread_id": tid, "step": st.get("current"), "kind": what, "trigger": trigger,
               "action": "diagnose"}
        money, broke, words = self._money(tid, st, sset)
        if broke:
            return self._record({**row, "summary": words}, "skipped", broke)
        call_id = uuid.uuid4().hex
        text = brief.diagnosis(st, what, detail, money, ledger.list_rows(thread_id=tid, limit=8))
        try:
            v, spent, model, ms, err = await self._turn(tid, st, sset, text, row, call_id)
        except asyncio.CancelledError:
            self._record({**row, "summary": "Turned off while it was looking."}, "cancelled", "turned-off")
            raise
        row.update(tokens=spent["tokens"], cost_usd=spent["cost_usd"], ms=ms, model=model.get("model"), effort=model.get("effort"))
        if err or v is None:
            return self._record({**row, "summary": err or policy.CODES["no-verdict"]}, "skipped", "model-error" if err else "no-verdict")
        row.update(why=v["why"], confidence=v["confidence"], evidence=v["evidence"], summary=v["summary"] or v["why"])
        out = self._record(row, "diagnosed", None)
        if emit:
            self._emit(row, "supervisor.diagnosis", {"source": what, "summary": row["summary"], "confidence": v["confidence"],
                                                     "evidence": v["evidence"]})
        return out

    async def status(self, tid: str) -> dict:
        """The flow's chip and its Inbox note (GET /threads/{id}/supervisor/status): where the Supervisor stands now, and
        what it suggests for the question that waits now. A suggestion only fills in the person's form."""
        st = await self.engine.state(tid)
        s = await self.settings(tid)
        rows = ledger.list_rows(thread_id=tid, limit=10)
        waiting = (st.get("waiting") or {}).get("id") if st.get("status") == "waiting" else None
        looking = tid in {t for t, _ in self.looks.values()}
        return {"state": chip_state(s["on"], looking, rows, waiting, self.last.get(tid), self.stalled.get(tid)),
                "on": s["on"], "off_by": s["switches"]["off_by"], "mode": s["mode"], "note": note_for(rows, waiting)}

    async def _kind(self, tid: str, st: dict, q: dict) -> str:
        ctx = await self.engine._context(tid)
        wf = ctx.workflow
        sid = str(q.get("step") or "")
        step = wf.step(sid[: -len("__fix")] if sid.endswith("__fix") else sid)
        if sid.endswith("__fix") or step is None:
            step_for = None
        else:
            step_for = step
        prev = nxt = None
        if step_for is not None:
            i = next((n for n, s in enumerate(wf.steps) if s.id == step_for.id), 0)
            prev = wf.steps[i - 1] if i else None
            nxt = wf.steps[i + 1] if i + 1 < len(wf.steps) else None
        per_ac = bool(step_for is not None and step_for.per_ac and st.get("ac"))
        return policy.kind_of(q, step_for, wf.flow, prev.actions() if prev else (), per_ac, nxt)

    async def _turn(self, tid: str, st: dict, sset: dict, text: str, row: dict, call_id: str, system: str = brief.SYSTEM,
                    agent: str = "supervisor", writes: bool = False):
        """The model's look: read-only tools, the thread's keys. Returns (verdict, spent, model, ms, error). The api
        stores it as an agent call (supervisor.started / supervisor.finished), so the Budget page counts it. `writes`: the
        small fix's turn: it may write what the flow's phase allows, and its shell cannot change files or git."""
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
                box = ToolBox(ctx.root, st.get("phase") or "none", readonly=not writes, agent=agent, confine=writes)
                if writes:
                    box.shell_readonly = True
                    box.readonly_label = "The Supervisor's small fix"
                    box.readonly_note = "keel commits after its own checks pass. Change files with your edit tools only."
                req = AgentRequest(agent=agent, system=system, prompt=text, root=ctx.root, phase=st.get("phase") or "none",
                                   model=model, toolbox=box,
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


def _fix_choice(q: dict) -> str | None:
    """The answer that makes keel run its own check again after a small fix: recheck (a failing check), or retry (a stuck
    step, or a suite: it runs the step again). A gate with neither cannot check a fix, so there is none."""
    choices = q.get("choices") or []
    return next((c for c in ("recheck", "retry") if c in choices), None)


def chip_state(on: bool, looking: bool, rows: list[dict], waiting: str | None, last: float | None, stalled: float | None) -> str:
    """off | looking | watching | acted | escalated | paused-budget | paused-repeats. rows: its newest first. What it
    said about a gate that is past no longer counts, nor a stall that ended."""
    if not on:
        return "off"
    if looking:
        return "looking"
    for r in rows:
        if r.get("outcome") in ("diagnosed", "skipped", "cancelled"):
            continue
        if r.get("outcome") == "applied":
            return "acted"
        if r.get("outcome") not in ("escalated", "suggested"):
            return "watching"
        code = str(r.get("code") or "")
        if code == "stalled":
            if last is not None and last == stalled:
                return "escalated"
            continue
        if r.get("question_id") != waiting:
            return "watching"
        if code.startswith("budget-"):
            return "paused-budget"
        return "paused-repeats" if code in ("repeat", "too-many") else "escalated"
    return "watching"


# what it said cannot be used as the person's answer after these
_NO_ANSWER = ("not-offered", "no-verdict", "model-error", "model-escalated", "turned-off")


def note_for(rows: list[dict], waiting: str | None) -> dict | None:
    """What the Inbox card shows for the question that waits now: its words, why it did not answer, and (when it has one) an
    answer the person may use. The answer has no "supervisor:" in front: when the person sends it, it is theirs."""
    r = next((r for r in rows if waiting and r.get("question_id") == waiting and r.get("outcome") in ("escalated", "suggested")), None)
    if not r:
        return None
    usable = r.get("action") in ("answer", "send_back") and r.get("decision") and r.get("code") not in _NO_ANSWER
    answer = {"decision": r["decision"], "choice": r.get("choice"), "answers": r.get("answers") or {}, "why": r.get("why") or ""} if usable else None
    return {"summary": r.get("summary") or "", "code": r.get("code"), "reason": policy.CODES.get(r.get("code") or "", r.get("code") or ""),
            "confidence": r.get("confidence"), "suggestion": answer, "patch": r.get("patch")}


def _is_retry(q: dict, payload: dict) -> bool:
    """A stuck step's "try again" with a note (choice retry offered by the question): what mode retry may answer. A field
    report: in shadow the Supervisor proposed a near-right retry note three times and could not apply it."""
    return payload.get("choice") == "retry" and "retry" in (q.get("choices") or q.get("options") or [])
