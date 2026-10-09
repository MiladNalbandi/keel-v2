"""The Database plugin's engine routes: the person's own database calls (Map › Query, KeelBot's button, Connections ›
Databases). The api sends the connection with its password, memory only. A PluginError or DbError becomes a 4xx answer
(the app maps the part's errors)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import core as db
from .core import PluginError

router = APIRouter()


class PluginDb(BaseModel):
    """The person's own database call: the api sends the connection with its password, memory only."""
    root: str = ""
    connection: dict[str, Any] = Field(default_factory=dict)
    sql: str = ""
    change: bool = False        # the person may change data (only on a local or test database)
    confirm: bool = False       # run a change for real (else keel counts its rows and rolls back)
    mask: bool = False          # a model reads the rows (keel2 mcp): columns named like a secret show as •••


@router.post("/plugins/db/{op}")
async def post_plugin_db(op: str, body: PluginDb):
    root = body.root if body.root and os.path.isdir(body.root) else ""
    if op == "suggest":
        return await asyncio.to_thread(db.suggest, root)
    conn = db.conn_of(body.connection)
    if op == "test":
        return await asyncio.to_thread(db.test, conn, root)
    if op == "schema":
        return await asyncio.to_thread(db.schema, conn, root)
    if op == "query":
        return await asyncio.to_thread(db.query, conn, body.sql, root=root, allow_change=body.change,
                                       confirm=body.confirm, mask=body.mask)
    if op == "classify":
        kind, why = db.classify(body.sql, conn.kind)
        return {"kind": kind, "why": why}
    raise PluginError(404, f"Unknown database call {op}.")
