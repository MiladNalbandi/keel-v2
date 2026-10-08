"""v0.10.0 plugins: Database and Git (keel_engine/plugins). The catalog, the person's own calls, the read tools an
agent call reaches with its key, the workflow steps, and the rules nobody can change (read only on staging and prod,
no force push, nothing to main)."""

import asyncio
import json
import sqlite3
import subprocess

import pytest
import yaml

from conftest import decide, start, wait
from keel_engine import extensions
from keel_engine.plugins.db import core as db
from keel_engine.plugins.git import core as g
from keel_engine.workflows.model import from_dict
from keel_engine.workflows.validate import validate

IDENT = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(root, *args):
    return subprocess.run(["git", *IDENT, *args], cwd=root, capture_output=True, text=True)


@pytest.fixture
def scores(repo):
    """The demo repo with a SQLite database: 3 players, 1 score, and a password column."""
    c = sqlite3.connect(repo / "app.db")
    c.executescript("""
        create table players (id integer primary key, name text, password_hash text);
        create table scores (id integer primary key, player_id integer references players(id), value integer);
        insert into players (name, password_hash) values ('Ada', 'h1'), ('Bo', 'h2'), ('Chen', 'h3');
        insert into scores (player_id, value) values (1, 10);""")
    c.commit()
    c.close()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: the database")
    return repo


def conn(env="local", name="local"):
    return {"name": name, "kind": "sqlite", "url": "sqlite:app.db", "env": env}


def keys(*conns, github=None):
    out = {f"db:{c['name']}": json.dumps(c) for c in conns}
    if github:
        out["github"] = github
    return out


# ------------------------------------------------------------------ the catalog and the commands

def test_the_catalog_lists_both_plugins_with_their_tools_steps_and_settings(client):
    cat = {p["name"]: p for p in client.get("/plugins").json()}
    assert set(cat) == {"db", "git", "review"}                  # CI/CD comes with its plugin (plugins/ci)
    assert cat["db"]["title"] == "Database" and cat["db"]["tools"] == {"server": "keel-db", "read": ["db_connections", "db_schema", "db_query"]}
    check = next(a for a in cat["db"]["actions"] if a["name"] == "db:check")
    assert check["with"] == {"sql": "required", "expect": "optional", "connection": "optional"} and "data check" in check["summary"]
    assert [a["name"] for a in cat["git"]["actions"]][:3] == ["git:branch", "git:sync", "git:push"]
    assert "core" not in cat                                    # keel's own commands are always on, not installed


def test_a_plugins_commands_come_only_when_it_is_on(client, repo):
    names = lambda plugins: {c["name"] for c in client.post("/helper/commands", json={"root": str(repo), "plugins": plugins}).json()}
    assert "sql" not in names([]) and "commit" not in names([])
    assert {"sql", "explain"} <= names(["db"]) and {"commit", "pr", "sync", "branch"} <= names(["git"])


# ------------------------------------------------------------------ the person's database calls

def test_the_person_reads_counts_a_change_and_runs_it_only_on_local_or_test(client, scores):
    root = str(scores)
    t = client.post("/plugins/db/test", json={"root": root, "connection": conn()}).json()
    assert t == {"ok": True, "server": t["server"], "tables": 2} and t["server"].startswith("SQLite")
    sch = client.post("/plugins/db/schema", json={"root": root, "connection": conn()}).json()
    assert [x["name"] for x in sch["tables"]] == ["players", "scores"]
    assert sch["tables"][1]["fks"] == [{"column": "player_id", "table": "players", "ref": "id"}]

    q = "SELECT p.name, p.password_hash FROM players p LEFT JOIN scores s ON s.player_id = p.id WHERE s.id IS NULL"
    r = client.post("/plugins/db/query", json={"root": root, "connection": conn(), "sql": q}).json()
    assert r["kind"] == "read" and r["rows"] == [["Bo", "h2"], ["Chen", "h3"]]      # the person sees the real values
    change = "INSERT INTO scores (player_id, value) SELECT p.id, 0 FROM players p LEFT JOIN scores s ON s.player_id = p.id WHERE s.id IS NULL"
    refused = client.post("/plugins/db/query", json={"root": root, "connection": conn(), "sql": change})
    assert refused.status_code == 403 and "only reads" in refused.json()["error"]
    dry = client.post("/plugins/db/query", json={"root": root, "connection": conn(), "sql": change, "change": True}).json()
    assert dry["changed"] == 2 and dry["done"] is False
    count = lambda: client.post("/plugins/db/query", json={"root": root, "connection": conn(), "sql": "select count(*) from scores"}).json()["rows"]
    assert count() == [[1]]                                     # the dry run rolled back
    done = client.post("/plugins/db/query", json={"root": root, "connection": conn(), "sql": change, "change": True, "confirm": True}).json()
    assert done["changed"] == 2 and done["done"] is True and count() == [[3]]
    prod = client.post("/plugins/db/query", json={"root": root, "connection": conn("prod", "prod"), "sql": change, "change": True})
    assert prod.status_code == 403 and "keel only reads there" in prod.json()["error"]
    for sql, why in (("DROP TABLE scores", "migrations"), ("DROP DATABASE x", "never drops"), ("TRUNCATE scores", "TRUNCATE"),
                     ("select 1; delete from scores", "One statement")):
        r = client.post("/plugins/db/query", json={"root": root, "connection": conn(), "sql": sql, "change": True})
        assert r.status_code == 400 and why in r.json()["error"], (sql, r.json())
    bad = client.post("/plugins/db/test", json={"root": root, "connection": {"name": "x", "url": "oracle://a/b"}})
    assert bad.status_code == 400 and "postgres://" in bad.json()["hint"]


