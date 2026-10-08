"""The map's engine routes, for the Map page (runtime/mapper.py)."""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter
from pydantic import BaseModel

from ...extensions import PartError
from ...runtime import mapper

router = APIRouter()


class MapBody(BaseModel):
    root: str


def project_root(root: str) -> str:
    if not os.path.isdir(root):
        raise PartError(400, f"The project folder {root} does not exist.", "Send the folder as the engine sees it.")
    return os.path.realpath(root)


@router.post("/projects/{pid}/map")
async def post_map(pid: str, body: MapBody):
    return await asyncio.to_thread(mapper.build_and_store, pid, project_root(body.root))


@router.get("/projects/{pid}/map")
async def get_map(pid: str):
    m = await asyncio.to_thread(mapper.load, pid)
    return m or {"missing": "No map yet. Build it to draw one."}
