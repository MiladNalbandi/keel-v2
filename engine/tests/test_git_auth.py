"""keel's GitHub login for git and gh in keel's container (tools/git_auth.py, tools/git_credential.py) and review-pr's
use of it: the token goes only to the host it belongs to (github.com, or the Enterprise host of Connections › GitHub),
never shows in argv, the environment, .git/config, notes or errors; without a login the start says how to go on; refs
fetched on the host are reviewed when keel cannot fetch."""

import os
import subprocess
from pathlib import Path

import pytest

from conftest import decide, wait
from test_flows_review_pr import TICKET, gh_recorder, on_branch, refused, review
from test_flows_base import answers
from test_v02 import git, graph_values
from keel_engine.runtime import ticket_actions as ta
from keel_engine.tools import git as git_tool
from keel_engine.tools import git_auth, git_credential

TOKEN = "gho_FAKEtoken1234567890abcdef"  # a fake for the tests  keel:allow-secret
VIEW = {"title": "Export orders", "url": "https://github.com/acme/shop/pull/20", "headRefName": "feature/SHOPS-1234-export",
        "baseRefName": "main", "author": {"login": "ana"}, "state": "OPEN", "isDraft": False}


@pytest.fixture(autouse=True)
def no_env_token(monkeypatch):
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def token_file(tmp_path, text=TOKEN) -> str:
    p = tmp_path / "tok"
    p.write_text(text)
    return str(p)


# ------------------------------------------------------------------ the credential helper

def test_the_helper_answers_only_https_requests_for_the_logins_host(tmp_path):
    env = {"KEEL_GIT_HOST": "github.com", "KEEL_GIT_TOKEN_FILE": token_file(tmp_path)}
    yes = git_credential.answer("get", "protocol=https\nhost=github.com\n", env)
    assert yes == f"username=x-access-token\npassword={TOKEN}\n"
    assert git_credential.answer("get", "protocol=https\nhost=GitHub.com\n", env) == yes
    for request in ("protocol=https\nhost=gitlab.com\n", "protocol=https\nhost=github.com.evil.example\n",
                    "protocol=https\nhost=evil.example\npath=github.com\n", "protocol=http\nhost=github.com\n",
                    "host=github.com\n", "protocol=https\n"):
        assert git_credential.answer("get", request, env) == "", request
    assert git_credential.answer("store", "protocol=https\nhost=github.com\n", env) == ""
    assert git_credential.answer("erase", "protocol=https\nhost=github.com\n", env) == ""
    assert git_credential.answer("get", "protocol=https\nhost=github.com\n", {**env, "KEEL_GIT_HOST": ""}) == ""
    assert git_credential.answer("get", "protocol=https\nhost=github.com\n", {**env, "KEEL_GIT_TOKEN_FILE": str(tmp_path / "gone")}) == ""


def test_the_helper_answers_the_enterprise_host_of_connections(tmp_path):
    env = {"KEEL_GIT_HOST": "ghe.acme.test", "KEEL_GIT_TOKEN_FILE": token_file(tmp_path)}
    assert TOKEN in git_credential.answer("get", "protocol=https\nhost=ghe.acme.test\n", env)
    assert TOKEN in git_credential.answer("get", "protocol=https\nhost=ghe.acme.test:8443\n", env)
    assert git_credential.answer("get", "protocol=https\nhost=github.com\n", env) == ""


def test_the_login_carries_its_host_and_never_shows_the_token():
    tok = git_auth.login({"github": TOKEN, "github_host": "https://GHE.acme.test/"})
    assert tok == TOKEN and tok.host == "ghe.acme.test"
    assert TOKEN not in repr(tok) and TOKEN not in repr([tok])
    assert git_auth.login({"github": TOKEN}).host == "github.com"
    assert git_auth.login({"copilot": TOKEN}).host == "github.com"
    assert git_auth.login({}) is None
    assert git_auth.mask(f"fatal: {TOKEN} https://x:{TOKEN}@github.com/a/b", tok) == "fatal: *** https://***@github.com/a/b"


