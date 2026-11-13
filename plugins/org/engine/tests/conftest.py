"""The Org plugin's engine tests: keel_plugin_org loaded into a real engine as the image loads it (KEEL_PLUGIN_PATHS and
KEEL_PLUGIN_ADDONS), on the fake model. The runner starts flows through a starter that calls the engine directly (the
image's runner calls keel's api). Run from engine/:

    PYTHONPATH=../plugins/org/engine uv run pytest ../plugins/org/engine/tests      (scripts/test-plugins.sh org)
"""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keel_engine import extensions
from keel_engine.app import create_app
from keel_engine.demo import create_demo
from keel_engine.events import EventBus
from keel_engine.workflows.model import from_dict
from keel_engine.workflows.templates import get_template

ENGINE = str(Path(__file__).resolve().parents[1])
FAKE = {"default": {"provider": "fake", "mode": "api", "model": "fake"}}
# a team member's flow in these tests: one agent step, no gates (the real small/feature flows stop at their final review)
TINY = {"id": "small", "name": "tiny", "keel_rules": False, "own_branch": False,
        "steps": [{"id": "work", "kind": "agent", "name": "do the part", "agent": "implementer", "phase": "review"}]}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("KEEL_FAKE", "1")
    monkeypatch.setenv("KEEL_API_URL", "off")
    monkeypatch.setenv("KEEL_DEMO", "0")
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", ENGINE)
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_org")
    monkeypatch.delenv("KEEL_ADDONS", raising=False)
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("KEEL_ENGINE_OPEN", "1")
    monkeypatch.delenv("KEEL_FAKE_DELAY", raising=False)
    extensions.reload()
    yield tmp_path
    monkeypatch.delenv("KEEL_PLUGIN_PATHS", raising=False)
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    extensions.reload()


@pytest.fixture
def repo(tmp_path):
    return create_demo(tmp_path / "demo")


@pytest.fixture
def client(repo):
    bus = EventBus()
    app = create_app(bus)
    started: list[dict] = []

    async def starter(project_id: str, body: dict) -> str:
        """What keel's api does on POST /api/projects/{pid}/flows, cut down: the flow on the demo repo, fake models."""
        wid = body["workflow_id"]
        wf = from_dict(TINY) if wid == "small" else get_template(wid)
        assert wf is not None, f"no workflow {wid}"
        started.append({"project_id": project_id, **body})
        settings = {"gates_mode": "every-ac", "cap_tokens": body.get("cap_tokens") or 0, "on_cap": "pause",
                    "run_mode": body.get("run_mode") or "manual"}
        return await app.state.engine.start_thread({
            "project_id": project_id, "root": str(repo), "workflow": wf, "title": body["title"],
            "request": body["request"], "data": body.get("options") or {}, "models": FAKE, "settings": settings,
            "mcp": [], "skills": {}})

    app.state.org_starter = starter
    with TestClient(app) as c:
        c.bus = bus
        c.started = started
        c.app = app
        yield c


def wait_for(fn, timeout=30.0, what="the condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}: {last}")


def mission(client, mid):
    r = client.get(f"/org/missions/{mid}")
    assert r.status_code == 200, r.text
    return r.json()


def wait_status(client, mid, *statuses, timeout=30.0):
    return wait_for(lambda: (lambda v: v if v["mission"]["status"] in statuses else None)(mission(client, mid)),
                    timeout, f"mission {mid} to be {statuses}")


def thread(client, tid):
    return client.get(f"/threads/{tid}").json()


def wait_thread(client, tid, timeout=30.0):
    return wait_for(lambda: (lambda s: s if s["status"] != "running" else None)(thread(client, tid)), timeout, f"thread {tid}")
