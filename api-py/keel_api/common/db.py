"""keel's SQLite database ($KEEL_DATA/keel.db), shared with the Kotlin api while it lives (common/AppConfig.kt).

Plain SQL like the Kotlin api's JdbcTemplate. WAL, so readers do not block writers, and a 10 s busy timeout, so two
processes writing at once wait for each other instead of failing. The schema is still the Kotlin api's (Flyway): this
module never creates or changes a table.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


class Db:
    def __init__(self, file: Path):
        self.file = file
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.file, timeout=10, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=10000")
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def query(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, tuple(args)).fetchone()

    def update(self, sql: str, args: Iterable[Any] = ()) -> int:
        """Runs one change; returns how many rows it changed (JdbcTemplate.update)."""
        return self._conn().execute(sql, tuple(args)).rowcount

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None
