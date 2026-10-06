"""The Graph page's data: the CodeGraph index rolled up into units (top-level declarations) and groups (packages, else
folders), the uses between them, search, and one symbol with who uses it and what it uses."""

import sqlite3

import pytest

from keel_engine.runtime import codegraph_view as cv
from keel_engine.runtime import scan

# id, kind, name, qualified_name, file, line
NODES = [
    ("file:A/web/Ctl.java", "file", "Ctl.java", "A/web/Ctl.java", "A/web/Ctl.java", 1),
    ("ns:web", "namespace", "com.x.web", "com.x.web", "A/web/Ctl.java", 1),
    ("imp:1", "import", "Svc", "com.x.app.Svc", "A/web/Ctl.java", 2),
    ("class:ctl", "class", "Ctl", "com.x.web::Ctl", "A/web/Ctl.java", 4),
    ("method:handle", "method", "handle", "com.x.web::Ctl::handle", "A/web/Ctl.java", 6),
    ("file:A/app/Svc.java", "file", "Svc.java", "A/app/Svc.java", "A/app/Svc.java", 1),
    ("ns:app", "namespace", "com.x.app", "com.x.app", "A/app/Svc.java", 1),
    ("class:svc", "class", "Svc", "com.x.app::Svc", "A/app/Svc.java", 3),
    ("method:run", "method", "run", "com.x.app::Svc::run", "A/app/Svc.java", 8),
    ("method:helper", "method", "helper", "com.x.app::Svc::helper", "A/app/Svc.java", 14),
    ("field:repo", "field", "repo", "com.x.app::Svc::repo", "A/app/Svc.java", 5),
    ("file:A/domain/Repo.java", "file", "Repo.java", "A/domain/Repo.java", "A/domain/Repo.java", 1),
    ("ns:domain", "namespace", "com.x.domain", "com.x.domain", "A/domain/Repo.java", 1),
    ("iface:repo", "interface", "Repo", "com.x.domain::Repo", "A/domain/Repo.java", 3),
    ("method:save", "method", "save", "com.x.domain::Repo::save", "A/domain/Repo.java", 4),
    ("file:A/infra/RepoImpl.java", "file", "RepoImpl.java", "A/infra/RepoImpl.java", "A/infra/RepoImpl.java", 1),
    ("ns:infra", "namespace", "com.x.infra", "com.x.infra", "A/infra/RepoImpl.java", 1),
    ("class:impl", "class", "RepoImpl", "com.x.infra::RepoImpl", "A/infra/RepoImpl.java", 3),
    ("file:ui/src/page.tsx", "file", "page.tsx", "ui/src/page.tsx", "ui/src/page.tsx", 1),
    ("function:page", "function", "Page", "Page", "ui/src/page.tsx", 3),
    ("file:ui/src/lib/api.ts", "file", "api.ts", "ui/src/lib/api.ts", "ui/src/lib/api.ts", 1),
    ("function:fetch", "function", "fetchIt", "fetchIt", "ui/src/lib/api.ts", 2),
    ("file:README.md", "file", "README.md", "README.md", "README.md", 1),
]
# source, target, kind, line
EDGES = [
    ("file:A/web/Ctl.java", "ns:web", "contains", None), ("ns:web", "imp:1", "contains", None),
    ("ns:web", "class:ctl", "contains", None), ("class:ctl", "method:handle", "contains", None),
    ("file:A/app/Svc.java", "ns:app", "contains", None), ("ns:app", "class:svc", "contains", None),
    ("class:svc", "method:run", "contains", None), ("class:svc", "method:helper", "contains", None),
    ("class:svc", "field:repo", "contains", None),
    ("file:A/domain/Repo.java", "ns:domain", "contains", None), ("ns:domain", "iface:repo", "contains", None),
    ("iface:repo", "method:save", "contains", None),
    ("file:A/infra/RepoImpl.java", "ns:infra", "contains", None), ("ns:infra", "class:impl", "contains", None),
    ("file:ui/src/page.tsx", "function:page", "contains", None), ("file:ui/src/lib/api.ts", "function:fetch", "contains", None),
    ("method:handle", "method:run", "calls", 7),
    ("method:handle", "class:svc", "instantiates", 6),
    ("method:run", "method:helper", "calls", 9),        # inside Svc: not a link
    ("method:run", "method:save", "calls", 10),
    ("field:repo", "iface:repo", "references", 5),
    ("method:helper", "method:handle", "calls", 15),    # Svc uses Ctl back: a cycle
    ("class:impl", "iface:repo", "implements", 3),
    ("function:page", "function:fetch", "calls", 4),
    ("file:ui/src/page.tsx", "function:page", "calls", 9),  # top-of-file code: counts for the file
    ("imp:1", "class:svc", "imports", 2),                # imports are left out
]


