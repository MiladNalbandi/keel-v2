"""0.18.64 the field report's MCP findings (bugs 12 and 13): keel_answer says the choice that ran, keel_start_flow
answers at once, and a client without forms confirms with a one-time token instead of `keel2 mcp --yes` (a dangerous
kind: in keel's Inbox)."""

import copy
import json

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from keel_engine import mcp_confirm, mcp_server
from test_mcp import THREAD, StubApi, call

SPEC_GATE = {"step": "spec_gate", "name": "spec approval", "kind": "gate", "title": "spec approval", "options": ["approve"],
             "choices": ["approve", "edit", "reject"], "detail": "4 criteria", "id": "q-spec"}


class Api(StubApi):
    """StubApi plus the Inbox's answer (with or without the engine's `answered`) and a stop."""

    def __init__(self, waiting=None, answered: dict | None = None, thread=None):
        super().__init__(thread or {**copy.deepcopy(THREAD), **({"current": waiting["step"], "waiting": waiting} if waiting else {})})
        self.answered = answered
        self.gets: list[str] = []
        # 0.18.64 the confirmations in keel's Inbox: id -> None (waiting) or the person's answer (read once)
        self.asks: dict[str, dict | None] = {}
        self.inbox = True

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "GET":
            self.gets.append(path)
        if req.method == "POST" and path == "/api/projects/shop/plugins/ask":
            if not self.inbox:
                return httpx.Response(403, json={"error": "This needs the MCP write level: run keel2 mcp --write"})
            self.asked = getattr(self, "asked", 0) + 1
            qid = f"p_{self.asked:012x}"
            self.asks[qid] = None
            self.posts.append((path, json.loads(req.content)))
            return httpx.Response(200, json={"id": qid, "title": "t"})
        if req.method == "GET" and path.startswith("/api/plugins/asks/"):
            qid = path.rsplit("/", 1)[1]
            if qid not in self.asks:
                return httpx.Response(404, json={"error": "keel knows no such question."})
            got = self.asks[qid]
            if got is None:
                return httpx.Response(200, json={"id": qid, "waiting": True})
            del self.asks[qid]
            return httpx.Response(200, json={"id": qid, **got})
        if req.method == "GET" and path == "/api/inbox":
            return httpx.Response(200, json={"items": []})
        if req.method == "POST" and path in ("/api/inbox/t-1/act", "/api/threads/t-1/stop"):
            body = json.loads(req.content or b"{}")
            self.posts.append((path, body))
            state = {**self.thread, "status": "running" if path.endswith("act") else "stopped", "waiting": None, "current": "spec"}
            return httpx.Response(200, json={**state, **({"answered": self.answered} if self.answered else {})})
        return super().__call__(req)


def server(api: Api, level: str = "write", yes: bool = False):
    return mcp_server.build_server(level=level, yes=yes, api=mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(api)))


@pytest.fixture(autouse=True)
def here(monkeypatch):
    monkeypatch.setenv("KEEL_PROJECT", "shop")
    monkeypatch.chdir("/")


# ---------------------------------------------------------------- keel_answer: what ran

async def test_a_reject_with_the_choice_edit_says_edit_ran_from_the_engines_answer():
    api = Api(SPEC_GATE, answered={"decision": "reject", "choice": "edit", "does": 'the choice "edit" (Edit the spec): rework it'})
    text = await call(server(api), "keel_answer", {"decision": "reject", "choice": "edit", "why": "AC-2 needs a 400"})
    assert api.posts[-1][1]["payload"]["choice"] == "edit"
    assert text.startswith('answered thread t-1: ran the choice "edit" (Edit the spec): rework it (the choice decides: "edit" ran, '
                           "not a plain reject). The flow is running")
    assert "(reject)" not in text


@pytest.mark.parametrize("decision, choice, answered, said", [
    ("approve", None, {"decision": "approve", "choice": "approve", "does": 'the choice "approve" (Approve)'}, 'ran the choice "approve"'),
    ("approve", "edit", {"decision": "approve", "choice": "edit", "does": 'the choice "edit" (Edit)'}, 'ran the choice "edit" (Edit)'),
    ("reject", None, {"decision": "reject", "choice": "reject", "does": 'the choice "reject" (Send back)'}, 'ran the choice "reject"'),
    ("reject", "edit", None, 'ran the choice "edit"'),                       # an older engine: the choice that was sent
])
async def test_every_combination_names_what_ran(decision, choice, answered, said):
    api = Api(SPEC_GATE, answered=answered)
    args = {"decision": decision, "why": "because", **({"choice": choice} if choice else {})}
    assert said in await call(server(api), "keel_answer", args)


