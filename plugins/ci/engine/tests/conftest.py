"""The CI/CD plugin's engine tests: keel_plugin_ci loaded into a real engine as the image loads it (KEEL_PLUGIN_PATHS and
KEEL_PLUGIN_ADDONS, as `keel-engine plugins resolve` writes them), on the fake model. Run from engine/:

    PYTHONPATH=../plugins/ci/engine uv run pytest ../plugins/ci/engine/tests      (scripts/test-plugins.sh)
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

ENGINE = str(Path(__file__).resolve().parents[1])          # plugins/ci/engine: the folder that holds keel_plugin_ci
# CI/CD needs Git (keel-plugin.yml requires.plugins): the resolver puts Git's engine first, as the image does
GIT_ENGINE = str(Path(__file__).resolve().parents[3] / "git" / "engine")
# KeelBot too (plugins/keelbot): the tests check what it is told about this part, as the image has it
KEELBOT_ENGINE = str(Path(__file__).resolve().parents[3] / "keelbot" / "engine")


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("KEEL_FAKE", "1")
    monkeypatch.setenv("KEEL_API_URL", "off")
    monkeypatch.setenv("KEEL_DEMO", "0")
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", f"{KEELBOT_ENGINE}:{GIT_ENGINE}:{ENGINE}")
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_keelbot,keel_plugin_git,keel_plugin_ci")
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
