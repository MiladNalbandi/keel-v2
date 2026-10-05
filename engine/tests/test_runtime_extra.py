import json

import httpx
from fastapi.testclient import TestClient

from conftest import decide, start, to_loop, wait
from keel_engine.app import create_app
from keel_engine.events import EventBus
from keel_engine.models.cli_runners import ClaudeStream, codex_line


def test_thread_survives_engine_restart(repo):
    with TestClient(create_app(EventBus())) as c1:
        tid = start(c1, repo)
        assert wait(c1, tid)["waiting"]["step"] == "spec_gate"
    with TestClient(create_app(EventBus())) as c2:
        s = c2.get(f"/threads/{tid}").json()
        assert s["status"] == "waiting" and s["waiting"]["step"] == "spec_gate"
        s = to_loop(c2, tid, s)
        assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-1"


async def test_events_are_posted_with_token(monkeypatch):
    monkeypatch.setenv("KEEL_API_URL", "http://api.test")
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "tok")
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.url.path, request.headers.get("X-Keel-Token"), json.loads(request.content)))
        return httpx.Response(204)

    bus = EventBus()
    bus.emit("thread.started", "t1", "p1", data={"x": 1})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await bus._send_once(client=client)
    assert seen[0][0] == "/internal/events" and seen[0][1] == "tok"
    assert seen[0][2][0]["type"] == "thread.started" and seen[0][2][0]["project_id"] == "p1"


async def test_events_kept_when_api_down(monkeypatch):
    monkeypatch.setenv("KEEL_API_URL", "http://api.test")

    def down(request):
        raise httpx.ConnectError("refused")

    bus = EventBus()
    bus.emit("thread.started", "t1", "p1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        assert not await bus._send_once(client=client)
    assert len(bus._pending) == 1


def test_claude_stream_parsing(tmp_path):
    steps = []
    s = ClaudeStream(lambda kind, text="", **kw: steps.append((kind, text, kw)), str(tmp_path))
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking."},
                                                      {"type": "tool_use", "id": "1", "name": "mcp__keel__keel_next", "input": {}},
                                                      {"type": "tool_use", "id": "2", "name": "Write",
                                                       "input": {"file_path": str(tmp_path / "a.py"), "content": "x = 1\n"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "1", "content": "red"}]}},
        {"type": "result", "result": "done", "usage": {"input_tokens": 10, "output_tokens": 2}},
    ]
    for line in lines:
        s.line(json.dumps(line))
    kinds = [k for k, _, _ in steps]
    # v0.3: a tool call is one step, paired with its result; calls without a result are flushed at the end
    assert kinds == ["text", "tool", "write"]
    assert steps[1][2]["server"] == "keel" and steps[1][2]["output"] == "red"
    assert steps[2][2]["path"] == "a.py" and "+x = 1" in steps[2][2]["diff"]
    assert s.result["result"] == "done"


def test_codex_stream_parsing(tmp_path):
    steps, state = [], {}
    emit = lambda kind, text="", **kw: steps.append((kind, kw))  # noqa: E731
    for ev in [
        {"type": "item.started", "item": {"type": "command_execution", "command": "pytest -q"}},
        {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 1, "aggregated_output": "1 failed"}},
        {"type": "item.completed", "item": {"type": "file_change", "changes": [{"kind": "add", "path": str(tmp_path / "b.py")}]}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "All done."}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 3}},
    ]:
        codex_line(emit, str(tmp_path), ev, state)
    assert [k for k, _ in steps] == ["tool", "write", "text"]
    assert steps[0][1]["output"] == "1 failed" and steps[0][1]["ok"] is False
    assert state["usage"]["output_tokens"] == 3 and state["text"] == "All done."
