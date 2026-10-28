"""The clarify flow (content/workflows/clarify.yaml) on the fake model: the clarify-loop skill from the person's
ai-coding-toolkit as keel's structured questions, its outputs as a document, and the hand-over to a keel flow."""

import json
import re
from pathlib import Path

from conftest import decide, start, wait
from test_flows_a import child, request_of
from test_flows_base import values
from keel_engine.models import fake as fake_mod
from keel_engine.runtime import clarify_flow, prompts, spec_check
from keel_engine.workflows.templates import get_template
from keel_engine.workflows.validate import validate


def by_step(monkeypatch, plans):
    """The fake model, but plans[<step name>](req) answers (text) for those steps; seen lists every request."""
    real = fake_mod._plan
    seen = []

    def plan(req):
        seen.append(req)
        fn = plans.get(req.step_name)
        return (None, "", fn(req), {}) if fn else real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    return seen


def to_rounds(client, repo, monkeypatch, plans=None, title="Player ranks", request="Show each player's rank.",
              kind="New feature", sections=None):
    seen = by_step(monkeypatch, plans or {})
    tid = start(client, repo, workflow="clarify", title=title, request=request)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "task_type", s["waiting"]
    s = decide(client, tid, "approve", payload={"answers": {"task_type": kind}})
    assert s["waiting"]["step"] == "sections", s["waiting"]
    s = decide(client, tid, "approve", payload={"answers": {"sections": sections}} if sections else None)
    return tid, s, seen


def finish(client, tid, s, payload=None):
    """Answer every round with the recommended options until the result gate."""
    for _ in range(8):
        if s["waiting"]["step"] == "result":
            return s
        assert s["waiting"]["step"] == "round_gate", s["waiting"]
        s = decide(client, tid, "approve", payload=payload)
    raise AssertionError("never reached the result gate")


def test_the_clarify_flow_is_valid_and_listed():
    wf = get_template("clarify")
    assert wf and validate(wf) == []
    rounds = wf.step("rounds")
    assert rounds.asks and rounds.rounds == 4 and rounds.keep


def test_the_task_type_gate_shows_six_types_with_the_recommended_one_first(client, repo, monkeypatch):
    seen = by_step(monkeypatch, {})
    tid = start(client, repo, workflow="clarify", title="Totals are wrong", request="The weekly total crashes on an empty week.")
    s = wait(client, tid)
    w = s["waiting"]
    assert w["step"] == "task_type" and w["kind"] == "clarify" and w["title"] == "What kind of task is this?"
    q = w["questions"][0]
    assert q["question"] == "What kind of task is this?" and not q.get("multiple")
    labels = [o["label"] for o in q["options"]]
    assert labels == ["Bug fix", "New feature", "Performance", "Refactor", "API integration", "Architecture"]
    assert q["options"][0]["recommended"] and not any(o["recommended"] for o in q["options"][1:])
    assert q["options"][0]["description"] == "Something is broken — find and fix the root cause."
    # the explorer read the request and the code before the first question
    assert [r.step_name for r in seen] == ["read the request and the code"]
    assert "TASK-TYPE: <one line>" in seen[0].prompt and "file:line" in seen[0].prompt


def test_the_sections_answer_drives_which_rounds_run(client, repo, monkeypatch):
    # sections 1 (requirement questions) and 5: the rounds ask the bug's questions, no red flags
    tid, s, seen = to_rounds(client, repo, monkeypatch, kind="Bug fix", request="The total crashes for new users.",
                             sections=["1. Requirement questions", "5. Ready-to-send prompt"])
    q = values(client, tid)["data"]["clarify"]
    # 2 and 3 are always in: the document is a keel spec
    assert q["type"] == "bug" and q["sections"] == [1, 2, 3, 5] and q["ask"] == "yes"
    rounds = next(r for r in seen if r.step_name == "clarify questions")
    assert "**Symptom** — What is happening?" in rounds.prompt and "**Already tried**" in rounds.prompt
    assert "Identity vs. authentication probe" in rounds.prompt and "B · Authentication" in rounds.prompt  # "users"
    assert "Section 4, red-flag check" not in rounds.prompt
    assert "✅ 1. Requirement questions" in rounds.prompt and "⬜ 4. Red-flag check" in rounds.prompt
    s = finish(client, tid, s)
    doc = Path(repo) / values(client, tid)["data"]["clarify"]["doc"]
    text = doc.read_text()
    assert "## Appendix: ready-to-send prompt" in text and "## Acceptance criteria" in text
    assert "## Red-flag check" not in text and "## Next step" not in text


def test_sections_that_ask_nothing_skip_the_rounds(client, repo, monkeypatch):
    tid, s, seen = to_rounds(client, repo, monkeypatch, sections=["2. Acceptance criteria (AC-1, AC-2 …)", "3. Definition of Done"])
    assert s["waiting"]["step"] == "result"
    assert "clarify questions" not in [r.step_name for r in seen]
    assert values(client, tid)["data"]["clarify"]["ask"] == "no"


