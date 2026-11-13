"""'Approve to try again' on a failed agent step costs one new run, not one more than the last time."""
from conftest import decide, start, wait
from keel_engine.models import fake as fake_mod
from keel_engine.models.base import ModelError
from test_flows_base import wf


def test_each_try_again_approval_runs_the_agent_once(client, repo, monkeypatch):
    calls = []

    async def run(self, req, emit):
        if req.agent == "explorer":
            calls.append(1)
            raise ModelError("the model is down")
        return await real(self, req, emit)

    real = fake_mod.FakeRunner.run
    monkeypatch.setattr(fake_mod.FakeRunner, "run", run)
    flow = wf([{"id": "work", "kind": "agent", "name": "work", "agent": "explorer"}])
    tid = start(client, repo, workflow=flow)
    s = wait(client, tid)
    assert s["waiting"]["title"] == "work failed" and len(calls) == 1
    # #101 part 3 the answer is read before the step runs again (flowdoctor repair.answer_first): one run for every
    # approval, the same for the first and the tenth. Before, a resume first ran the node again from its top (two runs
    # per approval), and before that approval k cost k + 1 runs.
    for j in (1, 2, 3):
        s = decide(client, tid, "approve")
        assert s["waiting"]["title"] == "work failed", s
        assert len(calls) == 1 + j, f"after {j} approvals the agent ran {len(calls)} times"
