"""v0.2 engine additions: blockers, ladder, unlocks, commit checks, live guard, knowledge-refresh, estimate."""

import json
import stat
import subprocess
import sys
from pathlib import Path


from conftest import decide, start, to_loop, wait
from keel_engine import rules
from keel_engine.models import login_keys
from keel_engine.models.base import AgentRequest
from keel_engine.models.cli import safe_env
from keel_engine.models.cli_runners import ClaudeCLIRunner, CopilotCLIRunner, OpenCodeRunner
from keel_engine.rules import checks
from keel_engine.runtime import blockers, ladder, verdicts
from keel_engine.runtime.actions import ActionInput, commit
from keel_engine.tools.agent_tools import ToolBox
from keel_engine.workflows.model import from_dict
from keel_engine.workflows.templates import get_template

FAKE = {"provider": "fake", "mode": "api", "model": "fake"}


def sh(repo, *args):
    return subprocess.run(list(args), cwd=repo, capture_output=True, text=True)


def git(repo, *args):
    return sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", *args)


def commit_all(repo, msg="setup"):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def head(repo):
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def log(repo):
    return git(repo, "log", "--format=%s").stdout.splitlines()


def write(repo, rel, text):
    f = Path(repo) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def configure(repo, text="version: 4\n"):
    write(repo, ".keel/config.yml", text)
    commit_all(repo, "chore: keel config")


def graph_values(client, tid) -> dict:
    """The thread's graph state (what the engine checkpoints), read on the app's own event loop."""
    eng = client.app.state.engine

    async def get():
        return (await (await eng._graph(tid)).aget_state(eng._cfg(tid))).values

    return client.portal.call(get)


def wf(steps, name="custom"):
    return from_dict({"name": name, "keel_rules": False, "steps": steps})


def ai(repo, phase, flow="feature", **kw):
    return ActionInput(root=str(repo), phase=phase, title="t", ac=None, acs=[], fake=True, flow=flow, **kw)


def run_to_end(client, tid, s, decision="approve"):
    for _ in range(40):
        if s["status"] != "waiting":
            return s
        s = decide(client, tid, decision)
    raise AssertionError("too many pauses")


# ------------------------------------------------------------------ 1. blockers

def test_push_blockers_from_verdicts(repo):
    assert blockers.push_blockers(str(repo)) == []          # not configured: workflow gates do not apply
    configure(repo)
    assert {b["gate"] for b in blockers.push_blockers(str(repo))} == {"release"}   # no coverage command: no coverage gate
    configure(repo, "version: 4\ncommands: {coverage: 'true'}\n")
    assert {b["gate"] for b in blockers.push_blockers(str(repo))} == {"release", "coverage"}
    verdicts.write(str(repo), "release", True, {}, head(repo))
    verdicts.write(str(repo), "coverage", True, {}, "0" * 40)
    b = blockers.push_blockers(str(repo))
    assert [x["gate"] for x in b] == ["coverage"] and "not" in b[0]["why"] and "verify_coverage" in b[0]["fix"]
    assert not any("keel " in x["fix"] for x in b)           # v2 words, not keel v1 commands
    verdicts.write(str(repo), "coverage", True, {"tree": git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()}, "0" * 40)
    assert blockers.push_blockers(str(repo)) == []           # same files as HEAD: still current
    verdicts.write("other-project", "release", False, {}, head(repo))
    assert blockers.push_blockers(str(repo)) == []           # verdicts are per project
    write(repo, "docs/knowledge/architecture.md", "# A\n")
    assert "knowledge" in {x["gate"] for x in blockers.push_blockers(str(repo))}


