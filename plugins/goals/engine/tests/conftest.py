"""The Goals plugin's engine tests: its content loads as a plugin's content does (run/resolved.json under KEEL_DATA, as
keel-start's resolver writes it), and its workflow runs in a real engine on the fake model. Run from engine/:

    uv run pytest ../plugins/goals/engine/tests      (scripts/test-plugins.sh goals)
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keel_engine import extensions
from keel_engine.app import create_app
from keel_engine.demo import create_demo
from keel_engine.events import EventBus
from keel_engine.models import usage

PLUGIN = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    run = data / "plugins" / "run"
    run.mkdir(parents=True)
    (run / "resolved.json").write_text(json.dumps({"plugins": [
        {"name": "goals", "version": "0.1.0", "source": "file", "dir": str(PLUGIN), "content": str(PLUGIN / "content")}]}))
    monkeypatch.setenv("KEEL_DATA", str(data))
    monkeypatch.setenv("KEEL_FAKE", "1")
    monkeypatch.setenv("KEEL_API_URL", "off")
    monkeypatch.setenv("KEEL_DEMO", "0")
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    monkeypatch.delenv("KEEL_ADDONS", raising=False)
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("KEEL_ENGINE_OPEN", "1")
    monkeypatch.delenv("KEEL_FAKE_DELAY", raising=False)
    usage.reset()
    extensions.reload()
    yield tmp_path


@pytest.fixture
def repo(tmp_path):
    return create_demo(tmp_path / "demo")


@pytest.fixture
def client():
    bus = EventBus()
    with TestClient(create_app(bus)) as c:
        c.bus = bus
        yield c


def wait(client, tid, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/threads/{tid}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.02)
    raise AssertionError(f"thread {tid} still running: {s}")