def test_keel_suggests_the_databases_the_project_names_never_from_env(client, scores):
    (scores / "docker-compose.yml").write_text(yaml.safe_dump({"services": {
        "db": {"image": "postgres:16", "environment": {"POSTGRES_USER": "app", "POSTGRES_PASSWORD": "app", "POSTGRES_DB": "scores"},
               "ports": ["15432:5432"]},
        "cache": {"image": "redis:7", "ports": ["6379:6379"]},
        "mysql": {"image": "mysql:8", "environment": ["MYSQL_DATABASE=shop", "MYSQL_ROOT_PASSWORD=${ROOT_PW:-root}"],
                  "ports": ["3307:3306"]}}}))
    (scores / ".env").write_text("DATABASE_URL=postgres://real:SECRET@prod.example.com:5432/live\n")   # keel:allow-secret
    out = client.post("/plugins/db/suggest", json={"root": str(scores)}).json()
    by = {s["url"]: s for s in out}
    pg = "postgres://app:app@localhost:15432/scores"  # keel:allow-secret
    assert pg in by and by[pg]["source"] == "docker-compose.yml (service db)"
    assert "mysql://root:root@localhost:3307/shop" in by         # keel:allow-secret
    assert "sqlite:app.db" in by
    (scores / ".codegraph").mkdir()
    (scores / ".codegraph" / "codegraph.db").write_text("")      # keel's own code graph index is not the project's database
    assert all(".codegraph" not in s["url"] for s in client.post("/plugins/db/suggest", json={"root": str(scores)}).json())
    assert all("SECRET" not in s["url"] for s in out)           # .env is the person's: keel never reads it
    assert by[pg]["shown"] == "postgres://app:•••@localhost:15432/scores"


# ------------------------------------------------------------------ an agent call's read tools

def test_an_agent_call_reads_through_its_key_with_secrets_hidden_and_changes_refused(client, scores):
    key = extensions.open_call(project="demo", root=str(scores), keys=keys(conn(), conn("prod", "prod")), plugins=["db"], who="keelbot")
    call = lambda tool, **args: client.post("/plugins/call", json={"key": key, "tool": tool, "args": args})
    lines = call("db_connections").json()["text"]
    assert "- local: SQLite, local (data changes: a keel-query button the person presses)" in lines
    assert "- prod: SQLite, prod (read only, always)" in lines
    schema = call("db_schema").json()["text"]
    assert "- players(id*, name, password_hash)" in schema and "fk: player_id → players.id" in schema
    assert "- player_id: INTEGER" in call("db_schema", table="scores").json()["text"]
    rows = call("db_query", sql="SELECT name, password_hash FROM players ORDER BY id").json()["text"]
    assert rows.startswith("3 rows from local") and "hidden as •••: password_hash" in rows and "| Ada | ••• |" in rows
    assert call("db_query", sql="DELETE FROM scores").json()["text"].startswith("Refused: This query changes data")
    assert call("git_status").status_code == 403                 # the git plugin is not on for this call
    extensions.close_call(key)
    assert call("db_schema").status_code == 401


# ------------------------------------------------------------------ workflows

def db_flow(client, repo, steps, run_mode="manual", on=("db",), k=None):
    wf = from_dict({"name": "data", "keel_rules": False, "steps": steps})
    return start(client, repo, workflow=wf, settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause",
                                                      "plugins": list(on), "run_mode": run_mode}, keys=k or keys(conn()))


ORPHANS = "SELECT s.id FROM scores s LEFT JOIN players p ON p.id = s.player_id WHERE p.id IS NULL"


