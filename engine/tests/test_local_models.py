"""Local models (provider `local`): a fake OpenAI-compatible server stands in for Ollama / LM Studio / vLLM. keel lists
its models, chats through its /v1 endpoint with tools (and without, for a model that cannot call them), counts the
usage it reports, packs a prompt into the model's window, and refuses a code step on a model without tool calls."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from keel_engine.models import catalog, local
from keel_engine.models.base import AgentRequest, ModelError
from keel_engine.tools.agent_tools import ToolBox


class FakeServer:
    """Ollama's /api/tags and /api/show, and the OpenAI-style /v1/models and /v1/chat/completions (streamed or not).
    Model "coder" calls tools; model "plain" refuses a request with tools, as Ollama does."""

    def __init__(self, ollama: bool = True, show_caps: bool = True):
        self.requests: list[dict] = []
        self.ollama, self.show_caps = ollama, show_caps
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                server.requests.append({"method": "GET", "path": self.path, "auth": self.headers.get("Authorization")})
                if self.path == "/api/tags" and server.ollama:
                    return self._json(200, {"models": [{"name": "coder:7b", "details": {"parameter_size": "7B"}},
                                                       {"name": "plain:3b", "details": {}}]})
                if self.path == "/v1/models":
                    return self._json(200, {"object": "list", "data": [{"id": "coder:7b", "max_model_len": 32768},
                                                                       {"id": "plain:3b"}]})
                return self._json(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                server.requests.append({"method": "POST", "path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                if self.path == "/api/show":
                    caps = ["completion", "tools"] if body.get("model", "").startswith("coder") else ["completion"]
                    params = "num_ctx 6144\nstop <|eot|>" if body.get("model", "").startswith("coder") else "stop <|eot|>"
                    out = {"model_info": {"llama.context_length": 131072}, "parameters": params}
                    if server.show_caps:
                        out["capabilities"] = caps
                    return self._json(200, out)
                if self.path != "/v1/chat/completions":
                    return self._json(404, {"error": "not found"})
                model = body.get("model", "")
                if body.get("tools") and model.startswith("plain"):
                    return self._json(400, {"error": {"message": f"registry.ollama.ai/library/{model} does not support tools"}})
                said_tool = any(m.get("role") == "tool" for m in body.get("messages", []))
                if body.get("tools") and not said_tool:
                    name = body["tools"][0]["function"]["name"]
                    args = {"path": "README.md"} if name == "read_file" else {}
                    msg = {"role": "assistant", "content": None,
                           "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
                else:
                    msg = {"role": "assistant", "content": "done: OK"}
                usage = {"prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127}
                if not body.get("stream"):
                    return self._json(200, {"id": "c1", "object": "chat.completion", "created": 0, "model": model,
                                            "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}], "usage": usage})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def chunk(delta, finish=None, use=None):
                    c = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": model,
                         "choices": [] if use else [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    if use:
                        c["usage"] = use
                    self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())

                if msg.get("tool_calls"):
                    tc = msg["tool_calls"][0]
                    chunk({"role": "assistant", "tool_calls": [{"index": 0, **tc}]})
                    chunk({}, "tool_calls")
                else:
                    chunk({"role": "assistant", "content": "done: "})
                    chunk({"content": "OK"})
                    chunk({}, "stop")
                if (body.get("stream_options") or {}).get("include_usage"):
                    chunk({}, use=usage)
                self.wfile.write(b"data: [DONE]\n\n")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    catalog.clear_cache()
    local.forget_tools()
    s = FakeServer()
    yield s
    s.close()
    catalog.clear_cache()
    local.forget_tools()


def cfg(url: str, kind: str = "ollama", **extra) -> str:
    return json.dumps({"enabled": True, "endpoints": [{"name": "box", "kind": kind, "url": url, **extra}]})


def collect():
    steps = []
    return steps, (lambda kind, text="", **kw: steps.append({"kind": kind, "text": text, **kw}))


def req(tmp_path, model: str, key: str, agent="implementer", phase="green", readonly=False, prompt="go") -> AgentRequest:
    (tmp_path / "README.md").write_text("# hello\n")
    return AgentRequest(agent=agent, system="You are keel.", prompt=prompt, root=str(tmp_path), phase=phase,
                        model={"provider": "local", "mode": "api", "model": model},
                        toolbox=ToolBox(str(tmp_path), phase, readonly=readonly), workdir=str(tmp_path), key=key)


# ------------------------------------------------------------------ listing

def test_ollama_models_are_listed_with_the_local_badge(server):
    entry = catalog.build_catalog({"local": cfg(server.url)})["local"]
    ids = [m["id"] for m in entry["modes"]["api"]]
    assert ids == ["box/coder:7b", "box/plain:3b"]
    assert all(m["badge"] == "local" and m["efforts"] == [] for m in entry["modes"]["api"])
    assert entry["modes"]["api"][0]["label"] == "coder:7b (7B) · box"
    assert entry["efforts"] == [] and entry["default"] == {"mode": "api", "model": "box/coder:7b"}
    assert any(r["path"] == "/api/tags" for r in server.requests)


def test_openai_compatible_servers_list_v1_models_with_the_key(server):
    key = cfg(server.url + "/v1", kind="lmstudio", key="sk-local-123")
    models = catalog.build_catalog({"local": key})["local"]["modes"]["api"]
    assert [m["id"] for m in models] == ["box/coder:7b", "box/plain:3b"]
    assert models[0]["context"] == 32768
    got = [r for r in server.requests if r["path"] == "/v1/models"]
    assert got and got[0]["auth"] == "Bearer sk-local-123"


def test_the_list_is_cached_and_a_dead_server_does_not_break_the_catalog(server):
    catalog.build_catalog({"local": cfg(server.url)})
    catalog.build_catalog({"local": cfg(server.url)})
    assert sum(r["path"] == "/api/tags" for r in server.requests) == 1
    dead = catalog.build_catalog({"local": cfg("http://127.0.0.1:9")})
    assert "claude" in dead and dead["local"]["modes"]["api"] == [] and dead["local"]["problems"] == ["box did not answer"]


def test_local_models_off_or_not_set_up_show_nothing(server):
    assert "local" not in catalog.build_catalog({})
    off = json.dumps({"enabled": False, "endpoints": [{"name": "box", "kind": "ollama", "url": server.url}]})
    assert "local" not in catalog.build_catalog({"local": off})
    assert "local" not in catalog.build_catalog()


def test_the_test_connection_lists_models_or_says_why(server):
    ok = local.test_endpoint({"name": "box", "kind": "ollama", "url": server.url})
    assert ok["ok"] and [m["id"] for m in ok["models"]] == ["coder:7b", "plain:3b"]
    bad = local.test_endpoint({"name": "box", "kind": "ollama", "url": "http://127.0.0.1:9"})
    assert not bad["ok"] and "cannot reach" in bad["error"] and bad["hint"]


def test_base_urls_per_kind():
    assert local.openai_base({"kind": "ollama", "url": "http://h:11434/"}) == "http://h:11434/v1"
    assert local.openai_base({"kind": "lmstudio", "url": "http://h:1234/v1"}) == "http://h:1234/v1"
    assert local.openai_base({"kind": "vllm", "url": "http://h:8000"}) == "http://h:8000/v1"
    assert local.openai_base({"kind": "openai-compatible", "url": "http://h/api/openai"}) == "http://h/api/openai"
    assert local.ollama_root({"kind": "ollama", "url": "http://h:11434/v1"}) == "http://h:11434"


def test_model_ids_name_their_server():
    two = {"endpoints": [{"name": "a", "url": "http://a"}, {"name": "b", "url": "http://b"}]}
    assert local.split(two, "b/llama3:8b")[0]["name"] == "b"
    assert local.split(two, "b/hf.co/x/y:q4")[1] == "hf.co/x/y:q4"
    one = {"endpoints": [{"name": "a", "url": "http://a"}]}
    assert local.split(one, "llama3")[1] == "llama3"
    with pytest.raises(ModelError):
        local.split(two, "c/llama3")
    with pytest.raises(ModelError):
        local.split({}, "a/x")


# ------------------------------------------------------------------ efforts, prices, context


def test_context_comes_from_the_endpoint_then_the_server(server):
    ep = {"name": "box", "kind": "ollama", "url": server.url}
    assert local.context_for(ep, "coder:7b") == 6144           # Ollama's num_ctx
    assert local.fetch_info(ep, "plain:3b")["context"] == 4096   # no num_ctx: Ollama's default, not the trained 131072
    assert local.context_for({**ep, "context": 16384}, "coder:7b") == 16384
    assert local.context_for({"name": "x", "kind": "vllm", "url": "http://127.0.0.1:9"}, "m") == local.DEFAULT_CONTEXT


def test_fit_drops_old_turns_then_the_middle_of_the_prompt():
    hist = [("user", "old " * 400), ("assistant", "older " * 400)]
    system, h, prompt, trimmed = local.fit("sys", hist, "start " + "x" * 20000 + " end", 2048)
    assert trimmed and h == []
    assert prompt.startswith("start ") and prompt.endswith(" end") and "left out the middle" in prompt
    assert len(system) + len(prompt) <= (2048 - 512) * local.CHARS_PER_TOKEN
    same = local.fit("sys", [("user", "hi")], "short", 8192)
    assert same == ("sys", [("user", "hi")], "short", False)


# ------------------------------------------------------------------ chat, tools, usage


async def test_other_servers_are_probed_once_and_the_answer_is_remembered(tmp_path):
    catalog.clear_cache()
    local.forget_tools()
    s = FakeServer(ollama=False)
    try:
        key = cfg(s.url + "/v1", kind="vllm")
        ep = {"name": "box", "kind": "vllm", "url": s.url + "/v1"}
        assert local.tool_support(ep, "coder:7b") is True
        assert local.tool_support(ep, "plain:3b") is False
        probes = [r for r in s.requests if r["path"] == "/v1/chat/completions"]
        assert len(probes) == 2 and probes[0]["body"]["tools"][0]["function"]["name"] == "keel_ping"
        local.forget_tools()            # a restart: the answers come from $KEEL_DATA/local-models.json
        assert local.tool_support(ep, "plain:3b") is False
        assert len([r for r in s.requests if r["path"] == "/v1/chat/completions"]) == 2
        models = catalog.build_catalog({"local": key})["local"]["modes"]["api"]
        assert {m["id"]: m.get("tools") for m in models} == {"box/coder:7b": True, "box/plain:3b": False}
    finally:
        s.close()
        catalog.clear_cache()
        local.forget_tools()


async def test_ask_and_the_provider_test_go_through_the_server(server, monkeypatch):
    from keel_engine import models

    monkeypatch.delenv("KEEL_FAKE")      # KEEL_FAKE=1 would put the fake model in place of every real one
    out = await models.test_provider("local", "api", "box/plain:3b", key=cfg(server.url))
    assert out["ok"] and out["text"] == "done: OK"
    m = {"provider": "local", "mode": "api", "model": "box/plain:3b"}
    said = await models.ask(m, "Judge.", "Is it right?", keys={"local": cfg(server.url)})
    assert said["ok"] and said["text"] == "done: OK" and said["tokens_in"] == 120


def test_no_server_set_up_says_where_to_add_one():
    with pytest.raises(ModelError) as e:
        local.chat_model(None, "box/x")
    assert "Connections" in e.value.hint


def test_the_engine_routes(client, server):
    r = client.post("/providers/models", json={"keys": {"local": cfg(server.url)}})
    assert r.status_code == 200 and "local" in r.json()
    r = client.post("/providers/local/test", json={"name": "box", "kind": "ollama", "url": server.url})
    assert r.json()["ok"] is True
    assert client.post("/providers/local/test", json={"kind": "ollama", "url": "file:///etc/passwd"}).status_code in (400, 422)
    assert client.get("/providers/models").json().get("local") is None
