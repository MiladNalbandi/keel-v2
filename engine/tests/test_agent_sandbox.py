"""Native mode: an agent's commands start in an OS sandbox that hides keel's own files (tools/agent_sandbox.py)."""

import os
import subprocess
import sys

import pytest

from keel_engine.tools import agent_sandbox, agent_user, testcmd


@pytest.fixture
def native(tmp_path, monkeypatch):
    """A native keel's folders: ~/.keel2/<name>/data (KEEL_DATA, with its sign-in key) and ~/.keel2/<name>/run."""
    home = tmp_path / "keel2" / "keel-x"
    (home / "data" / "run").mkdir(parents=True)
    (home / "data" / "run" / "web-key").write_text("the-web-key\n")
    (home / "data" / "keel.db").write_text("the-db-content")
    (home / "run").mkdir()
    (home / "run" / "token").write_text("the-internal-token")
    monkeypatch.setenv("KEEL_NATIVE", "1")
    monkeypatch.setenv("KEEL_DATA", str(home / "data"))
    monkeypatch.delenv("KEEL_SANDBOX", raising=False)
    monkeypatch.delenv("KEEL_AGENT_USER", raising=False)
    agent_sandbox._reset_for_tests()
    yield home
    agent_sandbox._reset_for_tests()


def _real(p):
    return os.path.realpath(str(p))


def test_nothing_changes_outside_native_mode(native, monkeypatch):
    monkeypatch.delenv("KEEL_NATIVE")
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    assert agent_sandbox.wrap(["sh", "-c", "make test"], "/p") == ["sh", "-c", "make test"]


def test_keel_sandbox_off_turns_it_off(native, monkeypatch):
    monkeypatch.setenv("KEEL_SANDBOX", "off")
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    assert agent_sandbox.wrap(["sh", "-c", "x"], "/p") == ["sh", "-c", "x"]
    assert "KEEL_SANDBOX=off" in agent_sandbox.status()


def test_the_data_folder_and_the_run_folder_are_hidden(native):
    assert agent_sandbox.hidden() == [_real(native / "data"), _real(native / "run")]


def test_bwrap_hides_keels_folders_and_keel_s_processes(native, monkeypatch, tmp_path):
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    project = tmp_path / "shop"
    project.mkdir()
    argv = agent_sandbox.wrap(["/bin/sh", "-c", "npm test"], str(project))
    assert argv[0] == "bwrap" and argv[-4:] == ["--", "/bin/sh", "-c", "npm test"]
    assert ["--tmpfs", _real(native / "data")] == argv[argv.index(_real(native / "data")) - 1:][:2]
    assert "--tmpfs" in argv and _real(native / "run") in argv
    assert "--unshare-pid" in argv and "--die-with-parent" in argv
    assert argv[argv.index("--chdir") + 1] == _real(project)
    assert "--bind" not in argv          # the project is not in keel's folders: nothing to open again


def test_a_project_inside_keels_data_folder_stays_open(native, monkeypatch):
    demo = native / "data" / "demo"
    demo.mkdir()
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    argv = agent_sandbox.wrap(["/bin/sh", "-c", "pytest"], str(demo))
    i = argv.index("--bind")
    assert argv[i:i + 3] == ["--bind", _real(demo), _real(demo)]
    assert argv.index("--bind") > max(j for j, a in enumerate(argv) if a == "--tmpfs")   # after the empty folder
    monkeypatch.setitem(agent_sandbox._found, "backend", "sandbox-exec")
    prof = agent_sandbox.wrap(["/bin/sh", "-c", "pytest"], str(demo))[2]
    assert prof.index("(deny file-read* file-write*") < prof.index(f'(allow file-read* file-write* (subpath "{_real(demo)}"))')


def test_a_flows_worktree_of_that_project_keeps_the_whole_project_open(native, monkeypatch):
    """A worktree's .git file points into the project's .git: the project stays open, the command works in the worktree."""
    demo = native / "data" / "demo"
    wt = demo / ".keel" / "worktrees" / "flow-x"
    wt.mkdir(parents=True)
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    argv = agent_sandbox.wrap(["/bin/sh", "-c", "git status"], str(wt))
    i = argv.index("--bind")
    assert argv[i:i + 3] == ["--bind", _real(demo), _real(demo)]
    assert argv[argv.index("--chdir") + 1] == _real(wt)
    monkeypatch.setitem(agent_sandbox._found, "backend", "sandbox-exec")
    prof = agent_sandbox.wrap(["/bin/sh", "-c", "git status"], str(wt))[2]
    assert f'(allow file-read* file-write* (subpath "{_real(demo)}"))' in prof


