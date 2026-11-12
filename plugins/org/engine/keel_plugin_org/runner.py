"""The org runner: it lives as long as keel's engine, sleeps, and wakes on flow events. No flow ever waits for another.

    assign      a mission → queued (or proposed when the role only watches)
    pump        starts queued missions while the org has fewer than max_running flows; each through keel's api, as the
                Flow page starts one (its own worktree for a team member, the cap, the run mode from the role's autonomy)
    a flow ends → the mission it ran:
        it gave work out (org:delegate)  → delegated; its children start
        it did not                        → reported (its report, or one made from the flow); its boss hears it
        a review flow                     → the person's answer (org:close): accept → done; send back → planned again
                                            (at most limits.send_backs times)
        failed or stopped                 → failed / stopped; its boss hears it
    a boss whose children all ended → its review flow (role.review), or reported with their reports when it has none

The mission tables hold the state, so a restart loses nothing: the runner pumps again when it starts and every SWEEP
seconds.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets

import httpx

from keel_engine import config as keel_config

from . import config, store

log = logging.getLogger("keel.org")
SWEEP = 30.0
WAKE = ("thread.done", "thread.failed", "gate.waiting")
RUN_MODE = {"act": "auto", "propose": "manual"}
REPORT_MAX = 6000


class StartError(Exception):
    def __init__(self, status: int, error: str):
        self.status = status
        super().__init__(error)


async def api_start(project_id: str, body: dict) -> str:
    """Starts a flow the way the Flow page does (POST /api/projects/{pid}/flows) and returns its thread id."""
    url = keel_config.api_url()
    if not url.startswith("http"):
        raise StartError(503, "keel's api is off here (KEEL_API_URL=off): the org cannot start flows.")
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{url}/api/projects/{project_id}/flows", json=body,
                             headers={"X-Keel-Token": keel_config.internal_token(), "X-Keel-Client": "org"})
    except httpx.HTTPError as exc:
        raise StartError(503, f"keel's api did not answer: {type(exc).__name__}") from None
    try:
        out = r.json()
    except ValueError:
        out = {}
    if r.status_code >= 400:
        err = out.get("error") if isinstance(out, dict) else None
        raise StartError(r.status_code, str(err or f"keel's api said {r.status_code}"))
    tid = out.get("thread_id") or out.get("threadId") or out.get("id") if isinstance(out, dict) else None
    if not tid:
        raise StartError(502, "keel's api started the flow but gave no thread id.")
    return str(tid)


class Runner:
    def __init__(self, bus, engine, starter=None):
        self.bus = bus
        self.engine = engine
        self.start_flow = starter or api_start
        self.queue: asyncio.Queue | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.tasks: list[asyncio.Task] = []
        self.handled: set[str] = set()
        self.lock = asyncio.Lock()

    # ------------------------------------------------------------------ life

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.bus.listen(self._hear)
        self.tasks = [asyncio.create_task(self._work()), asyncio.create_task(self._sweep())]

    async def close(self) -> None:
        self.bus.unlisten(self._hear)
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.tasks = []

    def _hear(self, ev: dict) -> None:
        if ev.get("type") in WAKE and self.loop and self.queue is not None:
            try:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, ev)
            except RuntimeError:       # the loop is closing
                pass

    async def _work(self) -> None:
        while True:
            ev = await self.queue.get()
            try:
                await self.on_event(ev)
            except Exception:
                log.exception("org: an event failed (%s)", ev.get("type"))

    async def _sweep(self) -> None:
        while True:
            try:
                for oid in await asyncio.to_thread(store.open_orgs):
                    await self.pump(oid)
            except Exception:
                log.exception("org: the sweep failed")
            await asyncio.sleep(SWEEP)

    # ------------------------------------------------------------------ the person's actions

    async def assign(self, oid: str, org: config.Org, *, role: str, project_id: str, title: str, request: str,
                     source: str | None = None, workflow: str | None = None, cap_tokens: int | None = None) -> dict:
        r = org.roles[role]
        wf = workflow or r.first_flow()
        stage = "plan" if wf and wf == r.plan else "work"
        m = await asyncio.to_thread(store.add, org_id=oid, role=role, project_id=project_id, title=title, request=request,
                                    source=source, workflow=wf, stage=stage,
                                    status="proposed" if r.autonomy == "watch" else "queued", cap_tokens=cap_tokens)
        await asyncio.to_thread(store.note, m["id"], "made", f"You gave this to {role} ({wf}).")
        await self.pump(oid)
        return await asyncio.to_thread(store.get, m["id"])

    async def begin(self, mid: str) -> dict | None:
        """A proposed mission (its role only watches): the person starts it."""
        m = await asyncio.to_thread(store.get, mid)
        if m and m["status"] == "proposed":
            await asyncio.to_thread(store.update, mid, status="queued")
            await asyncio.to_thread(store.note, mid, "started", "You started it.")
            await self.pump(m["org_id"])
        return await asyncio.to_thread(store.get, mid)

    async def accept(self, mid: str, why: str = "") -> dict | None:
        m = await asyncio.to_thread(store.get, mid)
        if m and m["status"] == "reported":
            await self._done(m, why or "You accepted the report.")
        return await asyncio.to_thread(store.get, mid)

    async def stop(self, mid: str) -> dict | None:
        """Stops the mission and everything under it that has not ended."""
        m = await asyncio.to_thread(store.get, mid)
        if not m:
            return None
        todo = [m]
        while todo:
            x = todo.pop()
            todo += await asyncio.to_thread(store.children, x["id"])
            if x["status"] not in store.OPEN:
                continue
            if x["status"] == "running" and x.get("thread_id"):
                self.handled.add(x["thread_id"])
                try:
                    await self.engine.stop(x["thread_id"])
                except Exception as exc:       # a flow that already ended
                    log.info("org: stop %s: %s", x["thread_id"], exc)
            await asyncio.to_thread(store.update, x["id"], status="stopped")
            await asyncio.to_thread(store.note, x["id"], "stopped", "You stopped it.")
        await self.pump(m["org_id"])
        return await asyncio.to_thread(store.get, mid)

    # ------------------------------------------------------------------ starting flows

    async def pump(self, oid: str) -> None:
        async with self.lock:
            row = await asyncio.to_thread(store.org_row, oid)
            if not row:
                return
            try:
                org = config.parse(row["yaml"])
            except config.OrgError as exc:
                for m in await asyncio.to_thread(store.queued, oid):
                    await asyncio.to_thread(store.update, m["id"], status="failed", note=f"The org has problems: {exc.problems[0]}")
                return
            free = org.limits.max_running - await asyncio.to_thread(store.running_count, oid)
            for m in (await asyncio.to_thread(store.queued, oid))[:max(0, free)]:
                await self._launch(org, row, m)

    async def _launch(self, org: config.Org, row: dict, m: dict) -> None:
        role = org.roles.get(m["role"])
        if role is None or role.human:
            await asyncio.to_thread(store.update, m["id"], status="failed", note=f"The role {m['role']} is not in the org any more.")
            return
        key = secrets.token_urlsafe(18)
        stage = m["stage"]
        wf = m["workflow"] if stage != "review" else role.review
        body = {
            "workflow_id": wf,
            "title": f"{role.title or m['role']}: {m['title']}"[:200],
            "request": await asyncio.to_thread(self._request, org, row, m, role),
            # level: "top" for the mission you gave (its report gate is yours), "team" for one a boss gave out (its report
            # goes up to that boss, who checks it in its own review)
            "options": {"org": {"org": m["org_id"], "mission": m["id"], "key": key, "role": m["role"], "stage": stage,
                                "autonomy": role.autonomy, "level": "team" if m.get("parent_id") else "top"},
                        "ticket": m.get("source") if m.get("source") and "#r" not in str(m.get("source")) else None},
        }
        if RUN_MODE.get(role.autonomy):
            body["run_mode"] = RUN_MODE[role.autonomy]
        if m.get("cap_tokens"):
            body["cap_tokens"] = max(1, int(m["cap_tokens"]) - int(m.get("used_tokens") or 0))
        if m.get("parent_id"):
            body["where"] = "worktree"     # team members work side by side, each in its own worktree
            body["force"] = True
        if os.environ.get("KEEL_FAKE") == "1":
            body["allow_fake"] = True      # a demo or test keel runs every agent on the fake model
        await asyncio.to_thread(store.update, m["id"], status="running", run_key=key)
        try:
            tid = await self.start_flow(m["project_id"], body)
        except Exception as exc:          # StartError, or anything else: the mission must not stay "running"
            why = str(exc) if isinstance(exc, StartError) else f"{type(exc).__name__}: {exc}"[:500]
            if not isinstance(exc, StartError):
                log.exception("org: starting %s failed", m["id"])
            await asyncio.to_thread(store.update, m["id"], status="failed", note=why)
            await asyncio.to_thread(store.note, m["id"], "failed", f"The flow did not start: {why}")
            await self._boss_hears(m)
            return
        await asyncio.to_thread(store.link_thread, tid, m["id"], stage)
        await asyncio.to_thread(store.update, m["id"], thread_id=tid)
        await asyncio.to_thread(store.note, m["id"], "flow", f"{wf} started.", {"thread_id": tid, "stage": stage})
        # a fast flow (the fake model) may have ended before its thread was linked: look once
        try:
            st = await self.engine.state(tid)
        except Exception:
            st = None
        if st and st.get("status") in ("done", "failed", "stopped"):
            self.loop.call_soon(self.queue.put_nowait, {"type": "thread.failed" if st["status"] == "failed" else "thread.done",
                                                        "thread_id": tid, "data": {"status": st["status"]}})

    def _request(self, org: config.Org, row: dict, m: dict, role: config.Role) -> str:
        boss = config.boss(org, m["role"])
        lines = [f"You are {role.title or m['role']} (role `{m['role']}`) in the org \"{org.name or row['name']}\"."]
        if m["stage"] == "review":
            lines += ["", f"Your team has ended its work on: {m['title']}", "", "The mission you planned:", m["request"], ""]
            if m.get("note"):
                lines += ["Your plan's summary:", m["note"], ""]
            lines += ["Your team's reports (each says what its flow did; check it, do not trust it):", ""]
            for c in store.children(m["id"]):
                if c.get("source") != f"{m['id']}#r{m['send_backs']}":
                    continue
                rep = c.get("report") or {}
                status = f"status: {c['status']} · flow: {c.get('thread_id') or '-'} · tokens: {c.get('used_tokens') or 0}"
                lines += [f"## {c['role']}: {c['title']}", status, str(rep.get("summary") or c.get("note") or "")[:REPORT_MAX], ""]
            return "\n".join(lines)
        lines += ["", f"Mission: {m['title']}", "", m["request"]]
        if m.get("source") and "#r" not in str(m["source"]):
            lines += ["", f"Source: {m['source']}"]
        dec = m.get("decision") or {}
        if m["send_backs"] and dec.get("choice") == "send_back":
            lines += ["", f"The person sent your last report back ({m['send_backs']} of {org.limits.send_backs}). They said:",
                      dec.get("why") or "(no reason given)"]
        if m["stage"] == "plan":
            members = config.team(org, m["role"])
            if members:
                lines += ["", "Your team (the roles you may give work to, and the workflows each may run):"]
                for t in members:
                    r = org.roles[t]
                    lines.append(f"- {t}: {r.title or t}; workflows: {', '.join(r.workflows)}")
            else:
                lines += ["", "You have no team: do not give work out (an empty children list)."]
            lines += ["", f"At most {org.limits.children} missions."]
        if boss:
            lines += ["", f"You report to {boss}."]
        if m.get("cap_tokens"):
            lines += [f"Budget: {m['cap_tokens']} tokens for this mission."]
        return "\n".join(lines)

    # ------------------------------------------------------------------ flows that end

    async def on_event(self, ev: dict) -> None:
        tid = ev.get("thread_id")
        if not tid:
            return
        found = await asyncio.to_thread(store.by_thread, tid)
        if not found:
            return
        m, stage = found
        if ev["type"] == "gate.waiting":
            d = ev.get("data") or {}
            await asyncio.to_thread(store.note, m["id"], "waiting", f"Waits: {d.get('title') or ev.get('step') or 'a gate'}",
                                    {"thread_id": tid, "step": ev.get("step")})
            return
        if tid in self.handled or m.get("thread_id") != tid or m["status"] != "running":
            return
        self.handled.add(tid)
        status = (ev.get("data") or {}).get("status") or ("failed" if ev["type"] == "thread.failed" else "done")
        used = await self._tokens(tid)
        if used:
            await asyncio.to_thread(store.update, m["id"], used_tokens=int(m.get("used_tokens") or 0) + used)
        m = await asyncio.to_thread(store.get, m["id"])
        if ev["type"] == "thread.failed" or status == "failed":
            err = str((ev.get("data") or {}).get("error") or "the flow failed")[:500]
            await asyncio.to_thread(store.update, m["id"], status="failed", note=err)
            await asyncio.to_thread(store.note, m["id"], "failed", f"The flow failed: {err}", {"thread_id": tid})
            await self._boss_hears(m)
        elif status == "stopped":
            await asyncio.to_thread(store.update, m["id"], status="stopped")
            await asyncio.to_thread(store.note, m["id"], "stopped", "The flow was stopped.", {"thread_id": tid})
            await self._boss_hears(m)
        elif stage == "review":
            await self._reviewed(m)
        else:
            await self._worked(m, tid)
        await self.pump(m["org_id"])

    async def _tokens(self, tid: str) -> int:
        try:
            u = (await self.engine.state(tid)).get("usage") or {}
        except Exception:
            return 0
        return int(u.get("tokens_in") or 0) + int(u.get("tokens_out") or 0)

    async def _worked(self, m: dict, tid: str) -> None:
        mark = f"{m['id']}#r{m['send_backs']}"
        kids = [c for c in await asyncio.to_thread(store.children, m["id"]) if c.get("source") == mark]
        if kids:
            await asyncio.to_thread(store.update, m["id"], status="delegated")
            await asyncio.to_thread(store.note, m["id"], "delegated", f"Waits for {len(kids)} mission(s) of its team.")
            return
        rep = m.get("report") or await self._flow_report(m, tid)
        await asyncio.to_thread(store.update, m["id"], status="reported", report=rep)
        await asyncio.to_thread(store.note, m["id"], "reported", "Its flow ended; the report went up.")
        await self._boss_hears(m)

    async def _flow_report(self, m: dict, tid: str) -> dict:
        """What a flow that is not an org flow did, from its state: its criteria and their status."""
        try:
            st = await self.engine.state(tid)
        except Exception:
            st = {}
        acs = st.get("acs") or []
        done = [a for a in acs if a.get("status") in ("done", "met", "passed")]
        lines = [f"The {m['workflow']} flow ended ({st.get('status') or 'done'})."]
        if acs:
            lines.append(f"{len(done)} of {len(acs)} criteria done:")
            lines += [f"- [{a.get('status')}] {a.get('id')}: {a.get('title')}" for a in acs[:30]]
        if m.get("note"):
            lines.append(m["note"])
        return {"summary": "\n".join(lines), "flow": tid}

    async def _reviewed(self, m: dict) -> None:
        dec = m.get("decision") or {}
        row = await asyncio.to_thread(store.org_row, m["org_id"])
        org = config.parse(row["yaml"]) if row else None
        limit = org.limits.send_backs if org else 0
        if dec.get("choice") == "send_back" and not m.get("parent_id"):
            if m["send_backs"] < limit:
                await asyncio.to_thread(store.update, m["id"], status="queued", stage="plan", send_backs=m["send_backs"] + 1,
                                        workflow=(org.roles[m["role"]].plan or m["workflow"]) if org and m["role"] in org.roles else m["workflow"])
                await asyncio.to_thread(store.note, m["id"], "sent_back", f"You sent it back: {dec.get('why') or '(no reason)'}")
                return
            await asyncio.to_thread(store.update, m["id"], status="reported",
                                    note=f"Sent back {m['send_backs']} times already, the org's limit: it waits for you.")
            await asyncio.to_thread(store.note, m["id"], "limit", "The send-back limit is reached.")
            return
        if dec.get("choice") == "accept" and not m.get("parent_id"):
            await self._done(m, dec.get("why") or "You accepted the report.")
            return
        await asyncio.to_thread(store.update, m["id"], status="reported")
        await asyncio.to_thread(store.note, m["id"], "reported", "The review ended; the report went up.")
        await self._boss_hears(m)

    async def _done(self, m: dict, why: str) -> None:
        """The mission is done, and so is every report under it (a failed or stopped one stays as it is)."""
        await asyncio.to_thread(store.update, m["id"], status="done")
        todo = list(await asyncio.to_thread(store.children, m["id"]))
        while todo:
            x = todo.pop()
            todo += await asyncio.to_thread(store.children, x["id"])
            if x["status"] == "reported":
                await asyncio.to_thread(store.update, x["id"], status="done")
        await asyncio.to_thread(store.note, m["id"], "done", why)

    async def _boss_hears(self, m: dict) -> None:
        """A mission ended: when all of its boss's missions of this round ended, the boss reviews (or reports)."""
        if not m.get("parent_id"):
            return
        boss = await asyncio.to_thread(store.get, m["parent_id"])
        if not boss or boss["status"] != "delegated":
            return
        mark = f"{boss['id']}#r{boss['send_backs']}"
        kids = [c for c in await asyncio.to_thread(store.children, boss["id"]) if c.get("source") == mark]
        if any(c["status"] in store.OPEN for c in kids):
            return
        row = await asyncio.to_thread(store.org_row, boss["org_id"])
        org = None
        if row:
            try:
                org = config.parse(row["yaml"])
            except config.OrgError:
                org = None
        role = org.roles.get(boss["role"]) if org else None
        if role and role.review:
            await asyncio.to_thread(store.update, boss["id"], status="queued", stage="review")
            await asyncio.to_thread(store.note, boss["id"], "review", f"Its team ended ({len(kids)}): {role.review} is next.")
            return
        rep = {"summary": boss.get("note") or f"The team ended {len(kids)} mission(s).",
               "children": [{"role": c["role"], "title": c["title"], "status": c["status"],
                             "summary": str((c.get("report") or {}).get("summary") or "")[:REPORT_MAX]} for c in kids]}
        await asyncio.to_thread(store.update, boss["id"], status="reported", report=rep)
        await asyncio.to_thread(store.note, boss["id"], "reported", "Its team ended; the report went up.")
        await self._boss_hears(boss)
