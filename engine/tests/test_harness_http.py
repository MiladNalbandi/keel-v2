"""#101 the harness over real HTTP: the official SDKs (anthropic, openai) talk to a local server that answers like the
Anthropic Messages API and the OpenAI chat API, streamed. So the SDKs build every request keel asks for (a parameter an
SDK does not take fails here, not on a person's first real run) and parse the stream keel reads: thinking with its
signature, text, a tool call whose input arrives in pieces, usage. No key, no network beyond 127.0.0.1."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from keel_engine.harness.runner import HarnessRunner
from keel_engine.models.base import AgentRequest
from keel_engine.tools.agent_tools import ToolBox


def sse(events: list[tuple[str | None, dict]]) -> bytes:
    out = []
    for name, data in events:
        out.append((f"event: {name}\n" if name else "") + f"data: {json.dumps(data)}\n\n")
    return "".join(out).encode()


class Server:
    """Answers each POST with the next scripted SSE body; records path, headers and JSON body."""

    def __init__(self, bodies: list[bytes]):
        self.bodies, self.seen = list(bodies), []
        srv = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server's name
                n = int(self.headers.get("content-length") or 0)
                srv.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                 "body": json.loads(self.rfile.read(n) or b"{}")})
                body = srv.bodies.pop(0) if srv.bodies else b""
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("# scores\nThe best score wins.\n")
    return root


def run(project, model: dict, key: str):
    req = AgentRequest(agent="implementer", system="You are keel's implementer.", prompt="Read the README, then answer.",
                       root=str(project), phase="none", model=model, toolbox=ToolBox(str(project), "none"),
                       timeout=60, key=key)
    steps: list = []
    res = asyncio.run(HarnessRunner().run(req, lambda kind, text="", **kw: steps.append((kind, text))))
    return res, steps


# ------------------------------------------------------------------ Anthropic

def a_turn(blocks: list[dict], stop: str, usage_in: int = 120) -> bytes:
    ev: list[tuple[str | None, dict]] = [("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": usage_in, "output_tokens": 1, "cache_creation_input_tokens": 900, "cache_read_input_tokens": 4000}}})]
    for i, b in enumerate(blocks):
        if b["type"] == "thinking":
            ev += [("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "thinking_delta", "thinking": b["thinking"]}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "signature_delta", "signature": b["signature"]}})]
        elif b["type"] == "text":
            ev += [("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}})]
        else:
            raw = json.dumps(b["input"])
            ev += [("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": raw[:7]}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": raw[7:]}})]
        ev.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    ev += [("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 30}}),
           ("message_stop", {"type": "message_stop"})]
    return sse(ev)


def test_claude_through_the_anthropic_sdk_over_http(project, monkeypatch):
    srv = Server([
        a_turn([{"type": "thinking", "thinking": "Read it first.", "signature": "sig-1"},
                {"type": "text", "text": "Reading."},
                {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "README.md"}}], "tool_use"),
        a_turn([{"type": "text", "text": "The best score wins."}], "end_turn", usage_in=40),
    ])
    monkeypatch.setenv("ANTHROPIC_BASE_URL", srv.url)
    try:
        res, steps = run(project, {"provider": "claude", "mode": "api", "model": "opus", "effort": "xhigh"}, "sk-ant-test")
    finally:
        srv.close()
    assert res.text == "The best score wins."
    first, second = srv.seen
    assert first["path"].startswith("/v1/messages") and first["headers"]["x-api-key"] == "sk-ant-test"
    betas = first["headers"].get("anthropic-beta", "")
    assert "compact-2026-01-12" in betas and "server-side-fallback-2026-07-01" in betas
    body = first["body"]
    assert body["model"] == "claude-opus-5-5" and body["stream"] is True
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"} and body["output_config"] == {"effort": "xhigh"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"} and body["cache_control"] == {"type": "ephemeral"}
    assert body["context_management"]["edits"][0]["type"] == "compact_20260112" and body["fallbacks"] == "default"
    read = next(t for t in body["tools"] if t["name"] == "Read")
    assert read["eager_input_streaming"] is True and read["input_schema"]["type"] == "object"
    # the second request: the assistant turn exactly as it came (the thinking's signature too), then the Read's result
    msgs = second["body"]["messages"]
    assistant, result = msgs[-2], msgs[-1]
    assert assistant["content"][0] == {"type": "thinking", "thinking": "Read it first.", "signature": "sig-1"}
    assert assistant["content"][2]["type"] == "tool_use" and assistant["content"][2]["input"] == {"file_path": "README.md"}
    assert result["content"][0]["type"] == "tool_result" and result["content"][0]["tool_use_id"] == "toolu_1"
    assert "The best score wins." in str(result["content"][0]["content"])
    assert ("delta", "Reading.") in steps and any(k == "read" for k, _ in steps)
    assert res.tokens_in == 120 + 900 + 40 + 900 and res.tokens_cached == 8000


# ------------------------------------------------------------------ OpenAI (and GitHub Models, a local server)

def o_chunk(delta: dict | None = None, finish: str | None = None, usage: dict | None = None) -> dict:
    return {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-5.5",
            "choices": [] if usage else [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
            **({"usage": usage} if usage else {})}


def o_turn(chunks: list[dict]) -> bytes:
    return sse([(None, c) for c in chunks]) + b"data: [DONE]\n\n"


def test_openai_through_the_openai_sdk_over_http(project, monkeypatch):
    srv = Server([
        o_turn([o_chunk({"role": "assistant", "content": "Reading."}),
                o_chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "Read", "arguments": '{"file_'}}]}),
                o_chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'path": "README.md"}'}}]}),
                o_chunk(finish="tool_calls"),
                o_chunk(usage={"prompt_tokens": 900, "completion_tokens": 20, "total_tokens": 920, "prompt_tokens_details": {"cached_tokens": 600}})]),
        o_turn([o_chunk({"content": "The best score wins."}), o_chunk(finish="stop"),
                o_chunk(usage={"prompt_tokens": 1000, "completion_tokens": 8, "total_tokens": 1008})]),
    ])
    monkeypatch.setenv("OPENAI_BASE_URL", srv.url + "/v1")
    try:
        res, steps = run(project, {"provider": "codex", "mode": "api", "model": "gpt-5.5", "effort": "high"}, "sk-openai-test")
    finally:
        srv.close()
    assert res.text == "The best score wins."
    first, second = srv.seen
    assert first["path"] == "/v1/chat/completions" and first["headers"]["authorization"] == "Bearer sk-openai-test"
    body = first["body"]
    assert body["model"] == "gpt-5.5" and body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["reasoning_effort"] == "high" and body["messages"][0]["role"] == "system"
    assert any(t["function"]["name"] == "Read" for t in body["tools"])
    msgs = second["body"]["messages"]
    assert msgs[-2]["tool_calls"][0]["function"] == {"name": "Read", "arguments": '{"file_path": "README.md"}'}
    assert msgs[-1]["role"] == "tool" and msgs[-1]["tool_call_id"] == "call_1" and "The best score wins." in msgs[-1]["content"]
    assert res.tokens_in == 300 + 1000 and res.tokens_cached == 600 and res.tokens_out == 28
