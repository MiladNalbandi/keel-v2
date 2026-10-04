"""The feature template, end to end in fake mode, through the HTTP API."""

import json
import subprocess
from pathlib import Path

from conftest import decide, start, wait


def git_log(repo):
    return subprocess.run(["git", "log", "--format=%s"], cwd=repo, capture_output=True, text=True).stdout.splitlines()


def run_to_done(client, tid, s):
    """Approve every gate until the flow ends. Returns the final state and the gates seen."""
    seen = []
    for _ in range(50):
        if s["status"] != "waiting":
            return s, seen
        seen.append((s["waiting"]["step"], s["ac"]))
        s = decide(client, tid)
    raise AssertionError("too many gates")


def test_feature_flow_reaches_done(client, repo):
    tid = start(client, repo)
    s = wait(client, tid)
    assert s["status"] == "waiting"
    assert s["waiting"]["step"] == "spec_gate" and s["waiting"]["kind"] == "gate"
    assert s["phase"] == "spec"
    assert [a["id"] for a in s["acs"]] == ["AC-1", "AC-2"]
    assert (Path(repo) / "docs/specs/player-ranks.md").is_file()

    s, gates = run_to_done(client, tid, s)
    assert s["status"] == "done", s
    assert gates == [("spec_gate", None), ("ac_gate", "AC-1"), ("ac_gate", "AC-2"), ("final_review", None)]
    assert all(a["status"] == "done" for a in s["acs"])
    assert s["usage"]["tokens_in"] > 0 and s["checkpoints"] > 10

    log = git_log(repo)
    assert "test(AC-1): Player ranks: the main case works" in log
    assert "feat(AC-2): Player ranks: bad input is refused with a clear message" in log

    # keel v1 mirror
    state = json.loads((Path(repo) / ".keel/state.json").read_text())
    assert state["flow"] == "feature" and state["acs"]["AC-1"]["status"] == "done"
    assert any(e.startswith("ac AC-2 approve") for e in state["gates"]["log"])
    kinds = {json.loads(line)["kind"] for line in (Path(repo) / ".keel/logs/events.jsonl").read_text().splitlines()}
    assert {"agent", "tool", "phase", "gate"} <= kinds

    types = {e["type"] for e in client.bus.of(tid)}
    assert {"thread.started", "step.started", "step.finished", "agent.started", "agent.step", "agent.finished",
            "gate.waiting", "gate.decided", "thread.done"} <= types
    steps = [e["data"] for e in client.bus.of(tid, "agent.step")]
    assert any(d["kind"] == "tool" and d.get("server") == "keel" for d in steps)
    assert any(d["kind"] == "write" and d.get("diff") for d in steps)


def test_reject_at_ac_gate_goes_back_to_red(client, repo):
    tid = start(client, repo)
    wait(client, tid)
    s = decide(client, tid)                       # spec approved
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-1"
    r = client.post(f"/threads/{tid}/resume", json={"decision": "reject"})
    assert r.status_code == 400                   # a reason is required to send back
    s = decide(client, tid, "reject", why="also check the error text")
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-1"   # went round red → green again
    started = [e["step"] for e in client.bus.of(tid, "step.started")]
    assert started.count("red") == 2 and started.count("verify_red") == 2
    prompts_fed = [e for e in client.bus.of(tid, "agent.step") if e["data"]["kind"] == "thinking"]
    assert any("also check the error text" in e["data"]["text"] for e in prompts_fed)
    decided = client.bus.of(tid, "gate.decided")
    assert decided[-1]["data"]["decision"] == "reject"


def test_rewind_continues_from_checkpoint(client, repo):
    tid = start(client, repo)
    wait(client, tid)
    s = decide(client, tid)
    assert s["ac"] == "AC-1"
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-2"
    hist = client.get(f"/threads/{tid}/history").json()
    assert hist == sorted(hist, key=lambda c: -c["n"])
    cp = next(c for c in hist if c["step"] == "spec")   # right after the spec agent
    s = client.post(f"/threads/{tid}/rewind", json={"checkpoint_id": cp["id"]}).json()
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "spec_gate"
    assert all(a["status"] == "todo" for a in s["acs"])
    assert not any(line.startswith("test(") for line in git_log(repo))   # code went back too
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-1"


