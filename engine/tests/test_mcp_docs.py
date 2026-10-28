"""keel 0.18.64 keel's MCP tools for a flow's document: keel_flow_document reads the clarify document (or spec) from its
file and, at the write level, replaces it while the flow waits; keel_reject_with_document sends the clarify result back
with a replacement draft."""

import asyncio
import copy
import json

import httpx
import pytest

from keel_engine import mcp_docs, mcp_server
from test_mcp import THREAD, StubApi, call

REL = ".keel/specs/clarify/2026-10-28-player-ranks.md"
TEXT = "# Player ranks\n\n## Acceptance criteria\n\n- **AC-1** [API] Given scores, when ranks run, then each has one\n"
DOC = {"thread_id": "t-1", "path": REL, "kind": "clarify", "exists": True, "text": TEXT, "edited": False, "waiting": "result"}
RESULT = {"step": "result", "name": "clarify result", "kind": "gate", "title": "clarify result", "id": "q-result",
          "options": ["approve"], "choices": ["feature", "small", "prompt", "reject", "stop"],
          "detail": f"Clarify document: {REL} (1 acceptance criteria)"}


class Api(StubApi):
    """The stub api with the document routes; `after` is the thread a resume answers with."""

    def __init__(self, waiting=RESULT, after: dict | None = None):
        super().__init__({**copy.deepcopy(THREAD), "workflow_id": "clarify", "current": waiting["step"], "waiting": waiting})
        self.after = after

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/threads/t-1/document":
            if req.method == "PUT":
                body = json.loads(req.content)
                self.posts.append((path, body))
                return httpx.Response(200, json={**DOC, "text": body["text"], "edited": True, "edited_at": "2026-10-28 14:05 CET"})
            return httpx.Response(200, json=DOC)
        if req.method == "POST" and path == "/api/threads/t-1/resume" and self.after is not None:
            self.posts.append((path, json.loads(req.content)))
            return httpx.Response(200, json=self.after)
        if req.method == "GET" and path == "/api/projects/shop/flows/t-1":
            return httpx.Response(200, json={"thread": self.after or self.thread, "workflow": {"id": "clarify"}})
        return super().__call__(req)


def server(api: Api, level: str = "read"):
    return mcp_server.build_server(level=level, api=mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(api)))


@pytest.fixture(autouse=True)
def here(monkeypatch):
    monkeypatch.setenv("KEEL_PROJECT", "shop")      # a write needs its project named when the folder matches none
    monkeypatch.chdir("/")


async def test_flow_document_reads_the_file_and_says_which_copy_it_is():
    text = await call(server(Api()), "keel_flow_document")
    head, body = text.split("\n\n", 1)
    assert f"document  {REL} · the clarify document of \"Player scores\" · thread t-1 · waits at result" in head
    assert "as keel wrote it; the hand-over uses the file as it is when the person chooses" in head
    assert body == TEXT


async def test_replacing_the_document_needs_the_write_level_and_is_the_persons_edit():
    api = Api()
    refused = await call(server(api, "read"), "keel_flow_document", {"text": TEXT + "- **AC-2** [API] more\n"})
    assert "needs the write level: run keel2 mcp --write" in refused and api.posts == []
    done = await call(server(api, "write"), "keel_flow_document", {"text": TEXT + "- **AC-2** [API] more\n"})
    assert api.posts == [("/api/threads/t-1/document", {"text": TEXT + "- **AC-2** [API] more\n"})]
    assert done.startswith(f"{REL}: replaced") and "records it as your edit" in done and "a hand-over uses it" in done


async def test_reject_with_document_sends_the_draft_with_the_reject_and_says_what_the_check_did():
    passed = {**copy.deepcopy(THREAD), "workflow_id": "clarify", "current": "result",
              "waiting": {**RESULT, "detail": f"Clarify document: {REL}\nThis is your own draft (sent with the reject at 14:05)"}}
    api = Api(after=passed)
    srv = server(api, "write")
    text = await call(srv, "keel_reject_with_document", {"text": TEXT, "why": "AC-1 names the tie rule"})
    assert api.posts == [("/api/threads/t-1/resume", {"decision": "approve", "why": "AC-1 names the tie rule",
                                                      "payload": {"choice": "reject", "document": TEXT, "question_id": "q-result"}})]
    assert "the spec check passed, and the result gate shows it again (no assemble pass)" in text
    # no note: the draft goes with one that says to keep it
    await call(srv, "keel_reject_with_document", {"text": TEXT})
    assert api.posts[-1][1]["why"] == mcp_docs.DRAFT_NOTE


async def test_reject_with_document_is_for_the_clarify_result_only():
    api = Api(waiting={**RESULT, "step": "spec_gate", "title": "spec approval", "choices": ["approve", "edit", "reject"]})
    text = await call(server(api, "write"), "keel_reject_with_document", {"text": TEXT})
    assert "is for a clarify flow's result gate (this flow waits at spec_gate)" in text and api.posts == []


def test_the_levels_of_the_two_tools():
    names = lambda level: {t.name for t in asyncio.run(mcp_server.build_server(level=level, api=object()).list_tools())}
    assert "keel_flow_document" in names("read") and "keel_reject_with_document" not in names("read")
    assert {"keel_flow_document", "keel_reject_with_document"} <= names("write")
    assert mcp_server.tool_level("keel_flow_document") == "read" and mcp_server.tool_level("keel_reject_with_document") == "write"


def test_reject_with_document_while_keel_still_checks_says_where_to_look():
    running = {**copy.deepcopy(THREAD), "workflow_id": "clarify", "status": "running", "waiting": None, "current": "redraft"}
    api = Api(after=running)
    text = mcp_docs.reject_with_document(mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(api)), TEXT,
                                         sleep=lambda _s: None, wait=0)
    assert "keel wrote it to the file and checks it now" in text and "keel_status" in text


async def test_handover_writes_the_document_first_then_chooses_the_flow_and_names_the_new_one():
    done = {**copy.deepcopy(THREAD), "workflow_id": "clarify", "status": "done", "waiting": None, "current": "to_feature",
            "children": [{"thread_id": "t-7", "workflow": "feature", "step": "to_feature", "title": "Player scores"}]}
    api = Api(after=done)
    text = await call(server(api, "write"), "keel_handover", {"flow": "feature", "text": TEXT})
    assert api.posts == [("/api/threads/t-1/document", {"text": TEXT}),
                         ("/api/threads/t-1/resume", {"decision": "approve",
                                                      "payload": {"choice": "feature", "question_id": "q-result"}})]
    assert f"keel wrote your document to {REL} first" in text and "the feature flow is thread t-7" in text
    assert "clarify t-1 → children [t-7] (feature); feature t-7 → parent t-1" in text


async def test_handover_takes_only_a_flow_the_gate_offers():
    api = Api(after={**copy.deepcopy(THREAD), "status": "done", "waiting": None})
    text = await call(server(api, "write"), "keel_handover", {"flow": "fix"})
    assert "does not offer fix (its task type fits feature, small)" in text and api.posts == []
    # no text: the file as it is goes over, nothing is written first
    await call(server(api, "write"), "keel_handover", {"flow": "small", "why": "it is one file"})
    assert api.posts == [("/api/threads/t-1/resume", {"decision": "approve", "why": "it is one file",
                                                      "payload": {"choice": "small", "question_id": "q-result"}})]
