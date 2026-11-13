"""#96 Access "private" (Results only to you): agents write and run reads, but the rows go only to the person. The agent
reads a result id and the column names: no row, no count, no "more rows", and a database error only when it names
nothing from the data. keel keeps the results in memory for the person (the engine's results routes), KeelBot gets a
card for them, keel2 mcp a link, and a workflow's db:query keeps them out of the flow's data. Fake model only; SQLite."""

import json
import sqlite3

import httpx
import pytest

from keel_engine import approvals, extensions
from keel_engine import mcp_server
from keel_engine.workflows.model import from_dict
from keel_plugin_db import core, private
from keel_plugin_db import mcp_tools as db_mcp

from conftest import start, wait


@pytest.fixture(autouse=True)
def fresh():
    private.clear()
    yield
    private.clear()


def conn(name="local", env="local", access="private", **kw):
    return {"name": name, "kind": "sqlite", "url": "sqlite:app.db", "env": env, "agent_access": access, **kw}


def keyset(*conns):
    return {f"db:{c['name']}": json.dumps(c) for c in conns}


def opened(client, root, *conns, who="agent:coder", thread="t-1"):
    return extensions.open_call(project="demo", root=str(root), keys=keyset(*conns), plugins=["db"], who=who,
                                bus=client.bus, thread=thread)


def call(key, tool, **args):
    return extensions.call(key, tool, args)


def test_the_agent_gets_the_result_id_and_columns_never_a_row_or_a_count(client, scores):
    key = opened(client, scores, conn())
    assert "- local: SQLite, local, this project's own (you run reads, but the rows go only to the person" in call(key, "db_connections")
    assert "- players(id*, name, password_hash)" in call(key, "db_schema")       # the schema stays: it writes the SQL
    text = call(key, "db_query", sql="select id, name, password_hash from players order by id")
    rid = text.split("result ", 1)[1].split(" ", 1)[0]
    assert text.startswith(f"Done: result {rid} from local, columns: id, name, password_hash.")
    assert "the results go only to the person" in text and "Settings › Databases › Results for you" in text
    for leak in ("Ada", "Bo", "Chen", "h1", "3 rows", "3 row", "more rows"):
        assert leak not in text, leak
    kept = private.one("demo", rid)
    assert kept["rows"] == [[1, "Ada", "•••"], [2, "Bo", "•••"], [3, "Chen", "•••"]] and kept["count"] == 3
    assert kept["who"] == "the agent coder" and kept["thread"] == "t-1" and kept["masked"] == ["password_hash"]
    listed = private.listed("demo")
    assert [r["id"] for r in listed] == [rid] and "rows" not in listed[0]
    assert private.listed("other") == []
    with pytest.raises(core.PluginError):
        private.one("other", rid)                 # another project's result does not exist for it


def test_keelbot_gets_a_card_block_for_the_person(client, scores):
    key = opened(client, scores, conn(), who="keelbot")
    text = call(key, "db_query", sql="select name from players")
    rid = text.split("result ", 1)[1].split(" ", 1)[0]
    assert f'```keel-result\n{{"id": "{rid}"}}\n```' in text and "in the card under your answer" in text
    assert private.one("demo", rid)["who"] == "KeelBot"


def test_a_change_is_refused_and_nothing_is_asked(client, scores):
    key = opened(client, scores, conn())
    text = call(key, "db_change", sql="DELETE FROM scores")
    assert text.startswith("Refused: On local agents only run reads, and the results go to the person.")
    assert not approvals.of(client.bus).pending("demo")
    assert call(key, "db_query", sql="DELETE FROM scores").startswith("Refused: This query changes data")
    assert private.listed("demo") == []           # keel refused it before the database ran anything


def test_a_database_error_reaches_the_agent_only_when_it_names_nothing_from_the_data(client, scores):
    key = opened(client, scores, conn())
    safe = call(key, "db_query", sql="select nme from players")
    assert "failed. The database refused the query: no such column: nme" in safe
    # SQLite's "malformed JSON" is not on the list of messages that only name the query's own words: it stays hidden
    hidden = call(key, "db_query", sql="select json(name) from players")
    rid = hidden.split("Result ", 1)[1].split(" ", 1)[0]
    assert hidden.endswith(private.HIDDEN_ERROR) and "malformed" not in hidden
    assert private.one("demo", rid)["error"] == "The database refused the query: malformed JSON"


class Pg(Exception):
    def __init__(self, msg, sqlstate):
        super().__init__(msg)
        self.sqlstate = sqlstate


