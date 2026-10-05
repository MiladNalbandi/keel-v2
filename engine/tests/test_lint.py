"""v0.4.1: tools (formatters, linters, static checks) from the stacks and .keel/config.yml, the edit and commit hooks,
the lint flow, ship's static checks and the review's lint lens. Real tiny commands (sh -c), the fake model."""

import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import decide, start, wait
from keel_engine.runtime import actions, lint_actions, stacks, tools, verdicts
from keel_engine.runtime.actions import ActionInput
from keel_engine.workflows.model import from_dict

IDENT = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(root, *args):
    return subprocess.run(["git", *IDENT, *args], cwd=root, capture_output=True, text=True)


def configure(root: Path, tool_block: dict, commit: bool = True, **more):
    cfg = {"version": 4, "backend": {"dir": "", "build": "python -m pytest"}, "tools": tool_block, **more}
    f = root / ".keel" / "config.yml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(yaml.safe_dump(cfg, sort_keys=False))
    if commit:
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "chore: tools")
    stacks.forget()


def ai(root, **kw) -> ActionInput:
    base = dict(root=str(root), phase="green", title="t", ac=None, acs=[], fake=True, flow="custom", project="demo")
    base.update(kw)
    return ActionInput(**base)


def wf(steps):
    return from_dict({"name": "custom", "keel_rules": False, "steps": steps})


@pytest.fixture
def content(tmp_path, monkeypatch):
    """A content folder with one stack, so resolution is under the test's control."""
    c = tmp_path / "content"
    (c / "stacks").mkdir(parents=True)
    (c / "stacks" / "toy.yml").write_text(yaml.safe_dump({
        "name": "toy", "lane": "web", "detect": {"files": ["toy.txt"]},
        "tools": {"fmt": {"run": "sh -c 'true' _ {FILES}", "on": "edit", "match": r"\.js$", "fail": "fix"},
                  "lint": {"run": "sh -c 'true'", "on": "manual", "fail": "block", "description": "lint it"},
                  "sonar": {"run": "sonar", "on": "manual", "kind": "status"}}}))
    monkeypatch.setenv("KEEL_CONTENT", str(c))
    stacks.forget()
    yield c
    stacks.forget()


# ------------------------------------------------------------------ resolve

def test_tools_merge_stack_then_project_and_false_turns_one_off(tmp_path, content):
    root = tmp_path / "p"
    root.mkdir()
    (root / "toy.txt").write_text("x")
    (root / ".keel").mkdir()
    (root / ".keel" / "config.yml").write_text(yaml.safe_dump({"tools": {
        "lint": {"fail": "warn"}, "sonar": False,
        "bad-on": {"run": "x", "on": "sometimes"}, "no-run": {"on": "edit"},
        "manual-files": {"run": "x {FILES}", "on": "manual"}, "bad-match": {"run": "x", "on": "edit", "match": "("}}}))
    got = {t.name: t for t in tools.resolve(str(root))}
    assert "sonar" not in got
    assert got["lint"].fail == "warn" and got["lint"].run == "sh -c 'true'" and got["lint"].source == "project"
    assert got["lint"].lane == "web" and got["lint"].description == "lint it", "keys the project does not name stay"
    assert got["fmt"].on == "edit" and got["fmt"].source == "toy", "`on:` survives YAML 1.1 (on = true)"
    probs = "\n".join(tools.problems(list(got.values())))
    for want in ("bad-on (project) has on: sometimes", "no-run (project) has no `run:`",
                 "manual-files (project) takes {FILE} or {FILES} but runs on: manual", "bad-match (project) has an unreadable match"):
        assert want in probs, want
    # a malformed tool is never picked
    assert [t.name for t, _f in tools.pick(list(got.values()), ("manual", "edit"), ["a.js"])] == ["fmt", "lint"]
    # simulated checks keep only the project's own tools
    assert "fmt" not in {t.name for t in tools.resolve(str(root), include_stacks=False)}


def test_a_stack_that_does_not_match_gives_nothing_and_a_bad_block_is_a_problem(tmp_path, content):
    root = tmp_path / "p"
    root.mkdir()
    assert tools.resolve(str(root)) == []
    (root / ".keel").mkdir()
    (root / ".keel" / "config.yml").write_text("tools: [a, b]\n")
    t = tools.resolve(str(root))
    assert len(t) == 1 and "not a block of named tools" in t[0].problem


