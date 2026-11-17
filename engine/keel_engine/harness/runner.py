"""keel's own agent harness (issue #101): the loop that runs every agent of every flow, KeelBot and the Doctor, on the
model the person picked, through its API with their key. No coding-agent app runs headless any more.

    one agent call (AgentRequest)
      │ the session: new, or the one this step continued (harness/transcript.py)
      ▼
    ┌───────────── turn ─────────────┐
    │ the provider streams the reply │  text and thinking live (delta, thinking_delta)
    │ the turn is saved              │  append-only, so a crash loses nothing
    │ its tool calls run             │  keel's rules judge each (harness/tools.py)
    │ the results are saved          │
    └────────── until done ──────────┘
      ▼
    AgentResult: the answer, tokens (new, cached, out), cost

What it does when things go wrong, so one hiccup never kills a flow:
- a rate limit, an overloaded provider, a dropped connection: the turn is asked again, waiting longer each time;
- a tool that raises, an input that does not fit the schema, a call keel refuses: the model reads why and goes on;
- a reply cut off by the length limit: the model continues it; a cut-off tool input is never run;
- the same call with the same answer again and again: a note to change course, then the run stops (it is stuck);
- a conversation that grows near the model's window: older turns are summarized (server-side on current Claude models);
- too many turns, or the step's time limit: the run stops with its session kept, so the step can continue it.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import date

from ..models import catalog
from ..models.base import AgentRequest, AgentResult, Emit, ModelError
from . import context, providers, steps, transcript
from . import skills as skills_mod
from .providers.base import MAX_TOKENS, PAUSE, REFUSAL, ProviderError, Request, Turn, Usage
from .tools import ToolSet, cap, run_calls, sweep_outputs

MAX_TURNS = int(os.environ.get("KEEL_HARNESS_MAX_TURNS") or 150)
SUB_TURNS = 40                  # a Task helper
RETRIES = 6                     # tries of one turn on a passing error
CONTINUES = 3                   # a reply cut off by the length limit, continued
REPEAT_NOTE, REPEAT_STOP = 3, 6
COMPACT_AT = 0.85               # keel's own compaction: this share of the model's window (0.32; peers use 0.80–0.95)
KEEP_RECENT = 8                 # messages kept whole after a compaction (a flow step)
# 0.32 old tool results are cleared before anything is compacted (Anthropic's context editing, OpenCode's prune): the
# newest CLEAR_KEEP tokens of tool results stay whole, older ones become a one-line stub, and only in batches of at least
# CLEAR_MIN tokens, so the start of the request (the cache) changes seldom. The last CLEAR_KEEP_USES results always stay.
CLEAR_KEEP = int(os.environ.get("KEEL_CLEAR_KEEP") or 40_000)
CLEAR_MIN = int(os.environ.get("KEEL_CLEAR_MIN") or 20_000)
CHAT_CLEAR_KEEP, CHAT_CLEAR_MIN = 12_000, 6_000
CLEAR_KEEP_USES = 3
CHAT_TAIL = 16_000              # a chat's compaction keeps about this many recent tokens whole (from a question on)
RECENT_FILES = 5                # files the model read last, named again after a compaction
SUB_ANSWER = 8_000              # characters of a Task helper's answer (about 2k tokens); the rest goes to a file
# 0.31 KeelBot's chats compact much earlier: a chat sends its whole history again with every message, so a long one
# costs on every "hi". Older turns become a summary; the last CHAT_KEEP_TURNS questions and their answers stay whole.
CHAT_AGENTS = frozenset({"helper"})
CHAT_COMPACT_AT = int(os.environ.get("KEEL_CHAT_COMPACT_AT") or 40_000)
CHAT_SERVER_COMPACT_AT = 50_000    # Anthropic's server-side compaction does not trigger lower
CHAT_KEEP_TURNS = 1             # at least the last question stays whole
TODO_EVERY = 10                 # turns without TodoWrite before keel shows the open tasks again

GUIDE = """\
## How you work here (keel's harness)

You work in a software project through tools. keel's rules judge every tool call before it runs; a call answered with \
"REFUSED: …" was stopped by them: read why and change course (another file, another approach, or stop and say so). \
Never try to get around a refusal.

