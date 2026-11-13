"""0.17.2 Rules: keel v1's hook gaps (push gate, content at write time, big reads, failed commands, wrappers, codex
hooks) and rule packs (.keel/rules/*.yaml) through every guard path: the hook, the ToolBox and the diff guard."""

import subprocess
from pathlib import Path

import pytest

from keel_engine import hook, rules
from keel_engine.runtime import blockers, guard_ctx
from keel_engine.tools import guard
from keel_engine.tools.agent_tools import ToolBox

PROD = "apps/api/src/main/kotlin/app/Score.kt"
TEST = "apps/api/src/test/kotlin/app/ScoreTest.kt"
WEB = "apps/web/src/App.tsx"
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"

PACK = """schema: 1
kind: rules
name: no-new-dependencies
title: No new dependencies
version: 1.0.0
publisher: keel
summary: Agents never add a package.
rules:
  - id: no-install
    kind: bash
    match: [left-pad]
    reason: Adding a package is a person's decision.
  - id: no-lockfile
    kind: edit
    match: ["**/package-lock.json"]
    phases: in-flow
    reason: The lockfile changes with npm, not by hand.
  - id: web-not-api
    kind: edit
    match: ["apps/api/**"]
    lanes: [web]
    reason: A web criterion does not change the api.
  - id: no-secrets-dir
    kind: read
    match: ["secrets/**"]
    reason: Secrets stay out of the conversation.
  - id: no-deploy
    kind: mcp
    match: [deploy]
    reason: Deploys are a person's job.
"""


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"
    for d in ("apps/api/src/main/kotlin/app", "apps/api/src/test/kotlin/app", "apps/web/src", ".keel/rules", "secrets"):
        (r / d).mkdir(parents=True)
    (r / PROD).write_text("class Score\n")
    (r / "README.md").write_text("# p\n")
    (r / ".keel/config.yml").write_text("layout: repo\n")
    (r / ".keel/rules/no-new-dependencies.yaml").write_text(PACK)
    return r


def ctx(tmp_path, root, phase="green", **kw) -> str:
    import uuid
    return guard_ctx.write_context(tmp_path / "ctx" / uuid.uuid4().hex / "guard.json", root=str(root), phase=phase, ac=None,
                                   lane=kw.pop("lane", None), unlocks=kw.pop("unlocks", []), agent="implementer", thread="t", **kw)


def refused(tool, ti, c) -> str | None:
    return hook.decide(tool, ti, hook.load_context(c))


# ------------------------------------------------------------------ packs

def test_pack_parses_and_validates():
    pack, errors = rules.parse_pack(PACK)
    assert not errors and pack["id"] == "no-new-dependencies" and len(pack["rules"]) == 5
    assert pack["rules"][0]["how"] == "contains" and pack["rules"][1]["how"] == "glob"
    assert pack["version"] == "1.0.0" and pack["publisher"] == "keel" and pack["requires"] == {"keel": "", "plugins": {}}
    _, errors = rules.parse_pack("name: Bad Id\nrules: [{id: x, kind: shell, match: [], reason: ''}]")
    joined = "\n".join(errors)
    for word in ("schema:", "name:", "title:", "version:", "kind:", "match:", "reason:"):
        assert word in joined
    head = "schema: 1\nname: p\ntitle: P\nversion: 1.0.0\n"
    _, errors = rules.parse_pack(head + "rules: [{id: r, kind: bash, how: regex, match: ['(('], reason: x}]")
    assert "regular expression" in errors[0]
    _, errors = rules.parse_pack(head + "rules: [{id: r, kind: edit, match: [a], phases: [nope], reason: x}]")
    assert "phases" in errors[0]
    _, errors = rules.parse_pack(head + "kind: plugin\nrequires: {keel: soon, plugins: {Db: x}}\nrules: [{id: r, kind: edit, match: [a], reason: x}]")
    assert any("kind: rules" in e for e in errors) and any("requires.keel" in e for e in errors) and any("requires.plugins" in e for e in errors)
    pack, errors = rules.parse_pack(head + "requires: {keel: '>=0.17.2', plugins: [db]}\nrules: [{id: r, kind: edit, match: [a], reason: x}]")
    assert not errors and pack["requires"] == {"keel": ">=0.17.2", "plugins": {"db": ""}} and pack["publisher"] == "local"


