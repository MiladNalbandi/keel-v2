"""The review, diagnose, fix and change workflows (content/workflows), walked with the fake model through every gate
exit and branch, plus the engine pieces they added: gate choices, waived gates, `then: <step>`, a code step's
attempts + back, `when` over several steps / any item, an item's own agent, step instructions, escalate_model."""

import json
import sys
from pathlib import Path

from conftest import decide, start, wait
from test_flows_base import answers, values, wf
from test_flows_cover_ship import script, ship_plan, skip_all_but_reviews
from test_v02 import ai, commit_all, configure, git, log, write
from keel_engine.models import fake as fake_mod
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


def fake_plan():
    return fake_mod._plan


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
    seen = answers(monkeypatch, {"lens-reviewer": lambda r: f"{r.item['lens']} says hi.\n" + said.get(r.item["lens"], "BLOCKING: no")})
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
    assert [r.agent for r in seen] == ["branch-reviewer"] and "CODE-REVIEW: pass" in s["waiting"]["detail"]
    seen.clear()
    wait(client, start(client, repo, workflow="review", title="Review", data={"lens": "performance", "base": "main"}))
    assert [(r.agent, r.item["lens"]) for r in seen] == [("lens-reviewer", "performance")]


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
    assert [r.agent for r in seen] == ["criterion-reviewer"]
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
    assert s["waiting"]["step"] == "gate_r" and s["waiting"]["choices"] == ["investigate", "reject", "stop"]
    s = decide(client, tid)
    assert s["waiting"]["step"] == "gate_f" and "ROOT-CAUSE: 3 confirmed" in s["waiting"]["detail"]
    assert s["waiting"]["choices"] == ["fix", "investigate", "feature", "reject"]
    assert len([r for r in seen if r.agent == "investigator" and r.item]) == 3
    s, body = through_ship(client, tid, decide(client, tid))
    assert s["status"] == "done" and "# Average of one score is wrong" in body
    subjects = [x for x in log(repo) if "(BUG-1)" in x]
    assert subjects[0].startswith("fix(BUG-1)") and subjects[1].startswith("test(BUG-1)")
    assert not any(r.agent == "e2e-author" for r in seen)
    fix = next(r for r in seen if r.agent == "implementer")
    assert "The approved plan" in fix.prompt and "ROOT-CAUSE" in fix.prompt


def test_fix_not_reproducible_hands_over_to_diagnose(client, repo, monkeypatch):
    answers(monkeypatch, {"reproducer": lambda r: "Could not make it fail.\nREPRO: not-reproducible"})
    s = wait(client, start(client, repo, workflow="fix", title="Flaky total", request="sometimes the total is 0"))
    assert s["status"] == "done"
    kid = child(client, s, "diagnose")
    assert "sometimes the total is 0" in request_of(client, kid)
    assert not any(x.startswith("test(BUG") for x in log(repo))