def test_push_blockers_deps_and_secrets(repo):
    configure(repo, "version: 4\ncoverage: {enabled: false}\n")
    base = head(repo)
    write(repo, "src/scores/keys.py", 'AWS = "AKIA' + 'ABCDEFGHIJKLMNOP"\n')  # keel:allow-secret (fixture)
    write(repo, "requirements.txt", "httpx==0.27\n")
    commit_all(repo, "feat: oops")
    b = {x["gate"]: x for x in blockers.push_blockers(str(repo), base)}
    assert set(b) == {"release", "secrets"}                 # deps and security cannot run in 0.4.0: warnings only
    assert "src/scores/keys.py" in b["secrets"]["why"] and "AWS" in b["secrets"]["why"]
    w = {x["gate"]: x for x in blockers.push_warnings(str(repo), base)}
    assert set(w) == {"deps", "security"} and "not run" in w["deps"]["why"]
    configure(repo, "version: 4\ncoverage: {enabled: false}\nsecurity: {required: [deps]}\n")
    b = {x["gate"] for x in blockers.push_blockers(str(repo), base)}
    assert b == {"release", "secrets", "deps"} and {x["gate"] for x in blockers.push_warnings(str(repo), base)} == {"security"}


def test_push_check_and_commit_fill_blockers(client, repo):
    configure(repo)
    flow = wf([{"id": "push", "kind": "code", "name": "push check", "action": "push_check", "phase": "ship"}])
    tid = start(client, repo, workflow=flow)
    s = wait(client, tid)
    assert s["status"] == "done", s
    assert {b["gate"] for b in s["blockers"]} == {"release"}
    assert all(set(b) == {"gate", "why", "fix"} for b in s["blockers"])

    tid = start(client, repo, workflow="fix", title="Average is wrong")
    s = run_to_end(client, tid, wait(client, tid))
    assert s["status"] == "done"
    assert {b["gate"] for b in s["blockers"]} == {"release"}   # refreshed after the fix commit (simulated runs write no verdict)


# ------------------------------------------------------------------ 2. ladder

def test_init_flow_ladder_simulated(client, repo):
    tid = start(client, repo, workflow="init", title="Set up keel")
    s = run_to_end(client, tid, wait(client, tid))
    assert s["status"] == "done", s
    rungs = s["ladder"]
    assert [r["n"] for r in rungs] == list(range(1, 13))
    assert all(set(r) >= {"n", "name", "cmd", "status"} for r in rungs)
    status = {r["n"]: r["status"] for r in rungs}
    assert status == {1: "pass", 2: "skipped", 3: "skipped", 4: "pass", 5: "skipped", 6: "pass", 7: "skipped",
                      8: "skipped", 9: "skipped", 10: "skipped", 11: "skipped", 12: "pass"}
    assert "refused an edit" in rungs[11]["detail"]                   # the guard self-test runs for real
    assert rungs[5]["cmd"] == "python -m pytest -q" and rungs[3]["cmd"].startswith("python3 -m compileall")
    assert json.loads((Path(repo) / ".keel/ladder.json").read_text())["rungs"] == rungs
    assert "python -m pytest -q" in (Path(repo) / "docs/RUNNING.md").read_text()
    # deterministic: a second plan gives the same commands
    assert [r["cmd"] for r in ladder.plan(str(repo))] == [r["cmd"] for r in rungs]


def test_ladder_real_mode_runs_and_marks_fixing(repo):
    write(repo, ".keel/config.yml", "commands:\n  unit_tests: 'true'\n  smoke: 'exit 3'\n  api_compile: 'true'\n")
    seen = {}

    def runner(root, cmd):
        if cmd == "exit 3":
            seen["during"] = {r["n"]: r["status"] for r in ladder.previous(root)}
        p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    ok, rungs = ladder.run(str(repo), False, runner)
    st = {r["n"]: r["status"] for r in rungs}
    assert not ok and st[4] == "pass" and st[6] == "pass" and st[11] == "fail" and st[7] == "skipped"
    assert "exit 3" in rungs[10]["detail"]
    ok, rungs = ladder.run(str(repo), False, runner)
    assert seen["during"][11] == "fixing"          # the rung that failed last time shows as being fixed


