"""0.18.64 security audit F2, F3, F14c: the git keel runs itself (a fetch with the GitHub login, a commit, a status) does
not run a program an agent planted in the repo's .git/config, and never with keel's keys in its environment.

Each attack is first shown to work with a plain git call (the control), then keel's own call is shown to refuse it or
leave it harmless. Real repositories in temp folders; the token is a fake."""

import os
import subprocess
from pathlib import Path

import pytest

from keel_engine.runtime import branch_sync
from keel_engine.tools import git, git_auth

TOKEN = "gho_FAKEtoken0000000000fortests"  # a fake for the tests  keel:allow-secret
ORIGIN = "https://github.com/acme/shop.git"
ID = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def sh(root, *args, env=None):
    return subprocess.run(["git", *ID, *args], cwd=root, capture_output=True, text=True, env=env)


@pytest.fixture(autouse=True)
def quiet_git(monkeypatch, tmp_path):
    # the person's own ~/.gitconfig stays out of it
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a\n")
    sh(r, "add", "-A")
    sh(r, "commit", "-q", "-m", "one")
    sh(r, "remote", "add", "origin", ORIGIN)
    return r


def plant_ext(root: Path, tmp_path: Path) -> Path:
    """What an agent writes into .git/config: github.com becomes a program that copies the token file it can see."""
    leak = tmp_path / "leak"
    evil = tmp_path / "evil.sh"
    evil.write_text(f'#!/bin/sh\ncat "$KEEL_GIT_TOKEN_FILE" > {leak} 2>/dev/null; echo "$KEEL_INTERNAL_TOKEN" >> {leak}\n'
                    "exit 1\n")
    sh(root, "config", "protocol.ext.allow", "always")
    sh(root, "config", f"url.ext::sh {evil} .insteadOf", "https://github.com/")
    return leak


# ------------------------------------------------------------------ F2: ext:: insteadOf steals the login

def test_the_ext_attack_is_real_with_a_plain_git_fetch(repo, tmp_path):
    leak = plant_ext(repo, tmp_path)
    tok = tmp_path / "t"
    tok.write_text(TOKEN)
    sh(repo, "fetch", "-q", ORIGIN, env={**os.environ, "KEEL_GIT_TOKEN_FILE": str(tok)})
    assert leak.is_file() and TOKEN in leak.read_text()          # the control: plain git ran the planted program


def test_keels_fetch_with_the_login_never_runs_an_ext_remote(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "keel-internal-fake")
    leak = plant_ext(repo, tmp_path)
    got = branch_sync.fetch_one(str(repo), "origin", {"github": TOKEN}, timeout=20)
    assert got["ok"] is False
    assert not leak.exists()
    assert TOKEN not in str(got)


def test_keels_own_git_never_runs_ext_even_without_the_login(repo, tmp_path):
    leak = plant_ext(repo, tmp_path)
    r = git.git(str(repo), "ls-remote", ORIGIN, timeout=20)
    assert r.returncode != 0 and not leak.exists()
    r = git.git(str(repo), "fetch", "-q", "ext::sh -c true", timeout=20)
    assert r.returncode != 0 and not leak.exists()


