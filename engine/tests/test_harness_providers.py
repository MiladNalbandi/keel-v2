"""#101 the harness's two provider adapters, with the SDKs' own response types and a stand-in client (no network):
what each sends (caching, thinking, effort, compaction, fallback, tools) and how it reads a streamed answer back."""

import asyncio
from types import SimpleNamespace

import anthropic
import openai
import pytest
from anthropic.types.beta import BetaMessage
from openai.types.chat import ChatCompletionChunk

from keel_engine.harness.providers import api_model, resolve
from keel_engine.harness.providers.anthropic import AnthropicProvider, _error, adaptive, compacts, falls_back, to_messages
from keel_engine.harness.providers.base import END, MAX_TOKENS, REFUSAL, TOOL_USE, ProviderError, Request, ToolSpec
from keel_engine.harness.providers.openai_compat import OpenAICompatProvider, tool_name
from keel_engine.harness.providers.openai_compat import to_messages as oa_messages

TOOLS = [ToolSpec("Read", "Read a file", {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]})]


# ------------------------------------------------------------------ Anthropic

class AStream:
    def __init__(self, events, final):
        self.events, self.final = events, final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for e in self.events:
                yield e
        return gen()

    async def get_final_message(self):
        return self.final


class AClient:
    def __init__(self, finals, events=None):
        self.finals, self.events, self.params = list(finals), events or [], []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **params):
        self.params.append(params)
        f = self.finals.pop(0)
        if isinstance(f, Exception):
            raise f
        return AStream(self.events, f)


def a_message(content, stop="tool_use", model="claude-opus-5-5"):
    return BetaMessage.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": content, "stop_reason": stop,
        "stop_sequence": None, "usage": {"input_tokens": 120, "output_tokens": 30, "cache_creation_input_tokens": 1000,
                                         "cache_read_input_tokens": 4000}})


def test_anthropic_sends_caching_thinking_effort_compaction_and_fallback():
    c = AClient([a_message([{"type": "text", "text": "hi"}], stop="end_turn")])
    p = AnthropicProvider("claude-opus-5-5", client=c)
    req = Request(system="SYS", messages=[{"role": "user", "content": [{"type": "text", "text": "go"}]}], tools=TOOLS,
                  effort="xhigh", web_search=True, extra={"compact_at": 120_000})
    asyncio.run(p.turn(req, lambda *a, **k: None))
    sent = c.params[0]
    assert sent["model"] == "claude-opus-5-5" and sent["max_tokens"] == 64_000
    assert sent["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert sent["cache_control"] == {"type": "ephemeral"}
    assert sent["thinking"] == {"type": "adaptive", "display": "summarized"} and sent["output_config"] == {"effort": "xhigh"}
    assert sent["tools"][0] == {"name": "Read", "description": "Read a file", "input_schema": TOOLS[0].schema,
                                "eager_input_streaming": True}
    assert sent["tools"][1] == {"type": "web_search_20260209", "name": "web_search"}
    assert sent["betas"] == ["compact-2026-01-12", "server-side-fallback-2026-07-01"] and sent["fallbacks"] == "default"
    assert sent["context_management"]["edits"][0] == {"type": "compact_20260112",
                                                      "trigger": {"type": "input_tokens", "value": 120_000}}


def test_anthropic_reads_a_streamed_turn_with_thinking_and_tool_calls():
    events = [SimpleNamespace(type="thinking", thinking="Let me look"), SimpleNamespace(type="text", text="Reading.")]
    final = a_message([{"type": "thinking", "thinking": "Let me look", "signature": "sig"},
                       {"type": "text", "text": "Reading."},
                       {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "a.py"}}])
    seen = []
    t = asyncio.run(AnthropicProvider("claude-opus-5-5", client=AClient([final], events)).turn(
        Request(system="s", messages=[], tools=TOOLS), lambda *a, **k: seen.append(a)))
    assert seen == [("thinking_delta", "Let me look"), ("delta", "Reading.")]
    assert t.stop == TOOL_USE and t.blocks == [{"type": "thinking", "text": "Let me look"}, {"type": "text", "text": "Reading."},
                                               {"type": "tool_call", "id": "toolu_1", "name": "Read", "input": {"file_path": "a.py"}}]
    assert (t.usage.input, t.usage.cache_write, t.usage.cache_read, t.usage.output) == (120, 1000, 4000, 30)
    assert t.raw[0] == {"type": "thinking", "thinking": "Let me look", "signature": "sig"}     # sent back unchanged


