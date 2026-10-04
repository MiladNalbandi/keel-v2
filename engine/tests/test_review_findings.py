"""Blocking findings from a review step stop the flow: fix them (and review again) or go on with a reason."""
import subprocess

from conftest import decide, start, wait


def _reviewer_finds_once(monkeypatch, calls):
    from keel_engine.models import fake as fake_mod
    real = fake_mod._plan

    def plan(req):
        if req.agent == "code-reviewer":
            calls.append(req.feedback)
            if len(calls) == 1:
                return None, "", ("## Review\n\n**Blocking**\n\n- `src/scores/ac_1.py:2` returns a constant, not the rank.\n\n"
                                  "**Non-blocking**\n- naming"), {}
            return None, "", "**Blocking:** none.\n\nLooks right now.", {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)


def _to(client, tid, s, step):
    for _ in range(40):
        if s["status"] == "waiting" and s["waiting"]["step"] == step:
            return s
        assert s["status"] == "waiting", s
        s = decide(client, tid)
    raise AssertionError(f"never reached {step}")


def test_fix_them_runs_the_implementer_commits_and_reviews_again(client, repo, monkeypatch):
    calls = []
    _reviewer_finds_once(monkeypatch, calls)
    tid = start(client, repo)
    s = _to(client, tid, wait(client, tid), "integration__fix")
    w = s["waiting"]
    assert w["title"] == "integration: 1 blocking finding(s)"
    assert "returns a constant, not the rank" in w["detail"] and "naming" not in w["detail"]
    assert w["labels"] == {"approve": "Fix them", "reject": "Go on anyway"}

    s = decide(client, tid, "approve")
    log = subprocess.run(["git", "log", "--format=%s"], cwd=repo, capture_output=True, text=True).stdout
    assert "fix(review): address integration findings" in log
    assert len(calls) == 2, "the review runs again after the fix"
    assert s["waiting"]["step"] != "integration__fix"


def test_go_on_anyway_needs_a_reason_and_is_logged(client, repo, monkeypatch):
    calls = []
    _reviewer_finds_once(monkeypatch, calls)
    tid = start(client, repo)
    _to(client, tid, wait(client, tid), "integration__fix")
    r = client.post(f"/threads/{tid}/resume", json={"decision": "reject"})
    assert r.status_code == 400
    s = decide(client, tid, "reject", why="known limitation, ticket 42")
    assert len(calls) == 1, "no second review when the findings are accepted"
    assert any("integration findings accepted: known limitation" in x for x in s["gate_log"])
    assert s["waiting"]["step"] != "integration__fix"