def test_fix_gate_r_send_back_and_stop(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    s = decide(client, tid, "reject", why="reproduce with two scores")
    assert s["waiting"]["step"] == "gate_r" and "reproduce with two scores" in [r for r in seen if r.agent == "reproducer"][-1].prompt
    s = decide(client, tid, "approve", payload={"choice": "stop"})
    assert s["status"] == "done" and log(repo)[0].startswith("test(BUG-1)")
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
    # the fix flow's own six come first (three, then three with the stronger model); the diagnose flow it hands over to
    # may already have started its investigators by now (CI once counted nine), so only the first six are this flow's
    inv = [r for r in seen if r.agent == "investigator" and r.item][:6]
    assert len(inv) == 6 and [r.model["model"] for r in inv] == ["fake"] * 3 + ["fake-strong"] * 3
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
    assert s["status"] == "done" and not any(x.startswith("fix(BUG") for x in log(repo))
    child(client, s, "feature")


def fails_in_bug_fix(monkeypatch, n):
    """verify_green fails n times in the bug-fix phase (the fix commit's check), then runs for real."""
    real = actions_mod.verify_green
    fails = {"n": n}

    async def verify_green(a):
        if a.phase == "bug-fix" and fails["n"]:
            fails["n"] -= 1
            return actions_mod.ActionResult(False, "the bug test still fails", "AssertionError")
        return await real(a)
    monkeypatch.setattr(actions_mod, "verify_green", verify_green)


def test_fix_first_failure_goes_back_to_investigate(client, repo, monkeypatch):
    """keel v1 fix.md: the first failed fix means the cause was wrong: back to the investigation and Gate F, not
    another try of the same fix."""
    seen = answers(monkeypatch, {})
    fails_in_bug_fix(monkeypatch, 1)
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    decide(client, tid)                                  # Gate R
    s = decide(client, tid)                              # Gate F: fix; the fix fails once
    assert s["waiting"]["step"] == "gate_f", s
    agents = [r.agent for r in seen]
    assert agents.count("implementer") == 1 and agents.count("reproducer") == 1
    again = [r for r in seen if r.step_name == "hypotheses"]
    assert len(again) == 2 and "the bug test still fails" in again[-1].prompt
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ship_plan"
    assert [x for x in log(repo) if x.startswith(("test(BUG-1)", "fix(BUG-1)"))] == \
        ["fix(BUG-1): Average is wrong", "test(BUG-1): Average is wrong"]


def test_fix_reset_carries_what_was_learned(client, repo, monkeypatch):
    """The second failed fix resets (keel's uncommitted changes go back, the reproducing test stays committed) and the
    investigation starts again with what was learned; nobody reproduces the bug a second time."""
    seen = answers(monkeypatch, {"investigator": lambda r: HYPS if r.step_name == "hypotheses" else
                                 ("ROOT-CAUSE: unconfirmed" if (r.item or {}).get("title") == "race" else
                                  "Root cause: the counter is read too early.\nROOT-CAUSE: confirmed")})
    fails_in_bug_fix(monkeypatch, 2)
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    decide(client, tid)                                  # Gate R
    decide(client, tid)                                  # Gate F; the first fix fails: investigate again
    s = decide(client, tid)                              # Gate F again; the second fix fails: reset
    assert s["waiting"]["step"] == "gate_f", s
    agents = [r.agent for r in seen]
    assert agents.count("implementer") == 2 and agents.count("reproducer") == 1
    assert not (Path(repo) / "src/scores/fix_average_is_wrong.py").exists()      # the failed fix was put back
    learned = values(client, tid)["data"]["learned"]
    assert "Reset 1" in learned and "the plan's root cause did not hold: the counter is read too early" in learned
    assert "ruled out by the investigators: race" in learned and "the bug test still fails" in learned
    hyp = [r for r in seen if r.step_name == "hypotheses"][-1]
    assert "What earlier fix attempts ruled out" in hyp.prompt and "Reset 1" in hyp.prompt
    assert sum(x.startswith("test(BUG-1)") for x in log(repo)) == 1
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ship_plan"


def test_fix_commit_body_has_the_root_cause(client, repo, monkeypatch):
    answers(monkeypatch, {})
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    decide(client, tid)
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ship_plan"
    sha = git(repo, "log", "--format=%H", "--grep=fix(BUG-1)", "--fixed-strings").stdout.split()[0]
    body = git(repo, "log", "-1", "--format=%B", sha).stdout
    assert body.startswith("fix(BUG-1): Average is wrong")
    assert "Root cause: ROOT CAUSE: the counter" not in body
    assert "Root cause: the counter is read before it is written (src/scores/__init__.py:1)." in body


# ------------------------------------------------------------------ fix: the bug's own test is the evidence

def real_checks(repo, web=False):
    """The demo with real test commands: the AC command filters by the criterion's key (pytest -k bug_1)."""
    py = sys.executable
    cfg = (f"version: 4\ncommands:\n  api_test_ac: \"{py} -m pytest -q tests -k {{AC_KEY}}\"\n"
           f"  api_test_module: \"{py} -m pytest -q tests\"\n")
    if web:
        cfg += (f"  web_test_ac: \"{py} -m pytest -q . -k {{AC_KEY}}\"\n  web_test_module: \"{py} -m pytest -q .\"\n"
                "frontend: {dir: web}\n")
        write(repo, "web/README.md", "the web app\n")
    configure(repo, cfg)
    return {**SETTINGS, "simulate_checks": False}


def reproducer_writes(monkeypatch, *files):
    """The fake reproducer writes files[n] on its n-th run (the last one repeats): (path, content, answer)."""
    real = fake_mod._plan
    seen, runs = [], {"n": 0}

    def plan(req):
        seen.append(req)
        if req.agent == "reproducer":
            path, content, answer = files[min(runs["n"], len(files) - 1)]
            runs["n"] += 1
            return path, content, answer, {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    return seen


RED_BUG1 = ("tests/test_bug_1_average.py", "from scores import average\n\n\ndef test_bug_1_average_of_one():\n"
            "    assert average([5]) == 6\n", "It fails.\nLAYER: API\nREPRO: confirmed")


def test_fix_red_runs_only_the_bug_test(client, repo, monkeypatch):
    reproducer_writes(monkeypatch, RED_BUG1)
    write(repo, "tests/test_old.py", "def test_old_broken():\n    assert 1 == 2\n")     # red before the flow
    settings = real_checks(repo)
    s = wait(client, start(client, repo, workflow="fix", title="Average of one score is wrong", settings=settings))
    assert s["waiting"]["step"] == "gate_r", s
    note = [e["data"]["note"] for e in client.bus.of(s["thread_id"], "step.finished") if e["step"] == "verify_repro"][-1]
    assert "BUG-1: red confirmed (tests/test_bug_1_average.py::test_bug_1_average_of_one)" in note
    detail = s["waiting"]["detail"]
    assert "-k bug_1" in detail and "deselected" in detail and "test_old_broken" not in detail
    assert log(repo)[0] == "test(BUG-1): Average of one score is wrong"


def test_fix_preexisting_red_suite_is_not_a_reproduction(client, repo, monkeypatch):
    """A suite that was red before the flow is not a reproduction: the failing test must be the bug's own test."""
    passing = ("tests/test_bug_1_average.py", "def test_bug_1_average_of_one():\n    assert True\n",
               "It fails.\nREPRO: confirmed")
    reproducer_writes(monkeypatch, passing)
    write(repo, "tests/test_old.py", "def test_old_broken():\n    assert 1 == 2\n")
    py = sys.executable
    # the AC command does not filter: it runs the whole suite, where only the old test fails
    configure(repo, f"version: 4\ncommands:\n  api_test_ac: \"{py} -m pytest -q tests\"\n")
    s = wait(client, start(client, repo, workflow="fix", title="Average of one score is wrong",
                           settings={**SETTINGS, "simulate_checks": False, "fix_attempts": 1}))
    assert s["waiting"]["kind"] == "fix" and "verify red + commit keeps failing" in s["waiting"]["title"], s
    assert "not BUG-1's: tests/test_old.py::test_old_broken" in s["waiting"]["detail"]
    assert "A test that was failing before is not a reproduction" in s["waiting"]["detail"]
    assert not any(x.startswith("test(BUG") for x in log(repo))


def test_fix_web_layer_repro_runs_the_web_test(client, repo, monkeypatch):
    web = ("web/test_bug_1_page.py", "def test_bug_1_save_button():\n    assert 'saved' == 'nothing'\n",
           "It fails.\nREPRO: confirmed")
    reproducer_writes(monkeypatch, web)
    settings = real_checks(repo, web=True)
    tid = start(client, repo, workflow="fix", title="The save button on the scores page does nothing", settings=settings)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "gate_r", s
    detail = s["waiting"]["detail"]
    assert "BUG-1 [WEB] is reproduced" in detail and "cd web && " in detail and "-k bug_1" in detail
    assert "web/test_bug_1_page.py::test_bug_1_save_button" in detail
    assert values(client, tid)["acs"][0]["layer"] == "WEB"
    # the reproducer's LAYER line wins over keel's guess (an API-sounding report, a web test)
    web2 = ("web/test_bug_2_total.py", "def test_bug_2_total():\n    assert 1 == 2\n", "It fails.\nLAYER: WEB\nREPRO: confirmed")
    reproducer_writes(monkeypatch, web2)
    s = wait(client, start(client, repo, workflow="fix", title="Total is off by one", settings=settings))
    assert "BUG-2 [WEB] is reproduced" in s["waiting"]["detail"] and "cd web && " in s["waiting"]["detail"], s


def test_fix_intake_numbers_the_bug_after_the_history(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    git(repo, "commit", "--allow-empty", "-q", "-m", "fix(BUG-7): an old bug")
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    s = wait(client, tid)
    v = values(client, tid)
    assert [(x["id"], x["layer"]) for x in v["acs"]] == [("BUG-8", "API")] and v["ac"] == "BUG-8", s
    repro = next(r for r in seen if r.agent == "reproducer")
    assert "name it after the bug's id BUG-8" in repro.prompt and "- BUG-8 [API] Average is wrong" in repro.prompt
    assert log(repo)[0] == "test(BUG-8): Average is wrong"


# ------------------------------------------------------------------ fix: Gate R and Gate F

def test_fix_gate_r_shows_the_failing_test_and_output(client, repo, monkeypatch):
    reproducer_writes(monkeypatch, RED_BUG1)
    s = wait(client, start(client, repo, workflow="fix", title="Average of one score is wrong", settings=real_checks(repo)))
    w = s["waiting"]
    assert w["step"] == "gate_r", s
    assert "Reproducing test: tests/test_bug_1_average.py::test_bug_1_average_of_one" in w["detail"]
    assert "Command: " in w["detail"] and "assert 5.0 == 6" in w["detail"]
    info = {c["name"]: c for c in w["choice_info"]}
    assert list(info) == ["investigate", "reject", "stop"] and info["investigate"]["default"]
    assert (info["reject"]["label"], info["reject"]["why"]) == ("Reproduce differently", "required")
    assert info["stop"]["label"] == "Stop here"


def test_fix_gate_r_reject_replaces_the_red_commit(client, repo, monkeypatch):
    first = ("tests/test_bug_1_first.py", "def test_bug_1_first():\n    assert 1 == 2\n", "REPRO: confirmed")
    second = ("tests/test_bug_1_second.py", "def test_bug_1_second():\n    assert 2 == 3\n", "REPRO: confirmed")
    seen = reproducer_writes(monkeypatch, first, second)
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    r = client.post(f"/threads/{tid}/resume", json={"decision": "reject"})
    assert r.status_code == 400 and "Say why" in r.text              # reproduce differently needs a note
    s = decide(client, tid, "reject", why="use two scores")
    assert s["waiting"]["step"] == "gate_r", s
    assert [x for x in log(repo) if x.startswith("test(BUG")] == ["test(BUG-1): Average is wrong"]
    assert not (Path(repo) / "tests/test_bug_1_first.py").exists() and (Path(repo) / "tests/test_bug_1_second.py").exists()
    files = git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert files == ["tests/test_bug_1_second.py"]
    again = [r for r in seen if r.agent == "reproducer"][-1].prompt
    assert "use two scores" in again and "rejected at Gate R" in again and "tests/test_bug_1_first.py" in again
    assert "tests/test_bug_1_second.py::test_bug_1_second" in s["waiting"]["detail"]


def test_fix_gate_f_investigate_more_goes_back_to_hypotheses(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    s = decide(client, tid)
    info = {c["name"]: c for c in s["waiting"]["choice_info"]}
    assert info["investigate"]["label"] == "Investigate more" and info["reject"]["label"] == "Change the approach"
    assert info["fix"]["default"]
    s = decide(client, tid, "approve", why="check the cache too", payload={"choice": "investigate"})
    assert s["waiting"]["step"] == "gate_f", s
    hyps = [r for r in seen if r.step_name == "hypotheses"]
    assert len(hyps) == 2 and "check the cache too" in hyps[-1].prompt
    assert len([r for r in seen if r.agent == "investigator" and r.item]) == 6
    assert not any(r.agent == "implementer" for r in seen)


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


def serves_http(repo):
    """The demo is a library: an e2e step is skipped there (B12). These tests give it an HTTP layer."""
    write(repo, "requirements.txt", "flask\n")
    commit_all(repo, "chore: serve the scores")


def test_fix_from_a_hunt_seed_writes_the_regression_e2e(client, repo, monkeypatch):
    serves_http(repo)
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
    assert any(r.agent == "e2e-author" for r in seen) and log(repo)[0].startswith("e2e(BUG-1)")


def test_a_fix_from_hunt_next_gets_its_own_fix_branch_from_the_base(client, repo, monkeypatch):
    """keel v1 hunt: each finding is drained into its own fix/ branch. The fix that hunt-next starts works in a worktree
    of its own, on fix/<slug> from the base branch: not on the hunt's branch, so fixes do not stack into one PR."""
    answers(monkeypatch, {})
    on_branch(repo, "feat/hunt-run")                     # the hunt's branch, with its own commit
    w = wf([{"id": "hand", "kind": "code", "name": "hand to fix", "action": "start_flow", "flow": "fix", "then": "end",
             "seed": {"title": "F-001: ranks overflow", "request": "500 on /ranks", "hunt": {"run": "H-1", "findings": ["F-001"]}}}])
    s = wait(client, start(client, repo, workflow=w))
    kid = child(client, s, "fix")
    k = wait(client, kid)
    assert k["waiting"]["step"] == "gate_r", k
    started = client.bus.of(kid, "thread.started")[-1]["data"]
    wt = started["worktree"]
    assert wt["branch"] == "fix/f-001-ranks-overflow" and wt["base"] == "main" and wt["name"].startswith("flow-f-001-ranks")
    assert started["root"].startswith(str(Path(repo) / ".keel/worktrees"))
    root = started["root"]
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "fix/f-001-ranks-overflow"
    assert git(root, "log", "--format=%s", "-2").stdout.splitlines() == ["test(BUG-1): F-001: ranks overflow", "chore: demo project"]
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "feat/hunt-run"   # the folder did not move
    assert not any(x.startswith("test(BUG") for x in log(repo))
    # a fix started without a hunt seed works where its parent works
    w = wf([{"id": "hand", "kind": "code", "name": "hand to fix", "action": "start_flow", "flow": "fix", "then": "end",
             "seed": {"title": "Average is wrong"}}])
    kid = child(client, wait(client, start(client, repo, workflow=w)), "fix")
    wait(client, kid)
    assert "worktree" not in client.bus.of(kid, "thread.started")[-1]["data"]
    assert log(repo)[0].startswith("test(BUG-2)")


def test_fix_evidence_and_symptoms_reach_the_investigators_and_the_plan_not_the_reproducer(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    tid = start(client, repo, workflow="fix", title="F-001: ranks overflow", request="500 on /ranks",
                data={"evidence": "G-01: the rank table is sized to 1000", "symptoms": ["F-001: 500 on /ranks", "F-007: empty page 11"]})
    wait(client, tid)
    decide(client, tid)
    repro = next(r for r in seen if r.agent == "reproducer").prompt
    assert "rank table is sized" not in repro and "regression criterion" not in repro
    hyp = next(r for r in seen if r.step_name == "hypotheses").prompt
    assert "Evidence handed over (a theory to check, not a fact):\nG-01: the rank table is sized to 1000" in hyp
    assert all("rank table is sized" in r.prompt for r in seen if r.agent == "investigator" and r.item)
    plan = next(r for r in seen if r.step_name == "fix plan").prompt
    assert "Each of these symptoms is a regression criterion" in plan and "- F-007: empty page 11" in plan
    assert "rank table is sized" in plan
    # without evidence or a group: plain sentences, never a gap
    seen.clear()
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    decide(client, tid)
    plan = next(r for r in seen if r.step_name == "fix plan").prompt
    assert "No evidence was handed over with this bug." in plan and "The reproducing test is the regression criterion." in plan
    assert "(nothing yet)" not in next(r for r in seen if r.step_name == "fix plan").prompt.split("The investigators")[0]


def test_fix_plan_can_ask_for_the_e2e(client, repo, monkeypatch):
    serves_http(repo)
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


def test_change_scope_gate_shows_the_criteria_once_and_no_agent_chatter(client, repo, monkeypatch):
    """0.18.57 real use: the scope gate showed "Same session — keeping the criteria…" and the triage's own AC-1/AC-2
    lines above keel's CHG-1.1/CHG-1.2 list: the same criteria twice, with different ids."""
    said = ("Same session — keeping the criteria from the first triage.\n\n"
            "Small: the rank orders by wins now, and a test can see it. Files: src/scores/rank.py.\n\n"
            "Criteria:\n- **AC-1** [API] the rank orders players by `wins`\n- **AC-2** [API] a tie keeps the older player first\n\n"
            "SIZE: small")
    answers(monkeypatch, {"explorer": lambda r: said})
    tid = start(client, repo, workflow="change", title="Tweak ranks")
    d = wait(client, tid)["waiting"]["detail"]
    assert "Small: the rank orders by wins now" in d and "Files: src/scores/rank.py." in d
    assert "- CHG-1.1 [API] the rank orders players by `wins`" in d and "- CHG-1.2 [API] a tie keeps" in d
    assert "Same session" not in d and "AC-1" not in d and "AC-2" not in d and "SIZE: small" not in d
    assert d.count("the rank orders players by") == 1 and d.count("Criteria:") == 1


def test_change_trivial_is_one_refactor_commit(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"explorer": lambda r: "A rename; no test could notice.\nSIZE: trivial"})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="change", title="Rename the helper")
    s = wait(client, tid)
    assert s["waiting"]["detail"].startswith("Proposed: trivial")
    assert "(default trivial)" in s["waiting"]["detail"]
    # a plain approve takes the triage's recommendation (real run: a rename became three criteria with the first choice)
    s, _body = through_ship(client, tid, decide(client, tid, "approve"))
    assert s["status"] == "done" and "refactor: Rename the helper" in log(repo)
    assert not any(r.agent == "test-author" for r in seen)


def test_change_trivial_that_is_not_trivial_goes_back_to_triage(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"explorer": lambda r: "SIZE: trivial"})
    real = actions_mod.commit

    def commit(a):
        if a.phase == "trivial":
            return actions_mod.ActionResult(False, "This is not a trivial change: it edits existing tests.", "tests/test_scores.py",
                                            final=True)
        return real(a)
    monkeypatch.setattr(actions_mod, "commit", commit)
    tid = start(client, repo, workflow="change", title="Rename the helper")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"choice": "trivial"})
    assert s["waiting"]["step"] == "scope_gate", s
    # the refusal is final: no second try in the same place, straight back to triage
    assert [r.agent for r in seen] == ["explorer", "implementer", "explorer"]
    assert "edits existing tests" in seen[-1].prompt
    assert git(repo, "status", "--porcelain").stdout.strip() == ""      # the trivial agent's edit was put back


