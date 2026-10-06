"""Synchronous access to the engine DB ($KEEL_DATA/checkpoints.db), for code that runs in a worker thread (code-step
actions, the scan job). The engine's own aiosqlite connection uses the same file in WAL mode; writes here are short."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from . import migrate


def path() -> Path:
    return config.data_dir() / "checkpoints.db"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
            for sql in migrate.MIGRATIONS:
                conn.execute(sql)
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
