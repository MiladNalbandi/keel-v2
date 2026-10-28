"""0.18.64 every agent call keeps the prompt it got (agent.prompt, read by the api's GET /jobs/{id}/prompt and the MCP
tool keel_agent_prompt): a field report could not tell whether a retry's prompt still had the person's reject note."""

from conftest import start, wait
from keel_engine.runtime import compiler


def test_each_agent_call_sends_its_system_and_task_prompt_once(client, repo):
    tid = start(client, repo)
    s = wait(client, tid)
    assert s["status"] == "waiting", s
    started = {e["call_id"]: e for e in client.bus.of(tid, "agent.started")}
    prompts = client.bus.of(tid, "agent.prompt")
    assert started and sorted(e["call_id"] for e in prompts) == sorted(started)       # one per call, the same ids
    for e in prompts:
        d = e["data"]
        assert e["step"] == started[e["call_id"]]["step"]
        assert d["agent"] == started[e["call_id"]]["data"]["agent"]
        assert d["system"].strip() and d["prompt"].strip()
        assert "Player ranks" in d["prompt"]                                           # the flow's title is in the task
        assert len(d["prompt"]) <= compiler.PROMPT_RECORD_MAX and d["prompt_cut"] is False
