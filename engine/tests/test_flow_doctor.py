"""#101 part 3 keel's flow doctor: a crash is named and logged, keel repairs what it can by itself (a busy provider, a
conversation too long, a stale git lock), and otherwise the flow waits with what happened and what to do; the
improvement report sums up the crash log. The model is scripted (no key, no network)."""

import os
import time

from conftest import decide, start, wait
from keel_engine.flowdoctor import classify, crashlog, report
from keel_engine.flowdoctor import repair as doctor
from keel_engine.harness.providers.base import ProviderError
from keel_engine.workflows.model import from_dict
from scripted_model import answer, scripted

CLAUDE = {"default": {"provider": "claude", "mode": "api", "model": "sonnet"}}
ONE = from_dict({"name": "one", "keel_rules": False,
                 "steps": [{"id": "impl", "kind": "agent", "name": "implement", "agent": "explorer", "phase": "spec"}]})


def test_a_crash_is_named_with_what_to_do():
    c = classify.classify(ProviderError("Anthropic: overloaded_error (529)", retry=True, status=529))
    assert (c.kind, c.repair) == ("overloaded", "wait")
    assert classify.classify("Error code: 401 - invalid x-api-key").kind == "auth"
    assert classify.classify("prompt is too long: 230000 tokens > 200000 maximum").repair == "fresh"
    assert classify.classify("Rate limit reached for requests (429)").kind == "rate-limit"
    assert classify.classify("fatal: Unable to create '/w/.git/index.lock': File exists.").repair == "git-lock"
    assert classify.classify("OSError: [Errno 28] No space left on device").kind == "disk-full"
    assert classify.classify("Your credit balance is too low to access the Anthropic API").kind == "billing"
    bug = classify.classify(KeyError("spec"))
    assert bug.kind == "keel-bug" and bug.repair is None
    assert classify.classify("something odd").kind == "unknown"


def test_where_names_the_place_in_keels_code():
    try:
        report.iso("not a time")       # a TypeError raised inside keel_engine
    except TypeError as exc:
        spot = classify.where(exc)
    assert spot.startswith("flowdoctor/report.py:") and spot.endswith(" in iso")


def test_the_crash_log_keeps_no_secret_and_reads_back(tmp_path):
    crashlog.record(thread="t1", kind="auth", title="The provider refused the key", outcome="asked",
                    error="401 for key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH")
    rows = crashlog.read()
    assert len(rows) == 1 and rows[0]["kind"] == "auth"
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in rows[0]["error"] and "•••" in rows[0]["error"]
    assert crashlog.clear() == 1 and crashlog.read() == []


def test_the_report_counts_each_crash_once_and_says_what_to_improve():
    t = time.time()
    for at, outcome in ((t - 30, "retrying"), (t - 20, "repaired")):
        crashlog.record(at=at, thread="a", step="impl", workflow="change", kind="overloaded", title="The provider was overloaded",
                        provider="claude", outcome=outcome, action="wait", error="529 overloaded")
    for at, outcome in ((t - 10, "asked"), (t - 5, "retried")):
        crashlog.record(at=at, thread="b", step="spec", workflow="feature", kind="auth", title="The provider refused the key",
                        provider="claude", outcome=outcome, error="401 invalid x-api-key")
    crashlog.record(at=t - 3, thread="c", step="impl", workflow="change", kind="auth", title="The provider refused the key",
                    outcome="asked", error="401")
    crashlog.record(at=t - 86_400 * 9, thread="old", step="x", kind="unknown", outcome="failed", error="long ago")
    rep = report.build(days=7, now=t)
    assert rep["total"] == 3 and rep["flows"] == 3 and rep["repaired"] == 1 and rep["retried"] == 1 and rep["waiting"] == 1
    auth, busy = rep["kinds"]
    assert (auth["kind"], auth["count"], auth["retried"], auth["waiting"]) == ("auth", 2, 1, 1)
    assert "Settings › Models & keys" in auth["advice"] and auth["steps"][0] in ("feature › spec", "change › impl")
    assert (busy["kind"], busy["repaired"], busy["providers"]) == ("overloaded", 1, ["claude"])
    md = rep["markdown"]
    assert md.startswith("# keel flow doctor: the last 7 days") and "## What to improve" in md and "long ago" not in md


def test_a_busy_provider_is_waited_out_and_the_step_goes_on(client, repo, monkeypatch):
    busy = ProviderError("Anthropic: overloaded (529)", retry=False, status=529)
    sm = scripted(monkeypatch, [busy, answer("Done: looked at it.")])
    tid = start(client, repo, workflow=ONE, models=CLAUDE)
    s = wait(client, tid)
    assert s["status"] == "done", s
    assert len(sm.runs) == 2
    repair = client.bus.of(tid, "doctor.repair")[0]["data"]
    assert (repair["kind"], repair["action"], repair["attempt"]) == ("overloaded", "wait", 1)
    assert client.bus.of(tid, "doctor.repaired")[0]["data"]["kind"] == "overloaded"
    assert [r["outcome"] for r in crashlog.read()] == ["retrying", "repaired"]
    assert report.build()["repaired"] == 1


