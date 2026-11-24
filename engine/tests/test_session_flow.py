"""0.29 the harness flow run by a connected session (data.runner "session"): the person's own Claude Code or OpenCode,
connected to keel's MCP server, plans and builds; keel publishes work orders, keeps the gates, runs its tests and its
checks after each step, and commits. No model is called for the session's steps (the fake model's planner and builder
must never run). Real git repo, real flow, the session's tools stand in as HTTP calls to the engine's routes."""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import decide, start, wait
from test_flows_base import answers
from keel_engine.runtime import actions as actions_mod, harness_actions, session_service, sessions

PLAN = "## Goal\nShow the owner's name.\n## Acceptance criteria\n1. The page shows it.\nPLAN: ready"
MODELS = {"default": {"provider": "fake", "mode": "api", "model": "fake"}}


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 1_800_000_000.0}
    monkeypatch.setattr(sessions, "now", lambda: t["now"])
    return t


@pytest.fixture
def no_model(monkeypatch):
    """The fake model's harness agents: a test fails loudly if a connected-session step called a model."""
    return answers(monkeypatch, {"harness-planner": lambda r: PLAN, "harness-builder": lambda r: "built by keel's harness"})


@pytest.fixture
def tests_pass(monkeypatch):
    """keel's own test run after the build (no pytest of the demo project here): pass, or fail as a test says."""
    calls = {"fail": 0, "n": 0}

    def check(a):
        calls["n"] += 1
        if calls["fail"]:
            calls["fail"] -= 1
            return actions_mod.ActionResult(False, "The project's tests failed: `pytest` exited 1.", "E   assert 2 == 3")
        return actions_mod.ActionResult(True, "tests pass: `pytest`", "1 passed")
    monkeypatch.setitem(harness_actions.ACTIONS, "harness_check", check)
    return calls


