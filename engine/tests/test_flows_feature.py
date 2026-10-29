"""The feature workflow (content/workflows/feature.yaml), walked with the fake model: preflight and the flow's own branch,
explorers after the criteria, every spec-gate choice, the freeze, the contract gate, spec amendments, integration,
security shown or hidden, the full-diff review's rounds, e2e without a tool, smoke, the optional-phase menu, waived gates,
and the seeds change and fix hand over."""

import json
from pathlib import Path

from conftest import decide, start, to_loop, wait
from test_flows_a import child, request_of, serves_http
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
# v0.17.5 ship's menu is answered at the spec gate (menus: true)
SKIPS = {"release": "r", "cover": "c", "deps": "d", "reviewers": "r", "spec_walk": "s"}
SHIP = {"spec_gate": {"choice": "approve", "skip": SKIPS}}


# ------------------------------------------------------------------ the shape and the happy path

def test_preflight_own_branch_explorers_after_the_criteria_and_the_freeze(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = wait(client, tid)
    assert git(repo, "branch", "--show-current").stdout.strip() == "feat/player-ranks"
    assert values(client, tid)["branch"] == "feat/player-ranks"
    names = [r.step_name for r in seen]
    # 0.18.21 web_check first: "Player ranks" needs nothing from outside, so web_docs does not run; then the interview
    assert names == ["outside docs needed?", "interview", "spec", "one explorer per area", "one explorer per area",
                     "plan under the criteria"]
    assert sorted(r.item["id"] for r in seen if r.item) == ["api", "data"]          # no [WEB] criterion: no web explorer
    plan = seen[-1]
    assert "MAP (api)" in plan.prompt and "MAP (data)" in plan.prompt and "ORDER: <one line>" in plan.prompt
    w = s["waiting"]
    assert w["step"] == "spec_gate" and w["options"] == ["approve"]
    assert w["choices"] == ["approve", "edit", "rewrite", "review", "order", "reject"]
    assert f"Spec: `{SPEC}`" in w["detail"] and "- `AC-1` [API]" in w["detail"] and "## Spec check" in w["detail"]
    # 0.18.25 the gate is a Markdown document: one ## part each, the spec's own Plan heading under the gate's
    for head in ("## Spec\n", "## Criteria\n", "## Plan (files per criterion, contract delta, test layer)", "## Your choice"):
        assert head in w["detail"], head
    assert "\n## Plan\n" not in w["detail"] and "- **approve**: freeze the spec" in w["detail"]
    assert log(repo)[0] == "chore: demo project", "nothing is committed before the gate"
    # v0.17.5 the optional-step menus are part of the spec gate: no separate stop
    for unit in ("integration_gate", "security", "e2e", "smoke", "release"):
        assert f"  - {unit}" in w["detail"], unit
    assert "— which optional phases run —" in w["detail"] and "decided here with this approval" in w["detail"]
    s = decide(client, tid)
    assert log(repo)[0] == "docs: spec and plan: Player ranks"
    front = (Path(repo) / SPEC).read_text().split("---")[1]
    assert "status: frozen" in front and "approved: 20" in front and "frozen: 20" in front
    assert git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split() == [SPEC]
    assert any(x == "gate options approve: chosen at spec_gate" for x in s["gate_log"])
    w = s["waiting"]
    assert w["step"] == "contract_gate" and w["choices"] == ["approve", "change", "amend"]
    assert "new file `docs/contract/player-ranks.md`" in w["detail"] and "contract: Player ranks" not in log(repo)
    s, gates = gates_until_done(client, tid, decide(client, tid))
    assert s["status"] == "done"
    assert gates == ["ac_gate", "ac_gate", "integration_gate", "ready_gate", "ship_final_review", "ship_pr_gate"]
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
    rev = [r for r in seen if r.agent == "lens-reviewer"]
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
    assert s["waiting"]["detail"].index("- `AC-2` [API]") < s["waiting"]["detail"].index("- `AC-1` [API]")
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
    assert [r.step_name for r in seen] == ["outside docs needed?", "interview", "spec"], "no explorer before the criteria are settled"
    skipped = {e["step"] for e in client.bus.of(tid, "step.finished") if e["data"].get("skipped")}
    assert {"explore_areas", "explore", "maps", "plan"} <= skipped
    s = decide(client, tid, "approve", payload={"answers": {"who": "A name"}})
    assert s["waiting"]["step"] == "spec_gate" and s["waiting"].get("kind") != "clarify" and s["acs"]
    assert [r.step_name for r in seen].count("one explorer per area") == 2


# ------------------------------------------------------------------ the interview (0.18.21, keel v1's lenses)

LENS_QS = [{"id": lens, "question": f"{lens}: which one?", "why": "it changes the criteria",
            "options": [{"label": f"{lens} A", "description": "cheap", "recommended": True}, {"label": f"{lens} B"}]}
           for lens in ("scope", "edge-cases", "validation", "security", "data", "rollout", "ux")]


def test_the_interview_asks_one_question_per_open_lens_and_the_answers_reach_the_spec(client, repo, monkeypatch):
    block = "- goal and users: the request says players\n```keel-questions\n" + json.dumps(LENS_QS) + "\n```"
    calls = []

    def interview(req, real):
        calls.append(req)
        if len(calls) == 1:
            return None, "", block, {}
        return None, "", "- goal and users: players\n- scope: scope A (asked)\n- data: data B (asked)\nNO_QUESTIONS: answered", {}
    seen = by_step(monkeypatch, {"interview": interview})
    tid = start(client, repo, acs=None, request="Show each player's rank next to the top 10.")
    s = wait(client, tid)
    w = s["waiting"]
    assert w["step"] == "interview_gate" and w["kind"] == "clarify", w
    assert [q["id"] for q in w["questions"]] == ["scope", "edge-cases", "validation", "security", "data", "rollout"]  # 6 at most
    assert w["questions"][0]["options"][0]["recommended"]
    assert [r.step_name for r in seen] == ["outside docs needed?", "interview"], "nothing is written before the person answers"
    first = calls[0].prompt
    assert "goal and users" in first and "rollout" in first and "out of scope" in first and "NO_QUESTIONS" in first
    assert "Write the spec under" not in first, "the interview writes no spec"
    s = decide(client, tid, "approve", why="ranks are public", payload={"answers": {"scope": "scope A", "data": "data B"}})
    assert len(calls) == 2 and calls[1].feedback.startswith("Answers to your questions:")
    assert "Now finish your step with these answers" in calls[1].feedback and "write the spec" not in calls[1].feedback
    spec = next(r for r in seen if r.step_name == "spec")
    assert "1. scope: which one?\n   → scope A" in spec.prompt and "→ data B" in spec.prompt
    assert "→ (not answered: use the recommended one)" in spec.prompt
    assert "in the person's words: ranks are public" in spec.prompt
    assert "- scope: scope A (asked)" in spec.prompt                      # the interview's record
    assert s["waiting"]["step"] == "spec_gate" and s["acs"]
    assert any("answered the explorer's 6 question(s)" in x or "interview_gate answered" in x for x in s["gate_log"]), s["gate_log"]


def test_a_request_that_answers_every_lens_skips_the_interview_gate(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo, acs=None)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"
    assert [r.step_name for r in seen][:3] == ["outside docs needed?", "interview", "spec"]
    assert any(x.startswith("gate interview_gate approve: not asked") for x in s["gate_log"]), s["gate_log"]
    spec = next(r for r in seen if r.step_name == "spec")
    assert "NO_QUESTIONS: the request answers every lens" in spec.prompt and "The person's answers:\n(nothing yet)" in spec.prompt


def test_handed_over_criteria_skip_the_interview(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo, acs=[{"id": "AC-1", "layer": "API", "title": "Given scores, when ranked, then ties share a rank"}])
    wait(client, tid)
    assert "interview" not in [r.step_name for r in seen]


def test_the_spec_step_is_told_keels_template_and_its_sections(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    wait(client, start(client, repo, acs=None))
    spec = next(r for r in seen if r.step_name == "spec").prompt
    assert "templates/spec.md" in spec
    for section in ("Goal", "User stories", "Findings (evidence)", "Acceptance criteria", "Test layers", "Edge cases",
                    "Validation and security rules", "Risks and cost", "Unverified and open questions", "Out of scope",
                    "Definition of done"):
        assert section in spec, section


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
    times = {"n": 0}

    def contract(req, real):
        path, content, said, data = real(req)
        times["n"] += 1
        return path, content + ("\nGET /ranks now returns shared ranks.\n" if times["n"] > 1 else ""), said, data
    red_says_amend_once(monkeypatch, {"spec amendment": amend, "contract": contract})
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


def test_the_contract_gate_lists_a_removed_field_under_breaks_callers(client, repo, monkeypatch):
    import yaml
    from test_contract_delta import OLD
    write(repo, "docs/contract/openapi.yaml", yaml.safe_dump(OLD))
    commit_all(repo, "chore: the contract")
    gone = yaml.safe_load(yaml.safe_dump(OLD))
    del gone["components"]["schemas"]["Rank"]["properties"]["score"]
    by_step(monkeypatch, {"contract": lambda req, real: ("docs/contract/openapi.yaml", yaml.safe_dump(gone), "score is gone", {})})
    tid = start(client, repo)
    s = to_loop(client, tid, upto="options")
    w = s["waiting"]
    assert w["step"] == "contract_gate"
    d = w["detail"]
    assert d.startswith("## In plain words\n\n") and "| `GET /ranks response 200.score` | removed |" in d
    assert "**Verdict: breaks: `GET /ranks response 200.score` removed" in d and "\n```diff\ndiff --git a/docs/contract/openapi.yaml" in d
    started = [e["step"] for e in client.bus.of(tid, "step.started")]
    assert started.index("contract_check") < started.index("contract_diff")


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
    assert w["detail"].startswith("## Blocking findings (1)\n\n- **[") and "\n## What the buttons do\n" in w["detail"]
    assert "- **Fix them**: the implementer fixes" in w["detail"] and "- **Go on anyway**: say why" in w["detail"]
    assert [r.item["id"] for r in calls] == ["audit"], "no dependency changed: no triager"
    s = decide(client, tid)
    assert len(calls) == 2 and "fix(review): address security auditor + dependency triager findings" in log(repo)
    assert s["waiting"]["step"] == "ready_gate", "clean the second time: nothing shown"


def test_the_ac_gate_shows_the_commits_the_review_and_the_board(client, repo, monkeypatch):
    """WP-B: the AC gate shows the criterion, RED sha + test files, GREEN sha + diffstat, the review and the board."""
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "ac_gate")
    d = s["waiting"]["detail"]
    red = git(repo, "log", "--format=%h", "--grep=test(AC-1)", "--fixed-strings").stdout.split()[0]
    green = git(repo, "log", "--format=%h", "--grep=feat(AC-1)", "--fixed-strings").stdout.split()[0]
    assert d.startswith("## Criterion\n\n`AC-1` [API] Player ranks: the main case works")
    assert f"**RED**: `{red}` `test(AC-1)" in d and "Test files:\n\n- `" in d
    assert f"**GREEN**: `{green}` `feat(AC-1)" in d and "changed" in d and "```text\n" in d, "the diffstat, fenced"
    assert "**AC-REVIEW: pass**" in d and "The criterion-reviewer's answer:" in d and "the test proves AC-1" in d
    assert "Board: done - · now `AC-1` · todo `AC-2`" in d
    s = decide(client, tid)
    s = to(client, tid, s, "ac_gate")
    assert "Board: done `AC-1` · now `AC-2` · todo -" in s["waiting"]["detail"]


TWO_FINDINGS = ("**Blocking**\n\n- `src/scores/ac_1.py:1` any user reads any rank.\n- `src/scores/ac_2.py:1` the error "
                "names the table.\n\n**Non-blocking**\n- `src/scores/ac_1.py:2` the name could be clearer\n\nSECURITY: findings")


def test_security_findings_are_decided_one_by_one(client, repo, monkeypatch):
    """WP-F: one finding fixed, one dismissed as not a finding with its reason; non-blocking ones are kept."""
    calls = []
    real = fake_mod._plan

    def plan(req):
        if req.agent == "security-auditor":
            calls.append(req)
            return None, "", TWO_FINDINGS, {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "security__fix")
    w = s["waiting"]
    assert [q["id"] for q in w["questions"]] == ["f1", "f2"]
    assert [o["label"] for o in w["questions"][0]["options"]] == ["fix", "accept", "not a finding", "amend spec"]
    assert w["questions"][0]["options"][0]["recommended"] and w["questions"][0]["options"][1]["why"] == "required"
    assert "decide each finding below" in w["detail"]
    nb = values(client, tid)["data"]["nonblocking_findings"]
    assert [x["text"] for x in nb] == ["`src/scores/ac_1.py:2` the name could be clearer"] and nb[0]["step"] == "security"
    # a dismissal without a reason is refused before the flow resumes
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve", "payload": {"answers": {"f2": "not a finding"}}})
    assert r.status_code == 400 and "needs a reason" in r.text
    s = decide(client, tid, payload={"answers": {"f1": "fix", "f2": "not a finding: the message is generic in prod"}})
    assert len(calls) == 2, "the review runs again after the fix"
    assert "fix(review): address security auditor + dependency triager findings" in log(repo)
    v = values(client, tid)
    dismissed = v["data"]["dismissed_findings"]
    assert dismissed == [{"step": "security", "name": "security auditor + dependency triager", "item": None,
                          "lens": "security auditor + dependency triager: security audit of the branch diff",
                          "findings": ["`src/scores/ac_2.py:1` the error names the table."],
                          "decision": "not a finding", "why": "the message is generic in prod"}]
    assert any("1 finding(s) accepted: not a finding: the message is generic in prod" in x for x in s["gate_log"])
    # the second review reports both again: the dismissed one is not asked again
    assert s["waiting"]["step"] == "security__fix"
    assert [q["question"] for q in s["waiting"]["questions"]] == ["`src/scores/ac_1.py:1` any user reads any rank."]
    assert len(values(client, tid)["data"]["nonblocking_findings"]) == 1, "the same non-blocking one is kept once"
    s = decide(client, tid, "reject", why="tracked in ticket 7")
    assert s["waiting"]["step"] == "ready_gate"     # wp-degh: "ready to ship?" comes before ship
    last = values(client, tid)["data"]["dismissed_findings"][-1]
    assert last["decision"] == "accept" and last["why"] == "tracked in ticket 7"


def test_a_security_finding_can_amend_the_spec(client, repo, monkeypatch):
    """WP-F: "amend spec" starts the amendment loop with the finding as its reason."""
    real = fake_mod._plan

    def plan(req):
        if req.agent == "security-auditor":
            return None, "", TWO_FINDINGS, {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "security__fix")
    s = decide(client, tid, payload={"answers": {"f1": "amend spec: only the owner may read a rank",
                                                 "f2": "accept: generic in prod"}})
    v = values(client, tid)
    assert "any user reads any rank" in v["data"]["amend"]["reason"] and "only the owner" in v["data"]["amend"]["reason"]
    assert v["data"]["dismissed_findings"][0]["decision"] == "accept"
    assert s["waiting"]["step"] == "amend_gate"


def test_the_dependency_triager_runs_after_verify_deps_found_something(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"dependency-triager": lambda r: "lodash 4.17.20: not reachable, dev-only.\nDEPS: clean"})
    script(monkeypatch, {"verify_deps": [ActionResult(False, "Dependencies: the web audit fails.", "lodash 4.17.20 high CVE-1")]})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "ship_final_review", SHIP)
    tri = [r for r in seen if r.agent == "dependency-triager"]
    assert len(tri) == 1 and "CVE-1" in tri[0].prompt and tri[0].item["id"] == "deps"
    assert sorted(r.item["id"] for r in seen if r.step_name == "security auditor + dependency triager") == ["audit", "deps"]
    assert s["waiting"]["step"] == "ship_final_review"


