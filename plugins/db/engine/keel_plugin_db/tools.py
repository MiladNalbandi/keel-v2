"""The Database plugin's read tools for KeelBot and agents (the MCP server keel-db, server.py). Every answer is text
for a model: a refusal says why instead of failing the call. Columns named like a secret show as •••."""

from __future__ import annotations

from . import core
from .core import PluginError, connections

ROWS_SHOWN = 50
CELL = 200
MAX_TEXT = 12_000
LABEL = {"postgres": "PostgreSQL", "mysql": "MySQL", "sqlite": "SQLite"}


def pick(keys: dict | None, name: str = "") -> core.Conn:
    """The named connection, else the project's local one, else its test one, else the first."""
    conns = connections(keys)
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
    lines = [f"{len(tables)} tables in {sch['connection']} ({LABEL.get(sch['kind'], sch['kind'])}); name(columns, * = primary key) "
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
        head += f"; more rows exist (keel returns at most {core.MAX_ROWS}): add a WHERE or a LIMIT"
    if r.get("masked"):
        head += f"; hidden as •••: {', '.join(r['masked'])}"
    return head + "\n" + table_text(r["columns"], r["rows"])


def call(c: dict, tool: str, args: dict) -> str:
    try:
        if tool == "db_connections":
            conns = connections(c["keys"])
            if not conns:
                return "This project has no database connection yet (Connections › Databases)."
            lines = []
            for d in conns.values():
                conn = core.conn_of(d)
                change = ("data changes: a keel-query button the person presses" if conn.env in core.CHANGE_ENVS
                          else "read only, always")
                lines.append(f"- {conn.name}: {LABEL.get(conn.kind, conn.kind)}, {conn.env} ({change})")
            return "\n".join(lines)
        conn = pick(c["keys"], str(args.get("connection") or ""))
        if tool == "db_schema":
            return _schema_text(core.schema(conn, c["root"]), str(args.get("table") or ""))
        if tool == "db_query":
            return query_text(core.query(conn, str(args.get("sql") or ""), root=c["root"], mask=True,
                                         limit=min(int(args.get("limit") or core.MAX_ROWS), core.MAX_ROWS)))
        return f"Unknown tool {tool}."
    except (core.DbError, PluginError) as exc:
        return f"Refused: {exc}" + (f" {exc.hint}" if getattr(exc, "hint", "") else "")