async def test_a_plain_gate_says_approved_or_sent_back_with_what_it_does():
    gate = {**THREAD["waiting"], "id": "q-1"}
    api = Api(gate, answered={"decision": "reject", "does": "sends it back to the step before; the agent gets your reason"})
    text = await call(server(api), "keel_answer", {"decision": "reject", "why": "say what a tie does"})
    assert text.startswith("answered thread t-1: sent back: sends it back to the step before")


# ---------------------------------------------------------------- keel_start_flow: at once

async def test_start_flow_answers_at_once_with_the_thread_its_status_and_how_to_follow_it():
    api = Api(thread={**copy.deepcopy(THREAD), "status": "running", "current": None, "phase": None, "waiting": None})
    text = await call(server(api), "keel_start_flow", {"type": "feature", "request": "Player ranks"})
    after = api.gets[api.gets.index("/api/projects/shop/workflows"):]
    assert not any("/flow" in g for g in after[1:]), "no poll for the first gate"
    assert "(thread t-9)" in text and "Status: starting." in text
    assert "Follow it: keel_status(project='shop') or keel_flows(project='shop')" in text
    assert "keel_read_gate(project='shop', flow='t-9') and keel_answer(thread_id='t-9')" in text


def test_start_flow_waits_only_when_asked_and_a_slow_start_is_not_keel_is_down(monkeypatch):
    api = Api(thread={**copy.deepcopy(THREAD), "status": "running", "current": None, "phase": None, "waiting": None})
    client = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(api))
    polled = []
    monkeypatch.setattr(mcp_server, "_flow_of", lambda a, pid, tid: polled.append(tid) or {**THREAD, "thread_id": tid})
    text = mcp_server.start_flow(client, "feature", "Player ranks", sleep=lambda s: None, wait=5)
    assert polled == ["t-9"] and "Status: waiting." in text and "It waits for a human: Approve the spec" in text

    def slow(req):
        if req.method == "POST" and req.url.path == "/api/projects/shop/flows":
            raise httpx.ReadTimeout("slow", request=req)
        return api(req)
    client = mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(slow))
    with pytest.raises(mcp_server.ApiError, match="It may still start: keel_flows"):
        mcp_server.start_flow(client, "feature", "Player ranks", sleep=lambda s: None)


# ---------------------------------------------------------------- confirmations without forms (bug 13)

def token_of(text: str) -> str:
    return text.split('confirm: "', 1)[1].split('"', 1)[0]


def acted(api: Api) -> list:
    return [p for p in api.posts if not p[0].endswith("/plugins/ask")]


async def test_a_client_without_forms_gets_a_one_time_token():
    api = Api()
    first = await call(server(api, level="admin"), "keel_stop_flow", {"thread_id": "t-1"})
    assert "cannot ask you to confirm stop the flow" in first and "--yes" not in first    # 0.16.0's opening words stay
    assert token_of(first).startswith("ok-") and not acted(api)


async def test_the_confirm_text_tells_the_model_never_to_confirm_by_itself():
    for inbox_only in (False, True):
        text = mcp_confirm.ask_text("keel_stop_flow", "stop the flow", "ok-x", "p_1" if inbox_only else None, inbox_only)
        assert "never confirm by yourself" in text and "--yes" not in text
    assert "--yes" not in mcp_confirm.bad_text("keel_stop_flow", "stop the flow", "ok-x")


async def test_a_token_for_a_kind_that_is_not_dangerous_still_works_once(monkeypatch):
    monkeypatch.setattr(mcp_confirm, "OUT_OF_BAND", set())       # stop the flow as a plain kind here
    api = Api()
    srv = server(api, level="admin")
    first = await call(srv, "keel_stop_flow", {"thread_id": "t-1"})
    assert "This needs the person's OK: stop the flow. Ask the person" in first and not acted(api)
    assert "keel also put the question in its Inbox" in first and api.posts[-1][0] == "/api/projects/shop/plugins/ask"
    token = token_of(first)
    # another call's arguments do not take it, and it is used up
    other = await call(srv, "keel_stop_flow", {"thread_id": "t-2", "confirm": token})
    assert "not valid for this call" in other and not acted(api)
    token = token_of(await call(srv, "keel_stop_flow", {"thread_id": "t-1"}))
    done = await call(srv, "keel_stop_flow", {"thread_id": "t-1", "confirm": token})
    assert acted(api) == [("/api/threads/t-1/stop", {})] and "not valid" not in done
    again = await call(srv, "keel_stop_flow", {"thread_id": "t-1", "confirm": token})
    assert "not valid for this call" in again and len(acted(api)) == 1


