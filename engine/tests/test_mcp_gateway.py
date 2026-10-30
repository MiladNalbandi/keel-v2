"""0.19.0 harness mode: keel's own MCP server is the gateway to the project's connected MCP servers
(keel_engine/mcp_gateway.py). The harness gets keel's server alone; keel re-exports the other servers' tools, runs a
read-only one at once and asks the person in its Inbox before any other."""

import json
import sys
import textwrap

from conftest import start, wait
from test_flows_base import answers
from keel_engine.tools import mcp as mcp_tools

FAKE = textwrap.dedent("""
    import sys
    from pathlib import Path
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    app = FastMCP("tickets")
    marker = Path(sys.argv[1])

    @app.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def lookup(ticket: str) -> str:
        "Read one ticket."
        return f"ticket {ticket}: open"

    @app.tool()
    def create(title: str) -> str:
        "Open a ticket."
        marker.write_text(title)
        return "created"

    app.run()
""")


def fake_server(tmp_path, name="tickets", tools=None) -> dict:
    script = tmp_path / "tickets.py"
    script.write_text(FAKE)
    return {"name": name, "command": sys.executable, "args": [str(script), str(tmp_path / f"{name}-created")],
            "tools": tools}


def keel_spec(via: list[dict], *flags: str) -> dict:
    spec = mcp_tools.keel_server_spec()
    spec["args"] = ["-m", "keel_engine.mcp", *(flags or ("--read-only",))]
    spec["env"] = {**spec["env"], "KEEL_API_URL": "http://127.0.0.1:9", mcp_tools.GATEWAY_ENV: json.dumps(via)}
    return spec


async def session_call(spec: dict, calls: list[tuple[str, dict]]) -> tuple[set[str], list[str], dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=spec["command"], args=spec["args"], env=mcp_tools._server_env(spec))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            listed = (await s.list_tools()).tools
            out = []
            for name, args in calls:
                res = await s.call_tool(name, args)
                out.append(("error: " if res.isError else "") + "".join(getattr(c, "text", "") for c in res.content))
            return {t.name for t in listed}, out, {t.name: t for t in listed}


async def test_keels_server_re_exports_a_connected_servers_tools(tmp_path):
    via = [fake_server(tmp_path), {"name": "broken", "command": "/does/not/exist", "args": []}]
    names, out, tools = await session_call(keel_spec(via), [("tickets__lookup", {"ticket": "7"}),
                                                             ("tickets__create", {"title": "new"})])
    assert {"tickets__lookup", "tickets__create", "keel_status"} <= names
    assert not any(n.startswith("broken__") for n in names)            # a server that does not start is left out
    assert tools["tickets__lookup"].inputSchema["properties"]["ticket"]["type"] == "string"
    assert tools["tickets__lookup"].description.startswith("[tickets] Read one ticket.")
    assert out[0] == "ticket 7: open"                                   # read-only: runs at once
    # not read-only: the person's OK in keel's Inbox first (the api is down here: not done)
    assert out[1].startswith("keel: not done") and "keel's Inbox" in out[1]
    assert not (tmp_path / "tickets-created").exists()


async def test_with_yes_a_write_tool_runs_and_the_tools_page_limits_the_tools(tmp_path):
    names, out, _ = await session_call(keel_spec([fake_server(tmp_path)], "--read-only", "--yes"),
                                       [("tickets__create", {"title": "new"})])
    assert out == ["created"] and (tmp_path / "tickets-created").read_text() == "new"
    names, _, _ = await session_call(keel_spec([fake_server(tmp_path, tools=["lookup"])]), [])
    assert "tickets__lookup" in names and "tickets__create" not in names


def test_no_gateway_without_the_env():
    from keel_engine import mcp_gateway

    assert mcp_gateway.configured({}) == []
    assert mcp_gateway.configured({mcp_tools.GATEWAY_ENV: "not json"}) == []
    assert mcp_gateway.configured({mcp_tools.GATEWAY_ENV: json.dumps([{"name": "keel", "command": "x"},
                                                                      {"name": "Bad Name", "command": "x"}])}) == []


def test_the_fold_gives_the_agent_keels_server_alone():
    gh = {"name": "github", "command": "gh-mcp", "env": {"GH_TOKEN": "t"}}
    jira = {"name": "jira", "command": "jira-mcp"}
    graph = {"name": "codegraph", "command": "graph"}           # a part's server (not connected by the person): untouched
    specs, allow = mcp_tools.gateway([gh, jira, graph], ["mcp:codegraph:*"], ["keel", "github", "jira"])
    keel = next(s for s in specs if s["name"] == "keel")
    via = json.loads(keel["env"][mcp_tools.GATEWAY_ENV])
    assert [v["name"] for v in via] == ["github", "jira"] and via[0]["env"] == {"GH_TOKEN": "t"}
    assert {s["name"] for s in specs} == {"keel", "codegraph"}
    assert allow == ["mcp:codegraph:*", "mcp:keel:*"]
    # the Tools page named servers (and tools) for this agent: only those, only those tools
    specs, allow = mcp_tools.gateway([gh, jira], ["mcp:jira:get_issue"], ["github", "jira"])
    keel = next(s for s in specs if s["name"] == "keel")
    assert json.loads(keel["env"][mcp_tools.GATEWAY_ENV]) == [{**jira, "tools": ["get_issue"]}]
    assert allow == ["mcp:keel:*"]
    # nothing connected: keel's own server still (its CI and git read tools)
    specs, allow = mcp_tools.gateway([], [], ["keel"])
    assert [s["name"] for s in specs] == ["keel"] and mcp_tools.GATEWAY_ENV not in specs[0]["env"] and allow == ["mcp:keel:*"]


def test_harness_steps_get_the_connected_servers_through_keel(client, repo, monkeypatch, tmp_path):
    seen = answers(monkeypatch, {"harness-planner": lambda r: "## Goal\nx\nPLAN: ready"})
    tid = start(client, repo, workflow="harness", title="Show the owner name",
                mcp=[{"name": "github", "command": "gh-mcp", "args": []}])
    wait(client, tid)
    [planner] = seen
    assert [s["name"] for s in mcp_tools.servers_for(planner.mcp_specs, planner.tools_allow)] == ["keel"]
    keel = next(s for s in planner.mcp_specs if s["name"] == "keel")
    assert [v["name"] for v in json.loads(keel["env"][mcp_tools.GATEWAY_ENV])] == ["github"]
    assert keel["env"].get("KEEL_PROJECT") == "demo"                  # keel's server stays on this flow's project


def test_opencode_gets_the_steps_mcp_servers_in_a_private_config(tmp_path):
    from pathlib import Path

    from keel_engine.models import cli_runners

    keel = {"name": "keel", "command": "/py", "args": ["-m", "keel_engine.mcp"], "env": {mcp_tools.GATEWAY_ENV: "[]"}}
    conf = Path(cli_runners.write_opencode_plugin(tmp_path / "oc", servers=[keel]))
    cfg = json.loads((conf / "opencode.json").read_text())
    assert cfg["mcp"]["keel"] == {"type": "local", "command": ["/py", "-m", "keel_engine.mcp"], "enabled": True,
                                  "environment": {mcp_tools.GATEWAY_ENV: "[]"}, "timeout": cli_runners.MCP_START_MS}
    assert (conf / "opencode.json").stat().st_mode & 0o077 == 0
    assert "mcp" not in json.loads((Path(cli_runners.write_opencode_plugin(tmp_path / "none")) / "opencode.json").read_text())
