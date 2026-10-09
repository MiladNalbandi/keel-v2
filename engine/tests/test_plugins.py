"""v0.10.0 plugins: what core keeps. The Database and Git plugins' tests moved with them (plugins/db/engine/tests,
plugins/git/engine/tests): without their packages keel has no plugin of its own, knows none of their commands or steps,
and still asks the person in the Inbox for Claude Code's acting tools."""

from keel_engine.workflows.model import from_dict
from keel_engine.workflows.validate import validate


# ------------------------------------------------------------------ the catalog and the commands

def test_the_catalog_lists_no_plugin_without_its_package(client):
    cat = {p["name"]: p for p in client.get("/plugins").json()}
    assert set(cat) == set()        # CI/CD, Code Review, Database and Git come with their plugins (plugins/ci, review, db, git)
    assert "core" not in cat                                    # keel's own commands are always on, not installed


def test_a_plugins_commands_come_only_when_it_is_on(client, repo):
    names = lambda plugins: {c["name"] for c in client.post("/helper/commands", json={"root": str(repo), "plugins": plugins}).json()}
    assert names(["db", "git"]) == names([])                    # their plugins are not loaded here
    assert not {"sql", "commit", "pr", "sync", "branch"} & names(["db", "git"])


# ------------------------------------------------------------------ workflows

def test_validation_knows_no_plugin_step_without_its_plugin():
    def errs(step):
        return validate(from_dict({"name": "x", "keel_rules": False, "steps": [step]}))
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "db:check"}) == ["Step 'a': unknown action 'db:check'."]
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "git:push"}) == ["Step 'a': unknown action 'git:push'."]


def test_claude_codes_acting_tool_asks_in_the_inbox_and_reads_the_answer_once(client):
    q = client.post("/plugins/ask", json={"project": "demo", "title": "Claude Code: push the branch?", "command": "push the branch"}).json()
    assert q["session"] == "mcp" and q["kind"] == "plugin"
    assert client.get(f"/plugins/ask/{q['id']}").json() == {"id": q["id"], "waiting": True}
    listed = client.get("/helper/permissions?project=demo").json()
    assert [x["id"] for x in listed] == [q["id"]]                   # the Inbox shows it like KeelBot's commands
    assert client.post(f"/helper/permissions/{q['id']}", json={"decision": "always"}).status_code == 200   # once, no grant
    assert client.get(f"/plugins/ask/{q['id']}").json() == {"id": q["id"], "decision": "allow", "why": ""}
    assert client.get(f"/plugins/ask/{q['id']}").status_code == 404     # read once
