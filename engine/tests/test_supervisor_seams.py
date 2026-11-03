"""The Supervisor's core seams (docs/plans/supervisor.md P0): in-process event listeners, who answered a gate (`by`),
and the side-usage ledger that a flow's caps count."""

from conftest import decide, start, wait
from keel_engine.events import EventBus
from keel_engine.runtime import side_usage
from keel_engine.workflows.model import from_dict

SETTINGS = {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}


def wf(steps, name="seams"):
    return from_dict({"name": name, "keel_rules": False, "steps": steps})


def agent(i, **extra):
    return {"id": f"a{i}", "kind": "agent", "name": f"look {i}", "agent": "explorer", "phase": "spec", **extra}


GATED = [agent(1), {"id": "g1", "kind": "gate", "name": "check one", "phase": "spec"}, agent(2)]


def test_a_listener_hears_every_event_and_its_errors_never_reach_the_flow():
    bus = EventBus()
    heard = []

    def boom(_ev):
        raise RuntimeError("listener bug")
    bus.listen(boom)
    bus.listen(heard.append)
    bus.listen(heard.append)                    # the same listener once only
    ev = bus.emit("gate.waiting", "t1", "p1", step="g", data={"kind": "gate"})
    assert ev and heard == [ev]
    bus.unlisten(heard.append)
    bus.emit("thread.done", "t1", "p1")
    assert len(heard) == 1


def run_to_gate(client, repo):
    tid = start(client, repo, workflow=wf(GATED), settings=SETTINGS)
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "g1", s
    return tid, s


def decided(client, tid):
    return [e["data"] for e in client.bus.of(tid, "gate.decided")]


def test_an_in_process_answer_names_who_gave_it(client, repo):
    tid, s = run_to_gate(client, repo)
    eng = client.app.state.engine
    client.portal.call(lambda: eng.resume(tid, "approve", "supervisor: looks right", None,
                                          question_id=s["waiting"]["id"], by="supervisor"))
    assert wait(client, tid)["status"] == "done"
    [gate] = decided(client, tid)
    assert gate["by"] == "supervisor" and gate["why"] == "supervisor: looks right"


def test_the_persons_answer_carries_no_by(client, repo):
    tid, _ = run_to_gate(client, repo)
    assert decide(client, tid)["status"] == "done"
    assert all("by" not in g for g in decided(client, tid))


def test_the_resume_route_cannot_claim_to_be_the_supervisor(client, repo):
    tid, _ = run_to_gate(client, repo)
    assert decide(client, tid, by="supervisor", payload={"by": "supervisor"})["status"] == "done"
    assert all("by" not in g for g in decided(client, tid))


def test_side_usage_adds_up_per_thread_and_source():
    side_usage.record("t1", "supervisor", {"tokens_in": 1000, "tokens_out": 200, "tokens_cached": 5000, "cost_usd": 0.5})
    side_usage.record("t1", "other", {"tokens_in": 10})
    side_usage.record("t2", "supervisor", {"tokens_in": 99})
    assert side_usage.total("t1") == {"tokens": 1710, "cost_usd": 0.5}
    assert side_usage.of_source("t1", "supervisor") == {"tokens": 1700, "cost_usd": 0.5}
    assert side_usage.total("nobody") == {"tokens": 0, "cost_usd": 0.0}


def test_the_flows_token_cap_counts_what_side_agents_spent(client, repo):
    tid, _ = run_to_gate(client, repo)
    eng = client.app.state.engine
    s = client.get(f"/threads/{tid}").json()
    own = int(s["usage"].get("tokens_in", 0)) + int(s["usage"].get("tokens_out", 0))
    # a cap the flow's own use would not reach before a2, but its Supervisor's use does
    client.portal.call(lambda: eng.set_mode(tid, "manual"))
    side_usage.record(tid, "supervisor", {"tokens_in": 10_000_000})
    tid2 = start(client, repo, workflow=wf(GATED), settings={**SETTINGS, "cap_tokens": own * 50 + 10_000})
    s2 = wait(client, tid2)
    assert s2["waiting"]["step"] == "g1", s2           # no side use on this flow: no cap gate
    side_usage.record(tid2, "supervisor", {"tokens_in": 10_000_000})
    s2 = decide(client, tid2)
    assert s2["status"] == "waiting" and s2["waiting"]["title"] == "Token cap reached", s2


def test_a_decided_gate_carries_the_kind_of_the_pause(client, repo):
    tid, _ = run_to_gate(client, repo)
    decide(client, tid, "approve", "fine")
    wait(client, tid)
    assert decided(client, tid)[0]["gate_kind"] == "spec"


def test_a_run_mode_answer_carries_no_kind_of_a_person(client, repo):
    tid = start(client, repo, workflow=wf(GATED), settings={**SETTINGS, "run_mode": "auto"})
    wait(client, tid)
    assert all("gate_kind" not in d for d in decided(client, tid))


def test_shell_readonly_lets_a_run_edit_but_refuses_a_shell_that_changes_git(repo):
    """0.21.0 the Supervisor's small fix: the ToolBox (API-key models) and the hook (CLI models) refuse the same commands."""
    from keel_engine import hook
    from keel_engine.tools.agent_tools import ToolBox

    box = ToolBox(str(repo), "green", agent="supervisor-fix")
    box.shell_readonly = True
    ctx = {"root": str(repo), "phase": "green", "shell_readonly": True, "agent": "supervisor-fix"}
    for cmd in ("git push origin HEAD", "git merge main", "git commit -m x", "git reset HEAD~1", "rm README.md"):
        assert box.run_command(cmd).startswith("REFUSED"), cmd
        assert hook.decide("Bash", {"command": cmd}, dict(ctx)), cmd
    assert hook.decide("Bash", {"command": "ls"}, dict(ctx)) is None
    assert hook.decide("Bash", {"command": "git push origin HEAD"}, {**ctx, "shell_readonly": False}) is None     # a flow's own agents: as before
    assert box.write_file("src/shell_readonly_edit.py", "x = 1\n").startswith("Wrote")


def test_the_context_file_carries_shell_readonly(repo):
    from types import SimpleNamespace

    from keel_engine.runtime import guard_ctx
    from keel_engine.tools.agent_tools import ToolBox

    box = ToolBox(str(repo), "green")
    box.shell_readonly = True
    req = SimpleNamespace(toolbox=box, root=str(repo), phase="green", ac=None, agent="supervisor-fix", thread="t", model={})
    assert guard_ctx.context_for(req)["shell_readonly"] is True
