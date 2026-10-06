"""keel's own "where to look" lookups in the code graph (runtime/graph_hints.py): no model, no tool call."""

import pytest

from keel_engine.runtime import codegraph_view, graph_hints, prompts
from keel_engine.runtime.codegraph_view import Graph


def _node(nid, kind, name, file, line, end=None, sig=None):
    return {"id": nid, "kind": kind, "name": name, "qualified": name, "file": file, "line": line, "end_line": end,
            "signature": sig, "docstring": None}


@pytest.fixture
def shop(monkeypatch):
    nodes = {n["id"]: n for n in [
        _node("f:cart", "file", "cart.js", "src/domain/cart.js", 1),
        _node("cart", "class", "Cart", "src/domain/cart.js", 3, 30),
        _node("cart.add", "method", "add", "src/domain/cart.js", 10, 15, "add(product, quantity = 1)"),
        _node("checkout", "function", "checkout", "src/app/checkout.js", 5, 20, "checkout(cart, orders)"),
        _node("format", "function", "format", "src/domain/money.js", 7, 9),
        _node("t:checkout", "function", "checkout", "test/checkout.test.js", 3, 12),
    ]}
    parent = {"cart.add": "cart"}
    unit = {nid: (parent.get(nid) or nid) for nid in nodes}
    g = Graph(nodes=nodes, parent=parent, children={"cart": ["cart.add"]}, unit=unit, group={u: "folder:src" for u in unit},
              groups={}, uses=[("checkout", "cart.add", "calls", 12), ("checkout", "format", "calls", 18)])
    monkeypatch.setattr(codegraph_view, "load", lambda pid: (g, {}))
    return g


def test_the_places_a_question_names_with_their_ranges_and_users(shop):
    out = graph_hints.where_to_look("shop", "Why does checkout refuse a Cart when add gets 0?")
    assert out.startswith("Where to look")
    assert "`src/app/checkout.js:5-20` function checkout checkout(cart, orders)" in out
    assert "`src/domain/cart.js:3-30` class Cart" in out
    assert "- `src/domain/money.js" not in out                   # format is not asked about (only what checkout calls)
    assert "calls Cart.add (src/domain/cart.js:10), format (src/domain/money.js:7)" in out
    assert "test/checkout.test.js" not in out                    # a test counts less unless the question is about tests
    assert out.endswith("Look further only when these are not enough.")


def test_a_method_shows_who_uses_it_and_tests_come_back_for_a_test_question(shop):
    out = graph_hints.where_to_look("shop", "What calls `add`? and which test covers checkout")
    assert "Cart.add add(product, quantity = 1) — used by checkout (src/app/checkout.js:12)" in out
    assert "test/checkout.test.js:3-12" in out


def test_mentions_and_the_open_file_count(shop):
    out = graph_hints.where_to_look("shop", "explain this", open_file="src/domain/money.js")
    assert "`src/domain/money.js:7-9` function format" in out


def test_nothing_without_an_index_or_a_match(monkeypatch, shop):
    assert graph_hints.where_to_look("shop", "hello there") == ""
    def missing(pid):
        raise codegraph_view.NoGraph("no index")
    monkeypatch.setattr(codegraph_view, "load", missing)
    assert graph_hints.where_to_look("shop", "Cart checkout") == ""


def test_terms_keep_identifiers_and_skip_plain_small_words():
    t = dict(graph_hints.terms("Where is save_score in ScoreController.kt, and the cart?"))
    assert t["save_score"] is True and t["scorecontroller"] is True and t["cart"] is False
    assert "where" not in t and "is" not in t
    t2 = dict(graph_hints.terms("Where are player sprites uploaded, and is the API key checked?"))
    assert "sprite" in t2 and "playersprites" in t2 and "apikey" in t2


def test_flow_agents_get_the_list_unless_their_hints_are_off(shop, tmp_path):
    know = {"sections": [], "code_graph": False, "memory": False, "strict": False, "hints": True}
    args = dict(agent="implementer", phase="green", step_name="green", title="Refuse an empty cart at checkout", root=str(tmp_path),
                ac={"id": "AC-1", "layer": "API", "title": "checkout refuses an empty Cart"}, acs=[], feedback=None, pid="shop")
    assert "Where to look" in prompts.task_prompt(**args, knowledge=know)
    assert "Where to look" not in prompts.task_prompt(**args, knowledge={**know, "hints": False})
