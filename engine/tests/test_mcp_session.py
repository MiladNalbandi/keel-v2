"""0.29 the connected-session MCP tools (mcp_session.py) against the real engine: keel's MCP server, as a person's own
Claude Code or OpenCode starts it (`keel2 mcp --write`), talks over HTTP to the engine's /sessions routes (the api's
/api/sessions/* pass through to them; this transport does it here). The flow is real; the session's work is the test."""

import json

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import Implementation

from conftest import start, wait
from test_flows_base import answers
from keel_engine import mcp_server

PLAN = "## Goal\nShow the owner's name.\n## Acceptance criteria\n1. The page shows it.\nPLAN: ready"


class ToEngine:
    """keel's api for the MCP server: the project list and a start of its own, every /api/sessions* to the engine."""

    def __init__(self, client, repo):
        self.client, self.repo = client, repo
        self.starts: list[dict] = []
        self.seen: list[tuple[str, str, dict]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        body = json.loads(req.content) if req.content else None
        self.seen.append((req.method, path, dict(req.headers)))
        if path == "/api/projects":
            return httpx.Response(200, json=[{"id": "demo", "name": "demo", "root": str(self.repo)}])
        if path == "/api/projects/demo/workflows":
            return httpx.Response(200, json=[{"id": "harness"}, {"id": "feature"}, {"id": "review"}])
        if path == "/api/projects/demo/flows" and req.method == "POST":
            self.starts.append(body)
            return httpx.Response(200, json={"thread_id": "t-9", "status": "running", "workflow_id": body["workflow_id"]})
        if path.startswith("/api/sessions"):
            r = self.client.request(req.method, path[len("/api"):], params=dict(req.url.params), json=body)
            return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})
        return httpx.Response(404, json={"error": f"no route {path}"})


@pytest.fixture
def keel(client, repo, monkeypatch):
    monkeypatch.setenv("KEEL_PROJECT", "demo")
    monkeypatch.delenv("KEEL_CLIENT_CWD", raising=False)
    t = ToEngine(client, repo)
    api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(t))
    t.srv = mcp_server.build_server(level="write", api=api)
    return t


@pytest.fixture
def no_model(monkeypatch):
    return answers(monkeypatch, {"harness-planner": lambda r: PLAN, "harness-builder": lambda r: "keel's harness built it"})


async def call(keel, *steps: tuple[str, dict]) -> list[str]:
    """Tool calls in ONE client session (one MCP server process keeps its session id), as Claude Code would."""
    out = []
    async with create_connected_server_and_client_session(keel.srv, client_info=Implementation(name="claude-code", version="2.1")) as s:
        for name, args in steps:
            res = await s.call_tool(name, args)
            assert not res.isError, res
            out.append(res.content[0].text)
    return out


def session_flow(client, repo):
    tid = start(client, repo, workflow="harness", title="Show the owner name", data={"runner": "session"},
                models={"default": {"provider": "fake", "mode": "api", "model": "fake"}})
    return tid, wait(client, tid)


async def test_open_says_how_it_works_and_registers_the_client_by_its_name(keel, client):
    opened, = await call(keel, ("keel_session_open", {}))
    assert "keel_session_next" in opened and "Never commit, push" in opened and "No work order waits yet" in opened
    got = client.get("/sessions", params={"project": "demo"}).json()
    assert got["connected"]["client"] == "claude-code"
    # a write: the audit headers say the MCP client and the tool
    method, path, headers = next(x for x in keel.seen if x[1] == "/api/sessions/open")
    assert headers.get("x-keel-via") == "mcp" and headers.get("x-keel-tool") == "keel_session_open"


async def test_a_session_plans_through_the_tools_and_the_flow_reaches_the_plan_gate(keel, client, repo, no_model):
    tid, s = session_flow(client, repo)
    assert s["waiting"]["kind"] == "session"
    order, todo, bad, ok, again = await call(
        keel,
        ("keel_session_next", {"wait": 0}),
        ("keel_session_todo", {"flow": tid, "todos": [{"text": "Read the pet page", "status": "done"},
                                                      {"text": "Write the plan", "status": "in_progress"}]}),
        ("keel_session_plan", {"flow": tid, "plan": "## Goal\nShow it."}),
        ("keel_session_plan", {"flow": tid, "plan": PLAN, "todos": [{"text": "Write the plan", "status": "done"}]}),
        ("keel_session_next", {"wait": 0}),
    )
    assert order.startswith("# Work order: keel → session: plan this") and tid in order
    assert f'keel_session_plan(flow="{tid}"' in order and "Never commit or push" in order
    assert "Instructions (what keel's own harness agent gets for this step)" in order
    assert todo == "keel shows your list: 1 of 2 done."
    assert bad.startswith("keel: ") and "PLAN: ready" in bad          # refused, with why
    assert ok.startswith("keel has the plan")
    assert again.startswith("Nothing yet")
    s = wait(client, tid)
    assert s["waiting"]["step"] == "plan_gate" and no_model == [], s


async def test_a_report_with_nothing_waiting_is_refused_in_plain_words(keel, client, repo, no_model):
    tid, _s = session_flow(client, repo)
    await call(keel, ("keel_session_next", {"wait": 0}), ("keel_session_plan", {"flow": tid, "plan": PLAN}))
    wait(client, tid)
    done, = await call(keel, ("keel_session_done", {"flow": tid, "summary": "Built it."}))
    assert done.startswith("keel: ") and "waits for the person" in done


async def test_next_opens_the_session_by_itself_and_again_after_a_keel_restart(keel, client, repo, no_model):
    first, = await call(keel, ("keel_session_next", {"wait": 0}))
    assert first.startswith("Nothing yet")
    client.app.state.engine.sessions.sessions.clear()           # keel restarted: it forgot every session
    tid, _s = session_flow(client, repo)
    order, = await call(keel, ("keel_session_next", {"wait": 0}))
    assert tid in order
    assert len(client.get("/sessions", params={"project": "demo"}).json()["sessions"]) == 1


async def test_start_flow_with_engine_session_needs_a_connected_session(keel, client):
    refused, = await call(keel, ("keel_start_flow", {"type": "feature", "request": "Show each owner's name on the pet page",
                                                     "engine": "session"}))
    assert "no session is connected" in refused and keel.starts == []
    _open, started = await call(keel, ("keel_session_open", {}),
                                ("keel_start_flow", {"type": "feature", "request": "Show each owner's name on the pet page",
                                                     "engine": "session"}))
    body = keel.starts[-1]
    assert body["workflow_id"] == "harness" and body["options"]["runner"] == "session"
    assert body["options"]["intent"] == "feature" and body["options"]["session_client"] == "claude-code"
    assert "engine='session'" in started and "keel_session_next" in started
    _o, no = await call(keel, ("keel_session_open", {}), ("keel_start_flow", {"type": "review", "engine": "session"}))
    assert "harness flow" in no


def test_the_work_order_text_names_the_plan_notes_and_failure():
    from keel_engine import mcp_session

    text = mcp_session.order_text({"kind": "fix", "flow": "t1", "title": "Owner name", "step": "build", "root": "/w/demo",
                                   "branch": "keel/owner", "instructions": "Build the plan.", "plan": "## Goal\nX",
                                   "failure": "E assert 2 == 3"})
    assert "keel → session: fix the failing tests" in text and "keel_session_done(flow=\"t1\"" in text
    assert "## The approved plan" in text and "E assert 2 == 3" in text and "(branch keel/owner)" in text
    assert "Nothing yet" in mcp_session.nothing_yet(240)
