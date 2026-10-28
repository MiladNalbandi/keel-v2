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


FLAG = "__pycache__/built.flag"     # git ignores it: the setup commit refuses any other new file in the repo
DOCTOR = (f"Rung 4 runs `test -f {FLAG}` and the file is missing: nothing built it yet. Evidence: ls shows no "
          f"{FLAG}. The fix changes repo files (one ignored file).\n```keel-fix\nmkdir -p __pycache__\ntouch {FLAG}\n```\n"
          "DIAGNOSIS: fixable")


@pytest.mark.parametrize("choice", ["apply_fix", "retry", "own_fix", "skip", "stop"])
def test_a_failing_rung_shows_the_doctors_diagnosis_and_asks(client, repo, monkeypatch, choice):
    # 0.18.22 the setup-doctor is read-only: it proposes, the rung gate shows its DIAGNOSIS and fix at once and asks
    # apply_fix (keel runs the proposed commands) / retry / skip (not checked) / stop
    (Path(repo) / ".keel").mkdir(exist_ok=True)
    (Path(repo) / ".keel" / "config.yml").write_text(f"commands:\n  api_compile: 'test -f {FLAG}'\n  unit_tests: 'true'\n")
    seen = answers(monkeypatch, {"setup-doctor": lambda r: DOCTOR})
    tid = start(client, repo, workflow="init", title="keel init", settings={**SETTINGS, "simulate_checks": False})
    s = to(client, tid, wait(client, tid), "rung_gate")
    w = s["waiting"]
    assert w["step"] == "rung_gate" and w["choices"] == ["apply_fix", "retry", "own_fix", "skip", "stop"]
    assert [c["name"] for c in w["choice_info"]] == ["apply_fix", "retry", "own_fix", "skip", "stop"] and w["choice_info"][0]["default"]
    assert all(c["description"] for c in w["choice_info"])
    doctors = [r for r in seen if r.agent == "setup-doctor"]
    assert len(doctors) == 1 and doctors[0].item["id"] == "failing-rungs" and [x["n"] for x in doctors[0].item["rungs"]] == [4]
    assert "You change nothing" in doctors[0].prompt and "keel-fix" in doctors[0].prompt
    assert "Rung 4 (Both apps compile) failed" in w["detail"] and f"$ test -f {FLAG}" in w["detail"]
    assert "DIAGNOSIS: fixable" in w["detail"] and "nothing built it yet" in w["detail"]
    # the doctor's keel-fix block is not repeated; keel's own fenced bash block shows what apply_fix runs
    assert f"```bash\n$ mkdir -p __pycache__\n$ touch {FLAG}\n```" in w["detail"] and "keel-fix" not in w["detail"]
    for head in ("## Failing rungs", "### Rung 4", "## Diagnosis", "## The proposed fix", "## Your choice"):
        assert head in w["detail"], head
    if choice == "retry":
        (Path(repo) / "__pycache__").mkdir(exist_ok=True)
        (Path(repo) / FLAG).write_text("")
    why = f"$ mkdir -p __pycache__\ntouch {FLAG}" if choice == "own_fix" else "checked it"
    s = decide(client, tid, "approve", why=why, payload={"choice": choice})
    assert any(line.startswith(f"gate rung_gate {choice}") for line in s["gate_log"])
    if choice == "stop":
        assert s["status"] != "waiting" and not (Path(repo) / FLAG).exists()
        return
    assert s["waiting"]["step"] == "audit_now", (s["waiting"]["title"], s["waiting"]["detail"])
    rung4 = next(r for r in s["ladder"] if r["n"] == 4)
    if choice == "skip":
        assert config(repo)["setup"]["not_checked"] == [4]
        assert rung4["status"] == "skipped" and "not checked" in rung4["detail"]
        assert "## Not checked" in (Path(repo) / "docs" / "RUNNING.md").read_text()
    else:
        assert rung4["status"] == "pass" and (Path(repo) / FLAG).exists()


# 0.18.57 e2e: the doctor's corrected command ran once (exit 0) but was never written to .keel/config.yml, so the
# ladder ran the old command again and failed on the same rung
FIXED = "cd __pycache__ && test -f built.flag"
DOCTOR_CMD = (f"Rung 4 runs `test -f built.flag` in the project folder, but the file is in __pycache__/. The fix changes "
              f"the rung's command.\n```keel-fix\n{FIXED}\n```\nDIAGNOSIS: fixable")


