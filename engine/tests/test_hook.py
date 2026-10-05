"""keel's own PreToolUse hook (python -m keel_engine.hook pre-tool), its context file, live unlocks and the engine's
unlock endpoints."""

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import aiosqlite
import pytest

from conftest import decide, start, wait
from keel_engine import hook, rules
from keel_engine.models.base import AgentRequest
from keel_engine.models.cli_runners import ClaudeCLIRunner, write_opencode_plugin
from keel_engine.runtime import guard_ctx, migrate
from keel_engine.runtime.state import ThreadContext
from keel_engine.tools.agent_tools import ToolBox
from keel_engine.workflows.templates import get_template

PROD = "apps/api/src/main/kotlin/app/Score.kt"
TEST = "apps/api/src/test/kotlin/app/ScoreTest.kt"


def ctx_file(tmp_path, root, phase="red", **kw) -> str:
    return guard_ctx.write_context(tmp_path / "ctx" / "guard.json", root=str(root), phase=phase, ac=None,
                                   lane=kw.pop("lane", None), unlocks=kw.pop("unlocks", []), agent="test-author", thread="t1", **kw)


def call(tool, **ti) -> str:
    return json.dumps({"tool_name": tool, "tool_input": ti, "cwd": "/somewhere"})


def judge(capsys, raw: str, ctx: str | None) -> tuple[int, str]:
    code = hook.pre_tool(raw, ctx)
    return code, capsys.readouterr().err


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"
    (r / "apps/api/src/main/kotlin/app").mkdir(parents=True)
    (r / PROD).write_text("class Score\n")
    (r / "README.md").write_text("# p\n")
    return r


# ------------------------------------------------------------------ decisions

@pytest.mark.parametrize("phase,tool,ti,allowed", [
    ("red", "Write", {"file_path": PROD}, False),
    ("green", "Write", {"file_path": PROD}, True),
    ("red", "Write", {"file_path": TEST}, True),
    ("green", "Edit", {"file_path": TEST}, False),
    ("red", "MultiEdit", {"file_path": PROD, "edits": [{"old_string": "a", "new_string": "b"}]}, False),
    ("red", "NotebookEdit", {"notebook_path": "apps/api/src/main/kotlin/app/n.ipynb", "new_source": "x"}, False),
    ("green", "NotebookEdit", {"notebook_path": "apps/api/src/main/kotlin/app/n.ipynb", "new_source": "x"}, True),
    ("red", "Write", {"file_path": ".env"}, False),
    ("none", "Write", {"file_path": PROD}, True),
    ("none", "Write", {"file_path": ".env.local"}, False),
    ("red", "Read", {"file_path": ".env"}, False),
    ("red", "Read", {"file_path": "README.md"}, True),
    ("red", "Read", {"file_path": "apps/web/node_modules/x/index.js"}, False),
    ("red", "Bash", {"command": "ls -la"}, True),
    ("green", "Bash", {"command": "npm install left-pad"}, False),
    ("green", "Bash", {"command": "uv add httpx"}, False),
    ("green", "Bash", {"command": "npm install"}, True),
    ("red", "Bash", {"command": "git commit -m x"}, False),
    ("red", "Bash", {"command": "cat .env"}, False),
    ("red", "Bash", {"command": f"echo x > {PROD}"}, False),
    ("red", "Bash", {"command": f"echo x > {TEST}"}, True),
    ("red", "Glob", {"pattern": "**/*.kt"}, True),
    ("red", "mcp__github__create_issue", {"title": "x"}, False),
    ("none", "mcp__github__create_issue", {"title": "x"}, True),
    ("red", "mcp__keel__keel_status", {}, True),
    ("red", "mcp__runner__list_jobs", {}, True),
    ("red", "mcp__serena__replace_symbol_body", {"relative_path": PROD, "body": "x"}, False),
    ("green", "mcp__serena__replace_symbol_body", {"relative_path": PROD, "body": "x"}, True),
    ("green", "mcp__serena__write_memory", {"memory_name": "m"}, False),
    ("red", "mcp__serena__find_symbol", {"name_path": "Score"}, True),
])
def test_decision_per_tool_and_phase(capsys, tmp_path, root, phase, tool, ti, allowed):
    code, err = judge(capsys, call(tool, **ti), ctx_file(tmp_path, root, phase))
    if allowed:
        assert code == 0 and err == ""
    else:
        assert code == 2 and err.startswith(hook.MARKER + " ") and len(err) > len(hook.MARKER) + 5


