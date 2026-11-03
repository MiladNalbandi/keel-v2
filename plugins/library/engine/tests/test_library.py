"""The Library plugin's engine part: who it is, its MCP server and what its tools answer, with the api stood in for."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import yaml

from keel_engine import extensions
from keel_engine.pluginhost import manifest as manifests
from keel_plugin_library import VERSION, tools
from keel_plugin_library.server import build

PLUGIN = Path(__file__).resolve().parents[2]
HIT = {"doc_id": "l1", "title": "Data rules", "section": "Backups", "version": 3, "quote": "Backups run each night.\nThey stay 30 days."}


def test_manifest_and_version_agree_and_the_tools_only_read():
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())
    assert m.name == "library" and m.version == VERSION and m.migrations == "migrations"
    raw = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())
    assert raw["permissions"]["agent_tools"] == "read" and raw["per_project"] is True
    names = {t.name for t in asyncio.run(build().list_tools())}
    assert names == {"library_search", "library_read"}
    assert all(t.annotations.readOnlyHint for t in asyncio.run(build().list_tools()))


@pytest.fixture(autouse=True)
def part(monkeypatch):
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", str(PLUGIN / "engine"))
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_library")
    extensions.reload()
    yield
    monkeypatch.delenv("KEEL_PLUGIN_PATHS", raising=False)
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    extensions.reload()


def test_keel_knows_the_part_and_its_read_tools():
    p = extensions.part("library")
    assert p is not None and p.per_project and set(p.read_tools) == {"library_search", "library_read"}
    assert extensions.servers().get("library") == "keel-library"


def test_it_is_refused_for_a_call_where_the_plugin_is_off():
    key = extensions.open_call(project="p1", root="/tmp", keys={}, plugins=[], who="agent:x")
    with pytest.raises(extensions.PartError) as e:
        extensions.call(key, "library_search", {"query": "backups"})
    assert e.value.status == 403
    extensions.close_call(key)


class Answer:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def stand_in(monkeypatch, status=200, body=None):
    seen = {}

    def get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers)
        return Answer(status, body)
    monkeypatch.setattr(httpx, "get", get)
    return seen


def test_search_quotes_with_its_source_for_the_calls_own_project(monkeypatch):
    seen = stand_in(monkeypatch, body=[HIT])
    key = extensions.open_call(project="p1", root="/tmp", keys={}, plugins=["library"], who="agent:x")
    text = extensions.call(key, "library_search", {"query": "backups", "project": "someone-else"})
    extensions.close_call(key)
    assert "/projects/p1/library/agent/search" in seen["url"]
    assert seen["params"]["q"] == "backups"
    assert "library:l1 Data rules › Backups (version 3)" in text
    assert "> Backups run each night.\n> They stay 30 days." in text


def test_a_search_that_finds_nothing_says_so_and_warns_against_guessing(monkeypatch):
    stand_in(monkeypatch, body=[])
    text = tools.search("p1", "pets")
    assert 'says nothing about "pets"' in text and "Do not fill the gap" in text
    assert tools.search("p1", "  ") == "Say what to look for."


def test_read_gives_one_document_and_a_refusal_says_so(monkeypatch):
    seen = stand_in(monkeypatch, body={"id": "l1", "title": "Data rules", "version": 2, "body": "All of it."})
    assert "All of it." in tools.read("p1", "library:l1")
    assert seen["url"].endswith("/projects/p1/library/agent/docs/l1")
    stand_in(monkeypatch, status=404)
    assert "no such document" in tools.read("p1", "nope")
    assert tools.read("p1", "") .startswith("Say which")


def test_the_api_being_down_is_a_sentence_not_a_crash(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx, "get", boom)
    assert "cannot be reached" in tools.search("p1", "x")
    assert "cannot be reached" in tools.read("p1", "x")
