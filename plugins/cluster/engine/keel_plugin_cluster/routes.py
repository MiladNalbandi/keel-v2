"""The Cluster plugin's engine routes: the person's own calls from the Cluster page, through the api (plugins/cluster/api). The api
sends the context's access with each command or change ("read", "ask", "free", or none when the context is off); a ClusterError
becomes {error, hint} with its status."""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import core, kube
from .kube import ClusterError

router = APIRouter()


class ClusterBody(BaseModel):
    context: str = ""
    ns: str = ""
    kind: str = ""
    name: str = ""
    container: str = ""
    previous: bool = False
    tail: int = 300
    command: str = ""
    action: str = ""
    replicas: int | None = None
    root: str = ""
    contexts: list[str] = Field(default_factory=list)
    access: str | None = None
    who: str = "person"


@router.post("/plugins/cluster/{op}")
async def post_cluster(op: str, body: ClusterBody):
    b = body
    if op == "contexts":
        return await asyncio.to_thread(kube.contexts)
    if op == "test":
        return await asyncio.to_thread(kube.test, b.context)
    if op == "snapshot":
        return await asyncio.to_thread(kube.snapshot, b.context, b.ns)
    if op == "logs":
        return await asyncio.to_thread(kube.logs, b.context, b.ns, b.name, b.container, b.previous, b.tail)
    if op == "describe":
        return await asyncio.to_thread(kube.describe, b.context, b.kind, b.ns, b.name)
    if op == "exec":
        return await asyncio.to_thread(core.person_exec, b.context, b.ns, b.name, b.command, b.container, b.access, b.who or "person")
    if op == "change":
        return await asyncio.to_thread(core.person_change, b.context, b.action, b.kind, b.ns, b.name, b.replicas, b.access,
                                       b.who or "person")
    if op == "runs":
        root = os.path.realpath(b.root) if b.root and os.path.isdir(b.root) else ""
        ctxs = [kube.check_context(c) for c in b.contexts]
        return await asyncio.to_thread(core.runs, root, ctxs)
    if op == "history":
        return core.history()
    raise ClusterError(404, f"Unknown cluster call {op}.")
