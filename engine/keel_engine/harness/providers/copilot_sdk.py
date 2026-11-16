"""GitHub Copilot (your plan): keel's harness on the person's Copilot plan, through GitHub's Copilot SDK.

GitHub retired GitHub Models (models.github.ai) on 2026-07-30; its API now answers every request with a plain "OK".
The official Copilot SDK (`github-copilot-sdk`, import `copilot`) is GitHub's way for an app to make Copilot requests
for a person, on their own Copilot plan. It drives the Copilot runtime (a native binary the SDK downloads, or
COPILOT_CLI_PATH) over JSON-RPC.

keel keeps its own agent loop (harness/runner.py) and its own tools. The SDK session gets keel's tools as custom tools
and none of Copilot's own (available_tools is keel's list), so every file read, edit and command still goes through
keel's rules. One harness turn maps onto the SDK session like this:

    turn 1   send the prompt ─────────► Copilot thinks ─► calls keel's Read (custom tool handler)
             ◄── Turn(tool_call Read, stop=tool_use)        the handler waits on a future
    runner   runs Read through keel's tools (rules, guard), adds the tool_result
    turn 2   the tool_result answers the waiting handler ─► Copilot goes on ─► ... ─► session.idle
             ◄── Turn(text, stop=end)

A turn whose messages are not the continuation of a live SDK session (a new conversation, a helper, keel's summary of
a long session, a brain call without tools) starts a new SDK session from the messages as text.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from typing import Any

from .base import END, TOOL_USE, Emit, ProviderError, Request, Turn, Usage

WHO = "GitHub Copilot"
DEFAULT_MODEL = "auto"
TURN_WAIT = float(os.environ.get("KEEL_COPILOT_TURN_WAIT", "900"))   # seconds one turn may take
BATCH_WAIT = 0.3                  # parallel tool calls arrive close together: gather them into one turn
MODELS_TTL = 600.0
EFFORTS = ("low", "medium", "high", "xhigh", "max")

HINT_INSTALL = ("keel's engine needs the Copilot SDK: pip install github-copilot-sdk, then python -m copilot "
                "download-runtime (or set COPILOT_CLI_PATH to the Copilot runtime).")
HINT_TOKEN = ("Sign in with GitHub in Settings › Connections, or paste a GitHub token on the GitHub Copilot row in "
              "Settings › Models & keys: an OAuth token (gho_), a GitHub App user token (ghu_) or a fine-grained "
              "token (github_pat_) with the Copilot Requests permission. The account needs a Copilot plan "
              "(Copilot Free works for personal use).")
HINT_CLASSIC = ("Classic tokens (ghp_) do not work with Copilot. Make a fine-grained token with the Copilot Requests "
                "permission, or sign in with GitHub in Settings › Connections.")
HINT_MODEL = "Pick another model on the GitHub Copilot row, or auto (Copilot picks one your plan has)."

_clients: dict[str, Any] = {}
_client_lock = asyncio.Lock()
_models: dict[str, tuple[float, list[dict]]] = {}


def sdk():
    """The SDK module, or a clear error when the engine does not have it."""
    try:
        import copilot  # noqa: F401
        from copilot import CopilotClient
        from copilot.session import PermissionHandler
        from copilot.tools import Tool, ToolResult
    except ImportError as exc:
        raise ProviderError("The Copilot SDK is not installed in keel's engine.", hint=HINT_INSTALL) from exc
    return CopilotClient, PermissionHandler, Tool, ToolResult


def check_token(token: str | None) -> str | None:
    t = (token or "").strip()
    if t.startswith("ghp_"):
        raise ProviderError("A classic GitHub token (ghp_) cannot use Copilot.", status=401, hint=HINT_CLASSIC)
    return t or None


def _token_key(token: str | None) -> str:
    return hashlib.sha256((token or "<signed-in user>").encode()).hexdigest()[:16]


async def client_for(token: str | None, factory=None):
    """One started SDK client per token (the runtime is one child process), kept for the engine's life."""
    token = check_token(token)
    key = _token_key(token)
    async with _client_lock:
        c = _clients.get(key)
        if c is not None:
            return c
        make = factory or sdk()[0]
        c = make(github_token=token, use_logged_in_user=False) if token else make()
        try:
            await c.start()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"The Copilot runtime did not start: {str(exc)[:300]}", hint=HINT_INSTALL) from exc
        _clients[key] = c
        return c


