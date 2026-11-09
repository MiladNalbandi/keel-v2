"""Safety & History routes: a project's history of tool calls, its filters and its chain check."""

import json

from conftest import TOKEN
from keel_api.safety import history

AUTH = {"X-Keel-Token": TOKEN}


def add_project(keel, pid="shop"):
    keel.ctx.db.update("insert into projects (id, name, root, created_at) values (?, ?, ?, ?)",
                       (pid, pid, f"/work/{pid}", "2026-11-09T10:00:00Z"))


def write(keel, pid, lines):
    """Lines as the engine writes them (engine/keel_engine/safety/history.py)."""
    p = history.path_for(keel.ctx.settings.data_dir, pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    prev, out = history.GENESIS, []
    for i, body in enumerate(lines, 1):
        rec = {**body, "seq": i, "prev": prev}
        rec["hash"] = history.line_hash(prev, rec)
        prev = rec["hash"]
        out.append(json.dumps(rec, sort_keys=True))
    p.write_text("\n".join(out) + "\n")
    return p


CALLS = [
    {"thread": "t-1", "kind": "read", "tool": "Read", "text": "src/app.py", "action": "allow", "tier": "T0"},
    {"thread": "t-1", "kind": "tool", "tool": "Bash", "text": "git push origin main", "action": "allow", "tier": "T2"},
    {"thread": "t-1", "kind": "tool", "tool": "Bash", "text": "psql -c 'DROP TABLE orders'", "action": "deny",
     "tier": "T3", "rule": "builtin.sql-drop", "reason": "It drops or truncates a database object."},
    {"thread": "t-2", "kind": "edit", "tool": "Edit", "text": "Edit README.md", "action": "allow", "tier": "T1"},
]


def test_the_line_hash_is_the_one_the_engine_writes():
    body = {"seq": 1, "prev": history.GENESIS, "text": "git push origin main", "tier": "T2"}
    assert history.line_hash(history.GENESIS, body) == "032c53b6f4d92afd48fa998501ac77a8d3c90504b7ff59379d2ec0860c913054"
    assert history.path_for("/d", "shop").as_posix() == "/d/history/shop.jsonl"


def test_the_history_comes_newest_first_with_counts_and_filters(keel):
    add_project(keel)
    write(keel, "shop", CALLS)
    r = keel.client.get("/api/projects/shop/history", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["seq"] for x in body["items"]] == [4, 3, 2, 1]
    assert body["counts"] == {"total": 4, "action": {"allow": 3, "deny": 1},
                              "tier": {"T0": 1, "T2": 1, "T3": 1, "T1": 1}, "rule": {"builtin.sql-drop": 1}}
    assert body["more"] is False and body["next"] is None
    assert not keel.back.seen, "answered here, never passed on to the Kotlin api"

    def seqs(**params):
        return [x["seq"] for x in keel.client.get("/api/projects/shop/history", headers=AUTH, params=params).json()["items"]]

    assert seqs(action="deny") == [3]
    assert seqs(tier="T2") == [2]
    assert seqs(thread="t-2") == [4]
    assert seqs(q="drop table") == [3]
    assert seqs(kind="read") == [1]
    assert seqs(limit=2) == [4, 3]
    assert seqs(limit=2, before=3) == [2, 1]


def test_the_chain_check_finds_a_changed_line(keel):
    add_project(keel)
    p = write(keel, "shop", CALLS)
    assert keel.client.get("/api/projects/shop/history/verify", headers=AUTH).json() == \
        {"ok": True, "text": "4 lines, chain intact"}
    p.write_text(p.read_text().replace("DROP TABLE orders", "SELECT 1"))
    assert keel.client.get("/api/projects/shop/history/verify", headers=AUTH).json() == \
        {"ok": False, "text": "line 3 was changed after it was written"}


def test_a_project_with_no_history_yet_and_an_unknown_project(keel):
    add_project(keel, "blog")
    r = keel.client.get("/api/projects/blog/history", headers=AUTH).json()
    assert r["items"] == [] and r["counts"]["total"] == 0
    assert keel.client.get("/api/projects/blog/history/verify", headers=AUTH).json()["ok"] is True
    r = keel.client.get("/api/projects/nope/history", headers=AUTH)
    assert r.status_code == 404 and r.json()["error"] == 'No project called "nope"'


def test_a_browser_must_sign_in(keel):
    add_project(keel)
    r = keel.client.get("/api/projects/shop/history")
    assert r.status_code == 401
