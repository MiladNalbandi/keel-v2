"""v0.17.4: the criteria's build order (the spec's ORDER line), and the amendment loop guard (a real run looped
red → AMEND → an amendment that changed nothing → red, four times)."""

from conftest import decide, start, to_loop, wait
from test_flows_base import values
from test_flows_feature import by_step, spec_with, text
from keel_engine import rules
from keel_engine.runtime import feature_actions as fa

NOT_A_DEFECT = "The spec is right; AC-1 needs the ranking table AC-2 builds.\nREOPEN: none — execution-order issue, not a spec defect\nCONTRACT: no"


def test_the_spec_order_line_is_parsed():
    assert fa.spec_order("## Plan\n\n**ORDER**: AC-006, AC-002, AC-007, AC-001, …\n") == "AC-006, AC-002, AC-007, AC-001, …"
    assert fa.spec_order("- **Order:** AC-2, AC-1") == "AC-2, AC-1"
    assert fa.spec_order("ORDER: AC-3\n...\nORDER: AC-2, AC-1\n") == "AC-2, AC-1"            # the last one wins
    assert fa.spec_order("Order: the user decides") is None
    acs = [{"id": f"AC-00{i}"} for i in (1, 2, 3, 6, 7)]
    assert [x["id"] for x in fa.order_acs(acs, "AC-006, AC-002, AC-007, AC-001, …")] == ["AC-006", "AC-002", "AC-007", "AC-001", "AC-003"]
    assert fa.move_before([{"id": "A-1"}, {"id": "A-2"}, {"id": "A-3"}], ["A-3"], "A-1") == [{"id": "A-3"}, {"id": "A-1"}, {"id": "A-2"}]


def test_gates_follow_the_build_order():
    acs = [{"id": "AC-2", "layer": "API"}, {"id": "AC-1", "layer": "API"}]
    assert rules.gate_due("end", acs, "AC-1")["due"] is True           # AC-1 is built last
    assert rules.gate_due("end", acs, "AC-2")["due"] is False


def test_the_spec_order_line_decides_the_loop(client, repo, monkeypatch):
    spec_with(monkeypatch, "**ORDER**: AC-2, AC-1")
    by_step(monkeypatch, {})                         # the plan agent's own marker says AC-1, AC-2: the spec wins
    tid = start(client, repo)
    s = wait(client, tid)
    assert "Criteria, in build order (from the spec's ORDER line):" in s["waiting"]["detail"]
    s = to_loop(client, tid, s)
    assert s["ac"] == "AC-2" and [a["id"] for a in s["acs"]] == ["AC-2", "AC-1"]
    assert values(client, tid)["data"]["ac_order"] == {"ids": ["AC-2", "AC-1"], "from": "spec"}


def red_amends(monkeypatch, explorer_says, always=False):
    reds, amends = [], []

    def red(req, real):
        reds.append(req.ac["id"])
        if req.ac["id"] == "AC-1" and (always or reds.count("AC-1") == 1):
            return None, "", "No seam to attach a test.\nAMEND: AC-1 needs the ranking table from AC-2", {}
        return real(req)

    def amend(req, real):
        amends.append(req)
        return None, "", explorer_says, {}
    by_step(monkeypatch, {"red": red, "spec amendment": amend})
    return reds, amends


def test_an_order_question_reorders_by_itself(client, repo, monkeypatch):
    reds, amends = red_amends(monkeypatch, NOT_A_DEFECT)
    tid = start(client, repo)
    s = to_loop(client, tid)
    assert s["ac"] == "AC-1" or s["waiting"]["step"] != "amend_gate"
    for _ in range(6):                               # no amend_gate on the way: it would commit nothing
        assert s["waiting"]["step"] not in ("amend_gate", "amend_loop"), s["waiting"]
        if s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-2":
            break
        s = decide(client, tid)
    assert s["waiting"]["step"] == "ac_gate" and s["ac"] == "AC-2"
    assert [a["id"] for a in s["acs"]] == ["AC-2", "AC-1"] and len(amends) == 1
    assert any("keel moved AC-2 before AC-1" in x for x in s["gate_log"])
    assert values(client, tid)["data"]["ac_order"]["from"] == "keel"


