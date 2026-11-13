"""Every model behind an OpenAI-compatible chat API: OpenAI (an API key), GitHub Models (a GitHub token), a local model
server (Ollama, LM Studio, vLLM, llama.cpp; models/local.py) and Gemini on Vertex AI (a Google access token). The
official `openai` SDK, streamed, with function tools.

The provider caches a repeated prompt prefix itself (OpenAI does it automatically); keel keeps the request's start
stable for it: the system prompt, then the tools, then the conversation in order. Tool results go back as `tool`
messages, one per call. A model's reasoning, where a server streams it (`reasoning_content` / `reasoning`), shows as
thinking.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from .base import END, MAX_TOKENS, REFUSAL, TOOL_USE, Emit, ProviderError, Request, Turn, Usage

GITHUB_MODELS_URL = "https://models.github.ai/inference"
STOPS = {"stop": END, "tool_calls": TOOL_USE, "function_call": TOOL_USE, "length": MAX_TOKENS, "content_filter": REFUSAL}
_NAME = re.compile(r"[^a-zA-Z0-9_-]")


def tool_name(name: str) -> str:
    """OpenAI's rule for function names (letters, digits, _ and -, at most 64)."""
    return _NAME.sub("_", name)[:64]


def to_messages(system: str, messages: list[dict]) -> list[dict]:
    """keel's neutral conversation as chat messages: assistant text and tool calls, one `tool` message per result."""
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        blocks = m.get("content") or []
        if m["role"] == "assistant":
            text = "\n".join(b["text"] for b in blocks if b["type"] == "text" and b.get("text"))
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": tool_name(b["name"]), "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                     for b in blocks if b["type"] == "tool_call"]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        texts = [b["text"] for b in blocks if b["type"] == "text" and b.get("text")]
        images: list[dict] = []
        for b in blocks:
            if b["type"] == "tool_result":
                body = str(b.get("content") or "")
                out.append({"role": "tool", "tool_call_id": b["id"], "content": ("ERROR: " + body) if b.get("is_error") else body})
                images += b.get("images") or []
        if images:      # a tool message carries no image: the pictures follow as the user's, right after the results
            out.append({"role": "user", "content": [{"type": "text", "text": "The images from the tool results above:"}] + [
                {"type": "image_url", "image_url": {"url": f"data:{im['media_type']};base64,{im['data']}"}} for im in images]})
        if texts:
            out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def _error(exc: Exception, who: str) -> ProviderError:
    import openai

    if isinstance(exc, openai.AuthenticationError):
        return ProviderError(f"{who} did not accept the key.", status=401, hint="Check the key in Settings › Models & logins.")
    if isinstance(exc, openai.PermissionDeniedError):
        return ProviderError(f"{who} refused this request: {exc.message}", status=403)
    if isinstance(exc, openai.NotFoundError):
        return ProviderError(f"{who} does not know this model: {exc.message}", status=404,
                             hint="Pick another model in Settings › Models & logins.")
    if isinstance(exc, openai.RateLimitError):
        after = exc.response.headers.get("retry-after") if exc.response is not None else None
        return ProviderError(f"{who}'s rate limit: waiting before the next try.", retry=True, status=429,
                             after=float(after) if after and after.replace(".", "", 1).isdigit() else None)
    if isinstance(exc, openai.BadRequestError):
        return ProviderError(f"{who} refused the request: {exc.message}", status=400)
    if isinstance(exc, openai.APIStatusError):
        return ProviderError(f"{who} answered {exc.status_code}: {exc.message}", retry=exc.status_code >= 500 or exc.status_code in (408, 409),
                             status=exc.status_code)
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return ProviderError(f"Could not reach {who}: {exc}", retry=True)
    return ProviderError(f"{type(exc).__name__}: {str(exc)[:300]}")


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, model: str, *, api_key: str | Callable[[], str] | None, base_url: str | None = None,
                 who: str = "OpenAI", reasoning: bool = True, client: Any = None, tools: bool = True):
        self.model = model
        self.who = who
        self.reasoning = reasoning        # the server takes reasoning_effort (OpenAI's reasoning models)
        self.tools_ok = tools             # a local model without tool calls answers from its prompt only
        self._key = api_key
        self._base = base_url
        self._client = client
        if client is None and not api_key:
            raise ProviderError(f"No key for {who}.", hint="Add it in Settings › Models & logins.")

    def client(self):
        if self._client is not None:
            return self._client
        from openai import AsyncOpenAI

        key = self._key() if callable(self._key) else self._key      # a Google token renews itself
        return AsyncOpenAI(api_key=key, base_url=self._base, max_retries=3, timeout=600)

    def params(self, req: Request) -> dict:
        p: dict[str, Any] = {"model": self.model, "messages": to_messages(req.system, req.messages), "stream": True,
                             "stream_options": {"include_usage": True}}
        if req.tools and self.tools_ok:
            p["tools"] = [{"type": "function", "function": {"name": tool_name(t.name), "description": t.description,
                                                            "parameters": t.schema}} for t in req.tools]
        if req.effort and self.reasoning:
            p["reasoning_effort"] = req.effort
        return p

    async def turn(self, req: Request, emit: Emit) -> Turn:
        import openai

        names = {tool_name(t.name): t.name for t in req.tools}
        text: list[str] = []
        thinking: list[str] = []
        calls: dict[int, dict] = {}
        finish = None
        usage = Usage()
        try:
            stream = await self.client().chat.completions.create(**self.params(req))
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    u = chunk.usage
                    cached = int(getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0)
                    usage = Usage(input=max(int(u.prompt_tokens or 0) - cached, 0), cache_read=cached,
                                  output=int(u.completion_tokens or 0))
                for choice in chunk.choices or []:
                    d = choice.delta
                    if d is not None:
                        if d.content:
                            text.append(d.content)
                            emit("delta", d.content)
                        extra = getattr(d, "model_extra", None) or {}
                        thought = extra.get("reasoning_content") or extra.get("reasoning")
                        if isinstance(thought, str) and thought:
                            thinking.append(thought)
                            emit("thinking_delta", thought)
                        for tc in d.tool_calls or []:
                            slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": []})
                            if tc.id:
                                slot["id"] = tc.id
                            if tc.function and tc.function.name:
                                slot["name"] += tc.function.name
                            if tc.function and tc.function.arguments:
                                slot["args"].append(tc.function.arguments)
                    if choice.finish_reason:
                        finish = choice.finish_reason
        except openai.APIError as exc:
            raise _error(exc, self.who) from exc
        blocks: list[dict] = []
        if thinking:
            blocks.append({"type": "thinking", "text": "".join(thinking)})
        if text:
            blocks.append({"type": "text", "text": "".join(text)})
        for i in sorted(calls):
            c = calls[i]
            raw = "".join(c["args"]).strip() or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {"INVALID_JSON": raw}        # harness/tools.py answers it as an error, so the model can retry
            blocks.append({"type": "tool_call", "id": c["id"] or f"call_{i}", "name": names.get(c["name"], c["name"]),
                           "input": args if isinstance(args, dict) else {"INVALID_JSON": raw}})
        stop = STOPS.get(finish or "", END)
        if calls and stop == END:
            stop = TOOL_USE            # some servers end a tool-calling turn with "stop"
        note = "The model declined this request (content filter)." if stop == REFUSAL else ""
        return Turn(blocks=blocks, stop=stop, usage=usage, raw=None, model=self.model, note=note)