def test_the_sections_question_is_a_multi_select_with_the_sensible_ones_ticked(client, repo, monkeypatch):
    by_step(monkeypatch, {})
    tid = start(client, repo, workflow="clarify")
    wait(client, tid)
    s = decide(client, tid, "approve", payload={"answers": {"task_type": "Refactor"}})
    w = s["waiting"]
    q = w["questions"][0]
    assert w["step"] == "sections" and w["kind"] == "clarify" and q["multiple"] is True
    assert [o["label"].split(".")[0] for o in q["options"]] == ["1", "2", "3", "4", "5", "6"]
    # the fake's read step said SECTIONS: 1, 2, 3, 5, 6
    assert [o["recommended"] for o in q["options"]] == [True, True, True, False, True, True]
    assert "For Refactor: What to refactor?" in q["options"][0]["description"]
    # no answer: the recommended sections run
    s = decide(client, tid)
    assert values(client, tid)["data"]["clarify"]["sections"] == [1, 2, 3, 5, 6]


def test_round_questions_are_structured_with_the_recommended_option_first(client, repo, monkeypatch):
    qs = [{"id": f"q{n}", "question": f"Question {n}?", "why": "it changes the criteria",
           "options": [{"label": "plain"}, {"label": f"src/scores/__init__.py:{n}", "description": "the code says so",
                                            "recommended": True}]} for n in range(1, 9)]
    calls = []

    def rounds(req):
        calls.append(req)
        if len(calls) == 1:
            return "- what: open\n```keel-questions\n" + json.dumps(qs) + "\n```"
        return "- what: answered\nNO_QUESTIONS: answered"
    tid, s, seen = to_rounds(client, repo, monkeypatch, {"clarify questions": rounds})
    w = s["waiting"]
    assert w["step"] == "round_gate" and w["kind"] == "clarify"
    assert len(w["questions"]) == 6                                     # at most 6 a round
    for q in w["questions"]:
        assert q["options"][0]["recommended"] and q["options"][0]["label"].startswith("src/scores/__init__.py:")
        assert not q["options"][1]["recommended"]
    s = decide(client, tid, "approve", why="ranks are public", payload={"answers": {"q1": "my own words"}})
    assert len(calls) == 2 and calls[1].feedback.startswith("Answers to your questions:")
    assert "→ my own words" in calls[1].feedback and "Round 1 of at most 4 is answered" in calls[1].feedback
    assert "NO_QUESTIONS" in calls[1].feedback
    assert s["waiting"]["step"] == "result"
    assemble = next(r for r in seen if r.step_name == "assemble the outputs")
    assert "→ my own words" in assemble.prompt and "in the person's words: ranks are public" in assemble.prompt


def test_rounds_go_on_until_no_questions(client, repo, monkeypatch):
    calls = []
    q = [{"id": "x", "question": "Which?", "options": [{"label": "a", "recommended": True}, {"label": "b"}]}]

    def rounds(req):
        calls.append(req)
        if len(calls) <= 2:
            return f"- round {len(calls)}\n```keel-questions\n" + json.dumps(q) + "\n```"
        return "NO_QUESTIONS: the chosen sections are answered"
    tid, s, _seen = to_rounds(client, repo, monkeypatch, {"clarify questions": rounds})
    s = finish(client, tid, s)
    assert len(calls) == 3, "two rounds of questions, then NO_QUESTIONS ends them"
    assert values(client, tid)["data"]["rounds_answers"].count("Answers to your questions:") == 2


def test_no_questions_ends_the_rounds_at_once(client, repo, monkeypatch):
    tid, s, seen = to_rounds(client, repo, monkeypatch,
                             {"clarify questions": lambda req: "- what: the request says it\nNO_QUESTIONS: the code answers it"})
    assert s["waiting"]["step"] == "result"
    assert [r.step_name for r in seen].count("clarify questions") == 1
    assert any(x.startswith("gate round_gate approve: not asked") for x in s["gate_log"]), s["gate_log"]


