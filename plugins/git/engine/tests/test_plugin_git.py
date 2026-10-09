"""v0.10.0 the Git plugin (plugins/git, moved from keel's engine tests test_plugins.py and test_mcp.py with the code):
its catalog entry and commands, the person's own git calls, the read tools an agent call reaches with its key, the
workflow steps and the push setting, and the rules nobody can change (no force push, nothing to main). Its tools in
keel's own MCP server go through keel's api and ask the person in keel's Inbox before they act."""

import asyncio
import json
import subprocess

import httpx
import pytest

from conftest import decide, start, wait
from keel_engine import extensions, mcp_server
from keel_engine.workflows.model import from_dict
from keel_engine.workflows.validate import validate
from keel_plugin_git import mcp_tools as git_mcp

IDENT = ["-c", "user.name=t", "-c", "user.email=t@t"]
READ_TOOLS = {"git_status", "git_diff", "git_log", "git_show", "git_blame", "git_branches", "pr_status"}


def git(root, *args):
    return subprocess.run(["git", *IDENT, *args], cwd=root, capture_output=True, text=True)


# ------------------------------------------------------------------ the catalog and the commands

def test_the_catalog_lists_it_with_its_steps(client):
    cat = {p["name"]: p for p in client.get("/plugins").json()}
    assert cat["git"]["title"] == "Git" and cat["git"]["tools"] == {"server": "keel-git", "read": [
        "git_status", "git_diff", "git_log", "git_show", "git_blame", "git_branches", "pr_status"]}
    assert [a["name"] for a in cat["git"]["actions"]][:3] == ["git:branch", "git:sync", "git:push"]


def test_its_commands_come_only_when_it_is_on(client, repo):
    names = lambda plugins: {c["name"] for c in client.post("/helper/commands", json={"root": str(repo), "plugins": plugins}).json()}
    assert "commit" not in names([])
    assert {"commit", "pr", "sync", "branch"} <= names(["git"])


# ------------------------------------------------------------------ an agent call's read tools

def test_an_agent_call_reads_git_through_its_key_and_only_while_it_is_on(client, repo):
    key = extensions.open_call(project="demo", root=str(repo), keys={}, plugins=["git"], who="keelbot")
    call = lambda tool, **args: client.post("/plugins/call", json={"key": key, "tool": tool, "args": args})
    text = call("git_status").json()["text"]
    assert text.startswith("Branch main; base main: 0 commit(s) ahead, 0 behind.") and "Not pushed yet" in text
    assert call("git_log", range="--all").json()["text"] == "Refused: a range is like main..HEAD."
    off = extensions.open_call(project="demo", root=str(repo), keys={}, plugins=["db"], who="keelbot")
    assert client.post("/plugins/call", json={"key": off, "tool": "git_status", "args": {}}).status_code == 403
    extensions.close_call(key)
    extensions.close_call(off)


# ------------------------------------------------------------------ workflows

def test_validation_knows_its_steps_and_their_settings():
    def errs(step):
        return validate(from_dict({"name": "x", "keel_rules": False, "steps": [step]}))
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "git:push", "with": {"force": True}}) == \
        ["Step 'a': git:push does not know `with: force`."]
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "git:pr", "with": {"title": "x", "draft": True}}) == []
    [only] = errs({"id": "a", "kind": "code", "name": "a", "action": "commit", "with": {"x": 1}})
    assert only.startswith("Step 'a': only a plugin step (") and "git:...)" in only     # (db:..., git:...) as before
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "git:rebase"}) == ["Step 'a': unknown action 'git:rebase'."]


