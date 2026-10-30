"""keel 0.19.0 harness mode: the harness flow (content/workflows/harness.yaml). The harness plans in its own session,
the person approves the plan, the same session builds it, keel runs the tests, the person looks, keel commits. On the
fake model in a real git repo; the fake model stands in for a harness that continues its session."""

from pathlib import Path

import pytest

from conftest import decide, start, wait
from test_flows_base import answers
from test_v02 import log
from keel_engine.runtime import actions as actions_mod, compiler as compiler_mod, harness_actions, memory as memory_mod
from keel_engine.tools import testcmd

PLAN = "## Goal\nShow the owner's name.\n## Acceptance criteria\n1. The page shows it.\nPLAN: ready"
MODELS = {"default": {"provider": "fake", "mode": "subscription", "model": "fake"}}


@pytest.fixture
def resumable(monkeypatch):
    """The fake model continues its session like claude does (a named session, resumed by the next step)."""
    monkeypatch.setattr(compiler_mod, "RESUMABLE", compiler_mod.RESUMABLE | {"fake"})
    monkeypatch.setattr(compiler_mod, "NAMED_SESSION", compiler_mod.NAMED_SESSION | {"fake"})


def build(req) -> str:
    (Path(req.root) / "owner.py").write_text("OWNER = 'Ada'\n")
    (Path(req.root) / "__pycache__").mkdir(exist_ok=True)
    (Path(req.root) / "__pycache__" / "owner.cpython-313.pyc").write_bytes(b"\x00junk")     # what a test run leaves
    return "Changed owner.py. Tests pass."


def plans(monkeypatch):
    return answers(monkeypatch, {"harness-planner": lambda r: PLAN, "harness-builder": build})


def test_plan_gate_then_the_same_session_builds_and_keel_commits(client, repo, monkeypatch, resumable):
    seen = plans(monkeypatch)
    tid = start(client, repo, workflow="harness", title="Show the owner name", models=MODELS)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "plan_gate", s
    assert [r.agent for r in seen] == ["harness-planner"]
    assert "This is a change to existing behaviour" in seen[0].prompt       # the intent line (data.intent: change)
    s = decide(client, tid, payload={"choice": "approve"})
    assert s["waiting"]["step"] == "done_gate", s
    assert [r.agent for r in seen] == ["harness-planner", "harness-builder"]
    planner, builder = seen
    assert planner.session and builder.session == planner.session          # one session for the whole flow
    assert not planner.resume and builder.resume
    s = decide(client, tid, payload={"choice": "commit"})
    assert s["waiting"]["step"] == "pr_gate", s
    s = decide(client, tid, payload={"choice": "skip"})
    assert s["status"] == "done", s
    assert log(repo)[0] == "feat: Show the owner name"
    import subprocess
    files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    assert "owner.py" in files and "__pycache__" not in files          # keel's commit leaves test-run junk out


def test_a_note_at_the_plan_gate_goes_back_to_the_same_session(client, repo, monkeypatch, resumable):
    seen = plans(monkeypatch)
    tid = start(client, repo, workflow="harness", title="Show the owner name", models=MODELS)
    wait(client, tid)
    s = decide(client, tid, why="also show the pet count", payload={"choice": "change"})
    assert s["waiting"]["step"] == "plan_gate", s
    assert [r.agent for r in seen] == ["harness-planner", "harness-planner"]
    assert "also show the pet count" in seen[-1].prompt
    assert seen[-1].resume and seen[-1].session == seen[0].session


def test_reject_at_the_plan_gate_ends_without_a_change(client, repo, monkeypatch):
    seen = plans(monkeypatch)
    tid = start(client, repo, workflow="harness", title="Show the owner name")
    wait(client, tid)
    s = decide(client, tid, why="not now", payload={"choice": "reject"})
    assert s["status"] == "done", s
    assert [r.agent for r in seen] == ["harness-planner"]


def test_failing_tests_go_back_to_the_builder_once_with_the_output(client, repo, monkeypatch, resumable):
    seen = plans(monkeypatch)
    left = {"n": 1}
    real = harness_actions.check

    def check(a):
        if left["n"]:
            left["n"] -= 1
            return actions_mod.ActionResult(False, "The project's tests failed: `pytest` exited 1.", "E assert 2 == 3")
        return real(a)
    monkeypatch.setitem(harness_actions.ACTIONS, "harness_check", check)
    tid = start(client, repo, workflow="harness", title="Show the owner name", models=MODELS)
    wait(client, tid)
    s = decide(client, tid, payload={"choice": "approve"})
    assert s["waiting"]["step"] == "done_gate", s
    assert [r.agent for r in seen] == ["harness-planner", "harness-builder", "harness-builder"]
    assert "assert 2 == 3" in seen[-1].prompt and seen[-1].session == seen[0].session


def test_the_intent_names_what_the_person_asked_for(client, repo, monkeypatch):
    seen = plans(monkeypatch)
    tid = start(client, repo, workflow="harness", title="Owner page crashes", data={"intent": "bug"})
    wait(client, tid)
    assert "reproduces it first" in seen[0].prompt


def test_the_plan_gate_shows_the_plan_and_how_to_open_the_session(client, repo, monkeypatch, resumable):
    plans(monkeypatch)
    tid = start(client, repo, workflow="harness", title="Show the owner name", models=MODELS)
    s = wait(client, tid)
    shown = s["waiting"].get("detail") or s.get("show") or ""
    assert "Show the owner's name." in shown
    assert "--resume" not in shown          # the fake model has no open command (claude, codex, copilot, opencode have)


def test_open_commands():
    assert memory_mod.open_command("claude", "s1", "subscription", "/data/agent-home/claude") == \
        "CLAUDE_CONFIG_DIR=/data/agent-home/claude claude --resume s1"
    assert memory_mod.open_command("codex", "s1") == "codex resume s1"
    assert memory_mod.open_command("copilot", "s1") == "copilot --resume s1"
    assert memory_mod.open_command("copilot", "s1", "opencode") == "opencode -s s1"
    assert memory_mod.open_command("claude", "s1", "api") is None
    assert memory_mod.open_command("fake", "s1") is None


def test_a_plain_python_project_with_a_tests_folder_runs_pytest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_books.py").write_text("def test_x():\n    pass\n")
    assert testcmd.detected(str(tmp_path)) == "python -m pytest -q"
    assert testcmd.detected(str(tmp_path / "missing")) is None
    (tmp_path / "package.json").write_text('{"scripts": {"test": "node --test"}}')
    assert testcmd.detected(str(tmp_path)) == "npm test --silent"           # a build file speaks first


def test_without_plan_mode_the_harness_builds_at_once(client, repo, monkeypatch, resumable):
    seen = plans(monkeypatch)
    tid = start(client, repo, workflow="harness", title="Show the owner name", models=MODELS, data={"plan_first": "no"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "done_gate", s
    assert [r.agent for r in seen] == ["harness-builder"]
