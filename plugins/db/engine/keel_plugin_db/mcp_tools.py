"""The Database plugin's tools in keel's own MCP server (keel2 mcp, keel_engine/mcp_server.py): through keel's api,
under keel's rules; the acting one waits for the person's answer in keel's Inbox.

#96 on a connection whose Access is private, or when Settings › Databases keeps MCP clients to "results only to you",
the api answers a query with a result id: the client gets a link to it, never the rows."""

from __future__ import annotations

import os
import time
from urllib.parse import quote

from mcp.server.fastmcp import Context
from keel_engine.mcp_server import KeelApi, _ask_person, _plugin_on, _project, inbox_refusal


ACCESS = {"none": "not offered to agents", "private": "reads whose rows go only to the person (you get a result id)",
          "read": "read only", "write_approve": "read; a change after the person's OK"}


def db_connections(api: KeelApi, project: str | None = None) -> str:
    """The connections the project uses, with where each comes from (its own or shared with every project) and what an
    agent may do there. The api leaves out those agents may not use."""
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "db")
    rows = [d for d in api.get(f"/projects/{pid}/databases") if not d.get("overridden") and not d.get("hidden")]
    if not rows:
        return "This project has no database connection for agents (Databases)."
    label = {"postgres": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB", "sqlite": "SQLite"}
    out = []
    for d in rows:
        scope = "shared with every project" if d.get("source") == "global" else "this project's own"
        out.append(f"- {d['name']}: {label.get(d.get('driver'), d.get('driver'))}, {d.get('env')}, {scope}, "
                   f"{ACCESS.get(d.get('agent_access_effective'), 'read only')}")
    return "\n".join(out)


def db_schema(api: KeelApi, project: str | None = None, connection: str | None = None, table: str | None = None) -> str:
    from .tools import _schema_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "db")
    return _schema_text(api.get(f"/projects/{pid}/db/schema", connection=connection), table or "")


def _as_tool(api: KeelApi, tool: str, client: str | None = None) -> KeelApi:
    """The api for one read tool's call, saying who asks (X-Keel-Client, X-Keel-Tool): keel's audit records a query by
    its tool and its SQL at every level (api McpAudit, keel 0.18.55)."""
    acting = getattr(api, "acting", None)
    return acting(client or getattr(api, "client", "an MCP client"), tool) if acting else api


def _client_of(ctx) -> str | None:
    try:
        params = ctx.session.client_params if ctx else None
        return params.clientInfo.name if params and params.clientInfo else None
    except Exception:  # noqa: BLE001 - only a label
        return None


def result_url(api: KeelApi, pid: str, rid: str) -> str:
    """The person's page for one private result: KEEL_PUBLIC_URL (keel2 mcp sets it), else the api's address."""
    base = (os.environ.get("KEEL_PUBLIC_URL") or api.url).rstrip("/")
    return f"{base}/#/projects/{quote(pid, safe='')}/settings/databases/results/{quote(rid, safe='')}"


def db_query(api: KeelApi, sql: str, project: str | None = None, connection: str | None = None) -> str:
    from . import private
    from .tools import query_text

    api = _as_tool(api, "keel_db_query")
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "db")
    r = api.post(f"/projects/{pid}/db/query", {"sql": sql, "connection": connection or "", "mask": True})
    if r.get("private"):
        return private.agent_text(r, where=f"in keel, at this link (give it to them):\n{result_url(api, pid, r['id'])}")
    return query_text(r)


def db_change(api: KeelApi, sql: str, project: str | None = None, connection: str | None = None, sleep=time.sleep) -> str:
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "db")
    dry = api.post(f"/projects/{pid}/db/query", {"sql": sql, "connection": connection or "", "change": True})
    ok, why = _ask_person(api, pid, f"Claude Code: change {dry['changed']} row(s) in {dry['connection']}?", dry["sql"], sleep)
    if not ok:
        return inbox_refusal(why)
    r = api.post(f"/projects/{pid}/db/query", {"sql": sql, "connection": connection or "", "change": True, "confirm": True})
    return f"{r['changed']} row(s) changed in {r['connection']}."



def _acting(srv, guard):
    """keel's way to run an acting tool (keel 0.16.1: who acts, and with `confirm` the person's OK through the client
    first; PartServer.act in keel_engine/mcp_server.py). A keel before 0.16.1 has none: then the tool runs as it did."""
    act = getattr(srv, "act", None)

    async def run(ctx, name: str, what: str, fn, *a, confirm: bool = False, **kw) -> str:
        if act is None:
            return guard(fn, *a, **kw)
        return await act(ctx, name, what, fn, *a, confirm=confirm, **kw)

    return run


def register(srv, api: KeelApi, guard, write: bool) -> None:
    """The read tools, or (write) the acting one."""
    from mcp.types import ToolAnnotations

    if not write:
        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_db_connections(project: str | None = None) -> str:
            """Database plugin: the project's database connections (its own and the ones shared with every project), with
            the kind of database, local/test/staging/prod and what an agent may do there."""
            return guard(db_connections, api, project)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_db_schema(project: str | None = None, connection: str | None = None, table: str | None = None) -> str:
            """Database plugin: the project's tables with columns, primary keys (*) and foreign keys; with `table`, one table."""
            return guard(db_schema, api, project, connection, table)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_db_query(sql: str, project: str | None = None, connection: str | None = None,
                          ctx: Context | None = None) -> str:
            """Database plugin: one read-only query (SELECT, EXPLAIN, SHOW) under keel's rules: at most 200 rows and 15
            seconds; columns that hold a secret show as •••. Where the person keeps results to themselves, you get a
            result id and a link for them, not the rows."""
            return guard(db_query, _as_tool(api, "keel_db_query", _client_of(ctx)), sql, project, connection)

        return
    rx = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
    act = _acting(srv, guard)

    @srv.tool(annotations=rx, structured_output=False)
    async def keel_db_change(sql: str, project: str | None = None, connection: str | None = None, ctx: Context | None = None) -> str:
        """Database plugin: change data (INSERT, UPDATE, DELETE) on a local or test database. keel counts the rows,
        then waits up to 10 minutes for the person's OK in keel's Inbox."""
        return await act(ctx, "keel_db_change", "change data in the database", db_change, api, sql, project, connection,
                         confirm=True)