def test_a_missing_program_is_not_available_never_a_failure(tmp_path):
    t = tools.Tool(name="ghost", run="definitely-not-a-program-keel {FILES}", on="edit", fail="block")
    (tmp_path / "a.py").write_text("x")
    r = tools.run_one(str(tmp_path), {}, t, ["a.py"])
    assert r["available"] is False and r["ok"] is False and r["code"] == 127
    assert tools.failing([r]) == [] and "not available" in tools.line(r)
    npx = tools.Tool(name="n", run="sh -c 'echo \"npm error could not determine executable to run\"; exit 1'")
    assert tools.run_one(str(tmp_path), {}, npx, [])["available"] is False


def test_files_are_relative_to_the_lane_folder_and_build_is_found_at_the_root(tmp_path):
    (tmp_path / "web" / "src").mkdir(parents=True)
    (tmp_path / "web" / "src" / "a.ts").write_text("x")
    (tmp_path / "gradlew").write_text("#!/bin/sh\necho gradle \"$@\"\n")
    (tmp_path / "gradlew").chmod(0o755)
    cfg = {"frontend": {"dir": "web"}, "backend": {"dir": "web", "build": "./gradlew"}}
    t = tools.Tool(name="ls", run="sh -c 'pwd; echo \"$@\"' _ {FILES}", on="edit", lane="web")
    r = tools.run_one(str(tmp_path), cfg, t, ["web/src/a.ts", "other/b.ts"])
    assert r["dir"] == "web" and r["files"] == 1 and "src/a.ts" in r["output"] and "other" not in r["output"]
    b = tools.run_one(str(tmp_path), cfg, tools.Tool(name="b", run="{BUILD} -q check", lane="api"), [])
    assert b["ok"] and "gradle -q check" in b["output"]


# ------------------------------------------------------------------ the commit hook

FIXER = "sh -c 'for f; do echo \"# fixed\" >> \"$f\"; done' _ {FILES}"


def test_pre_commit_fix_restages_and_block_refuses_the_commit(repo):
    configure(repo, {"fixer": {"run": FIXER, "on": "pre-commit", "match": r"\.py$", "fail": "fix"}})
    f = repo / "src" / "scores" / "new.py"
    f.write_text("x = 1\n")
    r = actions.commit(ai(repo))
    assert r.ok, r.note
    assert "fixer re-staged" in r.note
    assert git(repo, "show", "HEAD:src/scores/new.py").stdout == "x = 1\n# fixed\n"
    assert not git(repo, "status", "--porcelain").stdout.strip(), "the fixed bytes are what was committed"

    configure(repo, {"style": {"run": "sh -c 'echo \"$1:1 bad style\"; exit 1' _ {FILES}", "on": "pre-commit",
                               "match": r"\.py$", "fail": "block"},
                     "noisy": {"run": "sh -c 'echo meh; exit 3'", "on": "pre-commit", "fail": "warn"}})
    head = git(repo, "rev-parse", "HEAD").stdout
    f.write_text("x = 2\n")
    r = actions.commit(ai(repo))
    assert not r.ok and r.note == "Static checks refused the commit: style." and "src/scores/new.py:1 bad style" in r.detail
    assert git(repo, "rev-parse", "HEAD").stdout == head and not git(repo, "diff", "--cached", "--name-only").stdout.strip()
    # a docs-only commit does not wake the .py tool; the warn tool only notes
    f.write_text("x = 1\n# fixed\n")
    (repo / "docs" / "knowledge").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "knowledge" / "n.md").write_text("n\n")
    r = actions.commit(ai(repo, phase="memory"))
    assert r.ok, r.note
    assert "noisy (warn): exit 3: meh" in r.note


def test_a_refused_commit_goes_back_to_the_agent_with_the_output(client, repo):
    counter = repo.parent / "count"
    configure(repo, {"style": {"run": f"sh -c 'n=$(cat {counter} 2>/dev/null || echo 0); echo $((n+1)) > {counter}; "
                                      f"[ $n -ge 1 ] || {{ echo \"E1 line too long\"; exit 1; }}' _ {{FILES}}",
                               "on": "pre-commit", "match": r"\.py$", "fail": "block"}})
    w = wf([{"id": "green", "kind": "agent", "name": "green", "agent": "implementer", "phase": "green"},
            {"id": "commit", "kind": "code", "name": "commit", "action": "commit", "phase": "green"}])
    tid = start(client, repo, workflow=w)
    s = wait(client, tid)
    assert s["status"] == "done", s
    starts = [e for e in client.bus.of(tid, "agent.started")]
    assert len(starts) == 2, "the refusal sent the work back once"
    ran = client.bus.of(tid, "tool.ran")
    assert [e["data"]["ok"] for e in ran if e["data"]["tool"] == "style"] == [False, True]
    assert "E1 line too long" in ran[0]["data"]["output"]