def test_the_full_diff_review_sends_findings_back_twice_then_asks(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"branch-reviewer": lambda r: "- `src/scores/ac_1.py:1` duplicates ac_2.py\nCODE-REVIEW: findings"})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "code_review__fix")
    fixes = [r for r in seen if r.step_name == "fix the full-diff review's findings"]
    # 0.18.71 the same finding after a fix round: no second automatic round, the person decides now, told why
    assert len(fixes) == 1 and all(r.phase == "review-fix" and "duplicates ac_2.py" in r.feedback for r in fixes)
    assert len([r for r in seen if r.agent == "branch-reviewer"]) == 2
    assert "the same findings as the last round" in s["waiting"]["detail"]
    s = decide(client, tid, "reject", why="the duplication is deliberate")
    assert s["waiting"]["step"] == "ready_gate"
    assert values(client, tid)["data"]["dismissed_findings"][0]["why"] == "the duplication is deliberate"


def test_a_fix_round_that_changes_the_spec_is_put_back_and_the_question_says_amend_the_spec(client, repo, monkeypatch):
    """0.18.71 a field report: the review-fix phase may not change the spec, keel put the line back each round, and the
    same finding came back four times. Now the question names the file and recommends amend spec."""
    def spec_file():
        return next(str(p.relative_to(repo)) for p in Path(repo).rglob("*.md") if "spec" in str(p).lower() and ".keel" not in str(p))

    def fix(req, real):
        f = spec_file()
        return f, (Path(repo) / f).read_text() + "\nThe webhook answers 200.\n", "Fixed the spec line.", {}
    seen = by_step(monkeypatch, {"fix the full-diff review's findings": fix})
    answers(monkeypatch, {"branch-reviewer": lambda r: f"- `{spec_file()}:44` says 201, the code answers 200\nCODE-REVIEW: findings"})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "code_review__fix")
    detail = s["waiting"]["detail"]
    assert "keel put back" in detail and spec_file() in detail and "amend spec" in detail
    q = s["waiting"]["questions"][0]
    assert q["options"][0]["label"] == "amend spec" and q["options"][0]["recommended"]


