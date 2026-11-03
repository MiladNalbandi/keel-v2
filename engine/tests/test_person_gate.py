"""A gate only a person answers (`person: true`): no run mode decides it, and an empty `when` never turns it into a free
pass. The Answers plugin's approvals use it (plugins/answers); the Supervisor's policy.NEVER holds its kind, "note"."""

from conftest import decide, start, wait
from keel_engine.models import fake as fake_mod
from keel_engine.runtime import run_mode
from keel_engine.workflows.model import from_dict
from keel_engine.workflows.validate import validate

SETTINGS = {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "run_mode": "auto"}


def wf(steps):
    return from_dict({"name": "custom", "keel_rules": False, "steps": steps})


def test_a_person_gate_stops_in_auto_mode_where_a_plain_gate_does_not(client, repo):
    tid = start(client, repo, workflow=wf([
        {"id": "plain", "kind": "gate", "name": "plain"},
        {"id": "mine", "kind": "gate", "name": "mine", "person": True}]), settings=SETTINGS)
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "mine", s
    assert run_mode.is_auto_line(" ".join(s.get("gate_log") or [])), "the plain gate was auto-approved"


def test_a_person_gate_asks_even_when_its_when_does_not_hold(client, repo):
    tid = start(client, repo, workflow=wf([
        {"id": "mine", "kind": "gate", "name": "mine", "person": True, "when": {"data": "not_there"}}]), settings=SETTINGS)
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "mine", s


def test_a_person_gate_with_a_choice_per_item_stops_in_auto_mode(client, repo, monkeypatch):
    real = fake_mod._plan
    monkeypatch.setattr(fake_mod, "_plan", lambda req: (None, "", 'Two.\n```json\n[{"title": "a"}, {"title": "b"}]\n```', {})
                        if req.agent == "explorer" else real(req))
    tid = start(client, repo, workflow=wf([
        {"id": "list", "kind": "agent", "name": "list", "agent": "explorer", "collect": "rows"},
        {"id": "each", "kind": "gate", "name": "each", "for_each": "rows", "person": True, "choices": ["yes", "no"]}]), settings=SETTINGS)
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "each", s
    s = decide(client, tid, payload={"choice": "no"})
    assert s["status"] == "waiting" and s["waiting"]["step"] == "each", "the second item waits too"


def test_the_waiting_question_says_it_is_a_persons(client, repo):
    tid = start(client, repo, workflow=wf([{"id": "plain", "kind": "gate", "name": "plain"}, {"id": "mine", "kind": "gate", "name": "mine", "person": True}]),
                settings={**SETTINGS, "run_mode": "manual"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "plain" and "person" not in s["waiting"]
    s = decide(client, tid)
    assert s["waiting"]["step"] == "mine" and s["waiting"]["person"] is True


def test_a_waiting_question_says_its_kind_and_the_api_may_say_who_answered(client, repo):
    tid = start(client, repo, workflow=wf([{"id": "g", "kind": "gate", "name": "g"}, {"id": "mine", "kind": "gate", "name": "mine", "person": True}]),
                settings={**SETTINGS, "run_mode": "manual"})
    s = wait(client, tid)
    assert s["waiting"]["gate_kind"] == "gate"
    seen = []
    real = client.bus.emit

    def emit(kind, *a, **k):
        if kind == "gate.decided":
            seen.append(k.get("data"))
        return real(kind, *a, **k)
    client.bus.emit = emit
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve", "by": "slack:Ann (U1)"})
    assert r.status_code == 200, r.text
    s = wait(client, tid)
    assert s["waiting"]["gate_kind"] == "note"
    assert seen[-1]["by"] == "slack:Ann (U1)"
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve", "by": "supervisor"})
    assert r.status_code == 200, r.text
    wait(client, tid)
    assert "by" not in seen[-1], "the route cannot claim to be the Supervisor"


def test_person_belongs_to_a_gate():
    errs = validate(wf([{"id": "a", "kind": "agent", "name": "a", "agent": "explorer", "person": True}]))
    assert any("only a gate takes person" in e for e in errs)