# ------------------------------------------------------------------ the edit hook

def test_after_an_agent_step_formatters_run_and_findings_reach_the_next_agent(client, repo, monkeypatch):
    from keel_engine.models import fake as fake_mod
    configure(repo, {"fmt": {"run": FIXER, "on": "edit", "match": r"\.py$", "fail": "fix"},
                     "lint": {"run": "sh -c 'echo \"$1: W1 unused import\"; exit 1' _ {FILES}", "on": "batch",
                              "match": r"\.py$", "fail": "warn"}})
    seen = []
    real = fake_mod._plan

    def plan(req):
        seen.append(req)
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    w = wf([{"id": "green", "kind": "agent", "name": "green", "agent": "implementer", "phase": "green"},
            {"id": "look", "kind": "agent", "name": "look", "agent": "reviewer", "phase": "review"},
            {"id": "g", "kind": "gate", "name": "check"}])
    tid = start(client, repo, workflow=w)
    s = wait(client, tid)
    written = repo / "src" / "scores" / "bug_1.py"
    assert written.read_text().endswith("# fixed\n"), "the formatter's change is kept"
    note = next(e["data"]["note"] for e in client.bus.of(tid, "step.finished") if e["step"] == "green")
    assert "tools: 2 ran, 1 file(s) formatted, 1 finding(s): lint (warn)" in note
    assert "W1 unused import" in seen[-1].prompt or "lint (warn)" in seen[-1].prompt
    assert "Static checks on the last changed files" in s["waiting"]["detail"] and "lint (warn)" in s["waiting"]["detail"]


# ------------------------------------------------------------------ the lint flow

def lint_flow(client, repo, **data):
    tid = start(client, repo, workflow="lint", data=data, title="tidy up")
    return tid, wait(client, tid)


def test_lint_flow_clean_runs_the_fixers_commits_and_reports(client, repo):
    configure(repo, {"fmt": {"run": FIXER, "on": "edit", "match": r"\.py$", "fail": "fix"},
                     "check": {"run": "sh -c 'echo all good'", "on": "manual", "fail": "block"},
                     "schema": {"run": "sh -c 'echo x > docs/schema.sql'", "on": "manual", "kind": "task"}})
    tid, s = lint_flow(client, repo, scope="all")
    w = s["waiting"]
    assert w["step"] == "read_report", s
    assert "| fmt | edit | fix | pass |" in w["detail"] and "| check | manual | block | pass |" in w["detail"]
    assert "schema" not in w["detail"] and not (repo / "docs" / "schema.sql").exists(), "a task is not a lint"
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == "chore(lint): tidy up"
    assert "src/scores/__init__.py" in git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    v = verdicts.latest("demo", "lint")
    assert v["ok"] and set(v["detail"]["tools"]) == {"fmt", "check"} and v["detail"]["tools"]["fmt"]["fail"] == "fix"
    assert v["commit"] or v["detail"]["tree"]
    assert decide(client, tid)["status"] == "done"
    assert not [e for e in client.bus.of(tid, "agent.started")], "nothing to fix, no agent"


GATE = "sh -c 'test -f src/scores/lint_fixed.py || { echo \"src/scores/__init__.py:1: E501 line too long\"; exit 1; }'"


def test_lint_flow_fixes_findings_in_round_one(client, repo):
    configure(repo, {"style": {"run": GATE, "on": "manual", "fail": "block"}})
    tid, s = lint_flow(client, repo)
    assert s["waiting"]["step"] == "read_report", s
    fixers = [e["data"] for e in client.bus.of(tid, "agent.started")]
    assert len(fixers) == 1 and fixers[0]["agent"] == "implementer" and fixers[0]["phase"] == "lint-fix"
    assert "Pass" in s["waiting"]["detail"] and "Runs: 2" in s["waiting"]["detail"]
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == "chore(lint): tidy up"
    assert "src/scores/lint_fixed.py" in git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert verdicts.latest("demo", "lint")["ok"]