def test_absolute_paths_are_judged_relative_to_the_project(capsys, tmp_path, root):
    code, err = judge(capsys, call("Edit", file_path=str(root / PROD)), ctx_file(tmp_path, root, "red"))
    assert code == 2 and f'{PROD}: editing api-main is blocked in phase "red"' in err


def test_an_unlock_allows_exactly_that_path_in_that_phase(capsys, tmp_path, root):
    ctx = ctx_file(tmp_path, root, "red", unlocks=[{"path": PROD, "phase": "red", "by": "user"}])
    assert judge(capsys, call("Write", file_path=PROD), ctx)[0] == 0
    assert judge(capsys, call("Write", file_path="apps/api/src/main/kotlin/app/Other.kt"), ctx)[0] == 2
    ctx = ctx_file(tmp_path, root, "red", unlocks=[{"path": PROD, "phase": "green"}])
    assert judge(capsys, call("Write", file_path=PROD), ctx)[0] == 2


def test_lane_and_project_config_are_honoured(capsys, tmp_path, root):
    ctx = ctx_file(tmp_path, root, "green", lane="web")
    code, err = judge(capsys, call("Write", file_path=PROD), ctx)
    assert code == 2 and '"web" lane' in err
    (root / ".keel").mkdir()
    (root / ".keel/config.yml").write_text("mcp: {allow: [create_issue]}\n")
    assert judge(capsys, call("mcp__github__create_issue", title="x"), ctx_file(tmp_path, root, "red"))[0] == 0


# ------------------------------------------------------------------ fail closed

@pytest.mark.parametrize("make", ["unset", "missing", "garbage", "no-phase", "no-root"])
def test_fails_closed_without_a_usable_context(capsys, tmp_path, root, make):
    f = tmp_path / "bad.json"
    if make == "garbage":
        f.write_text("{not json")
    elif make == "no-phase":
        f.write_text(json.dumps({"root": str(root)}))
    elif make == "no-root":
        f.write_text(json.dumps({"root": str(tmp_path / "gone"), "phase": "green"}))
    ctx = None if make == "unset" else str(f)
    for tool, ti in [("Write", {"file_path": TEST}), ("Edit", {"file_path": "README.md"}), ("MultiEdit", {"file_path": "a"}),
                     ("NotebookEdit", {"notebook_path": "a.ipynb"}), ("Bash", {"command": "ls"}), ("mcp__keel__keel_status", {})]:
        code, err = judge(capsys, call(tool, **ti), ctx)
        assert code == 2 and "Only reads are allowed" in err, (tool, err)
    for tool, ti in [("Read", {"file_path": "README.md"}), ("Glob", {"pattern": "*"}), ("Grep", {"pattern": "x"}), ("LS", {})]:
        assert judge(capsys, call(tool, **ti), ctx) == (0, "")
    assert judge(capsys, call("Read", file_path=str(root / ".env")), ctx)[0] == 2      # secrets stay blocked


def test_garbage_on_stdin_and_a_crashing_rule_refuse(capsys, tmp_path, root, monkeypatch):
    ctx = ctx_file(tmp_path, root, "green")
    assert judge(capsys, "not json", ctx)[0] == 2
    assert judge(capsys, "[1, 2]", ctx)[0] == 2

    def boom(*_a, **_k):
        raise RuntimeError("rules broke")
    monkeypatch.setattr(rules, "check_edit", boom)
    code, err = judge(capsys, call("Write", file_path=TEST), ctx)
    assert code == 2 and "RuntimeError: rules broke" in err
    assert judge(capsys, call("Read", file_path="README.md"), ctx)[0] == 0


# ------------------------------------------------------------------ the real process

def run_hook(root, ctx, raw, argv=None):
    env = {k: v for k, v in os.environ.items() if k != guard_ctx.ENV}
    if ctx:
        env[guard_ctx.ENV] = ctx
    return subprocess.run(argv or guard_ctx.hook_argv(), input=raw, cwd=root, env=env, capture_output=True, text=True, timeout=30)


