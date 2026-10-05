"""Milestone 4 engine foundations: fan-out from a state list, batches, for_each loops, markers, start_flow, FLOW_START."""

import asyncio
import json
from pathlib import Path

from conftest import decide, start, wait
from keel_engine import rules
from keel_engine.models import fake as fake_mod
from keel_engine.runtime import markers
from keel_engine.workflows.estimate import estimate
from keel_engine.workflows.model import from_dict, load_yaml
from keel_engine.workflows.validate import validate

ROOT = Path(__file__).resolve().parents[2]


def wf(steps, name="custom"):
    return from_dict({"name": name, "keel_rules": False, "steps": steps})


def values(client, tid) -> dict:
    eng = client.app.state.engine

    async def get():
        return (await (await eng._graph(tid)).aget_state(eng._cfg(tid))).values

    return client.portal.call(get)


def answers(monkeypatch, by_agent):
    """The fake model answers with by_agent[agent](req) (text) for those agents; real fake output for the rest."""
    real = fake_mod._plan
    seen = []

    def plan(req):
        seen.append(req)
        if req.agent in by_agent:
            return None, "", by_agent[req.agent](req), {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    return seen


# ------------------------------------------------------------------ fan-out + batch

HYPOTHESES = "Three ideas:\n```json\n" + json.dumps([{"title": "stale cache"}, {"title": "off by one"}, {"title": "race"},
                                                     {"title": "clock skew"}]) + "\n```"


def test_fan_out_from_a_state_list_with_cap_and_batch(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"explorer": lambda r: HYPOTHESES,
                                 "investigator": lambda r: f"Checked it.\nROOT-CAUSE: {'confirmed' if 'off by one' in r.prompt else 'unconfirmed'}"})
    running, peak = {"n": 0}, {"n": 0}
    real_run = fake_mod.FakeRunner.run

    async def run(self, req, emit):
        if req.agent == "investigator":
            running["n"] += 1
            peak["n"] = max(peak["n"], running["n"])
            await asyncio.sleep(0.05)
            try:
                return await real_run(self, req, emit)
            finally:
                running["n"] -= 1
        return await real_run(self, req, emit)
    monkeypatch.setattr(fake_mod.FakeRunner, "run", run)

    w = wf([{"id": "frame", "kind": "agent", "name": "frame", "agent": "explorer", "collect": "hypotheses"},
            {"id": "investigate", "kind": "parallel", "name": "one investigator per hypothesis", "agent": "investigator",
             "from": "hypotheses", "cap": 3, "batch": 2, "markers": ["ROOT-CAUSE"]}])
    tid = start(client, repo, workflow=w)
    s = wait(client, tid)
    assert s["status"] == "done", s
    inv = [r for r in seen if r.agent == "investigator"]
    assert len(inv) == 3, "cap 3 of 4 hypotheses"
    assert peak["n"] == 2, "batch 2: never more than two at once"
    assert sorted(r.item["title"] for r in inv) == ["off by one", "race", "stale cache"]
    assert all("Your item" in r.prompt for r in inv)
    v = values(client, tid)
    assert [h["id"] for h in v["data"]["hypotheses"]] == ["hypotheses-1", "hypotheses-2", "hypotheses-3", "hypotheses-4"]
    res = {r["item"]: r["markers"]["ROOT-CAUSE"] for r in v["data"]["investigate_results"]}
    assert res == {"hypotheses-1": "unconfirmed", "hypotheses-2": "confirmed", "hypotheses-3": "unconfirmed"}
    assert v["markers"]["investigate"]["ROOT-CAUSE"] in ("confirmed", "unconfirmed")


def test_parallel_copies_still_work_and_an_empty_list_runs_nobody(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    w = wf([{"id": "lenses", "kind": "parallel", "name": "lenses", "agent": "reviewer", "parallel": 2},
            {"id": "none", "kind": "parallel", "name": "nobody", "agent": "investigator", "from": "missing"}])
    s = wait(client, start(client, repo, workflow=w))
    assert s["status"] == "done"
    assert [r.agent for r in seen] == ["reviewer", "reviewer"]


# ------------------------------------------------------------------ for_each

def test_for_each_runs_per_item_with_a_gate_that_sends_back_and_skips(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"explorer": lambda r: f"looked at {r.item['id']}"})
    w = wf([{"id": "work", "kind": "agent", "name": "work on the group", "agent": "explorer", "for_each": "groups"},
            {"id": "check", "kind": "gate", "name": "group gate", "per_item": True, "back": "work"},
            {"id": "after", "kind": "code", "name": "after the loop", "action": "run:true"}])
    assert validate(w) == []
    groups = [{"id": "g1", "title": "a.py:10-12"}, {"id": "g2", "title": "b.py:3"}]
    tid = start(client, repo, workflow=w, data={"groups": groups})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "check" and s["waiting"]["title"] == "group gate · g1" and s["item"] == "g1"
    s = decide(client, tid, "reject", why="test the empty case too")
    assert [r.item["id"] for r in seen] == ["g1", "g1"]
    assert "test the empty case too" in seen[-1].prompt
    assert s["waiting"]["title"] == "group gate · g1"
    s = decide(client, tid, "approve")
    assert s["waiting"]["title"] == "group gate · g2"
    s = decide(client, tid, "approve", payload={"skip": True})
    assert s["status"] == "done", s
    v = values(client, tid)
    assert {g["id"]: g["status"] for g in v["data"]["groups"]} == {"g1": "done", "g2": "skipped"}
    assert any(line.startswith("gate check reject (g1)") for line in s["gate_log"])


