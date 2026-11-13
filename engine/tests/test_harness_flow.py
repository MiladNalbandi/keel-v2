"""#101 a real keel flow whose agent step runs in keel's own harness (the model scripted, everything else real): the
runtime picks the harness for an API model, the agent's tool calls change the project under keel's rules, its steps and
usage arrive as events, and a step that runs again continues its own session."""

import pytest
from conftest import start, wait

from keel_engine.harness import providers as providers_mod
from keel_engine.harness.providers.base import END, TOOL_USE, Turn, Usage
from keel_engine.harness.runner import HarnessRunner
from keel_engine.models import runner_for
from keel_engine.workflows.model import from_dict

ONE_STEP = {"name": "one", "keel_rules": False, "own_branch": False,
            "steps": [{"id": "work", "kind": "agent", "name": "do the work", "agent": "implementer", "phase": "green"}]}
MODEL = {"default": {"provider": "codex", "mode": "subscription", "model": "gpt-5.5"}}


class Script:
    name, model = "openai", "gpt-5.5"

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []

    async def turn(self, req, emit):
        self.requests.append(req)
        return self.turns.pop(0)


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def test_every_real_model_runs_in_the_harness():
    assert isinstance(runner_for({"provider": "claude", "mode": "subscription", "model": "opus"}), HarnessRunner)
    assert isinstance(runner_for({"provider": "copilot", "mode": "opencode", "model": "gpt-5"}), HarnessRunner)
    assert type(runner_for({"provider": "fake", "mode": "api", "model": "fake"})).__name__ == "FakeRunner"


def test_a_flow_step_runs_in_the_harness_and_writes_under_keels_rules(client, repo, real, monkeypatch):
    script = Script([
        Turn([{"type": "tool_call", "id": "c1", "name": "Write", "input": {"file_path": "src/scores/notes.py", "content": "HARNESS = True\n"}}],
             TOOL_USE, Usage(input=500, cache_read=1500, output=40)),
        Turn([{"type": "text", "text": "Wrote notes.py."}], END, Usage(input=100, output=10)),
    ])
    seen_keys = []

    def resolve(model, key):
        seen_keys.append((model.get("provider"), model.get("mode"), key))
        return script

    monkeypatch.setattr(providers_mod, "resolve", resolve)
    tid = start(client, repo, workflow=from_dict(ONE_STEP), models=MODEL, keys={"codex": "sk-from-keel"})
    s = wait(client, tid)
    assert s["status"] == "done", s
    steps = [(e["data"]["kind"], e["data"].get("text", "")[:200]) for e in client.bus.of(tid, "agent.step")]
    assert (repo / "src/scores/notes.py").exists(), steps
    assert (repo / "src/scores/notes.py").read_text() == "HARNESS = True\n"
    # a model saved with a subscription mode runs on its provider's API key
    assert seen_keys == [("codex", "api", "sk-from-keel")]
    started = client.bus.of(tid, "agent.started")[0]["data"]
    assert started["provider"] == "codex" and started["mode"] == "api"
    kinds = [e["data"]["kind"] for e in client.bus.of(tid, "agent.step")]
    assert "write" in kinds and kinds[-1] == "answer"
    done = client.bus.of(tid, "agent.finished")[0]["data"]
    assert done["tokens_out"] == 50 and done.get("tokens_cached", 0) == 1500