def test_which_database_errors_are_safe_for_an_agent():
    assert core.sql_error("postgres", Pg('column "emial" does not exist', "42703")) == ("42703", True)
    assert core.sql_error("postgres", Pg('invalid input syntax for type integer: "ann@example.com"', "22P02")) == ("22P02", False)
    assert core.sql_error("postgres", Pg("cannot execute UPDATE in a read-only transaction", "25006"))[1] is True
    assert core.sql_error("postgres", Exception("no code")) == ("", False)
    assert core.sql_error("mysql", Exception(1054, "Unknown column 'emial' in 'field list'")) == ("1054", True)
    assert core.sql_error("mysql", Exception(1292, "Truncated incorrect DOUBLE value: 'ann'")) == ("1292", False)
    assert core.sql_error("sqlite", sqlite3.OperationalError("no such table: playerz"))[1] is True
    assert core.sql_error("sqlite", sqlite3.OperationalError("malformed JSON"))[1] is False


def test_the_engine_routes_answer_an_mcp_query_with_an_id_and_keep_the_rows_for_the_person(client, scores):
    root = str(scores)
    body = {"root": root, "connection": conn(), "sql": "select name from players order by id", "private": True,
            "project": "shop", "who": "Claude Code"}
    r = client.post("/plugins/db/query", json=body).json()
    assert set(r) == {"private", "id", "connection", "columns"} and r["columns"] == ["name"]
    one = client.post("/plugins/db/result", json={"project": "shop", "id": r["id"]}).json()
    assert one["rows"] == [["Ada"], ["Bo"], ["Chen"]] and one["who"] == "Claude Code" and one["sql"] == body["sql"]
    assert [x["id"] for x in client.post("/plugins/db/results", json={"project": "shop"}).json()] == [r["id"]]
    assert client.post("/plugins/db/result", json={"project": "other", "id": r["id"]}).status_code == 404
    change = client.post("/plugins/db/query", json={**body, "sql": "delete from scores", "change": True})
    assert change.status_code == 403
    assert client.post("/plugins/db/forget", json={"project": "shop", "id": r["id"]}).json() == {"ok": True}
    assert client.post("/plugins/db/results", json={"project": "shop"}).json() == []


def test_results_stay_in_memory_at_most_keep_and_a_day(scores, monkeypatch):
    monkeypatch.setattr(private, "KEEP", 2)
    c = core.conn_of(conn())
    ids = [private.run(c, "select 1", root=str(scores), project="demo", who="keelbot")["id"] for _ in range(3)]
    assert [r["id"] for r in private.listed("demo")] == ids[:0:-1]        # the newest two, newest first
    import datetime as dt
    for r in private._results.values():
        r["_at"] -= dt.timedelta(seconds=private.KEEP_S + 1)
    assert private.listed("demo") == []


def test_a_workflow_keeps_a_private_connections_rows_out_of_the_flow(client, scores):
    wf = from_dict({"name": "data", "keel_rules": False, "steps": [
        {"id": "players", "kind": "code", "name": "the players", "action": "db:query", "with": {"sql": "select id, name from players"}},
        {"id": "orphans", "kind": "code", "name": "every player has a score", "action": "db:check", "soft": True,
         "with": {"sql": "select p.name from players p left join scores s on s.player_id = p.id where s.id is null"}},
        {"id": "look", "kind": "gate", "name": "look"}]})
    tid = start(client, scores, workflow=wf, keys=keyset(conn()),
                settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "plugins": ["db"], "run_mode": "manual"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "look", s
    done = {e["step"]: e["data"] for e in client.bus.of(tid, "step.finished")}
    everything = json.dumps(done) + json.dumps(s)
    for leak in ("Ada", "Bo", "Chen"):
        assert leak not in everything, leak
    assert done["players"]["note"].startswith("Result r_") and "only you see its rows" in done["players"]["note"]
    assert done["orphans"]["note"] == "Data check on local: 2 row(s), expected no rows."
    kept = private.listed("demo")
    assert [r["who"] for r in kept] == ["the workflow step orphans", "the workflow step players"]
    assert private.one("demo", kept[0]["id"])["rows"] == [["Bo"], ["Chen"]]


# ------------------------------------------------------------------ keel2 mcp: the api answers with an id

class PrivateApi:
    def __init__(self):
        self.posts = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/projects":
            return httpx.Response(200, json=[{"id": "shop", "name": "shop", "root": "/w/shop"}])
        if path == "/api/projects/shop/plugins":
            return httpx.Response(200, json=[{"name": "db", "enabled": True}])
        if path == "/api/projects/shop/db/query":
            self.posts.append(json.loads(req.content))
            return httpx.Response(200, json={"private": True, "id": "r_0123456789ab", "connection": "prod", "columns": ["email"]})
        return httpx.Response(404, json={"error": path})


def test_keel2_mcp_gives_the_client_a_link_not_the_rows(monkeypatch):
    monkeypatch.setenv("KEEL_PUBLIC_URL", "http://localhost:8080")
    api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(PrivateApi()))
    text = db_mcp.db_query(api, "select email from users", "shop")
    assert text.startswith("Done: result r_0123456789ab from prod, columns: email.")
    assert "at this link (give it to them):\nhttp://localhost:8080/#/projects/shop/settings/databases/results/r_0123456789ab\n" in text
    assert "keel-result" not in text                      # a card only KeelBot's chat can show