def test_the_document_holds_criteria_keel_reads_a_definition_of_done_and_a_prompt(client, repo, monkeypatch):
    tid, s, _seen = to_rounds(client, repo, monkeypatch, sections=["1", "2", "3", "4", "5", "6"])
    s = finish(client, tid, s)
    st = values(client, tid)["data"]["clarify"]
    rel = st["doc"]
    assert rel.startswith(".keel/specs/clarify/") or rel.startswith("docs/specs/clarify/"), rel
    text = (Path(repo) / rel).read_text()
    assert "ai-coding-toolkit" in text                                   # where it comes from
    acs = prompts.parse_acs(text)
    assert [a["id"] for a in acs] == ["AC-1", "AC-2"] and acs[0]["layer"] == "API"
    assert acs[0]["title"].startswith("Given scores, when")
    # a keel spec: the spec check finds nothing missing
    assert [f for f in spec_check.check(text, acs) if f["level"] == "fix"] == []
    assert text.startswith("---\nid: clarify-") and "status: draft" in text
    heads = re.findall(r"^## (.+)$", text, re.M)
    # keel's spec format: the six sections a reader scans first, In plain words first
    assert heads[:7] == ["In plain words", "For engineers", "What will change", "Payload changes", "Database changes",
                         "Architecture", "Request"]
    want = ["Goal", "User stories", "Decisions", "Findings (evidence)", "Acceptance criteria", "Test layers", "Request path",
            "Data and migrations", "Validation and security rules", "Edge cases", "Risks and cost",
            "Assumptions", "Unverified and open questions", "Out of scope", "Definition of done"]
    assert [h for h in heads if h in want] == want
    assert heads[-1] == "Appendix: ready-to-send prompt"                # the prompt is the last appendix
    assert "// + count" in text and "Compatible: old callers" in text      # the payload, before and after
    assert "[run() ~changed]" in text and "- src/scores/__init__.py: changed" in text
    assert "like the third runner in a race" in spec_check.sections(text)["in plain words"]
    assert "idempotent" in spec_check.sections(text)["for engineers"]
    assert spec_check.sections(text)["database changes"].startswith("none: ")          # nothing stored
    assert "- [ ] Input validation is explicit and tested." in text
    assert "## Red-flag check" in text and "## Next step" in text
    assert "I need to implement: Player ranks" in text and st["prompt"].startswith("I need to implement: Player ranks")
    assert "## Appendix: questions and answers" in text and "Which module does this live in?" in text
    # the result gate: the document, the matching flow first and recommended, each choice described
    w = s["waiting"]
    assert w["choices"] == ["feature", "small", "prompt", "reject", "stop"]
    info = {c["name"]: c for c in w["choice_info"]}
    assert info["feature"]["default"] and "(recommended)" in info["feature"]["description"]
    assert info["reject"]["why"] == "required" and "the criteria and the answers" in info["feature"]["description"]
    assert rel in w["detail"] and "AC-1" in w["detail"]


def test_a_new_feature_is_handed_to_feature_with_the_criteria_as_its_seed(client, repo, monkeypatch):
    tid, s, _seen = to_rounds(client, repo, monkeypatch)
    s = finish(client, tid, s)
    s = decide(client, tid)                                              # the default: feature
    assert s["status"] == "done"
    kid = child(client, s, "feature")
    v = values(client, kid)
    assert [a["id"] for a in v["acs"]] == ["AC-1", "AC-2"]
    seed = v["data"]["seed"]
    assert [a["id"] for a in seed["acs"]] == ["AC-1", "AC-2"] and "Which module does this live in?" in seed["evidence"]
    req = request_of(client, kid)
    assert "Show each player's rank." in req and "- **AC-1** [API] Given scores" in req


def test_a_bug_fix_goes_to_the_fix_flow(client, repo, monkeypatch):
    tid, s, _seen = to_rounds(client, repo, monkeypatch, kind="Bug fix", title="Total crashes",
                              request="The weekly total crashes on an empty week.")
    s = finish(client, tid, s)
    assert s["waiting"]["choices"][0] == "fix" and "feature" not in s["waiting"]["choices"]
    st = values(client, tid)["data"]["clarify"]
    assert st["template"].startswith("Bug Report")
    s = decide(client, tid, "approve", payload={"choice": "fix"})
    assert s["status"] == "done"
    kid = child(client, s, "fix")
    assert "The weekly total crashes on an empty week." in request_of(client, kid)


def test_copy_the_prompt_only_ends_without_a_child_flow(client, repo, monkeypatch):
    tid, s, _seen = to_rounds(client, repo, monkeypatch)
    s = finish(client, tid, s)
    s = decide(client, tid, "approve", payload={"choice": "prompt"})
    assert s["status"] == "done" and not s.get("children")
    assert "the prompt is ready to copy" in values(client, tid)["note"]


def test_send_back_writes_the_outputs_again_with_the_note(client, repo, monkeypatch):
    tid, s, seen = to_rounds(client, repo, monkeypatch)
    s = finish(client, tid, s)
    s = decide(client, tid, "approve", why="split AC-1 in two", payload={"choice": "reject"})
    assert s["waiting"]["step"] == "result"
    again = [r for r in seen if r.step_name == "assemble the outputs"]
    assert len(again) == 2 and "split AC-1 in two" in (again[1].feedback or "")


