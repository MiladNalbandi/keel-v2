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


@contextmanager
def connect():
    """A connection whose tables exist (the migrations are idempotent, so a unit test needs no running engine)."""
    conn = sqlite3.connect(str(path()), timeout=10)
    try:
        conn.execute("pragma busy_timeout = 10000")
        for sql in migrate.MIGRATIONS:
            conn.execute(sql)
        yield conn
        conn.commit()
    finally:
        conn.close()


def loads(text: str | None, default=None):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default
