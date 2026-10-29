"""Compile a Workflow into a LangGraph StateGraph.

    START ─▶ __setup ─▶ step ─▶ step ─▶ __ac_begin ─▶ [per_ac steps ...] ─▶ __ac_end ─┐ ─▶ step ... ─▶ __finish ─▶ END
                                 ▲                                         │
                                 └──────────── next AC ────────────────────┘

__setup (keel 0.18.22, runtime/stack_setup.py) sets up the stack first: it waits for the system packages the stacks
need and runs their each_worktree commands in a new worktree; it is silent when there is nothing to do, and a failure
pauses at __setup_gate (retry / skip / stop).

Every node returns Command(goto=...): the default is the next step, a gate's reject follows `back`,
a branch's "no" follows `no`, a failed check goes back to the agent before it. Gates, budget pauses
and "keeps failing" pauses are interrupt()s; only POST /threads/{id}/resume continues them.

interrupt() re-runs its node from the top on resume, so nothing with a side effect may sit before
an interrupt() unless re-running it is the intended retry.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import datetime
import hashlib
import json
import logging
import re
import tempfile
import time
import uuid
from pathlib import Path

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .. import addons, agent_names, extensions, models, rules
from . import doc_history, md, review_config, test_scope, thread_models
from ..models import catalog
from ..models import usage as provider_usage
from ..models.base import AgentRequest, AgentResult, ModelError
from ..models.stream import DeltaThrottle
from ..rules import checks as rule_checks
from .findings import REVIEWERS, blocking, nonblocking, unique
from .findings import key as finding_key
from . import finding_decisions, security_actions
from ..tools import git, guard, mcp, testcmd
from ..tools.agent_tools import ToolBox, command_env
from ..workflows.model import Loop, Step, Workflow
from . import container
from . import agent_knowledge, clarify, guard_ctx, init_gates, markers, prompts, project_shape, run_mode, spec_check, stack_setup, stacks
from . import blockers as push_gates
from . import choices as choices_mod
from . import flow_actions
from . import side_usage
from .gate_state import deep_gates
from .compiler_agent import AgentMixin
from .compiler_code import CodeMixin
from .compiler_findings import FindingsMixin
from .compiler_gates import GateMixin
from . import tools as tool_runner
from . import memory as memory_mod
from . import ship as ship_mod
from . import suite_failure
from .actions import ActionInput, ActionResult, revert_manifests, run_action
from .state import FlowState, ThreadContext, merge_unlocks, normalize_unlocks

log = logging.getLogger(__name__)

AC_BEGIN, AC_END, FINISH = "__ac_begin", "__ac_end", "__finish"
SETUP, SETUP_GATE = "__setup", "__setup_gate"      # keel 0.18.22 the stack's setup before the first step
SETUP_NAME = "set up the stack"
PREFLIGHT, PREFLIGHT_GATE = "__preflight", "__preflight_gate"   # 0.18.57 the toolchain answers (runtime/container.py)
PREFLIGHT_NAME = "check the toolchain"
PREFLIGHT_TIMEOUT = 120
END_TARGET = "end"                   # a branch's `no` or a gate choice that finishes the flow
KNOWN_SECTIONS = {"architecture", "domain", "conventions", "data", "integrations"}
OPTIONS = ["approve", "reject"]
# How often one gate (step and criterion or item) may be sent back in a row before keel asks: go on anyway or stop
# (settings.sendback_limit). A loop of send-backs that changes nothing never runs by itself.
SENDBACK_LIMIT = flow_actions.SENDBACK_LIMIT
# What the next agent reads when a send-back came without a reason (an old client, a driver script).
NO_REASON = ("The person sent this back but gave no reason. Do not do the same work again: if you cannot tell what to "
             "change, say so in one line and ask them once what they want.")
# A spec path an agent's answer names: `.keel/specs/x.md`, docs/specs/x.md
SPEC_PATH = re.compile(r"(?<![\w./-])((?:\.{0,2}/)?[\w@.-]+(?:/[\w@.-]+)*\.md)\b")
# The knowledge check's actions: a failure goes to the librarian, never fails the flow (Compiler._knowledge_failed).
KNOWLEDGE_CHECKS = ("knowledge_check", "memory_check")
RESUMABLE = {"claude", "codex"}      # CLIs whose sessions keel can continue (claude --resume, codex exec resume)
ALREADY_MET = "already-met"
DONE = ("done", ALREADY_MET)        # AC statuses the per-AC loop is finished with
ITEM_DONE = ("done", "skipped", "failed")   # item statuses a for_each loop is finished with (todo is the rest)
# Where a review fix goes (findings_step, payload.route) and the actions that follow the fixing agent.
ROUTES = {"review-fix": ("verify_green", "commit"), "coverage-fix": ("verify_green", "commit"), "red": ("commit",)}
STEP_FIELD_MAX = 24_000          # per string field of an agent.step (runners already cap at 20 KB plus a note)
PROMPT_RECORD_MAX = 60_000       # 0.18.64 per prompt (system, task) kept with an agent call (agent.prompt)
RESULT_MAX = 20_000              # 0.18.64 an agent call's answer as the job keeps it (agent.finished result)
USAGE_EVERY = 15.0               # 0.18.17 seconds between two agent.usage events of one running call (the usage so far)



_COUNT = {3: "three", 4: "four"}   # init's questions: "three questions", "four questions" (with the layout)
KEEP_MAX = 60_000       # `keep: true`: the most of an agent's answer the flow keeps for its next steps


def add_usage(usage: dict, used: dict | None) -> dict:
    """usage += used (tokens_in, tokens_out, tokens_cached, cost_usd, premium_requests); returns usage."""
    for k in ("tokens_in", "tokens_out", "tokens_cached", "premium_requests"):
        if (used or {}).get(k):
            usage[k] = int(usage.get(k, 0)) + int(used[k])
    if (used or {}).get("cost_usd"):
        usage["cost_usd"] = round(float(usage.get("cost_usd", 0.0)) + float(used["cost_usd"]), 6)
    return usage


CONTINUE = ("You used all your turns before you finished. Continue the same task from where you stopped: everything you "
            "changed is still on disk. Do not start over. End with the answer the task asks for (and its result lines).")


async def run_or_continue(runner, req: AgentRequest, emit, can_resume: bool, memory) -> AgentResult:
    """v0.17.5 an agent that runs out of turns continues its own session once with "continue" (claude --resume, codex
    exec resume) instead of starting fresh; the tokens of the cut-off run are added to the result. `memory` returns the
    step's memory row (its session) or None."""
    try:
        return await runner.run(req, emit)
    except ModelError as exc:
        if not (exc.max_turns and can_resume):
            raise
        row = memory()
        row = (await row) if row is not None and hasattr(row, "__await__") else row
        sid = (row or {}).get("session") or req.session
        if not sid:
            raise
        spent = dict(exc.usage or {})
    emit("text", "The agent used all its turns: keel continues its own session (\"continue\").")

    def emit_on(kind: str, text: str = "", **extra):
        # 0.18.17 the continued run counts its usage from 0: the call's usage so far includes the cut-off run's
        emit(kind, text, **(add_usage(dict(extra), spent) if kind == "usage" else extra))
    try:
        res = await runner.run(dataclasses.replace(req, prompt=CONTINUE, session=sid, resume=True), emit_on)
    except ModelError as exc:
        exc.usage = add_usage(dict(exc.usage or {}), spent)
        raise
    res.tokens_in += int(spent.get("tokens_in", 0))
    res.tokens_out += int(spent.get("tokens_out", 0))
    res.tokens_cached += int(spent.get("tokens_cached", 0))
    res.cost_usd += float(spent.get("cost_usd", 0.0))
    return res


