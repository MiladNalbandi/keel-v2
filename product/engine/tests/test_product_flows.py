"""keel Product's five stages as real flows on the engine with the fake model: the questions before the brief, a sent-
back brief that becomes v2, one read-only impact analyst per repo, the decision with its presentation, the plan with its
checks, and the outcome. Every document is a version in the product repo and a product.* event for the api."""

import json
from pathlib import Path

from conftest import decide, start, wait


def docs_of(client, tid, kind=None):
    return [e["data"] for e in client.bus.of(tid, "product.doc") if kind is None or e["data"]["kind"] == kind]


def test_discover_asks_first_then_writes_the_brief_and_a_send_back_makes_v2(client, product_repo):
    tid = start(client, product_repo, "product-discover", {})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "approve_brief" and s["waiting"]["kind"] == "clarify"
    assert [q["id"] for q in s["waiting"]["questions"]] == ["who", "metric"]
    assert docs_of(client, tid) == []                       # nothing saved while questions are open
    s = decide(client, tid, payload={"answers": {"who": "Everyone in the target group", "metric": "Checkout conversion in the target group"}})
    assert s["waiting"]["step"] == "approve_brief" and s["waiting"].get("kind") != "clarify"
    (v1,) = docs_of(client, tid, "brief")
    assert v1["version"] == 1 and v1["path"] == "initiatives/INI-12/brief-v1.md" and "## Outcome metric" in v1["text"]
    assert (Path(product_repo) / v1["path"]).read_text().startswith("## Problem")
    assert v1["sha"]                                        # committed in the product repo
    s = decide(client, tid, decision="reject", why="Add the mobile app to Not in scope.")
    assert s["waiting"]["step"] == "approve_brief"
    v2 = docs_of(client, tid, "brief")[-1]
    assert v2["version"] == 2 and "Revised after the product owner's note" in v2["text"]
    s = decide(client, tid)
    assert s["status"] == "done"


def test_impact_runs_one_read_only_analyst_per_repo_and_merges_their_reports(client, product_repo, repos):
    tid = start(client, product_repo, "product-impact", {"repos": repos, "objection": ""})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "confirm_impact"
    starts = [e["data"] for e in client.bus.of(tid, "agent.started")]
    assert sorted(x["item"] for x in starts) == ["payments-api", "web-shop"]
    (doc,) = docs_of(client, tid, "impact")
    assert {r["repo"]: r["risk"] for r in doc["repos"]} == {"web-shop": "low", "payments-api": "medium"}
    assert {r["repo"]: r["team"] for r in doc["repos"]} == {"web-shop": "web", "payments-api": "payments"}
    assert "UNKNOWN:" in doc["text"] and "## payments-api" in doc["text"]
    # the analysts only read: their repos are unchanged
    for r in repos:
        assert sorted(p.name for p in Path(r["root"]).iterdir() if p.name != ".git") == ["README.md"]
    assert decide(client, tid)["status"] == "done"


def test_decide_writes_the_memo_builds_the_presentation_and_records_the_choice(client, product_repo):
    docs = {"brief": "## Problem\nEU visitors see USD.\n\n## Outcome metric\n2.1% → 2.6%",
            "impact_repos": [{"repo": "payments-api", "team": "payments", "risk": "medium", "text": "UNKNOWN: the rate provider"}]}
    tid = start(client, product_repo, "product-decide", {"docs": docs, "versions": {"brief": 2, "impact": 1}})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "decide"
    (memo,) = docs_of(client, tid, "decision")
    assert "## Options" in memo["text"]
    (deck,) = docs_of(client, tid, "deck")
    html = (Path(product_repo) / deck["path"]).read_text()
    assert deck["path"] == "initiatives/INI-12/deck-v1.html"
    assert "Prices in euro" in html and "Option A" in html and "the rate provider" in html and "brief v2" in html
    s = decide(client, tid, payload={"choice": "go", "option": "A"}, why="Most of the gain.")
    assert s["status"] == "done"
    (d,) = [e["data"] for e in client.bus.of(tid, "product.decision")]
    assert d["choice"] == "go" and d["option"] == "A" and d["recommended"] == "A" and d["why"] == "Most of the gain."


def test_plan_checks_and_saves_the_plan_with_its_critical_path(client, product_repo):
    tid = start(client, product_repo, "product-plan", {"team_list": "web, payments", "repo_list": "web-shop (web), payments-api (payments)"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "agree_plan"
    (p,) = [e["data"] for e in client.bus.of(tid, "product.plan")]
    assert p["ok"] and p["problems"] == []
    assert p["counts"] == {"epics": 2, "stories": 3, "tasks": 5}
    assert p["critical_path"][0] == "PAYM-S0"
    assert p["teams"] == {"payments": [3.0, 5.0], "web": [2.0, 4.0]}
    saved = json.loads((Path(product_repo) / p["path"]).read_text())
    assert [e["team"] for e in saved["epics"]] == ["payments", "web"]
    assert (Path(product_repo) / "initiatives/INI-12/plan-v1.md").read_text().startswith("# Plan")
    assert decide(client, tid)["status"] == "done"


def test_outcome_compares_the_metric_with_the_target(client, product_repo):
    tid = start(client, product_repo, "product-outcome", {"metric": "2.5%"})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "close"
    (doc,) = docs_of(client, tid, "outcome")
    assert "## Result" in doc["text"]
    assert decide(client, tid)["status"] == "done"
