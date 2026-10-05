"""EngineEvents: buffered POSTs to the api.

emit() never blocks and never raises. A background task sends batches to
`$KEEL_API_URL/internal/events` with `X-Keel-Token`; when the api is down it keeps a bounded buffer,
backs off, and logs once.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone

import httpx

from .. import config

log = logging.getLogger(__name__)

MAX_BUFFER = 10_000
BATCH = 200


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventBus:
    def __init__(self):
        self.recent: deque[dict] = deque(maxlen=5000)  # newest last; tests and debugging read this
        self._pending: deque[dict] = deque(maxlen=MAX_BUFFER)
        self._open_step: dict[str, tuple] = {}
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._down_logged = False

    def emit(self, type: str, thread_id: str, project_id: str, *, step: str | None = None,
             call_id: str | None = None, data: dict | None = None) -> dict | None:
        try:
            data = data or {}
            # A gate or budget pause re-runs its node on resume; one step.started per visit is enough.
            key = (step, data.get("ac"))
            if type == "step.started":
                if self._open_step.get(thread_id) == key:
                    return None
                self._open_step[thread_id] = key
            elif type == "step.finished":
                self._open_step.pop(thread_id, None)
            ev = {"type": type, "thread_id": thread_id, "project_id": project_id, "at": now(), "data": data}
            if step is not None:
                ev["step"] = step
            if call_id is not None:
                ev["call_id"] = call_id
            self.recent.append(ev)
            self._pending.append(ev)
            if self._wake:
                self._wake.set()
            return ev
        except Exception:  # an event must never break the flow
            log.exception("emit failed")
            return None

    def of(self, thread_id: str, type: str | None = None) -> list[dict]:
        return [e for e in self.recent if e["thread_id"] == thread_id and (type is None or e["type"] == type)]

    # ------------------------------------------------------------ sending

    def start(self):
        if self._task is None:
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self._send_once(timeout=1.0)

    async def _run(self):
        delay = 0.25
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                ok = await self._send_once(client=client)
                delay = 0.25 if ok else min(max(delay * 2, 1.0), 30.0)

    async def _send_once(self, client: httpx.AsyncClient | None = None, timeout: float = 5.0) -> bool:
        url = config.api_url()
        if not self._pending:
            return True
        if not url or url == "off":
            self._pending.clear()
            return True
        batch = [self._pending.popleft() for _ in range(min(BATCH, len(self._pending)))]
        headers = {"X-Keel-Token": config.internal_token()}
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=timeout) as c:
                    r = await c.post(f"{url}/internal/events", json=batch, headers=headers)
            else:
                r = await client.post(f"{url}/internal/events", json=batch, headers=headers)
            if r.status_code >= 500:
                raise httpx.HTTPError(f"api answered {r.status_code}")
            if r.status_code >= 400:
                log.warning("api refused %d events: %s %s", len(batch), r.status_code, r.text[:200])
            self._down_logged = False
            return True
        except Exception as exc:
            # Put them back in order; the deque drops the oldest when full.
            self._pending.extendleft(reversed(batch))
            if not self._down_logged:
                log.info("api not reachable at %s (%s); keeping events and retrying", url, exc)
                self._down_logged = True
            return False


bus = EventBus()
