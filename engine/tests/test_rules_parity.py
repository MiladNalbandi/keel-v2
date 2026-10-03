"""keel v2 rules must behave like keel v1. The fixture was exported from keel v1 itself."""

import json
from pathlib import Path

import pytest

from keel_engine import rules

FIX = json.loads((Path(__file__).parent / "fixtures" / "keel_v1_rules.json").read_text())

# One file per bucket under keel's default layout.
SAMPLES = {
    "api-main": "apps/api/src/main/kotlin/app/A.kt",
    "api-test": "apps/api/src/test/kotlin/app/ATest.kt",
    "web-src": "apps/web/src/App.tsx",
    "web-test": "apps/web/src/App.test.tsx",
    "migration": "apps/api/src/main/resources/db/migration/V9__x.sql",
    "contract": "contracts/openapi.yaml",
    "e2e": "e2e/a.spec.ts",
    "smoke": "smoke/a.spec.ts",
    "specs": "specs/feature.md",
    "other": "README.md",
    "protected-env": ".env",
    "generated": "apps/web/src/api/generated/client.ts",
}


def test_tables_match_v1():
    assert rules.PHASES == FIX["PHASES"]
    assert rules.TRANSITIONS == FIX["TRANSITIONS"]
    assert rules.LADDER == FIX["LADDER"]
    assert rules.RAILS == FIX["RAILS"]
    assert rules.MATRIX == FIX["MATRIX"]
    assert rules.COMMIT_RULES == FIX["COMMIT_RULES"]
    assert rules.FLOW_START == FIX["FLOW_START"]
    assert rules.RED_ACCEPT == FIX["red_accept"]
    assert rules.RED_REJECT == FIX["red_reject"]


def test_every_rail_phase_is_known_and_flows_start_on_their_rail():
    for flow, rail in FIX["RAILS"].items():
        assert set(rail) <= set(rules.PHASES), flow
        assert FIX["FLOW_START"][flow] in rail


@pytest.mark.parametrize("frm", FIX["PHASES"])
def test_can_transition(frm):
    for to in FIX["PHASES"]:
        expected = frm == to or to in FIX["TRANSITIONS"].get(frm, [])
        assert rules.can_transition(frm, to) is expected, (frm, to)


def test_unknown_phase_cannot_transition():
    assert not rules.can_transition("nope", "spec")
    assert rules.can_transition("nope", "nope")


@pytest.mark.parametrize("path,bucket", sorted(FIX["classify"].items()))
def test_classify_cases(path, bucket):
    cfg = rules.make_config(FIX["classify_cfg"])
    assert rules.classify(cfg, path) == bucket


def test_samples_classify_to_their_bucket():
    for bucket, path in SAMPLES.items():
        assert rules.classify(None, path) == bucket, path


@pytest.mark.parametrize("phase", FIX["PHASES"])
def test_check_edit_follows_matrix(phase):
    row = FIX["MATRIX"].get(phase, {"other": "allow", "*": "deny"})
    for bucket, path in SAMPLES.items():
        if bucket in ("protected-env", "generated"):
            assert not rules.check_edit(phase, path).ok
            continue
        rule = row.get(bucket) or row.get("*") or "deny"
        new = rules.check_edit(phase, path, exists=False)
        old = rules.check_edit(phase, path, exists=True)
        if bucket == "migration":
            assert not old.ok  # existing migrations are immutable in every phase
        if phase == "none":
            assert new.ok
            continue
        if rule == "allow":
            assert new.ok, (phase, bucket)
            if bucket != "migration":
                assert old.ok
        elif rule == "deny":
            assert not new.ok and not old.ok, (phase, bucket)
        elif rule == "new-only":
            assert new.ok and not old.ok, (phase, bucket)
        elif rule == "delete-only":
            assert not new.ok and old.ok, (phase, bucket)


