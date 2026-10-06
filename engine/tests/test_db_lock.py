"""Parallel agents write to the engine DB at the same time: every connection waits for the lock instead of failing."""
import threading
import time

from keel_engine.runtime import db


def test_a_writer_waits_for_another_instead_of_failing(tmp_path, monkeypatch):
    # CI once failed a ship step with "database is locked" while parallel reviewers wrote; the wait is now 30 s.
    monkeypatch.setenv("KEEL_DATA", str(tmp_path))
    with db.connect() as conn:
        assert conn.execute("pragma busy_timeout").fetchone()[0] == db.BUSY_MS
        conn.execute("create table if not exists t (x int)")
    held = threading.Event()

    def hold():
        with db.connect() as c:
            c.execute("begin immediate")
            c.execute("insert into t values (1)")
            held.set()
            time.sleep(1.5)                     # longer than sqlite's old default wait would matter here
    th = threading.Thread(target=hold)
    th.start()
    held.wait(5)
    with db.connect() as conn:                  # waits for the other writer, then writes
        conn.execute("insert into t values (2)")
    th.join()
    with db.connect() as conn:
        assert conn.execute("select count(*) from t").fetchone()[0] == 2
