"""Threads: start, run in the background, resume, stop, history, rewind.

One LangGraph thread per flow. Checkpoints live in `$KEEL_DATA/checkpoints.db` (AsyncSqliteSaver);
the same file holds a small registry (thread -> project, workflow, title, status, start body) so a
restarted engine can rebuild each graph and carry on with threads that were running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from .. import config, rules
from ..events import EventBus
from ..tools import codegraph, git
from ..workflows.model import Workflow, from_dict
from ..workflows.templates import get_template
from . import ladder as ladder_mod
from . import memory as memory_mod
from . import migrate
from .compiler import compile_workflow
from .state import ThreadContext, initial_state, merge_unlocks, normalize_unlocks

log = logging.getLogger(__name__)

REGISTRY = """
create table if not exists keel_threads (
  thread_id text primary key, project_id text not null, workflow_id text not null, title text not null,
  status text not null, root text not null, body text not null, error text,
  created_at text not null, updated_at text not null
)"""


CONTINUE_GRACE = float(os.environ.get("KEEL_CONTINUE_GRACE", "60"))


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
        self.pending: set[str] = set()            # running when keel stopped; continued by the api (or the grace timer)
        self._grace: asyncio.Task | None = None
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
        await self.conn.execute(memory_mod.SCHEMA)
        await self.conn.commit()
        await migrate.migrate(self.conn)
        if resume_running:
            # Threads that were running when keel stopped continue once the api has sent their logins (they live in
            # memory only) and their folder: POST /threads/{id}/continue. Without that call they continue by
            # themselves after CONTINUE_GRACE seconds (fine for models that need no login).
            async with self.conn.execute("select thread_id from keel_threads where status = 'running'") as cur:
                self.pending = {tid for (tid,) in await cur.fetchall()}
            if self.pending:
                log.info("%d thread(s) wait for the api to continue them", len(self.pending))
                self._grace = asyncio.create_task(self._continue_later())

    async def _continue_later(self):
        await asyncio.sleep(CONTINUE_GRACE)
        for tid in list(self.pending):
            await self.continue_after_restart(tid, None, None)

    async def continue_after_restart(self, tid: str, keys: dict | None, root: str | None) -> dict:
        """Continue a thread that was running when keel stopped, with its logins and its folder now."""
        await self._row(tid)
        if keys:
            await self.set_keys(tid, keys)
        if tid in self.pending and tid not in self.tasks:
            self.pending.discard(tid)
            try:
                await self.set_root(tid, root)
            except EngineError as exc:
                log.warning("not continuing thread %s: %s", tid, exc)
                await self._set_status(tid, "failed", f"{exc} {exc.hint or ''}".strip())
                return await self.state(tid)
            log.info("continuing thread %s after a restart%s", tid, "" if keys else " (no logins sent)")
            await self._import_legacy_unlocks(tid)
            self._launch(tid, None)
        return await self.state(tid)

    async def close(self):
        if self._grace:
            self._grace.cancel()
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
                            request=(body.get("request") or "").strip(), models=body.get("models") or {}, settings=body.get("settings") or {}, mcp=body.get("mcp") or [],
                            skills=body.get("skills") or {}, agents=body.get("agents") or {}, keys=self.keys.get(tid, {}), bus=self.bus,
                            memory=memory_mod.AgentMemory(self.conn, tid))
        ctx.spawn = lambda workflow_id, seed, link, tid=tid: self.start_child(tid, workflow_id, seed, link)
        async with self.conn.execute("select path, phase, by, reason, at from thread_unlocks where thread_id = ? order by rowid",
                                     (tid,)) as cur:
            ctx.api_unlocks = [{k: v for k, v in zip(("path", "phase", "by", "reason", "at"), r) if v} for r in await cur.fetchall()]
        self.ctxs[tid] = ctx
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
            async for _values in graph.astream(inp, cfg or self._cfg(tid), stream_mode="values"):
                pass
            snap = await graph.aget_state(self._cfg(tid))
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

    @staticmethod
    def _at_pause(ctx: ThreadContext, snap) -> dict:
        """The state to show while a step runs or waits for the user.

        A paused node has not returned yet, so the graph values still hold the step before it (on the first step:
        phase "none"). Show the paused step and its phase instead.
        """
        values = dict(snap.values or {})
        node = next(iter(snap.next or ()), None)
        if not node:
            return values
        step = ctx.workflow.step(node[: -len("__fix")] if node.endswith("__fix") else node)
        phase = "review-fix" if node.endswith("__fix") else ((step.phase if step else None) or values.get("phase") or "none")
        return {**values, "current": node, "phase": phase, "status": "waiting"}

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
        state["data"] = dict(body.get("data") or {})
        state["parent"] = body.get("parent")
        if git.is_repo(root):
            state["branch"] = git.branch(root)
            state["git_head"] = git.head(root)
            state["base_head"] = state["git_head"]
            git.exclude_engine_files(root)
            state["preexisting"] = git.snapshot(root)
        codegraph.sync_later(root)          # the code graph catches up with edits made since the last index (background)
        await self._import_legacy_unlocks(tid)
        started = {"workflow": wf.id, "workflow_id": wf.id, "title": ctx.title, "flow": wf.flow, "root": root,
                   "acs": len(state["acs"]), "fake": ctx.fake}
        if body.get("parent"):
            started["parent"] = body["parent"]
        ctx.emit("thread.started", data=started)
        self._launch(tid, state)
        return tid

    async def start_child(self, parent: str, workflow_id: str, seed: dict, link: dict) -> str:
        """start_flow: a new thread of another workflow on the parent's project, with the parent's models, settings and
        logins, and a seed (title, request, recipe, symptoms, evidence, inline acs with their status, needs_e2e, no_gates). It starts the way POST /threads
        does, so the api learns about it from thread.started (data.parent links it back)."""
        row = await self._row(parent)
        body = json.loads(row["body"])
        wf = get_template(workflow_id)
        if not wf and (body.get("workflow") or {}).get("id") == workflow_id:
            wf = from_dict(body["workflow"], body["workflow"].get("yaml") or None)
        if not wf:
            raise EngineError(404, f"No workflow {workflow_id} to start.", "start_flow takes a keel workflow id (fix, feature, ...).")
        request = str(seed.get("request") or "").strip()
        for label, k in (("Symptoms", "symptoms"), ("Reproduction recipe", "recipe"), ("Evidence so far", "evidence")):
            v = seed.get(k)
            if v:
                text = "\n".join(f"- {x}" for x in v) if isinstance(v, list) else str(v)
                request += f"\n\n{label}:\n{text}"
        if seed.get("needs_e2e"):
            request += "\n\nA regression end-to-end test is required for this bug."
        acs = []
        for n, a in enumerate(seed.get("acs") or []):
            a = a if isinstance(a, dict) else {"title": str(a)}
            acs.append({"id": str(a.get("id") or f"AC-{n + 1}"), "layer": a.get("layer") or "API", "title": str(a.get("title") or ""),
                        **({"status": a["status"]} if a.get("status") else {})})
        child = {k: body[k] for k in ("project_id", "models", "settings", "mcp", "skills", "agents") if k in body}
        child.update(root=row["root"], workflow=wf, title=str(seed.get("title") or row["title"])[:200], acs=acs or None,
                     request=request.strip()[:8000], parent=link, keys=self.keys.get(parent),
                     data={"seed": seed, **{k: seed[k] for k in ("recipe", "symptoms", "needs_e2e", "no_gates") if k in seed}})
        return await self.start_thread(child)

    async def state(self, tid: str) -> dict:
        row = await self._row(tid)
        graph = await self._graph(tid)
        snap = await graph.aget_state(self._cfg(tid))
        v = snap.values or {}
        waiting = self._waiting(snap)
        running = tid in self.tasks
        ctx_now = await self._context(tid)
        status = row["status"]
        if running:
            status = "running"
        elif status not in ("stopped", "failed"):
            if waiting:
                status = "waiting"
            elif v.get("status") in ("done", "failed", "stopped"):
                status = v["status"]
        # The graph's values hold the last step that FINISHED; while a step runs (or waits) show that step instead.
        now = self._at_pause(ctx_now, snap) if (running or waiting) and snap.next else v
        n = 0
        async for _ in graph.aget_state_history(self._cfg(tid)):
            n += 1
        out = {
            "thread_id": tid, "project_id": row["project_id"], "workflow_id": row["workflow_id"], "title": row["title"],
            "status": status, "current": now.get("current"), "phase": now.get("phase") or "none", "ac": v.get("ac"),
            "acs": [{"id": a["id"], "layer": a.get("layer", "API"), "title": a.get("title", ""), "status": a.get("status", "todo")}
                    for a in v.get("acs") or []],
            "usage": {**{"tokens_in": 0, "tokens_out": 0, "tokens_cached": 0, "cost_usd": 0.0, "premium_requests": 0, "cap_tokens": 0}, **(v.get("usage") or {})},
            "checkpoints": n, "updated_at": row["updated_at"],
            "blockers": list(v.get("blockers") or []),
            "unlocks": [{"path": u.get("path"), "phase": u.get("phase")} for u in merge_unlocks(list(v.get("unlocks") or []), ctx_now.api_unlocks)],
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
            if waiting.get("labels"):
                out["waiting"]["labels"] = dict(waiting["labels"])
            if waiting.get("questions"):
                out["waiting"]["questions"] = list(waiting["questions"])
            if waiting.get("choices"):
                out["waiting"]["choices"] = list(waiting["choices"])
        out["gate_log"] = list(((v.get("gates") or {}).get("log") or [])[-50:])
        if v.get("parent"):
            out["parent"] = v["parent"]
        if v.get("children"):
            out["children"] = list(v["children"])
        if v.get("item"):
            out["item"] = v["item"]
        err = row["error"] or v.get("error")
        if err and status in ("failed", "stopped"):
            out["error"] = err
        return out

    async def set_root(self, tid: str, root: str | None) -> None:
        """Moves a thread to its project's folder now, and refuses to go on in a folder that is gone.

        A thread keeps the folder it started in. Started again another way (`keel2 start --docker` mounts the
        project at its real path instead of /workspace), the old folder is empty or missing: agents there would
        work on nothing and the commits would go nowhere.
        """
        row = await self._row(tid)
        old = row["root"]
        if not root and not _project_there(old):
            root = relocate(old)
        if root and root != old:
            if not _project_there(root):
                raise EngineError(409, f"The project folder {root} is empty or missing.", "Check how keel was started (keel2 status).")
            await self.conn.execute("update keel_threads set root = ? where thread_id = ?", (root, tid))
            await self.conn.commit()
            ctx = self.ctxs.get(tid)
            if ctx:
                ctx.root = root
            log.info("thread %s moved from %s to %s", tid, old, root)
            return
        if not _project_there(old):
            raise EngineError(409, f"This flow's project folder {old} is empty or missing.",
                              "keel was started another way since this flow began (for example with or without --docker). "
                              "Start keel the same way again, or start a new flow.")

    async def set_keys(self, tid: str, keys: dict) -> None:
        """Logins are kept in memory only (never stored), so after an engine restart the api sends them again."""
        await self._row(tid)
        self.keys[tid] = dict(keys)
        if tid in self.ctxs:
            self.ctxs[tid].keys = dict(keys)

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
        await self._import_legacy_unlocks(tid)
        asked = self._waiting(snap).get("id")
        self._launch(tid, Command(resume={"decision": decision, "why": why or "", "payload": payload or {}, "asked": asked}))
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
        ctx.emit("thread.done", data={"status": "stopped"})
        return await self.state(tid)

    # ------------------------------------------------------------ unlocks

    async def unlocks(self, tid: str) -> list[dict]:
        """The thread's unlocks: those in its graph state plus those granted since its last step."""
        await self._row(tid)
        ctx = await self._context(tid)
        snap = await (await self._graph(tid)).aget_state(self._cfg(tid))
        return merge_unlocks(list((snap.values or {}).get("unlocks") or []), ctx.api_unlocks)

    async def add_unlock(self, tid: str, path: str, phase: str | None, reason: str | None, by: str = "api") -> list[dict]:
        """Opens one path for one phase (default: the phase the thread is in now). The engine is the only writer:
        the unlock is stored in the engine DB, merged into the flow state at the next step, and the guards of the
        thread's running agents (hook context files, ToolBoxes) get it at once."""
        rel = str(path or "").strip().removeprefix("./")
        if not rel:
            raise EngineError(400, "path is empty.", "Send the file path relative to the repo root.")
        if rel.startswith("/") or ".." in rel.split("/") or "\0" in rel:
            raise EngineError(400, "That path is outside the project.", "Use a path relative to the repo root.")
        phase = (phase or "").strip() or (await self.state(tid))["phase"]
        if phase not in rules.PHASES:
            raise EngineError(400, f'"{phase}" is not a keel phase.', "Pick one of: " + ", ".join(rules.PHASES))
        item = {"path": rel, "phase": phase, "by": by, "at": _now()}
        if (reason or "").strip():
            item["reason"] = reason.strip()[:500]
        await self._store_unlocks(tid, normalize_unlocks(item, phase, by))
        return await self.unlocks(tid)

    async def _store_unlocks(self, tid: str, items: list[dict]):
        for u in items:
            await self.conn.execute("insert or ignore into thread_unlocks values (?,?,?,?,?,?)",
                                    (tid, u["path"], u["phase"], u.get("by") or "api", u.get("reason"), u.get("at") or _now()))
        await self.conn.commit()
        if tid in self.ctxs:              # a context made later reads them from the table
            self.ctxs[tid].add_unlocks(items)

    async def _import_legacy_unlocks(self, tid: str):
        """Once per project: unlocks a keel v0.3 state file in the project still holds join this thread (that file is
        never written again). Recorded in legacy_unlock_imports, so a later start or resume does not import them twice."""
        root = (await self._row(tid))["root"]
        async with self.conn.execute("select 1 from legacy_unlock_imports where root = ?", (root,)) as cur:
            if await cur.fetchone():
                return
        items = await asyncio.to_thread(migrate.legacy_unlocks, root, tid)
        if not items:
            return
        await self.conn.execute("insert or ignore into legacy_unlock_imports values (?,?,?,?)", (root, tid, len(items), _now()))
        await self._store_unlocks(tid, items)
        log.info("thread %s: imported %d unlock(s) from the project's keel v0.3 state file", tid, len(items))

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
            elif v.get("item") and s and s.per_item:
                note = f"{v['item']} · {note}"
            out.append({"id": snap.config["configurable"]["checkpoint_id"], "n": n, "step": step,
                        "at": snap.created_at, "note": note[:300]})
        return out

    async def rewind(self, tid: str, checkpoint_id: str) -> dict:
        await self._row(tid)
        graph = await self._graph(tid)
        ctx = await self._context(tid)
        ctx.done_calls.clear()
        await ctx.memory.clear()          # a rewind goes back on purpose: agents start fresh from there
        target = None
        async for snap in graph.aget_state_history(self._cfg(tid)):
            if snap.config["configurable"]["checkpoint_id"] == checkpoint_id:
                target = snap
                break
        if not target:
            raise EngineError(404, f"No checkpoint {checkpoint_id} in this thread.")
        # "Rewind here" re-runs that step: go to the state before it. Continuing *after* it would keep the step's
        # result in the flow while git (back at the last commit) no longer has its files.
        if target.parent_config:
            async for snap in graph.aget_state_history(self._cfg(tid)):
                if snap.config["configurable"]["checkpoint_id"] == target.parent_config["configurable"]["checkpoint_id"]:
                    target = snap
                    break
        checkpoint_id = target.config["configurable"]["checkpoint_id"]
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


def _project_there(root: str | None) -> bool:
    """A git repo, or a folder of them; not a folder that holds nothing but keel's own .keel/."""
    if not root or not os.path.isdir(root):
        return False
    if git.is_repo(root):
        return True
    return any(name != ".keel" for name in os.listdir(root))


def relocate(old: str | None) -> str | None:
    """Where a thread's project is now, when keel2 mounts it the other way (/workspace <-> its real path)."""
    if not old:
        return None
    ws = str(config.workspace())
    cands = []
    if old == "/workspace" or old.startswith("/workspace/"):
        cands.append(ws + old[len("/workspace"):])
    elif ws == "/workspace":
        name = os.path.basename(old.rstrip("/"))
        if name == os.environ.get("KEEL_PROJECT_NAME"):
            cands.append("/workspace")
        cands.append(f"/workspace/{name}")
    return next((c for c in cands if c != old and _project_there(c)), None)