# ------------------------------------------------------------------ refactor, smoke before e2e, stack up, gate mode

def test_refactor_runs_after_green_commits_only_when_it_tidied_and_is_skippable(client, repo, monkeypatch):
    def refactor(req, real):
        if (req.ac or {}).get("id") == "AC-1":
            return "src/scores/ac_1.py", 'def run():\n    return "AC-1 done"  # tidied\n', "Renamed a helper.\nREFACTOR: done", {}
        return None, "", "Nothing worth tidying.\nREFACTOR: none", {}
    seen = by_step(monkeypatch, {"refactor (tests frozen)": refactor})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "ready_gate")
    assert [r.phase for r in seen if r.step_name == "refactor (tests frozen)"] == ["refactor", "refactor"]
    assert [m for m in log(repo) if m.startswith("refactor(")] == ["refactor(AC-1): Player ranks: the main case works"]
    skipped = [e["data"]["note"] for e in client.bus.of(tid, "step.finished") if e["step"] == "refactor_commit" and e["data"].get("skipped")]
    assert len(skipped) == 1 and "REFACTOR" in skipped[0]
    # skipped at the spec gate: no refactor agent at all
    seen2 = by_step(monkeypatch, {})
    tid = start(client, repo)
    to(client, tid, wait(client, tid), "ready_gate", {"spec_gate": {"choice": "approve", "skip": {"refactor": "a tiny change"}}})
    assert not [r for r in seen2 if r.step_name == "refactor (tests frozen)"]


