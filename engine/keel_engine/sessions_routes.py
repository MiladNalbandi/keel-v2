"""The engine's connected-session routes (runtime/session_service.py). Only the api calls them, with keel's internal
token: the web and keel's MCP server reach them as /api/sessions/* (api-py engine_parts.py; the MCP write level for every
POST).

    GET  /sessions?project=                  the project's sessions and the work orders that wait
    POST /sessions/open                      {project, client, folder}
    POST /sessions/{id}/next                 heartbeat + the next work order (or none)
    POST /sessions/flows/{tid}/plan          {session, plan, todos}
    POST /sessions/flows/{tid}/todo          {session, todos}
    POST /sessions/flows/{tid}/done          {session, summary, status, todos}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .runtime import session_service

router = APIRouter()


class Open(BaseModel):
    project: str
    client: str | None = None
    folder: str | None = None


class Report(BaseModel):
    session: str
    plan: str | None = None
    summary: str | None = None
    status: str | None = None
    todos: list[Any] | None = None


def _engine(request: Request):
    return request.app.state.engine


@router.get("/sessions")
async def get_sessions(request: Request, project: str):
    return await session_service.list_view(_engine(request), project)


@router.post("/sessions/open")
async def post_open(body: Open, request: Request):
    return session_service.open_session(_engine(request), body.project, body.client, body.folder)


@router.post("/sessions/{sid}/next")
async def post_next(sid: str, request: Request):
    return await session_service.next_order(_engine(request), sid)


@router.post("/sessions/flows/{tid}/plan")
async def post_plan(tid: str, body: Report, request: Request):
    return await session_service.report(_engine(request), tid, body.session, "plan", body.model_dump())


@router.post("/sessions/flows/{tid}/todo")
async def post_todo(tid: str, body: Report, request: Request):
    return await session_service.todo(_engine(request), tid, body.session, body.todos)


@router.post("/sessions/flows/{tid}/done")
async def post_done(tid: str, body: Report, request: Request):
    return await session_service.report(_engine(request), tid, body.session, "done", body.model_dump())
