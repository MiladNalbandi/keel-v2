"""keel 0.18.5: what keel TUI can do, through keel's MCP tools too (keel_engine/mcp_tui.py): stop flows, a run mode, go on
with a stopped flow, the agents (the flow's and the person's own), KeelBot (ask, chats, changes, keep, undo) and the
ASCII diagrams. Against a stub api; the write and admin tools with `yes` (the person's OK is tested in test_mcp.py)."""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from keel_engine import mcp_server

PROJECTS = [{"id": "shop", "name": "shop", "root": "/workspace/shop", "branch": "main"}]
RUNS = [
    {"thread_id": "t-1aaaaaaa", "title": "Player scores", "workflow_id": "feature", "status": "waiting", "current": "spec_gate"},
    {"thread_id": "t-2bbbbbbb", "title": "Logger", "workflow_id": "change", "status": "running", "current": "green"},
    {"thread_id": "t-3ccccccc", "title": "Old one", "workflow_id": "feature", "status": "stopped", "current": "red"},
]
THREAD = {"thread_id": "t-3ccccccc", "project_id": "shop", "workflow_id": "feature", "title": "Old one", "status": "stopped",
          "current": "red", "phase": "red", "run_mode": "manual"}


class Stub:
    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.thread = dict(THREAD)
        self.session = {"id": "s-1", "title": "write the docs", "mode": "side", "status": "idle", "busy": False, "branch": "keel/helper/s-1",
                        "cost_usd": 0.03, "turns": 1, "updated_at": "2026-10-16T10:00:00Z", "messages": []}
        self.answer = "Done: docs/health.md.\n\n```keel-flow\n{\"do\": \"stop\", \"flows\": [\"t-2bbbbbbb\"]}\n```"

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        body = json.loads(req.content) if req.content else None
        self.calls.append((m, path, body))
        ok = lambda x: httpx.Response(200, json=x)  # noqa: E731
        if path == "/api/mcp/token":
            return ok({"token": "t"})
        if m == "GET" and path == "/api/projects":
            return ok(PROJECTS)
        if m == "GET" and path == "/api/projects/shop/runs":
            return ok(RUNS)
        if m == "GET" and path == "/api/projects/shop/flow":
            return ok({"thread": self.thread, "workflow": {"id": "feature", "steps": []}})
        if m == "POST" and path.startswith("/api/threads/") and path.endswith("/stop"):
            return ok({"status": "stopped"})
        if m == "POST" and path.endswith("/mode"):
            self.thread["run_mode"] = body["mode"]
            return ok(self.thread)
        if m == "GET" and path.endswith("/history"):
            return ok([{"id": "c1", "n": 1, "step": "spec"}, {"id": "c2", "n": 2, "step": "red"}])
        if m == "POST" and path.endswith("/rewind"):
            return ok({**self.thread, "status": "running"})
        if m == "GET" and path == "/api/jobs":
            return ok([{"id": "j-1", "agent": "explorer", "step": "plan", "ac": "AC-2", "tokens_in": 900, "tokens_out": 100}])
        if m == "GET" and path == "/api/jobs/j-1/steps":
            return ok({"steps": [{"n": 1, "kind": "tool", "tool": "read_file", "text": "src/app.ts", "ok": True},
                                 {"n": 2, "kind": "thinking", "text": "hmm"}], "running": True})
        if m == "GET" and path.startswith("/api/jobs/"):
            return httpx.Response(404, json={"error": "no such job"})
        if m == "GET" and path == "/api/projects/shop/helper/sessions":
            return ok([self.session])
        if m == "POST" and path == "/api/projects/shop/helper/sessions":
            self.session = {**self.session, "mode": body["mode"], "title": body["title"], "messages": []}
            return ok(self.session)
        if m == "POST" and path == "/api/projects/shop/helper/sessions/s-1/turn":
            self.session["messages"] = [*self.session["messages"], {"role": "user", "text": body["text"]},
                                        {"role": "helper", "text": self.answer}]
            return ok({"session": "s-1", "call_id": "c-9"})
        if m == "GET" and path == "/api/projects/shop/helper/sessions/s-1":
            return ok(self.session)
        if m == "PATCH" and path == "/api/projects/shop/helper/sessions/s-1":
            self.session = {**self.session, **body}
            return ok(self.session)
        if m == "GET" and path == "/api/providers/models":
            from keel_engine.models import catalog
            return ok({"claude": {"modes": {"subscription": catalog.CLAUDE_SUBSCRIPTION}, "efforts": []}})
        if m == "GET" and path.endswith("/changes"):
            return ok([{"path": "docs/health.md", "status": "A", "added": 3, "removed": 0, "diff": "+# Health"}])
        if m == "POST" and path.endswith("/done"):
            return ok({"files": body.get("paths") or ["docs/health.md"], "message": "docs: health", "sha": "abc1234def", "checks": "npm test"})
        if m == "POST" and path.endswith("/undo"):
            return ok([])
        if m == "GET" and path == "/api/projects/shop/workflows":
            return ok([{"id": "feature", "name": "feature", "steps": [{"id": "spec"}]}])
        if m == "POST" and path == "/api/diagrams/ascii":
            return ok({"text": f"DIAGRAM {body['kind']} {sorted(body['data'])} w={body['width']}"})
        return httpx.Response(404, json={"error": f"stub: no {m} {path}"})


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.delenv("KEEL_PROJECT", raising=False)
    monkeypatch.chdir("/")
    s = Stub()
    s.api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(s))
    return s


