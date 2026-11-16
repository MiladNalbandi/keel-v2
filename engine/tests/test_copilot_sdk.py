"""GitHub Copilot (your plan): keel's harness turns over the Copilot SDK, with a fake SDK client (no network).

The live test at the bottom runs only with KEEL_LIVE_COPILOT=1 and a GitHub token in GHT.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from keel_engine.harness.providers import api_model, copilot_model, copilot_sdk
from keel_engine.harness.providers.base import (
    END,
    TOOL_USE,
    ProviderError,
    Request,
    ToolSpec,
)

# ---------------------------------------------------------------- a fake SDK: plain objects named like the SDK's events

class AssistantMessageDeltaData:
    def __init__(self, d):
        self.delta_content = d


class AssistantMessageData:
    def __init__(self, c):
        self.content = c


class AssistantUsageData:
    def __init__(self, i, o):
        self.input_tokens, self.output_tokens, self.cache_read_tokens, self.cache_write_tokens = i, o, 0, 0
        self.model = "fake-model"


class SessionIdleData:
    pass


class SessionErrorData:
    def __init__(self, m):
        self.message = m


def ev(data):
    return SimpleNamespace(agent_id=None, data=data)


class FakeSession:
    def __init__(self, opts, script):
        self.opts, self.script, self.cb, self.sent, self.closed = opts, script, None, [], False
        self.results: list = []

    def on(self, cb):
        self.cb = cb

    async def send(self, prompt):
        self.sent.append(prompt)
        steps, self.script = self.script, [("say", "")]
        asyncio.get_running_loop().create_task(self._play(steps))

    async def _play(self, steps):
        await asyncio.sleep(0)
        tools = {t.name: t for t in self.opts.get("tools") or []}
        for step in steps:
            kind = step[0]
            if kind == "say":
                self.cb(ev(AssistantMessageDeltaData(step[1])))
            elif kind == "calls":
                outs = await asyncio.gather(*[tools[n].handler(SimpleNamespace(tool_call_id=f"c{i}", arguments=a))
                                              for i, (n, a) in enumerate(step[1])])
                self.results.append([o.text_result_for_llm for o in outs])
            elif kind == "usage":
                self.cb(ev(AssistantUsageData(*step[1])))
            elif kind == "error":
                self.cb(ev(SessionErrorData(step[1])))
                return
        self.cb(ev(SessionIdleData()))

    async def disconnect(self):
        self.closed = True


class FakeClient:
    sessions: list = []
    scripts: list = []
    missing: set = set()

    def __init__(self, **kw):
        self.kw = kw

    async def start(self):
        pass

    async def create_session(self, **opts):
        if opts.get("model") in FakeClient.missing:
            raise RuntimeError(f'Execution failed: Model "{opts["model"]}" is not available.')
        s = FakeSession(opts, FakeClient.scripts.pop(0))
        FakeClient.sessions.append(s)
        return s

    async def get_auth_status(self):
        return SimpleNamespace(isAuthenticated=True, login="demo-user", statusMessage="ok")

    async def list_models(self):
        return []


@pytest.fixture(autouse=True)
def fresh():
    copilot_sdk._clients.clear()
    copilot_sdk._models.clear()
    FakeClient.sessions, FakeClient.scripts, FakeClient.missing = [], [], set()
    yield


def provider(model="auto", token="gho_demo"):
    return copilot_sdk.CopilotProvider(model, token, factory=FakeClient)


READ = ToolSpec("Read", "Read a file", {"type": "object", "properties": {"file_path": {"type": "string"}}})
GREP = ToolSpec("Grep", "Search", {"type": "object", "properties": {"pattern": {"type": "string"}}})


def user(text):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- tests

def test_a_brain_call_without_tools_answers_and_streams():
    FakeClient.scripts = [[("say", "O"), ("say", "K"), ("usage", (12, 3))]]
    p, seen = provider(), []
    turn = run(p.turn(Request(system="sys", messages=[user("Reply with exactly: OK")], tools=[]),
                      lambda kind, *a, **k: seen.append((kind, a))))
    assert turn.stop == END and turn.text() == "OK"
    assert ("delta", ("O",)) in seen and turn.usage.input == 12 and turn.usage.output == 3
    s = FakeClient.sessions[0]
    # none of Copilot's own tools; keel's system prompt replaces Copilot's
    assert s.opts["available_tools"] == [] and s.opts["system_message"] == {"mode": "replace", "content": "sys"}
    assert s.closed                         # an answered conversation ends its SDK session


def test_a_tool_call_comes_back_to_keels_loop_and_its_result_goes_on():
    FakeClient.scripts = [[("say", "Let me read. "), ("calls", [("Read", {"file_path": "a.txt"})]), ("say", "It says hi.")]]
    p = provider()

    async def go():
        msgs = [user("What is in a.txt?")]
        t1 = await p.turn(Request(system="s", messages=msgs, tools=[READ], extra={"root": "/tmp/x"}), lambda *a, **k: None)
        assert t1.stop == TOOL_USE
        call = t1.tool_calls()[0]
        assert call["name"] == "Read" and call["input"] == {"file_path": "a.txt"} and t1.text() == "Let me read."
        # keel's runner ran Read through its own tools and rules; the result goes back to the waiting handler
        msgs += [{"role": "assistant", "content": t1.blocks},
                 {"role": "user", "content": [{"type": "tool_result", "id": call["id"], "content": "hi", "is_error": False}]}]
        t2 = await p.turn(Request(system="s", messages=msgs, tools=[READ]), lambda *a, **k: None)
        return t2

    t2 = run(go())
    s = FakeClient.sessions[0]
    assert t2.stop == END and t2.text() == "It says hi."
    assert s.results == [["hi"]] and len(FakeClient.sessions) == 1        # the same SDK session went on
    assert s.opts["available_tools"] == ["Read"] and s.opts["working_directory"] == "/tmp/x"
    assert all(t.overrides_built_in_tool and t.skip_permission for t in s.opts["tools"])


def test_parallel_calls_come_as_one_turn():
    FakeClient.scripts = [[("calls", [("Read", {"file_path": "a"}), ("Grep", {"pattern": "x"})]), ("say", "done")]]
    p = provider()

    async def go():
        msgs = [user("look")]
        t1 = await p.turn(Request(system="s", messages=msgs, tools=[READ, GREP]), lambda *a, **k: None)
        calls = t1.tool_calls()
        msgs += [{"role": "assistant", "content": t1.blocks},
                 {"role": "user", "content": [{"type": "tool_result", "id": c["id"], "content": f"out-{c['name']}",
                                               "is_error": False} for c in calls]}]
        t2 = await p.turn(Request(system="s", messages=msgs, tools=[READ, GREP]), lambda *a, **k: None)
        return calls, t2

    calls, t2 = run(go())
    assert [c["name"] for c in calls] == ["Read", "Grep"] and t2.text() == "done"
    assert FakeClient.sessions[0].results == [["out-Read", "out-Grep"]]


def test_a_conversation_the_session_has_not_seen_starts_one_from_the_text():
    FakeClient.scripts = [[("say", "summary")]]
    p = provider()
    msgs = [user("first ask"), {"role": "assistant", "content": [{"type": "text", "text": "an answer"}]}, user("summarize")]
    turn = run(p.turn(Request(system="s", messages=msgs, tools=[]), lambda *a, **k: None))
    sent = FakeClient.sessions[0].sent[0]
    assert turn.text() == "summary" and "Earlier in this conversation" in sent and "an answer" in sent and sent.endswith("summarize")


def test_a_model_the_plan_lacks_falls_back_to_auto_with_a_note():
    FakeClient.missing = {"gpt-9"}
    FakeClient.scripts = [[("say", "ok")]]
    turn = run(provider("gpt-9").turn(Request(system="s", messages=[user("hi")], tools=[]), lambda *a, **k: None))
    assert FakeClient.sessions[0].opts["model"] == "auto" and "no model 'gpt-9'" in turn.note


def test_errors_say_what_to_do():
    FakeClient.scripts = [[("error", "Request failed: 401 Unauthorized")]]
    with pytest.raises(ProviderError) as e:
        run(provider().turn(Request(system="s", messages=[user("hi")], tools=[]), lambda *a, **k: None))
    assert e.value.status == 401 and "Copilot Requests" in e.value.hint
    with pytest.raises(ProviderError) as e2:
        copilot_sdk.CopilotProvider("auto", "ghp_classic", factory=FakeClient)
    assert "Classic tokens" in e2.value.hint


def test_saved_github_models_names_become_auto():
    assert copilot_model("openai/gpt-5") == "auto" and copilot_model("") == "auto"
    assert copilot_model("claude-sonnet-4.5") == "claude-sonnet-4.5"
    assert api_model("copilot", "openai/gpt-4.1") == "auto"


def test_the_model_list_always_has_auto_and_fills_the_catalog():
    from keel_engine.models import catalog

    models = run(copilot_sdk.list_models("gho_demo", factory=FakeClient, fresh=True))
    assert [m["id"] for m in models] == ["auto"]
    row = catalog.build_catalog()["copilot"]
    assert row["label"] == "GitHub Copilot (your plan)" and row["modes"]["api"][0]["id"] == "auto"
    assert not any("/" in m["id"] for m in row["modes"]["api"])          # no GitHub Models names left


# ---------------------------------------------------------------- live (opt-in): a real tool-using harness run

@pytest.mark.skipif(os.environ.get("KEEL_LIVE_COPILOT") != "1" or not os.environ.get("GHT"),
                    reason="live GitHub Copilot test: KEEL_LIVE_COPILOT=1 and GHT=<token>")
def test_live_a_harness_run_reads_a_file_through_keels_read_tool(tmp_path):
    from keel_engine.harness import HarnessRunner
    from keel_engine.models.base import AgentRequest
    from keel_engine.tools.agent_tools import ToolBox

    (tmp_path / "note.txt").write_text("The secret word is pelican.\n")
    box = ToolBox(str(tmp_path), "none")
    req = AgentRequest(agent="test", system="You are keel's agent. Use keel's tools.", root=str(tmp_path), phase="none",
                       prompt="Read note.txt with the Read tool and tell me the secret word.",
                       model={"provider": "copilot", "mode": "api", "model": "auto"}, key=os.environ["GHT"],
                       toolbox=box, workdir=str(tmp_path), timeout=300)
    events = []
    res = asyncio.run(HarnessRunner().run(req, lambda kind, *a, **k: events.append((kind, a, k))))
    assert "pelican" in res.text.lower()
    assert any("note.txt" in str(a) + str(kw) for k, a, kw in events if k != "answer")
