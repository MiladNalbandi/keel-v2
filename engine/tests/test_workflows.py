import yaml

from keel_engine.workflows.estimate import estimate
from keel_engine.workflows.model import dump_yaml, load_yaml
from keel_engine.workflows.templates import get_template, templates
from keel_engine.workflows.validate import validate


def test_templates_are_valid_and_have_phases():
    ids = [t.id for t in templates()]
    assert ids == ["feature", "change", "quick", "address", "small", "implement", "spike", "clarify", "fix", "diagnose", "review", "review-pr", "init", "knowledge-refresh", "cover", "ship", "hunt", "hunt-next", "lint",
                   "plugin", "harness"]
    for t in templates():
        assert validate(t) == [], t.id
        assert all(s.phase for s in t.steps), t.id


def test_plugin_template_shape():
    """0.18.6 the plugin workflow: spec → keel-plugin new → criteria red/green → keel-plugin lint → the plugin's tests →
    review gate (scripts/plugin-check.sh runs both checks, each with 2 fix rounds)."""
    t = get_template("plugin")
    main = [s.id for s in t.steps if not s.per_ac]
    assert main[:6] == ["preflight", "spec", "spec_sync", "spec_gate", "freeze", "scaffold"]
    assert [s.id for s in t.steps if s.per_ac] == ["red", "verify_red", "green", "verify_green", "ac_review", "ac_gate"]
    assert t.step("lint").action == "run: scripts/plugin-check.sh lint" and t.step("tests").action == "run: scripts/plugin-check.sh test"
    assert "keel-plugin new" in t.step("scaffold").instructions
    assert t.step("lint_clean").rounds == 2 and t.step("tests_clean").rounds == 2
    assert main[-3:] == ["code_review", "review_gate", "close"]


def test_feature_template_shape():
    t = get_template("feature")
    loop = [s.id for s in t.steps if s.per_ac]
    assert loop == ["red", "red_amend", "red_to_amend", "red_blocked", "red_stuck", "verify_red", "green", "green_amend",
                    "green_to_amend", "green_blocked", "green_stuck", "verify_green", "refactor", "refactor_commit", "ac_suite", "ac_review", "ac_gate"]
    assert t.step("ac_gate").back == "red" and t.step("spec_gate").back == "spec"
    assert list(t.step("spec_gate").choices) == ["approve", "edit", "rewrite", "review", "order", "reject"]
    main = [s.id for s in t.steps[:t.steps.index(t.step("close")) + 1] if not s.per_ac and not s.id.startswith("ship_")]
    assert main == ["preflight", "web_check", "web_docs", "interview", "interview_gate", "spec", "explore_areas", "explore", "maps",
                    "plan", "spec_sync", "spec_gate", "freeze", "options", "contract", "contract_check", "contract_diff", "contract_gate",
                    "contract_commit", "integration", "integration_commit",
                    "integration_diff", "integration_gate", "security_deps", "security_scope", "security", "review_fix",
                    "review_fix_commit", "code_review", "full_review", "stack_up", "smoke_scope", "smoke_needed", "smoke", "smoke_commit",
                    "smoke_gate", "e2e_scope", "e2e_needed", "e2e_tool", "e2e", "e2e_commit", "e2e_gate", "ready_show", "ready_gate", "adr", "adr_commit", "close"]
    # ship's steps are included (its review, final review, memory and PR); feature has no copies of its own
    assert t.step("ship_final_review").lock and t.step("ship_open_pr") and not t.step("final_review")
    assert {s.id for s in t.steps if s.lock} >= {"spec_gate", "freeze", "contract_gate", "verify_red", "verify_green", "ac_gate",
                                                 "amend_gate", "ship_final_review"}


def test_yaml_round_trip():
    t = get_template("fix")
    again = load_yaml(dump_yaml(t))
    assert again.model_dump(exclude={"yaml"}) == t.model_dump(exclude={"yaml"})


def wf(steps, **extra):
    return load_yaml(yaml.safe_dump({"name": "Mine", "keel_rules": False, "steps": steps, **extra}))


def test_validator_catches_mistakes():
    errors = validate(wf([
        {"id": "a", "kind": "agent", "name": "a"},
        {"id": "a", "kind": "code", "name": "b", "action": "fly"},
        {"id": "g", "kind": "gate", "name": "g", "back": "later"},
        {"id": "later", "kind": "branch", "name": "br"},
        {"id": "p1", "kind": "agent", "name": "p1", "agent": "x", "per_ac": True, "phase": "nope"},
        {"id": "x", "kind": "agent", "name": "x", "agent": "x"},
        {"id": "p2", "kind": "agent", "name": "p2", "agent": "x", "per_ac": True},
    ]))
    text = "\n".join(errors)
    for needle in ["used more than once", "needs an agent", "unknown action 'fly'", "back must point to an earlier step",
                   "needs a 'no' target", "not a keel phase", "must sit together"]:
        assert needle in text, needle


def test_locked_steps_stay_while_keel_rules_on():
    t = get_template("feature")
    data = yaml.safe_load(dump_yaml(t))
    data["steps"] = [s for s in data["steps"] if s["id"] != "verify_red"]
    for s in data["steps"]:
        if s.get("no") == "verify_red":
            s["no"] = "green"        # the amendment branch skipped to verify_red
    data["based_on"] = "keel/feature"
    data["id"] = "mine"
    errors = validate(load_yaml(yaml.safe_dump(data)))
    assert any("verify_red" in e and "keel rule" in e for e in errors)
    data["keel_rules"] = False
    assert validate(load_yaml(yaml.safe_dump(data))) == []


