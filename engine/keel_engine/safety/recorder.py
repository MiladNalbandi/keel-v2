"""Writes each project's safety history from the engine's own events (safety/history.py has the file format).

Every tool call of an agent is already an event: `agent.step` (a flow's agents) and `helper.step` (KeelBot), with
{kind: read | write | edit | tool, text, path, tool, output, ok}. A call keel's guard refused before it ran is a step
of kind `guard` (text: the reason, command: the shell command). A question to the person ends as
`approval.answered`. The recorder listens to the bus (EventBus.listen), turns those into history lines with their
safety tier, and one background thread appends them, so an emit never waits on the disk.

    rec = Recorder(data_dir); bus.listen(rec.hear)      app.py does this at start
    rec.flush()                                         tests: wait until everything heard is written
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from pathlib import Path

from . import history, judge

log = logging.getLogger(__name__)

STEPS = {"agent.step": "flow", "helper.step": "keelbot"}
KINDS = {"read", "write", "edit", "tool", "guard"}
# the ToolBox's names (API-key models, the fake model) as the hook's
_TOOL = {"read": "Read", "write": "Write", "edit": "Edit", "run_command": "Bash", "write_file": "Write",
         "read_file": "Read"}
KEEP_AGENTS = 2000          # call id -> agent name, for the lines of a flow's steps
_T3_RULE = re.compile(r"so keel never runs it \(([\w.-]+), T3\)")


def _risk_of(tool: str, d: dict, command: str):
    return judge(tool, {"command": command, "file_path": str(d.get("path") or "")})


def line_of(ev: dict, agent: str = "") -> dict | None:
    """The history line of one event, or None when the event is not a tool call or an answer."""
    t, d = ev.get("type"), ev.get("data") or {}
    base = {"at": ev.get("at"), "thread": ev.get("thread_id"), "step": ev.get("step") or "", "agent": agent}
    if t in STEPS:
        kind = str(d.get("kind") or "")
        if kind not in KINDS:
            return None
        raw_tool = str(d.get("tool") or kind)
        tool = _TOOL.get(raw_tool) or _TOOL.get(kind) or raw_tool
        if kind == "guard":
            # a call keel's guard refused before it ran: text is the reason, command the shell command (if any)
            command = str(d.get("command") or "")
            reason = str(d.get("text") or "").removeprefix("REFUSED:").strip()
            risk = _risk_of(tool, d, command)
            t3 = _T3_RULE.search(reason)
            return {**base, "source": STEPS[t], "kind": "tool" if tool not in ("Read", "Write", "Edit") else
                    {"Read": "read", "Write": "write", "Edit": "edit"}[tool], "tool": tool, "server": None,
                    "text": command or str(d.get("path") or ""), "path": d.get("path") or None, "action": "deny",
                    "ok": False, "tier": "T3" if t3 else (f"T{int(risk.tier)}" if risk else None),
                    "rule": t3.group(1) if t3 else "keel-rules", "reason": reason[:1000], "undo": None}
        command = str(d.get("text") or "") if tool == "Bash" else ""
        risk = _risk_of(tool, d, command)
        # a read step's text is the file's content: the history keeps the file's name
        text = str(d.get("path") or "") if kind == "read" else str(d.get("text") or "")
        return {**base, "source": STEPS[t], "kind": kind, "tool": tool, "server": d.get("server") or None,
                "text": text, "path": d.get("path") or None, "action": "allow",
                "ok": d.get("ok") is not False, "tier": f"T{int(risk.tier)}" if risk else None,
                "rule": risk.rule if risk else None, "reason": risk.reason if risk else None,
                "undo": (risk.undo or None) if risk else None}
    if t == "approval.answered":
        status = str(d.get("status") or "")
        return {**base, "source": str(d.get("source") or ""), "kind": "approval", "tool": str(d.get("kind") or ""),
                "text": str(d.get("command") or d.get("title") or ""), "path": d.get("path") or None,
                "action": "allow" if status == "approved" else "deny", "ok": True,
                "answer": d.get("decision") or None, "status": status, "by": d.get("by") or None,
                "reason": str(d.get("why") or "") or None, "question": d.get("id")}
    return None


class Recorder:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._q: queue.Queue = queue.Queue(maxsize=50_000)
        self._agents: dict[str, str] = {}
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.errors = 0

    def hear(self, ev: dict) -> None:
        """The bus listener: quick, never blocks, never raises."""
        try:
            t = ev.get("type")
            if t == "agent.started" and ev.get("call_id"):
                self._agents[ev["call_id"]] = str((ev.get("data") or {}).get("agent") or "")
                while len(self._agents) > KEEP_AGENTS:
                    self._agents.pop(next(iter(self._agents)))
                return
            if t not in STEPS and t != "approval.answered":
                return
            pid = ev.get("project_id") or (ev.get("data") or {}).get("project")
            if not pid:
                return
            agent = self._agents.get(ev.get("call_id") or "", "") if t == "agent.step" else \
                ("KeelBot" if t == "helper.step" else "")
            line = line_of(ev, agent)
            if line is None:
                return
            self._start()
            self._q.put_nowait((str(pid), line))
        except queue.Full:
            self.errors += 1
        except Exception:  # the record must never break a run
            self.errors += 1
            log.exception("safety history: could not take an event")

    def _start(self) -> None:
        if self._thread is None:
            with self._lock:
                if self._thread is None:
                    self._thread = threading.Thread(target=self._write, name="keel-safety-history", daemon=True)
                    self._thread.start()

    def _write(self) -> None:
        while True:
            pid, line = self._q.get()
            try:
                history.append(history.path_for(self.data_dir, pid), line)
            except Exception as exc:
                self.errors += 1
                log.warning("safety history: could not write a line for %s (%s)", pid, exc)
            finally:
                self._q.task_done()

    def flush(self) -> None:
        if self._thread is not None:
            self._q.join()