@pytest.fixture
def graph(tmp_path):
    folder = tmp_path / "index"
    folder.mkdir()
    conn = sqlite3.connect(folder / "codegraph.db")
    conn.execute("create table nodes (id text primary key, kind text, name text, qualified_name text, file_path text, "
                 "start_line int, end_line int, signature text, docstring text)")
    conn.execute("create table edges (id integer primary key autoincrement, source text, target text, kind text, line int)")
    conn.executemany("insert into nodes values (?,?,?,?,?,?,?,?,?)",
                     [(*n, (n[5] or 0) + 3, "void run()" if n[0] == "method:run" else None,
                       "Runs it." if n[0] == "method:run" else None) for n in NODES])
    conn.executemany("insert into edges (source, target, kind, line) values (?,?,?,?)", EDGES)
    conn.commit()
    conn.close()
    cv.clear_cache()
    scan._save("demo", str(tmp_path), "ready", files=5, index_dir=str(folder))
    return folder


def test_overview_groups_units_and_links(graph):
    o = cv.overview("demo")
    assert o["available"] is True
    labels = {g["label"]: g for g in o["groups"]}
    # what every package shares (com.x) is left out; files without a package go by folder
    assert set(labels) == {"web", "app", "domain", "infra", "ui/src", "ui/src/lib"}
    assert labels["app"]["kind"] == "package" and labels["app"]["path"] == ["app"]
    assert labels["ui/src/lib"]["path"] == ["ui", "src", "lib"]
    units = {u["id"]: u for u in o["units"]}
    # methods and fields count for their class; a README with no links is left out; top-of-file code is the file
    assert set(units) == {"class:ctl", "class:svc", "iface:repo", "class:impl", "function:page", "function:fetch",
                          "file:ui/src/page.tsx"}
    assert units["class:svc"]["members"] == 3
    assert units["class:svc"]["group"] == "package:com.x.app"
    links = {(lk["from"], lk["to"]): lk for lk in o["links"]}
    assert links[("class:ctl", "class:svc")]["k"] == {"calls": 1, "instantiates": 1}
    assert links[("class:ctl", "class:svc")]["n"] == 2
    assert links[("class:svc", "class:ctl")]["n"] == 1
    assert links[("class:svc", "iface:repo")]["k"] == {"calls": 1, "references": 1}
    assert links[("class:impl", "iface:repo")]["k"] == {"implements": 1}
    assert ("class:svc", "class:svc") not in links                  # inside one unit
    assert not any("imports" in lk["k"] for lk in o["links"])
    assert o["counts"]["units"] == 7


def test_search_ranks_names_and_names_members_by_their_class(graph):
    r = cv.search("demo", "sav")["results"]
    assert r[0]["name"] == "Repo.save" and r[0]["kind"] == "method" and r[0]["unit"] == "iface:repo"
    r = cv.search("demo", "repo")["results"]
    assert [x["name"] for x in r][:3] == ["Repo", "Svc.repo", "RepoImpl"]   # exact, then the shorter starts
    assert cv.search("demo", "  ")["results"] == []


