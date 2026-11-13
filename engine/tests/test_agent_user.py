"""0.21.3 agents run as their own user (tools/agent_user.py): what keel starts as the agents' user, and with what."""

import os
import pwd
import subprocess

from keel_engine.tools import agent_user, testcmd


def _fake_user(monkeypatch, uid=os.getuid() + 1, home="/home/agent"):
    real = pwd.getpwnam

    def getpwnam(name):
        if name == "agent":
            return pwd.struct_passwd(("agent", "x", uid, os.getgid(), "", home, "/bin/sh"))
        return real(name)

    monkeypatch.setattr(agent_user.pwd, "getpwnam", getpwnam)
    monkeypatch.setenv("KEEL_AGENT_USER", "agent")
    monkeypatch.delenv("KEEL_NATIVE", raising=False)


def test_off_without_the_user(monkeypatch):
    monkeypatch.delenv("KEEL_AGENT_USER", raising=False)
    assert not agent_user.enabled()
    assert agent_user.wrap(["claude", "-p"], None) == (["claude", "-p"], None)


def test_off_in_native_mode(monkeypatch):
    _fake_user(monkeypatch)
    monkeypatch.setenv("KEEL_NATIVE", "1")
    assert not agent_user.enabled()


def test_off_when_it_is_keels_own_user(monkeypatch):
    _fake_user(monkeypatch, uid=os.getuid())
    assert not agent_user.enabled()


def test_wrap_starts_the_program_as_the_agent_without_keels_token(monkeypatch):
    _fake_user(monkeypatch)
    me = pwd.getpwuid(os.getuid()).pw_dir
    argv, env = agent_user.wrap(["claude", "-p"], {"PATH": "/usr/bin", "HOME": me, "KEEL_INTERNAL_TOKEN": "t",
                                                   "CLAUDE_CODE_OAUTH_TOKEN": "c"}, nice=10)
    assert argv == ["sudo", "-n", "-E", "-u", "agent", "--", agent_user.HELPER, "claude", "-p"]
    assert "KEEL_INTERNAL_TOKEN" not in env
    assert env["HOME"] == "/home/agent" and env["USER"] == "agent"
    assert env[agent_user.PATH_ENV] == "/usr/bin" and env[agent_user.NICE_ENV] == "10"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "c"         # the CLI's own login is what it runs on


def test_no_env_means_keels_without_its_keys(monkeypatch):
    _fake_user(monkeypatch)
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "t")
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    _argv, env = agent_user.wrap(["sh"], None)
    assert "KEEL_INTERNAL_TOKEN" not in env and "GITHUB_TOKEN" not in env


def test_project_commands_go_through_the_agents_user_and_keels_own_do_not(monkeypatch, tmp_path):
    _fake_user(monkeypatch)
    seen = []
    monkeypatch.setattr(agent_user, "share", lambda *a, **k: None)

    def fake_wrap(argv, env=None, nice=None):
        seen.append(argv)
        return ["/bin/sh", "-c", "echo as-agent"], dict(os.environ)

    monkeypatch.setattr(agent_user, "wrap", fake_wrap)
    assert testcmd.run(str(tmp_path), "echo hi", 10) == (0, "as-agent\n")
    assert seen == [["/bin/sh", "-c", "echo hi"]]
    code, out, _err = testcmd.run_split(str(tmp_path), "echo keel", 10)
    assert (code, out) == (0, "keel\n") and len(seen) == 1
    assert testcmd.run(str(tmp_path), "echo own", 10, as_agent=False) == (0, "own\n") and len(seen) == 1


def test_kill_group_asks_the_agents_user_too(monkeypatch):
    _fake_user(monkeypatch)
    calls = []
    monkeypatch.setattr(agent_user.os, "killpg", lambda pgid, sig: (_ for _ in ()).throw(PermissionError()))
    monkeypatch.setattr(agent_user.subprocess, "run", lambda argv, **kw: calls.append(argv))
    agent_user.kill_group(4242, 9)
    assert calls == [["sudo", "-n", "-u", "agent", "--", agent_user.HELPER, "kill", "-9", "--", "-4242"]]


def test_share_makes_keels_files_group_rw_and_never_follows_links(monkeypatch, tmp_path):
    _fake_user(monkeypatch)
    monkeypatch.setattr(agent_user, "wrap", lambda argv, env=None, nice=None: (["true"], env))
    proj, outside = tmp_path / "p", tmp_path / "outside"
    (proj / "src").mkdir(parents=True)
    outside.write_text("x")
    outside.chmod(0o600)
    f = proj / "src" / "a.py"
    f.write_text("x")
    f.chmod(0o600)
    (proj / "src").chmod(0o700)
    (proj / "link").symlink_to(outside)
    agent_user.share(str(proj))
    assert f.stat().st_mode & 0o060 == 0o060
    assert (proj / "src").stat().st_mode & 0o070 == 0o070
    assert outside.stat().st_mode & 0o777 == 0o600


def test_readable_opens_keels_run_files_for_reading_only(monkeypatch, tmp_path):
    _fake_user(monkeypatch)
    run = tmp_path / "keel-run"
    run.mkdir(mode=0o700)
    g = run / "guard.json"
    g.write_text("{}")
    g.chmod(0o600)
    agent_user.readable(str(run))
    assert run.stat().st_mode & 0o777 == 0o750
    assert g.stat().st_mode & 0o777 == 0o640
    assert agent_user.private_mode() == 0o640


