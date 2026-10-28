"""0.18.28 keel's MCP server: the whole gate text (paged, never cut without a word), the suite gate's choices, the
edit choice of any gate, and a server that stays up (opencode dropped keel's tools mid-chat: a tool's HTTP call blocked
the event loop, and one stray print on stdout broke the stream)."""

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import anyio
import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from keel_engine import mcp_server
from test_mcp import THREAD, StubApi, call

ENGINE = Path(__file__).resolve().parents[1]

SUITE = {"step": "integration_commit", "name": "verify the suite + commit", "kind": "fix", "id": "q-s",
         "title": "verify the suite + commit: the suite fails (likely AC-008's test)", "options": ["approve", "reject"],
         "choices": ["send_back_ac", "retry", "stop"],
         "choice_info": [
             {"name": "send_back_ac", "label": "Send back to AC-008", "description": "AC-008's test is the likely cause", "why": "optional", "default": True},
             {"name": "retry", "label": "Check again", "description": "run the suite again (after you fixed it yourself)", "why": "none", "default": False},
             {"name": "stop", "label": "Stop the flow", "description": "stop here; the commits so far stay", "why": "optional", "default": False}],
         "plain": {"approve": {"does": 'the choice "send_back_ac"', "reason": "used", "choice": "send_back_ac"},
                   "reject": {"does": 'the choice "send_back_ac" (Send back to AC-008)', "reason": "used", "choice": "send_back_ac"}},
         "detail": "Failing tests:\n" + "\n".join(f"- App\\Tests\\T{n}::testCase  Duplicate entry 'pp' for key 'code' ({'x' * 200})"
                                                 for n in range(150)) + "\nTHE END OF THE OUTPUT"}
SPEC_GATE = {"step": "spec_gate", "name": "spec approval", "kind": "gate", "title": "spec approval", "options": ["approve"],
             "choices": ["approve", "edit", "rewrite", "review", "order", "reject"], "detail": "4 criteria", "id": "q-spec"}


class Api(StubApi):
    def __init__(self, waiting, slow: float = 0.0, flows: dict | None = None):
        super().__init__({**copy.deepcopy(THREAD), "current": waiting["step"], "waiting": waiting})
        self.slow = slow

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.slow and req.url.path == "/api/projects":
            time.sleep(self.slow)
        if req.method == "GET" and req.url.path == "/api/projects/shop/flows/t-1":
            return httpx.Response(200, json={"thread": self.thread, "workflow": {"id": "feature"}})
        return super().__call__(req)


def server(api: Api, write: bool = False):
    return mcp_server.build_server(write=write, api=mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(api)))


@pytest.fixture(autouse=True)
def here(monkeypatch):
    monkeypatch.setenv("KEEL_PROJECT", "shop")      # a write needs its project named when the folder matches none
    monkeypatch.chdir("/")


