"""Graph state (mirrors keel v1 state) and the per-thread context the nodes need."""

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
    spec: str | None
    branch: str | None
    git_head: str | None
    last_failure: str | None
    note: str
    error: str | None
    base_head: str | None       # HEAD when the thread started; the branch diff for blockers starts here
    last_answer: str            # the last agent's final answer (shown at a spec gate that has no criteria)
    preexisting: dict           # {path: fingerprint} of the user's uncommitted files at start; never committed unless an agent changed them
    unlocks: list[dict]         # [{path, phase, by?}] keel v1 unlocks: that path bypasses the matrix in that phase
    deps: list[str]             # dependencies the user approved at a commit (keel v1 state.deps)
    blockers: list[dict]        # [{gate, why, fix}] push blockers, refreshed by push_check and every commit
    ladder: list[dict] | None   # init: [{n, name, cmd, status, detail?}]


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
    keys: dict[str, str] = field(default_factory=dict)   # memory only, never stored
    bus: EventBus | None = None

    @property
    def fake(self) -> bool:
        return config.fake() or all((m or {}).get("provider", "fake") == "fake" for m in self.models.values())

    @property
    def simulate_checks(self) -> bool:
        """Simulate test runs? Default: when every model is fake. settings.simulate_checks overrides."""
        v = self.settings.get("simulate_checks")
        return self.fake if v is None else bool(v)

    def disk_unlocks(self) -> list[dict]:
        """Unlocks the api (or keel v1's CLI) appended to <root>/.keel/state.json while this thread runs."""
        from ..events import mirror

        return normalize_unlocks(mirror.read_state(self.root).get("unlocks"), "none", "api")

    def write_mirror(self, values: dict, merge_disk: bool = True):
        """Write <root>/.keel/state.json from graph values (keel v1 hooks read it before every tool call).

        Unlocks already on disk are merged in (by path+phase), never overwritten: the api may add them
        while the thread runs. A thread's first write (merge_disk=False) starts clean, like `keel state start`.
        """
        from ..events import mirror

        if not values:
            return
        if merge_disk:
            merged = merge_unlocks(list(values.get("unlocks") or []), self.disk_unlocks())
            if len(merged) != len(values.get("unlocks") or []):
                values = {**values, "unlocks": merged}
        step = self.workflow.step(values.get("current") or "")
        mirror.write_state(self.root, mirror.state_json(values, self.workflow.flow, self.thread_id, step.name if step else None))

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
        usage={"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "premium_requests": 0, "cap_tokens": cap},
        retries={}, step_tokens={}, feedback=None, model_override=None, warned=False, spec=None, branch=None,
        git_head=None, last_failure=None, note="started", error=None, base_head=None, preexisting={}, last_answer="",
        unlocks=normalize_unlocks(s.get("unlocks"), "none", "settings"), deps=[], blockers=[], ladder=None,
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
