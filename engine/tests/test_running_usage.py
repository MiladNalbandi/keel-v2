"""0.18.17 an agent's usage while it runs: the runner reports it (keel's harness after every model turn; Claude Code's
stream-json had usage on every assistant message), the engine sends agent.usage, the thread's view and the token cap see it before agent.finished books it."""

from conftest import start, wait
from keel_engine.runtime.compiler import running_usage
from keel_engine.workflows.model import from_dict
from keel_engine.harness.providers.base import ProviderError
from scripted_model import answer, scripted, tool

CLAUDE = {"default": {"provider": "claude", "mode": "subscription", "model": "haiku"}}
ONE = from_dict({"name": "one", "keel_rules": False,
                 "steps": [{"id": "impl", "kind": "agent", "name": "implement", "agent": "explorer", "phase": "spec"}]})


def assistant(mid, text, **usage):
    """An assistant line in the shape claude 2.1 prints (one line per content block, all with the message's usage)."""
    return {"type": "assistant", "message": {"id": mid, "role": "assistant", "content": [{"type": "text", "text": text}],
                                             "usage": {"input_tokens": 9, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                                                       "output_tokens": 1, "service_tier": "standard", **usage}}}


TURNS = [
    assistant("msg_01", "Reading the code.", input_tokens=3, cache_creation_input_tokens=13_605, cache_read_input_tokens=22_383, output_tokens=6),
    assistant("msg_01", "Still the same message.", input_tokens=3, cache_creation_input_tokens=13_605, cache_read_input_tokens=22_383, output_tokens=6),
    assistant("msg_02", "Editing.", input_tokens=5, cache_creation_input_tokens=1_000, cache_read_input_tokens=35_988, output_tokens=420),
]
RESULT = {"type": "result", "result": "done", "total_cost_usd": 0.05,
          "usage": {"input_tokens": 8, "cache_creation_input_tokens": 14_605, "cache_read_input_tokens": 58_371, "output_tokens": 430}}


def test_running_usage_adds_the_calls_that_still_run():
    u = running_usage({"tokens_in": 10, "tokens_out": 5, "cap_tokens": 100}, {"c1": {"tokens_in": 7, "tokens_cached": 30}})
    assert u == {"tokens_in": 17, "tokens_out": 5, "tokens_cached": 30, "cap_tokens": 100}


# #101 keel's harness reports the run's usage after every model turn
WORK = [tool("Read", 1, usage=(3, 6), more={"cache_write": 13_605, "cache_read": 22_383}, file_path="README.md"),
        tool("Grep", 2, usage=(5, 420), more={"cache_write": 1_000, "cache_read": 35_988}, pattern="score")]


def test_a_flow_sends_agent_usage_while_the_agent_runs_and_warns_past_the_cap(client, repo, monkeypatch):
    sm = scripted(monkeypatch, [*WORK, answer("done", usage=(0, 4))])
    tid = start(client, repo, workflow=ONE, models=CLAUDE, settings={"gates_mode": "every-ac", "cap_tokens": 10_000, "on_cap": "pause"})
    s = wait(client, tid)
    assert s["status"] == "done", s
    assert len(sm.calls) == 3
    [first, *_] = client.bus.of(tid, "agent.usage")             # sent at once, then at most every 15 s
    assert first["call_id"] and first["data"]["tokens_in"] == 13_608 and first["data"]["agent"] == "explorer"
    started = client.bus.of(tid, "agent.started")[0]
    assert first["call_id"] == started["call_id"]
    [warn] = [e["data"] for e in client.bus.of(tid, "budget.warn") if e["data"].get("running")]
    assert warn["cap"] == 10_000 and warn["used"] >= 10_000 and "still runs" in warn["detail"]
    done = client.bus.of(tid, "agent.finished")[-1]["data"]
    assert done["tokens_in"] == 14_613 and done["tokens_out"] == 430 and done["tokens_cached"] == 58_371


def test_a_run_that_dies_without_a_result_still_books_its_usage_so_far(client, repo, monkeypatch):
    scripted(monkeypatch, [*WORK, ProviderError("Anthropic refused the request: bad", status=400)])
    tid = start(client, repo, workflow=ONE, models=CLAUDE)
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["kind"] == "fix", s
    failed = client.bus.of(tid, "agent.finished")[-1]["data"]
    assert failed["status"] == "failed" and failed["tokens_in"] == 14_613 and failed["tokens_cached"] == 58_371
