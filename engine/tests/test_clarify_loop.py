"""The explorer asks its questions as buttons; the answers go back to the same explorer; then the spec is written."""
from keel_engine.runtime import clarify
from conftest import decide, start, wait

QUESTIONS = [
    {"id": "identity", "question": "What does \"user\" mean here?", "why": "scope differs a lot",
     "options": [{"label": "A · Named entity", "description": "name + email", "recommended": True},
                 {"label": "B · Login", "description": "needs its own spec"}]},
    {"id": "where", "question": "Where does the filter run?",
     "options": [{"label": "In the browser"}, {"label": "On the server"}]},
]
BLOCK = "I read the README and src/.\n\n```keel-questions\n" + __import__("json").dumps(QUESTIONS) + "\n```\n"


def test_parse_keeps_valid_questions_and_marks_a_recommended_option():
    qs = clarify.parse_questions(BLOCK)
    assert [q["id"] for q in qs] == ["identity", "where"]
    assert qs[0]["options"][0]["recommended"] and qs[1]["options"][0]["recommended"], "the first option is recommended when none is"
    assert clarify.parse_questions("no block here") == []
    assert clarify.parse_questions("```keel-questions\n{not json}\n```") == []
    many = [{"question": f"q{i}", "options": [{"label": "a"}, {"label": "b"}]} for i in range(9)]
    assert len(clarify.parse_questions("```keel-questions\n" + __import__("json").dumps(many) + "\n```")) == clarify.MAX_QUESTIONS


def test_answers_text_uses_the_recommended_option_for_unanswered_questions():
    qs = clarify.parse_questions(BLOCK)
    t = clarify.answers_text(qs, {"identity": "C · Permissions"}, "keep it small", 1)
    assert t.startswith("Answers to your questions:") and "→ C · Permissions" in t
    assert "not answered: use In the browser" in t and "keep it small" in t and "Do not ask again" not in t
    assert "Do not ask again" in clarify.answers_text(qs, {}, "", clarify.MAX_ROUNDS)


def test_questions_become_a_gate_and_the_answers_reach_the_explorer(client, repo, monkeypatch):
    from keel_engine.models import fake as fake_mod
    real = fake_mod._plan
    seen = []

    def plan(req):
        if req.agent == "explorer" and req.phase == "spec" and req.step_name == "spec":
            seen.append(req.feedback)
            if len(seen) == 1:
                return None, "", BLOCK, {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    tid = start(client, repo, acs=None)
    s = wait(client, tid)
    w = s["waiting"]
    assert w["kind"] == "clarify" and w["title"].startswith("The explorer has 2 questions")
    assert [q["id"] for q in w["questions"]] == ["identity", "where"] and w["options"] == ["approve"]
    assert w["labels"] == {"approve": "Send my answers"} and s["acs"] == []

    s = decide(client, tid, "approve", why="no passwords please", payload={"answers": {"identity": "A · Named entity"}})
    assert len(seen) == 2 and seen[1].startswith("Answers to your questions:")
    assert "→ A · Named entity" in seen[1] and "not answered: use In the browser" in seen[1] and "no passwords please" in seen[1]
    assert s["waiting"]["step"] == "spec_gate" and s["waiting"].get("kind") != "clarify", "the spec itself is next"
    assert s["acs"], "criteria exist after the answers"


def test_the_third_round_is_never_asked(client, repo, monkeypatch):
    from keel_engine.models import fake as fake_mod
    real = fake_mod._plan

    def always_asks(req):
        if req.agent == "explorer" and req.step_name == "spec" and not (req.feedback or "").count("Do not ask again"):
            return None, "", BLOCK, {}
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", always_asks)
    tid = start(client, repo, acs=None)
    s = wait(client, tid)
    for _ in range(clarify.MAX_ROUNDS):
        assert s["waiting"]["kind"] == "clarify"
        s = decide(client, tid, "approve", payload={"answers": {}})
    assert s["waiting"].get("kind") != "clarify" and s["acs"], s["waiting"]
