"""0.18.64 security audit F2: keel's push and fetch with the GitHub login never run a program an agent planted in
.git/config (`protocol.ext.allow=always` and `url."ext::sh <script>".insteadOf=https://github.com/`), and never send
the login when the repo turns certificate checks off. The control shows the attack works with a plain git push."""

import os
import subprocess

import pytest

from keel_plugin_git import core

TOKEN = "gho_FAKEtoken0000000000fortests"  # a fake for the tests  keel:allow-secret
ID = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def sh(root, *args, env=None):
    return subprocess.run(["git", *ID, *args], cwd=root, capture_output=True, text=True, env=env)


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "w"
    root.mkdir()
    sh(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a\n")
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "one")
    sh(root, "checkout", "-q", "-b", "feature/x")
    sh(root, "remote", "add", "origin", "https://github.com/acme/shop.git")
    leak = tmp_path / "leak"
    evil = tmp_path / "evil.sh"
    evil.write_text(f'#!/bin/sh\ncat "$KEEL_GIT_TOKEN_FILE" > {leak} 2>/dev/null\nexit 1\n')
    sh(root, "config", "protocol.ext.allow", "always")
    sh(root, "config", f"url.ext::sh {evil} .insteadOf", "https://github.com/")
    return root, leak


def test_the_attack_is_real_with_a_plain_git_push(work, tmp_path):
    root, leak = work
    tok = tmp_path / "t"
    tok.write_text(TOKEN)
    sh(root, "push", "https://github.com/acme/shop.git", "HEAD:refs/heads/feature/x",
       env={**os.environ, "KEEL_GIT_TOKEN_FILE": str(tok)})
    assert leak.is_file() and TOKEN in leak.read_text()


def test_keels_push_with_the_login_never_runs_the_planted_program(work):
    root, leak = work
    with pytest.raises(core.GitError):
        core.push(str(root), TOKEN)
    assert not leak.exists()


def test_keels_fetch_with_the_login_never_runs_the_planted_program(work):
    root, leak = work
    r = core._fetch(str(root), "main", TOKEN)
    assert r.returncode != 0 and not leak.exists() and TOKEN not in (r.stderr + r.stdout)


def test_keel_does_not_push_with_the_login_when_certificate_checks_are_off(work):
    root, leak = work
    sh(root, "config", "--unset", "protocol.ext.allow")
    sh(root, "config", "http.sslVerify", "false")
    with pytest.raises(core.GitError) as e:
        core.push(str(root), TOKEN)
    assert e.value.status == 400 and "sslverify" in str(e.value).lower() and not leak.exists()


def test_a_branch_name_that_is_an_option_is_not_fetched(work):
    root, _leak = work
    r = core._fetch(str(root), "--upload-pack=touch x", TOKEN)
    assert r.returncode != 0 and not (root / "x").exists()
