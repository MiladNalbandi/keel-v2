"""keel 0.18.18 the guard's rules for KeelBot's git work and its failures: git branch work in Edit mode runs after the
person's OK, commands that throw work away always ask with a warning, "always" keeps a command's family, Ask mode refuses
in its own words, and the same failing command is refused while nothing changed (runtime/repeats.py)."""

import subprocess
from pathlib import Path

import pytest

from keel_engine import hook, rules
from keel_engine.runtime import permissions, repeats, run_mode
from keel_engine.tools.agent_tools import ToolBox


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def folder(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("one\n")
    git(r, "add", "a.txt")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one")
    return r


@pytest.mark.parametrize("command", ["git status", "git stash", "git stash pop", "git checkout main", "git switch main",
                                     "git pull", "git fetch origin", "git commit -m 'wip: my changes'",
                                     "git checkout -- a.txt", "git reset --hard"])
def test_git_branch_work_passes_the_guard_with_no_flow(command):
    """Edit and side mode (phase none): the guard lets git branch work through; the person's OK decides."""
    assert rules.check_bash("none", command).ok


@pytest.mark.parametrize("command,in_flow", [("git reset --hard", False), ("git commit -m x", False),
                                             ("git stash", True), ("git checkout main", True)])
def test_in_a_flows_phase_reset_and_commit_stay_refused(command, in_flow):
    assert rules.check_bash("green", command).ok is in_flow


def test_which_commands_ask_and_which_warn():
    for cmd in ("git stash", "git stash pop", "git checkout main", "git switch main", "git pull", "git commit -m x"):
        assert permissions.needs_ask(cmd) and not permissions.destructive(cmd), cmd
    assert not permissions.needs_ask("git status") and not permissions.needs_ask("git fetch origin")
    for cmd, word in (("git checkout -- .", "lost"), ("git checkout .", "lost"), ("git restore src/a.py", "lost"),
                      ("git reset --hard HEAD", "cannot be undone"), ("git clean -fd", "deletes"),
                      ("git stash drop", "for good"), ("git stash clear", "for good"), ("git branch -D old", "deletes"),
                      ("git switch --discard-changes main", "throws away"), ("cd x && git reset --hard", "cannot be undone")):
        assert word in permissions.destructive(cmd), cmd
    assert not permissions.destructive("git restore --staged a.txt")          # unstaging loses nothing
    assert permissions.changes_tree("git pull") and permissions.changes_tree("git stash")
    assert not permissions.changes_tree("git status") and not permissions.changes_tree("git log")


def test_always_keeps_the_commands_family_and_never_a_destructive_one():
    assert permissions.always_pattern("git checkout main") == "git checkout *"
    assert permissions.always_pattern("npm run build -- --watch") == "npm run build *"
    assert permissions.always_pattern("npm install left-pad") == "npm install *"
    assert permissions.always_pattern("git stash pop") == "git stash pop"
    assert permissions.always_pattern("ruff check src") == "ruff *"
    assert permissions.always_pattern("git add a && git commit -m x") == "git add a && git commit -m x"   # compound: exact
    assert permissions.always_pattern("git checkout -- .") == "git checkout -- ."
    assert permissions.granted("git checkout feature/x", ["git checkout *"])
    assert not permissions.granted("git checkout -- .", ["git checkout *", "git checkout -- ."])      # asked every time
    assert not permissions.granted("git reset --hard", ["git reset --hard"])


def test_a_readonly_refusal_in_keelbots_own_words(folder):
    why = run_mode.readonly_bash("git checkout main")
    assert why.startswith(run_mode.READONLY_WHO)
    said = run_mode.relabel(why, "KeelBot's Ask mode is read only", "Offer /mode edit.")
    assert said.startswith("KeelBot's Ask mode is read only: \"git checkout\"") and said.endswith("Offer /mode edit.")
    assert run_mode.relabel(why) == why                                     # a flow's agents: as before
    ctx = {"root": str(folder), "phase": "none", "readonly": True, "readonly_label": "KeelBot's Ask mode is read only",
           "readonly_note": "Offer /mode edit."}
    assert hook.decide("Bash", {"command": "git stash"}, ctx).startswith("KeelBot's Ask mode is read only")
    assert hook.decide("Write", {"file_path": str(folder / "a.txt"), "content": "x"}, ctx).startswith("KeelBot's Ask mode")
    tb = ToolBox(str(folder), "none", readonly=True)
    tb.readonly_label, tb.readonly_note = "KeelBot's Ask mode is read only", "Offer /mode edit."
    assert tb.run_command("git checkout main").startswith("REFUSED: KeelBot's Ask mode is read only")


def test_the_same_failing_command_is_refused_until_something_changes(folder, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    tb = ToolBox(str(folder), "none")
    tb.no_repeat = str(run)
    assert tb.run_command("git checkout nope").startswith("exit 1")
    again = tb.run_command("git checkout   nope")                          # the same command (spaces do not count)
    assert again.startswith("REFUSED: This exact command failed before and nothing in the folder changed")
    assert "pathspec 'nope' did not match" in again and "keel-questions" in again
    (folder / "a.txt").write_text("two\n")                                 # something changed: it may run again
    assert tb.run_command("git checkout nope").startswith("exit 1")
    # the hook reads the same list (failed.json next to the run's guard context)
    ctx = {"root": str(folder), "phase": "none", "no_repeat": True, "_path": str(run / "guard.json")}
    assert "failed before" in hook.decide("Bash", {"command": "git checkout nope"}, ctx)
    assert hook.decide("Bash", {"command": "git checkout nope"}, {**ctx, "no_repeat": False}) is None
    # a flow's agents (no no_repeat folder) run a command as often as they ask
    plain = ToolBox(str(folder), "none")
    assert plain.run_command("git checkout nope").startswith("exit 1") and plain.run_command("git checkout nope").startswith("exit 1")


def test_a_new_run_starts_with_the_last_runs_failures(folder, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir(), second.mkdir()
    note = repeats.remember(first, str(folder), "git checkout nope", "exit 1\nerror: pathspec 'nope' did not match")
    assert note["said"] == "error: pathspec 'nope' did not match" and note["state"]
    repeats.seed(second, [note])
    assert "failed before" in repeats.refusal(second, str(folder), "git checkout nope")
    (folder / "b.txt").write_text("new\n")                                 # the person changed something in between
    assert repeats.refusal(second, str(folder), "git checkout nope") is None
    outside = tmp_path / "plain"
    outside.mkdir()
    repeats.remember(first, str(outside), "make x", "boom")
    assert repeats.refusal(first, str(outside), "make x") is None          # no git: keel cannot tell, so it runs


def test_a_network_error_is_not_a_failure_to_refuse_again(folder, tmp_path):
    # keel 0.18.63 `gh run view` failed on "TLS handshake timeout": the same call a moment later may work
    run = tmp_path / "run"
    run.mkdir()
    assert repeats.transient("failed to get run: Get \"https://api.github.com/...\": net/http: TLS handshake timeout")
    assert repeats.transient("curl: (6) Could not resolve host: example.com")
    assert not repeats.transient("error: pathspec 'nope' did not match")
    assert repeats.remember(run, str(folder), "gh run view 1234", "net/http: TLS handshake timeout") == {}
    assert repeats.refusal(run, str(folder), "gh run view 1234") is None
    repeats.remember(run, str(folder), "git checkout nope", "error: pathspec 'nope' did not match")
    assert "failed before" in repeats.refusal(run, str(folder), "git checkout nope")
