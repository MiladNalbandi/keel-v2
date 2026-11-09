"""API-key mode: a LangChain chat model with keel's guarded tools (and allowed MCP tools) in a tool loop."""

from __future__ import annotations

import json
import time

from ..tools import mcp
from . import catalog
from .base import AgentRequest, AgentResult, Emit, ModelError

MAX_TURNS = 40
GITHUB_MODELS_URL = "https://models.github.ai/inference"


def chat_model(provider: str, model: str, key: str | None, effort: str | None = None):
    # local and vertex: the "key" is the provider's connection as JSON (the api's SecretService.engineKeys)
    if provider == "local":
        from . import local

        return local.chat_model(key, model)
    if provider == "vertex":
        from . import vertex

        return vertex.chat_model(key, model, effort)
    if not key:
        raise ModelError(f"No API key for {provider}.", "Add the key in Connections, or set it in the engine's environment.")
    if provider == "claude":
        from langchain_anthropic import ChatAnthropic

        # effort: the Messages API's output_config.effort (low | medium | high | xhigh | max)
        kw = {"output_config": {"effort": effort}} if effort else {}
        return ChatAnthropic(model=catalog.CLAUDE_API_IDS.get(model, model), api_key=key, max_tokens=16000, timeout=600, **kw)
    from langchain_openai import ChatOpenAI

    if provider == "codex":
        kw = {"reasoning_effort": effort} if effort else {}
        # stream_usage: a streamed reply still says how many tokens it used
        return ChatOpenAI(model=model or "gpt-5.5", api_key=key, timeout=600, stream_usage=True, **kw)
    if provider == "copilot":
        return ChatOpenAI(model=model or "openai/gpt-5", api_key=key, base_url=GITHUB_MODELS_URL, timeout=600, stream_usage=True)
    raise ModelError(f"Provider {provider} has no API mode.")


def cost_usd(provider: str, model: str, key: str | None, tokens_in: int, tokens_out: int) -> float:
    """keel's price table; a local endpoint's own price when the person set one."""
    if provider == "local":
        from . import local

        p = local.price(key, model)
        if p is not None:
            return round((tokens_in * p["in"] + tokens_out * p["out"]) / 1e6, 6)
    return catalog.cost_usd(provider, model, tokens_in, tokens_out)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return str(content or "")


def _thinking(chunk) -> str:
    """0.18.23 the reasoning in a streamed chunk, where the provider sends it: Anthropic's `thinking` blocks, OpenAI's
    `reasoning` summaries, or `reasoning_content` (DeepSeek and others); "" when there is none. Never the answer."""
    out = []
    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "thinking" and c.get("thinking"):
                out.append(str(c["thinking"]))
            elif c.get("type") == "reasoning":
                summary = c.get("summary")
                if isinstance(summary, list):
                    out += [str(x.get("text", "")) for x in summary if isinstance(x, dict) and x.get("text")]
                elif c.get("reasoning"):
                    out.append(str(c["reasoning"]))
    extra = getattr(chunk, "additional_kwargs", None) or {}
    if isinstance(extra, dict) and isinstance(extra.get("reasoning_content"), str):
        out.append(extra["reasoning_content"])
    return "".join(out)


