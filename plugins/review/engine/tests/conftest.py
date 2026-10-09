"""The Code Review plugin's engine tests: keel_plugin_review loaded into a real engine as the image loads it
(KEEL_PLUGIN_PATHS and KEEL_PLUGIN_ADDONS, as `keel-engine plugins resolve` writes them), on the fake model. Run from
engine/:

    PYTHONPATH=../plugins/review/engine uv run pytest ../plugins/review/engine/tests      (scripts/test-plugins.sh)
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keel_engine import extensions
from keel_engine.app import create_app
from keel_engine.demo import create_demo
from keel_engine.events import EventBus
from keel_engine.models import usage

ENGINE = str(Path(__file__).resolve().parents[1])          # plugins/review/engine: the folder that holds keel_plugin_review


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("KEEL_FAKE", "1")
    monkeypatch.setenv("KEEL_API_URL", "off")
    monkeypatch.setenv("KEEL_DEMO", "0")
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", ENGINE)
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_review")
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