async def drop_client(token: str | None) -> None:
    c = _clients.pop(_token_key(check_token(token)), None)
    if c is not None:
        try:
            await c.stop()
        except Exception:  # noqa: BLE001
            pass


def _field(obj, *names, default=None):
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


async def auth_status(token: str | None, factory=None) -> dict:
    c = await client_for(token, factory)
    st = await c.get_auth_status()
    return {"ok": bool(_field(st, "isAuthenticated", "is_authenticated", default=False)),
            "login": _field(st, "login"), "message": _field(st, "statusMessage", "status_message")}


async def list_models(token: str | None, factory=None, fresh: bool = False) -> list[dict]:
    """The models this person's plan has (the SDK asks Copilot), always with auto first. Cached for MODELS_TTL."""
    key = _token_key(check_token(token))
    hit = _models.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < MODELS_TTL:
        return hit[1]
    out = [{"id": DEFAULT_MODEL, "label": "Auto (Copilot picks a model your plan has)"}]
    try:
        c = await client_for(token, factory)
        for m in await c.list_models() or []:
            mid = str(_field(m, "id", default="") or "")
            if mid and mid != DEFAULT_MODEL:
                out.append({"id": mid, "label": str(_field(m, "name", default=mid) or mid)})
    except ProviderError:
        raise
    except Exception:  # noqa: BLE001 - an empty or failing list leaves auto, which always works
        pass
    _models[key] = (time.monotonic(), out)
    return out


def cached_models() -> list[dict]:
    """The last model list any token got (the catalog shows it without waiting); auto when there is none yet."""
    best: list[dict] = []
    for _, models in _models.values():
        if len(models) > len(best):
            best = models
    return best or [{"id": DEFAULT_MODEL, "label": "Auto (Copilot picks a model your plan has)"}]