def test_anthropic_history_goes_back_unchanged_to_the_same_model_only():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "go"}]},
            {"role": "assistant", "api": "anthropic", "model": "claude-opus-5-5",
             "raw": [{"type": "thinking", "thinking": "t", "signature": "s"}, {"type": "tool_use", "id": "x", "name": "Read", "input": {}}],
             "content": [{"type": "thinking", "text": "t"}, {"type": "tool_call", "id": "x", "name": "Read", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "id": "x", "content": "file", "is_error": False}]}]
    same = to_messages(msgs, "claude-opus-5-5")
    assert same[1]["content"][0]["type"] == "thinking" and same[1]["content"][0]["signature"] == "s"
    assert same[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "file", "is_error": False}]}
    other = to_messages(msgs, "claude-sonnet-5-5")          # another model: its text and tool calls only, no thinking
    assert other[1]["content"] == [{"type": "tool_use", "id": "x", "name": "Read", "input": {}}]


def test_anthropic_capabilities_by_model():
    assert adaptive("claude-opus-5-5") and adaptive("claude-sonnet-4-6") and adaptive("claude-haiku-5-5")
    assert not adaptive("claude-haiku-4-5") and not compacts("claude-haiku-4-5")
    assert falls_back("claude-sonnet-5-5") and not falls_back("claude-haiku-5-5")
    p = AnthropicProvider("claude-haiku-4-5", client=AClient([]))
    sent = p.params(Request(system="s", messages=[], tools=[]))
    assert "thinking" not in sent and "betas" not in sent and "tools" not in sent


def test_anthropic_refusal_fallback_and_errors_are_named():
    refused = a_message([], stop="refusal")
    t = asyncio.run(AnthropicProvider("claude-opus-5-5", client=AClient([refused])).turn(
        Request(system="s", messages=[], tools=[]), lambda *a, **k: None))
    assert t.stop == REFUSAL and t.note.startswith("The model declined this request")
    served = asyncio.run(AnthropicProvider("claude-opus-5-5", client=AClient([a_message([{"type": "text", "text": "x"}], stop="end_turn",
                                                                                          model="claude-opus-4-8")])).turn(
        Request(system="s", messages=[], tools=[]), lambda *a, **k: None))
    assert served.note == "claude-opus-5-5 declined; claude-opus-4-8 answered this turn." and served.stop == END
    import httpx2

    def resp(code, headers=None):
        return httpx2.Response(code, headers=headers or {}, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))

    rl = _error(anthropic.RateLimitError("slow down", response=resp(429, {"retry-after": "7"}), body=None))
    assert rl.retry and rl.after == 7.0 and rl.status == 429
    auth = _error(anthropic.AuthenticationError("bad key", response=resp(401), body=None))
    assert not auth.retry and "Settings › Models & logins" in auth.hint
    over = _error(anthropic.InternalServerError("overloaded", response=resp(529), body=None))
    assert over.retry
    bad = _error(anthropic.BadRequestError("no", response=resp(400), body=None))
    assert not bad.retry
    c = AClient([ValueError("Unable to parse tool input")])
    with pytest.raises(ProviderError) as e:
        asyncio.run(AnthropicProvider("claude-opus-5-5", client=c).turn(Request(system="s", messages=[], tools=TOOLS),
                                                                         lambda *a, **k: None))
    assert e.value.retry and "could not read" in str(e.value)


# ------------------------------------------------------------------ OpenAI-compatible

def chunk(delta=None, finish=None, usage=None):
    d = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "gpt-5.5",
         "choices": [] if delta is None and finish is None else [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage:
        d["usage"] = usage
    return ChatCompletionChunk.model_validate(d)


class OClient:
    def __init__(self, chunks):
        self.chunks, self.params = chunks, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **params):
        self.params.append(params)

        async def gen():
            for c in self.chunks:
                yield c
        return gen()


