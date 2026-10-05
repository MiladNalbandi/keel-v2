"""The review, diagnose, fix and change workflows (content/workflows), walked with the fake model through every gate
exit and branch, plus the engine pieces they added: gate choices, waived gates, `then: <step>`, a code step's
attempts + back, `when` over several steps / any item, an item's own agent, step instructions, escalate_model."""

import json
from pathlib import Path

from conftest import decide, start, wait
from test_flows_base import answers, values, wf
from test_flows_cover_ship import script, ship_plan, skip_all_but_reviews
from test_v02 import commit_all, configure, git, log, write
from keel_engine.runtime import actions as actions_mod
from keel_engine.runtime.compiler import stronger
from keel_engine.workflows.templates import get_template
from keel_engine.workflows.validate import validate

SETTINGS = {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}


def on_branch(repo, name="feat/ranks"):
    git(repo, "checkout", "-q", "-b", name)
    write(repo, "src/scores/ranks.py", "def rank(xs):\n    return sorted(xs)\n")
    commit_all(repo, "feat: ranks")


def request_of(client, tid) -> str:
    return json.loads(client.portal.call(client.app.state.engine._row, tid)["body"])["request"]


def through_ship(client, tid, s):
    """The included ship steps (actions scripted green, reviews skipped): its plan, final review and PR gates.
    Returns the final state and the PR body shown at the PR gate."""
    assert s["waiting"]["step"] == "ship_plan", s
    s = decide(client, tid, "approve", payload={"skip": {"reviewers": "covered by the flow", **skip_all_but_reviews()}})
    assert s["waiting"]["step"] == "ship_final_review", s
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ship_pr_gate", s
    body = s["waiting"]["detail"]
    return decide(client, tid), body


def child(client, s, workflow):
    kids = [c for c in s.get("children") or [] if c["workflow"] == workflow]
    assert len(kids) == 1, s.get("children")
    return kids[0]["thread_id"]


# ------------------------------------------------------------------ templates

def test_the_four_templates_are_valid():
    for name in ("review", "diagnose", "fix", "change"):
        t = get_template(name)
        assert t and validate(t) == [], name
        assert all(s.phase for s in t.steps), name
    assert {s.phase for s in get_template("review").steps} == {"review"}
    assert {s.phase for s in get_template("diagnose").steps} == {"bug-investigate"}


# ------------------------------------------------------------------ review

