"""keel 0.19.x MCP catches up with the dashboard and keel TUI: any question gate shown and asked from its data (the
feature interview's lens questions, per-finding security decisions, "ready to ship?", the init doctor's rung, review-pr's
share-sections pick), the flows to start with what each is for and its inputs, pause / resume, the focused project, and
KeelBot's question cards with the failure rule."""

import json

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ElicitResult

from keel_engine import mcp_ask, mcp_flows, mcp_server, mcp_tui
from keel_engine.runtime import choices
from test_mcp import THREAD, StubApi, call

# gates the other helpers are building now, written only from the clarify / choice_info shapes
INTERVIEW = {"step": "interview", "kind": "clarify", "title": "Lens questions", "options": ["approve", "reject"], "detail": "",
             "questions": [
                 {"id": "security-auth", "question": "Who may see a player's scores?", "why": "the security lens",
                  "options": [{"label": "everyone", "description": "public board", "recommended": True},
                              {"label": "only the player", "description": "private"}]},
                 {"id": "ux-empty", "question": "What shows when there are no scores yet?", "why": "the UX lens", "options": []}]}
FINDINGS = {"step": "security_decide", "kind": "clarify", "title": "2 security findings", "options": ["approve"], "detail": "",
            "questions": [
                {"id": "S-1", "question": "S-1 SQL built from input in ScoreRepo", "why": "high",
                 "options": [{"label": "fix", "description": "fix it now", "recommended": True},
                             {"label": "accept", "description": "accept the risk, with a reason"}]},
                {"id": "S-2", "question": "S-2 a missing rate limit", "why": "low",
                 "options": [{"label": "fix"}, {"label": "accept", "recommended": True}]}]}
SHARE = {"step": "share", "kind": "clarify", "title": "Share the review", "options": ["approve"], "detail": "",
         "questions": [{"id": "sections", "question": "Which sections go on the PR?", "multiple": True,
                        "options": [{"label": "summary", "recommended": True}, {"label": "findings", "recommended": True},
                                    {"label": "nits"}]}]}
READY = choices.for_question({"step": "ready", "kind": "gate", "title": "Ready to ship?", "options": ["approve", "reject"],
                              "choices": ["ship", "more", "stop"],
                              "detail": "ship: open the PR now.\nmore: one more round on the findings.\nstop: end here."},
                             None, needs_why={"more": "required"})
RUNG = choices.for_question({"step": "doctor", "kind": "fix", "title": "The rung `docker compose up` fails", "options": ["approve"],
                             "choices": ["fix", "exclude", "accept"],
                             "detail": "- fix: let the setup doctor try again\n- exclude: leave this rung out\n"
                                       "- accept: accept it as not checked"})
REVIEW_PR_YAML = """# Review a pull request and share the findings. Inputs (the api's `options`):
#   pr       the pull request number or URL
#   ticket   the ticket key the PR is for
id: review-pr
name: review pr
steps:
  - id: read
    kind: agent
"""
WORKFLOWS = [{"id": "quick", "name": "quick (keel)", "yaml": "id: quick\nsteps: []\n"},
             {"id": "spike", "name": "spike (keel)", "yaml": "id: spike\nsteps: []\n"},
             {"id": "review-pr", "name": "review pr", "yaml": REVIEW_PR_YAML},
             {"id": "mine", "name": "mine", "yaml": "about: 'My own flow, for docs'\nid: mine\nsteps: []\n"}]


