"""A plugin's MCP server for KeelBot and agents: `python -m keel_engine.plugins.server db`, over stdio.

Its tools only read. Each call goes back to the engine with the agent call's key (keel_engine/partmcp.py), so no secret
is in this process or its config. The CI/CD and Git plugins' servers moved with them (plugins/ci, keel_plugin_ci.server;
plugins/git, keel_plugin_git.server).
"""

from __future__ import annotations

import sys

from ..partmcp import call as _call


def build(plugin: str):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    if plugin == "db":
        srv = FastMCP("keel-db", log_level="WARNING", instructions=(
            "The project's database, read only. Look at the schema before you write a query. A change of data is never "
            "run here: give it to the person as a keel-query block."))

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
            15 seconds; columns named like a secret show as •••. A change of data is refused here."""
            return _call("db_query", sql=sql, connection=connection, limit=limit)

        return srv
    raise SystemExit(f"unknown plugin {plugin!r} (db)")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "").run("stdio")
