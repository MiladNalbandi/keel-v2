"""Graph state (the flow's whole state lives here, in the engine's checkpoints) and the per-thread context the nodes need."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from .. import config
from ..events import EventBus
from ..workflows.model import Workflow


class FlowState(TypedDict, total=False):
    flow: str
    workflow_id: str
    title: str
    status: str                 # running | done | failed | stopped
    phase: str                  # keel v1 phase
    current: str | None         # step id
    acs: list[dict]             # {id, layer, title, status}
    ac: str | None
    gates: dict                 # {mode, log: [str], skipped: {}}
    stall: dict                 # {fingerprint, count, step}
    usage: dict                 # {tokens_in, tokens_out, cost_usd, premium_requests, cap_tokens}
    retries: dict               # "<step>:<ac>" -> failed attempts
    step_tokens: dict           # step id -> tokens used
    feedback: str | None        # why a gate or check sent work back; goes into the next agent's prompt
    model_override: dict | None # set by on_cap=cheaper
    warned: bool
    usage_seen: dict            # "provider:window:resets_at" -> "warn" | "ok": plan-window warnings already given or passed
    spec: str | None
    branch: str | None
    git_head: str | None
    last_failure: str | None
    note: str
    error: str | None
    base_head: str | None       # HEAD when the thread started; the branch diff for blockers starts here
    last_answer: str            # the last agent's final answer (shown at a spec gate that has no criteria)
    findings: list              # blocking findings of the review step that just ran: [{lens, text}]
    review_rounds: dict         # {review step id: fix rounds so far}
    init: dict                  # keel init's answers: runs_on, services, knowledge_sections
    clarify: dict               # the explorer's open questions: {questions: [...]}; empty when none
    clarify_rounds: int         # rounds of questions asked so far (the explorer decides after clarify.MAX_ROUNDS)
    spec_revisions: int         # times keel's spec check sent the spec back by itself (once)
    preexisting: dict           # {path: fingerprint} of the user's uncommitted files at start; never committed unless an agent changed them
    unlocks: list[dict]         # [{path, phase, by?, reason?, at?}]: that path bypasses the guard matrix in that phase
    deps: list[str]             # dependencies the user approved at a commit
    blockers: list[dict]        # [{gate, why, fix}] push blockers, refreshed by push_check and every commit
    ladder: list[dict] | None   # init: [{n, name, cmd, status, detail?}]
    data: dict                  # lists and results steps make: a `collect` list, "<step>_results" of a fan-out, a seed
    markers: dict               # {step id: {NAME: value}} from agents' answers; "*" holds the latest value of each name
    item: str | None            # the current item id of a for_each loop
    children: list[dict]        # flows this one started (start_flow): [{thread_id, workflow, step, title}]
    parent: dict | None         # the flow that started this one: {thread_id, workflow, step}
    pr_body: str | None         # the PR body the pr action built
    flaky: list[dict]           # tests that failed once and passed on the rerun: [{label, tests, at}]
    rounds: dict                # {branch step id: send-backs so far} for a branch with `rounds`


@dataclass
class ThreadContext:
    thread_id: str
    project_id: str
    root: str
    workflow: Workflow
    title: str
    request: str = ""                                    # what the user asked for, in their words
    models: dict[str, dict] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    mcp: list[dict] = field(default_factory=list)
    skills: dict[str, str] = field(default_factory=dict)
    agents: dict[str, dict] = field(default_factory=dict)   # per agent: {knowledge: {...}} (agent_knowledge.py)
    keys: dict[str, str] = field(default_factory=dict)   # memory only, never stored
    bus: EventBus | None = None
    # Agent results of a parallel step whose other agents failed: "try again" re-runs the node from the top
    # (LangGraph), and only the failed agents should run again. Memory only; cleared when the step finishes.
    done_calls: dict[str, Any] = field(default_factory=dict)
    memory: Any = None                                   # AgentMemory: agent sessions and trails on /data
    # Unlocks granted through POST /threads/{id}/unlocks (stored in the engine DB; the next step merges them into the
    # graph state) and the guards of the agents running now (guard context files, ToolBoxes), which get them at once.
    api_unlocks: list[dict] = field(default_factory=list)
    guards: list = field(default_factory=list)
    # start_flow: async (workflow id, seed, link to this thread) -> the new thread's id (set by the Engine)
    spawn: Any = None

    @property
    def fake(self) -> bool:
        return config.fake() or all((m or {}).get("provider", "fake") == "fake" for m in self.models.values())

    @property
    def simulate_checks(self) -> bool:
        """Simulate test runs? Default: when every model is fake. settings.simulate_checks overrides."""
        v = self.settings.get("simulate_checks")
        return self.fake if v is None else bool(v)

    def add_unlocks(self, new: list[dict]) -> list[dict]:
        """Adds unlocks for this thread; every guard running now (hook context file, ToolBox) gets them at once.
        Returns the ones that were new."""
        merged = merge_unlocks(self.api_unlocks, new)
        added = merged[len(self.api_unlocks):]
        self.api_unlocks = merged
        if added:
            for g in list(self.guards):
                g.add_unlocks(added)
        return added

    def emit(self, type: str, *, step: str | None = None, call_id: str | None = None, data: dict | None = None):
        if self.bus:
            self.bus.emit(type, self.thread_id, self.project_id, step=step, call_id=call_id, data=data)


def initial_state(ctx: ThreadContext, acs: list[dict] | None) -> FlowState:
    s = ctx.settings
    cap = int(s.get("cap_tokens") or (ctx.workflow.budget.max_tokens if ctx.workflow.budget and ctx.workflow.budget.max_tokens else 0) or 0)
    return FlowState(
        flow=ctx.workflow.flow, workflow_id=ctx.workflow.id, title=ctx.title, status="running", phase="none",
        current=None, acs=[{"id": a["id"], "layer": a.get("layer", "API"), "title": a.get("title", ""), "status": "todo"} for a in acs or []],
        ac=None, gates={"mode": s.get("gates_mode", "every-ac"), "log": [], "skipped": {}},
        stall={"fingerprint": None, "count": 0, "step": 0},
        usage={"tokens_in": 0, "tokens_out": 0, "tokens_cached": 0, "cost_usd": 0.0, "premium_requests": 0, "cap_tokens": cap},
        retries={}, step_tokens={}, feedback=None, model_override=None, warned=False, spec=None, branch=None,
        git_head=None, last_failure=None, note="started", error=None, base_head=None, preexisting={}, last_answer="", findings=[], review_rounds={}, init={}, clarify={}, clarify_rounds=0, spec_revisions=0,
        unlocks=normalize_unlocks(s.get("unlocks"), "none", "settings"), deps=[], blockers=[], ladder=None,
        data={}, markers={}, item=None, children=[], parent=None, pr_body=None, flaky=[], rounds={},
    )


def normalize_unlocks(raw, phase: str, by: str) -> list[dict]:
    """[{path, phase, by}] from a dict or a list of dicts; phase defaults to the current phase."""
    items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    out = []
    for u in items:
        if not isinstance(u, dict) or not str(u.get("path") or "").strip():
            continue
        item = {"path": str(u["path"]).strip().removeprefix("./"), "phase": u.get("phase") or phase, "by": u.get("by") or by}
        item.update({k: u[k] for k in ("reason", "at") if u.get(k)})
        out.append(item)
    return out


def merge_unlocks(have: list[dict], new: list[dict]) -> list[dict]:
    seen = {(u["path"], u["phase"]) for u in have}
    return list(have) + [u for u in new if (u["path"], u["phase"]) not in seen]
