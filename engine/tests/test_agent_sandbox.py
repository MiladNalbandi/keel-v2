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
    monkeypatch.setenv("KEEL2_HOME", str(home.parent))        # never the real ~/.keel2 of the computer the tests run on
    monkeypatch.delenv("KEEL_HIDE", raising=False)
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


def test_every_keel2_keels_data_run_and_logs_are_hidden_but_not_its_release_or_clis(native):
    other = native.parent / "keel-other"
    for d in ("data", "run", "logs", "npm/bin", "current"):
        (other / d).mkdir(parents=True)
    (native / "logs").mkdir()
    got = agent_sandbox.hidden()
    for d in (native / "data", native / "run", native / "logs", other / "data", other / "run", other / "logs"):
        assert _real(d) in got, d
    assert not any(h.endswith(("/npm", "/npm/bin", "/current")) for h in got)
    assert _real(native.parent) not in got


def test_keel_hide_adds_folders_and_a_folder_inside_another_is_not_named_twice(native, monkeypatch, tmp_path):
    app = tmp_path / "app"
    (app / "run").mkdir(parents=True)
    (app / "logs").mkdir()
    monkeypatch.setenv("KEEL_HIDE", f"{app / 'run'}:{app / 'logs'}:{app / 'missing'}::{native / 'data' / 'run'}")
    got = agent_sandbox.hidden()
    assert _real(app / "run") in got and _real(app / "logs") in got
    assert not any("missing" in h for h in got)
    assert _real(native / "data" / "run") not in got           # inside the data folder, which is hidden already


def test_the_run_folder_is_not_guessed_next_to_a_data_folder_that_is_not_keel2s(native, monkeypatch, tmp_path):
    """The desktop app or a custom KEEL_DATA: only what KEEL_HIDE names, not a "run" that happens to be next to it."""
    (tmp_path / "elsewhere" / "data").mkdir(parents=True)
    (tmp_path / "elsewhere" / "run").mkdir()
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "elsewhere" / "data"))
    monkeypatch.setenv("KEEL2_HOME", str(tmp_path / "no-keel2-here"))
    assert agent_sandbox.hidden() == [_real(tmp_path / "elsewhere" / "data")]


def test_bwrap_runs_from_the_path_the_probe_found(native, monkeypatch, tmp_path):
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    monkeypatch.setitem(agent_sandbox._found, "bwrap", "/usr/bin/bwrap")
    assert agent_sandbox.wrap(["/bin/sh", "-c", "x"], str(tmp_path))[0] == "/usr/bin/bwrap"


def test_a_probe_that_found_no_sandbox_is_tried_again_later_and_one_that_found_it_is_kept(native, monkeypatch, tmp_path):
    monkeypatch.setattr(agent_sandbox.sys, "platform", "linux")
    exe = tmp_path / "bin" / "bwrap"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(agent_sandbox.shutil, "which", lambda name: str(exe))
    works, probes, now = [False], [], [1000.0]
    monkeypatch.setattr(agent_sandbox, "_probe", lambda argv: probes.append(argv) or works[0])
    monkeypatch.setattr(agent_sandbox.time, "monotonic", lambda: now[0])
    assert agent_sandbox.backend() is None and len(probes) == 1
    assert probes[0][0] == os.path.realpath(exe) and "--unshare-pid" in probes[0]
    now[0] += 10
    assert agent_sandbox.backend() is None and len(probes) == 1          # within a minute: the answer stands
    works[0] = True
    now[0] += agent_sandbox.RETRY_AFTER
    assert agent_sandbox.backend() == "bwrap" and len(probes) == 2
    now[0] += 10 * agent_sandbox.RETRY_AFTER
    works[0] = False
    assert agent_sandbox.backend() == "bwrap" and len(probes) == 2       # found: kept for the process


def test_bwrap_hides_keels_folders_and_keel_s_processes(native, monkeypatch, tmp_path):
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    project = tmp_path / "shop"
    project.mkdir()
    argv = agent_sandbox.wrap(["/bin/sh", "-c", "npm test"], str(project))
    assert argv[0] == "bwrap" and argv[-4:] == ["--", "/bin/sh", "-c", "npm test"]
    assert ["--tmpfs", _real(native / "data")] == argv[argv.index(_real(native / "data")) - 1:][:2]
    assert "--tmpfs" in argv and _real(native / "run") in argv
    # --unshare-pid stays: without it /proc/<keel's pid>/root would show keel's folders (the real test below)
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


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc: Linux")
def test_for_real_keels_folders_cannot_be_reached_through_proc_root_of_keels_processes(native):
    """/proc/<pid>/root of a process outside the sandbox is that process's view of the files: keel's own folders as
    keel sees them. Its own process namespace (--unshare-pid) keeps keel's processes out of the command's /proc."""
    _need_backend()
    keel = subprocess.Popen(["sleep", "60"])
    try:
        token = native / "run" / "token"
        code, out = testcmd.run("/tmp", f"cat /proc/{keel.pid}/root{token}; ls /proc/{keel.pid}/root{native / 'data'}", 60)
        assert "the-internal-token" not in out and "keel.db" not in out
    finally:
        keel.kill()


def test_for_real_another_keel2_keels_folders_are_hidden_too(native, tmp_path):
    _need_backend()
    other = native.parent / "keel-other"
    (other / "run").mkdir(parents=True)
    (other / "run" / "token").write_text("the-other-token")
    (other / "npm" / "bin").mkdir(parents=True)
    (other / "npm" / "bin" / "tool").write_text("a tool\n")
    code, out = testcmd.run(str(tmp_path), f"cat '{other / 'run' / 'token'}'; cat '{other / 'npm' / 'bin' / 'tool'}'", 60)
    assert "the-other-token" not in out and "a tool" in out