def test_openai_streams_text_reasoning_and_parallel_tool_calls():
    chunks = [
        chunk({"role": "assistant", "content": "Checking", "reasoning_content": "think"}),
        chunk({"tool_calls": [{"index": 0, "id": "call_a", "type": "function", "function": {"name": "Read", "arguments": '{"file_'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'path": "a.py"}'}}]}),
        chunk({"tool_calls": [{"index": 1, "id": "call_b", "type": "function", "function": {"name": "Read", "arguments": '{"oops'}}]}),
        chunk(finish="tool_calls"),
        chunk(usage={"prompt_tokens": 900, "completion_tokens": 40, "total_tokens": 940,
                     "prompt_tokens_details": {"cached_tokens": 600}}),
    ]
    c = OClient(chunks)
    seen = []
    t = asyncio.run(OpenAICompatProvider("gpt-5.5", api_key="k", client=c).turn(
        Request(system="SYS", messages=[{"role": "user", "content": [{"type": "text", "text": "go"}]}], tools=TOOLS, effort="high"),
        lambda *a, **k: seen.append(a)))
    sent = c.params[0]
    assert sent["messages"][0] == {"role": "system", "content": "SYS"} and sent["reasoning_effort"] == "high"
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}
    assert sent["tools"][0]["function"]["name"] == "Read"
    assert ("delta", "Checking") in seen and ("thinking_delta", "think") in seen
    assert t.stop == TOOL_USE and t.blocks[0] == {"type": "thinking", "text": "think"}
    calls = t.tool_calls()
    assert calls[0] == {"type": "tool_call", "id": "call_a", "name": "Read", "input": {"file_path": "a.py"}}
    assert calls[1]["input"] == {"INVALID_JSON": '{"oops'}             # the harness answers it as an error
    assert (t.usage.input, t.usage.cache_read, t.usage.output) == (300, 600, 40)


def test_openai_history_and_limits():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "go"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"},
                                              {"type": "tool_call", "id": "x", "name": "mcp__srv.v2__q", "input": {"a": 1}}]},
            {"role": "user", "content": [{"type": "tool_result", "id": "x", "content": "boom", "is_error": True},
                                         {"type": "text", "text": "and then?"}]}]
    out = oa_messages("", msgs)
    assert out[1]["tool_calls"][0]["function"] == {"name": "mcp__srv_v2__q", "arguments": '{"a": 1}'}
    assert out[2] == {"role": "tool", "tool_call_id": "x", "content": "ERROR: boom"} and out[3] == {"role": "user", "content": "and then?"}
    assert tool_name("a" * 80) == "a" * 64
    cut = asyncio.run(OpenAICompatProvider("m", api_key="k", client=OClient([chunk({"content": "half"}, finish="length")])).turn(
        Request(system="", messages=[], tools=[]), lambda *a, **k: None))
    assert cut.stop == MAX_TOKENS
    no_tools = OpenAICompatProvider("m", api_key="k", client=OClient([]), tools=False, reasoning=False)
    assert "tools" not in no_tools.params(Request(system="", messages=[], tools=TOOLS, effort="high"))
    assert "reasoning_effort" not in no_tools.params(Request(system="", messages=[], tools=[], effort="high"))


# ------------------------------------------------------------------ which connection

def test_keels_provider_names_go_to_their_api_and_need_a_key():
    assert api_model("claude", "opus") == "claude-opus-5-5" and api_model("claude", "haiku") == "claude-haiku-5-5"
    assert api_model("codex", "") == "gpt-5.5" and api_model("copilot", "") == "openai/gpt-5"
    a = resolve({"provider": "claude", "model": "sonnet"}, "sk-ant-x")
    assert isinstance(a, AnthropicProvider) and a.model == "claude-sonnet-5-5"
    gh = resolve({"provider": "copilot", "model": "openai/gpt-5"}, "ghp_x")
    assert isinstance(gh, OpenAICompatProvider) and gh._base == "https://models.github.ai/inference"
    with pytest.raises(ProviderError, match="No Anthropic API key"):
        resolve({"provider": "claude", "model": "opus"}, None)
    with pytest.raises(ProviderError, match="no connection called"):
        resolve({"provider": "opencode", "model": "x"}, "k")


def test_github_models_gets_the_makers_name_for_a_model_saved_for_the_copilot_cli():
    from keel_engine.harness.providers import api_model

    assert api_model("copilot", "gpt-5") == "openai/gpt-5" and api_model("copilot", "o3-mini") == "openai/o3-mini"
    assert api_model("copilot", "openai/gpt-4.1") == "openai/gpt-4.1" and api_model("copilot", "") == "openai/gpt-5"
    assert api_model("copilot", "claude-sonnet-4.5") == "claude-sonnet-4.5"    # not on GitHub Models: its answer says so