def test_a_second_triage_gets_its_earlier_answer_and_a_changed_one_is_shown(client, repo, monkeypatch):
    """0.18.74 triage once: a field report's change flow said feature, then small, 22 s later, with no reason."""
    sizes = iter(["SIZE: trivial", "WHY: it edits a test\nSIZE: small"])
    seen = answers(monkeypatch, {"explorer": lambda r: next(sizes)})
    real = actions_mod.commit

    def commit(a):
        if a.phase == "trivial":
            return actions_mod.ActionResult(False, "This is not a trivial change: it edits existing tests.", "tests/x.py",
                                            final=True)
        return real(a)
    monkeypatch.setattr(actions_mod, "commit", commit)
    tid = start(client, repo, workflow="change", title="Rename the helper")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"choice": "trivial"})
    assert "You triaged this change before: SIZE trivial" in seen[-1].prompt
    assert s["waiting"]["step"] == "scope_gate"
    assert "Triage changed its answer: earlier trivial, now small." in s["waiting"]["detail"]


def test_change_trivial_red_suite_retries_before_retriage(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"explorer": lambda r: "A rename; no test could notice.\nSIZE: trivial"})
    script(monkeypatch, ship_plan())
    real = actions_mod.verify_green
    left = {"n": 2}

    async def verify_green(a):
        if a.phase == "trivial" and left["n"]:
            left["n"] -= 1
            return actions_mod.ActionResult(False, "1 test fails: test_rank", "AssertionError")
        return await real(a)
    monkeypatch.setattr(actions_mod, "verify_green", verify_green)
    tid = start(client, repo, workflow="change", title="Rename the helper")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"choice": "trivial"})
    # a red suite is a failed try, not "not trivial": two more rounds with the implementer, the work is kept
    assert s["waiting"]["step"] == "ship_plan", s
    assert [r.agent for r in seen] == ["explorer", "implementer", "implementer", "implementer"]
    assert "1 test fails: test_rank" in seen[-1].prompt and "refactor: Rename the helper" in log(repo)


