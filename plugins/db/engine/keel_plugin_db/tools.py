"""The Database plugin's tools for KeelBot and agents (the MCP server keel-db, server.py). Every answer is text for a
model: a refusal says why instead of failing the call. Columns named like a secret show as •••.

What an agent may do is each connection's Access (keel 0.18.38, the api's agent_access):
    none            the connection is not offered to agents at all (not listed, not found)
    private         db_schema and db_query, but the rows go only to the person (#96, private.py): the agent gets a
                    result id and the column names
    read            db_schema and db_query, read only (keel 0.18.37's way)
    write_approve   and db_change: keel counts the rows, asks the person in the Inbox, and runs it only after Yes
staging and prod are read only for agents, always (Conn.agent_may). With ask_first, an agent's first call on the
connection in a flow (or a KeelBot chat) asks the person once.
"""

from __future__ import annotations

import threading

from . import core, private
from .core import PluginError, connections

ROWS_SHOWN = 50
CELL = 200
MAX_TEXT = 12_000
LABEL = {"postgres": "PostgreSQL", "mysql": "MySQL", "sqlite": "SQLite"}


def pick(keys: dict | None, name: str = "", *, agent: bool = False) -> core.Conn:
    """The named connection, else the project's local one, else its test one, else the first. For an agent, only the
    connections it may use (Access: not none)."""
    conns = connections(keys)
    if agent:
        conns = {k: d for k, d in conns.items() if str(d.get("agent_access") or "read") != "none"}
    if not conns:
        raise PluginError(400, "This project has no database connection.", "Add one in Connections › Databases.")
    if name:
        if name not in conns:
            raise PluginError(404, f"There is no connection called {name}.", f"Known: {', '.join(sorted(conns))}.")
        return core.conn_of(conns[name])
    for env in ("local", "test"):
        for d in conns.values():
            if (d.get("env") or "local") == env:
                return core.conn_of(d)
    return core.conn_of(next(iter(conns.values())))


def _limit(v) -> int:
    """An agent's `limit`: a number from 1 to MAX_ROWS; anything else is MAX_ROWS."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return core.MAX_ROWS
    return max(1, min(n, core.MAX_ROWS))


def _cell(v) -> str:
    s = "NULL" if v is None else str(v)
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= CELL else s[:CELL] + "…"


def table_text(cols: list[str], rows: list[list], limit: int = ROWS_SHOWN) -> str:
    if not cols:
        return "(no columns)"
    out = ["| " + " | ".join(_cell(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(_cell(v) for v in r) + " |" for r in rows[:limit]]
    if len(rows) > limit:
        out.append(f"(+{len(rows) - limit} more rows)")
    return "\n".join(out)


def _schema_text(sch: dict, table: str = "") -> str:
    tables = sch["tables"]
    if table:
        t = next((x for x in tables if x["name"].lower() == table.lower() or x["name"].lower().endswith("." + table.lower())), None)
        if not t:
            return f"There is no table {table}. Tables: {', '.join(x['name'] for x in tables[:80])}"
        lines = [f"Table {t['name']}:"]
        for c in t["columns"]:
            bits = [c["type"] or "?"] + (["primary key"] if c["pk"] else []) + ([] if c["nullable"] else ["not null"])
            lines.append(f"- {c['name']}: {', '.join(bits)}")
        lines += [f"- foreign key {f['column']} → {f['table']}.{f['ref']}" for f in t["fks"]]
        return "\n".join(lines)
    count = (f"showing {len(tables)} of {sch['total']} tables (the project's schema first)" if sch.get("truncated")
             else f"{len(tables)} tables")
    lines = [f"{count} in {sch['connection']} ({LABEL.get(sch['kind'], sch['kind'])}); name(columns, * = primary key) "
             "and foreign keys:"]
    for t in tables:
        cols = ", ".join(c["name"] + ("*" if c["pk"] else "") for c in t["columns"])
        fks = "; ".join(f"{f['column']} → {f['table']}.{f['ref']}" for f in t["fks"])
        lines.append(f"- {t['name']}({cols})" + (f" fk: {fks}" if fks else ""))
    text = "\n".join(lines)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n… (ask for one table by name)"


def query_text(r: dict) -> str:
    head = f"{r['count']} row{'' if r['count'] == 1 else 's'} from {r['connection']} ({r['ms']} ms)"
    if r.get("truncated"):
        head += f"; more rows exist (keel returned at most {r.get('limit') or core.MAX_ROWS}): add a WHERE or a LIMIT"
    if r.get("masked"):
        head += f"; hidden as •••: {', '.join(r['masked'])}"
    return head + "\n" + table_text(r["columns"], r["rows"])


# ask_first: the answers already given, per flow (or KeelBot chat, or agent call) and connection: {(scope, name): ok}
_first: dict[tuple[str, str], bool] = {}
_first_lock = threading.Lock()


def _ask(c: dict, title: str, command: str) -> tuple[bool, str]:
    """Asks the person in keel's Inbox (keel_engine/approvals.py) and waits: (yes, why). Without a way to ask (an older
    caller gave no event bus), the answer is no."""
    bus = c.get("bus")
    if bus is None:
        return False, "keel cannot ask the person from here."
    from keel_engine import approvals

    broker = approvals.of(bus)
    q = broker.open("plugin", c.get("project") or "", title, command, source="plugin:db", thread_id=c.get("thread"),
                    session=c.get("session"))
    ans = broker.wait_blocking(q["id"])
    return ans.get("decision") == "allow", ans.get("why") or ""


def _first_ok(c: dict, conn: core.Conn) -> str | None:
    """ask_first: the first call on this connection in this flow asks the person once. None: go on; else the refusal."""
    if not conn.ask_first:
        return None
    scope = str(c.get("thread") or c.get("session") or c.get("key") or "")
    with _first_lock:
        known = _first.get((scope, conn.name))
    if known is None:
        who = str(c.get("who") or "an agent").replace("agent:", "the agent ")
        known, _why = _ask(c, f"May {who} query {conn.name} ({conn.env})?",
                           f"{who} wants to read the database {conn.name} ({conn.env}) in this flow. keel asks once.")
        with _first_lock:
            _first[(scope, conn.name)] = known
            while len(_first) > 2000:
                _first.pop(next(iter(_first)))
    return None if known else f"Refused: the person said no to queries on {conn.name} in this flow."


def _change(c: dict, conn: core.Conn, sql: str) -> str:
    """write_approve: count the rows (a run keel rolls back), ask the person in the Inbox, run it only after Yes."""
    if conn.agent_may != "write_approve":
        why = (f"On {conn.name} agents only run reads, and the results go to the person." if conn.agent_may == "private"
               else f"{conn.name} is a {conn.env} database: agents only read there, always." if conn.env not in core.CHANGE_ENVS
               else f"Agents may only read {conn.name}.")
        return (f"Refused: {why} Give the change to the person as a keel-query block instead "
                "(they press its button), or ask them to allow \"change after approval\" in Databases.")
    dry = core.query(conn, sql, root=c["root"], allow_change=True)
    ok, why = _ask(c, f"Change {dry['changed']} row(s) in {conn.name}?", dry["sql"])
    if not ok:
        return f"Not run: the person said no in keel's Inbox. {why}".strip()
    done = core.query(conn, sql, root=c["root"], allow_change=True, confirm=True)
    return f"{done['changed']} row(s) changed in {conn.name} after the person's OK."


PLACE = "Settings › Databases › Results for you"
WHERE = f"in keel ({PLACE})"


def _private_query(c: dict, conn: core.Conn, sql: str) -> str:
    """Access private: run the read for the person; the agent reads only the result's id and its columns."""
    entry = private.run(conn, sql, root=c["root"], project=str(c.get("project") or ""), who=str(c.get("who") or ""),
                        thread=c.get("thread"))
    keelbot = c.get("who") == "keelbot"
    return private.agent_text(private.agent_answer(entry), keelbot=keelbot,
                              where="in the card under your answer" if keelbot else WHERE)


