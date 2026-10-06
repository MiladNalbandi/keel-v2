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
    if not key:
        raise ModelError(f"No API key for {provider}.", "Add the key in Connections, or set it in the engine's environment.")
    if provider == "claude":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=catalog.CLAUDE_API_IDS.get(model, model), api_key=key, max_tokens=16000, timeout=600)
    from langchain_openai import ChatOpenAI

    if provider == "codex":
        kw = {"reasoning_effort": effort} if effort else {}
        return ChatOpenAI(model=model or "gpt-5.5", api_key=key, timeout=600, **kw)
    if provider == "copilot":
        return ChatOpenAI(model=model or "openai/gpt-5", api_key=key, base_url=GITHUB_MODELS_URL, timeout=600)
    raise ModelError(f"Provider {provider} has no API mode.")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return str(content or "")


class APIRunner:
    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        provider, model = req.model["provider"], req.model.get("model", "")
        llm = chat_model(provider, model, req.key, req.model.get("effort"))
        tools = req.toolbox.langchain_tools()
        mcp_tools, _client = await mcp.langchain_tools(req.mcp_specs, req.tools_allow)
        tools += mcp_tools
        by_name = {t.name: t for t in tools}
        bound = llm.bind_tools(tools) if tools else llm
        messages = [SystemMessage(req.system)] if req.system else []
        # a Helper session's earlier turns, so the model has the conversation (CLIs continue their own sessions)
        messages += [HumanMessage(t) if role == "user" else AIMessage(t) for role, t in req.history if t]
        messages.append(HumanMessage(req.prompt))
        tin = tout = 0
        text = ""
        for _ in range(MAX_TURNS):
            reply = await bound.ainvoke(messages)
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
        return AgentResult(text=text, tokens_in=tin, tokens_out=tout, cost_usd=catalog.cost_usd(provider, model, tin, tout),
                           premium_requests=premium)

    @staticmethod
    def _step(emit: Emit, req: AgentRequest, name: str, args: dict, out: str, ms: int):
        from .cli_runners import OUTPUT_LINES, READ_LINES, cap, diff_stat, head_lines

        refused = out.startswith("REFUSED")
        ok = not out.startswith("ERROR")
        if refused:
            emit("guard", out, tool=name, path=args.get("path"), ok=False, ms=ms)
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
