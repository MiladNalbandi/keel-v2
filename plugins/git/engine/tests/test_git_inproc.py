"""0.37 (#190) keel's harness runs the Git plugin's MCP server inside the engine: no process to list its tools or to call
one, the agent call's key decides as it did over POST /plugins/call, and an ended call is refused."""

import asyncio
import time

from keel_engine import extensions, partmcp
from keel_engine.tools import mcp

READ = {"git_status", "git_diff", "git_log", "git_show", "git_blame", "git_branches", "pr_status"}


def test_the_git_tools_run_in_the_engine_with_the_calls_key(repo, monkeypatch):
    from langchain_mcp_adapters.client import MultiServerMCPClient

    async def no_process(*_a, **_k):
        raise AssertionError("an in-process server must not be started")

    monkeypatch.setattr(MultiServerMCPClient, "get_tools", no_process)
    key = extensions.open_call(project="demo", root=str(repo), keys={}, plugins=["git"], who="keelbot")
    [spec] = extensions.server_specs(["git"], key)
    assert spec["inproc"] == {"module": "keel_plugin_git.server", "key": key}
    timing: dict = {}
    t0 = time.monotonic()
    tools, _ = asyncio.run(mcp.langchain_tools([spec], ["mcp:keel-git:*"], timing))
    assert {t.name for t in tools} == {f"mcp__keel-git__{n}" for n in READ}
    status = next(t for t in tools if t.name.endswith("__git_status"))
    assert status.metadata["annotations"]["readOnlyHint"] is True
    assert status.args_schema["type"] == "object"
    out = asyncio.run(status.ainvoke({}))
    assert "main" in out or "master" in out, out
    log = next(t for t in tools if t.name.endswith("__git_log"))
    assert asyncio.run(log.ainvoke({"n": 1})).strip()
    assert time.monotonic() - t0 < 5 and partmcp.INPROC_KEY.get() is None      # the key never leaks out of a call
    # a second turn lists from the built server, at once
    again: dict = {}
    asyncio.run(mcp.langchain_tools([spec], ["mcp:keel-git:git_status"], again))
    assert again["keel-git"] < 0.05
    extensions.close_call(key)
    assert asyncio.run(status.ainvoke({})).startswith("Refused: This tool call has no valid key")


def test_a_plugin_that_is_off_is_refused_in_process(repo):
    key = extensions.open_call(project="demo", root=str(repo), keys={}, plugins=[], who="keelbot")
    spec = {**extensions.server_specs(["git"], key)[0]}
    tools, _ = asyncio.run(mcp.langchain_tools([spec], ["mcp:keel-git:git_status"]))
    out = asyncio.run(tools[0].ainvoke({}))
    assert out.startswith("Refused: The git plugin is off") and "Settings › Plugins" in out
    extensions.close_call(key)
