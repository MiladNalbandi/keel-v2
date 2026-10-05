"""v0.4.1 run modes: the policy (runtime/run_mode.py), each mode at each gate kind, the safety stops, readonly's guard,
changing the mode during a run, and what auto mode leaves in the gate log, the final review and the PR body."""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import decide, start, wait
from keel_engine import hook
from keel_engine.models import fake as fake_mod
from keel_engine.runtime import guard_ctx, run_mode, ship
from keel_engine.runtime.actions import ActionInput, commit
from keel_engine.runtime.verdict_actions import _open_pr, pr_body
from keel_engine.tools import guard
from keel_engine.tools.agent_tools import ToolBox
from keel_engine.workflows.model import from_dict
from keel_engine.workflows.templates import get_template

SETTINGS = {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}


def wf(steps, name="custom"):
    return from_dict({"name": name, "keel_rules": False, "steps": steps})


def mode_settings(mode, **extra):
    return {**SETTINGS, "run_mode": mode, **extra}


def values(client, tid) -> dict:
    eng = client.app.state.engine

    async def get():
        return (await (await eng._graph(tid)).aget_state(eng._cfg(tid))).values

    return client.portal.call(get)


def answers(monkeypatch, by_agent):
    real = fake_mod._plan

    def plan(req):
        if req.agent in by_agent:
            return None, "", by_agent[req.agent](req), {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)


def gates_until_done(client, tid, s):
    seen = []
    for _ in range(60):
        if s["status"] != "waiting":
            return s, seen
        seen.append(s["waiting"]["step"])
        s = decide(client, tid)
    raise AssertionError("too many gates")


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True)


# ------------------------------------------------------------------ the policy table

GATE_KINDS = sorted(run_mode.AUTO_KINDS)
SAFETY_KINDS = sorted(run_mode.SAFETY)
Q = {"step": "g", "kind": "gate", "title": "a gate", "options": ["approve", "reject"]}


@pytest.mark.parametrize("kind", GATE_KINDS + SAFETY_KINDS)
@pytest.mark.parametrize("mode", ["manual", "readonly"])
def test_manual_and_readonly_ask_at_every_kind(mode, kind):
    assert run_mode.decide(mode, kind, Q, {}) is None


@pytest.mark.parametrize("kind", GATE_KINDS)
def test_auto_approves_every_gate_kind_it_can_decide(kind):
    a = run_mode.decide("auto", kind, Q, {})
    assert a == {"decision": "approve", "why": "auto-approved (mode auto)", "payload": {"answers": {}} if kind == "clarify" else {},
                 "auto": True, "kind": kind}


@pytest.mark.parametrize("mode", ["important", "auto"])
@pytest.mark.parametrize("kind", SAFETY_KINDS)
def test_safety_stops_ask_in_every_mode(mode, kind):
    assert run_mode.decide(mode, kind, Q, {}) is None


@pytest.mark.parametrize("kind", [k for k in GATE_KINDS if k != "ac"])
def test_important_asks_at_everything_but_a_clean_ac_gate(kind):
    assert run_mode.decide("important", kind, Q, {}) is None
    assert run_mode.decide("important", "ac", Q, {}, review="AC-REVIEW: PASS")["why"] == "auto-approved (mode important)"


def test_important_ac_gate_needs_passing_checks_and_a_clean_review():
    assert run_mode.decide("important", "ac", Q, {}, review=None)            # no review step: the checks decide
    assert run_mode.decide("important", "ac", Q, {"last_failure": "tests fail"}) is None
    assert run_mode.decide("important", "ac", Q, {}, review="Missing edge case.\nAC-REVIEW: findings") is None
    assert run_mode.decide("important", "ac", Q, {}, review="## Blocking\n- `a.py:3` divides by zero") is None
    assert run_mode.decide("important", "ac", Q, {}, review="## Blocking\nnone\n\nAC-REVIEW: pass")
    # auto does not read the review: it approves what it can (the final review lists it)
    assert run_mode.decide("auto", "ac", Q, {}, review="AC-REVIEW: findings")


