"""The live event stream, GET /api/events (events/EventHub.kt).

A client follows one project, or every project. It gets a `:connected` comment first, then `event:<type>` /
`data:<json>` frames, and a `:ping` comment after 20 seconds without one, so a dead client is found. The frames are
the bytes Spring's SseEmitter wrote, so the web, the TUI and the IDEs read them as before.

Events come from this api's own routes (publish) and from the Kotlin api, which posts every event it publishes to
POST /internal/hub (publish_raw, the JSON already written). Each client has its own queue; a client that falls
too far behind is dropped and reconnects, so it never slows keel down.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

PING_S = 20.0
QUEUE = 2000  # frames waiting for one client; live text comes every 80 ms per agent

LIGHT = frozenset({"notification", "notification.done", "project.changed"})

# The one generic channel: an engine event the web does not listen to by name goes out as `event: engine`.
ENGINE = "engine"

# Engine events the web listens to by their own name (web api.ts ENGINE_EVENT_TYPES).
NAMED = frozenset({
    "thread.started", "step.started", "step.finished", "agent.started", "agent.step", "agent.finished",
    "code.started", "code.step", "code.finished", "gate.waiting", "gate.decided", "budget.warn", "budget.stop",
    "guard.refused", "thread.done", "thread.failed", "helper.started", "helper.step", "helper.finished",
    "helper.permission", "helper.permission.answered", "helper.commit", "helper.delta", "agent.delta", "helper.tool",
})

CONNECTED = b":connected\n\n"
PING = b":ping\n\n"


def channel_of(type_: str) -> str:
    """The SSE event name an engine event goes out under."""
    return type_ if type_ in NAMED else ENGINE


def frame(type_: str, data_json: str) -> bytes:
    # as SseEmitter: a line break in the data starts a new data: line
    return f"event:{type_}\ndata:{data_json.replace(chr(10), chr(10) + 'data:')}\n\n".encode()


@dataclass(eq=False)
class Sub:
    """[light]: `project=*`, every project but only the bell's events. [notify_all]: `project=X&notify=all`,
    everything for X plus the bell's events from every project, so a browser tab needs one stream."""

    project: str | None
    light: bool = False
    notify_all: bool = False
    queue: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(QUEUE))
    dropped: bool = False

    def wants(self, project: str | None, type_: str) -> bool:
        other_project = self.project is not None and project is not None and self.project != project
        if other_project and not (self.notify_all and type_ in LIGHT):
            return False
        return not (self.light and type_ not in LIGHT)


class EventHub:
    def __init__(self) -> None:
        self._subs: set[Sub] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """The loop the streams run on: publish may be called from any thread (a plain `def` route)."""
        self._loop = loop

    def count(self) -> int:
        return len(self._subs)

    def subscribe(self, project: str | None, notify: str | None = None) -> Sub:
        self._loop = asyncio.get_running_loop()
        every = (project or "").strip() == "*"
        sub = Sub(
            project=project if project and project.strip() and not every else None,
            light=every,
            notify_all=notify == "all" and not every,
        )
        with self._lock:
            self._subs.add(sub)
        return sub

    def remove(self, sub: Sub) -> None:
        with self._lock:
            self._subs.discard(sub)

    def publish(self, project: str | None, type_: str, data: Any) -> None:
        """Sends to the subscribers of [project] and to the subscribers of every project."""
        if not self._subs:  # no open tab or terminal: nothing to write
            return
        self.publish_raw(project, type_, json.dumps(data, separators=(",", ":"), ensure_ascii=False))

    def publish_raw(self, project: str | None, type_: str, data_json: str) -> None:
        with self._lock:
            subs = [s for s in self._subs if s.wants(project, type_)]
        if not subs:
            return
        f = frame(type_, data_json)
        loop = self._loop
        if loop is None:
            return
        try:
            here = asyncio.get_running_loop() is loop
        except RuntimeError:
            here = False
        if here:
            self._deliver(subs, f)
        else:
            loop.call_soon_threadsafe(self._deliver, subs, f)

    def _deliver(self, subs: list[Sub], f: bytes) -> None:
        for sub in subs:
            if sub.dropped:
                continue
            try:
                sub.queue.put_nowait(f)
            except asyncio.QueueFull:
                # too far behind: end its stream; the client reconnects and reads the state afresh
                sub.dropped = True
                self.remove(sub)

    async def stream(self, sub: Sub) -> AsyncIterator[bytes]:
        try:
            yield CONNECTED
            while not sub.dropped:
                try:
                    f = await asyncio.wait_for(sub.queue.get(), PING_S)
                except TimeoutError:
                    f = PING
                if sub.dropped:
                    return
                yield f
        finally:
            self.remove(sub)
