"""Approvals (keel_engine/approvals.py): keel's one place to ask a person and wait, its events and its routes."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from keel_engine import approvals, hook
from keel_engine.events import EventBus
from keel_engine.runtime import permissions


@pytest.fixture
def broker():
    return approvals.Approvals(EventBus())


def events(b, type_):
    return [e for e in b.bus.recent if e["type"] == type_]


def answer_soon(b, decision, why="", project=None):
    """Answers the first question that waits, from another thread (as the person would, through the Inbox)."""
    def run():
        deadline = time.time() + 10
        while not b.pending(project) and time.time() < deadline:
            time.sleep(0.01)
        b.answer(b.pending(project)[0]["id"], decision, why)
    t = threading.Thread(target=run)
    t.start()
    return t


def test_ask_waits_for_the_person_and_both_events_carry_the_question(broker):
    t = answer_soon(broker, "once")
    got = asyncio.run(broker.ask("command", "demo", "Fix the totals", "npm install left-pad", source="keelbot",
                                 thread_id="t-1", session="h_1", path="package.json"))
    t.join(5)
    assert got == {"decision": "allow", "why": ""}
    asked, answered = events(broker, "approval.asked")[0], events(broker, "approval.answered")[0]
    assert asked["thread_id"] == "h_1" and asked["project_id"] == "demo" and asked["step"] == "approval"
    q = asked["data"]
    assert {k: q[k] for k in ("kind", "project", "title", "command", "source", "thread_id", "session", "path")} == {
        "kind": "command", "project": "demo", "title": "Fix the totals", "command": "npm install left-pad", "source": "keelbot",
        "thread_id": "t-1", "session": "h_1", "path": "package.json"}
    assert q["id"].startswith("p_") and q["at"]
    a = answered["data"]
    assert a["id"] == q["id"] and a["decision"] == "once" and a["status"] == "approved" and a["by"] == "person"
    assert a["source"] == "keelbot" and a["thread_id"] == "t-1"
    assert broker.pending() == []


def test_a_deny_carries_the_reason_and_a_bare_deny_gets_one(broker):
    t = answer_soon(broker, "deny", "keel pushes, not you")
    assert asyncio.run(broker.ask("command", "demo", "t", "git push", source="keelbot")) == {
        "decision": "deny", "why": "keel pushes, not you"}
    t.join(5)
    t = answer_soon(broker, "deny")
    assert asyncio.run(broker.ask("command", "demo", "t", "git push", source="keelbot"))["why"] == approvals.DENY_WHY
    t.join(5)
    assert [e["data"]["status"] for e in events(broker, "approval.answered")] == ["denied", "denied"]


def test_nobody_answering_in_time_is_a_deny_and_the_question_expires(broker, monkeypatch):
    monkeypatch.setattr(permissions, "ASK_TIMEOUT", 0.05)          # the default timeout is read at ask time
    assert asyncio.run(broker.ask("command", "demo", "t", "rm -rf build", source="keelbot")) == {
        "decision": "deny", "why": approvals.TIMEOUT_WHY}
    assert broker.ask_blocking("command", "demo", "t", "rm -rf dist", source="keelbot", timeout=0.05)["decision"] == "deny"
    assert broker.pending() == []
    assert [(e["data"]["status"], e["data"]["by"], e["data"]["decision"]) for e in events(broker, "approval.answered")] == [
        ("expired", "keel", None), ("expired", "keel", None)]
    with pytest.raises(approvals.ApprovalError) as late:
        broker.answer(events(broker, "approval.asked")[0]["data"]["id"], "once")
    assert late.value.status == 404


def test_the_blocking_twin_waits_in_a_worker_thread(broker):
    t = answer_soon(broker, "always")
    got = broker.ask_blocking("command", "demo", "t", "npm test", source="keelbot", session="h_2")
    t.join(5)
    assert got == {"decision": "allow", "why": ""}
    assert events(broker, "approval.answered")[0]["data"]["decision"] == "always"


def test_on_answer_hears_the_persons_word_once(broker):
    heard = []
    q = broker.open("command", "demo", "t", "npm i x", source="keelbot", session="h_3",
                    on_answer=lambda q, said, ans: heard.append((q["id"], said, ans["decision"])))
    assert broker.answer(q["id"], "always") == {"id": q["id"], "decision": "always"}
    assert heard == [(q["id"], "always", "allow")]
    with pytest.raises(approvals.ApprovalError):
        broker.answer(q["id"], "once")                           # answered already
    assert heard == [(q["id"], "always", "allow")]


def test_pending_asked_and_bad_answers(broker):
    a = broker.open("plugin", "shop", "Claude Code: push?", "push the branch", source="mcp", session="mcp")
    b = broker.open("command", "other", "t", "rm x", source="keelbot", session="h_4")
    assert [q["id"] for q in broker.pending("shop")] == [a["id"]]
    assert {q["id"] for q in broker.pending()} == {a["id"], b["id"]}
    assert broker.asked(a["id"]) == {"id": a["id"], "waiting": True}
    with pytest.raises(approvals.ApprovalError) as bad:
        broker.answer(a["id"], "maybe")
    assert bad.value.status == 400
    with pytest.raises(approvals.ApprovalError) as missing:
        broker.answer("p_000000000000", "once")
    assert missing.value.status == 404
    broker.answer(a["id"], "once")
    assert broker.asked(a["id"]) == {"id": a["id"], "decision": "allow", "why": ""}
    with pytest.raises(approvals.ApprovalError):
        broker.asked(a["id"])                                    # read once


def test_closing_a_sessions_questions_denies_them_with_the_reason(broker):
    got = {}
    t = threading.Thread(target=lambda: got.update(broker.ask_blocking("command", "demo", "t", "make", source="keelbot", session="h_5")))
    t.start()
    deadline = time.time() + 5
    while not broker.pending() and time.time() < deadline:
        time.sleep(0.01)
    other = broker.open("command", "demo", "t", "make", source="keelbot", session="h_6")
    assert broker.close(session="h_5", why="The person stopped KeelBot.") == 1
    t.join(5)
    assert got == {"decision": "deny", "why": "The person stopped KeelBot."}
    assert [q["id"] for q in broker.pending()] == [other["id"]]
    assert events(broker, "approval.answered")[0]["data"]["status"] == "closed"


def test_one_broker_per_bus():
    bus = EventBus()
    assert approvals.of(bus) is approvals.of(bus) and approvals.of(bus) is not approvals.of(EventBus())


# ------------------------------------------------------------------ routes


def test_the_routes_list_answer_and_poll(client):
    broker = approvals.of(client.bus)
    q = broker.open("plugin", "demo", "Claude Code: push?", "push the branch", source="mcp", session="mcp")
    listed = client.get("/approvals", params={"project": "demo"}).json()
    assert [x["id"] for x in listed] == [q["id"]] and listed[0]["source"] == "mcp"
    assert client.get("/approvals", params={"project": "other"}).json() == []
    assert client.get(f"/approvals/{q['id']}").json() == {"id": q["id"], "waiting": True}
    assert client.post(f"/approvals/{q['id']}", json={"decision": "maybe"}).status_code == 400
    r = client.post(f"/approvals/{q['id']}", json={"decision": "deny", "why": "not on Friday"})
    assert r.json() == {"id": q["id"], "decision": "deny"}
    assert client.get(f"/approvals/{q['id']}").json() == {"id": q["id"], "decision": "deny", "why": "not on Friday"}
    gone = client.post(f"/approvals/{q['id']}", json={"decision": "once"})
    assert gone.status_code == 404 and "answered already" in gone.json()["error"]
    assert client.get("/approvals/p_000000000000").status_code == 404


def test_the_routes_need_the_internal_token(client, monkeypatch):
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "secret-token")
    assert client.get("/approvals").status_code == 401
    assert client.post("/approvals/p_1", json={"decision": "once"}).status_code == 401
    assert client.get("/approvals", headers={"X-Keel-Token": "secret-token"}).status_code == 200


def test_plugin_asks_reach_the_broker_with_the_events_keel_sent_before(client):
    """keel2 mcp --write asks through core (no KeelBot needed); KeelBot's old routes reach the same broker
    (plugins/keelbot/engine/tests)."""
    q = client.post("/plugins/ask", json={"project": "demo", "title": "Claude Code: push the branch?", "command": "push"}).json()
    assert q["source"] == "mcp" and q["session"] == approvals.MCP_SESSION == "mcp"
    assert [x["id"] for x in client.get("/approvals").json()] == [q["id"]]
    assert client.get("/helper/permissions").status_code == 404             # KeelBot's route: its plugin is not here
    assert client.post(f"/approvals/{q['id']}", json={"decision": "once"}).status_code == 200
    assert client.get(f"/plugins/ask/{q['id']}").json() == {"id": q["id"], "decision": "allow", "why": ""}
    # the events keel 0.15.1 sent for it still go out (the api's notification), next to approval.*
    types = [e["type"] for e in client.bus.recent]
    assert types == ["approval.asked", "helper.permission", "helper.permission.answered", "approval.answered"]
    asked = next(e for e in client.bus.recent if e["type"] == "helper.permission")
    assert asked["thread_id"] == "mcp" and asked["step"] == "helper" and asked["data"]["id"] == q["id"]


# ------------------------------------------------------------------ the hook's ask path


class _Asked(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _Asked.seen.append((self.path, body))
        out = json.dumps({"decision": "deny", "why": "not now"} if "rm" in body["command"] else {"decision": "allow"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_the_hook_asks_at_the_url_its_context_names(repo):
    srv = HTTPServer(("127.0.0.1", 0), _Asked)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _Asked.seen = []
        url = f"http://127.0.0.1:{srv.server_port}/helper/permissions/ask"
        ctx = {"root": str(repo), "phase": "green", "unlocks": [], "ask": {"url": url, "key": "k1", "session": "h_1"}}
        assert hook.decide("Bash", {"command": "rm -rf build"}, dict(ctx)) == "not now"
        assert hook.decide("Bash", {"command": "mkdir -p notes"}, dict(ctx)) is None
        assert hook.decide("Bash", {"command": "git status"}, dict(ctx)) is None                 # changes nothing: not asked
        assert [(p, b["command"], b["key"], b["session"], b["kind"]) for p, b in _Asked.seen] == [
            ("/helper/permissions/ask", "rm -rf build", "k1", "h_1", "command"),
            ("/helper/permissions/ask", "mkdir -p notes", "k1", "h_1", "command")]
    finally:
        srv.shutdown()
