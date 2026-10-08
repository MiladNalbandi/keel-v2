"""The Database plugin's workflow steps. Their settings sit in the step's `with:`.

    db:query    {sql, connection?}                    a read; its rows go to data["<step id>"] (at most 200, secrets •••)
    db:check    {sql, expect?: none | some | <n>, connection?}   a read that passes when the rows match `expect`
                                                      (default none: "no row may come back", the usual data check)
    db:change   {sql, connection?}                    a change of data, only on a local or test database: keel counts
                                                      the rows (a run it rolls back) and asks the person first, unless
                                                      the run mode is auto and the database is local
    db:migrate  {}                                    the project's migration command (commands.migrate, .keel/config.yml)
"""

from __future__ import annotations

import asyncio
import hashlib

from ... import rules
from ...runtime import run_mode as run_modes
from .. import PluginError
from . import core, tools

SHOWN = 20


def _result(ok: bool, note: str, detail: str = "", update: dict | None = None, ask: dict | None = None):
    from ...runtime.actions import ActionResult

    return ActionResult(ok, note, detail, update or {}, ask=ask)


def _conn(a) -> core.Conn:
    return tools.pick(a.keys, str((a.params or {}).get("connection") or ""))


def _sql(a) -> str:
    sql = str((a.params or {}).get("sql") or "").strip()
    if not sql:
        raise PluginError(400, "This step has no SQL.", "Put the query in the step's with: sql.")
    return sql


def _fail(exc: Exception):
    return _result(False, str(exc), getattr(exc, "hint", "") or "")


def _read(a):
    conn, sql = _conn(a), _sql(a)
    return conn, core.query(conn, sql, root=a.root, mask=True)


def _query(a):
    try:
        conn, r = _read(a)
    except (core.DbError, PluginError) as exc:
        return _fail(exc)
    rows = [dict(zip(r["columns"], row)) for row in r["rows"]]
    data = {**(a.data or {}), (a.step or "db_query"): rows}
    return _result(True, f"{r['count']} row(s) from {conn.name}" + (" (more exist)" if r["truncated"] else ""),
                   f"```sql\n{r['sql']}\n```\n" + tools.table_text(r["columns"], r["rows"], SHOWN), {"data": data})


def _expect(value) -> tuple[str, int | None]:
    v = str(value if value is not None else "none").strip().lower()
    if v in ("none", "0", "empty", "no rows"):
        return "none", 0
    if v in ("some", "any", "rows"):
        return "some", None
    v = v.removeprefix("count:").strip()
    if v.isdigit():
        return "count", int(v)
    raise PluginError(400, f"expect: {value} is not none, some or a number.")


def _check(a):
    try:
        mode, n = _expect((a.params or {}).get("expect"))
        conn, r = _read(a)
    except (core.DbError, PluginError) as exc:
        return _fail(exc)
    got = r["count"]
    ok = got == 0 if mode == "none" else got > 0 if mode == "some" else got == n and not r["truncated"]
    want = {"none": "no rows", "some": "some rows", "count": f"{n} row(s)"}[mode]
    shown = f"{got}{'+' if r['truncated'] else ''} row(s)"
    note = f"Data check on {conn.name}: {shown}, as expected." if ok else f"Data check on {conn.name}: {shown}, expected {want}."
    detail = f"```sql\n{r['sql']}\n```\n" + (tools.table_text(r["columns"], r["rows"], SHOWN) if got else "(no rows)")
    return _result(ok, note, detail)


def _fingerprint(conn: core.Conn, sql: str) -> str:
    return hashlib.sha256(f"{conn.name}\n{conn.env}\n{sql.strip()}".encode()).hexdigest()[:16]


def _change(a):
    try:
        conn, sql = _conn(a), _sql(a)
        dry = core.query(conn, sql, root=a.root, allow_change=True)
    except (core.DbError, PluginError) as exc:
        return _fail(exc)
    fp = _fingerprint(conn, sql)
    auto = run_modes.normalize((a.settings or {}).get("run_mode")) == "auto" and conn.env == "local"
    if not auto and ((a.state or {}).get("plugin_ok") or {}).get(a.step) != fp:
        detail = (f"This step changes {dry['changed']} row(s) in {conn.name} ({conn.env}):\n\n```sql\n{dry['sql']}\n```\n\n"
                  "keel ran it once and rolled it back to count the rows. Approve to run it for real; reject to stop the flow.")
        return _result(False, f"{dry['changed']} row(s) would change in {conn.name}.", detail, ask={
            "type": "plugin", "kind": "gate", "title": f"Change data in {conn.name}?", "detail": detail, "fingerprint": fp,
            "labels": {"approve": f"Run it ({dry['changed']} rows)", "reject": "Stop the flow"}})
    try:
        r = core.query(conn, sql, root=a.root, allow_change=True, confirm=True)
    except (core.DbError, PluginError) as exc:
        return _fail(exc)
    return _result(True, f"{r['changed']} row(s) changed in {conn.name}.", f"```sql\n{r['sql']}\n```")


async def db_query(a):
    return await asyncio.to_thread(_query, a)


async def db_check(a):
    return await asyncio.to_thread(_check, a)


async def db_change(a):
    return await asyncio.to_thread(_change, a)


async def db_migrate(a):
    from ...runtime.actions import run_command

    cmd = str(((rules.load_config(a.root).get("commands") or {}).get("migrate")) or "").strip()
    if not cmd:
        return _result(False, "This project has no migration command.",
                       "Set commands.migrate in .keel/config.yml (for example ./gradlew flywayMigrate or npx prisma migrate deploy).")
    r = await run_command(cmd, a)
    r.note = ("Migrations ran: " if r.ok else "Migrations failed: ") + r.note
    return r


ACTIONS = {"db:query": db_query, "db:check": db_check, "db:change": db_change, "db:migrate": db_migrate}
# what each needs in `with:` is PARAMS in this package's PART (__init__.py)
