"""0.32 caching and the providers (docs/harness-parity.md): native tool search instead of a growing tool list, a chat's
1-hour cache, one cache key per session, cache writes and compaction counted, GitHub Copilot's own tool search, credits
and price multipliers, and the per-turn context line that says when the cache broke. No network."""

import asyncio
from types import SimpleNamespace

import anthropic
import httpx

from keel_engine.harness import runner as runner_mod
from keel_engine.harness.providers import copilot_sdk
from keel_engine.harness.providers.anthropic import AnthropicProvider, _usage, min_cache, searches_tools
from keel_engine.harness.providers.base import Request, ToolSpec, Usage
from keel_engine.harness.providers.openai_compat import OpenAICompatProvider
from tests.test_harness_providers import AClient, a_message

READ = ToolSpec("Read", "Read a file", {"type": "object", "properties": {}})
TASK = ToolSpec("Task", "A helper", {"type": "object", "properties": {}}, deferrable=True)
PLUGIN = ToolSpec("mcp__db__query", "Run SQL", {"type": "object", "properties": {}}, deferrable=True)


def user(t):
    return {"role": "user", "content": [{"type": "text", "text": t}]}


def test_anthropic_defers_with_its_tool_search_and_keeps_the_list_the_same():
    c = AClient([a_message([{"type": "text", "text": "ok"}], stop="end_turn")] * 2)
    p = AnthropicProvider("claude-sonnet-5-5", client=c)
    assert p.native_deferral and searches_tools("claude-haiku-4-5") and not searches_tools("claude-opus-4-1")
    for _ in range(2):
        asyncio.run(p.turn(Request(system="s", messages=[user("hi")], tools=[READ, TASK, PLUGIN]), lambda *a, **k: None))
    first, second = c.params
    assert first["tools"] == second["tools"]                                   # the list never changes: cache-safe
    names = {t["name"]: t for t in first["tools"]}
    assert "defer_loading" not in names["Read"] and names["Task"]["defer_loading"] and names["mcp__db__query"]["defer_loading"]
    assert names["tool_search_tool_bm25"]["type"] == "tool_search_tool_bm25_20251119"
    # only base tools: nothing to defer, no search tool
    c2 = AClient([a_message([{"type": "text", "text": "ok"}], stop="end_turn")])
    asyncio.run(AnthropicProvider("claude-sonnet-5-5", client=c2).turn(Request(system="s", messages=[], tools=[READ]), lambda *a, **k: None))
    assert [t["name"] for t in c2.params[0]["tools"]] == ["Read"]


def test_an_endpoint_without_tool_search_falls_back_to_every_tool_plainly():
    req400 = anthropic.BadRequestError("tool_search_tool_bm25_20251119 is not supported here",
                                       response=httpx.Response(400, request=httpx.Request("POST", "https://x")), body=None)
    c = AClient([req400, a_message([{"type": "text", "text": "ok"}], stop="end_turn")])
    p = AnthropicProvider("claude-sonnet-5-5", client=c)
    t = asyncio.run(p.turn(Request(system="s", messages=[user("hi")], tools=[READ, TASK]), lambda *a, **k: None))
    assert t.text() == "ok" and not p.native_deferral
    assert all("defer_loading" not in x for x in c.params[1]["tools"]) and len(c.params[1]["tools"]) == 2


