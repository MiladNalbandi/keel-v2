"""The code graph for people (the Graph page): the CodeGraph index (tools/codegraph.py), read only, rolled up into
units and groups.

    unit    a top-level class, interface, enum, function, type or constant: methods, fields and nested classes count
            for the unit that holds them; code at the top of a file counts for the file
    group   the unit's Java/Kotlin package (the file's package line), else its folder

    overview(pid)              every group and unit, and the links between units
    search(pid, q)             symbols by name: units first, then methods and the rest
    focus(pid, id, depth)      one symbol: who uses it (left) and what it uses (right), one or two steps away

A link counts the uses from one unit (or symbol) to another: calls, creates (instantiates), implements, extends and
refers to (references). Imports are left out: what a file imports shows again as what its code uses.
"""

from __future__ import annotations

import posixpath
import re
import sqlite3
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..tools import codegraph
from . import scan

USES = ("calls", "instantiates", "implements", "extends", "references")
UNIT_KINDS = {"class", "interface", "enum", "struct", "trait", "function", "type_alias", "constant", "variable", "route",
              "component", "module"}
TOP = {"file", "namespace"}
COLUMN_CAP = 24
SEARCH_CAP = 30
IMPACT_CAP = 500


@dataclass
class Graph:
    nodes: dict[str, dict]
    parent: dict[str, str]
    children: dict[str, list[str]]
    unit: dict[str, str]                       # node -> its unit
    group: dict[str, str]                      # unit -> its group id
    groups: dict[str, dict]
    uses: list[tuple[str, str, str, int | None]]   # (user node, used node, kind, line)
    unit_links: dict[tuple[str, str], Counter] = field(default_factory=dict)


_cache: dict[str, tuple[tuple, Graph]] = {}
_lock = threading.Lock()


class NoGraph(Exception):
    """The project has no usable index; `reason` says why in words."""

    def __init__(self, reason: str, status: str = "missing"):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def db_file(pid: str) -> tuple[Path, dict]:
    st = scan.status(pid)
    if st.get("status") == "indexing":
        raise NoGraph("keel is indexing the code right now. The graph shows when it is done.", "indexing")
    base = st.get("index_dir") or (posixpath.join(st["root"], codegraph.DIR) if st.get("root") else None)
    path = Path(base) / "codegraph.db" if base else None
    if not path or not path.is_file():
        why = st.get("error") or "No code graph yet: rebuild the index to make one."
        raise NoGraph(why, st.get("status") or "missing")
    return path, st


def _connect(path: Path) -> sqlite3.Connection:
    # read only; a WAL database without its -shm file cannot be opened that way, so then a normal connection (keel
    # owns the index folder) that only reads
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        conn.execute("select 1 from nodes limit 1")
        return conn
    except sqlite3.OperationalError:
        return sqlite3.connect(str(path), timeout=10)


def load(pid: str) -> tuple[Graph, dict]:
    path, st = db_file(pid)
    stat = path.stat()
    wal = path.with_name(path.name + "-wal")
    key = (stat.st_mtime_ns, stat.st_size, wal.stat().st_mtime_ns if wal.exists() else 0)
    with _lock:
        hit = _cache.get(str(path))
        if hit and hit[0] == key:
            return hit[1], st
    g = _build(path)
    with _lock:
        _cache[str(path)] = (key, g)
    return g, st


def _build(path: Path) -> Graph:
    conn = _connect(path)
    try:
        cols = "id, kind, name, qualified_name, file_path, start_line, end_line, signature, docstring"
        nodes = {r[0]: {"id": r[0], "kind": r[1], "name": r[2], "qualified": r[3], "file": r[4], "line": r[5],
                        "end_line": r[6], "signature": r[7], "docstring": r[8]}
                 for r in conn.execute(f"select {cols} from nodes")}
        edges = conn.execute("select source, target, kind, line from edges").fetchall()
    finally:
        conn.close()

    parent: dict[str, str] = {}
    children: dict[str, list[str]] = defaultdict(list)
    uses = []
    for s, t, k, line in edges:
        if s not in nodes or t not in nodes:
            continue
        if k == "contains":
            parent.setdefault(t, s)
            children[s].append(t)
        elif k in USES:
            uses.append((s, t, k, line))

    files = {n["file"]: i for i, n in nodes.items() if n["kind"] == "file"}
    package = {n["file"]: n["qualified"] or n["name"] for n in nodes.values() if n["kind"] == "namespace"}

    def file_node(path_: str) -> str:
        fid = files.get(path_)
        if not fid:  # a file the index has symbols of but no file node for: a synthetic one
            fid = f"file:{path_}"
            nodes[fid] = {"id": fid, "kind": "file", "name": posixpath.basename(path_), "qualified": path_, "file": path_,
                          "line": 1, "end_line": None, "signature": None, "docstring": None}
            files[path_] = fid
        return fid

    unit: dict[str, str] = {}
    for nid in list(nodes):
        top, seen = nid, set()
        while top in parent and nodes[parent[top]]["kind"] not in TOP and top not in seen:
            seen.add(top)
            top = parent[top]
        unit[nid] = top if nodes[top]["kind"] in UNIT_KINDS else file_node(nodes[top]["file"])
    for nid in list(nodes):  # synthetic file nodes made above
        unit.setdefault(nid, nid)

    raw: dict[str, tuple[str, str]] = {}
    for u in set(unit.values()):
        f = nodes[u]["file"]
        raw[u] = ("package", package[f]) if f in package else ("folder", posixpath.dirname(f) or "(root)")
    groups = _groups({v for v in raw.values()})
    group = {u: f"{k}:{name}" for u, (k, name) in raw.items()}

    links: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for s, t, k, _ in uses:
        a, b = unit[s], unit[t]
        if a != b:
            links[(a, b)][k] += 1
    return Graph(nodes, parent, dict(children), unit, group, groups, uses, dict(links))