def test_focus_on_a_class_shows_units_both_ways_and_keeps_a_cycle_on_one_side(graph):
    f = cv.focus("demo", "class:svc")
    assert f["level"] == "unit"
    assert f["focus"]["name"] == "Svc" and f["focus"]["group"] == "package:com.x.app"
    assert [m["name"] for m in f["focus"]["members"]] == ["repo", "run", "helper"]
    run = next(m for m in f["focus"]["members"] if m["name"] == "run")
    assert (run["in"], run["out"]) == (1, 1)                        # Ctl.handle calls it; it calls Repo.save
    cols = {n["id"]: n["col"] for n in f["nodes"]}
    assert cols == {"class:ctl": -1, "iface:repo": 1}               # Ctl uses Svc and Svc uses Ctl: drawn once, left
    edges = {(e["from"], e["to"]): e for e in f["edges"]}
    assert edges[("class:ctl", "class:svc")]["n"] == 2
    assert edges[("class:svc", "class:ctl")]["n"] == 1             # the cycle's line is still there
    assert edges[("class:svc", "iface:repo")]["sites"] == [{"file": "A/app/Svc.java", "line": 10}, {"file": "A/app/Svc.java", "line": 5}]
    assert f["impact"] == 1                                         # only Ctl reaches Svc


def test_focus_on_a_method_shows_symbols(graph):
    f = cv.focus("demo", "method:run")
    assert f["level"] == "member"
    assert f["focus"]["unit"] == {"id": "class:svc", "name": "Svc", "kind": "class"}
    assert f["focus"]["signature"] == "void run()" and f["focus"]["docstring"] == "Runs it."
    cols = {n["name"]: n["col"] for n in f["nodes"]}
    assert cols == {"Ctl.handle": -1, "Svc.helper": 1, "Repo.save": 1}


def test_two_steps_and_what_depends_on_a_type(graph):
    f = cv.focus("demo", "iface:repo", depth=2)
    cols = {n["id"]: n["col"] for n in f["nodes"]}
    assert cols == {"class:svc": -1, "class:impl": -1, "class:ctl": -2}
    assert f["impact"] == 3
    assert f["more"] == {"-1": 0, "1": 0, "-2": 0, "2": 0}


def test_unknown_symbol_and_missing_or_busy_index(graph, tmp_path):
    assert "No symbol" in cv.focus("demo", "nope")["missing"]
    scan._save("demo", str(tmp_path), "indexing")
    o = cv.overview("demo")
    assert o["available"] is False and o["status"] == "indexing"
    scan._save("other", str(tmp_path / "nowhere"), "failed", error="codegraph is not installed")
    o = cv.overview("other")
    assert o == {"available": False, "status": "failed", "reason": "codegraph is not installed"}


def test_the_index_is_read_again_when_it_changes(graph):
    assert len(cv.overview("demo")["units"]) == 7
    conn = sqlite3.connect(graph / "codegraph.db")
    conn.execute("insert into nodes values ('function:new', 'function', 'fresh', 'fresh', 'ui/src/lib/api.ts', 9, 12, null, null)")
    conn.execute("insert into edges (source, target, kind, line) values ('function:page', 'function:new', 'calls', 5)")
    conn.commit()
    conn.close()
    assert "function:new" in {u["id"] for u in cv.overview("demo")["units"]}


def test_graph_endpoints(client, graph):
    o = client.get("/projects/demo/graph").json()
    assert o["available"] and len(o["units"]) == 7
    r = client.post("/projects/demo/graph/search", json={"q": "fetch"}).json()
    assert r["results"][0]["name"] == "fetchIt"
    f = client.post("/projects/demo/graph/node", json={"id": "function:fetch", "depth": 2}).json()
    assert {n["name"]: n["col"] for n in f["nodes"]} == {"Page": -1, "page.tsx": -2}


def test_doc_comments_become_plain_text():
    doc = "/**\n * Reading, publishing and removing waves. <p>Two views of the same table, see {@link #forAuthors} and\n * {@code 404}.\n */"
    assert cv.clean_doc(doc) == "Reading, publishing and removing waves. Two views of the same table, see forAuthors and 404."
    assert cv.clean_doc("x" * 700).endswith("…") and len(cv.clean_doc("x" * 700)) == 600
    assert cv.clean_doc("") is None and cv.clean_doc(None) is None