def test_auto_never_approves_what_only_a_send_back_can_answer_and_stops_repeating():
    assert run_mode.decide("auto", "spec", {**Q, "options": ["reject"]}, {}) is None
    assert run_mode.decide("auto", "gate", Q, {}, repeats=run_mode.MAX_REPEATS - 1)
    assert run_mode.decide("auto", "gate", Q, {}, repeats=run_mode.MAX_REPEATS) is None


def test_auto_answers_the_explorers_questions_with_the_recommended_options():
    q = {"step": "spec_gate", "kind": "clarify", "title": "questions", "options": ["approve"], "questions": [
        {"id": "q1", "question": "Who?", "options": [{"label": "players", "recommended": True}, {"label": "admins"}]},
        {"id": "q2", "question": "Where?", "options": [{"label": "top"}, {"label": "side"}]}]}
    a = run_mode.decide("auto", run_mode.classify(q), q, {})
    assert a["payload"] == {"answers": {"q1": "players"}}
    assert run_mode.decide("important", "clarify", q, {}) is None


def test_classify_names_each_pause():
    t = get_template("feature")
    by = {s.id: s for s in t.steps}
    assert run_mode.classify({"kind": "budget"}) == "budget" and run_mode.classify({"kind": "usage"}) == "budget"
    assert run_mode.classify({"kind": "fix", "title": "Approve new dependency"}) == "dependency"
    assert run_mode.classify({"kind": "fix", "title": "verify keeps failing"}) == "failure"
    assert run_mode.classify({"kind": "clarify"}) == "clarify"
    assert run_mode.classify({"kind": "gate", "step": "ship_review__fix"}) == "findings"
    assert run_mode.classify({}, by["spec_gate"], flow="feature") == "spec"
    assert run_mode.classify({}, by["options"], flow="feature") == "skip-menu"
    assert run_mode.classify({}, by["contract_gate"], flow="feature") == "contract"
    assert run_mode.classify({}, by["ac_gate"], flow="feature", per_ac=True) == "ac"
    assert run_mode.classify({}, by["integration_gate"], flow="feature") == "gate"
    assert run_mode.classify({}, by["ship_final_review"], flow="feature") == "final-review"
    assert run_mode.classify({}, by["ship_pr_gate"], flow="feature", prev_actions=["pr"]) == "pr"
    fix = {s.id: s for s in get_template("fix").steps}
    assert run_mode.classify({}, fix["gate_r"], flow="fix") == "choice"
    init = {s.id: s for s in get_template("init").steps}
    assert run_mode.classify({}, init["rung_gate"], flow="init") == "rung"
    nxt = {s.id: s for s in get_template("hunt-next").steps}
    assert run_mode.classify({}, nxt["close_gate"], flow="hunt-next") == "note"


# ------------------------------------------------------------------ whole flows in each mode

def test_auto_runs_the_feature_flow_to_done_and_logs_every_gate(client, repo):
    tid = start(client, repo, settings=mode_settings("auto"))
    s = wait(client, tid)
    assert s["status"] == "done", s
    assert s["run_mode"] == "auto"
    auto = [line for line in s["gate_log"] if "auto-approved (mode auto)" in line]
    for gate in ("gate spec_gate approve", "gate options approve", "gate contract_gate approve", "ac AC-1 approve",
                 "ac AC-2 approve", "gate integration_gate approve", "gate ship_plan approve", "gate ship_final_review approve",
                 "gate ship_pr_gate approve"):
        assert any(line.startswith(gate) for line in auto), (gate, s["gate_log"])
    assert not client.bus.of(tid, "gate.waiting")
    decided = client.bus.of(tid, "gate.decided")
    assert decided and all(e["data"]["why"] == "auto-approved (mode auto)" for e in decided if e["data"].get("gate") != "unlock")
    v = values(client, tid)
    # the PR body lists the gates keel approved by itself; open_pr opened nothing
    assert "## Auto-approved gates" in v["pr_body"] and "Run mode: auto." in v["pr_body"]
    assert "ac AC-1 approve: auto-approved (mode auto)" in v["pr_body"]
    notes = [e["data"].get("note") or "" for e in client.bus.of(tid, "step.finished") if e["step"] == "ship_open_pr"]
    assert any("Run mode auto: keel opens no PR by itself" in n for n in notes), notes
    # the final review's exceptions name them too
    report = ship.final_report(str(repo), "demo", v, "Player ranks")
    assert "- gate auto-approved: ac AC-2 approve: auto-approved (mode auto)" in report


