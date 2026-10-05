"""The feature workflow (content/workflows/feature.yaml), walked with the fake model: preflight and the flow's own branch,
explorers after the criteria, every spec-gate choice, the freeze, the contract gate, spec amendments, integration,
security shown or hidden, the full-diff review's rounds, e2e without a tool, smoke, the optional-phase menu, waived gates,
and the seeds change and fix hand over."""

import json
from pathlib import Path

from conftest import decide, start, to_loop, wait
from test_flows_a import child, request_of
from test_flows_base import answers, values
from test_flows_cover_ship import script
from test_v02 import commit_all, configure, git, log, write
from keel_engine.models import fake as fake_mod
from keel_engine.runtime import feature_actions
from keel_engine.runtime.actions import ActionResult


def by_step(monkeypatch, plans):
    """The fake model, but plans[<step name>](req, real) decides for those steps (real = the fake's own plan)."""
    real = fake_mod._plan
    seen = []

    def plan(req):
        seen.append(req)
        fn = plans.get(req.step_name)
        return fn(req, real) if fn else real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    return seen


def text(answer):
    return lambda req, real: (None, "", answer(req) if callable(answer) else answer, {})


def to(client, tid, s, step, payloads=None):
    """Approve every pause until the flow waits at `step` (payloads: {step: payload} for the ones on the way)."""
    for _ in range(60):
        if s["status"] == "waiting" and s["waiting"]["step"] == step:
            return s
        assert s["status"] == "waiting", s
        s = decide(client, tid, payload=(payloads or {}).get(s["waiting"]["step"]))
    raise AssertionError(f"never reached {step}")


def gates_until_done(client, tid, s, payloads=None):
    seen = []
    for _ in range(60):
        if s["status"] != "waiting":
            return s, seen
        seen.append(s["waiting"]["step"])
        s = decide(client, tid, payload=(payloads or {}).get(s["waiting"]["step"]))
    raise AssertionError("too many pauses")


def spec_with(monkeypatch, *extra):
    """The fake spec, with extra lines under its criteria ([E2E], [SMOKE] ...)."""
    real = fake_mod.spec_text

    def spec_text(title, acs):
        return real(title, acs).replace("\n## Plan", "\n" + "\n".join(extra) + "\n\n## Plan")
    monkeypatch.setattr(fake_mod, "spec_text", spec_text)


SPEC = "docs/specs/player-ranks.md"
SHIP = {"ship_plan": {"skip": {"release": "r", "cover": "c", "deps": "d", "reviewers": "r", "spec_walk": "s"}}}


# ------------------------------------------------------------------ the shape and the happy path

