"""A keel commit must never take the user's own uncommitted work with it."""
import asyncio
import subprocess

from keel_engine.runtime.actions import ActionInput, commit
from keel_engine.tools import git


def sh(root, *a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout


def repo(tmp_path):
    sh(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text("x = 1\n")
    sh(tmp_path, "add", "-A"); sh(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return tmp_path


def test_preexisting_changes_stay_out_of_keel_commits(tmp_path):
    root = repo(tmp_path)
    (root / "app.py").write_text("x = 2  # my own work in progress\n")
    (root / "notes.md").write_text("mine, untracked\n")
    pre = git.snapshot(str(root))
    assert set(pre) == {"app.py", "notes.md"}

    (root / "tests").mkdir()
    (root / "tests" / "test_new.py").write_text("def test_x():\n    assert True\n")   # what the agent wrote
    a = ActionInput(root=str(root), phase="red", title="x", ac={"id": "AC-001"}, acs=[{"id": "AC-001"}], fake=True,
                    flow="feature", preexisting=pre)
    res = asyncio.run(commit(a)) if asyncio.iscoroutinefunction(commit) else commit(a)
    assert res.ok, res
    committed = sh(root, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["tests/test_new.py"]
    status = sh(root, "status", "--porcelain")
    assert " M app.py" in status and "?? notes.md" in status          # still there, still uncommitted


def test_a_preexisting_file_the_agent_changed_is_committed(tmp_path):
    root = repo(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("# draft\n")
    pre = git.snapshot(str(root))
    (root / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n")       # the agent rewrote it
    a = ActionInput(root=str(root), phase="red", title="x", ac={"id": "AC-001"}, acs=[{"id": "AC-001"}], fake=True,
                    flow="feature", preexisting=pre)
    res = asyncio.run(commit(a)) if asyncio.iscoroutinefunction(commit) else commit(a)
    assert res.ok, res
    assert sh(root, "show", "--name-only", "--format=", "HEAD").split() == ["tests/test_a.py"]
