"""keel init asks its three questions with defaults, shows the real plan, and records the answers."""
from pathlib import Path

import yaml

from conftest import decide, start, wait
from keel_engine.runtime import init_gates


def test_questions_plan_and_answers_reach_the_config(client, repo):
    tid = start(client, repo, workflow="init", title="keel init")
    s = wait(client, tid)
    while s["status"] == "waiting" and s["waiting"]["step"] != "questions":
        s = decide(client, tid)
    w = s["waiting"]
    assert w["title"] == "three questions" and "1. Where does the app run" in w["detail"] and "3. Which knowledge sections" in w["detail"]
    assert w["labels"] == {"approve": "Use the defaults", "reject": "Use my answers"}
    s = decide(client, tid, "reject", why="1 dev container, 3 conventions and data")
    w = s["waiting"]
    assert w["step"] == "plan_gate"
    assert "knowledge sections conventions, data" in w["detail"] and "runs_on: dev container" in w["detail"]
    assert "chore(setup): keel init" in w["detail"]
    while s["status"] == "waiting":
        s = decide(client, tid)
    cfg = yaml.safe_load((Path(repo) / ".keel" / "config.yml").read_text())
    assert cfg["init"]["knowledge_sections"] == ["conventions", "data"] and cfg["init"]["runs_on"] == "dev container"


def test_answers_are_read_from_free_text(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    d = init_gates.defaults(str(tmp_path))
    assert d["services"] == "use docker-compose.yml" and d["knowledge_sections"] == init_gates.SECTIONS
    a = init_gates.answers(str(tmp_path), "later; I run them myself; sections: none")
    assert a["runs_on"] == "decide later" and a["services"] == "I run them myself" and a["knowledge_sections"] == []
    assert "docker-compose.yml exists" in init_gates.questions(str(tmp_path))


def test_init_starts_one_librarian_per_chosen_section(client, repo, monkeypatch):
    from keel_engine.models import fake as fake_mod
    real = fake_mod._plan
    got = []

    def plan(req):
        if req.agent == "librarian":
            got.append(req.section)
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    tid = start(client, repo, workflow="init", title="keel init")
    s = wait(client, tid)
    while s["status"] == "waiting":
        s = decide(client, tid, "reject", why="3 conventions, data") if s["waiting"]["step"] == "questions" else decide(client, tid)
    assert s["status"] == "done", s
    assert sorted(got) == ["conventions", "data"]
