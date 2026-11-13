"""Claude through the Anthropic Messages API (an API key), or through Google Vertex AI (AsyncAnthropicVertex): the
official `anthropic` SDK, streamed.

Each turn:
- the system prompt is one cached block (tools render before it, so the one breakpoint caches both), and top-level
  automatic caching caches the conversation's tail, so a long agent loop re-reads its history at about a tenth of the
  price;
- tools stream their input as the model writes it (eager_input_streaming); keel validates every input itself before a
  tool runs (harness/tools.py);
- current models think adaptively (display "summarized", so the person can read the thinking in the flow) at the
  chosen effort;
- long sessions compact on the server (beta compact-2026-01-12): the API summarizes older context when the input passes
  the trigger, and the compaction block comes back in the content keel sends next time;
- on the newest models a declined request is retried on another model by the API itself (server-side fallbacks).

The history is append-only: an assistant turn goes back exactly as it came (`raw`) while the model stays the same.
Thinking blocks are bound to their conversation, and an edited history is refused by the newest models.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import END, MAX_TOKENS, PAUSE, REFUSAL, TOOL_USE, Emit, ProviderError, Request, Turn, Usage

COMPACT_BETA = "compact-2026-01-12"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
STOPS = {"end_turn": END, "tool_use": TOOL_USE, "max_tokens": MAX_TOKENS, "refusal": REFUSAL, "pause_turn": PAUSE,
         "stop_sequence": END}
_ID = re.compile(r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d+))?")


def _version(model: str) -> tuple[str, int, int] | None:
    m = _ID.search(model or "")
    return (m.group(1), int(m.group(2)), int(m.group(3) or 0)) if m else None


def adaptive(model: str) -> bool:
    """Adaptive thinking: the 4.6 family and later (Haiku from 5.5). Haiku 4.5 still takes a token budget: keel runs it
    without thinking."""
    v = _version(model)
    if not v:
        return False
    fam, major, minor = v
    if fam == "haiku":
        return (major, minor) >= (5, 5)
    return (major, minor) >= (4, 6)


def compacts(model: str) -> bool:
    """Server-side compaction (beta): the same models as adaptive thinking."""
    return adaptive(model)


def falls_back(model: str) -> bool:
    """The API's own refusal fallback ("default"): the newest Opus, Sonnet and Fable on the Claude API."""
    return model in ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")


