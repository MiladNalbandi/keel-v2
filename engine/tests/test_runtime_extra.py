import json

import httpx
from fastapi.testclient import TestClient

from conftest import start, to_loop, wait
from keel_engine.app import create_app
from keel_engine.events import EventBus


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