def test_the_macos_profile_denies_keels_folders_after_allowing_the_rest(native, monkeypatch):
    monkeypatch.setitem(agent_sandbox._found, "backend", "sandbox-exec")
    argv = agent_sandbox.wrap(["/bin/sh", "-c", "go test ./..."], "/elsewhere")
    assert argv[:2] == ["/usr/bin/sandbox-exec", "-p"] and argv[3:] == ["/bin/sh", "-c", "go test ./..."]
    prof = argv[2]
    assert prof.startswith("(version 1)\n(allow default)\n(deny file-read* file-write* ")
    assert f'(subpath "{_real(native / "data")}")' in prof and f'(subpath "{_real(native / "run")}")' in prof
    assert "(allow file-read* file-write* (subpath" not in prof


def test_a_quote_in_a_path_cannot_end_the_profile_string():
    prof = agent_sandbox.macos_profile(['/x/a"b) (allow default'], None)
    assert '(subpath "/x/a\\"b) (allow default")' in prof


def test_without_a_sandbox_commands_run_as_before_and_keel_says_so(native, monkeypatch):
    monkeypatch.setitem(agent_sandbox._found, "backend", None)
    assert agent_sandbox.wrap(["sh", "-c", "x"], "/p") == ["sh", "-c", "x"]
    assert "agents' commands: no sandbox (" in agent_sandbox.status()


def test_agent_user_wrap_uses_the_sandbox_natively(native, monkeypatch):
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    argv, env = agent_user.wrap(["sh", "/tmp/c/run-1.sh"], {"PATH": "/bin"}, cwd="/tmp/c")
    assert argv[0] == "bwrap" and argv[-2:] == ["sh", "/tmp/c/run-1.sh"] and env == {"PATH": "/bin"}


def test_an_agents_command_goes_through_the_sandbox_and_keels_own_do_not(native, monkeypatch, tmp_path):
    """testcmd: the agent's shell and the project's commands (as_agent) start in the sandbox; keel's own (gh with
    keel's token) do not."""
    seen = []

    def fake(argv, cwd=None):
        seen.append((list(argv), cwd))
        return ["/usr/bin/env", "IN_THE_SANDBOX=yes", *argv]

    monkeypatch.setattr(agent_sandbox, "wrap", fake)
    code, out = testcmd.run(str(tmp_path), "echo box=$IN_THE_SANDBOX", 30)
    assert code == 0 and "box=yes" in out and seen == [(["/bin/sh", "-c", "echo box=$IN_THE_SANDBOX"], str(tmp_path))]
    code, out = testcmd.run(str(tmp_path), "echo box=$IN_THE_SANDBOX", 30, as_agent=False)
    assert code == 0 and "box=yes" not in out and len(seen) == 1


# ---------------------------------------------------------------- the real sandbox (Linux with bubblewrap, macOS)

def _need_backend():
    b = agent_sandbox.backend()
    if b is None:
        if os.environ.get("KEEL_TEST_SANDBOX") == "1":
            pytest.fail("KEEL_TEST_SANDBOX=1 but this computer has no working sandbox (bubblewrap on Linux)")
        pytest.skip("no working sandbox here (bubblewrap on Linux, sandbox-exec on macOS)")
    return b


def test_for_real_an_agents_command_cannot_read_keels_keys(native, tmp_path):
    _need_backend()
    project = tmp_path / "shop"
    project.mkdir()
    (project / "README.md").write_text("# shop\n")
    key, token, db = native / "data" / "run" / "web-key", native / "run" / "token", native / "data" / "keel.db"
    code, out = testcmd.run(str(project), f"cat README.md; cat '{key}'; cat '{token}'; cat '{db}'; ls '{native / 'data'}'", 60)
    assert "# shop" in out
    assert "the-web-key" not in out and "the-internal-token" not in out and "the-db-content" not in out
    assert "keel.db" not in out.splitlines()                          # ls of the data folder shows nothing of it
    code, out = testcmd.run(str(project), f"echo forged > '{key}' 2>/dev/null; echo done", 60)
    assert key.read_text() == "the-web-key\n"
    # the project's own files: written as before
    code, out = testcmd.run(str(project), "echo built > out.txt && cat out.txt", 60)
    assert code == 0 and (project / "out.txt").read_text().strip() == "built"


def test_for_real_a_project_inside_keels_data_folder_still_works(native):
    _need_backend()
    demo = native / "data" / "demo"
    demo.mkdir()
    (demo / "a.txt").write_text("demo file\n")
    code, out = testcmd.run(str(demo), f"cat a.txt && echo new > b.txt; cat '{native / 'data' / 'run' / 'web-key'}'", 60)
    assert "demo file" in out and (demo / "b.txt").read_text().strip() == "new" and "the-web-key" not in out


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc: Linux")
def test_for_real_an_agents_command_cannot_read_keels_environment(native):
    _need_backend()
    keel = subprocess.Popen(["sleep", "60"], env={**os.environ, "KEEL_INTERNAL_TOKEN": "secret-of-the-api"})
    try:
        code, out = testcmd.run("/tmp", f"cat /proc/{keel.pid}/environ; ls /proc | grep -c '^[0-9]'", 60)
        assert "secret-of-the-api" not in out
    finally:
        keel.kill()