def test_the_hook_process_exit_codes_and_isolation(tmp_path, root):
    ctx = ctx_file(tmp_path, root, "red")
    # A module the agent writes into the project cannot replace one the hook imports (python -I).
    (root / "json.py").write_text("import sys\nsys.exit(0)\n")
    (root / "re.py").write_text("import sys\nsys.exit(0)\n")
    denied = run_hook(root, ctx, call("Write", file_path=PROD))
    assert denied.returncode == 2 and denied.stderr.startswith("[keel guard] ") and denied.stdout == ""
    allowed = run_hook(root, ctx, call("Write", file_path=TEST))
    assert allowed.returncode == 0 and allowed.stderr == "" and allowed.stdout == ""
    assert run_hook(root, None, call("Bash", command="ls")).returncode == 2
    assert run_hook(root, ctx, call("Read", file_path="x"), argv=[sys.executable, "-I", "-m", "keel_engine.hook"]).returncode == 2


def test_the_settings_command_turns_any_failure_into_a_refusal(tmp_path, root):
    cmd = guard_ctx.hook_command()
    assert cmd == f'"{sys.executable}" -I -m keel_engine.hook pre-tool || exit 2'
    ctx = ctx_file(tmp_path, root, "red")
    ok = subprocess.run(["sh", "-c", cmd], input=call("Write", file_path=TEST), cwd=root, capture_output=True, text=True,
                        env={**os.environ, guard_ctx.ENV: ctx})
    assert ok.returncode == 0
    broken = subprocess.run(["sh", "-c", cmd.replace("keel_engine.hook", "keel_engine.no_such_module")], input="{}", cwd=root,
                            capture_output=True, text=True)
    assert broken.returncode == 2                                     # Claude Code would let exit 1 through


def test_latency_smoke(tmp_path, root):
    ctx = ctx_file(tmp_path, root, "red")
    for _ in range(5):
        t0 = time.monotonic()
        assert run_hook(root, ctx, call("Write", file_path=PROD)).returncode == 2
        assert time.monotonic() - t0 < 1.0


def test_self_test_passes_and_catches_a_broken_hook(root, monkeypatch):
    ok, detail = hook.self_test(str(root))
    assert ok, detail
    assert "apps/api/src/main/KeelGuardCheck.kt" in detail
    monkeypatch.setattr(guard_ctx, "hook_argv", lambda: [sys.executable, "-c", "pass"])
    ok, detail = hook.self_test(str(root))
    assert not ok and "was not refused" in detail


# ------------------------------------------------------------------ context file + live unlocks

def test_context_file_is_private_and_rewritten_on_unlock(tmp_path, root):
    g = guard_ctx.GuardFile(tmp_path / "run" / "guard.json", root=str(root), phase="red", ac={"id": "AC-1", "layer": "API"},
                            lane="api", unlocks=[], agent="implementer", thread="t1")
    assert stat.S_IMODE(os.stat(g.path).st_mode) == 0o600
    g.add_unlocks([{"path": PROD, "phase": "red", "by": "api"}])
    g.add_unlocks([{"path": PROD, "phase": "red", "by": "api"}])
    data = json.loads(Path(g.path).read_text())
    assert data["unlocks"] == [{"path": PROD, "phase": "red", "by": "api"}] and data["ac"]["id"] == "AC-1"
    assert stat.S_IMODE(os.stat(g.path).st_mode) == 0o600
    assert not list(Path(g.path).parent.glob(".guard.*"))            # no temp files left behind


def test_thread_unlock_reaches_every_running_guard(tmp_path, root):
    ctx = ThreadContext(thread_id="t1", project_id="p", root=str(root), workflow=get_template("feature"), title="t")
    g = guard_ctx.GuardFile(tmp_path / "guard.json", root=str(root), phase="red", unlocks=[])
    tb = ToolBox(str(root), "red")
    ctx.guards += [g, tb]
    added = ctx.add_unlocks([{"path": PROD, "phase": "red", "by": "api"}])
    assert added and ctx.add_unlocks([{"path": PROD, "phase": "red", "by": "api"}]) == []
    assert json.loads(Path(g.path).read_text())["unlocks"][0]["path"] == PROD
    assert tb.write_file(PROD, "class Score2\n").startswith("Wrote")