def _groups(raw: set[tuple[str, str]]) -> dict[str, dict]:
    """Labels without what every package shares (io.ludus.), and the path segments the web folds by depth."""
    pkgs = sorted(name for k, name in raw if k == "package")
    split = [p.split(".") for p in pkgs]
    common = 0
    if len(split) > 1:
        while all(len(s) > common and s[common] == split[0][common] for s in split):
            common += 1
    out = {}
    for k, name in raw:
        # the shared package itself (io.ludus) keeps its last segment
        path = (name.split(".")[common:] or [name.split(".")[-1]]) if k == "package" else ([] if name == "(root)" else name.split("/"))
        label = ".".join(path) if k == "package" else name
        out[f"{k}:{name}"] = {"id": f"{k}:{name}", "kind": k, "name": name, "label": label or name, "path": path or [name]}
    return out


# ------------------------------------------------------------------ views

def _unit_row(g: Graph, u: str) -> dict:
    n = g.nodes[u]
    members = sum(1 for c in g.children.get(u, ()) if g.nodes[c]["kind"] not in ("import",))
    return {"id": u, "name": n["name"], "kind": n["kind"], "group": g.group[u], "file": n["file"], "line": n["line"],
            "members": members}


def _unavailable(e: NoGraph) -> dict:
    return {"available": False, "status": e.status, "reason": e.reason}


def overview(pid: str) -> dict:
    try:
        g, st = load(pid)
    except NoGraph as e:
        return _unavailable(e)
    units = sorted(set(g.unit.values()), key=lambda u: (g.group[u], g.nodes[u]["name"].lower()))
    used = {u for pair in g.unit_links for u in pair}
    # a file unit with no links (a config file, a README the index read) only clutters the picture
    units = [u for u in units if g.nodes[u]["kind"] != "file" or u in used]
    keep = set(units)
    groups = sorted({g.group[u] for u in units})
    return {
        "available": True, "status": st.get("status"), "indexed_at": st.get("indexed_at"),
        "counts": {"files": st.get("files") or 0, "symbols": len(g.nodes), "units": len(units), "links": len(g.unit_links),
                   "uses": len(g.uses)},
        "groups": [g.groups[x] for x in groups],
        "units": [_unit_row(g, u) for u in units],
        "links": [{"from": a, "to": b, "n": sum(c.values()), "k": dict(c)} for (a, b), c in g.unit_links.items()
                  if a in keep and b in keep],
    }


def _short(g: Graph, nid: str) -> str:
    """Class.method for a member, the name for the rest."""
    n = g.nodes[nid]
    u = g.unit[nid]
    if u != nid and g.nodes[u]["kind"] != "file":
        return f"{g.nodes[u]['name']}.{n['name']}"
    return n["name"]


def search(pid: str, q: str) -> dict:
    try:
        g, _ = load(pid)
    except NoGraph as e:
        return {**_unavailable(e), "results": []}
    q = (q or "").strip().lower()
    if not q:
        return {"available": True, "results": []}
    found = []
    for nid, n in g.nodes.items():
        if n["kind"] in ("import", "namespace", "file", "enum_member"):
            continue
        name = n["name"].lower()
        if q not in name and q not in (n["qualified"] or "").lower():
            continue
        rank = (0 if name == q else 1 if name.startswith(q) else 2 if q in name else 3,
                0 if g.unit[nid] == nid else 1, len(name))
        found.append((rank, nid))
    found.sort()
    return {"available": True, "results": [
        {"id": nid, "name": _short(g, nid), "kind": g.nodes[nid]["kind"], "file": g.nodes[nid]["file"],
         "line": g.nodes[nid]["line"], "unit": g.unit[nid], "group": g.group[g.unit[nid]]}
        for _, nid in found[:SEARCH_CAP]]}


