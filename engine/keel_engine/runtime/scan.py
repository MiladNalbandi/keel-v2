"""Scan a project when it is added (POST /projects/{p}/scan): stack, code graph, map, knowledge sections.

    detect the stack (marker files) -> codegraph init / sync / index -> build the map -> which docs/knowledge sections
    exist -> project_index row: idle | indexing | ready | failed, with file and symbol counts

Progress goes out as events `index.progress` and `index.done` (the api turns index.done into a notification).
The index lives in <project>/.codegraph/ (in .git/info/exclude, never committed). If CodeGraph cannot keep its SQLite
database there (a file system without the locks it needs), the index moves to $KEEL_DATA/index/<project> and
<project>/.codegraph becomes a link to it. Without CodeGraph the scan still builds the map and says why the index failed;
agents then work with grep.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from .. import config
from ..events import EventBus
from ..tools import codegraph, git
from . import db, knowledge, mapper

log = logging.getLogger(__name__)

STACKS = [("build.gradle.kts", "kotlin (gradle)"), ("build.gradle", "java/kotlin (gradle)"), ("pom.xml", "java (maven)"),
          ("package.json", "node"), ("tsconfig.json", "typescript"), ("pyproject.toml", "python"),
          ("requirements.txt", "python"), ("go.mod", "go"), ("Cargo.toml", "rust"), ("composer.json", "php"),
          ("Gemfile", "ruby"), ("docker-compose.yml", "docker compose"), ("compose.yml", "docker compose")]
# CodeGraph's own words for a database it cannot open or lock where it is.
DB_TROUBLE = re.compile(r"sqlite|disk i/o|database is locked|readonly database|locking protocol|SQLITE_", re.I)


def detect_stack(root: str) -> list[str]:
    """Marker files in the root folder and one folder down."""
    r = Path(root)
    if not r.is_dir():
        return []
    dirs = [r] + sorted(p for p in r.iterdir() if p.is_dir() and not p.name.startswith("."))
    found: list[str] = []
    for name, stack in STACKS:
        if stack not in found and any((d / name).is_file() for d in dirs):
            found.append(stack)
    return found


def knowledge_sections(root: str) -> dict:
    present = knowledge.built(root)
    return {"present": present, "missing": [s for s in knowledge.SECTIONS if s not in present],
            "configured": (Path(root) / ".keel" / "config.yml").is_file()}


# ------------------------------------------------------------------ status rows

FIELDS = ("project", "root", "status", "files", "symbols", "indexed_at", "error", "detail_json", "updated_at")


def status(project: str) -> dict:
    with db.connect() as conn:
        r = conn.execute(f"select {', '.join(FIELDS)} from project_index where project = ?", (project,)).fetchone()
    if not r:
        return {"project": project, "status": "idle", "files": 0, "symbols": 0, "indexed_at": None, "error": None,
                "available": codegraph.binary() is not None}
    row = dict(zip(FIELDS, r))
    detail = db.loads(row.pop("detail_json"), {})
    return {**row, **detail, "available": codegraph.binary() is not None}


def _save(project: str, root: str, status_: str, **fields) -> dict:
    cur = status(project)
    detail = {k: cur[k] for k in ("stack", "knowledge", "index_dir", "map") if k in cur}
    detail.update({k: fields.pop(k) for k in list(fields) if k in ("stack", "knowledge", "index_dir", "map")})
    row = {"files": cur.get("files") or 0, "symbols": cur.get("symbols") or 0, "indexed_at": cur.get("indexed_at"),
           "error": None, **fields}
    with db.connect() as conn:
        conn.execute("insert or replace into project_index (project, root, status, files, symbols, indexed_at, error, "
                     "detail_json, updated_at) values (?,?,?,?,?,?,?,?,?)",
                     (project, root, status_, row["files"], row["symbols"], row["indexed_at"], row["error"],
                      json.dumps(detail), db.now()))
    return status(project)


def ready(root: str) -> bool:
    """Has keel indexed this folder, and is that index usable now?"""
    with db.connect() as conn:
        r = conn.execute("select status from project_index where root = ? order by updated_at desc limit 1", (root,)).fetchone()
    return bool(r and r[0] == "ready")


# ------------------------------------------------------------------ the code graph

def _move_index(project: str, root: str) -> str:
    """Keep the index under $KEEL_DATA/index/<project> and link <root>/.codegraph to it."""
    target = config.data_dir() / "index" / re.sub(r"[^A-Za-z0-9_.-]", "_", project)
    link = Path(root) / codegraph.DIR
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link, ignore_errors=True)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    return str(target)


def index_graph(project: str, root: str, rebuild: bool = False) -> dict:
    """{ok, files, symbols, index_dir, error}. Blocking (subprocesses); the job runs it in a worker thread."""
    if not codegraph.binary():
        return {"ok": False, "error": "CodeGraph is not installed in this image (agents use grep instead)."}
    git.exclude(root, [f"{codegraph.DIR}/"], "keel v2 code graph index")
    index_dir = str((Path(root) / codegraph.DIR).resolve())

    def attempt() -> subprocess.CompletedProcess:
        if not codegraph.has_index(root):
            return codegraph.run(root, "init", "-y", root)
        return codegraph.run(root, "index", "-q", root) if rebuild else codegraph.run(root, "sync", "-q", root)

    try:
        r = attempt()
        if r.returncode != 0 and DB_TROUBLE.search(r.stderr + r.stdout):
            log.warning("codegraph cannot keep its index in %s; moving it to the data folder", root)
            index_dir = _move_index(project, root)
            r = attempt()
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Indexing took longer than {codegraph.INDEX_TIMEOUT}s and was stopped.", "index_dir": index_dir}
    except OSError as exc:
        return {"ok": False, "error": f"Could not run codegraph: {exc}", "index_dir": index_dir}
    if r.returncode != 0:
        why = [x for x in (r.stderr or r.stdout).splitlines() if x.strip()][-3:]
        return {"ok": False, "error": "codegraph failed: " + (" ".join(why) or f"exit {r.returncode}")[:500], "index_dir": index_dir}
    st = codegraph.status(root)
    if st and st.get("initialized") is False:
        return {"ok": False, "error": "codegraph finished but wrote no index.", "index_dir": index_dir}
    return {"ok": True, "files": int(st.get("fileCount") or 0), "symbols": int(st.get("symbols") or 0), "index_dir": index_dir}


# ------------------------------------------------------------------ the job

class Scanner:
    """One scan per project at a time; a second request while one runs returns the running one's status."""

    def __init__(self, bus: EventBus):
        self.bus = bus
        self.tasks: dict[str, asyncio.Task] = {}

    def _emit(self, type_: str, project: str, data: dict):
        self.bus.emit(type_, "", project, data=data)

    async def start(self, project: str, root: str, rebuild: bool = False) -> dict:
        if project in self.tasks and not self.tasks[project].done():
            return await asyncio.to_thread(status, project)
        st = await asyncio.to_thread(_save, project, root, "indexing")
        self.tasks[project] = asyncio.create_task(self.run(project, root, rebuild))
        return st

    async def wait(self, project: str, timeout: float = 60.0):
        task = self.tasks.get(project)
        if task:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    async def run(self, project: str, root: str, rebuild: bool = False) -> dict:
        try:
            self._emit("index.progress", project, {"status": "indexing", "step": "stack"})
            stack = await asyncio.to_thread(detect_stack, root)
            self._emit("index.progress", project, {"status": "indexing", "step": "graph", "stack": stack})
            graph = await asyncio.to_thread(index_graph, project, root, rebuild)
            self._emit("index.progress", project, {"status": "indexing", "step": "map"})
            try:
                m = await asyncio.to_thread(mapper.build_and_store, project, root)
                map_info = {"counts": m.get("counts"), "sha": m.get("sha")}
            except Exception as exc:  # the map is a picture; a bad migration file must not fail the index
                log.warning("map for %s failed: %s", project, exc)
                map_info = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            sections = await asyncio.to_thread(knowledge_sections, root)
            extra = {"stack": stack, "knowledge": sections, "map": map_info, "index_dir": graph.get("index_dir")}
            if graph["ok"]:
                st = await asyncio.to_thread(_save, project, root, "ready", files=graph["files"], symbols=graph["symbols"],
                                             indexed_at=db.now(), **extra)
            else:
                st = await asyncio.to_thread(_save, project, root, "failed", error=graph["error"], **extra)
        except Exception as exc:
            log.exception("scan of %s failed", project)
            st = await asyncio.to_thread(_save, project, root, "failed", error=f"{type(exc).__name__}: {exc}"[:500])
        self._emit("index.done", project, {k: st.get(k) for k in ("status", "files", "symbols", "error", "indexed_at", "stack", "knowledge")})
        return st