def test_important_stops_at_the_big_gates_and_approves_clean_ac_gates(client, repo):
    tid = start(client, repo, settings=mode_settings("important"))
    s, seen = gates_until_done(client, tid, wait(client, tid))
    assert s["status"] == "done", s
    assert seen == ["spec_gate", "options", "contract_gate", "integration_gate", "ship_plan", "ship_final_review", "ship_pr_gate"]
    assert "ac AC-1 approve: auto-approved (mode important)" in s["gate_log"]
    assert "ac AC-2 approve: auto-approved (mode important)" in s["gate_log"]
    assert not any("auto-approved" in line for line in s["gate_log"] if line.startswith("gate "))


def test_important_stops_at_an_ac_gate_whose_review_found_something(client, repo, monkeypatch):
    answers(monkeypatch, {"ac-reviewer": lambda r: f"The test asserts nothing for {r.ac['id']}.\nAC-REVIEW: findings"
                          if r.ac["id"] == "AC-1" else "AC-REVIEW: pass"})
    tid = start(client, repo, settings=mode_settings("important"))
    s = wait(client, tid)
    for step in ("spec_gate", "options", "contract_gate"):
        assert s["waiting"]["step"] == step, s
        s = decide(client, tid)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-1"          # findings: the user decides
    s = decide(client, tid)
    assert s["waiting"]["step"] == "integration_gate"                       # AC-2 was clean: approved by itself
    assert "ac AC-2 approve: auto-approved (mode important)" in s["gate_log"]
    assert any(line == "ac AC-1 approve" for line in s["gate_log"])


def test_changing_the_mode_mid_run_keeps_the_waiting_question_for_the_user(client, repo):
    tid = start(client, repo, settings=mode_settings("manual"))
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate" and s["run_mode"] == "manual" and s["waiting"]["id"]
    r = client.post(f"/threads/{tid}/mode", json={"mode": "auto"})
    assert r.status_code == 200 and r.json()["run_mode"] == "auto"
    assert r.json()["status"] == "waiting" and r.json()["waiting"]["step"] == "spec_gate"    # from the next gate on
    assert client.bus.of(tid, "thread.mode")[-1]["data"] == {"mode": "auto", "from": "manual"}
    # the user's send-back wins over the new mode; every later gate is keel's
    s = decide(client, tid, "reject", why="say what a tie does")
    assert s["status"] == "done", s
    log = s["gate_log"]
    assert log[0] == "gate spec_gate reject: say what a tie does"
    assert "gate spec_gate approve: auto-approved (mode auto)" in log[1:]
    # the mode is stored with the thread: a fresh context (an engine restart) still has it
    eng = client.app.state.engine
    eng.ctxs.pop(tid)
    assert client.get(f"/threads/{tid}").json()["run_mode"] == "auto"


def test_switching_back_to_manual_stops_at_the_next_gate(client, repo):
    tid = start(client, repo, settings=mode_settings("important"))
    s = wait(client, tid)
    assert client.post(f"/threads/{tid}/mode", json={"mode": "manual"}).json()["run_mode"] == "manual"
    for step in ("spec_gate", "options", "contract_gate"):
        assert s["waiting"]["step"] == step, s
        s = decide(client, tid)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-1"


