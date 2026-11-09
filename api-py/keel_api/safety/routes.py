"""Safety & History routes (new in the Python api; the Kotlin api never had them).

    GET /api/projects/{id}/history          the newest lines first, with filters, and counts over the whole history
        ?thread=  ?action=allow|deny  ?tier=T0..T3  ?kind=read|write|edit|tool|approval  ?q=<text>
        ?before=<seq> (the next page)  ?limit= (1..500, 200)
    GET /api/projects/{id}/history/verify   {ok, text}: is every line as the engine wrote it?
"""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, Query, Request

from ..common.errors import NotFound
from ..mcp.levels import guard
from . import history

router = APIRouter(dependencies=[Depends(guard)])

FIELDS = ("text", "path", "reason", "tool", "agent", "rule", "step")


def _file(request: Request, pid: str):
    ctx = request.app.state.ctx
    if ctx.db.one("select id from projects where id = ?", (pid,)) is None:
        raise NotFound(f'No project called "{pid}"', "GET /api/projects lists the projects keel knows.")
    return history.path_for(ctx.settings.data_dir, pid)


def _matches(line: dict, thread, action, tier, kind, q) -> bool:
    if thread and line.get("thread") != thread:
        return False
    if action and line.get("action") != action:
        return False
    if tier and line.get("tier") != tier:
        return False
    if kind and line.get("kind") != kind:
        return False
    if q:
        words = q.lower()
        if not any(words in str(line.get(f) or "").lower() for f in FIELDS):
            return False
    return True


@router.get("/api/projects/{pid}/history")
def project_history(request: Request, pid: str, thread: str | None = None, action: str | None = None,
                    tier: str | None = None, kind: str | None = None, q: str | None = Query(None, max_length=200),
                    before: int | None = None, limit: int = Query(200, ge=1, le=500)) -> dict:
    lines = history.read(_file(request, pid))
    counts = {
        "total": len(lines),
        "action": dict(Counter(x.get("action") for x in lines if x.get("action"))),
        "tier": dict(Counter(x.get("tier") for x in lines if x.get("tier"))),
        "rule": dict(Counter(x.get("rule") for x in lines if x.get("action") == "deny" and x.get("rule"))),
    }
    found = [x for x in reversed(lines)
             if (before is None or int(x.get("seq") or 0) < before) and _matches(x, thread, action, tier, kind, q)]
    page = found[:limit]
    return {"items": page, "more": len(found) > limit, "next": page[-1].get("seq") if len(found) > limit else None,
            "counts": counts}


@router.get("/api/projects/{pid}/history/verify")
def verify_history(request: Request, pid: str) -> dict:
    ok, text = history.verify(_file(request, pid))
    return {"ok": ok, "text": text}