def _access_line(conn: core.Conn) -> str:
    may = conn.agent_may
    if may == "private":
        return "you run reads, but the rows go only to the person: you get a result id and the column names"
    if may == "write_approve":
        return "you read; a change with db_change waits for the person's OK in the Inbox"
    if conn.env in core.CHANGE_ENVS:
        return "you read; a change is a keel-query button the person presses"
    return "read only, always"


def call(c: dict, tool: str, args: dict) -> str:
    try:
        if tool == "db_connections":
            conns = [d for d in connections(c["keys"]).values() if str(d.get("agent_access") or "read") != "none"]
            if not conns:
                return "This project has no database connection for agents yet (Databases)."
            lines = []
            for d in conns:
                if not d.get("url"):
                    lines.append(f"- {d.get('name')}: not reachable now ({d.get('unavailable')})")
                    continue
                conn = core.conn_of(d)
                scope = "shared with every project" if conn.scope == "global" else "this project's own"
                ask = "; the person is asked before your first query" if conn.ask_first else ""
                lines.append(f"- {conn.name}: {LABEL.get(conn.kind, conn.kind)}, {conn.env}, {scope} ({_access_line(conn)}{ask})")
            return "\n".join(lines)
        conn = pick(c["keys"], str(args.get("connection") or ""), agent=True)
        refused = _first_ok(c, conn)
        if refused:
            return refused
        if tool == "db_schema":
            return _schema_text(core.schema(conn, c["root"]), str(args.get("table") or ""))
        if tool == "db_query":
            if conn.agent_may == "private":
                return _private_query(c, conn, str(args.get("sql") or ""))
            return query_text(core.query(conn, str(args.get("sql") or ""), root=c["root"], mask=True,
                                         limit=_limit(args.get("limit"))))
        if tool == "db_change":
            return _change(c, conn, str(args.get("sql") or ""))
        return f"Unknown tool {tool}."
    except (core.DbError, PluginError) as exc:
        return f"Refused: {exc}" + (f" {exc.hint}" if getattr(exc, "hint", "") else "")
