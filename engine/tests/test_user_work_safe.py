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


def test_commit_subject_is_short_and_body_keeps_the_criterion():
    from keel_engine.runtime.actions import commit_message
    long = "For a playerId with at least one recorded score, `rankOf` returns the 1-based rank of that player's best score"
    subject, body = commit_message("feat(AC-2)", long)
    assert len(subject) <= 72 and subject.startswith("feat(AC-2): For a playerId") and subject.endswith("…")
    assert body == long
    assert commit_message("test(AC-1)", "rankOf is exported") == ("test(AC-1): rankOf is exported", "")


def test_output_tail_starts_at_a_whole_line():
    from keel_engine.runtime.actions import tail
    out = "\n".join(f"line {i} some text" for i in range(200))
    t = tail(out, 300)
    first, second = t.split("\n")[:2]
    assert first.endswith("earlier lines not shown") and second.startswith("line ") and second.endswith("some text")
    assert t.endswith("line 199 some text") and tail("short", 300) == "short"