def test_preflight_own_branch_explorers_after_the_criteria_and_the_freeze(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = wait(client, tid)
    assert git(repo, "branch", "--show-current").stdout.strip() == "feat/player-ranks"
    assert values(client, tid)["branch"] == "feat/player-ranks"
    names = [r.step_name for r in seen]
    assert names == ["spec", "one explorer per area", "one explorer per area", "plan under the criteria"]
    assert sorted(r.item["id"] for r in seen if r.item) == ["api", "data"]          # no [WEB] criterion: no web explorer
    plan = seen[-1]
    assert "MAP (api)" in plan.prompt and "MAP (data)" in plan.prompt and "ORDER: <one line>" in plan.prompt
    w = s["waiting"]
    assert w["step"] == "spec_gate" and w["options"] == ["approve"]
    assert w["choices"] == ["approve", "edit", "rewrite", "review", "order", "reject"]
    assert f"Spec: {SPEC}" in w["detail"] and "- AC-1 [API]" in w["detail"] and "Spec check" in w["detail"]
    assert "## Plan" not in w["detail"] and "Plan (files per criterion" in w["detail"]
    assert log(repo)[0] == "chore: demo project", "nothing is committed before the gate"
    s = decide(client, tid)
    assert s["waiting"]["step"] == "options"
    assert log(repo)[0] == "docs: spec and plan: Player ranks"
    front = (Path(repo) / SPEC).read_text().split("---")[1]
    assert "status: frozen" in front and "approved: 20" in front and "frozen: 20" in front
    assert git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split() == [SPEC]
    for unit in ("integration_gate", "security", "e2e", "smoke"):
        assert f"  - {unit}" in s["waiting"]["detail"], unit
    assert "  - release" not in s["waiting"]["detail"], "ship asks about its own steps"
    s = decide(client, tid)
    w = s["waiting"]
    assert w["step"] == "contract_gate" and w["choices"] == ["approve", "change", "amend"]
    assert "new file docs/contract/player-ranks.md" in w["detail"] and "contract: Player ranks" not in log(repo)
    s, gates = gates_until_done(client, tid, decide(client, tid))
    assert s["status"] == "done"
    assert gates == ["ac_gate", "ac_gate", "integration_gate", "ship_plan", "ship_final_review", "ship_pr_gate"]
    started = [e["step"] for e in client.bus.of(tid, "step.started")]
    assert {"security", "code_review", "adr", "close"} <= set(started) and "review_fix" not in [
        e["step"] for e in client.bus.of(tid, "step.started") if not e["data"].get("skipped")]
    closed = [e["data"]["note"] for e in client.bus.of(tid, "step.finished") if e["step"] == "close"]
    assert closed == ["2/2 criteria done; no ADR (no decision had a real alternative)"]


def test_a_handed_over_flow_stays_on_its_branch(client, repo, monkeypatch):
    git(repo, "checkout", "-q", "-b", "feat/ranks")
    write(repo, "src/scores/ranks.py", "X = 1\n")
    commit_all(repo, "feat: ranks")
    by_step(monkeypatch, {})
    s = wait(client, start(client, repo))
    assert s["waiting"]["step"] == "spec_gate"
    assert git(repo, "branch", "--show-current").stdout.strip() == "feat/ranks" and "feat: ranks" in log(repo)


# ------------------------------------------------------------------ every spec-gate choice

def test_spec_gate_edit_and_rewrite_come_back_to_the_gate(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    wait(client, tid)
    s = decide(client, tid, payload={"choice": "edit"}, why="AC-2: name the message")
    edit = [r for r in seen if r.step_name == "edit the spec"]
    assert len(edit) == 1 and 'chose "edit"' in edit[0].prompt and "AC-2: name the message" in edit[0].prompt
    assert s["waiting"]["step"] == "spec_gate"
    s = decide(client, tid, payload={"choice": "rewrite"}, why="out of scope: no paging")
    edit = [r for r in seen if r.step_name == "edit the spec"]
    assert len(edit) == 2 and 'chose "rewrite"' in edit[1].prompt and "no paging" in edit[1].prompt
    assert s["waiting"]["step"] == "spec_gate"
    assert [x for x in s["gate_log"] if x.startswith("gate spec_gate")] == [
        "gate spec_gate edit: AC-2: name the message", "gate spec_gate rewrite: out of scope: no paging"]
    assert log(repo)[0] == "chore: demo project", "a revision is not committed"


def test_spec_gate_review_first_shows_the_reviewer_word_for_word(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {"review the spec before approving": text("AC-2 is vague: which message?\nBLOCKING: yes")})
    tid = start(client, repo)
    wait(client, tid)
    s = decide(client, tid, payload={"choice": "review"})
    rev = [r for r in seen if r.agent == "reviewer"]
    assert len(rev) == 1 and rev[0].phase == "review" and SPEC in rev[0].prompt
    w = s["waiting"]
    assert w["step"] == "spec_gate" and "Spec review (you asked for it)" in w["detail"] and "AC-2 is vague" in w["detail"]
    assert not any(e["step"].endswith("__fix") for e in client.bus.of(tid, "step.started")), "a spec review fixes nothing"
    s = decide(client, tid, payload={"choice": "edit"}, why="AC-2 names the message")
    assert "Spec review" not in s["waiting"]["detail"]


def test_spec_gate_change_order_writes_the_plan_again(client, repo, monkeypatch):
    calls = []

    def plan(req, real):
        calls.append(req)
        path, content, _a, data = real(req)
        return path, content, "Plan written.\nORDER: " + ("AC-2, AC-1" if len(calls) > 1 else "AC-1, AC-2"), data
    by_step(monkeypatch, {"plan under the criteria": plan})
    tid = start(client, repo)
    s = wait(client, tid)
    assert [a["id"] for a in s["acs"]] == ["AC-1", "AC-2"]
    s = decide(client, tid, payload={"choice": "order"}, why="AC-2 first: it validates the input AC-1 reads")
    assert len(calls) == 2 and "AC-2 first" in calls[1].prompt
    assert s["waiting"]["step"] == "spec_gate" and [a["id"] for a in s["acs"]] == ["AC-2", "AC-1"]
    assert s["waiting"]["detail"].index("- AC-2 [API]") < s["waiting"]["detail"].index("- AC-1 [API]")
    s = to_loop(client, tid, s)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-2", "the loop follows the plan's order"


def test_spec_gate_reject_starts_the_interview_again(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    wait(client, tid)
    r = client.post(f"/threads/{tid}/resume", json={"decision": "reject"})
    assert r.status_code == 400                       # a reason is required to send back
    s = decide(client, tid, "reject", why="this is about teams, not players")
    specs = [r for r in seen if r.step_name == "spec"]
    assert len(specs) == 2 and "this is about teams, not players" in specs[1].prompt
    assert "keel-questions" in specs[1].prompt, "the interview may ask again"
    assert s["waiting"]["step"] == "spec_gate" and any(x.startswith("gate spec_gate reject") for x in s["gate_log"])


def test_the_clarify_loop_still_runs_on_the_spec_gate_before_any_explorer(client, repo, monkeypatch):
    qs = [{"id": "who", "question": "Who is a player?", "options": [{"label": "A login"}, {"label": "A name"}]}]
    block = "```keel-questions\n" + json.dumps(qs) + "\n```"
    calls = []

    def spec(req, real):
        calls.append(req)
        return (None, "", block, {}) if len(calls) == 1 else real(req)
    seen = by_step(monkeypatch, {"spec": spec})
    tid = start(client, repo, acs=None)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate" and s["waiting"]["kind"] == "clarify"
    assert [r.step_name for r in seen] == ["spec"], "no explorer before the criteria are settled"
    skipped = {e["step"] for e in client.bus.of(tid, "step.finished") if e["data"].get("skipped")}
    assert {"explore_areas", "explore", "maps", "plan"} <= skipped
    s = decide(client, tid, "approve", payload={"answers": {"who": "A name"}})
    assert s["waiting"]["step"] == "spec_gate" and s["waiting"].get("kind") != "clarify" and s["acs"]
    assert [r.step_name for r in seen].count("one explorer per area") == 2


# ------------------------------------------------------------------ the contract gate

def test_contract_gate_change_sends_the_contract_back(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = to_loop(client, tid, upto="options")
    assert s["waiting"]["step"] == "contract_gate"
    s = decide(client, tid, payload={"choice": "change"}, why="keep the old field name")
    assert s["waiting"]["step"] == "contract_gate"
    contract = [r for r in seen if r.agent == "contract-author"]
    assert len(contract) == 2 and "keep the old field name" in contract[1].prompt


# ------------------------------------------------------------------ amendments

def red_says_amend_once(monkeypatch, more=None, said="AMEND: ties need a shared rank; the spec says strictly by score", ac="AC-2"):
    calls = []

    def red(req, real):
        if req.ac["id"] == ac:
            calls.append(req)
            if len(calls) == 1:
                return None, "", f"The spec cannot be true here.\n{said}", {}
        return real(req)
    return by_step(monkeypatch, {"red": red, **(more or {})}), calls


def test_an_amendment_from_red_has_its_own_gate_and_commit(client, repo, monkeypatch):
    seen, reds = red_says_amend_once(monkeypatch)
    tid = start(client, repo)
    s = to_loop(client, tid)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-1"
    s = decide(client, tid)
    w = s["waiting"]
    assert w["step"] == "amend_gate" and w["choices"] == ["approve", "context", "change", "rebuild"]
    assert "AC-2 · red: ties need a shared rank" in w["detail"] and "### 2026-01-01 — AC-1" in w["detail"]
    assert "Was: the main case works." in w["detail"] and "finished under the old wording: AC-1" in w["detail"]
    amend = [r for r in seen if r.step_name == "spec amendment"]
    assert len(amend) == 1 and amend[0].phase == "spec" and "ties need a shared rank" in amend[0].prompt
    assert "REOPEN: <one line>" in amend[0].prompt and "CONTRACT: <one line>" in amend[0].prompt
    before = log(repo)
    s = decide(client, tid, payload={"choice": "context"}, why="ties are rare; a shared rank is fine")
    assert s["waiting"]["step"] == "amend_gate" and log(repo) == before, "nothing is committed while it is open"
    amend = [r for r in seen if r.step_name == "spec amendment"]
    assert len(amend) == 2 and "ties are rare" in amend[1].prompt
    s = decide(client, tid)
    amended = [n for n, m in enumerate(log(repo)) if m.startswith("docs: amend: ties need a shared rank")]
    assert amended == [2], log(repo)            # then AC-2's test and code
    assert git(repo, "show", "--name-only", "--format=", "HEAD~2").stdout.split() == [SPEC]
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-2" and len(reds) == 2
    assert any(x.startswith("spec amended: AC-2 · red: ties") for x in s["gate_log"])
    assert [a["status"] for a in s["acs"]] == ["done", "green"]
    v = values(client, tid)
    assert v["data"]["amendments"][0]["from"] == "red" and "AMEND" not in v["markers"]["*"]


def test_an_amendment_that_reopens_a_criterion_and_moves_the_contract(client, repo, monkeypatch):
    def amend(req, real):
        path, content, _a, data = real(req)
        return path, content, "Amended.\nREOPEN: AC-1\nCONTRACT: yes", data
    red_says_amend_once(monkeypatch, {"spec amendment": amend})
    tid = start(client, repo)
    to_loop(client, tid)
    s = decide(client, tid)
    assert s["waiting"]["step"] == "amend_gate"
    assert "Reopened by this amendment: AC-1" in s["waiting"]["detail"] and "contract is done again" in s["waiting"]["detail"]
    s = decide(client, tid)
    assert s["waiting"]["step"] == "contract_gate", "the API shape moved: the contract first"
    assert [a["status"] for a in s["acs"]][0] == "todo"
    s = decide(client, tid)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-1", "the reopened criterion is built again"


def test_the_amendment_gate_can_rebuild_the_spec_in_a_new_flow(client, repo, monkeypatch):
    red_says_amend_once(monkeypatch)
    tid = start(client, repo)
    to_loop(client, tid)
    s = decide(client, tid)
    assert s["waiting"]["step"] == "amend_gate"
    before = log(repo)
    s = decide(client, tid, payload={"choice": "rebuild"}, why="the whole ranking idea is wrong")
    assert s["status"] == "done"
    kid = child(client, s, "feature")
    assert "ties need a shared rank" in request_of(client, kid)
    k = wait(client, kid)
    assert k["waiting"]["step"] == "spec_gate"
    assert all(c in log(repo) for c in before), "the commits stay"


def test_the_contract_gate_can_amend_the_spec(client, repo, monkeypatch):
    by_step(monkeypatch, {})
    tid = start(client, repo)
    s = to_loop(client, tid, upto="options")
    assert (Path(repo) / "docs/contract/player-ranks.md").is_file()
    s = decide(client, tid, payload={"choice": "amend"}, why="this breaks the mobile client: it sends no id")
    assert s["waiting"]["step"] == "amend_gate" and "contract gate: this breaks the mobile client" in s["waiting"]["detail"]
    assert not (Path(repo) / "docs/contract/player-ranks.md").exists(), "the contract draft was put back"
    s = decide(client, tid)
    assert s["waiting"]["step"] == "contract_gate" and log(repo)[0].startswith("docs: amend: this breaks the mobile client")


def test_an_amendment_must_be_a_dated_block(client, repo, monkeypatch):
    calls = []

    def amend(req, real):
        calls.append(req)
        if len(calls) == 1:
            return None, "", "I edited AC-2 in place.\nREOPEN: none\nCONTRACT: no", {}
        return real(req)
    red_says_amend_once(monkeypatch, {"spec amendment": amend})
    tid = start(client, repo)
    to_loop(client, tid)
    s = decide(client, tid)
    assert s["waiting"]["step"] == "amend_gate" and len(calls) == 2
    assert "## Amendments" in calls[1].feedback


# ------------------------------------------------------------------ security and the full-diff review

def test_security_is_not_shown_when_clean_and_asks_when_it_finds_something(client, repo, monkeypatch):
    found = "- `src/scores/ac_1.py:1` any user reads any rank (AC-1 says only their own). Blocking.\nSECURITY: findings"
    calls = []
    real = fake_mod._plan

    def plan(req):
        if req.agent == "security-auditor":
            calls.append(req)
            return None, "", found if len(calls) == 1 else "Checked the routes.\nSECURITY: clean", {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "security__fix")
    w = s["waiting"]
    assert w["title"].startswith("security auditor + dependency triager: 1 blocking finding")
    assert "any user reads any rank" in w["detail"]
    assert [r.item["id"] for r in calls] == ["audit"], "no dependency changed: no triager"
    s = decide(client, tid)
    assert len(calls) == 2 and "fix(review): address security auditor + dependency triager findings" in log(repo)
    assert s["waiting"]["step"] == "ship_plan", "clean the second time: nothing shown"


def test_the_dependency_triager_runs_after_verify_deps_found_something(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"dependency-triager": lambda r: "lodash 4.17.20: not reachable, dev-only.\nDEPS: clean"})
    script(monkeypatch, {"verify_deps": [ActionResult(False, "Dependencies: the web audit fails.", "lodash 4.17.20 high CVE-1")]})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "ship_plan")
    tri = [r for r in seen if r.agent == "dependency-triager"]
    assert len(tri) == 1 and "CVE-1" in tri[0].prompt and tri[0].item["id"] == "deps"
    assert sorted(r.item["id"] for r in seen if r.step_name == "security auditor + dependency triager") == ["audit", "deps"]
    assert s["waiting"]["step"] == "ship_plan"


def test_the_full_diff_review_sends_findings_back_twice_then_asks(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"code-reviewer": lambda r: "- `src/scores/ac_1.py:1` duplicates ac_2.py\nCODE-REVIEW: findings"})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "code_review__fix")
    fixes = [r for r in seen if r.step_name == "fix the full-diff review's findings"]
    assert len(fixes) == 2 and all(r.phase == "review-fix" and "duplicates ac_2.py" in r.feedback for r in fixes)
    assert len([r for r in seen if r.agent == "code-reviewer"]) == 3
    assert "the limit is 2" in s["waiting"]["detail"]
    s = decide(client, tid, "reject", why="the duplication is deliberate")
    assert s["waiting"]["step"] == "ship_plan"
    assert values(client, tid)["data"]["dismissed_findings"][0]["why"] == "the duplication is deliberate"