def test_a_network_call_may_use_https_ssh_and_a_local_path_only(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text('#!/bin/sh\necho "$GIT_ALLOW_PROTOCOL|$*"\n')
    (bin_dir / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    out = git.git(str(tmp_path), "fetch", "origin").stdout
    assert out.startswith("https:ssh:file|") and "protocol.ext.allow=never" in out
    assert "core.hooksPath=/dev/null" in out and "core.fsmonitor=false" in out
    assert git.git(str(tmp_path), "status").stdout.startswith("|")     # a local command needs no protocol list


def test_with_the_login_git_may_speak_https_only_and_runs_no_askpass(repo):
    with git_auth.remote(str(repo), {"github": TOKEN}) as r:
        assert r.target == ORIGIN and r.login
        assert r.env["GIT_ALLOW_PROTOCOL"] == "https" and r.env["GIT_ASKPASS"] == ""
        assert "SSH_ASKPASS" not in r.env


def test_the_login_is_not_sent_when_the_repo_turns_certificate_checks_off(repo):
    sh(repo, "config", "http.https://github.com/.sslVerify", "false")
    with pytest.raises(git_auth.UnsafeRemote, match="sslverify"):
        with git_auth.remote(str(repo), {"github": TOKEN}):
            pass
    got = branch_sync.fetch_one(str(repo), "origin", {"github": TOKEN}, timeout=20)
    assert got["ok"] is False and "sslverify" in got["error"].lower()


# ------------------------------------------------------------------ F3: a planted hook or fsmonitor sees keel's keys

def plant_hook(root: Path, tmp_path: Path) -> Path:
    seen = tmp_path / "seen"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in ("pre-commit", "post-commit", "pre-push", "post-checkout"):
        (hooks / name).write_text(f'#!/bin/sh\necho "{name} $KEEL_INTERNAL_TOKEN" >> {seen}\nexit 0\n')
        (hooks / name).chmod(0o755)
    sh(root, "config", "core.hooksPath", str(hooks))
    return seen


def test_the_planted_hook_attack_is_real_with_a_plain_git_commit(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "keel-internal-fake")
    seen = plant_hook(repo, tmp_path)
    (repo / "b.txt").write_text("b\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "two")
    assert "keel-internal-fake" in seen.read_text()              # the control


def test_keels_commit_runs_no_planted_hook_and_hands_no_key_on(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "keel-internal-fake")
    seen = plant_hook(repo, tmp_path)
    (repo / "b.txt").write_text("b\n")
    git.git(str(repo), "add", "-A")
    r = git.git(str(repo), *ID, "commit", "-q", "-m", "two")
    assert r.returncode == 0 and git.git(str(repo), "log", "-1", "--format=%s").stdout.strip() == "two"
    git.git(str(repo), "checkout", "-q", "-b", "other")
    assert not seen.exists()


def test_with_git_hooks_on_the_projects_hook_runs_on_a_local_commit_without_keys_and_never_on_a_push(repo, tmp_path,
                                                                                                    monkeypatch):
    # 0.18.64 the person's choice (.keel/config.yml git.hooks: on): their own hooks run on keel's local commits
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "keel-internal-fake")
    seen = plant_hook(repo, tmp_path)
    (repo / ".keel").mkdir()
    (repo / ".keel" / "config.yml").write_text("git:\n  hooks: on\n")
    (repo / "b.txt").write_text("b\n")
    git.git(str(repo), "add", "-A")
    assert git.git(str(repo), *ID, "commit", "-q", "-m", "two").returncode == 0
    assert seen.exists() and "keel-internal-fake" not in seen.read_text()        # it ran, and saw no key
    assert git.SAFE_ARGS_HOOKS == ("-c", "protocol.ext.allow=never", "-c", "core.fsmonitor=false")
    assert "push" not in git.LOCAL_HOOK_VERBS and "fetch" not in git.LOCAL_HOOK_VERBS


def test_keels_status_runs_no_planted_fsmonitor(repo, tmp_path):
    seen = tmp_path / "fsmon"
    prog = tmp_path / "fsmon.sh"
    prog.write_text(f"#!/bin/sh\necho ran >> {seen}\nexit 1\n")
    prog.chmod(0o755)
    sh(repo, "config", "core.fsmonitor", str(prog))
    sh(repo, "status")
    assert seen.is_file()                                        # the control: plain git status ran it
    seen.unlink()
    git.dirty(str(repo))
    git.git(str(repo), "status")
    assert not seen.exists()


def test_the_git_that_gh_runs_with_the_login_runs_no_planted_program(repo, tmp_path):
    seen = tmp_path / "fsmon"
    prog = tmp_path / "fsmon.sh"
    prog.write_text(f'#!/bin/sh\necho "$GH_TOKEN" >> {seen}\nexit 1\n')
    prog.chmod(0o755)
    sh(repo, "config", "core.fsmonitor", str(prog))
    env = git_auth.gh_env(str(repo), {"github": TOKEN})
    assert env["GH_TOKEN"] == TOKEN
    subprocess.run(["git", "status"], cwd=repo, env=env, capture_output=True)      # what gh pr create does
    assert not seen.exists()


def test_keels_git_gets_no_keys_in_its_environment(tmp_path, monkeypatch):
    for k, v in {"KEEL_INTERNAL_TOKEN": "x1", "KEEL_WEB_KEY": "x2", "ANTHROPIC_API_KEY": "x3", "GH_TOKEN": "x4",
                 "DB_PASSWORD": "x5", "AWS_SECRET_ACCESS_KEY": "x6", "GIT_CREDENTIALS": "x7"}.items():
        monkeypatch.setenv(k, v)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text("#!/bin/sh\nenv\n")
    (bin_dir / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    for env in (None, dict(os.environ), {**os.environ, "KEEL_GIT_TOKEN_FILE": "/t", "KEEL_GIT_HOST": "github.com"}):
        out = git.git(str(tmp_path), "status", env=env).stdout
        assert not any(f"={v}\n" in out for v in ("x1", "x2", "x3", "x4", "x5", "x6", "x7"))
        assert "PATH=" in out
    assert "KEEL_GIT_TOKEN_FILE=/t" in out and "KEEL_GIT_HOST=github.com" in out   # keel's own helper still works


# ------------------------------------------------------------------ F14c: a remote or branch name that is an option

def test_a_remote_named_like_an_option_is_never_fetched(repo, tmp_path):
    seen = tmp_path / "ran"
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    with (repo / ".git" / "config").open("a") as f:
        f.write(f'[remote "--upload-pack=touch {seen};"]\n\turl = {bare}\n')
    names = git.remotes(str(repo))
    assert any(n.startswith("--upload-pack") for n in names)
    got = branch_sync.fetch_all(str(repo), timeout=20)
    bad = next(x for x in got if x["remote"].startswith("--"))
    assert bad["ok"] is False and not seen.exists()


def test_sync_refuses_a_base_that_is_an_option(repo):
    sh(repo, "checkout", "-q", "-b", "feature")
    with pytest.raises(branch_sync.SyncError) as e:
        branch_sync.sync(str(repo), onto="--exec=touch x")
    assert e.value.status == 400 and not (repo / "x").exists()


def test_plain_names():
    assert git.plain_name("origin") and git.plain_name("upstream/main") and git.plain_name("feature/a-1")
    for bad in ("", "-x", "--upload-pack=x", "a b", "a\nb"):
        assert not git.plain_name(bad)