def test_a_chat_caches_for_an_hour_a_step_for_five_minutes():
    c = AClient([a_message([{"type": "text", "text": "ok"}], stop="end_turn")] * 2)
    p = AnthropicProvider("claude-opus-5-5", client=c)
    asyncio.run(p.turn(Request(system="s", messages=[], tools=[], extra={"chat": True}), lambda *a, **k: None))
    asyncio.run(p.turn(Request(system="s", messages=[], tools=[]), lambda *a, **k: None))
    chat, step = c.params
    assert chat["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"} and chat["cache_control"]["ttl"] == "1h"
    assert step["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_compaction_on_the_server_is_counted_and_minimums_by_model():
    u = _usage(SimpleNamespace(input_tokens=100, output_tokens=10, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                               iterations=[{"type": "compaction", "input_tokens": 40_000, "output_tokens": 2_000},
                                           {"type": "message", "input_tokens": 100, "output_tokens": 10}]))
    assert u.compaction == 42_000 and u.input == 100
    assert (min_cache("claude-opus-5-5"), min_cache("claude-sonnet-4-5"), min_cache("claude-opus-4-7"),
            min_cache("claude-haiku-4-5")) == (512, 1024, 2048, 4096)


def test_openai_gets_one_cache_key_per_session_and_counts_cache_writes():
    p = OpenAICompatProvider("gpt-5.6", api_key="sk-x", who="OpenAI")
    assert p.params(Request(system="s", messages=[], tools=[], extra={"cache_key": "sess-1"}))["prompt_cache_key"] == "sess-1"
    local = OpenAICompatProvider("qwen", api_key="x", base_url="http://127.0.0.1:1/v1", who="the local server")
    assert "prompt_cache_key" not in local.params(Request(system="s", messages=[], tools=[], extra={"cache_key": "sess-1"}))
    assert p.min_cache_tokens == 1024 and local.min_cache_tokens == 0


def test_the_context_line_says_when_the_cache_broke_and_what_compaction_cost():
    parts = {"system": 300, "tools": 400, "tool_count": 5, "chat": 9000, "new": 50}
    ok = runner_mod.context_line(parts, Usage(input=50, cache_read=9600, output=20), prev_cached=9000)
    assert "cache break" not in ok and "9.6k read from it" in ok
    broke = runner_mod.context_line(parts, Usage(input=9700, cache_read=0, output=20), prev_cached=9000)
    assert "cache break: the last call read 9.0k from the cache, this one 0" in broke
    small = runner_mod.context_line(parts, Usage(input=700), min_cache=1024)
    assert "below this model's cache minimum (1.0k)" in small
    comp = runner_mod.context_line(parts, Usage(input=10, compaction=42_000, ai_credits=0.31))
    assert "compacted the conversation (42.0k tokens)" in comp and "about 0.31 AI credits" in comp
    # the cost: compaction at the input price, Copilot by its credits
    assert runner_mod._cost({"provider": "claude", "model": "opus"}, None, Usage(compaction=1_000_000)) > 0
    assert runner_mod._cost({"provider": "copilot", "model": "auto"}, None, Usage(ai_credits=50)) == 0.5


class _Session:
    def __init__(self, opts):
        self.opts, self.cb = opts, None

    def on(self, cb):
        self.cb = cb

    async def send(self, prompt):
        cb = self.cb

        async def play():
            await asyncio.sleep(0)
            ev = lambda d: SimpleNamespace(agent_id=None, data=d)  # noqa: E731
            usage = type("AssistantUsageData", (), {})()
            usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens = 900, 5, 800, 0
            usage.model, usage.copilot_usage = "m", SimpleNamespace(total_nano_aiu=120_000_000)
            comp = type("SessionCompactionCompleteData", (), {})()
            comp.compaction_tokens_used = SimpleNamespace(input_tokens=3000, output_tokens=200)
            msg = type("AssistantMessageDeltaData", (), {})()
            msg.delta_content = "ok"
            for d in (msg, usage, comp, type("SessionIdleData", (), {})()):
                cb(ev(d))
        asyncio.get_running_loop().create_task(play())

    async def disconnect(self):
        pass


class _Client:
    made: list = []

    def __init__(self, **kw):
        pass

    async def start(self):
        pass

    async def create_session(self, **opts):
        s = _Session(opts)
        _Client.made.append(s)
        return s

    async def list_models(self):
        return [SimpleNamespace(id="claude-sonnet-5.5", name="Claude Sonnet 5.5", supported_reasoning_efforts=["high"],
                                billing=SimpleNamespace(multiplier=9.0)),
                SimpleNamespace(id="gpt-5-mini", name="GPT-5 mini", supported_reasoning_efforts=None,
                                billing=SimpleNamespace(multiplier=0.0))]


def test_copilot_registers_every_tool_with_its_own_tool_search_and_counts_credits_and_compaction():
    copilot_sdk._clients.clear()
    copilot_sdk._live.clear()
    _Client.made = []
    p = copilot_sdk.CopilotProvider("auto", "gho_demo", factory=_Client)
    t = asyncio.run(p.turn(Request(system="s", messages=[user("hi")], tools=[READ, TASK, PLUGIN]), lambda *a, **k: None))
    opts = _Client.made[0].opts
    assert {x.name: x.defer for x in opts["tools"]} == {"Read": "never", "Task": "auto", "mcp__db__query": "auto"}
    assert opts["tool_search"] == {"enabled": True, "defer_threshold": 0}
    assert opts["available_tools"] == ["custom:*", "builtin:tool_search_tool"] and copilot_sdk.CopilotProvider.native_deferral
    assert t.usage.compaction == 3200 and round(t.usage.ai_credits, 3) == 0.12 and t.usage.input == 100


def test_copilot_models_show_the_plans_price_multiplier():
    from keel_engine.models import catalog

    copilot_sdk._models.clear()
    asyncio.run(copilot_sdk.list_models("gho_demo", factory=_Client, fresh=True))
    rows = {m["id"]: m for m in catalog.build_catalog()["copilot"]["modes"]["api"]}
    assert rows["claude-sonnet-5.5"]["badge"] == "×9" and rows["gpt-5-mini"]["badge"] == "×0 (included)"
    copilot_sdk._models_file().unlink(missing_ok=True)


def test_keels_summary_request_keeps_the_same_deferred_tools_so_it_reuses_the_cache():
    # 0.32 keel's own compaction asks for the summary with the conversation's system and tools (runner._compact): with
    # native tool search the request has the same tool list, defer_loading and the search tool, so nothing fails and the
    # cached start is the same; the fallback without tools has no deferral at all
    c = AClient([a_message([{"type": "text", "text": "ok"}], stop="end_turn")] * 2)
    p = AnthropicProvider("claude-sonnet-5-5", client=c)
    turn_req = Request(system="s", messages=[user("hi")], tools=[READ, TASK, PLUGIN])
    summary_req = Request(system="s", messages=[user("hi"), user("Write a summary")], tools=[READ, TASK, PLUGIN])
    a, b = p.params(turn_req), p.params(summary_req)
    assert a["tools"] == b["tools"] and a["system"] == b["system"]
    assert any(t.get("defer_loading") for t in b["tools"]) and any(t.get("name", "").startswith("tool_search") for t in b["tools"])
    plain = p.params(Request(system="s", messages=[user("hi"), user("Write a summary")], tools=[]))
    assert "tools" not in plain or not any(t.get("defer_loading") for t in plain.get("tools") or [])
