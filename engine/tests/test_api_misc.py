import subprocess
import sys
import textwrap
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import decide, start, to_loop, wait
from keel_engine.app import create_app
from keel_engine.events import EventBus
from keel_engine.models.cli import safe_env
from keel_engine.tools import guard, mcp
from keel_engine.tools.agent_tools import ToolBox
from keel_engine.workflows.model import load_yaml


def test_health_and_models(client):
    h = client.get("/health").json()
    assert h["ok"] is True and h["fake"] is True and h["version"]
    m = client.get("/providers/models").json()
    assert set(m) == {"fake", "claude", "codex", "copilot"}
    assert all({"id", "label"} <= set(x) for p in m.values() for items in p["modes"].values() for x in items)


def test_provider_test_fake(client):
    r = client.post("/providers/test", json={"provider": "claude", "mode": "subscription", "model": "opus"}).json()
    assert r["ok"] and r["text"] == "OK" and isinstance(r["ms"], int)


def test_internal_token(monkeypatch):
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "s3cret")
    with TestClient(create_app(EventBus())) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/templates").status_code == 401
        assert c.get("/templates", headers={"X-Keel-Token": "s3cret"}).status_code == 200


def test_mcp_tools_lists_a_stdio_server(client, tmp_path):
    server = tmp_path / "server.py"
    server.write_text(textwrap.dedent("""
        from mcp.server.fastmcp import FastMCP
        app = FastMCP("demo")

        @app.tool()
        def keel_next() -> str:
            "The next keel step."
            return "red"

        app.run()
    """))
    r = client.post("/mcp/tools", json={"name": "demo", "command": sys.executable, "args": [str(server)]}).json()
    assert r["ok"], r
    assert r["tools"] == [{"name": "keel_next", "description": "The next keel step."}]
    bad = client.post("/mcp/tools", json={"name": "x", "command": "/does/not/exist", "args": []}).json()
    assert bad["ok"] is False and bad["tools"] == [] and bad["error"]


def test_mcp_allowlist_parsing():
    # a bare server name is all its tools (Tools › "Who may use what" saves those); a built-in tool name is not a server
    assert mcp.parse_allow(["mcp:keel:keel_next", "mcp:serena:*", "mcp:x", "other", "Read"]) == \
        {"keel": {"keel_next"}, "serena": None, "x": None, "other": None}


def test_subscription_env_has_no_keys(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", "KEEL_INTERNAL_TOKEN"):
        monkeypatch.setenv(k, "x")
    env = safe_env()
    assert not {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", "KEEL_INTERNAL_TOKEN"} & set(env)
    assert "PATH" in env
    assert safe_env({"GH_TOKEN": "t"})["GH_TOKEN"] == "t"


def test_toolbox_refuses_by_phase(repo):
    refused = []
    tb = ToolBox(str(repo), "green", on_refuse=lambda tool, path, reason: refused.append((tool, path)))
    assert tb.write_file("tests/test_x.py", "x").startswith("REFUSED")
    assert tb.write_file("src/scores/x.py", "x = 1\n").startswith("Wrote")
    assert tb.write_file("../escape.txt", "x").startswith("REFUSED")
    assert tb.run_command("git commit -m x").startswith("REFUSED")
    assert tb.read_file(".env").startswith("REFUSED")
    assert [p for _, p in refused][:1] == ["tests/test_x.py"] and len(refused) == 4
    assert tb.writes[0]["diff"].startswith("--- /dev/null")


def test_diff_guard_unit(repo):
    test_file = Path(repo) / "tests/test_scores.py"
    (Path(repo) / "notes.txt").write_text("mine, from before\n")
    before = guard.snapshot(str(repo))
    original = test_file.read_text()
    test_file.write_text("# tampered\n")
    (Path(repo) / "tests/test_new.py").write_text("x")
    (Path(repo) / "src/scores/ok.py").write_text("y = 2\n")
    refused = guard.guard_diff(str(repo), "green", before)
    assert sorted(r["path"] for r in refused) == ["tests/test_new.py", "tests/test_scores.py"]
    assert test_file.read_text() == original
    assert not (Path(repo) / "tests/test_new.py").exists()
    assert (Path(repo) / "src/scores/ok.py").exists() and (Path(repo) / "notes.txt").exists()


def test_real_checks_on_demo(client, repo):
    """simulate_checks=false: verify_red/verify_green run pytest on the demo for real."""
    tid = start(client, repo, settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "simulate_checks": False})
    s = to_loop(client, tid)
    assert s["waiting"]["step"] == "ac_gate", s
    notes = [e["data"]["note"] for e in client.bus.of(tid, "step.finished") if e["step"] in ("verify_red", "verify_green")]
    assert any("red confirmed" in n and "simulated" not in n for n in notes), notes
    assert any("AC-1: green." in n for n in notes), notes


def test_failing_check_retries_then_asks(client, repo):
    wf = load_yaml("""
name: Always failing
keel_rules: false
steps:
  - { id: work, kind: agent, name: work, agent: explorer, phase: green }
  - { id: check, kind: code, name: check, action: "run:exit 1", phase: green }
""")
    tid = start(client, repo, workflow=wf, settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "fix_attempts": 2})
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["kind"] == "fix", s
    assert [e["step"] for e in client.bus.of(tid, "agent.started")] == ["work"] * 3
    s = decide(client, tid, "reject")
    assert s["status"] == "failed" and "exited 1" in s["error"]


# ------------------------------------------------------------------ a flow's own worktree (POST /worktrees)

def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout


def test_a_flow_worktree_starts_from_the_base_branch_and_goes_with_or_without_its_branch(client, repo):
    _git(repo, "checkout", "-q", "-b", "feat/other")
    (Path(repo) / "other.txt").write_text("another flow's work\n")
    _git(repo, "add", "other.txt")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "other"], cwd=repo, check=True)
    base = _git(repo, "rev-parse", "main").strip() or _git(repo, "rev-parse", "master").strip()
    start = "main" if _git(repo, "rev-parse", "--verify", "--quiet", "main").strip() else "master"
    w = client.post("/worktrees", json={"root": str(repo), "name": "flow-ranks-1", "branch": "feat/ranks", "start": start}).json()
    assert w["branch"] == "feat/ranks" and w["base"] == base and not (Path(w["path"]) / "other.txt").exists()
    again = client.post("/worktrees", json={"root": str(repo), "name": "flow-ranks-2", "branch": "feat/ranks", "start": start})
    assert again.status_code == 409 and "exists already" in again.json()["error"]
    assert client.post("/worktrees/remove", json={"root": str(repo), "name": "flow-ranks-1"}).json() == {"ok": True}
    assert not Path(w["path"]).exists() and _git(repo, "branch", "--list", "feat/ranks").strip()     # the branch stays
    bad = client.post("/worktrees/remove", json={"root": str(repo), "name": "../../etc"})
    assert bad.status_code >= 400
