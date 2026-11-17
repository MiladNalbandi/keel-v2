"""0.35 what a review or KeelBot turn waits for before its first answer: the MCP servers start side by side and a recent
tool list is used again; the harness writes one "start:" line; review-pr asks gh about the PR and fetches it once a run."""

from __future__ import annotations

import asyncio
import sys
import time
import types

from keel_engine.harness import runner as runner_mod
from keel_engine.runtime import ticket_actions
from keel_engine.tools import mcp


class _Tool:
    def __init__(self, name):
        self.name = name

    def model_copy(self, update=None):
        t = _Tool(self.name)
        for k, v in (update or {}).items():
            setattr(t, k, v)
        return t


def _fake_adapter(monkeypatch, delay: float, calls: list):
    class Client:
        def __init__(self, conns):
            self.conns = conns

        async def get_tools(self, server_name):
            calls.append(server_name)
            await asyncio.sleep(delay)
            return [_Tool("search"), _Tool("other")]

    mod = types.ModuleType("langchain_mcp_adapters.client")
    mod.MultiServerMCPClient = Client
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters", types.ModuleType("langchain_mcp_adapters"))
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", mod)
    monkeypatch.setattr(mcp, "_LISTED", {})


def test_mcp_servers_start_side_by_side_and_a_recent_list_is_kept(monkeypatch):
    calls: list = []
    _fake_adapter(monkeypatch, 0.3, calls)
    specs = [{"name": n, "command": "x", "args": [n]} for n in ("a", "b", "c")]
    timing: dict = {}
    t0 = time.monotonic()
    tools, _ = asyncio.run(mcp.langchain_tools(specs, ["a", "b", "c"], timing))
    took = time.monotonic() - t0
    assert took < 0.8, f"three servers of 0.3 s each took {took:.2f} s: they did not start side by side"
    assert sorted(t.name for t in tools)[:2] == ["mcp__a__other", "mcp__a__search"] and len(tools) == 6
    assert set(timing) == {"a", "b", "c"} and all(v >= 0.25 for v in timing.values())
    # the next turn: no server is started again, and the kept list keeps its own names
    timing2: dict = {}
    again, _ = asyncio.run(mcp.langchain_tools(specs, ["mcp:b:search"], timing2))
    assert calls.count("a") == 1 and [t.name for t in again] == ["mcp__b__search"]
    assert timing2 == {"b": 0.0}


def test_a_failed_server_is_not_kept(monkeypatch):
    calls: list = []
    _fake_adapter(monkeypatch, 0.0, calls)

    class Broken:
        def __init__(self, conns):
            pass

        async def get_tools(self, server_name):
            calls.append(server_name)
            raise RuntimeError("no such command")

    sys.modules["langchain_mcp_adapters.client"].MultiServerMCPClient = Broken
    specs = [{"name": "a", "command": "x"}]
    assert asyncio.run(mcp.langchain_tools(specs, ["a"]))[0] == []
    assert asyncio.run(mcp.langchain_tools(specs, ["a"]))[0] == []
    assert calls == ["a", "a"]


def test_the_start_line_says_where_the_wait_went():
    t = {"prepare": 0.31, "tools": 1.24, "servers": {"keel": 0.4, "codegraph": 1.2, "db": 0.0}, "git": 0.08,
         "t_model": 10.0}
    line = runner_mod.start_line(t, 12.4)
    assert line == ("start: prepare 0.3 s · tools 1.2 s (codegraph 1.2 s, keel 0.4 s, db kept) · git 0.1 s · "
                    "first answer 2.4 s")
    assert runner_mod.start_line({"servers": {}}, 0.0) == "start: "
    assert runner_mod.start_line({"t_model": 1.0, "session": 1.25}, 4.0) == \
        "start: first answer 3.0 s (opening the model's session 1.2 s)"
    assert runner_mod.start_line({"t_model": 1.0, "session": 0.02}, 4.0) == "start: first answer 3.0 s"


def test_review_pr_asks_about_the_pr_once_a_run(monkeypatch, tmp_path):
    asked: list = []

    def source_pr(a, n, force, cfg, base):
        asked.append(n)
        return {"pr": n, "ref": "HEAD", "base_ref": "HEAD"}, None

    monkeypatch.setattr(ticket_actions, "_source_pr", source_pr)
    monkeypatch.setattr(ticket_actions, "_ref_ok", lambda root, ref: True)
    monkeypatch.setattr(ticket_actions, "_SOURCES", {})
    a = types.SimpleNamespace(root=str(tmp_path))
    want = {"uncommitted": False, "pr": 7, "branch": None, "base": None, "force": False}
    ticket_actions._source(a, {}, want, keep=True)            # the preflight
    src, err = ticket_actions._source(a, {}, want)             # the run's prep a moment later
    assert asked == [7] and src["pr"] == 7 and not err
    ticket_actions._source(a, {}, want)                        # a later prep (a retry) asks again
    assert asked == [7, 7]
    # refs that went away are fetched again
    ticket_actions._source(a, {}, want, keep=True)
    monkeypatch.setattr(ticket_actions, "_ref_ok", lambda root, ref: False)
    ticket_actions._source(a, {}, want)
    assert asked == [7, 7, 7, 7]
