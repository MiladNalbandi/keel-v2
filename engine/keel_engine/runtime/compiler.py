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
import json
import logging
import tempfile
import time
import uuid
from pathlib import Path

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .. import models, rules
from ..models import catalog
from ..models import usage as provider_usage
from ..models.base import AgentRequest, AgentResult
from .findings import REVIEWERS, blocking, unique
from ..tools import git, guard, mcp
from ..tools.agent_tools import ToolBox
from ..workflows.model import Loop, Step, Workflow
from . import agent_knowledge, clarify, guard_ctx, init_gates, markers, prompts, run_mode, spec_check
from . import tools as tool_runner
from . import memory as memory_mod
from . import ship as ship_mod
from .actions import ActionInput, ActionResult, revert_manifests, run_action
from .state import FlowState, ThreadContext, merge_unlocks, normalize_unlocks

log = logging.getLogger(__name__)

AC_BEGIN, AC_END, FINISH = "__ac_begin", "__ac_end", "__finish"
END_TARGET = "end"                   # a branch's `no` or a gate choice that finishes the flow
KNOWN_SECTIONS = {"architecture", "domain", "conventions", "data", "integrations"}
OPTIONS = ["approve", "reject"]
RESUMABLE = {"claude", "codex"}      # CLIs whose sessions keel can continue (claude --resume, codex exec resume)
ALREADY_MET = "already-met"
DONE = ("done", ALREADY_MET)        # AC statuses the per-AC loop is finished with
ITEM_DONE = ("done", "skipped", "failed")   # item statuses a for_each loop is finished with (todo is the rest)
# Where a review fix goes (findings_step, payload.route) and the actions that follow the fixing agent.
ROUTES = {"review-fix": ("verify_green", "commit"), "coverage-fix": ("verify_green", "commit"), "red": ("commit",)}
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
    """Where to go next, given the step list and its loops (the per-AC loop and the for_each loops).

    A loop is entered through its begin node, which picks the next unfinished entry (AC or item) or leaves the loop;
    its last step goes to its end node, which marks the entry done and goes round to the begin node.
    """

    def __init__(self, wf: Workflow):
        self.steps = wf.steps
        self.index = {s.id: i for i, s in enumerate(wf.steps)}
        self.loops = wf.loops()
        ac = next((lp for lp in self.loops if lp.per_ac), None)
        self.first, self.last = (ac.first, ac.last) if ac else (None, None)

    def loop_of(self, i: int) -> Loop | None:
        return next((lp for lp in self.loops if lp.first <= i <= lp.last), None)

    @staticmethod
    def begin(lp: Loop) -> str:
        return AC_BEGIN if lp.per_ac else f"__each_{lp.id}"

    @staticmethod
    def end(lp: Loop) -> str:
        return AC_END if lp.per_ac else f"__each_{lp.id}_end"

    def enter(self, j: int) -> str:
        if j >= len(self.steps):
            return FINISH
        lp = next((lp for lp in self.loops if lp.first == j), None)
        return self.begin(lp) if lp else self.steps[j].id

    def after(self, i: int) -> str:
        lp = self.loop_of(i)
        if lp and i == lp.last:
            return self.end(lp)
        return self.enter(i + 1)

    def after_loop(self, lp: Loop | None = None) -> str:
        lp = lp or next((x for x in self.loops if x.per_ac), None)
        return self.enter(lp.last + 1) if lp else FINISH

    def jump(self, sid: str, from_i: int) -> str:
        if sid == END_TARGET:
            return FINISH
        j = self.index[sid]
        target, here = self.loop_of(j), self.loop_of(from_i)
        if target and (not here or here.id != target.id):
            return self.begin(target)
        return self.steps[j].id if target else self.enter(j)

    def retry_target(self, i: int) -> str | None:
        """The agent step a failed check sends work back to (never across a loop's edge)."""
        here = (self.loop_of(i) or Loop(id="", key="", first=-1, last=-1)).id
        for k in range(i - 1, -1, -1):
            s = self.steps[k]
            if (self.loop_of(k) or Loop(id="", key="", first=-1, last=-1)).id != here:
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


def get_list(state: FlowState, key: str) -> list[dict]:
    """A list of items in the state: state.data[key] (collected by a step, or a seed), else the state's own key (acs).
    Entries that are not dicts are left out; every entry has an id."""
    data = state.get("data") or {}
    raw = data[key] if key in data else state.get(key)
    if not isinstance(raw, list):
        return []
    return [dict(x, id=str(x.get("id") or f"{key}-{n + 1}")) for n, x in enumerate(raw) if isinstance(x, dict)]


