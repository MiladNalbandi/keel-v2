"""The person's switch "Whole test suite before push" (.keel/config.yml `release: {required: false}`): the push gate,
its warning, ship's verify release step, the setting's round trip (engine routes, rules_view), the MCP tool (turning it
off asks the person; on does not), and the guard that keeps agents from turning it off."""

import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import decide, start, wait
from test_flows_cover_ship import finished, script, ship_plan
from test_flows_base import answers, values
from test_mcp import call
from test_mcp_ux import Api, server, token_of
from keel_engine import mcp_confirm, mcp_server, rules
from keel_engine.runtime import blockers, explain, rules_view, ship, verdicts
from keel_engine.workflows.templates import get_template

CONFIG = """# the demo shop's keel settings
layout: repo
commands:
  test: pytest -q   # the whole suite
coverage:
  enabled: false
"""


def git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, capture_output=True,
                          text=True)


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "shop"
    (root / ".keel").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("demo shop\n")
    (root / ".keel/config.yml").write_text(CONFIG)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: start")
    return root


def release_off(root: Path, commit: bool = True) -> None:
    blockers.set_release_required(str(root), False)
    if commit:
        git(root, "commit", "-qam", "chore: the whole suite runs in CI")


# ------------------------------------------------------------------ the push gate

def test_by_default_a_push_waits_for_the_whole_suite_as_before(proj):
    found = {b["gate"]: b for b in blockers.push_blockers(str(proj), project="shop")}
    assert found["release"]["why"] == "no release verdict for this commit"
    assert "release" not in {w["gate"] for w in blockers.push_warnings(str(proj), project="shop")}
    assert blockers.gates_off(str(proj)) == []
    assert blockers.release_view(str(proj)) == {"configured": True, "required": True, "setting": None, "why": None}


def test_release_off_does_not_block_a_push_and_warns_instead(proj):
    release_off(proj)
    assert "release" not in {b["gate"] for b in blockers.push_blockers(str(proj), project="shop")}
    assert "release" not in {b["gate"] for b in blockers.for_agents(str(proj), project="shop")}
    warn = {w["gate"]: w for w in blockers.push_warnings(str(proj), project="shop")}
    assert warn["release"]["why"] == "the whole test suite is off for pushes in this project"
    assert blockers.gates_off(str(proj)) == [{"gate": "release", "why": "off in this project's settings: CI runs the whole suite",
                                              "fix": blockers.RELEASE_ON_FIX}]
    # a failing release verdict that exists is still shown (the final review's table), it only blocks nothing
    head, tree = verdicts.stamp(str(proj))
    verdicts.write("shop", "release", False, {"summary": "2 tests failed", "tree": tree}, head)
    assert "release" not in {b["gate"] for b in blockers.push_blockers(str(proj), project="shop")}
    report = ship.final_report(str(proj), "shop", {"data": {}}, "Checkout totals")
    assert "| release | FAIL | 2 tests failed |" in report


def test_required_true_and_old_configs_block_as_before():
    assert blockers.release_required({}) and blockers.release_required({"release": None})
    assert blockers.release_required({"release": {"required": True}}) and blockers.release_required({"release": {}})
    assert not blockers.release_required({"release": {"required": False}})
    assert not blockers.release_required(rules.load_config(None) | {"release": {"required": False}})


# ------------------------------------------------------------------ the setting's round trip

def test_the_switch_round_trips_and_keeps_the_other_lines(proj):
    off = blockers.set_release_required(str(proj), False)
    assert off == {"configured": True, "required": False, "setting": False,
                   "why": "off in this project's settings: CI runs the whole suite"}
    text = (proj / ".keel/config.yml").read_text()
    assert text.startswith(CONFIG) and yaml.safe_load(text)["release"] == {"required": False}
    assert rules.load_config(str(proj))["release"] == {"required": False}
    on = blockers.set_release_required(str(proj), True)
    assert on["required"] is True and on["setting"] is True
    assert (proj / ".keel/config.yml").read_text() == CONFIG + "release:\n  required: true\n"


