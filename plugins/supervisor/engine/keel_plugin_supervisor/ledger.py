"""The Supervisor's audit log: one row per look, answer and escalation, with its reason (engine DB, its own table).

    record(**row) -> row      list_rows(thread_id=None, project_id=None, limit=100)
    answered(thread_id, question_id) / answered_title(thread_id, key) / answers(thread_id): the repeat limits read these
    tokens_today(project_id): the daily cap reads it
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

from keel_engine.runtime import db

_TABLE = """create table if not exists supervisor_decisions (
  id text primary key, project_id text not null, thread_id text not null, question_id text, step text, ac text,
  kind text, title_key text, trigger text not null, action text not null, decision text, choice text, answers_json text,
  why text, confidence text, evidence_json text, code text, summary text, model text, effort text,
  tokens integer not null default 0, cost_usd real not null default 0, ms integer not null default 0,
  outcome text not null, at text not null, commands_json text
)"""
# 0.18.64 the commands of an own_fix verdict: a table made before has no such column yet
_ADD = "alter table supervisor_decisions add column commands_json text"
_INDEX = "create index if not exists supervisor_decisions_thread on supervisor_decisions (thread_id, at)"
_ready: set[str] = set()
COLS = ("id", "project_id", "thread_id", "question_id", "step", "ac", "kind", "title_key", "trigger", "action",
        "decision", "choice", "answers_json", "why", "confidence", "evidence_json", "code", "summary", "model", "effort",
        "tokens", "cost_usd", "ms", "outcome", "at", "commands_json")


@contextmanager
def _db():
    with db.connect() as conn:
        p = str(db.path())
        if p not in _ready:
            conn.execute(_TABLE)
            conn.execute(_INDEX)
            if "commands_json" not in {r[1] for r in conn.execute("pragma table_info(supervisor_decisions)")}:
                conn.execute(_ADD)
            _ready.add(p)
        yield conn


def record(**row) -> dict:
    full = {c: None for c in COLS}
    full.update({k: v for k, v in row.items() if k in COLS})
    full["id"] = full["id"] or uuid.uuid4().hex
    full["at"] = full["at"] or db.now()
    full["answers_json"] = json.dumps(row.get("answers") or {})
    full["evidence_json"] = json.dumps(row.get("evidence") or [])
    full["commands_json"] = json.dumps(row.get("commands") or [])
    for k in ("tokens", "ms"):
        full[k] = int(full[k] or 0)
    full["cost_usd"] = float(full["cost_usd"] or 0)
    with _db() as conn:
        conn.execute(f"insert into supervisor_decisions ({', '.join(COLS)}) values ({', '.join('?' * len(COLS))})",
                     tuple(full[c] for c in COLS))
    return _out(full)


def _out(r: dict) -> dict:
    r = dict(r)
    r["answers"] = json.loads(r.pop("answers_json") or "{}")
    r["evidence"] = json.loads(r.pop("evidence_json") or "[]")
    r["commands"] = json.loads(r.pop("commands_json", None) or "[]")
    return r


def list_rows(thread_id: str | None = None, project_id: str | None = None, limit: int = 100) -> list[dict]:
    where, args = [], []
    if thread_id:
        where.append("thread_id = ?")
        args.append(thread_id)
    if project_id:
        where.append("project_id = ?")
        args.append(project_id)
    sql = f"select {', '.join(COLS)} from supervisor_decisions" + (f" where {' and '.join(where)}" if where else "")
    with _db() as conn:
        rows = conn.execute(sql + " order by at desc, rowid desc limit ?", (*args, int(limit))).fetchall()
    return [_out(dict(zip(COLS, r))) for r in rows]


def _count(sql: str, args: tuple) -> int:
    with _db() as conn:
        return int(conn.execute(sql, args).fetchone()[0] or 0)


def answered(thread_id: str, question_id: str) -> int:
    return _count("select count(*) from supervisor_decisions where thread_id = ? and question_id = ? and outcome = 'applied'",
                  (thread_id, question_id))


def answered_title(thread_id: str, key: str) -> int:
    return _count("select count(*) from supervisor_decisions where thread_id = ? and title_key = ? and outcome = 'applied'",
                  (thread_id, key))


def tokens_today(project_id: str) -> int:
    """What its turns used in this project today (UTC): the daily cap reads it."""
    return _count("select sum(tokens) from supervisor_decisions where project_id = ? and at >= ?",
                  (project_id, db.now()[:10]))


def answers(thread_id: str) -> int:
    return _count("select count(*) from supervisor_decisions where thread_id = ? and outcome = 'applied'", (thread_id,))