def test_smoke_runs_before_e2e_and_the_stack_comes_up_first(client, repo, monkeypatch):
    spec_with(monkeypatch, "- **AC-5** [E2E] a player sees their rank", "- **AC-6** [SMOKE] the rank page answers")
    configure(repo, "version: 4\ncommands: {services: 'echo up', migrate: 'echo migrated', e2e: 'true'}\n")
    by_step(monkeypatch, {})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "ready_gate")
    started = [e["step"] for e in client.bus.of(tid, "step.started")]
    assert started.index("stack_up") < started.index("smoke_scope") < started.index("smoke") < started.index("e2e_scope") < started.index("e2e")
    note = [e["data"]["note"] for e in client.bus.of(tid, "step.finished") if e["step"] == "stack_up"][-1]
    assert note == "stack up + migrate: simulated"


def test_stack_up_only_when_configured_and_needed(tmp_path):
    from test_preflight import project
    from keel_engine.runtime.actions import ActionInput
    root = project(tmp_path, "version: 4\ncommands: {services: 'echo up > up.txt', migrate: 'exit 4'}\n")
    write(root, "docs/specs/x.md", "# X\n\n- **AC-1** [SMOKE] health answers\n")

    def go(fake=False, cfg=None, spec="docs/specs/x.md"):
        if cfg:
            write(root, ".keel/config.yml", cfg)
        a = ActionInput(root=str(root), project="p", phase="smoke", title="x", ac=None, acs=[], fake=fake, flow="feature",
                        step="stack_up", state={"spec": spec})
        return feature_actions.stack_up(a)
    r = go()
    assert not r.ok and r.note.startswith("stack up: commands.migrate failed (exit 4)") and (root / "up.txt").is_file()
    assert r.update["data"]["warnings"][0]["step"] == "stack_up"
    assert go(cfg="version: 4\ncommands: {services: 'true', migrate: 'true'}\n").note == "stack up: commands.services and commands.migrate passed"
    write(root, "docs/specs/x.md", "# X\n\n- **AC-1** [API] health answers\n")
    assert go().note == "no smoke check or e2e test to run: the stack is not brought up"
    assert go(cfg="version: 4\n").note.startswith("no stack configured")


