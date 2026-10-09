"""The KeelBot plugin's engine tests: keel_plugin_keelbot loaded into a real engine as the image loads it
(KEEL_PLUGIN_PATHS and KEEL_PLUGIN_ADDONS, as `keel-engine plugins resolve` writes them), on the fake model. Run from
engine/:

    PYTHONPATH=../plugins/keelbot/engine uv run pytest ../plugins/keelbot/engine/tests      (scripts/test-plugins.sh)
"""

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

ENGINE = str(Path(__file__).resolve().parents[1])          # plugins/keelbot/engine: the folder that holds keel_plugin_keelbot


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("KEEL_FAKE", "1")
    monkeypatch.setenv("KEEL_API_URL", "off")
    monkeypatch.setenv("KEEL_DEMO", "0")
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", ENGINE)
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_keelbot")
    monkeypatch.delenv("KEEL_ADDONS", raising=False)
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("KEEL_FAKE_DELAY", raising=False)
    usage.reset()                     # plan windows live in memory: one test's must not pause the next
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


def to_loop(client, tid, s=None, upto="contract_gate"):
    """The feature flow up to its criteria loop: approve the spec gate, the optional-phases menu and the contract gate
    (stop after `upto`). Returns the state at the next pause."""
    s = s or wait(client, tid)
    for step in ("spec_gate", "options", "contract_gate"):
        assert s.get("waiting", {}).get("step") == step, s
        s = decide(client, tid)
        if step == upto:
            break
    return s
