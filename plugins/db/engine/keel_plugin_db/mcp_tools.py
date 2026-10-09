"""The Database plugin's tools in keel's own MCP server (keel2 mcp, keel_engine/mcp_server.py): through keel's api,
under keel's rules; the acting one waits for the person's answer in keel's Inbox."""

from __future__ import annotations

import time

from keel_engine.mcp_server import KeelApi, _ask_person, _plugin_on, _project


def db_schema(api: KeelApi, project: str | None = None, connection: str | None = None, table: str | None = None) -> str:
    from .tools import _schema_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "db")
    return _schema_text(api.get(f"/projects/{pid}/db/schema", connection=connection), table or "")


def db_query(api: KeelApi, sql: str, project: str | None = None, connection: str | None = None) -> str:
    from .tools import query_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "db")
    return query_text(api.post(f"/projects/{pid}/db/query", {"sql": sql, "connection": connection or "", "mask": True}))


def db_change(api: KeelApi, sql: str, project: str | None = None, connection: str | None = None, sleep=time.sleep) -> str:
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "db")
    dry = api.post(f"/projects/{pid}/db/query", {"sql": sql, "connection": connection or "", "change": True})
    ok, why = _ask_person(api, pid, f"Claude Code: change {dry['changed']} row(s) in {dry['connection']}?", dry["sql"], sleep)
    if not ok:
        return f"The person said no in keel's Inbox: {why}".strip()
    r = api.post(f"/projects/{pid}/db/query", {"sql": sql, "connection": connection or "", "change": True, "confirm": True})
    return f"{r['changed']} row(s) changed in {r['connection']}."


def register(srv, api: KeelApi, guard, write: bool) -> None:
    """The read tools, or (write) the acting one."""
    from mcp.types import ToolAnnotations

    if not write:
        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_db_schema(project: str | None = None, connection: str | None = None, table: str | None = None) -> str:
            """Database plugin: the project's tables with columns, primary keys (*) and foreign keys; with `table`, one table."""
            return guard(db_schema, api, project, connection, table)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_db_query(sql: str, project: str | None = None, connection: str | None = None) -> str:
            """Database plugin: one read-only query (SELECT, EXPLAIN, SHOW) under keel's rules: at most 200 rows and 15
            seconds; columns named like a secret show as •••."""
            return guard(db_query, api, sql, project, connection)

        return
    rw = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

    @srv.tool(annotations=rw, structured_output=False)
    def keel_db_change(sql: str, project: str | None = None, connection: str | None = None) -> str:
        """Database plugin: change data (INSERT, UPDATE, DELETE) on a local or test database. keel counts the rows,
        then waits up to 10 minutes for the person's OK in keel's Inbox."""
        return guard(db_change, api, sql, project, connection)