# ------------------------------------------------------------------ e2e and smoke

def test_e2e_without_a_tool_asks_and_writes_the_specs_unrun(client, repo, monkeypatch):
    spec_with(monkeypatch, "- **AC-3** [E2E] Given two players, when the board opens, then both ranks show.")
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "e2e_tool")
    w = s["waiting"]
    assert w["choices"] == ["write", "check"] and "commands.e2e" in w["detail"] and "AC-3 [E2E]" in w["detail"]
    s = decide(client, tid)
    e2e = [r for r in seen if r.agent == "e2e-author"]
    assert len(e2e) == 1 and "do not run them" in e2e[0].prompt and "AC-3 [E2E] Given two players" in e2e[0].prompt
    assert s["waiting"]["step"] == "e2e_gate" and log(repo)[0] == "e2e: Player ranks"
    skipped = values(client, tid)["data"]["ship_skipped"]
    assert any(x["step"] == "e2e-run" and "not run" in x["reason"] for x in skipped)
    s = to(client, tid, decide(client, tid), "ship_pr_gate", SHIP)
    assert "e2e-run" in s["waiting"]["detail"], "the PR body says the e2e specs did not run"


def test_e2e_with_a_tool_does_not_ask_and_check_looks_again(client, repo, monkeypatch):
    spec_with(monkeypatch, "- **AC-3** [E2E] Given two players, when the board opens, then both ranks show.")
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "e2e_tool")
    configure(repo, "version: 4\ncommands:\n  e2e: \"true\"\n")
    s = decide(client, tid, payload={"choice": "check"})
    assert s["waiting"]["step"] == "e2e_gate", "the tool is there now: no question"
    e2e = [r for r in seen if r.agent == "e2e-author"]
    assert len(e2e) == 1 and "Run them with: true" in e2e[0].prompt


