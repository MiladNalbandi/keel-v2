"""The engine's approvals routes (keel_engine/approvals.py). Only the api calls them, with keel's internal token.

    GET  /approvals?project=    the questions that wait for a person
    GET  /approvals/{id}        {waiting: true}, or the answer once (an asker that polls: keel2 mcp)
    POST /approvals/{id}        the person's answer {decision: once | always | deny, why}

KeelBot's old routes (`/helper/permissions*`) still work: they reach the same broker.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from . import approvals
from .runtime.service import EngineError

router = APIRouter()


class Answer(BaseModel):
    decision: Literal["once", "always", "deny"]
    why: str = ""


def _broker(request: Request) -> approvals.Approvals:
    return approvals.of(request.app.state.bus)


@router.get("/approvals")
async def get_approvals(request: Request, project: str | None = None):
    return _broker(request).pending(project)


@router.get("/approvals/{qid}")
async def get_approval(qid: str, request: Request):
    try:
        return _broker(request).asked(qid)
    except approvals.ApprovalError as exc:
        raise EngineError(exc.status, str(exc), exc.hint or None) from exc


@router.post("/approvals/{qid}")
async def post_approval(qid: str, body: Answer, request: Request):
    try:
        return _broker(request).answer(qid, body.decision, body.why[:2000])
    except approvals.ApprovalError as exc:
        raise EngineError(exc.status, str(exc), exc.hint or None) from exc
