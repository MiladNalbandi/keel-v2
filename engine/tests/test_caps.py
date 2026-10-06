"""v0.4.2 project caps in the engine: the dollar cap (settings.cap_usd against usage.cost_usd) and the default per-step
token cap (settings.step_cap_tokens) next to a step's own max_tokens."""

import pytest

from conftest import decide, start, wait
from keel_engine.models import fake as fake_mod
from keel_engine.runtime.compiler import _step_cap
from keel_engine.workflows.model import Step, from_dict

SETTINGS = {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}


def wf(steps, name="caps"):
    return from_dict({"name": name, "keel_rules": False, "steps": steps})


def agent(i, **extra):
    return {"id": f"a{i}", "kind": "agent", "name": f"look {i}", "agent": "explorer", "phase": "spec", **extra}


THREE = [agent(1), agent(2), agent(3)]


@pytest.fixture
def costly(monkeypatch):
    """Every fake agent run reports $0.40 (the fake model reports no cost of its own)."""
    real = fake_mod.FakeRunner.run

    async def run(self, req, emit):
        res = await real(self, req, emit)
        res.cost_usd = 0.4
        return res
    monkeypatch.setattr(fake_mod.FakeRunner, "run", run)


def test_the_dollar_cap_is_kept_and_pauses_before_the_next_agent(client, repo, costly):
    tid = start(client, repo, workflow=wf(THREE), settings={**SETTINGS, "cap_usd": 0.7})
    s = wait(client, tid)
    # a1 ($0.40) and a2 ($0.80) ran; before a3 the cost is past $0.70
    assert s["status"] == "waiting" and s["waiting"]["kind"] == "budget", s
    assert s["waiting"]["step"] == "a3" and s["waiting"]["title"] == "Cost cap reached"
    assert "$0.80 of $0.70" in s["waiting"]["detail"]
    assert s["usage"]["cap_usd"] == 0.7 and s["usage"]["cost_usd"] == pytest.approx(0.8)
    s = decide(client, tid, "approve")
    assert s["status"] == "done", s
    assert s["usage"]["cap_usd"] == pytest.approx(1.5)          # continued past the cap: it doubled
    assert s["usage"]["cost_usd"] == pytest.approx(1.2)


def test_the_dollar_cap_has_its_own_action(client, repo, costly):
    tid = start(client, repo, workflow=wf(THREE), settings={**SETTINGS, "cap_usd": 0.7, "on_cap_usd": "stop"})
    s = wait(client, tid)
    assert s["status"] == "stopped", s
    stop = client.bus.of(tid, "budget.stop")[-1]["data"]
    assert stop["limit"] == "cost" and stop["cap_usd"] == 0.7
    ran = [e["step"] for e in client.bus.of(tid, "agent.started")]
    assert ran == ["a1", "a2"]                    # a3's agent never ran


def test_the_dollar_cap_warns_at_80_percent(client, repo, costly):
    tid = start(client, repo, workflow=wf(THREE), settings={**SETTINGS, "cap_usd": 1.0})
    s = wait(client, tid)
    warn = [e["data"] for e in client.bus.of(tid, "budget.warn")]
    assert any(w.get("cap_usd") == 1.0 and w["pct"] == 80 for w in warn), warn   # before a3, at $0.80
    assert s["status"] == "done"                                                # a3 ran: $1.20 is past, but no step is left


def test_no_dollar_cap_means_no_dollar_check(client, repo, costly):
    tid = start(client, repo, workflow=wf(THREE), settings=SETTINGS)
    s = wait(client, tid)
    assert s["status"] == "done" and s["usage"]["cap_usd"] == 0


def test_the_new_settings_are_not_dropped_and_are_checked(client, repo):
    body = {"project_id": "demo", "root": str(repo), "workflow": wf(THREE).model_dump(), "title": "x",
            "settings": {**SETTINGS, "cap_usd": -1}}
    assert client.post("/threads", json=body).status_code in (400, 422)
    body["settings"] = {**SETTINGS, "step_on_cap": "explode"}
    assert client.post("/threads", json=body).status_code in (400, 422)


def test_step_cap_picks_the_smaller_limit():
    plain = Step(id="s", kind="agent", name="s", agent="explorer")
    own = Step(id="s", kind="agent", name="s", agent="explorer", max_tokens=5_000)
    assert _step_cap(plain, {}) == (0, False)
    assert _step_cap(plain, {"step_cap_tokens": 9_000}) == (9_000, False)        # the default for a step without max_tokens
    assert _step_cap(own, {"step_cap_tokens": 9_000}) == (5_000, True)           # the step's own smaller limit wins
    assert _step_cap(own, {"step_cap_tokens": 2_000}) == (2_000, False)          # a smaller project cap wins
    assert _step_cap(own, {}) == (5_000, True)


# A gate that sends a1 back runs it a second time; the step cap is checked before that second run (44k used).
AGAIN = [agent(1), {"id": "g", "kind": "gate", "name": "check", "back": "a1", "phase": "spec"}, agent(2)]


def to_second_run(client, tid):
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "g", s
    return decide(client, tid, "reject", why="look again")


def test_step_cap_tokens_is_the_default_for_a_step_without_max_tokens(client, repo):
    tid = start(client, repo, workflow=wf(AGAIN), settings={**SETTINGS, "step_cap_tokens": 10_000, "step_on_cap": "stop"})
    s = to_second_run(client, tid)
    assert s["status"] == "stopped", s
    stop = client.bus.of(tid, "budget.stop")[-1]["data"]
    assert stop["limit"] == "step" and stop["step_cap"] == 10_000 and stop["step_used"] >= 44_000


def test_a_steps_own_smaller_max_tokens_wins_with_its_own_action(client, repo):
    steps = [agent(1, max_tokens=8_000), *AGAIN[1:]]
    tid = start(client, repo, workflow=wf(steps), settings={**SETTINGS, "step_cap_tokens": 1_000_000, "step_on_cap": "stop"})
    s = to_second_run(client, tid)
    # the step's own 8k binds, so step_on_cap (stop) does not apply: on_cap pauses and asks
    assert s["status"] == "waiting" and s["waiting"]["kind"] == "budget", s
    assert "of 8,000 tokens for this step" in s["waiting"]["detail"]


def test_a_smaller_project_step_cap_wins_over_a_steps_max_tokens(client, repo):
    steps = [agent(1, max_tokens=5_000_000), *AGAIN[1:]]
    tid = start(client, repo, workflow=wf(steps), settings={**SETTINGS, "step_cap_tokens": 10_000, "step_on_cap": "stop"})
    s = to_second_run(client, tid)
    assert s["status"] == "stopped", s


def test_no_step_cap_lets_a_step_run_again(client, repo):
    tid = start(client, repo, workflow=wf(AGAIN), settings=SETTINGS)
    s = to_second_run(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "g", s
