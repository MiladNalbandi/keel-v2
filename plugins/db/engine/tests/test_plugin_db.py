"""v0.10.0 the Database plugin (plugins/db, keel_plugin_db; moved from keel_engine/plugins/db and engine/tests/test_plugins.py):
its catalog entry and commands, the person's own calls, the read tools an agent call reaches with its key, the workflow
steps, and the rules nobody can change (read only on staging and prod, a change counted first)."""

import asyncio
import json
import sqlite3

import httpx
import pytest
import yaml

from conftest import conn, decide, keys, start, wait
from keel_engine import extensions, mcp_server
from keel_engine.workflows.model import from_dict
from keel_engine.workflows.validate import validate
from keel_plugin_db import mcp_tools as db_mcp


# ------------------------------------------------------------------ the catalog and the commands

def test_the_catalog_lists_it_with_its_tools_steps_and_settings(client):
    cat = {p["name"]: p for p in client.get("/plugins").json()}
    assert set(cat) == {"db"}    # Code Review and Git are their own plugins (not loaded here)
    assert cat["db"]["title"] == "Database" and cat["db"]["tools"] == {"server": "keel-db", "read": ["db_connections", "db_schema", "db_query"]}
    check = next(a for a in cat["db"]["actions"] if a["name"] == "db:check")
    assert check["with"] == {"sql": "required", "expect": "optional", "connection": "optional"} and "data check" in check["summary"]


def test_its_commands_come_only_when_it_is_on(client, repo):
    names = lambda plugins: {c["name"] for c in client.post("/helper/commands", json={"root": str(repo), "plugins": plugins}).json()}
    assert "sql" not in names([])
    assert {"sql", "explain"} <= names(["db"])


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
    assert by[pg]["shown"] == "postgres://app:•••@localhost:15432/scores"  # keel:allow-secret


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
    assert call("git_status").status_code == 403                 # the git plugin is not on for this call (nor loaded here)
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


def test_validation_knows_its_steps_and_their_settings():
    def errs(step):
        return validate(from_dict({"name": "x", "keel_rules": False, "steps": [step]}))
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "db:check", "with": {"sql": "select 1", "expect": "none"}}) == []
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "db:check"}) == ["Step 'a': db:check needs `with: {sql: ...}`."]
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "commit", "with": {"x": 1}}) == \
        ["Step 'a': only a plugin step (db:...) takes `with`."]
    assert errs({"id": "a", "kind": "code", "name": "a", "action": "db:drop"}) == ["Step 'a': unknown action 'db:drop'."]


# ------------------------------------------------------------------ its tools in keel2 mcp: through keel's api

PROJECTS = [{"id": "shop", "name": "shop", "root": "/workspace/shop", "branch": "feat/scores", "flow": None, "phase": "none",
             "acs": [0, 0], "waiting": 0, "running": 0}]


class DbApi:
    """The keel api routes the Database plugin's keel2 mcp tools use, recording what they are sent."""

    def __init__(self, on=("db",), answer="allow"):
        self.on, self.answer, self.polls = on, answer, 0
        self.posts: list[tuple[str, dict]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, body = req.url.path, (json.loads(req.content) if req.content else {})
        if path == "/api/projects":
            return httpx.Response(200, json=PROJECTS)
        if path == "/api/projects/shop/plugins":
            return httpx.Response(200, json=[{"name": n, "enabled": n in self.on} for n in ("db", "git")])
        if path == "/api/projects/shop/db/query":
            self.posts.append((path, body))
            if body.get("change"):
                return httpx.Response(200, json={"connection": "local", "sql": body["sql"], "changed": 2, "done": bool(body.get("confirm"))})
            return httpx.Response(200, json={"connection": "local", "columns": ["name", "api_key"], "rows": [["Ada", "•••"]],
                                             "count": 1, "truncated": False, "masked": ["api_key"], "ms": 3})
        if path == "/api/projects/shop/plugins/ask":
            self.posts.append((path, body))
            return httpx.Response(200, json={"id": "p_000000000001"})
        if path == "/api/plugins/asks/p_000000000001":
            self.polls += 1
            done = self.polls > 1
            return httpx.Response(200, json={"id": "p_000000000001", "waiting": True} if not done else
                                  {"id": "p_000000000001", "decision": self.answer, "why": "" if self.answer == "allow" else "not now"})
        return httpx.Response(404, json={"error": f"No route {path}"})


@pytest.fixture
def no_project(monkeypatch):
    monkeypatch.delenv("KEEL_PROJECT", raising=False)
    monkeypatch.chdir("/")


def test_its_keel_mcp_tools_read_through_the_api_and_say_when_it_is_off(no_project):
    s = DbApi()
    api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(s))
    text = db_mcp.db_query(api, "select name, api_key from players", "shop")
    assert text.startswith("1 row from local") and "| Ada | ••• |" in text
    assert s.posts[-1][1] == {"sql": "select name, api_key from players", "connection": "", "mask": True}
    off = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(DbApi(on=())))
    with pytest.raises(mcp_server.ApiError, match="The Database plugin is off for shop"):
        db_mcp.db_query(off, "select 1", "shop")


def test_its_acting_tool_waits_for_the_persons_inbox_answer(no_project):
    s = DbApi()
    api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(s))
    out = db_mcp.db_change(api, "update scores set value = 0", "shop", sleep=lambda _s: None)
    assert out == "2 row(s) changed in local."
    asked = next(b for p, b in s.posts if p.endswith("/plugins/ask"))
    assert asked == {"title": "Claude Code: change 2 row(s) in local?", "command": "update scores set value = 0"}
    assert [b.get("confirm") for p, b in s.posts if p.endswith("/db/query")] == [None, True]
    no = DbApi(answer="deny")
    api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(no))
    assert db_mcp.db_change(api, "update scores set value = 0", "shop", sleep=lambda _s: None) == \
        "The person said no in keel's Inbox: not now"
    assert [b.get("confirm") for p, b in no.posts if p.endswith("/db/query")] == [None]     # never run for real


def test_its_keel_mcp_tools_join_keels_own_server():
    tools = lambda write: {t.name for t in asyncio.run(mcp_server.build_server(write=write, api=object()).list_tools())}
    assert {"keel_db_schema", "keel_db_query"} <= tools(False) and "keel_db_change" not in tools(False)
    assert "keel_db_change" in tools(True)