async def test_a_dangerous_kind_waits_for_the_persons_ok_in_keels_inbox(monkeypatch):
    monkeypatch.delenv("KEEL_MCP_CONFIRM", raising=False)
    api = Api()
    srv = server(api, level="admin")
    first = await call(srv, "keel_stop_flow", {"thread_id": "t-1"})
    assert "keel put the question in its Inbox (p_000000000001)" in first and "The token alone does not do it" in first
    ask = api.posts[-1][1]
    assert ask["title"].endswith(": stop the flow?") and "keel_stop_flow(" in ask["command"] and "thread_id='t-1'" in ask["command"]
    token = token_of(first)
    # the model sends the token back by itself: nothing happens while the question waits, and the token stays open
    early = await call(srv, "keel_stop_flow", {"thread_id": "t-1", "confirm": token})
    assert "has not approved stop the flow in keel's Inbox yet" in early and not acted(api)
    api.asks["p_000000000001"] = {"decision": "allow", "why": ""}       # the person approves it in the Inbox
    done = await call(srv, "keel_stop_flow", {"thread_id": "t-1", "confirm": token})
    assert acted(api) == [("/api/threads/t-1/stop", {})] and "not done" not in done
    # a no in the Inbox is a no
    token = token_of(await call(srv, "keel_stop_flow", {"thread_id": "t-1"}))
    api.asks["p_000000000002"] = {"decision": "deny", "why": "not now"}
    no = await call(srv, "keel_stop_flow", {"thread_id": "t-1", "confirm": token})
    assert "said no to stop the flow in keel's Inbox (not now)" in no and len(acted(api)) == 1


async def test_a_dangerous_kind_with_no_inbox_is_refused(monkeypatch):
    monkeypatch.delenv("KEEL_MCP_CONFIRM", raising=False)
    api = Api()
    api.inbox = False
    text = await call(server(api, level="admin"), "keel_stop_flow", {"thread_id": "t-1"})
    assert "could not put the question in its Inbox" in text and "confirm it in keel's dashboard" in text
    assert 'confirm: "' not in text and not acted(api)


async def test_keel_mcp_confirm_token_lets_the_token_alone_confirm_again(monkeypatch):
    monkeypatch.setenv("KEEL_MCP_CONFIRM", "token")
    api = Api()
    srv = server(api, level="admin")
    first = await call(srv, "keel_stop_flow", {"thread_id": "t-1"})
    assert "The token alone does not do it" not in first
    await call(srv, "keel_stop_flow", {"thread_id": "t-1", "confirm": token_of(first)})
    assert acted(api) == [("/api/threads/t-1/stop", {})]


def test_the_dangerous_kinds():
    for name in ("keel_delete_run", "keel_remove_project", "keel_stop_flow", "keel_stop_flows", "keel_pause",
                 "keel_agent_model", "keel_git_push", "keel_db_change", "keel_stack_remove", "keel_keelbot_undo"):
        assert mcp_confirm.out_of_band(name), name
    assert mcp_confirm.out_of_band("keel_run_action", "open_pr") and not mcp_confirm.out_of_band("keel_run_action", "verify_fast")
    assert not mcp_confirm.out_of_band("keel_rule_create")


async def test_a_token_ends_after_its_time_and_only_asking_tools_take_one(monkeypatch):
    monkeypatch.setenv("KEEL_MCP_CONFIRM", "token")
    now = [1000.0]
    monkeypatch.setattr(mcp_confirm.time, "monotonic", lambda: now[0])
    api = Api()
    srv = server(api, level="admin")
    first = await call(srv, "keel_stop_flow", {"thread_id": "t-1"})
    token = token_of(first)
    assert "within 10 minutes" in first                       # it has a question in the Inbox: as long as that waits
    now[0] += mcp_confirm.INBOX_TTL + 1
    late = await call(srv, "keel_stop_flow", {"thread_id": "t-1", "confirm": token})
    assert "too old" in late and not acted(api)
    async with create_connected_server_and_client_session(srv) as s:
        tools = {t.name: t.inputSchema for t in (await s.list_tools()).tools}
    assert "confirm" in tools["keel_stop_flow"]["properties"] and "confirm" in tools["keel_agent_model"]["properties"]
    assert "confirm" not in tools["keel_status"]["properties"] and "confirm" not in tools["keel_start_flow"]["properties"]


async def test_yes_and_a_form_work_as_before():
    api = Api()
    await call(server(api, level="admin", yes=True), "keel_stop_flow", {"thread_id": "t-1"})
    assert api.posts == [("/api/threads/t-1/stop", {})]

    async def say_yes(_ctx, params):
        from mcp.types import ElicitResult
        return ElicitResult(action="accept", content={"confirm": True})
    api = Api()
    async with create_connected_server_and_client_session(server(api, level="admin"), elicitation_callback=say_yes) as s:
        res = await s.call_tool("keel_stop_flow", {"thread_id": "t-1"})
    assert not res.isError and api.posts == [("/api/threads/t-1/stop", {})]