async def test_claude_runner_writes_context_and_settings_outside_the_project(tmp_path, monkeypatch, root):
    out = tmp_path / "out"
    out.mkdir()
    res = {"type": "result", "result": "done", "usage": {}}
    bin_ = tmp_path / "claude"
    bin_.write_text(f"#!/bin/sh\ncat > /dev/null\necho \"$@\" > {out}/argv\nenv > {out}/env\necho '{json.dumps(res)}'\n")
    bin_.chmod(0o755)
    monkeypatch.setenv("KEEL_CLAUDE_BIN", str(bin_))
    work = tmp_path / "work"
    work.mkdir()
    req = AgentRequest(agent="implementer", system="", prompt="go", root=str(root), phase="green",
                       model={"provider": "claude", "mode": "subscription", "model": "haiku"},
                       toolbox=ToolBox(str(root), "green", lane="api", unlocks=[{"path": TEST, "phase": "green"}]),
                       ac={"id": "AC-2", "layer": "API"}, workdir=str(work), thread="t9")
    await ClaudeCLIRunner().run(req, lambda *a, **k: None)
    env = dict(line.split("=", 1) for line in (out / "env").read_text().splitlines() if "=" in line)
    assert env[guard_ctx.ENV] == str(work / "guard.json")
    ctx = json.loads((work / "guard.json").read_text())
    assert ctx == {"root": str(root.resolve()), "phase": "green", "ac": {"id": "AC-2", "layer": "API"}, "lane": "api",
                   "unlocks": [{"path": TEST, "phase": "green"}], "agent": "implementer", "thread": "t9"}
    assert stat.S_IMODE(os.stat(work / "guard.json").st_mode) == 0o600
    settings = json.loads((work / "keel-guard.json").read_text())
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"].startswith(f'"{sys.executable}" -I -m keel_engine.hook pre-tool')
    assert not list(root.rglob("guard.json")) and not list(root.rglob("keel-guard.json"))


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_opencode_plugin_refuses_through_the_hook(tmp_path, root):
    """The generated plugin, run by node against the real hook (opencode itself is not needed for this half)."""
    conf = write_opencode_plugin(tmp_path / "opencode")
    plugin = Path(conf) / "plugins" / "keel-guard.js"
    ctx = ctx_file(tmp_path, root, "red")
    probe = tmp_path / "probe.mjs"
    probe.write_text(f"""
import {{ KeelGuard }} from {json.dumps(plugin.as_uri())};
const hooks = await KeelGuard({{ directory: {json.dumps(str(root))}, worktree: {json.dumps(str(root))} }});
const before = hooks['tool.execute.before'];
const out = {{}};
for (const [name, tool, args] of [
  ['prod', 'write', {{ filePath: {json.dumps(str(root / PROD))} }}],
  ['test', 'edit', {{ filePath: {json.dumps(str(root / TEST))} }}],
  ['bash', 'bash', {{ command: 'ls' }}],
  ['dep', 'bash', {{ command: 'npm install left-pad' }}],
  ['patch', 'apply_patch', {{ patchText: '*** Begin Patch\\n*** Update File: {PROD}\\n*** End Patch' }}],
  ['nopatch', 'apply_patch', {{ patchText: 'nothing' }}],
  ['task', 'task', {{ subagent_type: 'implementer' }}],
  ['mcp', 'github_create_issue', {{ title: 'x' }}],
  ['glob', 'glob', {{ pattern: '*' }}],
]) {{
  try {{ await before({{ tool }}, {{ args }}); out[name] = 'allowed'; }} catch (e) {{ out[name] = String(e.message); }}
}}
console.log(JSON.stringify(out));
""")
    r = subprocess.run(["node", str(probe)], capture_output=True, text=True, env={**os.environ, guard_ctx.ENV: ctx}, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["prod"].startswith("[keel guard] ") and "blocked in phase" in out["prod"]
    assert out["test"] == "allowed" and out["bash"] == "allowed" and out["glob"] == "allowed"
    assert "dependency" in out["dep"] and "blocked in phase" in out["patch"]
    assert "no file could be read" in out["nopatch"] and "sub-agents are off" in out["task"]
    assert "allowlist" in out["mcp"]
    r = subprocess.run(["node", str(probe)], capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if k != guard_ctx.ENV}, timeout=60)
    assert "Only reads are allowed" in json.loads(r.stdout)["test"]          # no context: fails closed


# ------------------------------------------------------------------ engine: unlock endpoints, legacy import

