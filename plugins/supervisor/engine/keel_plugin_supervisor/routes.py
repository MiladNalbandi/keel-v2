"""The Supervisor's engine routes.

    GET  /supervisor/decisions?thread_id=&project_id=&limit=   its audit log, newest first
    GET  /supervisor/switch?project_id=                         the keel-wide switch (and a project's), what it looks at now
    POST /supervisor/switch  {keel, projects: {pid: {on, plugin}}}
                                    keel's api sends every change of its switches (Settings, Control › Plugins); off
                                    stops at once (switch.py)
    GET  /threads/{tid}/supervisor                              the flow's Supervisor settings, its tokens, its last rows
    POST /threads/{tid}/supervisor  {on, mode, model, effort, cap_tokens, max_answers, policy}
                                    changes them during a run (stored with the thread's settings, like the run mode);
                                    `on: null` follows the project again; off stops its look at once; turned on while the
                                    flow waits, it looks at the waiting gate at once
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from keel_engine.runtime import side_usage
from keel_engine.runtime.service import EngineError

from . import ledger, policy
from .supervisor import MODES, SOURCE, settings_of

router = APIRouter()


class SwitchBody(BaseModel):
    keel: bool | None = None
    projects: dict[str, dict[str, bool | None]] | None = None


class SupervisorBody(BaseModel):
    on: bool | None = None
    mode: str | None = None
    model: dict | None = None
    effort: str | None = None
    cap_tokens: int | None = None
    max_answers: int | None = None
    policy: dict[str, str] | None = None


def _sup(request: Request):
    sup = getattr(request.app.state, "supervisor", None)
    if sup is None:
        raise HTTPException(503, "The Supervisor is not running.")
    return sup


async def _view(sup, tid: str) -> dict:
    s = await sup.settings(tid)
    return {"thread_id": tid, "settings": s, "used": side_usage.of_source(tid, SOURCE),
            "decisions": ledger.list_rows(thread_id=tid, limit=20)}


@router.get("/supervisor/decisions")
async def decisions(thread_id: str | None = None, project_id: str | None = None, limit: int = 100):
    return {"decisions": ledger.list_rows(thread_id=thread_id, project_id=project_id, limit=max(1, min(limit, 500)))}


@router.get("/supervisor/switch")
async def get_switch(request: Request, project_id: str | None = None):
    return _sup(request).switch_view(project_id)


@router.post("/supervisor/switch")
async def set_switch(body: SwitchBody, request: Request):
    return await _sup(request).set_switch(body.keel, body.projects)


@router.get("/threads/{tid}/supervisor")
async def get_settings(tid: str, request: Request):
    sup = _sup(request)
    try:
        return await _view(sup, tid)
    except EngineError as exc:
        raise HTTPException(exc.status, exc.error) from exc


@router.post("/threads/{tid}/supervisor")
async def set_settings(tid: str, body: SupervisorBody, request: Request):
    sup = _sup(request)
    eng = sup.engine
    if body.mode is not None and body.mode not in MODES:
        raise HTTPException(400, f'"{body.mode}" is not a Supervisor mode. Pick one of: {", ".join(MODES)}.')
    for kind, want in (body.policy or {}).items():
        if kind in policy.NEVER or want not in policy.RULES:
            raise HTTPException(400, f'"{kind}: {want}" cannot be set: only you answer {kind} gates.' if kind in policy.NEVER
                                else f'"{want}" is not a rule. Pick one of: {", ".join(policy.RULES)}.')
    try:
        row = await eng._row(tid)
    except EngineError as exc:
        raise HTTPException(exc.status, exc.error) from exc
    if body.on and not (await sup.settings(tid))["plugin_on"]:
        raise HTTPException(409, "The Supervisor plugin is off for this project: turn it on in Control › Plugins first.")
    patch = body.model_dump(exclude_unset=True)      # on: null = follow the project's switch again
    data = json.loads(row["body"])
    settings = dict(data.get("settings") or {})
    settings["supervisor"] = {**(settings.get("supervisor") or {}), **patch}
    data["settings"] = settings
    await eng.conn.execute("update keel_threads set body = ? where thread_id = ?", (json.dumps(data), tid))
    await eng.conn.commit()
    ctx = await eng._context(tid)
    ctx.settings = {**(ctx.settings or {}), "supervisor": settings["supervisor"]}
    ctx.emit("supervisor.settings", data={"settings": settings_of(settings["supervisor"])})
    if (await sup.settings(tid))["on"]:
        # 0.18.64 a new mode or policy is a new look at the waiting gate: shadow's look at it must not keep act from answering
        sup.seen = {k for k in sup.seen if k[0] != tid}
        sup._spawn(sup.look(tid, trigger="turned-on"))
    else:
        sup.stop_where(thread=tid)
    return await _view(sup, tid)
