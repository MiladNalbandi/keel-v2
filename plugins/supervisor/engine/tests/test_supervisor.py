"""The Supervisor (docs/plans/supervisor.md): off by default, shadow mode only suggests, act mode answers through the
engine as `by: supervisor`, and every safety rule: the never-kinds and cap titles, only offered answers, confidence and
evidence, repeat limits, its own and the flow's budget, a stale question. Fake model only (KEEL_FAKE_SUPERVISOR)."""

import json
from pathlib import Path

import pytest
import yaml

from conftest import decide, start, wait
from keel_engine import extensions
from keel_engine.runtime import side_usage
from keel_engine.workflows.model import from_dict
from keel_plugin_supervisor import VERSION, ledger, policy, verdict

SETTINGS = {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "plugins": ["supervisor"]}
GOOD = {"action": "answer", "decision": "approve", "why": "the check passed and matches the spec", "confidence": "high",
        "evidence": ["knowledge:conventions"], "summary": "Looks right."}


def wf(steps, name="sup"):
    return from_dict({"name": name, "keel_rules": False, "steps": steps})


def agent(i, phase="green", **extra):
    return {"id": f"a{i}", "kind": "agent", "name": f"work {i}", "agent": "implementer", "phase": phase, **extra}


def gate(gid="g1", phase="green", **extra):
    return {"id": gid, "kind": "gate", "name": f"check {gid}", "phase": phase, **extra}


GATED = [agent(1), gate(), agent(2)]


@pytest.fixture
def client(client):
    """keel's client with the Supervisor's keel-wide switch on (it is off until the person turns it on); projects stay
    off, so each test's flow turns it on for itself (test_switch.py tests the switches)."""
    client.portal.call(lambda: client.app.state.supervisor.set_switch(keel=True))
    return client


@pytest.fixture
def script(tmp_path, monkeypatch):
    """What the fake Supervisor answers: one verdict, a list (one per turn) or {kind: verdict}."""
    def put(v):
        p = tmp_path / "sup.json"
        p.write_text(json.dumps(v))
        monkeypatch.setenv("KEEL_FAKE_SUPERVISOR", str(p))
        verdict.reset_fake()
    return put


def sup_of(client):
    return client.app.state.supervisor


def idle(client):
    client.portal.call(sup_of(client).idle)


def run(client, repo, steps=GATED, sup=None, **settings):
    s = {**SETTINGS, **settings}
    if sup is not None:
        s["supervisor"] = sup
    tid = start(client, repo, workflow=wf(steps), settings=s)
    st = wait(client, tid)
    idle(client)
    return tid, wait(client, tid)


def rows(tid):
    return ledger.list_rows(thread_id=tid)


def events(client, tid, type_):
    return [e["data"] for e in client.bus.of(tid, type_)]


def test_the_plugin_version_matches_its_manifest():
    yml = yaml.safe_load((Path(__file__).resolve().parents[2] / "keel-plugin.yml").read_text())
    assert yml["version"] == VERSION and yml["name"] == "supervisor"
    assert "supervisor" in [p.name for p in extensions.parts()]


def test_it_is_off_unless_a_flow_turns_it_on(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo)
    assert s["status"] == "waiting" and rows(tid) == []


def test_it_stays_off_while_the_plugin_is_off_for_the_project(client, repo, script):
    script(GOOD)
    tid = start(client, repo, workflow=wf(GATED), settings={**SETTINGS, "plugins": [], "supervisor": {"on": True, "mode": "act"}})
    wait(client, tid)
    idle(client)
    assert wait(client, tid)["status"] == "waiting" and rows(tid) == []
    r = client.post(f"/threads/{tid}/supervisor", json={"on": True})
    assert r.status_code == 409 and "Control › Plugins" in r.text
    assert client.get(f"/threads/{tid}/supervisor").json()["settings"]["on"] is False


def test_shadow_mode_only_suggests(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo, sup={"on": True})
    assert s["status"] == "waiting" and s["waiting"]["step"] == "g1"
    [row] = rows(tid)
    assert row["outcome"] == "suggested" and row["code"] == "shadow" and row["confidence"] == "high"
    [esc] = events(client, tid, "supervisor.escalated")
    assert esc["suggestion"]["decision"] == "approve" and esc["suggestion"]["why"].startswith("supervisor: ")
    assert row["tokens"] > 0 and side_usage.of_source(tid, "supervisor")["tokens"] == row["tokens"]