def test_mode_route_and_start_validate_the_mode(client, repo):
    tid = start(client, repo)
    assert wait(client, tid)["run_mode"] == "manual"                       # the default
    assert client.post(f"/threads/{tid}/mode", json={"mode": "yolo"}).status_code == 400
    assert client.post("/threads/nope/mode", json={"mode": "auto"}).status_code == 404
    body = {"project_id": "demo", "root": str(repo), "workflow": get_template("fix").model_dump(), "title": "x",
            "settings": {"run_mode": "fast"}}
    assert client.post("/threads", json=body).status_code == 400


# ------------------------------------------------------------------ safety stops in auto mode

def test_auto_stops_at_the_token_cap(client, repo):
    tid = start(client, repo, settings=mode_settings("auto", cap_tokens=180_000))
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["kind"] == "budget", s
    assert any("auto-approved (mode auto)" in line for line in s["gate_log"])   # the gates before it were keel's


def test_auto_stops_for_a_new_dependency(client, repo):
    w = wf([{"id": "add", "kind": "code", "name": "add dep", "phase": "green", "action": "run:printf 'httpx = \">=0.27\"\\n' >> pyproject.toml"},
            {"id": "commit", "kind": "code", "name": "commit", "action": "commit", "phase": "green"}])
    tid = start(client, repo, workflow=w, settings=mode_settings("auto"))
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["title"] == "Approve new dependency"
    assert s["waiting"]["labels"] == {"approve": "Allow", "reject": "Refuse"}


def test_every_mode_stops_for_a_secret_and_reject_stops_the_flow(client, repo):
    token = "ghp_" + "a" * 36
    w = wf([{"id": "leak", "kind": "code", "name": "write a config", "phase": "green",
             "action": f"run:printf 'TOKEN = \"{token}\"\\n' > src/scores/cfg.py"},
            {"id": "commit", "kind": "code", "name": "commit", "action": "commit", "phase": "green"}])
    for mode in ("auto", "manual"):
        git(repo, "checkout", "-q", "--", ".")
        (Path(repo) / "src/scores/cfg.py").unlink(missing_ok=True)
        tid = start(client, repo, workflow=w, settings=mode_settings(mode))
        s = wait(client, tid)
        assert s["status"] == "waiting" and s["waiting"]["title"] == "A secret is staged for commit", (mode, s)
        assert s["waiting"]["kind"] == "fix" and "GitHub token" in s["waiting"]["detail"]
        s = decide(client, tid, "reject", why="I will rotate it")
        assert s["status"] == "stopped" and "secret" in s["error"]
        assert "secrets reject: I will rotate it" in s["gate_log"]
    assert "ghp_" not in git(repo, "log", "-p", "-3").stdout


def test_auto_stops_when_a_check_keeps_failing_and_when_a_loop_is_past_its_rounds(client, repo, monkeypatch):
    w = wf([{"id": "check", "kind": "code", "name": "check", "action": "run:false"}])
    tid = start(client, repo, workflow=w, settings=mode_settings("auto", fix_attempts=0))
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["kind"] == "fix" and "keeps failing" in s["waiting"]["title"]

    answers(monkeypatch, {"explorer": lambda r: "Not yet.\nREADY: no"})
    w = wf([{"id": "look", "kind": "agent", "name": "look", "agent": "explorer", "markers": ["READY"]},
            {"id": "ready", "kind": "branch", "name": "ready", "when": {"marker": "READY", "equals": "yes"}, "no": "look", "rounds": 2}])
    tid = start(client, repo, workflow=w, settings=mode_settings("auto"))
    s = wait(client, tid)
    assert s["status"] == "waiting" and "still no after 2 round(s)" in s["waiting"]["title"]


