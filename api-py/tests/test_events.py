"""The live event stream and the hub the Kotlin api posts its events to (events/EventHub.kt, EventsApiTest)."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest
import uvicorn
from conftest import TOKEN, make_keel

from keel_api.app import create_app
from keel_api.events import hub as hub_module
from keel_api.events.hub import EventHub, Sub, channel_of, frame

INTERNAL = {"X-Keel-Token": TOKEN}


class Live:
    """The api on a real port: Starlette's TestClient reads a whole answer, and a stream never ends."""

    def __init__(self, tmp_path):
        self.keel = make_keel(tmp_path)
        config = uvicorn.Config(create_app(ctx=self.keel.ctx), host="127.0.0.1", port=0, log_level="warning",
                                proxy_headers=False, timeout_graceful_shutdown=1)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            time.sleep(0.01)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.client = httpx.Client(base_url=self.url, timeout=5)

    @property
    def hub(self) -> EventHub:
        return self.keel.ctx.hub

    def wait_subs(self, n: int) -> None:
        end = time.monotonic() + 5
        while self.hub.count() != n:
            assert time.monotonic() < end, f"{self.hub.count()} subscribers, not {n}"
            time.sleep(0.01)

    def post(self, *events, headers=INTERNAL) -> httpx.Response:
        return self.client.post("/internal/hub", headers=headers, json=list(events))

    def close(self) -> None:
        self.client.close()
        self.server.should_exit = True
        self.thread.join(5)


@pytest.fixture
def live(tmp_path):
    lv = Live(tmp_path)
    yield lv
    lv.close()


def read_until(chunks, want: bytes) -> bytes:
    got = b""
    for c in chunks:
        got += c
        if want in got:
            return got
    raise AssertionError(f"the stream ended before {want!r}: {got!r}")


def ev(project, type_, data) -> dict:
    return {"project": project, "type": type_, "data": json.dumps(data, separators=(",", ":"), ensure_ascii=False)}


def test_a_stream_starts_with_connected_and_carries_the_kotlin_apis_events_as_spring_wrote_them(live):
    with live.client.stream("GET", "/api/events?project=p1", headers=INTERNAL) as r:
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/event-stream"
        chunks = r.iter_raw()
        assert read_until(chunks, b":connected\n\n") == b":connected\n\n"
        live.wait_subs(1)
        answer = live.post(ev("p2", "step.started", {"n": 2}), ev("p1", "step.started", {"n": 1, "name": "é"}))
        assert answer.json() == {"ok": True, "subscribers": 1}
        got = read_until(chunks, b'"n":1')
        assert got == 'event:step.started\ndata:{"n":1,"name":"é"}\n\n'.encode()
    live.wait_subs(0)


def test_each_kind_of_stream_gets_its_own_events(live):
    streams = {}
    with (live.client.stream("GET", "/api/events?project=*", headers=INTERNAL) as light,
          live.client.stream("GET", "/api/events?project=p1&notify=all", headers=INTERNAL) as tab,
          live.client.stream("GET", "/api/events", headers=INTERNAL) as everything):
        for name, r in (("light", light), ("tab", tab), ("all", everything)):
            streams[name] = r.iter_raw()
            read_until(streams[name], b":connected\n\n")
        live.wait_subs(3)
        live.post(ev("p2", "agent.step", {"a": 1}), ev("p2", "notification", {"b": 2}), ev(None, "project.changed", {"c": 3}),
                  ev("p1", "agent.step", {"d": 4}))
        assert read_until(streams["all"], b'"d":4').count(b"event:") == 4
        # the bell's events from every project, nothing else
        assert read_until(streams["light"], b'"c":3') == b'event:notification\ndata:{"b":2}\n\nevent:project.changed\ndata:{"c":3}\n\n'
        # its own project in full, the bell's events from the others
        assert read_until(streams["tab"], b'"d":4') == (
            b'event:notification\ndata:{"b":2}\n\nevent:project.changed\ndata:{"c":3}\n\nevent:agent.step\ndata:{"d":4}\n\n')


def test_this_apis_own_routes_publish_from_any_thread(live):
    with live.client.stream("GET", "/api/events?project=p1", headers=INTERNAL) as r:
        chunks = r.iter_raw()
        read_until(chunks, b":connected\n\n")
        live.wait_subs(1)
        t = threading.Thread(target=live.hub.publish, args=("p1", "notification", {"title": "done", "read": False, "x": None}))
        t.start()
        t.join()
        assert read_until(chunks, b"done") == b'event:notification\ndata:{"title":"done","read":false,"x":null}\n\n'


def test_a_quiet_stream_is_pinged(live, monkeypatch):
    monkeypatch.setattr(hub_module, "PING_S", 0.05)
    with live.client.stream("GET", "/api/events", headers=INTERNAL) as r:
        assert read_until(r.iter_raw(), b":ping\n\n") == b":connected\n\n:ping\n\n"


def test_only_keels_kotlin_api_may_post_to_the_hub(live):
    person = {"Authorization": f"Bearer {live.keel.ctx.key.value}"}
    r = live.post(ev("p1", "x", {}), headers=person)
    assert r.status_code == 403 and r.json()["error"] == "Only keel's Kotlin api can send events to the hub"
    assert live.post(headers={}).status_code == 401
    assert live.client.post("/internal/hub", headers=INTERNAL, content=b"{nope").status_code == 400
    r = live.client.post("/internal/hub", headers=INTERNAL, json=[{"type": "x", "data": {"not": "text"}}])
    assert r.status_code == 400 and r.json()["error"] == "Each event needs a type and its data as JSON text"


def test_the_stream_needs_a_sign_in(tmp_path):
    k = make_keel(tmp_path)
    with k.client as c:
        assert c.get("/api/events").status_code == 401


def test_a_client_that_falls_far_behind_is_dropped(monkeypatch):
    monkeypatch.setattr(hub_module, "QUEUE", 3)

    async def run():
        hub = EventHub()
        slow = hub.subscribe(None)
        for i in range(5):
            hub.publish("p", "agent.delta", {"i": i})
        return hub, slow

    hub, slow = asyncio.run(run())
    assert slow.dropped and hub.count() == 0


def test_frames_and_channels_are_spring_and_kotlins_own():
    assert frame("x", '{"a":"b"}') == b'event:x\ndata:{"a":"b"}\n\n'
    assert frame("x", "1\n2") == b"event:x\ndata:1\ndata:2\n\n"
    assert channel_of("helper.delta") == "helper.delta" and channel_of("approval.asked") == "engine"
    assert not Sub("p1").wants("p2", "notification")
    assert Sub("p1", notify_all=True).wants("p2", "notification") and Sub(None).wants("p2", "agent.step")
