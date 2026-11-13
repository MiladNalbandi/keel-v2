"""0.18.64 an MCP server's env holds its secrets (security audit F9), and a model CLI gets only its own login (F8).

    stored flow body (checkpoints.db)   the env names, never the values (memory only, like the logins)
    claude's mcp.json                   0600 from its first byte
    codex                               the values in codex's environment, `env_vars` names them, never argv
    copilot                             a 0600 file passed as "@path", never the JSON in argv
    copilot's login                     keys["copilot"] or GH_TOKEN, never the container's GITHUB_TOKEN
"""
import json
import os
import stat
from pathlib import Path

from conftest import start, wait

from keel_engine.models.base import AgentRequest
from keel_engine.tools import mcp as mcp_tools
from keel_engine.tools.agent_tools import ToolBox

SECRET = "fake-secret-value-123"
SERVER = {"name": "tracker", "command": "tracker-mcp", "args": ["--stdio"], "env": {"TRACKER_TOKEN": SECRET, "TRACKER_URL": "https://t.example"}}


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_flow_stores_its_mcp_servers_env_names_not_values(client, repo):
    tid = start(client, repo, mcp=[SERVER])
    wait(client, tid)
    import sqlite3

    from keel_engine import config

    eng = client.app.state.engine
    db = sqlite3.connect(str(config.data_dir() / "checkpoints.db"))
    (raw,) = db.execute("select body from keel_threads where thread_id = ?", (tid,)).fetchone()
    db.close()
    assert SECRET not in raw
    stored = json.loads(raw)["mcp"][0]
    assert stored["env_names"] == ["TRACKER_TOKEN", "TRACKER_URL"] and "env" not in stored
    # the run itself still has the values (memory)
    assert eng.ctxs[tid].mcp[0]["env"]["TRACKER_TOKEN"] == SECRET


def test_strip_and_join_env():
    stored, values = mcp_tools.strip_env([SERVER, {"name": "plain", "command": "x"}])
    assert stored[0] == {"name": "tracker", "command": "tracker-mcp", "args": ["--stdio"], "env_names": ["TRACKER_TOKEN", "TRACKER_URL"]}
    assert stored[1] == {"name": "plain", "command": "x"}
    assert mcp_tools.with_env(stored, values)[0]["env"] == SERVER["env"]
    # after an engine restart (no values in memory) the server starts without them, never with the names as values
    assert "env" not in mcp_tools.with_env(stored, {})[0]
    # the api can send them again with the logins: "mcp_env:<server>:<NAME>"
    logins, again = mcp_tools.env_from_keys({"claude_oauth": "tok", "mcp_env:tracker:TRACKER_TOKEN": SECRET})
    assert logins == {"claude_oauth": "tok"} and again == {"tracker": {"TRACKER_TOKEN": SECRET}}


async def test_an_older_stored_body_loses_its_env_values_at_open(tmp_path, repo):
    from keel_engine.events import EventBus
    from keel_engine.runtime.service import Engine

    eng = Engine(EventBus(), tmp_path / "eng")
    await eng.open(resume_running=False)
    body = json.dumps({"mcp": [SERVER]})
    await eng.conn.execute("insert into keel_threads (thread_id, project_id, root, workflow_id, title, status, body, created_at, updated_at, error) "
                           "values ('t1', 'p', ?, 'feature', 'x', 'done', ?, '', '', null)", (str(repo), body))
    await eng.conn.commit()
    await eng.close()
    eng2 = Engine(EventBus(), tmp_path / "eng")
    await eng2.open(resume_running=False)
    async with eng2.conn.execute("select body from keel_threads where thread_id = 't1'") as cur:
        (raw,) = await cur.fetchone()
    assert SECRET not in raw and json.loads(raw)["mcp"][0]["env_names"] == ["TRACKER_TOKEN", "TRACKER_URL"]
    assert eng2.mcp_env["t1"]["tracker"]["TRACKER_TOKEN"] == SECRET     # this run still has them
    await eng2.set_keys("t1", {"claude_oauth": "tok", "mcp_env:tracker:TRACKER_URL": "https://other.example"})
    assert eng2.keys["t1"] == {"claude_oauth": "tok"} and eng2.mcp_env["t1"]["tracker"]["TRACKER_URL"] == "https://other.example"
    await eng2.close()


def test_claude_mcp_config_is_private_from_the_start(tmp_path):
    path, _ = mcp_tools.claude_mcp_config([SERVER], ["mcp:tracker"], str(tmp_path))
    assert mode(path) == 0o600
    assert json.loads(Path(path).read_text())["mcpServers"]["tracker"]["env"]["TRACKER_TOKEN"] == SECRET
    # an older file that was world-readable is made private too
    os.chmod(path, 0o644)
    mcp_tools.claude_mcp_config([SERVER], ["mcp:tracker"], str(tmp_path))
    assert mode(path) == 0o600


def req(tmp_path, provider, **kw):
    return AgentRequest(agent="implementer", system="", prompt="go", root=str(tmp_path / "repo"), phase="green",
                        model={"provider": provider, "mode": "subscription", "model": "m"},
                        toolbox=ToolBox(str(tmp_path / "repo"), "green"), workdir=str(tmp_path / "work"),
                        mcp_specs=[SERVER], tools_allow=["mcp:tracker"], **kw)


def fake_cli(tmp_path, monkeypatch, name):
    (tmp_path / "repo").mkdir(exist_ok=True)
    (tmp_path / "work").mkdir(exist_ok=True)
    exe = tmp_path / name
    exe.write_text(f'#!/bin/sh\ncat >/dev/null\nfor a in "$@"; do echo "$a"; done > {tmp_path}/argv\nenv > {tmp_path}/env\n')
    exe.chmod(0o755)
    monkeypatch.setenv(f"KEEL_{name.upper()}_BIN", str(exe))


def test_two_servers_with_one_name_keep_their_own_values():
    other = {"name": "other", "command": "o", "env": {"TRACKER_TOKEN": "another-fake", "PATH": "/x"}}
    shared, names, inline = mcp_tools.env_by_name([SERVER, other], {"PATH": "/usr/bin"})
    assert shared["TRACKER_TOKEN"] == SECRET and names["tracker"] == ["TRACKER_TOKEN", "TRACKER_URL"]
    assert inline == {"other": {"TRACKER_TOKEN": "another-fake", "PATH": "/x"}}


