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


def test_a_criterion_wrapped_over_lines_is_read_whole():
    text = """## Acceptance criteria

- **AC-1** [API] Given players with distinct best scores, when
  `rankOf(playerId)` is called, then it returns the 1-based rank.
- **AC-2** [WEB] Short one.
    - a nested note is not part of the title
"""
    acs = prompts.parse_acs(text)
    assert acs[0]["title"] == "Given players with distinct best scores, when `rankOf(playerId)` is called, then it returns the 1-based rank."
    assert acs[1]["title"] == "Short one."


def test_logins_can_be_given_again_after_a_restart(client, repo):
    tid = start(client, repo)
    wait(client, tid)
    svc = client.app.state.engine
    svc.keys.pop(tid, None)                      # what an engine restart does
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve", "keys": {"claude_oauth": "tok-again"}})
    assert r.status_code == 200
    assert svc.keys[tid] == {"claude_oauth": "tok-again"}


def test_green_prompt_keeps_later_criteria_out(tmp_path):
    from keel_engine.runtime.prompts import task_prompt
    acs = [{"id": "AC-1", "layer": "API", "title": "a", "status": "done"},
           {"id": "AC-2", "layer": "API", "title": "b", "status": "todo"},
           {"id": "AC-3", "layer": "API", "title": "c", "status": "todo"}]
    text = task_prompt(agent="implementer", phase="green", step_name="green", title="t", root=str(tmp_path),
                       ac=acs[1], acs=acs, feedback=None)
    assert "later criteria (AC-3)" in text and "AC-1" not in text.split("later criteria")[1].split(")")[0]


def test_librarian_gets_the_templates_and_a_short_memory_brief(tmp_path, monkeypatch):
    from keel_engine.runtime.prompts import task_prompt
    monkeypatch.setenv("KEEL_HOME", str(tmp_path))
    text = task_prompt(agent="librarian", phase="memory", step_name="memory", title="t", root="/r", ac=None, acs=[], feedback=None)
    assert f"{tmp_path}/templates/knowledge/<section>.md" in text and "Do not read keel's own source" in text
    assert "only the knowledge sections this branch changes" in text


def test_skill_references_get_full_paths(tmp_path, monkeypatch):
    from keel_engine.runtime import prompts
    ref = tmp_path / "skills" / "spec-authoring" / "references"
    ref.mkdir(parents=True)
    (ref / "acceptance-criteria.md").write_text("# AC")
    monkeypatch.setenv("KEEL_HOME", str(tmp_path))
    out = prompts.system_prompt("explorer", {"explorer": "Read `references/acceptance-criteria.md` and references/nope.md"})
    assert f"{ref}/acceptance-criteria.md" in out and "references/nope.md" in out
    assert "never search the disk" in out


def test_a_sent_back_spec_is_changed_not_rewritten_from_scratch():
    from keel_engine.runtime.prompts import task_prompt
    p = task_prompt(agent="explorer", phase="spec", step_name="spec", title="t", root="/r", ac=None, acs=[],
                    feedback="1 entity types first", spec="docs/specs/x.md")
    assert "The spec docs/specs/x.md exists from the last try" in p and "do not explore the project again" in p