def test_the_gate_mode_for_this_flow_is_chosen_at_the_spec_gate(client, repo, monkeypatch):
    by_step(monkeypatch, {})
    tid = start(client, repo)
    s = wait(client, tid)
    assert any(q["id"] == "gate_mode" for q in s["waiting"].get("questions") or [])
    assert '"gate_mode": "every-ac" | "end-of-lane" | "end"' in s["waiting"]["detail"]
    s, gates = gates_until_done(client, tid, s, {"spec_gate": {"choice": "approve", "gate_mode": "end"}})
    assert gates.count("ac_gate") == 1, gates
    assert "gate mode for this flow: end (was every-ac)" in s["gate_log"]


def test_the_gate_mode_holds_when_a_skip_without_reason_is_asked_again(client, repo, monkeypatch):
    """B10: the spec gate's gate mode was lost when the menu asked again about a skip without its reason (B9)."""
    by_step(monkeypatch, {})
    tid = start(client, repo)
    s = wait(client, tid)
    s = decide(client, tid, payload={"choice": "approve", "answers": {"skip": ["security"], "gate_mode": "end"}})
    assert s["waiting"]["step"] == "options" and "skipping security needs a reason" in s["waiting"]["title"]
    mode_q = next(q for q in s["waiting"]["questions"] if q["id"] == "gate_mode")
    assert [o["label"] for o in mode_q["options"] if o["recommended"]] == ["end"]
    s = decide(client, tid, payload={"answers": {"skip": ["security", *SKIPS]}, "reasons": {"security": "no new endpoint", **SKIPS}})
    s, gates = gates_until_done(client, tid, s)
    assert gates.count("ac_gate") == 1, gates
    assert "gate mode for this flow: end (was every-ac)" in s["gate_log"]