- Read a file before you change it. Prefer Edit (an exact, unique old_string) to rewriting a whole file with Write.
- Find code with Grep and Glob; read the parts that matter with Read (offset and limit for long files).
- Run commands with Bash and tests with RunTests. A command that fails tells you why: read it before you try again.
- Work in small, checked steps. Do not run the same failing call again unchanged.
- For work with several steps, keep a task list with TodoWrite and update it as you go.
- Hand a long search to Task (a helper that only reads) so your own context stays small.
- keel makes the commits and opens pull requests: you never commit or push unless your step says so.
- When you are done, end with a short answer: what you did, what you checked, what is left.
"""

SUB_SYSTEM = """\
You are a helper agent of keel. You read and search a software project to answer one question; you change nothing \
(writes and commands that change files are refused). Answer with what you found: file paths with line numbers, the \
facts that decide the question, and what you could not find. Be complete and short: at most about 40 lines (your \
answer goes into another agent's context).
"""


def _cost(model: dict, key: str | None, u: Usage) -> float:
    """keel's price table (cache writes about 1.25x, cache reads about a tenth of the input price); a local server's own
    price when the person set one."""
    provider, name = str(model.get("provider") or ""), str(model.get("model") or "")
    if provider == "local":
        from ..models import local

        p = local.price(key, name) or {"in": 0.0, "out": 0.0}
    else:
        p = catalog.price(provider, name)
    pin, pout = float(p.get("in", 0) or 0), float(p.get("out", 0) or 0)
    if u.ai_credits and not (pin or pout):
        return round(u.ai_credits / 100, 6)            # GitHub Copilot's own count: 1 AI credit = $0.01
    # 0.32 a compaction on the provider is billed too (counted here at the input price)
    return round((u.input * pin + u.cache_write * pin * 1.25 + u.cache_read * pin * 0.1 + u.output * pout
                  + u.compaction * pin) / 1e6, 6)


def _usage_dict(model: dict, key: str | None, u: Usage) -> dict:
    return {"tokens_in": u.input + u.cache_write, "tokens_cached": u.cache_read, "tokens_out": u.output,
            "cost_usd": _cost(model, key, u), "tokens_cache_write": u.cache_write, "tokens_compaction": u.compaction,
            **({"ai_credits": round(u.ai_credits, 4)} if u.ai_credits else {})}


def _tokens(messages: list[dict], system: str) -> int:
    """A rough count of what a request holds (four characters a token): enough to know when to compact."""
    return (len(system) + len(json.dumps(messages, ensure_ascii=False, default=str))) // 4


def _k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def context_parts(request: Request, tool_count: int) -> dict:
    """What one model call carries, in tokens (four characters a token): the system text, the tools, the earlier
    conversation and what is new since the model's last turn. Shown per turn in "Show what it did"."""
    msgs = request.messages
    last = max((i for i, m in enumerate(msgs) if m.get("role") == "assistant"), default=-1)
    dump = lambda x: json.dumps(x, ensure_ascii=False, default=str)  # noqa: E731
    tools = [{"name": t.name, "description": t.description, "input_schema": t.schema} for t in request.tools]
    return {"system": len(request.system) // 4, "tools": len(dump(tools)) // 4, "tool_count": tool_count,
            "chat": len(dump(msgs[:last + 1])) // 4 if last >= 0 else 0, "new": len(dump(msgs[last + 1:])) // 4}


def context_line(parts: dict, u: Usage, prev_cached: int = 0, min_cache: int = 0) -> str:
    sent = parts["system"] + parts["tools"] + parts["chat"] + parts["new"]
    line = (f"context: system {_k(parts['system'])} · tools {parts['tool_count']} ({_k(parts['tools'])}) · chat "
            f"{_k(parts['chat'])} · new {_k(parts['new'])} = about {_k(sent)}")
    if u.input or u.cache_read or u.cache_write:
        line += (f" · the provider counted {_k(u.input)} new + {_k(u.cache_write)} written to its cache + "
                 f"{_k(u.cache_read)} read from it, {_k(u.output)} out")
    # 0.32 cache health: what the cache did on this call, said where it went wrong
    if prev_cached >= 1000 and u.cache_read < prev_cached // 10:
        line += (f" · cache break: the last call read {_k(prev_cached)} from the cache, this one {_k(u.cache_read)} (the "
                 "start of the request changed, or the cache expired)")
    if min_cache and parts["system"] + parts["tools"] < min_cache and not u.cache_read:
        line += f" · the fixed start is below this model's cache minimum ({_k(min_cache)}): only a longer chat is cached"
    if u.compaction:
        line += f" · the provider compacted the conversation ({_k(u.compaction)} tokens)"
    if u.ai_credits:
        line += f" · about {u.ai_credits:.2f} AI credits"
    return line


def _env(req: AgentRequest) -> str:
    return (f"<environment>\nProject folder: {req.root}\nToday: {date.today().isoformat()}\nPlatform: linux\n"
            "</environment>")


class HarnessRunner:
    """The Runner of every real model (models/__init__.py runner_for); the fake model keeps its own."""

    def __init__(self, provider=None):
        self._provider = provider          # tests: a provider made already
        self._chat = False                 # a KeelBot chat (CHAT_AGENTS): earlier compaction

    async def _resolve(self, req: AgentRequest):
        if self._provider is not None:
            return self._provider
        try:
            return await asyncio.to_thread(providers.resolve, req.model, req.key)
        except ProviderError as exc:
            raise ModelError(str(exc), exc.hint) from exc

    # ------------------------------------------------------------------ one turn, with retries

    async def _turn(self, provider, request: Request, emit: Emit, used: Usage, model: dict, key: str | None) -> Turn:
        for attempt in range(1, RETRIES + 1):
            try:
                return await provider.turn(request, emit)
            except ProviderError as exc:
                if not exc.retry or attempt == RETRIES:
                    err = ModelError(str(exc), exc.hint, usage=_usage_dict(model, key, used))
                    raise err from exc
                wait = exc.after if exc.after is not None else min(60.0, 2 ** attempt + random.uniform(0, 1))
                emit("text", f"{exc} Trying again in {wait:.0f} s (try {attempt + 1} of {RETRIES}).")
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ keel's own compaction

    def _clear_old_results(self, session: transcript.Session, emit: Emit) -> None:
        """Old tool results become a one-line stub (see CLEAR_KEEP): the model can run the tool again when it needs one."""
        keep_tokens, minimum = (CHAT_CLEAR_KEEP, CHAT_CLEAR_MIN) if self._chat else (CLEAR_KEEP, CLEAR_MIN)
        cleared = clear_old_results(session.messages, keep_tokens, minimum)
        if cleared:
            session.clear_results(cleared)
            emit("text", f"keel cleared {len(cleared)} old tool result(s) to keep the context small (the model can run "
                         "those tools again).")

    async def _compact(self, provider, session: transcript.Session, system: str, window: int, emit: Emit,
                       used: Usage, model: dict, key: str | None, server_side: bool, tools: ToolSet | None = None) -> None:
        """When the conversation passes COMPACT_AT of the window (a chat: CHAT_COMPACT_AT): the model summarizes it, and
        the session starts again from that summary and the recent messages. One owner: when Anthropic compacts on the
        server (current Claude models), keel does not."""
        chat = self._chat
        if server_side or (not chat and len(session.messages) <= KEEP_RECENT + 2) or len(session.messages) < 3:
            return
        if _tokens(session.view(), system) < (min(CHAT_COMPACT_AT, window * COMPACT_AT) if chat else window * COMPACT_AT):
            return
        cut = chat_cut(session.messages, CHAT_TAIL) if chat else len(session.messages) - KEEP_RECENT
        if cut <= 0:
            return
        # keep from a message the model can start from: a user message with text (not only tool results)
        while cut < len(session.messages) and not (session.messages[cut]["role"] == "user" and
                                                  any(b["type"] == "text" for b in session.messages[cut]["content"])):
            cut += 1
        if cut >= len(session.messages):
            cut = len(session.messages)
        ask = {"role": "user", "content": [{"type": "text", "text": SUMMARY_ASK}]}
        # 0.32 the same system and tools as the conversation, the ask appended: the summary call reads the cached start
        specs = tools.specs() if tools is not None else []
        summary_turn = await self._turn(provider, Request(system=system, messages=session.view() + [ask], tools=specs),
                                        lambda *a, **k: None, used, model, key)
        used.add(summary_turn.usage)
        text = summary_turn.text()
        if not text.strip():
            # it called a tool instead of writing: ask again without tools (no cache, but a summary)
            summary_turn = await self._turn(provider, Request(system=system, messages=session.view() + [ask], tools=[]),
                                            lambda *a, **k: None, used, model, key)
            used.add(summary_turn.usage)
            text = summary_turn.text()
        text += progress_note(session, tools)
        session.compact(text, session.messages[cut:])
        emit("text", f"The conversation got long: keel summarized its older part ({cut} messages) and goes on from there.")

    # ------------------------------------------------------------------ the loop

    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult:
        provider = await self._resolve(req)
        self._chat = req.agent in CHAT_AGENTS
        await asyncio.to_thread(transcript.sweep)
        await asyncio.to_thread(sweep_outputs)
        session, fresh = self._session(req, provider, emit)
        used = Usage()
        system = (req.system.rstrip() + "\n\n" + GUIDE) if req.system else GUIDE
        tools = await self._tools(req, session, provider, used, emit)
        web = "WebSearch" in self._web(req) and provider.name == "anthropic"
        effort = catalog.effort_for(req.model)
        window = self._window(req)
        server_side = provider.name == "anthropic" and _server_compacts(provider.model)
        extra = {"compact_at": min(CHAT_SERVER_COMPACT_AT if self._chat else 150_000, int(window * 0.6)), "root": req.root,
                 "chat": self._chat, "cache_key": session.id}      # 0.32 a chat's 1-hour cache; one cache key a session

        if fresh and req.history:
            for role, text in req.history:
                if text:
                    session.add("user" if role == "user" else "assistant", [{"type": "text", "text": text}])
        start = await asyncio.to_thread(context.session_start, req.toolbox) if fresh else ""
        session.add("user", [{"type": "text", "text": (start + "\n\n" + req.prompt) if start else req.prompt}])
        try:
            return await self._loop(req, emit, provider, session, used, system, tools, web, effort, window, server_side, extra)
        finally:
            tools.close()           # no background command outlives the agent call
            close = getattr(provider, "close", None)
            if close is not None and self._provider is None:
                await close()       # a provider with a live session (GitHub Copilot) ends it with the agent call

    async def _loop(self, req, emit, provider, session, used, system, tools, web, effort, window, server_side, extra):
        model = req.model

        deadline = time.monotonic() + max(60, int(req.timeout or 1800))
        answer, continues, repeats, last, turns_since_todo = "", 0, 0, None, 0
        prev_cached = 0
        limit = self._turns(req)
        for _ in range(limit):
            if time.monotonic() > deadline:
                err = ModelError(f"The step's time limit ({req.timeout} s) ran out; its session is kept, so it can continue.",
                                 usage=_usage_dict(model, req.key, used))
                err.max_turns = True
                raise err
            self._clear_old_results(session, emit)
            await self._compact(provider, session, system, window, emit, used, model, req.key, server_side, tools)
            specs = tools.specs()
            request = Request(system=system, messages=session.view(), tools=specs, effort=effort,
                              web_search=web, extra=extra)
            parts = context_parts(request, len(specs))
            turn = await self._turn(provider, request, emit, used, model, req.key)
            used.add(turn.usage)
            # the run's tokens and cost so far, live; and what this call carried, part by part
            emit("usage", **_usage_dict(model, req.key, used), context={**parts, "cached": turn.usage.cache_read})
            emit("context", context_line(parts, turn.usage, prev_cached, int(getattr(provider, "min_cache_tokens", 0) or 0)),
                 **parts, cached=turn.usage.cache_read, cache_write=turn.usage.cache_write, compaction=turn.usage.compaction)
            prev_cached = turn.usage.cache_read
            session.add("assistant", turn.blocks, api=provider.name, model=provider.model, raw=turn.raw)
            turns_since_todo += 1
            for b in turn.blocks:
                if b["type"] == "thinking" and b.get("text", "").strip():
                    emit("thinking", steps.cap(b["text"]))
                elif b["type"] == "text" and b.get("text", "").strip():
                    emit("text", steps.cap(b["text"]))
            if turn.note:
                emit("text", turn.note)
            if turn.stop == REFUSAL:
                emit("error", turn.note)
                raise ModelError(turn.note or "The model declined this request.", usage=_usage_dict(model, req.key, used))
            if turn.stop == PAUSE:
                continue                     # a server tool paused the turn: ask again, the same conversation
            calls = turn.tool_calls()
            if not calls:
                answer = turn.text() or answer
                if turn.stop == MAX_TOKENS and continues < CONTINUES:
                    continues += 1
                    session.add("user", [{"type": "text", "text": "Your answer was cut off by the length limit. Go on "
                                                                  "exactly where it stopped."}])
                    continue
                break
            if turn.stop == MAX_TOKENS:
                # a tool input cut off at the length limit parses as a valid but partial object: never run it
                session.add("user", [{"type": "tool_result", "id": c["id"], "is_error": True,
                                      "content": "Your input was cut off by the length limit and was not run. Do it in "
                                                 "smaller parts (several Edits instead of one large Write)."} for c in calls])
                continue
            n_writes = len(req.toolbox.writes)
            outs: dict[str, str] = {}

            def done(call: dict, out: str, ms: int) -> None:
                outs[call["id"]] = out
                steps.tool_step(emit, req.toolbox, call, out, ms, n_writes)

            tools.note_calls(calls)          # a deferred tool called straight away: it runs, and is loaded from now on
            results = await run_calls(tools.tools, calls, done)
            key = json.dumps([(c["name"], c.get("input")) for c in calls], sort_keys=True, default=str) + \
                json.dumps([outs.get(c["id"]) for c in calls], default=str)
            repeats = repeats + 1 if key == last else 1
            last = key
            if repeats >= REPEAT_STOP:
                session.add("user", results)
                emit("error", f"The agent made the same call {repeats} times with the same answer: keel stopped it.")
                raise ModelError(f"The agent was stuck: the same tool call {repeats} times with the same answer.",
                                 "Look at the step's last calls; a clearer step or another model may help.",
                                 usage=_usage_dict(model, req.key, used))
            if repeats >= REPEAT_NOTE:
                results[-1]["content"] += (f"\n\nNOTE from keel: you made this same call {repeats} times and got the same "
                                           "answer. Do something different, or stop and say what blocks you.")
            if any(c["name"] == "TodoWrite" for c in calls):
                turns_since_todo = 0
            notes = self._reminders(tools, session, turns_since_todo)
            if notes:
                results[-1]["content"] += "\n\n" + notes
                if turns_since_todo >= TODO_EVERY:
                    turns_since_todo = 0
            session.add("user", results)
        else:
            err = ModelError(f"The agent used all {limit} turns of this step; its session is kept, so it can continue.",
                             usage=_usage_dict(model, req.key, used))
            err.max_turns = True
            raise err
        emit("answer", steps.cap(answer))
        u = _usage_dict(model, req.key, used)
        return AgentResult(text=answer, tokens_in=u["tokens_in"], tokens_cached=u["tokens_cached"], tokens_out=u["tokens_out"],
                           cost_usd=u["cost_usd"], data={"session": session.id,
                                                         **({"ai_credits": u["ai_credits"]} if u.get("ai_credits") else {})})

    # ------------------------------------------------------------------ pieces

    def _session(self, req: AgentRequest, provider, emit: Emit) -> tuple[transcript.Session, bool]:
        """(the session, whether it is new). A step continues its session when the runtime asks (resume); a session
        that is gone starts again, and the agent is told."""
        meta = {"provider": req.model.get("provider"), "model": provider.model, "root": req.root, "agent": req.agent,
                "thread": req.thread}
        if req.resume and req.session:
            found = transcript.Session.load(req.session)
            if found and found.messages:
                return found, False
            emit("text", "Could not continue the earlier session for this step; starting a new one.")
        sid = req.session if req.session and not req.resume and transcript.Session.load(req.session) is None else None
        s = transcript.Session.open(sid, **meta)
        if s.id != req.session and req.on_session:
            req.on_session(s.id)
        return s, True

    @staticmethod
    def _turns(req: AgentRequest) -> int:
        """The agent file's maxTurns (explorer: 20) when it has one, never more than MAX_TURNS."""
        from ..runtime import prompts

        try:
            n = prompts.max_turns(req.agent, req.phase)
        except Exception:  # noqa: BLE001 - an agent without a file takes keel's limit
            n = None
        return max(1, min(int(n), MAX_TURNS)) if n else MAX_TURNS

    @staticmethod
    def _web(req: AgentRequest) -> list[str]:
        from ..runtime import prompts

        try:
            return prompts.web_tools(req.agent)
        except Exception:  # noqa: BLE001 - an agent without a file has no web tools
            return []

    def _window(self, req: AgentRequest) -> int:
        provider, name = str(req.model.get("provider") or ""), str(req.model.get("model") or "")
        if provider == "local":
            from ..models import local

            try:
                ep, real = local.split(local.parse(req.key), name)
                return local.context_for(ep, real)
            except Exception:  # noqa: BLE001
                return catalog.context_window(provider, name)
        return catalog.context_window(provider, name)

    async def _tools(self, req: AgentRequest, session: transcript.Session, provider, used: Usage, emit: Emit) -> ToolSet:
        from .. import hook, rules
        from ..tools import mcp

        box = req.toolbox
        mcp_tools: list = []
        if req.mcp_specs:
            try:
                mcp_tools, _client = await mcp.langchain_tools(req.mcp_specs, req.tools_allow)
            except Exception as exc:  # noqa: BLE001 - a server that does not start costs its tools, not the run
                emit("text", f"An MCP server did not start, so its tools are missing: {str(exc)[:200]}")

        def mcp_check(name: str, args: dict) -> str | None:
            def edit(path: str) -> str | None:
                r = box._resolve(path)
                if not r:
                    return f"{path} is outside the project."
                v = rules.check_edit(box.phase, r[1], box.cfg, exists=r[0].exists(), lane=box.lane, unlocks=box.unlocks)
                return None if v.ok else v.reason

            return hook.check_mcp(name, args, box.phase, box.cfg, edit)

        def todos(items: list[dict]) -> None:
            session.set_todos(items)
            emit("text", steps.todo_text(items))

        async def task(description: str, prompt: str, kind: str | None = None) -> str:
            return await self._helper(req, provider, description, prompt, used, emit, kind)

        found_skills = await asyncio.to_thread(skills_mod.find_skills, box.root)
        agents = await asyncio.to_thread(skills_mod.agent_types)
        # 0.31 deferred tools for every provider (GitHub Copilot adds a loaded tool to its live session: dynamic_tools)
        # 0.32 a provider with its own tool search (Anthropic, Copilot) gets every tool from the start, the deferrable ones
        # marked: the list never changes. The others (OpenAI-compatible) get keel's LoadTools.
        lazy = not getattr(provider, "native_deferral", False) and bool(
            getattr(provider, "dynamic_tools", getattr(provider, "name", "") != "copilot"))
        return ToolSet(box, web=self._web(req), todos=todos, task=task, mcp=mcp_tools, mcp_check=mcp_check,
                       skills=found_skills, agents=agents, lazy=lazy, loaded=session.tools, on_load=session.set_tools)

    @staticmethod
    def _reminders(tools: ToolSet, session: transcript.Session, turns_since_todo: int) -> str:
        """What the model should know before its next turn: files it read that changed under it, and its open tasks when
        it has not looked at its list for a while."""
        out = []
        changed = tools.changed_since_read()
        if changed:
            out.append("NOTE from keel: these files changed since you read them (not by your own Write or Edit): "
                       + ", ".join(changed[:20]) + ". Read them again before you rely on them.")
        open_items = [t for t in session.todos if t.get("status") != "completed"]
        if open_items and turns_since_todo >= TODO_EVERY:
            out.append("NOTE from keel: your task list still has open items:\n" + "\n".join(
                f"- [{'~' if t.get('status') == 'in_progress' else ' '}] {t.get('content')}" for t in open_items[:20])
                + "\nKeep it current with TodoWrite.")
        return "\n\n".join(out)

    async def _helper(self, req: AgentRequest, provider, description: str, prompt: str, used: Usage, emit: Emit,
                      kind: str | None = None) -> str:
        """Task: a helper in its own short conversation; its answer is the tool's result, its tokens count for the step.
        Without a kind it only reads. With one it works as that keel agent: its role, its turn limit, and it may write
        only when that agent's file gives it Write or Edit and the step itself may write."""
        from ..tools.agent_tools import ToolBox

        box = req.toolbox
        agents = await asyncio.to_thread(skills_mod.agent_types) if kind else {}
        agent = agents.get(kind) if kind else None
        if kind and agent is None:
            return f"ERROR: there is no agent type {kind}. Types: {', '.join(sorted(agents))}."
        writes = bool(agent and agent.writes and not box.readonly)
        sub_box = ToolBox(box.root, box.phase, cfg=box.cfg, lane=box.lane, agent=kind or box.agent, knowledge=box.knowledge,
                          readonly=not writes, on_refuse=box.on_refuse, unlocks=box.unlocks)
        sub_box.others, sub_box.others_off = box.others, box.others_off
        sub_box.push_blockers, sub_box.flow = box.push_blockers, box.flow
        if not writes:
            sub_box.readonly_label = "a Task helper"
            sub_box.readonly_note = "It only reads: say in your answer what should change instead."
        found_skills = await asyncio.to_thread(skills_mod.find_skills, box.root)
        tools = ToolSet(sub_box, web=[t for t in self._web(req) if t == "WebFetch"], skills=found_skills)
        system = (agent.role.rstrip() + "\n\n" + GUIDE) if agent else SUB_SYSTEM
        limit = min(agent.max_turns or SUB_TURNS, SUB_TURNS * 2) if agent else SUB_TURNS
        emit("tool", steps.cap(description, 400), tool="Task" + (f"·{kind}" if kind else ""), output="started", ok=True)

        def quiet(kind: str, text: str = "", **extra) -> None:
            if kind in ("read", "tool", "guard"):
                emit(kind, text, **{**extra, "tool": f"Task·{extra.get('tool') or kind}"})

        messages = [{"role": "user", "content": [{"type": "text", "text": _env(req) + "\n\n" + prompt}]}]
        try:
            return await self._helper_loop(req, provider, tools, sub_box, messages, system, limit, used, quiet)
        finally:
            tools.close()

    async def _helper_loop(self, req, provider, tools, sub_box, messages, system, limit, used, quiet) -> str:
        answer = ""
        for _ in range(limit):
            turn = await self._turn(provider, Request(system=system, messages=list(messages), tools=tools.specs(), effort="low"),
                                    lambda *a, **k: None, used, req.model, req.key)
            used.add(turn.usage)
            messages.append({"role": "assistant", "content": turn.blocks, "api": provider.name, "model": provider.model,
                             "raw": turn.raw})
            calls = turn.tool_calls()
            if not calls or turn.stop in (REFUSAL, MAX_TOKENS):
                answer = turn.text()
                break
            results = await run_calls(tools.tools, calls, lambda c, out, ms: steps.tool_step(quiet, sub_box, c, out, ms, 0))
            messages.append({"role": "user", "content": results})
        # 0.32 a helper answers in about 2k tokens; a longer answer is cut, and saved whole for the model to read
        return cap(answer, SUB_ANSWER) if answer else "The helper found nothing it could report."


def _server_compacts(model: str) -> bool:
    from .providers.anthropic import compacts

    return compacts(model)


# ---------------------------------------------------------------- 0.32 context management (pure: tested directly)

SUMMARY_ASK = """Write a summary of this session so far for yourself, so you can go on from it alone. Do not call tools.
Use exactly these sections (leave one out when it has nothing):
## Goal
What the person or the step asked for, in their words where they matter.
## Instructions
Rules and preferences the person gave, and constraints of the step.
## Discoveries
What you found: facts, causes, where things are (paths with line numbers).
## Files
Every file you changed and how; the files that matter and why.
## Decisions
What was decided and why; what was tried and did not work.
## Done
What is finished and checked (commands run and their result).
## Next
What is left, in order.
Keep paths, names, numbers and error messages exact."""


def _result_tokens(block: dict) -> int:
    c = block.get("content")
    return (len(c) if isinstance(c, str) else len(json.dumps(c, default=str))) // 4


def _call_of(messages: list[dict], upto: int, cid: str) -> dict | None:
    for m in reversed(messages[:upto]):
        if m["role"] == "assistant":
            for b in m.get("content") or []:
                if b.get("type") == "tool_call" and b.get("id") == cid:
                    return b
            return None
    return None


def _stub(call: dict | None, block: dict) -> str:
    name = (call or {}).get("name") or "a tool"
    args = (call or {}).get("input") or {}
    what = next((str(args[k]) for k in ("file_path", "path", "pattern", "command", "url", "query") if args.get(k)), "")
    c = block.get("content")
    lines = (c.count("\n") + 1) if isinstance(c, str) else 0
    return (f"[keel cleared this old result to keep the context small: {name}"
            f"{' ' + what[:160] if what else ''}{f' ({lines} lines)' if lines else ''}. Run it again if you need it.]")


CLEARED = "[keel cleared this old result"


def clear_old_results(messages: list[dict], keep_tokens: int, minimum: int, keep_uses: int = CLEAR_KEEP_USES) -> dict[str, str]:
    """{tool result id: stub} for the old tool results to clear: the newest keep_tokens of results (and the last
    keep_uses results) stay; nothing is cleared unless at least `minimum` tokens go (so the request's start changes
    seldom). Errors and results with images are small or needed: they stay."""
    found = []                                    # (message index, block) of every tool result, oldest first
    for i, m in enumerate(messages):
        if m["role"] != "user":
            continue
        for b in m.get("content") or []:
            if b.get("type") == "tool_result" and not str(b.get("content") or "").startswith(CLEARED):
                found.append((i, b))
    kept, total, out = 0, 0, {}
    candidates = []
    for n, (i, b) in enumerate(reversed(found)):
        t = _result_tokens(b)
        if n < keep_uses or kept + t <= keep_tokens or b.get("is_error") or b.get("images"):
            kept += t
            continue
        candidates.append((i, b, t))
        total += t
    if total < minimum:
        return {}
    for i, b, _t in candidates:
        out[b["id"]] = _stub(_call_of(messages, i, b["id"]), b)
    return out


def chat_cut(messages: list[dict], tail_tokens: int) -> int:
    """Where a chat's compaction cuts: the messages from there on (about tail_tokens, from a question on) stay whole."""
    asks = [i for i, m in enumerate(messages) if m["role"] == "user" and any(b.get("type") == "text" for b in m["content"])]
    if len(asks) <= CHAT_KEEP_TURNS:
        return 0
    size = 0
    cut = asks[-CHAT_KEEP_TURNS]
    for i in range(len(messages) - 1, -1, -1):
        size += len(json.dumps(messages[i].get("content"), default=str)) // 4
        if size > tail_tokens:
            break
        if i in asks:
            cut = min(cut, i)
    return cut if cut > asks[0] else (asks[1] if len(asks) > 1 else 0)


def progress_note(session: transcript.Session, tools: ToolSet | None) -> str:
    """What keel itself knows of the work (it survives a compaction exactly): the task list, the files changed, the files
    read last, and the project's notes."""
    parts = []
    if session.todos:
        parts.append(steps.todo_text(session.todos))
    box = getattr(tools, "box", None)
    changed = list(dict.fromkeys(w.get("path") for w in (getattr(box, "writes", None) or []) if w.get("path")))
    if changed:
        parts.append("Files changed so far: " + ", ".join(changed[:40]))
    read = sorted((getattr(tools, "read_at", None) or {}).keys(), key=lambda r: -(tools.read_at.get(r) or 0))[:RECENT_FILES]
    if read:
        parts.append("Files read last (read them again before you rely on them): " + ", ".join(read))
    if box is not None:
        try:
            notes = context.project_notes(box)
        except Exception:  # noqa: BLE001 - the notes are a help, never a reason to fail a compaction
            notes = ""
        if notes:
            parts.append(notes)
    return ("\n\n## Progress kept by keel\n\n" + "\n\n".join(parts)) if parts else ""
