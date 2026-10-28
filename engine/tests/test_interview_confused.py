"""0.18.28 the interview: an answer that asks back ("what do you mean") is no answer. keel asks that question again
once, in plain words with examples, and never falls back to the recommended option for it."""
import json

from conftest import decide, start, wait
from test_flows_base import answers, wf
from keel_engine.runtime import clarify

QUESTIONS = [
    {"id": "scope", "question": "Should the due date also update old orders?", "why": "orders already delivered keep or lose their due date",
     "options": [{"label": "new only", "description": "only orders that arrive after the deploy", "recommended": True},
                 {"label": "backfill", "description": "a migration fills the due date of every open order"}]},
    {"id": "errors", "question": "What does the caller see on a bad payload?", "why": "the carrier retries on 5xx",
     "options": [{"label": "400", "description": "the carrier stops retrying", "recommended": True},
                 {"label": "202", "description": "keel accepts and logs it"}]},
]
ASKS = "Lenses: scope and errors are open.\n```keel-questions\n" + json.dumps(QUESTIONS) + "\n```"


def interview_flow():
    return wf([{"id": "interview", "kind": "agent", "name": "interview", "agent": "explorer", "asks": True, "keep": True, "phase": "spec"},
               {"id": "interview_gate", "kind": "gate", "name": "your answers", "phase": "spec", "when": {"state": "clarify.questions"}},
               {"id": "after", "kind": "gate", "name": "after the interview", "phase": "spec"}])


def run(client, repo, monkeypatch):
    seen = answers(monkeypatch, {"explorer": lambda r: ASKS if "Answers to your questions" not in (r.feedback or "") else "NO_QUESTIONS: done"})
    tid = start(client, repo, workflow=interview_flow())
    return tid, seen, wait(client, tid)


def test_what_do_you_mean_asks_that_question_again_in_plain_words(client, repo, monkeypatch):
    tid, seen, s = run(client, repo, monkeypatch)
    assert s["waiting"]["step"] == "interview_gate" and len(s["waiting"]["questions"]) == 2
    s = decide(client, tid, payload={"answers": {"scope": "what do you mean", "errors": "400"}})
    w = s["waiting"]
    assert w["step"] == "interview_gate" and w["title"].endswith(": in plain words")
    assert [q["id"] for q in w["questions"]] == ["scope"]
    q = w["questions"][0]
    assert q["explained"] and 'You asked: "what do you mean"' in q["question"]
    assert "What it decides: orders already delivered keep or lose their due date" in q["question"]
    assert '"backfill" means a migration fills the due date of every open order' in q["question"]
    assert len([r for r in seen if r.agent == "explorer"]) == 1          # no agent call for the plain words
    s = decide(client, tid, payload={"answers": {"scope": "backfill"}})
    assert s["waiting"]["step"] == "after"
    fed = seen[-1].feedback
    assert "→ backfill" in fed and "→ 400" in fed and "not answered" not in fed
    assert "what do you mean" not in fed


def test_a_question_mark_alone_and_asking_twice_never_takes_the_default(client, repo, monkeypatch):
    tid, seen, s = run(client, repo, monkeypatch)
    s = decide(client, tid, payload={"answers": {"scope": "?", "errors": "explain please"}})
    assert sorted(q["id"] for q in s["waiting"]["questions"]) == ["errors", "scope"]
    s = decide(client, tid, payload={"answers": {"scope": "new only", "errors": "I still don't understand"}})
    assert s["waiting"]["step"] == "after"                              # once: then the person's words go on
    fed = seen[-1].feedback
    assert "→ new only" in fed
    assert "did not understand this question" in fed and "do not pick an option for them" in fed
    assert "use 400" not in fed


def test_only_words_that_ask_back_count_as_confusion():
    q = QUESTIONS[0]
    for said in ("what do you mean", "?", "explain", "I don't understand", "what does backfill mean", "huh?"):
        assert clarify.is_confused(said, q), said
    for said in ("new only", "backfill: but only for open orders", "only the new ones, please", ["new only"], ""):
        assert not clarify.is_confused(said, q), said