@pytest.mark.parametrize("url,host,want", [
    ("git@github.com:acme/shop.git", "github.com", "https://github.com/acme/shop.git"),
    ("ssh://git@github.com:22/acme/shop.git", "github.com", "https://github.com/acme/shop.git"),
    ("https://github.com/acme/shop", "github.com", "https://github.com/acme/shop.git"),
    ("https://GHE.acme.test:8443/acme/shop.git", "ghe.acme.test", "https://ghe.acme.test:8443/acme/shop.git"),
    ("git@github.com:acme/shop.git", "ghe.acme.test", None),
    ("https://github.com.evil.example/acme/shop.git", "github.com", None),
    ("https://evil.example/github.com/shop.git", "github.com", None),
    ("/srv/git/shop.git", "github.com", None),
    ("file:///srv/git/shop.git", "github.com", None),
])
def test_only_a_remote_on_the_logins_host_is_fetched_with_it(url, host, want):
    assert git_auth.https_url(url, host) == want


# ------------------------------------------------------------------ git, end to end

def test_git_itself_gets_the_token_for_github_only_and_nothing_is_left_behind(repo):
    git(repo, "remote", "add", "origin", "git@github.com:acme/shop.git")
    with git_auth.remote(str(repo), git_auth.login({"github": TOKEN})) as r:
        assert r.target == "https://github.com/acme/shop.git"
        assert not any(TOKEN in a for a in r.args) and not any(TOKEN in str(v) for v in r.env.values())
        path = r.env["KEEL_GIT_TOKEN_FILE"]
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"

        def fill(host):
            return subprocess.run(["git", *r.args, "credential", "fill"], input=f"protocol=https\nhost={host}\n\n",
                                  env={**r.env, "GIT_ASKPASS": "", "SSH_ASKPASS": ""}, cwd=repo, capture_output=True,
                                  text=True, timeout=30)
        assert f"password={TOKEN}" in fill("github.com").stdout
        other = fill("evil.example")
        assert other.returncode != 0 and TOKEN not in other.stdout + other.stderr
    assert not os.path.exists(path)
    assert TOKEN not in (Path(repo) / ".git" / "config").read_text()
    assert git(repo, "config", "--get", "remote.origin.url").stdout.strip() == "git@github.com:acme/shop.git"


def test_another_host_or_a_local_remote_gets_no_token(repo, tmp_path):
    git(repo, "remote", "add", "origin", "https://gitlab.example/acme/shop.git")
    with git_auth.remote(str(repo), git_auth.login({"github": TOKEN})) as r:
        assert (r.target, r.args, r.login) == ("origin", [], None) and "KEEL_GIT_TOKEN_FILE" not in r.env
    git(repo, "remote", "set-url", "origin", str(tmp_path / "origin.git"))
    with git_auth.remote(str(repo), git_auth.login({"github": TOKEN})) as r:
        assert r.target == "origin" and r.login is None


def test_gh_gets_the_login_for_its_own_host_only(repo):
    assert git_auth.gh_env(str(repo), git_auth.login({"github": TOKEN}))["GH_TOKEN"] == TOKEN
    ghe = git_auth.login({"github": TOKEN, "github_host": "ghe.acme.test"})
    git(repo, "remote", "add", "origin", "git@github.com:acme/shop.git")
    env = git_auth.gh_env(str(repo), ghe)
    assert TOKEN not in env.values()                          # origin is not on the Enterprise host
    git(repo, "remote", "set-url", "origin", "https://ghe.acme.test/acme/shop.git")
    env = git_auth.gh_env(str(repo), ghe)
    assert env["GH_HOST"] == "ghe.acme.test" and env["GH_ENTERPRISE_TOKEN"] == TOKEN and "GH_TOKEN" not in env
    assert "GH_TOKEN" not in git_auth.gh_env(str(repo), None)


# ------------------------------------------------------------------ review-pr

def fake_fetch(monkeypatch, rc=128, said=None):
    """git fetch records its argv, env and what the token file held, then answers rc (no network); other git runs."""
    calls = []
    real = git_tool.git

    def fake(root, *args, **kw):
        if "fetch" in args:
            env = kw.get("env") or {}
            f = env.get("KEEL_GIT_TOKEN_FILE")
            calls.append({"args": list(args), "env": dict(env), "file": Path(f).read_text() if f else None})
            return subprocess.CompletedProcess(["git", *args], rc, "", said if said is not None else
                                               f"fatal: Authentication failed for 'https://x-access-token:{TOKEN}@github.com/'")
        return real(root, *args, **kw)
    monkeypatch.setattr(git_tool, "git", fake)
    return calls


