"""Native mode: an agent's commands start in an OS sandbox that hides keel's own files (tools/agent_sandbox.py)."""

import os
import shutil
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
    (home / "mode").write_text("native\n")                     # keel2's marker file
    monkeypatch.setenv("KEEL_NATIVE", "1")
    monkeypatch.setenv("KEEL_DATA", str(home / "data"))
    monkeypatch.setenv("KEEL2_HOME", str(home.parent))        # never the real ~/.keel2 of the computer the tests run on
    monkeypatch.delenv("KEEL_HIDE", raising=False)
    monkeypatch.setenv("KEEL_DESKTOP_HOME", str(tmp_path / "no-desktop-app"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-config"))
    for v in ("KEEL_APP", "KEEL2_PATH", "KEEL_DESKTOP"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.delenv("KEEL_SANDBOX", raising=False)
    monkeypatch.delenv("KEEL_AGENT_USER", raising=False)
    agent_sandbox._reset_for_tests()
    yield home
    agent_sandbox._reset_for_tests()


def _real(p):
    return os.path.realpath(str(p))


def _keep_bind(argv, folder):
    """The index of `--bind folder folder` that comes after the empty folders (the project opened again), or -1."""
    last_tmpfs = max((j for j, a in enumerate(argv) if a == "--tmpfs"), default=-1)
    for j in range(last_tmpfs + 1, len(argv) - 2):
        if argv[j:j + 3] == ["--bind", folder, folder]:
            return j
    return -1


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
    (other / "mode").write_text("docker\n")
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


def test_a_symlink_inside_keel2s_folder_decides_nothing_and_a_left_keel_stays_hidden(native, tmp_path):
    """~/.keel2/x/data -> / must not hide everything (or open everything again). A keel keel2 uninstall left (no mode
    file any more) may still have its data: hidden."""
    (native.parent / "x").mkdir()
    os.symlink("/", native.parent / "x" / "data")
    os.symlink(str(tmp_path), native.parent / "y")
    (native.parent / "left" / "data").mkdir(parents=True)
    got = agent_sandbox.hidden()
    assert "/" not in got and _real(tmp_path) not in got
    assert sorted(got) == sorted([_real(native / "data"), _real(native / "run"), _real(native.parent / "left" / "data")])


def test_a_keel2_folder_moved_to_another_disk_and_linked_is_followed(native, monkeypatch, tmp_path):
    link = tmp_path / "linked-keel2"
    os.symlink(str(native.parent), link)
    monkeypatch.setenv("KEEL2_HOME", str(link))
    assert _real(native / "run") in agent_sandbox.hidden()
    assert _real(native.parent) in agent_sandbox.read_only()


def test_keel2s_settings_and_the_desktop_apps_own_files_are_read_only(native, monkeypatch, tmp_path):
    cfg = tmp_path / "config" / "keel2"
    cfg.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    res = tmp_path / "app" / "resources"
    (res / "keel").mkdir(parents=True)
    (res / "runtime" / "bin").mkdir(parents=True)
    monkeypatch.setenv("KEEL_APP", str(res / "keel"))
    monkeypatch.setenv("KEEL_DESKTOP", "1")
    ro = agent_sandbox.read_only()
    assert _real(cfg) in ro and _real(res) in ro
    monkeypatch.delenv("KEEL_DESKTOP")
    assert _real(res / "keel") in agent_sandbox.read_only()          # keel2's release: the release folder itself


def test_the_desktop_apps_folders_are_hidden_and_its_folder_is_read_only(native, monkeypatch, tmp_path):
    app = tmp_path / "desktop"
    for d in ("data", "run", "logs"):
        (app / d).mkdir(parents=True)
    (app / "desktop.json").write_text("{}")
    monkeypatch.setenv("KEEL_DESKTOP_HOME", str(app))
    got = agent_sandbox.hidden()
    assert all(_real(app / d) in got for d in ("data", "run", "logs"))
    assert _real(app) in agent_sandbox.read_only()


def test_keels_programs_are_read_only_and_the_folders_above_keels_cannot_be_renamed(native, monkeypatch, tmp_path):
    """Code in ~/.keel2/<name>/current (keel-start, the engine) runs outside the sandbox at keel's next start; a folder
    above a hidden one, renamed, would show it under another name."""
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    (native / "current").mkdir()
    script = tmp_path / "bin" / "keel2"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    monkeypatch.setenv("KEEL2_PATH", str(script))
    project = tmp_path / "shop"
    project.mkdir()
    argv = agent_sandbox.wrap(["/bin/sh", "-c", "x"], str(project))
    k2 = _real(native.parent)
    assert argv[argv.index(k2) - 1] == "--ro-bind" and _real(script) in argv
    for above in (os.path.dirname(k2), _real(tmp_path)):
        i = argv.index(above)
        assert argv[i - 1:i + 2] == ["--bind", above, above]
    assert k2 + "/keel-x" not in argv                       # inside a read-only folder: nothing to pin
    order = [argv.index(a) for a in ("--ro-bind", "--tmpfs")]
    assert order == sorted(order)
    # a link to the script (~/.local/bin/keel2 -> a checkout): the file it points to is read only
    link = tmp_path / "bin" / "keel2-link"
    os.symlink(str(script), link)
    monkeypatch.setenv("KEEL2_PATH", str(link))
    assert _real(script) in agent_sandbox.read_only()
    # the keel2 script inside the project (a keel checkout) is the project's own file
    monkeypatch.setenv("KEEL2_PATH", str(project / "keel2"))
    (project / "keel2").write_text("#!/bin/sh\n")
    assert _real(project / "keel2") not in agent_sandbox.wrap(["/bin/sh", "-c", "x"], str(project))


def test_a_project_that_holds_keels_folders_does_not_open_them_again(native, monkeypatch, tmp_path):
    """The project is the folder keel2's folder is in (a home folder as the project): opened again, without keel's."""
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    home = native.parent.parent
    argv = agent_sandbox.wrap(["/bin/sh", "-c", "x"], str(home))
    assert _keep_bind(argv, _real(home)) == -1                       # nothing of it to open again
    pairs = [argv[k:k + 2] for k in range(len(argv) - 1)]
    assert ["--tmpfs", _real(native / "data")] in pairs and ["--ro-bind", _real(native.parent)] in pairs


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
    assert _keep_bind(argv, _real(project)) == -1     # the project is not in keel's folders: nothing to open again


def test_a_project_inside_keels_data_folder_stays_open(native, monkeypatch):
    demo = native / "data" / "demo"
    demo.mkdir()
    monkeypatch.setitem(agent_sandbox._found, "backend", "bwrap")
    argv = agent_sandbox.wrap(["/bin/sh", "-c", "pytest"], str(demo))
    assert _keep_bind(argv, _real(demo)) > 0                # after the empty folder
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
    assert _keep_bind(argv, _real(demo)) > 0
    assert argv[argv.index("--chdir") + 1] == _real(wt)
    monkeypatch.setitem(agent_sandbox._found, "backend", "sandbox-exec")
    prof = agent_sandbox.wrap(["/bin/sh", "-c", "git status"], str(wt))[2]
    assert f'(allow file-read* file-write* (subpath "{_real(demo)}"))' in prof


def test_the_macos_profile_denies_keels_folders_after_allowing_the_rest(native, monkeypatch):
    monkeypatch.setitem(agent_sandbox._found, "backend", "sandbox-exec")
    argv = agent_sandbox.wrap(["/bin/sh", "-c", "go test ./..."], "/elsewhere")
    assert argv[:2] == ["/usr/bin/sandbox-exec", "-p"] and argv[3:] == ["/bin/sh", "-c", "go test ./..."]
    prof = argv[2]
    assert prof.startswith("(version 1)\n(allow default)\n(deny file-write* (literal ")
    deny = next(ln for ln in prof.splitlines() if ln.startswith("(deny file-read* file-write* "))
    assert f'(subpath "{_real(native / "data")}")' in deny and f'(subpath "{_real(native / "run")}")' in deny
    assert f'(deny file-write* (subpath "{_real(native.parent)}"))' in prof        # keel2's folder: read only
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
        # CI on Linux with bubblewrap (GitHub's Ubuntu runners have it): the real tests must run, not skip
        ci_bwrap = os.environ.get("CI") == "true" and sys.platform.startswith("linux") and shutil.which("bwrap")
        if os.environ.get("KEEL_TEST_SANDBOX") == "1" or ci_bwrap:
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
    (other / "mode").write_text("native\n")
    (other / "run" / "token").write_text("the-other-token")
    (other / "npm" / "bin").mkdir(parents=True)
    (other / "npm" / "bin" / "tool").write_text("a tool\n")
    code, out = testcmd.run(str(tmp_path), f"cat '{other / 'run' / 'token'}'; cat '{other / 'npm' / 'bin' / 'tool'}'", 60)
    assert "the-other-token" not in out and "a tool" in out


def test_for_real_keels_folders_cannot_be_renamed_into_view_or_keels_programs_changed(native, tmp_path):
    """A rename on the same mount (keel2's folder in the folder above it; a keel inside keel2's folder) is refused; a
    move to another place becomes a copy (mv), which holds nothing of keel's."""
    _need_backend()
    (native / "current").mkdir()
    (native / "current" / "keel-start").write_text("# keel's own\n")
    k2 = native.parent
    code, out = testcmd.run(str(tmp_path), f"mv '{k2}' '{tmp_path / 'k2-moved'}'; mv '{native}' '{k2 / 'renamed'}'; "
                            f"mv '{native}' '{tmp_path / 'copied'}'; "
                            f"echo evil >> '{native / 'current' / 'keel-start'}'; echo done", 60)
    assert "done" in out
    assert native.is_dir() and not (tmp_path / "k2-moved").exists() and not (k2 / "renamed").exists()
    assert (native / "current" / "keel-start").read_text() == "# keel's own\n"
    copied = tmp_path / "copied"
    if copied.exists():
        assert not (copied / "run" / "token").exists() or "the-internal-token" not in (copied / "run" / "token").read_text()
        assert not (copied / "data" / "keel.db").exists()
    code, out = testcmd.run(str(tmp_path), f"cat '{native / 'run' / 'token'}' '{tmp_path / 'copied' / 'run' / 'token'}'", 60)
    assert "the-internal-token" not in out
