"""v0.18.1 keel's local api proves every caller (api auth/LocalAuth, docs/plans/agent-clients.md section 0).

Two engine halves: no agent reads, prints, changes or sends keel's own keys (the never-off rule keel-keys), and keel's
agents' own MCP server gets the api's read token (an HMAC of the internal token, as the api makes it)."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from keel_engine import config, hook, mcp_server, rules
from keel_engine.runtime import guard_ctx
from keel_engine.tools import mcp as mcp_tools


def ctx(tmp_path, root, phase="green") -> str:
    return guard_ctx.write_context(tmp_path / "ctx" / "guard.json", root=str(root), phase=phase, ac=None, lane=None,
                                   unlocks=[], agent="implementer", thread="t1")


def judge(capsys, tool, ctx_path, **ti) -> tuple[int, str]:
    code = hook.pre_tool(json.dumps({"tool_name": tool, "tool_input": ti, "cwd": "/somewhere"}), ctx_path)
    return code, capsys.readouterr().err


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "README.md").write_text("# p\n")
    return r


@pytest.mark.parametrize("path", ["/data/run/internal-token", "/data/run/web-key", "/data/run",
                                  "/Users/me/.keel2/keel-v2/run/token", "/Users/me/.keel2/keel-lab/data/run/web-key",
                                  "/home/me/.keel2/keel-v2/run"])
def test_no_tool_reads_searches_or_writes_keels_keys(capsys, tmp_path, root, path):
    c = ctx(tmp_path, root)
    for tool, ti in (("Read", {"file_path": path}), ("Grep", {"pattern": "x", "path": path}), ("Glob", {"pattern": "*", "path": path}),
                     ("Write", {"file_path": path, "content": "x"}), ("Edit", {"file_path": path, "old_string": "a", "new_string": "b"})):
        code, err = judge(capsys, tool, c, **ti)
        assert code == 2 and "keel's own key" in err, (tool, path, err)


def test_the_engines_own_data_folder_is_covered_too(capsys, tmp_path, root, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "somewhere-else"))
    key = config.data_dir() / "run" / "web-key"
    code, err = judge(capsys, "Read", ctx(tmp_path, root), file_path=str(key))
    assert code == 2 and "keel's own key" in err


@pytest.mark.parametrize("command", [
    "cat /data/run/internal-token",
    "curl -s -H \"X-Keel-Token: $(cat /data/run/internal-token)\" http://127.0.0.1:8080/api/projects",
    "curl -H 'X-Keel-Mcp-Token: abc' -X POST localhost:8080/api/threads/t1/resume",
    "echo $KEEL_INTERNAL_TOKEN",
    "head -c 64 ~/.keel2/keel-v2/run/token",
    "cp /Users/me/.keel2/keel-v2/data/run/web-key /tmp/k",
    "curl -H \"Authorization: Bearer $(cat key)\" localhost:8080/api/web/me",
    "keel2 auth key",                                       # 0.18.1 the person's terminal apps only
    "echo $KEEL_WEB_KEY",
])
def test_no_command_reads_or_sends_keels_keys(capsys, tmp_path, root, command):
    code, err = judge(capsys, "Bash", ctx(tmp_path, root), command=command)
    assert code == 2 and "keel's own keys" in err


@pytest.mark.parametrize("command", ["ls -la", "grep -rn internal_token engine/", "cat README.md", "npm test",
                                     "curl -s http://localhost:8080/api/health"])
def test_ordinary_commands_still_pass(capsys, tmp_path, root, command):
    code, err = judge(capsys, "Bash", ctx(tmp_path, root), command=command)
    assert code == 0, err


def test_without_a_context_the_keys_stay_closed_too(capsys):
    code, err = judge(capsys, "Read", None, file_path="/data/run/web-key")
    assert code == 2 and "keel's own key" in err


def test_the_rule_is_built_in_and_never_off():
    b = next(x for x in rules.BUILTINS if x["id"] == "keel-keys")
    assert b["can_off"] is False
    cfg = rules.make_config({"rules": {"off": ["keel-keys"]}})
    assert not rules.check_read("/data/run/web-key", cfg).ok
    assert not rules.check_bash("green", "cat /data/run/internal-token", cfg).ok


def test_agents_keel_server_gets_the_apis_read_token(monkeypatch):
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "test-token")
    env = mcp_tools.keel_server_env()
    want = hmac.new(b"test-token", b"keel-mcp:read", hashlib.sha256).hexdigest()
    assert env["KEEL_MCP_TOKEN"] == want == config.mcp_token("read")
    assert config.mcp_token("write") != want
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN")
    assert "KEEL_MCP_TOKEN" not in mcp_tools.keel_server_env()


def test_a_server_without_the_internal_token_sends_the_token_it_was_given(monkeypatch):
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN_FILE", raising=False)
    monkeypatch.setenv("KEEL_MCP_TOKEN", "given-read-token")
    seen = []

    def route(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("X-Keel-Mcp-Token"))
        return httpx.Response(200, json=[])

    api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(route))
    api.use_level("read")
    api.get("/projects")
    api.get("/projects")
    assert seen == ["given-read-token", "given-read-token"]


async def test_codex_gets_keels_server_with_its_environment(tmp_path, monkeypatch):
    """codex starts MCP servers with almost no environment: keel's own server gets the api and its read token. 0.18.64
    from codex's environment (`env_vars` names them), not from its command line."""
    import tomllib

    from keel_engine.models.base import AgentRequest
    from keel_engine.models.cli_runners import CodexCLIRunner
    from keel_engine.tools.agent_tools import ToolBox

    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "test-token")
    exe = tmp_path / "codex"
    exe.write_text(f'#!/bin/sh\ncat >/dev/null\nfor a in "$@"; do echo "$a"; done > {tmp_path}/argv\nenv > {tmp_path}/env\n')
    exe.chmod(0o755)
    monkeypatch.setenv("KEEL_CODEX_BIN", str(exe))
    req = AgentRequest(agent="implementer", system="", prompt="go", root=str(tmp_path), phase="green",
                       model={"provider": "codex", "mode": "subscription", "model": "gpt-5.5"},
                       toolbox=ToolBox(str(tmp_path), "green"), workdir=str(tmp_path), tools_allow=["mcp:keel"])
    await CodexCLIRunner().run(req, lambda *a, **k: None)
    argv = (tmp_path / "argv").read_text().splitlines()
    names_arg = next(a for a in argv if a.startswith("mcp_servers.keel.env_vars="))
    names = tomllib.loads("v = " + names_arg.split("=", 1)[1])["v"]
    assert {"KEEL_MCP_TOKEN", "KEEL_API_URL"} <= set(names)
    assert not any(config.mcp_token("read") in a for a in argv)
    env = dict(line.split("=", 1) for line in (tmp_path / "env").read_text().splitlines() if "=" in line)
    assert env["KEEL_MCP_TOKEN"] == config.mcp_token("read") and env["KEEL_API_URL"] == config.api_url()