def test_review_pr_fetches_with_the_helper_and_masks_the_token(client, repo, monkeypatch):
    git(repo, "remote", "add", "origin", "git@github.com:acme/shop.git")
    calls = fake_fetch(monkeypatch)
    gh_recorder(monkeypatch, VIEW)
    code, err = refused(client, repo, "#20\n" + TICKET, keys={"github": TOKEN})
    assert code == 409 and err.startswith("Could not fetch PR #20 from origin") and TOKEN not in err and "***" in err
    assert "git fetch origin pull/20/head:refs/keel/pr-20" in err
    c = calls[0]
    helper = next(a for a in c["args"] if a.startswith("credential.helper=!"))
    assert "git_credential.py" in helper and c["args"][c["args"].index(helper) - 2] == "credential.helper="
    assert "https://github.com/acme/shop.git" in c["args"] and "origin" not in c["args"]
    assert c["env"]["KEEL_GIT_HOST"] == "github.com" and c["file"] == TOKEN
    assert not any(TOKEN in a for a in c["args"]) and not any(TOKEN in str(v) for v in c["env"].values())
    assert TOKEN not in (Path(repo) / ".git" / "config").read_text()


def test_review_pr_without_a_login_says_to_connect_github_or_fetch_on_the_host(client, repo, monkeypatch):
    git(repo, "remote", "add", "origin", "git@github.com:acme/shop.git")
    calls = fake_fetch(monkeypatch, said="git@github.com: Permission denied (publickey).")
    gh_recorder(monkeypatch)                                   # gh is not logged in either
    code, err = refused(client, repo, "https://github.com/acme/shop/pull/20\n" + TICKET)
    assert code == 409
    assert "GitHub is not connected in keel (Connections › GitHub) — or fetch the branch on the host first" in err
    assert "`git fetch origin pull/20/head:refs/keel/pr-20`" in err
    assert calls and calls[0]["args"][-3:] == ["--end-of-options", "origin", "+refs/pull/20/head:refs/keel/review-pr/20"]
    assert not any(a.startswith("credential.helper=!") for a in calls[0]["args"])


def test_review_pr_reviews_refs_fetched_on_the_host_when_keel_cannot_fetch(client, repo, monkeypatch):
    git(repo, "remote", "add", "origin", "git@github.com:acme/shop.git")
    on_branch(repo, "feature/SHOPS-1234-export")
    git(repo, "update-ref", "refs/keel/pr-20", "feature/SHOPS-1234-export")      # the host's git fetch
    git(repo, "branch", "-q", "-D", "feature/SHOPS-1234-export")
    fake_fetch(monkeypatch)
    gh_recorder(monkeypatch)                                   # gh cannot read it: the base is main
    answers(monkeypatch, {})
    tid = review(client, repo, request="#20\n" + TICKET)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "share", s
    src = graph_values(client, tid)["data"]["ticket_review"]["source"]
    assert "reviews keel/pr-20" in src["fallback"] and "fetched on the host" in src["fallback"]
    assert "its state was not checked" in src["fallback"] and src["base"] == "main"
    assert git(repo, "rev-parse", "refs/keel/review-pr/20").stdout == git(repo, "rev-parse", "refs/keel/pr-20").stdout
    assert decide(client, tid, payload={"choice": "done"})["status"] == "done"


def test_the_start_names_what_it_reviews(client, repo):
    on_branch(repo)
    r = client.post("/threads", json={"project_id": "demo", "root": str(repo), "workflow": ta_template(), "title": "Review",
                                      "models": {"default": {"provider": "fake", "mode": "api", "model": "fake"}},
                                      "request": "branch: feat/ranks\n" + TICKET})
    assert r.status_code == 200 and r.json()["source"] == "branch:feat/ranks"


def ta_template():
    from keel_engine.workflows.templates import get_template

    return get_template("review-pr").model_dump()


