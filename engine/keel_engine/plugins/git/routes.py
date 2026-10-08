"""The Git plugin's engine routes: the person's own git calls (Code › Git, KeelBot's button). A PluginError or GitError
becomes a 4xx answer (the app maps the part's errors)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import PluginError, github_token
from . import core as g

router = APIRouter()


class PluginGit(BaseModel):
    """The person's own git call (Code › Git, KeelBot's button)."""
    root: str
    keys: dict[str, str] = Field(default_factory=dict)        # github: the token
    settings: dict[str, Any] = Field(default_factory=dict)    # commit_author, commit_coauthor
    branch: str = ""
    create: bool = False
    message: str = ""
    title: str = ""
    body: str = ""
    draft: bool = False
    path: str = ""
    sha: str = ""


@router.post("/plugins/git/{op}")
async def post_plugin_git(op: str, body: PluginGit):
    if not os.path.isdir(body.root):
        raise PluginError(404, "The project folder is gone.")
    token = github_token(body.keys)
    calls = {
        "status": lambda: g.status(body.root),
        "branches": lambda: g.branches(body.root),
        "log": lambda: g.log(body.root, 30),
        "switch": lambda: g.switch(body.root, body.branch, body.create),
        "commit": lambda: g.commit(body.root, body.message, body.settings),
        "sync": lambda: g.sync(body.root, token),
        "push": lambda: g.push(body.root, token),
        "pr": lambda: g.pr(body.root, token, body.title, body.body, body.draft),
        "pr_status": lambda: {"pr": g.pr_status(body.root, token)},
        "cleanup": lambda: g.cleanup(body.root),
    }
    if op not in calls:
        raise PluginError(404, f"Unknown git call {op}.")
    return await asyncio.to_thread(calls[op])
