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
        if opts.get("reasoning_effort") and opts.get("model") in FakeClient.no_effort:
            raise RuntimeError(f'Execution failed: Model "{opts["model"]}" does not support reasoning effort.')
        s = FakeSession(opts, FakeClient.scripts.pop(0))
        FakeClient.sessions.append(s)
        return s

    async def get_auth_status(self):
        return SimpleNamespace(isAuthenticated=True, login="demo-user", statusMessage="ok")

    listed: list = []
    no_effort: set = set()

    async def list_models(self):
        return list(FakeClient.listed)


@pytest.fixture(autouse=True)
def fresh():
    copilot_sdk._clients.clear()
    copilot_sdk._models.clear()
    FakeClient.sessions, FakeClient.scripts, FakeClient.missing = [], [], set()
    FakeClient.listed, FakeClient.no_effort = [], set()
    copilot_sdk._models_file().unlink(missing_ok=True)
    yield
    copilot_sdk._models_file().unlink(missing_ok=True)


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


def test_a_models_efforts_come_from_copilot_reach_the_picker_and_the_session_and_stay_after_a_restart():
    from keel_engine.models import catalog

    FakeClient.listed = [
        SimpleNamespace(id="claude-sonnet-5.5", name="Claude Sonnet 5.5", supported_reasoning_efforts=["low", "medium", "high"]),
        SimpleNamespace(id="gpt-4.1", name="GPT-4.1", supported_reasoning_efforts=None),
    ]
    run(copilot_sdk.list_models("gho_demo", factory=FakeClient, fresh=True))
    row = catalog.build_catalog()["copilot"]
    by = {m["id"]: m["efforts"] for m in row["modes"]["api"] if "badge" not in m}
    assert by == {"auto": [], "claude-sonnet-5.5": ["low", "medium", "high"], "gpt-4.1": []}
    assert row["efforts"] == ["low", "medium", "high"]
    # the effort the person picks is kept for a model that takes it, dropped for one that does not, passed on for one
    # keel has not seen in a list
    eff = lambda m, e: catalog.effort_for({"provider": "copilot", "mode": "api", "model": m, "effort": e})  # noqa: E731
    assert eff("claude-sonnet-5.5", "high") == "high" and eff("claude-sonnet-5.5", "max") is None
    assert eff("gpt-4.1", "high") is None and eff("auto", "high") is None and eff("typed-by-hand", "high") == "high"
    # ... and reaches the Copilot session
    FakeClient.scripts = [[("say", "ok")]]
    run(provider("claude-sonnet-5.5").turn(Request(system="s", messages=[user("hi")], tools=[], effort="high"), lambda *a, **k: None))
    assert FakeClient.sessions[0].opts["reasoning_effort"] == "high"
    # after a restart (nothing in memory) the list is still the plan's, not only auto
    copilot_sdk._models.clear()
    assert [m["id"] for m in catalog.build_catalog()["copilot"]["modes"]["api"]][:3] == ["auto", "claude-sonnet-5.5", "gpt-4.1"]


def test_every_model_copilot_knows_is_offered_the_plans_first():
    from keel_engine.models import catalog

    FakeClient.listed = [SimpleNamespace(id="claude-sonnet-5.5", name="Claude Sonnet 5.5", supported_reasoning_efforts=["high"])]
    run(copilot_sdk.list_models("gho_demo", factory=FakeClient, fresh=True))
    ids = [m["id"] for m in catalog.build_catalog()["copilot"]["modes"]["api"]]
    assert ids[:2] == ["auto", "claude-sonnet-5.5"] and ids.count("claude-sonnet-5.5") == 1
    assert {"claude-opus-5.5", "gpt-6.1-sol", "gemini-4-argon"} <= set(ids)
    rows = {m["id"]: m for m in catalog.build_catalog()["copilot"]["modes"]["api"]}
    assert rows["claude-opus-5.5"]["badge"] == "not in your plan's list" and "badge" not in rows["claude-sonnet-5.5"]
    # an unconfirmed model passes the effort on (Copilot checks it); auto takes none
    assert catalog.efforts_for("copilot", "api", "claude-opus-5.5") is None and catalog.efforts_for("copilot", "api", "auto") == []


def test_a_free_plan_with_an_empty_list_still_offers_every_model():
    from keel_engine.models import catalog

    run(copilot_sdk.list_models("gho_demo", factory=FakeClient, fresh=True))
    ids = [m["id"] for m in catalog.build_catalog()["copilot"]["modes"]["api"]]
    assert ids[0] == "auto" and len(ids) == 1 + len(copilot_sdk.KNOWN_MODELS)


def test_an_effort_the_model_refuses_runs_without_it_and_says_so():
    FakeClient.no_effort = {"typed-by-hand"}
    FakeClient.scripts = [[("say", "ok")]]
    turn = run(provider("typed-by-hand").turn(Request(system="s", messages=[user("hi")], tools=[], effort="high"), lambda *a, **k: None))
    assert "reasoning_effort" not in FakeClient.sessions[0].opts and "does not take the effort 'high'" in turn.note


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
