"""The Supervisor's on/off switches, as keel's api last sent them (engine DB, its own table).

    keel        the keel-wide switch (Settings › General, plugins.supervisor.on). Off (the default): the Supervisor does
                nothing at all: no event listener, no model call, no Inbox card.
    <project>   the project's switch (its own Settings, or General's when it sets none) and whether the plugin is on
                for the project (Control › Plugins)

The api is the source of truth (its settings and plugin switches); it sends every change (POST /supervisor/switch).
A flow's own switch (settings.supervisor.on: true, false or unset) is in the flow's settings (routes.py).

    get_keel() -> bool                   project(pid) -> {"on": bool, "plugin": bool} | None
    put(keel=None, projects=None)        projects: {pid: {"on": bool | None, "plugin": bool | None}}
    defaults() -> {mode, stall_minutes}  keel-wide (Settings › General › Supervisor); a flow's own settings win
    put_defaults(values)
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from keel_engine.runtime import db

KEEL = "*"
_TABLE = """create table if not exists supervisor_switch (
  scope text primary key, on_ integer, plugin integer, at text not null
)"""
_DEFAULTS_TABLE = """create table if not exists supervisor_defaults (
  name text primary key, value text not null, at text not null
)"""
_ready: set[str] = set()
DEFAULT_KEYS = ("mode", "stall_minutes")


@contextmanager
def _db():
    with db.connect() as conn:
        p = str(db.path())
        if p not in _ready:
            conn.execute(_TABLE)
            conn.execute(_DEFAULTS_TABLE)
            _ready.add(p)
        yield conn


def _row(scope: str) -> tuple | None:
    with _db() as conn:
        return conn.execute("select on_, plugin from supervisor_switch where scope = ?", (scope,)).fetchone()


def get_keel() -> bool:
    r = _row(KEEL)
    return bool(r and r[0])


def project(pid: str) -> dict | None:
    r = _row(pid)
    if r is None:
        return None
    return {"on": bool(r[0]), "plugin": None if r[1] is None else bool(r[1])}


def defaults() -> dict:
    with _db() as conn:
        rows = conn.execute("select name, value from supervisor_defaults").fetchall()
    return {k: json.loads(v) for k, v in rows if k in DEFAULT_KEYS}


def put_defaults(values: dict | None) -> None:
    with _db() as conn:
        for k, v in (values or {}).items():
            if k in DEFAULT_KEYS and v is not None:
                conn.execute("insert into supervisor_defaults (name, value, at) values (?,?,?) on conflict(name) do update "
                             "set value = excluded.value, at = excluded.at", (k, json.dumps(v), db.now()))


def _put(conn, scope: str, on: bool | None, plugin: bool | None) -> None:
    old = conn.execute("select on_, plugin from supervisor_switch where scope = ?", (scope,)).fetchone()
    on_ = int(on) if on is not None else (old[0] if old else 0)
    plug = int(plugin) if plugin is not None else (old[1] if old else None)
    conn.execute("insert into supervisor_switch (scope, on_, plugin, at) values (?,?,?,?) on conflict(scope) do update "
                 "set on_ = excluded.on_, plugin = excluded.plugin, at = excluded.at", (scope, on_, plug, db.now()))


def put(keel: bool | None = None, projects: dict | None = None) -> None:
    with _db() as conn:
        if keel is not None:
            _put(conn, KEEL, keel, None)
        for pid, s in (projects or {}).items():
            if pid and pid != KEEL:
                _put(conn, pid, (s or {}).get("on"), (s or {}).get("plugin"))
