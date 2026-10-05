"""Red/green checks against a real node:test project (found by the real-life e2e run)."""
import asyncio
import json
import shutil
import subprocess

import pytest

from keel_engine.runtime.actions import ActionInput, verify_red
from keel_engine.tools import testcmd

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def project(tmp_path, with_ac_test: bool):
    (tmp_path / "package.json").write_text(json.dumps({"name": "p", "type": "module", "scripts": {"test": "node --test"}}))
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "a.test.js").write_text('import {test} from "node:test"; test("old", () => {});\n')
    if with_ac_test:
        (tmp_path / "test" / "rank.test.js").write_text(
            'import {test} from "node:test"; import assert from "node:assert/strict";\n'
            'test("AC-1 ranks the best player first", () => { assert.equal(1, 2); });\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def red(root):
    a = ActionInput(root=str(root), phase="red", title="t", ac={"id": "AC-1"}, acs=[{"id": "AC-1"}], fake=False, flow="feature")
    return asyncio.run(verify_red(a))


def test_node_test_gets_its_own_filter_flag(tmp_path):
    root = project(tmp_path, True)
    assert testcmd.command_for(str(root), "AC-1") == "npm test --silent -- --test-name-pattern=AC-1"


def test_a_real_failing_ac_test_is_red(tmp_path):
    assert red(project(tmp_path, True)).ok


def test_no_test_for_the_ac_is_never_red(tmp_path):
    res = red(project(tmp_path, False))
    assert not res.ok
    assert "No test for AC-1 ran" in res.note or "already pass" in res.note


def test_option_errors_and_empty_runs_are_spotted():
    assert testcmd.ran_no_tests("node: bad option: -t")
    assert testcmd.ran_no_tests("ℹ tests 0\nℹ pass 0")
    assert not testcmd.ran_no_tests("ℹ tests 3\nℹ fail 1")


# ------------------------------------------------------------------ already-met (keel v1)

def met_project(tmp_path):
    root = tmp_path / "met"
    (root / "test").mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"name": "p", "type": "module", "scripts": {"test": "node --test"}}))
    (root / "test" / "a.test.js").write_text('import {test} from "node:test"; test("old", () => {});\n')
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=root, check=True)
    # The AC's test: earlier work already makes it pass.
    (root / "test" / "rank.test.js").write_text(
        'import {test} from "node:test"; import assert from "node:assert/strict";\n'
        'test("AC-1 ranks the best player first", () => { assert.equal(1, 1); });\n')
    return root


def met_flow():
    from keel_engine.workflows.model import from_dict

    return from_dict({"name": "met", "keel_rules": False, "steps": [
        {"id": "red", "kind": "agent", "name": "red", "agent": "test-author", "phase": "red", "per_ac": True},
        {"id": "verify_red", "kind": "code", "name": "verify_red", "action": "verify_red+commit", "phase": "red", "per_ac": True},
        {"id": "green", "kind": "agent", "name": "green", "agent": "implementer", "phase": "green", "per_ac": True},
        {"id": "verify_green", "kind": "code", "name": "verify_green", "action": "verify_green", "phase": "green", "per_ac": True},
        {"id": "ac_gate", "kind": "gate", "name": "AC gate", "back": "red", "phase": "gate", "per_ac": True},
    ]})


def start_met(client, root):
    from conftest import start

    return start(client, root, workflow=met_flow(), acs=[{"id": "AC-1", "layer": "API", "title": "ranks"}],
                 settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "simulate_checks": False})


def test_an_ac_test_that_already_passes_asks_at_once_and_can_be_marked_met(client, tmp_path):
    from conftest import decide, wait

    root = met_project(tmp_path)
    tid = start_met(client, root)
    s = wait(client, tid, timeout=60)
    assert s["status"] == "waiting", s
    w = s["waiting"]
    assert w["kind"] == "gate" and w["title"] == "AC-1 already passes" and w["options"] == ["approve", "reject"]
    assert "earlier" in w["detail"] and "AC-1 ranks the best player first" in w["detail"]
    assert len(client.bus.of(tid, "agent.started")) == 1                 # no retries of the red agent

    s = decide(client, tid, "approve", why="covered by AC-0")
    assert s["status"] == "done", s
    assert s["acs"][0]["status"] == "already-met"
    subjects = subprocess.run(["git", "log", "--format=%s"], cwd=root, capture_output=True, text=True).stdout.splitlines()
    assert subjects[0].startswith("test(AC-1)")
    started = [e["data"]["agent"] for e in client.bus.of(tid, "agent.started")]
    assert started == ["test-author"]                                    # green and the AC gate were skipped


