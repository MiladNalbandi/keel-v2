"""0.18.64 the field report's MCP findings (bugs 12 and 13): keel_answer says the choice that ran, keel_start_flow
answers at once, and a client without forms confirms with a one-time token instead of `keel2 mcp --yes`."""

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

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "GET":
            self.gets.append(path)
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

async def test_a_client_without_forms_confirms_with_a_one_time_token():
    api = Api()
    srv = server(api, level="admin")
    first = await call(srv, "keel_stop_flow", {"thread_id": "t-1"})
    assert "This needs your OK: stop the flow. Ask the person" in first and not api.posts
    assert "cannot ask you to confirm stop the flow" in first and "--yes" in first      # 0.16.0's words stay
    token = first.split('confirm: "', 1)[1].split('"', 1)[0]
    assert token.startswith("ok-")
    # another call's arguments do not take it, and it is used up
    other = await call(srv, "keel_stop_flow", {"thread_id": "t-2", "confirm": token})
    assert "not valid for this call" in other and not api.posts
    first = await call(srv, "keel_stop_flow", {"thread_id": "t-1"})
    token = first.split('confirm: "', 1)[1].split('"', 1)[0]
    done = await call(srv, "keel_stop_flow", {"thread_id": "t-1", "confirm": token})
    assert api.posts == [("/api/threads/t-1/stop", {})] and "not valid" not in done
    again = await call(srv, "keel_stop_flow", {"thread_id": "t-1", "confirm": token})
    assert "not valid for this call" in again and len(api.posts) == 1


async def test_a_token_ends_after_five_minutes_and_only_asking_tools_take_one(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(mcp_confirm.time, "monotonic", lambda: now[0])
    api = Api()
    srv = server(api, level="admin")
    first = await call(srv, "keel_stop_flow", {"thread_id": "t-1"})
    token = first.split('confirm: "', 1)[1].split('"', 1)[0]
    now[0] += mcp_confirm.TTL + 1
    late = await call(srv, "keel_stop_flow", {"thread_id": "t-1", "confirm": token})
    assert "older than 5 minutes" in late and not api.posts
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