def test_no_e2e_criteria_no_e2e_and_smoke_checks_run_with_their_gate(client, repo, monkeypatch):
    spec_with(monkeypatch, "- **AC-4** [SMOKE] the health check answers UP")
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "smoke_gate")
    assert not any(r.agent == "e2e-author" and r.phase == "e2e" for r in seen)
    smoke = [r for r in seen if r.phase == "smoke"]
    assert len(smoke) == 1 and "AC-4 [SMOKE] the health check answers UP" in smoke[0].prompt
    assert log(repo)[0] == "test(smoke): Player ranks" and (Path(repo) / "smoke/player-ranks.sh").is_file()
    s = decide(client, tid, "reject", why="also check the board route")
    assert s["waiting"]["step"] == "smoke_gate" and len([r for r in seen if r.phase == "smoke"]) == 2


# ------------------------------------------------------------------ the options menu and waived gates

def test_the_options_menu_skips_security_with_a_reason(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "options")
    s = decide(client, tid, payload={"skip": {"security": "no new endpoint, no input"}})
    s = to(client, tid, s, "ship_plan")
    assert not any(r.agent == "security-auditor" for r in seen)
    assert {"step": "security", "band": "optional", "reason": "no new endpoint, no input"} in values(client, tid)["data"]["ship_skipped"]
    s = decide(client, tid, payload=SHIP["ship_plan"])
    assert "security" in s["waiting"]["detail"] and "no new endpoint" in s["waiting"]["detail"]     # the final review


