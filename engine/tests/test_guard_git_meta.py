"""0.21.3 a step that writes the repo's own git settings (.git/config, hooks, info) gets them put back and refused."""

import os
import subprocess
from pathlib import Path

from keel_engine.tools import guard


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    (r / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(r), "add", "."], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], check=True)
    return r


def test_fsmonitor_and_hook_written_by_a_step_are_put_back(tmp_path):
    r = _repo(tmp_path)
    config_before = (r / ".git" / "config").read_bytes()
    before = guard.snapshot(str(r))
    # what a script of the agent's could do; git status never shows .git/
    with open(r / ".git" / "config", "a") as f:
        f.write("[core]\n\tfsmonitor = /tmp/evil.sh\n")
    hook = r / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ntouch /tmp/owned\n")
    hook.chmod(0o755)
    (r / ".git" / "info" / "attributes").write_text("* filter=evil\n")
    refused = guard.guard_diff(str(r), "none", before)
    paths = sorted(x["path"] for x in refused)
    assert paths == [".git/config", ".git/hooks/pre-commit", ".git/info/attributes"]
    assert all(x["bucket"] == "git-settings" for x in refused)
    assert (r / ".git" / "config").read_bytes() == config_before
    assert not hook.exists() and not (r / ".git" / "info" / "attributes").exists()


def test_a_removed_sample_hook_comes_back_with_its_mode(tmp_path):
    r = _repo(tmp_path)
    samples = sorted((r / ".git" / "hooks").glob("*.sample"))
    if not samples:
        return
    s = samples[0]
    mode = s.stat().st_mode & 0o777
    before = guard.snapshot(str(r))
    s.unlink()
    refused = guard.guard_diff(str(r), "green", before)
    assert any(x["path"].endswith(s.name) for x in refused)
    assert s.exists() and s.stat().st_mode & 0o777 == mode


def test_nothing_changed_nothing_refused(tmp_path):
    r = _repo(tmp_path)
    before = guard.snapshot(str(r))
    (r / "a.txt").write_text("b\n")          # the project's own files are the phase's business, not this check
    assert [x for x in guard.guard_diff(str(r), "none", before) if x["bucket"] == "git-settings"] == []


def test_a_worktree_guards_the_common_config(tmp_path):
    r = _repo(tmp_path)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(r), "worktree", "add", "-q", "-b", "side", str(wt)], check=True)
    before = guard.snapshot(str(wt))
    with open(r / ".git" / "config", "a") as f:
        f.write("[core]\n\thooksPath = /tmp/h\n")
    refused = guard.guard_diff(str(wt), "none", before)
    assert any(os.path.basename(x["path"]) == "config" for x in refused)
    assert "hooksPath" not in (r / ".git" / "config").read_text()


def test_keels_own_sharing_of_the_folder_is_not_a_change(tmp_path):
    # #101 keel makes the project group rw before every command an agent runs (agent_user.share); the harness runs
    # commands inside the step, so .git's files got g+rw during it and every step reported "changed the git settings"
    r = _repo(tmp_path)
    for f in [r / ".git" / "config", *(r / ".git" / "hooks").glob("*.sample")]:
        f.chmod(f.stat().st_mode & ~0o070)
    before = guard.snapshot(str(r))
    subprocess.run(["chmod", "-R", "g+rwX", str(r / ".git")], check=True)
    assert [x for x in guard.guard_diff(str(r), "none", before) if x["bucket"] == "git-settings"] == []
    # a mode the step really changed still counts: a file the owner can now run
    cfg = r / ".git" / "config"
    cfg.chmod(cfg.stat().st_mode | 0o100)
    refused = [x for x in guard.guard_diff(str(r), "none", before) if x["bucket"] == "git-settings"]
    assert [x["path"] for x in refused] == [".git/config"] and not cfg.stat().st_mode & 0o100
