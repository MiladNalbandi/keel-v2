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


@dataclass
class ThreadContext:
    thread_id: str
    project_id: str
    root: str
    workflow: Workflow
    title: str
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
        git_head=None, last_failure=None, note="started", error=None,
    )