def test_no_gates_waives_the_integration_gate_only(client, repo, monkeypatch):
    by_step(monkeypatch, {})
    tid = start(client, repo, data={"no_gates": True})
    s, gates = gates_until_done(client, tid, wait(client, tid))
    assert s["status"] == "done"
    assert "integration_gate" not in gates and gates[:3] == ["spec_gate", "options", "contract_gate"]
    assert "gate integration_gate approve: waived (gates waived (no_gates option))" in s["gate_log"]


# ------------------------------------------------------------------ seeds from change and fix

def test_criteria_handed_over_skip_the_interview_and_finished_ones_stay_done(client, repo, monkeypatch):
    git(repo, "checkout", "-q", "-b", "feat/tweak")
    write(repo, "src/scores/chg.py", "X = 1\n")
    commit_all(repo, "feat(CHG-1.1): first rule")
    seen = by_step(monkeypatch, {})
    acs = [{"id": "CHG-1.1", "layer": "API", "title": "first rule holds", "status": "done"},
           {"id": "CHG-1.2", "layer": "API", "title": "second rule holds"}]
    tid = start(client, repo, title="Tweak ranks", acs=acs, data={"seed": {"acs": acs, "escalated_from": "change"}})
    s = wait(client, tid)
    spec = [r for r in seen if r.step_name == "spec"][0]
    assert "keel-questions" not in spec.prompt and "CHG-1.1 [API] first rule holds (done)" in spec.prompt
    assert s["waiting"]["step"] == "spec_gate" and [a["id"] for a in s["acs"]] == ["CHG-1.1", "CHG-1.2"]
    assert "criteria handed over (1 done): no interview" in next(
        e["data"]["note"] for e in client.bus.of(tid, "step.finished") if e["step"] == "preflight")
    s = to_loop(client, tid, s)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "CHG-1.2"
    assert [a["status"] for a in s["acs"]] == ["done", "green"]
    assert "feat(CHG-1.1): first rule" in log(repo) and git(repo, "branch", "--show-current").stdout.strip() == "feat/tweak"