def test_apply_fix_writes_the_corrected_rung_command_to_the_config(client, repo, monkeypatch):
    (Path(repo) / "__pycache__").mkdir(exist_ok=True)
    (Path(repo) / FLAG).write_text("")
    (Path(repo) / ".keel").mkdir(exist_ok=True)
    (Path(repo) / ".keel" / "config.yml").write_text("commands:\n  api_compile: 'test -f built.flag'\n  unit_tests: 'true'\n")
    answers(monkeypatch, {"setup-doctor": lambda r: DOCTOR_CMD})
    tid = start(client, repo, workflow="init", title="keel init", settings={**SETTINGS, "simulate_checks": False})
    s = to(client, tid, wait(client, tid), "rung_gate")
    assert s["waiting"]["choices"][0] == "apply_fix"
    s = decide(client, tid, "approve", payload={"choice": "apply_fix"})
    assert config(repo)["commands"]["api_compile"] == FIXED
    assert s["waiting"]["step"] == "audit_now", (s["waiting"]["title"], s["waiting"]["detail"])
    assert next(r for r in s["ladder"] if r["n"] == 4)["status"] == "pass"


def test_corrected_commands_and_the_keel_config_block():
    from keel_engine.runtime import init_actions

    rungs = [{"n": 6, "cmd": "vendor/bin/phpunit", "key": "api_test_module"}, {"n": 2, "cmd": "composer install"}]
    ran = ["docker compose up -d app", "docker compose exec -T app vendor/bin/phpunit"]
    assert init_actions.corrected_commands(rungs, ran) == {"api_test_module": "docker compose exec -T app vendor/bin/phpunit"}
    # a setup command that only starts something is not a corrected rung command
    assert init_actions.corrected_commands(rungs, ["docker compose up -d app"]) == {}
    text = ("x\n```keel-config\ncommands.unit_tests: docker compose exec -T app vendor/bin/phpunit\n"
            "commands.nope: x\nbackend.dir: api\n```\nDIAGNOSIS: fixable")
    assert init_actions.config_fixes(text) == {"unit_tests": "docker compose exec -T app vendor/bin/phpunit"}


def test_a_fix_that_runs_the_tests_in_a_container_moves_the_other_backend_commands_there(tmp_path):
    from keel_engine.runtime import init_actions
    from keel_engine.runtime.actions import ActionInput

    (tmp_path / ".keel").mkdir()
    (tmp_path / ".keel" / "config.yml").write_text(
        "commands:\n  api_test_module: vendor/bin/phpunit\n  api_test_ac: vendor/bin/phpunit --filter '{AC_ID}'\n"
        "  web_test_module: npm test\n")
    a = ActionInput(root=str(tmp_path), phase="setup", title="keel init", ac=None, acs=[], fake=True, flow="init",
                    data={"failed_rungs": [{"n": 6, "cmd": "vendor/bin/phpunit", "key": "api_test_module"}]})
    log = init_actions._apply_fix(a, ["docker compose exec -T app vendor/bin/phpunit"])
    cmds = config(tmp_path)["commands"]
    assert cmds["api_test_module"] == "docker compose exec -T app vendor/bin/phpunit"
    assert cmds["api_test_ac"] == "docker compose exec -T app vendor/bin/phpunit --filter '{AC_ID}'"
    assert cmds["web_test_module"] == "npm test" and "wrote to .keel/config.yml: commands.api_test_module" in log


def test_a_rung_without_a_proposed_command_offers_no_apply_and_a_destructive_fix_is_refused(client, repo, monkeypatch):
    from keel_engine.runtime import init_actions

    (Path(repo) / ".keel").mkdir(exist_ok=True)
    (Path(repo) / ".keel" / "config.yml").write_text("commands:\n  api_compile: 'false'\n  unit_tests: 'true'\n")
    answers(monkeypatch, {"setup-doctor": lambda r: "No JDK on this machine.\nDIAGNOSIS: needs-you"})
    tid = start(client, repo, workflow="init", title="keel init", settings={**SETTINGS, "simulate_checks": False})
    s = to(client, tid, wait(client, tid), "rung_gate")
    assert s["waiting"]["choices"] == ["retry", "own_fix", "skip", "stop"] and "proposed no command" in s["waiting"]["detail"]
    assert "DIAGNOSIS: needs-you" in s["waiting"]["detail"]
    assert init_actions.fix_commands("x\n```keel-fix\n# start it\n$ docker compose up -d db\n\nnpm ci\n```") == \
        ["docker compose up -d db", "npm ci"]
    assert init_actions.DESTRUCTIVE.search("rm -rf node_modules") and init_actions.DESTRUCTIVE.search("git reset --hard")
    assert not init_actions.DESTRUCTIVE.search("docker compose up -d")


