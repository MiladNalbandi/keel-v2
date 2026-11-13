"""Private results (#96): a connection whose Access is `private` lets agents write and run reads, but the rows go only to
the person.

    agent ──SQL──► keel ──► database
    agent ◄── "result r_…, columns a, b: the person sees it" ── keel
                         keel ──rows──► the person (Settings › Databases › Results for you, KeelBot's card)

The agent gets the result's id and its column names (the names its own SQL gave). No rows, no row count, no "more rows":
a count is data too (`WHERE salary > 100000`). A database error reaches it only when its message names nothing but the
query's own words (core.sql_error); the full message is kept for the person.

The results live in this process's memory only: never on disk, gone when keel restarts, at most KEEP of them and none
older than KEEP_S. Only the person reads them (the api's routes refuse every MCP caller). Columns named like a secret
are •••, as for agents: the person's own Query shows them.

This stops an agent from *seeing* the data. It does not stop one that tries hard to learn it: how long a query takes is
still a signal. On data no model may learn anything from, use Access `none` and run the queries yourself.
"""

from __future__ import annotations

import datetime as _dt
import secrets
import threading
from collections import OrderedDict

from . import core

KEEP = 100
KEEP_S = 24 * 3600
ROWS = 1000          # a private result may hold more rows than a model ever gets (core.MAX_ROWS)

_results: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _sweep() -> None:
    old = _now() - _dt.timedelta(seconds=KEEP_S)
    for rid in [k for k, r in _results.items() if r["_at"] < old]:
        _results.pop(rid, None)
    while len(_results) > KEEP:
        _results.popitem(last=False)


def who_label(who: str) -> str:
    """keelbot → KeelBot, agent:<id> → the agent <id>, anything else as it is (an MCP client's name)."""
    w = str(who or "").strip()
    if w == "keelbot":
        return "KeelBot"
    if w.startswith("agent:"):
        return f"the agent {w[6:]}"
    if w.startswith("step:"):
        return f"the workflow step {w[5:]}"
    return w or "an agent"


def keep(project: str, conn: core.Conn, sql: str, who: str, *, result: dict | None = None,
         error: core.DbError | None = None, thread: str | None = None) -> dict:
    """Keep one result (or the error the database gave) for the person; returns it, rows and all."""
    at = _now()
    entry = {
        "id": "r_" + secrets.token_hex(6),
        "project": project or "",
        "connection": conn.name,
        "env": conn.env,
        "kind": conn.kind,
        "sql": (result or {}).get("sql") or (sql or "").strip(),
        "who": who_label(who),
        "thread": thread or None,
        "at": at.isoformat(timespec="seconds"),
        "_at": at,
    }
    if result is not None:
        entry.update(columns=result["columns"], rows=result["rows"], count=result["count"],
                     truncated=bool(result.get("truncated")), masked=result.get("masked") or [], ms=result.get("ms"))
    if error is not None:
        entry.update(error=str(error), hint=getattr(error, "hint", "") or "", columns=[], rows=[], count=0,
                     truncated=False, masked=[], _safe=bool(getattr(error, "safe", False)))
    with _lock:
        _results[entry["id"]] = entry
        _sweep()
    return entry


def run(conn: core.Conn, sql: str, *, root: str, project: str, who: str, thread: str | None = None) -> dict:
    """Run one read for the person and keep what came back (rows or the database's error). A query keel refuses
    before it reaches the database (a change, a dangerous function) raises: its message is keel's own, and safe."""
    try:
        r = core.query(conn, sql, root=root, mask=True, limit=ROWS, max_rows=ROWS)
    except core.DbError as exc:
        if not exc.from_db:
            raise              # keel's own refusal (not a read, a dangerous function, a timeout, no connection): safe
        return keep(project, conn, sql, who, error=exc, thread=thread)
    return keep(project, conn, sql, who, result=r, thread=thread)


def public(entry: dict, *, rows: bool = True) -> dict:
    """What the person's page gets: the result without keel's own fields; `rows=False` for the list."""
    out = {k: v for k, v in entry.items() if not k.startswith("_")}
    if not rows:
        out.pop("rows", None)
    return out


def listed(project: str) -> list[dict]:
    """A project's results, newest first, without their rows."""
    with _lock:
        _sweep()
        return [public(r, rows=False) for r in reversed(_results.values()) if r["project"] == (project or "")]


def one(project: str, rid: str) -> dict:
    with _lock:
        _sweep()
        r = _results.get(rid)
    if not r or r["project"] != (project or ""):
        raise core.PluginError(404, "This result is gone.",
                               "keel keeps private results in memory for a day, at most 100, and forgets them when it restarts.")
    return public(r)


def forget(project: str, rid: str) -> bool:
    with _lock:
        r = _results.get(rid)
        if r and r["project"] == (project or ""):
            _results.pop(rid, None)
            return True
    return False


def clear() -> None:
    """Tests: forget every result."""
    with _lock:
        _results.clear()


HIDDEN_ERROR = ("The database refused the query. Its message can quote values from the data, so keel keeps it for the "
                "person only. Check the schema (db_schema) and your SQL, then try again.")


def agent_answer(entry: dict) -> dict:
    """What an agent may know about a result: its id, the connection, its column names, and whether it worked (with
    the database's message only when it names nothing from the data, core.sql_error)."""
    out = {"private": True, "id": entry["id"], "connection": entry["connection"], "columns": list(entry.get("columns") or [])}
    if entry.get("error"):
        out.update(failed=True, error=entry["error"] if entry.get("_safe") else HIDDEN_ERROR)
    return out


def agent_text(answer: dict, *, where: str, keelbot: bool = False) -> str:
    """The text a model reads for a private result: never a row, a count or a value."""
    rid, conn = answer["id"], answer["connection"]
    if answer.get("failed"):
        return f"Result {rid} on {conn} failed. {answer.get('error') or HIDDEN_ERROR}"
    cols = ", ".join(answer.get("columns") or []) or "(none)"
    see = f"They see it {where}"
    if not see.split()[-1].startswith("http"):           # a link stays clickable: no full stop after it
        see += "."
    lines = [f"Done: result {rid} from {conn}, columns: {cols}.",
             f"You do not see its rows: on {conn} the results go only to the person. {see}",
             "Tell them in one line what the query shows. If they want you to work with some of it, they will give "
             "you the rows they choose. Do not guess the data."]
    if keelbot:
        lines.append(f'Show it to them with this block in your answer:\n```keel-result\n{{"id": "{rid}"}}\n```')
    return "\n".join(lines)