def test_lane_scoping_in_red_and_green():
    assert rules.check_edit("red", SAMPLES["api-test"], lane="api").ok
    assert not rules.check_edit("red", SAMPLES["web-test"], lane="api").ok
    assert rules.check_edit("green", SAMPLES["web-src"], lane="web").ok
    assert not rules.check_edit("green", SAMPLES["api-main"], lane="web").ok


def test_unknown_phase_fails_closed():
    assert not rules.check_edit("made-up", SAMPLES["api-main"]).ok
    assert rules.check_edit("made-up", SAMPLES["other"]).ok


@pytest.mark.parametrize("ctype", list(FIX["COMMIT_RULES"]))
def test_check_commit_denies_buckets(ctype):
    rule = FIX["COMMIT_RULES"][ctype]
    for bucket in rule["deny"]:
        v = rules.check_commit(ctype, [SAMPLES[bucket]])
        assert not v.ok and v.bad == [(SAMPLES[bucket], bucket)], (ctype, bucket)
    for bucket in rule["allow"]:
        path = SAMPLES[bucket]
        if rule.get("setupOnly"):
            path = ".keel/config.yml"
        if rule.get("memoryOnly"):
            path = "docs/knowledge/architecture.md"
        assert rules.check_commit(ctype, [path]).ok, (ctype, bucket)


def test_commit_special_rules():
    assert not rules.check_commit("setup", ["src/app.py"]).ok
    assert rules.check_commit("setup", [".keel/config.yml", "docs/RUNNING.md", "docs/knowledge/a.md", "CLAUDE.md", ".gitignore"]).ok
    assert not rules.check_commit("memory", ["README.md"]).ok
    assert not rules.check_commit("trivial", ["contracts/openapi.yaml"]).ok
    assert not rules.check_commit("red", []).ok
    assert not rules.check_commit("nope", ["a"]).ok


def test_red_classification():
    for phrase in FIX["red_accept"]:
        assert rules.red_accept(f"FAILED ... {phrase.upper()} 3")
        assert rules.classify_failure(phrase)["kind"] == "assertion"
    for phrase in FIX["red_reject"]:
        assert rules.red_reject(f"error: {phrase}")
        assert rules.classify_failure(phrase)["kind"] == "setup"
    # an assertion signal wins over scary words
    assert rules.classify_failure("Compilation error ... AssertionError: expected 3")["kind"] == "assertion"
    assert rules.classify_failure("something odd")["kind"] == "assertion"


def test_gate_due():
    acs = [{"id": "AC-1", "layer": "API"}, {"id": "AC-2", "layer": "WEB"}, {"id": "AC-3", "layer": "API"}]
    assert all(rules.gate_due("every-ac", acs, a["id"])["due"] for a in acs)
    assert [rules.gate_due("end", acs, a["id"])["due"] for a in acs] == [False, False, True]
    assert [rules.gate_due("end-of-lane", acs, a["id"])["due"] for a in acs] == [False, True, True]
    tagged = [dict(acs[0], gate="skip")] + acs[1:]
    assert not rules.gate_due("every-ac", tagged, "AC-1")["due"]
    assert not rules.gate_due("every-ac", acs, "AC-1", skipped={"api": "flow"})["due"]
    assert rules.gate_due("every-ac", acs, "AC-9")["due"]


def test_bash_guard():
    assert not rules.check_bash("green", "git push --force").ok
    assert not rules.check_bash("none", "cat .env").ok
    assert rules.check_bash("none", "cat .env.example").ok
    assert not rules.check_bash("green", "git commit -m x").ok
    assert not rules.check_bash("green", "npm install left-pad").ok
    assert rules.check_bash("green", "npm install").ok
    assert not rules.check_bash("green", "echo x > apps/api/src/test/kotlin/ATest.kt").ok
    assert rules.check_bash("red", "echo x > apps/api/src/test/kotlin/ATest.kt").ok


def test_empty_state_shape():
    assert rules.EMPTY_STATE == FIX["EMPTY"]
