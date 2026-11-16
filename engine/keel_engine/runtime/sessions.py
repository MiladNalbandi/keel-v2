"""Connected sessions: a person's own Claude Code or OpenCode session does the harness flow's work for keel.

keel never starts such a session and never touches its login: the person opens it, connects it to keel's MCP server
(`keel2 mcp --write`) and tells it to work for keel. Its MCP server process calls keel_session_open once, then
keel_session_next over and over (a long poll). keel stays the control room: the flow, its gates, the tests, the checks
after each step and the commits.

    the person's session ── keel's MCP server ── /api/sessions/* ── this hub (in the engine, in memory)
        keel_session_open           register: client name, folder           Hub.open
        keel_session_next           heartbeat + the next work order          Hub.seen, session_service.orders
        keel_session_plan/todo/done the report: resumes the waiting step     session_service.report

A session is "connected" while it was seen within CONNECTED_S (every next poll counts). A step that waits for a session
while none was seen for GONE_S is "gone": the flow says so and the Inbox offers keep waiting / run this step on keel's
harness / stop.

The hub lives in memory: after an engine restart a session registers again by itself (its next call), and the waiting
work orders are found again from the flows that wait (session_service.orders).
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..clock import utc_iso
from ..tools import git
from ..tools.guard import Snapshot, git_meta

CONNECTED_S = 120       # seen within this many seconds: connected (a long poll calls in every few seconds)
GONE_S = 600            # a step waits and no session was seen this long: gone (the Inbox asks the person)
TODO_MAX = 60           # todo items kept per step
TODO_TEXT_MAX = 300
TODO_STATUS = ("pending", "in_progress", "done")
CLIENT_MAX = 80

# the harness flow's agents a connected session stands in for (content/workflows/harness.yaml)
SESSION_AGENTS = ("harness-planner", "harness-builder")


def now() -> float:
    return time.time()


def iso(t: float) -> str:
    """An epoch time in keel's one UTC format (clock.utc_iso)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


@dataclass
class Session:
    id: str
    project: str
    client: str
    opened: float
    seen: float
    folder: str = ""

    def view(self, at: float | None = None) -> dict:
        at = now() if at is None else at
        ago = max(0, int(at - self.seen))
        return {"id": self.id, "project": self.project, "client": self.client, "folder": self.folder or None,
                "connected_since": iso(self.opened), "last_seen": iso(self.seen), "seen_ago_s": ago,
                "connected": ago <= CONNECTED_S}


@dataclass
class Wait:
    """A flow step that waits for a connected session (Engine._drive records it when the flow pauses there)."""
    tid: str
    project: str
    step: str
    kind: str
    order: dict
    qid: str | None
    since: float
    flagged: bool = False           # the gone notice went out (gate.waiting): the Inbox asks the person
    claimed: str | None = None      # the session that took this work order (keel_session_next)


@dataclass
class Hub:
    sessions: dict[str, Session] = field(default_factory=dict)
    waits: dict[str, Wait] = field(default_factory=dict)
    todos: dict[str, dict] = field(default_factory=dict)     # thread -> {step, todos, at}: the live list of the step
    not_session: dict[str, str] = field(default_factory=dict)  # thread -> updated_at: a pause that is not a session's

    def open(self, project: str, client: str | None, folder: str | None = None) -> Session:
        t = now()
        s = Session(uuid.uuid4().hex[:16], project, (str(client or "") or "an MCP client")[:CLIENT_MAX], t, t,
                    str(folder or "")[:500])
        self.sessions[s.id] = s
        return s

    def seen(self, sid: str) -> Session | None:
        s = self.sessions.get(sid)
        if s:
            s.seen = now()
        return s

    def of_project(self, project: str) -> list[Session]:
        return sorted((s for s in self.sessions.values() if s.project == project), key=lambda s: -s.seen)

    def connected(self, project: str, within: float = CONNECTED_S) -> Session | None:
        at = now()
        return next((s for s in self.of_project(project) if at - s.seen <= within), None)

    def forget_old(self, older: float = 24 * 3600) -> None:
        at = now()
        for sid in [k for k, s in self.sessions.items() if at - s.seen > older]:
            del self.sessions[sid]


def todos_of(raw) -> list[dict]:
    """A todo list as the tools send it ({text, status}), checked: at most TODO_MAX items, a known status each."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("todos is a list of {text, status} (status: pending, in_progress or done).")
    out = []
    for n, t in enumerate(raw[:TODO_MAX]):
        if isinstance(t, str):
            t = {"text": t, "status": "pending"}
        if not isinstance(t, dict):
            raise ValueError(f"todo {n + 1} is not an object {{text, status}}.")
        text = str(t.get("text") or t.get("content") or "").strip()
        status = str(t.get("status") or "pending").strip().lower().replace("-", "_").replace(" ", "_")
        if status == "completed":
            status = "done"
        if status not in TODO_STATUS:
            raise ValueError(f'todo {n + 1}: status "{t.get("status")}" is not one of pending, in_progress, done.')
        if text:
            out.append({"text": text[:TODO_TEXT_MAX], "status": status})
    return out


# ------------------------------------------------------------------ the step's snapshot, kept across the pause
#
# A step that waits for a session pauses (interrupt) and runs again from its top when the report arrives. keel's checks
# after the step (the diff guard, the project's edit tools) compare the folder with how it was when the work order went
# out, so that snapshot is kept on disk until the step finishes (a restart in between loses nothing).

def _snap_file(tid: str, key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:120]
    return config.data_dir() / "sessions" / tid / f"{safe}.json"


def _b64(b: bytes | None) -> str | None:
    return None if b is None else base64.b64encode(b).decode()


def _unb64(s: str | None) -> bytes | None:
    return None if s is None else base64.b64decode(s)


def take_or_load(root: str, tid: str, key: str) -> tuple[Snapshot | None, dict]:
    """The snapshot of the folder when this work order went out: taken the first time, read back after the pause.
    Returns (snapshot or None outside git, {head, at})."""
    f = _snap_file(tid, key)
    if f.is_file():
        try:
            raw = json.loads(f.read_text())
            snap = None
            if raw.get("dirty") is not None:
                snap = Snapshot(dict(raw["dirty"]), {k: _unb64(v) for k, v in (raw.get("content") or {}).items()},
                                {k: (_unb64(v[0]) or b"", int(v[1])) for k, v in (raw.get("gitmeta") or {}).items()})
            return snap, {"head": raw.get("head"), "at": raw.get("at")}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    snap = None
    head = None
    if git.is_repo(root):
        d = git.dirty(root)
        content = {}
        for rel in d:
            p = Path(root) / rel
            content[rel] = p.read_bytes() if p.is_file() else None
        snap = Snapshot(d, content, git_meta(root))
        head = git.head(root)
    meta = {"head": head, "at": utc_iso()}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({
        **meta,
        "dirty": snap.dirty if snap else None,
        "content": {k: _b64(v) for k, v in (snap.content if snap else {}).items()},
        "gitmeta": {k: [_b64(v[0]), v[1]] for k, v in (snap.gitmeta if snap else {}).items()},
    }))
    return snap, meta


def drop_snapshot(tid: str, key: str) -> None:
    try:
        _snap_file(tid, key).unlink(missing_ok=True)
    except OSError:
        pass