# ------------------------------------------------------------------ "ready to ship?"

def test_ready_to_ship_shows_the_state_and_can_show_the_diff_or_stop(client, repo, monkeypatch):
    by_step(monkeypatch, {})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "ready_gate")
    w = s["waiting"]
    assert w["choices"] == ["ship", "diff", "wrong", "stop"]
    assert [c["name"] for c in w["choice_info"] if c["default"]] == ["ship"]
    assert next(c for c in w["choice_info"] if c["name"] == "wrong")["why"] == "required"
    for want in ("## Ready to ship?", "## Checks", "## Still open", "Criteria: 2/2 done", "| Full-diff review | pass at `",
                 "Security: clean", "| E2E | skipped: no frontend, no HTTP server framework", "| Smoke | nothing to do", "- **ship**: ship it (recommended)"):
        assert want in w["detail"], want
    assert values(client, tid)["data"]["full_review"]["verdict"] == "pass"
    s = decide(client, tid, payload={"choice": "diff"})
    assert s["waiting"]["step"] == "ready_gate" and "Branch diff since the flow started" in s["waiting"]["detail"]
    assert "Criteria: 2/2 done" in s["waiting"]["detail"]
    s = decide(client, tid, payload={"choice": "stop"}, why="not this sprint")
    assert s["status"] == "stopped" and "Stopped before ship: not this sprint" in s.get("error", s.get("note", ""))
    started = [e["step"] for e in client.bus.of(tid, "step.started")]
    assert "ship_verify" not in started


def test_ready_to_ship_wrong_starts_an_amendment(client, repo, monkeypatch):
    by_step(monkeypatch, {})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "ready_gate")
    s = decide(client, tid, payload={"choice": "wrong"}, why="ranks must be per season")
    assert s["waiting"]["step"] == "amend_gate" and "ready-to-ship gate: ranks must be per season" in s["waiting"]["detail"]


# ------------------------------------------------------------------ e2e and smoke

def test_e2e_without_a_tool_asks_and_writes_the_specs_unrun(client, repo, monkeypatch):
    serves_http(repo)
    spec_with(monkeypatch, "- **AC-3** [E2E] Given two players, when the board opens, then both ranks show.")
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = to(client, tid, wait(client, tid), "e2e_tool", SHIP)
    w = s["waiting"]
    assert w["choices"] == ["write", "check"] and "commands.e2e" in w["detail"] and "AC-3 [E2E]" in w["detail"]
    s = decide(client, tid)
    e2e = [r for r in seen if r.agent == "e2e-author"]
    assert len(e2e) == 1 and "do not run them" in e2e[0].prompt and "AC-3 [E2E] Given two players" in e2e[0].prompt
    assert s["waiting"]["step"] == "e2e_gate" and log(repo)[0] == "e2e: Player ranks"
    skipped = values(client, tid)["data"]["ship_skipped"]
    assert any(x["step"] == "e2e-run" and "not run" in x["reason"] for x in skipped)
    s = to(client, tid, decide(client, tid), "ship_pr_gate")
    assert "e2e-run" in s["waiting"]["detail"], "the PR body says the e2e specs did not run"


def test_e2e_with_a_tool_does_not_ask_and_check_looks_again(client, repo, monkeypatch):
    serves_http(repo)
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


