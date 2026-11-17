"""KeelBot's view of keel (keel_plugin_keelbot.keelbot): the workflows it suggests, the flows it answers about, the
buttons it gives, and the workflow format only when the person wants a workflow of their own."""

import re

from keel_engine.workflows.validate import validate_yaml
from keel_plugin_keelbot import helper, keelbot

KEEL = {
    "workflows": [
        {"id": "feature", "name": "feature (keel)", "source": "keel", "steps": [{"id": "spec", "name": "spec"}],
         "last_run": {"title": "Euro prices", "status": "waiting"}},
        {"id": "demo-checks", "name": "checks", "source": "yours", "folder": "Daily", "based_on": None,
         "steps": [{"id": "lint", "kind": "code", "name": "run the linters"}, {"id": "look", "kind": "gate", "name": "look"}]},
    ],
    "flows": [
        {"thread_id": "t-1", "title": "Euro prices", "workflow": "feature", "status": "waiting",
         "waiting": {"title": "spec approval"}, "step": "g-spec", "phase": "spec", "acs_done": 1, "acs_total": 3,
         "branch": "feat/euro-prices", "where": "worktree", "tokens": 120_000},
    ],
}


def test_the_block_names_the_workflows_the_flows_and_the_buttons():
    b = keelbot.keel_block(KEEL, "How do I add a report page?")
    assert "- `feature` feature (keel): a new feature with a spec" in b and '(last run: waiting, "Euro prices")' in b
    assert "- `demo-checks` checks: yours, folder Daily; 2 steps: run the linters → look; no agents" in b
    assert ('- "Euro prices" (feature, id t-1): waiting; waits for the person at "spec approval"; step g-spec; phase spec; '
            "criteria 1/3 done; branch feat/euro-prices (own worktree); 120k tokens") in b
    assert "```keel-start" in b and "keel-workflow" in b
    # 0.18.4 the person's other buttons: decide a gate, stop flows, a run mode (KeelBot never says it cannot)
    assert "```keel-questions" in b and '"multiple": true' in b          # 0.18.9 KeelBot asks the person to choose
    assert "```keel-flow" in b and '"do": "stop"' in b and '"do": "mode"' in b and '"do": "approve"' in b
    assert "How a workflow is written" not in b                 # the format only when the person wants a workflow
    # 0.18.5 a new rule pack or agent: its format only when the person wants one
    assert "keel-rule" in b and "keel-agent" in b and "How a rule pack is written" not in b
    rb = keelbot.keel_block(KEEL, "write a rule that blocks edits to applied migrations")
    assert "How a rule pack is written" in rb and "How an agent is written" not in rb
    ab = keelbot.keel_block(KEEL, "create a new agent that writes the docs")
    assert "How an agent is written" in ab and "How a rule pack is written" not in ab
    # 0.18.6 a new skill: its button always, its format only when the person wants one (not the agent's format)
    assert "keel-skill" in b and "How a skill is written" not in b
    sb = keelbot.keel_block(KEEL, "write a skill for the reviewer agent about our changelog")
    assert "How a skill is written" in sb and "How an agent is written" not in sb
    # 0.18.9 a new stack: its button always, its format when the person wants one
    assert "keel-stack" in b and "How a stack is written" not in b
    stb = keelbot.keel_block(KEEL, "write a new stack for this project: it builds with zig")
    assert "How a stack is written" in stb
    assert keelbot.keel_block(None, "hi") == ""


def test_choices_are_asked_with_the_questions_block_and_a_flow_s_question_gets_an_answer_button():
    b = keelbot.keel_block(KEEL, "Should the flow's report be weekly or monthly?")
    # 0.18.13 one way to ask: a choice for the person is a keel-questions block, never a prose "A or B?"
    assert "Whenever the person must choose between options, ask with this block" in b and '("A or B?")' in b
    # KeelBot proposes the answer to the question a flow waits at; the person presses Send
    assert '{"do": "answer", "flow": "<flow id>", "answers": {"<question id>": "<label>"' in b
    assert '{"do": "answer", "flow": "<flow id>", "choice": "<choice name>", "why":' in b
    assert "named choices is answered (the answer button above" in b


def test_a_waiting_flow_shows_its_question_ids_and_choice_names_when_the_api_sends_them():
    asks = {**KEEL, "flows": [
        {"thread_id": "t-2", "title": "Set up", "workflow": "init", "status": "waiting", "waiting": {
            "title": "Three questions", "questions": [
                {"id": "run", "question": "How does it run?", "options": [{"label": "docker", "recommended": True}, {"label": "local"}]},
                {"id": "knowledge", "question": "Which pages?", "multiple": True, "options": ["architecture", "domain"]}]}},
        {"thread_id": "t-3", "title": "Fix it", "workflow": "fix", "status": "waiting", "waiting": {
            "title": "AC-1 is stuck", "choice_info": [
                {"name": "retry", "label": "Try again", "description": "", "why": "optional", "default": True},
                {"name": "stop", "label": "Stop", "description": "", "why": "none", "default": False},
                {"name": "reject", "label": "Send back", "description": "", "why": "required", "default": False}]}},
        {"thread_id": "t-4", "title": "Old engine", "workflow": "diagnose", "status": "waiting", "waiting": {
            "title": "diagnosis", "choices": ["fix", "reject"]}},
    ]}
    b = keelbot.keel_block(asks, "what waits?")
    assert ('waits for the person at "Three questions" (questions: run: How does it run? [docker | local]; '
            "knowledge [several]: Which pages? [architecture | domain])") in b
    assert 'waits for the person at "AC-1 is stuck" (choices: retry, stop, reject*; * needs a reason)' in b
    assert 'waits for the person at "diagnosis" (choices: fix, reject*; * needs a reason)' in b
    assert 'waits for the person at "spec approval"; step g-spec' in keelbot.keel_block(KEEL, "hi")    # nothing more asked