def test_lint_flow_still_failing_after_two_rounds_asks(client, repo, monkeypatch):
    from keel_engine.models import fake as fake_mod
    seen = []
    real = fake_mod._plan

    def plan(req):
        seen.append(req)
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    configure(repo, {"style": {"run": "sh -c 'echo \"E9 always\"; exit 1'", "on": "manual", "fail": "block"}})
    tid, s = lint_flow(client, repo)
    w = s["waiting"]
    assert w["step"] == "clean" and "still no after 2 round(s)" in w["title"], s
    assert len(seen) == 2 and "E9 always" in seen[0].prompt and "Do not change behaviour" in seen[0].prompt
    s = decide(client, tid, "approve", why="the rule is wrong for this file")
    assert s["waiting"]["step"] == "read_report"
    assert "FAIL" in s["waiting"]["detail"] and "## Still failing" in s["waiting"]["detail"]
    assert "## Went on with findings" in s["waiting"]["detail"]
    assert not verdicts.latest("demo", "lint")["ok"]

    tid2, s2 = lint_flow(client, repo)
    assert decide(client, tid2, "reject", why="stop")["status"] == "stopped"


def test_lint_flow_diff_scope_skips_file_tools_with_no_changed_file(client, repo):
    configure(repo, {"fmt": {"run": "sh -c 'exit 1' _ {FILES}", "on": "edit", "match": r"\.py$", "fail": "block"}})
    tid, s = lint_flow(client, repo)
    assert s["waiting"]["step"] == "read_report" and "No tool ran." in s["waiting"]["detail"]
    assert verdicts.latest("demo", "lint")["detail"]["available"] is False


# ------------------------------------------------------------------ ship + review

def test_ship_runs_the_checks_without_fixers_and_shows_the_verdict(client, repo, monkeypatch):
    from test_flows_cover_ship import script, ship_plan
    configure(repo, {"fmt": {"run": FIXER, "on": "edit", "match": r"\.py$", "fail": "fix"},
                     "types": {"run": "sh -c 'echo \"x.py:3 error: bad type\"; exit 1'", "on": "manual", "fail": "block"}})
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    s = wait(client, tid)
    assert "  - lint" in s["waiting"]["detail"], "the skip menu offers it"
    s = decide(client, tid, "approve", payload={"skip": {"release": "r", "cover": "c", "deps": "d", "reviewers": "r",
                                                         "spec_walk": "s"}})
    w = s["waiting"]
    assert w["step"] == "final_review", s
    assert "| lint | FAIL |" in w["detail"] and "static checks fail: 1 of 1 tool(s) failed: types" in w["detail"]
    assert not any(e["data"]["tool"] == "fmt" for e in client.bus.of(tid, "tool.ran")), "a check changes no file"
    s = decide(client, tid)
    assert "## Static checks" in s["waiting"]["detail"] and "- types (manual, block): fail" in s["waiting"]["detail"]


def test_review_lens_lint_reports_without_fixing(client, repo):
    configure(repo, {"fmt": {"run": FIXER, "on": "edit", "match": r"\.py$", "fail": "fix"},
                     "check": {"run": "sh -c 'echo \"W2 shadowed name\"; exit 1'", "on": "manual", "fail": "warn"}})
    git(repo, "checkout", "-q", "-b", "feat/x")
    (repo / "src" / "scores" / "more.py").write_text("y = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feat: more")
    tid = start(client, repo, workflow="review", data={"lens": "lint"})
    s = wait(client, tid)
    d = s["waiting"]["detail"]
    assert s["waiting"]["step"] == "read_report" and "## Static checks" in d and "| check | manual | warn | warning |" in d
    assert "fmt" not in d and (repo / "src" / "scores" / "more.py").read_text() == "y = 1\n"
    assert not client.bus.of(tid, "agent.started")


def test_unit_scope_leaves_the_users_own_files_out(repo):
    (repo / "mine.py").write_text("mine\n")
    a = ai(repo, preexisting={"mine.py": git_fp(repo, "mine.py")})
    files, _label = lint_actions.scope_files(a, {}, "all")
    assert "mine.py" not in files and "src/scores/__init__.py" in files


def git_fp(root, rel):
    from keel_engine.tools import git as g
    return g.fingerprint(str(root), rel)