def test_auto_approves_a_repeating_gate_at_most_three_times(client, repo):
    w = wf([{"id": "work", "kind": "code", "name": "work", "action": "run:true"},
            {"id": "g", "kind": "gate", "name": "again", "choices": {"again": "work", "done": "end"}}])
    tid = start(client, repo, workflow=w, settings=mode_settings("auto"))
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "g"
    assert sum("auto-approved" in line for line in s["gate_log"]) == run_mode.MAX_REPEATS
    s = decide(client, tid, payload={"choice": "done"})
    assert s["status"] == "done"


# ------------------------------------------------------------------ open_pr and the PR body

def test_open_pr_refuses_in_auto_mode_and_after_an_automatic_approval(repo):
    def ai(**kw):
        return ActionInput(root=str(repo), phase="close", title="t", ac=None, acs=[], fake=True, flow="feature",
                           keys={"github": "x"}, **kw)
    r = _open_pr(ai(state={"pr_body": "# PR", "pr_approved": True}, settings={"run_mode": "auto"}))
    assert r.ok and "Run mode auto: keel opens no PR by itself" in r.note and r.detail == "# PR"
    r = _open_pr(ai(state={"pr_body": "# PR", "pr_approved": True, "pr_auto": True}, settings={"run_mode": "manual"}))
    assert "Run mode auto" in r.note
    r = _open_pr(ai(state={"pr_body": "# PR", "pr_approved": True}, settings={}))
    assert "Run mode auto" not in r.note                     # manual: the usual checks (here: not pushed)


def test_pr_body_lists_auto_approved_gates():
    state = {"gates": {"log": ["gate spec_gate approve", "ac AC-1 approve: auto-approved (mode important)"]}}
    body = pr_body("/nonexistent", "p", state, "t", None, run_mode="important")
    assert "## Auto-approved gates" in body and "- ac AC-1 approve: auto-approved (mode important)" in body
    assert "gate spec_gate approve\n" not in body.split("## Auto-approved gates")[1]
    assert "## Auto-approved gates" not in pr_body("/nonexistent", "p", {"gates": {"log": []}}, "t", None, run_mode="manual")


# ------------------------------------------------------------------ readonly

def test_readonly_toolbox_refuses_writes_and_changing_commands(repo):
    seen = []
    tb = ToolBox(str(repo), "green", on_refuse=lambda *a: seen.append(a), readonly=True)
    assert tb.write_file("src/scores/new.py", "x = 1\n").startswith("REFUSED: This flow runs read-only")
    assert not (Path(repo) / "src/scores/new.py").exists()
    for cmd in ("echo x > notes.txt", "rm -rf src", "git commit -am x", "git add -A", "sed -i s/a/b/ README.md",
                "npm install left-pad", "mv a b"):
        assert tb.run_command(cmd).startswith("REFUSED: This flow runs read-only"), cmd
    assert tb.run_command("ls src").startswith("exit 0")
    assert tb.run_command("grep -r run src > /dev/null; echo ok").startswith("exit")
    assert len(seen) == 8
    assert ToolBox(str(repo), "green", readonly=False).write_file("src/scores/ok.py", "x = 1\n").startswith("Wrote")


