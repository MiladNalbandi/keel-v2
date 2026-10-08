"""A plugin's MCP server for KeelBot and agents: `python -m keel_engine.plugins.server db|git`, over stdio.

Its tools only read. Each call goes back to the engine with the agent call's key (keel_engine/partmcp.py), so no secret
is in this process or its config. The CI/CD plugin's server moved with it (plugins/ci, keel_plugin_ci.server).
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
    if plugin == "git":
        srv = FastMCP("keel-git", log_level="WARNING", instructions=(
            "git and the pull request, read only. A commit, push, pull request or branch switch is never run here: give "
            "it to the person as a keel-git block."))

        @srv.tool(annotations=ro, structured_output=False)
        def git_status() -> str:
            """The branch, how far it is from the base branch and its remote, and the uncommitted changes."""
            return _call("git_status")

        @srv.tool(annotations=ro, structured_output=False)
        def git_diff(path: str = "", staged: bool = False, against_base: bool = False) -> str:
            """The diff of the uncommitted changes (or only the staged ones, or the whole branch against its base),
            for one file or all; at most 40k characters."""
            return _call("git_diff", path=path, staged=staged, against_base=against_base)

        @srv.tool(annotations=ro, structured_output=False)
        def git_log(n: int = 20, path: str = "", range: str = "") -> str:
            """Recent commits (sha, date, author, subject), for one file or a range like main..HEAD."""
            return _call("git_log", n=n, path=path, range=range)

        @srv.tool(annotations=ro, structured_output=False)
        def git_show(sha: str) -> str:
            """One commit: its message, files and diff."""
            return _call("git_show", sha=sha)

        @srv.tool(annotations=ro, structured_output=False)
        def git_blame(path: str, start: int = 1, end: int = 0) -> str:
            """Who last changed these lines of a file, and in which commit."""
            return _call("git_blame", path=path, start=start, end=end)

        @srv.tool(annotations=ro, structured_output=False)
        def git_branches() -> str:
            """The local branches, newest first, with how far each is from the base branch."""
            return _call("git_branches")

        @srv.tool(annotations=ro, structured_output=False)
        def pr_status() -> str:
            """The branch's pull request on GitHub: state, review, CI checks and the review comments."""
            return _call("pr_status")

        return srv
    raise SystemExit(f"unknown plugin {plugin!r} (db or git)")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "").run("stdio")
