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


def test_the_file_is_wal_whoever_creates_it(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "fresh"))
    (tmp_path / "fresh").mkdir()
    with db.connect() as conn:                  # a short connection creates the file before the engine opens it
        assert conn.execute("pragma journal_mode").fetchone()[0] == "wal"


def test_project_commands_do_not_see_keels_own_python(monkeypatch):
    # Real bug: in the image keel's venv was first on PATH, so a project's `python -m pytest` ran keel's Python (no
    # pytest), and UV_PROJECT_ENVIRONMENT pointed a project's `uv sync` at keel's own venv.
    import os
    import sys
    from keel_engine.models.cli import project_env, safe_env
    from keel_engine.tools.agent_tools import command_env
    venv_bin = os.path.join(sys.prefix, "bin")
    monkeypatch.setenv("PATH", os.pathsep.join([venv_bin, "/usr/local/bin", "/usr/bin"]))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/opt/engine/.venv")
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    for env in (command_env(), safe_env()):
        assert "UV_PROJECT_ENVIRONMENT" not in env and "VIRTUAL_ENV" not in env
        if sys.prefix != sys.base_prefix:                 # only a venv's own bin is removed
            assert venv_bin not in env["PATH"].split(os.pathsep)
        assert "/usr/bin" in env["PATH"].split(os.pathsep)
    assert project_env({"PATH": "/usr/bin", "UV_LINK_MODE": "copy"}) == {"PATH": "/usr/bin"}