def test_ladder_failure_pauses_init_with_waiting_rungs(client, repo):
    # v0.4.0: a failing rung gets a setup-doctor, then asks (fix / exclude / accept) after fix_attempts_per_rung rounds
    configure(repo, "commands:\n  api_compile: 'false'\n  unit_tests: 'true'\n")
    tid = start(client, repo, workflow="init", title="Set up keel",
                settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "simulate_checks": False,
                          "fix_attempts_per_rung": 1})
    s = wait(client, tid)
    while s["status"] == "waiting" and s["waiting"]["step"] in ("questions", "plan_gate"):
        s = decide(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "rung_gate", s
    assert s["waiting"]["choices"] == ["fix", "exclude", "accept"] and "Rung 4" in s["waiting"]["detail"]
    st = {r["n"]: r["status"] for r in s["ladder"]}
    assert st[4] == "fail" and st[6] == "waiting" and st[1] == "pass"


# ------------------------------------------------------------------ 3. unlocks

def test_unlock_rules_and_toolbox(repo):
    assert not rules.check_edit("green", "tests/test_x.py").ok
    u = [{"path": "tests/test_x.py", "phase": "green"}]
    assert rules.check_edit("green", "tests/test_x.py", unlocks=u).ok
    assert not rules.check_edit("green", "tests/test_y.py", unlocks=u).ok          # another path
    assert not rules.check_edit("bug-investigate", "tests/test_x.py", unlocks=u).ok  # another phase
    assert not rules.check_edit("green", ".env", unlocks=[{"path": ".env", "phase": "green"}]).ok   # secrets stay locked
    tb = ToolBox(str(repo), "green", unlocks=u)
    assert tb.write_file("tests/test_x.py", "x = 1\n").startswith("Wrote")
    assert tb.write_file("tests/test_y.py", "x = 1\n").startswith("REFUSED")


def _rogue_models():
    return {"default": FAKE, "implementer": {"provider": "fake", "mode": "api", "model": "fake-rogue"}}


def test_settings_unlock_lets_guard_keep_file(client, repo):
    rel = "tests/test_rogue_implementer.py"
    tid = start(client, repo, models=_rogue_models(),
                settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "unlocks": [{"path": rel, "phase": "green"}]})
    s = to_loop(client, tid)
    assert s["waiting"]["step"] == "ac_gate"
    assert (Path(repo) / rel).exists()
    assert not any(e["data"]["path"] == rel for e in client.bus.of(tid, "guard.refused"))
    assert s["unlocks"] == [{"path": rel, "phase": "green"}]
    assert not (Path(repo) / ".keel" / "state.json").exists()


def test_resume_payload_unlock(client, repo):
    rel = "tests/test_rogue_implementer.py"
    tid = start(client, repo, models=_rogue_models())
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"
    s = decide(client, tid, payload={"unlock": {"path": rel, "phase": "green"}})
    for _ in ("options", "contract_gate"):
        s = decide(client, tid)
    assert s["waiting"]["step"] == "ac_gate"
    assert (Path(repo) / rel).exists()
    assert {"path": rel, "phase": "green"} in s["unlocks"]
    ev = [e for e in client.bus.of(tid, "gate.decided") if e["data"]["gate"] == "unlock"]
    assert ev and ev[0]["data"]["unlock"] == {"path": rel, "phase": "green"}


def test_guard_refused_events_carry_path_and_phase(client, repo):
    tid = start(client, repo, models=_rogue_models())
    to_loop(client, tid)
    refused = client.bus.of(tid, "guard.refused")
    assert refused
    for e in refused:
        assert e["data"]["path"] and e["data"]["phase"]
    seen = []
    tb = ToolBox(str(repo), "green", on_refuse=lambda *a: seen.append(a))
    tb.run_command("echo x > tests/test_new_thing.py")
    assert seen[0][1] == "tests/test_new_thing.py"