# 0.18.63 a narrow fix that kept failing: the doctor proposed it again each round, and a plain approve took apply_fix
# each time (seven rounds). The doctor now sees the fixes keel ran; one that failed before is not offered again.
NARROW = "Rung 4 needs one package updated.\n```keel-fix\ntest -f __pycache__/never.flag\n```\nDIAGNOSIS: fixable"


def test_a_fix_that_failed_is_not_offered_again_and_a_plain_approve_stops(client, repo, monkeypatch):
    (Path(repo) / ".keel").mkdir(exist_ok=True)
    (Path(repo) / ".keel" / "config.yml").write_text(f"commands:\n  api_compile: 'test -f {FLAG}'\n  unit_tests: 'true'\n")
    seen = answers(monkeypatch, {"setup-doctor": lambda r: NARROW})
    tid = start(client, repo, workflow="init", title="keel init", settings={**SETTINGS, "simulate_checks": False})
    s = to(client, tid, wait(client, tid), "rung_gate")
    assert s["waiting"]["choices"][0] == "apply_fix"
    s = decide(client, tid, "approve", payload={"choice": "apply_fix"})
    w = s["waiting"]
    assert w["step"] == "rung_gate" and "apply_fix" not in w["choices"] and w["choices"][0] == "stop"
    assert "## keel stops the loop" in w["detail"] and "already ran, and it failed" in w["detail"]
    doctors = [r for r in seen if r.agent == "setup-doctor"]
    tried = doctors[-1].item["fixes_tried"]
    assert len(doctors) == 2 and tried[0]["commands"] == ["test -f __pycache__/never.flag"] and not tried[0]["ok"]
    assert "exit 1" in tried[0]["log"] and doctors[-1].item["runs_in"] == "on this machine, in keel's own shell"
    assert "never propose one of them again" in doctors[-1].prompt
    s = decide(client, tid, "approve")
    assert s["status"] != "waiting" and any(line.startswith("gate rung_gate stop") for line in s["gate_log"])


def test_after_three_failed_fixes_the_gate_offers_no_apply_fix(tmp_path):
    from keel_engine.runtime import init_actions
    from keel_engine.runtime.actions import ActionInput

    tried = [{"commands": [f"fix {i}"], "key": f"fix {i}", "ok": False} for i in range(init_actions.FIX_ROUNDS)]
    a = ActionInput(root=str(tmp_path), phase="setup", title="keel init", ac=None, acs=[], fake=True, flow="init",
                    data={"rung_question": {"n": 6, "name": "unit tests", "cmd": "x"}, "rung_fixes_tried": tried,
                          "doctor_results": [{"text": "```keel-fix\nsomething new\n```", "markers": {"DIAGNOSIS": "fixable"}}]})
    data = init_actions._rung_report(a).update["data"]
    assert data["rung_gate_without"] == ["apply_fix"] and data["rung_gate_order"] == ["stop"]
    assert "3 fixes failed on this rung" in data["rung_gate_detail"]
    a.data["rung_fixes_tried"] = tried[:1]
    data = init_actions._rung_report(a).update["data"]
    assert data["rung_gate_without"] == [] and data["rung_gate_order"] == []
    assert init_actions.own_commands({"why": "$ composer update -W\n# then\n\nphp -v"}) == ["composer update -W", "php -v"]
    assert init_actions.own_commands({"why": "x", "payload": {"commands": ["a", "b"]}}) == ["a", "b"]


def _devcontainer(root: Path, body: str) -> None:
    (root / ".devcontainer").mkdir(exist_ok=True)
    (root / ".devcontainer" / "devcontainer.json").write_text(body)


