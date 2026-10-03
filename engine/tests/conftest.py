import time

import pytest
from fastapi.testclient import TestClient

from keel_engine.app import create_app
from keel_engine.demo import create_demo
from keel_engine.events import EventBus
from keel_engine.workflows.templates import get_template


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("KEEL_FAKE", "1")
    monkeypatch.setenv("KEEL_API_URL", "off")
    monkeypatch.setenv("KEEL_DEMO", "0")
    monkeypatch.setenv("KEEL_HOME", str(tmp_path / "no-keel-home"))
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("KEEL_FAKE_DELAY", raising=False)
    return tmp_path


@pytest.fixture
def repo(tmp_path):
    return create_demo(tmp_path / "demo")


@pytest.fixture
def client():
    bus = EventBus()
    app = create_app(bus)
    with TestClient(app) as c:
        c.bus = bus
        yield c


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
