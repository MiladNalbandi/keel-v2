"""The CI/CD plugin's engine routes (Run › Jobs › Pipelines, the api's watcher). A PluginError or CiError becomes a 4xx
answer (the app maps the part's errors)."""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import core as ci
from .core import PluginError, github_token

router = APIRouter()


class PluginCi(BaseModel):
    """The CI/CD plugin's calls (Run › Jobs › Pipelines, the api's watcher)."""
    root: str
    keys: dict[str, str] = Field(default_factory=dict)        # github: the token
    branch: str = ""
    limit: int = Field(default=20, ge=1, le=50)
    run: int = 0


@router.post("/plugins/ci/{op}")
async def post_plugin_ci(op: str, body: PluginCi):
    if not os.path.isdir(body.root):
        raise PluginError(404, "The project folder is gone.")
    token = github_token(body.keys)
    calls = {
        "runs": lambda: ci.runs(body.root, token, body.branch or None, body.limit),
        "run": lambda: ci.run(body.root, token, body.run),
        "rerun": lambda: ci.rerun(body.root, token, body.run),
    }
    if op not in calls:
        raise PluginError(404, f"Unknown CI call {op}.")
    return await asyncio.to_thread(calls[op])
