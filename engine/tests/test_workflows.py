import yaml

from keel_engine.workflows.estimate import estimate
from keel_engine.workflows.model import dump_yaml, load_yaml
from keel_engine.workflows.templates import get_template, templates
from keel_engine.workflows.validate import validate


def test_templates_are_valid_and_have_phases():
    ids = [t.id for t in templates()]
    assert ids == ["feature", "change", "fix", "diagnose", "review", "init", "knowledge-refresh", "cover", "ship", "hunt", "hunt-next", "lint"]
    for t in templates():
        assert validate(t) == [], t.id
        assert all(s.phase for s in t.steps), t.id


def test_feature_template_shape():
    t = get_template("feature")
    loop = [s.id for s in t.steps if s.per_ac]
    assert loop == ["red", "red_amend", "red_to_amend", "verify_red", "green", "green_amend", "green_to_amend", "verify_green",
                    "ac_review", "ac_gate"]
    assert t.step("ac_gate").back == "red" and t.step("spec_gate").back == "spec"
    assert list(t.step("spec_gate").choices) == ["approve", "edit", "rewrite", "review", "order", "reject"]
    main = [s.id for s in t.steps[:t.steps.index(t.step("close")) + 1] if not s.per_ac and not s.id.startswith("ship_")]
    assert main == ["preflight", "spec", "explore_areas", "explore", "maps", "plan", "spec_sync", "spec_gate", "freeze", "options",
                    "contract", "contract_diff", "contract_gate", "contract_commit", "integration", "integration_commit",
                    "integration_diff", "integration_gate", "security_deps", "security_scope", "security", "review_fix",
                    "review_fix_commit", "code_review", "e2e_scope", "e2e_needed", "e2e_tool", "e2e", "e2e_commit", "e2e_gate",
                    "smoke_scope", "smoke_needed", "smoke", "smoke_commit", "smoke_gate", "adr", "adr_commit", "close"]
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
    assert [t["id"] for t in tpls] == ["feature", "change", "fix", "diagnose", "review", "init", "knowledge-refresh", "cover", "ship", "hunt", "hunt-next", "lint"]
    good = tpls[0]["yaml"]
    r = client.post("/workflows/validate", json={"yaml": good}).json()
    assert r["ok"] and r["errors"] == [] and r["workflow"]["id"] == "feature"
    r = client.post("/workflows/validate", json={"yaml": "name: x\nsteps: [ {id: a, kind: gate, name: g, back: zz} ]"}).json()
    assert not r["ok"] and r["errors"]
    r = client.post("/workflows/validate", json={"yaml": ": : :"}).json()
    assert not r["ok"]
    e = client.post("/workflows/estimate", json={"yaml": good, "acs": 3}).json()
    assert e["tokens"] > 0 and set(e["by_provider"]) == {"fake", "claude", "codex", "copilot"}