def test_load_config_reads_packs_and_reports_bad_files(root):
    (root / ".keel/rules/broken.yaml").write_text("name: broken\nrules: nope\n")
    (root / ".keel/rules/future.yaml").write_text(PACK.replace("no-new-dependencies", "future").replace("rules:\n", "requires: {keel: '>=99.0.0'}\nrules:\n", 1))
    (root / ".keel/rules/other.yaml").write_text(PACK)          # id is not the file name
    cfg = rules.load_config(root)
    assert [p["id"] for p in cfg["packs"]] == ["no-new-dependencies"]
    assert {e["file"] for e in cfg["pack_errors"]} == {".keel/rules/broken.yaml", ".keel/rules/other.yaml", ".keel/rules/future.yaml"}
    assert any("needs keel >=99.0.0" in e["errors"][0] for e in cfg["pack_errors"])
    assert cfg["packs"][0]["source"] == "file"          # no install record: a hand-written file
    assert cfg["configured"] is True


def test_pack_rules_in_every_check(root):
    cfg = rules.load_config(root)
    v = rules.check_bash("none", "npm i left-pad", cfg)
    assert not v.ok and "(rule no-new-dependencies/no-install)" in v.reason
    assert not rules.check_edit("green", "apps/web/package-lock.json", cfg).ok
    assert rules.check_edit("none", "apps/web/package-lock.json", cfg).ok          # in-flow only
    assert not rules.check_edit("green", PROD, cfg, lane="web").ok                   # lanes
    assert rules.check_edit("green", PROD, cfg, lane="api").ok
    assert not rules.check_read("secrets/prod.txt", cfg).ok
    # an unlock passes a pack's edit rule, like the matrix
    assert rules.check_edit("green", "apps/web/package-lock.json", cfg, unlocks=[{"path": "apps/web/package-lock.json", "phase": "green"}]).ok
    # rules.off: pack/<id> switches the whole pack off
    cfg["rules"]["off"] = ["pack/no-new-dependencies"]
    assert rules.check_bash("none", "npm i left-pad", cfg).ok


def test_pack_rules_through_the_hook_toolbox_and_diff_guard(tmp_path, root):
    c = ctx(tmp_path, root, lane="web")
    assert "rule no-new-dependencies/web-not-api" in refused("Write", {"file_path": str(root / PROD), "content": "x"}, c)
    assert "no-deploy" in refused("mcp__ops__deploy_service", {}, c)
    assert "no-secrets-dir" in refused("Read", {"file_path": "secrets/a.txt"}, c)
    tb = ToolBox(str(root), "green", lane="web")
    assert tb.write_file(PROD, "class Score2\n").startswith("REFUSED")
    assert tb.run_command("npm i left-pad").startswith("REFUSED")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    before = guard.snapshot(str(root))
    (root / "apps/api/src/main/kotlin/app/New.kt").write_text("class New\n")
    out = guard.guard_diff(str(root), "green", before, lane="web")
    assert any("web-not-api" in r["reason"] for r in out) and not (root / "apps/api/src/main/kotlin/app/New.kt").exists()


def test_agents_may_not_edit_their_own_rules(tmp_path, root):
    c = ctx(tmp_path, root, phase="green")
    for path in (".keel/config.yml", ".keel/rules/no-new-dependencies.yaml", ".keel/rules/mine.yaml"):
        assert "Agents never change their own rules" in refused("Write", {"file_path": path, "content": "x"}, c)
    assert "Agents never change their own rules" in refused("Bash", {"command": "echo off > .keel/config.yml"}, c)
    assert refused("Write", {"file_path": ".keel/config.yml", "content": "x"}, ctx(tmp_path, root, phase="none"))
    assert rules.check_edit("setup", ".keel/config.yml", rules.make_config()).ok      # keel init writes it
    assert not rules.check_edit("setup", ".keel/rules/x.yaml", rules.make_config()).ok


# ------------------------------------------------------------------ built-in rules: off and never off

def test_never_off_rules_stay_on_and_can_off_ones_switch(root):
    cfg = rules.make_config({"rules": {"off": ["force-push", "env-files", "generated", "read-build-output",
                                               "skip-test-flags", "new-dependency"]},
                             "guards": {"bash_deny_always": [], "bash_deny_in_flow": []}})
    # the config emptied the lists and asked to switch off never-off rules: they still hold
    assert not rules.check_bash("none", "git push --force origin x", cfg).ok
    assert not rules.check_bash("none", "git commit --no-verify -m x", cfg).ok
    assert not rules.check_bash("green", "git reset --hard HEAD", cfg).ok
    assert not rules.check_edit("green", ".env", cfg).ok
    # the switchable ones are off
    assert rules.check_bash("none", "./gradlew build -x test", cfg).ok
    assert rules.check_bash("green", "npm install left-pad", cfg).ok
    assert rules.check_read("apps/web/node_modules/x.js", cfg).ok
    assert rules.classify(cfg, "apps/web/src/generated/api.ts") != "generated"
    assert rules.is_off(cfg, "generated") and not rules.is_off(cfg, "force-push")
    assert {b["id"] for b in rules.BUILTINS if not b["can_off"]} >= {"env-files", "force-push", "no-verify", "commit-in-flow",
                                                                     "secrets-at-write", "keel-rules-files"}