def test_the_engine_routes_and_their_checks(client, proj, tmp_path):
    got = client.get("/release-check", params={"root": str(proj)}).json()
    assert got["required"] is True and got["setting"] is None
    put = client.put("/release-check", json={"root": str(proj), "required": False})
    assert put.status_code == 200 and put.json()["required"] is False
    assert client.get("/release-check", params={"root": str(proj)}).json()["setting"] is False
    assert client.put("/release-check", json={"root": str(proj), "required": "maybe"}).status_code == 400
    # the Rules page's writer takes only true or false for it
    bad = client.put("/rules/config", json={"root": str(proj), "patch": {"release": {"required": "no"}}})
    assert bad.status_code == 400 and "release.required is true or false" in bad.text
    with pytest.raises(rules_view.RulesError):
        rules_view.set_config(str(proj), {"release": {"required": False, "why": "x"}})
    # a project without .keel/config.yml has no gates: nothing is created
    bare = tmp_path / "bare"
    bare.mkdir()
    assert client.get("/release-check", params={"root": str(bare)}).json() == {"configured": False, "required": True,
                                                                             "setting": None, "why": None}
    assert client.put("/release-check", json={"root": str(bare), "required": False}).status_code == 409
    assert not (bare / ".keel").exists()


# ------------------------------------------------------------------ ship's verify release

def test_ship_skips_verify_release_when_it_is_off(client, repo, monkeypatch):
    (repo / ".keel").mkdir(exist_ok=True)
    (repo / ".keel/config.yml").write_text("layout: repo\nrelease:\n  required: false\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: keel config")
    answers(monkeypatch, {})
    calls = script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    w = wait(client, tid)["waiting"]
    assert w["step"] == "plan"
    assert "Off in this project's settings (CI runs the whole suite; the push does not wait for it): verify release" in w["detail"]
    assert "  - release" not in w["detail"] and "  - cover" in w["detail"]
    skip_q = next(q for q in w.get("questions") or [] if q["id"] == "skip")
    assert "release" not in [o["label"] for o in skip_q["options"]]
    decide(client, tid, "approve", payload={"skip": {"cover": "c", "deps": "d", "reviewers": "r", "spec_walk": "s"}})
    assert "verify_release" not in calls
    done = finished(client, tid, "release")[-1]
    assert done["skipped"] is True and done["note"] == "verify release skipped: off in this project's settings: CI runs the whole suite"
    skipped = {x["step"]: x for x in values(client, tid)["data"]["ship_skipped"]}
    assert skipped["release"] == {"step": "release", "band": "off", "reason": "off in this project's settings: CI runs the whole suite"}
    # the Flow page's Before ship (and keel_next) say it is off
    assert client.get(f"/threads/{tid}").json()["gates_off"][0]["gate"] == "release"


def test_ship_runs_verify_release_by_default(client, repo, monkeypatch):
    answers(monkeypatch, {})
    calls = script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    w = wait(client, tid)["waiting"]
    assert "  - release" in w["detail"] and "Off in this project's settings" not in w["detail"]
    decide(client, tid, "approve", payload={"skip": {"cover": "c", "deps": "d", "reviewers": "r", "spec_walk": "s"}})
    assert "verify_release" in calls
    assert "gates_off" not in client.get(f"/threads/{tid}").json()


def test_the_feature_flows_own_suite_steps_are_not_touched():
    # only ship's verify release (action verify_release) is the switch's; the feature/fix flows' whole-suite
    # verify_green steps stay
    for flow in ("feature", "fix", "change"):
        wf = get_template(flow)
        own = [s for s in wf.steps if s.kind == "code" and s.action == "verify_release" and not s.included_from]
        assert own == [], flow


def test_the_step_explainer_says_it_is_off(proj):
    wf = get_template("ship")
    i = [s.id for s in wf.steps].index("release")
    assert "off" not in explain._explain(wf, i, str(proj), {}, None, None)
    release_off(proj)
    out = explain._explain(wf, i, str(proj), {}, None, None)
    assert out["off"].startswith("off in this project's settings: CI runs the whole suite")
    assert "does not wait" in out["skippable"]["text"]


# ------------------------------------------------------------------ agents cannot turn it off