def test_invalid_workflow_returns_errors_list(client, repo):
    body = {"project_id": "demo", "root": str(repo), "title": "x", "workflow": {"name": "bad", "steps": [
        {"id": "a", "kind": "gate", "name": "g", "back": "zzz"}, {"id": "a", "kind": "code", "name": "c", "action": "fly"}]}}
    r = client.post("/threads", json=body)
    assert r.status_code == 400
    errs = r.json()["errors"]
    assert isinstance(errs, list) and len(errs) >= 2 and any("fly" in e for e in errs)
    r = client.post("/workflows/estimate", json={"yaml": ": : :", "acs": 1})
    assert r.status_code == 400 and r.json()["errors"]


# ------------------------------------------------------------------ 4. commit checks

def test_secret_scan_on_staged_diff(repo):
    write(repo, "src/scores/cfg.py", 'TOKEN = "ghp_' + "a" * 36 + '"\n')
    r = commit(ai(repo, "green"))
    assert not r.ok and "secret" in r.note and "GitHub token" in r.note
    assert git(repo, "diff", "--cached", "--name-only").stdout == ""          # unstaged again
    assert r.update["blockers"][0]["gate"] == "secrets"
    write(repo, "src/scores/cfg.py", 'TOKEN = "ghp_' + "a" * 36 + '"  # keel:allow-secret\n')
    assert commit(ai(repo, "green")).ok
    assert checks.scan_secrets('password = "changeme-please-now"') == []
    assert checks.scan_secrets('password = "Zq8#mL2!vR9@xT4w"')  # keel:allow-secret (fixture)


def test_manifest_additions_parse():
    diff = ("+++ b/pyproject.toml\n+httpx = \">=0.27\"\n+    \"rich>=13\",\n+++ b/package.json\n+  \"zod\": \"^3.22.0\",\n"
            "+++ b/build.gradle.kts\n+    implementation(\"io.ktor:ktor-client:2.3\")\n+++ b/README.md\n+requests==2\n")
    adds = checks.manifest_additions(diff)
    assert [a["file"] for a in adds] == ["pyproject.toml", "pyproject.toml", "package.json", "build.gradle.kts"]
    assert checks.unapproved_additions(diff, ["httpx", "rich", "zod", "ktor-client"]) == []
    assert len(checks.unapproved_additions(diff, ["zod"])) == 3


def _dep_flow():
    return wf([
        {"id": "add", "kind": "code", "name": "add dep", "phase": "green",
         "action": "run:printf 'httpx = \">=0.27\"\\n' >> pyproject.toml"},
        {"id": "commit", "kind": "code", "name": "commit", "action": "commit", "phase": "green"},
    ])


def test_new_dependency_approve_commits(client, repo):
    tid = start(client, repo, workflow=_dep_flow())
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["kind"] == "fix" and s["waiting"]["title"] == "Approve new dependency"
    assert "httpx" in s["waiting"]["detail"]
    s = decide(client, tid)
    assert s["status"] == "done", s
    assert log(repo)[0].startswith("feat")
    assert "httpx" in (Path(repo) / "pyproject.toml").read_text()
    assert "httpx" in graph_values(client, tid)["deps"]


def test_new_dependency_reject_reverts_manifest(client, repo):
    before = (Path(repo) / "pyproject.toml").read_text()
    tid = start(client, repo, workflow=_dep_flow())
    wait(client, tid)
    s = decide(client, tid, "reject")
    assert s["status"] == "done", s
    assert (Path(repo) / "pyproject.toml").read_text() == before
    assert log(repo) == ["chore: demo project"]


def test_coverage_commit_delete_only(repo):
    configure(repo, "backend: {dir: src}\n")
    f = Path(repo) / "src/scores/__init__.py"
    lines = f.read_text().splitlines(keepends=True)
    f.write_text("".join(lines) + "def extra():\n    return 1\n")
    r = commit(ai(repo, "coverage-fix"))
    assert not r.ok and "only delete" in r.note
    f.write_text("".join(lines[:-1]))
    write(repo, "tests/test_more.py", "def test_more():\n    assert True\n")
    r = commit(ai(repo, "coverage-fix"))
    assert r.ok, r.detail
    assert log(repo)[0].startswith("test(coverage)")


