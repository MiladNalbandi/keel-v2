"""Threads: start, run in the background, resume, stop, history, rewind.

One LangGraph thread per flow. Checkpoints live in `$KEEL_DATA/checkpoints.db` (AsyncSqliteSaver);
the same file holds a small registry (thread -> project, workflow, title, status, start body) so a
restarted engine can rebuild each graph and carry on with threads that were running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from .. import config
from ..events import EventBus, mirror
from ..tools import git
from ..workflows.model import Workflow, from_dict
from . import ladder as ladder_mod
from .compiler import compile_workflow
from .state import ThreadContext, initial_state

log = logging.getLogger(__name__)

REGISTRY = """
create table if not exists keel_threads (
  thread_id text primary key, project_id text not null, workflow_id text not null, title text not null,
  status text not null, root text not null, body text not null, error text,
  created_at text not null, updated_at text not null
)"""


class EngineError(Exception):
    def __init__(self, status: int, error: str, hint: str | None = None):
        super().__init__(error)
        self.status, self.error, self.hint = status, error, hint


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Engine:
    def __init__(self, bus: EventBus, data_dir: Path | None = None):
        self.bus = bus
        self.data_dir = data_dir
        self.conn: aiosqlite.Connection | None = None
        self.saver: AsyncSqliteSaver | None = None
        self.graphs: dict[str, object] = {}
        self.ctxs: dict[str, ThreadContext] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.keys: dict[str, dict] = {}

    # ------------------------------------------------------------ lifecycle

    async def open(self, resume_running: bool = True):
        d = self.data_dir or config.data_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(str(d / "checkpoints.db"))
        await self.conn.execute("pragma journal_mode=wal")
        self.saver = AsyncSqliteSaver(self.conn)
        await self.saver.setup()
        await self.conn.execute(REGISTRY)
        await self.conn.commit()
        if resume_running:
            async with self.conn.execute("select thread_id from keel_threads where status = 'running'") as cur:
                rows = await cur.fetchall()
            for (tid,) in rows:
                log.info("continuing thread %s after a restart", tid)
                self._launch(tid, None)

    async def close(self):
        for t in list(self.tasks.values()):
            t.cancel()
        for t in list(self.tasks.values()):
            try:
                await t
            except BaseException:
                pass
        if self.conn:
            await self.conn.close()

    # ------------------------------------------------------------ registry

    async def _row(self, tid: str) -> dict:
        async with self.conn.execute("select thread_id, project_id, workflow_id, title, status, root, body, error, updated_at "
                                     "from keel_threads where thread_id = ?", (tid,)) as cur:
            r = await cur.fetchone()
        if not r:
            raise EngineError(404, f"No thread {tid}.")
        keys = ["thread_id", "project_id", "workflow_id", "title", "status", "root", "body", "error", "updated_at"]
        return dict(zip(keys, r))

    async def _set_status(self, tid: str, status: str, error: str | None = None):
        await self.conn.execute("update keel_threads set status = ?, error = ?, updated_at = ? where thread_id = ?",
                                (status, error, _now(), tid))
        await self.conn.commit()

    async def _context(self, tid: str) -> ThreadContext:
        if tid in self.ctxs:
            return self.ctxs[tid]
        row = await self._row(tid)
        body = json.loads(row["body"])
        wf = from_dict(body["workflow"], body["workflow"].get("yaml") or None)
        ctx = ThreadContext(thread_id=tid, project_id=row["project_id"], root=row["root"], workflow=wf, title=row["title"],
                            models=body.get("models") or {}, settings=body.get("settings") or {}, mcp=body.get("mcp") or [],
                            skills=body.get("skills") or {}, keys=self.keys.get(tid, {}), bus=self.bus)
        self.ctxs[tid] = ctx
        self.bus.register(tid, ctx.root)
        return ctx

    async def _graph(self, tid: str):
        if tid not in self.graphs:
            self.graphs[tid] = compile_workflow(await self._context(tid), self.saver)
        return self.graphs[tid]

    @staticmethod
    def _cfg(tid: str, checkpoint_id: str | None = None) -> dict:
        c = {"configurable": {"thread_id": tid}, "recursion_limit": 10_000}
        if checkpoint_id:
            c["configurable"]["checkpoint_id"] = checkpoint_id
        return c

    # ------------------------------------------------------------ running

    def _launch(self, tid: str, inp, cfg: dict | None = None):
        task = asyncio.create_task(self._drive(tid, inp, cfg))
        self.tasks[tid] = task
        task.add_done_callback(lambda t, tid=tid: self.tasks.pop(tid, None) if self.tasks.get(tid) is t else None)

    async def _drive(self, tid: str, inp, cfg: dict | None):
        ctx = await self._context(tid)
        graph = await self._graph(tid)
        await self._set_status(tid, "running")
        try:
            async for values in graph.astream(inp, cfg or self._cfg(tid), stream_mode="values"):
                self._mirror(ctx, values)
            snap = await graph.aget_state(self._cfg(tid))
            self._mirror(ctx, snap.values)
            waiting = self._waiting(snap)
            if waiting:
                await self._set_status(tid, "waiting")
                kind = waiting.get("kind")
                data = {"kind": kind, "title": waiting.get("title"), "detail": waiting.get("detail")}
                if kind == "budget":
                    ctx.emit("budget.warn", step=waiting.get("step"), data={**data, "paused": True})
                ctx.emit("gate.waiting", step=waiting.get("step"), data=data)
                return
            status = snap.values.get("status") or "done"
            if status not in ("stopped", "failed"):
                status = "done"
            await self._set_status(tid, status, snap.values.get("error"))
            if status == "failed":
                ctx.emit("thread.failed", data={"error": snap.values.get("error"), "status": status})
            else:
                ctx.emit("thread.done", data={"status": status, "usage": snap.values.get("usage")})
        except asyncio.CancelledError:
            await self._set_status(tid, "stopped")
            raise
        except Exception as exc:
            log.exception("thread %s failed", tid)
            await self._set_status(tid, "failed", f"{type(exc).__name__}: {exc}"[:1000])
            ctx.emit("thread.failed", data={"error": f"{type(exc).__name__}: {exc}"[:1000]})

    def _mirror(self, ctx: ThreadContext, values: dict):
        if not values:
            return
        if not values.get("ladder") and self._has_ladder(ctx):
            rungs = ladder_mod.previous(ctx.root)
            if rungs:
                values = {**values, "ladder": rungs}
        ctx.write_mirror(values)

    @staticmethod
    def _has_ladder(ctx: ThreadContext) -> bool:
        return any("ladder" in s.actions() or any(l.sub == "ladder" for l in s.lanes or []) for s in ctx.workflow.steps)

    @staticmethod
    def _waiting(snap) -> dict | None:
        for task in snap.tasks or ():
            for it in getattr(task, "interrupts", ()) or ():
                if isinstance(it.value, dict):
                    return it.value
        return None

    async def wait_idle(self, tid: str, timeout: float = 30.0):
        """Wait until the thread's background run ends (tests and the demo use this)."""
        task = self.tasks.get(tid)
        if task:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    # ------------------------------------------------------------ API

    async def start_thread(self, body: dict) -> str:
        wf: Workflow = body["workflow"]
        root = str(Path(body["root"]).resolve())
        if not Path(root).is_dir():
            raise EngineError(400, f"The project folder {root} does not exist.")
        tid = uuid.uuid4().hex
        stored = {k: v for k, v in body.items() if k not in ("keys",)}
        stored["workflow"] = {**wf.model_dump(exclude_none=True)}
        stored["root"] = root
        now = _now()
        await self.conn.execute("insert into keel_threads values (?,?,?,?,?,?,?,?,?,?)",
                                (tid, body["project_id"], wf.id, body["title"], "running", root, json.dumps(stored), None, now, now))
        await self.conn.commit()
        if body.get("keys"):
            self.keys[tid] = dict(body["keys"])
        ctx = await self._context(tid)
        state = initial_state(ctx, body.get("acs"))
        if git.is_repo(root):
            state["branch"] = git.branch(root)
            state["git_head"] = git.head(root)
            state["base_head"] = state["git_head"]
            state["preexisting"] = git.snapshot(root)
        ctx.write_mirror(state, merge_disk=False)
        for u in state.get("unlocks") or []:
            mirror.append_log(root, {"kind": "gate", "gate": "unlock", "verdict": "approve",
                                     "detail": f"{u['path']} in {u['phase']} (from settings)"})
        ctx.emit("thread.started", data={"workflow": wf.id, "title": ctx.title, "flow": wf.flow, "root": root,
                                         "acs": len(state["acs"]), "fake": ctx.fake})
        self._launch(tid, state)
        return tid

    async def state(self, tid: str) -> dict:
        row = await self._row(tid)
        graph = await self._graph(tid)
        snap = await graph.aget_state(self._cfg(tid))
        v = snap.values or {}
        waiting = self._waiting(snap)
        running = tid in self.tasks
        status = row["status"]
        if running:
            status = "running"
        elif status not in ("stopped", "failed"):
            if waiting:
                status = "waiting"
            elif v.get("status") in ("done", "failed", "stopped"):
                status = v["status"]
        n = 0
        async for _ in graph.aget_state_history(self._cfg(tid)):
            n += 1
        out = {
            "thread_id": tid, "project_id": row["project_id"], "workflow_id": row["workflow_id"], "title": row["title"],
            "status": status, "current": v.get("current"), "phase": v.get("phase") or "none", "ac": v.get("ac"),
            "acs": [{"id": a["id"], "layer": a.get("layer", "API"), "title": a.get("title", ""), "status": a.get("status", "todo")}
                    for a in v.get("acs") or []],
            "usage": {**{"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "premium_requests": 0, "cap_tokens": 0}, **(v.get("usage") or {})},
            "checkpoints": n, "updated_at": row["updated_at"],
            "blockers": list(v.get("blockers") or []),
            "unlocks": [{"path": u.get("path"), "phase": u.get("phase")} for u in v.get("unlocks") or []],
        }
        ctx = await self._context(tid)
        rungs = v.get("ladder")
        if self._has_ladder(ctx):
            # A failed rung stops the step at a "fix" pause before the graph state is updated,
            # so .keel/ladder.json (written as the ladder runs) is the fresher source.
            rungs = ladder_mod.previous(ctx.root) or rungs
        if rungs:
            out["ladder"] = rungs
        if waiting and status == "waiting":
            out["waiting"] = {"step": waiting.get("step"), "kind": waiting.get("kind", "gate"), "title": waiting.get("title", ""),
                              "detail": waiting.get("detail", ""), "options": waiting.get("options") or ["approve", "reject"]}
        err = row["error"] or v.get("error")
        if err and status in ("failed", "stopped"):
            out["error"] = err
        return out

    async def resume(self, tid: str, decision: str, why: str | None, payload: dict | None) -> dict:
        row = await self._row(tid)
        if tid in self.tasks:
            raise EngineError(409, "The flow is running, not waiting.", "Wait for it to reach a gate.")
        if row["status"] == "stopped":
            raise EngineError(409, "The flow was stopped.", "Rewind to a checkpoint to continue it.")
        graph = await self._graph(tid)
        snap = await graph.aget_state(self._cfg(tid))
        if not self._waiting(snap):
            raise EngineError(409, "The flow is not waiting for a decision.")
        if decision not in ("approve", "reject"):
            raise EngineError(400, "decision must be approve or reject.")
        if decision == "reject" and self._waiting(snap).get("kind") == "gate" and not (why or "").strip():
            raise EngineError(400, "Say why when you send it back.", "The reason goes into the next agent's prompt.")
        await self._set_status(tid, "running")
        self._launch(tid, Command(resume={"decision": decision, "why": why or "", "payload": payload or {}}))
        return await self.state(tid)

    async def stop(self, tid: str) -> dict:
        await self._row(tid)
        task = self.tasks.get(tid)
        if task:
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        await self._set_status(tid, "stopped")
        ctx = await self._context(tid)
        graph = await self._graph(tid)
        snap = await graph.aget_state(self._cfg(tid))
        self._mirror(ctx, {**(snap.values or {}), "status": "stopped"})
        ctx.emit("thread.done", data={"status": "stopped"})
        return await self.state(tid)

    async def history(self, tid: str) -> list[dict]:
        await self._row(tid)
        graph = await self._graph(tid)
        ctx = await self._context(tid)
        out = []
        async for snap in graph.aget_state_history(self._cfg(tid)):
            n = (snap.metadata or {}).get("step", -1)
            if n < 0:
                continue
            v = snap.values or {}
            step = v.get("current") or "start"
            s = ctx.workflow.step(step)
            note = v.get("note") or ""
            if v.get("ac") and s and s.per_ac:
                note = f"{v['ac']} · {note}"
            out.append({"id": snap.config["configurable"]["checkpoint_id"], "n": n, "step": step,
                        "at": snap.created_at, "note": note[:300]})
        return out

    async def rewind(self, tid: str, checkpoint_id: str) -> dict:
        await self._row(tid)
        graph = await self._graph(tid)
        ctx = await self._context(tid)
        target = None
        async for snap in graph.aget_state_history(self._cfg(tid)):
            if snap.config["configurable"]["checkpoint_id"] == checkpoint_id:
                target = snap
                break
        if not target:
            raise EngineError(404, f"No checkpoint {checkpoint_id} in this thread.")
        task = self.tasks.get(tid)
        if task:
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        sha = (target.values or {}).get("git_head")
        # The code goes back with the flow: otherwise the next step would see commits from the abandoned branch.
        if sha and git.is_repo(ctx.root) and git.head(ctx.root) != sha:
            await asyncio.to_thread(git.git, ctx.root, "reset", "--hard", "-q", sha)
        log.info("thread %s rewound to %s (git %s)", tid, checkpoint_id, sha)
        await self._set_status(tid, "running")
        self._launch(tid, None, self._cfg(tid, checkpoint_id))
        return await self.state(tid)