def test_readonly_hook_denies_every_edit_and_commit(repo, tmp_path):
    ctx = {"root": str(repo), "phase": "green", "ac": None, "lane": None, "unlocks": [{"path": "README.md", "phase": "green"}],
           "agent": "implementer", "thread": "", "readonly": True}
    deny = lambda tool, **ti: hook.decide(tool, ti, ctx)                                            # noqa: E731
    assert deny("Edit", file_path=str(Path(repo) / "src/scores/__init__.py")) == run_mode.READONLY_EDIT
    assert deny("Write", file_path=str(Path(repo) / "README.md")) == run_mode.READONLY_EDIT     # an unlock does not open it
    assert deny("MultiEdit") == run_mode.READONLY_EDIT
    assert "read-only" in deny("Bash", command="git commit -m x")
    assert "read-only" in deny("Bash", command="cat README.md > copy.md")
    assert deny("mcp__serena__replace_symbol_body", relative_path="src/scores/__init__.py") == run_mode.READONLY_MCP
    (Path(repo) / ".keel").mkdir(exist_ok=True)
    (Path(repo) / ".keel/config.yml").write_text("version: 4\nmcp:\n  allow: [write_note]\n")
    assert deny("mcp__notes__write_note") == run_mode.READONLY_MCP                                 # mcp.allow does not open it
    assert deny("Bash", command="ls src") is None and deny("Read", file_path=str(Path(repo) / "README.md")) is None
    assert hook.decide("Edit", {"file_path": str(Path(repo) / "src/scores/__init__.py")}, {**ctx, "readonly": False}) is None
    # the same through the real hook process: exit 2 with the reason
    path = guard_ctx.write_context(tmp_path / "guard.json", **ctx)
    code = hook.pre_tool(json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(Path(repo) / "x.py")}}), path)
    assert code == 2


def test_readonly_reaches_the_guard_context_and_the_diff_guard_puts_everything_back(repo):
    class Req:
        root, phase, ac, agent, thread, knowledge = str(repo), "green", None, "implementer", "t", None
        toolbox = ToolBox(str(repo), "green", readonly=True)
    assert guard_ctx.context_for(Req())["readonly"] is True
    before = guard.snapshot(str(repo))
    (Path(repo) / "src/scores/allowed.py").write_text("x = 1\n")            # green may write production code
    assert guard.guard_diff(str(repo), "green", before) == []
    before = guard.snapshot(str(repo))
    (Path(repo) / "src/scores/also.py").write_text("x = 2\n")
    put_back = guard.guard_diff(str(repo), "green", before, readonly=True)
    assert [p["path"] for p in put_back] == ["src/scores/also.py"] and "read-only" in put_back[0]["reason"]
    assert not (Path(repo) / "src/scores/also.py").exists()


def test_readonly_flow_reverts_the_agents_edits_and_the_commit_asks(client, repo):
    w = wf([{"id": "green", "kind": "agent", "name": "green", "agent": "implementer", "phase": "green"},
            {"id": "commit", "kind": "code", "name": "commit", "action": "commit", "phase": "green"}])
    head = git(repo, "rev-parse", "HEAD").stdout
    tid = start(client, repo, workflow=w, settings=mode_settings("readonly"))
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["title"] == "Read-only run: the commit is refused", s
    assert s["waiting"]["labels"]["approve"] == "Try the commit again"
    refused = client.bus.of(tid, "guard.refused")
    assert refused and all("read-only" in e["data"]["reason"] for e in refused)
    assert git(repo, "status", "--porcelain").stdout.strip() == ""            # nothing the agent wrote is left
    s = decide(client, tid)                                                     # still readonly: asks again
    assert s["waiting"]["title"] == "Read-only run: the commit is refused"
    client.post(f"/threads/{tid}/mode", json={"mode": "manual"})
    s = decide(client, tid)
    assert s["status"] == "done", s
    assert git(repo, "rev-parse", "HEAD").stdout == head                        # nothing was there to commit
    tid = start(client, repo, workflow=w, settings=mode_settings("readonly"))
    s = decide(client, tid, "reject", why="read-only on purpose") if wait(client, tid)["status"] == "waiting" else None
    assert s["status"] == "stopped"


def test_readonly_commit_refuses_before_staging(repo):
    (Path(repo) / "src/scores/x.py").write_text("x = 1\n")
    r = commit(ActionInput(root=str(repo), phase="green", title="t", ac=None, acs=[], fake=True, flow="feature",
                           settings={"run_mode": "readonly"}))
    assert not r.ok and r.ask["type"] == "readonly" and r.ask["kind"] == "fix"
    assert git(repo, "diff", "--cached", "--name-only").stdout == ""
