"""The Database plugin as a part (keel_engine/extensions.py): its workflow steps (actions.py), its read tools for
agents (tools.py, the keel-db server), its routes (routes.py), its tools in keel2 mcp (mcp_tools.py), and what KeelBot
and "explain a step" say about it. Its code loads only when it is used: this module stays light."""

from __future__ import annotations

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


PART = {
    "name": "db",
    "title": "Database",
    "order": 10,            # first, then Git (20): the order KeelBot, validation and keel2 mcp name them
    "per_project": True,
    "actions": _actions,
    "params": PARAMS,
    "docs": DOCS,
    "read_tools": ("db_connections", "db_schema", "db_query"),
    "mcp": {"server": "keel-db", "module": "keel_engine.plugins.server", "args": ["db"], "call": _call},
    "router": _router,
    "errors": _errors,
    "keelbot": {"prompt": KEELBOT, "actions": KEELBOT_ACTIONS},
    "keel_mcp": _keel_mcp,
}
