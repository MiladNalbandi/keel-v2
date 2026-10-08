"""keel Product's pieces: the plan check (ids, loops, criteria, critical path, totals), versioned documents, and a
presentation that escapes everything a document says."""

from keel_product import deck, docs, plan
from keel_product.md import to_html


def story(sid, deps=(), days=(1, 2), criteria=("AC-1 [API] it works",)):
    return {"id": sid, "title": sid, "repo": "r", "criteria": list(criteria), "depends_on": list(deps), "estimate_days": list(days), "tasks": ["t"]}


def test_plan_check_finds_problems_and_the_critical_path():
    p = {"epics": [{"id": "A", "team": "a", "stories": [story("A-1", days=(1, 3)), story("A-2", ["A-1"], (2, 5))]},
                   {"id": "B", "team": "b", "stories": [story("B-1", ["A-1"], (1, 1)), story("B-2", ["A-2", "B-1"], (1, 2))]}]}
    r = plan.check(p)
    assert r["ok"] and r["critical_path"] == ["A-1", "A-2", "B-2"] and r["critical_days"] == 10.0
    assert r["teams"] == {"a": [3.0, 8.0], "b": [2.0, 3.0]} and r["total_days"] == [5.0, 11.0]
    bad = {"epics": [{"team": "", "stories": [story("X", ["Y"]), story("Y", ["X"]), story("Z", ["nope"], criteria=["just text"]), story("Z")]}]}
    problems = plan.check(bad)["problems"]
    assert any("no team" in x for x in problems)
    assert any("loop" in x and "X" in x for x in problems)
    assert any("waits for nope" in x for x in problems)
    assert any("not 'AC-<n> [LAYER] text'" in x for x in problems)
    assert any("used twice" in x for x in problems)


def test_plan_parse_reads_the_last_json_block():
    text = 'x\n```json\n{"epics": []}\n```\nmore\n```json\n{"epics": [{"team": "a", "stories": []}]}\n```'
    assert plan.parse(text) == {"epics": [{"team": "a", "stories": []}]}
    assert plan.parse("no plan here") is None


def test_documents_are_versions_committed_in_the_product_repo(tmp_path):
    from conftest import git_repo

    root = git_repo(tmp_path / "p", {"README.md": "x\n"})
    a = docs.write(str(root), "INI-3", "brief", "## Problem\nx")
    b = docs.write(str(root), "INI-3", "brief", "## Problem\ny")
    assert (a["version"], b["version"]) == (1, 2) and a["sha"] and b["sha"] != a["sha"]
    assert docs.versions(str(root), "INI-3", "brief") == [1, 2]
    assert docs.extract("intro\nBEGIN-DOC\n## A\nb\nEND-DOC\nRISK: low") == "## A\nb"
    assert docs.extract('hello\n```keel-questions\n[{"question": "q"}]\n```') == "hello"
    try:
        docs.write(str(root), "../etc", "brief", "x")
        raise AssertionError("an id with a path must be refused")
    except docs.DocError:
        pass


def test_the_presentation_escapes_what_documents_say():
    html = deck.render({"id": "INI-1", "title": "<script>alert(1)</script>", "idea": "**bold** idea"},
                       brief="## Problem\n<img src=x onerror=alert(1)> and `code`",
                       impact_repos=[{"repo": "<b>r</b>", "team": "t", "risk": "high", "text": "UNKNOWN: <svg onload=x>"}],
                       memo="## Value\n2 → 3\n\n## Cost\n1–2 days", recommend="A")
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html and "<svg onload" not in html
    assert "<b>bold</b> idea" in html and "<code>code</code>" in html and "Option A" in html
    assert to_html("| a | b |\n|---|---|\n| 1 | 2 |").startswith("<table>")