def test_a_failing_item_can_be_marked_failed_and_the_loop_goes_on(client, repo, monkeypatch):
    answers(monkeypatch, {})
    w = wf([{"id": "work", "kind": "agent", "name": "work", "agent": "explorer", "for_each": "items"},
            {"id": "check", "kind": "code", "name": "check", "action": "run:false", "per_item": True}])
    tid = start(client, repo, workflow=w, data={"items": [{"id": "a"}, {"id": "b"}]},
                settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "fix_attempts": 0})
    s = wait(client, tid)
    assert s["waiting"]["kind"] == "fix" and "mark this item failed" in s["waiting"]["detail"]
    s = decide(client, tid, "reject")
    assert s["waiting"]["kind"] == "fix" and s["item"] == "b"
    s = decide(client, tid, "reject")
    assert s["status"] == "done"
    assert {g["id"]: g["status"] for g in values(client, tid)["data"]["items"]} == {"a": "failed", "b": "failed"}


def test_loop_validation():
    bad = wf([{"id": "a", "kind": "agent", "name": "a", "agent": "explorer", "per_item": True}])
    assert any("must follow a for_each" in e for e in validate(bad))
    bad = wf([{"id": "a", "kind": "agent", "name": "a", "agent": "explorer", "from": "x"}])
    assert any("only a parallel step takes 'from'" in e for e in validate(bad))
    bad = wf([{"id": "a", "kind": "branch", "name": "a", "when": {"equals": "x"}, "no": "a"}])
    assert any("when needs a marker" in e for e in validate(bad))
    bad = wf([{"id": "a", "kind": "code", "name": "a", "action": "start_flow"}])
    assert any("start_flow needs the workflow" in e for e in validate(bad))
    two = wf([{"id": "a", "kind": "agent", "name": "a", "agent": "explorer", "for_each": "xs"},
              {"id": "b", "kind": "gate", "name": "b", "per_item": True},
              {"id": "c", "kind": "agent", "name": "c", "agent": "explorer", "for_each": "ys"}])
    assert validate(two) == [] and [(lp.id, lp.key, lp.first, lp.last) for lp in two.loops()] == [("a", "xs", 0, 1), ("c", "ys", 2, 2)]


def test_yaml_round_trip_and_estimate():
    text = """name: example
keel_rules: false
steps:
  - { id: frame, kind: agent, name: frame, agent: explorer, collect: hypotheses }
  - { id: investigate, kind: parallel, name: investigators, agent: investigator, from: hypotheses, cap: 4, batch: 2, markers: [ROOT-CAUSE] }
  - { id: fix_group, kind: agent, name: write a test, agent: test-author, for_each: coverage_groups }
  - { id: group_gate, kind: gate, name: group gate, per_item: true, back: fix_group }
  - { id: repro, kind: agent, name: reproduce, agent: reproducer, markers: [REPRO] }
  - { id: reproduced, kind: branch, name: reproduced, when: { marker: REPRO, equals: confirmed }, no: to_diagnose }
  - { id: done, kind: gate, name: done }
  - { id: to_diagnose, kind: code, name: hand to diagnose, action: start_flow, flow: diagnose, seed: { title: $title, symptoms: $data.seed.symptoms }, then: end }
"""
    w = load_yaml(text)
    assert validate(w) == []
    again = from_dict(json.loads(json.dumps(w.model_dump(exclude_none=True))))
    assert again.step("investigate").items_from == "hypotheses" and again.step("to_diagnose").then == "end"
    assert w.model_dump(exclude_none=True)["steps"][1]["from"] == "hypotheses"
    per = {p["step"]: p["tokens"] for p in estimate(w, acs=2)["per_step"]}
    one = per["frame"]
    assert per["investigate"] > 2 * per["frame"] * 0.5 and per["fix_group"] > 0 and one > 0


# ------------------------------------------------------------------ markers

def test_markers_parse():
    text = "Done.\n**REPRO:** Confirmed — the test fails\nE2E-RESULT: fail (timeout)\nRECIPE: curl -X POST /api/x\nREPRO: not-reproducible"
    assert markers.parse(text) == {"REPRO": "not-reproducible", "E2E-RESULT": "fail"}
    assert markers.parse(text, ["recipe", "ROOT-CAUSE"]) == {"RECIPE": "curl -X POST /api/x"}
    assert markers.matches("Confirmed", {"equals": "confirmed"}) and not markers.matches(None, {"equals": "x"})
    assert markers.matches("b", {"in": ["a", "b"]})
    assert markers.collect('```json\n{"lenses": ["correctness", {"id": "sec", "title": "security"}]}\n```', "lenses") == [
        {"title": "correctness", "id": "lenses-1"}, {"id": "sec", "title": "security"}]