def test_review_refuses_an_empty_diff(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    s = wait(client, start(client, repo, workflow="review", title="Review"))
    assert s["status"] == "stopped" and "Nothing to review" in s["error"] and seen == []


def test_review_refuses_an_unknown_lens_and_a_missing_base(client, repo):
    on_branch(repo)
    s = wait(client, start(client, repo, workflow="review", title="Review", data={"lens": "style"}))
    assert s["status"] == "stopped" and "Unknown review argument 'style'" in s["error"]
    s = wait(client, start(client, repo, workflow="review", title="Review", data={"base": "nope"}))
    assert s["status"] == "stopped" and "nope does not exist" in s["error"]


def test_review_all_runs_one_reviewer_per_lens_and_reports_verbatim(client, repo, monkeypatch):
    on_branch(repo)
    configure(repo, "version: 4\narchitecture: {style: hexagonal}\n")
    said = {"security": "- src/scores/ranks.py:2 trusts its input\nBLOCKING: yes"}
    seen = answers(monkeypatch, {"reviewer": lambda r: f"{r.item['lens']} says hi.\n" + said.get(r.item["lens"], "BLOCKING: no")})
    tid = start(client, repo, workflow="review", title="Review", data={"lens": "all"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "read_report" and s["phase"] == "review"
    assert sorted(r.item["lens"] for r in seen) == ["architecture", "correctness", "performance", "security"]
    assert all("git diff main...HEAD" in r.prompt and "Do not fix anything" in r.prompt for r in seen)
    detail = s["waiting"]["detail"]
    assert "## security lens" in detail and "src/scores/ranks.py:2 trusts its input" in detail
    assert "1 of 4 blocking" in detail
    before = log(repo)
    s = decide(client, tid, "reject", why="look again at sorting")
    assert len(seen) == 8 and "look again at sorting" in seen[-1].prompt
    s = decide(client, tid)
    assert s["status"] == "done" and log(repo) == before        # read-only: nothing fixed, nothing committed
    assert not any(e["step"].endswith("__fix") for e in client.bus.of(tid, "step.started"))


def test_review_code_and_one_lens_pick_their_agent(client, repo, monkeypatch):
    on_branch(repo)
    seen = answers(monkeypatch, {})
    s = wait(client, start(client, repo, workflow="review", title="Review"))
    assert [r.agent for r in seen] == ["code-reviewer"] and "CODE-REVIEW: pass" in s["waiting"]["detail"]
    seen.clear()
    wait(client, start(client, repo, workflow="review", title="Review", data={"lens": "performance", "base": "main"}))
    assert [(r.agent, r.item["lens"]) for r in seen] == [("reviewer", "performance")]


def test_review_one_criterion_needs_its_commits(client, repo, monkeypatch):
    on_branch(repo)
    seen = answers(monkeypatch, {})
    s = wait(client, start(client, repo, workflow="review", title="Review", data={"lens": "ac AC-1"}))
    assert s["status"] == "stopped" and "no feat(AC-1) and no test(AC-1) commit" in s["error"]
    write(repo, "tests/test_ac1.py", "def test_ac1():\n    assert True\n")
    commit_all(repo, "test(AC-1): ranks sort")
    write(repo, "src/scores/ac1.py", "X = 1\n")
    commit_all(repo, "feat(AC-1): ranks sort")
    s = wait(client, start(client, repo, workflow="review", title="Review", data={"lens": "ac ac-1"}))
    assert [r.agent for r in seen] == ["ac-reviewer"]
    assert seen[0].item["red"] and seen[0].item["green"] and "AC-REVIEW: PASS" in s["waiting"]["detail"]


# ------------------------------------------------------------------ diagnose

HYPS = "Framed.\n```json\n" + json.dumps([{"title": "stale cache"}, {"title": "off by one"}, {"title": "race"}]) + "\n```"


def diagnose_answers(monkeypatch, recipe="run the nightly job twice"):
    return answers(monkeypatch, {
        "explorer": lambda r: HYPS,
        "investigator": lambda r: (f"Ranked: the race wins.\nRECIPE: {recipe}\nKIND: defect" if r.step_name.startswith("rank")
                                   else f"Looked at {r.item['title']}: killed.\nROOT-CAUSE: unconfirmed")})


def test_diagnose_fans_out_and_hands_the_recipe_to_fix(client, repo, monkeypatch):
    seen = diagnose_answers(monkeypatch)
    tid = start(client, repo, workflow="diagnose", title="Totals double at night", request="totals doubled after midnight")
    s = wait(client, tid)
    assert s["waiting"]["step"] == "decide" and s["waiting"]["choices"] == ["fix", "feature", "unresolved"]
    inv = [r for r in seen if r.agent == "investigator" and r.item]
    assert sorted(r.item["title"] for r in inv) == ["off by one", "race", "stale cache"]
    rank = next(r for r in seen if r.step_name.startswith("rank"))
    assert "Looked at race: killed." in rank.prompt          # the ranking sees every investigator's answer
    detail = s["waiting"]["detail"]
    assert detail.startswith("Ranked: the race wins.") and "## stale cache" in detail and "ROOT-CAUSE: 3 unconfirmed" in detail
    s = decide(client, tid, "approve", payload={"choice": "fix"})
    assert s["status"] == "done" and any("[fix]" in x for x in s["gate_log"])
    kid = child(client, s, "fix")
    req = request_of(client, kid)
    assert "run the nightly job twice" in req and "totals doubled after midnight" in req and "race wins" not in req


def test_diagnose_hands_an_unspecified_behaviour_to_feature(client, repo, monkeypatch):
    diagnose_answers(monkeypatch)
    tid = start(client, repo, workflow="diagnose", title="Totals double at night")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"choice": "feature"})
    kid = child(client, s, "feature")
    assert "Evidence so far" in request_of(client, kid) and "race wins" in request_of(client, kid)


def test_diagnose_records_unresolved_with_every_hypothesis(client, repo, monkeypatch):
    seen = diagnose_answers(monkeypatch, recipe="none")
    tid = start(client, repo, workflow="diagnose", title="Totals double at night")
    wait(client, tid)
    s = decide(client, tid, "reject", why="also look at the timezone")
    assert s["waiting"]["step"] == "decide" and "also look at the timezone" in [r for r in seen if r.agent == "explorer"][-1].prompt
    s = decide(client, tid, "approve", payload={"choice": "unresolved"}, why="no repro yet")
    assert s["status"] == "done" and not s.get("children")
    notes = list((Path(repo) / "docs" / "investigations").glob("*-totals-double-at-night.md"))
    assert len(notes) == 1
    text = notes[0].read_text()
    assert "Looked at stale cache: killed." in text and "Looked at off by one: killed." in text and "no repro yet" in text
    assert log(repo)[0].startswith("docs: investigation note")


# ------------------------------------------------------------------ fix

def test_fix_happy_path_commits_test_then_fix_and_ships(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="fix", title="Average of one score is wrong")
    s = wait(client, tid)
    assert s["waiting"]["step"] == "gate_r" and s["waiting"]["choices"] == ["investigate", "stop"]
    s = decide(client, tid)
    assert s["waiting"]["step"] == "gate_f" and "ROOT-CAUSE: 3 confirmed" in s["waiting"]["detail"]
    assert len([r for r in seen if r.agent == "investigator" and r.item]) == 3
    s, body = through_ship(client, tid, decide(client, tid))
    assert s["status"] == "done" and "# Average of one score is wrong" in body
    subjects = [x for x in log(repo) if "(BUG)" in x]
    assert subjects[0].startswith("fix(BUG)") and subjects[1].startswith("test(BUG)")
    assert not any(r.agent == "e2e-author" for r in seen)
    fix = next(r for r in seen if r.agent == "implementer")
    assert "The approved plan" in fix.prompt and "ROOT-CAUSE" in fix.prompt


def test_fix_not_reproducible_hands_over_to_diagnose(client, repo, monkeypatch):
    answers(monkeypatch, {"reproducer": lambda r: "Could not make it fail.\nREPRO: not-reproducible"})
    s = wait(client, start(client, repo, workflow="fix", title="Flaky total", request="sometimes the total is 0"))
    assert s["status"] == "done"
    kid = child(client, s, "diagnose")
    assert "sometimes the total is 0" in request_of(client, kid)
    assert not any(x.startswith("test(BUG)") for x in log(repo))


def test_fix_gate_r_send_back_and_stop(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    s = decide(client, tid, "reject", why="reproduce with two scores")
    assert s["waiting"]["step"] == "gate_r" and "reproduce with two scores" in [r for r in seen if r.agent == "reproducer"][-1].prompt
    s = decide(client, tid, "approve", payload={"choice": "stop"})
    assert s["status"] == "done" and log(repo)[0].startswith("test(BUG)")
    assert not any(r.agent == "investigator" for r in seen)


def investigator(said):
    """The fix flow's investigator: the hypotheses step lists three, the others answer said(req)."""
    return lambda r: HYPS if r.step_name == "hypotheses" else said(r)


def test_fix_escalates_the_model_once_then_hands_over_to_diagnose(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"investigator": investigator(lambda r: "Nothing yet.\nROOT-CAUSE: unconfirmed")})
    strong = {"provider": "fake", "mode": "api", "model": "fake-strong"}
    tid = start(client, repo, workflow="fix", title="Average is wrong", settings={**SETTINGS, "stronger_model": strong})
    wait(client, tid)
    s = decide(client, tid)
    assert s["status"] == "done", s
    inv = [r for r in seen if r.agent == "investigator" and r.item]
    assert len(inv) == 6 and [r.model["model"] for r in inv[3:]] == ["fake-strong"] * 3
    assert values(client, tid)["agent_models"]["investigator"] == strong
    child(client, s, "diagnose")


def test_fix_escalation_that_confirms_goes_on_to_gate_f(client, repo, monkeypatch):
    answers(monkeypatch, {"investigator": investigator(
        lambda r: "ROOT-CAUSE: " + ("confirmed" if r.model["model"] == "fake-strong" and "race" in r.prompt else "unconfirmed"))})
    strong = {"provider": "fake", "mode": "api", "model": "fake-strong"}
    tid = start(client, repo, workflow="fix", title="Average is wrong", settings={**SETTINGS, "stronger_model": strong})
    wait(client, tid)
    s = decide(client, tid)
    assert s["waiting"]["step"] == "gate_f"


def test_fix_gate_f_send_back_and_missing_requirement(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    decide(client, tid)
    s = decide(client, tid, "reject", why="smaller change please")
    assert s["waiting"]["step"] == "gate_f"
    assert "smaller change please" in [r for r in seen if r.step_name == "fix plan"][-1].prompt
    s = decide(client, tid, "approve", payload={"choice": "feature"})
    assert s["status"] == "done" and not any(x.startswith("fix(BUG)") for x in log(repo))
    child(client, s, "feature")


def test_fix_that_fails_twice_resets_and_reproduces_again(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    real = actions_mod.verify_green
    fails = {"n": 2}

    async def verify_green(a):
        if a.phase == "bug-fix" and fails["n"]:
            fails["n"] -= 1
            return actions_mod.ActionResult(False, "the bug test still fails", "AssertionError")
        return await real(a)
    monkeypatch.setattr(actions_mod, "verify_green", verify_green)
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    decide(client, tid)
    s = decide(client, tid)
    assert s["waiting"]["step"] == "gate_r", s          # two failed fixes: reset, reproduce, Gate R again
    assert [r.agent for r in seen].count("implementer") == 2 and [r.agent for r in seen].count("reproducer") == 2
    assert "the bug test still fails" in [r for r in seen if r.agent == "reproducer"][-1].prompt
    assert not (Path(repo) / "src/scores/fix_average_is_wrong.py").exists()      # the failed fix was put back
    s = decide(client, tid)
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ship_plan"


def test_fix_no_gates_waives_both_gates_and_says_so(client, repo, monkeypatch):
    answers(monkeypatch, {})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="fix", title="Average is wrong", data={"no_gates": True})
    s = wait(client, tid)
    assert any(x.startswith("gate gate_r approve: waived") for x in s["gate_log"])
    s, body = through_ship(client, tid, s)
    assert s["status"] == "done" and "bug gates waived (no_gates option)" in body
    configure(repo, "version: 4\ngates: {bug_gates: false}\n")
    tid = start(client, repo, workflow="fix", title="Other bug")
    _s, body = through_ship(client, tid, wait(client, tid))
    assert "gates.bug_gates: false" in body


def test_fix_from_a_hunt_seed_writes_the_regression_e2e(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    w = wf([{"id": "hand", "kind": "code", "name": "hand to fix", "action": "start_flow", "flow": "fix", "then": "end",
             "seed": {"title": "Ranks overflow", "recipe": "POST 1001 scores", "symptoms": ["500 on /ranks"], "needs_e2e": True}}])
    s = wait(client, start(client, repo, workflow=w))
    kid = child(client, s, "fix")
    wait(client, kid)
    assert "POST 1001 scores" in [r for r in seen if r.agent == "reproducer"][0].prompt
    decide(client, kid)
    s = decide(client, kid)
    assert s["waiting"]["step"] == "ship_plan"
    assert any(r.agent == "e2e-author" for r in seen) and log(repo)[0].startswith("e2e(BUG)")


def test_fix_plan_can_ask_for_the_e2e(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"investigator": investigator(lambda r: "ROOT-CAUSE: confirmed\nE2E: yes")})
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    decide(client, tid)
    decide(client, tid)
    assert any(r.agent == "e2e-author" for r in seen)


# ------------------------------------------------------------------ change

def test_change_small_runs_the_loop_with_one_gate_at_the_end(client, repo, monkeypatch):
    answers(monkeypatch, {})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="change", title="Tweak ranks")
    s = wait(client, tid)
    assert s["waiting"]["step"] == "scope_gate" and s["waiting"]["choices"] == ["small", "trivial", "feature"]
    assert s["waiting"]["detail"].startswith("Proposed: small") and "CHG-1.1 [API]" in s["waiting"]["detail"]
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "CHG-1.2"        # one gate, after the last criterion
    s, body = through_ship(client, tid, decide(client, tid))
    assert s["status"] == "done" and "no gate here (gate mode is end)" in body
    assert [x.split(":")[0] for x in log(repo) if "CHG-" in x] == ["feat(CHG-1.2)", "test(CHG-1.2)", "feat(CHG-1.1)", "test(CHG-1.1)"]


def test_change_trivial_is_one_refactor_commit(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"explorer": lambda r: "A rename; no test could notice.\nSIZE: trivial"})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="change", title="Rename the helper")
    s = wait(client, tid)
    assert s["waiting"]["detail"].startswith("Proposed: trivial")
    s, _body = through_ship(client, tid, decide(client, tid, "approve", payload={"choice": "trivial"}))
    assert s["status"] == "done" and "refactor: Rename the helper" in log(repo)
    assert not any(r.agent == "test-author" for r in seen)