def test_estimate_defaults_and_history():
    t = get_template("feature")
    e1 = estimate(t, 1)
    e3 = estimate(t, 3)
    assert e3["tokens"] > e1["tokens"]
    assert e3["low"] == round(e3["tokens"] * 0.7) and e3["high"] == round(e3["tokens"] * 1.6)
    per = {p["step"]: p["tokens"] for p in e3["per_step"]}
    assert per["spec_gate"] == 0 and per["verify_red"] == 0
    assert per["red"] == round((24_000 + 3_000) * 3 * 1.2)
    assert per["explore"] == round(3 * 44_000 * 1.1) and per["ship_review"] == 3 * 33_000

    hist = [{"agent": "test-author", "tokens_in": 1000, "tokens_out": 100, "retries": 0}] * 3
    e = estimate(t, 3, hist)
    assert {p["step"]: p["tokens"] for p in e["per_step"]}["red"] == 3 * 1100


def test_estimate_cost_by_provider_and_premium():
    t = get_template("fix")
    models = {"default": {"provider": "claude", "mode": "api", "model": "sonnet"},
              "implementer": {"provider": "copilot", "mode": "subscription", "model": "gpt-5"}}
    e = estimate(t, 0, None, models)
    assert e["by_provider"]["copilot"] > 0 and e["by_provider"]["claude"] > 0 and e["by_provider"]["fake"] == 0
    assert e["premium_requests"] >= 1
    assert e["cost_usd"] > 0


def test_api_validate_and_estimate(client):
    tpls = client.get("/templates").json()
    # (a plugin's own workflow comes after these, with "plugin": the CI/CD plugin's ci-fix, plugins/ci/engine/tests)
    assert [t["id"] for t in tpls] == ["feature", "change", "quick", "address", "small", "implement", "spike", "clarify", "fix", "diagnose", "review", "review-pr", "init", "knowledge-refresh", "cover", "ship", "hunt", "hunt-next", "lint",
                   "plugin", "harness"]
    assert "plugin" not in tpls[0]
    good = tpls[0]["yaml"]
    r = client.post("/workflows/validate", json={"yaml": good}).json()
    assert r["ok"] and r["errors"] == [] and r["workflow"]["id"] == "feature"
    r = client.post("/workflows/validate", json={"yaml": "name: x\nsteps: [ {id: a, kind: gate, name: g, back: zz} ]"}).json()
    assert not r["ok"] and r["errors"]
    r = client.post("/workflows/validate", json={"yaml": ": : :"}).json()
    assert not r["ok"]
    e = client.post("/workflows/estimate", json={"yaml": good, "acs": 3}).json()
    assert e["tokens"] > 0 and set(e["by_provider"]) == {"fake", "claude", "codex", "copilot"}


def test_spike_researches_the_web_before_the_angles():
    """0.18.20 spike: frame → web_docs (web-researcher, CONFIDENCE) → one researcher per angle → findings with sources."""
    t = get_template("spike")
    assert [s.id for s in t.steps] == ["frame", "frame_questions", "web_docs", "research", "collect", "findings", "report", "decide",
                                       "hand_spec"]
    frame, web, research, findings = t.step("frame"), t.step("web_docs"), t.step("research"), t.step("findings")
    assert frame.keep and frame.collect == "angles" and "outside this repository" in frame.instructions
    assert (web.kind, web.agent, web.phase, web.markers, web.keep) == ("agent", "web-researcher", "spec", ["CONFIDENCE"], True)
    assert "{{data.frame_text}}" in web.instructions and "one failed URL" in web.instructions
    assert "{{data.web_docs_text}}" in research.instructions and "curl -sL" in research.instructions
    assert "{{data.web_docs_text}}" in findings.instructions and "{{data.report}}" in findings.instructions
    assert "URL" in findings.instructions and "confirmed" in findings.instructions


def test_feature_researches_the_web_only_when_it_needs_outside_docs():
    """0.18.21 feature: web_check (explorer, WEB: yes | no) → web_docs (web-researcher) only on yes → the interview → the
    spec, which cites what is confirmed and turns what is not into open questions."""
    t = get_template("feature")
    ids = [s.id for s in t.steps]
    assert ids[:6] == ["preflight", "web_check", "web_docs", "interview", "interview_gate", "spec"]
    assert "{{data.web_docs_text}}" in t.step("interview").instructions
    check, web, spec = t.step("web_check"), t.step("web_docs"), t.step("spec")
    assert (check.kind, check.agent, check.phase, check.markers, check.keep) == ("agent", "explorer", "spec", ["WEB"], True)
    for must in ("third-party API", "webhook", "library", "WEB: yes", "WEB: no", "refactor"):
        assert must in check.instructions, must
    assert (web.kind, web.agent, web.phase, web.markers, web.keep) == ("agent", "web-researcher", "spec", ["CONFIDENCE"], True)
    assert web.when == {"marker": "WEB", "equals": "yes", "step": "web_check"}
    assert "{{data.web_check_text}}" in web.instructions and "one failed URL" in web.instructions
    assert "{{data.web_docs_text}}" in spec.instructions and "open question" in spec.instructions
    assert "URL" in spec.instructions and "invent" in spec.instructions


def test_a_change_that_rests_on_outside_docs_is_a_feature():
    """0.18.21 change: no web step of its own (triage writes the criteria in the same answer); a change whose criteria
    rest on outside docs the repository does not hold is triaged as a feature, whose flow researches them."""
    t = get_template("change")
    assert not any(s.agent == "web-researcher" for s in t.steps)
    assert "third-party API" in t.step("triage").instructions and "researches them on the web" in t.step("triage").instructions