class Api(StubApi):
    """The stub api plus the Inbox, a thread, runs, stop, rewind, the workflows with their YAML and the focus."""

    def __init__(self):
        super().__init__()
        self.focus = {"project": "shop", "chats": {}, "by": "web", "at": "2026-10-19T10:00:00Z"}

    def __call__(self, req):
        path = req.url.path
        body = json.loads(req.content) if req.content else None
        if req.method == "POST" and path in ("/api/inbox/t-1/act", "/api/threads/t-1/stop", "/api/threads/t-1/rewind"):
            self.posts.append((path, body))
            return httpx.Response(200, json={**self.thread, "status": "running", "waiting": None})
        if req.method == "PUT" and path == "/api/ui/focus":
            self.posts.append((path, body))
            self.focus = {**self.focus, "project": body.get("project") or self.focus["project"], "by": body.get("by")}
            return httpx.Response(200, json=self.focus)
        routes = {"/api/projects/shop/runs": [], "/api/projects/shop/workflows": WORKFLOWS,
                  "/api/ui/focus": self.focus, "/api/projects/shop/flows/t-1": {"thread": self.thread}}
        if req.method == "GET" and path in routes:
            return httpx.Response(200, json=routes[path])
        return super().__call__(req)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("KEEL_PROJECT", "shop")      # a write needs its project named when the folder matches none
    monkeypatch.chdir("/")
    s = Api()
    s.api = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(s))
    return s


def srv(api, level="write"):
    return mcp_server.build_server(level=level, api=api.api)


# ---------------------------------------------------------------- 1. questions everywhere

async def test_new_question_gates_show_from_their_data_in_status_next_and_inbox(api):
    api.thread = {**THREAD, "waiting": INTERVIEW}
    text = await call(srv(api), "keel_status")
    assert "security-auth: Who may see a player's scores?  (pick one)" in text and "why: the security lens" in text
    assert "- everyone (recommended): public board" in text
    assert "ux-empty: What shows when there are no scores yet?  (your words)" in text
    api.thread = {**THREAD, "waiting": SHARE}
    nxt = await call(srv(api), "keel_next")
    assert "sections: Which sections go on the PR?  (pick any)" in nxt and "ask_person=true" in nxt
    api.thread = {**THREAD, "waiting": READY}
    nxt = await call(srv(api), "keel_next")
    assert 'more "' in nxt and "[needs a reason]" in nxt and "ship" in nxt

    class Inbox:
        def get(self, route, **_kw):
            return {"items": [{"project_id": "shop", "thread_id": "t-1", **FINDINGS},
                              {"project_id": "shop", "thread_id": "t-2", **RUNG}]}

    from keel_engine import mcp_more
    box = mcp_more.inbox(Inbox())
    assert "S-1: S-1 SQL built from input in ScoreRepo  (pick one)" in box and "- accept (recommended)" in box
    assert "exclude" in box and "leave this rung out" in box
    assert "answers={<question id>" in box and "thread_id='t-2'" in box


async def test_an_approve_without_answers_asks_in_a_form_with_multi_select_and_own_words(api):
    api.thread = {**THREAD, "waiting": {**SHARE, "questions": SHARE["questions"] + INTERVIEW["questions"][1:]}}
    seen = {}

    async def fill(context, params):
        seen["schema"] = params.requestedSchema
        return ElicitResult(action="accept", content={"q1": ["summary", "nits"], "q2_own_words": "an empty board with a hint"})

    async with create_connected_server_and_client_session(srv(api), elicitation_callback=fill) as s:
        res = await s.call_tool("keel_approve_gate", {"decision": "approve"})
    assert not res.isError and "approved" in res.content[0].text
    props = seen["schema"]["properties"]
    assert props["q1"]["type"] == "array" and props["q1"]["items"]["enum"] == ["summary", "findings", "nits"]
    assert props["q1"]["default"] == ["summary", "findings"] and "enum" not in props["q2_own_words"]
    path, body = api.posts[-1]
    assert path == "/api/threads/t-1/resume"
    assert body["payload"] == {"answers": {"sections": ["summary", "nits"], "ux-empty": "an empty board with a hint"}}