def test_the_fix_runs_where_init_says_the_tools_live(tmp_path):
    # 0.18.63 runs_on "dev container" was kept but never used: the rungs and the doctor's fix ran on keel's side
    from keel_engine.runtime import container, init_actions, init_gates
    from keel_engine.runtime.actions import ActionInput

    a = ActionInput(root=str(tmp_path), phase="setup", title="keel init", ac=None, acs=[], fake=True, flow="init",
                    data={}, init={"runs_on": "dev container"})
    log, ok = init_actions._run_fix(a, ["composer update -W"])
    assert not ok and "no .devcontainer/devcontainer.json" in log
    _devcontainer(tmp_path, '{\n  // the app service\n  "dockerComposeFile": ["../compose.yml", "dev.yml"],\n'
                            '  "service": "php",\n  "workspaceFolder": "/app",\n}\n')
    prefix = "docker compose -f compose.yml -f .devcontainer/dev.yml exec -T php"
    assert container.devcontainer(str(tmp_path)) == {"prefix": prefix, "source": ".devcontainer/devcontainer.json"}
    log, ok = init_actions._run_fix(a, ["composer update -W", "php -v && composer validate", "docker compose up -d db"])
    assert ok and f"$ {prefix} composer update -W" in log
    assert f"$ {prefix} sh -c 'php -v && composer validate'" in log and "$ docker compose up -d db\n" in log
    assert "`" + prefix + " <command>`" in init_actions._runs_in(a)
    cmds = init_gates.answered_commands(str(tmp_path), {"runs_on": "dev container"},
                                        {"unit_tests": "vendor/bin/phpunit", "web_test_module": "npm test"})
    assert cmds == {"unit_tests": f"{prefix} vendor/bin/phpunit", "web_test_module": "npm test"}
    _devcontainer(tmp_path, '{"image": "php:8.5-cli"}')
    assert init_gates.run_prefix(str(tmp_path), {"runs_on": "dev container"}) == "devcontainer exec --workspace-folder ."
    assert init_gates.run_prefix(str(tmp_path), {"runs_on": "this machine"}) is None


def test_keel_simulate_checks_0_runs_the_rungs_on_the_fake_model(client, repo, monkeypatch):
    # 0.18.64 an e2e on the fake model (KEEL_FAKE=1) sees a real setup rung fail: the api has no simulate_checks setting
    (Path(repo) / ".keel").mkdir(exist_ok=True)
    (Path(repo) / ".keel" / "config.yml").write_text("commands:\n  api_compile: 'false'\n  unit_tests: 'true'\n")
    answers(monkeypatch, {"setup-doctor": lambda r: "Nothing to run.\nDIAGNOSIS: needs-you"})
    monkeypatch.setenv("KEEL_SIMULATE_CHECKS", "0")
    tid = start(client, repo, workflow="init", title="keel init", settings=SETTINGS)
    s = to(client, tid, wait(client, tid), "rung_gate")
    assert s["waiting"]["step"] == "rung_gate" and "Rung 4" in s["waiting"]["detail"]


def test_audit_now_can_start_a_hunt(client, repo, monkeypatch):
    answers(monkeypatch, {})
    tid = start(client, repo, workflow="init", title="keel init", settings=SETTINGS)
    s = to(client, tid, wait(client, tid), "audit_now")
    assert s["waiting"]["choices"] == ["skip", "hunt", "notes"] and "liveness only" in s["waiting"]["detail"]
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


def test_a_rung_says_when_the_folder_is_a_worktree_keel_cannot_see(tmp_path):
    # 0.18.17 inside keel's container a worktree's .git file points at a repository that is not mounted
    from keel_engine.tools import git

    root = tmp_path / "wt"
    (root / ".keel").mkdir(parents=True)
    (root / ".keel" / "config.yml").write_text("commands:\n  api_compile: 'git status'\n")
    gone = tmp_path / "main" / ".git" / "worktrees" / "wt"
    (root / ".git").write_text(f"gitdir: {gone}\n")
    want = (f"this folder is a git worktree; keel cannot see its repository at {gone}; "
            "run keel2 add <main repo> or keel2 restart with keel2 0.18.17")
    assert git.unseen_gitdir_hint(str(root)) == want

    def runner(_root, cmd):
        return 128, f"fatal: not a git repository: {gone}"
    ok, rungs = ladder.run(str(root), False, runner)
    rung = next(r for r in rungs if r["n"] == 4)
    assert not ok and rung["status"] == "fail" and rung["detail"].endswith(want)
    # the repository is there (keel2 mounted it): no hint
    gone.mkdir(parents=True)
    assert git.unseen_gitdir_hint(str(root)) is None
    (root / ".git").unlink()
    (root / ".git").mkdir()
    assert git.unseen_gitdir_hint(str(root)) is None


