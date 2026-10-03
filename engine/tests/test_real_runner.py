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
