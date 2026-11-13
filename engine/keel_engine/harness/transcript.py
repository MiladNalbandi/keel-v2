"""keel's own agent sessions: one conversation per session id, kept on disk so a step that runs again (a restart, a
retry, a send-back, KeelBot's next message) continues where the agent was.

    $KEEL_DATA/harness/sessions/<id>.jsonl      one JSON object per line, appended after every turn:
        {"t": "meta", "id", "provider", "model", "root", "agent", "created"}
        {"t": "msg", "role": "user" | "assistant", "content": [blocks], "api"?, "model"?, "raw"?, "at"}
        {"t": "summary", "text", "at"}          a compaction keel made itself (OpenAI-compatible models): later loads
                                                start from it
        {"t": "todos", "items": [...]}          the agent's task list (harness/tools.py todo_write)

Append-only, so a crash never loses an earlier turn, and an assistant turn always goes back to its model exactly as it
came (the newest Claude models refuse an edited history). A line that cannot be read (the last one, after a crash in
the middle of a write) is skipped. Sessions older than KEEP_DAYS are removed.

A message's blocks are the neutral ones of harness/providers/base.py. A user message that holds tool results always
answers every tool call of the assistant message before it: a load that finds a call without its result (the process
stopped while a tool ran) adds the missing result as an error, so the conversation stays valid for every API.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

KEEP_DAYS = 14
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def folder() -> Path:
    p = Path(config.data_dir()) / "harness" / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def new_id() -> str:
    return str(uuid.uuid4())


def _path(sid: str) -> Path:
    if not _ID.match(sid or ""):
        raise ValueError(f"not a session id: {sid!r}")
    return folder() / f"{sid}.jsonl"


def sweep(now: float | None = None) -> int:
    """Remove the sessions nobody wrote to for KEEP_DAYS. How many went."""
    cutoff = (now or time.time()) - KEEP_DAYS * 86400
    n = 0
    for f in folder().glob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except OSError:
            continue
    return n


@dataclass
class Session:
    id: str
    meta: dict = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)    # since the last summary
    summary: str = ""
    todos: list[dict] = field(default_factory=list)
    turns: int = 0                                        # assistant turns in the whole session

    # ------------------------------------------------------------------ disk

    @classmethod
    def load(cls, sid: str) -> "Session | None":
        p = _path(sid)
        if not p.is_file():
            return None
        s = cls(id=sid)
        for line in p.read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = e.get("t")
            if t == "meta":
                s.meta = e
            elif t == "msg":
                s.messages.append({k: v for k, v in e.items() if k not in ("t", "at")})
                if e.get("role") == "assistant":
                    s.turns += 1
            elif t == "summary":
                s.summary, s.messages = str(e.get("text") or ""), []
            elif t == "todos":
                s.todos = list(e.get("items") or [])
        s.close_open_calls()
        return s

    @classmethod
    def open(cls, sid: str | None, **meta) -> "Session":
        """The session `sid` if it exists, else a new one with that id (or a new id)."""
        sid = sid or new_id()
        found = cls.load(sid)
        if found:
            return found
        s = cls(id=sid, meta={"t": "meta", "id": sid, "created": time.time(), **meta})
        s._write(s.meta)
        return s

    def _write(self, entry: dict) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with open(_path(self.id), "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    # ------------------------------------------------------------------ the conversation

    def add(self, role: str, content: list[dict], **extra) -> dict:
        msg = {"role": role, "content": content, **{k: v for k, v in extra.items() if v is not None}}
        self.messages.append(msg)
        if role == "assistant":
            self.turns += 1
        self._write({"t": "msg", "at": time.time(), **msg})
        return msg

    def compact(self, summary: str, keep: list[dict]) -> None:
        """keel's own compaction: later loads start from `summary`, then the messages in `keep` (the recent ones)."""
        self.summary = summary
        self._write({"t": "summary", "at": time.time(), "text": summary})
        self.messages = []
        for m in keep:
            self.add(m["role"], m["content"], **{k: v for k, v in m.items() if k not in ("role", "content")})

    def set_todos(self, items: list[dict]) -> None:
        self.todos = list(items)
        self._write({"t": "todos", "items": self.todos})

    def close_open_calls(self) -> None:
        """A tool call with no result (the process stopped while it ran): answer it as an error, in memory and on disk."""
        if not self.messages or self.messages[-1]["role"] != "assistant":
            return
        calls = [b for b in self.messages[-1].get("content") or [] if b.get("type") == "tool_call"]
        if calls:
            self.add("user", [{"type": "tool_result", "id": c["id"], "is_error": True,
                               "content": "keel stopped before this tool finished; its result is unknown. Check before you go on."}
                              for c in calls])

    def view(self) -> list[dict]:
        """What the model gets: the summary (when there is one) as the first user message, then the messages."""
        if not self.summary:
            return list(self.messages)
        head = {"role": "user", "content": [{"type": "text", "text": "Summary of the work so far in this session (earlier "
                                                                     f"messages were compacted):\n\n{self.summary}"}]}
        msgs = list(self.messages)
        if msgs and msgs[0]["role"] == "user":            # two user messages in a row: one message, both parts
            return [{"role": "user", "content": head["content"] + list(msgs[0]["content"])}] + msgs[1:]
        return [head] + msgs
