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
    assert [(u["name"], u["band"]) for u in ship.skip_units(sh.steps, 0)] == [
        ("lint", "optional"), ("release", "deferred"), ("cover", "deferred"), ("deps", "deferred"), ("reviewers", "optional"),
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
    assert errs({"id": "b", "kind": "agent", "name": "b", "agent": "reviewer", "back": "a", "rounds": 2}) == []


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

    def review(req):
        said["n"] += 1
        return BLOCK if req.item["id"] == "src/a.py:3-4" else CLEAN
    seen = answers(monkeypatch, {"reviewer": review})
    script(monkeypatch, {"verify_coverage": [cov_fail("src/a.py:3-4", "src/b.py:10-10", "src/c.py:7-9"), cov_fail("src/d.py:1-2")]})
    tid = start(client, repo, workflow="cover")
    s = wait(client, tid)
    assert s["waiting"]["step"] == "decide" and s["waiting"]["title"] == "decide test / delete / accept · src/a.py:3-4"
    assert s["waiting"]["choices"] == ["test", "delete", "accept"] and s["waiting"]["options"] == ["approve"]

    # g1: test -> the reviewer blocks twice, each time straight back to the writer; the third time keel asks
    s = decide(client, tid, "approve", payload={"choice": "test"})
    writes = [r for r in seen if r.agent == "test-author"]
    assert len(writes) == 3 and writes[0].item["decision"] == "test"
    assert "Fix these blocking findings" in writes[1].prompt and "round 2" in writes[2].prompt
    assert all("assertions lens" in r.step_name for r in seen if r.agent == "reviewer")
    assert s["waiting"]["step"] == "review__fix" and s["waiting"]["labels"]["approve"] == "Send back"
    assert "the limit is 2" in s["waiting"]["detail"]
    s = decide(client, tid, "reject", why="it checks the rank order, which is the requirement")

    # g2: accept needs a reason; with one it is recorded and skipped
    assert s["waiting"]["title"].endswith("src/b.py:10-10")
    s = decide(client, tid, "approve", payload={"choice": "accept"})
    assert "accept needs a reason" in s["waiting"]["title"]
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
    assert v["review_rounds"]["review:src/a.py:3-4"] == 2
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
    answers(monkeypatch, {"reviewer": lambda r: CLEAN})
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
    seen = answers(monkeypatch, {"reviewer": review})
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
    fixers = [r for r in seen if r.agent == "implementer"]
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
    assert sorted(r.item["id"] for r in seen if r.agent == "reviewer") == ["correctness", "correctness", "security", "security"]
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
    assert any(r.agent == "librarian" for r in seen)
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
    assert len([r for r in seen if r.agent == "implementer"]) == 2


def test_ship_review_findings_past_the_limit_are_dismissed_and_shown(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"reviewer": lambda r: BLOCK})
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
    seen = answers(monkeypatch, {"reviewer": lambda r: CLEAN})
    calls = script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"skip": {"reviewers": "tiny", **skip_all_but_reviews()}})
    assert s["waiting"]["step"] == "final_review" and not [r for r in seen if r.agent == "reviewer"]
    s = decide(client, tid, "reject", why="rename the endpoint to /ranks")
    fixers = [r for r in seen if r.agent == "implementer"]
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
    assert [x["id"] for x in r.update["data"]["review_lenses"]] == ["correctness", "security", "performance"]
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