def to_messages(messages: list[dict], model: str) -> list[dict]:
    """keel's neutral conversation as Anthropic messages. An assistant turn this provider and model wrote goes back as
    it came (its thinking, compaction and fallback blocks too); another one as its text and tool calls."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "assistant":
            if m.get("api") == "anthropic" and m.get("model") == model and m.get("raw"):
                out.append({"role": "assistant", "content": m["raw"]})
                continue
            blocks: list[dict] = []
            for b in m.get("content") or []:
                if b["type"] == "text" and b.get("text"):
                    blocks.append({"type": "text", "text": b["text"]})
                elif b["type"] == "tool_call":
                    blocks.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b.get("input") or {}})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no text)"}]})
            continue
        content: list[dict] = []
        for b in m.get("content") or []:
            if b["type"] == "text" and b.get("text"):
                content.append({"type": "text", "text": b["text"]})
            elif b["type"] == "tool_result":
                body: Any = str(b.get("content") or "")
                if b.get("images"):            # a Read of a picture: the image inside the tool's result
                    body = [{"type": "text", "text": body}] + [
                        {"type": "image", "source": {"type": "base64", "media_type": im["media_type"], "data": im["data"]}}
                        for im in b["images"]]
                content.append({"type": "tool_result", "tool_use_id": b["id"], "content": body,
                                "is_error": bool(b.get("is_error"))})
        if content:
            out.append({"role": "user", "content": content})
    return out


def _neutral(block: Any) -> dict | None:
    t = getattr(block, "type", None)
    if t == "text":
        return {"type": "text", "text": block.text}
    if t == "thinking":
        return {"type": "thinking", "text": getattr(block, "thinking", "") or ""}
    if t == "tool_use":
        return {"type": "tool_call", "id": block.id, "name": block.name, "input": block.input}
    return None        # compaction, fallback, server tools: kept in raw only


def _usage(u: Any) -> Usage:
    if u is None:
        return Usage()
    return Usage(input=int(getattr(u, "input_tokens", 0) or 0), cache_write=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
                 cache_read=int(getattr(u, "cache_read_input_tokens", 0) or 0), output=int(getattr(u, "output_tokens", 0) or 0))


def _error(exc: Exception) -> ProviderError:
    """The SDK's typed errors, most specific first: what may be tried again, and what the person must fix."""
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError("Anthropic did not accept the API key.", status=401,
                             hint="Check the Anthropic API key in Settings › Models & logins.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderError(f"Anthropic refused this request: {exc.message}", status=403,
                             hint="The key's workspace may not have this model, or its spend limit is reached.")
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError(f"Anthropic does not know this model: {exc.message}", status=404,
                             hint="Pick another model in Settings › Models & logins.")
    if isinstance(exc, anthropic.RateLimitError):
        after = exc.response.headers.get("retry-after") if exc.response is not None else None
        return ProviderError("Anthropic's rate limit: waiting before the next try.", retry=True, status=429,
                             after=float(after) if after and after.replace(".", "", 1).isdigit() else None)
    if isinstance(exc, anthropic.BadRequestError):
        return ProviderError(f"Anthropic refused the request: {exc.message}", status=400)
    if isinstance(exc, anthropic.APIStatusError):
        retry = exc.status_code >= 500 or exc.status_code in (408, 409)
        return ProviderError(f"Anthropic answered {exc.status_code}: {exc.message}", retry=retry, status=exc.status_code)
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return ProviderError(f"Could not reach Anthropic: {exc}", retry=True)
    return ProviderError(f"{type(exc).__name__}: {str(exc)[:300]}")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, *, client: Any = None, vertex: bool = False):
        self.model = model
        self.vertex = vertex
        if client is None:
            from anthropic import AsyncAnthropic

            if not api_key:
                raise ProviderError("No Anthropic API key.", hint="Add one in Settings › Models & logins (or keel2 token anthropic).")
            # the SDK retries connection errors, 408, 409, 429 and 5xx itself; the harness retries a whole turn on top
            client = AsyncAnthropic(api_key=api_key, max_retries=3, timeout=600)
        self.client = client

    def params(self, req: Request) -> dict:
        tools: list[dict] = [{"name": t.name, "description": t.description, "input_schema": t.schema,
                              "eager_input_streaming": True} for t in req.tools]
        if req.web_search:
            # Vertex AI has only the basic web search; the Claude API the one with dynamic filtering
            tools.append({"type": "web_search_20250305" if self.vertex else "web_search_20260209", "name": "web_search"})
        p: dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "system": [{"type": "text", "text": req.system or "You are a careful software engineer.",
                        "cache_control": {"type": "ephemeral"}}],
            "messages": to_messages(req.messages, self.model),
            "cache_control": {"type": "ephemeral"},      # the conversation's tail, automatically
        }
        if tools:
            p["tools"] = tools
        betas: list[str] = []
        if adaptive(self.model):
            p["thinking"] = {"type": "adaptive", "display": "summarized"}
            p["output_config"] = {"effort": req.effort or "high"}
        if compacts(self.model):
            betas.append(COMPACT_BETA)
            trigger = int(req.extra.get("compact_at") or 150_000)
            p["context_management"] = {"edits": [{"type": "compact_20260112",
                                                  "trigger": {"type": "input_tokens", "value": trigger}}]}
        if falls_back(self.model) and not self.vertex:
            betas.append(FALLBACK_BETA)
            p["fallbacks"] = "default"
        if betas:
            p["betas"] = betas
        return p

    async def turn(self, req: Request, emit: Emit) -> Turn:
        import anthropic

        params = self.params(req)
        try:
            async with self.client.beta.messages.stream(**params) as stream:
                async for event in stream:
                    kind = getattr(event, "type", "")
                    if kind == "text" and getattr(event, "text", ""):
                        emit("delta", event.text)
                    elif kind == "thinking" and getattr(event, "thinking", ""):
                        emit("thinking_delta", event.thinking)
                final = await stream.get_final_message()
        except ValueError as exc:
            # a tool input the SDK could not parse at all: there is no tool_use id to answer, so the turn is asked again
            raise ProviderError(f"The model wrote a tool input keel could not read ({str(exc)[:200]}).", retry=True) from exc
        except anthropic.APIError as exc:
            raise _error(exc) from exc
        blocks = [b for b in (_neutral(x) for x in final.content) if b]
        raw = [x.model_dump(mode="json", exclude_none=True) for x in final.content]
        stop = STOPS.get(final.stop_reason or "", END)
        note = ""
        served = getattr(final, "model", "") or self.model
        if served != self.model:
            note = f"{self.model} declined; {served} answered this turn."
        if stop == REFUSAL:
            details = getattr(final, "stop_details", None)
            why = getattr(details, "explanation", None) or getattr(details, "category", None) or "no reason given"
            note = f"The model declined this request ({why})."
        return Turn(blocks=blocks, stop=stop, usage=_usage(final.usage), raw=raw, model=self.model, note=note)


def vertex_provider(model: str, cfg: dict) -> AnthropicProvider:
    """Claude on Google Vertex AI: the same Messages API through AsyncAnthropicVertex, Google's credentials."""
    from anthropic import AsyncAnthropicVertex

    from ...models import vertex

    project, region = vertex._need(cfg)
    client = AsyncAnthropicVertex(project_id=project, region=region, credentials=vertex.credentials(cfg), max_retries=3,
                                  timeout=600)
    return AnthropicProvider(model.removeprefix("anthropic/"), client=client, vertex=True)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
