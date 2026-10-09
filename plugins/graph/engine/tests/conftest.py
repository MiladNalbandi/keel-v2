"""The Graph plugin's engine tests: keel_plugin_graph loaded into a real engine as the image loads it (KEEL_PLUGIN_PATHS
and KEEL_PLUGIN_ADDONS, as `keel-engine plugins resolve` writes them), on the fake model. Run from engine/:

    PYTHONPATH=../plugins/graph/engine uv run pytest ../plugins/graph/engine/tests      (scripts/test-plugins.sh)
"""

import stat
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

ENGINE = str(Path(__file__).resolve().parents[1])          # plugins/graph/engine: the folder that holds keel_plugin_graph

FAKE_CODEGRAPH = """#!/bin/sh
# A stand-in for @colbymchenry/codegraph: init/index write .codegraph/codegraph.db, status prints counts.
cmd="$1"; shift
for a in "$@"; do case "$a" in -*) ;; *) root="$a";; esac; done
echo "$cmd $*" >> "$root/../codegraph-calls.log"
case "$cmd" in
  init|index) mkdir -p "$root/.codegraph" && echo db > "$root/.codegraph/codegraph.db" && echo "Indexed 3 files" ;;
  sync) exit 0 ;;
  status) printf '{"initialized":true,"fileCount":3,"nodeCount":20,"nodesByKind":{"file":3,"import":5,"function":9,"class":3},"journalMode":"wal"}\\n' ;;
  *) echo "unknown command $cmd" >&2; exit 2 ;;
esac
"""


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("KEEL_FAKE", "1")
    monkeypatch.setenv("KEEL_API_URL", "off")
    monkeypatch.setenv("KEEL_DEMO", "0")
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", ENGINE)
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_graph")
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


def fake_codegraph(tmp_path, monkeypatch, body=FAKE_CODEGRAPH):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    f = bin_dir / "codegraph"
    f.write_text(body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("KEEL_CODEGRAPH_BIN", str(f))
    return f


def no_codegraph(monkeypatch):
    monkeypatch.setenv("KEEL_CODEGRAPH_BIN", "")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")


def scan_and_wait(client, root, rebuild=False, project="demo"):
    r = client.post(f"/projects/{project}/scan", json={"root": str(root), "rebuild": rebuild})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "indexing"
    deadline = time.time() + 20
    while time.time() < deadline:
        s = client.get(f"/projects/{project}/index").json()
        if s["status"] != "indexing":
            return s
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


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
