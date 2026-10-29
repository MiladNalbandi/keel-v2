"""Threads: start, run in the background, resume, stop, history, rewind.

One LangGraph thread per flow. Checkpoints live in `$KEEL_DATA/checkpoints.db` (AsyncSqliteSaver);
the same file holds a small registry (thread -> project, workflow, title, status, start body) so a
restarted engine can rebuild each graph and carry on with threads that were running.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import uuid
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from .. import config, extensions, layout, rules
from ..events import EventBus
from ..tools import git, source_ref
from ..tools import mcp as mcp_tools
from ..workflows.model import Workflow, from_dict
from ..workflows.templates import get_template
from . import bug, hunt, ladder as ladder_mod
from . import memory as memory_mod
from . import branch_sync, db, doc_history, flow_doc, migrate, prompts, run_mode, spec_check
from . import choices as choices_mod
from . import finding_decisions, thread_models
from .compiler import FINISH, answer_problem, compile_workflow, running_usage
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


from ..clock import utc_iso as _now


class Engine:
    def __init__(self, bus: EventBus, data_dir: Path | None = None):
        self.bus = bus
        self.data_dir = data_dir
        self.conn: aiosqlite.Connection | None = None
        self.saver: AsyncSqliteSaver | None = None
        self.saver_conn: aiosqlite.Connection | None = None
        self.graphs: dict[str, object] = {}
        self.ctxs: dict[str, ThreadContext] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.pending: set[str] = set()            # running when keel stopped; continued by the api (or the grace timer)
        self._grace: asyncio.Task | None = None
        self._resuming: dict[str, asyncio.Lock] = {}   # one resume at a time per thread (web + MCP, a double click)
        self._closing = False                     # close() is cancelling the drivers: a flow it cuts off is not a stopped flow
        self.keys: dict[str, dict] = {}
        # 0.18.64 thread -> {MCP server: {NAME: value}}: its MCP servers' env values, in memory only like the logins
        # (checkpoints.db keeps the names: tools/mcp.py strip_env)
        self.mcp_env: dict[str, dict] = {}
        self.sources: dict[str, str] = {}     # thread -> what a started flow works on ("pr:20"), until POST /threads answers
        self.base_notes: dict[str, str] = {}  # 0.18.64 thread -> its base against the remotes, until POST /threads answers

    # ------------------------------------------------------------ lifecycle

    async def open(self, resume_running: bool = True):
        self._closing = False
        d = self.data_dir or config.data_dir()
        d.mkdir(parents=True, exist_ok=True)
        # Autocommit: a write commits inside its own statement, so this connection never holds the write lock while it
        # waits for the event loop (a sync writer on the loop then waited for that lock: "database is locked" on CI).
        self.conn = await aiosqlite.connect(str(d / "checkpoints.db"), timeout=db.BUSY_MS / 1000, isolation_level=None)
        async with self.conn.execute("pragma journal_mode=wal") as cur:
            mode = (await cur.fetchone() or ["?"])[0]
        if str(mode).lower() != "wal":
            log.warning("engine DB is in %s mode, not WAL: parallel writers may wait on each other", mode)
        await self.conn.execute(f"pragma busy_timeout = {db.BUSY_MS}")
        # LangGraph's checkpointer gets its own connection. It serialises its own reads and writes with a lock, but
        # keel's code (memory, registry) used the same connection without that lock: a cursor one coroutine still read
        # from turned another coroutine's checkpoint INSERT into a stale read-then-write, which SQLite refuses at once
        # ("database is locked" on CI, no busy wait). Separate connections never share a read snapshot.
        self.saver_conn = await aiosqlite.connect(str(d / "checkpoints.db"), timeout=db.BUSY_MS / 1000, isolation_level=None)
        await self.saver_conn.execute(f"pragma busy_timeout = {db.BUSY_MS}")
        self.saver = AsyncSqliteSaver(self.saver_conn)
        await self.saver.setup()
        await self.conn.execute(REGISTRY)
        await self.conn.execute(memory_mod.SCHEMA)
        await self.conn.commit()
        await migrate.migrate(self.conn)
        await self._strip_stored_env()
        if resume_running:
            # Threads that were running when keel stopped continue once the api has sent their logins (they live in
            # memory only) and their folder: POST /threads/{id}/continue. Without that call they continue by
            # themselves after CONTINUE_GRACE seconds (fine for models that need no login).
            async with self.conn.execute("select thread_id from keel_threads where status = 'running'") as cur:
                self.pending = {tid for (tid,) in await cur.fetchall()}
            if self.pending:
                log.info("%d thread(s) wait for the api to continue them", len(self.pending))
                self._grace = asyncio.create_task(self._continue_later())

    async def _strip_stored_env(self):
        """0.18.64 a flow stored before 0.18.64 kept its MCP servers' env values in keel_threads: they move to memory
        (this run still has them) and the row keeps the names."""
        async with self.conn.execute("select thread_id, body from keel_threads where body like '%\"env\"%'") as cur:
            rows = await cur.fetchall()
        for tid, raw in rows:
            try:
                body = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not any(isinstance(s, dict) and s.get("env") for s in body.get("mcp") or []):
                continue
            body["mcp"], values = mcp_tools.strip_env(body["mcp"])
            self.mcp_env[tid] = {**values, **self.mcp_env.get(tid, {})}
            await self.conn.execute("update keel_threads set body = ? where thread_id = ?", (json.dumps(body), tid))
        await self.conn.commit()

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
        # Set before the cancel: _drive then leaves a cut-off flow 'running', so open() continues it after the restart.
        self._closing = True
        if self._grace:
            self._grace.cancel()
        for t in list(self.tasks.values()):
            t.cancel()
        for t in list(self.tasks.values()):
            try:
                await t
            except BaseException:
                pass
        for conn in (self.saver_conn, self.conn):
            if conn:
                await conn.close()

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
                            request=(body.get("request") or "").strip(), models=body.get("models") or {}, settings=body.get("settings") or {},
                            mcp=mcp_tools.with_env(body.get("mcp"), self.mcp_env.get(tid)),
                            skills=body.get("skills") or {}, agents=body.get("agents") or {}, keys=self.keys.get(tid, {}), bus=self.bus,
                            other_projects=list(body.get("other_projects") or []),
                            memory=memory_mod.AgentMemory(self.conn, tid))
        ctx.spawn = lambda workflow_id, seed, link, tid=tid: self.start_child(tid, workflow_id, seed, link)
        ctx.synced_base = body.get("synced_base")      # 0.18.64 keel_branch_sync moved where the branch's work starts
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
                data = {"kind": kind, "title": waiting.get("title"), "detail": waiting.get("detail"), **self._notice(ctx, waiting)}
                if kind == "budget":
                    ctx.emit("budget.warn", step=waiting.get("step"), data={**data, "paused": True})
                ctx.emit("gate.waiting", step=waiting.get("step"), data=data)
                return
            status = snap.values.get("status") or "done"
            if status not in ("stopped", "failed"):
                status = "done"
            await self._set_status(tid, status, snap.values.get("error"))
            await self._end_worktree_containers(tid, ctx)
            await self._hunt_link(ctx, snap.values, status)
            if status == "failed":
                ctx.emit("thread.failed", data={"error": snap.values.get("error"), "status": status})
            else:
                done = {"status": status, "usage": snap.values.get("usage")}
                warned = (snap.values.get("data") or {}).get("warnings") or []
                if warned:
                    # the close note says what the flow went on with (the api's "flow is done" notification shows title)
                    done.update(warnings=[w.get("text") for w in warned], title=snap.values.get("note") or "")
                ctx.emit("thread.done", data=done)
        except asyncio.CancelledError:
            if not self._closing:      # a user's Stop (or a rewind); a shutdown keeps the row 'running'
                await self._set_status(tid, "stopped")
                await self._end_worktree_containers(tid, ctx)
            raise
        except Exception as exc:
            log.exception("thread %s failed", tid)
            await self._set_status(tid, "failed", f"{type(exc).__name__}: {exc}"[:1000])
            await self._end_worktree_containers(tid, ctx)
            ctx.emit("thread.failed", data={"error": f"{type(exc).__name__}: {exc}"[:1000]})
        finally:
            ctx.resume_qid = None        # the answer it carried was used (or the run ended without it)

    async def _end_worktree_containers(self, tid: str, ctx: ThreadContext):
        """0.18.57 a flow that ends in a worktree takes the containers docker compose made there with it (an agent's
        `docker compose run` and its dependencies; they stayed running after the flow). Not while another flow of this
        engine still runs or waits in the same worktree. Never fails the flow."""
        from . import container

        if not rules.in_keel_worktree(ctx.root):
            return
        try:
            async with self.conn.execute("select count(*) from keel_threads where root = ? and thread_id != ? "
                                         "and status in ('running', 'waiting')", (ctx.root, tid)) as cur:
                (others,) = await cur.fetchone()
            if others:
                return
            removed = await asyncio.to_thread(container.cleanup_worktree, ctx.root)
            if removed:
                log.info("thread %s: removed %d container(s) made in its worktree %s", tid, len(removed), ctx.root)
                ctx.emit("worktree.containers_removed", data={"worktree": ctx.root, "containers": removed})
        except Exception:      # noqa: BLE001
            log.warning("thread %s: could not clean up its worktree's containers", tid, exc_info=True)

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
        names = {"ladder", "ladder_soft"}
        return any(names & set(s.actions()) or any(l.sub in names for l in s.lanes or []) for s in ctx.workflow.steps)

    @staticmethod
    def _notice(ctx: ThreadContext, waiting: dict) -> dict:
        """0.18.55 what the api needs for a notification a person can read: the flow, the step's name and phase, the
        criterion, and the gate's short lines (summary) when it has them."""
        step = ctx.workflow.step(str(waiting.get("step") or ""))
        out = {"flow": ctx.workflow.flow, "step_name": step.name if step else None, "phase": step.phase if step else None,
               "ac": waiting.get("ac"), "summary": waiting.get("summary")}
        return {k: v for k, v in out.items() if v}

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
        if wf.needs_plugins:
            from ..marketplace import needs   # a workflow that needs a plugin keel did not load: 409 {error, missing}

            needs.check(wf.id, wf.needs_plugins)
        root = str(Path(body["root"]).resolve())
        if not Path(root).is_dir():
            raise EngineError(400, f"The project folder {root} does not exist.")
        source = await self._preflight(wf, root, body)       # refused here, no run exists
        tid = uuid.uuid4().hex
        if source:
            self.sources[tid] = source
        stored = {k: v for k, v in body.items() if k not in ("keys",)}
        # 0.18.64 an MCP server's env holds its secrets: the row keeps the names, memory the values (like the logins)
        stored["mcp"], env_values = mcp_tools.strip_env(body.get("mcp"))
        stored["workflow"] = {**wf.model_dump(exclude_none=True)}
        stored["root"] = root
        now = _now()
        await self.conn.execute("insert into keel_threads values (?,?,?,?,?,?,?,?,?,?)",
                                (tid, body["project_id"], wf.id, body["title"], "running", root, json.dumps(stored), None, now, now))
        await self.conn.commit()
        logins, from_keys = mcp_tools.env_from_keys(body.get("keys"))
        if logins:
            self.keys[tid] = logins
        if env_values or from_keys:
            self.mcp_env[tid] = {**from_keys, **env_values}
        ctx = await self._context(tid)
        state = initial_state(ctx, body.get("acs"))
        state["data"] = dict(body.get("data") or {})
        state["parent"] = body.get("parent")
        # 0.17.3 started from a spec (the Wiki): it is the flow's spec, and its criteria are the flow's criteria
        spec = spec_check.start_spec(root, state["data"].get("spec"))
        if spec:
            state["spec"] = spec
            text = spec_check.read_spec(root, spec)
            # 0.18.64 its text at the start: the spec check names a risk or criterion the flow's agents drop from it
            state["data"]["spec_at_start"] = text[:prompts.EVIDENCE_MAX]
            if not state["acs"]:
                from .feature_actions import order_acs, spec_order, with_depends

                # v0.17.5 the spec's ORDER line is the build order from the start, with each criterion's depends_on
                state["acs"] = with_depends(order_acs(prompts.parse_acs(text), spec_order(text)), text)
        if await asyncio.to_thread(git.is_repo, root):
            state["branch"] = await asyncio.to_thread(git.branch, root)
            state["git_head"] = await asyncio.to_thread(git.head, root)
            state["base_head"] = state["git_head"]
            # 0.18.74 the branch already has commits of its own (a flow that continues a pull request's branch): a flow that
            # builds (a RED/GREEN loop) checks and reviews its own work only, from here (verdict_actions.flow_base). A
            # flow that checks the branch itself (an audit, a knowledge refresh) still reads all of it.
            builds = any(st.phase in ("red", "green") for st in wf.steps)
            if builds and await asyncio.to_thread(_ahead_of_base, root):
                state["data"]["own_since"] = state["git_head"]
            await asyncio.to_thread(git.exclude_engine_files, root)
            layout.hide(root)               # keel / external: .keel/ stays out of git (.git/info/exclude)
            state["preexisting"] = await asyncio.to_thread(git.snapshot, root)
            check = await self._base_check(root, body, state["git_head"])
            state["data"].pop("remotes_fetched", None)
            if check:
                state["data"]["base_check"] = check
        else:
            check = None
        extensions.on_thread_start(root, tid)   # the parts catch up (the code graph syncs edits since its last index)
        await self._import_legacy_unlocks(tid)
        started = {"workflow": wf.id, "workflow_id": wf.id, "title": ctx.title, "flow": wf.flow, "root": root,
                   "acs": len(state["acs"]), "fake": ctx.fake}
        if body.get("parent"):
            started["parent"] = body["parent"]
        if isinstance((body.get("data") or {}).get("worktree"), dict):
            started["worktree"] = body["data"]["worktree"]      # its own worktree (bug.own_worktree): the api records it
        via = (body.get("data") or {}).get("started_via")
        if via in ("mcp", "tui", "xal"):
            started["via"] = via            # v0.15.8 keel_start_flow (keel2 mcp --write), keel TUI's /flow, xal: the timeline says so
        if check and check.get("note"):
            started["note"] = check["note"]  # 0.18.64 how far the base is behind each remote; a failed fetch said plainly
            started["remotes"] = check["remotes"]
            self.base_notes[tid] = check["note"]
        ctx.emit("thread.started", data=started)
        self._launch(tid, state)
        return tid

    async def _base_check(self, root: str, body: dict, head: str | None) -> dict | None:
        """0.18.64 (field report bug 4) the flow's base against every remote: what the api's start fetched
        (data.remotes_fetched: it fetched before it made the branch, so the branch could start from the newer code),
        else a fetch here (every remote at once, git.FETCH_EACH s each). A hand-over works on its parent's branch: its
        parent's start said it. Never fails the start: a failed fetch is a line of the note."""
        data = body.get("data") or {}
        fetched = data.get("remotes_fetched")
        try:
            if not isinstance(fetched, list):
                if body.get("parent"):
                    return None
                fetched = await asyncio.to_thread(branch_sync.fetch_all, root, body.get("keys"))
            if not fetched:
                return None
            return await asyncio.to_thread(branch_sync.report, root, fetched, head)
        except Exception:                                   # noqa: BLE001 the start goes on without the note
            log.exception("base check for %s", root)
            return None

    async def sync_branch(self, tid: str, onto: str | None = None, mode: str = "merge", action: str = "start") -> dict:
        """0.18.64 (field report bug 6) keel merges (or rebases) the flow's branch onto a base, in the folder or the
        worktree the flow works in (runtime/branch_sync.py). keel runs it, not an agent, so the guards that refuse an
        agent's merge or commit do not apply; never while a step runs. A conflict stops it with the files; continue
        commits the resolution with keel's commit identity. When the branch moved, where its own work starts (the
        merge base) becomes the flow's base_head at its next step (ctx.synced_base), so the diff of the flow's work does
        not hold the base's commits it brought in."""
        row = await self._row(tid)
        if tid in self.tasks:
            raise EngineError(409, "The flow is running a step now.",
                              "Sync its branch when it waits at a gate, or pause it first.")
        ctx = await self._context(tid)
        root = row["root"]
        # the checkout must be on the flow's own branch (a rebase that waits shows HEAD): never sync another flow's work
        mine = ((await (await self._graph(tid)).aget_state(self._cfg(tid))).values or {}).get("branch")
        now = await asyncio.to_thread(git.branch, root)
        if mine and now and now not in (mine, "HEAD") and await asyncio.to_thread(git.rev, root, f"refs/heads/{mine}"):
            raise EngineError(409, f"The folder is on {now}, not on this flow's branch {mine}.",
                              f"Check out {mine} again (or sync the flow that works on {now}).")
        try:
            out = await asyncio.to_thread(branch_sync.sync, root, onto, mode, action, ctx.settings)
        except branch_sync.SyncError as exc:
            raise EngineError(exc.status, exc.message, exc.hint) from exc
        if out.get("ok") and out.get("synced") and out.get("base_head"):
            body = json.loads(row["body"])
            body["synced_base"] = out["base_head"]
            await self.conn.execute("update keel_threads set body = ?, updated_at = ? where thread_id = ?",
                                    (json.dumps(body), _now(), tid))
            await self.conn.commit()
            ctx.synced_base = out["base_head"]
            if out.get("commit"):
                files = await asyncio.to_thread(lambda: git.git(root, "diff", "--name-only", "HEAD^1", "HEAD").stdout.split())
                ctx.emit("keel.applied", data={"kind": "commit", "sha": out["commit"], "ref": out["commit"],
                                               "subject": f"Merge {out.get('onto')} into {out.get('branch')}", "files": files})
            extensions.on_commit(root)
        ctx.emit("branch.synced", data={k: out.get(k) for k in ("ok", "synced", "mode", "onto", "branch", "head", "note",
                                                               "conflicts") if out.get(k) is not None})
        return {**out, "thread": await self.state(tid)}

    async def _preflight(self, wf: Workflow, root: str, body: dict) -> str | None:
        """A workflow whose first step checks its input before anything runs (review-pr's ticket_prep: the PR or
        branch must be named and reachable) refuses the start with an EngineError; else the source's key, or None."""
        first = wf.steps[0] if wf.steps else None
        if first is None or first.kind != "code" or first.action != "ticket_prep":
            return None
        from . import ticket_actions

        return await asyncio.to_thread(ticket_actions.preflight, root, body)

    async def _hunt_link(self, ctx, values: dict, status: str) -> None:
        """A flow hunt-next started has ended: close its bug group (done) or open it again (failed / stopped)."""
        try:
            msg = await asyncio.to_thread(hunt.child_finished, ctx.project_id or ctx.root, ctx.root, values.get("data") or {},
                                          status, ctx.title, ctx.workflow.id)
            if msg:
                ctx.emit("hunt.closed" if status == "done" else "hunt.reopened", data={"note": msg})
        except Exception:                                   # the flow's own end never fails because of the backlog
            log.exception("hunt link for %s", ctx.title)

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
        # The request goes into every agent's prompt. A flow with a reproducer (fix) must not show it a theory about the
        # cause (a hunt group's cause, a diagnosis): its test would confirm the theory. There the evidence stays in
        # data.evidence for the steps that may read it (the investigators, the plan).
        blind = any(s.agent == "reproducer" for s in wf.steps)
        for label, k in (("Symptoms", "symptoms"), ("Reproduction recipe", "recipe"), ("Evidence so far", "evidence")):
            v = seed.get(k)
            if v and not (blind and k == "evidence"):
                text = "\n".join(f"- {x}" for x in v) if isinstance(v, list) else str(v)
                request += f"\n\n{label}:\n{text}"
            if k == "symptoms" and isinstance(v, list) and len(v) > 1:
                request += ("\n\nOne cause, several symptoms: each symptom above is a regression criterion. The plan names "
                            "the assertion that covers each one, not only the one the first test reproduces.")
        if seed.get("needs_e2e"):
            request += "\n\nA regression end-to-end test is required for this bug."
        acs = []
        for n, a in enumerate(seed.get("acs") or []):
            a = a if isinstance(a, dict) else {"title": str(a)}
            acs.append({"id": str(a.get("id") or f"AC-{n + 1}"), "layer": a.get("layer") or "API", "title": str(a.get("title") or ""),
                        **({"status": a["status"]} if a.get("status") else {}), **({"kind": a["kind"]} if a.get("kind") else {})})
        child = {k: body[k] for k in ("project_id", "models", "settings", "mcp", "skills", "agents", "other_projects") if k in body}
        request = request.strip()
        if len(request) > prompts.REQUEST_MAX:
            # every agent of the flow reads the request; the whole evidence (data.seed) goes to the steps that write
            # the spec (prompts.evidence_block). It was cut here, and a spike's file:line evidence never reached them.
            request = (request[:prompts.REQUEST_MAX].rstrip() + f"\n\n[cut at {prompts.REQUEST_MAX} of {len(request)} "
                       "characters: the steps that write the spec get the whole evidence]")
        if isinstance(seed.get("evidence"), str):
            seed = {**seed, "evidence": seed["evidence"][:prompts.EVIDENCE_MAX]}
        if "mcp" in child:
            child["mcp"] = mcp_tools.with_env(child["mcp"], self.mcp_env.get(parent))   # the values live in memory only
        child.update(root=row["root"], workflow=wf, title=str(seed.get("title") or row["title"])[:200], acs=acs or None,
                     request=request, parent=link, keys=self.keys.get(parent),
                     data={"seed": seed, **{k: seed[k] for k in ("recipe", "symptoms", "evidence", "stale", "needs_e2e", "no_gates")
                                             if k in seed}})
        # a fix of a hunt finding works on its own fix/ branch from the base branch, not on the hunt's (runtime/bug.py)
        own = await asyncio.to_thread(bug.own_worktree, row["root"], workflow_id, seed, body.get("settings"), child["title"])
        if own:
            child.update(root=own["root"], data={**child["data"], "worktree": own["worktree"]})
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
            "acs": [{"id": a["id"], "layer": a.get("layer", "API"), "title": a.get("title", ""), "status": a.get("status", "todo"),
                     **({"kind": a["kind"]} if a.get("kind") else {})}
                    for a in v.get("acs") or []],
            # 0.18.17 with the usage so far of the agents that still run (they book it only when they finish)
            "usage": {**{"tokens_in": 0, "tokens_out": 0, "tokens_cached": 0, "cost_usd": 0.0, "premium_requests": 0, "cap_tokens": 0, "cap_usd": 0.0},
                      **running_usage(v.get("usage") or {}, ctx_now.running if running else {})},
            "checkpoints": n, "updated_at": row["updated_at"],
            # 0.17.3 the spec the flow works on (or was started from): the Wiki's spec status links the two
            "spec": v.get("spec") or None,
            "blockers": list(v.get("blockers") or []),
            "unlocks": [{"path": u.get("path"), "phase": u.get("phase")} for u in merge_unlocks(list(v.get("unlocks") or []), ctx_now.api_unlocks)],
            "run_mode": run_mode.normalize((ctx_now.settings or {}).get("run_mode")),
            # 0.18.57 the branch or ref the flow's file:line citations are about (the Code page opens them there)
            "source_ref": await asyncio.to_thread(source_ref.flow_source_ref, row["root"], v.get("data")),
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
            if waiting.get("doc"):
                # 0.18.64 a gate that shows a flow's document: its text from the file now, so an edit made while it
                # waits (by hand, keel_flow_document) shows at once, with when it was made
                out["waiting"]["detail"] = await asyncio.to_thread(flow_doc.gate_text, row["root"], v.get("data") or {},
                                                                   str(waiting.get("step") or ""))
            if waiting.get("id"):
                out["waiting"]["id"] = waiting["id"]
            if waiting.get("ac"):
                # 0.18.64 an AC gate names its criterion: the web loads its code (GET /threads/{tid}/ac-code)
                out["waiting"]["ac"] = waiting["ac"]
            # 0.18.28 the waiting step's own name ("verify the suite + commit"): a suggestion names the gate by it, never
            # by the flow's title cut short
            name = self._step_name(ctx_now, waiting.get("step"))
            if name:
                out["waiting"]["name"] = name
            # 0.18.28 what a plain approve and a plain reject do here, and whether the reason is used
            out["waiting"]["plain"] = choices_mod.plain_effects(waiting)
            if waiting.get("labels"):
                out["waiting"]["labels"] = dict(waiting["labels"])
            if waiting.get("questions"):
                out["waiting"]["questions"] = list(waiting["questions"])
            if waiting.get("choices"):
                out["waiting"]["choices"] = list(waiting["choices"])
                # 0.19.0 each choice's label, meaning, whether it needs a reason, and the plain approve's pick
                out["waiting"]["choice_info"] = list(
                    waiting.get("choice_info") or choices_mod.for_question(waiting, waiting.get("step"))["choice_info"])
        out["gate_log"] = list(((v.get("gates") or {}).get("log") or [])[-50:])
        if v.get("parent"):
            out["parent"] = v["parent"]
        if v.get("children"):
            out["children"] = list(v["children"])
        if v.get("item"):
            out["item"] = v["item"]
        order = (v.get("data") or {}).get("ac_order")
        if order:
            # v0.17.4 the criteria are in build order; this says where the order came from (spec, plan, keel, person)
            out["ac_order"] = order
        pr_url = (v.get("data") or {}).get("pr_url")
        if pr_url:
            # The PR the open_pr step opened (v0.5.0: a task moves to review with it).
            out["pr_url"] = pr_url
        warned = (v.get("data") or {}).get("warnings") or []
        if warned:
            out["warnings"] = [w.get("text") for w in warned]
        err = row["error"] or v.get("error")
        if err and status in ("failed", "stopped"):
            out["error"] = err
        return out

    @staticmethod
    def _step_name(ctx, step_id: str | None) -> str | None:
        """A step's name by its id; a review's fix node (<id>__fix) is its review's; None when the flow has no such step."""
        if not step_id:
            return None
        base = str(step_id).removesuffix("__fix")
        found = next((s for s in ctx.workflow.steps if s.id in (step_id, base)), None)
        return found.name if found else None

    async def set_root(self, tid: str, root: str | None) -> None:
        """Moves a thread to its project's folder now, and refuses to go on in a folder that is gone.

        A thread keeps the folder it started in. Started again another way (`keel2 start --docker` mounts the
        project at its real path instead of /workspace), the old folder is empty or missing: agents there would
        work on nothing and the commits would go nowhere.
        """
        row = await self._row(tid)
        old = row["root"]
        if not root and not await asyncio.to_thread(_project_there, old):
            root = await asyncio.to_thread(relocate, old)
        if root and root != old:
            if not await asyncio.to_thread(_project_there, root):
                raise EngineError(409, f"The project folder {root} is empty or missing.", "Check how keel was started (keel2 status).")
            await self.conn.execute("update keel_threads set root = ? where thread_id = ?", (root, tid))
            await self.conn.commit()
            ctx = self.ctxs.get(tid)
            if ctx:
                ctx.root = root
            log.info("thread %s moved from %s to %s", tid, old, root)
            return
        if not await asyncio.to_thread(_project_there, old):
            raise EngineError(409, f"This flow's project folder {old} is empty or missing.",
                              "keel was started another way since this flow began (for example with or without --docker). "
                              "Start keel the same way again, or start a new flow.")

    async def set_keys(self, tid: str, keys: dict) -> None:
        """Logins are kept in memory only (never stored), so after an engine restart the api sends them again.
        0.18.64 an "mcp_env:<server>:<NAME>" entry is an MCP server's env value: it goes to the servers, not the logins."""
        await self._row(tid)
        keys, values = mcp_tools.env_from_keys(keys)
        self.keys[tid] = dict(keys)
        if tid in self.ctxs:
            self.ctxs[tid].keys = dict(keys)
        if values:
            mine = self.mcp_env.setdefault(tid, {})
            for server, env in values.items():
                mine[server] = {**mine.get(server, {}), **env}
            if tid in self.ctxs:
                row = await self._row(tid)
                self.ctxs[tid].mcp = mcp_tools.with_env(json.loads(row["body"]).get("mcp"), mine)

    async def resume(self, tid: str, decision: str, why: str | None, payload: dict | None,
                     question_id: str | None = None, by: str | None = None) -> dict:
        """`by` names who answers when it is not the person (an in-process caller only, the Supervisor: no route passes
        it); the step's gate.decided events carry it (ThreadContext.answer_by)."""
        # The checks below await; without the lock two calls both pass "not running" and start two drivers on one thread.
        async with self._resuming.setdefault(tid, asyncio.Lock()):
            return await self._resume(tid, decision, why, payload, question_id, by)

    async def _resume(self, tid: str, decision: str, why: str | None, payload: dict | None,
                      question_id: str | None = None, by: str | None = None) -> dict:
        # The question the answer is for: a field, or `payload.question_id` (the api passes the payload through as it is).
        if payload and "question_id" in payload:
            payload = dict(payload)
            question_id = question_id or payload.pop("question_id") or None
        row = await self._row(tid)
        if tid in self.tasks:
            raise EngineError(409, "The flow is running, not waiting.", "Wait for it to reach a gate.")
        if row["status"] == "stopped":
            raise EngineError(409, "The flow was stopped.", "Rewind to a checkpoint to continue it.")
        graph = await self._graph(tid)
        snap = await graph.aget_state(self._cfg(tid))
        if not self._waiting(snap):
            raise EngineError(409, "The flow is not waiting for a decision.")
        now = self._waiting(snap).get("id")
        if question_id and now and question_id != now:
            raise EngineError(409, "That question is not the one the flow waits at now: the flow moved on.",
                              "Read the waiting question again and answer that one.")
        if decision not in ("approve", "reject"):
            raise EngineError(400, "decision must be approve or reject.")
        problem = answer_problem(self._waiting(snap), decision, why, payload) \
            or finding_decisions.problem(self._waiting(snap), decision, why, payload)
        if problem:
            raise EngineError(400, *problem)
        await self._set_status(tid, "running")
        await self._import_legacy_unlocks(tid)
        asked = self._waiting(snap).get("id")
        # The user answers this question: a run mode changed while it waited does not answer it instead (Compiler._ask).
        (await self._context(tid)).resume_qid = asked
        resume = {"decision": decision, "why": why or "", "payload": payload or {}, "asked": asked}
        if by:
            resume["by"] = by
        took = choices_mod.answered(self._waiting(snap), decision, payload)
        self._launch(tid, Command(resume=resume))
        # 0.18.64 what the answer did (the choice that runs): a reject with the choice edit runs edit, and keel_answer
        # printed "(reject)"
        return {**(await self.state(tid)), "answered": took}

    async def set_models(self, tid: str, model: dict | None, agent: str | None = None, step: str | None = None,
                         phase: str | None = None, keys: dict | None = None) -> dict:
        """0.18.64 a model for this flow only (an agent, a step, or an agent's steps in one phase; runtime/thread_models.py),
        stored with the thread's models like set_mode's run mode, used from the agent's next call. model None: the pick
        is taken back. Other flows and the project's settings do not change."""
        row = await self._row(tid)
        ctx = await self._context(tid)
        targets, problem = thread_models.targets(ctx.workflow, agent, step, phase)
        if problem:
            raise EngineError(400, problem)
        body = json.loads(row["body"])
        models, picks = thread_models.apply(ctx.models, body.get("model_picks") or {}, targets, model)
        body["models"], body["model_picks"] = models, picks
        await self.conn.execute("update keel_threads set body = ?, updated_at = ? where thread_id = ?", (json.dumps(body), _now(), tid))
        await self.conn.commit()
        ctx.models = models
        if keys:
            # the new model's login or API key (the api sends the thread's with it): kept in memory only, as at the start
            await self.set_keys(tid, {**self.keys.get(tid, {}), **keys})
        ctx.emit("thread.models", data={"changed": targets, "model": model})
        return {"thread_id": tid, "changed": targets, **thread_models.view(models, picks, ctx.workflow, ctx.root)}

    async def models_of(self, tid: str) -> dict:
        """0.18.64 the flow's model table: the default, each agent's, each agent step's model now, the person's picks."""
        row = await self._row(tid)
        ctx = await self._context(tid)
        return {"thread_id": tid, **thread_models.view(ctx.models, json.loads(row["body"]).get("model_picks") or {}, ctx.workflow,
                                                     ctx.root)}

    async def set_mode(self, tid: str, mode: str, by: str | None = None) -> dict:
        """Changes the thread's run mode (runtime/run_mode.py) during a run: stored with the thread's settings, used from
        the next pause on. A question that waits now keeps waiting for the user. 0.18.74 [by] (who changed it, as the api
        knows it) goes into the event and the flow's gate log: a field report could not tell who turned auto on."""
        row = await self._row(tid)
        if mode not in run_mode.MODES:
            raise EngineError(400, f'"{mode}" is not a run mode.', "Pick one of: " + ", ".join(run_mode.MODES))
        body = json.loads(row["body"])
        before = run_mode.normalize((body.get("settings") or {}).get("run_mode"))
        settings = {**(body.get("settings") or {}), "run_mode": mode}
        who = (by or "").strip()[:80] or "someone (an older client did not say)"
        if before != mode:
            # the gate log gets it at the flow's next step (Compiler._wrap): the final review and the PR body list it
            settings["mode_log"] = [*list(settings.get("mode_log") or []), f"run mode {before} → {mode} by {who} at {_now()}"]
        body["settings"] = settings
        await self.conn.execute("update keel_threads set body = ?, updated_at = ? where thread_id = ?", (json.dumps(body), _now(), tid))
        await self.conn.commit()
        ctx = await self._context(tid)
        ctx.settings = {**(ctx.settings or {}), "run_mode": mode, "mode_log": settings.get("mode_log") or []}
        if before != mode:
            ctx.emit("thread.mode", data={"mode": mode, "from": before, "by": who})
        return await self.state(tid)

    async def finish_by_hand(self, tid: str, done: list[str] | None, message: str = "", by: str = "") -> dict:
        """0.18.74 a stopped flow that the person finished themselves (a field report: a stopped flow could not be "finished
        by hand"): the folder's changes are committed through keel's secret scan (keel's engine files left out), the
        criteria named in [done] are marked done, and the flow ends as done with a gate log line that says so."""
        row = await self._row(tid)
        if row["status"] not in ("stopped", "failed"):
            raise EngineError(409, "Only a stopped flow can be finished by hand.", "Stop it first, or answer its question.")
        ctx = await self._context(tid)
        root = ctx.root
        graph = await self._graph(tid)
        snap = await graph.aget_state(self._cfg(tid))
        values = dict(snap.values or {})
        acs = [dict(a) for a in values.get("acs") or []]
        known = {a["id"] for a in acs}
        wanted = [x for x in dict.fromkeys(done or []) if x]
        unknown = [x for x in wanted if x not in known]
        if unknown:
            raise EngineError(400, f"This flow has no criterion {', '.join(unknown)}.")
        sha = None
        if await asyncio.to_thread(git.is_repo, root):
            sha = await asyncio.to_thread(_commit_by_hand, root, row["title"], message)
        for a in acs:
            if a["id"] in wanted:
                a["status"] = "done"
        gates = copy.deepcopy(values.get("gates") or {})
        who = (by or "").strip()[:80] or "the person"
        line = (f"finished by hand by {who}" + (f": {message.strip()[:200]}" if message.strip() else "")
                + (f" · commit {sha[:7]}" if sha else " · nothing to commit")
                + (f" · done: {', '.join(wanted)}" if wanted else ""))
        gates["log"] = [*list(gates.get("log") or []), line]
        await graph.aupdate_state(self._cfg(tid), {"acs": acs, "gates": gates, "status": "done", "note": line[:300]},
                                  as_node=FINISH)
        await self._set_status(tid, "done")
        ctx.emit("thread.done", data={"status": "done", "by_hand": True, "commit": sha, "done": wanted})
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
        snap = await (await self._graph(tid)).aget_state(self._cfg(tid))
        await self._hunt_link(ctx, dict(snap.values or {}), "stopped")
        ctx.emit("thread.done", data={"status": "stopped"})
        return await self.state(tid)

    # ------------------------------------------------------------ unlocks

    async def rules_now(self, tid: str) -> dict:
        """v0.17.2 "Rules now" on the Flow page: what agents may change in the thread's phase (runtime/rules_view.py),
        with the engine of its last agent and the last shell command an agent saw fail."""
        from . import rules_view

        st = await self.state(tid)
        ctx = await self._context(tid)
        graph = await self._graph(tid)
        v = (await graph.aget_state(self._cfg(tid))).values or {}
        ac_id = st.get("ac") if isinstance(st.get("ac"), str) else (st.get("ac") or {}).get("id")
        ac = next((a for a in st.get("acs") or [] if a["id"] == ac_id), None)
        lane = rules.ac_lane(ac) if ac else None
        started = self.bus.of(tid, "agent.started")
        last = (started[-1].get("data") or {}) if started else {}
        if not last:
            m = (ctx.models or {}).get("default") or {}
            last = {"provider": m.get("provider"), "mode": m.get("mode")}
        unlocks = merge_unlocks(list(v.get("unlocks") or []), ctx.api_unlocks)
        out = await asyncio.to_thread(rules_view.now, ctx.root, st.get("phase"), lane, unlocks, last.get("provider"),
                                      last.get("mode"), (v.get("data") or {}).get("last_command_failure"))
        return {**out, "thread_id": tid, "status": st.get("status"), "current": st.get("current")}

    async def document(self, tid: str) -> dict:
        """0.18.64 the flow's document (its clarify document, else its spec) from its file: path, kind, text, whether the
        person edited it after keel wrote it (and when), and the gate the flow waits at (None while it runs)."""
        row = await self._row(tid)
        snap = await (await self._graph(tid)).aget_state(self._cfg(tid))
        doc = await asyncio.to_thread(flow_doc.read, row["root"], snap.values or {})
        if not doc:
            raise EngineError(404, "This flow has no document yet.",
                              "A clarify flow writes its document before its result gate; a feature flow writes its spec first.")
        waiting = self._waiting(snap) if tid not in self.tasks and row["status"] != "stopped" else None
        return {**doc, "thread_id": tid, "waiting": (waiting or {}).get("step")}

    async def ac_code(self, tid: str, ac_id: str, sha: str | None = None, path: str | None = None) -> dict:
        """0.18.64 a criterion's code for the web's AC gate: its RED and GREEN commits with each file's diff (cut), found
        since the flow's start as the gate's text finds them (flow_actions.ac_code); with `sha` and `path` one file."""
        from . import flow_actions

        ac_id = str(ac_id or "").strip().upper()
        if not re.fullmatch(r"[A-Z]+-[\w.]+", ac_id):
            raise EngineError(400, f"'{ac_id}' is not an acceptance criterion id.", "Send ac=AC-1, for example.")
        await self._row(tid)
        ctx = await self._context(tid)
        snap = await (await self._graph(tid)).aget_state(self._cfg(tid))
        since = (snap.values or {}).get("base_head")
        out = await asyncio.to_thread(flow_actions.ac_code, ctx.root, ac_id, since, sha or None, path or None)
        return {**out, "thread_id": tid}

    async def review_result(self, tid: str) -> dict:
        """0.18.67 a review flow's result (review-pr's data.ticket_review after record) for the Review page's Problems:
        the confirmed findings, the dropped and not-checked ones, quality notes, the build checks, the depth. Before record
        has run: {"done": false}."""
        await self._row(tid)
        snap = await (await self._graph(tid)).aget_state(self._cfg(tid))
        data = (snap.values or {}).get("data") or {}
        review = data.get("ticket_review") or {}
        if "verdict" not in review:
            return {"thread_id": tid, "done": False}
        keep = ("title", "source", "depth", "verdict", "counts", "findings", "dropped", "not_checked", "quality", "build",
                "coverage", "standards", "own_lenses", "stack", "files", "skipped", "summary", "items")
        return {"thread_id": tid, "done": True, **{k: review.get(k) for k in keep if k in review}}

    async def set_document(self, tid: str, text: str) -> dict:
        """0.18.64 replace the flow's document while it waits at a gate (keel_flow_document), recorded in keel's file
        history as the person's edit. The gate's text shows the file from then on; a clarify hand-over uses it."""
        if not (text or "").strip():
            raise EngineError(400, "text is empty.", "Send the document's whole new text.")
        # the resume lock: an answer that arrives at the same time never runs a step on half of a write
        async with self._resuming.setdefault(tid, asyncio.Lock()):
            row = await self._row(tid)
            if tid in self.tasks:
                raise EngineError(409, "The flow is running, not waiting at a gate.", "Wait for it to reach a gate.")
            snap = await (await self._graph(tid)).aget_state(self._cfg(tid))
            if row["status"] == "stopped" or not self._waiting(snap):
                raise EngineError(409, "The flow is not waiting at a gate.",
                                  "Its document changes only while it waits; edit the file itself after the flow ended.")
            found = flow_doc.of(snap.values or {})
            if not found:
                raise EngineError(404, "This flow has no document yet.")
            rel, _kind = found
            try:
                await asyncio.to_thread(flow_doc.write, row["root"], rel, text)
            except (ValueError, OSError) as exc:
                raise EngineError(400, f"keel cannot write {rel}: {exc}") from exc
            ctx = await self._context(tid)
            sha = await asyncio.to_thread(doc_history.sha_of, row["root"], rel)
            ctx.emit("doc.version", step=self._waiting(snap).get("step"),
                     data={**doc_history.event(row["root"], rel, sha), "by_kind": "user", "by_name": "edited while the flow waited",
                           "phase": (snap.values or {}).get("phase"), "via": "api"})
        return await self.document(tid)

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
        sha = (target.values or {}).get("git_head")
        goes_back = bool(sha) and await asyncio.to_thread(git.is_repo, ctx.root) and await asyncio.to_thread(git.head, ctx.root) != sha
        if goes_back:
            # `git reset --hard` below would throw away uncommitted work in tracked files, and nothing in keel can bring it
            # back. keel has no stash precedent (agents are even forbidden to stash), so refuse: the person decides.
            changed = sorted(p for p, xy in (await asyncio.to_thread(git.dirty, ctx.root)).items() if xy != "??")
            if changed:
                shown = ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else "")
                raise EngineError(409, f"The project folder has uncommitted changes ({shown}); going back to that step would delete them.",
                                  "Commit or stash them (git stash), then rewind again.")
        task = self.tasks.get(tid)
        if task:
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        ctx.done_calls.clear()
        ctx.auto_seen.clear()
        await ctx.memory.clear()          # a rewind goes back on purpose: agents start fresh from there
        # The code goes back with the flow: otherwise the next step would see commits from the abandoned branch.
        if goes_back:
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


