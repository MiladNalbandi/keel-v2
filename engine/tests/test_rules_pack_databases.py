"""#96 the rule pack "Databases only through keel" (content/rules/databases-only-through-keel.yaml): agents reach a
database only through keel's Database tools, where each connection's Access decides what they see. It refuses database
clients and connection scripts in the shell, database files and dumps, and other database MCP servers, and leaves the
everyday commands alone."""

from pathlib import Path

import pytest

from keel_engine import rules
from keel_engine.rules import packs

PACK_FILE = Path(__file__).resolve().parents[2] / "content" / "rules" / "databases-only-through-keel.yaml"


@pytest.fixture(scope="module")
def pack():
    p, errors = packs.parse_pack(PACK_FILE.read_text())
    assert not errors, errors
    return p


def refused(pack, kind, target):
    return rules.pack_verdict({}, kind, "green", target, packs=[{**pack, "id": pack["name"]}])


@pytest.mark.parametrize("cmd", [
    "psql -U app -d shop -c 'select * from users'",
    "PGPASSWORD=x psql -h db.internal shop",
    "cd api && mysql -u root -e 'select 1'",
    "sudo -u postgres pg_dump shop > shop.sql",
    "env PGHOST=db /usr/bin/psql shop",
    "docker compose exec db psql -U app",
    "kubectl exec -it pg-0 -- psql -U app",
    "echo hi; sqlite3 app.db 'select * from users'",
    "x=$(mysql -N -e 'select count(*) from users')",
    "redis-cli -h cache keys '*'",
    "mongosh mongodb://db/shop --eval 'db.users.find()'",
    'python3 -c "import psycopg; print(psycopg.connect(\'dbname=shop\').execute(\'select 1\').fetchall())"',
    "node -e \"require('pg')\"",
])
def test_database_clients_and_scripts_in_the_shell_are_refused(pack, cmd):
    v = refused(pack, "bash", cmd)
    assert v is not None and "goes around keel's Database tools" in v.reason, cmd


@pytest.mark.parametrize("cmd", [
    "grep -r psql docs", "npm install pg", "pip install psycopg", "cat migrations/V1__init.sql", "pytest -q",
    "echo mysql", "python -c 'print(1)'", "git log --oneline", "ls db/", "./gradlew flywayMigrate",
])
def test_everyday_commands_pass(pack, cmd):
    assert refused(pack, "bash", cmd) is None, cmd


def test_database_files_and_dumps_are_not_read_but_migrations_are(pack):
    for path in ("app.db", "data/shop.sqlite3", "backups/prod-dump.sql", "backups/shop.sql.gz", "tmp/users.dump"):
        assert refused(pack, "read", path) is not None, path
    for path in ("migrations/V1__init.sql", "src/db/schema.sql", "README.md", "src/db.ts"):
        assert refused(pack, "read", path) is None, path


def test_other_database_mcp_servers_are_refused_but_keels_own_tools_pass(pack):
    for name in ("mcp__postgres__query", "mcp__keel__supabase__execute_sql", "mcp__mysql-mcp__run_query",
                 "mcp__dbhub__execute_sql", "mcp__anything__execute_sql"):
        assert refused(pack, "mcp", name) is not None, name
    for name in ("mcp__keel-db__db_query", "mcp__keel__keel_db_query", "mcp__github__search_issues", "mcp__keel__keel_status"):
        assert refused(pack, "mcp", name) is None, name


def test_it_is_in_keels_library(client):
    ids = [p["id"] for p in client.get("/rules/library").json()]
    assert "databases-only-through-keel" in ids
