"""The Database plugin's engine tests: keel_plugin_db loaded into a real engine as the image loads it (KEEL_PLUGIN_PATHS and
KEEL_PLUGIN_ADDONS, as `keel-engine plugins resolve` writes them), on the fake model. Run from engine/:

    PYTHONPATH=../plugins/db/engine uv run pytest ../plugins/db/engine/tests      (scripts/test-plugins.sh)
"""

import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keel_engine import extensions
from keel_engine.app import create_app
from keel_engine.demo import create_demo
from keel_engine.events import EventBus
from keel_engine.models import usage
from keel_engine.workflows.templates import get_template

ENGINE = str(Path(__file__).resolve().parents[1])          # plugins/db/engine: the folder that holds keel_plugin_db
IDENT = ["-c", "user.name=t", "-c", "user.email=t@t"]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("KEEL_FAKE", "1")
    monkeypatch.setenv("KEEL_API_URL", "off")
    monkeypatch.setenv("KEEL_DEMO", "0")
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", ENGINE)
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_db")
    monkeypatch.delenv("KEEL_ADDONS", raising=False)
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("KEEL_FAKE_DELAY", raising=False)
    usage.reset()
    extensions.reload()
    yield tmp_path
    monkeypatch.delenv("KEEL_PLUGIN_PATHS", raising=False)
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    extensions.reload()


@pytest.fixture
def repo(tmp_path):
    return create_demo(tmp_path / "demo")


@pytest.fixture
def client():
    bus = EventBus()
    with TestClient(create_app(bus)) as c:
        c.bus = bus
        yield c


def git(root, *args):
    return subprocess.run(["git", *IDENT, *args], cwd=root, capture_output=True, text=True)


@pytest.fixture
def scores(repo):
    """The demo repo with a SQLite database: 3 players, 1 score, and a password column."""
    c = sqlite3.connect(repo / "app.db")
    c.executescript("""
        create table players (id integer primary key, name text, password_hash text);
        create table scores (id integer primary key, player_id integer references players(id), value integer);
        insert into players (name, password_hash) values ('Ada', 'h1'), ('Bo', 'h2'), ('Chen', 'h3');
        insert into scores (player_id, value) values (1, 10);""")
    c.commit()
    c.close()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: the database")
    return repo


def conn(env="local", name="local"):
    return {"name": name, "kind": "sqlite", "url": "sqlite:app.db", "env": env}


def keys(*conns, github=None):
    out = {f"db:{c['name']}": json.dumps(c) for c in conns}
    if github:
        out["github"] = github
    return out


def start(client, repo, workflow="feature", **extra):
    wf = get_template(workflow) if isinstance(workflow, str) else workflow
    body = {"project_id": "demo", "root": str(repo), "workflow": wf.model_dump(), "title": "Player ranks",
            "models": {"default": {"provider": "fake", "mode": "api", "model": "fake"}},
            "settings": {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}, "mcp": [], "skills": {}}
    body.update(extra)
    r = client.post("/threads", json=body)
    assert r.status_code == 200, r.text
    return r.json()["thread_id"]


def wait(client, tid, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/threads/{tid}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.02)
    raise AssertionError(f"thread {tid} still running: {s}")


def decide(client, tid, decision="approve", why=None, **kw):
    r = client.post(f"/threads/{tid}/resume", json={"decision": decision, "why": why, **kw})
    assert r.status_code == 200, r.text
    return wait(client, tid)