def test_act_mode_answers_as_the_supervisor(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo, sup={"on": True, "mode": "act"})
    assert s["status"] == "done", s
    [row] = rows(tid)
    assert row["outcome"] == "applied" and row["decision"] == "approve"
    [g] = events(client, tid, "gate.decided")
    assert g["by"] == "supervisor" and g["why"] == "supervisor: the check passed and matches the spec"
    assert events(client, tid, "supervisor.decided")
    lines = [line for _who, fn in extensions.hooks("pr_body_sections") for line in fn(tid)]
    assert lines[0] == "Supervisor answered 1 gate(s) in this flow:" and "check passed" in lines[1]


def test_turning_it_on_while_a_gate_waits_looks_at_once(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo)
    r = client.post(f"/threads/{tid}/supervisor", json={"on": True, "mode": "act"})
    assert r.status_code == 200, r.text
    idle(client)
    assert wait(client, tid)["status"] == "done"
    assert client.get(f"/threads/{tid}/supervisor").json()["decisions"][0]["outcome"] == "applied"


@pytest.mark.parametrize("v,code", [
    ({**GOOD, "confidence": "medium"}, "low-confidence"),
    ({**GOOD, "evidence": []}, "no-evidence"),
    ({**GOOD, "evidence": ["no/such/file.py:3"]}, "bad-evidence"),
    ({**GOOD, "evidence": ["../../etc/passwd"]}, "bad-evidence"),
    ({**GOOD, "choice": "retry"}, "not-offered"),
    ({**GOOD, "action": "escalate"}, "model-escalated"),
    ({"no": "verdict"}, "model-escalated"),
])
def test_an_unsure_or_unoffered_verdict_is_escalated_and_the_flow_waits(client, repo, script, v, code):
    script(v)
    tid, s = run(client, repo, sup={"on": True, "mode": "act"})
    assert s["status"] == "waiting", s
    [row] = rows(tid)
    assert row["outcome"] == "escalated" and row["code"] == code, row


def test_a_send_back_carries_its_reason_and_a_loop_stops_at_the_repeat_limit(client, repo, script):
    script({**GOOD, "action": "send_back", "why": "AC-1 wants a 404, the code gives 500"})
    tid, s = run(client, repo, sup={"on": True, "mode": "act"})
    for _ in range(5):                       # each send-back brings the same gate back: it looks again
        idle(client)
        s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "g1"
    newest, *older = rows(tid)
    assert [r["outcome"] for r in older] == ["applied", "applied"] and all(r["decision"] == "reject" for r in older)
    assert newest["code"] == "repeat" and newest["tokens"] == 0
    gates = events(client, tid, "gate.decided")
    assert len(gates) == 2 and all(g["by"] == "supervisor" and "404" in g["why"] for g in gates)


def test_spec_gates_are_only_suggested_by_default(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo, steps=[agent(1, "spec"), gate(phase="spec"), agent(2)], sup={"on": True, "mode": "act"})
    assert s["status"] == "waiting"
    assert rows(tid)[0]["code"] == "policy-escalate" and rows(tid)[0]["kind"] == "spec"


def test_the_token_cap_is_never_answered_and_costs_no_turn(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo, steps=[agent(1), agent(2)], sup={"on": True, "mode": "act"}, cap_tokens=10)
    assert s["waiting"]["title"] == "Token cap reached", s
    [row] = rows(tid)
    assert row["code"] == "never-kind" and row["tokens"] == 0 and row["kind"] == "budget"
    assert side_usage.of_source(tid, "supervisor")["tokens"] == 0


def test_never_kinds_cannot_be_turned_on(client, repo, script):
    script(GOOD)
    tid, _ = run(client, repo)
    r = client.post(f"/threads/{tid}/supervisor", json={"policy": {"budget": "may"}})
    assert r.status_code == 400 and "only you" in r.text
    assert client.post(f"/threads/{tid}/supervisor", json={"mode": "auto"}).status_code == 400
    assert policy.rule("final-review", "", {"policy": {"final-review": "may"}}) == "never"
    assert policy.rule("gate", "Cost cap reached", {}) == "never"


def test_the_same_question_is_answered_twice_at_most(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo)
    for _ in range(2):
        ledger.record(project_id="demo", thread_id=tid, question_id=s["waiting"]["id"], step="g1", trigger="t",
                      action="answer", outcome="applied")
    client.post(f"/threads/{tid}/supervisor", json={"on": True, "mode": "act"})
    idle(client)
    assert wait(client, tid)["status"] == "waiting"
    assert rows(tid)[0]["code"] == "repeat" and rows(tid)[0]["tokens"] == 0


def test_too_many_answers_pause_it_for_the_flow(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo, sup={"on": True, "mode": "act", "max_answers": 0})
    assert s["status"] == "waiting" and rows(tid)[0]["code"] == "too-many"
    assert events(client, tid, "supervisor.paused")


