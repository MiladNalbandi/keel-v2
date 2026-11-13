"""The Database plugin's MCP server for KeelBot and agents: `python -m keel_plugin_db.server`, over stdio (the part's
"mcp" in __init__.py; keel_engine/extensions.py server_specs starts it with this plugin's folder on PYTHONPATH).

Its tools read; db_change acts only after the person's OK in keel's Inbox (and only where the connection's Access
allows it). Each call goes back to the engine with the agent call's key (keel_engine/partmcp.py), so no secret
is in this process or its config: no connection, no password. The answers come from tools.py, in the engine.
"""

from __future__ import annotations

from keel_engine.partmcp import call as _call


def build():
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    srv = FastMCP("keel-db", log_level="WARNING", instructions=(
        "The project's databases. Look at the schema before you write a query. A change of data runs only with db_change "
        "on a connection that allows it, after the person's OK in keel's Inbox; else give it to the person as a "
        "keel-query block."))

    @srv.tool(annotations=ro, structured_output=False)
    def db_connections() -> str:
        """The project's database connections: name, kind (PostgreSQL, MySQL, SQLite) and whether it is local, test,
        staging or prod."""
        return _call("db_connections")

    @srv.tool(annotations=ro, structured_output=False)
    def db_schema(connection: str = "", table: str = "") -> str:
        """The tables with their columns, primary keys (*) and foreign keys; with `table`, one table in detail.
        `connection` defaults to the local one."""
        return _call("db_schema", connection=connection, table=table)

    @srv.tool(annotations=ro, structured_output=False)
    def db_query(sql: str, connection: str = "", limit: int = 200) -> str:
        """Run one read-only SQL query (SELECT, EXPLAIN, SHOW) and get the rows as a table: at most 200 rows and
        15 seconds; columns named like a secret show as •••. A change of data is refused here. On a connection whose
        results go only to the person (db_connections says so) you get a result id and the columns, not the rows."""
        return _call("db_query", sql=sql, connection=connection, limit=limit)

    rx = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)

    @srv.tool(annotations=rx, structured_output=False)
    def db_change(sql: str, connection: str = "") -> str:
        """Change data (one INSERT, UPDATE or DELETE) on a connection whose Access allows "change after approval" (see
        db_connections): keel counts the rows, asks the person in keel's Inbox, and runs it only after their Yes. Never
        on staging or prod. A schema change is refused: it belongs in a migration."""
        return _call("db_change", sql=sql, connection=connection)

    return srv


if __name__ == "__main__":
    build().run("stdio")