def test_change_hands_its_inline_criteria_to_feature(client, repo, monkeypatch):
    answers(monkeypatch, {})
    tid = start(client, repo, workflow="change", title="Tweak ranks")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"choice": "feature"})
    kid = wait(client, child(client, s, "feature"))
    assert kid["waiting"]["step"] == "spec_gate" and kid["waiting"].get("kind") != "clarify"
    assert [a["id"] for a in kid["acs"]] == ["CHG-1.1", "CHG-1.2"]


def test_fix_hands_its_evidence_to_feature_and_the_interview_runs(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    tid = start(client, repo, workflow="fix", title="Average is wrong")
    wait(client, tid)
    decide(client, tid)
    s = decide(client, tid, "approve", payload={"choice": "feature"})
    kid = child(client, s, "feature")
    k = wait(client, kid)
    assert k["waiting"]["step"] == "spec_gate"
    assert "Evidence so far" in request_of(client, kid)
    spec = [r for r in seen if r.step_name == "spec" and "Evidence so far" in r.prompt]
    assert spec and "keel-questions" in spec[0].prompt, "no criteria were handed over: the interview runs"


# ------------------------------------------------------------------ helpers

def test_merge_and_order_keep_finished_and_handed_over_criteria():
    have = [{"id": "AC-1", "layer": "API", "title": "a", "status": "done"}, {"id": "AC-2", "layer": "API", "title": "b", "status": "todo"},
            {"id": "CHG-1.1", "layer": "API", "title": "c", "status": "todo"}]
    parsed = [{"id": "AC-1", "layer": "API", "title": "a2", "status": "todo"}, {"id": "AC-3", "layer": "WEB", "title": "d", "status": "todo"}]
    out = feature_actions.merge_acs(have, parsed, drop=True)
    assert [(x["id"], x["status"], x["title"]) for x in out] == [("AC-1", "done", "a2"), ("AC-3", "todo", "d"), ("CHG-1.1", "todo", "c")]
    assert [x["id"] for x in feature_actions.merge_acs(have, parsed, drop=False)] == ["AC-1", "AC-3", "AC-2", "CHG-1.1"]
    assert feature_actions.merge_acs(have, [], drop=True) == have
    assert [x["id"] for x in feature_actions.order_acs(out, "ac-3, AC-1, AC-9")] == ["AC-3", "AC-1", "CHG-1.1"]
    assert feature_actions._frontmatter("# T\n", {"status": "frozen"}) == "---\nstatus: frozen\n---\n# T\n"
    assert feature_actions._frontmatter("---\nstatus: draft\nid: 7\n---\n# T\n", {"status": "frozen"}).startswith(
        "---\nstatus: frozen\nid: 7\n---\n")

