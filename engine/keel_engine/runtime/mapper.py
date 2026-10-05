"""The project map: what the code is made of, drawn for the web's Map page (keel v1 lib/map.js, the parts v2 keeps).

    build(root) -> {sha, at, demo, limits, counts, sources, levels: {system, modules, er}}

Levels hold laid-out boxes (x, y, w, h, rows) and edges (an SVG path `d`), so the page only draws:
  system   the code, the API contract and the database, as three boxes
  modules  the top folders with their file counts, the endpoints from openapi.(yaml|yml|json), the tables
  er       tables and columns from the SQL migrations (CREATE TABLE, ALTER TABLE ADD COLUMN / FOREIGN KEY, DROP
           TABLE), with an edge per foreign key from the referenced key to the referencing column
Journeys and classes are not built (the page shows its empty state). Nothing connects to a running system.
Stored in the engine DB (table project_map), one map per project.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

from .. import rules
from ..tools import git
from . import db

HEAD, SUB, ROW, PAD = 30, 16, 18, 10
CH_TITLE, CH_SUB = 7.3, 6.2
ER_CAP, EP_CAP, MOD_CAP = 16, 14, 40
SKIP = {".git", ".keel", ".codegraph", "node_modules", "build", "dist", "target", ".gradle", ".venv", "venv", "__pycache__",
        ".idea", ".next", "coverage", "out"}
CONTAINERS = {"apps", "packages", "services", "modules", "libs", "src"}
VERBS = ["get", "post", "put", "patch", "delete", "head", "options", "trace"]

RE_CREATE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[\"`]?(?:\w+\.)?(\w+)[\"`]?\s*\(", re.I)
RE_ALTER_ADD = re.compile(r"alter\s+table\s+(?:if\s+exists\s+)?[\"`]?(?:\w+\.)?(\w+)[\"`]?\s+add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?"
                          r"[\"`]?(\w+)[\"`]?\s+([^,;]+)", re.I)
RE_ALTER_FK = re.compile(r"alter\s+table\s+(?:if\s+exists\s+)?[\"`]?(?:\w+\.)?(\w+)[\"`]?\s+add\s+(?:constraint\s+\w+\s+)?foreign\s+key\s*"
                         r"\(\s*[\"`]?(\w+)[\"`]?\s*\)\s*references\s+[\"`]?(?:\w+\.)?(\w+)[\"`]?", re.I)
RE_DROP = re.compile(r"drop\s+table\s+(?:if\s+exists\s+)?[\"`]?(?:\w+\.)?(\w+)[\"`]?", re.I)
RE_REF = re.compile(r"references\s+[\"`]?(?:\w+\.)?(\w+)[\"`]?", re.I)
NOT_ADDED = {"constraint", "primary", "foreign", "unique", "check", "index", "key"}
NOT_A_COLUMN = re.compile(r"^(primary|foreign|unique|constraint|check|key|index|exclude|references|on\s+(delete|update))\b", re.I)


# ------------------------------------------------------------------ sources

def files(root: str) -> list[str]:
    """Every file the project tracks (or would: untracked but not ignored); a folder walk outside git."""
    if git.is_repo(root):
        r = git.git(root, "ls-files", "-co", "--exclude-standard")
        if r.returncode == 0:
            return sorted(f for f in r.stdout.splitlines() if f and not set(f.split("/")) & SKIP)
    out = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP]
        out += [os.path.relpath(os.path.join(d, n), root) for n in names]
    return sorted(out)


def _natural(rel: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", os.path.basename(rel))]


def migration_files(root: str, all_files: list[str]) -> list[str]:
    cfg = rules.load_config(root)
    be = cfg.get("backend") or {}
    conf = "/".join(p.strip("/") for p in (be.get("dir") or "", be.get("migrations") or "") if p and p.strip("/"))
    found = [f for f in all_files if f.lower().endswith(".sql") and conf and f.startswith(conf + "/")]
    if not found:
        found = [f for f in all_files if f.lower().endswith(".sql") and ("/db/migration/" in "/" + f or
                                                                          os.path.basename(os.path.dirname(f)) == "migrations")]
    return sorted(found, key=_natural)


def _body(text: str, open_at: int) -> str:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    return ""


def _columns(body: str, table: str, fks: list) -> list[dict]:
    cols: list[dict] = []
    for raw in body.split("\n"):
        line = raw.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        if NOT_A_COLUMN.match(line):
            pk = re.search(r"primary\s+key\s*\(([^)]*)\)", line, re.I)
            for name in (re.sub(r"[\"`\s]", "", n) for n in (pk.group(1).split(",") if pk else [])):
                for c in cols:
                    c["pk"] = c["pk"] or c["name"] == name
            fk = re.search(r"foreign\s+key\s*\(\s*[\"`]?(\w+)[\"`]?\s*\)\s*references\s+[\"`]?(?:\w+\.)?(\w+)", line, re.I)
            if fk:
                fks.append({"column": fk.group(1), "to": fk.group(2)})
            continue
        m = re.match(r"^[\"`]?(\w+)[\"`]?\s+(.+)$", line)
        if not m:
            continue
        ref = RE_REF.search(m.group(2))
        cols.append({"name": m.group(1), "type": m.group(2).split()[0], "pk": bool(re.search(r"primary\s+key", m.group(2), re.I)),
                     "fk": ref.group(1) if ref else None})
        if ref:
            fks.append({"column": m.group(1), "to": ref.group(1)})
    return cols


def tables(root: str, sql_files: list[str]) -> list[dict]:
    """The schema the migrations leave behind, applied in file order (a later migration wins)."""
    by: dict[str, dict] = {}
    for rel in sql_files:
        try:
            text = (Path(root) / rel).read_text(errors="replace")
        except OSError:
            continue
        for m in RE_CREATE.finditer(text):
            fks: list = []
            by[m.group(1)] = {"name": m.group(1), "columns": _columns(_body(text, m.end() - 1), m.group(1), fks), "fks": fks,
                              "cite": {"rel": rel, "line": text.count("\n", 0, m.start()) + 1}}
        for line in text.split("\n"):
            if (fk := RE_ALTER_FK.search(line)) and fk.group(1) in by:
                by[fk.group(1)]["fks"].append({"column": fk.group(2), "to": fk.group(3)})
            elif (add := RE_ALTER_ADD.search(line)) and add.group(1) in by and add.group(2).lower() not in NOT_ADDED:
                t = by[add.group(1)]
                if not any(c["name"] == add.group(2) for c in t["columns"]):
                    ref = RE_REF.search(add.group(3))
                    t["columns"].append({"name": add.group(2), "type": add.group(3).split()[0], "pk": False,
                                         "fk": ref.group(1) if ref else None})
                    if ref:
                        t["fks"].append({"column": add.group(2), "to": ref.group(1)})
            if (gone := RE_DROP.search(line)):
                by.pop(gone.group(1), None)
    return [by[k] for k in sorted(by)]


def contract_file(root: str, all_files: list[str]) -> str | None:
    conf = (rules.load_config(root).get("contract") or {}).get("file")
    if conf and (Path(root) / conf).is_file():
        return conf
    named = [f for f in all_files if os.path.basename(f).lower() in ("openapi.yaml", "openapi.yml", "openapi.json")]
    return min(named, key=lambda f: (f.count("/"), f)) if named else None


def endpoints(root: str, rel: str | None) -> list[dict]:
    if not rel:
        return []
    try:
        text = (Path(root) / rel).read_text(errors="replace")
        doc = yaml.safe_load(text)
    except (OSError, yaml.YAMLError):
        return []
    paths = doc.get("paths") if isinstance(doc, dict) else None
    if not isinstance(paths, dict):
        return []
    out = []
    for p, item in paths.items():
        if not isinstance(item, dict):
            continue
        m = re.search(rf"^\s*[\"']?{re.escape(str(p))}[\"']?\s*:", text, re.M)
        line = text.count("\n", 0, m.start()) + 1 if m else 1
        for verb in VERBS:
            if isinstance(item.get(verb), dict):
                out.append({"method": verb.upper(), "path": str(p), "cite": {"rel": rel, "line": line}})
    return sorted(out, key=lambda e: (e["path"], e["method"]))


def modules(all_files: list[str]) -> list[dict]:
    """Top folders (a container folder such as apps/ or src/ opens one level), with file counts per extension."""
    groups: dict[str, list[str]] = {}
    for f in all_files:
        parts = f.split("/")
        if len(parts) == 1:
            key = "(root)"
        elif parts[0] in CONTAINERS and len(parts) > 2:
            key = f"{parts[0]}/{parts[1]}"
        else:
            key = parts[0]
        groups.setdefault(key, []).append(f)
    out = []
    for name, fs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        ext: dict[str, int] = {}
        for f in fs:
            e = os.path.splitext(f)[1].lower() or os.path.basename(f)
            ext[e] = ext.get(e, 0) + 1
        out.append({"name": name, "files": len(fs), "kinds": sorted(ext.items(), key=lambda kv: (-kv[1], kv[0]))[:4]})
    return out


# ------------------------------------------------------------------ layout (keel v1's router, columns of boxes)

def _r(n: float) -> float:
    return round(float(n) * 10) / 10


def _box_h(n: dict) -> int:
    return HEAD + (SUB if n.get("sub") else 0) + len(n.get("rows") or []) * ROW + PAD


def _column(nodes: list[dict], x: float, top: float, w: float, gap: float):
    y = top
    for n in nodes:
        n.update(x=x, w=w, h=_box_h(n), y=y)
        y += n["h"] + gap


def _width(nodes: list[dict], lo: int, hi: int) -> int:
    need = lo
    for n in nodes:
        need = max(need, 24 + len(n.get("title") or "") * CH_TITLE, 24 + len(n.get("sub") or "") * CH_SUB,
                   *[24 + len(str(r["t"])) * CH_SUB for r in n.get("rows") or []])
    return int(min(need, hi) + 0.999)


def _elbow(a: dict, b: dict, bend: float, ay: float | None = None, by: float | None = None) -> dict:
    """An orthogonal path out of one box and into the next; ay/by pin it to a row (a key column)."""
    y1 = ay if ay is not None else a["y"] + a["h"] / 2
    y2 = by if by is not None else b["y"] + b["h"] / 2
    if b["x"] > a["x"] + a["w"]:
        x1, x2 = a["x"] + a["w"], b["x"]
        mx = _r(x1 + (x2 - x1) * bend)
        return {"d": f"M {_r(x1)} {_r(y1)} H {mx} V {_r(y2)} H {_r(x2 - 7)}", "lx": mx, "ly": _r((y1 + y2) / 2)}
    if b["x"] + b["w"] < a["x"]:
        x1, x2 = a["x"], b["x"] + b["w"]
        mx = _r(x2 + (x1 - x2) * (1 - bend))
        return {"d": f"M {_r(x1)} {_r(y1)} H {mx} V {_r(y2)} H {_r(x2 + 7)}", "lx": mx, "ly": _r((y1 + y2) / 2)}
    # the same column: out of the right edge, around, into the right edge of the other box
    rx = a["x"] + a["w"] + 22
    return {"d": f"M {_r(a['x'] + a['w'])} {_r(y1)} H {_r(rx)} V {_r(y2)} H {_r(b['x'] + b['w'] + 7)}", "lx": _r(rx), "ly": _r((y1 + y2) / 2)}


def _level(nodes: list[dict], pairs: list[dict]) -> dict:
    by = {n["id"]: n for n in nodes}
    edges = []
    for p in pairs:
        a, b = by.get(p["from"]), by.get(p["to"])
        if a and b:
            e = _elbow(a, b, p.get("bend", 0.5), p.get("ay"), p.get("by"))
            edges.append({"from": p["from"], "to": p["to"], "kind": p["kind"], "label": p.get("label", ""), **e})
    w = max([n["x"] + n["w"] for n in nodes] + [e["lx"] for e in edges] + [0]) + 30
    h = max([n["y"] + n["h"] for n in nodes] + [e["ly"] for e in edges] + [0]) + 30
    return {"nodes": nodes, "edges": edges, "width": _r(w), "height": _r(h)}


def _row_y(n: dict, i: int) -> float:
    return n["y"] + HEAD - 10 + (SUB if n.get("sub") else 0) + (i + 1) * ROW - 5


def system_level(mods: list[dict], eps: list[dict], tbls: list[dict], contract: str | None, n_files: int) -> dict:
    code = {"id": "app:code", "kind": "app", "title": "code", "sub": "the repository", "drill": "modules", "cite": None,
            "rows": [{"t": f"{len(mods)} folder(s)"}, {"t": f"{n_files} file(s)"}]}
    right, pairs = [], []
    if eps:  # the contract sits right of the code it describes; its edge goes back left into the code
        right.append({"id": "api:contract", "kind": "ext", "title": "API", "sub": contract, "cite": None,
                      "rows": [{"t": f"{len(eps)} endpoint(s)"}]})
        pairs.append({"from": "api:contract", "to": "app:code", "kind": "http", "label": "REST", "bend": 0.6})
    if tbls:
        right.append({"id": "db:main", "kind": "data", "title": "database", "sub": "from the migrations", "drill": "er",
                      "cite": None, "rows": [{"t": f"{len(tbls)} table(s)"}]})
        pairs.append({"from": "app:code", "to": "db:main", "kind": "sql", "bend": 0.4})
    _column([code], 30, 60, 220, 20)
    _column(right, 360, 30, _width(right, 200, 360), 30)
    return _level([code] + right, pairs)


def modules_level(mods: list[dict], eps: list[dict], tbls: list[dict], contract: str | None) -> dict:
    left = [{"id": f"mod:{m['name']}", "kind": "app", "title": m["name"], "sub": f"{m['files']} file(s)", "cite": None,
             "rows": [{"t": f"{n} {e}"} for e, n in m["kinds"]]} for m in mods[:MOD_CAP]]
    right = []
    if eps:
        rows = [{"t": f"{e['method']} {e['path']}"} for e in eps[:EP_CAP]] + ([{"t": f"{len(eps) - EP_CAP} more…"}] if len(eps) > EP_CAP else [])
        right.append({"id": "api:contract", "kind": "ext", "title": "API endpoints", "sub": contract, "rows": rows, "cite": eps[0]["cite"]})
    right += [{"id": f"tbl:{t['name']}", "kind": "data", "title": t["name"], "sub": "table", "cite": t["cite"],
               "rows": [{"t": f"{len(t['columns'])} column(s)"}]} for t in tbls]
    lw = _width(left, 220, 360)
    _column(left, 30, 30, lw, 16)
    _column(right, 30 + lw + 120, 30, _width(right, 240, 380), 14)
    out = _level(left + right, [])
    if len(mods) > MOD_CAP:
        out["overflow"] = len(mods) - MOD_CAP
    return out


def er_level(tbls: list[dict]) -> dict:
    by = {t["name"]: t for t in tbls}
    order: list[str] = []

    def visit(name: str):  # tables that reference each other sit next to each other
        if name in order or name not in by:
            return
        order.append(name)
        for fk in by[name]["fks"]:
            visit(fk["to"])
        for other in tbls:
            if any(f["to"] == name for f in other["fks"]):
                visit(other["name"])

    for t in tbls:
        visit(t["name"])
    nodes = {}
    for name in order:
        t = by[name]
        shown = t["columns"][:ER_CAP]
        wide = max([len(c["name"]) for c in shown] + [0])
        nodes[name] = {"id": f"tbl:{name}", "kind": "data", "title": name, "cite": t["cite"],
                       "sub": f"{len(t['columns'])} columns" + (f", {ER_CAP} shown" if len(t["columns"]) > ER_CAP else ""),
                       "rows": [{"t": f"{c['name'].ljust(wide + 2)}{c['type']}", "flag": "pk" if c["pk"] else "fk" if c["fk"] else None}
                                for c in shown]}
    seq = [nodes[n] for n in order]
    per = max(1, -(-len(seq) // 3))
    x = 30
    for i in range(3):
        col = seq[i * per:(i + 1) * per]
        if col:
            w = _width(col, 250, 380)
            _column(col, x, 30, w, 26)
            x += w + 64
    pairs = []
    for t in tbls:
        for fk in t["fks"]:
            if fk["to"] == t["name"] or fk["to"] not in nodes:
                continue
            ci = next((i for i, c in enumerate(t["columns"]) if c["name"] == fk["column"]), -1)
            pi = next((i for i, c in enumerate(by[fk["to"]]["columns"]) if c["pk"]), 0)
            parent, child = nodes[fk["to"]], nodes[t["name"]]
            pairs.append({"from": parent["id"], "to": child["id"], "kind": "fk", "label": "1 : n",
                          "ay": _row_y(parent, pi) if pi < ER_CAP else None, "by": _row_y(child, ci) if 0 <= ci < ER_CAP else None})
    return _level(seq, pairs)


# ------------------------------------------------------------------ the map

def build(root: str) -> dict:
    all_files = files(root)
    sql = migration_files(root, all_files)
    tbls = tables(root, sql)
    contract = contract_file(root, all_files)
    eps = endpoints(root, contract)
    mods = modules(all_files)
    levels = {"system": system_level(mods, eps, tbls, contract, len(all_files)), "modules": modules_level(mods, eps, tbls, contract)}
    if tbls:
        levels["er"] = er_level(tbls)
    return {
        "sha": git.head(root) if git.is_repo(root) else None, "at": db.now(), "demo": False,
        "sources": {"contract": contract, "migrations": sql, "scanned": len(all_files)},
        "limits": ["endpoints come from the API contract, not from the code",
                   "tables come from the SQL migrations, so an ORM-generated schema is invisible",
                   "a module is a top folder", "journeys and classes are not drawn yet"],
        "counts": {"modules": len(mods), "files": len(all_files), "endpoints": len(eps), "tables": len(tbls)},
        "levels": levels,
    }


def store(project: str, m: dict) -> dict:
    with db.connect() as conn:
        conn.execute('insert or replace into project_map (project, "commit", json, at) values (?,?,?,?)',
                     (project, m.get("sha"), json.dumps(m), m.get("at") or db.now()))
    return m


def build_and_store(project: str, root: str) -> dict:
    return store(project, build(root))


def load(project: str) -> dict | None:
    with db.connect() as conn:
        r = conn.execute("select json from project_map where project = ?", (project,)).fetchone()
    return db.loads(r[0]) if r else None
