"""Scan a project when it is added (POST /projects/{p}/scan): stack, the parts' own steps, knowledge sections.

    detect the stack (marker files) -> each part's on_scan hook, in the registry's order (keel_engine/extensions.py:
    the code graph's init / sync / index, then the map) -> which docs/knowledge sections exist -> project_index row:
    idle | indexing | ready | failed, with file and symbol counts

A part's on_scan returns fields for the row: "index" ({ok, files, symbols, index_dir, error}, the code graph's) decides
ready or failed; "map" and the like go into its detail. Progress goes out as events `index.progress` (one step per
part, by its name) and `index.done` (the api turns index.done into a notification). Without a code index the scan still
builds the map and says why the index failed; agents then work with grep.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .. import extensions
from ..events import EventBus
from . import db, knowledge

log = logging.getLogger(__name__)

STACKS = [("build.gradle.kts", "kotlin (gradle)"), ("build.gradle", "java/kotlin (gradle)"), ("pom.xml", "java (maven)"),
          ("package.json", "node"), ("tsconfig.json", "typescript"), ("pyproject.toml", "python"),
          ("requirements.txt", "python"), ("go.mod", "go"), ("Cargo.toml", "rust"), ("composer.json", "php"),
          ("Gemfile", "ruby"), ("docker-compose.yml", "docker compose"), ("compose.yml", "docker compose")]
# The scan's result when no part builds a code index (the code graph part is not loaded).
NO_INDEX = {"ok": False, "error": "No part of keel builds a code index here (agents use grep instead)."}


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
ROW = ("files", "symbols", "indexed_at", "error")    # what _save writes into the row; anything else is its detail
                                                    # (stack, knowledge, index_dir, and what the parts' scans found)


def status(project: str) -> dict:
    with db.connect() as conn:
        r = conn.execute(f"select {', '.join(FIELDS)} from project_index where project = ?", (project,)).fetchone()
    if not r:
        return {"project": project, "status": "idle", "files": 0, "symbols": 0, "indexed_at": None, "error": None,
                "available": extensions.index_available()}
    row = dict(zip(FIELDS, r))
    detail = db.loads(row.pop("detail_json"), {})
    return {**row, **detail, "available": extensions.index_available()}


def _save(project: str, root: str, status_: str, **fields) -> dict:
    cur = status(project)
    detail = {k: v for k, v in cur.items() if k not in FIELDS and k != "available"}
    detail.update({k: fields.pop(k) for k in list(fields) if k not in ROW})
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
            found: dict = {}
            for n, (part, fn) in enumerate(extensions.hooks("on_scan")):
                # one step per part (the code graph's "graph", then "map"); the first one carries the stack
                self._emit("index.progress", project, {"status": "indexing", "step": part, **({"stack": stack} if not n else {})})
                found.update(await asyncio.to_thread(extensions.call_hook, fn, root, project, rebuild=rebuild) or {})
            graph = found.pop("index", None) or NO_INDEX
            sections = await asyncio.to_thread(knowledge_sections, root)
            found = {k: v for k, v in found.items() if k not in FIELDS and k not in ROW and k != "available"}
            extra = {"stack": stack, "knowledge": sections, **found, "index_dir": graph.get("index_dir")}
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
