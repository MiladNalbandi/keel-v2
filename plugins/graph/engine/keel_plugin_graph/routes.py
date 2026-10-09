"""The code graph's engine routes, for the Graph page (codegraph_view.py)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from . import codegraph_view

router = APIRouter()


class GraphSearch(BaseModel):
    q: str = ""


class GraphNode(BaseModel):
    id: str
    depth: int = 1                        # 1: who uses it and what it uses; 2: one more step on both sides


@router.get("/projects/{pid}/graph")
async def get_graph(pid: str):
    """The code graph for people: groups (packages or folders), units and the uses between them (codegraph_view.py)."""
    return await asyncio.to_thread(codegraph_view.overview, pid)


@router.post("/projects/{pid}/graph/search")
async def post_graph_search(pid: str, body: GraphSearch):
    return await asyncio.to_thread(codegraph_view.search, pid, body.q)


@router.post("/projects/{pid}/graph/node")
async def post_graph_node(pid: str, body: GraphNode):
    """One symbol: who uses it (left), what it uses (right), its members and how much depends on it."""
    return await asyncio.to_thread(codegraph_view.focus, pid, body.id, body.depth)