def test_not_a_git_repository_asks_first(client, repo, monkeypatch):
    # keel v1's blocking question: without git, init commits nothing and verdicts carry no commit
    import shutil

    shutil.rmtree(Path(repo) / ".git")
    answers(monkeypatch, {})
    tid = start(client, repo, workflow="init", title="keel init", settings={**SETTINGS, "run_mode": "auto"})
    s = wait(client, tid)
    w = s["waiting"]
    assert w["step"] == "git_repo" and w["choices"] == ["init", "retry", "go_on", "stop"]
    assert "not a git repository" in w["detail"] and "src" in w["detail"]
    assert [c["label"] for c in w["choice_info"]] == ["Make the first commit", "I did it, check again", "Go on without git",
                                                     "Stop init"]
    # a file that looks like a secret: no first commit, the gate asks again and names it
    (Path(repo) / ".env").write_text("TOKEN=x\n")
    s = decide(client, tid, "approve", payload={"choice": "init"})
    assert s["waiting"]["step"] == "git_repo" and ".env" in s["waiting"]["detail"] and "did not make the first commit" in s["waiting"]["detail"]
    (Path(repo) / ".env").unlink()
    s = decide(client, tid, "approve", payload={"choice": "init"})
    assert (Path(repo) / ".git").is_dir()
    s = to(client, tid, s, "audit_now")
    log = subprocess.run(["git", "log", "--format=%s"], cwd=repo, capture_output=True, text=True).stdout.splitlines()
    assert log[-1] == "chore: initial commit" and "chore(setup): keel init" in log[0]


def test_going_on_without_git_says_so_at_the_hand_over(client, repo, monkeypatch):
    import shutil

    shutil.rmtree(Path(repo) / ".git")
    answers(monkeypatch, {})
    tid = start(client, repo, workflow="init", title="keel init", settings={**SETTINGS, "fast": True})
    s = wait(client, tid)
    s = decide(client, tid, "approve", payload={"choice": "go_on"})
    s = to(client, tid, s, "audit_now")
    s = decide(client, tid, "approve", payload={"choice": "skip"})
    w = s["waiting"]
    assert w["step"] == "hand_over"
    assert not (Path(repo) / ".git").exists()
    assert "not a git repository with a commit, so nothing was committed" in w["detail"]
    assert "fast init" in w["detail"] and "Start the stack" in w["detail"]


def test_the_explorers_map_reaches_the_librarians_and_the_hand_over_says_what_next(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"explorer": lambda r: "- backend: python, src/scores (src/scores/__init__.py:1)\nMAP-END"})
    tid = start(client, repo, workflow="init", title="keel init", settings=SETTINGS)
    s = wait(client, tid)
    s = to(client, tid, s, "questions")
    s = decide(client, tid, payload={"answers": {"knowledge": ["conventions"]}})
    s = to(client, tid, s, "audit_now")
    explorer = next(r for r in seen if r.agent == "explorer")
    assert "At most 60 lines" in explorer.prompt and "MAP-END" in explorer.prompt
    librarians = [r for r in seen if r.agent == "librarian"]
    assert [r.section for r in librarians] == ["conventions"]
    assert "backend: python, src/scores" in librarians[0].prompt and "The explorer's map" in librarians[0].prompt
    s = decide(client, tid, "approve", payload={"choice": "skip"})
    w = s["waiting"]
    assert w["step"] == "hand_over"
    assert "Setup ladder:" in w["detail"] and "Knowledge: wrote conventions" in w["detail"]
    assert "Not checked:" in w["detail"] and "- feature:" in w["detail"] and "- fix:" in w["detail"]
    assert "fast init" not in w["detail"]


def test_an_unknown_architecture_follows_the_knowledge_base(tmp_path):
    """init's config said architecture unknown while its knowledge base named a style with high confidence: the
    knowledge check now writes that style to the config; a style a person set stays."""
    from keel_engine.runtime import init_actions
    root = tmp_path / "repo"
    (root / ".keel").mkdir(parents=True)
    (root / "docs" / "knowledge").mkdir(parents=True)
    cfg = root / ".keel" / "config.yml"
    cfg.write_text("version: 4\narchitecture: {style: unknown, confidence: low, source: detected}\n")
    (root / "docs" / "knowledge" / "architecture.md").write_text("# Architecture\n\nStyle: **layered** (high, surveyed) — at `abc`.\n")
    assert init_actions.arch_from_knowledge(str(root)).startswith("architecture: layered (high)")
    assert yaml.safe_load(cfg.read_text())["architecture"] == {"style": "layered", "confidence": "high", "source": "knowledge"}
    assert init_actions.arch_from_knowledge(str(root)) is None                    # known now: nothing more
    cfg.write_text("version: 4\narchitecture: {style: unknown, source: person}\n")
    assert init_actions.arch_from_knowledge(str(root)) is None                    # a person's choice stays
    cfg.write_text("version: 4\narchitecture: {style: unknown, confidence: low, source: detected}\n")
    (root / "docs" / "knowledge" / "architecture.md").write_text("Style: **{{ARCH_STYLE}}** (low, detected)\n")
    assert init_actions.arch_from_knowledge(str(root)) is None                    # no known style named