def test_the_same_question_again_is_a_choice_not_a_loop(client, repo, monkeypatch):
    reds, amends = red_amends(monkeypatch, "Nothing to change: the behaviour is covered by AC-1 as written.\nREOPEN: none", always=True)
    tid = start(client, repo)
    s = to_loop(client, tid)
    for _ in range(6):
        if s["waiting"]["step"] == "amend_loop":
            break
        s = decide(client, tid)
    w = s["waiting"]
    assert w["step"] == "amend_loop" and w["choices"] == ["first", "skip", "edit", "stop"]
    assert "changed nothing in the spec" in w["detail"] and "- first: build AC-2" in w["detail"]
    assert len(amends) == 1                           # the explorer ran once; the guard asked instead of a second round
    # edit: the amendment again with the note; it changes nothing again, so the choice comes back
    s = decide(client, tid, payload={"choice": "edit"}, why="AC-1 should read the table through a port")
    assert len(amends) == 2 and "The person says: AC-1 should read the table through a port" in amends[1].prompt
    assert s["waiting"]["step"] == "amend_loop"
    # first: AC-2 is built before AC-1
    s = decide(client, tid, payload={"choice": "first"})
    assert s["ac"] == "AC-2" and [a["id"] for a in s["acs"]] == ["AC-2", "AC-1"]
    assert any("you chose to build AC-2 before AC-1" in x for x in s["gate_log"])
    # AC-1 comes back with the same AMEND: asked straight away, no explorer round
    for _ in range(6):
        if s["waiting"]["step"] == "amend_loop":
            break
        s = decide(client, tid)
    assert s["waiting"]["step"] == "amend_loop" and len(amends) == 2
    s = decide(client, tid, payload={"choice": "stop"}, why="I will split this spec")
    assert s["status"] == "stopped" and "amendment loop" in (s.get("error") or "")


def test_skip_moves_the_criterion_to_the_end(client, repo, monkeypatch):
    red_amends(monkeypatch, "Covered by AC-1 as written; no amendment.\nREOPEN: none", always=True)
    tid = start(client, repo)
    s = to_loop(client, tid)
    for _ in range(6):
        if s["waiting"]["step"] == "amend_loop":
            break
        s = decide(client, tid)
    s = decide(client, tid, payload={"choice": "skip"})
    assert s["ac"] == "AC-2" and [a["id"] for a in s["acs"]] == ["AC-2", "AC-1"]
    assert values(client, tid)["data"]["ac_order"]["from"] == "person"



def test_the_loop_gate_is_never_decided_by_a_run_mode():
    from keel_engine.runtime import run_mode
    from keel_engine.workflows.templates import get_template

    step = next(s for s in get_template("feature").steps if s.id == "amend_loop")
    kind = run_mode.classify({"step": "amend_loop", "kind": "gate", "title": "x"}, step, flow="feature")
    assert kind == "rounds"
    for mode in ("important", "auto"):
        assert run_mode.decide(mode, kind, {"options": ["approve"]}) is None


# ------------------------------------------------------------------ Java-safe test names

def test_java_safe_test_filters(tmp_path):
    from keel_engine.tools import testcmd

    assert testcmd.ac_id("AC-006") == "AC006"
    assert testcmd.fill("../mvnw -f .. -q test -Dtest=*{AC}*", "AC-006") == "../mvnw -f .. -q test -Dtest=*AC-006*,*AC006*"
    assert testcmd.fill("./gradlew -q test --tests '*{AC}*'", "AC-006") == "./gradlew -q test --tests '*AC-006*' --tests '*AC006*'"
    assert testcmd.fill("mvn test -Dtest='*{AC_ID}*'", "AC-006") == "mvn test -Dtest='*AC006*'"         # already safe: kept
    assert testcmd.fill('python -m pytest -k "{AC_KEY}"', "AC-006") == 'python -m pytest -k "ac_006"'
    # Maven is detected (mvnw first, else mvn), with the widened filter; init writes it with placeholders
    (tmp_path / "pom.xml").write_text("<project/>")
    assert testcmd.detected(str(tmp_path), "AC-6") == "mvn -q test -Dtest='*AC-6*,*AC6*'"
    (tmp_path / "mvnw").write_text("#!/bin/sh\n")
    assert testcmd.detected(str(tmp_path)) == "./mvnw -q test"
    assert testcmd.config_commands(str(tmp_path))["api_test_ac"] == "./mvnw -q test -Dtest='*{AC}*,*{AC_ID}*'"


