"""An answer given to one pause must not answer a different pause the re-run node reaches first."""
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from keel_engine.runtime.compiler import ask_once, question_id


class S(TypedDict, total=False):
    runs: int
    result: str


def _graph(flip: dict):
    def node(state: S):
        # First run: "keeps failing". After the user's retry the same node finds a different situation.
        q = {"step": "verify_red", "kind": "gate", "title": "AC-3 already passes"} if flip["now"] else \
            {"step": "verify_red", "kind": "fix", "title": "verify_red keeps failing"}
        a = ask_once(q)
        return {"result": f"{q['title']}={a.get('decision')}"}

    g = StateGraph(S)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile(checkpointer=MemorySaver())


def _waiting(graph, cfg):
    snap = graph.get_state(cfg)
    return [i.value for t in snap.tasks for i in t.interrupts]


def test_retry_answer_does_not_approve_the_new_question():
    flip = {"now": False}
    graph = _graph(flip)
    cfg = {"configurable": {"thread_id": "t"}}
    graph.invoke({}, cfg)
    first = _waiting(graph, cfg)[0]
    assert first["title"] == "verify_red keeps failing" and first["id"] == question_id(first)

    flip["now"] = True
    graph.invoke(Command(resume={"decision": "approve", "asked": first["id"]}), cfg)
    second = _waiting(graph, cfg)
    assert second and second[0]["title"] == "AC-3 already passes", "the new question must be asked, not auto-approved"

    graph.invoke(Command(resume={"decision": "reject", "asked": second[0]["id"]}), cfg)
    assert graph.get_state(cfg).values["result"] == "AC-3 already passes=reject"


def test_same_question_is_answered_directly():
    graph = _graph({"now": False})
    cfg = {"configurable": {"thread_id": "t2"}}
    graph.invoke({}, cfg)
    q = _waiting(graph, cfg)[0]
    graph.invoke(Command(resume={"decision": "approve", "asked": q["id"]}), cfg)
    assert graph.get_state(cfg).values["result"] == "verify_red keeps failing=approve"


def test_answers_without_an_id_still_work():
    graph = _graph({"now": False})
    cfg = {"configurable": {"thread_id": "t3"}}
    graph.invoke({}, cfg)
    graph.invoke(Command(resume={"decision": "approve"}), cfg)
    assert graph.get_state(cfg).values["result"] == "verify_red keeps failing=approve"