async def test_status_never_cuts_a_gate_without_a_word_and_read_gate_pages_it_whole():
    api = Api(SUITE)
    srv = server(api)
    status = await call(srv, "keel_status")
    assert "send_back_ac \"Send back to AC-008\": AC-008's test is the likely cause" in status
    assert "a plain reject here the choice \"send_back_ac\"" in status
    assert "THE END OF THE OUTPUT" not in status
    assert "(the gate's text goes on:" in status and "keel_read_gate(flow='t-1') reads it whole" in status
    first = await call(srv, "keel_read_gate", {})
    n = -(-len(SUITE["detail"]) // mcp_server.GATE_PAGE)
    assert n >= 2
    assert "gate      verify the suite + commit:" in first and f"page 1 of {n}" in first
    assert "the next is keel_read_gate(flow='t-1', page=2)" in first
    pages = [first] + [await call(srv, "keel_read_gate", {"page": k}) for k in range(2, n + 1)]
    assert "THE END OF THE OUTPUT" in pages[-1] and f"page {n} of {n}" in pages[-1] and "the next is" not in pages[-1]
    whole = "".join(p.split("characters)\n", 1)[1].split("\n\npage ")[0] for p in pages)
    assert whole == SUITE["detail"]
    nxt = await call(srv, "keel_next")
    assert "send_back_ac" in nxt and "a plain reject here" in nxt


async def test_the_suite_gates_choices_go_through_approve_gate():
    api = Api(SUITE)
    await call(server(api, write=True), "keel_approve_gate", {"decision": "approve", "choice": "send_back_ac",
                                                              "why": "the AC-008 test leaks a row"})
    path, body = api.posts[-1]
    assert path == "/api/threads/t-1/resume"
    assert body == {"decision": "approve", "why": "the AC-008 test leaks a row", "payload": {"choice": "send_back_ac", "question_id": "q-s"}}


async def test_edit_gate_takes_the_spec_gates_edit_choice_with_its_notes():
    api = Api(SPEC_GATE)
    text = await call(server(api, write=True), "keel_edit_gate", {"notes": "AC-3 must say 400, not 422"})
    path, body = api.posts[-1]
    assert body == {"decision": "approve", "why": "AC-3 must say 400, not 422", "payload": {"choice": "edit", "question_id": "q-spec"}}
    assert text.startswith("spec approval on thread t-1: ran the choice \"edit\"")      # 0.18.64 the choice that ran
    # a gate that only takes a send-back still gets one
    api = Api({**THREAD["waiting"]})
    await call(server(api, write=True), "keel_edit_gate", {"notes": "say why"})
    assert api.posts[-1][1] == {"decision": "reject", "why": "say why"}


async def test_a_slow_api_call_never_blocks_the_server():
    """keel_status waits 1 s for the api; a ping answers meanwhile (a sync tool used to block the whole loop)."""
    api = Api(SUITE, slow=1.0)
    async with create_connected_server_and_client_session(server(api)) as session:
        took: dict = {}

        async def slow():
            res = await session.call_tool("keel_status", {})
            took["status"] = res

        async with anyio.create_task_group() as tg:
            tg.start_soon(slow)
            await anyio.sleep(0.2)
            t0 = time.monotonic()
            await session.send_ping()
            took["ping"] = time.monotonic() - t0
        assert took["ping"] < 0.5, took
        assert not took["status"].isError


async def test_the_server_answers_a_long_list_of_calls_and_a_tool_that_breaks(monkeypatch):
    api = Api(SUITE)
    calls = 0

    def explain(*a, **k):
        raise KeyError("waiting")
    monkeypatch.setattr(mcp_server, "explain", explain)
    async with create_connected_server_and_client_session(server(api, write=True)) as session:
        for n in range(60):
            for name, args in (("keel_status", {}), ("keel_next", {}), ("keel_read_gate", {"page": 1 + n % 3}),
                               ("keel_inbox", {}), ("keel_explain", {})):
                res = await session.call_tool(name, args)
                calls += 1
                text = res.content[0].text
                if name == "keel_explain":
                    assert "keel: explain failed (KeyError" in text
                else:
                    assert not res.isError and text
        await session.send_ping()
    assert calls == 300


STRAY = r"""
import sys
from keel_engine import mcp_server
real = mcp_server.projects
def noisy(api):
    print("a stray print from a plugin")          # on stdout: it must not reach the protocol
    return "no project"
mcp_server.projects = noisy
mcp_server.main([])
"""


async def test_the_stdio_server_keeps_stdout_for_the_protocol(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "KEEL_API_URL": "http://127.0.0.1:9", "PYTHONPATH": str(ENGINE), "KEEL_DATA": str(tmp_path)}
    params = StdioServerParameters(command=sys.executable, args=["-c", STRAY], env=env, cwd=str(tmp_path))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for _ in range(30):
                res = await session.call_tool("keel_projects", {})
                assert res.content[0].text == "no project"
            res = await session.call_tool("keel_status", {})
            assert "keel's api does not answer" in res.content[0].text