def test_change_trivial_that_is_not_trivial_goes_back_to_triage(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"explorer": lambda r: "SIZE: trivial"})
    real = actions_mod.commit

    def commit(a):
        if a.phase == "trivial":
            return actions_mod.ActionResult(False, "This is not a trivial change: it edits existing tests.", "tests/test_scores.py")
        return real(a)
    monkeypatch.setattr(actions_mod, "commit", commit)
    tid = start(client, repo, workflow="change", title="Rename the helper")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"choice": "trivial"})
    assert s["waiting"]["step"] == "scope_gate", s
    assert [r.agent for r in seen] == ["explorer", "implementer", "implementer", "explorer"]
    assert "edits existing tests" in seen[-1].prompt
    assert git(repo, "status", "--porcelain").stdout.strip() == ""      # the trivial agent's edit was put back


def test_change_choosing_feature_hands_the_inline_criteria_over(client, repo, monkeypatch):
    answers(monkeypatch, {})
    tid = start(client, repo, workflow="change", title="Tweak ranks")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"choice": "feature"})
    assert s["status"] == "done"
    kid = wait(client, child(client, s, "feature"))
    assert [a["id"] for a in kid["acs"]] == ["CHG-1.1", "CHG-1.2"]


def test_change_too_many_criteria_recommends_feature_and_an_override_is_recorded(client, repo, monkeypatch):
    answers(monkeypatch, {})
    script(monkeypatch, ship_plan())
    acs = [{"id": f"AC-{n}", "layer": "API", "title": f"rule {n} holds"} for n in range(1, 5)]
    tid = start(client, repo, workflow="change", title="Tweak ranks", acs=acs)
    s = wait(client, tid)
    assert s["waiting"]["detail"].startswith("Proposed: feature") and "4 criteria (limit 3" in s["waiting"]["detail"]
    s = decide(client, tid, "approve", payload={"choice": "small"}, why="four tiny rules, one file")
    assert "escalation-override: four tiny rules, one file" in s["gate_log"]
    _s, body = through_ship(client, tid, decide(client, tid))
    assert "escalation-override: four tiny rules, one file" in body