def test_surefire_and_gradle_report_no_tests():
    from keel_engine.tools import testcmd

    for out in ('[ERROR] Failed to execute goal ... No tests matching pattern "*AC-006*,*AC006*" were executed! (Set '
                '-Dsurefire.failIfNoSpecifiedTests=false to ignore this error.)',
                "[ERROR] No tests were executed!  (Set -DfailIfNoTests=false to ignore this error.)",
                "> No tests found for given includes: [*AC-006*](--tests filter)"):
        assert testcmd.ran_no_tests(out), out
    assert testcmd.ac_test_passed("✓ AC006RankTest > ranks()\n✓ other", "AC-006") is True          # the dash-free name counts


def test_the_red_prompt_names_the_test_the_filter_finds(tmp_path):
    from keel_engine.runtime import prompts

    (tmp_path / "pom.xml").write_text("<project/>")
    p = prompts.task_prompt(agent="test-author", phase="red", step_name="red", title="t", root=str(tmp_path),
                            ac={"id": "AC-006", "layer": "API", "title": "x"}, acs=[], feedback=None)
    assert "Put AC006 in the test class or method name" in p and "-Dtest='*AC-006*,*AC006*'" in p


# ------------------------------------------------------------------ MCP text

def test_mcp_status_shows_the_order_and_the_real_choices_without_cutting_words():
    from keel_engine import mcp_server

    detail = "AC-1 came back.\n\nChoose one:\n- first: build AC-2 (ranks) first, then AC-1\n- skip: move AC-1 to the end\n- stop: stop the flow\n" + "word " * 1000
    thread = {"thread_id": "t", "status": "waiting", "current": "amend_loop", "phase": "spec", "ac": "AC-1",
              "acs": [{"id": "AC-2", "status": "done"}, {"id": "AC-1", "status": "todo"}], "ac_order": {"from": "keel"},
              "waiting": {"step": "amend_loop", "kind": "gate", "title": "the amendment changed nothing", "options": ["approve"],
                          "choices": ["first", "skip", "edit", "stop"], "detail": detail}}

    class Api:
        def get(self, route, **_):
            return [{"id": "p", "branch": "b"}] if route == "/projects" else {"thread": thread, "workflow": {"id": "feature"}}
    text = mcp_server.status(Api(), "p")
    assert "order     AC-2 → AC-1 (from keel moved it (an order question))" in text
    assert "first: build AC-2 (ranks) first, then AC-1" in text and "options: approve" not in text
    assert "word word …\n" in text and "wor …" not in text      # cut between words, with the sign it was cut
    assert "(the gate's text goes on: 5,1" in text and "keel_read_gate(project='p', flow='t') reads it whole" in text   # 0.18.28
    nxt = mcp_server.next_action(Api(), "p")
    assert "choose one" in nxt and "stop:" in nxt


def test_fake_lab_order_from_the_spec_then_the_loop_guard(client, repo):
    """The real check's fake: ORDER only in the spec (AC-2 first), AC-1's AMEND, an explorer that changes nothing."""
    tid = start(client, repo, models={"default": {"provider": "fake", "mode": "api", "model": "fake-lab"}})
    s = to_loop(client, tid)
    assert s["ac"] == "AC-2" and values(client, tid)["data"]["ac_order"]["from"] == "spec"
    for _ in range(8):
        if s["waiting"]["step"] == "amend_loop":
            break
        assert s["waiting"]["step"] != "amend_gate"
        s = decide(client, tid)
    assert s["waiting"]["step"] == "amend_loop" and s["ac"] == "AC-1"


def test_a_skipped_criterions_red_test_commit_is_reverted(tmp_path):
    """0.18.69 a field report: after a skip, every later criterion's suite failed on the skipped one's RED test."""
    from test_v02 import git
    from keel_engine.runtime import flow_actions
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.py").write_text("x = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: start")
    start = git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "test_ac1.py").write_text("def test_ac_1():\n    assert False\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "test(AC-1): ranks")
    ok, note = flow_actions.revert_red(str(root), "AC-1", start)
    assert ok and "reverted" in note
    assert not (root / "test_ac1.py").exists()
    assert git(root, "log", "-1", "--format=%s").stdout.startswith("revert(AC-1): skipped")
    assert git(root, "status", "--porcelain").stdout == ""
    assert flow_actions.revert_red(str(root), "AC-2", start) is None        # no RED commit: nothing to do
    (root / "test_ac3.py").write_text("def test_ac_3():\n    assert False\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "test(AC-3): x")
    (root / "a.py").write_text("x = 3\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat(AC-3): x")
    assert flow_actions.revert_red(str(root), "AC-3", start) is None        # built: its test is not taken away
