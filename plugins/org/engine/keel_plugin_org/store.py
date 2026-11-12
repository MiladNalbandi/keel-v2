"""The Org plugin's tables in the engine's database (like the Supervisor's ledger): orgs, missions, the flows each
mission ran, and a mission's history.

    orgs: save_org(oid, name, yaml) / org_row(oid) / org_rows() / delete_org(oid)
    missions: add(...) / get(mid) / update(mid, **fields) / children(mid) / of_org(oid, top_only) / queued(oid) /
              running_count(oid) / by_thread(tid)
    flows: link_thread(tid, mid, stage)
    history: note(mid, kind, text, data) / history(mid)
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

from keel_engine.clock import utc_iso as now
from keel_engine.runtime import db

_TABLES = (
    """create table if not exists org_orgs (
      id text primary key, name text not null, yaml text not null, updated_at text not null)""",
    """create table if not exists org_missions (
      id text primary key, org_id text not null, role text not null, parent_id text, project_id text not null,
      title text not null, request text not null default '', source text, workflow text, stage text not null,
      status text not null, thread_id text, cap_tokens integer, used_tokens integer not null default 0,
      send_backs integer not null default 0, depth integer not null default 1, report_json text, decision_json text,
      note text, run_key text, created_at text not null, updated_at text not null)""",
    "create index if not exists org_missions_org on org_missions (org_id, parent_id)",
    "create index if not exists org_missions_parent on org_missions (parent_id)",
    """create table if not exists org_threads (
      thread_id text primary key, mission_id text not null, stage text not null, at text not null)""",
    """create table if not exists org_history (
      id integer primary key autoincrement, mission_id text not null, at text not null, kind text not null,
      text text not null, data_json text)""",
    "create index if not exists org_history_mission on org_history (mission_id, id)",
)
_ready: set[str] = set()

# a mission's status: it waits for a person to start it (watch), waits for a free slot, its flow runs, it gave work out
# and waits for it, it handed its report up, it is done, it failed, a person stopped it
STATUSES = ("proposed", "queued", "running", "delegated", "reported", "done", "failed", "stopped")
OPEN = ("proposed", "queued", "running", "delegated")
ENDED = ("reported", "done", "failed", "stopped")
COLS = ("id", "org_id", "role", "parent_id", "project_id", "title", "request", "source", "workflow", "stage", "status",
        "thread_id", "cap_tokens", "used_tokens", "send_backs", "depth", "report_json", "decision_json", "note",
        "run_key", "created_at", "updated_at")


@contextmanager
def _db():
    with db.connect() as conn:
        p = str(db.path())
        if p not in _ready:
            for sql in _TABLES:
                conn.execute(sql)
            _ready.add(p)
        yield conn
        conn.commit()


def _mission(row) -> dict:
    m = dict(zip(COLS, row))
    m["report"] = json.loads(m.pop("report_json") or "null")
    m["decision"] = json.loads(m.pop("decision_json") or "null")
    return m


# ------------------------------------------------------------------ orgs

def save_org(oid: str, name: str, text: str) -> dict:
    at = now()
    with _db() as c:
        c.execute("insert into org_orgs values (?,?,?,?) on conflict(id) do update set name=excluded.name, "
                  "yaml=excluded.yaml, updated_at=excluded.updated_at", (oid, name, text, at))
    return {"id": oid, "name": name, "yaml": text, "updated_at": at}


def org_row(oid: str) -> dict | None:
    with _db() as c:
        r = c.execute("select id, name, yaml, updated_at from org_orgs where id = ?", (oid,)).fetchone()
    return dict(zip(("id", "name", "yaml", "updated_at"), r)) if r else None


def org_rows() -> list[dict]:
    with _db() as c:
        rows = c.execute("select id, name, yaml, updated_at from org_orgs order by name, id").fetchall()
    return [dict(zip(("id", "name", "yaml", "updated_at"), r)) for r in rows]


def delete_org(oid: str) -> bool:
    with _db() as c:
        n = c.execute("delete from org_orgs where id = ?", (oid,)).rowcount
    return n > 0


# ------------------------------------------------------------------ missions

def add(*, org_id: str, role: str, project_id: str, title: str, request: str = "", parent_id: str | None = None,
        source: str | None = None, workflow: str | None, stage: str, status: str, cap_tokens: int | None = None,
        depth: int = 1) -> dict:
    mid = "m_" + uuid.uuid4().hex[:12]
    at = now()
    with _db() as c:
        c.execute(f"insert into org_missions ({', '.join(COLS)}) values ({', '.join('?' * len(COLS))})",
                  (mid, org_id, role, parent_id, project_id, title[:200], request[:20_000], source, workflow, stage, status,
                   None, cap_tokens, 0, 0, depth, None, None, None, None, at, at))
    return get(mid)


def get(mid: str) -> dict | None:
    with _db() as c:
        r = c.execute(f"select {', '.join(COLS)} from org_missions where id = ?", (mid,)).fetchone()
    return _mission(r) if r else None


def update(mid: str, **fields) -> dict | None:
    if not fields:
        return get(mid)
    if "report" in fields:
        fields["report_json"] = json.dumps(fields.pop("report"))
    if "decision" in fields:
        fields["decision_json"] = json.dumps(fields.pop("decision"))
    bad = [k for k in fields if k not in COLS]
    if bad:
        raise ValueError(f"no mission field {bad}")
    fields["updated_at"] = now()
    with _db() as c:
        c.execute(f"update org_missions set {', '.join(f'{k} = ?' for k in fields)} where id = ?", (*fields.values(), mid))
    return get(mid)


def children(mid: str) -> list[dict]:
    with _db() as c:
        rows = c.execute(f"select {', '.join(COLS)} from org_missions where parent_id = ? order by rowid",
                         (mid,)).fetchall()
    return [_mission(r) for r in rows]


def of_org(oid: str, top_only: bool = True, limit: int = 100) -> list[dict]:
    where = "org_id = ? and parent_id is null" if top_only else "org_id = ?"
    with _db() as c:
        rows = c.execute(f"select {', '.join(COLS)} from org_missions where {where} order by rowid desc limit ?",
                         (oid, limit)).fetchall()
    return [_mission(r) for r in rows]


def queued(oid: str) -> list[dict]:
    with _db() as c:
        rows = c.execute(f"select {', '.join(COLS)} from org_missions where org_id = ? and status = 'queued' "
                         "order by rowid", (oid,)).fetchall()
    return [_mission(r) for r in rows]


def running_count(oid: str) -> int:
    with _db() as c:
        return c.execute("select count(*) from org_missions where org_id = ? and status = 'running'", (oid,)).fetchone()[0]


def open_orgs() -> list[str]:
    with _db() as c:
        rows = c.execute("select distinct org_id from org_missions where status = 'queued'").fetchall()
    return [r[0] for r in rows]


# ------------------------------------------------------------------ the flows a mission ran

def link_thread(tid: str, mid: str, stage: str) -> None:
    with _db() as c:
        c.execute("insert or replace into org_threads values (?,?,?,?)", (tid, mid, stage, now()))


def by_thread(tid: str) -> tuple[dict, str] | None:
    with _db() as c:
        r = c.execute("select mission_id, stage from org_threads where thread_id = ?", (tid,)).fetchone()
    if not r:
        return None
    m = get(r[0])
    return (m, r[1]) if m else None


def threads_of(mid: str) -> list[dict]:
    with _db() as c:
        rows = c.execute("select thread_id, stage, at from org_threads where mission_id = ? order by rowid", (mid,)).fetchall()
    return [{"thread_id": t, "stage": s, "at": a} for t, s, a in rows]


# ------------------------------------------------------------------ history

def note(mid: str, kind: str, text: str, data: dict | None = None) -> None:
    with _db() as c:
        c.execute("insert into org_history (mission_id, at, kind, text, data_json) values (?,?,?,?,?)",
                  (mid, now(), kind, text[:2000], json.dumps(data) if data else None))


def history(mid: str, limit: int = 200) -> list[dict]:
    with _db() as c:
        rows = c.execute("select at, kind, text, data_json from org_history where mission_id = ? order by id limit ?",
                         (mid, limit)).fetchall()
    return [{"at": a, "kind": k, "text": t, "data": json.loads(d) if d else None} for a, k, t, d in rows]
