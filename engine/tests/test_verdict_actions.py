"""Verdict actions on small fixture repos (runtime/verdict_actions.py), the release/coverage extensions, and the PR body."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from keel_engine.runtime import verdict_actions as va
from keel_engine.runtime import verdicts
from keel_engine.runtime.actions import ActionInput, run_action

JACOCO = """<?xml version="1.0"?><report name="x">
<package name="app/score"><sourcefile name="Rank.kt">
<line nr="10" mi="2" ci="0" mb="0" cb="0"/><line nr="12" mi="1" ci="0" mb="1" cb="1"/><line nr="25" mi="3" ci="0"/>
<line nr="30" mi="0" ci="4"/>
</sourcefile></package>
<package name="app/security"><sourcefile name="Guard.kt"><line nr="3" mi="1" ci="0"/><line nr="4" mi="0" ci="2"/></sourcefile></package>
</report>"""

LCOV = """SF:src/web/list.ts
DA:1,1
DA:2,0
DA:3,0
BRDA:2,0,0,1
BRDA:2,0,1,-
end_of_record
"""


def sh(root, *args):
    return subprocess.run(list(args), cwd=root, capture_output=True, text=True)


def git(root, *args):
    return sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", *args)


def write(root, rel, text):
    f = Path(root) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def commit(root, msg, *files):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "README.md", "x\n")
    write(root, ".keel/config.yml", "version: 4\n")
    commit(root, "chore: start")
    git(root, "checkout", "-q", "-b", "feat/x")
    return root


def config(root, text):
    write(root, ".keel/config.yml", "version: 4\n" + text)
    commit(root, "chore: config")


def ai(root, **kw):
    kw.setdefault("acs", [])
    return ActionInput(root=str(root), project="p1", phase="ship", title="Rank players", ac=None, fake=False, flow="ship", **kw)


def run(action, a):
    return asyncio.run(run_action(action, a))


def lines(n):
    return "".join(f"line {i}\n" for i in range(1, n + 1))


# ------------------------------------------------------------------ coverage

def test_parsers_and_grouping():
    j = va.parse_jacoco(JACOCO)
    assert j["app/score/Rank.kt"][12] == {"covered": False, "branches": 2, "branches_covered": 1}
    assert j["app/score/Rank.kt"][30]["covered"] is True
    lc = va.parse_lcov(LCOV)
    assert lc["src/web/list.ts"][2] == {"covered": False, "branches": 2, "branches_covered": 1}
    groups = va.group(["a.kt:10", "a.kt:12", "a.kt:20", "a.kt:29", "b.kt:3"])
    assert [(g["file"], g["lines"]) for g in groups] == [("a.kt", [10, 12, 20]), ("a.kt", [29]), ("b.kt", [3])]
    assert groups[0]["key"] == "a.kt:10-20"


def test_verify_coverage_reads_changed_lines_from_jacoco_and_lcov(proj):
    config(proj, "coverage:\n  reports: {api: build/jacoco.xml, web: build/lcov.info}\n  changed_lines: 80\n"
                 "security: {coverage_paths: ['**/security/**']}\n")
    write(proj, "src/main/kotlin/app/score/Rank.kt", lines(40))
    write(proj, "src/main/kotlin/app/security/Guard.kt", lines(5))
    write(proj, "src/web/list.ts", lines(3))
    commit(proj, "feat(AC-1): rank")
    write(proj, "build/jacoco.xml", JACOCO)
    write(proj, "build/lcov.info", LCOV)
    r = run("verify_coverage", ai(proj))
    assert not r.ok
    assert "changed lines" in r.note and "security path" in r.note
    groups = r.update["data"]["coverage_groups"]
    keys = [g["key"] for g in groups]
    assert keys[0] == "src/main/kotlin/app/security/Guard.kt:3-3" and groups[0]["critical"], "security paths first"
    assert "src/main/kotlin/app/score/Rank.kt:10-12" in keys and "src/main/kotlin/app/score/Rank.kt:25-25" in keys
    assert "src/web/list.ts:2-3" in keys
    v = verdicts.latest("p1", "coverage")
    assert v["ok"] is False and v["commit"] == git(proj, "rev-parse", "HEAD").stdout.strip()
    apps = {a["app"]: a for a in v["detail"]["apps"]}
    assert apps["api"]["changed_executable"] == 6 and apps["api"]["changed_covered"] == 2
    assert apps["web"]["changed_pct"] == 33.3


def test_verify_coverage_passes_with_nothing_changed_and_has_no_gate_without_config(proj):
    r = run("verify_coverage", ai(proj))
    assert r.ok and "not checked" in r.note
    config(proj, "coverage: {reports: {api: build/jacoco.xml}}\n")
    git(proj, "checkout", "-q", "main")
    git(proj, "merge", "-q", "feat/x")
    r = run("verify_coverage", ai(proj))
    assert r.ok and verdicts.latest("p1", "coverage")["ok"] is True


def test_backend_dir_dot_is_the_root_and_coverage_app_command_makes_the_report(proj):
    # Real run (lab, shop): backend.dir "." matched no changed file, and commands.coverage_api never ran.
    write(proj, "fixtures/lcov.info", "SF:src/cart.js\nDA:1,1\nDA:2,0\nDA:3,0\nend_of_record\n")
    config(proj, "backend: {dir: .}\ncoverage: {reports: {api: coverage/lcov.info}, changed_lines: 90}\n"
                 "commands: {coverage_api: 'mkdir -p coverage && cp fixtures/lcov.info coverage/'}\n")
    write(proj, "src/cart.js", lines(3))
    commit(proj, "feat: cart")
    r = run("verify_coverage", ai(proj))
    assert not r.ok and "changed lines" in r.note
    assert [g["key"] for g in r.update["data"]["coverage_groups"]] == ["src/cart.js:2-3"]
    assert va._dir_of({"backend": {"dir": "./"}}, "backend") is None and va._dir_of({"backend": {"dir": "./api"}}, "backend") == "api"


# ------------------------------------------------------------------ flaky

def flaky_cmd(tmp_path):
    """A suite that fails the first time it runs and passes after (outside the repo, so the tree stays clean)."""
    flag, script = tmp_path / "ran-once", tmp_path / "flaky.sh"
    script.write_text(f"if [ -f {flag} ]; then echo '1 passed'; exit 0; fi\ntouch {flag}\n"
                      "echo 'FAILED tests/test_rank.py::test_tie'\nexit 1\n")
    return f"sh {script}"


def test_verify_release_reruns_a_failing_suite_once_and_records_flaky(proj, tmp_path):
    config(proj, f"commands: {{api_test_module: \"{flaky_cmd(tmp_path)}\"}}\n")
    r = run("verify_release", ai(proj))
    assert r.ok and "flaky" in r.note and "tests/test_rank.py::test_tie" in r.note
    assert r.update["flaky"][0]["tests"] == ["tests/test_rank.py::test_tie"]
    v = verdicts.latest("p1", "release")
    assert v["ok"] and v["detail"]["flaky"][0]["label"] == "release suite"


def test_verify_release_fails_when_the_rerun_fails_too(proj):
    config(proj, "commands: {api_test_module: 'false'}\n")
    r = run("verify_release", ai(proj))
    assert not r.ok and verdicts.latest("p1", "release")["ok"] is False


def test_verify_module_and_fast(proj, tmp_path):
    r = run("verify_module", ai(proj))
    assert r.ok and "not available" in r.note
    assert verdicts.latest("p1", "module")["detail"]["available"] is False
    r = run("verify_fast", ai(proj))
    assert r.ok and "nothing compiles" in r.note
    write(proj, "src/a.py", "x = 0\n")
    r = run("verify_fast", ai(proj))
    assert r.ok and "not available" not in r.note and "no command configured" in r.note
    assert verdicts.latest("p1", "fast")["detail"]["available"] is False
    config(proj, f"commands: {{api_compile: 'true', api_test_module: \"{flaky_cmd(tmp_path)}\"}}\nbackend: {{dir: ''}}\n")
    r = run("verify_module", ai(proj))
    assert r.ok and r.update["flaky"]
    write(proj, "src/a.py", "x = 1\n")
    r = run("verify_fast", ai(proj))
    assert r.ok and "api compile passed" in r.note and verdicts.latest("p1", "fast")["ok"]
    config(proj, "commands: {api_compile: 'echo broken; false'}\nbackend: {dir: ''}\n")
    r = run("verify_fast", ai(proj))
    assert not r.ok and "broken" in r.detail


# ------------------------------------------------------------------ trace, audit

def test_trace_from_commit_subjects(proj):
    for msg, f in (("test(AC-1): rank", "tests/test_a.py"), ("feat(AC-1): rank", "src/a.py"), ("test(AC-2): ties", "tests/test_b.py")):
        write(proj, f, "x\n")
        commit(proj, msg)
    acs = [{"id": f"AC-{n}", "layer": "API", "title": t, "status": "done"} for n, t in ((1, "rank"), (2, "ties"), (3, "empty"))]
    r = run("trace", ai(proj, acs=acs))
    assert r.ok and "no test commit: AC-3" in r.note and "no implementation commit: AC-2" in r.note
    assert "| AC-1 | API | done |" in r.detail
    rows = {x["id"]: x for x in r.update["data"]["trace"]}
    assert rows["AC-1"]["complete"] and not rows["AC-2"]["complete"]
    r = run("trace_strict", ai(proj, acs=acs))
    assert not r.ok and verdicts.latest("p1", "trace")["detail"]["strict"] is True
    assert run("trace", ai(proj)).ok and "no acceptance criteria" in run("trace", ai(proj)).note


def test_audit_finds_mixed_commits_skips_and_unlocks_without_reason(proj):
    write(proj, "tests/test_a.py", "def test_a():\n    pass\n")
    write(proj, "src/main/kotlin/A.kt", "class A\n")
    commit(proj, "test(AC-1): mixed")
    write(proj, "tests/test_b.py", "@pytest.mark.skip\ndef test_b():\n    pass\n")
    commit(proj, "test(AC-2): skipped")
    r = run("audit", ai(proj, unlocks=[{"path": "src/x.py", "phase": "red", "by": "user"}]))
    assert not r.ok
    assert "a test commit contains production code" in r.detail and "@pytest.mark.skip" in r.detail
    assert "unlock of src/x.py in red has no reason" in r.detail
    assert verdicts.latest("p1", "audit")["ok"] is False


def test_audit_allows_tests_in_keels_own_repair_commits(proj):
    # Real run (lab ship): the review-fix commit "fix(review): ..." changed src and test and audit refused it forever.
    write(proj, "src/main/kotlin/A.kt", "a\n")
    write(proj, "src/test/kotlin/ATest.kt", "t\n")
    commit(proj, "fix(review): address ship review findings")
    r = run("audit", ai(proj))
    assert r.ok, r.note


def test_audit_clean_branch(proj):
    write(proj, "tests/test_a.py", "def test_a():\n    pass\n")
    commit(proj, "test(AC-1): ok")
    assert run("audit", ai(proj)).ok


# ------------------------------------------------------------------ deps, arch

def test_deps_paths(proj):
    r = run("verify_deps", ai(proj))
    assert r.ok and "no manifest" in r.note
    write(proj, "package.json", '{"dependencies": {"left-pad": "1.0.0"}}\n')
    commit(proj, "build: add left-pad")
    r = run("verify_deps", ai(proj))
    assert r.ok and "not available" in r.note
    v = verdicts.latest("p1", "deps")
    assert v["detail"]["available"] is False and v["detail"]["manifests"] == ["package.json"]
    config(proj, "commands: {deps_web: 'echo 1 high; false'}\nfrontend: {dir: ''}\n")
    r = run("verify_deps", ai(proj))
    assert not r.ok and "web audit fails" in r.note


def test_arch_not_available_then_violations(proj):
    r = run("arch", ai(proj))
    assert r.ok and "not available" in r.note
    config(proj, "boundaries:\n  rules:\n    - {name: domain-framework-free, from: '**/domain/**', deny_imports: ['org.springframework.**']}\n")
    write(proj, "src/main/kotlin/app/domain/Rank.kt", "package app.domain\nimport org.springframework.stereotype.Service\nclass Rank\n")
    write(proj, "src/main/kotlin/app/web/Api.kt", "import org.springframework.web.bind.annotation.GetMapping\n")
    r = run("arch", ai(proj))
    assert not r.ok and "Rank.kt:2 imports org.springframework.stereotype.Service (domain-framework-free)" in r.detail
    assert "Api.kt" not in r.detail


def test_unknown_tool_never_crashes(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    for action in ("verify_fast", "verify_module", "verify_deps", "audit", "trace", "arch"):
        r = run(action, ai(plain))
        assert r.ok, (action, r.note)


# ------------------------------------------------------------------ PR

def pr_state(proj):
    write(proj, "docs/specs/rank.md", "# Rank players\n\n- **AC-1** [API] rank by score\n")
    write(proj, "tests/test_a.py", "x\n")
    commit(proj, "test(AC-1): rank")
    return {"spec": "docs/specs/rank.md", "acs": [{"id": "AC-1", "layer": "API", "title": "rank by score", "status": "done"}],
            "gates": {"log": ["ac AC-1 approve: no gate here (end mode)", "gate final approve"], "skipped": {"web": "no web lane"}},
            "unlocks": [{"path": "src/legacy.py", "phase": "green", "by": "user", "reason": "generated file"}],
            "flaky": [{"label": "release suite", "tests": ["tests/test_rank.py::test_tie"]}],
            "data": {"coverage_accepted": [{"key": "src/a.py:3-4", "reason": "defensive branch"}]}}


def test_pr_body_content(proj):
    st = pr_state(proj)
    va.record_verdict("p1", "coverage", True, {"summary": "api changed 92.0%, global 81.0%"}, root=str(proj))
    r = run("pr", ai(proj, state=st, acs=st["acs"]))
    body = r.update["pr_body"]
    for want in ("# Rank players", "Spec: `docs/specs/rank.md`", "Spec extract", "| AC-1 | API | done |", "Pass — api changed 92.0%",
                 "## Uncovered lines accepted", "`src/a.py:3-4`: defensive branch", "## Skipped gates", "- web: no web lane",
                 "no gate here", "## Unlocks used", "`src/legacy.py` in green: generated file", "## Flaky tests seen",
                 "tests/test_rank.py::test_tie", "_Prepared by keel._"):
        assert want in body, want


def test_open_pr_never_pushes(proj, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    st = {**pr_state(proj), "pr_body": "the body"}
    r = run("open_pr", ai(proj, state=st))
    assert r.ok and "not approved" in r.note and r.detail == "the body"
    r = run("open_pr", ai(proj, state={**st, "pr_approved": True}))
    assert r.ok and "No GitHub token" in r.note and "Copy the PR body" in r.note
    r = run("open_pr", ai(proj, state={**st, "pr_approved": True}, keys={"github": "ghp_test"}))
    assert r.ok and "not pushed" in r.note and "keel never pushes" in r.note
    assert sh(proj, "git", "remote").stdout == "", "nothing was pushed or added"


def test_pr_gate_shows_the_body_and_open_pr_needs_its_approval(client, repo, monkeypatch):
    from conftest import decide, start, wait
    from keel_engine.workflows.model import from_dict
    w = from_dict({"name": "pr", "keel_rules": False, "steps": [
        {"id": "body", "kind": "code", "name": "PR body", "action": "pr"},
        {"id": "pr_gate", "kind": "gate", "name": "open the PR?"},
        {"id": "open", "kind": "code", "name": "open PR", "action": "open_pr"}]})
    tid = start(client, repo, workflow=w)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "pr_gate" and "_Prepared by keel._" in s["waiting"]["detail"]
    s = decide(client, tid, "approve")
    assert s["status"] == "done"
    notes = [e["data"].get("note") or "" for e in client.bus.of(tid, "step.finished") if e.get("step") == "open"]
    assert notes and "No GitHub token" in notes[-1]
