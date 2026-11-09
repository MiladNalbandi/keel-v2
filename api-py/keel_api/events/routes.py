"""GET /api/events (the live stream, events/EventController.kt) and POST /internal/hub (the Kotlin api's events)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from ..common.errors import BadRequest, Forbidden
from ..mcp.levels import guard
from .hub import EventHub

router = APIRouter(dependencies=[Depends(guard)])


def _hub(request: Request) -> EventHub:
    return request.app.state.ctx.hub


@router.get("/api/events")
async def stream(request: Request, project: str | None = None, notify: str | None = None) -> StreamingResponse:
    hub = _hub(request)
    sub = hub.subscribe(project, notify)
    # Starlette would add "; charset=utf-8" to a text/* media type; Spring sent the type alone
    return StreamingResponse(hub.stream(sub), headers={"content-type": "text/event-stream"})


@router.post("/internal/hub")
async def from_kotlin(request: Request) -> dict:
    """The Kotlin api's events, in the order it published them: [{project, type, data}], data as JSON text."""
    if getattr(request.state, "who", None) != "internal":
        raise Forbidden("Only keel's Kotlin api can send events to the hub", None)
    try:
        events = json.loads(await request.body())
    except ValueError:
        raise BadRequest("The events are not JSON", None) from None
    if not isinstance(events, list):
        raise BadRequest("The events must be a list", None)
    hub = _hub(request)
    for e in events:
        if not (isinstance(e, dict) and isinstance(e.get("type"), str) and isinstance(e.get("data"), str)
                and (e.get("project") is None or isinstance(e.get("project"), str))):
            raise BadRequest("Each event needs a type and its data as JSON text", None)
    for e in events:
        hub.publish_raw(e.get("project"), e["type"], e["data"])
    return {"ok": True, "subscribers": hub.count()}
