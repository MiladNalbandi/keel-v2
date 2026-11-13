"""Try again after a parallel step failed runs only the agents that failed."""
from conftest import decide, start, wait
from keel_engine.workflows.templates import get_template


def test_only_the_failed_librarian_runs_again(client, repo, monkeypatch):
    from keel_engine.models import fake as fake_mod
    real = fake_mod._plan
    calls = []

    def plan(req):
        calls.append(req.section)
        if req.section == "domain" and calls.count("domain") == 1:
            raise RuntimeError("ran out of turns")
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    wf = get_template("knowledge-refresh")
    tid = start(client, repo, workflow=wf, settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause",
                                                      "sections": ["architecture", "domain", "conventions"]})
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["kind"] == "fix"
    assert "2 of 3 agents finished" in s["waiting"]["detail"]
    assert sorted(calls) == ["architecture", "conventions", "domain"]
    s = decide(client, tid, "approve")
    assert sorted(calls) == ["architecture", "conventions", "domain", "domain"], calls


def test_knowledge_refresh_without_a_list_gives_each_librarian_a_section(client, repo, monkeypatch):
    from keel_engine.models import fake as fake_mod
    real = fake_mod._plan
    got = []

    def plan(req):
        got.append(req.section)
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    tid = start(client, repo, workflow=get_template("knowledge-refresh"))
    wait(client, tid)
    assert None not in got and sorted(got) == sorted(["architecture", "domain", "conventions", "data", "integrations"])