# ------------------------------------------------------------------ v1 gaps

def test_secrets_and_disabled_tests_are_refused_at_write_time(tmp_path, root):
    c = ctx(tmp_path, root, phase="green")
    assert "AWS access key" in refused("Write", {"file_path": PROD, "content": f'val k = "{AWS}"\n'}, c)
    assert "AWS access key" in refused("Edit", {"file_path": PROD, "old_string": "a", "new_string": f'"{AWS}"'}, c)
    assert refused("Edit", {"file_path": PROD, "old_string": "a", "new_string": f'"{AWS}" // keel:allow-secret'}, c) is None
    red = ctx(tmp_path, root, phase="red")
    assert "@Disabled" in refused("Write", {"file_path": TEST, "content": "@Disabled\nclass ScoreTest\n"}, red)
    assert "@Disabled" in refused("MultiEdit", {"file_path": TEST, "edits": [{"old_string": "x", "new_string": "@Disabled fun t() {}"}]}, red)
    # a marker that was there before is not the agent's
    (root / TEST).write_text("@Disabled\nclass ScoreTest\n")
    assert refused("Write", {"file_path": TEST, "content": "@Disabled\nclass ScoreTest\nfun more() {}\n"}, red) is None
    # .env.example and generated code are not scanned
    assert rules.check_content("apps/web/.env.example", f"KEY={AWS}").ok
    # the ToolBox and the diff guard do the same
    tb = ToolBox(str(root), "green")
    assert "AWS access key" in tb.write_file(PROD, f'val k = "{AWS}"\n')
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    before = guard.snapshot(str(root))
    (root / "apps/api/src/main/kotlin/app/Key.kt").write_text(f'val k = "{AWS}"\n')
    out = guard.guard_diff(str(root), "green", before)
    assert out and out[0]["bucket"] == "content" and not (root / "apps/api/src/main/kotlin/app/Key.kt").exists()


def test_big_files_are_read_in_parts(tmp_path, root):
    (root / "big.txt").write_text("x\n" * 2500)
    c = ctx(tmp_path, root)
    assert "has 2500 lines" in refused("Read", {"file_path": "big.txt"}, c)
    assert refused("Read", {"file_path": "big.txt", "offset": 1, "limit": 200}, c) is None
    tb = ToolBox(str(root), "green")
    assert tb.read_file("big.txt").startswith("REFUSED") and not tb.read_file("big.txt", limit=10).startswith("REFUSED")
    (root / ".keel/config.yml").write_text("guards:\n  read_guard_max_lines: 0\n")
    assert refused("Read", {"file_path": "big.txt"}, ctx(tmp_path, root)) is None


def test_push_gate_refuses_an_agents_push_while_blockers_exist(tmp_path, root):
    gate = [{"gate": "release", "why": "no release verdict for this commit", "fix": "run the whole test suite"}]
    c = ctx(tmp_path, root, phase="none", push_blockers=gate)
    assert "push gate: release" in refused("Bash", {"command": "git push origin feat"}, c)
    assert "push gate" in refused("Bash", {"command": "cd x && gh pr create --fill"}, c)
    assert refused("Bash", {"command": "git status"}, c) is None
    assert refused("Bash", {"command": "git push origin feat"}, ctx(tmp_path, root, phase="none")) is None   # no blockers
    tb = ToolBox(str(root), "none")
    tb.push_blockers = gate
    assert "push gate" in tb.run_command("git push")
    (root / ".keel/config.yml").write_text("rules:\n  push_gate: false\n")
    assert refused("Bash", {"command": "git push origin feat"}, c) is None
    (root / ".keel/config.yml").unlink()
    assert refused("Bash", {"command": "git push origin feat"}, c) is None                    # not adopted: no gate