def test_unlock_endpoints_validate_and_reach_running_guards(client, repo, tmp_path):
    tid = start(client, repo)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"
    assert client.get(f"/threads/{tid}/unlocks").json() == []
    for bad in ({"path": "../etc/passwd"}, {"path": "/etc/passwd"}, {"path": " "}, {"path": "a.py", "phase": "nonsense"}):
        r = client.post(f"/threads/{tid}/unlocks", json=bad)
        assert r.status_code == 400, (bad, r.text)
    assert client.post("/threads/nope/unlocks", json={"path": "a.py"}).status_code == 404

    # an agent of this thread is running: its hook context file gets the unlock at once
    eng = client.app.state.engine
    live = guard_ctx.GuardFile(tmp_path / "live" / "guard.json", root=str(repo), phase="spec", unlocks=[])
    eng.ctxs[tid].guards.append(live)
    r = client.post(f"/threads/{tid}/unlocks", json={"path": "./src/scores/x.py", "reason": "needed"})
    assert r.status_code == 200, r.text
    assert r.json() == [{"path": "src/scores/x.py", "phase": "spec", "by": "api", "reason": "needed", "at": r.json()[0]["at"]}]
    assert json.loads(Path(live.path).read_text())["unlocks"][0]["path"] == "src/scores/x.py"
    client.post(f"/threads/{tid}/unlocks", json={"path": "src/scores/x.py", "phase": "spec"})       # same again: no duplicate
    client.post(f"/threads/{tid}/unlocks", json={"path": "src/scores/x.py", "phase": "green"})
    assert [(u["path"], u["phase"]) for u in client.get(f"/threads/{tid}/unlocks").json()] == [
        ("src/scores/x.py", "spec"), ("src/scores/x.py", "green")]
    assert {"path": "src/scores/x.py", "phase": "green"} in client.get(f"/threads/{tid}").json()["unlocks"]


def test_unlocks_survive_an_engine_restart(tmp_path, repo):
    from fastapi.testclient import TestClient
    from keel_engine.app import create_app
    from keel_engine.events import EventBus

    with TestClient(create_app(EventBus(), resume_running=False)) as c:
        tid = start(c, repo)
        wait(c, tid)
        assert c.post(f"/threads/{tid}/unlocks", json={"path": "a.py", "phase": "green"}).status_code == 200
    with TestClient(create_app(EventBus(), resume_running=False)) as c:
        assert [u["path"] for u in c.get(f"/threads/{tid}/unlocks").json()] == ["a.py"]


def _legacy(repo, **data):
    f = Path(repo) / ".keel" / "state.json"
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps({"flow": "feature", "phase": "red", **data}))
    return f


def test_legacy_unlocks_are_imported_once_and_the_file_is_never_written(client, repo):
    f = _legacy(repo, unlocks=[{"path": "tests/test_rogue_implementer.py", "phase": "green", "reason": "kept from 0.3"}])
    before = f.read_text()
    tid = start(client, repo)
    s = wait(client, tid)
    assert {"path": "tests/test_rogue_implementer.py", "phase": "green"} in s["unlocks"]
    got = [u for u in client.get(f"/threads/{tid}/unlocks").json() if u["path"] == "tests/test_rogue_implementer.py"]
    assert got[0]["by"] == "import" and got[0]["reason"] == "kept from 0.3"
    other = start(client, repo)                                       # a second flow in the same project: not again
    assert wait(client, other)["unlocks"] == []
    assert f.read_text() == before


def test_legacy_unlocks_of_another_or_finished_flow_are_not_taken(client, repo):
    _legacy(repo, unlocks=[{"path": "a.py", "phase": "green"}], engine={"thread_id": "someone-else"})
    tid = start(client, repo)
    assert wait(client, tid)["unlocks"] == []
    _legacy(repo, flow=None, unlocks=[{"path": "a.py", "phase": "green"}])
    tid = start(client, repo)
    assert wait(client, tid)["unlocks"] == []


def test_a_resumed_0_3_flow_gets_its_own_legacy_unlocks(client, repo):
    tid = start(client, repo)
    assert wait(client, tid)["waiting"]["step"] == "spec_gate"
    _legacy(repo, unlocks=[{"path": "a.py", "phase": "green", "by": "api"}], engine={"thread_id": tid})
    s = decide(client, tid)
    assert {"path": "a.py", "phase": "green"} in s["unlocks"]


async def test_migrations_are_idempotent(tmp_path):
    async with aiosqlite.connect(tmp_path / "m.db") as conn:
        await migrate.migrate(conn)
        await migrate.migrate(conn)
        async with conn.execute("select name from sqlite_master where type = 'table'") as cur:
            names = {r[0] for r in await cur.fetchall()}
    assert {"thread_unlocks", "legacy_unlock_imports"} <= names
