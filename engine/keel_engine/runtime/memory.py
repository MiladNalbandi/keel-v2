"""Agent memory: what one agent did on one step, kept in the engine's database on /data.

An agent run is known by its attempt key (step, criterion, agent, copy, section). When the same step runs again,
after a keel restart, a "try again" or a send-back, the agent continues its own CLI session (claude --resume,
codex exec resume) instead of starting from nothing. A provider that cannot resume gets a short trail of what its
last try read and did. A rewind clears the thread's memory on purpose: the user asked to go back.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import aiosqlite

SCHEMA = """
create table if not exists agent_memory (
  thread_id text not null,
  key text not null,
  provider text not null,
  root text not null,
  session text,
  status text not null,          -- running | done | failed
  trail text not null default '[]',
  said text not null default '',
  updated_at text not null,
  primary key (thread_id, key)
)
"""

TRAIL_MAX = 40


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class AgentMemory:
    def __init__(self, conn: aiosqlite.Connection, thread_id: str):
        self.conn, self.thread_id = conn, thread_id
        self._trails: dict[str, list[str]] = {}
        self._tasks: set[asyncio.Task] = set()

    async def get(self, key: str) -> dict | None:
        async with self.conn.execute(
                "select provider, root, session, status, trail, said from agent_memory where thread_id = ? and key = ?",
                (self.thread_id, key)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return {"provider": row[0], "root": row[1], "session": row[2], "status": row[3],
                "trail": json.loads(row[4] or "[]"), "said": row[5] or ""}

    async def start(self, key: str, provider: str, root: str, session: str | None, keep_trail: bool):
        trail = (await self.get(key) or {}).get("trail", []) if keep_trail else []
        self._trails[key] = list(trail)
        await self.conn.execute(
            "insert into agent_memory values (?,?,?,?,?,?,?,?,?) on conflict(thread_id, key) do update set "
            "provider = excluded.provider, root = excluded.root, session = excluded.session, status = excluded.status, "
            "trail = excluded.trail, updated_at = excluded.updated_at",
            (self.thread_id, key, provider, root, session, "running", json.dumps(trail[-TRAIL_MAX:]), "", now()))
        await self.conn.commit()

    def note(self, key: str, line: str):
        """One line of the trail (a file read, a command, a write); saved in the background, never blocks the agent."""
        trail = self._trails.setdefault(key, [])
        trail.append(line[:200])
        del trail[:-TRAIL_MAX]
        self._later(self._save_trail(key))

    def set_session(self, key: str, session: str):
        self._later(self._exec("update agent_memory set session = ?, updated_at = ? where thread_id = ? and key = ?",
                               (session, now(), self.thread_id, key)))

    async def finish(self, key: str, status: str, said: str = ""):
        await self.flush()
        await self.conn.execute(
            "update agent_memory set status = ?, said = ?, trail = ?, updated_at = ? where thread_id = ? and key = ?",
            (status, said[:1500], json.dumps(self._trails.get(key, [])[-TRAIL_MAX:]), now(), self.thread_id, key))
        await self.conn.commit()

    async def clear(self):
        await self.flush()
        await self.conn.execute("delete from agent_memory where thread_id = ?", (self.thread_id,))
        await self.conn.commit()
        self._trails.clear()

    async def flush(self):
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # -- internals
    def _later(self, coro):
        try:
            t = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            return
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _save_trail(self, key: str):
        await self._exec("update agent_memory set trail = ?, updated_at = ? where thread_id = ? and key = ?",
                         (json.dumps(self._trails.get(key, [])[-TRAIL_MAX:]), now(), self.thread_id, key))

    async def _exec(self, sql: str, args: tuple):
        await self.conn.execute(sql, args)
        await self.conn.commit()


def attempt_key(step_id: str, ac: dict | None, agent: str, index: int, section: str | None) -> str:
    return "|".join([step_id, (ac or {}).get("id", ""), agent, str(index), section or ""])


def trail_line(kind: str, text: str, extra: dict) -> str | None:
    """A short line for the trail, or None for steps not worth remembering."""
    if kind == "read":
        return f"read {extra.get('path') or text[:120]}"
    if kind in ("write", "edit"):
        return f"{kind} {extra.get('path') or ''}".strip()
    if kind == "tool":
        tool = extra.get("tool") or "tool"
        return f"{tool}: {text.splitlines()[0][:160] if text else ''}"
    return None


def resume_note(prev: dict, resuming: bool) -> str:
    """What the agent is told about its last try on this step."""
    if resuming:
        if prev.get("status") == "running":
            return ("keel was restarted while you were working on this step. Continue the same task from where you "
                    "stopped: everything you changed is still on disk. Do not start over.")
        return "You worked on this step before (this is your earlier conversation). Use what you already know."
    lines = prev.get("trail") or []
    if not lines and not prev.get("said"):
        return ""
    out = ["Your last try on this step " + ("was stopped by a keel restart" if prev.get("status") == "running"
                                            else "ended") + ". It already did this (do not repeat it unless needed):"]
    out += [f"- {l}" for l in lines[-25:]]
    if prev.get("said"):
        out.append("It said at the end: " + prev["said"][:600])
    return "\n".join(out)
