"""keel Product's engine tests: the add-on loaded into a real engine (fake model). Run from engine/:

    KEEL_ADDONS=keel_product PYTHONPATH=../product/engine uv run pytest ../product/engine/tests
"""


import pytest
from fastapi.testclient import TestClient

from keel_engine import addons
from keel_engine.app import create_app
from keel_engine.events import EventBus
from keel_engine.models import usage
from keel_engine.runtime import prompts
from keel_engine.workflows import templates

from product_support import git_repo


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