def test_the_prep_error_text_names_the_way_out_without_a_login(repo, monkeypatch):
    git(repo, "remote", "add", "origin", "git@github.com:acme/shop.git")
    fake_fetch(monkeypatch, said="fatal: could not read from remote repository")
    gh_recorder(monkeypatch)
    from types import SimpleNamespace

    src, err = ta._source_pr(SimpleNamespace(root=str(repo), keys={}, data={}, request=""), 20, False, {}, None)
    assert src is None and err.startswith("could not fetch PR #20: " + git_auth.NOT_CONNECTED)


def test_keels_mcp_run_list_marks_a_run_another_one_replaced(monkeypatch):
    from keel_engine import mcp_flows, mcp_more

    rows = [{"thread_id": "t2", "workflow_id": "review-pr", "title": "Review", "status": "waiting", "source": "pr:20"},
            {"thread_id": "t1", "workflow_id": "review-pr", "title": "Review", "status": "stopped", "source": "pr:20",
             "replaced_by": "t2", "error": "Could not fetch PR #20"}]

    class Api:
        def get(self, path, **kw):
            return rows
    monkeypatch.setattr(mcp_more, "_project", lambda api, p: {"id": "shop"})
    monkeypatch.setattr(mcp_flows, "startable", lambda api, pid: "")
    out = mcp_more.flows(Api(), "shop").splitlines()
    assert "replaced" not in out[0] and "replaced by t2 (same pr:20, started again)" in out[1]


# ------------------------------------------------------------------ gh's answers

def test_gh_list_answers_two_pages_slurped_pages_and_nothing_are_lists():
    assert git_auth.json_pages('[{"a": 1}]\n[{"a": 2}, {"a": 3}]') == [{"a": 1}, {"a": 2}, {"a": 3}]
    assert git_auth.json_pages('[[{"a": 1}], [{"a": 2}]]') == [{"a": 1}, {"a": 2}]
    assert git_auth.json_pages("") == [] and git_auth.json_pages("  \n") == [] and git_auth.json_pages("[]") == []
    with pytest.raises(ValueError) as e:
        git_auth.json_pages("A new release of gh is available")
    assert "A new release" in str(e.value) and "character 0" not in str(e.value)
    with pytest.raises(ValueError):
        git_auth.json_pages('{"message": "Not Found"}')


def test_a_failed_gh_says_what_gh_said_masked_and_the_way_out():
    login = git_auth.login({"github": TOKEN})
    said = git_auth.gh_message(4, "", "A new release of gh is available: 2.40 → 2.60\nTo get started with GitHub CLI, "
                               f"please run:  gh auth login\n{TOKEN}\n", login)
    assert said.startswith("To get started with GitHub CLI") and TOKEN not in said
    assert git_auth.gh_fix(said, None) == "GitHub is not connected in keel — Connections › GitHub"
    assert "sign in again" in git_auth.gh_fix("HTTP 401: Bad credentials", login)
    assert git_auth.gh_fix("HTTP 404: Not Found", login) == "gh: HTTP 404: Not Found"
    assert git_auth.gh_message(1, "", "", login) == "gh ended with exit code 1 and said nothing"


def test_run_split_keeps_a_long_answer_whole_and_stderr_apart(tmp_path):
    from keel_engine.tools import testcmd

    code, out, err = testcmd.run_split(str(tmp_path), "python3 -c \"import sys; print('[' + ','.join(['1'] * 30000) + ']'); "
                                       "print('notice', file=sys.stderr)\"")
    assert code == 0 and len(git_auth.json_pages(out)) == 30000 and err.strip() == "notice"


def test_review_prs_gh_calls_get_the_message_not_a_parse_error(repo, monkeypatch):
    from keel_engine.tools import testcmd

    monkeypatch.setattr(testcmd, "run_split", lambda root, cmd, timeout=60, env=None:
                        (4, "", f"HTTP 401: Bad credentials {TOKEN}\n"))
    code, out = ta._gh(str(repo), "pr view 20 --json title", {"GH_TOKEN": TOKEN})
    assert code == 4 and out == "HTTP 401: Bad credentials ***"