def test_branch_on_a_marker(client, repo, monkeypatch):
    said = {"v": "confirmed"}
    answers(monkeypatch, {"reproducer": lambda r: f"Wrote the test.\nREPRO: {said['v']}"})
    w = wf([{"id": "repro", "kind": "agent", "name": "reproduce", "agent": "reproducer", "markers": ["REPRO"]},
            {"id": "reproduced", "kind": "branch", "name": "reproduced", "when": {"marker": "REPRO", "equals": "confirmed"}, "no": "other"},
            {"id": "yes", "kind": "gate", "name": "reproduced gate"},
            {"id": "other", "kind": "gate", "name": "not reproduced gate"}])
    s = wait(client, start(client, repo, workflow=w))
    assert s["waiting"]["step"] == "yes"
    said["v"] = "not-reproducible"
    s = wait(client, start(client, repo, workflow=w))
    assert s["waiting"]["step"] == "other"


# ------------------------------------------------------------------ start_flow

def test_start_flow_creates_a_linked_child_and_the_parent_ends(client, repo, monkeypatch):
    answers(monkeypatch, {"reproducer": lambda r: "REPRO: not-reproducible" if "diagnose" not in r.step_name else "x",
                          "investigator": lambda r: "RECIPE: run the nightly job twice\nROOT-CAUSE: confirmed"})
    w = wf([{"id": "investigate", "kind": "agent", "name": "investigate", "agent": "investigator", "markers": ["RECIPE", "ROOT-CAUSE"]},
            {"id": "hand", "kind": "code", "name": "hand to fix", "action": "start_flow", "flow": "fix", "then": "end",
             "seed": {"title": "Nightly job double counts", "recipe": "$markers.investigate.RECIPE",
                      "symptoms": ["totals doubled"], "needs_e2e": True,
                      "acs": [{"title": "the nightly job counts each score once"}]}},
            {"id": "never", "kind": "gate", "name": "never reached"}])
    tid = start(client, repo, workflow=w, request="scores double at night")
    s = wait(client, tid)
    assert s["status"] == "done", s
    kids = s["children"]
    assert len(kids) == 1 and kids[0]["workflow"] == "fix" and kids[0]["step"] == "hand"
    child = kids[0]["thread_id"]
    c = wait(client, child)
    assert c["workflow_id"] == "fix" and c["title"] == "Nightly job double counts"
    assert c["parent"] == {"thread_id": tid, "workflow": "custom", "step": "hand"}
    assert c["acs"][0]["title"] == "the nightly job counts each score once"
    cv = values(client, child)
    assert cv["data"]["recipe"] == "run the nightly job twice" and cv["data"]["needs_e2e"] is True
    started = [e for e in client.bus.of(child, "thread.started")]
    assert started and started[0]["data"]["workflow_id"] == "fix" and started[0]["data"]["parent"]["thread_id"] == tid
    assert [e["data"]["child"] for e in client.bus.of(tid, "flow.started")] == [child]
    eng = client.app.state.engine
    body = client.portal.call(eng._row, child)["body"]
    req = json.loads(body)["request"]
    assert "scores double at night" in req and "run the nightly job twice" in req and "totals doubled" in req


def test_start_flow_with_an_unknown_workflow_asks(client, repo, monkeypatch):
    answers(monkeypatch, {})
    w = wf([{"id": "think", "kind": "agent", "name": "think", "agent": "explorer"},
            {"id": "hand", "kind": "code", "name": "hand", "action": "start_flow", "flow": "nope"}])
    s = wait(client, start(client, repo, workflow=w, settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause",
                                                                "fix_attempts": 0}))
    assert s["waiting"]["kind"] == "fix" and "No workflow nope" in s["waiting"]["detail"]


# ------------------------------------------------------------------ rules

def test_flow_start_rows_for_every_flow():
    want = {"feature": "spec", "change": "triage", "fix": "bug-report", "hunt": "hunt-scope", "review": "review",
            "diagnose": "bug-investigate", "cover": "coverage-fix", "ship": "ship", "init": "setup", "knowledge-refresh": "memory",
            "lint": "lint-fix"}
    assert rules.FLOW_START == want
    for flow, phase in want.items():
        assert phase in rules.PHASES and phase in rules.RAILS[flow] and rules.can_transition("none", phase)
    assert rules.MATRIX["review"] == {"*": "deny"}
    assert not rules.check_edit("review", "README.md").ok and not rules.check_edit("review", "src/a.py").ok


def test_rules_json_is_byte_identical_to_the_api_copy():
    engine = ROOT / "engine" / "keel_engine" / "rules" / "data" / "keel_rules.json"
    api = ROOT / "api" / "src" / "main" / "resources" / "keel" / "keel_rules.json"
    assert engine.read_bytes() == api.read_bytes()