def test_a_note_survives_the_spec_checks_send_back_and_a_second_reject(client, repo, monkeypatch):
    # 0.18.64 a reject note was used once, then the spec check sent the outputs back with only its own findings as the
    # feedback, and the outputs were written again without the note (the gate showed the old spec)
    calls = []

    def assemble(req):
        calls.append(req)
        full = fake_mod.clarify_answer(req)
        return full.replace("## Test layers", "## Notes") if len(calls) == 2 else full   # the reject's pass misses one
    tid, s, _seen = to_rounds(client, repo, monkeypatch, {"assemble the outputs": assemble}, sections=["2", "3"])
    s = decide(client, tid, "approve", why="the id field is required, not optional", payload={"choice": "reject"})
    assert s["waiting"]["step"] == "result" and len(calls) == 3
    retry = calls[2]
    assert "Test layers" in (retry.feedback or "") and "the id field is required, not optional" in retry.feedback
    assert "the id field is required, not optional" in retry.prompt
    s = decide(client, tid, "approve", why="keep the old route too", payload={"choice": "reject"})
    assert values(client, tid)["data"]["clarify"]["notes"] == ["the id field is required, not optional", "keep the old route too"]
    assert "the id field is required, not optional" in calls[-1].prompt and "keep the old route too" in calls[-1].prompt


def test_types_map_to_their_flows_and_templates():
    assert set(clarify_flow.TYPES) == set(clarify_flow.QUESTIONS) == set(clarify_flow.TEMPLATES) == set(clarify_flow.FLOWS)
    assert clarify_flow.FLOWS["bug"][0] == "fix" and clarify_flow.FLOWS["feature"][0] == "feature"
    assert clarify_flow.FLOWS["api"][0] == "feature" and clarify_flow.FLOWS["refactor"][:2] == ["change", "small"]
    assert clarify_flow.FLOWS["architecture"][0] == "spike"
    assert clarify_flow.type_of("Bug fix") == "bug" and clarify_flow.type_of("api — a webhook") == "api"
    assert clarify_flow.sections_of("1, 2, 5") == [1, 2, 5] and clarify_flow.sections_of("all") == [1, 2, 3, 4, 5, 6]
    assert clarify_flow.sections_of(["2. Acceptance criteria (AC-1, AC-2 …)", "6. Next-step recommendation"]) == [2, 6]


def test_a_document_the_spec_check_refuses_is_written_again_once(client, repo, monkeypatch):
    calls = []

    def assemble(req):
        calls.append(req)
        full = fake_mod.clarify_answer(req)
        return full if len(calls) > 1 else full.replace("## Test layers", "## Notes")     # no test layers the first time
    tid, s, _seen = to_rounds(client, repo, monkeypatch, {"assemble the outputs": assemble}, sections=["2", "3"])
    assert s["waiting"]["step"] == "result" and len(calls) == 2
    assert "Test layers" in (calls[1].feedback or "") and "spec check" in calls[1].feedback
    text = (Path(repo) / values(client, tid)["data"]["clarify"]["doc"]).read_text()
    assert "## Test layers" in text


def test_a_document_that_cites_lines_past_a_files_end_is_written_again_once(client, repo, monkeypatch):
    # 0.18.64 a field report: the spec cited lines 235-245 of a 38-line file and passed the spec check
    calls = []

    def assemble(req):
        calls.append(req)
        full = fake_mod.clarify_answer(req)
        return full if len(calls) > 1 else full.replace("src/scores/__init__.py:1", "src/scores/__init__.py:235-245")
    tid, s, _seen = to_rounds(client, repo, monkeypatch, {"assemble the outputs": assemble}, sections=["2", "3"])
    assert s["waiting"]["step"] == "result" and len(calls) == 2
    n = len((Path(repo) / "src/scores/__init__.py").read_text().splitlines())
    assert f"src/scores/__init__.py:235-245 does not exist: the file has {n} lines" in (calls[1].feedback or "")
    assert "does not exist" not in s["waiting"]["detail"]                 # the second answer cites real lines


def test_a_request_that_stores_something_shows_the_database_change(client, repo, monkeypatch):
    tid, s, _seen = to_rounds(client, repo, monkeypatch, request="Store each player's rank in the scores table.")
    finish(client, tid, s)
    text = (Path(repo) / values(client, tid)["data"]["clarify"]["doc"]).read_text()
    db = spec_check.sections(text)["database changes"]
    assert "+ rank    int     null" in db and "+ index scores_rank_idx (rank)" in db         # before / after, marked
    assert "up `ALTER TABLE scores ADD COLUMN rank int NULL" in db and "down `DROP INDEX" in db
    assert "Online on PostgreSQL" in db and "Deploy order: migration, then consumer, then producer." in db
    assert "Rollback:" in db and "ScoreRepository.save" in db
    assert [f for f in spec_check.check(text, prompts.parse_acs(text)) if f["level"] == "fix"] == []
