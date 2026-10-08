"""POST /steps/explain (runtime/explain.py): what a step really does, per kind, with placeholders or a real thread's
state, and the action docs registry that must cover every action the engine dispatches."""

from conftest import start, to_loop
from keel_engine.runtime import action_docs
from keel_engine.workflows.templates import get_template


def explain(client, step_id, workflow="feature", **extra):
    wf = get_template(workflow).model_dump() if isinstance(workflow, str) else workflow
    r = client.post("/steps/explain", json={"workflow": wf, "step_id": step_id, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def bucket(out, name):
    return next(b for b in out["rules"]["buckets"] if b["bucket"] == name)


# ------------------------------------------------------------------ the docs registry

def test_every_dispatched_action_has_plain_words():
    names = action_docs.dispatch_names()
    for must in ("verify_red", "commit", "run:", "start_flow", "escalate_model", "hunt_take", "spec_freeze", "trace_strict"):
        assert must in names
    docs = action_docs.all_docs()
    missing = [n for n in names if n not in docs]
    assert not missing, f"actions without an entry in runtime/action_docs.py DOCS or their part's docs: {missing}"
    empty = [n for n in names if not action_docs.describe(n)["steps"]]
    assert not empty, f"actions with neither steps in DOCS nor a docstring: {empty}"
    stale = [n for n in docs if n not in names]
    assert not stale, f"DOCS entries for actions the engine no longer runs: {stale}"
    assert {"db:query", "git:push"} <= set(names)                # the parts' actions (keel_engine/extensions.py)


def test_a_docstring_fills_an_entry_without_steps():
    d = action_docs.describe("spec_freeze")
    assert d["known"] and "frozen" in d["steps"][0]
    assert action_docs.describe("nope")["known"] is False
    assert action_docs.describe("run:make lint")["command"] == "make lint"


# ------------------------------------------------------------------ agent

def test_red_agent_rules_loop_and_the_prompt_with_placeholders(client, repo):
    out = explain(client, "red", root=str(repo))
    assert out["kind"] == "agent" and out["phase"] == "red" and not out["thread"]
    assert "failing test" in out["phase_meaning"]
    assert bucket(out, "api-test")["may"] == "edit"
    assert bucket(out, "api-main")["may"] == "read-only"
    assert bucket(out, "protected-env")["may"] == "no-access"
    assert any("git commit" in x for x in out["rules"]["shell_refused"])
    assert out["rules"]["commit"]["prefix"] == "test(AC-n)" and "api-main" in out["rules"]["commit"]["may_not_contain"]
    assert out["rules"]["lane_scoped"]
    assert out["loop"]["kind"] == "per_ac" and out["loop"]["last"] == "AC gate"
    assert out["next"][0]["to"] == "red_amend"
    a = out["agent"]
    assert a["id"] == "test-author" and a["model"]["agent_file"] == "opus" and "Agents" in a["model"]["rule"]
    assert a["markers"][0]["name"] == "AMEND" and "AMEND:" in a["instructions"]
    assert a["placeholders"] and "«the current criterion»" in a["prompt"]
    assert "Step: red (keel phase: red)" in a["prompt"]
    assert "Write the failing test for the current criterion only" in a["prompt"]
    assert "end your answer with one line AMEND" in a["prompt"]
    assert "python -m pytest" in a["prompt"]                     # the demo's real test command
    assert "failing tests" in a["role"]


def test_a_fan_out_and_data_in_the_instructions(client, repo):
    out = explain(client, "explore", root=str(repo))
    assert out["kind"] == "parallel" and out["loop"]["fan_out"]["from"] == "explore_areas"
    assert out["runs_only_when"] == "acs is set"
    plan = explain(client, "plan", root=str(repo))["agent"]
    assert "«data.report: filled in from the flow state when the step runs»" in plan["prompt"]
    assert "ORDER: <one line>" in plan["prompt"]


def test_a_reviewer_says_where_its_findings_go(client):
    out = explain(client, "code_review")
    f = next(r for r in out["next"] if r["label"] == "blocking findings")
    assert f["to"] == "review_fix" and "2 round(s)" in f["text"]


# ------------------------------------------------------------------ code

def test_commit_contract_in_words(client, repo):
    out = explain(client, "contract_commit", root=str(repo))
    assert out["name"] == "commit contract" and out["kind"] == "code"
    act = out["code"]["actions"][0]
    assert act["name"] == "commit" and any("secret" in s for s in act["steps"])
    assert any("Unstages your own edits" in s for s in act["steps"])
    assert act["commit"]["type"] == "contract" and act["commit"]["message"] == "contract: «the flow's title»"
    assert "contract commit" in act["for_this_step"]
    passes, fails = out["next"]
    assert passes["to"] == "red" and "first criterion" in passes["text"]
    assert fails["to"] == "contract"


def test_a_chained_code_step_and_its_loop_end(client, repo):
    out = explain(client, "verify_red", root=str(repo))
    assert out["code"]["chain"] == "verify_red → commit"
    red, commit = out["code"]["actions"]
    assert "python -m pytest" in red["for_this_step"] and commit["after"] == "runs only when verify_red passed"
    assert out["lock"]
    gate = explain(client, "ac_gate")
    nxt = gate["gate"]["answers"][0]
    assert nxt["label"] == "approve" and "next criterion" in nxt["text"] and "integration" in nxt["text"]


def test_a_run_command_is_checked_against_the_phase(client):
    wf = {"name": "w", "steps": [{"id": "lint", "kind": "code", "name": "lint", "action": "run:git commit -m x", "phase": "green"}]}
    out = explain(client, "lint", workflow=wf)
    assert "refused in phase green" in out["code"]["actions"][0]["for_this_step"]


# ------------------------------------------------------------------ gate, branch, include

def test_a_gate_with_named_exits(client):
    out = explain(client, "spec_gate")
    exits = {a["label"]: a["to_name"] for a in out["gate"]["answers"]}
    assert exits["approve"] == "freeze + commit the spec" and exits["reject"] == "the interview starts again"
    assert any("keel rule" in n for n in out["gate"]["notes"])
    ac = explain(client, "ac_gate")["gate"]
    back = next(a for a in ac["answers"] if a["label"] == "send back")
    assert back["to"] == "red" and "todo again" in back["text"]
    assert any("gates_mode" in n for n in ac["notes"])


def test_a_branch_in_words_and_an_included_step(client):
    out = explain(client, "red_amend")
    assert out["branch"]["condition"] == "marker AMEND from red is set"
    yes, no = out["branch"]["routes"]
    assert yes["to"] == "red_to_amend" and no["to"] == "verify_red"
    inc = explain(client, "ship_audit")
    assert inc["included_from"]["flow"] == "ship" and inc["included_from"]["step"] == "audit"


def test_unknown_step_and_missing_workflow(client):
    r = client.post("/steps/explain", json={"workflow": get_template("feature").model_dump(), "step_id": "nope"})
    assert r.status_code == 404
    assert client.post("/steps/explain", json={"step_id": "red"}).status_code == 400
    assert client.post("/steps/explain", json={"step_id": "red", "thread_id": "missing"}).status_code == 404


# ------------------------------------------------------------------ a real thread

def test_a_real_thread_fills_the_prompt_and_shows_the_last_runs(client, repo):
    tid = start(client, repo)
    s = to_loop(client, tid)
    assert s["waiting"]["step"] == "ac_gate"
    r = client.post("/steps/explain", json={"step_id": "red", "thread_id": tid})
    assert r.status_code == 200, r.text
    out = r.json()
    a = out["agent"]
    assert out["thread"] and not a["placeholders"]
    assert "Flow: Player ranks" in a["prompt"] and "Current criterion: AC-1 [API]" in a["prompt"]
    assert "Spec: docs/specs/player-ranks.md" in a["prompt"] and "«" not in a["prompt"]
    assert a["model"]["now"]["provider"] == "fake"
    run = out["last_runs"]["runs"][0]
    assert out["last_runs"]["count"] == 1 and run["ok"] and run["ac"] == "AC-1"
    assert run["answer"].startswith("RED:") and run["tokens"]["in"] > 0

    code = client.post("/steps/explain", json={"step_id": "verify_red", "thread_id": tid}).json()
    run = code["last_runs"]["runs"][0]
    assert run["commit"]["subject"] == "test(AC-1): Player ranks: the main case works"
    assert "AssertionError" in run["output"] and run["went_to"] == "green"

    gate = client.post("/steps/explain", json={"step_id": "ac_gate", "thread_id": tid}).json()
    assert gate["last_runs"]["now"] == "waiting for you here" and gate["last_runs"]["count"] == 0
    spec_gate = client.post("/steps/explain", json={"step_id": "spec_gate", "thread_id": tid}).json()
    assert spec_gate["last_runs"]["runs"][0]["decided"] == ["gate spec_gate approve"]


def test_the_lint_flow_explains_its_phase_and_its_actions(client):
    fix = explain(client, "fix", workflow="lint")
    assert fix["phase"] == "lint-fix" and "static checks" in fix["phase_meaning"]
    assert bucket(fix, "migration")["may"] == "read-only" and bucket(fix, "api-main")["may"] == "edit"
    assert fix["rules"]["commit"]["prefix"] == "chore(lint)"
    run = explain(client, "run", workflow="lint")
    assert run["code"]["actions"][0]["name"] == "lint_run" and "fixers first" in run["code"]["actions"][0]["summary"]