def test_change_override_is_per_trigger(client, repo, monkeypatch):
    configure(repo, "change: {auth_paths: ['src/scores/**']}\n")
    real = fake_plan()

    def plan(req):
        if req.agent == "implementer" and (req.ac or {}).get("id") == "CHG-1.2":
            return "db/migration/V9__ranks.sql", "alter table scores add column rank int;\n", "GREEN: CHG-1.2 passes.", {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    tid = start(client, repo, workflow="change", title="Tweak ranks")
    wait(client, tid)
    s = decide(client, tid)
    assert s["waiting"]["title"] == "Escalate to a feature flow?" and "auth" in s["waiting"]["detail"], s
    s = decide(client, tid, "reject", why="it is a one-line helper")
    assert "escalation-override [auth]: it is a one-line helper" in s["gate_log"]
    # CHG-1.2 adds a migration: the auth override does not cover it, so keel asks again (auth stays silenced)
    for _ in range(6):
        if s["waiting"]["title"] == "Escalate to a feature flow?":
            break
        s = decide(client, tid)
    assert s["waiting"]["title"] == "Escalate to a feature flow?", s
    assert "migration" in s["waiting"]["detail"] and "auth" not in s["waiting"]["detail"]


def test_change_suggest_trigger_shown_not_blocking(repo):
    from keel_engine.rules import checks
    from keel_engine.runtime.actions import commit
    write(repo, "apps/api/src/Rank.kt", "class Rank\n")
    write(repo, "apps/web/src/Rank.tsx", "export const Rank = 1;\n")
    gates = {"mode": "end", "log": [], "skipped": {}}
    a = ai(repo, "trivial", flow="change", state={"gates": gates})
    r = commit(a)
    assert r.ok and "suggest: both apps changed (not blocking)" in r.note, r
    line = r.update["gates"]["log"][-1]
    assert line.startswith("escalation-suggest [both-apps]: both apps changed") and "no gate here" in line
    # shown once: the next commit that changes both apps again says nothing more
    write(repo, "apps/api/src/Rank.kt", "class Rank2\n")
    write(repo, "apps/web/src/Rank.tsx", "export const Rank = 2;\n")
    r2 = commit(ai(repo, "trivial", flow="change", gates_log=r.update["gates"]["log"], state={"gates": r.update["gates"]}))
    assert r2.ok and "suggest" not in r2.note and "gates" not in r2.update
    assert checks.escalation({}, ["apps/api/a.kt"], [])[1] == []


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