def put_list(state: FlowState, key: str, items: list[dict]) -> dict:
    """The state update that stores a list where get_list reads it."""
    data = state.get("data") or {}
    if key not in data and key in FlowState.__annotations__:
        return {key: items}
    return {"data": {**data, key: items}}


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
        for lp in self.nav.loops:
            g.add_node(self.nav.begin(lp), self._loop_begin(lp))
            g.add_node(self.nav.end(lp), self._loop_end(lp))
        g.add_node(FINISH, self.finish)
        g.add_edge(START, self.nav.enter(0))
        return g.compile(checkpointer=checkpointer)

    def _wrap(self, i: int, step: Step, fn):
        ctx = self.ctx

        async def node(state: FlowState):
            if state.get("status") in ("stopped", "failed"):
                return Command(goto=END)
            # Unlocks granted through the engine API (POST /threads/{id}/unlocks) since the last step join the thread's state.
            have = list(state.get("unlocks") or [])
            merged = merge_unlocks(have, ctx.api_unlocks)
            if len(merged) != len(have):
                state = {**state, "unlocks": merged}
            prev = state.get("phase") or "none"
            phase = step.phase or prev
            ac = state.get("ac") if step.per_ac else None
            item = state.get("item") if step.per_item else None
            skipped = self._skipped(i, step, state)
            if skipped:
                why, goto = skipped
                ctx.emit("step.started", step=step.id, data={"name": step.name, "kind": step.kind, "phase": prev, "ac": ac,
                                                             "from": prev, "flow": self.wf.flow, "skipped": True})
                ctx.emit("step.finished", step=step.id, data={"name": step.name, "kind": step.kind, "phase": prev, "ac": ac,
                                                              "ok": True, "note": why, "skipped": True})
                return Command(update={"note": why, "current": step.id,
                                       **({"unlocks": merged} if len(merged) != len(have) else {})}, goto=goto)
            started = {"name": step.name, "kind": step.kind, "phase": phase, "ac": ac, "from": prev, "flow": self.wf.flow,
                       "phase_changed": prev != phase, "transition_ok": rules.can_transition(prev, phase)}
            if item:
                started["item"] = item
            ctx.emit("step.started", step=step.id, data=started)
            update, goto = await fn(i, step, {**state, "phase": phase})
            if len(merged) != len(have):
                update.setdefault("unlocks", merged)
            update.setdefault("phase", phase)
            update.setdefault("current", step.id)
            failed = update.get("status") in ("stopped", "failed")
            finished = {"name": step.name, "kind": step.kind, "phase": phase, "ac": ac, "ok": not failed, "note": update.get("note")}
            if item:
                finished["item"] = item
            ctx.emit("step.finished", step=step.id, data=finished)
            return Command(update=update, goto=END if failed else goto)

        node.__name__ = f"step_{step.id}"
        return node

    def _skipped(self, i: int, step: Step, state: FlowState) -> tuple[str, str] | None:
        """(why, where to) when this step does not run: turned off at the opening skip menu (the whole unit of steps
        that share its group is passed over), or a retry_only step that nothing sent work back to."""
        unit = ship_mod.unit_of(step)
        if step.skippable:
            skip = next((s for s in (state.get("data") or {}).get("ship_skipped") or [] if s.get("step") == unit), None)
            if skip:
                j = i
                while j + 1 < len(self.wf.steps) and self.wf.steps[j + 1].skippable and ship_mod.unit_of(self.wf.steps[j + 1]) == unit:
                    j += 1
                return f"{unit} skipped: {skip.get('reason') or 'no reason given'}", self.nav.after(j)
        if step.retry_only and not (state.get("last_failure") or state.get("feedback")):
            return f"{step.name}: nothing was sent back; not needed", self.nav.after(i)
        if step.when and step.kind in ("agent", "parallel", "code"):
            # On a working step, `when` says when it runs (feature: the explorers only once there are criteria).
            label, values = self._when(state, step.when)
            if not self._holds(step.when, values):
                return f"{step.name}: not needed ({label}: {self._shown_values(values)})", self.nav.after(i)
        return None

    # ------------------------------------------------------------ loop + end

    def _loop_begin(self, lp: Loop):
        """The loop's entry: the next AC (or item) that is not finished, else on past the loop."""
        cursor, finished = ("ac", DONE) if lp.per_ac else ("item", ITEM_DONE)

        async def begin(state: FlowState):
            if state.get("status") in ("stopped", "failed"):
                return Command(goto=END)
            nxt = next((a for a in get_list(state, lp.key) if a.get("status") not in finished), None)
            if not nxt:
                return Command(update={cursor: None}, goto=self.nav.after_loop(lp))
            return Command(update={cursor: nxt["id"], "retries": {}}, goto=self.wf.steps[lp.first].id)

        begin.__name__ = f"loop_begin_{lp.id}"
        return begin

    def _loop_end(self, lp: Loop):
        """The loop's last step is through: the entry is done (unless it is already met, skipped or failed)."""
        cursor = "ac" if lp.per_ac else "item"
        keep = (ALREADY_MET,) if lp.per_ac else ("skipped", "failed")

        async def end(state: FlowState):
            items = get_list(state, lp.key)
            cur = next((a for a in items if a["id"] == state.get(cursor)), None)
            if cur and cur.get("status") not in keep:
                items = _set_ac(items, state[cursor], "done")
            return Command(update=put_list(state, lp.key, items), goto=self.nav.begin(lp))

        end.__name__ = f"loop_end_{lp.id}"
        return end

    def _item(self, state: FlowState, step: Step) -> dict | None:
        """The current item of the for_each loop this step is in."""
        if not step.per_item:
            return None
        lp = self.nav.loop_of(self.nav.index[step.id]) if step.id in self.nav.index else None
        if not lp:
            return None
        return next((x for x in get_list(state, lp.key) if x["id"] == state.get("item")), None)

    async def finish(self, state: FlowState):
        return Command(update={"status": "done", "ac": None, "note": "done"}, goto=END)

    # ------------------------------------------------------------ models

    def _model(self, state: FlowState, step: Step, agent: str) -> dict:
        m = None
        if state.get("model_override"):
            m = state["model_override"]
        elif (state.get("agent_models") or {}).get(agent):
            m = state["agent_models"][agent]
        elif step.model and step.model != "default" and step.model in self.ctx.models:
            m = self.ctx.models[step.model]
        elif agent in self.ctx.models:
            m = self.ctx.models[agent]
        elif "default" in self.ctx.models:
            m = self.ctx.models["default"]
        return models.effective(m)

    def _ask(self, state: FlowState, question: dict, gate: str | None = None, review: str | None = None) -> tuple[dict, dict]:
        """interrupt() for any pause, plus what every answer may carry: payload.unlock {path, phase}.

        The thread's run mode may answer first (runtime/run_mode.py): `gate` names the kind of pause when the caller knows
        it (else it is classified from the question), `review` is the AC review's answer for an AC gate. An automatic
        answer is {decision: approve, why: "auto-approved (mode <m>)", auto: true}; the caller logs it like any answer.

        Returns (answer, state update). An unlock is added to state.unlocks and logged as a gate event.
        """
        answer = self._auto_answer(state, question, gate, review)
        if answer is None:
            answer = ask_once(question)
        payload = answer.get("payload") or {}
        new = normalize_unlocks(payload.get("unlock"), state.get("phase") or "none", "user")
        if not new:
            return answer, {}
        have = list(state.get("unlocks") or [])
        merged = merge_unlocks(have, new)
        for u in merged[len(have):]:
            self.ctx.emit("gate.decided", step=question.get("step"), data={
                "gate": "unlock", "decision": "approve", "unlock": {"path": u["path"], "phase": u["phase"]},
                "why": f"{u['path']} in {u['phase']}" + (f": {answer.get('why')}" if answer.get("why") else "")})
        return answer, {"unlocks": merged}

    def _mode(self) -> str:
        return run_mode.normalize((self.ctx.settings or {}).get("run_mode"))

    def _readonly(self) -> bool:
        return self._mode() == "readonly"

    def _auto_answer(self, state: FlowState, question: dict, gate: str | None, review: str | None) -> dict | None:
        """The run mode's answer to this pause, or None to ask. A question the user is answering right now (it waited
        before the mode changed: the resume carries its id) is always theirs. keel approves the very same question by
        itself at most run_mode.MAX_REPEATS times in a run; then it asks."""
        mode = self._mode()
        if mode in ("manual", "readonly"):
            return None
        qid = question_id(question)
        if getattr(self.ctx, "resume_qid", None) == qid:
            self.ctx.resume_qid = None
            return None
        kind = gate or run_mode.classify(question)
        seen = self.ctx.auto_seen.get(qid, 0)
        answer = run_mode.decide(mode, kind, question, dict(state), review=review, repeats=seen)
        if answer:
            self.ctx.auto_seen[qid] = seen + 1
        return answer

    def _gate_kind(self, i: int, step: Step, state: FlowState, question: dict, ac: dict | None) -> tuple[str, str | None]:
        """A gate step's kind for the run mode, and the AC review's answer when an AC gate follows a review step."""
        prev = self.wf.steps[i - 1] if i else None
        kind = run_mode.classify(question, step, flow=self.wf.flow, prev_actions=prev.actions() if prev else (), per_ac=bool(ac))
        review = None
        if kind == "ac" and prev and prev.per_ac and prev.kind in ("agent", "parallel") and \
                (prev.agent or "") in ("ac-reviewer", "reviewer", "code-reviewer"):
            review = state.get("last_answer") or ""
        return kind, review

    async def _run_agent(self, state: FlowState, step: Step, agent: str, index: int, section: str | None = None,
                         item: dict | None = None) -> tuple[AgentResult, dict, ToolBox]:
        ctx = self.ctx
        phase = state["phase"]
        ac = _ac(state) if step.per_ac else None
        model = self._model(state, step, agent)
        call_id = uuid.uuid4().hex
        cfg = rules.load_config(ctx.root)
        # What this agent may use: its knowledge sections, the code graph, its memory (agent_knowledge.py).
        know = agent_knowledge.for_agent(agent, ctx.agents)
        # The code graph joins the MCP servers once the project's index is ready (scan.py); filter_mcp keeps it only
        # for agents with code_graph on.
        graph = await asyncio.to_thread(mcp.codegraph_server_spec, ctx.root)
        specs = list(ctx.mcp) + ([graph] if graph and not any(s.get("name") == graph["name"] for s in ctx.mcp) else [])
        allow = list(step.tools or []) + ([f"mcp:{graph['name']}:*"] if graph else [])
        mcp_specs, tools_allow = agent_knowledge.filter_mcp(specs, allow, know)

        def on_refuse(tool: str, path: str, reason: str, command: str | None = None):
            data = {"tool": tool, "path": path or "", "reason": reason, "agent": agent, "phase": phase}
            if command:
                data["command"] = command
            ctx.emit("guard.refused", step=step.id, call_id=call_id, data=data)

        toolbox = ToolBox(ctx.root, phase, cfg=cfg, lane=rules.ac_lane(ac) if ac else None,
                          ac=(ac or {}).get("id"), ac_layer=(ac or {}).get("layer", "API"), on_refuse=on_refuse,
                          unlocks=state.get("unlocks") or [], agent=agent, knowledge=know, readonly=self._readonly())
        ctx.emit("agent.started", step=step.id, call_id=call_id, data={
            "agent": agent, "provider": model["provider"], "model": model.get("model"), "mode": model.get("mode"),
            "phase": phase, "ac": (ac or {}).get("id"), "index": index, **({"item": item["id"]} if item else {})})
        counter = {"n": 0}

        # Agent memory: the same step again (restart, try again, send-back) continues this agent's own session.
        mem = ctx.memory
        key = memory_mod.attempt_key(step.id, ac or item, agent, index, section)
        prev = await mem.get(key) if mem and know["memory"] else None
        same = bool(prev and prev["provider"] == model["provider"] and prev["root"] == ctx.root)
        can_resume = model.get("mode") != "api" and model["provider"] in RESUMABLE
        resuming = bool(same and can_resume and prev.get("session"))
        session = prev["session"] if resuming else (str(uuid.uuid4()) if can_resume and model["provider"] == "claude" else None)
        if mem:
            await mem.start(key, model["provider"], ctx.root, session, keep_trail=same)

        def emit(kind: str, text: str = "", **extra):
            counter["n"] += 1
            # Every string field (text, diff, output, ...) is bounded; newlines are kept for the web's Markdown and diff views.
            data = {"n": counter["n"], "kind": kind, "text": str(text)[:STEP_FIELD_MAX]}
            data.update({k: (v[:STEP_FIELD_MAX] if isinstance(v, str) else v) for k, v in extra.items() if v is not None})
            ctx.emit("agent.step", step=step.id, call_id=call_id, data=data)
            line = memory_mod.trail_line(kind, str(text), extra) if mem else None
            if line:
                mem.note(key, line)

        prompt = prompts.task_prompt(agent=agent, phase=phase, step_name=step.name, title=ctx.title, root=ctx.root, ac=ac,
                                     acs=state.get("acs") or [], feedback=state.get("feedback"), index=index, spec=state.get("spec"),
                                     section=section, unlocks=state.get("unlocks") or [], request=ctx.request,
                                     knowledge=know, graph=agent_knowledge.has_codegraph(mcp_specs, tools_allow), item=item)
        asks = prompts.step_asks(step, lambda path: self._seed_value("$" + path, state, item))
        if asks:
            prompt = f"{prompt}\n{asks}"
        lint_notes = (state.get("data") or {}).get("lint_notes") or []
        if lint_notes:
            why = "know them while you review" if rules.read_only(phase) else "fix them when they are in your scope"
            prompt += (f"\n\nStatic checks on the files the last step changed ({why}):\n"
                       + "\n".join(f"- {n}" for n in lint_notes))
        note = memory_mod.resume_note(prev, resuming) if same else ""
        if note:
            prompt = f"{note}\n\n{prompt}"
            emit("text", "Continuing this agent's earlier session for this step." if resuming
                 else "This agent gets a summary of its last try on this step.")
        with tempfile.TemporaryDirectory(prefix="keel-agent-") as tmp:
            req = AgentRequest(agent=agent, system=prompts.system_prompt(agent, ctx.skills), prompt=prompt, root=ctx.root,
                               phase=phase, model=model, toolbox=toolbox, ac=ac, acs=state.get("acs") or [], title=ctx.title,
                               step_name=step.name, index=index, feedback=state.get("feedback"), mcp_specs=mcp_specs,
                               tools_allow=tools_allow, key=models.key_for(model["provider"], ctx.keys), workdir=tmp,
                               keys=dict(ctx.keys), section=section, session=session, resume=resuming,
                               on_session=(lambda sid: mem.set_session(key, sid)) if mem else None, thread=ctx.thread_id,
                               knowledge=know, item=item)
            # The guard context keel's hook reads on every tool call (CLI agents). It sits in this run's scratch folder,
            # outside the project; an unlock granted while the agent runs rewrites it (ThreadContext.add_unlocks).
            gfile = guard_ctx.GuardFile(Path(tmp) / guard_ctx.FILE, **guard_ctx.context_for(req))
            req.guard_ctx = gfile.path
            live = [gfile, toolbox]
            ctx.guards.extend(live)
            t0 = time.time()
            try:
                res = await models.runner_for(model).run(req, emit)
            except asyncio.CancelledError:
                ctx.emit("agent.finished", step=step.id, call_id=call_id, data={"agent": agent, "status": "stopped", "tokens_in": 0,
                                                                                "tokens_out": 0, "cost_usd": 0, "premium_requests": 0})
                raise
            except Exception as exc:
                emit("error", f"{exc}{(' ' + exc.hint) if getattr(exc, 'hint', '') else ''}", ok=False)
                if mem:
                    await mem.finish(key, "failed", str(exc))
                used = getattr(exc, "usage", None) or {}
                # A failed run still used tokens (a turn limit after 30 turns is not free): the job and the budget count them.
                ctx.emit("agent.finished", step=step.id, call_id=call_id, data={
                    "agent": agent, "status": "failed", "tokens_in": used.get("tokens_in", 0), "tokens_out": used.get("tokens_out", 0),
                    "tokens_cached": used.get("tokens_cached", 0), "cost_usd": round(used.get("cost_usd", 0.0), 6),
                    "premium_requests": 0, "result": str(exc)[:500]})
                raise
            finally:
                for g in live:
                    ctx.guards.remove(g)
                self._usage_event(step, model["provider"], t0)
        if mem:
            await mem.finish(key, "done", res.text or "")
        if not res.cost_usd and model.get("mode") == "api" and model["provider"] != "fake":
            res.cost_usd = catalog.cost_usd(model["provider"], model.get("model", ""), res.tokens_in, res.tokens_out)
        ctx.emit("agent.finished", step=step.id, call_id=call_id, data={
            "agent": agent, "status": "done", "tokens_in": res.tokens_in, "tokens_out": res.tokens_out, "tokens_cached": res.tokens_cached,
            "cost_usd": round(res.cost_usd, 6), "premium_requests": res.premium_requests, "result": res.text[:2000]})
        return res, model, toolbox

    # ------------------------------------------------------------ budget

    def _usage_event(self, step: Step, provider: str, since: float):
        """provider.usage: the plan's windows this run reported (claude's rate_limit_event), for the api's usage cards."""
        entry = provider_usage.LATEST.get(provider)
        if entry and entry["at"] >= since:
            self.ctx.emit("provider.usage", step=step.id, data={"provider": provider, "windows": entry["windows"],
                                                                "source": entry["source"], "at": provider_usage.iso(entry["at"])})

    async def _provider_window(self, state: FlowState, step: Step) -> dict:
        """Before a subscription agent: warn when its plan window is nearly used (settings.usage_warn, default 0.80) and
        pause at settings.usage_pause (default 0.95): continue, wait for the reset, use the cheaper model, or stop."""
        ctx = self.ctx
        model = self._model(state, step, step.agent or "")
        provider = model.get("provider")
        if model.get("mode") == "api" or provider in (None, "fake"):
            return {}
        await provider_usage.refresh_before_step(model, ctx.keys)
        w = provider_usage.fullest(provider)
        warn, pause = float(ctx.settings.get("usage_warn") or 0.8), float(ctx.settings.get("usage_pause") or 0.95)
        if not w or w["used_pct"] < warn:
            return {}
        seen = dict(state.get("usage_seen") or {})
        key = f"{provider}:{w['window']}:{w.get('resets_at') or ''}"
        info = {"provider": provider, "window": w["window"], "used_pct": w["used_pct"], "resets_at": provider_usage.iso(w.get("resets_at")),
                "detail": provider_usage.headline(provider, w), "kind": "usage"}
        if w["used_pct"] < pause or seen.get(key) == "ok":
            if seen.get(key):
                return {}
            ctx.emit("budget.warn", step=step.id, data=info)
            return {"usage_seen": {**seen, key: "warn"}}
        qkey = (ctx.thread_id, step.id, key)
        question = provider_usage.QUESTIONS.setdefault(qkey, {
            "step": step.id, "kind": "usage", "title": info["detail"], "options": OPTIONS, "choices": ["continue", "wait", "cheaper", "stop"],
            "detail": "Continue uses the plan anyway (the provider may refuse when it is full). Wait sleeps until the window resets, "
                      "then goes on. Cheaper switches to the cheaper model in settings. Stop ends the flow here."})
        answer, extra = self._ask(state, question)
        provider_usage.QUESTIONS.pop(qkey, None)
        choice = ((answer or {}).get("payload") or {}).get("choice")
        if (answer or {}).get("decision") != "approve" or choice == "stop":
            ctx.emit("budget.stop", step=step.id, data={**info, "why": (answer or {}).get("why")})
            return {**extra, "status": "stopped", "note": f"stopped: {info['detail']}"}
        upd = {**extra, "usage_seen": {**seen, key: "ok"}}
        cheaper = ctx.settings.get("cheaper_model")
        if choice == "cheaper" and cheaper:
            ctx.emit("budget.warn", step=step.id, data={**info, "action": "cheaper", "model": cheaper})
            return {**upd, "model_override": cheaper, "note": "plan window nearly used; switched to the cheaper model"}
        if choice == "wait" and w.get("resets_at"):
            delay = min(max(0.0, w["resets_at"] - time.time()) + 30, 8 * 24 * 3600)
            ctx.emit("budget.warn", step=step.id, data={**info, "action": "wait", "seconds": int(delay)})
            await asyncio.sleep(delay)
        return upd

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
        plan = await self._provider_window(state, step)
        upd = {**upd, **plan}
        if plan.get("status") == "stopped":
            return upd, END
        state = {**state, **plan}

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
        # Dynamic fan-out: one call per item of a state list (at most `cap`); a for_each step gives its item to each call.
        loop_item = self._item(state, step)
        item_of: dict[int, dict] = {}
        if step.items_from:
            items = get_list(state, step.items_from)[: step.cap or None]
            if not items:
                note = f"{step.name}: no items in {step.items_from}; no agent ran"
                return {**upd, "note": note, "data": {**(state.get("data") or {}), f"{step.id}_results": []}}, self.nav.after(i)
            calls = [(self._item_agent(step, it), k) for k, it in enumerate(items)]
            item_of = dict(enumerate(items))
        elif loop_item:
            item_of = {n: loop_item for n in range(len(calls))}

        before = await asyncio.to_thread(guard.snapshot, ctx.root)
        ac = _ac(state) if step.per_ac else None

        async def lane(action: str) -> ActionResult:
            r = await run_action(action, self._action_input(state, ac))
            if not r.ok:
                raise LaneFailed(f"{action}: {r.note}\n{r.detail}")
            return r

        done = ctx.done_calls
        attempt = f"{step.id}|{(ac or loop_item or {}).get('id', '')}"
        # `batch: N`: at most N agents of this step run at the same time; the step is still one node.
        # "$data.x" reads the number from the state when the step runs.
        batch = self._seed_value(step.batch, state, None) if isinstance(step.batch, str) else step.batch
        batch = int(batch) if isinstance(batch, (int, float)) or str(batch or "").isdigit() else None
        gate = asyncio.Semaphore(batch) if batch and batch > 0 else None

        async def one(n: int, a: str, k: int):
            key = f"{attempt}|{a}|{k}"
            if key in done:
                return done[key]
            if gate:
                async with gate:
                    r = await self._run_agent(state, step, a, k, section_of.get(n), item_of.get(n))
            else:
                r = await self._run_agent(state, step, a, k, section_of.get(n), item_of.get(n))
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
                log.warning("step %s failed: %s", step.id, exc, exc_info=True)
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
                                          rules.ac_lane(ac) if ac else None, state.get("unlocks") or [], self._readonly())
        for r in refused:
            ctx.emit("guard.refused", step=step.id, data={"tool": "diff-guard", "phase": state["phase"], **r})
        tools_note = ""
        if agent_results:
            tools_note, lint_data = await self._after_edit(step, state, before, refused)
            if lint_data is not None:
                state = {**state, "data": lint_data}
                upd["data"] = lint_data

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
        if tools_note:
            upd["note"] += f" · {tools_note}"
        if sections and state.get("acs"):
            upd["acs"] = [dict(a, status="done") if a["id"] in sections else dict(a) for a in state["acs"]]

        for res, _m, tb in agent_results:
            # A CLI agent stopped by keel's PreToolUse hook: the runner reported it as a guard step.
            for r in res.data.get("refusals") or []:
                data = {"tool": r.get("tool") or "hook", "phase": state["phase"], "path": r.get("path") or "",
                        "reason": r.get("reason", ""), "source": "keel-hook"}
                if r.get("command"):
                    data["command"] = r["command"]
                ctx.emit("guard.refused", step=step.id, data=data)
        if agent_results:
            upd["last_answer"] = (agent_results[-1][0].text or "")[:8000]
        upd.update(self._outputs(step, state, [r[0] for r in agent_results], item_of))
        said = ((upd.get("markers") or {}).get(step.id) or {}) if step.markers else {}
        if said:
            upd["note"] += " · " + ", ".join(f"{k}: {v}" for k, v in said.items())
        if state["phase"] in ("spec", "triage") and not state.get("acs"):
            acs, spec = self._acs_from(agent_results)
            if acs:
                # keel's spec check: something clear is missing (a screen with no mockup, an API with no request path)
                # → send the spec back to the explorer ONCE, in the same session. Otherwise the gate shows the warnings.
                found = spec_check.check(await asyncio.to_thread(spec_check.read_spec, ctx.root, spec or state.get("spec")), acs)
                if any(f["level"] == "fix" for f in found) and not state.get("spec_revisions") \
                        and ctx.settings.get("spec_check", not ctx.fake):
                    upd.update(spec_revisions=1, feedback=spec_check.revision_note(found), clarify={},
                               note=upd["note"] + " · spec check: sent back once")
                    if spec:
                        upd["spec"] = spec
                    return upd, self.nav.jump(step.id, i)
                upd["acs"] = acs
                upd["clarify"] = {}
            elif state.get("clarify_rounds", 0) < clarify.MAX_ROUNDS:
                # No criteria, but questions: the spec gate shows them as buttons (clarify loop).
                asked = [q for res, _m, _tb in agent_results for q in clarify.parse_questions(res.text)]
                upd["clarify"] = {"questions": asked[:clarify.MAX_QUESTIONS]} if asked else {}
            if spec:
                upd["spec"] = spec
        if self._reviews(step):
            def lens(n: int) -> str:
                if step.items_from and n in item_of:
                    return f"{step.name}: {item_of[n].get('title') or item_of[n]['id']}"
                return f"{step.name} #{n + 1}" if len(agent_results) > 1 else step.name
            found = unique([{"lens": lens(n), "text": t}
                            for n, (res, _m, _tb) in enumerate(agent_results) for t in blocking(res.text)])
            upd["findings"] = found
            if found:
                upd["note"] += f" · {len(found)} blocking finding(s)"
                key = self._rounds_key(step, loop_item)
                done_rounds = int((state.get("review_rounds") or {}).get(key, 0))
                if step.back and done_rounds < (step.rounds or 2):
                    # Findings go straight back to the step that wrote the work (cover: the test author), no question,
                    # until the rounds are used up; then the fix node asks.
                    rounds = {**(state.get("review_rounds") or {}), key: done_rounds + 1}
                    listed = "\n".join(f"- [{f['lens']}] {f['text']}" for f in found)
                    upd.update(review_rounds=rounds, findings=[],
                               feedback=f"Fix these blocking findings from {step.name} (round {done_rounds + 1}):\n{listed}")
                    upd["note"] += f" · sent back to {step.back} (round {done_rounds + 1})"
                    return upd, self.nav.jump(step.back, i)
                return upd, fix_id(step.id)
        return upd, self.nav.after(i)

    def _tool_emit(self, step_id: str):
        """Each tool run as an event `tool.ran` with its full output (the dashboard); the agent only gets one line."""
        def emit(r: dict):
            self.ctx.emit("tool.ran", step=step_id, data={
                "tool": r["name"], "on": r["on"], "fail": r["fail"], "ok": r["ok"], "available": r["available"],
                "ms": r["ms"], "files": r["files"], "cmd": r["cmd"], "dir": r["dir"], "code": r["code"],
                "source": r.get("source"), "output": r.get("output", "")[:STEP_FIELD_MAX]})
        return emit

    async def _after_edit(self, step: Step, state: FlowState, before, refused: list[dict]) -> tuple[str, dict | None]:
        """After an agent step that changed files: the project's `edit` and `batch` tools on those files (runtime/tools.py).
        A formatter's changes stay; block and warn failures go to data.lint_notes, which the next agent's prompt and
        the next gate show. Returns (a note for the step, the new data or None when nothing ran)."""
        ctx = self.ctx
        if before is None or rules.read_only(state.get("phase")) or self._readonly():
            return "", None
        gone = {r.get("path") for r in refused}
        changed = await asyncio.to_thread(guard.changed_since, ctx.root, before)
        files = [f for f in changed if f not in gone]
        if not files:
            return "", None
        res = await asyncio.to_thread(tool_runner.after_edit, ctx.root, files, include_stacks=not ctx.simulate_checks,
                                      emit=self._tool_emit(step.id))
        if not res["ran"]:
            return "", None
        data = {**(state.get("data") or {}), "lint_notes": res["notes"]}
        parts = [f"tools: {len(res['ran'])} ran"]
        if res["changed"]:
            parts.append(f"{len(res['changed'])} file(s) formatted")
        if res["notes"]:
            parts.append(f"{len(res['notes'])} finding(s): " + "; ".join(n.split(':')[0] for n in res["notes"]))
        return ", ".join(parts), data

    def _item_agent(self, step: Step, item: dict) -> str:
        """The agent for one item of a fan-out: the item's own `agent` when the list was made by the flow's code
        (review_scope: reviewer, code-reviewer or ac-reviewer), never when an agent's answer made the list."""
        own = item.get("agent")
        if own and not any(s.collect == step.items_from for s in self.wf.steps):
            return str(own)
        return step.agent or ""

    @staticmethod
    def _rounds_key(step: Step, item: dict | None) -> str:
        """Fix rounds count per review step, and per item inside a for_each loop."""
        return f"{step.id}:{item['id']}" if item and step.per_item else step.id

    def _outputs(self, step: Step, state: FlowState, results: list[AgentResult], item_of: dict[int, dict]) -> dict:
        """What a step's answers put into the state: per-item results (fan-out), markers, a collected list."""
        upd: dict = {}
        data = dict(state.get("data") or {})
        found = [markers.parse(r.text, step.markers) if step.markers else {} for r in results]
        if step.items_from:
            data[f"{step.id}_results"] = [{"item": item_of[n]["id"], "title": item_of[n].get("title") or item_of[n]["id"],
                                           "text": (r.text or "")[:16000], "markers": found[n]} for n, r in enumerate(results)]
        if step.markers:
            mine: dict = {}
            for f in found:
                mine.update(f)
            mk = dict(state.get("markers") or {})
            mk[step.id] = mine
            mk["*"] = {**(mk.get("*") or {}), **mine}
            upd["markers"] = mk
        if step.collect:
            got: list[dict] = []
            for n, r in enumerate(results):
                # In a fan-out, each collected entry says which item's agent gave it (from_item).
                src = (item_of.get(n) or {}).get("id") if step.items_from else None
                got += [dict(x, from_item=src) if src else x for x in markers.collect(r.text, step.collect)]
            ids = [x["id"] for x in got]
            if len(set(ids)) != len(ids):
                got = [dict(x, id=f"{step.collect}-{n + 1}") for n, x in enumerate(got)]
            data[step.collect] = got
        if data != (state.get("data") or {}):
            upd["data"] = data
        return upd

    def _reviews(self, step: Step) -> bool:
        """A review step whose blocking findings must be fixed or accepted before the flow goes on."""
        if rules.read_only(step.phase):
            return False      # a read-only flow (review) reports its findings; nothing is fixed
        return step.kind in ("agent", "parallel") and not step.per_ac and not step.lanes and (step.agent or "") in REVIEWERS

    async def findings_step(self, i: int, fix: Step, state: FlowState):
        """After a review with blocking findings: ask; fix and review again, or go on with the user's reason (the
        findings are kept as dismissed). Nothing runs before the question, so answering it never re-runs anything.

        The fix is routed by payload.route: review-fix (default: implementer, tests, fix commit), coverage-fix
        (test-author, tests, coverage commit) or red (test-author, red commit). After it the flow goes on from the
        review's `redo` step (default the review itself). A review with `back` sends its findings to that step by
        itself; this question only comes once its `rounds` are used up, and approving sends them back once more.
        """
        ctx = self.ctx
        review = self.wf.steps[i]
        item = self._item(state, review)
        found = list(state.get("findings") or [])
        rounds = dict(state.get("review_rounds") or {})
        key = self._rounds_key(review, item)
        done_rounds = rounds.get(key, 0)
        listed = "\n".join(f"- [{f['lens']}] {f['text']}" for f in found)
        again = f" (fix round {done_rounds + 1})" if done_rounds else ""
        limit = review.rounds or (2 if review.back else None)
        past = f"\n\n{done_rounds} fix round(s) done, the limit is {limit}: the check and the change disagree, and " \
               f"repeating may not settle it. Deciding is yours." if limit and done_rounds >= limit else ""
        fix_txt = (f"Send back: {review.back} gets these findings once more, then {review.name} runs again." if review.back else
                   f"Fix them: the implementer fixes these findings (payload route: review-fix, coverage-fix or red picks "
                   f"who and how), the tests run, the fix is committed, and the flow goes on from "
                   f"{review.redo or review.name}.")
        answer, extra = self._ask(state, {
            "step": fix.id, "kind": "gate", "title": f"{review.name}: {len(found)} blocking finding(s){again}" + (f" · {item['id']}" if item else ""),
            "detail": f"{listed}\n\n{fix_txt}\nGo on anyway: say why; the findings are kept as dismissed and shown "
                      f"at the final review.{past}",
            "options": OPTIONS, "labels": {"approve": "Send back" if review.back else "Fix them", "reject": "Go on anyway"}},
            gate="findings" if done_rounds < (review.rounds or 2) else "rounds")
        gates = copy.deepcopy(state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
        why = (answer.get("why") or "").strip()
        if answer.get("decision") != "approve":
            gates["log"].append(f"{review.name} findings accepted: {why}")
            ctx.emit("gate.decided", step=fix.id, data={"gate": f"{review.name} findings", "decision": "reject", "why": why,
                                                        "findings": [f["text"] for f in found]})
            data = dict(state.get("data") or {})
            data["dismissed_findings"] = list(data.get("dismissed_findings") or []) + [{
                "step": review.id, "name": review.name, "item": (item or {}).get("id"), "findings": [f["text"] for f in found],
                "why": why}]
            return {**extra, "gates": gates, "findings": [], "data": data,
                    "note": f"{len(found)} finding(s) accepted: {why}"[:300]}, self.nav.after(i)
        ctx.emit("gate.decided", step=fix.id, data={"gate": f"{review.name} findings", "decision": "approve", "why": why})
        if answer.get("auto"):
            gates["log"].append(f"{review.name} findings sent to fix: {why}")
        feedback = f"Fix these blocking findings from {review.name}:\n{listed}" + (f"\n\nFrom the user: {why}" if why and not answer.get("auto") else "")
        rounds[key] = done_rounds + 1
        if review.back:
            return {**extra, "gates": gates, "review_rounds": rounds, "findings": [], "feedback": feedback,
                    "note": f"sent back to {review.back} (round {rounds[key]})"}, self.nav.jump(review.back, i)
        route = str((answer.get("payload") or {}).get("route") or "review-fix")
        if route not in ROUTES:
            route = "review-fix"
        agent = "implementer" if route == "review-fix" else "test-author"
        fix = fix.model_copy(update={"phase": route, "agent": agent})
        state = {**state, **extra, "phase": route, "feedback": feedback}
        before = await asyncio.to_thread(guard.snapshot, ctx.root)
        res, model, _tb = await self._run_agent(state, fix, agent, 0)
        refused = await asyncio.to_thread(guard.guard_diff, ctx.root, route, before, None, None, state.get("unlocks") or [],
                                          self._readonly())
        for r in refused:
            ctx.emit("guard.refused", step=fix.id, data={"tool": "diff-guard", "phase": route, **r})
        usage = dict(state.get("usage") or {})
        for k in ("tokens_in", "tokens_out", "tokens_cached", "premium_requests"):
            usage[k] = usage.get(k, 0) + getattr(res, k)
        usage["cost_usd"] = round(usage.get("cost_usd", 0.0) + res.cost_usd, 6)
        step_tokens = dict(state.get("step_tokens") or {})
        step_tokens[review.id] = step_tokens.get(review.id, 0) + res.tokens_in + res.tokens_out + res.tokens_cached // 10
        upd = {**extra, "gates": gates, "usage": usage, "step_tokens": step_tokens, "review_rounds": rounds, "findings": [],
               "feedback": None, "last_answer": (res.text or "")[:2000], "phase": route}
        a = self._action_input(state, None)
        a.title = f"address {review.name} findings"
        a.emit = self._tool_emit(fix.id)
        notes = []
        for action in ROUTES[route]:
            r = await run_action(action, a)
            notes.append(r.note)
            upd.update(r.update)
            if not r.ok:
                # Tests broke or the commit was refused: back to the question with what went wrong.
                found = found + [{"lens": "keel", "text": f"after the fix: {r.note}"}]
                return {**upd, "findings": found, "note": f"fix round {rounds[key]}: {r.note}"[:300]}, fix.id
        upd["note"] = " · ".join([f"{agent} ({route}) · {model['provider']} {model.get('model', '')}", *notes])[:300]
        return upd, self.nav.jump(review.redo or review.id, i)

    def _sections(self, step: Step, state: FlowState) -> list[str]:
        """knowledge-refresh: one librarian per section, for a parallel librarian step.

        Sections come from settings.sections, else from the thread's acs (the api starts the flow with
        one AC per stale section: {id: <section>, layer: "API", title: <section>}).
        """
        if step.kind != "parallel" or step.lanes or step.items_from or (step.agent or "") != "librarian":
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

    def _action_input(self, state: FlowState, ac: dict | None, item: dict | None = None) -> ActionInput:
        return ActionInput(root=self.ctx.root, project=self.ctx.project_id, phase=state["phase"], title=self.ctx.title, ac=ac, init=dict(state.get("init") or {}),
                           acs=copy.deepcopy(state.get("acs") or []), fake=self.ctx.simulate_checks, flow=self.wf.flow,
                           deps=list(state.get("deps") or []), gates_log=list((state.get("gates") or {}).get("log") or []),
                           base=state.get("base_head"), unlocks=list(state.get("unlocks") or []),
                           preexisting=dict(state.get("preexisting") or {}), item=item, data=dict(state.get("data") or {}),
                           keys=dict(self.ctx.keys), state=dict(state), settings=dict(self.ctx.settings or {}),
                           thread_id=self.ctx.thread_id, request=self.ctx.request)

    async def code_step(self, i: int, step: Step, state: FlowState):
        ac = _ac(state) if step.per_ac else None
        item = self._item(state, step)
        st = dict(state)
        upd: dict = {}
        notes, details = [], []
        actions = step.actions()
        for n, action in enumerate(actions):
            if action == "start_flow":
                r = await self._start_flow(step, st, item)
            elif action == "escalate_model":
                r = self._escalate_model(step, st)
            else:
                a = self._action_input(st, ac, item)
                a.step = step.id
                a.emit = self._tool_emit(step.id)
                if action == "open_pr":
                    a.state["pr_approved"] = self._gate_approved(i, st)
                    a.state["pr_auto"] = run_mode.is_auto_line(self._gate_line(i, st))
                r = await run_action(action, a)
            if r.ask and r.ask.get("type") == "already-met" and ac:
                return await self._already_met(i, step, st, ac, r, upd, actions[n + 1:])
            if r.ask:
                return await self._answer_check(i, step, st, r, upd)
            if r.stop:
                # A refusal no retry can change (an empty diff to review): the flow ends here and says why.
                return {**upd, "status": "stopped", "error": r.note, "note": r.note}, END
            if not r.ok and step.soft:
                # A soft check records what it found and goes on; a branch reads markers[<id>].RESULT, a later step
                # reads what it said in data["<id>_output"].
                st.update(r.update)
                upd.update(r.update)
                data = {**(st.get("data") or {}), f"{step.id}_output": f"{r.note}\n{r.detail}".strip()[:6000]}
                st["data"] = upd["data"] = data
                notes.append(r.note)
                break
            if not r.ok:
                for k in ("blockers", "ladder"):
                    if k in r.update:
                        upd[k] = r.update[k]
                return self._check_failed(i, step, st, ac or item, r, upd)
            st.update(r.update)
            upd.update(r.update)
            notes.append(r.note)
            if r.detail:
                details.append(r.detail)
        else:
            r = None
        if step.soft:
            result = "fail" if r is not None else "pass"
            mk = dict(state.get("markers") or {})
            mk[step.id] = {"RESULT": result}
            mk["*"] = {**(mk.get("*") or {}), "RESULT": result}
            upd["markers"] = mk
        key = f"{step.id}:{(ac or item or {}).get('id')}"
        retries = dict(state.get("retries") or {})
        retries.pop(key, None)
        upd.update(note="; ".join(notes), retries=retries, stall={"fingerprint": None, "count": 0, "step": 0}, last_failure=None,
                   output="\n".join(details)[:2000] or None)   # what the actions printed (explain.py shows its head)
        upd.setdefault("show", None)        # what the next gate shows: only an action of this step sets it
        if step.then == "end":
            # The work goes on in the flow start_flow started (or there is nothing more to do); this one ends here.
            return {**upd, "status": "done", "ac": None, "item": None}, END
        if step.then and step.then != "continue":
            return upd, self.nav.jump(step.then, i)
        return upd, self.nav.after(i)

    def _escalate_model(self, step: Step, state: FlowState) -> ActionResult:
        """keel v1 "escalate the model once": the step's agent (default investigator) runs on a stronger model from here
        on (settings.stronger_model, else Opus for Claude, else high effort). Marker ESCALATED: yes."""
        agent = step.agent or "investigator"
        cur = self._model(state, step, agent)
        strong = self.ctx.settings.get("stronger_model") or stronger(cur)
        mk = dict(state.get("markers") or {})
        mk[step.id] = {"ESCALATED": "yes"}
        mk["*"] = {**(mk.get("*") or {}), "ESCALATED": "yes"}
        upd = {"markers": mk, "agent_models": {**(state.get("agent_models") or {}), agent: strong}}
        same = strong == cur
        label = f"{strong.get('provider')} {strong.get('model', '')}{' ' + strong['effort'] if strong.get('effort') else ''}"
        return ActionResult(True, f"{agent}: no stronger model to escalate to; it tries again on {label}" if same
                            else f"{agent} escalated to {label}", update=upd)

    def _gate_approved(self, i: int, state: FlowState) -> bool:
        """Was the nearest gate before step i approved (its last line in the gate log)?"""
        gate = next((s for s in reversed(self.wf.steps[:i]) if s.kind == "gate"), None)
        last = self._gate_line(i, state)
        return bool(gate and last.startswith(f"gate {gate.id} approve"))

    def _gate_line(self, i: int, state: FlowState) -> str:
        """The last gate-log line of the nearest gate before step i ("" when it has none)."""
        gate = next((s for s in reversed(self.wf.steps[:i]) if s.kind == "gate"), None)
        if not gate:
            return ""
        mine = [line for line in (state.get("gates") or {}).get("log") or [] if line.startswith(f"gate {gate.id} ")]
        return mine[-1] if mine else ""

    def _seed_value(self, value, state: FlowState, item: dict | None):
        """A seed value: "$a.b.c" reads that path from the state (item = the loop's item), anything else is literal."""
        if isinstance(value, dict):
            return {k: self._seed_value(v, state, item) for k, v in value.items()}
        if isinstance(value, list):
            return [self._seed_value(v, state, item) for v in value]
        if not (isinstance(value, str) and value.startswith("$")):
            return value
        cur = {**state, "item": item, "request": self.ctx.request}
        for part in value[1:].split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return None
        return cur

    async def _start_flow(self, step: Step, state: FlowState, item: dict | None) -> ActionResult:
        return await self.start_flow(step.flow, step.seed or {}, step, state, item)

    async def start_flow(self, flow: str, raw_seed: dict, step: Step, state: FlowState, item: dict | None) -> ActionResult:
        """Hand-off: start another workflow's thread on this project with a seed; both threads record the link."""
        ctx = self.ctx
        if not ctx.spawn:
            return ActionResult(False, "This engine cannot start another flow from here.")
        seed = {"title": (item or {}).get("title") or ctx.title, "request": ctx.request}
        seed.update({k: self._seed_value(v, state, item) for k, v in raw_seed.items()})
        link = {"thread_id": ctx.thread_id, "workflow": self.wf.id, "step": step.id}
        key = f"start_flow|{step.id}|{(item or {}).get('id', '')}"
        try:
            # A node that runs again (try again after a later failure in the same step) must not start a second child.
            child = ctx.done_calls.get(key) or await ctx.spawn(flow, seed, link)
        except Exception as exc:
            return ActionResult(False, f"Could not start the {flow} flow: {getattr(exc, 'error', None) or exc}")
        ctx.done_calls[key] = child
        children = list(state.get("children") or []) + [{"thread_id": child, "workflow": flow, "step": step.id,
                                                        "title": str(seed.get("title") or "")[:200]}]
        ctx.emit("flow.started", step=step.id, data={"child": child, "workflow": flow, "title": seed.get("title"),
                                                     "then": step.then or "continue"})
        return ActionResult(True, f"started the {flow} flow ({child[:8]}): {seed.get('title')}", update={"children": children})

    async def _already_met(self, i: int, step: Step, state: FlowState, ac: dict, r: ActionResult, upd: dict, rest: list[str]):
        """verify_red found the AC's own test already passing (keel v1 "already-met"). No retries: ask right away.

        approve: run the rest of this step (the red commit: `test(<AC>): ...`), set the AC to already-met and
        skip its green, review and gate steps. reject: back to the red step with the user's note as feedback.
        """
        q = r.ask
        answer, extra = self._ask(state, {"step": step.id, "kind": q["kind"], "title": q["title"], "detail": q["detail"],
                                          "options": OPTIONS}, gate="already-met")
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
        kind = {"deps": "dependency", "escalate": "escalate", "secrets": "secrets", "readonly": "readonly"}.get(q["type"], "failure")
        question = {"step": step.id, "kind": q["kind"], "title": q["title"], "detail": q["detail"], "options": OPTIONS}
        if q.get("labels"):
            question["labels"] = dict(q["labels"])
        answer, extra = self._ask(state, question, gate=kind)
        upd = {**upd, **extra}
        decision = answer.get("decision", "reject")
        why = (answer.get("why") or "").strip()
        gates = copy.deepcopy(state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
        if q["type"] in ("secrets", "readonly"):
            # A secret in the staged diff (every mode stops for it), or a read-only run's commit: the user decides.
            self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": decision, "why": why})
            gates["log"].append(f"{q['type']} {decision}" + (f": {why}" if why else ""))
            if decision != "approve":
                return {**upd, "gates": gates, "status": "stopped", "error": r.note, "note": f"stopped: {r.note}"[:300]}, END
            if q["type"] == "readonly":
                # Approve = try the commit again (after the run mode was changed; still read-only, it asks again).
                return {**upd, "gates": gates, "note": "the commit runs again"}, step.id
            feedback = f"{r.note}\n{r.detail[-2000:]}".strip() + (f"\n\nFrom the user: {why}" if why else "")
            target = self.nav.jump(step.back, i) if step.back else self.nav.retry_target(i)
            return {**upd, "gates": gates, "feedback": feedback, "last_failure": r.note[:300],
                    "note": f"{r.note} · sent back to remove it"[:300]}, target or step.id
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
            gates["log"].append(f"escalation: {q['why']}" + (f" ({why})" if answer.get("auto") else ""))
            # keel v1 `keel escalate`: the inline criteria become the feature flow's, finished ones stay finished, and
            # every commit stays on the branch.
            r2 = await self.start_flow("feature", {"acs": "$acs", "escalated_from": self.wf.id, "why": q["why"]}, step, state, None) \
                if self.ctx.spawn else None
            if not r2 or not r2.ok:
                return {**upd, "gates": gates, "status": "stopped", "error": f"Escalated to a feature flow: {q['why']}.",
                        "note": "escalated: start a feature flow for this work"}, END
            return {**upd, **r2.update, "gates": gates, "status": "done", "ac": None,
                    "note": f"escalated: {r2.note}"[:300]}, END
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
        # A code step's `back` names where its failures go; else the agent step before it. `rounds` caps the attempts.
        target = self.nav.jump(step.back, i) if step.back else self.nav.retry_target(i)
        attempts = step.rounds if step.rounds is not None else int(self.ctx.settings.get("fix_attempts") or 3)
        base = {**upd, "stall": stall, "last_failure": r.note[:300], "feedback": feedback, "note": r.note}
        if target and count <= attempts:
            retries[key] = count
            return {**base, "retries": retries}, target
        if step.after_rounds:
            # keel v1 "fails twice: reset and reproduce again": a fresh start instead of another try in the same place.
            return {**base, "retries": {**retries, key: 0}, "note": f"{r.note} · on to {step.after_rounds}"[:300]}, \
                self.nav.jump(step.after_rounds, i)
        lp = self.nav.loop_of(i)
        in_items = bool(lp and not lp.per_ac and ac)
        reject = "reject to mark this item failed and go on with the next one" if in_items else "reject to stop the flow"
        answer, extra = self._ask(state, {"step": step.id, "kind": "fix", "title": f"{step.name} keeps failing",
                                          "detail": f"{r.note}\n\n{r.detail[-1500:]}\n\nApprove to try again, {reject}.",
                                          "options": OPTIONS})
        base.update(extra)
        if (answer or {}).get("decision") != "approve":
            if in_items:
                items = _set_ac(get_list(state, lp.key), ac["id"], "failed")
                return {**base, **put_list(state, lp.key, items), "retries": {**retries, key: 0}, "feedback": None,
                        "note": f"{ac['id']} failed: {r.note}"[:300]}, self.nav.end(lp)
            return {**base, "status": "failed", "error": r.note}, END
        retries[key] = 0
        why = (answer or {}).get("why")
        if why:
            base["feedback"] = f"{feedback}\n\nFrom the user: {why}"
        return {**base, "retries": retries}, target or step.id

    async def gate_step(self, i: int, step: Step, state: FlowState):
        ctx = self.ctx
        ac = _ac(state) if step.per_ac else None
        item = self._item(state, step)
        acs = state.get("acs") or []
        gates = copy.deepcopy(state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
        if ac:
            due = rules.gate_due(gates.get("mode", "every-ac"), acs, ac["id"], gates.get("skipped"), rules.ac_lane(ac))
            if not due["due"]:
                gates["log"].append(f"ac {ac['id']} approve: no gate here ({due['why']})")
                return {"gates": gates, "acs": _set_ac(acs, ac["id"], "done"), "note": f"no gate: {due['why']}"}, self.nav.after(i)
        if step.when:
            label, values = self._when(state, step.when)
            if not self._holds(step.when, values):
                # Not this time (a --semi gate in an --auto run, a question with nothing to ask): approved unasked.
                why = f"not asked, {label}: {self._shown_values(values)}"
                gates["log"].append(f"gate {step.id} approve: {why}")
                ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "why": why, "by": "engine"})
                return {"gates": gates, "note": f"{step.name}: {why}"[:300]}, self.nav.after(i)
        if self.wf.flow == "init" and step.id == "questions":
            return self._init_questions(step, state, gates), self.nav.after(i)
        if step.skip_menu:
            return self._skip_menu(i, step, state, gates)
        if isinstance(step.choices, list) and item:
            return self._choice_gate(i, step, state, gates, item)
        waived = (gates.get("skipped") or {}).get(step.phase or "") if not ac and not item else None
        if waived:
            # keel v1 --no-gates: the gate records an automatic approval (it shows in the PR body's skipped gates).
            gates["log"].append(f"gate {step.id} approve: waived ({waived})")
            self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "why": f"waived: {waived}"})
            upd = {"gates": gates, "note": f"{step.name}: waived ({waived})"}
            if isinstance(step.choices, dict):
                first = next(iter(step.choices))
                upd["data"] = {**(state.get("data") or {}), f"{step.id}_answer": {"choice": first, "why": "waived", "payload": {}}}
                return upd, self.nav.jump(step.choices[first], i)
            if step.choices:
                upd["markers"] = self._choice_markers(state, step, _default_choice(state, step), "waived")
            return upd, self.nav.after(i)
        options = OPTIONS
        title = step.name + (f" · {ac['id']}" if ac else f" · {item['id']}" if item else "")
        no_criteria = not ac and step.phase in ("spec", "triage") and not acs
        asked = (state.get("clarify") or {}).get("questions") if no_criteria else None
        if asked:
            return self._clarify_gate(i, step, state, gates, asked)
        if no_criteria:
            # Nothing real to approve: say so and only allow "send back" with what the agent should do.
            options = ["reject"]
            title = f"{step.name} — no acceptance criteria yet"
            said = (state.get("last_answer") or state.get("note") or "").strip()
            detail = ("The spec step wrote no acceptance criteria, so there is nothing to approve yet. "
                      "Send it back and say what to build.\n\nWhat the agent said:\n" + said[:1200])
        elif step.report == "verdicts":
            detail = await asyncio.to_thread(ship_mod.final_report, ctx.root, ctx.project_id or ctx.root, dict(state), ctx.title)
        elif self.wf.flow == "init" and step.id == "plan_gate":
            detail = init_gates.plan(ctx.root, state.get("init") or init_gates.defaults(ctx.root, bool(ctx.settings.get("fast"))))
        elif (state.get("data") or {}).get(f"{step.id}_detail"):
            # A step before this gate wrote what it should show (the hunt's lens list, its report summary).
            detail = str(state["data"][f"{step.id}_detail"])
        elif step.when and step.when.get("data") and not {"equals", "in"} & set(step.when) and isinstance(self._seed_value(f"$data.{step.when['data']}", state, None), str):
            # A gate that pauses on a data value shows it (cover: why coverage could not be measured).
            detail = self._seed_value(f"$data.{step.when['data']}", state, None)
        elif ac:
            detail = f"{ac['id']} [{ac.get('layer', 'API')}] {ac.get('title', '')}\nLast step: {state.get('note', '')}"
        elif item:
            about = item.get("title") or item.get("summary") or json.dumps({k: v for k, v in item.items() if k not in ("id", "status")},
                                                                          ensure_ascii=False, default=str)[:600]
            detail = f"{item['id']}: {about}\nLast step: {state.get('note', '')}\n\nApprove with payload skip: true to skip this item."
        elif i and self.wf.steps[i - 1].kind == "code" and state.get("show"):
            # What the code step before this gate prepared for it (a review report, a diagnosis, a size proposal).
            detail = state["show"]
        elif i and "pr" in self.wf.steps[i - 1].actions() and state.get("pr_body"):
            # The PR body the pr step built: approve to go on (an open_pr step after this gate may open it).
            detail = state["pr_body"]
        else:
            detail = "\n".join(f"{a['id']} [{a.get('layer', 'API')}] {a.get('title', '')}" for a in acs) or (state.get("note") or "")
            if state.get("spec"):
                detail = f"Spec: {state['spec']}\n{detail}"
                if step.phase in ("spec", "triage"):
                    detail += "\n\n" + spec_check.describe(spec_check.check(spec_check.read_spec(ctx.root, state["spec"]), acs))
        lint_notes = (state.get("data") or {}).get("lint_notes") or []
        if lint_notes and not no_criteria:
            detail = f"{detail}\n\nStatic checks on the last changed files:\n" + "\n".join(f"- {n}" for n in lint_notes)
        question = {"step": step.id, "kind": "gate", "title": title, "detail": detail, "options": options}
        routes = isinstance(step.choices, dict) and not no_criteria
        if routes:
            question.update(options=["approve"], choices=list(step.choices))
        elif step.choices and not no_criteria:
            # A list outside a loop: named exits as markers CHOICE and WHY (branches route on them); reject sends back.
            question.update(choices=list(step.choices), detail=detail + "\n\nApprove with one of: " + ", ".join(step.choices)
                            + f" (default {_default_choice(state, step)}). Send back to {step.back or 'the step before'} with a note.")
        kind, review = self._gate_kind(i, step, state, question, ac)
        answer, extra = self._ask(state, question, gate=kind, review=review)
        decision = answer.get("decision", "reject") if not no_criteria else "reject"
        why = (answer.get("why") or "").strip()
        payload = answer.get("payload") or {}
        if routes:
            return self._choice(i, step, state, gates, extra, answer, item)
        choice = None
        if step.choices and decision == "approve":
            choice = payload.get("choice") if payload.get("choice") in step.choices else _default_choice(state, step)
        subject = f"ac {ac['id']}" if ac else f"gate {step.id}"
        gates["log"].append(f"{subject} {decision}" + (f" ({item['id']})" if item else "") + (f" [{choice}]" if choice else "")
                            + (f": {why}" if why else ""))
        decided = {"gate": step.name, "decision": decision, "why": why, "ac": (ac or {}).get("id")}
        if item:
            decided["item"] = item["id"]
        if choice:
            decided["choice"] = choice
        ctx.emit("gate.decided", step=step.id, data=decided)
        upd: dict = {"gates": gates, **extra}
        if payload.get("acs"):
            acs = [{"id": a["id"], "layer": a.get("layer", "API"), "title": a.get("title", ""), "status": a.get("status", "todo")}
                   for a in payload["acs"]]
            upd["acs"] = acs
        if decision == "approve":
            if ac:
                upd["acs"] = _set_ac(acs, ac["id"], "done")
            upd["note"] = f"{step.name} approved" + (f" [{choice}]" if choice else "") + (f": {why}" if why else "")
            if choice:
                upd["markers"] = self._choice_markers(state, step, choice, why)
            if item and payload.get("skip"):
                lp = self.nav.loop_of(i)
                upd.update(self._end_item(state, lp, item, "skipped", None, why, (step.on_skip or {}).get("record")))
                upd["note"] = f"{item['id']} skipped" + (f": {why}" if why else "")
                return upd, self.nav.end(lp)
            return upd, self.nav.after(i)
        if ac:
            upd["acs"] = _set_ac(acs, ac["id"], "todo")
        upd["feedback"] = why or f"Sent back at {step.name}."
        upd["note"] = f"{step.name} sent back" + (f": {why}" if why else "")
        target = self.nav.jump(step.back, i) if step.back else self.nav.enter(max(i - 1, 0))
        return upd, target

    @staticmethod
    def _choice_markers(state: FlowState, step: Step, choice: str, why: str) -> dict:
        """A gate's named exit as markers (CHOICE, WHY) that branches read: `when: {marker: CHOICE, step: <gate>}`."""
        mk = dict(state.get("markers") or {})
        mk[step.id] = {"CHOICE": choice, "WHY": why}
        mk["*"] = {**(mk.get("*") or {}), "CHOICE": choice, "WHY": why}
        return mk

    def _when(self, state: FlowState, when: dict) -> tuple[str, list]:
        """A `when`'s subject and the values it looks at: a path in state.data (`data: hunt.mode`), or a marker an earlier
        agent ended with (when.step names that step or a list of steps, else the latest value from any step; `any: true`
        adds each fan-out item's marker). Empty values count as not given."""
        if when.get("data") or when.get("state"):
            # `data: hunt.mode` reads state.data; `state: acs` reads any path of the flow state.
            path = str(when["data"]).removeprefix("data.") if when.get("data") else str(when["state"])
            value = self._seed_value(f"$data.{path}" if when.get("data") else f"${path}", state, None)
            return path, [None if value in (None, "", [], {}) else value]
        name = str(when["marker"]).upper()
        return name, self._marker_values(state, when, name)

    @staticmethod
    def _holds(when: dict, values: list) -> bool:
        return any(markers.matches(v, when) for v in values)

    @staticmethod
    def _shown_values(values: list) -> str:
        shown = [f"{len(v)} item(s)" if isinstance(v, (list, dict)) else str(v) for v in values if v not in (None, "")]
        return ", ".join(shown) or "not given"

    def _choice(self, i: int, step: Step, state: FlowState, gates: dict, extra: dict, answer: dict, item: dict | None):
        """A gate with named exits: payload.choice picks one (default the first); its answer is kept in
        state.data["<gate id>_answer"] for the step it leads to ({choice, why, payload})."""
        payload = answer.get("payload") or {}
        why = (answer.get("why") or "").strip()
        names = list(step.choices or {})
        # A plain "send back" picks the exit named reject when the gate has one; otherwise the first exit.
        plain = "reject" if answer.get("decision") == "reject" and "reject" in names else names[0]
        choice = str(payload.get("choice") or plain)
        if choice not in step.choices:
            gates["log"].append(f"gate {step.id} unknown choice {choice}")
            return {"gates": gates, **extra, "note": f"{step.name}: '{choice}' is not one of {', '.join(names)}"}, step.id
        gates["log"].append(f"gate {step.id} {choice}" + (f" ({item['id']})" if item else "") + (f": {why}" if why else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "choice": choice, "why": why})
        data = {**(state.get("data") or {}), f"{step.id}_answer": {"choice": choice, "why": why, "payload": payload}}
        upd = {"gates": gates, **extra, "data": data, "feedback": (None if answer.get("auto") else why) or None,
               "note": f"{step.name}: {choice}" + (f": {why}" if why else "")}
        return upd, self.nav.jump(step.choices[choice], i)

    def _skip_menu(self, i: int, step: Step, state: FlowState, gates: dict):
        """keel v1 ship's opening question: which skippable steps after this gate run this time. Every skip needs a
        reason; skips land in data.ship_skipped (the PR body and the final review show them) and the gate log."""
        units = ship_mod.skip_units(self.wf.steps, i)
        detail = ship_mod.skip_menu_detail(self.wf.steps, i)
        title, extra_all = step.name, {}
        while True:
            answer, extra = self._ask(state, {"step": step.id, "kind": "gate", "title": title, "detail": detail,
                                              "options": ["approve"], "labels": {"approve": "Run these steps"}}, gate="skip-menu")
            extra_all.update(extra)
            state = {**state, **extra}
            skips, lenses, problem = ship_mod.parse_skips(answer.get("payload") or {}, units)
            if not problem:
                break
            title = f"{step.name}: {problem}"
        why = (answer.get("why") or "").strip()
        data = dict(state.get("data") or {})
        names = {s["step"] for s in skips}
        data["ship_skipped"] = [x for x in data.get("ship_skipped") or [] if x.get("step") not in names] + skips
        if lenses:
            data["lenses_chosen"] = lenses
        gates["log"].append(f"gate {step.id} approve" + (f": {why}" if why else ""))
        for sk in skips:
            gates["log"].append(f"gate {step.id} skip {sk['step']} ({sk['band']}): {sk['reason']}")
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "why": why,
                                                          "skipped": skips, **({"lenses": lenses} if lenses else {})})
        note = ("skipped: " + ", ".join(f"{s['step']} ({s['reason']})" for s in skips)) if skips else "every step runs"
        return {**extra_all, "gates": gates, "data": data, "note": note[:300]}, self.nav.after(i)

    def _choice_gate(self, i: int, step: Step, state: FlowState, gates: dict, item: dict):
        """One choice per item of a for_each loop (cover: test, delete or accept). The choice is stored on the item
        (decision, reason) and as markers[<id>].CHOICE; on_skip's choice ends the item as skipped, needs a reason and is
        appended to data[on_skip.record]."""
        lp = self.nav.loop_of(i)
        on_skip = step.on_skip or {}
        skip_choice = on_skip.get("choice")
        about = item.get("title") or json.dumps({k: v for k, v in item.items() if k not in ("id", "status")}, ensure_ascii=False,
                                                default=str)[:600]
        detail = (f"{item['id']}: {about}\nLast step: {state.get('note', '')}\n\nChoose one: {' | '.join(step.choices)} "
                  f"(approve with payload {{\"choice\": \"<one>\"}}; plain approve = {step.choices[0]})."
                  + (f" {skip_choice} needs a reason (why): it is recorded and shown at the final review and in the PR body."
                     if skip_choice else ""))
        title, extra_all = f"{step.name} · {item['id']}", {}
        while True:
            answer, extra = self._ask(state, {"step": step.id, "kind": "gate", "title": title, "detail": detail,
                                              "options": ["approve"], "choices": list(step.choices)}, gate="choice")
            extra_all.update(extra)
            state = {**state, **extra}
            payload = answer.get("payload") or {}
            choice = str(payload.get("choice") or (skip_choice if payload.get("skip") and skip_choice else step.choices[0]))
            why = (answer.get("why") or str(payload.get("reason") or "")).strip()
            problem = (f"{choice} is not one of {', '.join(step.choices)}" if choice not in step.choices else
                       f"{choice} needs a reason" if choice == skip_choice and not why else None)
            if not problem:
                break
            title = f"{step.name} · {item['id']}: {problem}"
        gates["log"].append(f"gate {step.id} {choice} ({item['id']})" + (f": {why}" if why else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "choice": choice,
                                                          "why": why, "item": item["id"]})
        mk = dict(state.get("markers") or {})
        mk[step.id] = {"CHOICE": choice}
        mk["*"] = {**(mk.get("*") or {}), "CHOICE": choice}
        upd = {**extra_all, "gates": gates, "markers": mk}
        if choice == skip_choice:
            upd.update(self._end_item(state, lp, item, "skipped", choice, why, on_skip.get("record")))
            upd["note"] = f"{item['id']}: {choice}: {why}"[:300]
            return upd, self.nav.end(lp)
        items = [dict(x, decision=choice, reason=why or None) if x["id"] == item["id"] else x for x in get_list(state, lp.key)]
        upd.update(put_list(state, lp.key, items))
        upd["note"] = f"{item['id']}: {choice}" + (f": {why}" if why else "")
        return upd, self.nav.after(i)

    @staticmethod
    def _end_item(state: FlowState, lp: Loop, item: dict, status: str, decision: str | None, why: str,
                  record: str | None) -> dict:
        """The state update that ends a loop item (skipped) and, with `record`, keeps it in data[record] with its reason."""
        items = [dict(x, status=status, **({"decision": decision} if decision else {}), reason=why or None)
                 if x["id"] == item["id"] else x for x in get_list(state, lp.key)]
        upd = put_list(state, lp.key, items)
        if record:
            data = dict(upd.get("data") or state.get("data") or {})
            rec = {k: item[k] for k in ("id", "key", "file", "lines", "title", "app", "critical") if k in item}
            rec.update(decision=decision or status, reason=why or None)
            ident = rec.get("key") or rec["id"]
            data[record] = [x for x in data.get(record) or [] if (x.get("key") or x.get("id")) != ident] + [rec]
            upd = {**upd, "data": data}
        return upd

    def _clarify_gate(self, i: int, step: Step, state: FlowState, gates: dict, asked: list[dict]):
        """The explorer's questions as a pause with buttons. Answers (clicked or typed) go back to the explorer."""
        n = len(asked)
        answer, extra = self._ask(state, {
            "step": step.id, "kind": "clarify", "title": f"The explorer has {n} question{'s' if n != 1 else ''} before the spec",
            "detail": clarify.describe(asked), "questions": asked, "options": ["approve"],
            "labels": {"approve": "Send my answers"}})
        payload = answer.get("payload") or {}
        round_no = int(state.get("clarify_rounds") or 0) + 1
        auto = answer.get("why") if answer.get("auto") else ""
        text = clarify.answers_text(asked, payload.get("answers"), "" if auto else (answer.get("why") or ""), round_no)
        gates["log"].append(f"gate {step.id} answered the explorer's {n} question(s)" + (f": {auto}, the recommended options" if auto else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "why": text[:600],
                                                          "clarify": True})
        upd = {"gates": gates, **extra, "feedback": text, "clarify": {}, "clarify_rounds": round_no,
               "note": f"answered {n} question(s)"}
        target = self.nav.jump(step.back, i) if step.back else self.nav.enter(max(i - 1, 0))
        return upd, target

    def _init_questions(self, step: Step, state: FlowState, gates: dict) -> dict:
        """keel init's three questions: approve = the defaults, "Use my answers" = the user's words. Both go on."""
        answer, extra = self._ask(state, {
            "step": step.id, "kind": "gate", "title": "three questions", "detail": init_gates.questions(self.ctx.root, fast=bool(self.ctx.settings.get("fast"))),
            "options": OPTIONS, "labels": {"approve": "Use the defaults", "reject": "Use my answers"}}, gate="gate")
        why = (answer.get("why") or "").strip()
        mine = answer.get("decision") == "reject" and why
        init = init_gates.answers(self.ctx.root, why if mine else None, fast=bool(self.ctx.settings.get("fast")))
        gates["log"].append(f"gate questions: {'answered: ' + why if mine else 'defaults'}" + (f" ({why})" if answer.get("auto") else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "why": why if mine else "defaults"})
        return {"gates": gates, **extra, "init": init,
                "note": "answers: " + ", ".join(f"{k} {v}" for k, v in init.items() if k != "said")[:300]}

    async def branch_step(self, i: int, step: Step, state: FlowState):
        yes = True
        note = "yes"
        if step.when:
            name, values = self._when(state, step.when)
            yes = self._holds(step.when, values)
            note = f"{name}: {self._shown_values(values)}"
        elif step.action and step.action.startswith("run:"):
            r = await run_action(step.action, self._action_input(state, _ac(state)))
            yes, note = r.ok, r.note
        elif step.agent:
            res, _m, _tb = await self._run_agent(state, step, step.agent, 0)
            yes = not res.text.strip().lower().startswith("no")
            note = res.text.strip()[:160]
        if step.rounds:
            return self._branch_rounds(i, step, state, yes, note)
        target = self.nav.after(i) if yes else self.nav.jump(step.no, i)
        return {"note": f"{step.name}: {'yes' if yes else 'no'} ({note})"}, target

    @staticmethod
    def _marker_values(state: FlowState, when: dict, name: str) -> list:
        """The values a `when` looks at: the marker of when.step (one id or a list; default the latest of any step), and
        with `any: true` each item's marker of those fan-out steps too."""
        src = when.get("step")
        srcs = src if isinstance(src, list) else [src or "*"]
        mk, data = state.get("markers") or {}, state.get("data") or {}
        values = [(mk.get(s) or {}).get(name) for s in srcs]
        if when.get("any"):
            values += [(r.get("markers") or {}).get(name) for s in srcs for r in data.get(f"{s}_results") or []]
        return values


    def _branch_rounds(self, i: int, step: Step, state: FlowState, yes: bool, note: str):
        """A branch with `rounds` closes a loop: "no" sends the flow back at most rounds - 1 times. After that keel asks:
        approve = go on anyway (said why, kept in the gate log), reject = stop the flow. "yes" starts the count again."""
        rounds = dict(state.get("rounds") or {})
        done = int(rounds.get(step.id, 0))
        if yes:
            rounds.pop(step.id, None)
            return {"rounds": rounds, "note": f"{step.name}: yes ({note})"}, self.nav.after(i)
        if done + 1 < step.rounds:
            rounds[step.id] = done + 1
            return {"rounds": rounds, "note": f"{step.name}: no ({note}); round {done + 2} of {step.rounds}"}, self.nav.jump(step.no, i)
        answer, extra = self._ask(state, {
            "step": step.id, "kind": "gate", "title": f"{step.name}: still no after {step.rounds} round(s)",
            "detail": f"{note}\n\nThe loop ran {step.rounds} time(s), its limit. Approve to go on anyway (say why; it is kept in "
                      f"the gate log and shown at the final review). Reject to stop the flow here.",
            "options": OPTIONS, "labels": {"approve": "Go on anyway", "reject": "Stop"}}, gate="rounds")
        why = (answer.get("why") or "").strip()
        gates = copy.deepcopy(state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
        decision = answer.get("decision", "reject")
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": decision, "why": why})
        rounds.pop(step.id, None)
        if decision != "approve":
            gates["log"].append(f"gate {step.id} stop after {step.rounds} round(s)" + (f": {why}" if why else ""))
            return {**extra, "gates": gates, "rounds": rounds, "status": "stopped",
                    "note": f"stopped: {step.name} still no after {step.rounds} round(s)"}, END
        gates["log"].append(f"gate {step.id} go on after {step.rounds} round(s): {why or 'no reason given'}")
        return {**extra, "gates": gates, "rounds": rounds, "note": f"{step.name}: went on after {step.rounds} round(s)"}, self.nav.after(i)


def _default_choice(state, step) -> str:
    """What a plain approve picks: the recommended marker's value when it names a choice, else the first choice."""
    if step.recommend:
        said = str(((state.get("markers") or {}).get("*") or {}).get(step.recommend) or "").strip().lower().split(" ")[0]
        if said in step.choices:
            return said
    return step.choices[0]


def stronger(model: dict) -> dict:
    """The next model up for an escalation: Opus for Claude, high effort elsewhere (the same model when there is none)."""
    if model.get("provider") == "claude" and "opus" not in str(model.get("model") or ""):
        return {**model, "model": "opus"}
    if model.get("provider") in ("codex", "copilot") and model.get("effort") != "high":
        return {**model, "effort": "high"}
    return dict(model)


def compile_workflow(ctx: ThreadContext, checkpointer):
    return Compiler(ctx).build(checkpointer)