class APIRunner:
    @staticmethod
    async def _reply(bound, messages, emit: Emit):
        """One model reply, streamed: each text chunk goes out as emit("delta") while it is written; the chunks added
        up are the reply (its tool calls and usage too). A model that cannot stream answers in one piece."""
        full = None
        try:
            async for chunk in bound.astream(messages):
                full = chunk if full is None else full + chunk
                piece = _text(chunk.content)
                if piece:
                    emit("delta", piece)
                thought = _thinking(chunk)
                if thought:
                    emit("thinking_delta", thought)
        except NotImplementedError:
            full = None
        return full if full is not None else await bound.ainvoke(messages)

    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        provider, model = req.model["provider"], req.model.get("model", "")
        llm = chat_model(provider, model, req.key, catalog.effort_for(req.model))
        tools = req.toolbox.langchain_tools()
        mcp_tools, _client = await mcp.langchain_tools(req.mcp_specs, req.tools_allow)
        tools += mcp_tools
        system, history, prompt = req.system, list(req.history), req.prompt
        if provider == "local":
            tools, system, history, prompt = await self._local(req, emit, model, tools, system, history, prompt)
        by_name = {t.name: t for t in tools}
        bound = llm.bind_tools(tools) if tools else llm
        messages = [SystemMessage(system)] if system else []
        # a Helper session's earlier turns, so the model has the conversation (CLIs continue their own sessions)
        messages += [HumanMessage(t) if role == "user" else AIMessage(t) for role, t in history if t]
        messages.append(HumanMessage(prompt))
        tin = tout = 0
        text = ""
        for _ in range(MAX_TURNS):
            reply = await self._reply(bound, messages, emit)
            usage = getattr(reply, "usage_metadata", None) or {}
            tin += int(usage.get("input_tokens", 0))
            tout += int(usage.get("output_tokens", 0))
            messages.append(reply)
            said = _text(reply.content).strip()
            if said:
                emit("text", said)
                text = said
            calls = getattr(reply, "tool_calls", None) or []
            if not calls:
                break
            for call in calls:
                name, args = call["name"], call.get("args") or {}
                t0 = time.monotonic()
                tool = by_name.get(name)
                if not tool:
                    out = f"ERROR: unknown tool {name}"
                else:
                    try:
                        out = await tool.ainvoke(args)
                    except Exception as exc:  # report to the model, keep going
                        out = f"ERROR: {exc}"
                out = out if isinstance(out, str) else json.dumps(out, default=str)
                ms = int((time.monotonic() - t0) * 1000)
                self._step(emit, req, name, args, out, ms)
                messages.append(ToolMessage(content=out[:20000], tool_call_id=call["id"]))
        else:
            emit("error", f"Stopped after {MAX_TURNS} turns.")
        emit("answer", text[:20000])
        premium = 1 if provider == "copilot" else 0
        return AgentResult(text=text, tokens_in=tin, tokens_out=tout, cost_usd=cost_usd(provider, model, req.key, tin, tout),
                           premium_requests=premium)

    @staticmethod
    async def _local(req: AgentRequest, emit: Emit, model: str, tools: list, system: str, history: list, prompt: str):
        """A local model: tools only when it can call them (a code step is refused without), and the prompt packed
        into the server's context window."""
        import asyncio

        from . import local

        ep, name = local.split(local.parse(req.key), model)
        if tools and not await asyncio.to_thread(local.tool_support, ep, name):
            if not local.ask_type(req):
                raise local.refuse_without_tools(model, req.agent)
            tools = []
            emit("text", f"{model} cannot call tools: it answers from what is in the prompt.")
        window = await asyncio.to_thread(local.context_for, ep, name)
        system, history, prompt, trimmed = local.fit(system, history, prompt, window, local.tool_tokens(tools))
        if trimmed:
            emit("text", f"The prompt was shortened to fit {model}'s context window ({window} tokens).")
        return tools, system, history, prompt

    @staticmethod
    def _step(emit: Emit, req: AgentRequest, name: str, args: dict, out: str, ms: int):
        from .cli_runners import OUTPUT_LINES, READ_LINES, cap, diff_stat, head_lines

        refused = out.startswith("REFUSED")
        ok = not out.startswith("ERROR")
        if refused:
            emit("guard", out, tool=name, path=args.get("path"), ok=False, ms=ms,
                 command=str(args["command"])[:500] if args.get("command") else None)
            return
        if name == "write_file":
            w = req.toolbox.writes[-1] if req.toolbox.writes else {}
            diff = w.get("diff") or ""
            emit("write" if w.get("new") else "edit", f"Write {args.get('path')} ({diff_stat(diff)})", path=args.get("path"),
                 diff=cap(diff), ms=ms, ok=True)
        elif name == "read_file" and ok:
            emit("read", head_lines(out, READ_LINES), path=args.get("path"), ms=ms, ok=True)
        elif name.startswith("mcp__"):
            _, server, tool = (name.split("__", 2) + ["", ""])[:3]
            emit("tool", cap(json.dumps(args, ensure_ascii=False), 2000) if args else "", tool=tool, server=server,
                 output=head_lines(out, OUTPUT_LINES), ms=ms, ok=ok)
        else:
            if name in ("run_command", "run_tests"):
                ok = ok and out.startswith("exit 0")
            target = args.get("command") or args.get("path") or args.get("ac") or ""
            emit("tool", cap(str(target), 4000), tool="Bash" if name == "run_command" else name, path=args.get("path"),
                 output=head_lines(out, OUTPUT_LINES), ms=ms, ok=ok)