def test_change_small_without_criteria_goes_back_to_triage(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"explorer": lambda r: "Hard to say.\nSIZE: trivial"})
    tid = start(client, repo, workflow="change", title="Tweak ranks")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"choice": "small"})
    assert s["waiting"]["step"] == "scope_gate"
    assert "needs 1 to 3 acceptance criteria" in seen[-1].prompt


# ------------------------------------------------------------------ engine pieces

def test_validation_of_the_new_keys():
    bad = wf([{"id": "a", "kind": "agent", "name": "a", "agent": "explorer", "choices": ["x"]},
              {"id": "g", "kind": "gate", "name": "g", "choices": ["x", "x"]},
              {"id": "c", "kind": "code", "name": "c", "action": "commit", "then": "nowhere", "back": "zz"},
              {"id": "d", "kind": "code", "name": "d"},
              {"id": "b", "kind": "branch", "name": "b", "when": {"marker": "X", "step": ["a", "q"]}, "no": "a"},
              {"id": "e", "kind": "agent", "name": "e", "agent": "explorer", "after_rounds": "a"}])
    text = "\n".join(validate(bad))
    for needle in ["choices and on_skip belong to a gate", "different names", "then target 'nowhere'", "back target 'zz'",
                   "needs an action (or a 'then')", "when.step 'q'", "after_rounds belongs to a code step"]:
        assert needle in text, needle


def test_stronger_model():
    assert stronger({"provider": "claude", "mode": "subscription", "model": "sonnet"})["model"] == "opus"
    assert stronger({"provider": "codex", "mode": "api", "model": "gpt-5.5"})["effort"] == "high"
    assert stronger({"provider": "fake", "model": "fake"}) == {"provider": "fake", "model": "fake"}


def test_an_item_agent_is_ignored_when_an_agent_made_the_list(client, repo, monkeypatch):
    lst = "```json\n" + json.dumps([{"title": "x", "agent": "implementer"}]) + "\n```"
    seen = answers(monkeypatch, {"explorer": lambda r: lst})
    w = wf([{"id": "frame", "kind": "agent", "name": "frame", "agent": "explorer", "collect": "ideas"},
            {"id": "each", "kind": "parallel", "name": "each", "agent": "investigator", "from": "ideas"}])
    wait(client, start(client, repo, workflow=w))
    assert [r.agent for r in seen] == ["explorer", "investigator"]