@pytest.fixture
def remote(repo, tmp_path):
    """The demo repo with a bare origin, main pushed, on a feature branch."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "main")
    return repo


def gitcall(client, repo, op, **body):
    return client.post(f"/plugins/git/{op}", json={"root": str(repo), **body})


def test_switch_commit_push_and_the_rules_nobody_changes(client, remote):
    assert gitcall(client, remote, "push").json()["error"] == "keel never pushes to main."
    assert gitcall(client, remote, "switch", branch="feat/ranks", create=True).json() == {"branch": "feat/ranks", "created": True}
    assert gitcall(client, remote, "switch", branch="--force").status_code == 400
    (remote / "src" / "scores" / "rank.py").write_text("RANK = 1\n")
    st = gitcall(client, remote, "status").json()
    assert st["branch"] == "feat/ranks" and st["base"] == "main" and st["changes"] == [{"path": "src/scores/rank.py", "status": "??"}]
    c = gitcall(client, remote, "commit", message="feat(ranks): a rank\n\nFirst one.",
                settings={"commit_author": "Ada Lovelace <ada@example.com>"}).json()
    assert c["subject"] == "feat(ranks): a rank" and c["files"] == ["src/scores/rank.py"]
    log = git(remote, "log", "-1", "--format=%an <%ae>%n%B").stdout
    assert log.startswith("Ada Lovelace <ada@example.com>\nfeat(ranks): a rank\n\nFirst one.") and \
        "Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>" in log
    (remote / "src" / "scores" / "key.py").write_text('KEY = "AKIAIOSFODNN7EXAMPLE"\n')     # keel:allow-secret
    r = gitcall(client, remote, "commit", message="chore: key")
    assert r.status_code == 409 and "secret" in r.json()["error"]
    (remote / "src" / "scores" / "key.py").unlink()
    p = gitcall(client, remote, "push").json()
    assert p["branch"] == "feat/ranks" and gitcall(client, remote, "status").json()["upstream"] == "origin/feat/ranks"
    # someone else pushed to the branch: keel does not force
    other = remote.parent / "other"
    subprocess.run(["git", "clone", "-q", "-b", "feat/ranks", str(remote.parent / "origin.git"), str(other)], check=True)
    (other / "x.txt").write_text("x\n")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "x")
    git(other, "push", "-q")
    (remote / "y.txt").write_text("y\n")
    gitcall(client, remote, "commit", message="chore: y")
    r = gitcall(client, remote, "push")
    assert r.status_code == 409 and "does not force" in r.json()["error"]


def test_sync_undoes_a_conflict_and_cleanup_keeps_unmerged_work(client, remote):
    gitcall(client, remote, "switch", branch="feat/a", create=True)
    (remote / "README.md").write_text("mine\n")
    gitcall(client, remote, "commit", message="docs: mine")
    git(remote, "switch", "-q", "main")
    (remote / "README.md").write_text("theirs\n")
    git(remote, "commit", "-qam", "docs: theirs")
    git(remote, "push", "-q", "origin", "main")
    git(remote, "switch", "-q", "feat/a")
    r = gitcall(client, remote, "sync")
    assert r.status_code == 409 and r.json()["hint"] == "README.md" and "nothing was changed" in r.json()["error"]
    assert not (remote / ".git" / "MERGE_HEAD").exists() and (remote / "README.md").read_text() == "mine\n"
    git(remote, "branch", "done-work", "main")
    git(remote, "switch", "-q", "main")
    out = gitcall(client, remote, "cleanup").json()
    assert out["deleted"] == ["done-work"] and "feat/a" in git(remote, "branch").stdout   # unmerged work stays


def test_git_steps_follow_the_push_setting(client, remote):
    git(remote, "switch", "-q", "-c", "feat/push")
    (remote / "z.txt").write_text("z\n")
    git(remote, "add", "-A")
    git(remote, "commit", "-q", "-m", "z")
    wf = lambda: from_dict({"name": "ship it", "keel_rules": False, "steps": [
        {"id": "push", "kind": "code", "name": "push", "action": "git:push"}, {"id": "end", "kind": "gate", "name": "end"}]})
    settings = lambda push_pr, mode="manual": {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "plugins": ["git"],
                                               "push_pr": push_pr, "run_mode": mode}
    s = wait(client, start(client, remote, workflow=wf(), settings=settings("never")))
    assert s["waiting"]["step"] == "end" and git(remote, "ls-remote", "origin", "feat/push").stdout == ""
    tid = start(client, remote, workflow=wf(), settings=settings("ask"))
    assert wait(client, tid)["waiting"]["title"] == "May keel push the branch?"
    assert decide(client, tid)["waiting"]["step"] == "end"
    assert git(remote, "ls-remote", "origin", "feat/push").stdout.strip()
    tid = start(client, remote, workflow=wf(), settings=settings("auto", "auto"))
    assert wait(client, tid)["status"] == "done"
    note = next(e["data"]["note"] for e in client.bus.of(tid, "step.finished") if e["step"] == "push")
    assert note.startswith("Run mode auto: keel does not push the branch by itself")


def test_a_git_step_fails_when_the_plugin_is_off(client, repo):
    wf = from_dict({"name": "push", "keel_rules": False, "steps": [{"id": "p", "kind": "code", "name": "p", "action": "git:push"}]})
    s = wait(client, start(client, repo, workflow=wf, settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}))
    assert "The Git plugin is off for this project" in json.dumps(s), s


# ------------------------------------------------------------------ its tools in keel's own MCP server (keel2 mcp)

class PluginApi(httpx.MockTransport):
    """The keel api routes the Git plugin's tools use, recording what they are sent."""

    def __init__(self, on=("git",), answer="allow"):
        super().__init__(self.handle)
        self.on, self.answer, self.polls, self.posts = on, answer, 0, []

    def handle(self, req: httpx.Request) -> httpx.Response:
        path, body = req.url.path, (json.loads(req.content) if req.content else {})
        if path == "/api/projects":
            return httpx.Response(200, json=[{"id": "shop", "name": "shop", "root": "/workspace/shop"}])
        if path == "/api/projects/shop/plugins":
            return httpx.Response(200, json=[{"name": n, "enabled": n in self.on} for n in ("db", "git")])
        if path == "/api/projects/shop/plugins/ask":
            self.posts.append((path, body))
            return httpx.Response(200, json={"id": "p_000000000001"})
        if path == "/api/plugins/asks/p_000000000001":
            self.polls += 1
            done = self.polls > 1
            return httpx.Response(200, json={"id": "p_000000000001", "waiting": True} if not done else
                                  {"id": "p_000000000001", "decision": self.answer, "why": "" if self.answer == "allow" else "not now"})
        if path == "/api/projects/shop/git/push":
            self.posts.append((path, body))
            return httpx.Response(200, json={"branch": "feat/scores", "sha": "abc1234def"})
        return httpx.Response(404, json={"error": f"No route {path}"})