async def test_keel_answer_asks_per_finding_decisions_and_a_client_without_forms_gets_text(api):
    api.thread = {**THREAD, "waiting": FINDINGS}

    async def fill(context, params):
        return ElicitResult(action="accept", content={"q1": "fix", "q2": "accept", "why": "rate limit is in the gateway"})

    async with create_connected_server_and_client_session(srv(api), elicitation_callback=fill) as s:
        res = await s.call_tool("keel_answer", {})
    assert "answered thread t-1" in res.content[0].text
    path, body = api.posts[-1]
    assert path == "/api/inbox/t-1/act" and body["why"] == "rate limit is in the gateway"
    assert body["payload"] == {"answers": {"S-1": "fix", "S-2": "accept"}}

    api.posts.clear()
    text = await call(srv(api), "keel_answer")
    assert "cannot show a form" in text and "S-2: S-2 a missing rate limit" in text and not api.posts
    text = await call(srv(api), "keel_answer", {"answers": {"S-1": "accept", "S-2": ["fix", "and add a test"]}})
    assert api.posts[-1][1]["payload"] == {"answers": {"S-1": "accept", "S-2": ["fix", "and add a test"]}}


async def test_a_choice_gate_asks_its_choices_with_ask_person_and_a_plain_approve_takes_the_default(api):
    api.thread = {**THREAD, "waiting": READY}

    async def fill(context, params):
        labels = params.requestedSchema["properties"]["choice"]["enum"]
        return ElicitResult(action="accept", content={"choice": labels[1], "why": "two findings left"})

    async with create_connected_server_and_client_session(srv(api), elicitation_callback=fill) as s:
        await s.call_tool("keel_approve_gate", {"decision": "approve", "ask_person": True})
    assert api.posts[-1][1]["payload"] == {"choice": "more"} and api.posts[-1][1]["why"] == "two findings left"
    await call(srv(api), "keel_approve_gate", {"decision": "approve"})          # no form needed: the default
    assert api.posts[-1][1].get("payload") is None


# ---------------------------------------------------------------- 3. flows

async def test_start_flow_without_a_type_lists_every_flow_with_its_line_and_inputs(api):
    text = await call(srv(api), "keel_start_flow")
    assert "quick" in text and mcp_flows.ABOUT["quick"] in text and mcp_flows.ABOUT["spike"] in text
    assert "review-pr" in text and "[inputs: pr, ticket]" in text and "My own flow, for docs" in text
    assert not api.posts
    flows = await call(srv(api, "read"), "keel_flows")
    assert "no flow has run on shop yet." in flows and "review-pr" not in flows and "keel_flows(catalog=true)" in flows
    flows = await call(srv(api, "read"), "keel_flows", {"catalog": True})       # 0.22.0 the catalogue only when asked
    assert "flows you can start" in flows and "review-pr" in flows


async def test_start_flow_takes_title_request_and_the_flows_inputs(api):
    await call(srv(api), "keel_start_flow", {"type": "review-pr", "pr": "42", "ticket": "SHOP-7"})
    path, body = api.posts[-1]
    assert path == "/api/projects/shop/flows" and body["workflow_id"] == "review-pr" and body["title"] == "PR 42"
    assert body["options"] == {"started_via": "mcp", "ticket": "SHOP-7", "pr": "42"}
    await call(srv(api), "keel_start_flow", {"type": "spike", "title": "Which queue?", "request": "Kafka or SQS for events",
                                             "inputs": {"depth": "short"}})
    body = api.posts[-1][1]
    assert body["title"] == "Which queue?" and body["request"] == "Kafka or SQS for events" and body["options"]["depth"] == "short"
    text = await call(srv(api), "keel_start_flow", {"type": "nope", "idea": "x"})
    assert 'no flow type "nope"' in text and "review-pr" in text


def test_a_flows_inputs_and_line_come_from_its_yaml():
    assert mcp_flows.inputs_of({"yaml": REVIEW_PR_YAML}) == [("pr", "the pull request number or URL"),
                                                            ("ticket", "the ticket key the PR is for")]
    assert mcp_flows.about_of({"id": "other", "yaml": REVIEW_PR_YAML}) == "Review a pull request and share the findings."


# ---------------------------------------------------------------- 2. projects: pause, resume, focus