def test_a_code_only_workflow_checks_the_data_and_keeps_the_rows(client, scores):
    tid = db_flow(client, scores, [
        {"id": "players", "kind": "code", "name": "the players", "action": "db:query", "with": {"sql": "select id, name from players"}},
        {"id": "orphans", "kind": "code", "name": "no score without a player", "action": "db:check", "soft": True,
         "with": {"sql": ORPHANS, "expect": "none"}},
        {"id": "ok", "kind": "branch", "name": "clean", "when": {"marker": "RESULT", "step": "orphans", "equals": "pass"}, "no": "look"},
        {"id": "fine", "kind": "gate", "name": "all clean"},
        {"id": "look", "kind": "gate", "name": "look at the data"}])
    s = wait(client, tid)
    assert s["waiting"]["step"] == "fine", s
    notes = {e["step"]: e["data"].get("note") for e in client.bus.of(tid, "step.finished")}
    assert notes["players"] == "3 row(s) from local" and notes["orphans"].startswith("Data check on local: 0 row(s), as expected")
    c = sqlite3.connect(scores / "app.db")
    c.execute("insert into scores (player_id, value) values (99, 1)")
    c.commit()
    c.close()
    tid = db_flow(client, scores, [
        {"id": "orphans", "kind": "code", "name": "no score without a player", "action": "db:check", "soft": True,
         "with": {"sql": ORPHANS}},
        {"id": "ok", "kind": "branch", "name": "clean", "when": {"marker": "RESULT", "step": "orphans", "equals": "pass"}, "no": "look"},
        {"id": "fine", "kind": "gate", "name": "all clean"},
        {"id": "look", "kind": "gate", "name": "look at the data"}])
    s = wait(client, tid)
    assert s["waiting"]["step"] == "look", s


def test_a_change_step_asks_with_the_row_count_and_runs_after_approve(client, scores):
    steps = [{"id": "zero", "kind": "code", "name": "give everyone a score", "action": "db:change",
              "with": {"sql": "INSERT INTO scores (player_id, value) SELECT p.id, 0 FROM players p LEFT JOIN scores s "
                              "ON s.player_id = p.id WHERE s.id IS NULL"}},
             {"id": "look", "kind": "gate", "name": "look"}]
    tid = db_flow(client, scores, steps)
    s = wait(client, tid)
    w = s["waiting"]
    assert w["title"] == "Change data in local?" and "changes 2 row(s) in local (local)" in w["detail"], w
    assert sqlite3.connect(scores / "app.db").execute("select count(*) from scores").fetchone() == (1,)
    s = decide(client, tid)
    assert s["waiting"]["step"] == "look", s
    assert sqlite3.connect(scores / "app.db").execute("select count(*) from scores").fetchone() == (3,)
    # run mode auto on a local database: no question; on a test one it still asks
    tid = db_flow(client, scores, [dict(steps[0], **{"with": {"sql": "UPDATE scores SET value = value + 1"}}), steps[1]], run_mode="auto")
    assert wait(client, tid)["status"] == "done"            # auto approves the gate too
    tid = db_flow(client, scores, [dict(steps[0], **{"with": {"sql": "UPDATE scores SET value = 1", "connection": "t"}}), steps[1]],
                  run_mode="auto", k=keys(conn("test", "t")))
    assert wait(client, tid)["waiting"]["title"] == "Change data in t?"


def test_a_plugin_step_fails_when_the_plugin_is_off(client, scores):
    tid = db_flow(client, scores, [{"id": "q", "kind": "code", "name": "q", "action": "db:query", "with": {"sql": "select 1"}}], on=())
    s = wait(client, tid)
    assert "The Database plugin is off for this project" in json.dumps(s), s


def test_validation_knows_the_plugin_steps_and_their_settings():
    def errs(step):
        return validate(from_dict({"name": "x", "keel_rules": False, "steps": [step]}))
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "db:check", "with": {"sql": "select 1", "expect": "none"}}) == []
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "db:check"}) == ["Step 'a': db:check needs `with: {sql: ...}`."]
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "git:push", "with": {"force": True}}) == \
        ["Step 'a': git:push does not know `with: force`."]
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "commit", "with": {"x": 1}}) == \
        ["Step 'a': only a plugin step (db:..., git:...) takes `with`."]
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "db:drop"}) == ["Step 'a': unknown action 'db:drop'."]


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

    for name, want in (("db", {"db_connections", "db_schema", "db_query"}),
                       ("git", {"git_status", "git_diff", "git_log", "git_show", "git_blame", "git_branches", "pr_status"})):
        tools = asyncio.run(build(name).list_tools())
        assert {t.name for t in tools} == want and all(t.annotations.readOnlyHint for t in tools)
    spec = extensions.server_specs(["db", "nope"], "pk_x")
    assert [s["name"] for s in spec] == ["keel-db"] and spec[0]["env"]["KEEL_PLUGIN_KEY"] == "pk_x"
    assert "url" not in json.dumps(spec)                         # no connection, no password in the server's config


def test_claude_codes_acting_tool_asks_in_the_inbox_and_reads_the_answer_once(client):
    q = client.post("/plugins/ask", json={"project": "demo", "title": "Claude Code: push the branch?", "command": "push the branch"}).json()
    assert q["session"] == "mcp" and q["kind"] == "plugin"
    assert client.get(f"/plugins/ask/{q['id']}").json() == {"id": q["id"], "waiting": True}
    listed = client.get("/helper/permissions?project=demo").json()
    assert [x["id"] for x in listed] == [q["id"]]                   # the Inbox shows it like KeelBot's commands
    assert client.post(f"/helper/permissions/{q['id']}", json={"decision": "always"}).status_code == 200   # once, no grant
    assert client.get(f"/plugins/ask/{q['id']}").json() == {"id": q["id"], "decision": "allow", "why": ""}
    assert client.get(f"/plugins/ask/{q['id']}").status_code == 404     # read once