async def call(srv, name, args=None) -> str:
    async with create_connected_server_and_client_session(srv) as session:
        res = await session.call_tool(name, args or {})
        assert not res.isError, res
        return res.content[0].text


def server(stub, level="write"):
    return mcp_server.build_server(level=level, api=stub.api, yes=True)


async def test_stop_flows_all_stops_only_the_running_and_waiting_ones(stub):
    text = await call(server(stub), "keel_stop_flows", {"all": True})
    stops = [p for m, p, _ in stub.calls if p.endswith("/stop")]
    assert stops == ["/api/threads/t-1aaaaaaa/stop", "/api/threads/t-2bbbbbbb/stop"]
    assert 'stopped "Player scores", "Logger"' in text


async def test_stop_flows_takes_ids_or_their_start(stub):
    text = await call(server(stub), "keel_stop_flows", {"flows": ["t-2bbb", "t-3ccccccc"]})
    assert [p for m, p, _ in stub.calls if p.endswith("/stop")] == ["/api/threads/t-2bbbbbbb/stop"]
    assert 'not running: "Old one" (stopped)' in text


async def test_stop_needs_the_persons_ok_when_the_client_cannot_ask(stub):
    srv = mcp_server.build_server(level="write", api=stub.api)
    text = await call(srv, "keel_stop_flows", {"all": True})
    assert "cannot ask you to confirm" in text and not any(p.endswith("/stop") for _, p, _ in stub.calls)


async def test_run_mode_shows_and_sets(stub):
    stub.thread["status"] = "waiting"
    shown = await call(server(stub), "keel_run_mode")
    assert '"Old one" runs in manual mode.' in shown and "auto: approves every gate" in shown
    text = await call(server(stub), "keel_run_mode", {"mode": "auto"})
    assert ("POST", "/api/threads/t-3ccccccc/mode", {"mode": "auto"}) in stub.calls
    assert "runs in auto mode from its next gate on" in text


async def test_resume_flow_goes_on_from_the_last_saved_step(stub):
    text = await call(server(stub), "keel_resume_flow")
    assert ("POST", "/api/threads/t-3ccccccc/rewind", {"checkpoint_id": "c2"}) in stub.calls
    assert 'after "red"' in text


