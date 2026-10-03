"""The spec gate must never offer a template placeholder as a criterion."""
from keel_engine.runtime import prompts
from conftest import start, wait


def test_placeholders_and_examples_are_not_criteria():
    text = """Pattern to copy:
```
## Acceptance criteria
- **AC-1** [API] <behaviour>
```
- **AC-2** [API] <what must be true>
- **AC-3** [WEB] Players see their rank next to the top 10
"""
    acs = prompts.parse_acs(text)
    assert [a["id"] for a in acs] == ["AC-3"]


def test_request_text_reaches_the_agents():
    p = prompts.task_prompt(agent="explorer", phase="spec", step_name="spec", title="ranks", root="/r", ac=None, acs=[],
                            feedback=None, request="Show each player's rank next to the top 10 list.")
    assert "What the user asked for:\nShow each player's rank" in p
    assert "If the request is too unclear" in p


def test_max_turns_comes_from_the_agent_file(tmp_path, monkeypatch):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "explorer.md").write_text("---\nname: explorer\nmaxTurns: 20\n---\nYou map code.\n")
    monkeypatch.setenv("KEEL_HOME", str(tmp_path))
    assert prompts.max_turns("explorer") == 20
    assert prompts.max_turns("nobody") is None


def test_a_spec_gate_without_criteria_only_allows_send_back(client, repo, monkeypatch):
    # the explorer answers without criteria and writes no spec file (like a real agent given only "test")
    from keel_engine.models import fake as fake_mod
    real = fake_mod._plan

    def no_spec(req):
        if req.agent == "explorer" and req.phase == "spec":
            return None, "", "No feature topic was named. Pattern: - **AC-1** [API] <behaviour>", {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", no_spec)
    tid = start(client, repo, acs=None)
    st = wait(client, tid)
    w = st["waiting"]
    assert w["options"] == ["reject"]
    assert "no acceptance criteria" in w["title"]
    assert "No feature topic was named" in w["detail"]
    assert st["acs"] == []
    # approving anyway still sends it back
    client.post(f"/threads/{tid}/resume", json={"decision": "approve", "why": "x"})
    st = wait(client, tid)
    assert st["acs"] == [] and st["status"] == "waiting"