def test_the_format_comes_with_a_question_about_a_workflow_of_ones_own():
    b = keelbot.keel_block(KEEL, "Make me a new workflow that runs the tests and the linters")
    assert "How a workflow is written" in b and "Phases: setup," in b and "- run: Runs one shell command" in b
    assert "- implementer:" in b and "- helper:" not in b        # agents to use, not KeelBot itself
    example = re.search(r"```keel-workflow\n(.*?)```", b, re.S)[1]
    assert validate_yaml(example)["ok"]                         # the example keel shows is a valid workflow


def test_the_prompt_carries_the_block(repo):
    p = helper.build_prompt(mode="ask", root=str(repo), question="Which workflow?", know={"sections": [], "code_graph": False, "memory": False}, graph=False, flow=None,
                            mentions=None, selection=None, open_file=None, transcript="", keel=KEEL)
    assert "The workflows this project can run" in p and p.endswith("Question: Which workflow?")


def test_advice_on_a_flow_s_question_comes_from_the_code_read_in_the_same_turn():
    # 0.18.23 KeelBot said "I suggest A for both, smallest and safest", then "I did not read the code myself"
    b = keelbot.keel_block(KEEL, "frame the question has 2 questions: what do you suggest?")
    assert keelbot.ADVICE in b
    assert "read the code the question is about, in the same turn" in b
    assert "the files the gate names, the flow's evidence and findings, or what a search finds" in b
    assert "claims to check, not evidence" in b and "`path/to/file.ext:LINE`" in b
    assert '"not checked in the code: <what and why>"' in b
    assert "Never call an option the safest, the smallest or the simplest without the evidence" in b
    assert "Before you give an answer button, read the code the question is about" in b


def test_a_question_not_about_keels_work_gets_the_short_block():
    # 0.31 "hi" or a code question: the workflow ids, the flows that wait, the start and questions buttons; not every
    # workflow's steps, not the gate buttons (they come when the question is about keel's work)
    short, full = keelbot.keel_block(KEEL, "where is the price computed?"), keelbot.keel_block(KEEL, "which workflow fits?")
    assert "feature" in short and "```keel-start" in short and "```keel-questions" in short and '"do": "approve"' not in short
    assert '"do": "approve"' in full and len(short) < len(full) / 2


def test_keelbot_reads_without_asking_and_asks_only_the_person_s_own_choices(repo):
    # 0.18.23 a question card asked leave to read ("Read the handler and entity, then confirm the rule fits")
    b = keelbot.keel_block(KEEL, "hi")
    assert 'Never use it to ask leave to read, search or explain ("Read the handler first?"): that needs no OK, do it' in b
    know = {"sections": [], "code_graph": False, "memory": False}
    for mode in ("ask", "edit", "fix", "side"):
        p = helper.build_prompt(mode=mode, root=str(repo), question="did you read the code?", know=know, graph=False,
                                flow=None, mentions=None, selection=None, open_file=None, transcript="", keel=KEEL)
        assert helper.READING in p, mode
    r = helper.READING
    assert "Reading is always allowed, in every mode (Ask mode too)" in r
    assert "Never ask the person before you read, search or explain" in r
    assert "Ask only for choices that are the person's: a decision, a change to a file, a command that changes something" in r
    assert '("did you read the code?"), read it now and answer from it in the same turn' in r
    agent = (helper.Path(keelbot.__file__).parents[2] / "content" / "agents" / "helper.md").read_text()
    assert "Reading, searching and explaining need nobody's OK, in Ask mode too" in agent
    assert '"not checked in the code: ..."' in agent


def test_keelbot_learns_the_short_answer_format_the_dashboard_renders(repo):
    # 0.18.38 "all text is white, it's hard to read": verdict tags, the Open items note and next steps as buttons
    know = {"sections": [], "code_graph": False, "memory": False}
    for mode in ("ask", "edit", "fix", "side"):
        p = helper.build_prompt(mode=mode, root=str(repo), question="check the PR comments", know=know, graph=False,
                                flow=None, mentions=None, selection=None, open_file=None, transcript="", keel=KEEL)
        assert helper.STYLE in p, mode
    s = helper.STYLE
    assert "`**Comment 1: keep x?** [valid]` (valid | partly | not valid | optional | unclear)" in s
    assert '"Open items:"' in s
    assert 'Never offer next steps in prose' in s and 'keel-questions block with one question, id "next"' in s
    assert "keel-start button" in s
    assert len(s) < 600          # a few lines: it goes with every turn
