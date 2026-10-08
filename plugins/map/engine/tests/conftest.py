"""The Map plugin's engine tests: keel_plugin_map loaded into a real engine as an add-on (fake model). Run from engine/:

    PYTHONPATH=../plugins/map/engine uv run pytest ../plugins/map/engine/tests      (scripts/test-plugins.sh)
"""

import pytest
from fastapi.testclient import TestClient

from keel_engine import extensions
from keel_engine.app import create_app
from keel_engine.demo import create_demo
from keel_engine.events import EventBus
from keel_engine.models import usage


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("KEEL_FAKE", "1")
    monkeypatch.setenv("KEEL_API_URL", "off")
    monkeypatch.setenv("KEEL_DEMO", "0")
    monkeypatch.setenv("KEEL_ADDONS", "keel_plugin_map")
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS", raising=False)
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("KEEL_FAKE_DELAY", raising=False)
    usage.reset()
    extensions.reload()
    yield tmp_path
    monkeypatch.delenv("KEEL_ADDONS", raising=False)
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
