"""The Links plugin's engine part: its tools against a stand-in Linear. The rule that matters most: a write happens only after a
person's yes in keel's approvals, with the exact words shown, and the key never appears in an answer."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import httpx
import pytest
import yaml

from keel_engine import approvals, extensions
from keel_engine.events import EventBus
from keel_engine.pluginhost import manifest as manifests
from keel_plugin_links import VERSION, tools
from keel_plugin_links.server import build

PLUGIN = Path(__file__).resolve().parents[2]
KEY = "lin_api_SECRETKEY"
KEYS = {"links:linear:token": KEY, "links:linear:url": "https://linear.test/graphql"}
ISSUE = {"id": "uuid-1", "identifier": "ENG-7", "title": "Export is empty", "description": "It is empty.", "url": "https://linear.test/ENG-7",
         "state": {"name": "Todo"}, "comments": {"nodes": [{"body": "Seen on Monday", "createdAt": "2026-10-01"}]}}


class Answer:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Stand:
    """Answers like Linear, and keeps what it was asked."""

    def __init__(self):
        self.calls: list[dict] = []
        self.answer: Answer | None = None
        self.boom = False

    def post(self, url, json=None, headers=None, timeout=None, follow_redirects=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "follow": follow_redirects})
        if self.boom:
            raise httpx.ConnectError(f"cannot reach {url} with {headers}")
        if self.answer:
            return self.answer
        q = json["query"]
        if "searchIssues" in q:
            return Answer(body={"data": {"searchIssues": {"nodes": [ISSUE] if json["variables"]["term"] != "nothing" else []}}})
        if "commentCreate" in q:
            return Answer(body={"data": {"commentCreate": {"success": True, "comment": {"url": "https://linear.test/ENG-7#c"}}}})
        if "issueCreate" in q:
            return Answer(body={"data": {"issueCreate": {"success": True, "issue": {"identifier": "ENG-8", "url": "https://linear.test/ENG-8"}}}})
        if "teams" in q:
            return Answer(body={"data": {"teams": {"nodes": [{"id": "team-1"}] if json["variables"]["key"] == "ENG" else []}}})
        return Answer(body={"data": {"issue": ISSUE if json["variables"]["id"] == "ENG-7" else None}})

    def writes(self):
        return [c for c in self.calls if "mutation" in c["json"]["query"]]


@pytest.fixture
def stand(monkeypatch):
    s = Stand()
    monkeypatch.setattr(httpx, "post", s.post)
    return s


@pytest.fixture
def bus():
    return EventBus()


def call_with(bus, keys=KEYS):
    return {"project": "p1", "keys": keys, "bus": bus, "thread": "t1", "session": None}


def answer_when_asked(bus, decision, why=""):
    """A person, in another thread: waits for the question and answers it; returns what was asked."""
    broker = approvals.of(bus)
    asked: dict = {}

    def person():
        for _ in range(200):
            waiting = list(broker.questions.values())
            if waiting:
                asked.update(waiting[0])
                broker.answer(waiting[0]["id"], decision, why)
                return
            time.sleep(0.01)
    t = threading.Thread(target=person)
    t.start()
    return asked, t


def test_manifest_version_and_its_tools():
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())
    assert m.name == "links" and m.version == VERSION
    raw = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())
    assert raw["permissions"]["agent_tools"] == "act" and raw["permissions"]["secrets"] == ["links-linear"]
    listed = {t.name: t.annotations.readOnlyHint for t in asyncio.run(build().list_tools())}
    assert listed == {"links_search": True, "links_issue": True, "links_comment": False, "links_create_issue": False}


def test_the_writing_tools_are_not_read_tools(monkeypatch):
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", str(PLUGIN / "engine"))
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_links")
    extensions.reload()
    p = extensions.part("links")
    assert set(p.read_tools) == {"links_search", "links_issue"} and set(p.act_tools) == {"links_comment", "links_create_issue"}
    monkeypatch.delenv("KEEL_PLUGIN_PATHS")
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS")
    extensions.reload()


def test_without_a_key_it_says_so_and_calls_nothing(stand, bus):
    c = call_with(bus, keys={})
    for tool, args in (("links_search", {"query": "x"}), ("links_issue", {"key": "ENG-7"}), ("links_comment", {"key": "ENG-7", "text": "hi"}),
                       ("links_create_issue", {"team": "ENG", "title": "t"})):
        assert "not linked" in tools.call_tool(c, tool, args)
    assert stand.calls == []


def test_reading_needs_no_ok_and_shows_no_names(stand, bus):
    c = call_with(bus)
    found = tools.call_tool(c, "links_search", {"query": "export"})
    assert "ENG-7 [Todo] Export is empty" in found
    assert "no issue" in tools.call_tool(c, "links_search", {"query": "nothing"})
    one = tools.call_tool(c, "links_issue", {"key": "ENG-7"})
    assert "It is empty." in one and "Seen on Monday" in one
    assert stand.writes() == [] and not approvals.of(bus).questions
    # the key goes to Linear, in its header, and to nowhere else; a redirect is not followed
    assert stand.calls[0]["headers"]["Authorization"] == KEY and stand.calls[0]["follow"] is False
    assert KEY not in found + one
    assert "assignee" not in stand.calls[0]["json"]["query"] and "creator" not in stand.calls[-1]["json"]["query"]


def test_a_comment_waits_for_the_persons_yes_and_shows_the_exact_words(stand, bus):
    asked, t = answer_when_asked(bus, "once")
    out = tools.call_tool(call_with(bus), "links_comment", {"key": "ENG-7", "text": "Fixed in the next release."})
    t.join()
    assert asked["kind"] == "plugin" and "ENG-7" in asked["title"] and "Fixed in the next release." in asked["command"]
    assert asked["source"] == "plugin:links" and asked["project"] == "p1"
    assert "The comment is on ENG-7" in out
    writes = stand.writes()
    assert len(writes) == 1 and writes[0]["json"]["variables"]["input"] == {"issueId": "uuid-1", "body": "Fixed in the next release."}


def test_a_no_writes_nothing_and_the_agent_hears_why(stand, bus):
    asked, t = answer_when_asked(bus, "deny", "not yet")
    out = tools.call_tool(call_with(bus), "links_comment", {"key": "ENG-7", "text": "hello"})
    t.join()
    assert stand.writes() == []
    assert "Nothing was written" in out and "not yet" in out


def test_nobody_answering_writes_nothing(stand, bus, monkeypatch):
    monkeypatch.setattr(approvals.permissions, "ASK_TIMEOUT", 0.05)
    out = tools.call_tool(call_with(bus), "links_comment", {"key": "ENG-7", "text": "hello"})
    assert stand.writes() == [] and "Nothing was written" in out


def test_without_a_way_to_ask_the_answer_is_no(stand):
    out = tools.call_tool({"project": "p1", "keys": KEYS, "bus": None}, "links_comment", {"key": "ENG-7", "text": "hello"})
    assert stand.writes() == [] and "cannot ask" in out


def test_a_new_issue_waits_too_and_a_wrong_team_asks_nobody(stand, bus):
    assert "no team" in tools.call_tool(call_with(bus), "links_create_issue", {"team": "NOPE", "title": "t"})
    assert not approvals.of(bus).questions and stand.writes() == []
    asked, t = answer_when_asked(bus, "once")
    out = tools.call_tool(call_with(bus), "links_create_issue", {"team": "ENG", "title": "Export broken", "text": "Steps"})
    t.join()
    assert "Export broken" in asked["command"] and "Steps" in asked["command"]
    assert "The issue ENG-8 is made." in out
    assert stand.writes()[0]["json"]["variables"]["input"] == {"teamId": "team-1", "title": "Export broken", "description": "Steps"}


def test_bad_input_asks_nobody(stand, bus):
    c = call_with(bus)
    assert "Say which" in tools.call_tool(c, "links_comment", {"key": "ENG-7", "text": " "})
    assert "too long" in tools.call_tool(c, "links_comment", {"key": "ENG-7", "text": "x" * (tools.TEXT_MAX + 1)})
    assert "no issue" in tools.call_tool(c, "links_comment", {"key": "ENG-9", "text": "hello"})
    assert not approvals.of(bus).questions


@pytest.mark.parametrize("answer, words", [(Answer(401, {}), "refused the key"), (Answer(500), "answered 500"),
                                           (Answer(200, {"errors": [{"message": "bad query"}]}), "Linear said: bad query")])
def test_a_failure_is_a_sentence_without_the_key(stand, bus, answer, words):
    stand.answer = answer
    out = tools.call_tool(call_with(bus), "links_search", {"query": "x"})
    assert words in out and KEY not in out


def test_a_network_failure_does_not_repeat_the_exception_with_the_key(stand, bus):
    stand.boom = True
    out = tools.call_tool(call_with(bus), "links_search", {"query": "x"})
    assert "cannot be reached" in out and KEY not in out and "linear.test" not in out and json.dumps(stand.calls[0]["json"])