def _usage_fields(u: dict) -> dict:
    """An agent event's usage fields (agent.usage, agent.finished) from a usage dict."""
    return {"tokens_in": int(u.get("tokens_in", 0)), "tokens_out": int(u.get("tokens_out", 0)),
            "tokens_cached": int(u.get("tokens_cached", 0)), "cost_usd": round(float(u.get("cost_usd", 0.0)), 6)}


def running_usage(usage: dict, running: dict) -> dict:
    """0.18.17 a flow's usage with the usage so far of the agents that still run (ThreadContext.running)."""
    out = dict(usage)
    for u in list(running.values()):
        add_usage(out, u)
    return out


def raised_cap(used: int, cap: int, first: int) -> int:
    """0.18.17 the token cap after "Token cap reached" is approved: what the flow used plus the largest of the cap now,
    the flow's first cap and half of what it used. One approval bought only one cap more (used + cap): with the 500,000
    default a big flow asked a person every 500k (500k, 1.03M, 1.53M, ...); half the use so far makes the gates come
    further apart as the flow grows, and an approval never buys less than before."""
    return int(used + max(cap, first, used // 2, 1))


def budget_tokens(usage: dict) -> int:
    """Tokens that count toward caps: new input + output + a tenth of cache reads (they cost about a tenth)."""
    return int(usage.get("tokens_in", 0)) + int(usage.get("tokens_out", 0)) + int(usage.get("tokens_cached", 0)) // 10


def answer_problem(question: dict, decision: str, why: str | None, payload: dict | None) -> tuple[str, str] | None:
    """(error, hint) when an answer does not fit the question that waits, else None. A gate takes only the decisions it
    offers (a spec gate without criteria offers only "reject": an "approve" there acted as a send-back without a reason,
    hundreds of times), and only one of its choices. A send-back from a gate needs a reason: it goes into the next
    agent's prompt. A gate with named exits takes a plain reject as its exit "reject" when it has one (Compiler._choice)."""
    title = question.get("title") or "This question"
    options = list(question.get("options") or OPTIONS)
    choices = [str(c) for c in question.get("choices") or []]
    if decision not in options and not (decision == "reject" and "reject" in choices):
        return (f'"{title}" does not take {decision}.', "Answer with one of: " + ", ".join(options) + ".")
    choice = (payload or {}).get("choice")
    if choice is not None and choices and str(choice) not in choices:
        return (f'"{choice}" is not a choice of "{title}".', "Choose one of: " + ", ".join(choices) + ".")
    back = decision == "reject" or choice == "reject"         # "reject" as a named exit sends back too (feature's spec gate)
    said = (why or "").strip() or str((payload or {}).get("reason") or (payload or {}).get("note") or "").strip()
    if back and question.get("kind", "gate") == "gate" and not said:
        return ("Say why when you send it back.", "The reason goes into the next agent's prompt.")
    # 0.19.0 a choice whose choice_info says a reason is required (skip an item, accept uncovered lines, close a finding)
    took = choices_mod.picked(question, decision, payload)
    if took and took.get("why") == choices_mod.REQUIRED and not said:
        return (f'Say why when you choose "{took.get("label") or took.get("name")}".', "The reason is recorded.")
    return None


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


def close_note(state: FlowState) -> str:
    """The flow's last note: "done", plus the warnings it went on with (a knowledge check that kept failing)."""
    warned = [w.get("text") or "" for w in (state.get("data") or {}).get("warnings") or []]
    return "done" + (f" with {len(warned)} warning(s): " + "; ".join(warned) if warned else "")


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


class Compiler(AgentMixin, FindingsMixin, CodeMixin, GateMixin):
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
        g.add_node(SETUP, self.setup_step)
        g.add_node(SETUP_GATE, self.setup_gate)
        g.add_node(PREFLIGHT, self.preflight_step)
        g.add_node(PREFLIGHT_GATE, self.preflight_gate)
        g.add_edge(START, SETUP)
        return g.compile(checkpointer=checkpointer)

    # ------------------------------------------------------------ the stack's setup (keel 0.18.22)

    async def setup_step(self, state: FlowState):
        """Before the first step: the system packages the project's stacks need (keel waits for its background install)
        and each stack's setup.each_worktree in a new worktree or a folder without its installed dependencies. Shown as
        a step with its own job; nothing at all when there is nothing to do. A failure goes to the setup gate."""
        if state.get("status") in ("stopped", "failed"):
            return Command(goto=END)
        ctx = self.ctx
        data = dict(state.get("data") or {})
        try:
            system, jobs = await asyncio.to_thread(stack_setup.flow_needs, ctx.root)
        except Exception:                                   # a broken check never stops a flow
            log.exception("stack setup check for %s", ctx.root)
            return Command(goto=PREFLIGHT)
        if not system and not jobs:
            return Command(goto=PREFLIGHT)
        if self.wf.steps and all(rules.read_only(s.phase) for s in self.wf.steps):
            # a read-only flow (review, review-pr) builds and changes nothing: it never installs packages into the
            # person's folder (keel 0.18.56 ran composer install in the folder of a PR review and stopped at its gate)
            data["setup"] = {"ok": True, "note": "skipped: a read-only flow needs no stack setup"}
            return Command(update={"data": data}, goto=PREFLIGHT)   # the preflight skips it too
        prev = state.get("phase") or "none"
        step = Step(id=SETUP, kind="code", name=SETUP_NAME, action="stack_setup")
        ctx.emit("step.started", step=SETUP, data={"name": SETUP_NAME, "kind": "code", "phase": prev, "ac": None,
                                                   "from": prev, "flow": self.wf.flow, "phase_changed": False,
                                                   "transition_ok": True})
        job = _CodeJob(ctx, step, state, name=SETUP_NAME, actions=["stack_setup"])
        job.start()
        try:
            r = await asyncio.to_thread(stack_setup.prepare, ctx.root, job.progress)
        except Exception as exc:                            # an unexpected error is a failure the person sees
            log.exception("stack setup for %s", ctx.root)
            r = {"ok": False, "note": f"the stack setup failed: {exc}", "detail": "", "jobs": []}
        job.ok = bool(r["ok"])
        job.progress(r["note"], tool="stack_setup", ok=job.ok)
        job.finish("done" if job.ok else "failed")
        ctx.emit("step.finished", step=SETUP, data={"name": SETUP_NAME, "kind": "code", "phase": prev, "ac": None,
                                                    "ok": job.ok, "note": r["note"]})
        data["setup"] = {"ok": job.ok, "note": r["note"], "detail": str(r.get("detail") or "")[-4000:],
                         "jobs": [{k: j[k] for k in ("stack", "dir", "commands", "why", "key")} for j in r.get("jobs") or []]}
        return Command(update={"data": data, "note": r["note"]}, goto=PREFLIGHT if job.ok else SETUP_GATE)

    async def setup_gate(self, state: FlowState):
        """The stack's setup failed: retry (after the person fixed it), skip (go on; recorded so it is not asked again
        for the same commands) or stop. Only a person answers it (run mode: a failure)."""
        ctx = self.ctx
        data = dict(state.get("data") or {})
        last = data.get("setup") or {}
        names = ["retry", "skip", "stop"]
        detail = (f"{last.get('note') or 'The stack setup failed.'}\n\n{last.get('detail') or ''}".rstrip()
                  + "\n\nretry: you fixed it (or it was a passing problem); keel runs the setup again.\n"
                    "skip: go on without it; tests that need it will fail until it is done.\nstop: end the flow here.")
        question = {"step": SETUP, "kind": "gate", "title": f"Setting up the stack failed: {last.get('note') or ''}"[:300],
                    "detail": detail, "options": OPTIONS, "choices": names,
                    "choice_info": choices_mod.choice_info(names, context="setup")}
        answer, extra = self._ask(state, question, gate="failure")
        payload = answer.get("payload") or {}
        choice = payload.get("choice") or ("retry" if answer.get("decision") == "approve" else "stop")
        ctx.emit("gate.decided", step=SETUP, data={"gate": "setup", "decision": answer.get("decision"), "choice": choice,
                                                   "why": answer.get("why")})
        if choice == "retry":
            return Command(update=extra, goto=SETUP)
        if choice == "skip":
            jobs = last.get("jobs") or []
            await asyncio.to_thread(stack_setup.skip_jobs, ctx.root, jobs)
            note = "stack setup skipped" + (f": {answer['why']}" if answer.get("why") else "")
            data["setup"] = {**last, "skipped": note}
            return Command(update={**extra, "data": data, "note": note}, goto=PREFLIGHT)
        note = f"stopped at the stack setup: {last.get('note') or ''}".rstrip(": ")
        return Command(update={**extra, "status": "stopped", "note": note, "error": note}, goto=END)

    async def preflight_step(self, state: FlowState):
        """0.18.57 before the first step: the project's toolchain answers. commands.preflight, else the test runner's
        --version through the container the test commands exec into (runtime/container.py). Nothing to check, a
        simulated run or init: straight on. A failure goes to the preflight gate (only a person answers it)."""
        first = self.nav.enter(0)
        if state.get("status") in ("stopped", "failed"):
            return Command(goto=END)
        ctx = self.ctx
        data = dict(state.get("data") or {})
        if ctx.simulate_checks or self.wf.flow == "init" or (data.get("preflight") or {}).get("ok"):
            return Command(goto=first)
        try:
            checks = await asyncio.to_thread(container.preflight_commands, ctx.root)
        except Exception:                                   # a broken check never stops a flow
            log.exception("preflight for %s", ctx.root)
            return Command(goto=first)
        if not checks or (self.wf.steps and all(rules.read_only(s.phase) for s in self.wf.steps)):
            return Command(goto=first)                      # a read-only flow (review-pr) runs no tests: nothing to check
        prev = state.get("phase") or "none"
        step = Step(id=PREFLIGHT, kind="code", name=PREFLIGHT_NAME, action="preflight")
        ctx.emit("step.started", step=PREFLIGHT, data={"name": PREFLIGHT_NAME, "kind": "code", "phase": prev, "ac": None,
                                                       "from": prev, "flow": self.wf.flow, "phase_changed": False,
                                                       "transition_ok": True})
        job = _CodeJob(ctx, step, state, name=PREFLIGHT_NAME, actions=["preflight"])
        job.start()
        failed = None
        for what, cmd, rel in checks:
            folder = str(Path(ctx.root) / rel) if rel else ctx.root
            job.progress(f"$ {cmd}", tool="preflight", command=cmd)
            t0 = time.monotonic()
            code, out = await asyncio.to_thread(testcmd.run, folder, cmd, PREFLIGHT_TIMEOUT, command_env())
            job.progress(f"{what}: {'answers' if code == 0 else f'failed (exit {code})'}", tool="preflight", command=cmd,
                         ok=code == 0, ms=int((time.monotonic() - t0) * 1000), output=(out or "").strip()[-2000:] or None)
            if code != 0:
                failed = {"what": what, "command": cmd, "exit": code, "output": (out or "").strip()[-1500:]}
                break
        job.ok = failed is None
        note = (f"the toolchain answers ({len(checks)} check{'s' if len(checks) != 1 else ''})" if job.ok else
                f"{failed['what']} does not answer: `{failed['command']}` failed (exit {failed['exit']})")
        job.progress(note, tool="preflight", ok=job.ok)
        job.finish("done" if job.ok else "failed")
        ctx.emit("step.finished", step=PREFLIGHT, data={"name": PREFLIGHT_NAME, "kind": "code", "phase": prev, "ac": None,
                                                        "ok": job.ok, "note": note})
        data["preflight"] = {"ok": job.ok, "note": note, **({"failed": failed} if failed else {})}
        return Command(update={"data": data, "note": note}, goto=first if job.ok else PREFLIGHT_GATE)

    async def preflight_gate(self, state: FlowState):
        """The preflight failed: retry (the person started the container), folder (a worktree flow: start it again in
        the project folder), skip (go on; recorded) or stop."""
        ctx = self.ctx
        data = dict(state.get("data") or {})
        last = data.get("preflight") or {}
        failed = last.get("failed") or {}
        in_worktree = await asyncio.to_thread(stack_setup.is_worktree, ctx.root)
        names = ["retry", *(["folder"] if in_worktree else []), "skip", "stop"]
        lines = [f"What failed: {failed.get('what') or 'the preflight'}.",
                 f"The command: {failed.get('command') or '-'} (exit {failed.get('exit')})",
                 "", (failed.get("output") or "").strip(), "",
                 "retry: start the container (docker compose up -d, in the project folder), then keel checks again."]
        if in_worktree:
            lines.append("folder: this flow runs in a worktree; the container sees only the project folder. Stop here "
                         "and start the flow again with Where it runs: the project folder.")
        lines += ["skip: go on without the check; the tests fail until the toolchain answers.", "stop: end the flow here."]
        question = {"step": PREFLIGHT, "kind": "gate", "title": f"The toolchain does not answer: {last.get('note') or ''}"[:300],
                    "detail": "\n".join(lines), "options": OPTIONS, "choices": names,
                    "choice_info": choices_mod.choice_info(names, context="preflight")}
        answer, extra = self._ask(state, question, gate="failure")
        payload = answer.get("payload") or {}
        choice = payload.get("choice") or ("retry" if answer.get("decision") == "approve" else "stop")
        ctx.emit("gate.decided", step=PREFLIGHT, data={"gate": "preflight", "decision": answer.get("decision"),
                                                       "choice": choice, "why": answer.get("why")})
        if choice == "retry":
            data.pop("preflight", None)
            return Command(update={**extra, "data": data}, goto=PREFLIGHT)
        if choice == "skip":
            note = "preflight skipped" + (f": {answer['why']}" if answer.get("why") else "")
            data["preflight"] = {**last, "ok": True, "skipped": note}
            return Command(update={**extra, "data": data, "note": note}, goto=self.nav.enter(0))
        if choice == "folder":
            note = ("stopped: start this flow again in the project folder (Start a flow › Where it runs: the project "
                    "folder); the container sees only the project folder")
            return Command(update={**extra, "status": "stopped", "note": note, "error": note}, goto=END)
        note = f"stopped at the preflight: {last.get('note') or ''}".rstrip(": ")
        return Command(update={**extra, "status": "stopped", "note": note, "error": note}, goto=END)

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
            # 0.18.64 the branch was synced onto its base (keel_branch_sync): the flow's own work starts at the merge base
            synced = ctx.synced_base if ctx.synced_base and state.get("base_head") != ctx.synced_base else None
            if synced:
                state = {**state, "base_head": synced}
            # 0.18.74 who changed the run mode, and when (service.set_mode): into the gate log, once
            said = [x for x in ((ctx.settings or {}).get("mode_log") or []) if x not in ((state.get("gates") or {}).get("log") or [])]
            if said:
                g = dict(state.get("gates") or {})
                g["log"] = [*list(g.get("log") or []), *said]
                state = {**state, "gates": g}
            prev = state.get("phase") or "none"
            phase = step.phase or prev
            ac = state.get("ac") if step.per_ac else None
            item = state.get("item") if step.per_item else None
            skipped = self._skipped(i, step, state)
            if skipped:
                why, goto, *more = skipped
                ctx.emit("step.started", step=step.id, data={"name": step.name, "kind": step.kind, "phase": prev, "ac": ac,
                                                             "from": prev, "flow": self.wf.flow, "skipped": True})
                ctx.emit("step.finished", step=step.id, data={"name": step.name, "kind": step.kind, "phase": prev, "ac": ac,
                                                              "ok": True, "note": why, "skipped": True})
                return Command(update={"note": why, "current": step.id, **(more[0] if more else {}),
                                       **({"unlocks": merged} if len(merged) != len(have) else {}),
                                       **({"base_head": synced} if synced else {})}, goto=goto)
            started = {"name": step.name, "kind": step.kind, "phase": phase, "ac": ac, "from": prev, "flow": self.wf.flow,
                       "phase_changed": prev != phase, "transition_ok": rules.can_transition(prev, phase)}
            if item:
                started["item"] = item
            ctx.emit("step.started", step=step.id, data=started)
            # v0.15.9 keel's file history: what changed before this step (outside keel) and during it (runtime/doc_history.py)
            docs_before = doc_history.before_step(ctx.emit, ctx.root, phase, step.id)
            update, goto = await fn(i, step, {**state, "phase": phase})
            by_kind, by_name = doc_history.who(step)
            doc_history.after_step(ctx.emit, ctx.root, docs_before, by_kind, by_name, phase, step.id,
                                   (state.get("data") or {}).get("started_via"))
            if len(merged) != len(have):
                update.setdefault("unlocks", merged)
            if synced:
                update.setdefault("base_head", synced)
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

    def _skipped(self, i: int, step: Step, state: FlowState) -> tuple | None:
        """(why, where to[, state update]) when this step does not run: turned off at the opening skip menu (the whole
        unit of steps that share its group is passed over), an e2e step in a project an end-to-end test cannot drive,
        or a retry_only step that nothing sent work back to."""
        unit = ship_mod.unit_of(step)
        if step.skippable:
            skip = next((s for s in (state.get("data") or {}).get("ship_skipped") or [] if s.get("step") == unit), None)
            if skip:
                j = i
                while j + 1 < len(self.wf.steps) and self.wf.steps[j + 1].skippable and ship_mod.unit_of(self.wf.steps[j + 1]) == unit:
                    j += 1
                return f"{unit} skipped: {skip.get('reason') or 'no reason given'}", self.nav.after(j)
        if step.phase == "e2e":
            # 0.18.57 B12 any flow's e2e steps: a project with no UI and no HTTP layer (a library, a CLI) has nothing an
            # end-to-end test could drive; the whole run of e2e steps is passed over and the PR body says why
            yes, layer = project_shape.ui_or_http(self.ctx.root)
            if not yes:
                j = i
                while j + 1 < len(self.wf.steps) and self.wf.steps[j + 1].phase == "e2e" \
                        and self.nav.loop_of(j + 1) == self.nav.loop_of(i):
                    j += 1
                why = f"{layer}: an end-to-end test has nothing to drive in this project"
                data = state.get("data") or {}
                kept = [x for x in data.get("ship_skipped") or [] if x.get("step") != "e2e"]
                return f"e2e skipped: {why}", self.nav.after(j), {"data": {**data, "ship_skipped": [
                    *kept, {"step": "e2e", "band": "optional", "reason": why}]}}
        if step.per_ac and step.kind in ("agent", "parallel") and (step.agent or "") == "criterion-reviewer" \
                and self._met_by_person(state, _ac(state)):
            # 0.18.28 a person approved "already passes" with the test in front of them: listed for the full-diff review
            return (f"{state.get('ac')}: already met (a person approved it); not reviewed per AC, the full-diff review "
                    "reads its test", self.nav.after(i))
        if step.per_ac and step.kind in ("agent", "parallel") and step.phase == "red" and test_scope.is_refactor(_ac(state)):
            # a refactor criterion has no test of its own: its proof is the existing tests, run before (verify_red) and
            # after (verify_green). A RED agent here could only write a test of the source text.
            return (f"{state.get('ac')} is a refactor: no new test; its proof is the existing tests, before and after",
                    self.nav.after(i))
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
            items = get_list(state, lp.key)
            if lp.per_ac:
                items = [a for a in items if rules.in_ac_loop(a)]     # [E2E]/[SMOKE]: built by the e2e and smoke steps
            nxt = next((a for a in items if a.get("status") not in finished), None)
            if not nxt:
                return Command(update={cursor: None}, goto=self.nav.after_loop(lp))
            reopen = (state.get("data") or {}).get("reopen") if lp.per_ac else None
            if reopen and any(a["id"] == reopen.get("ac") for a in items) and reopen.get("to") in self.nav.index:
                # 0.18.28 a criterion sent back from integration: it goes on at its green, not at its red (its test passes)
                data = {k: v for k, v in (state.get("data") or {}).items() if k != "reopen"}
                return Command(update={cursor: reopen["ac"], "retries": {}, "data": data}, goto=reopen["to"])
            if lp.per_ac:
                # v0.17.5 a criterion waits for the ones it depends on (the plan's depends_on): the first unfinished one
                # whose dependencies are finished; when none is (a cycle, or one that is not in the list), list order.
                open_ids = {a["id"] for a in items if a.get("status") not in finished}
                nxt = next((a for a in items if a.get("status") not in finished
                            and not (set(a.get("depends_on") or []) & open_ids)), nxt)
            update = {cursor: nxt["id"], "retries": {}}
            esc = (state.get("data") or {}).get("ladder_escalated")
            if lp.per_ac and esc and esc.get("ac") != nxt["id"]:
                # the stall ladder's stronger model was for that criterion only: the agent's model goes back
                models_ = dict(state.get("agent_models") or {})
                if esc.get("before"):
                    models_[esc["agent"]] = esc["before"]
                else:
                    models_.pop(esc["agent"], None)
                data = {k: v for k, v in (state.get("data") or {}).items() if k != "ladder_escalated"}
                update.update(agent_models=models_, data=data)
            return Command(update=update, goto=self.wf.steps[lp.first].id)

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
        return Command(update={"status": "done", "ac": None, "note": close_note(state), **self._booked(state)}, goto=END)

    def _booked(self, state: FlowState) -> dict:
        """{"usage": ...} with the failed calls' tokens booked in, or {} when there are none."""
        if not self.ctx.unbooked:
            return {}
        usage = add_usage(dict(state.get("usage") or {}), self.ctx.unbooked)
        self.ctx.unbooked.clear()
        return {"usage": usage}

    # ------------------------------------------------------------ models

    def _model(self, state: FlowState, step: Step, agent: str) -> dict:
        m = None
        if state.get("model_override"):
            m = state["model_override"]
        elif self.ctx.models.get(thread_models.step_key(step.id)):
            m = self.ctx.models[thread_models.step_key(step.id)]     # 0.18.64 the person's model for this step of this flow
        elif agent_names.lookup(state.get("agent_models"), agent):
            m = agent_names.lookup(state.get("agent_models"), agent)    # a flow from before a rename: its old key
        elif step.model and step.model != "default" and step.model in self.ctx.models:
            m = self.ctx.models[step.model]
        elif agent_names.lookup(self.ctx.models, agent):
            m = agent_names.lookup(self.ctx.models, agent)
        elif "default" in self.ctx.models:
            m = self.ctx.models["default"]
        # 0.18.65 a flow's last review (the whole-branch review, a ship lens) runs on a stronger model than the one it
        # would get (Opus at high effort for Claude, see review_config.raised), unless Settings › Review says otherwise.
        # A model the flow was switched to (a cap's cheaper model, an escalation) or the person's pick for the step wins.
        if (m and not state.get("model_override") and not self.ctx.models.get(thread_models.step_key(step.id))
                and (review_config.final_step(step, agent) or review_config.deep_check(step, state))):
            m = review_config.final_model(self.ctx.root, m)
        return models.effective(m)

    def _ask(self, state: FlowState, question: dict, gate: str | None = None, review: str | None = None) -> tuple[dict, dict]:
        """interrupt() for any pause, plus what every answer may carry: payload.unlock {path, phase}.

        The thread's run mode may answer first (runtime/run_mode.py): `gate` names the kind of pause when the caller knows
        it (else it is classified from the question), `review` is the AC review's answer for an AC gate. An automatic
        answer is {decision: approve, why: "auto-approved (mode <m>)", auto: true}; the caller logs it like any answer.

        Returns (answer, state update). An unlock is added to state.unlocks and logged as a gate event.
        """
        answer = self._auto_answer(state, question, gate, review)
        self.ctx.answer_by = None
        if answer is None:
            answer = ask_once(question)
            self.ctx.answer_by = answer.get("by") or None
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
                (prev.agent or "") in ("criterion-reviewer", "lens-reviewer", "branch-reviewer"):
            review = state.get("last_answer") or ""
        return kind, review

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
            yes = not re.match(r"no(?![\w-])", res.text.strip(), flags=re.I)      # the word "no", not "Nothing blocks"
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
        gates = deep_gates(state)
        decision = answer.get("decision", "reject")
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": decision, "why": why})
        rounds.pop(step.id, None)
        if decision != "approve":
            gates["log"].append(f"gate {step.id} stop after {step.rounds} round(s)" + (f": {why}" if why else ""))
            return {**extra, "gates": gates, "rounds": rounds, "status": "stopped",
                    "note": f"stopped: {step.name} still no after {step.rounds} round(s)"}, END
        gates["log"].append(f"gate {step.id} go on after {step.rounds} round(s): {why or 'no reason given'}")
        return {**extra, "gates": gates, "rounds": rounds, "note": f"{step.name}: went on after {step.rounds} round(s)"}, self.nav.after(i)


def _step_cap(step: Step, settings: dict) -> tuple[int, bool]:
    """A step's token limit and whether it is the step's own: the project's per-step cap (settings.step_cap_tokens)
    applies to every step, but a step's own max_tokens wins when it is smaller. (0, False) = no limit."""
    own = int(step.max_tokens or 0)
    default = int(settings.get("step_cap_tokens") or 0)
    if own and (not default or own <= default):
        return own, True
    return default, False


def _real_cheaper(ctx):
    """The cheaper model for a budget switch, but never the fake model for a flow that runs real models: the fake model
    writes example files (the settings default once was fake). None means keel pauses and asks instead."""
    cheaper = ctx.settings.get("cheaper_model")
    if not cheaper or cheaper.get("provider") != "fake":
        return cheaper
    models = [m for m in (ctx.models or {}).values() if isinstance(m, dict)]
    return cheaper if models and all(m.get("provider") == "fake" for m in models) else None


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


class _CodeJob:
    """0.18.15 a code step shown as a job: its own code.* events (stored as a job like an agent's), agent "keel", kind "code",
    no model and no tokens. progress(text, ...) adds one live line (a command, a rung, an action's result)."""

    def __init__(self, ctx, step: Step, state: FlowState, name: str | None = None, actions: list[str] | None = None):
        self.ctx, self.step, self.state = ctx, step, state
        self.name, self.actions = name or step.name, actions
        self.call_id = uuid.uuid4().hex
        self.n = 0
        self.ok = True
        self.t0 = time.monotonic()
        self.last = ""
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    def start(self):
        ac = (_ac(self.state) or {}).get("id") if self.step.per_ac else None
        self.ctx.emit("code.started", step=self.step.id, call_id=self.call_id, data={
            "agent": "keel", "kind": "code", "provider": "keel", "model": "code", "name": self.name,
            "phase": self.state.get("phase"), "ac": ac, "action": ",".join(self.actions or self.step.actions())})

    def progress(self, text: str, **fields):
        # many actions run in a worker thread (asyncio.to_thread): their lines go back to the event loop's thread
        if self.loop is not None and not self.loop.is_closed():
            try:
                same = asyncio.get_running_loop() is self.loop
            except RuntimeError:
                same = False
            if not same:
                self.loop.call_soon_threadsafe(lambda: self.progress(text, **fields))
                return
        self.n += 1
        self.last = str(text)
        data = {"n": self.n, "kind": "tool", "job": "code", "text": str(text)[:STEP_FIELD_MAX]}
        data.update({k: (v[:STEP_FIELD_MAX] if isinstance(v, str) else v) for k, v in fields.items() if v is not None})
        self.ctx.emit("code.step", step=self.step.id, call_id=self.call_id, data=data)

    def finish(self, status: str):
        self.ctx.emit("code.finished", step=self.step.id, call_id=self.call_id, data={
            "agent": "keel", "kind": "code", "status": status, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
            "ms": int((time.monotonic() - self.t0) * 1000), "result": self.last[:500]})
