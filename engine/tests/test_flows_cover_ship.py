"""cover and ship (content/workflows/cover.yaml, ship.yaml), the include step, and the engine keys they use:
soft checks, retry_only, rounds, review back/redo/routes, the skip menu, choice gates and the final verdict report."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from conftest import decide, start, wait
from test_flows_base import answers, values, wf
from keel_engine.runtime import compiler as comp
from keel_engine.runtime import ship, verdict_actions, verdicts
from keel_engine.runtime.actions import ActionInput, ActionResult, run_action
from keel_engine.workflows import templates
from keel_engine.workflows.model import WorkflowError, from_dict
from keel_engine.workflows.templates import get_template
from keel_engine.workflows.validate import validate

SETTINGS = {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}
BLOCK = "## Blocking\n- tests/test_cover.py:3 asserts what the code does, not what it should do\n\nBLOCKING: yes"
CLEAN = "No findings.\n\nBLOCKING: no"


def script(monkeypatch, plan):
    """Scripted results for some actions (each list is used in order, its last entry repeats); the rest run for real."""
    real = comp.run_action
    calls = []

    async def run(action, a):
        calls.append(action)
        seq = plan.get(action)
        if seq is None:
            return await real(action, a)
        r = seq.pop(0) if len(seq) > 1 else seq[0]
        return r(a) if callable(r) else r
    monkeypatch.setattr(comp, "run_action", run)
    return calls


def ok(note="ok"):
    return ActionResult(True, note)


def bad(note="failed"):
    return ActionResult(False, note, "details")


def groups(*keys):
    return [{"id": k, "key": k, "file": k.split(":")[0], "lines": [1], "title": k, "app": "api", "critical": False} for k in keys]


def cov_fail(*keys):
    return lambda a: ActionResult(False, "Coverage: api: changed lines 50.0% < 80%", "", {"data": {**a.data, "coverage_groups": groups(*keys)}})


def cov_pass(a):
    return ActionResult(True, "Coverage: api changed 100.0%; verdict recorded.", "", {"data": {**a.data, "coverage_groups": []}})


def finished(client, tid, step):
    return [e["data"] for e in client.bus.of(tid, "step.finished") if e.get("step") == step]


# ------------------------------------------------------------------ templates + include

def test_cover_and_ship_templates_validate_and_ship_includes_cover():
    cover, sh = get_template("cover"), get_template("ship")
    assert validate(cover) == [] and validate(sh) == []
    assert [s.id for s in cover.steps if s.per_item] == ["decide", "write", "review", "commit"]
    ids = [s.id for s in sh.steps]
    assert ids[ids.index("release") + 1:ids.index("deps")] == [f"cover_{s.id}" for s in cover.steps]
    # references inside the included steps follow the prefix; the include's skippable makes them one unit
    assert sh.step("cover_needs_work").no == "cover_report" and sh.step("cover_covered").when["step"] == "cover_remeasure"
    assert sh.step("cover_review").back == "cover_write" and {sh.step(f"cover_{s.id}").group for s in cover.steps} == {"cover"}
    # each included step says where it came from (the web folds an include into one block); ship's own steps do not
    assert {sh.step(f"cover_{s.id}").included_from for s in cover.steps} == {"cover"} and sh.step("verify").included_from is None
    feature = get_template("feature")
    assert feature.step("ship_verify").included_from == "ship" and feature.step("ship_cover_decide").included_from == "ship/cover"
    assert feature.step("red").included_from is None
    assert [(u["name"], u["band"]) for u in ship.skip_units(sh.steps, 0)] == [
        ("doc_updates", "optional"), ("lint", "optional"), ("ci", "optional"), ("release", "deferred"), ("cover", "deferred"), ("deps", "deferred"), ("reviewers", "optional"),
        ("spec_walk", "optional")]


def test_include_prefixes_ids_and_rejects_cycles_and_unknown_flows(monkeypatch):
    w = wf([{"id": "fix", "kind": "agent", "name": "fix", "agent": "implementer"},
            {"id": "tail", "kind": "include", "name": "cover tail", "flow": "cover"}])
    assert [s.id for s in w.steps][:3] == ["fix", "tail_measure", "tail_measured"]
    assert w.step("tail_covered").no == "tail_decide" and validate(w) == []
    with pytest.raises(WorkflowError, match="no workflow nope to include"):
        wf([{"id": "x", "kind": "include", "name": "x", "flow": "nope"}])
    with pytest.raises(WorkflowError, match="needs the workflow to include"):
        wf([{"id": "x", "kind": "include", "name": "x"}])

    def loop(name):
        return from_dict({"name": name, "keel_rules": False, "steps": [{"id": "again", "kind": "include", "name": "x", "flow": name}]})
    monkeypatch.setattr(templates, "get_template", loop)
    with pytest.raises(WorkflowError, match="include cycle"):
        loop("self")


def test_an_include_sent_as_json_to_the_engine_is_expanded(client, repo, monkeypatch):
    answers(monkeypatch, {})
    script(monkeypatch, {"verify_coverage": [cov_pass]})
    body = {"id": "mine", "name": "mine", "keel_rules": False,
            "steps": [{"id": "cov", "kind": "include", "name": "coverage", "flow": "cover"}]}
    tid = start(client, repo, workflow=from_dict(body))
    assert wait(client, tid)["status"] == "done"
    r = client.post("/workflows/validate", json={"yaml": "name: tail\nsteps:\n  - { id: ship, kind: include, name: ship, flow: ship }\n"}).json()
    assert r["ok"], r
    assert r["workflow"]["steps"][0]["id"] == "ship_plan" and any(s["id"] == "ship_cover_decide" for s in r["workflow"]["steps"])


def test_new_step_keys_are_validated():
    def errs(step):
        return validate(wf([{"id": "a", "kind": "agent", "name": "a", "agent": "explorer"}, step]))
    assert any("only a code step is soft" in e for e in errs({"id": "b", "kind": "gate", "name": "b", "soft": True}))
    assert any("rounds belongs to" in e for e in errs({"id": "b", "kind": "gate", "name": "b", "rounds": 2}))
    assert any("only a review step has redo" in e for e in errs({"id": "b", "kind": "agent", "name": "b", "agent": "explorer", "redo": "a"}))
    assert any("belong to a gate" in e for e in errs({"id": "b", "kind": "code", "name": "b", "action": "commit", "choices": ["x"]}))
    assert any("must be an earlier step" in e for e in errs({"id": "b", "kind": "code", "name": "b", "action": "commit", "back": "b"}))
    assert any("only gates, code steps and review steps" in e for e in errs({"id": "b", "kind": "agent", "name": "b", "agent": "explorer", "back": "a"}))
    assert any("on_skip belongs to a gate inside a for_each" in e for e in errs({"id": "b", "kind": "gate", "name": "b", "on_skip": {}}))
    assert errs({"id": "b", "kind": "agent", "name": "b", "agent": "lens-reviewer", "back": "a", "rounds": 2}) == []


# ------------------------------------------------------------------ cover

def test_cover_passing_coverage_needs_no_decision(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    script(monkeypatch, {"verify_coverage": [cov_pass]})
    tid = start(client, repo, workflow="cover")
    s = wait(client, tid)
    assert s["status"] == "done" and not seen
    assert values(client, tid)["markers"]["measure"] == {"RESULT": "pass"}
    assert "Coverage" in finished(client, tid, "report")[-1]["note"]


def test_cover_every_decision_review_round_and_the_round_limit(client, repo, monkeypatch):
    said = {"n": 0}

    # a new finding each round (0.18.71: the same one twice asks after one round; this test is about the round limit)
    rounds = ["tests/test_cover.py:3 asserts what the code does, not what it should do",
              "tests/test_cover.py:9 mocks the rounding helper it should exercise",
              "tests/test_cover.py:15 compares against a constant copied from the output"]

    def review(req):
        said["n"] += 1
        if req.item["id"] != "src/a.py:3-4":
            return CLEAN
        return f"## Blocking\n- {rounds[min(said['g1'], 2)]}\n\nBLOCKING: yes" if not said.update(g1=said.get("g1", -1) + 1) else ""
    seen = answers(monkeypatch, {"lens-reviewer": review})
    script(monkeypatch, {"verify_coverage": [cov_fail("src/a.py:3-4", "src/b.py:10-10", "src/c.py:7-9"), cov_fail("src/d.py:1-2")]})
    tid = start(client, repo, workflow="cover")
    s = wait(client, tid)
    assert s["waiting"]["step"] == "decide" and s["waiting"]["title"] == "decide test / delete / accept · src/a.py:3-4"
    assert s["waiting"]["choices"] == ["test", "delete", "accept"] and s["waiting"]["options"] == ["approve"]

    # g1: test -> the reviewer blocks, straight back to the writer; the fake writer commits nothing new, so (0.18.71)
    # keel asks after that round instead of sending the next finding back to a writer that changes nothing
    s = decide(client, tid, "approve", payload={"choice": "test"})
    writes = [r for r in seen if r.agent == "test-author"]
    assert len(writes) == 2 and writes[0].item["decision"] == "test"
    assert "Fix these blocking findings" in writes[1].prompt
    assert all("assertions lens" in r.step_name for r in seen if r.agent == "lens-reviewer")
    assert s["waiting"]["step"] == "review__fix" and s["waiting"]["labels"]["approve"] == "Send back"
    assert "The last fix round changed nothing" in s["waiting"]["detail"]
    s = decide(client, tid, "reject", why="it checks the rank order, which is the requirement")

    # g2: accept needs a reason; with one it is recorded and skipped
    assert s["waiting"]["title"].endswith("src/b.py:10-10")
    # 0.19.0 choice_info says accept needs a reason: the resume itself is refused without one
    assert {c["name"]: c["why"] for c in s["waiting"]["choice_info"]}["accept"] == "required"
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve", "payload": {"choice": "accept"}})
    assert r.status_code == 400 and "Accept as uncovered" in r.text
    s = decide(client, tid, "approve", why="a shutdown hook", payload={"choice": "accept"})

    # g3: delete -> the writer is told to delete; the review passes; the coverage commit runs
    s = decide(client, tid, "approve", payload={"choice": "delete"})
    assert [r.item["decision"] for r in seen if r.agent == "test-author"][-1] == "delete"
    # measured again: still failing -> round 2 over the new group, then the round limit asks
    assert s["waiting"]["title"].endswith("src/d.py:1-2")
    s = decide(client, tid, "approve")
    assert s["waiting"]["step"] == "covered" and "still no after 2 round(s)" in s["waiting"]["title"]
    s = decide(client, tid, "approve", why="the threshold is raised on main next week")
    assert s["status"] == "done", s

    v = values(client, tid)
    assert v["data"]["coverage_accepted"] == [{"id": "src/b.py:10-10", "key": "src/b.py:10-10", "file": "src/b.py", "lines": [1],
                                               "title": "src/b.py:10-10", "app": "api", "critical": False,
                                               "decision": "accept", "reason": "a shutdown hook"}]
    assert v["data"]["dismissed_findings"][0]["item"] == "src/a.py:3-4"
    assert v["review_rounds"]["review:src/a.py:3-4"] == 1                 # one round, then asked (nothing changed)
    # the last measurement is what is left: the group the second round worked on is still uncovered
    assert [(g["id"], g.get("status")) for g in v["data"]["coverage_groups"]] == [("src/d.py:1-2", None)]
    assert v["markers"]["remeasure"] == {"RESULT": "fail"} and v["markers"]["decide"] == {"CHOICE": "test"}
    log = s["gate_log"]
    assert "gate decide accept (src/b.py:10-10): a shutdown hook" in log and any("go on after 2 round(s)" in x for x in log)
    assert any("findings accepted" in x for x in log)
    assert "1 group(s) accepted" in finished(client, tid, "report")[-1]["note"]


def test_cover_says_so_when_the_coverage_command_fails(client, repo, monkeypatch):
    # Real run: the coverage command failed (no report), and cover said "still no after 2 round(s)" with no round run.
    def broken(a):
        return ActionResult(False, "The coverage command `x` failed.", "",
                            {"data": {**a.data, "coverage_groups": [], "coverage_error": "`x` exited 7, so there is no report to read."}})
    seen = answers(monkeypatch, {})
    fixed = lambda a: cov_pass(type(a)(**{**a.__dict__, "data": {**a.data, "coverage_error": None}}))   # a real measure clears it
    script(monkeypatch, {"verify_coverage": [broken, fixed]})
    tid = start(client, repo, workflow="cover")
    s = wait(client, tid)
    assert s["waiting"]["step"] == "measure_failed" and "exited 7" in s["waiting"]["detail"]
    s = decide(client, tid, "approve", payload={"choice": "try_again"})
    assert s["status"] == "done" and not seen, (s.get("waiting"), s.get("gate_log"))


def test_cover_stops_when_the_user_says_so_at_the_round_limit(client, repo, monkeypatch):
    answers(monkeypatch, {"lens-reviewer": lambda r: CLEAN})
    script(monkeypatch, {"verify_coverage": [cov_fail("src/a.py:1-1")]})
    tid = start(client, repo, workflow="cover")
    wait(client, tid)
    decide(client, tid, "approve")
    s = decide(client, tid, "approve")
    assert s["waiting"]["step"] == "covered"
    s = decide(client, tid, "reject", why="needs a design change first")
    assert s["status"] == "stopped" and any("stop after 2 round(s)" in x for x in s["gate_log"])


def lcov_repo(tmp_path):
    root = tmp_path / "cov"
    root.mkdir()

    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=root, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    (root / ".keel").mkdir()
    (root / ".keel/config.yml").write_text("version: 4\ncoverage:\n  reports: {web: build/lcov.info}\n  changed_lines: 90\n")
    git("add", "-A")
    git("commit", "-q", "-m", "chore: start")
    git("checkout", "-q", "-b", "feat/x")
    (root / "src").mkdir()
    (root / "src/list.ts").write_text("".join(f"l{i}\n" for i in range(1, 40)))
    git("add", "-A")
    git("commit", "-q", "-m", "feat(AC-1): list")
    (root / "build").mkdir()
    (root / "build/lcov.info").write_text("SF:src/list.ts\nDA:1,1\nDA:2,0\nDA:3,0\nDA:30,0\nend_of_record\n")
    return root


def test_coverage_decisions_carry_across_rounds_and_a_stalled_round_says_so(tmp_path):
    root = lcov_repo(tmp_path)
    a = ActionInput(root=str(root), project="p9", phase="coverage-fix", title="t", ac=None, acs=[], fake=False, flow="cover")
    r = asyncio.run(run_action("verify_coverage", a))
    keys = [g["key"] for g in r.update["data"]["coverage_groups"]]
    assert not r.ok and keys == ["src/list.ts:2-3", "src/list.ts:30-30"] and "same lines" not in r.note
    a.data = {**r.update["data"], "coverage_accepted": [{"key": "src/list.ts:30-30", "reason": "shutdown hook"}]}
    r = asyncio.run(run_action("verify_coverage", a))
    g = {x["key"]: x for x in r.update["data"]["coverage_groups"]}
    assert g["src/list.ts:30-30"]["status"] == "skipped" and g["src/list.ts:30-30"]["reason"] == "shutdown hook"
    assert "status" not in g["src/list.ts:2-3"]
    a.data = r.update["data"]
    r = asyncio.run(run_action("verify_coverage", a))
    assert "The same lines are still uncovered" in r.note
    assert verdicts.latest("p9", "coverage")["commit"]


def test_a_coverage_step_without_a_command_says_why_not_not_run(tmp_path):
    """0.18.57 real use: cover ran (not skipped) on a project with no commands.coverage; the final review said
    "coverage | not run" and the PR body "No coverage verdict", as if cover had been skipped."""
    root, _git, _write, _commit_all = _lens_repo(tmp_path)
    a = ActionInput(root=str(root), project="pcov", phase="coverage-fix", title="t", ac=None, acs=[], fake=False, flow="cover")
    r = asyncio.run(run_action("verify_coverage", a))
    assert r.ok and "coverage is not checked" in r.note
    text = ship.final_report(str(root), "pcov", {"data": {}}, "Rank")
    assert "| coverage | not available | no coverage command in .keel/config.yml (commands.coverage) |" in text
    body = verdict_actions.pr_body(str(root), "pcov", {"data": {}}, "Rank", None)
    assert "## Coverage\n\nNot measured: no coverage command in .keel/config.yml (commands.coverage)." in body


def test_the_coverage_commit_refuses_added_production_lines_and_config_edits(tmp_path):
    from keel_engine.runtime.actions import commit
    root = tmp_path / "c"
    root.mkdir()

    def git(*a):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=root, capture_output=True, text=True)
    git("init", "-q", "-b", "main")
    (root / ".keel").mkdir()
    (root / ".keel/config.yml").write_text("version: 4\ncoverage: {changed_lines: 80}\n")
    (root / "src").mkdir()
    (root / "src/a.py").write_text("def a():\n    return 1\n\n\ndef dead():\n    return 2\n")
    git("add", "-A")
    git("commit", "-q", "-m", "chore: start")
    a = ActionInput(root=str(root), project="p", phase="coverage-fix", title="cover a", ac=None, acs=[], fake=False, flow="cover")
    (root / "src/a.py").write_text("def a():\n    return 1\n\n\ndef dead():\n    return 3\n")
    r = commit(a)
    assert not r.ok and "may only delete" in r.note
    git("checkout", "--", "src/a.py")
    (root / ".keel/config.yml").write_text("version: 4\ncoverage: {changed_lines: 10}\n")
    r = commit(a)
    assert not r.ok and "may not edit .keel/config.yml" in r.note
    git("checkout", "--", ".keel/config.yml")
    (root / "src/a.py").write_text("def a():\n    return 1\n")
    (root / "tests").mkdir()
    (root / "tests/test_a.py").write_text("from a import a\n\n\ndef test_a():\n    assert a() == 1\n")
    r = commit(a)
    assert r.ok and r.note.startswith("test(coverage): cover a")


# ------------------------------------------------------------------ ship

SHIP_OK = {"verify_fast": [ok("Fast check: passed")], "verify_module": [ok("Module suites: green")],
           "verify_release": [ok("Release suite: green")], "verify_coverage": [cov_pass], "verify_deps": [ok("Dependencies: none")],
           "audit": [ok("Audit: clean")], "trace_strict": [ok("Trace (strict): ok")]}


def ship_plan(**over):
    plan = {k: list(v) for k, v in SHIP_OK.items()}
    plan.update(over)
    return plan


def test_ship_end_to_end_with_skips_a_verify_fix_and_a_routed_review_fix(client, repo, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    reviews = {"security": 0}

    def review(req):
        if req.item and req.item["id"] == "security":
            reviews["security"] += 1
            return BLOCK if reviews["security"] == 1 else CLEAN
        return CLEAN
    seen = answers(monkeypatch, {"lens-reviewer": review})
    calls = script(monkeypatch, ship_plan(verify_fast=[bad("Fast check: api compile failed."), ok("Fast check: passed")]))
    tid = start(client, repo, workflow="ship")
    s = wait(client, tid)
    w = s["waiting"]
    assert w["step"] == "plan" and w["labels"] == {"approve": "Run these steps"}
    for want in ("Always runs: ", "Deferred, not avoided", "  - release", "  - cover", "  - deps", "Optional", "  - reviewers", "  - spec_walk"):
        assert want in w["detail"], want
    s = decide(client, tid, "approve", payload={"skip": {"deps": ""}})
    assert "skipping deps needs a reason" in s["waiting"]["title"]
    s = decide(client, tid, "approve", payload={"skip": {"nope": "x"}})
    assert "nope cannot be skipped" in s["waiting"]["title"]
    s = decide(client, tid, "approve", payload={"skip": {"release": "no browser surface", "spec_walk": "a three-line change"},
                                                "lenses": ["correctness", "security"]})
    # verify failed once: the implementer fixed it (review-fix), then verify passed; release and spec_walk were skipped
    fixers = [r for r in seen if r.agent == "implementer" and r.phase != "close"]
    assert len(fixers) == 1 and fixers[0].phase == "review-fix" and "api compile failed" in fixers[0].prompt
    assert "verify_release" not in calls
    assert finished(client, tid, "release")[-1]["skipped"] is True
    # the security lens blocked: the question offers routes; route coverage-fix makes the test author fix it
    w = s["waiting"]
    assert w["step"] == "review__fix" and "[ship review, one lens each: security lens]" in w["detail"] and "route" in w["detail"]
    s = decide(client, tid, "approve", payload={"route": "coverage-fix"})
    fix = [r for r in seen if r.step_name.endswith("fix findings")][-1]
    assert fix.agent == "test-author" and fix.phase == "coverage-fix"
    assert calls.count("verify_fast") == 3, "redo: verify ran again after the review fix"
    assert sorted(r.item["id"] for r in seen if r.agent == "lens-reviewer") == ["correctness", "correctness", "security", "security"]
    w = s["waiting"]
    assert w["step"] == "final_review"
    for want in ("## Exceptions (2)", "skipped: release (deferred): no browser surface — its push gate is still outstanding",
                 "skipped: spec_walk (optional): a three-line change", "| release | not run |", "## Review fix rounds", "- review: 1"):
        assert want in w["detail"], want
    s = decide(client, tid, "approve")
    assert s["waiting"]["step"] == "pr_gate"
    assert "## Ship steps skipped" in s["waiting"]["detail"] and "- release: no browser surface" in s["waiting"]["detail"]
    s = decide(client, tid, "approve")
    assert s["status"] == "done", s
    assert "No GitHub token" in finished(client, tid, "open_pr")[-1]["note"]
    # the demo has no knowledge base (0.18.57: so its notes are off): no librarian, no knowledge question; the docs step
    # ran first (it changed nothing)
    assert not any(r.agent == "librarian" for r in seen) and values(client, tid)["data"]["memory_choice"]["choice"] == "off"
    assert [r.phase for r in seen if r.agent == "implementer"][0] == "close"
    v = values(client, tid)
    assert [x["step"] for x in v["data"]["ship_skipped"]] == ["release", "spec_walk"]
    assert any(line.startswith("gate plan skip release (deferred)") for line in s["gate_log"])


def skip_all_but_reviews():
    return {"release": "r", "cover": "c", "deps": "d", "spec_walk": "s"}


def test_ship_verify_gets_two_fix_rounds_then_asks(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    script(monkeypatch, ship_plan(verify_fast=[bad("Fast check: api compile failed.")]))
    tid = start(client, repo, workflow="ship")
    wait(client, tid)
    s = decide(client, tid, "approve")
    assert s["waiting"]["kind"] == "fix" and s["waiting"]["title"] == "verify fast + module keeps failing"
    assert len([r for r in seen if r.agent == "implementer" and r.phase != "close"]) == 2


def test_ship_review_findings_past_the_limit_are_dismissed_and_shown(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"lens-reviewer": lambda r: BLOCK})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"skip": skip_all_but_reviews(), "lenses": ["correctness"]})
    assert s["waiting"]["step"] == "review__fix" and "limit" not in s["waiting"]["detail"]
    s = decide(client, tid, "approve")
    s = decide(client, tid, "approve")
    assert "(fix round 3)" in s["waiting"]["title"] and "the limit is 2" in s["waiting"]["detail"]
    assert [r.phase for r in seen if r.step_name.endswith("fix findings")] == ["review-fix", "review-fix"]
    s = decide(client, tid, "reject", why="false positive: the test asserts the spec")
    assert s["waiting"]["step"] == "final_review"
    assert "blocking finding dismissed (ship review, one lens each)" in s["waiting"]["detail"]
    assert "false positive: the test asserts the spec" in s["waiting"]["detail"]


def test_ship_final_review_send_back_goes_to_the_implementer_and_runs_again(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"lens-reviewer": lambda r: CLEAN})
    calls = script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"skip": {"reviewers": "tiny", **skip_all_but_reviews()}})
    assert s["waiting"]["step"] == "final_review" and not [r for r in seen if r.agent == "lens-reviewer"]
    s = decide(client, tid, "reject", why="rename the endpoint to /ranks")
    fixers = [r for r in seen if r.agent == "implementer" and r.phase != "close"]
    assert len(fixers) == 1 and "rename the endpoint to /ranks" in fixers[0].prompt
    assert calls.count("verify_fast") == 2 and s["waiting"]["step"] == "final_review"


def test_a_flow_with_the_ship_tail(client, repo, monkeypatch):
    """The mechanism other flows use: `- { id: ship, kind: include, flow: ship }` appends the ship steps."""
    answers(monkeypatch, {})
    script(monkeypatch, ship_plan())
    w = get_template("fix")              # the fix flow ends with the ship include
    assert validate(w) == [] and w.step("ship_final_review").back == "ship_verify_fix"
    tid = start(client, repo, workflow=w)
    s = wait(client, tid)
    while s["status"] == "waiting" and s["waiting"]["step"] != "ship_plan":
        s = decide(client, tid, "approve")
    assert "  - cover" in s["waiting"]["detail"] and "  - release" in s["waiting"]["detail"]
    s = decide(client, tid, "approve", payload={"skip": {"reviewers": "covered by gate F", **skip_all_but_reviews()}})
    assert s["waiting"]["step"] == "ship_final_review"
    s = decide(client, tid, "approve")
    s = decide(client, tid, "approve")
    assert s["status"] == "done", s


# ------------------------------------------------------------------ ship helpers

def test_review_lenses(tmp_path):
    root = tmp_path / "p"
    (root / ".keel").mkdir(parents=True)
    a = ActionInput(root=str(root), project="p", phase="ship", title="t", ac=None, acs=[], fake=True, flow="ship")
    r = asyncio.run(run_action("review_lenses", a))
    assert [x["id"] for x in r.update["data"]["review_lenses"]] == ["correctness", "security", "performance", "consumers-config"]
    # 0.18.65 a lens with a lens file carries its checklist: consumers-config is keel's; the others the agent knows
    items = {x["id"]: x for x in r.update["data"]["review_lenses"]}
    assert "CI config" in items["consumers-config"]["checklist"]
    assert items["consumers-config"]["lens_title"] == "Consumers and config" and "checklist" not in items["security"]
    (root / ".keel/lenses").mkdir()
    (root / ".keel/lenses/security.md").write_text("# Our security\n\nours\n\n## Checklist\n\n- every route checks the shop id\n")
    r = asyncio.run(run_action("review_lenses", a))
    assert {x["id"]: x for x in r.update["data"]["review_lenses"]}["security"]["checklist"] == "- every route checks the shop id"
    (root / ".keel/config.yml").write_text("version: 4\narchitecture: {style: hexagonal}\n")
    r = asyncio.run(run_action("review_lenses", a))
    assert [x["id"] for x in r.update["data"]["review_lenses"]][-1] == "architecture"
    (root / ".keel/config.yml").write_text("version: 4\nreview: {lenses: [correctness]}\n")
    assert [x["id"] for x in asyncio.run(run_action("review_lenses", a)).update["data"]["review_lenses"]] == ["correctness"]
    a.data = {"lenses_chosen": ["security"]}
    assert [x["id"] for x in asyncio.run(run_action("review_lenses", a)).update["data"]["review_lenses"]] == ["security"]


def test_final_report_lists_exceptions_before_the_verdicts(tmp_path):
    root = Path(tmp_path)
    verdict_actions.record_verdict("pf", "release", True, {"summary": "suite green"})
    state = {"data": {"ship_skipped": [{"step": "deps", "band": "deferred", "reason": "no manifest"}],
                      "coverage_accepted": [{"key": "a.py:3-4", "reason": "defensive"}],
                      "dismissed_findings": [{"step": "review", "name": "ship review", "findings": ["x.py:1 N+1"], "why": "batch job"}]},
             "flaky": [{"label": "release suite", "tests": ["t1"]}], "gates": {"log": []}}
    text = ship.final_report(str(root), "pf", state, "Rank")
    assert text.index("## Exceptions (4)") < text.index("## Verdicts")
    for want in ("skipped: deps (deferred): no manifest — its push gate is still outstanding",
                 "coverage accepted, not covered: `a.py:3-4`: defensive", "blocking finding dismissed (ship review): x.py:1 N+1 — why: batch job",
                 "flaky: release suite: t1", "| release | pass | suite green |", "| coverage | not run | — |"):
        assert want in text, want


def test_a_coverage_group_carries_its_code_and_the_security_path_in_words(tmp_path):
    # 0.18.22 the decide gate showed only "file:3-4": the person chose test / delete / accept without seeing the code
    root = lcov_repo(tmp_path)
    (root / ".keel/config.yml").write_text("version: 4\ncoverage:\n  reports: {web: build/lcov.info}\n  changed_lines: 90\n"
                                           "security:\n  coverage_paths: ['src/**']\n")
    m = verdict_actions.measure(str(root), verdict_actions.rules.load_config(str(root)), "main")
    g = {x["key"]: x for x in m["groups"]}["src/list.ts:2-3"]
    assert g["critical"] and g["title"].endswith("· security path")
    assert "  1 | l1\n> 2 | l2\n> 3 | l3\n  4 | l4" in g["show"]
    assert "100% is needed" in g["show"]
    many = verdict_actions.excerpt(str(root), "src/list.ts", list(range(5, 30)))
    assert len(many.splitlines()) == verdict_actions.EXCERPT_MOST + 1 and "more uncovered line(s) up to 29" in many


def test_the_decide_gate_shows_the_code_and_a_delete_is_reviewed_for_reachability(client, repo, monkeypatch):
    def with_code(a):
        g = groups("src/a.py:3-4")
        g[0]["show"] = "Uncovered (marked >) in src/a.py:\n> 3 | if x:\n> 4 |     return 1"
        return ActionResult(False, "Coverage: api: changed lines 50.0% < 80%", "", {"data": {**a.data, "coverage_groups": g}})
    seen = answers(monkeypatch, {"lens-reviewer": lambda r: CLEAN})
    script(monkeypatch, {"verify_coverage": [with_code, cov_pass]})
    tid = start(client, repo, workflow="cover")
    s = wait(client, tid)
    w = s["waiting"]
    assert w["step"] == "decide" and "> 4 |     return 1" in w["detail"] and "{" not in w["detail"].split("Last step")[0]
    s = decide(client, tid, "approve", payload={"choice": "delete"})
    review = next(r for r in seen if r.agent == "lens-reviewer")
    assert "really unreachable" in review.prompt and review.item["decision"] == "delete"


def test_cover_without_coverage_reports_asks_at_once(client, repo, monkeypatch):
    # only commands.coverage: a failing command has no groups; it measured twice with nothing to decide, then asked
    (Path(repo) / ".keel").mkdir(exist_ok=True)
    (Path(repo) / ".keel" / "config.yml").write_text("version: 4\ncommands:\n  coverage: 'exit 3'\n")
    calls = script(monkeypatch, {})
    tid = start(client, repo, workflow="cover", settings={**SETTINGS, "simulate_checks": False})
    s = wait(client, tid)
    w = s["waiting"]
    assert w["step"] == "no_reports" and w["choices"] == ["go_on", "stop"], w
    assert "coverage.reports" in w["detail"] and "exit 3" in w["detail"]
    assert calls.count("verify_coverage") == 1
    s = decide(client, tid, "approve", payload={"choice": "stop"})
    assert s["status"] != "waiting" and calls.count("verify_coverage") == 1


# ------------------------------------------------------------------ the bug section (fix flow)

def _fix_through_ship(client, repo, monkeypatch, **extra):
    """The fix flow with the fake model up to its PR gate (ship's actions scripted green; spec_walk not skipped).
    Returns (thread id, final review text, PR body)."""
    answers(monkeypatch, {})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="fix", title="Average of one score is wrong", **extra)
    s = wait(client, tid)
    while s["status"] == "waiting" and s["waiting"]["step"] != "ship_plan":
        s = decide(client, tid)
    s = decide(client, tid, "approve", payload={"skip": {"reviewers": "covered by gate F", "release": "r", "cover": "c",
                                                         "deps": "d"}})
    assert s["waiting"]["step"] == "ship_final_review", s
    final = s["waiting"]["detail"]
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ship_pr_gate", s
    return tid, final, s["waiting"]["detail"]


def test_fix_pr_body_has_bug_section(client, repo, monkeypatch):
    _tid, final, body = _fix_through_ship(client, repo, monkeypatch, request="The average of [5] is 6, not 5.")
    for text in (final, body):
        assert "## Bug" in text
        for want in ("- **BUG-1** [API]", "- Symptom: The average of [5] is 6, not 5.",
                     "- Root cause: the counter is read before it is written (src/scores/__init__.py:1).",
                     "- Reproducing test: `tests/test_bug_average_of_one_score_is_wrong.py::test_bug_1_average_of_one_score_is_wrong`", "- Regression e2e: no"):
            assert want in text, (want, text)
        # the trace table names the bug like any criterion: its test and fix commits
        row = next(line for line in text.splitlines() if line.startswith("| BUG-1 |"))
        assert "| — |" not in row, row


def test_final_review_lists_hunt_findings_closed(tmp_path):
    state = {"data": {"bug": {"id": "BUG-4", "layer": "API", "symptom": "500 on /ranks with 1001 scores",
                              "root_cause": "the rank table is sized to 1000", "hunt": {"run": "H-2", "group": "G-01",
                                                                                        "lead": "F-003", "findings": ["F-003", "F-007"]},
                              "test": {"file": "tests/test_ranks.py", "name": "test_bug_4_overflow"}}},
             "markers": {"plan": {"E2E": "yes"}, "e2e": {"E2E-RESULT": "pass"}}, "gates": {"log": []}}
    text = ship.final_report(str(tmp_path), "pb", state, "Ranks overflow")
    for want in ("## Bug", "- Symptom: 500 on /ranks with 1001 scores", "- Root cause: the rank table is sized to 1000",
                 "- Reproducing test: `tests/test_ranks.py::test_bug_4_overflow`", "- Regression e2e: yes (pass)",
                 "- Closes F-003, F-007 (G-01, hunt H-2)"):
        assert want in text, want
    assert text.index("## Exceptions") < text.index("## Bug") < text.index("## Verdicts")
    body = verdict_actions.pr_body(str(tmp_path), "pb", state, "Ranks overflow", None)
    assert "- Closes F-003, F-007 (G-01, hunt H-2)" in body
    # a flow without a bug has no bug section
    assert "## Bug" not in ship.final_report(str(tmp_path), "pb", {"data": {}, "gates": {"log": []}}, "Rank")


def test_spec_walk_skipped_without_spec(client, repo, monkeypatch):
    tid, final, _body = _fix_through_ship(client, repo, monkeypatch)
    walk = finished(client, tid, "ship_spec_walk")
    assert walk and walk[-1].get("skipped") and "not needed (spec: not given)" in walk[-1]["note"], walk
    assert not [e for e in client.bus.of(tid, "agent.started") if e.get("step") == "ship_spec_walk"]
    # with a spec the walk runs (the feature flow's case)
    w = get_template("ship")
    assert w.step("spec_walk").when == {"state": "spec"}


def _lens_repo(tmp_path):
    from test_v02 import commit_all, git, write
    root = tmp_path / "p"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, ".keel/config.yml", "version: 4\n")
    write(root, "src/app.py", "X = 1\n")
    commit_all(root, "init")
    return root, git, write, commit_all


def test_correctness_lens_kept_after_a_passed_full_review_by_default(tmp_path):
    # keel 0.18.65 (a field report: the full-diff review passed and missed what a correctness look finds)
    root, git, write, commit_all = _lens_repo(tmp_path)
    sha = git(root, "rev-parse", "HEAD").stdout.strip()
    a = ActionInput(root=str(root), project="p", phase="ship", title="t", ac=None, acs=[], fake=True, flow="ship",
                    data={"full_review": {"verdict": "pass", "sha": sha}})
    r = asyncio.run(run_action("review_lenses", a))
    assert [x["id"] for x in r.update["data"]["review_lenses"]][0] == "correctness" and "lens_dropped" not in r.update["data"]


def test_correctness_lens_dropped_when_the_full_review_passed_and_no_production_file_changed(tmp_path):
    root, git, write, commit_all = _lens_repo(tmp_path)
    write(root, ".keel/config.yml", "version: 4\nreview:\n  drop_correctness: true\n")
    commit_all(root, "drop the lens after a pass")
    sha = git(root, "rev-parse", "HEAD").stdout.strip()
    a = ActionInput(root=str(root), project="p", phase="ship", title="t", ac=None, acs=[], fake=True, flow="ship",
                    data={"full_review": {"verdict": "pass", "sha": sha}})
    write(root, "docs/notes.md", "a doc\n")
    write(root, "tests/test_app.py", "def test(): pass\n")
    commit_all(root, "docs and tests only")
    r = asyncio.run(run_action("review_lenses", a))
    assert [x["id"] for x in r.update["data"]["review_lenses"]] == ["security", "performance", "consumers-config"]
    assert "correctness lens dropped" in r.note and sha[:7] in r.update["data"]["lens_dropped"]
    write(root, "src/app.py", "X = 2\n")
    commit_all(root, "a production change")
    r = asyncio.run(run_action("review_lenses", a))
    assert [x["id"] for x in r.update["data"]["review_lenses"]][0] == "correctness" and "lens_dropped" not in r.update["data"]
    a.data = {"full_review": {"verdict": "findings accepted", "sha": git(root, "rev-parse", "HEAD").stdout.strip()}}
    assert [x["id"] for x in asyncio.run(run_action("review_lenses", a)).update["data"]["review_lenses"]][0] == "correctness"
    a.data = {"full_review": {"verdict": "pass", "sha": git(root, "rev-parse", "HEAD").stdout.strip()}, "lenses_chosen": ["correctness"]}
    assert [x["id"] for x in asyncio.run(run_action("review_lenses", a)).update["data"]["review_lenses"]] == ["correctness"]


def test_final_report_shows_the_spec_amendments_reviews_security_and_files_to_open(tmp_path):
    root, git, write, commit_all = _lens_repo(tmp_path)
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    write(root, "docs/specs/rank.md", "# Rank\n\n## Goal\n\nPlayers see their rank.\n\n## Acceptance criteria\n\n"
          "- **AC-1** [API] rank\n\n## Amendments\n\n### 2026-10-01 — AC-1\n\nWas: rank by score\nNow: rank by wins\nWhy: measured\n")
    write(root, "src/app.py", "X = 1\nY = 2\nZ = 3\n")
    write(root, "src/small.py", "S = 1\n")
    commit_all(root, "feat")
    state = {"spec": "docs/specs/rank.md", "base_head": base, "acs": [{"id": "AC-1", "layer": "API", "title": "rank", "status": "done"}],
             "gates": {"log": []},
             "data": {"full_review": {"verdict": "pass", "sha": base}, "lens_dropped": "correctness lens dropped: because",
                      "ship_spec_walk_text": "An extra endpoint GET /debug implements no criterion.",
                      "security_results": [{"item": "audit", "title": "security audit", "text": "SECURITY: clean"},
                                           {"item": "deps", "title": "dependency triage", "text": "- lodash: not reachable (dev only)\nDEPS: clean"}],
                      "nonblocking_findings": [{"lens": "performance", "text": "a.py:3 could cache"}, "plain note"],
                      "dismissed_findings": [{"step": "review", "name": "ship review",
                                              "findings": [{"text": "x.py:1 N+1", "why": "batch job", "decision": "accept"}]}]}}
    text = ship.final_report(str(root), "pf2", state, "Rank")
    for want in ("## The spec", "Players see their rank.", "- AC-1 [API] rank (done)", "Amendments (1)", "Was: rank by score",
                 "## Security: clean", "triaged unreachable: lodash: not reachable (dev only)",
                 f"- verdict: pass at {base[:7]}", "correctness lens dropped: because", "GET /debug implements no criterion",
                 "- performance: a.py:3 could cache", "- plain note",
                 "blocking finding dismissed (ship review, accept): x.py:1 N+1 — why: batch job",
                 "Files a reviewer should open", "`src/app.py` (+2 -0)"):
        assert want in text, want
    assert text.index("`src/app.py`") < text.index("`src/small.py`")
    assert text.index("## Exceptions") < text.index("## The spec")


def _knowledge_branch(repo, change_cited: bool):
    from test_v02 import commit_all, git, write
    write(repo, "docs/knowledge/architecture.md", "# Architecture\n\nThe code starts in `src/scores/__init__.py:1`.\n")
    commit_all(repo, "docs: knowledge")
    git(repo, "checkout", "-q", "-b", "feat/x")
    write(repo, "src/scores/__init__.py" if change_cited else "src/scores/other.py", "def run():\n    return 1\n")
    commit_all(repo, "feat: a change")


SKIP_ALL = {"skip": {"reviewers": "r", "doc_updates": "d", **{"release": "r", "cover": "c", "deps": "d", "spec_walk": "s"}}}


def test_the_knowledge_update_is_asked_when_a_cited_file_changed_and_a_skip_skips_the_check_too(client, repo, monkeypatch):
    _knowledge_branch(repo, change_cited=True)
    seen = answers(monkeypatch, {})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    wait(client, tid)
    s = decide(client, tid, "approve", payload=SKIP_ALL)
    assert s["waiting"]["step"] == "final_review"
    s = decide(client, tid, "approve")
    w = s["waiting"]
    assert w["step"] == "memory_gate" and w["choices"] == ["update", "skip"]
    assert "- architecture: cites `src/scores/__init__.py`" in w["detail"] and "it costs tokens" in w["detail"]
    assert [c["name"] for c in w["choice_info"] if c["default"]] == ["update"]
    assert next(c for c in w["choice_info"] if c["name"] == "skip")["why"] == "required"
    s = decide(client, tid, "approve", payload={"choice": "skip"}, why="a rename only, nothing to learn")
    assert s["waiting"]["step"] == "pr_gate" and not any(r.agent == "librarian" for r in seen)
    assert "## Knowledge base\n\nskipped: a rename only, nothing to learn" in s["waiting"]["detail"]
    # 0.18.57 the check does not run either: on a stale knowledge base it fails, and its librarian rounds are the update
    # the person skipped (a real run spent $0.64 and 3.5 minutes there)
    ran = [e["step"] for e in client.bus.of(tid, "step.finished") if not e["data"].get("skipped")]
    assert "memory_skip" in ran and "memory_check" not in ran
    assert "Neither the librarian nor the knowledge check runs" in w["detail"]


def test_the_knowledge_update_runs_only_on_update_and_not_at_all_when_current(client, repo, monkeypatch):
    _knowledge_branch(repo, change_cited=True)
    seen = answers(monkeypatch, {})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    wait(client, tid)
    decide(client, tid, "approve", payload=SKIP_ALL)
    decide(client, tid, "approve")
    s = decide(client, tid, "approve", payload={"choice": "update"})
    lib = [r for r in seen if r.agent == "librarian"]
    assert len(lib) == 1 and "src/scores/__init__.py" in lib[0].prompt and "only the claims that cite" in lib[0].prompt
    assert s["waiting"]["step"] == "pr_gate" and "## Knowledge base\n\nupdated: architecture" in s["waiting"]["detail"]


def test_a_current_knowledge_base_is_not_asked_about(client, repo, monkeypatch):
    _knowledge_branch(repo, change_cited=False)
    seen = answers(monkeypatch, {})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    wait(client, tid)
    decide(client, tid, "approve", payload=SKIP_ALL)
    s = decide(client, tid, "approve")
    assert s["waiting"]["step"] == "pr_gate" and not any(r.agent == "librarian" for r in seen)
    assert "current: no file a section cites changed" in s["waiting"]["detail"]


def test_the_docs_step_is_optional_and_shown_at_the_final_review(client, repo, monkeypatch):
    answers(monkeypatch, {"implementer": lambda r: "README.md: the new rank command.\nCHANGELOG.md: one entry."})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"skip": {"reviewers": "r", **skip_all_but_reviews()}})
    assert s["waiting"]["step"] == "final_review"
    assert "## Docs this change made wrong (edited before verify)" in s["waiting"]["detail"]
    assert "README.md: the new rank command." in s["waiting"]["detail"]
    tid = start(client, repo, workflow="ship")
    wait(client, tid)
    s = decide(client, tid, "approve", payload=SKIP_ALL)
    assert "skipped: doc_updates (optional): d" in s["waiting"]["detail"] and "## Docs this change" not in s["waiting"]["detail"]


def test_ship_lens_reviewers_get_the_diff_scope(tmp_path):
    root, git, write, commit_all = _lens_repo(tmp_path)
    git(root, "checkout", "-q", "-b", "feat/x")
    write(root, "src/app.py", "X = 2\n")
    commit_all(root, "feat")
    a = ActionInput(root=str(root), project="p", phase="ship", title="t", ac=None, acs=[], fake=True, flow="ship",
                    state={"spec": "docs/specs/x.md"})
    items = asyncio.run(run_action("review_lenses", a)).update["data"]["review_lenses"]
    assert items[0] == {"id": "correctness", "title": "correctness lens", "lens": "correctness", "scope": "git diff main...HEAD",
                        "base": "main", "files": ["src/app.py"], "spec": "docs/specs/x.md"}


def test_the_pr_body_and_the_final_review_carry_security_and_dismissed_findings(tmp_path):
    state = {"gates": {"log": []}, "data": {
        "security_results": [{"item": "audit", "title": "security audit", "text": "- `a.py:1` IDOR. Blocking.\nSECURITY: findings"}],
        "dismissed_findings": [{"step": "security", "name": "security", "findings": ["a.py:1 IDOR"], "why": "admin-only route"}]}}
    body = verdict_actions.pr_body(str(tmp_path), "pf3", state, "Rank", None)
    assert "## Security: findings" in body and "- security audit: 1 blocking finding(s)" in body
    assert "let through: a.py:1 IDOR — why: admin-only route" in body
    assert "## Blocking findings let through" in body and "blocking finding dismissed (security): a.py:1 IDOR" in body
    text = ship.final_report(str(tmp_path), "pf3", state, "Rank")
    assert "| security | findings | security audit: 1 blocking finding(s) |" in text
    assert "## Security: not run" in verdict_actions.pr_body(str(tmp_path), "pf3", {"data": {}}, "Rank", None)


def test_skip_answers_take_a_shared_reason_and_a_refused_one_never_runs_the_steps(client, repo, monkeypatch):
    """0.18.57 real use: a skip answered through MCP without per-step reasons was refused, then keel ran every step.
    The simple forms work (skip: [names] with one reason, the answer's why), and after a refusal an answer that says
    nothing about skipping asks again instead of running what the person wanted skipped."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    answers(monkeypatch, {})
    calls = script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    assert wait(client, tid)["waiting"]["step"] == "plan"
    s = decide(client, tid, "approve", payload={"skip": ["deps"]})
    assert "skipping deps needs a reason" in s["waiting"]["title"] and "nothing was skipped" in s["waiting"]["title"]
    s = decide(client, tid, "approve")
    assert s["waiting"]["step"] == "plan" and "you asked to skip deps" in s["waiting"]["title"], s["waiting"]
    assert "verify_fast" not in calls
    s = decide(client, tid, "approve", why="no manifest changed", payload={"answers": {"skip": ["deps"]}})
    assert "verify_deps" not in calls
    assert finished(client, tid, "deps")[-1]["skipped"] is True


def test_skip_payload_simple_forms():
    names = ["deps", "lint"]
    p = ship.skip_payload({"skip": ["deps", "lint"], "reason": "tiny"}, names)
    skips, _l, problem = ship.parse_skips(p, [{"name": "deps", "band": "deferred"}, {"name": "lint", "band": "optional"}])
    assert problem is None and {s["step"]: s["reason"] for s in skips} == {"deps": "tiny", "lint": "tiny"}
    _s, _l, problem = ship.parse_skips(ship.skip_payload({"skip": "deps"}, names), [{"name": "deps", "band": "deferred"}])
    assert problem and problem.startswith("skipping deps needs a reason")
    assert ship.names_skip({}) is None and ship.names_skip({"skip": []}) == set()


def test_a_failing_strict_trace_has_a_way_out_and_a_refactor_counts_as_traced(client, repo, monkeypatch, tmp_path):
    # 0.18.58 e2e: trace --strict failed on a "no behaviour change" criterion and the gate offered only "try again"
    # (the same gate came back) or "stop"
    answers(monkeypatch, {"lens-reviewer": lambda req: CLEAN})
    script(monkeypatch, ship_plan(trace_strict=[bad("Trace (strict): 1/2 AC(s) have a test commit; no test commit: AC-2.")] * 3))
    tid = start(client, repo, workflow="ship")
    s = wait(client, tid)
    s = decide(client, tid, "approve", payload={"skip": {"release": "no browser surface", "spec_walk": "a short change"},
                                                "lenses": ["correctness"]})
    for _ in range(12):
        w = s.get("waiting") or {}
        if w.get("step") == "trace":
            break
        s = decide(client, tid, "approve")
    w = s["waiting"]
    assert w["step"] == "trace" and w["labels"] == {"approve": "Go on with a warning", "reject": "Stop"}, w
    assert "trying again finds the same thing" in w["detail"]
    s = decide(client, tid, "approve")
    assert (s.get("waiting") or {}).get("step") != "trace", s
    assert any("trace --strict" in str(w) for w in s["warnings"]), s["warnings"]

    from keel_engine.runtime import verdict_actions
    root = tmp_path / "r"
    root.mkdir()
    run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *a], cwd=root, check=True,
                                    capture_output=True)
    run("init", "-q", "-b", "main")
    (root / "a.py").write_text("x = 1\n")
    run("add", "-A")
    run("commit", "-qm", "feat(AC-2): the fixtures go through one helper")
    rows = verdict_actions.trace_rows(str(root), [
        {"id": "AC-2", "layer": "API", "title": "the fixtures go through one helper, no behaviour change", "status": "done"},
        {"id": "AC-3", "layer": "API", "title": "a total in cents", "status": "done"}])
    assert rows[0]["complete"] and rows[0]["test"] == "existing tests"
    assert not rows[1]["complete"]


def test_coverage_without_a_driver_or_with_an_old_report_never_passes(tmp_path):
    """0.18.75 a field report: PHP without a coverage driver said "No code coverage driver available", wrote no report,
    and an old report made "coverage passes"."""
    import os
    import time as _time
    root = lcov_repo(tmp_path)
    cfg = root / ".keel/config.yml"
    a = ActionInput(root=str(root), project="pdrv", phase="coverage-fix", title="t", ac=None, acs=[], fake=False, flow="cover")
    cfg.write_text(cfg.read_text() + "commands:\n  coverage: \"echo 'No code coverage driver available'\"\n")
    r = asyncio.run(run_action("verify_coverage", a))
    assert not r.ok and "no coverage driver" in r.note and "pcov" in r.detail
    # a command that runs but does not write the report: the report there is from before, so it does not count
    cfg.write_text(cfg.read_text().replace("echo 'No code coverage driver available'", "true"))
    old = _time.time() - 3600
    os.utime(root / "build/lcov.info", (old, old))
    r = asyncio.run(run_action("verify_coverage", a))
    assert not r.ok and "older than this run" in r.note
