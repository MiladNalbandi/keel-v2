"""init in v0.4.0: architecture detection and the arch-surveyor, the per-rung fix question, the audit-now follow-up
and the fast option."""

import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import decide, start, wait
from keel_engine.runtime import arch, hunt, ladder
from test_flows_base import answers, values

SETTINGS = {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}


def config(repo) -> dict:
    return yaml.safe_load((Path(repo) / ".keel" / "config.yml").read_text())


def to(client, tid, s, step):
    """Approve every pause until the flow waits at `step`."""
    for _ in range(20):
        if s["status"] != "waiting" or s["waiting"]["step"] == step:
            return s
        s = decide(client, tid)
    raise AssertionError(f"never reached {step}: {s}")


def test_arch_detect_scores_the_layout(tmp_path):
    for f in ("src/controller/A.kt", "src/service/B.kt", "src/repository/C.kt"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("class X\n")
    r = arch.detect(str(tmp_path))
    assert r["style"] == "layered" and r["confidence"] == "medium" and "role-first" in r["evidence"][0]
    for f in ("app/domain/Order.kt", "app/adapter/Db.kt", "app/ports/Repo.kt"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("class Y\n")
    assert arch.detect(str(tmp_path))["style"] == "hexagonal"
    assert arch.detect(str(tmp_path / "nothing"))["style"] == "unknown"


def test_low_confidence_asks_the_arch_surveyor_and_its_answer_is_recorded(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"arch-surveyor": lambda r: "Domain types import nothing.\nARCH: hexagonal medium"})
    tid = start(client, repo, workflow="init", title="keel init", settings=SETTINGS)
    s = to(client, tid, wait(client, tid), "audit_now")
    surveyors = [r for r in seen if r.agent == "arch-surveyor"]
    assert len(surveyors) == 1 and surveyors[0].item["detection"]["confidence"] == "low"
    assert config(repo)["architecture"] == {"style": "hexagonal", "confidence": "medium", "source": "surveyed"}


@pytest.mark.parametrize("choice", ["exclude", "accept", "fix"])
def test_a_rung_that_keeps_failing_asks_fix_exclude_or_accept(client, repo, monkeypatch, choice):
    (Path(repo) / ".keel").mkdir(exist_ok=True)
    (Path(repo) / ".keel" / "config.yml").write_text("commands:\n  api_compile: 'false'\n  unit_tests: 'true'\n")
    seen = answers(monkeypatch, {"setup-doctor": lambda r: f"rung {r.item['n']}: the compiler is missing.\nDIAGNOSIS: needs-you"})
    tid = start(client, repo, workflow="init", title="keel init",
                settings={**SETTINGS, "simulate_checks": False, "fix_attempts_per_rung": 2})
    s = to(client, tid, wait(client, tid), "rung_gate")
    assert s["waiting"]["step"] == "rung_gate" and s["waiting"]["choices"] == ["fix", "exclude", "accept"]
    doctors = [r for r in seen if r.agent == "setup-doctor"]
    assert len(doctors) == 2 and all(r.item["n"] == 4 for r in doctors), "one doctor per failing rung, per round"
    assert "Rung 4 (Both apps compile) failed 2 time(s)" in s["waiting"]["detail"]
    if choice == "fix":
        (Path(repo) / ".keel" / "config.yml").write_text("commands:\n  api_compile: 'true'\n  unit_tests: 'true'\n")
    s = decide(client, tid, "approve", why="the build machine has no JDK", payload={"choice": choice})
    assert s["waiting"]["step"] == "audit_now", s
    rung4 = next(r for r in s["ladder"] if r["n"] == 4)
    if choice == "fix":
        assert rung4["status"] == "pass"
    else:
        key = "ladder_exclude" if choice == "exclude" else "not_checked"
        assert config(repo)["setup"][key] == [4]
        assert rung4["status"] == "skipped" and ("excluded" if choice == "exclude" else "not checked") in rung4["detail"]
    assert any(line.startswith(f"gate rung_gate {choice}") for line in s["gate_log"])


def test_audit_now_can_start_a_hunt(client, repo, monkeypatch):
    answers(monkeypatch, {})
    tid = start(client, repo, workflow="init", title="keel init", settings=SETTINGS)
    s = to(client, tid, wait(client, tid), "audit_now")
    assert s["waiting"]["choices"] == ["skip", "hunt"] and "liveness only" in s["waiting"]["detail"]
    s = decide(client, tid, "approve", payload={"choice": "hunt"})
    assert s["status"] == "done"
    child = s["children"][0]
    assert child["workflow"] == "hunt"
    c = wait(client, child["thread_id"])
    assert c["waiting"]["step"] == "confirm_lenses"
    run = hunt.get_run("demo")
    assert run["mode"] == "semi" and run["scope"]["mode"] == "all" and run["thread_id"] == child["thread_id"]


def test_a_fast_init_writes_no_knowledge_and_skips_the_survey(client, repo, monkeypatch):
    seen = answers(monkeypatch, {})
    tid = start(client, repo, workflow="init", title="keel init", settings={**SETTINGS, "fast": True})
    s = wait(client, tid)
    s = to(client, tid, s, "questions")
    assert "fast init" in s["waiting"]["detail"]
    s = to(client, tid, decide(client, tid), "audit_now")
    assert not [r for r in seen if r.agent in ("librarian", "arch-surveyor")]
    assert config(repo)["init"]["knowledge_sections"] == []
    assert config(repo)["architecture"]["source"] == "detected"


def test_a_fast_ladder_reuses_rungs_that_passed_with_the_same_command(repo):
    (Path(repo) / ".keel").mkdir(exist_ok=True)
    (Path(repo) / ".keel" / "config.yml").write_text("commands:\n  api_compile: 'true'\n  unit_tests: 'true'\n")
    calls = []

    def runner(root, cmd):
        calls.append(cmd)
        p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True)
        return p.returncode, p.stdout
    ok, _ = ladder.run(str(repo), False, runner)
    assert ok and calls
    calls.clear()
    ok, rungs = ladder.run(str(repo), False, runner, reuse=True)
    assert ok and calls == [] and "re-used" in next(r for r in rungs if r["n"] == 4)["detail"]
