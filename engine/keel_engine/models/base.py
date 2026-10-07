"""What every model runner takes and returns.

A runner gets one agent call (AgentRequest) and an `emit(kind, text, **extra)` callback. Each
thing the agent does becomes one emitted step (kind: text|thinking|tool|write|edit|result|answer|
guard|error), which the runtime forwards as an `agent.step` event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..tools.agent_tools import ToolBox

Emit = Callable[..., None]


@dataclass
class AgentRequest:
    agent: str
    system: str
    prompt: str
    root: str
    phase: str
    model: dict                      # {provider, mode, model, effort?}
    toolbox: ToolBox
    ac: dict | None = None
    acs: list[dict] = field(default_factory=list)
    title: str = ""
    step_name: str = ""
    index: int = 0                   # copy number in a parallel step
    feedback: str | None = None
    mcp_specs: list[dict] = field(default_factory=list)
    tools_allow: list[str] = field(default_factory=list)
    key: str | None = None           # API key or GitHub token for this provider, never logged
    workdir: str = ""                # scratch folder for config files (mcp.json, schema files)
    timeout: int = 1800
    keys: dict[str, str] = field(default_factory=dict)   # StartThread.keys (e.g. claude_oauth, copilot), never logged
    section: str | None = None       # knowledge-refresh: the knowledge section this librarian writes
    item: dict | None = None         # a fan-out or for_each step: the one item this agent works on
    session: str | None = None       # the CLI session id to use (claude --session-id) or continue (resume=True)
    resume: bool = False             # continue `session` (claude --resume, codex exec resume) instead of a new one
    on_session: Any = None           # callback(session_id) when the CLI reports its session id (codex)
    guard_ctx: str = ""              # the guard context file keel's hook reads (runtime/guard_ctx.py); "" = the runner writes one
    thread: str = ""                 # the thread this call belongs to
    knowledge: dict | None = None    # {sections, code_graph, memory, strict} (runtime/agent_knowledge.py)
    history: list[tuple[str, str]] = field(default_factory=list)   # earlier turns [(user|assistant, text)]: KeelBot, api mode


@dataclass
class AgentResult:
    text: str = ""
    tokens_in: int = 0               # new input (fresh + cache writes)
    tokens_cached: int = 0           # cache reads: re-sent context, about a tenth of the price
    tokens_out: int = 0
    cost_usd: float = 0.0
    premium_requests: int = 0
    data: dict[str, Any] = field(default_factory=dict)


class Runner(Protocol):
    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult: ...


class ModelError(Exception):
    def __init__(self, message: str, hint: str = "", usage: dict | None = None):
        super().__init__(message)
        self.hint = hint
        self.usage = usage or {}   # what a failed run still used: tokens_in, tokens_out, tokens_cached, cost_usd
