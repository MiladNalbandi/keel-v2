"""Google Vertex AI (provider `vertex`): Claude through the Anthropic SDK's AnthropicVertex client, Gemini through
Vertex's OpenAI-compatible endpoint. A fake Vertex server and fake Google credentials stand in for Google: keel sends
the right paths with the Google token, passes the effort on for Claude, and counts usage and cost from the replies."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from keel_engine.models import catalog, vertex
from keel_engine.models.base import AgentRequest, ModelError
from keel_engine.tools.agent_tools import ToolBox


class FakeCreds:
    """Stands in for google.auth credentials: always valid, one token."""
    valid = True
    expired = False
    token = "ya29.fake-token"  # keel:allow-secret (a test value)


class FakeVertex:
    def __init__(self):
        self.requests: list[dict] = []
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                server.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                if ":streamRawPredict" in self.path or ":rawPredict" in self.path:
                    return self.claude(body, stream=":streamRawPredict" in self.path)
                if self.path.endswith("/chat/completions"):
                    return self.gemini(body)
                self.send_response(404)
                self.end_headers()

            def sse(self, events):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for e in events:
                    self.wfile.write(e.encode())

            def claude(self, body, stream):
                usage = {"input_tokens": 50, "output_tokens": 9}
                if not stream:
                    out = json.dumps({"id": "m1", "type": "message", "role": "assistant", "model": "claude", "stop_reason": "end_turn",
                                      "stop_sequence": None, "content": [{"type": "text", "text": "OK from Vertex"}], "usage": usage}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return

                def ev(kind, data):
                    return f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n"

                self.sse([
                    ev("message_start", {"message": {"id": "m1", "type": "message", "role": "assistant", "model": "claude", "content": [],
                                                     "stop_reason": None, "stop_sequence": None,
                                                     "usage": {"input_tokens": 50, "output_tokens": 1}}}),
                    ev("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
                    ev("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "OK from Vertex"}}),
                    ev("content_block_stop", {"index": 0}),
                    ev("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"input_tokens": 50, "output_tokens": 9}}),
                    ev("message_stop", {}),
                ])

            def gemini(self, body):
                usage = {"prompt_tokens": 30, "completion_tokens": 4, "total_tokens": 34}
                if not body.get("stream"):
                    out = json.dumps({"id": "g1", "object": "chat.completion", "created": 0, "model": body["model"],
                                      "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK from Gemini"},
                                                   "finish_reason": "stop"}], "usage": usage}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                base = {"id": "g1", "object": "chat.completion.chunk", "created": 0, "model": body["model"]}
                chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK from Gemini"}, "finish_reason": None}]},
                          {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                          {**base, "choices": [], "usage": usage}]
                self.sse([f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"])

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


KEY = json.dumps({"project": "my-proj", "region": "us-east5"})


@pytest.fixture
def fake(monkeypatch):
    s = FakeVertex()
    monkeypatch.setattr(vertex, "credentials", lambda cfg: FakeCreds())
    monkeypatch.setenv("ANTHROPIC_VERTEX_BASE_URL", s.url + "/v1")       # AnthropicVertex's own override
    monkeypatch.setattr(vertex, "openai_base", lambda project, region: f"{s.url}/v1/projects/{project}/locations/{region}/endpoints/openapi")
    yield s
    s.close()


def req(tmp_path, model: str, effort: str | None = None) -> AgentRequest:
    m = {"provider": "vertex", "mode": "api", "model": model, **({"effort": effort} if effort else {})}
    return AgentRequest(agent="implementer", system="You are keel.", prompt="go", root=str(tmp_path), phase="green", model=m,
                        toolbox=ToolBox(str(tmp_path), "green"), workdir=str(tmp_path), key=KEY)


def collect():
    steps = []
    return steps, (lambda kind, text="", **kw: steps.append({"kind": kind, "text": text, **kw}))


def test_the_catalog_shows_vertex_once_it_has_a_project_and_region():
    assert "vertex" not in catalog.build_catalog({})
    assert "vertex" not in catalog.build_catalog({"vertex": json.dumps({"project": "p"})})
    entry = catalog.build_catalog({"vertex": KEY})["vertex"]
    ids = {m["id"]: m["efforts"] for m in entry["modes"]["api"]}
    assert ids["claude-opus-5-5"] == catalog.CLAUDE_EFFORTS and ids["claude-haiku-4-5@20251001"] == []
    assert ids["claude-haiku-5-5"] == catalog.CLAUDE_EFFORTS
    assert ids["gemini-2.5-pro"] == [] and "gemini-3-pro-preview" in ids
    assert entry["default"]["model"] == "claude-sonnet-5-5" and entry["label"] == "Vertex AI"


def test_efforts_follow_the_anthropic_api_for_claude_and_none_for_gemini():
    assert catalog.efforts_for("vertex", "api", "claude-sonnet-5-5") == catalog.CLAUDE_EFFORTS
    assert catalog.efforts_for("vertex", "api", "gemini-2.5-flash") == []
    assert catalog.effort_for({"provider": "vertex", "mode": "api", "model": "claude-opus-5-5", "effort": "xhigh"}) == "xhigh"
    assert catalog.effort_for({"provider": "vertex", "mode": "api", "model": "gemini-2.5-pro", "effort": "high"}) is None


def test_prices_for_vertex_models():
    assert catalog.cost_usd("vertex", "claude-sonnet-5-5", 1_000_000, 1_000_000) == 12.0
    assert catalog.cost_usd("vertex", "gemini-2.5-flash", 1_000_000, 0) == 0.3
    assert catalog.cost_usd("vertex", "some-new-model", 1_000_000, 0) == 2.0


def test_not_set_up_says_where():
    with pytest.raises(ModelError) as e:
        vertex.chat_model(None, "claude-sonnet-5-5")
    assert "Connections" in e.value.hint
    with pytest.raises(ModelError):
        vertex.chat_model(json.dumps({"project": "p"}), "claude-sonnet-5-5")


def test_regional_hosts():
    assert vertex.api_host("global") == "https://aiplatform.googleapis.com"
    assert vertex.api_host("eu") == "https://aiplatform.eu.rep.googleapis.com"
    assert vertex.api_host("us-east5") == "https://us-east5-aiplatform.googleapis.com"
    assert vertex.openai_base("p", "europe-west1") == \
        "https://europe-west1-aiplatform.googleapis.com/v1/projects/p/locations/europe-west1/endpoints/openapi"


async def test_the_provider_test_and_ask(fake, monkeypatch):
    from keel_engine import models

    monkeypatch.delenv("KEEL_FAKE")
    out = await models.test_provider("vertex", "api", "claude-haiku-5-5", key=KEY)
    assert out["text"] == "OK from Vertex"
    said = await models.ask({"provider": "vertex", "mode": "api", "model": "gemini-2.5-pro"}, "Judge.", "Right?", keys={"vertex": KEY})
    assert said["ok"] and said["text"] == "OK from Gemini" and said["tokens_in"] == 30


def test_a_bad_service_account_key_is_refused_clearly(monkeypatch):
    pytest.importorskip("google.oauth2")
    with pytest.raises(ModelError) as e:
        vertex.credentials({"credentials": "{not json"})
    assert "not JSON" in str(e.value)
    with pytest.raises(ModelError) as e:
        vertex.credentials({"credentials": {"type": "service_account"}})
    assert "not valid" in str(e.value)