def test_trivial_may_not_edit_existing_tests(repo):
    f = Path(repo) / "tests/test_scores.py"
    f.write_text(f.read_text() + "\n# tweak\n")
    r = commit(ai(repo, "trivial"))
    assert not r.ok and "existing tests" in r.note
    git(repo, "checkout", "--", "tests/test_scores.py")
    write(repo, "src/scores/util.py", "X = 1\n")
    assert commit(ai(repo, "trivial")).ok


def test_change_flow_escalation_override(client, repo):
    configure(repo, "change: {auth_paths: ['src/scores/**']}\n")
    tid = start(client, repo, workflow="change", title="Tweak ranks")
    s = wait(client, tid)
    assert s["waiting"]["step"] == "scope_gate"
    s = decide(client, tid)
    assert s["waiting"]["title"] == "Escalate to a feature flow?" and s["waiting"]["kind"] == "gate", s
    assert "auth" in s["waiting"]["detail"]
    s = decide(client, tid, "reject", why="it is a one-line helper")
    assert s["waiting"]["step"] == "ac_gate", s
    assert any(x.startswith("feat(CHG-1.1)") for x in log(repo))
    s = run_to_end(client, tid, s)
    assert s["status"] == "done"          # AC-2 did not ask again: the override stands for the flow
    assert any(x.startswith("escalation-override") for x in s["gate_log"])


def test_change_flow_escalation_approve_hands_over_to_feature(client, repo):
    configure(repo, "change: {auth_paths: ['src/scores/**']}\n")
    tid = start(client, repo, workflow="change", title="Tweak ranks")
    wait(client, tid)
    decide(client, tid)
    s = decide(client, tid, "approve")
    assert s["status"] == "done" and [c["workflow"] for c in s["children"]] == ["feature"]
    assert any(x.startswith("escalation: auth") for x in s["gate_log"])
    child = wait(client, s["children"][0]["thread_id"])
    assert [a["id"] for a in child["acs"]] == ["CHG-1.1", "CHG-1.2"]


# ------------------------------------------------------------------ 5. live guard + CLI logins

def script(tmp_path, name, body):
    f = tmp_path / name
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return str(f)


CLAUDE_OUT = [
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "__ROOT__/src/scores/x.py", "content": "x"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "1", "is_error": True,
                                              "content": "PreToolUse:Write hook error: [\"/venv/bin/python\" -I -m keel_engine.hook pre-tool"
                                                         " || exit 2]: [keel guard] src/scores/x.py: "
                                                         "editing api-main is blocked in phase \"red\"."}]}},
    {"type": "result", "result": "done", "usage": {"input_tokens": 10, "output_tokens": 2}},
]


def fake_claude(tmp_path, root, out_dir):
    lines = "\n".join(json.dumps(x).replace("__ROOT__", str(root)) for x in CLAUDE_OUT)
    (tmp_path / "claude.out").write_text(lines + "\n")
    return script(tmp_path, "claude", f"""cat > /dev/null
echo "$@" > {out_dir}/argv
env > {out_dir}/env
cp "$KEEL_GUARD_CTX" {out_dir}/guard.json 2>/dev/null
cat {tmp_path}/claude.out
""")


def req(root, model, **kw):
    return AgentRequest(agent="test-author", system="", prompt="go", root=str(root), phase="red", model=model,
                        toolbox=ToolBox(str(root), "red"), workdir=str(root), **kw)