async def test_pause_stops_the_flow_and_resume_without_a_decision_goes_on(api, monkeypatch):
    s = srv(api, "admin")
    text = await call(s, "keel_pause")
    assert "cannot ask you to confirm pause the flow" in text and not api.posts
    s = mcp_server.build_server(level="admin", api=api.api, yes=True)
    text = await call(s, "keel_pause")
    assert api.posts[-1][0] == "/api/threads/t-1/stop" and "keel_resume" in text
    api.thread = {**THREAD, "status": "stopped", "waiting": None}
    text = await call(s, "keel_resume")
    assert api.posts[-1] == ("/api/threads/t-1/rewind", {"checkpoint_id": "c2"}) and "resumed" in text
    assert "nothing to pause" in await call(s, "keel_pause")
    assert "keel_pause" not in {t.name for t in await srv(api, "write").list_tools()}     # admin only
    assert mcp_server.tool_level("keel_pause") == "admin" and mcp_server.tool_level("keel_focus") == "admin"


async def test_focus_shows_and_switches_the_dashboards_and_tuis_project(api):
    s = srv(api, "admin")
    assert "in focus: shop · set by web" in await call(s, "keel_focus")
    text = await call(s, "keel_focus", {"project": "blog"})
    path, body = api.posts[-1]
    assert path == "/api/ui/focus" and body["project"] == "blog" and body["by"] == "mcp"
    assert "now look at blog" in text
    assert "keel_focus" not in {t.name for t in await srv(api, "write").list_tools()}


# ---------------------------------------------------------------- 4. KeelBot's question cards and the failure rule

def test_keelbot_cards_carry_the_failure_rule_and_the_server_says_how_to_answer():
    text = ('`npm test` failed: missing module.\n```keel-questions\n[{"id": "next", "question": "What now?", "options": '
            '[{"label": "install it", "recommended": true}, {"label": "skip the tests"}]}]\n```')
    hint = mcp_tui._buttons_hint(text, "c-1")
    assert "keel_keelbot_ask(chat='c-1'" in hint and "never run the failed command again unchanged" in hint
    s = mcp_server.build_server(level="read", api=object())
    assert "keel-questions" in s.instructions and "do not run it again unchanged" in s.instructions
    assert mcp_ask.needs_answers(INTERVIEW, "approve", None, None) and not mcp_ask.needs_answers(READY, "approve", None, None)


async def test_start_flow_takes_the_run_mode_the_person_named(api):
    """0.18.28 a flow started over MCP in a named mode starts in it (keel_run_mode afterwards came too late)."""
    text = await call(srv(api), "keel_start_flow", {"type": "spike", "request": "Kafka or SQS", "run_mode": "important"})
    assert api.posts[-1][1]["run_mode"] == "important" and "important mode" in text
    await call(srv(api), "keel_start_flow", {"type": "spike", "request": "Kafka or SQS"})
    assert "run_mode" not in api.posts[-1][1]


# ---------------------------------------------------------------- 0.21.2 how keel2 starts keel

def test_start_options_says_how_keel_runs_and_saves_a_change_for_keel2():
    from keel_engine import mcp_more

    class Api:
        def __init__(self):
            self.puts = []
            self.view = {"start": {"docker": False, "wifi": True}, "start_wanted": {}}

        def get(self, path):
            assert path == "/resources"
            return self.view

        def put(self, path, body):
            self.puts.append((path, body))
            self.view = {**self.view, "start_wanted": {"docker": True}}
            return self.view

    api = Api()
    text = mcp_more.start_options(api)
    assert "Docker access for the project's tests (--docker): off" in text and "Wi-Fi (--wifi): on" in text and not api.puts
    text = mcp_more.start_options(api, docker=True)
    assert api.puts == [("/resources", {"docker": True})]
    assert "off, on after keel2's next start" in text and "keel2 restart" in text and "asks the person there" in text
    assert mcp_server.tool_level("keel_start_options") == "admin" and "keel_start_options" in mcp_server.CONFIRM
