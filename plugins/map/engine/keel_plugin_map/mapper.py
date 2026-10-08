"""The project map: what the code is made of, drawn for the web's Map page (keel v1 lib/map.js, the parts v2 keeps).

    build(root) -> {sha, at, demo, limits, counts, sources, schema, api, levels: {system, modules, er}}

`schema` is the database the SQL migrations leave behind (sqlschema.py): every table and view with its columns
(type, nullable, default, keys), primary key, unique constraints, indexes and foreign keys, and `relations` (one per
foreign key, column to column, with ON DELETE / ON UPDATE; one per table a view reads). Each carries the migration
file and line it came from. Nothing is capped: the web lays the diagram out and handles the size.
`api` is every endpoint of the API contract (openapi.(yaml|yml|json)).

Levels hold laid-out boxes (x, y, w, h, rows) and edges (an SVG path `d`), for clients that only draw:
  system   the code, the API contract and the database, as three boxes
  modules  the top folders with their file counts, the endpoints, the tables
  er       the tables of `schema` with all their columns, an edge per foreign key from the referenced key to the
           referencing column
Journeys and classes are not built (the page shows its empty state). Nothing connects to a running system.
Stored in the engine DB (table project_map), one map per project.

Where the migrations are: `map.migrations` in .keel/config.yml (folders or globs) when set; else `backend.dir` +
`backend.migrations` (with Flyway's db/vendor/* next to it); else every .sql file in a migration folder (db/migration,
db/changelog, any folder named migration(s) such as prisma/migrations or supabase/migrations) and db/schema.sql or
db/structure.sql. Down migrations (*.down.sql, Flyway undo U*__) and the undo half of dbmate / goose files are skipped;
test folders only count when nothing else is found.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path

import yaml

from keel_engine import rules
from keel_engine.runtime import db
from keel_engine.tools import git

from . import sqlschema

HEAD, SUB, ROW, PAD = 30, 16, 18, 10
CH_TITLE, CH_SUB = 7.3, 6.2
EP_CAP, MOD_CAP = 14, 40
SKIP = {".git", ".keel", ".codegraph", "node_modules", "build", "dist", "target", ".gradle", ".venv", "venv", "__pycache__",
        ".idea", ".next", "coverage", "out"}
CONTAINERS = {"apps", "packages", "services", "modules", "libs", "src"}
VERBS = ["get", "post", "put", "patch", "delete", "head", "options", "trace"]

MIGRATION_DIRS = {"migration", "migrations", "changelog", "changelogs"}
TEST_DIRS = {"test", "tests", "__tests__", "testdata", "test-data", "fixtures", "spec", "specs", "it", "e2e"}
LOOKED_IN = ["db/migration, db/migrations and db/vendor/* (Flyway)", "db/changelog (Liquibase SQL)",
             "any folder named migration or migrations (prisma/migrations, supabase/migrations, ...)",
             "db/schema.sql and db/structure.sql"]
CONFIG_HINT = "map.migrations in .keel/config.yml (a folder or a glob, or a list of them)"

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
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.split(r"(\d+)", rel)]


def _order(rel: str):
    """Flyway versions in version order (V2 < V4.1 < V10, across db/migration and db/vendor/*), repeatables (R__) after
    them, then everything else by its path with numbers compared as numbers (prisma's dated folders, 0001_x.up.sql)."""
    base = os.path.basename(rel)
    if (m := re.match(r"^[Vv](\d+(?:[._]\d+)*)__", base)):
        return (0, tuple(int(x) for x in re.split(r"[._]", m.group(1))), [])
    if re.match(r"^[Rr]__", base):
        return (2, (), _natural(base))
    return (1, (), _natural(rel))


def _down(rel: str) -> bool:
    base = os.path.basename(rel).lower()
    return base.endswith((".down.sql", "_down.sql", ".undo.sql")) or bool(re.match(r"^u\d+(?:[._]\d+)*__", base)) \
        or "/down/" in "/" + rel.lower()


def _migration_like(rel: str) -> bool:
    parts = rel.lower().split("/")
    dirs, base = parts[:-1], parts[-1]
    if not base.endswith(".sql") or _down(rel):
        return False
    if any(d in MIGRATION_DIRS for d in dirs):
        return True
    if any(d == "db" and i + 1 < len(dirs) and dirs[i + 1] == "vendor" for i, d in enumerate(dirs)):
        return True
    return base in ("schema.sql", "structure.sql") and bool(dirs) and dirs[-1] in ("db", "resources", "sql")


def _under(rel: str, entry: str) -> bool:
    entry = entry.strip().strip("/")
    if not entry:
        return False
    if any(ch in entry for ch in "*?["):
        return fnmatch.fnmatch(rel, entry) or fnmatch.fnmatch(rel, entry.rstrip("/") + "/*")
    return rel == entry or rel.startswith(entry + "/")


def migration_search(root: str, all_files: list[str]) -> dict:
    """{files, looked_in, configured}: the SQL files that build the schema, in the order they apply."""
    cfg = rules.load_config(root)
    sql = [f for f in all_files if f.lower().endswith(".sql") and not _down(f)]
    conf = (cfg.get("map") or {}).get("migrations")
    if conf:
        entries = [conf] if isinstance(conf, str) else [str(e) for e in conf]
        found = [f for f in sql if any(_under(f, e) for e in entries)]
        return {"files": sorted(found, key=_order), "looked_in": entries, "configured": True}
    be = cfg.get("backend") or {}
    folder = "/".join(p.strip("/") for p in (be.get("dir") or "", be.get("migrations") or "") if p and p.strip("/"))
    found = [f for f in sql if folder and f.startswith(folder + "/")]
    if found and re.search(r"(^|/)db/migrations?$", folder):     # Flyway's vendor folders sit next to db/migration
        vendor = folder.rsplit("/", 1)[0] + "/vendor/"
        found += [f for f in sql if f.startswith(vendor)]
    looked = ([folder + " (backend.dir + backend.migrations)"] if folder else []) + LOOKED_IN
    if not found:
        found = [f for f in sql if _migration_like(f)]
        real = [f for f in found if not set(f.lower().split("/")[:-1]) & TEST_DIRS]
        found = real or found
    return {"files": sorted(set(found), key=_order), "looked_in": looked, "configured": False}


def migration_files(root: str, all_files: list[str]) -> list[str]:
    return migration_search(root, all_files)["files"]


def read_schema(root: str, sql_files: list[str]) -> dict:
    """The schema the migrations leave behind (sqlschema.parse), applied in the order given."""
    texts = []
    for rel in sql_files:
        try:
            texts.append((rel, (Path(root) / rel).read_text(errors="replace")))
        except OSError:
            continue
    return sqlschema.parse(texts)


def legacy_tables(schema: dict) -> list[dict]:
    """The tables in keel v1's shape ({name, columns: [{name, type, pk, fk}], fks: [{column, to}], cite}) for the levels."""
    out = []
    for t in schema["tables"]:
        if t["kind"] != "table":
            continue
        out.append({"name": t["id"], "cite": t["cite"],
                    "columns": [{"name": c["name"], "type": c["type"], "pk": c["pk"],
                                 "fk": (c["fk"] or {}).get("table") if c["fk"] and not c["fk"].get("missing") else None}
                                for c in t["columns"]],
                    "fks": [{"column": f["columns"][0], "to": f["ref_table"]} for f in t["foreign_keys"]
                            if f["ref_table"] and f["columns"]]})
    return out


def tables(root: str, sql_files: list[str]) -> list[dict]:
    """The schema the migrations leave behind, applied in file order (a later migration wins), in keel v1's shape."""
    return legacy_tables(read_schema(root, sql_files))


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
            op = item.get(verb)
            if isinstance(op, dict):
                tags = [str(t) for t in op.get("tags") or [] if isinstance(t, (str, int))] if isinstance(op.get("tags"), list) else []
                out.append({"method": verb.upper(), "path": str(p), "cite": {"rel": rel, "line": line},
                            "summary": str(op.get("summary") or "")[:200], "tags": tags, "operation": op.get("operationId")})
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
        shown = t["columns"]
        wide = max([len(c["name"]) for c in shown] + [0])
        nodes[name] = {"id": f"tbl:{name}", "kind": "data", "title": name, "cite": t["cite"],
                       "sub": f"{len(t['columns'])} columns",
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
                          "ay": _row_y(parent, pi), "by": _row_y(child, ci) if ci >= 0 else None})
    return _level(seq, pairs)


# ------------------------------------------------------------------ the map

def build(root: str) -> dict:
    all_files = files(root)
    search = migration_search(root, all_files)
    schema = read_schema(root, search["files"])
    tbls = legacy_tables(schema)
    contract = contract_file(root, all_files)
    eps = endpoints(root, contract)
    mods = modules(all_files)
    levels = {"system": system_level(mods, eps, tbls, contract, len(all_files)), "modules": modules_level(mods, eps, tbls, contract)}
    if tbls:
        levels["er"] = er_level(tbls)
    views = sum(1 for t in schema["tables"] if t["kind"] != "table")
    fks = sum(1 for r in schema["relations"] if r["kind"] == "fk")
    return {
        "sha": git.head(root) if git.is_repo(root) else None, "at": db.now(), "demo": False,
        "sources": {"contract": contract, "migrations": search["files"], "scanned": len(all_files),
                    "looked_in": search["looked_in"], "configured": search["configured"], "config": CONFIG_HINT,
                    "skipped_statements": schema["skipped"]},
        "limits": ["endpoints come from the API contract, not from the code",
                   "tables come from the SQL migrations, so an ORM-generated schema is invisible",
                   "a module is a top folder", "journeys and classes are not drawn yet"],
        "counts": {"modules": len(mods), "files": len(all_files), "endpoints": len(eps), "tables": len(tbls), "views": views,
                   "relations": fks},
        "schema": {"tables": schema["tables"], "relations": schema["relations"]},
        "api": {"contract": contract, "endpoints": eps},
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
