"""The Database plugin's engine part (plugins/db): its workflow steps (actions.py), its read tools for agents (tools.py,
the keel-db server in server.py), its routes (routes.py), its tools in keel2 mcp (mcp_tools.py), and what KeelBot and
"explain a step" say about it. Its code loads only when it is used: this module stays light (the guard's hook reads
the read tools on each tool call). Its Python libraries (sqlglot, psycopg, pymysql) are in keel's engine.

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_db, written by `keel-engine plugins resolve`): ADDON says who
it is and where its content is, PART holds the keys it had as a built-in part (keel_engine/extensions.py). Its content
is the per-project plugin of Tools › Plugins: content/plugins/db/plugin.yml (commands, tools, steps).

    repo:    plugins/db/engine/keel_plugin_db/   ->  plugins/db/content
    plugin:  <plugin>/engine/keel_plugin_db/     ->  <plugin>/content   (keel-plugin.yml, scripts/build-plugin.sh)
"""

from __future__ import annotations

from pathlib import Path

# the same version as ../../keel-plugin.yml (tests/test_db_part.py checks it)
VERSION = "1.0.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"

PARAMS = {
    "db:query": {"sql": "required", "connection": "optional"},
    "db:check": {"sql": "required", "expect": "optional", "connection": "optional"},
    "db:change": {"sql": "required", "connection": "optional"},
    "db:migrate": {},
}

DOCS = {
    "db:query": {
        "summary": "Database plugin: runs one read query; its rows go into the flow's data under the step's id.",
        "steps": [
            "with: sql (one SELECT, EXPLAIN or SHOW) and connection (default: the project's local database).",
            "Runs in a read-only transaction: at most 200 rows and 15 seconds; columns named like a secret show as •••.",
            "Fails when the plugin is off for the project, the connection is missing, or the SQL is not a read.",
        ],
    },
    "db:check": {
        "summary": "Database plugin: a data check; runs a read query and passes when the rows match what you expect.",
        "steps": [
            "with: sql, expect (none: no row may come back, the default; some; or a number) and connection.",
            "A failed check shows the query and the first 20 rows; with soft: true a branch reads RESULT pass or fail.",
        ],
    },
    "db:change": {
        "summary": "Database plugin: changes data (INSERT, UPDATE, DELETE), only on a local or test database.",
        "steps": [
            "keel runs it once in a transaction it rolls back, to count the rows.",
            "Then it asks you (Run it, or stop the flow), unless the run mode is auto and the database is local.",
            "Schema changes are refused: they belong in the project's migrations (db:migrate).",
        ],
    },
    "db:migrate": {
        "summary": "Database plugin: runs the project's migration command (commands.migrate in .keel/config.yml).",
        "steps": ["The command goes through keel's shell guard first; a nonzero exit fails the step."],
    },
}

KEELBOT = """The Database plugin is on. Your tools db_connections, db_schema and db_query only read. For a data question, \
look at the schema first, then run one query, and show the rows. You never change data yourself: a change (INSERT, \
UPDATE, DELETE) is a button the person presses. keel counts the rows it would change first, and runs it only on a \
local or test database:
```keel-query
{"sql": "UPDATE scores SET value = 0 WHERE value IS NULL", "connection": "local"}
```
Use the connection names db_connections gives. A schema change is never a button: it belongs in a migration."""

KEELBOT_ACTIONS = [
    "db:query    {sql, connection?}: a read; its rows go to data.<step id>",
    "db:check    {sql, expect: none | some | <n>, connection?}: a data check (soft: true + a branch on RESULT)",
    "db:change   {sql, connection?}: a change of data on a local or test database; keel asks the person first",
    "db:migrate  {}: the project's migration command (commands.migrate in .keel/config.yml)",
]


def _actions() -> dict:
    from .actions import ACTIONS

    return ACTIONS


def _call(c: dict, tool: str, args: dict) -> str:
    from .tools import call

    return call(c, tool, args)


def _router():
    from .routes import router

    return router


def _errors() -> tuple:
    from .core import DbError

    return (DbError,)


def _keel_mcp(server, api, guard, write: bool) -> None:
    from .mcp_tools import register

    register(server, api, guard, write)


ADDON = {
    "name": "db",
    "title": "Database",
    "version": VERSION,
    "content": CONTENT,
}

PART = {
    "name": "db",
    "title": "Database",
    # first in the registry, where it was as a built-in: KeelBot hears about Database, then Git, then CI/CD (keel 0.15.1)
    "order": 10,
    "per_project": True,
    "actions": _actions,
    "params": PARAMS,
    "docs": DOCS,
    "read_tools": ("db_connections", "db_schema", "db_query"),
    "mcp": {"server": "keel-db", "module": "keel_plugin_db.server", "call": _call},
    "router": _router,
    "errors": _errors,
    "keelbot": {"prompt": KEELBOT, "actions": KEELBOT_ACTIONS},
    "keel_mcp": _keel_mcp,
}