def test_e2e_and_smoke_criteria_are_criteria_but_not_built_in_the_loop(client, repo, monkeypatch):
    """[E2E] and [SMOKE] are criteria: on the spec gate and the final trace, never a RED/GREEN round of their own; the
    e2e and smoke commits say which criteria they cover, and passing them finishes those criteria."""
    configure(repo, "version: 4\ncommands:\n  e2e: \"true\"\n")
    spec_with(monkeypatch, "- **AC-3** [E2E] Given two players, when the board opens, then both ranks show.",
              "- **AC-4** [SMOKE] the health check answers UP")
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = wait(client, tid)
    w = s["waiting"]
    assert w["step"] == "spec_gate" and "- `AC-3` [E2E] Given two players" in w["detail"] and "- `AC-4` [SMOKE]" in w["detail"]
    assert [a["id"] for a in values(client, tid)["acs"]] == ["AC-1", "AC-2", "AC-3", "AC-4"]
    s = to(client, tid, s, "ship_final_review", SHIP)
    red = {r.ac["id"] for r in seen if r.phase == "red"}
    assert red == {"AC-1", "AC-2"}, "the loop builds only the [API] criteria"
    assert {r.ac["id"] for r in seen if r.agent == "criterion-reviewer"} == {"AC-1", "AC-2"}
    e2e = git(repo, "log", "--format=%s%n%b", "--grep=^e2e:", "-1").stdout
    assert "Covers: AC-3" in e2e and "AC-3" in (Path(repo) / "e2e/test_player_ranks_e2e.py").read_text()
    smoke = git(repo, "log", "--format=%s%n%b", "--grep=^test(smoke)", "-1").stdout
    assert "Covers: AC-4" in smoke
    assert {a["id"]: a["status"] for a in values(client, tid)["acs"]} == {"AC-1": "done", "AC-2": "done", "AC-3": "done", "AC-4": "done"}
    detail = s["waiting"]["detail"]
    assert "| AC-3 | E2E | done |" in detail and "test file e2e/test_player_ranks_e2e.py" in detail and "| AC-4 | SMOKE |" in detail


def test_parse_acs_reads_e2e_and_smoke_tags():
    from keel_engine.runtime import prompts
    acs = prompts.parse_acs("- **AC-1** [API] the list answers\n- **AC-2:** [E2E] the journey works end to end\n"
                            "- AC-3 - [smoke] the health check answers UP\n- AC-4 [DOCS] not a layer keel knows\n")
    assert [(a["id"], a["layer"]) for a in acs] == [("AC-1", "API"), ("AC-2", "E2E"), ("AC-3", "SMOKE")]
    from keel_engine import rules
    assert [rules.in_ac_loop(a) for a in acs] == [True, False, False]
    # an [E2E] criterion last in the build order never holds the "end" gate of the loop
    assert rules.gate_due("end", acs, "AC-1")["due"] is True


PLANNED = """# Ranks

## Acceptance criteria

- **AC-1** [API] the list answers with ranks
- **AC-10** [API] ties share a rank

## Plan

ORDER: AC-10, AC-1

| AC | files | test layer |
|---|---|---|
| AC-1 | src/scores/ranks.py | unit, tests/test_ranks.py |
| AC-10 | src/scores/ties.py | unit |

### AC-1
- reuse the scorer in src/scores/__init__.py:12

- AC-1 depends on: AC-10

## Out of scope

- paging (AC-1 shows every player)
"""


def test_red_and_green_prompts_carry_the_criterion_s_plan_row(tmp_path):
    from keel_engine.runtime import prompts
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/ranks.md").write_text(PLANNED)
    row = prompts.plan_row(PLANNED, "AC-1")
    assert "| AC-1 | src/scores/ranks.py |" in row and "reuse the scorer" in row and "AC-1 depends on: AC-10" in row
    assert "ties.py" not in row and "ORDER" not in row and "paging" not in row
    assert prompts.plan_row(PLANNED, "AC-7") == "" and prompts.plan_row("# no plan", "AC-1") == ""
    ac = {"id": "AC-1", "layer": "API", "title": "the list answers with ranks", "depends_on": ["AC-10"]}
    for agent, phase in (("test-author", "red"), ("implementer", "green")):
        p = prompts.task_prompt(agent=agent, phase=phase, step_name=phase, title="t", root=str(tmp_path), ac=ac,
                                acs=[ac], feedback=None, spec="docs/ranks.md")
        assert "The plan for AC-1" in p and "src/scores/ranks.py" in p and "Built after: AC-10" in p, agent
    p = prompts.task_prompt(agent="criterion-reviewer", phase="gate", step_name="AC review", title="t", root=str(tmp_path),
                            ac=ac, acs=[ac], feedback=None, spec="docs/ranks.md")
    assert "The plan for AC-1" not in p


