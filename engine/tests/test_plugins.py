"""v0.10.0 plugins: Git (keel_engine/plugins; the Database plugin's tests moved with it: plugins/db/engine/tests). The
catalog, the person's own calls, the workflow steps, and the rules nobody can change (no force push, nothing to main)."""

import asyncio
import subprocess

import pytest

from conftest import decide, start, wait
from keel_engine import extensions
from keel_engine.plugins.git import core as g
from keel_engine.workflows.model import from_dict
from keel_engine.workflows.validate import validate

IDENT = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(root, *args):
    return subprocess.run(["git", *IDENT, *args], cwd=root, capture_output=True, text=True)


# ------------------------------------------------------------------ the catalog and the commands

def test_the_catalog_lists_both_plugins_with_their_tools_steps_and_settings(client):
    cat = {p["name"]: p for p in client.get("/plugins").json()}
    assert set(cat) == {"git"}             # CI/CD, Code Review and Database come with their plugins (plugins/ci, review, db)
    assert [a["name"] for a in cat["git"]["actions"]][:3] == ["git:branch", "git:sync", "git:push"]
    assert "core" not in cat                                    # keel's own commands are always on, not installed


def test_a_plugins_commands_come_only_when_it_is_on(client, repo):
    names = lambda plugins: {c["name"] for c in client.post("/helper/commands", json={"root": str(repo), "plugins": plugins}).json()}
    assert "commit" not in names([])
    assert {"commit", "pr", "sync", "branch"} <= names(["git"])


# ------------------------------------------------------------------ workflows

def test_validation_knows_the_plugin_steps_and_their_settings():
    def errs(step):
        return validate(from_dict({"name": "x", "keel_rules": False, "steps": [step]}))
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "git:push", "with": {"force": True}}) == \
        ["Step 'a': git:push does not know `with: force`."]
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "commit", "with": {"x": 1}}) == \
        ["Step 'a': only a plugin step (git:...) takes `with`."]


# ------------------------------------------------------------------ git

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


def test_the_mcp_servers_offer_only_read_tools():
    from keel_engine.plugins.server import build

    for name, want in (("git", {"git_status", "git_diff", "git_log", "git_show", "git_blame", "git_branches", "pr_status"}),):
        tools = asyncio.run(build(name).list_tools())
        assert {t.name for t in tools} == want and all(t.annotations.readOnlyHint for t in tools)
    spec = extensions.server_specs(["git", "nope"], "pk_x")
    assert [s["name"] for s in spec] == ["keel-git"] and spec[0]["env"]["KEEL_PLUGIN_KEY"] == "pk_x"


def test_claude_codes_acting_tool_asks_in_the_inbox_and_reads_the_answer_once(client):
    q = client.post("/plugins/ask", json={"project": "demo", "title": "Claude Code: push the branch?", "command": "push the branch"}).json()
    assert q["session"] == "mcp" and q["kind"] == "plugin"
    assert client.get(f"/plugins/ask/{q['id']}").json() == {"id": q["id"], "waiting": True}
    listed = client.get("/helper/permissions?project=demo").json()
    assert [x["id"] for x in listed] == [q["id"]]                   # the Inbox shows it like KeelBot's commands
    assert client.post(f"/helper/permissions/{q['id']}", json={"decision": "always"}).status_code == 200   # once, no grant
    assert client.get(f"/plugins/ask/{q['id']}").json() == {"id": q["id"], "decision": "allow", "why": ""}
    assert client.get(f"/plugins/ask/{q['id']}").status_code == 404     # read once