def open_session(client, client_name="claude-code"):
    r = client.post("/sessions/open", json={"project": "demo", "client": client_name, "folder": "/work/demo"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def session_flow(client, repo, **kw):
    tid = start(client, repo, workflow="harness", title="Show the owner name", models=MODELS,
                data={"runner": "session", **kw.pop("data", {})}, **kw)
    return tid, wait(client, tid)


def next_order(client, sid):
    r = client.post(f"/sessions/{sid}/next")
    assert r.status_code == 200, r.text
    return r.json()["order"]


def test_presence_is_connected_within_two_minutes(client, clock):
    assert client.get("/sessions", params={"project": "demo"}).json()["connected"] is None
    sid = open_session(client)
    got = client.get("/sessions", params={"project": "demo"}).json()
    assert got["connected"]["id"] == sid and got["connected"]["client"] == "claude-code"
    assert got["connected_s"] == 120 and got["gone_s"] == 600
    clock["now"] += 100
    assert client.get("/sessions", params={"project": "demo"}).json()["connected"]["id"] == sid
    clock["now"] += 30                                   # 130 s since it was seen
    got = client.get("/sessions", params={"project": "demo"}).json()
    assert got["connected"] is None and got["sessions"][0]["connected"] is False
    next_order(client, sid)                              # a long poll counts as seen
    assert client.get("/sessions", params={"project": "demo"}).json()["connected"]["id"] == sid
    assert client.post("/sessions/nope/next").status_code == 404     # keel restarted: open again
    assert client.get("/sessions", params={"project": "other"}).json()["connected"] is None


def test_the_plan_step_publishes_a_work_order_and_the_plan_resumes_it_to_the_plan_gate(client, repo, no_model, clock):
    sid = open_session(client)
    tid, s = session_flow(client, repo, request="Show the owner's name on the pet page.")
    assert s["status"] == "waiting" and s["waiting"]["kind"] == "session", s
    assert s["waiting"]["step"] == "plan" and s["waiting"]["order"]["kind"] == "plan"
    assert s["runner"] == "session" and s["session"]["connected"] and s["session"]["client"] == "claude-code"
    assert s["usage"]["tokens_in"] == 0 and no_model == []          # no model was called
    assert not client.bus.of(tid, "gate.waiting")                    # nobody is asked: no Inbox item yet
    assert client.bus.of(tid, "session.waiting")[0]["data"]["kind"] == "plan"
    order = next_order(client, sid)
    assert order["flow"] == tid and order["kind"] == "plan" and order["step"] == "plan"
    assert "Show the owner's name on the pet page." in order["instructions"]          # the request
    assert "This is a change to existing behaviour" in order["instructions"]         # the intent note
    assert "PLAN: ready" in order["instructions"] and order["root"] == str(repo)
    # live progress
    r = client.post(f"/sessions/flows/{tid}/todo", json={"session": sid, "todos": [
        {"text": "Read the pet page", "status": "done"}, {"text": "Write the plan", "status": "in_progress"},
        {"text": "List the tests", "status": "pending"}]})
    assert r.status_code == 200, r.text
    s = client.get(f"/threads/{tid}").json()
    assert [t["status"] for t in s["session"]["todos"]] == ["done", "in_progress", "pending"]
    assert client.bus.of(tid, "session.todo")
    # a plan without its result line is refused, the flow still waits
    r = client.post(f"/sessions/flows/{tid}/plan", json={"session": sid, "plan": "## Goal\nShow it."})
    assert r.status_code == 400 and "PLAN: ready" in r.json()["hint"]
    # a build report is not what this step waits for
    r = client.post(f"/sessions/flows/{tid}/done", json={"session": sid, "summary": "done"})
    assert r.status_code == 409 and "plan" in r.json()["error"]
    r = client.post(f"/sessions/flows/{tid}/plan", json={"session": sid, "plan": PLAN,
                                                         "todos": [{"text": "Write the plan", "status": "done"}]})
    assert r.status_code == 200, r.text
    s = wait(client, tid)
    assert s["waiting"]["step"] == "plan_gate", s
    assert "Show the owner's name." in s["waiting"]["detail"]          # plan_show shows the plan word for word
    assert "the connected session builds this plan" in s["waiting"]["detail"]
    assert no_model == []
    log = s["session"]["log"]
    assert log[-1]["kind"] == "plan" and log[-1]["by"] == "claude-code" and log[-1]["todos"][0]["status"] == "done"
    # nothing waits for the session now: a report is refused with a plain reason
    r = client.post(f"/sessions/flows/{tid}/plan", json={"session": sid, "plan": PLAN})
    assert r.status_code == 409 and "waits for the person" in r.json()["error"]
    assert next_order(client, sid) is None


def test_approve_builds_through_the_session_then_keel_tests_and_a_failure_is_a_fix_order(client, repo, no_model, clock, tests_pass):
    sid = open_session(client)
    tid, _s = session_flow(client, repo)
    client.post(f"/sessions/flows/{tid}/plan", json={"session": sid, "plan": PLAN})
    wait(client, tid)
    tests_pass["fail"] = 1
    s = decide(client, tid, payload={"choice": "approve"})
    assert s["waiting"]["kind"] == "session" and s["waiting"]["step"] == "build", s
    order = next_order(client, sid)
    assert order["kind"] == "build" and "Show the owner's name." in order["plan"]       # the approved plan
    (Path(repo) / "owner.py").write_text("OWNER = 'Ada'\n")
    r = client.post(f"/sessions/flows/{tid}/done", json={"session": sid, "summary": "Added owner.py. Tests pass.",
                                                         "todos": [{"text": "owner.py", "status": "done"}]})
    assert r.status_code == 200, r.text
    s = wait(client, tid)
    # keel's test run failed: the fix round goes to the session, with the failing output
    assert tests_pass["n"] == 1
    assert s["waiting"]["kind"] == "session" and s["waiting"]["order"]["kind"] == "fix", s
    order = next_order(client, sid)
    assert order["kind"] == "fix" and "assert 2 == 3" in order["failure"]
    r = client.post(f"/sessions/flows/{tid}/done", json={"session": sid, "summary": "Fixed the off-by-one."})
    assert r.status_code == 200, r.text
    s = wait(client, tid)
    assert s["waiting"]["step"] == "done_gate", s
    assert "What the connected session says it did" in s["waiting"]["detail"]
    assert "Fixed the off-by-one." in s["waiting"]["detail"]
    assert no_model == []
    s = decide(client, tid, payload={"choice": "commit"})
    assert s["waiting"]["step"] == "pr_gate", s
    files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    assert "owner.py" in files                                     # keel made the commit
    assert [x["kind"] for x in s["session"]["log"]] == ["plan", "build", "fix"]


def test_keel_checks_the_change_after_a_session_step(client, repo, no_model, clock, tests_pass):
    """The plan step is read only: a file the session changed there is put back and named at the done gate; a commit
    the session made is said (HEAD moved)."""
    sid = open_session(client)
    tid, _s = session_flow(client, repo)
    (Path(repo) / "sneaky.py").write_text("X = 1\n")
    client.post(f"/sessions/flows/{tid}/plan", json={"session": sid, "plan": PLAN})
    s = wait(client, tid)
    assert not (Path(repo) / "sneaky.py").exists()                       # keel's diff guard put it back
    assert any(f["kind"] == "put-back" and f["path"] == "sneaky.py" for f in s["session"]["findings"])
    assert any(e["data"].get("source") == "session" for e in client.bus.of(tid, "guard.refused"))
    decide(client, tid, payload={"choice": "approve"})
    (Path(repo) / "owner.py").write_text("OWNER = 'Ada'\n")
    subprocess.run(["git", "add", "owner.py"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Demo", "-c", "user.email=demo@example.test", "commit", "-qm", "wip"], cwd=repo, check=True)
    client.post(f"/sessions/flows/{tid}/done", json={"session": sid, "summary": "Built it."})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "done_gate", s
    assert "HEAD moved" in s["waiting"]["detail"] and "sneaky.py" in s["waiting"]["detail"]


def test_a_blocked_build_skips_the_test_run(client, repo, no_model, clock, tests_pass):
    sid = open_session(client)
    tid, _s = session_flow(client, repo)
    client.post(f"/sessions/flows/{tid}/plan", json={"session": sid, "plan": PLAN})
    wait(client, tid)
    decide(client, tid, payload={"choice": "approve"})
    r = client.post(f"/sessions/flows/{tid}/done", json={"session": sid, "summary": "The schema file is missing.",
                                                         "status": "blocked"})
    assert r.status_code == 200, r.text
    s = wait(client, tid)
    assert s["waiting"]["step"] == "done_gate" and tests_pass["n"] == 0, s
    assert s["session"]["log"][-1]["status"] == "blocked"


def test_another_connected_session_cannot_report_a_work_order_it_did_not_take(client, repo, no_model, clock):
    one, two = open_session(client), open_session(client, "opencode")
    tid, _s = session_flow(client, repo)
    assert next_order(client, one)["flow"] == tid
    assert next_order(client, two) is None                     # taken by the other, which is connected
    r = client.post(f"/sessions/flows/{tid}/plan", json={"session": two, "plan": PLAN})
    assert r.status_code == 409 and "Another connected session" in r.json()["error"]
    clock["now"] += 200                                        # the first one went away: the second may take it
    assert next_order(client, two)["flow"] == tid


def test_a_gone_session_puts_the_flow_in_the_inbox_with_three_choices(client, repo, no_model, clock):
    tid, s = session_flow(client, repo)                        # no session was ever opened
    eng = client.app.state.engine
    assert client.portal.call(session_service.check_gone, eng) == []           # not 10 minutes yet
    clock["now"] += 601
    assert client.portal.call(session_service.check_gone, eng) == [tid]
    assert client.portal.call(session_service.check_gone, eng) == []           # once per work order
    asked = client.bus.of(tid, "gate.waiting")
    assert len(asked) == 1 and asked[0]["data"]["kind"] == "session"
    s = client.get(f"/threads/{tid}").json()
    assert s["waiting"]["title"] == "The connected session is gone" and s["session"]["gone"] is True
    assert s["waiting"]["choices"] == ["wait", "harness", "stop"]
    assert [c["label"] for c in s["waiting"]["choice_info"]][:2] == ["Keep waiting", "Run this step on keel's harness"]
    # keep waiting: the same work order again, the gone clock starts again
    s = decide(client, tid, payload={"choice": "wait"})
    assert s["waiting"]["kind"] == "session" and s["waiting"]["step"] == "plan" and not s["session"]["gone"], s
    assert client.bus.of(tid, "gate.decided")[-1]["data"]["decision"] == "wait"
    assert no_model == []
    clock["now"] += 601
    assert client.portal.call(session_service.check_gone, eng) == [tid]
    # run this step on keel's harness: keel's own planner plans (the model is called once), then the plan gate
    s = decide(client, tid, payload={"choice": "harness"})
    assert s["waiting"]["step"] == "plan_gate", s
    assert [r.agent for r in no_model] == ["harness-planner"]
    assert s["session"]["log"][-1]["by"] == "keel's harness"
    # the build goes back to the session
    s = decide(client, tid, payload={"choice": "approve"})
    assert s["waiting"]["kind"] == "session" and s["waiting"]["step"] == "build", s
    s = decide(client, tid, "reject", payload={"choice": "stop"})
    assert s["status"] == "stopped", s


def test_a_flow_without_the_runner_still_runs_on_keels_harness(client, repo, no_model):
    tid = start(client, repo, workflow="harness", title="Show the owner name", models=MODELS)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "plan_gate" and "session" not in s, s
    assert [r.agent for r in no_model] == ["harness-planner"]


def test_todos_are_checked():
    assert sessions.todos_of([{"text": "a", "status": "completed"}, "b", {"content": "c", "status": "in-progress"}]) == [
        {"text": "a", "status": "done"}, {"text": "b", "status": "pending"}, {"text": "c", "status": "in_progress"}]
    with pytest.raises(ValueError):
        sessions.todos_of([{"text": "a", "status": "maybe"}])
    with pytest.raises(ValueError):
        sessions.todos_of("a list")


def test_two_sessions_on_two_parallel_flows_each_hold_a_different_work_order(client, repo, no_model, clock):
    """Several agents at once (STU-81): two connected sessions of the same client and two waiting flows of one project.
    Each session gets a different work order and keeps it; neither order is handed out twice, and a session cannot
    report the order the other one holds. Today the oldest order goes to whichever session asks first, not to the
    session that started the flow (keel-v2#217)."""
    one, two = open_session(client), open_session(client)            # both "claude-code", like three Claude Codes
    first, _ = session_flow(client, repo)
    second, _ = session_flow(client, repo)
    got_one, got_two = next_order(client, one), next_order(client, two)
    # With the frozen clock both waits have the same `since`, so "the oldest" is decided by the order the flows
    # were added to the hub (sorted() keeps it on a tie). When #217 hands an order to the session that started
    # the flow, change this test with it.
    assert got_one["flow"] == first and got_two["flow"] == second      # the oldest one to the first that asks
    assert next_order(client, one)["flow"] == first                    # each keeps the order it took
    assert next_order(client, two)["flow"] == second
    r = client.post(f"/sessions/flows/{first}/plan", json={"session": two, "plan": PLAN})
    assert r.status_code == 409 and "Another connected session" in r.json()["error"]
    r = client.post(f"/sessions/flows/{second}/plan", json={"session": two, "plan": PLAN})
    assert r.status_code == 200, r.text
    assert wait(client, second)["waiting"]["step"] == "plan_gate"
    assert wait(client, first)["waiting"]["order"]["kind"] == "plan"   # the other flow still waits for its session
    assert next_order(client, two) is None                             # nothing else waits that two may take
    assert no_model == []


def test_tests_that_keep_failing_stop_for_the_person_then_the_session_hears_it_is_the_same_failure(
        client, repo, no_model, clock, tests_pass):
    """keel's testing loop with a connected session (STU-81, seen live on 0.40.0): a failed test run is a fix order;
    failing again stops the flow for the person ("keeps failing"), never a third silent round; the person's approve
    runs the tests again and the next order tells the session it is the same failure, so it tries another cause."""
    sid = open_session(client)
    tid, _s = session_flow(client, repo)
    client.post(f"/sessions/flows/{tid}/plan", json={"session": sid, "plan": PLAN})
    wait(client, tid)
    tests_pass["fail"] = 99
    decide(client, tid, payload={"choice": "approve"})
    assert next_order(client, sid)["kind"] == "build"
    client.post(f"/sessions/flows/{tid}/done", json={"session": sid, "summary": "Built it."})
    s = wait(client, tid)
    assert s["waiting"]["kind"] == "session" and s["waiting"]["order"]["kind"] == "fix"
    assert "assert 2 == 3" in next_order(client, sid)["failure"]
    client.post(f"/sessions/flows/{tid}/done", json={"session": sid, "summary": "Fixed it, I think."})
    s = wait(client, tid)
    assert s["waiting"]["kind"] == "fix" and "keeps failing" in s["waiting"]["title"], s   # the person decides now
    assert next_order(client, sid) is None
    r = client.post(f"/sessions/flows/{tid}/done", json={"session": sid, "summary": "again"})
    assert r.status_code == 409 and "waits for the person" in r.json()["error"]
    s = decide(client, tid)                                            # "try again"
    checks = tests_pass["n"]
    assert s["waiting"]["kind"] == "session", s
    order = next_order(client, sid)
    assert "SAME FAILURE" in json.dumps(order)
    tests_pass["fail"] = 0
    client.post(f"/sessions/flows/{tid}/done", json={"session": sid, "summary": "Found the real cause."})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "done_gate" and tests_pass["n"] == checks + 1, s
    assert no_model == []
