"""Compile a Workflow into a LangGraph StateGraph.

    START ─▶ step ─▶ step ─▶ __ac_begin ─▶ [per_ac steps ...] ─▶ __ac_end ─┐ ─▶ step ... ─▶ __finish ─▶ END
                                 ▲                                         │
                                 └──────────── next AC ────────────────────┘

Every node returns Command(goto=...): the default is the next step, a gate's reject follows `back`,
a branch's "no" follows `no`, a failed check goes back to the agent before it. Gates, budget pauses
and "keeps failing" pauses are interrupt()s; only POST /threads/{id}/resume continues them.

interrupt() re-runs its node from the top on resume, so nothing with a side effect may sit before
an interrupt() unless re-running it is the intended retry.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import tempfile
import uuid
from pathlib import Path

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .. import models, rules
from ..models import catalog
from ..models.base import AgentRequest, AgentResult
from .findings import REVIEWERS, blocking, unique
from ..tools import git, guard
from ..tools.agent_tools import ToolBox
from ..workflows.model import Step, Workflow
from . import init_gates, prompts
from .actions import ActionInput, ActionResult, revert_manifests, run_action
from .state import FlowState, ThreadContext, merge_unlocks, normalize_unlocks

log = logging.getLogger(__name__)

AC_BEGIN, AC_END, FINISH = "__ac_begin", "__ac_end", "__finish"
KNOWN_SECTIONS = {"architecture", "domain", "conventions", "data", "integrations"}
OPTIONS = ["approve", "reject"]
ALREADY_MET = "already-met"
DONE = ("done", ALREADY_MET)        # AC statuses the per-AC loop is finished with
STEP_FIELD_MAX = 24_000          # per string field of an agent.step (runners already cap at 20 KB plus a note)



def budget_tokens(usage: dict) -> int:
    """Tokens that count toward caps: new input + output + a tenth of cache reads (they cost about a tenth)."""
    return int(usage.get("tokens_in", 0)) + int(usage.get("tokens_out", 0)) + int(usage.get("tokens_cached", 0)) // 10


def question_id(question: dict) -> str:
    """Names a question by what it asks (not its detail, which changes between runs)."""
    raw = "|".join(str(question.get(k) or "") for k in ("step", "kind", "title"))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def ask_once(question: dict) -> dict:
    """interrupt(), but an answer only counts for the question it was given to.

    A resumed node runs again from the top, so it can reach a different question first (verify_red: "keeps
    failing" before, "already passes" now). The resume carries `asked` = the id the user saw; when it does not
    match, the node pauses again on the new question. LangGraph keeps one resume value per interrupt() in a node.
    """
    qid = question_id(question)
    question = {**question, "id": qid}
    answer = interrupt(question) or {}
    while answer.get("asked") and answer["asked"] != qid:
        answer = interrupt(question) or {}
    return answer

class LaneFailed(Exception):
    pass


class Nav:
    """Where to go next, given the step list and the per-AC loop."""

    def __init__(self, wf: Workflow):
        self.steps = wf.steps
        self.index = {s.id: i for i, s in enumerate(wf.steps)}
        loop = [i for i, s in enumerate(wf.steps) if s.per_ac]
        self.first, self.last = (loop[0], loop[-1]) if loop else (None, None)

    def enter(self, j: int) -> str:
        if j >= len(self.steps):
            return FINISH
        if self.first is not None and j == self.first:
            return AC_BEGIN
        return self.steps[j].id

    def after(self, i: int) -> str:
        if self.last is not None and i == self.last:
            return AC_END
        return self.enter(i + 1)

    def after_loop(self) -> str:
        return self.enter(self.last + 1) if self.last is not None else FINISH

    def jump(self, sid: str, from_i: int) -> str:
        j = self.index[sid]
        inside_now = self.steps[from_i].per_ac
        if self.steps[j].per_ac and not inside_now:
            return AC_BEGIN
        return self.enter(j) if not self.steps[j].per_ac else self.steps[j].id

    def retry_target(self, i: int) -> str | None:
        """The agent step a failed check sends work back to."""
        here = self.steps[i]
        for k in range(i - 1, -1, -1):
            s = self.steps[k]
            if bool(s.per_ac) != bool(here.per_ac):
                break
            if s.kind in ("agent", "parallel"):
                return s.id
        return None


def fix_id(review_id: str) -> str:
    return f"{review_id}__fix"


def _ac(state: FlowState) -> dict | None:
    return next((a for a in state.get("acs") or [] if a["id"] == state.get("ac")), None)


def _set_ac(acs: list[dict], ac_id: str | None, status: str) -> list[dict]:
    return [dict(a, status=status) if a["id"] == ac_id else dict(a) for a in acs]


class Compiler:
    def __init__(self, ctx: ThreadContext):
        self.ctx = ctx
        self.wf = ctx.workflow
        self.nav = Nav(self.wf)

    # ------------------------------------------------------------ build

    def build(self, checkpointer):
        g = StateGraph(FlowState)
        for i, step in enumerate(self.wf.steps):
            fn = {"agent": self.agent_step, "parallel": self.agent_step, "code": self.code_step,
                  "gate": self.gate_step, "branch": self.branch_step}[step.kind]
            g.add_node(step.id, self._wrap(i, step, fn))
            if self._reviews(step):
                fix = Step(id=fix_id(step.id), kind="agent", name=f"{step.name}: fix findings", agent="implementer",
                           model=step.model, phase="review-fix")
                g.add_node(fix.id, self._wrap(i, fix, self.findings_step))
        g.add_node(AC_BEGIN, self.ac_begin)
        g.add_node(AC_END, self.ac_end)
        g.add_node(FINISH, self.finish)
        g.add_edge(START, self.nav.enter(0))
        return g.compile(checkpointer=checkpointer)

    def _wrap(self, i: int, step: Step, fn):
        ctx = self.ctx

        async def node(state: FlowState):
            if state.get("status") in ("stopped", "failed"):
                return Command(goto=END)
            # Unlocks the api appended to .keel/state.json since the last step join the thread's state.
            have = list(state.get("unlocks") or [])
            merged = merge_unlocks(have, await asyncio.to_thread(ctx.disk_unlocks))
            if len(merged) != len(have):
                state = {**state, "unlocks": merged}
            prev = state.get("phase") or "none"
            phase = step.phase or prev
            ac = state.get("ac") if step.per_ac else None
            ctx.emit("step.started", step=step.id, data={
                "name": step.name, "kind": step.kind, "phase": phase, "ac": ac, "from": prev, "flow": self.wf.flow,
                "phase_changed": prev != phase, "transition_ok": rules.can_transition(prev, phase)})
            # keel v1 reads .keel/state.json: its hooks on every tool call (this step's phase, AC and unlocks must be
            # there before an agent starts) and `keel status` (a flow whose phase is still "none" looks finished).
            await asyncio.to_thread(ctx.write_mirror, {**state, "phase": phase, "current": step.id})
            update, goto = await fn(i, step, {**state, "phase": phase})
            if len(merged) != len(have):
                update.setdefault("unlocks", merged)
            update.setdefault("phase", phase)
            update.setdefault("current", step.id)
            failed = update.get("status") in ("stopped", "failed")
            ctx.emit("step.finished", step=step.id, data={"name": step.name, "kind": step.kind, "phase": phase, "ac": ac,
                                                         "ok": not failed, "note": update.get("note")})
            return Command(update=update, goto=END if failed else goto)

        node.__name__ = f"step_{step.id}"
        return node

    # ------------------------------------------------------------ loop + end

    async def ac_begin(self, state: FlowState):
        if state.get("status") in ("stopped", "failed"):
            return Command(goto=END)
        nxt = next((a for a in state.get("acs") or [] if a.get("status") not in DONE), None)
        if not nxt:
            return Command(update={"ac": None}, goto=self.nav.after_loop())
        return Command(update={"ac": nxt["id"], "retries": {}}, goto=self.wf.steps[self.nav.first].id)

    async def ac_end(self, state: FlowState):
        acs = state.get("acs") or []
        cur = next((a for a in acs if a["id"] == state.get("ac")), None)
        if cur and cur.get("status") != ALREADY_MET:
            acs = _set_ac(acs, state["ac"], "done")
        return Command(update={"acs": acs}, goto=AC_BEGIN)

    async def finish(self, state: FlowState):
        return Command(update={"status": "done", "ac": None, "note": "done"}, goto=END)

    # ------------------------------------------------------------ models

    def _model(self, state: FlowState, step: Step, agent: str) -> dict:
        m = None
        if state.get("model_override"):
            m = state["model_override"]
        elif step.model and step.model != "default" and step.model in self.ctx.models:
            m = self.ctx.models[step.model]
        elif agent in self.ctx.models:
            m = self.ctx.models[agent]
        elif "default" in self.ctx.models:
            m = self.ctx.models["default"]
        return models.effective(m)

    def _ask(self, state: FlowState, question: dict) -> tuple[dict, dict]:
        """interrupt() for any pause, plus what every answer may carry: payload.unlock {path, phase}.

        Returns (answer, state update). An unlock is added to state.unlocks and logged as a gate event.
        """
        answer = ask_once(question)
        payload = answer.get("payload") or {}
        new = normalize_unlocks(payload.get("unlock"), state.get("phase") or "none", "user")
        if not new:
            return answer, {}
        have = list(state.get("unlocks") or [])
        merged = merge_unlocks(have, new)
        for u in merged[len(have):]:
            # gate.decided becomes a `gate` line in .keel/logs/events.jsonl through the bus.
            self.ctx.emit("gate.decided", step=question.get("step"), data={
                "gate": "unlock", "decision": "approve", "unlock": {"path": u["path"], "phase": u["phase"]},
                "why": f"{u['path']} in {u['phase']}" + (f": {answer.get('why')}" if answer.get("why") else "")})
        return answer, {"unlocks": merged}

    async def _run_agent(self, state: FlowState, step: Step, agent: str, index: int, section: str | None = None) -> tuple[AgentResult, dict, ToolBox]:
        ctx = self.ctx
        phase = state["phase"]
        ac = _ac(state) if step.per_ac else None
        model = self._model(state, step, agent)
        call_id = uuid.uuid4().hex
        cfg = rules.load_config(ctx.root)

        def on_refuse(tool: str, path: str, reason: str, command: str | None = None):
            data = {"tool": tool, "path": path or "", "reason": reason, "agent": agent, "phase": phase}
            if command:
                data["command"] = command
            ctx.emit("guard.refused", step=step.id, call_id=call_id, data=data)

        toolbox = ToolBox(ctx.root, phase, cfg=cfg, lane=rules.ac_lane(ac) if ac else None,
                          ac=(ac or {}).get("id"), ac_layer=(ac or {}).get("layer", "API"), on_refuse=on_refuse,
                          unlocks=state.get("unlocks") or [])
        ctx.emit("agent.started", step=step.id, call_id=call_id, data={
            "agent": agent, "provider": model["provider"], "model": model.get("model"), "mode": model.get("mode"),
            "phase": phase, "ac": (ac or {}).get("id"), "index": index})
        counter = {"n": 0}

        def emit(kind: str, text: str = "", **extra):
            counter["n"] += 1
            # Every string field (text, diff, output, ...) is bounded; newlines are kept for the web's Markdown and diff views.
            data = {"n": counter["n"], "kind": kind, "text": str(text)[:STEP_FIELD_MAX]}
            data.update({k: (v[:STEP_FIELD_MAX] if isinstance(v, str) else v) for k, v in extra.items() if v is not None})
            ctx.emit("agent.step", step=step.id, call_id=call_id, data=data)

        prompt = prompts.task_prompt(agent=agent, phase=phase, step_name=step.name, title=ctx.title, root=ctx.root, ac=ac,
                                     acs=state.get("acs") or [], feedback=state.get("feedback"), index=index, spec=state.get("spec"),
                                     section=section, unlocks=state.get("unlocks") or [], request=ctx.request)
        with tempfile.TemporaryDirectory(prefix="keel-agent-") as tmp:
            req = AgentRequest(agent=agent, system=prompts.system_prompt(agent, ctx.skills), prompt=prompt, root=ctx.root,
                               phase=phase, model=model, toolbox=toolbox, ac=ac, acs=state.get("acs") or [], title=ctx.title,
                               step_name=step.name, index=index, feedback=state.get("feedback"), mcp_specs=ctx.mcp,
                               tools_allow=step.tools or [], key=models.key_for(model["provider"], ctx.keys), workdir=tmp,
                               keys=dict(ctx.keys), section=section)
            try:
                res = await models.runner_for(model).run(req, emit)
            except asyncio.CancelledError:
                ctx.emit("agent.finished", step=step.id, call_id=call_id, data={"agent": agent, "status": "stopped", "tokens_in": 0,
                                                                                "tokens_out": 0, "cost_usd": 0, "premium_requests": 0})
                raise
            except Exception as exc:
                emit("error", f"{exc}{(' ' + exc.hint) if getattr(exc, 'hint', '') else ''}", ok=False)
                used = getattr(exc, "usage", None) or {}
                # A failed run still used tokens (a turn limit after 30 turns is not free): the job and the budget count them.
                ctx.emit("agent.finished", step=step.id, call_id=call_id, data={
                    "agent": agent, "status": "failed", "tokens_in": used.get("tokens_in", 0), "tokens_out": used.get("tokens_out", 0),
                    "tokens_cached": used.get("tokens_cached", 0), "cost_usd": round(used.get("cost_usd", 0.0), 6),
                    "premium_requests": 0, "result": str(exc)[:500]})
                raise
        if not res.cost_usd and model.get("mode") == "api" and model["provider"] != "fake":
            res.cost_usd = catalog.cost_usd(model["provider"], model.get("model", ""), res.tokens_in, res.tokens_out)
        ctx.emit("agent.finished", step=step.id, call_id=call_id, data={
            "agent": agent, "status": "done", "tokens_in": res.tokens_in, "tokens_out": res.tokens_out, "tokens_cached": res.tokens_cached,
            "cost_usd": round(res.cost_usd, 6), "premium_requests": res.premium_requests, "result": res.text[:2000]})
        return res, model, toolbox

    # ------------------------------------------------------------ budget

    def _budget(self, state: FlowState, step: Step) -> dict:
        ctx = self.ctx
        usage = dict(state.get("usage") or {})
        cap = int(usage.get("cap_tokens") or 0)
        used = budget_tokens(usage)
        step_used = int((state.get("step_tokens") or {}).get(step.id, 0))
        over_step = bool(step.max_tokens) and step_used >= step.max_tokens
        over_cap = bool(cap) and used >= cap
        if not (over_step or over_cap):
            if cap and used >= 0.8 * cap and not state.get("warned"):
                ctx.emit("budget.warn", step=step.id, data={"used": used, "cap": cap, "pct": round(used * 100 / cap)})
                return {"warned": True}
            return {}
        wf_on = self.wf.budget.on_limit if self.wf.budget else None
        on = (step.on_limit if over_step else None) or ctx.settings.get("on_cap") or wf_on or "pause"
        cheaper = ctx.settings.get("cheaper_model")
        info = {"used": used, "cap": cap, "step_used": step_used, "step_cap": step.max_tokens}
        if on == "cheaper" and cheaper and state.get("model_override") != cheaper:
            ctx.emit("budget.warn", step=step.id, data={**info, "action": "cheaper", "model": cheaper})
            return {"model_override": cheaper, "warned": True, "note": "token cap reached; switched to the cheaper model"}
        if on == "stop":
            ctx.emit("budget.stop", step=step.id, data=info)
            return {"status": "stopped", "note": "token cap reached; flow stopped"}
        limit_txt = f"{step_used:,} of {step.max_tokens:,} tokens for this step" if over_step else f"{used:,} of {cap:,} tokens"
        answer, extra = self._ask(state, {"step": step.id, "kind": "budget", "title": "Token cap reached",
                                          "detail": f"This flow has used {limit_txt}. Approve to continue past the cap, reject to stop.",
                                          "options": OPTIONS})
        if (answer or {}).get("decision") != "approve":
            ctx.emit("budget.stop", step=step.id, data={**info, "why": (answer or {}).get("why")})
            return {**extra, "status": "stopped", "note": "stopped at the token cap"}
        payload = (answer or {}).get("payload") or {}
        upd: dict = {"warned": False, **extra}
        if over_cap:
            usage["cap_tokens"] = int(payload.get("cap_tokens") or (used + max(cap, 1)))
            upd["usage"] = usage
        if over_step:
            st = dict(state.get("step_tokens") or {})
            st[step.id] = 0
            upd["step_tokens"] = st
        return upd

    # ------------------------------------------------------------ step kinds

    async def agent_step(self, i: int, step: Step, state: FlowState):
        ctx = self.ctx
        upd = self._budget(state, step)
        if upd.get("status") == "stopped":
            return upd, END
        state = {**state, **upd}

        calls: list[tuple[str, int]] = []
        code_lanes: list[str] = []
        if step.kind == "agent":
            calls = [(step.agent or "", 0)]
        elif step.lanes:
            lane_sections: dict[int, str] = {}
            for lane in step.lanes:
                if lane.kind == "agent":
                    who = lane.sub or step.agent or ""
                    chosen = (state.get("init") or {}).get("knowledge_sections")
                    if who == "librarian" and isinstance(chosen, list):
                        # init: one librarian per section the user chose, each told its section (none: no librarian).
                        for sec in chosen:
                            lane_sections[len(calls)] = sec
                            calls.append((who, len(lane_sections) - 1))
                    else:
                        calls += [(who, k) for k in range(max(1, step.parallel or 1))]
                elif lane.sub:
                    code_lanes.append(lane.sub)
        else:
            calls = [(step.agent or "", k) for k in range(max(1, step.parallel or 1))]
        sections = self._sections(step, state)
        if sections:
            calls = [(step.agent or "librarian", k) for k in range(len(sections))]
        section_of = {n: sections[k] for n, (_a, k) in enumerate(calls)} if sections else \
            (dict(lane_sections) if step.lanes else {})

        before = await asyncio.to_thread(guard.snapshot, ctx.root)
        ac = _ac(state) if step.per_ac else None

        async def lane(action: str) -> ActionResult:
            r = await run_action(action, self._action_input(state, ac))
            if not r.ok:
                raise LaneFailed(f"{action}: {r.note}\n{r.detail}")
            return r

        done = ctx.done_calls
        attempt = f"{step.id}|{(ac or {}).get('id', '')}"

        async def one(n: int, a: str, k: int):
            key = f"{attempt}|{a}|{k}"
            if key in done:
                return done[key]
            r = await self._run_agent(state, step, a, k, section_of.get(n))
            if len(calls) > 1:
                done[key] = r
            return r

        while True:
            try:
                outs = await asyncio.gather(*[one(n, a, k) for n, (a, k) in enumerate(calls)], *[lane(c) for c in code_lanes], return_exceptions=True)
                for o in outs:
                    if isinstance(o, (asyncio.CancelledError, GraphBubbleUp)):
                        raise o
                failed = [o for o in outs if isinstance(o, BaseException)]
                if failed:
                    raise failed[0]
                out = list(outs)
                break
            except (asyncio.CancelledError, GraphBubbleUp):
                raise
            except Exception as exc:
                log.warning("step %s failed: %s", step.id, exc)
                if len(calls) > 1:
                    kept = sum(1 for key in done if key.startswith(attempt + "|"))
                    if kept:
                        exc.args = (f"{exc} ({kept} of {len(calls)} agents finished; try again runs only the others)",)
                answer, extra = self._ask(state, {"step": step.id, "kind": "fix", "title": f"{step.name} failed",
                                                  "detail": f"{exc}{(' ' + exc.hint) if getattr(exc, 'hint', '') else ''}\n\nApprove to try again, reject to stop the flow.",
                                                  "options": OPTIONS})
                upd.update(extra)
                state = {**state, **extra}
                if (answer or {}).get("decision") != "approve":
                    return {**upd, "status": "failed", "error": str(exc)[:500], "note": f"{step.name} failed"}, END

        for key in [key for key in done if key.startswith(attempt + "|")]:
            del done[key]
        agent_results = [o for o in out if isinstance(o, tuple)]
        lane_notes = [o.note for o in out if isinstance(o, ActionResult)]
        for o in out:
            if isinstance(o, ActionResult):
                upd.update(o.update)

        refused = await asyncio.to_thread(guard.guard_diff, ctx.root, state["phase"], before, None,
                                          rules.ac_lane(ac) if ac else None, state.get("unlocks") or [])
        for r in refused:
            ctx.emit("guard.refused", step=step.id, data={"tool": "diff-guard", "phase": state["phase"], **r})

        usage = dict(state.get("usage") or {})
        step_tokens = dict(state.get("step_tokens") or {})
        notes = []
        for res, model, _tb in agent_results:
            usage["tokens_in"] = usage.get("tokens_in", 0) + res.tokens_in
            usage["tokens_out"] = usage.get("tokens_out", 0) + res.tokens_out
            usage["tokens_cached"] = usage.get("tokens_cached", 0) + res.tokens_cached
            usage["cost_usd"] = round(usage.get("cost_usd", 0.0) + res.cost_usd, 6)
            usage["premium_requests"] = usage.get("premium_requests", 0) + res.premium_requests
            step_tokens[step.id] = step_tokens.get(step.id, 0) + res.tokens_in + res.tokens_out + res.tokens_cached // 10
            notes.append(res.text.strip().splitlines()[0][:160] if res.text.strip() else "")
        upd.update(usage=usage, step_tokens=step_tokens, feedback=None)
        label = step.agent or "lanes"
        if agent_results:
            m = agent_results[0][1]
            label = f"{step.agent or calls[0][0]} · {m['provider']} {m.get('model', '')}"
        upd["note"] = " · ".join(x for x in [label, *notes[:2], *lane_notes] if x)
        if refused:
            upd["note"] += f" · guard put back {len(refused)} file(s)"
        if sections and state.get("acs"):
            upd["acs"] = [dict(a, status="done") if a["id"] in sections else dict(a) for a in state["acs"]]

        for res, _m, tb in agent_results:
            # A CLI agent stopped by keel v1's hooks: the runner reported it as a guard step.
            for r in res.data.get("refusals") or []:
                data = {"tool": r.get("tool") or "hook", "phase": state["phase"], "path": r.get("path") or "",
                        "reason": r.get("reason", ""), "source": "keel-hook"}
                if r.get("command"):
                    data["command"] = r["command"]
                ctx.emit("guard.refused", step=step.id, data=data)
        if agent_results:
            upd["last_answer"] = (agent_results[-1][0].text or "")[:2000]
        if state["phase"] in ("spec", "triage") and not state.get("acs"):
            acs, spec = self._acs_from(agent_results)
            if acs:
                upd["acs"] = acs
            if spec:
                upd["spec"] = spec
        if self._reviews(step):
            found = unique([{"lens": f"{step.name} #{n + 1}" if len(agent_results) > 1 else step.name, "text": t}
                            for n, (res, _m, _tb) in enumerate(agent_results) for t in blocking(res.text)])
            upd["findings"] = found
            if found:
                upd["note"] += f" · {len(found)} blocking finding(s)"
                return upd, fix_id(step.id)
        return upd, self.nav.after(i)

    def _reviews(self, step: Step) -> bool:
        """A review step whose blocking findings must be fixed or accepted before the flow goes on."""
        return step.kind in ("agent", "parallel") and not step.per_ac and not step.lanes and (step.agent or "") in REVIEWERS

    async def findings_step(self, i: int, fix: Step, state: FlowState):
        """After a review with blocking findings: ask; fix (implementer in review-fix, tests, commit) and review again,
        or go on with the user's reason. Nothing runs before the question, so answering it never re-runs anything."""
        ctx = self.ctx
        review = self.wf.steps[i]
        found = list(state.get("findings") or [])
        rounds = dict(state.get("review_rounds") or {})
        listed = "\n".join(f"- [{f['lens']}] {f['text']}" for f in found)
        again = f" (fix round {rounds[review.id] + 1})" if rounds.get(review.id) else ""
        answer, extra = self._ask(state, {
            "step": fix.id, "kind": "gate", "title": f"{review.name}: {len(found)} blocking finding(s){again}",
            "detail": f"{listed}\n\nFix them: the implementer fixes these findings, the tests run, the fix is committed, "
                      f"and {review.name} runs again.\nGo on anyway: say why; the findings are kept in the gate log.",
            "options": OPTIONS, "labels": {"approve": "Fix them", "reject": "Go on anyway"}})
        gates = copy.deepcopy(state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
        why = (answer.get("why") or "").strip()
        if answer.get("decision") != "approve":
            gates["log"].append(f"{review.name} findings accepted: {why}")
            ctx.emit("gate.decided", step=fix.id, data={"gate": f"{review.name} findings", "decision": "reject", "why": why,
                                                        "findings": [f["text"] for f in found]})
            return {**extra, "gates": gates, "findings": [], "note": f"{len(found)} finding(s) accepted: {why}"[:300]}, self.nav.after(i)
        ctx.emit("gate.decided", step=fix.id, data={"gate": f"{review.name} findings", "decision": "approve", "why": why})
        state = {**state, **extra, "feedback": f"Fix these blocking findings from {review.name}:\n{listed}"
                                               + (f"\n\nFrom the user: {why}" if why else "")}
        before = await asyncio.to_thread(guard.snapshot, ctx.root)
        res, model, _tb = await self._run_agent(state, fix, "implementer", 0)
        refused = await asyncio.to_thread(guard.guard_diff, ctx.root, "review-fix", before, None, None, state.get("unlocks") or [])
        for r in refused:
            ctx.emit("guard.refused", step=fix.id, data={"tool": "diff-guard", "phase": "review-fix", **r})
        usage = dict(state.get("usage") or {})
        for k in ("tokens_in", "tokens_out", "tokens_cached", "premium_requests"):
            usage[k] = usage.get(k, 0) + getattr(res, k)
        usage["cost_usd"] = round(usage.get("cost_usd", 0.0) + res.cost_usd, 6)
        step_tokens = dict(state.get("step_tokens") or {})
        step_tokens[review.id] = step_tokens.get(review.id, 0) + res.tokens_in + res.tokens_out + res.tokens_cached // 10
        rounds[review.id] = rounds.get(review.id, 0) + 1
        upd = {**extra, "gates": gates, "usage": usage, "step_tokens": step_tokens, "review_rounds": rounds, "findings": [],
               "feedback": None, "last_answer": (res.text or "")[:2000]}
        a = self._action_input({**state, "phase": "review-fix"}, None)
        a.title = f"address {review.name} findings"
        notes = []
        for action in ("verify_green", "commit"):
            r = await run_action(action, a)
            notes.append(r.note)
            upd.update(r.update)
            if not r.ok:
                # Tests broke or the commit was refused: back to the question with what went wrong.
                found = found + [{"lens": "keel", "text": f"after the fix: {r.note}"}]
                return {**upd, "findings": found, "note": f"fix round {rounds[review.id]}: {r.note}"[:300]}, fix.id
        upd["note"] = " · ".join([f"implementer · {model['provider']} {model.get('model', '')}", *notes])[:300]
        return upd, self.nav.jump(review.id, i)

    def _sections(self, step: Step, state: FlowState) -> list[str]:
        """knowledge-refresh: one librarian per section, for a parallel librarian step.

        Sections come from settings.sections, else from the thread's acs (the api starts the flow with
        one AC per stale section: {id: <section>, layer: "API", title: <section>}).
        """
        if step.kind != "parallel" or step.lanes or (step.agent or "") != "librarian":
            return []
        secs = [str(x) for x in self.ctx.settings.get("sections") or [] if str(x).strip()]
        if not secs and (self.wf.flow == "knowledge-refresh" or all(a["id"] in KNOWN_SECTIONS for a in state.get("acs") or [{"id": "-"}])):
            secs = [a["id"] for a in state.get("acs") or []]
        if not secs and self.wf.flow == "knowledge-refresh":
            # Started without a list (the plain "start a flow" form): the sections init chose, else the ones that
            # exist, else all five. A librarian always gets its own section; without one it wanders.
            chosen = (rules.load_config(self.ctx.root).get("init") or {}).get("knowledge_sections")
            have = [s for s in init_gates.SECTIONS if (Path(self.ctx.root) / "docs" / "knowledge" / f"{s}.md").is_file()]
            secs = list(chosen) if isinstance(chosen, list) and chosen else (have or list(init_gates.SECTIONS))
        return secs

    def _acs_from(self, results) -> tuple[list[dict], str | None]:
        from pathlib import Path

        for res, _m, tb in results:
            if res.data.get("acs"):
                return [dict(a, status="todo") for a in res.data["acs"]], res.data.get("spec")
        seen = None
        for res, _m, tb in results:
            spec = next((w["path"] for w in tb.writes if w["path"].endswith(".md")), None)
            # The spec file the agent wrote comes first; its chat answer only when no file has criteria.
            texts = [(Path(self.ctx.root) / w["path"]).read_text(errors="replace") for w in tb.writes
                     if (Path(self.ctx.root) / w["path"]).is_file()]
            if git.is_repo(self.ctx.root):
                for rel in git.dirty(self.ctx.root):
                    if "spec" in rel and rel.endswith(".md") and (Path(self.ctx.root) / rel).is_file():
                        texts.append((Path(self.ctx.root) / rel).read_text(errors="replace"))
                        spec = spec or rel
            seen = seen or spec
            for t in texts + [res.text]:
                acs = prompts.parse_acs(t)
                if acs:
                    return acs, spec
        # No criteria yet, but the spec file is known: a send-back changes that file instead of starting over.
        return [], seen

    def _action_input(self, state: FlowState, ac: dict | None) -> ActionInput:
        return ActionInput(root=self.ctx.root, phase=state["phase"], title=self.ctx.title, ac=ac, init=dict(state.get("init") or {}),
                           acs=copy.deepcopy(state.get("acs") or []), fake=self.ctx.simulate_checks, flow=self.wf.flow,
                           deps=list(state.get("deps") or []), gates_log=list((state.get("gates") or {}).get("log") or []),
                           base=state.get("base_head"), unlocks=list(state.get("unlocks") or []),
                           preexisting=dict(state.get("preexisting") or {}))

    async def code_step(self, i: int, step: Step, state: FlowState):
        ac = _ac(state) if step.per_ac else None
        st = dict(state)
        upd: dict = {}
        notes = []
        actions = step.actions()
        for n, action in enumerate(actions):
            r = await run_action(action, self._action_input(st, ac))
            if r.ask and r.ask.get("type") == "already-met" and ac:
                return await self._already_met(i, step, st, ac, r, upd, actions[n + 1:])
            if r.ask:
                return await self._answer_check(i, step, st, r, upd)
            if not r.ok:
                for k in ("blockers", "ladder"):
                    if k in r.update:
                        upd[k] = r.update[k]
                return self._check_failed(i, step, st, ac, r, upd)
            st.update(r.update)
            upd.update(r.update)
            notes.append(r.note)
        key = f"{step.id}:{(ac or {}).get('id')}"
        retries = dict(state.get("retries") or {})
        retries.pop(key, None)
        upd.update(note="; ".join(notes), retries=retries, stall={"fingerprint": None, "count": 0, "step": 0}, last_failure=None)
        return upd, self.nav.after(i)

    async def _already_met(self, i: int, step: Step, state: FlowState, ac: dict, r: ActionResult, upd: dict, rest: list[str]):
        """verify_red found the AC's own test already passing (keel v1 "already-met"). No retries: ask right away.

        approve: run the rest of this step (the red commit: `test(<AC>): ...`), set the AC to already-met and
        skip its green, review and gate steps. reject: back to the red step with the user's note as feedback.
        """
        q = r.ask
        answer, extra = self._ask(state, {"step": step.id, "kind": q["kind"], "title": q["title"], "detail": q["detail"],
                                          "options": OPTIONS})
        upd = {**upd, **extra}
        state = {**state, **extra}
        decision = answer.get("decision", "reject")
        why = (answer.get("why") or "").strip()
        gates = copy.deepcopy(state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
        gates["log"].append(f"ac {ac['id']} already-met {decision}" + (f": {why}" if why else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": decision, "why": why, "ac": ac["id"]})
        key = f"{step.id}:{ac['id']}"
        retries = dict(state.get("retries") or {})
        retries.pop(key, None)
        if decision != "approve":
            feedback = (f"{ac['id']}: the test passed before any code was written, so it does not prove the criterion. "
                        f"Write a stricter test that fails until {ac['id']} is built.")
            if why:
                feedback += f"\n\nFrom the user: {why}"
            target = self.nav.retry_target(i) or step.id
            return {**upd, "gates": gates, "retries": retries, "feedback": feedback, "last_failure": r.note[:300],
                    "note": f"{ac['id']} already passes; sent back for a stricter test" + (f": {why}" if why else "")}, target
        st = {**state, "gates": gates}
        notes = [r.note]
        for action in rest:
            res = await run_action(action, self._action_input(st, ac))
            if not res.ok or res.ask:
                # A refused commit is an ordinary failed check (it goes back to the red agent).
                return self._check_failed(i, step, st, ac, res, {**upd, "gates": gates})
            st.update(res.update)
            upd.update(res.update)
            notes.append(res.note)
        acs = _set_ac(st.get("acs") or [], ac["id"], ALREADY_MET)
        st.update(acs=acs, current=step.id)
        await asyncio.to_thread(self.ctx.write_mirror, st)       # keel v1 shows the AC as already-met too
        upd.update(acs=acs, gates=gates, retries=retries, feedback=None, last_failure=None,
                   stall={"fingerprint": None, "count": 0, "step": 0},
                   note=" · ".join([f"{ac['id']} marked as already met" + (f": {why}" if why else ""), *notes[1:]]))
        return upd, AC_END if step.per_ac and self.nav.last is not None else self.nav.after(i)

    async def _answer_check(self, i: int, step: Step, state: FlowState, r: ActionResult, upd: dict):
        """A commit check asked the user (new dependency, escalation). Apply the answer and run the step again.

        Returning to the same step keeps one interrupt per node run, so a resume can never hand one
        question's answer to another.
        """
        q = r.ask
        answer, extra = self._ask(state, {"step": step.id, "kind": q["kind"], "title": q["title"], "detail": q["detail"],
                                          "options": OPTIONS})
        upd = {**upd, **extra}
        decision = answer.get("decision", "reject")
        why = (answer.get("why") or "").strip()
        gates = copy.deepcopy(state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
        if q["type"] == "deps":
            if decision == "approve":
                deps = list(state.get("deps") or []) + [d for d in q["deps"] if d not in (state.get("deps") or [])]
                gates["log"].append("deps approve: " + ", ".join(q["deps"]) + (f": {why}" if why else ""))
                upd.update(deps=deps, gates=gates, note="new dependency approved: " + ", ".join(q["deps"]))
            else:
                await asyncio.to_thread(revert_manifests, self.ctx.root, q["files"])
                gates["log"].append("deps reject: " + ", ".join(q["deps"]) + (f": {why}" if why else ""))
                upd.update(gates=gates, note="new dependency rejected; manifest put back: " + ", ".join(q["files"]))
            self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": decision, "why": why})
            return upd, step.id
        # escalate
        self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": decision, "why": why})
        if decision == "approve":
            gates["log"].append(f"escalation: {q['why']}")
            return {**upd, "gates": gates, "status": "stopped", "error": f"Escalated to a feature flow: {q['why']}.",
                    "note": "escalated: start a feature flow for this work"}, END
        gates["log"].append(f"escalation-override: {why or q['why']}")
        return {**upd, "gates": gates, "note": f"stays a small change: {why}"}, step.id

    def _check_failed(self, i: int, step: Step, state: FlowState, ac: dict | None, r: ActionResult, upd: dict):
        key = f"{step.id}:{(ac or {}).get('id')}"
        retries = dict(state.get("retries") or {})
        count = retries.get(key, 0) + 1
        fp = hashlib.sha1((r.note + r.detail[:400]).encode()).hexdigest()[:12]
        stall = dict(state.get("stall") or {"fingerprint": None, "count": 0, "step": 0})
        if stall.get("fingerprint") == fp:
            stall["count"] = stall.get("count", 0) + 1
        else:
            stall = {"fingerprint": fp, "count": 1, "step": 0}
        feedback = f"{r.note}\n{r.detail[-2000:]}".strip()
        limit = rules.load_config(self.ctx.root).get("loops", {}).get("stall_repeats", 3)
        if stall["count"] >= limit:
            stall["step"] = min(stall.get("step", 0) + 1, len(rules.LADDER))
            feedback += "\n\nStall ladder: " + rules.LADDER[stall["step"] - 1]
        target = self.nav.retry_target(i)
        attempts = int(self.ctx.settings.get("fix_attempts") or 3)
        base = {**upd, "stall": stall, "last_failure": r.note[:300], "feedback": feedback, "note": r.note}
        if target and count <= attempts:
            retries[key] = count
            return {**base, "retries": retries}, target
        answer, extra = self._ask(state, {"step": step.id, "kind": "fix", "title": f"{step.name} keeps failing",
                                          "detail": f"{r.note}\n\n{r.detail[-1500:]}\n\nApprove to try again, reject to stop the flow.",
                                          "options": OPTIONS})
        base.update(extra)
        if (answer or {}).get("decision") != "approve":
            return {**base, "status": "failed", "error": r.note}, END
        retries[key] = 0
        why = (answer or {}).get("why")
        if why:
            base["feedback"] = f"{feedback}\n\nFrom the user: {why}"
        return {**base, "retries": retries}, target or step.id

    async def gate_step(self, i: int, step: Step, state: FlowState):
        ctx = self.ctx
        ac = _ac(state) if step.per_ac else None
        acs = state.get("acs") or []
        gates = copy.deepcopy(state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
        if ac:
            due = rules.gate_due(gates.get("mode", "every-ac"), acs, ac["id"], gates.get("skipped"), rules.ac_lane(ac))
            if not due["due"]:
                gates["log"].append(f"ac {ac['id']} approve: no gate here ({due['why']})")
                return {"gates": gates, "acs": _set_ac(acs, ac["id"], "done"), "note": f"no gate: {due['why']}"}, self.nav.after(i)
        if self.wf.flow == "init" and step.id == "questions":
            return self._init_questions(step, state, gates), self.nav.after(i)
        options = OPTIONS
        title = step.name + (f" · {ac['id']}" if ac else "")
        no_criteria = not ac and step.phase in ("spec", "triage") and not acs
        if no_criteria:
            # Nothing real to approve: say so and only allow "send back" with what the agent should do.
            options = ["reject"]
            title = f"{step.name} — no acceptance criteria yet"
            said = (state.get("last_answer") or state.get("note") or "").strip()
            detail = ("The spec step wrote no acceptance criteria, so there is nothing to approve yet. "
                      "Send it back and say what to build.\n\nWhat the agent said:\n" + said[:1200])
        elif self.wf.flow == "init" and step.id == "plan_gate":
            detail = init_gates.plan(ctx.root, state.get("init") or init_gates.defaults(ctx.root))
        elif ac:
            detail = f"{ac['id']} [{ac.get('layer', 'API')}] {ac.get('title', '')}\nLast step: {state.get('note', '')}"
        else:
            detail = "\n".join(f"{a['id']} [{a.get('layer', 'API')}] {a.get('title', '')}" for a in acs) or (state.get("note") or "")
            if state.get("spec"):
                detail = f"Spec: {state['spec']}\n{detail}"
        answer, extra = self._ask(state, {"step": step.id, "kind": "gate", "title": title, "detail": detail, "options": options})
        decision = answer.get("decision", "reject") if not no_criteria else "reject"
        why = (answer.get("why") or "").strip()
        payload = answer.get("payload") or {}
        subject = f"ac {ac['id']}" if ac else f"gate {step.id}"
        gates["log"].append(f"{subject} {decision}" + (f": {why}" if why else ""))
        ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": decision, "why": why, "ac": (ac or {}).get("id")})
        upd: dict = {"gates": gates, **extra}
        if payload.get("acs"):
            acs = [{"id": a["id"], "layer": a.get("layer", "API"), "title": a.get("title", ""), "status": a.get("status", "todo")}
                   for a in payload["acs"]]
            upd["acs"] = acs
        if decision == "approve":
            if ac:
                upd["acs"] = _set_ac(acs, ac["id"], "done")
            upd["note"] = f"{step.name} approved" + (f": {why}" if why else "")
            return upd, self.nav.after(i)
        if ac:
            upd["acs"] = _set_ac(acs, ac["id"], "todo")
        upd["feedback"] = why or f"Sent back at {step.name}."
        upd["note"] = f"{step.name} sent back" + (f": {why}" if why else "")
        target = self.nav.jump(step.back, i) if step.back else self.nav.enter(max(i - 1, 0))
        return upd, target

    def _init_questions(self, step: Step, state: FlowState, gates: dict) -> dict:
        """keel init's three questions: approve = the defaults, "Use my answers" = the user's words. Both go on."""
        answer, extra = self._ask(state, {
            "step": step.id, "kind": "gate", "title": "three questions", "detail": init_gates.questions(self.ctx.root),
            "options": OPTIONS, "labels": {"approve": "Use the defaults", "reject": "Use my answers"}})
        why = (answer.get("why") or "").strip()
        mine = answer.get("decision") == "reject" and why
        init = init_gates.answers(self.ctx.root, why if mine else None)
        gates["log"].append(f"gate questions: {'answered: ' + why if mine else 'defaults'}")
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "why": why if mine else "defaults"})
        return {"gates": gates, **extra, "init": init,
                "note": "answers: " + ", ".join(f"{k} {v}" for k, v in init.items() if k != "said")[:300]}

    async def branch_step(self, i: int, step: Step, state: FlowState):
        yes = True
        note = "yes"
        if step.action and step.action.startswith("run:"):
            r = await run_action(step.action, self._action_input(state, _ac(state)))
            yes, note = r.ok, r.note
        elif step.agent:
            res, _m, _tb = await self._run_agent(state, step, step.agent, 0)
            yes = not res.text.strip().lower().startswith("no")
            note = res.text.strip()[:160]
        target = self.nav.after(i) if yes else self.nav.jump(step.no, i)
        return {"note": f"{step.name}: {'yes' if yes else 'no'} ({note})"}, target


def compile_workflow(ctx: ThreadContext, checkpointer):
    return Compiler(ctx).build(checkpointer)
