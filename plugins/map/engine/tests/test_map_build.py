"""The map (ER + endpoints), its routes, and the map a project scan builds (the hook on_scan, after the Graph plugin's
code index when that plugin is there too). Moved from engine/tests/test_scan_map.py with the map's code."""

import stat
import subprocess
import time
from pathlib import Path

import pytest

from keel_engine import extensions
from keel_engine.runtime import scan
from keel_plugin_map import mapper

GRAPH_ENGINE = str(Path(__file__).resolve().parents[3] / "graph" / "engine")


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True)


def write(repo, rel, text):
    f = Path(repo) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def commit_all(repo, msg="x"):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


# ------------------------------------------------------------------ map

MIGRATIONS = {
    "apps/api/src/main/resources/db/migration/V1__players.sql": """
create table players (
    id bigserial primary key,
    name varchar(80) not null
);
create table teams (
    id bigint not null,
    title text,
    primary key (id)
);
""",
    "apps/api/src/main/resources/db/migration/V2__scores.sql": """
CREATE TABLE IF NOT EXISTS scores (
    id      BIGSERIAL PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(id),
    points  NUMERIC(10,2)
);
ALTER TABLE players ADD COLUMN team_id BIGINT;
ALTER TABLE players ADD CONSTRAINT fk_team FOREIGN KEY (team_id) REFERENCES teams(id);
CREATE TABLE tmp (id int);
DROP TABLE tmp;
""",
    "apps/api/src/main/resources/db/migration/V10__later.sql": "ALTER TABLE scores ADD COLUMN at timestamp;\n",
}

OPENAPI = """openapi: 3.0.0
info: {title: scores, version: '1'}
paths:
  /players:
    get: {summary: list}
    post: {summary: add}
  "/players/{id}/scores":
    get: {summary: scores}
    description: |
      get: this line is prose, not an endpoint
"""


def test_map_er_from_migrations_and_endpoints_from_openapi(repo):
    for rel, text in MIGRATIONS.items():
        write(repo, rel, text)
    write(repo, "contracts/openapi.yaml", OPENAPI)
    commit_all(repo)
    m = mapper.build(str(repo))
    assert m["counts"]["tables"] == 3 and m["counts"]["endpoints"] == 3
    assert m["sha"] == git(repo, "rev-parse", "HEAD").stdout.strip()
    er = m["levels"]["er"]
    nodes = {n["title"]: n for n in er["nodes"]}
    assert set(nodes) == {"players", "teams", "scores"}                    # tmp was dropped
    rows = [r["t"].split()[0] for r in nodes["players"]["rows"]]
    assert rows == ["id", "name", "team_id"] and nodes["players"]["rows"][0]["flag"] == "pk"
    assert nodes["teams"]["rows"][0]["flag"] == "pk"                       # table-level primary key (id)
    assert [r["t"].split()[0] for r in nodes["scores"]["rows"]] == ["id", "player_id", "points", "at"]  # V10 after V2
    assert nodes["scores"]["rows"][1]["flag"] == "fk" and nodes["scores"]["cite"]["line"] == 2
    fks = {(e["from"], e["to"]) for e in er["edges"]}
    assert fks == {("tbl:players", "tbl:scores"), ("tbl:teams", "tbl:players")}
    assert all(e["d"].startswith("M ") and e["kind"] == "fk" for e in er["edges"])
    assert all({"x", "y", "w", "h"} <= set(n) for n in er["nodes"]) and er["width"] > 0 and er["height"] > 0
    api = next(n for n in m["levels"]["modules"]["nodes"] if n["id"] == "api:contract")
    assert [r["t"] for r in api["rows"]] == ["GET /players", "POST /players", "GET /players/{id}/scores"]
    assert api["cite"] == {"rel": "contracts/openapi.yaml", "line": 4}
    assert {n["title"] for n in m["levels"]["modules"]["nodes"]} >= {"apps/api", "src/scores", "contracts"}  # apps/ and src/ open one level
    assert [n["id"] for n in m["levels"]["system"]["nodes"]] == ["app:code", "api:contract", "db:main"]
    assert "flow" not in m["levels"] and "classes" not in m["levels"]


def test_map_endpoints_store_and_read(client, repo):
    assert client.get("/projects/demo/map").json() == {"missing": "No map yet. Build it to draw one."}
    m = client.post("/projects/demo/map", json={"root": str(repo)}).json()
    assert m["levels"]["system"]["nodes"] and "er" not in m["levels"]
    assert client.get("/projects/demo/map").json()["at"] == m["at"]
    assert client.post("/projects/demo/map", json={"root": "/no/such/folder"}).status_code == 400


# ------------------------------------------------------------------ the map a scan builds

FAKE_CODEGRAPH = """#!/bin/sh
# A stand-in for @colbymchenry/codegraph: init/index write .codegraph/codegraph.db, status prints counts.
cmd="$1"; shift
for a in "$@"; do case "$a" in -*) ;; *) root="$a";; esac; done
case "$cmd" in
  init|index) mkdir -p "$root/.codegraph" && echo db > "$root/.codegraph/codegraph.db" && echo "Indexed 3 files" ;;
  sync) exit 0 ;;
  status) printf '{"initialized":true,"fileCount":3,"nodeCount":20,"nodesByKind":{"file":3,"import":5,"function":9,"class":3},"journalMode":"wal"}\\n' ;;
  *) echo "unknown command $cmd" >&2; exit 2 ;;
esac
"""


def scan_and_wait(client, root, project="demo"):
    r = client.post(f"/projects/{project}/scan", json={"root": str(root), "rebuild": False})
    assert r.status_code == 200, r.text
    deadline = time.time() + 20
    while time.time() < deadline:
        s = client.get(f"/projects/{project}/index").json()
        if s["status"] != "indexing":
            return s
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


@pytest.fixture
def with_graph(monkeypatch):
    """The Graph plugin (plugins/graph) next to this one, as the image has them: its code index runs first."""
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", GRAPH_ENGINE)
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_graph")
    extensions.reload()
    yield
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS")
    extensions.reload()


def test_a_scan_without_the_graph_plugin_still_builds_the_map(client, repo):
    s = scan_and_wait(client, repo)
    assert s["status"] == "failed" and s["error"] == scan.NO_INDEX["error"] and s["map"]["counts"]["files"] > 0
    steps = [e["data"].get("step") for e in client.bus.recent if e["type"] == "index.progress"]
    assert steps == ["stack", "map"]


def test_a_scan_builds_the_map_after_the_code_graph(with_graph, client, repo, tmp_path, monkeypatch):
    exe = tmp_path / "bin" / "codegraph"
    exe.parent.mkdir()
    exe.write_text(FAKE_CODEGRAPH)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("KEEL_CODEGRAPH_BIN", str(exe))
    s = scan_and_wait(client, repo)
    assert s["status"] == "ready" and s["map"]["counts"]["files"] > 0
    assert mapper.load("demo")["counts"]["files"] > 0
    steps = [e["data"].get("step") for e in client.bus.recent if e["type"] == "index.progress"]
    assert steps == ["stack", "graph", "map"]


def test_a_scan_without_codegraph_fails_clearly_but_builds_the_map(with_graph, client, repo, monkeypatch):
    monkeypatch.setenv("KEEL_CODEGRAPH_BIN", "")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    s = scan_and_wait(client, repo)
    assert s["status"] == "failed" and "not installed" in s["error"]
    assert mapper.load("demo") is not None