def _plain(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        t = b.get("type")
        if t == "text":
            parts.append(b.get("text") or "")
        elif t == "tool_call":
            parts.append(f"[called {b.get('name')} {json.dumps(b.get('input'), default=str)[:2000]}]")
        elif t == "tool_result":
            parts.append(f"[result{' (error)' if b.get('is_error') else ''}: {str(b.get('content') or '')[:4000]}]")
    return "\n".join(p for p in parts if p)


def as_prompt(messages: list[dict]) -> str:
    """A conversation the SDK session has not seen, as one prompt: the earlier part as a transcript, then the last ask."""
    if not messages:
        return ""
    last = messages[-1]
    ask = _plain(last.get("content"))
    if len(messages) == 1:
        return ask
    lines = ["Earlier in this conversation:"]
    for m in messages[:-1]:
        text = _plain(m.get("content"))
        if text:
            lines.append(f"--- {m.get('role')}:\n{text}")
    lines.append("--- Now:\n" + ask)
    return "\n\n".join(lines)


def _conv_key(req: Request) -> str:
    first = req.messages[0] if req.messages else {}
    return hashlib.sha256((req.system + json.dumps(first.get("content"), sort_keys=True, default=str)).encode()).hexdigest()


class _Conv:
    """One live SDK session: the tool calls waiting for keel's answers and what the model said since the last turn."""

    def __init__(self):
        self.session = None
        self.seen = 0                       # messages of the harness conversation this session has had
        self.batch: list[tuple[dict, asyncio.Future]] = []
        self.pending: dict[str, asyncio.Future] = {}
        self.text: list[str] = []
        self.final: str | None = None
        self.thinking: list[str] = []
        self.usage = Usage()
        self.model = ""
        self.idle = asyncio.Event()
        self.wake = asyncio.Event()
        self.error: str | None = None
        self.note = ""


class CopilotProvider:
    """keel's harness turn on the person's Copilot plan (see the module docstring)."""

    name = "copilot"

    def __init__(self, model: str, token: str | None, factory=None):
        self.model = model or DEFAULT_MODEL
        self._token = check_token(token)
        self._factory = factory              # tests: a fake CopilotClient class
        self._convs: dict[str, _Conv] = {}

    # ------------------------------------------------------------------ the SDK session

    async def _open(self, conv: _Conv, req: Request, emit: Emit, root: str | None) -> None:
        if self._factory is not None:
            Tool, ToolResult, approve_all = _FakeTool, _FakeResult, None
        else:
            _, PermissionHandler, Tool, ToolResult = sdk()
            approve_all = PermissionHandler.approve_all
        client = await client_for(self._token, self._factory)
        loop = asyncio.get_running_loop()

        def handler_for(name: str):
            async def handler(inv):
                fut: asyncio.Future = loop.create_future()
                call = {"type": "tool_call", "id": str(_field(inv, "tool_call_id", default="") or f"call_{uuid.uuid4().hex[:12]}"),
                        "name": name, "input": _field(inv, "arguments", default=None) or {}}
                conv.batch.append((call, fut))
                conv.wake.set()
                out = await fut
                content, is_error = out
                return ToolResult(text_result_for_llm=content, result_type="failure" if is_error else "success")
            return handler

        tools = [Tool(name=t.name, description=t.description, parameters=t.schema, handler=handler_for(t.name),
                      overrides_built_in_tool=True, skip_permission=True) for t in req.tools]
        opts: dict[str, Any] = {
            "model": self.model, "streaming": True, "tools": tools,
            # none of Copilot's own tools: only keel's, so keel's rules check every call
            "available_tools": [t.name for t in req.tools],
            "system_message": {"mode": "replace", "content": req.system or "You are a helpful assistant."},
        }
        if approve_all is not None:
            opts["on_permission_request"] = approve_all
        if root:
            opts["working_directory"] = root
        if req.effort in EFFORTS:
            opts["reasoning_effort"] = req.effort
        try:
            conv.session = await client.create_session(**opts)
        except Exception as exc:
            err = _error(exc)
            if err.status != 404 or opts["model"] == DEFAULT_MODEL:
                raise err from exc
            # a model the plan does not have (a name saved for GitHub Models or another plan): Copilot picks one
            opts["model"] = DEFAULT_MODEL
            conv.note = (f"Your Copilot plan has no model {self.model!r}: Copilot picked one (auto). Pick a model your "
                         "plan has on the GitHub Copilot row in Settings › Models & keys.")
            try:
                conv.session = await client.create_session(**opts)
            except Exception as exc2:
                raise _error(exc2) from exc2
        conv.session.on(lambda ev: self._event(conv, ev, emit))

    def _event(self, conv: _Conv, ev, emit: Emit) -> None:
        if _field(ev, "agent_id"):
            return                            # a sub-agent's events (keel turns Copilot's sub-agents off anyway)
        data = _field(ev, "data")
        kind = type(data).__name__
        if kind == "AssistantMessageDeltaData":
            d = _field(data, "delta_content") or ""
            if d:
                conv.text.append(d)
                emit("delta", d)
        elif kind == "AssistantReasoningDeltaData":
            d = _field(data, "delta_content") or ""
            if d:
                conv.thinking.append(d)
                emit("thinking_delta", d)
        elif kind == "AssistantMessageData":
            content = _field(data, "content") or ""
            if content:
                conv.final = content
        elif kind == "AssistantUsageData":
            conv.usage.add(Usage(input=int(_field(data, "input_tokens") or 0), output=int(_field(data, "output_tokens") or 0),
                                 cache_read=int(_field(data, "cache_read_tokens") or 0),
                                 cache_write=int(_field(data, "cache_write_tokens") or 0)))
            conv.model = str(_field(data, "model") or conv.model or "")
        elif kind == "SessionErrorData":
            conv.error = str(_field(data, "message") or "Copilot stopped with an error.")
            conv.idle.set()
            conv.wake.set()
        elif kind == "SessionIdleData":
            conv.idle.set()
            conv.wake.set()

    async def _send(self, conv: _Conv, prompt: str) -> None:
        conv.idle.clear()
        try:
            await conv.session.send(prompt)
        except Exception as exc:
            raise _error(exc) from exc

    # ------------------------------------------------------------------ one harness turn

    async def turn(self, req: Request, emit: Emit) -> Turn:
        root = (req.extra or {}).get("root")
        key = _conv_key(req)
        conv = self._convs.get(key)
        new = req.messages[conv.seen:] if conv and len(req.messages) >= conv.seen else None
        if conv is None or new is None or not new or (conv.pending and not _answers(new, conv.pending)):
            if conv is not None:
                await self._close(key)
            conv = _Conv()
            self._convs[key] = conv
            await self._open(conv, req, emit, root)
            await self._send(conv, as_prompt(req.messages))
        else:
            results = {b["id"]: b for m in new for b in (m.get("content") or []) if isinstance(b, dict)
                       and b.get("type") == "tool_result"}
            texts = [b.get("text") for m in new if m.get("role") == "user" for b in (m.get("content") or [])
                     if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
            if conv.pending:
                for cid, fut in list(conv.pending.items()):
                    r = results.get(cid)
                    if not fut.done():
                        fut.set_result((str(r.get("content") or "") if r else "keel did not run this call.",
                                        bool(r.get("is_error")) if r else True))
                conv.pending.clear()
            elif texts:
                await self._send(conv, "\n\n".join(texts))
        try:
            turn = await self._next(conv)
        except ProviderError:
            await self._close(key)
            raise
        conv.seen = len(req.messages) + 1          # the runner adds this turn as the next assistant message
        if turn.stop == END:
            await self._close(key)
        return turn

    async def _next(self, conv: _Conv) -> Turn:
        deadline = time.monotonic() + TURN_WAIT
        while True:
            if conv.batch:
                await asyncio.sleep(BATCH_WAIT)          # parallel calls of the same model turn come together
                calls, conv.batch = conv.batch, []
                for call, fut in calls:
                    conv.pending[call["id"]] = fut
                return self._take(conv, [c for c, _ in calls], TOOL_USE)
            if conv.idle.is_set():
                if conv.error:
                    raise _message_error(conv.error)
                return self._take(conv, [], END)
            left = deadline - time.monotonic()
            if left <= 0:
                raise ProviderError(f"{WHO} did not answer in {int(TURN_WAIT)} s.", retry=False,
                                    hint="Try again; if it keeps happening, pick another model on the GitHub Copilot row.")
            conv.wake.clear()
            if conv.batch or conv.idle.is_set():
                continue
            try:
                await asyncio.wait_for(conv.wake.wait(), timeout=min(left, 30))
            except TimeoutError:
                pass

    def _take(self, conv: _Conv, calls: list[dict], stop: str) -> Turn:
        blocks: list[dict] = []
        if conv.thinking:
            blocks.append({"type": "thinking", "text": "".join(conv.thinking)})
        text = "".join(conv.text) if conv.text else (conv.final or "")
        if stop == END and conv.final and len(conv.final) > len(text):
            text = conv.final
        if text.strip():
            blocks.append({"type": "text", "text": text})
        blocks.extend(calls)
        usage, model, note = conv.usage, conv.model or self.model, conv.note
        conv.text, conv.thinking, conv.final, conv.usage, conv.note = [], [], None, Usage(), ""
        return Turn(blocks=blocks, stop=stop, usage=usage, raw=None, model=model, note=note)

    async def _close(self, key: str) -> None:
        conv = self._convs.pop(key, None)
        if conv is None:
            return
        for fut in conv.pending.values():
            if not fut.done():
                fut.set_result(("The conversation ended.", True))
        if conv.session is not None:
            try:
                await conv.session.disconnect()
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        for key in list(self._convs):
            await self._close(key)


def _answers(new: list[dict], pending: dict) -> bool:
    ids = {b.get("id") for m in new for b in (m.get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_result"}
    return bool(ids & set(pending))


def _message_error(text: str) -> ProviderError:
    low = text.lower()
    if "not available" in low and "model" in low:
        return ProviderError(f"{WHO}: {text}", status=404, hint=HINT_MODEL)
    if any(w in low for w in ("unauthorized", "not authenticated", "authentication", "401", "403", "no copilot", "subscription")):
        return ProviderError(f"{WHO}: {text}", status=401, hint=HINT_TOKEN)
    if any(w in low for w in ("rate limit", "429", "quota", "premium request", "credits")):
        return ProviderError(f"{WHO}: {text}", status=429, retry=True,
                             hint="Your Copilot plan's limit for now is used up; wait, or use another model or an API key.")
    return ProviderError(f"{WHO}: {text}", retry=False)


def _error(exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    return _message_error(str(exc)[:500] or type(exc).__name__)


# tests run without the SDK: the fake client takes plain objects
class _FakeTool:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeResult:
    def __init__(self, text_result_for_llm: str = "", result_type: str = "success"):
        self.text_result_for_llm = text_result_for_llm
        self.result_type = result_type
