"""Approvals: keel's one place to ask a person and wait for the answer (core).

Who asks today: KeelBot's commands that change something (runtime/helper.py: the guard hook and the ToolBox ask
through it) and the acting tools of `keel2 mcp --write` (`/plugins/ask`). A question waits in memory until the person
answers (the Inbox, KeelBot's panel), its time is up (permissions.ASK_TIMEOUT) or its asker closes it. The api keeps a
row per question in its `approvals` table, from the events below.

    ask(kind, project, title, command, *, source, ...)   waits for the answer (async)
    ask_blocking(...)                                     the same, from a worker thread (the ToolBox)
    open(...) + wait(id) / wait_blocking(id)              the two halves, for an asker that tells others in between
    answer(id, decision, why)                             the person's answer: once | always | deny
    pending(project), asked(id), close(...)

An answer is {"decision": "allow" | "deny", "why": str}. `asked(id)` is for an asker that polls (`keel2 mcp`).

Events (thread_id = the asker's session, else the question's id; step "approval"):
    approval.asked      {id, kind, project, title, command, path, source, thread_id, session, at}
    approval.answered   the same, plus {decision: once | always | deny | null, why, status, by}
                        status: approved | denied | expired (nobody answered in time) | closed (its asker ended)
                        by: person | keel

One broker per event bus (`of(bus)`): the app and its tests each have their own bus.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import uuid
import weakref
from datetime import datetime, timezone
from typing import Callable

from .runtime import permissions

log = logging.getLogger(__name__)

DECISIONS = ("once", "always", "deny")
TIMEOUT_WHY = "Nobody answered in 10 minutes, so the command did not run."
DENY_WHY = "The person said no to this command."
KEEP_DECIDED = 200          # answers kept for asked() until the asker reads them

# called once when a question ends: (question, the person's word or "" when nobody answered, the answer)
OnAnswer = Callable[[dict, str, dict], None]


class ApprovalError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Approvals:
    """The questions that wait for a person, and their answers."""

    def __init__(self, bus):
        self.bus = bus
        self._lock = threading.Lock()
        self.questions: dict[str, dict] = {}
        self._futures: dict[str, concurrent.futures.Future] = {}
        self._on_answer: dict[str, OnAnswer] = {}
        self.decided: dict[str, dict] = {}

    # ---- asking ------------------------------------------------------------------------------------------------

    def open(self, kind: str, project: str, title: str, command: str, *, source: str, thread_id: str | None = None,
             session: str | None = None, path: str = "", on_answer: OnAnswer | None = None) -> dict:
        """A new waiting question (approval.asked); the asker waits with wait() or polls with asked()."""
        qid = "p_" + uuid.uuid4().hex[:12]
        q = {"id": qid, "kind": kind, "project": project, "title": title, "command": command, "path": path or "",
             "source": source, "thread_id": thread_id, "session": session, "at": _now()}
        with self._lock:
            self.questions[qid] = q
            self._futures[qid] = concurrent.futures.Future()
            if on_answer:
                self._on_answer[qid] = on_answer
        self._emit("approval.asked", q)
        return dict(q)

    async def wait(self, qid: str, timeout: float | None = None) -> dict:
        fut = self._futures.get(qid)
        if fut is None:
            return self._gone(qid)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(fut), timeout or permissions.ASK_TIMEOUT)
        except asyncio.TimeoutError:
            return self._expire(qid, fut)

    def wait_blocking(self, qid: str, timeout: float | None = None) -> dict:
        fut = self._futures.get(qid)
        if fut is None:
            return self._gone(qid)
        try:
            return fut.result(timeout=timeout or permissions.ASK_TIMEOUT)
        except concurrent.futures.TimeoutError:
            return self._expire(qid, fut)

    async def ask(self, kind: str, project: str, title: str, command: str, *, source: str, thread_id: str | None = None,
                  session: str | None = None, path: str = "", on_answer: OnAnswer | None = None,
                  timeout: float | None = None) -> dict:
        """Asks the person and waits, at most `timeout` (permissions.ASK_TIMEOUT); no answer in time is a deny."""
        q = self.open(kind, project, title, command, source=source, thread_id=thread_id, session=session, path=path,
                      on_answer=on_answer)
        return await self.wait(q["id"], timeout)

    def ask_blocking(self, kind: str, project: str, title: str, command: str, *, source: str, thread_id: str | None = None,
                     session: str | None = None, path: str = "", on_answer: OnAnswer | None = None,
                     timeout: float | None = None) -> dict:
        """ask() for a worker thread (API-key models run their tools in one)."""
        q = self.open(kind, project, title, command, source=source, thread_id=thread_id, session=session, path=path,
                      on_answer=on_answer)
        return self.wait_blocking(q["id"], timeout)

    # ---- answering -----------------------------------------------------------------------------------------------

    def answer(self, qid: str, decision: str, why: str = "") -> dict:
        """The person's answer: once | always | deny (with a reason). What "always" keeps is the asker's (on_answer)."""
        if qid not in self.questions:
            raise ApprovalError(404, "That question was answered already, or its command ended.")
        if decision not in DECISIONS:
            raise ApprovalError(400, "Answer once, always or deny.")
        deny = decision == "deny"
        ans = {"decision": "deny" if deny else "allow", "why": (why or DENY_WHY) if deny else ""}
        if not self._settle(qid, decision, ans, "denied" if deny else "approved", "person"):
            raise ApprovalError(404, "That question was answered already, or its command ended.")
        return {"id": qid, "decision": decision}

    def close(self, qid: str | None = None, *, session: str | None = None, why: str) -> int:
        """Ends waiting questions nobody answered (one, or all of a session's): the asker gets a deny with `why`."""
        with self._lock:
            ids = [k for k, q in self.questions.items() if k == qid or (session is not None and q["session"] == session)]
        return sum(1 for k in ids if self._settle(k, "", {"decision": "deny", "why": why}, "closed", "keel"))

    # ---- reading -------------------------------------------------------------------------------------------------

    def pending(self, project: str | None = None) -> list[dict]:
        with self._lock:
            return [dict(q) for q in self.questions.values() if not project or q["project"] == project]

    def asked(self, qid: str) -> dict:
        """{waiting: true} until the person answers, then {decision: allow | deny, why} once."""
        with self._lock:
            if qid in self.questions:
                return {"id": qid, "waiting": True}
            if qid in self.decided:
                return {"id": qid, **self.decided.pop(qid)}
        raise ApprovalError(404, "keel knows no such question.")

    # ---- inside ------------------------------------------------------------------------------------------------------

    def _gone(self, qid: str) -> dict:
        with self._lock:
            got = self.decided.get(qid)
        return dict(got) if got else {"decision": "deny", "why": "keel lost this question, so the command did not run."}

    def _expire(self, qid: str, fut: concurrent.futures.Future) -> dict:
        ans = {"decision": "deny", "why": TIMEOUT_WHY}
        if self._settle(qid, "", ans, "expired", "keel"):
            return ans
        # answered at the very moment the time was up: that answer counts
        if fut.done() and not fut.cancelled():
            return fut.result()
        return self._gone(qid)

    def _settle(self, qid: str, said: str, ans: dict, status: str, by: str) -> bool:
        """Ends one question once: tells its asker (on_answer, then the waiting call) and emits approval.answered."""
        with self._lock:
            q = self.questions.pop(qid, None)
            fut = self._futures.pop(qid, None)
            hook = self._on_answer.pop(qid, None)
            if q:
                self.decided[qid] = dict(ans)
                while len(self.decided) > KEEP_DECIDED:
                    self.decided.pop(next(iter(self.decided)))
        if not q:
            return False
        if hook:
            try:
                hook(q, said, ans)
            except Exception:  # an asker's bookkeeping must not keep the answer from the one who waits
                log.exception("on_answer of %s failed", qid)
        if fut and not fut.done():
            fut.set_result(dict(ans))
        self._emit("approval.answered", {**q, "decision": said or None, "why": ans.get("why", ""), "status": status, "by": by})
        return True

    def _emit(self, type_: str, data: dict):
        self.bus.emit(type_, data.get("session") or data["id"], data.get("project") or "", step="approval", data=data)


_brokers: "weakref.WeakKeyDictionary[object, Approvals]" = weakref.WeakKeyDictionary()
_brokers_lock = threading.Lock()


def of(bus) -> Approvals:
    """The broker of this event bus (made on first use)."""
    with _brokers_lock:
        broker = _brokers.get(bus)
        if broker is None:
            broker = _brokers[bus] = Approvals(bus)
        return broker
