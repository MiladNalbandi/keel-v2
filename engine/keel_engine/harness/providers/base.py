"""What every provider adapter of keel's harness takes and returns.

The harness keeps one conversation in keel's own neutral form (harness/transcript.py) and asks a provider for one
model turn at a time. An adapter turns that conversation into its API's request, streams the answer (text and thinking
as `delta` / `thinking_delta` events), and gives back the turn in the neutral form, plus the provider's own content
(`raw`) so the same provider and model get their blocks back unchanged on the next turn.

    Neutral blocks: {"type": "text", "text"}
                    {"type": "thinking", "text"}
                    {"type": "tool_call", "id", "name", "input"}
                    {"type": "tool_result", "id", "content", "is_error"}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

Emit = Callable[..., None]

# why a turn ended, in keel's words (each adapter maps its API's reason onto these)
END, TOOL_USE, MAX_TOKENS, REFUSAL, PAUSE = "end", "tool_use", "max_tokens", "refusal", "pause"


@dataclass
class ToolSpec:
    """A tool as the model sees it: name, what it does, its JSON Schema."""
    name: str
    description: str
    schema: dict
    # 0.32 a tool keel would defer (Task, Skill, WebFetch, plugin tools): a provider that defers natively (Anthropic's
    # tool search, Copilot's) marks it so; the tool list itself never changes mid-session, so the cache stays valid
    deferrable: bool = False


@dataclass
class Usage:
    input: int = 0           # new input tokens, at full price
    cache_write: int = 0     # input written to the provider's prompt cache (Anthropic: about 1.25x)
    cache_read: int = 0      # input read from the cache (about a tenth of the price)
    output: int = 0
    compaction: int = 0      # 0.32 tokens a provider spent compacting the conversation (in + out; billed, not in the above)
    ai_credits: float = 0.0  # 0.32 GitHub Copilot: AI credits this call cost, as Copilot reported it (1 credit = $0.01)

    def add(self, other: "Usage") -> None:
        self.input += other.input
        self.cache_write += other.cache_write
        self.cache_read += other.cache_read
        self.output += other.output
        self.compaction += other.compaction
        self.ai_credits += other.ai_credits

    @property
    def total_in(self) -> int:
        return self.input + self.cache_write + self.cache_read


@dataclass
class Turn:
    """One model turn: its blocks (neutral), the provider's own content to send back as it came, why it ended."""
    blocks: list[dict]
    stop: str
    usage: Usage
    raw: list[dict] | None = None
    model: str = ""                         # the model that answered (a fallback can be another one)
    note: str = ""                          # something the person should know about this turn (a fallback, a refusal)

    def tool_calls(self) -> list[dict]:
        return [b for b in self.blocks if b.get("type") == "tool_call"]

    def text(self) -> str:
        return "\n".join(b["text"] for b in self.blocks if b.get("type") == "text" and b.get("text")).strip()


class ProviderError(Exception):
    """A model call that failed. `retry`: the harness may try the same turn again (rate limit, overload, network);
    `after`: seconds the provider asked to wait; `hint`: what the person can do about it."""

    def __init__(self, message: str, *, retry: bool = False, after: float | None = None, hint: str = "",
                 status: int | None = None):
        super().__init__(message)
        self.retry = retry
        self.after = after
        self.hint = hint
        self.status = status


@dataclass
class Request:
    """What the harness asks of a provider for one turn."""
    system: str
    messages: list[dict]            # neutral messages: {"role": "user"|"assistant", "content": [blocks], "raw"?, ...}
    tools: list[ToolSpec]
    max_tokens: int = 64_000
    effort: str | None = None
    web_search: bool = False        # the provider's own web search, where it has one (Anthropic's server tool)
    extra: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    name: str                       # "anthropic" | "openai": the shape of the API, for transcripts
    model: str                      # the API's model id
    # 0.32 optional: native_deferral (True: send every tool from the start, deferrable ones marked; the provider's own
    # tool search loads them, so keel's LoadTools is not needed), min_cache_tokens (below it the provider caches nothing)

    async def turn(self, req: Request, emit: Emit) -> Turn: ...