def test_budget_cap_pauses_and_can_continue(client, repo):
    tid = start(client, repo, settings={"gates_mode": "every-ac", "cap_tokens": 50_000, "on_cap": "pause"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"
    s = decide(client, tid)       # contract (18k) pushes past 50k after spec (44k)
    assert s["status"] == "waiting" and s["waiting"]["kind"] == "budget", s
    assert s["usage"]["tokens_in"] + s["usage"]["tokens_out"] >= 50_000
    assert client.bus.of(tid, "budget.warn")
    s = decide(client, tid, "approve")
    assert s["usage"]["cap_tokens"] > 50_000
    assert s["status"] in ("waiting", "done")


def test_budget_stop(client, repo):
    tid = start(client, repo, settings={"gates_mode": "every-ac", "cap_tokens": 10_000, "on_cap": "stop"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"
    s = decide(client, tid)
    assert s["status"] == "stopped"
    assert client.bus.of(tid, "budget.stop")


def test_guard_reverts_disallowed_write(client, repo):
    tid = start(client, repo, models={"default": {"provider": "fake", "mode": "api", "model": "fake"},
                                     "implementer": {"provider": "fake", "mode": "api", "model": "fake-rogue"}})
    wait(client, tid)
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ac_gate"
    refused = client.bus.of(tid, "guard.refused")
    assert any(e["data"]["path"] == "tests/test_rogue_implementer.py" and e["data"]["phase"] == "green" for e in refused)
    assert not (Path(repo) / "tests/test_rogue_implementer.py").exists()
    assert (Path(repo) / "src/scores/ac_1.py").exists()     # the allowed write stays


def test_stop_and_resume_refused(client, repo):
    tid = start(client, repo)
    wait(client, tid)
    s = client.post(f"/threads/{tid}/stop").json()
    assert s["status"] == "stopped"
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve"})
    assert r.status_code == 409 and "error" in r.json()


def test_gates_mode_end_only_stops_at_last_ac(client, repo):
    tid = start(client, repo, settings={"gates_mode": "end", "cap_tokens": 0, "on_cap": "pause"})
    s = wait(client, tid)
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-2"


def test_fix_flow_and_init_flow(client, repo):
    tid = start(client, repo, workflow="fix", title="Average of one score is wrong")
    s, gates = run_to_done(client, tid, wait(client, tid))
    assert s["status"] == "done" and [g for g, _ in gates] == ["gate_r", "gate_f"]
    assert any(line.startswith("fix(BUG)") for line in git_log(repo))

    tid = start(client, repo, workflow="init", title="Set up keel")
    s, gates = run_to_done(client, tid, wait(client, tid))
    assert s["status"] == "done", s
    assert [g for g, _ in gates] == ["questions", "plan_gate", "hand_over"]
    assert (Path(repo) / ".keel/config.yml").is_file()
    assert {p.name for p in (Path(repo) / "docs/knowledge").glob("*.md")} == {"architecture.md", "domain.md", "conventions.md"}
    assert "chore(setup): keel init" in git_log(repo)


def test_unknown_thread_is_404(client):
    r = client.get("/threads/nope")
    assert r.status_code == 404 and r.json()["error"]


def test_invalid_workflow_refused(client, repo):
    body = {"project_id": "demo", "root": str(repo), "title": "x", "workflow": {"name": "bad", "steps": [
        {"id": "a", "kind": "gate", "name": "g", "back": "zzz"}]}}
    r = client.post("/threads", json=body)
    assert r.status_code == 400 and "back target" in r.json()["hint"]


def test_feature_flow_leaves_nothing_uncommitted(client, repo):
    tid = start(client, repo)
    s, _ = run_to_done(client, tid, wait(client, tid))
    assert s["status"] == "done", s
    log = git_log(repo)
    assert any(m.startswith("e2e") for m in log), log
    assert any(m.startswith("docs(memory)") for m in log), log
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert [l for l in dirty.splitlines() if ".keel/" not in l] == []