def test_for_agents_lists_blockers_only_for_an_adopted_project(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    assert blockers.for_agents(str(r)) == []
    (r / ".keel").mkdir()
    (r / ".keel/config.yml").write_text("layout: repo\n")
    (r / "a").write_text("a")
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qam", "x", "--allow-empty"], cwd=r, check=True)
    assert any(b["gate"] == "release" for b in blockers.for_agents(str(r), project="p-x"))


# ------------------------------------------------------------------ codex and copilot

def test_codex_tool_names_are_judged_as_bash_and_edit(tmp_path, root):
    c = ctx(tmp_path, root, phase="red")
    assert "git commit" in refused("shell", {"command": ["bash", "-lc", "git commit -m x"]}, c).lower() or \
        "commits are made" in refused("shell", {"command": ["bash", "-lc", "git commit -m x"]}, c).lower()
    assert refused("exec_command", {"cmd": "ls"}, c) is None
    patch = f"*** Begin Patch\n*** Update File: {PROD}\n@@\n-class Score\n+class Score2\n*** End Patch\n"
    assert "blocked in phase" in refused("apply_patch", {"input": patch}, c)
    test_patch = f"*** Begin Patch\n*** Add File: {TEST}\n+class ScoreTest\n*** End Patch\n"
    assert refused("apply_patch", {"input": test_patch}, c) is None
    secret = f"*** Begin Patch\n*** Add File: {TEST}\n+val k = \"{AWS}\"\n*** End Patch\n"
    assert "AWS" in refused("apply_patch", {"input": secret}, c)
    assert "patch" in refused("apply_patch", {"input": "nonsense"}, c)


# ------------------------------------------------------------------ the editor (v0.17.2 feedback)

def test_errors_name_their_line():
    from keel_engine.rules import packs
    text = "schema: 1\nname: p\ntitle: P\nversion: 1.0.0\nrules:\n  - id: r\n    kind: shell\n    match: [x]\n"
    _, errors = packs.parse_pack(text)
    lines = {e["message"].split(":")[0]: e["line"] for e in packs.error_lines(text, errors)}
    assert lines["rules[1].kind"] == 7 and lines["rules[1].reason"] == 6
    assert packs.error_lines("a: [1\nb: 2\n", ["not valid YAML"])[0]["line"] == 2


def test_normalize_keeps_the_meaning_and_the_header_comment():
    from keel_engine.rules import packs
    for f in sorted((Path(__file__).resolve().parents[2] / "content" / "rules").glob("*.yaml")):
        text = f.read_text()
        tidy = packs.normalize(text)
        assert packs.parse_pack(tidy) == packs.parse_pack(text), f.name
        assert tidy.startswith("#") and "\n  - id: " in tidy and packs.normalize(tidy) == tidy, f.name
    messy = "rules: [{reason: Not here., match: [a, b], kind: bash, id: r}]\nversion: 1.0.0\ntitle: T\nname: t\nschema: 1\n"
    tidy = packs.normalize(messy)
    assert tidy.split("\n")[:4] == ["schema: 1", "name: t", "title: T", "version: 1.0.0"]
    assert "    match: [a, b]\n" in tidy and tidy.index("  - id: r") < tidy.index("    kind: bash") < tidy.index("    reason:")
    long = messy.replace("[a, b]", "[" + ", ".join(f"pattern-number-{i}" for i in range(6)) + "]")
    assert "    match:\n      - pattern-number-0\n" in packs.normalize(long)


# ------------------------------------------------------------------ 0.22.0 one part of a compound command (issue #76)

def test_only_gradles_x_skips_tests(root):
    cfg = rules.make_config()
    for cmd in ("./gradlew build -x test", "gradle check -x :app:test", "cd api && ./gradlew build --exclude-task check",
                "FOO=1 ./gradlew build --exclude-task=test", 'bash -c "./gradlew build -x test"',
                'bash -c "cd api && ./gradlew build -x test"'):
        v = rules.check_bash("none", cmd, cfg)
        assert not v.ok and "skips checks" in v.reason, cmd
    for cmd in ("uv run pytest -x tests/test_scores.py::test_rank", "pytest -x -k rank", "python -m pytest -x tests",
                "./gradlew test --tests 'x.ScoreTest' -x lint", "tar -x test.tar"):
        assert rules.check_bash("none", cmd, cfg).ok, cmd


def test_a_refused_push_names_the_part_that_may_run_alone(tmp_path, root):
    gate = [{"gate": "release", "why": "no release verdict for this commit", "fix": "run the whole test suite"}]
    c = ctx(tmp_path, root, phase="none", push_blockers=gate)
    why = refused("Bash", {"command": "cd api && sed -i 's/a/b/' x.txt && git push origin feat"}, c)
    assert "push gate: release" in why
    assert "Nothing in this command ran. Run the other part on its own: sed -i 's/a/b/' x.txt" in why
    assert "push after the gate clears" in why
    assert "Nothing in this command ran" not in refused("Bash", {"command": "git push origin feat"}, c)


def test_a_refused_commit_in_a_flow_names_the_part_that_may_run_alone(root):
    v = rules.check_bash("green", "ruff format src && git commit -am x", rules.make_config())
    assert not v.ok and "commit step" in v.reason and "Run the other part on its own: ruff format src" in v.reason