def test_its_own_cap_stops_it(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo)
    side_usage.record(tid, "supervisor", {"tokens_in": 200_000})
    client.post(f"/threads/{tid}/supervisor", json={"on": True, "mode": "act"})
    idle(client)
    assert rows(tid)[0]["code"] == "budget-own" and wait(client, tid)["status"] == "waiting"


def test_a_turn_that_would_pass_the_flows_cap_does_not_run(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo, cap_tokens=10_000_000)
    used = s["usage"]["tokens_in"] + s["usage"]["tokens_out"]
    side_usage.record(tid, "other", {"tokens_in": 10_000_000 - used - 1000})
    client.post(f"/threads/{tid}/supervisor", json={"on": True, "mode": "act"})
    idle(client)
    assert rows(tid)[0]["code"] == "budget-flow" and rows(tid)[0]["tokens"] == 0


def test_the_persons_answer_first_makes_its_answer_stale(client, repo, script):
    script(GOOD)
    tid, s = run(client, repo)
    sup = sup_of(client)
    decide(client, tid)
    row = {"project_id": "demo", "thread_id": tid, "question_id": s["waiting"]["id"], "step": "g1", "trigger": "t"}
    out = client.portal.call(lambda: sup._apply(row, s, s["waiting"], "approve", "supervisor: x", {}))
    assert out["outcome"] == "stale"


def test_offered_answers_only_for_the_explorers_questions():
    q = {"kind": "clarify", "title": "Questions", "options": ["approve", "reject"],
         "questions": [{"id": "db", "options": [{"label": "Postgres"}, {"label": "SQLite"}]},
                       {"id": "many", "multiple": True, "options": [{"label": "a"}, {"label": "b"}]}]}
    assert policy.offered_problem(q, "approve", "w", {"answers": {"db": "Postgres", "many": ["a", "b"]}}) is None
    assert policy.offered_problem(q, "approve", "w", {"answers": {"db": "MySQL"}})
    assert policy.offered_problem(q, "approve", "w", {"answers": {"db": ["Postgres", "SQLite"]}})
    assert policy.offered_problem(q, "approve", "w", {"answers": {"nope": "a"}})
    assert policy.offered_problem({**q, "options": ["reject"]}, "approve", "w", {})


def test_a_verdict_is_read_from_the_last_json_block():
    v = verdict.parse('I think {"a": 1}\n```json\n{"action": "answer", "confidence": "HIGH", "evidence": "x.py"}\n```')
    assert v["action"] == "answer" and v["confidence"] == "high" and v["evidence"] == ["x.py"]
    assert verdict.parse("no json here") is None
    assert verdict.parse('{"action": "launch-missiles"}')["action"] == "escalate"


def test_a_verdict_can_name_the_commands_of_an_own_fix():
    # 0.18.63 a stuck setup rung: the Supervisor's better fix goes to the gate as own_fix commands
    v = verdict.parse('```json\n{"action": "escalate", "choice": "own_fix", "commands": "composer update -W\\n\\nphp -v"}\n```')
    assert v["commands"] == ["composer update -W", "php -v"]
    assert verdict.to_answer(v)[2] == {"choice": "own_fix", "commands": ["composer update -W", "php -v"]}
    assert "commands" not in verdict.to_answer(verdict.normalize({"action": "answer"}))[2]


def test_without_its_own_model_it_looks_one_step_above_the_projects_default():
    # 0.18.64 it judges what the other agents could not settle: not the same model that wrote the work
    from keel_plugin_supervisor.supervisor import model_for

    sonnet = {"provider": "claude", "model": "sonnet"}
    assert model_for({}, {"default": sonnet})["model"] == "opus"
    assert model_for({}, {"default": {"provider": "codex", "model": "gpt-5", "effort": "medium"}})["effort"] == "high"
    assert model_for({}, {"default": sonnet, "supervisor": {"provider": "claude", "model": "haiku"}})["model"] == "haiku"
    assert model_for({"model": {"provider": "fake", "model": "fake"}}, {"default": sonnet})["model"] == "fake"


def test_its_log_keeps_the_commands_of_an_own_fix_and_an_old_table_gets_the_column():
    from keel_engine.runtime import db

    with db.connect() as conn:
        conn.execute("drop table if exists supervisor_decisions")
        conn.execute(ledger._TABLE.replace(", commands_json text", ""))
    ledger._ready.clear()
    r = ledger.record(project_id="p", thread_id="t-old", trigger="gate.waiting", action="escalate", outcome="escalated",
                      choice="own_fix", commands=["tools/lock.py --all"])
    assert r["commands"] == ["tools/lock.py --all"]
    assert ledger.list_rows(thread_id="t-old")[0]["commands"] == ["tools/lock.py --all"]