def test_its_tools_say_when_the_plugin_is_off(monkeypatch):
    monkeypatch.delenv("KEEL_PROJECT", raising=False)
    api = mcp_server.KeelApi("http://keel.test", transport=PluginApi(on=("db",)))
    with pytest.raises(mcp_server.ApiError, match="The Git plugin is off for shop"):
        git_mcp.git_status(api, "shop")


def test_an_acting_tool_waits_for_the_persons_inbox_answer(monkeypatch):
    monkeypatch.delenv("KEEL_PROJECT", raising=False)
    yes = PluginApi()
    api = mcp_server.KeelApi("http://keel.test", transport=yes)
    assert git_mcp.git_act(api, "push", "shop", sleep=lambda _s: None) == "Pushed feat/scores (abc1234)."
    assert next(b for p, b in yes.posts if p.endswith("/plugins/ask")) == {"title": "Claude Code: push the branch?",
                                                                            "command": "push the branch"}
    no = PluginApi(answer="deny")
    api = mcp_server.KeelApi("http://keel.test", transport=no)
    assert git_mcp.git_act(api, "push", "shop", sleep=lambda _s: None) == "The person said no in keel's Inbox: not now"
    assert not any(p.endswith("/git/push") for p, _ in no.posts)


def test_its_mcp_server_offers_only_read_tools():
    from keel_plugin_git.server import build

    tools = asyncio.run(build().list_tools())
    assert {t.name for t in tools} == READ_TOOLS and all(t.annotations.readOnlyHint for t in tools)
    spec = extensions.server_specs(["git", "nope"], "pk_x")
    assert [s["name"] for s in spec] == ["keel-git"] and spec[0]["env"]["KEEL_PLUGIN_KEY"] == "pk_x"