def test_the_ac_reviewer_prompt_names_the_red_and_green_shas(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    to(client, tid, wait(client, tid), "ac_gate", SHIP)
    review = [r for r in seen if r.agent == "criterion-reviewer"][0]
    aid = review.ac["id"]
    red = git(repo, "log", "--format=%H", f"--grep=test({aid})", "-F", "-1").stdout.strip()
    green = git(repo, "log", "--format=%H", f"--grep=feat({aid})", "-F", "-1").stdout.strip()
    assert red and green
    assert f"RED commit of {aid}: {red}" in review.prompt and f"GREEN commit of {aid}: {green}" in review.prompt
    assert f"git show {red} and git show {green}" in review.prompt
    from keel_engine.runtime import prompts
    assert "none found" in prompts.review_commits("AC-9", None)


# ------------------------------------------------------------------ the options menu and waived gates

def test_the_options_menu_skips_security_with_a_reason(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = wait(client, tid)
    s = decide(client, tid, payload={"choice": "approve", "skip": {"security": "no new endpoint, no input", **SKIPS}})
    s = to(client, tid, s, "ship_final_review")
    assert not any(r.agent == "security-auditor" for r in seen)
    skipped = values(client, tid)["data"]["ship_skipped"]
    assert {"step": "security", "band": "optional", "reason": "no new endpoint, no input"} in skipped
    assert {"step": "release", "band": "deferred", "reason": "r"} in skipped                       # ship's own menu too
    assert "security" in s["waiting"]["detail"] and "no new endpoint" in s["waiting"]["detail"]     # the final review


def test_the_spec_gate_asks_the_skip_menus_as_a_question(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo)
    s = wait(client, tid)
    skip_q = next(q for q in s["waiting"]["questions"] if q["id"] == "skip")
    labels = [o["label"] for o in skip_q["options"]]
    assert skip_q["multiple"] is True and "security" in labels and "release" in labels
    assert not any(o["recommended"] for o in skip_q["options"])                      # nothing ticked: every step runs
    info = {c["name"]: c for c in s["waiting"]["choice_info"]}
    assert info["approve"]["default"] and info["reject"]["why"] == "required"
    s = decide(client, tid, payload={"choice": "approve", "answers": {"skip": ["security", *SKIPS]},
                                     "reasons": {"security": "no new endpoint, no input", **SKIPS}})
    s = to(client, tid, s, "ship_final_review")
    assert not any(r.agent == "security-auditor" for r in seen)
    skipped = values(client, tid)["data"]["ship_skipped"]
    assert {"step": "security", "band": "optional", "reason": "no new endpoint, no input"} in skipped


def test_a_wrong_skip_at_the_spec_gate_asks_at_the_menu(client, repo, monkeypatch):
    by_step(monkeypatch, {})
    tid = start(client, repo)
    wait(client, tid)
    s = decide(client, tid, payload={"choice": "approve", "skip": {"nonsense": "why not"}})
    assert s["waiting"]["step"] == "options" and "nonsense cannot be skipped" in s["waiting"]["title"]


def test_no_gates_waives_the_integration_gate_only(client, repo, monkeypatch):
    by_step(monkeypatch, {})
    tid = start(client, repo, data={"no_gates": True})
    s, gates = gates_until_done(client, tid, wait(client, tid))
    assert s["status"] == "done"
    assert "integration_gate" not in gates and gates[:2] == ["spec_gate", "contract_gate"]
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


# ------------------------------------------------------------------ 0.17.3 started from a spec (the Wiki)

def test_a_flow_started_from_a_spec_takes_its_path_and_criteria(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    write(repo, "docs/specs/scores.md", "# Scores\n\n- **AC-1** [API] scores are saved\n- **AC-2** [WEB] the board shows them\n")
    commit_all(repo, "docs: scores spec")
    tid = start(client, repo, title="Scores", data={"spec": "docs/specs/scores.md"})
    s = wait(client, tid)
    assert s["spec"] == "docs/specs/scores.md"
    assert [a["id"] for a in s["acs"]] == ["AC-1", "AC-2"]
    spec = [r for r in seen if r.step_name == "spec"][0]
    assert "keel-questions" not in spec.prompt and "starts from the spec docs/specs/scores.md" in spec.prompt
    assert s["waiting"]["step"] == "spec_gate" and "Spec: `docs/specs/scores.md`" in s["waiting"]["detail"]


def test_a_spec_outside_the_project_or_not_a_file_is_not_taken(tmp_path):
    from keel_engine.runtime import spec_check

    root = tmp_path / "p"
    (root / "docs/specs").mkdir(parents=True)
    (root / "docs/specs/a.md").write_text("# A\n")
    (tmp_path / "out.md").write_text("# out\n")
    assert spec_check.start_spec(str(root), "docs/specs/a.md") == "docs/specs/a.md"
    assert spec_check.start_spec(str(root), "./docs/specs/a.md") == "docs/specs/a.md"
    for bad in ("../out.md", "/etc/passwd.md", "docs/specs/none.md", "docs/specs", "README.txt", None, 3, ""):
        assert spec_check.start_spec(str(root), bad) is None, bad
    (root / "docs/specs/link.md").symlink_to(tmp_path / "out.md")
    assert spec_check.start_spec(str(root), "docs/specs/link.md") is None

