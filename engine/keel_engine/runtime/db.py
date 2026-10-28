"""Synchronous access to the engine DB ($KEEL_DATA/checkpoints.db), for code that runs in a worker thread (code-step
actions, the scan job). The engine's own aiosqlite connection uses the same file in WAL mode; writes here are short."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .. import config
from . import migrate


def path() -> Path:
    return config.data_dir() / "checkpoints.db"


from ..clock import utc_iso as now


# How long a writer waits for another one (parallel agents write verdicts, memory and checkpoints at the same time; on
# a slow machine 5-10 s was not enough and a step failed with "database is locked").
BUSY_MS = 30_000
_migrated: set[str] = set()


@contextmanager
def connect():
    """A connection whose tables exist (the migrations are idempotent, so a unit test needs no running engine); they
    run once per database file and process, not on every connection."""
    p = str(path())
    conn = sqlite3.connect(p, timeout=BUSY_MS / 1000)
    try:
        conn.execute(f"pragma busy_timeout = {BUSY_MS}")
        if p not in _migrated:
            # WAL (readers never block a writer) whoever opens the file first: the engine set it only on its own open,
            # and a file a short connection had created first could stay in rollback mode ("database is locked").
            conn.execute("pragma journal_mode=wal")
            for sql in migrate.MIGRATIONS:
                conn.execute(sql)
            # 0.18.19 the columns added after a table first shipped too: a file a short connection made first (a unit
            # test, a worker before the engine's own migrate) had helper_sessions without ctx_tokens ("no such column")
            for table, column, decl in migrate.COLUMNS:
                if column not in {row[1] for row in conn.execute(f"pragma table_info({table})")}:
                    try:
                        conn.execute(f"alter table {table} add column {column} {decl}")
                    except sqlite3.OperationalError as exc:
                        if not migrate.duplicate_column(exc):
                            raise
            conn.commit()
            _migrated.add(p)
        yield conn
        conn.commit()
    finally:
        conn.close()


def loads(text: str | None, default=None):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default
