"""0.18.64 a model for one flow, or one step of it (POST /threads/{id}/models), and what an answer really ran.

The field report: changing "planning" meant changing the explorer for the whole project (clarify and contract too), and
keel_answer said "(reject)" for an answer that ran the gate's edit choice."""

from conftest import decide, start, to_loop, wait
from keel_engine.runtime import choices, thread_models
from keel_engine.workflows.templates import get_template

FAKE = {"provider": "fake", "mode": "api", "model": "fake"}


def started(client, tid, step):
    return [e["data"] for e in client.bus.of(tid, "agent.started") if e.get("step") == step]


def test_a_steps_and_an_agents_model_change_this_flow_only(client, repo):
    tid = start(client, repo)
    other = start(client, repo)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"
    view = client.get(f"/threads/{tid}/models").json()
    assert {"id": "green", "agent": "implementer", "phase": "green"}.items() <= next(x for x in view["steps"] if x["id"] == "green").items()
    assert view["picks"] == []

    r = client.post(f"/threads/{tid}/models", json={"step": "green", "model": {**FAKE, "effort": "medium"}})
    assert r.status_code == 200 and r.json()["changed"] == ["step:green"]
    r = client.post(f"/threads/{tid}/models", json={"agent": "test-author", "model": {**FAKE, "effort": "low"}})
    assert r.json()["changed"] == ["test-author"] and sorted(r.json()["picks"]) == ["step:green", "test-author"]
    assert client.bus.of(tid, "thread.models")[-1]["data"]["changed"] == ["test-author"]

    s = to_loop(client, tid)
    while s.get("status") == "waiting" and s["waiting"]["step"] != "ac_gate":
        s = decide(client, tid)
    assert started(client, tid, "green")[0]["effort"] == "medium"
    assert started(client, tid, "red")[0]["effort"] == "low"
    # the other flow and a fresh context (an engine restart) keep what each was given
    assert client.get(f"/threads/{other}/models").json()["picks"] == []
    client.app.state.engine.ctxs.pop(tid)
    assert client.get(f"/threads/{tid}/models").json()["models"]["step:green"]["effort"] == "medium"
    # no model: the pick goes, and the agent's entry is what the flow started with again
    back = client.post(f"/threads/{tid}/models", json={"agent": "test-author"}).json()
    assert "test-author" not in back["models"] and back["picks"] == ["step:green"]


def test_a_role_with_a_phase_changes_that_agents_steps_in_the_phase_and_bad_targets_say_what_there_is(client, repo):
    tid = start(client, repo)
    wait(client, tid)
    r = client.post(f"/threads/{tid}/models", json={"agent": "explorer", "phase": "spec", "model": FAKE}).json()
    spec_steps = [s.id for s in get_template("feature").steps if s.agent == "explorer" and s.phase == "spec"]
    assert r["changed"] == [f"step:{i}" for i in spec_steps] and len(spec_steps) > 1
    bad = client.post(f"/threads/{tid}/models", json={"step": "nope", "model": FAKE})
    assert bad.status_code == 400 and "Its agent steps:" in bad.json()["error"] and "green" in bad.json()["error"]
    bad = client.post(f"/threads/{tid}/models", json={"agent": "pr-reviewer", "model": FAKE})
    assert bad.status_code == 400 and "this flow does not run pr-reviewer" in bad.json()["error"]
    bad = client.post(f"/threads/{tid}/models", json={"step": "green", "agent": "explorer", "model": FAKE})
    assert bad.status_code == 400 and "runs implementer" in bad.json()["error"]
    assert client.post("/threads/nope/models", json={"agent": "explorer"}).status_code == 404


def test_the_targets_and_the_static_model():
    wf = get_template("feature")
    assert thread_models.targets(wf, "reviewer", None, None) == (["lens-reviewer"], None)    # an old name: the new agent
    assert thread_models.targets(wf, "implementer", None, None) == (["implementer"], None)
    green = next(s for s in wf.steps if s.id == "green")
    models = {"default": FAKE, "implementer": {**FAKE, "model": "fake-lab"}, "step:green": {**FAKE, "effort": "high"}}
    assert thread_models.static_model(models, green, "implementer")["effort"] == "high"
    models.pop("step:green")
    assert thread_models.static_model(models, green, "implementer")["model"] == "fake-lab"


def test_answered_names_the_choice_that_runs():
    spec = {"step": "spec_gate", "title": "spec approval", "options": ["approve"],
            "choices": ["approve", "edit", "reject"], "detail": "x"}
    assert choices.answered(spec, "reject", {"choice": "edit"})["choice"] == "edit"
    assert choices.answered(spec, "approve", None)["choice"] == "approve"         # the default choice
    assert choices.answered(spec, "reject", None)["choice"] == "reject"
    plain = {"step": "g", "kind": "gate", "title": "a gate", "options": ["approve", "reject"]}
    a = choices.answered(plain, "reject", None)
    assert "choice" not in a and a["decision"] == "reject" and "sends it back" in a["does"]


def test_the_resume_answer_says_what_ran(client, repo):
    tid = start(client, repo)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"
    r = client.post(f"/threads/{tid}/resume", json={"decision": "reject", "why": "say what a tie does"})
    assert r.status_code == 200 and r.json()["answered"]["decision"] == "reject"
