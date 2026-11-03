"""keel 0.22.0 #75 an e2e skip is kept for the branch until a file the e2e tests cover changes (runtime/kept_skips.py)."""
import subprocess
from pathlib import Path

import pytest

from keel_engine.runtime import feature_actions, kept_skips
from keel_engine.runtime.actions import ActionInput


def git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, capture_output=True, text=True)


def write(root, rel, text="x\n"):
    f = Path(root) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def commit(root, msg):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, ".keel/config.yml", "version: 4\nfrontend: {dir: web}\nbackend: {dir: api}\ne2e: {dir: e2e}\n")
    write(root, "web/src/App.tsx")
    write(root, "api/src/app.py")
    commit(root, "chore: start")
    git(root, "checkout", "-q", "-b", "feat/x")
    return root


def test_a_skip_holds_until_a_covered_file_changes(proj):
    kept_skips.remember("p1", str(proj), [{"step": "e2e", "reason": "no UI change"}, {"step": "lint", "reason": "x"}])
    keep = kept_skips.kept("p1", str(proj), "e2e")
    assert keep and keep["reason"] == "no UI change" and keep["note"].startswith("kept from ")
    assert kept_skips.kept("p1", str(proj), "lint") is None             # only e2e skips are kept
    write(proj, "api/src/app.py", "y\n")                                 # the backend: e2e tests do not cover it here
    commit(proj, "feat: api")
    assert kept_skips.kept("p1", str(proj), "e2e")
    write(proj, "web/src/App.tsx", "changed\n")                          # the UI: asked again, the file named
    assert kept_skips.kept("p1", str(proj), "e2e") is None
    assert "web/src/App.tsx" in kept_skips.stale("p1", str(proj), "e2e")


def test_a_skip_is_for_its_own_branch(proj):
    kept_skips.remember("p1", str(proj), [{"step": "e2e", "reason": "no UI change"}])
    git(proj, "checkout", "-q", "-b", "feat/y")
    assert kept_skips.kept("p1", str(proj), "e2e") is None and kept_skips.stale("p1", str(proj), "e2e") == ""


def test_a_skip_without_a_reason_is_not_kept(proj):
    kept_skips.remember("p1", str(proj), [{"step": "e2e", "reason": " "}])
    assert kept_skips.kept("p1", str(proj), "e2e") is None


def test_e2e_scope_keeps_write_without_running_and_does_not_ask_again(proj, monkeypatch):
    monkeypatch.setattr(feature_actions, "_tagged", lambda text, tag: ["the order page shows the total"])
    monkeypatch.setattr(feature_actions, "_layer_ids", lambda a, text, tag: ["AC-1"])
    a = ActionInput(root=str(proj), project="p1", phase="e2e", title="Orders", ac=None, fake=False, flow="feature", acs=[])
    first = feature_actions.e2e_scope(a)
    assert first.update["markers"]["*"]["TOOL"] == "no"                  # the e2e_tool gate asks
    feature_actions.e2e_unrun(a)                                          # the e2e_tool gate's "write"
    again = feature_actions.e2e_scope(a)
    assert "kept from" in again.note and again.update["data"]["e2e"]["run"] is False
    assert again.update["markers"]["*"]["TOOL"] == "kept"                 # the gate does not ask
    assert any(s["step"] == "e2e-run" and "kept from" in s["reason"] for s in again.update["data"]["ship_skipped"])
    write(proj, "e2e/orders.spec.ts")                                     # an e2e file changed: the gate asks again
    third = feature_actions.e2e_scope(a)
    assert "kept from" not in third.note and "e2e/orders.spec.ts" in third.update["data"]["e2e_tool_detail"]