def test_an_agent_never_turns_the_gate_off_by_editing_the_config():
    cfg = rules.make_config()
    for phase in ("green", "review-fix", "ship", "coverage-fix", "close"):
        v = rules.check_edit(phase, ".keel/config.yml", cfg, exists=True)
        assert not v.ok and v.bucket == "keel-rules", phase
    assert not rules.check_bash("green", "sed -i 's/required: true/required: false/' .keel/config.yml", cfg).ok
    # init's setup phase may write the file, but not a gate turned off
    assert rules.check_edit("setup", ".keel/config.yml", cfg, exists=True).ok
    for text in ("release:\n  required: false\n", "release: {required: false}\n", "  required: no"):
        v = rules.check_content(".keel/config.yml", text, cfg)
        assert not v.ok and "An agent never turns a safety check off" in v.reason, text
    assert rules.check_content(".keel/config.yml", "release:\n  required: true\n", cfg).ok
    assert rules.check_content(".keel/config.yml", "commands:\n  test: pytest -q\n", cfg).ok


# ------------------------------------------------------------------ MCP: keel_release_check

class ReleaseApi(Api):
    def __init__(self):
        super().__init__()
        self.required = True
        self.puts: list[dict] = []

    def __call__(self, req):
        import httpx
        import json

        if req.url.path == "/api/projects/shop/release-check":
            if req.method == "PUT":
                body = json.loads(req.content)
                self.puts.append(body)
                self.required = body["required"]
            return httpx.Response(200, json={"configured": True, "required": self.required, "setting": self.required,
                                             "why": None if self.required else blockers.RELEASE_OFF})
        return super().__call__(req)


@pytest.fixture
def here(monkeypatch):
    monkeypatch.setenv("KEEL_PROJECT", "shop")
    monkeypatch.delenv("KEEL_MCP_CONFIRM", raising=False)
    monkeypatch.chdir("/")


async def test_the_mcp_tool_turning_it_off_needs_the_persons_ok_in_keels_inbox(here):
    api = ReleaseApi()
    srv = server(api, level="write")
    first = await call(srv, "keel_release_check", {"required": False})
    assert "This needs the person's OK: turn off the whole test suite before push" in first
    assert "keel put the question in its Inbox" in first and "The token alone does not do it" in first
    assert api.puts == [] and api.posts[-1][0] == "/api/projects/shop/plugins/ask"
    token = token_of(first)
    early = await call(srv, "keel_release_check", {"required": False, "confirm": token})
    assert "has not approved" in early and api.puts == []
    api.asks["p_000000000001"] = {"decision": "allow", "why": ""}           # the person approves it in the Inbox
    done = await call(srv, "keel_release_check", {"required": False, "confirm": token})
    assert api.puts == [{"required": False}] and done.startswith("saved: whole test suite before push in shop: off")


async def test_the_mcp_tool_turning_it_on_asks_nothing_and_reading_changes_nothing(here):
    api = ReleaseApi()
    api.required = False
    srv = server(api, level="write")
    now = await call(srv, "keel_release_check", {})
    assert "whole test suite before push in shop: off" in now and api.puts == []
    on = await call(srv, "keel_release_check", {"required": True})
    assert api.puts == [{"required": True}] and on.startswith("saved: whole test suite before push in shop: required")
    assert not any(p[0].endswith("/plugins/ask") for p in api.posts)


async def test_the_mcp_tool_is_write_level_dangerous_and_says_an_agent_cannot_turn_it_off(here):
    assert mcp_server.tool_level("keel_release_check") == "write" and "keel_release_check" in mcp_server.CONFIRM
    assert mcp_confirm.out_of_band("keel_release_check")
    from test_mcp import tool_names
    assert "keel_release_check" not in await tool_names(server(ReleaseApi(), level="read"))
    from mcp.shared.memory import create_connected_server_and_client_session
    async with create_connected_server_and_client_session(server(ReleaseApi())) as s:
        tool = next(t for t in (await s.list_tools()).tools if t.name == "keel_release_check")
    assert "an agent cannot switch a safety check off without the person" in " ".join(tool.description.split())
    assert "confirm" in tool.inputSchema["properties"]