async def test_claude_runner_loads_keels_own_hook_oauth_and_hook_refusal(tmp_path, monkeypatch, repo):
    monkeypatch.setenv("KEEL_CLAUDE_BIN", fake_claude(tmp_path, repo, tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-pass")
    monkeypatch.setenv("GH_TOKEN", "gh-should-not-pass")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-oauth")
    steps = []
    res = await ClaudeCLIRunner().run(req(repo, {"provider": "claude", "mode": "subscription", "model": "sonnet"},
                                          keys={"claude_oauth": "key-oauth"}), lambda k, t="", **kw: steps.append((k, t, kw)))
    argv = (tmp_path / "argv").read_text()
    env = (tmp_path / "env").read_text()
    # only keel's guard hook is loaded (no plugin), and it is keel v2's own: this venv's python, outside the project
    assert "--plugin-dir" not in argv and "--settings" in argv
    assert "--disallowedTools Skill,Task" in argv
    settings = argv.split("--settings ")[1].split()[0]
    assert not settings.startswith(str(repo))
    hook = json.loads(open(settings).read())["hooks"]["PreToolUse"][0]
    assert hook["hooks"][0]["command"] == f'"{sys.executable}" -I -m keel_engine.hook pre-tool || exit 2'
    assert hook["matcher"] == "Edit|Write|MultiEdit|NotebookEdit|Bash|Read|mcp__.*"
    ctx_file = next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("KEEL_GUARD_CTX="))
    assert not ctx_file.startswith(str(repo))
    ctx = json.loads((tmp_path / "guard.json").read_text())
    assert ctx["phase"] == "red" and ctx["root"] == str(Path(repo).resolve()) and ctx["agent"] == "test-author"
    assert "KEEL_BIN" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN=key-oauth" in env
    assert "ANTHROPIC_API_KEY" not in env and "GH_TOKEN" not in env
    # the MCP servers are connected before the first model call (claude 2.1 -p connects them in the background)
    assert "MCP_CONNECTION_NONBLOCKING=0" in env
    guard = [s for s in steps if s[0] == "guard"]
    assert guard and guard[0][1] == 'src/scores/x.py: editing api-main is blocked in phase "red".' and guard[0][2]["path"] == "src/scores/x.py"
    assert res.data["refusals"][0]["path"] == "src/scores/x.py"

    monkeypatch.delenv("KEEL_CLAUDE_BIN")
    monkeypatch.setenv("KEEL_CLAUDE_BIN", fake_claude(tmp_path, repo, tmp_path))
    await ClaudeCLIRunner().run(req(repo, {"provider": "claude", "mode": "subscription", "model": "sonnet"}), lambda *a, **k: None)
    assert "CLAUDE_CODE_OAUTH_TOKEN=env-oauth" in (tmp_path / "env").read_text()


def test_claude_flow_writes_the_guard_context_first_and_reports_hook_refusals(client, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    monkeypatch.setenv("KEEL_CLAUDE_BIN", fake_claude(tmp_path, repo, tmp_path))
    flow = wf([{"id": "r", "kind": "agent", "name": "red", "agent": "test-author", "phase": "red"}])
    tid = start(client, repo, workflow=flow, models={"default": {"provider": "claude", "mode": "subscription", "model": "sonnet"}},
                settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause",
                          "unlocks": [{"path": "src/scores/y.py", "phase": "red"}]})
    s = wait(client, tid)
    assert s["status"] == "done", s
    seen = json.loads((tmp_path / "guard.json").read_text())
    assert seen["phase"] == "red" and seen["thread"] == tid and seen["agent"] == "test-author"
    assert seen["unlocks"][0]["path"] == "src/scores/y.py"
    refused = client.bus.of(tid, "guard.refused")
    assert refused and refused[0]["data"]["source"] == "keel-hook"
    assert refused[0]["data"]["path"] == "src/scores/x.py" and refused[0]["data"]["phase"] == "red"
    assert not (Path(repo) / ".keel" / "state.json").exists()


async def test_copilot_runner_gets_github_token_only_for_copilot(tmp_path, monkeypatch, repo):
    bin_ = script(tmp_path, "copilot", f"env > {tmp_path}/env\necho done\n")
    monkeypatch.setenv("KEEL_COPILOT_BIN", bin_)
    monkeypatch.setenv("GH_TOKEN", "env-gh")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    model = {"provider": "copilot", "mode": "subscription", "model": "gpt-5"}
    await CopilotCLIRunner().run(req(repo, model), lambda *a, **k: None)
    env = (tmp_path / "env").read_text()
    assert "GH_TOKEN=env-gh" in env and "ANTHROPIC_API_KEY" not in env
    await CopilotCLIRunner().run(req(repo, model, keys={"copilot": "key-gh"}), lambda *a, **k: None)
    env = (tmp_path / "env").read_text()
    assert "GH_TOKEN=key-gh" in env and "COPILOT_GITHUB_TOKEN=key-gh" in env
    assert "GH_TOKEN" not in safe_env() and "CLAUDE_CODE_OAUTH_TOKEN" not in safe_env()


async def test_opencode_loads_keels_guard_plugin_from_the_run_folder(tmp_path, monkeypatch, repo):
    old = Path(repo) / ".opencode" / "plugins" / "keel.js"          # keel v0.3 installed keel v1's adapter here
    old.parent.mkdir(parents=True)
    old.write_text("// keel's enforcement, for OpenCode.\n")
    refused = {"type": "tool_use", "part": {"type": "tool", "tool": "write", "state": {
        "status": "error", "input": {"filePath": str(Path(repo) / "src/scores/x.py")},
        "error": 'Error: [keel guard] src/scores/x.py: editing api-main is blocked in phase "red".'}}}
    done = {"type": "step_finish", "part": {"type": "step_finish", "tokens": {"input": 3, "output": 1}}}
    out = "\n".join(json.dumps(x) for x in (refused, done))
    (tmp_path / "oc.out").write_text(out + "\n")
    monkeypatch.setenv("KEEL_OPENCODE_BIN", script(tmp_path, "opencode", f"env > {tmp_path}/env\ncat {tmp_path}/oc.out\n"))
    notes = []
    res = await OpenCodeRunner().run(req(repo, {"provider": "copilot", "mode": "opencode", "model": "gpt-5"}),
                                     lambda k, t="", **kw: notes.append((k, t)))
    assert res.tokens_in == 3
    env = dict(line.split("=", 1) for line in (tmp_path / "env").read_text().splitlines() if "=" in line)
    conf = Path(env["OPENCODE_CONFIG_DIR"])
    plugin = (conf / "plugins" / "keel-guard.js").read_text()
    assert json.dumps([sys.executable, "-I", "-m", "keel_engine.hook", "pre-tool"]) in plugin
    assert "tool.execute.before" in plugin and "KEEL_BIN" not in plugin
    assert not str(conf).startswith(str(repo)) and Path(env["KEEL_GUARD_CTX"]).is_file()
    assert "KEEL_BIN" not in env
    assert not old.exists() and any("keel v1's opencode adapter" in t for k, t in notes)
    assert res.data["refusals"][0]["path"] == "src/scores/x.py" and "blocked in phase" in res.data["refusals"][0]["reason"]
    assert git(repo, "status", "--porcelain").stdout == ""          # nothing of keel's is written into the project


def test_provider_test_uses_login_rules(client, tmp_path, monkeypatch, repo):
    assert login_keys("claude", "subscription", "t") == {"claude_oauth": "t"}
    assert login_keys("copilot", "subscription", "t") == {"copilot": "t"}
    assert login_keys("claude", "api", "t") == {}
    monkeypatch.setenv("KEEL_FAKE", "0")
    ok = json.dumps({"type": "result", "result": "OK", "usage": {}})
    monkeypatch.setenv("KEEL_CLAUDE_BIN", script(tmp_path, "claude", f"cat >/dev/null\nenv > {tmp_path}/env\necho '{ok}'\n"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    r = client.post("/providers/test", json={"provider": "claude", "mode": "subscription", "model": "sonnet", "key": "oat-1"}).json()
    assert r["ok"], r
    env = (tmp_path / "env").read_text()
    assert "CLAUDE_CODE_OAUTH_TOKEN=oat-1" in env and "ANTHROPIC_API_KEY" not in env


# ------------------------------------------------------------------ 6. knowledge-refresh

def test_knowledge_refresh_with_sections(client, repo):
    tid = start(client, repo, workflow="knowledge-refresh", title="Refresh knowledge",
                settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "sections": ["architecture", "data"]})
    s = wait(client, tid)
    assert s["status"] == "done", s
    files = {p.name for p in (Path(repo) / "docs/knowledge").glob("*.md")}
    assert files == {"architecture.md", "data.md"}
    assert len(client.bus.of(tid, "agent.started")) == 2
    assert log(repo)[0] == "docs(memory): Refresh knowledge"
    v = verdicts.latest("demo", "memory")
    assert v["ok"] is True and v["commit"] and v["detail"]["selected"] == ["architecture", "data"]
    assert not (Path(repo) / ".keel/memory.json").exists()


def test_knowledge_refresh_default_all_sections(client, repo):
    tid = start(client, repo, workflow="knowledge-refresh", title="Refresh knowledge")
    assert wait(client, tid)["status"] == "done"
    assert len(list((Path(repo) / "docs/knowledge").glob("*.md"))) == 5
    assert get_template("knowledge-refresh").step("commit").lock


# ------------------------------------------------------------------ 7. estimate + catalog

def test_estimate_prices_per_provider_and_catalog(client):
    y = get_template("fix").yaml
    api = client.post("/workflows/estimate", json={"yaml": y, "acs": 1, "models": {
        "default": {"provider": "claude", "mode": "api", "model": "sonnet"},
        "implementer": {"provider": "codex", "mode": "api", "model": "gpt-5.5"}}}).json()
    assert api["cost_usd"] > 0 and api["cost_by_provider"]["claude"] > 0 and api["cost_by_provider"]["codex"] > 0
    assert api["by_provider"]["claude"] > 0 and api["by_provider"]["codex"] > 0
    sub = client.post("/workflows/estimate", json={"yaml": y, "acs": 1, "models": {
        "default": {"provider": "claude", "mode": "subscription", "model": "sonnet"},
        "implementer": {"provider": "copilot", "mode": "subscription", "model": "gpt-5"}}}).json()
    assert sub["cost_usd"] == 0 and sub["premium_requests"] >= 1 and sub["by_provider"]["claude"] == api["by_provider"]["claude"]
    cat = client.get("/providers/models").json()
    assert set(cat) == {"fake", "claude", "codex", "copilot"}
    assert all({"id", "label"} <= set(x) for p in cat.values() for items in p["modes"].values() for x in items)


def test_knowledge_refresh_sections_from_acs(client, repo):
    acs = [{"id": s, "layer": "API", "title": s} for s in ("domain", "integrations")]
    tid = start(client, repo, workflow="knowledge-refresh", title="Refresh knowledge", acs=acs)
    s = wait(client, tid)
    assert s["status"] == "done", s
    assert {p.name for p in (Path(repo) / "docs/knowledge").glob("*.md")} == {"domain.md", "integrations.md"}
    assert all(a["status"] == "done" for a in s["acs"])


def test_api_written_unlocks_are_merged_and_honoured(client, repo):
    rel = "tests/test_rogue_implementer.py"
    tid = start(client, repo, models=_rogue_models())
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"
    r = client.post(f"/threads/{tid}/unlocks", json={"path": rel, "phase": "green", "reason": "fixture helper"})
    assert r.status_code == 200, r.text
    assert [u for u in r.json() if u["path"] == rel][0]["by"] == "api"
    assert {"path": rel, "phase": "green"} in client.get(f"/threads/{tid}").json()["unlocks"]
    s = to_loop(client, tid, s)
    assert s["waiting"]["step"] == "ac_gate"
    assert (Path(repo) / rel).exists()                                   # the guard honoured it
    assert {"path": rel, "phase": "green"} in s["unlocks"]
    mine = [u for u in client.get(f"/threads/{tid}/unlocks").json() if u["path"] == rel]
    assert len(mine) == 1 and mine[0]["reason"] == "fixture helper" and mine[0]["phase"] == "green"
    assert [u for u in graph_values(client, tid)["unlocks"] if u["path"] == rel]   # merged into the flow state
    assert not (Path(repo) / ".keel" / "state.json").exists()
