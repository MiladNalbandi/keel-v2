"""0.18.66 which AI tool calls keel (api McpClients: Help › Start here, Settings › Access) and keel_health's update line."""

import httpx
from test_mcp import StubApi, call

from keel_engine import mcp_server


def _stub(monkeypatch) -> StubApi:
    monkeypatch.setenv("KEEL_PROJECT", "shop")
    monkeypatch.chdir("/")
    s = StubApi()
    s.api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(s))
    return s


async def test_every_request_says_the_clients_name_reads_too(monkeypatch):
    stub = _stub(monkeypatch)
    seen = []

    def record(req):
        seen.append((req.method, req.headers.get("x-keel-client"), req.headers.get("x-keel-mcp-kind")))
        return stub(req)

    stub.api.http._transport = httpx.MockTransport(record)
    assert "shop" in await call(mcp_server.build_server(api=stub.api), "keel_status", {"project": "shop"})
    gets = [c for m, c, _ in seen if m == "GET"]
    # the tests' MCP client session says its name is "mcp" in its initialize
    assert gets and all(c == "mcp" for c in gets)
    assert all(k is None for _, _, k in seen)        # a person's own server, not an agent's


def test_an_agents_server_says_it_is_one(monkeypatch):
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN_FILE", raising=False)
    monkeypatch.setenv("KEEL_MCP_TOKEN", "tok-read")
    api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert api._audit("GET") == {"X-Keel-Mcp-Kind": "agent"}
    api.name_client("claude-code")
    api.name_client("another")                       # the first name stays
    assert api._audit("GET") == {"X-Keel-Mcp-Kind": "agent", "X-Keel-Client": "claude-code"}
    w = api._audit("POST")
    assert w["X-Keel-Client"] == "claude-code" and w["X-Keel-Via"] == "mcp"


async def test_health_says_when_a_newer_keel_is_out(monkeypatch):
    monkeypatch.delenv("KEEL_MCP_RELAY_N", raising=False)
    stub = _stub(monkeypatch)

    def route(req):
        if req.url.path == "/api/health":
            return httpx.Response(200, json={"ok": True, "engine": True, "version": "0.18.64", "fake": False, "latest": "0.18.66"})
        return stub(req)

    stub.api.http._transport = httpx.MockTransport(route)
    text = await call(mcp_server.build_server(api=stub.api), "keel_health")
    assert "update      keel 0.18.66 is out (this keel is 0.18.64): run keel2 update" in text
    plain = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(StubApi()))
    assert "update " not in await call(mcp_server.build_server(api=plain), "keel_health")