def focus(pid: str, nid: str, depth: int = 1) -> dict:
    try:
        g, _ = load(pid)
    except NoGraph as e:
        return _unavailable(e)
    if nid not in g.nodes:
        return {"available": True, "missing": f"No symbol {nid} in the code graph: rebuild the index if the code changed."}
    depth = 2 if depth >= 2 else 1
    is_unit = g.unit[nid] == nid
    # a unit's neighbours are units (its members' uses count for it); a member's are symbols
    key = (lambda x: g.unit[x]) if is_unit else (lambda x: x)
    out_by: dict[str, list[tuple[str, str, str, int | None]]] = defaultdict(list)
    in_by: dict[str, list[tuple[str, str, str, int | None]]] = defaultdict(list)
    for e in g.uses:
        a, b = key(e[0]), key(e[1])
        if a != b:
            out_by[a].append(e)
            in_by[b].append(e)

    def neigh(x: str, side: str) -> list[tuple[str, Counter, list[int]]]:
        acc: dict[str, tuple[Counter, list]] = {}
        for s, t, k, line in (in_by if side == "in" else out_by).get(x, ()):
            other = key(s) if side == "in" else key(t)
            c, sites = acc.setdefault(other, (Counter(), []))
            c[k] += 1
            if line and len(sites) < 8:
                sites.append({"file": g.nodes[s]["file"], "line": line})
        return sorted(((o, c, s) for o, (c, s) in acc.items()), key=lambda r: (-sum(r[1].values()), _short(g, r[0]).lower()))

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    more: dict[str, int] = {}

    def put(o: str, col: int):
        if o not in nodes and o != nid:
            n = g.nodes[o]
            nodes[o] = {"id": o, "name": _short(g, o), "kind": n["kind"], "unit": g.unit[o], "group": g.group[g.unit[o]],
                        "file": n["file"], "line": n["line"], "col": col}

    def side(start: list[str], col: int, way: str):
        rows = [(x, o, c, sites) for x in start for o, c, sites in neigh(x, way) if o != nid]
        shown: list[str] = []
        for x, o, c, sites in rows:
            elsewhere = o in nodes and nodes[o]["col"] != col   # a cycle: the box stays where it is, the line is drawn
            if not elsewhere and o not in shown:
                if len(shown) >= COLUMN_CAP:
                    continue
                shown.append(o)
                put(o, col)
            user, used = (o, x) if way == "in" else (x, o)
            edges.append({"from": user, "to": used, "n": sum(c.values()), "k": dict(c), "sites": sites})
        more[str(col)] = len({o for _, o, _, _ in rows if not (o in nodes and nodes[o]["col"] != col)} - set(shown))
        return shown

    left = side([nid], -1, "in")
    right = side([nid], 1, "out")
    if depth == 2:
        side(left, -2, "in")
        side(right, 2, "out")

    # impact: everything that reaches this symbol through uses, any number of steps
    seen, todo = {nid}, [nid]
    while todo and len(seen) <= IMPACT_CAP:
        x = todo.pop()
        for s, _, _, _ in in_by.get(x, ()):
            k = key(s)
            if k not in seen:
                seen.add(k)
                todo.append(k)

    n = g.nodes[nid]
    unit = g.unit[nid]
    members = [] if not is_unit else [
        {"id": c, "name": g.nodes[c]["name"], "kind": g.nodes[c]["kind"], "line": g.nodes[c]["line"],
         "in": sum(1 for e in g.uses if e[1] == c and g.unit[e[0]] != unit),
         "out": sum(1 for e in g.uses if e[0] == c and g.unit[e[1]] != unit)}
        for c in sorted(g.children.get(nid, ()), key=lambda c: g.nodes[c]["line"] or 0)
        if g.nodes[c]["kind"] not in ("import", "namespace")]
    return {
        "available": True, "level": "unit" if is_unit else "member", "depth": depth,
        "focus": {"id": nid, "name": _short(g, nid), "kind": n["kind"], "qualified": n["qualified"],
                  "signature": n["signature"], "docstring": clean_doc(n["docstring"]), "file": n["file"],
                  "line": n["line"], "end_line": n["end_line"], "group": g.group[unit],
                  "unit": None if is_unit else {"id": unit, "name": g.nodes[unit]["name"], "kind": g.nodes[unit]["kind"]},
                  "members": members},
        "nodes": list(nodes.values()), "edges": edges, "more": more,
        "impact": min(len(seen) - 1, IMPACT_CAP), "impact_capped": len(seen) > IMPACT_CAP,
    }


def clean_doc(text: str | None, limit: int = 600) -> str | None:
    """A doc comment as plain text: no comment marks, HTML tags or {@link x} braces (Javadoc, KDoc, JSDoc)."""
    if not text:
        return None
    t = re.sub(r"^\s*/\*\*?|\*/\s*$", "", text.strip())
    t = re.sub(r"(?m)^\s*\*\s?", "", t)
    t = re.sub(r"\{@\w+\s+([^}]*)\}", lambda m: m.group(1).strip().lstrip("#"), t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[: limit - 1] + "…" if len(t) > limit else t) or None


def clear_cache():
    with _lock:
        _cache.clear()