async def test_agents_and_one_agent(stub):
    text = await call(server(stub, "read"), "keel_agents")
    assert "j-1  ▶ explorer (flow) · plan AC-2 · 1,000 tokens" in text
    assert 's-1  idle · "write the docs" · side · branch keel/helper/s-1' in text
    steps = await call(server(stub, "read"), "keel_agent", {"agent_id": "j-1"})
    assert "agent call j-1 · at work" in steps and "read_file ✓ src/app.ts" in steps and "hmm" not in steps
    chat = await call(server(stub, "read"), "keel_agent", {"agent_id": "s-1"})
    assert chat.startswith('agent "write the docs" · side · branch keel/helper/s-1')


async def test_agent_start_works_in_its_own_worktree(stub):
    text = await call(server(stub), "keel_agent_start", {"task": "write the docs for /health"})
    assert ("POST", "/api/projects/shop/helper/sessions", {"mode": "side", "title": "write the docs for /health"}) in stub.calls
    assert ("POST", "/api/projects/shop/helper/sessions/s-1/turn", {"text": "write the docs for /health"}) in stub.calls
    assert "agent s-1 started" in text and "its own worktree and branch" in text


async def test_keelbot_ask_waits_for_the_answer_and_names_its_buttons_as_tools(stub, monkeypatch):
    monkeypatch.setattr("keel_engine.mcp_tui.time.sleep", lambda _s: None)
    text = await call(server(stub), "keel_keelbot_ask", {"text": "stop the logger flow"})
    assert text.startswith("[KeelBot · chat s-1]\nDone: docs/health.md.")
    assert "stop: keel_stop_flows(flows=['t-2bbbbbbb'])" in text


async def test_keelbot_chats_changes_keep_and_undo(stub):
    assert 's-1  "write the docs" · side · 1 turns' in await call(server(stub, "read"), "keel_keelbot_chats")
    ch = await call(server(stub, "read"), "keel_keelbot_changes", {"chat": "s-1"})
    assert "A docs/health.md  +3 -0" in ch and "+# Health" in ch
    kept = await call(server(stub), "keel_keelbot_keep", {"chat": "s-1", "paths": ["docs/health.md"]})
    assert kept == "kept 1 file(s): docs: health (abc1234) · checks: npm test"
    assert "put back every file" in await call(server(stub), "keel_keelbot_undo", {"chat": "s-1"})


async def test_diagram_draws_with_keels_renderer(stub):
    text = await call(server(stub, "read"), "keel_diagram", {"kind": "flow"})
    assert text == "DIAGRAM flow ['thread', 'visited', 'workflow'] w=120"
    assert (await call(server(stub, "read"), "keel_diagram", {"kind": "workflow", "arg": "feature"})).startswith("DIAGRAM workflow ['workflow']")


async def test_keels_own_agents_get_none_of_the_acting_ones(stub):
    async with create_connected_server_and_client_session(server(stub, "read")) as s:
        names = {t.name for t in (await s.list_tools()).tools}
    assert {"keel_agents", "keel_agent", "keel_diagram", "keel_workflows"} <= names
    assert not names & {"keel_stop_flows", "keel_run_mode", "keel_agent_start", "keel_keelbot_ask", "keel_keelbot_keep"}


async def test_a_side_agent_says_which_model_it_runs_on(stub):
    """0.22.0 a field report: side agents ran on another model than the person thought, and nothing said so."""
    stub.session = {**stub.session, "model": {"provider": "claude", "mode": "subscription", "model": "sonnet"}}
    text = await call(server(stub), "keel_agent_start", {"task": "write the docs for /health"})
    assert "It runs on" in text and "sonnet" in text.lower() and "KeelBot's model" in text
    text = await call(server(stub), "keel_agent_start", {"task": "write the docs for /health", "model": "claude-opus-5-5"})
    assert ("PATCH", "/api/projects/shop/helper/sessions/s-1",
            {"model": {"provider": "claude", "mode": "subscription", "model": "claude-opus-5-5"}}) in stub.calls
    assert "asked for in this call" in text
