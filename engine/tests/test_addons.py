"""keel add-ons (keel_engine/addons.py): an optional package adds workflows, agents, code actions, fake answers and routes
without changing the engine; one that needs another keel is left out. Also the two workflow switches add-ons use:
`root: item` (a parallel agent per folder, read only) and `asks: true` (keel's clarify loop on any agent step)."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import decide, start, wait
from keel_engine import addons
from keel_engine.app import create_app
from keel_engine.events import EventBus
from keel_engine.runtime import prompts
from keel_engine.runtime.actions import ActionInput, run_action
from keel_engine.workflows import templates
from keel_engine.workflows.validate import validate_yaml

FIXTURES = str(Path(__file__).parent / "fixtures" / "addons")


def _reset():
    addons.reload()
    templates.clear_cache()
    prompts._agent_file.cache_clear()


@pytest.fixture
def with_addons(monkeypatch):
    def use(names: str):
        monkeypatch.syspath_prepend(FIXTURES)
        monkeypatch.setenv("KEEL_ADDONS", names)
        _reset()
        return addons.loaded()
    yield use
    monkeypatch.delenv("KEEL_ADDONS", raising=False)
    for mod in [m for m in sys.modules if m.startswith(("keel_demo_addon", "keel_old_addon"))]:
        sys.modules.pop(mod, None)
    _reset()


@pytest.fixture
def demo_client(with_addons):
    with_addons("keel_demo_addon")
    bus = EventBus()
    with TestClient(create_app(bus)) as c:
        c.bus = bus
        yield c


def test_no_addon_means_nothing_changes():
    assert addons.reload() == ()
    assert addons.info() == {"addons": [], "problems": []}
    assert "demo-flow" not in [t.id for t in templates.templates()]
    assert addons.agent_path("explorer").is_file()


def test_an_addon_brings_its_workflow_agent_action_and_route(with_addons, demo_client):
    (demo,) = addons.loaded()
    assert (demo.name, demo.version) == ("demo", "0.0.1")
    info = demo_client.get("/addons").json()
    assert info["addons"][0]["workflows"] == ["demo-flow"] and info["addons"][0]["agents"] == ["demo-agent"]
    assert info["addons"][0]["actions"] == ["demo:echo"]
    assert demo_client.get("/demo/ping").json() == {"pong": True}
    # its workflow is listed after keel's own, marked with the add-on; keel's own workflows stay unmarked
    listed = demo_client.get("/templates").json()
    ids = [t["id"] for t in listed]
    assert ids.index("demo-flow") > ids.index("feature")
    assert next(t for t in listed if t["id"] == "demo-flow")["addon"] == "demo"
    assert next(t for t in listed if t["id"] == "feature")["addon"] is None
    # its agent's role and turn limit come from its own file; keel's agents are unchanged
    assert prompts.role_text("demo-agent") == "You are the demo agent of the test add-on."
    assert prompts.max_turns("demo-agent") == 7
    assert addons.agent_path("explorer").parent.parent.name == "content"
    # the validator knows its action and lets it read its own `with:`
    assert validate_yaml(templates.folder().joinpath("..").resolve().joinpath("workflows/feature.yaml").read_text())["ok"]
    assert validate_yaml((demo.content / "workflows" / "demo-flow.yaml").read_text())["ok"]


def test_an_addon_for_another_keel_or_a_broken_one_is_left_out(with_addons):
    loaded = with_addons("keel_old_addon,keel_missing_addon,keel_demo_addon")
    assert [a.name for a in loaded] == ["demo"]
    problems = {p["package"]: p["error"] for p in addons.info()["problems"]}
    assert problems["keel_old_addon"].startswith("needs keel <0.1.0")
    assert problems["keel_missing_addon"].startswith("does not import")


def test_versions_and_event_names():
    assert addons.satisfies("0.13.2-beta.1", ">=0.13.0,<0.15.0")
    assert not addons.satisfies("0.15.0", ">=0.13.0,<0.15.0")
    assert addons.satisfies("1.0.0", "")
    assert not addons.satisfies("1.0.0", "~1.0")


async def test_actions_go_to_their_addon_and_unknown_stays_unknown(with_addons, tmp_path):
    with_addons("keel_demo_addon")
    seen = []
    a = ActionInput(root=str(tmp_path), phase="review", title="t", ac=None, acs=[], fake=True, flow="demo-flow",
                    params={"say": "hello"}, data={"folders": [1, 2]})
    a.event = lambda kind, data: seen.append((kind, data))
    r = await run_action("demo:echo", a)
    assert r.ok and r.note == "echo: hello" and seen == [("demo.saved", {"items": 2, "answers": None, "read": None, "kept": None})]
    assert not (await run_action("demo:nope", a)).ok
    assert addons.may_emit("demo.saved") and not addons.may_emit("thread.done") and not addons.may_emit("demo")


def test_root_item_and_asks_in_a_flow(demo_client, repo, tmp_path):
    folders = []
    for name in ("web", "api"):
        d = tmp_path / name
        d.mkdir()
        (d / "README.md").write_text(f"# {name}\n")
        folders.append({"id": name, "title": name, "root": str(d)})
    tid = start(demo_client, repo, workflow=templates.get_template("demo-flow"), data={"folders": folders})
    s = wait(demo_client, tid)
    # asks: the agent's question is on the next gate as buttons
    assert s["waiting"]["step"] == "answers" and s["waiting"]["kind"] == "clarify"
    assert s["waiting"]["title"] == "ask first has 1 question"
    assert [q["id"] for q in s["waiting"]["questions"]] == ["colour"]
    s = decide(demo_client, tid, payload={"answers": {"colour": "Green"}})
    # the answer went back to the same step, which then finished; the gate now asks for a plain approval
    assert s["waiting"]["step"] == "answers" and s["waiting"].get("kind") != "clarify"
    s = decide(demo_client, tid)
    assert s["status"] == "done", s
    # the add-on action ran and emitted its own event, with what the flow kept in its data
    (saved,) = [e["data"] for e in demo_client.bus.of(tid, "demo.saved")]
    assert saved["items"] == 2
    assert saved["answers"][0]["answers"] == {"colour": "Green"}
    assert saved["answers"][0]["questions"][0]["question"] == "Which colour?"
    # keep: the asking step's whole last answer is in the flow's data
    assert saved["kept"] == "DONE with 1. Which colour?"
    # root: item: one agent per folder, each in its own folder, read only
    results = {r["item"]: r["text"] for r in saved["read"]}
    assert results["web"] == f"READ {folders[0]['root']} readonly=True"
    assert results["api"] == f"READ {folders[1]['root']} readonly=True"


def test_the_validator_checks_root_and_asks():
    bad = """id: x
name: x
steps:
  - { id: a, kind: code, name: a, action: preflight, root: item }
  - { id: g, kind: gate, name: g, asks: true }
"""
    errors = validate_yaml(bad)["errors"]
    assert any("'root: item' needs a parallel step" in e for e in errors)
    assert any("only an agent step takes 'asks'" in e for e in errors)