def _ahead_of_base(root: str) -> bool:
    """Does HEAD have commits its base does not (keel init's setup commits aside)? A new branch made for the flow: no."""
    from . import bases, verdict_actions

    base = verdict_actions.base_ref(root, rules.load_config(root))
    if not base:
        return False
    own, _setup = bases.own_base(root, base)
    n = git.git(root, "rev-list", "--count", f"{own or base}..HEAD").stdout.strip()
    return n.isdigit() and int(n) > 0


def _commit_by_hand(root: str, title: str, message: str) -> str | None:
    """The folder's changes as one commit, keel's engine files left out, refused when the staged diff holds a secret."""
    from .actions import commit_args

    git.git(root, "add", "-A", "--", ".")          # keel's engine files stay out through .git/info/exclude
    staged = git.git(root, "diff", "--cached").stdout
    if not staged.strip():
        return None
    from ..rules import checks

    hits = checks.scan_secrets("\n".join(ln[1:] for ln in staged.splitlines() if ln.startswith("+") and not ln.startswith("+++")))
    if hits:
        git.git(root, "reset", "-q")
        raise EngineError(409, "A secret is in the changes: nothing was committed.",
                          "; ".join(str(h.get("why")) for h in hits[:3]) + ". Take it out, then finish again.")
    who, trailer = commit_args(root, None, rules.load_config(root))
    subject = f"chore: {title.strip()[:60] or 'the flow'}: finished by hand"
    body = message.strip()[:2000]
    r = git.git(root, *who, "commit", "-q", "-m", subject, *(["-m", body] if body else []), *trailer)
    if r.returncode != 0:
        git.git(root, "reset", "-q")
        raise EngineError(409, f"git commit failed: {(r.stderr or r.stdout).strip()[:300]}")
    return git.head(root)
