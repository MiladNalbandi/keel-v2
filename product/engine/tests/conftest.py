"""keel Product's engine tests: the add-on loaded into a real engine (fake model). Run from engine/:

    KEEL_ADDONS=keel_product PYTHONPATH=../product/engine uv run pytest ../product/engine/tests
"""

import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from keel_engine import addons
from keel_engine.app import create_app
from keel_engine.events import EventBus
from keel_engine.models import usage
from keel_engine.runtime import prompts
from keel_engine.workflows import templates

FAKE = {"provider": "fake", "mode": "api", "model": "fake"}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("KEEL_FAKE", "1")
    monkeypatch.setenv("KEEL_API_URL", "off")
    monkeypatch.setenv("KEEL_DEMO", "0")
    monkeypatch.setenv("KEEL_ADDONS", "keel_product")
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("KEEL_FAKE_DELAY", raising=False)
    usage.reset()
    addons.reload()
    templates.clear_cache()
    prompts._agent_file.cache_clear()
    yield tmp_path
    monkeypatch.delenv("KEEL_ADDONS", raising=False)
    addons.reload()
    templates.clear_cache()


def git_repo(path, files: dict[str, str]):
    path.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "first"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", "-c", "commit.gpgsign=false", *args], cwd=path,
                       check=True, capture_output=True)
    return path


@pytest.fixture
def product_repo(tmp_path):
    return git_repo(tmp_path / "product", {"README.md": "# product\n"})


@pytest.fixture
def repos(tmp_path):
    out = []
    for rid, team in (("web-shop", "web"), ("payments-api", "payments")):
        root = git_repo(tmp_path / rid, {"README.md": f"# {rid}\n"})
        out.append({"id": rid, "title": rid, "root": str(root), "team": team, "team_title": team.capitalize()})
    return out


@pytest.fixture
def client():
    bus = EventBus()
    with TestClient(create_app(bus)) as c:
        c.bus = bus
        yield c


INITIATIVE = {"id": "INI-12", "title": "Prices in euro", "idea": "EU visitors see USD prices. Show euro.", "owner": "Product"}


def start(client, root, workflow: str, data: dict, request: str = "Initiative INI-12: Prices in euro."):
    wf = templates.get_template(workflow)
    assert wf is not None, workflow
    body = {"project_id": "product", "root": str(root), "workflow": wf.model_dump(), "title": f"INI-12 · {workflow}",
            "models": {"default": FAKE}, "settings": {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"},
            "mcp": [], "skills": {}, "request": request, "data": {"initiative": INITIATIVE, "note": "", **data}}
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
    raise AssertionError(f"thread {tid} still running")


def decide(client, tid, decision="approve", why=None, **kw):
    r = client.post(f"/threads/{tid}/resume", json={"decision": decision, "why": why, **kw})
    assert r.status_code == 200, r.text
    return wait(client, tid)