def test_rejecting_already_met_sends_the_red_step_back_with_the_note(client, tmp_path):
    from conftest import decide, wait

    root = met_project(tmp_path)
    tid = start_met(client, root)
    assert wait(client, tid, timeout=60)["waiting"]["title"] == "AC-1 already passes"
    s = decide(client, tid, "reject", why="assert the order, not just the length")
    assert s["status"] == "waiting" and s["waiting"]["title"] == "AC-1 already passes", s   # still passes: asked again
    started = client.bus.of(tid, "agent.started")
    assert len(started) == 2
    fed = [e["data"]["text"] for e in client.bus.of(tid, "agent.step") if e["data"]["kind"] == "thinking"]
    assert fed and "stricter test" in fed[-1] and "assert the order" in fed[-1]


def test_unmatched_node_pattern_is_not_already_met():
    out = "✔ test/a.test.js (43ms)\n✔ test/r.test.js (41ms)\nℹ tests 2\nℹ pass 2\nℹ fail 0\n"
    assert testcmd.ac_test_passed(out, "AC-1") is False
    assert testcmd.ac_test_passed("✔ test/a.test.js (52ms)\n✔ AC-1 ranks (0.3ms)\nℹ tests 2\n", "AC-1") is True
    assert testcmd.ac_test_passed("ok 2 - AC-1 ranks\n# tests 2\n", "AC-1") is True
    assert testcmd.ac_test_passed("1 passed, 3 deselected in 0.02s", "AC-1") is None
    assert not testcmd.ran_no_tests("# tests 5\n# suites 0\n# pass 1\n# fail 0\n# skipped 4\n")


def test_turn_limit_is_explained_and_token_counts_are_not_a_rate_limit():
    from keel_engine.models.cli import classify_failure
    out = ('{"type":"assistant","message":{"usage":{"input_tokens":429}}}\n'
           '{"type":"result","subtype":"error_max_turns","num_turns":21,"is_error":true,"modelUsage":{"claude-haiku-4-5":'
           '{"inputTokens":429,"outputTokens":15,"contextWindow":200000}},"terminal_reason":"max_turns"}\n')
    e = classify_failure("claude", out, "", 1)
    assert str(e).startswith("The agent used all its turns (21)") and "{" not in str(e)
    assert "maxTurns" in e.hint
    e2 = classify_failure("claude", '{"type":"assistant","usage":{"input_tokens":429}}\n', "", 1)
    assert "usage limit" not in str(e2) and "{" not in str(e2)
    e3 = classify_failure("claude", "", "Error: 429 Too Many Requests", 1)
    assert "usage limit" in str(e3)


def test_the_spec_step_gives_the_explorer_more_turns(tmp_path, monkeypatch):
    from keel_engine.runtime import prompts
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "explorer.md").write_text("---\nname: explorer\nmaxTurns: 20\n---\nYou map code.\n")
    monkeypatch.setenv("KEEL_HOME", str(tmp_path))
    assert prompts.max_turns("explorer") == 20
    assert prompts.max_turns("explorer", "spec") == 40
    assert prompts.max_turns("explorer", "triage") == 40
    assert prompts.max_turns("explorer", "red") == 20


def test_not_logged_in_inside_claudes_json_is_reported_as_such():
    from keel_engine.models.cli import classify_failure
    out = ('{"type":"assistant","message":{"content":[{"type":"text","text":"Not logged in · Please run /login"}]}}\n'
           '{"type":"result","subtype":"success","is_error":true,"result":"Not logged in · Please run /login"}\n')
    assert "not logged in" in str(classify_failure("claude", out, "", 1))