def test_a_refused_key_waits_for_the_person_with_what_to_do_and_retry_goes_on(client, repo, monkeypatch):
    refused = ProviderError("Error code: 401 - invalid x-api-key", status=401)
    sm = scripted(monkeypatch, [refused, answer("Done: looked at it.")])
    tid = start(client, repo, workflow=ONE, models=CLAUDE)
    s = wait(client, tid)
    assert s["status"] == "waiting", s
    w = s["waiting"]
    assert w["kind"] == "fix" and "What happened (auth)" in w["detail"]
    assert "The provider refused the key" in w["detail"] and "Settings › Models & keys" in w["detail"]
    assert not client.bus.of(tid, "doctor.repair")         # nothing keel can repair by itself: no wait, no retry
    assert len(sm.runs) == 1
    s = decide(client, tid, "approve")
    assert s["status"] == "done", s
    outcomes = [r["outcome"] for r in crashlog.read()]
    assert outcomes[0] == "asked" and outcomes[-1] == "retried"
    assert report.build()["retried"] == 1


def test_a_conversation_too_long_runs_again_in_a_new_session(client, repo, monkeypatch):
    long = ProviderError("prompt is too long: 230000 tokens > 200000 maximum", status=400)
    sm = scripted(monkeypatch, [long, answer("Done.")])
    tid = start(client, repo, workflow=ONE, models=CLAUDE)
    assert wait(client, tid)["status"] == "done"
    first, again = sm.runs
    assert again.resume is False
    assert client.bus.of(tid, "doctor.repair")[0]["data"]["action"] == "fresh"


def test_the_doctor_gives_up_its_own_repairs_after_two_and_asks(client, repo, monkeypatch):
    sm = scripted(monkeypatch, lambda req, n: ProviderError("Anthropic: overloaded (529)", status=529))
    tid = start(client, repo, workflow=ONE, models=CLAUDE)
    s = wait(client, tid)
    assert s["status"] == "waiting" and "What happened (overloaded)" in s["waiting"]["detail"], s
    assert len(sm.runs) == 3 and len(client.bus.of(tid, "doctor.repair")) == 2
    assert "keel tried 2 repair(s) by itself first" in s["waiting"]["detail"]
    s = decide(client, tid, "reject", why="later")      # the step runs once more on the way (keel's resume), then stops
    assert s["status"] in ("failed", "stopped")
    assert "stopped" in [r["outcome"] for r in crashlog.read()]


def test_a_step_that_is_not_an_agent_waits_with_retry_and_stop(client, repo, monkeypatch):
    from keel_engine.runtime import actions as actions_mod, flow_actions

    calls = {"n": 0}

    def flaky(a):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyError("missing")
        return actions_mod.ActionResult(True, "fine")

    monkeypatch.setitem(flow_actions.ACTIONS, "investigation_note", flaky)
    wf = from_dict({"name": "code", "keel_rules": False,
                    "steps": [{"id": "act", "kind": "code", "name": "the action", "action": "investigation_note", "phase": "none"}]})
    tid = start(client, repo, workflow=wf)
    s = wait(client, tid)
    if s["status"] == "waiting":                  # a code step that raises: the doctor's own pause
        w = s["waiting"]
        assert w["kind"] == "crash" and w["choices"] == ["retry", "stop"] and "What happened (keel-bug)" in w["detail"]
        s = decide(client, tid, "approve", payload={"choice": "retry"})
    assert s["status"] == "done", s
    assert calls["n"] == 2


def test_a_stale_git_lock_is_removed_and_a_fresh_one_is_left(tmp_path, monkeypatch):
    git = tmp_path / ".git"
    git.mkdir()
    lock = git / "index.lock"
    lock.write_text("")
    monkeypatch.setattr(doctor, "_git_running", lambda: False)
    assert doctor.clear_stale_lock(str(tmp_path)) is False and lock.exists()     # just made: a git may hold it
    old = time.time() - 120
    os.utime(lock, (old, old))
    assert doctor.clear_stale_lock(str(tmp_path)) is True and not lock.exists()


def test_the_report_and_the_crash_log_over_http(client):
    crashlog.record(thread="t9", step="impl", workflow="change", kind="network", title="The provider could not be reached",
                    outcome="asked", error="ConnectError: connection refused")
    rep = client.get("/flow-doctor/report?days=3").json()
    assert rep["days"] == 3 and rep["total"] == 1 and rep["kinds"][0]["kind"] == "network"
    got = client.get("/flow-doctor/crashes").json()["crashes"]
    assert got[0]["thread"] == "t9" and got[0]["outcome"] == "asked"
    assert client.delete("/flow-doctor/crashes").json() == {"removed": 1}
    assert client.get("/flow-doctor/report").json()["total"] == 0
