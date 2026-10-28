"""keel 0.18.64 keel's spec check and citation check on a project's file, outside a flow (the MCP tools keel_spec_check
and keel_citations_check, through the api's POST /api/projects/{pid}/specs/check and .../citations/check). Both read:
they change nothing.

    POST /specs/check {root, path}               runtime/spec_check.check_file: criteria, findings, the gate's text
    POST /citations/check {root, path?, text?}   runtime/citations.check: every path:line that names a project file
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from . import citations, spec_check
from .service import EngineError

router = APIRouter()
MAX_BYTES = 2_000_000       # a spec or a text this long is not a spec


class SpecBody(BaseModel):
    root: str
    path: str


class CitationsBody(BaseModel):
    root: str
    path: str | None = None
    text: str | None = None


def _root(root: str) -> str:
    if not root or not os.path.isdir(root):
        raise EngineError(400, f"The project folder {root} does not exist.", "Send the folder as the engine sees it.")
    return os.path.realpath(root)


def _file(root: str, rel: str) -> str:
    """The path inside the project (no ../, no absolute path, no symlink out of it) of a file that exists."""
    rel = (rel or "").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise EngineError(400, f"{rel or 'The path'} is not a path inside the project.", "Give it relative to the project, e.g. docs/specs/orders.md.")
    p = Path(root) / rel
    if not p.resolve().is_relative_to(Path(root)) or not p.is_file():
        raise EngineError(404, f"No file {rel} in the project.")
    if p.stat().st_size > MAX_BYTES:
        raise EngineError(400, f"{rel} is larger than {MAX_BYTES // 1_000_000} MB.")
    return rel


@router.post("/specs/check")
async def post_spec_check(body: SpecBody):
    root = _root(body.root)
    return await asyncio.to_thread(spec_check.check_file, root, _file(root, body.path))


@router.post("/citations/check")
async def post_citations_check(body: CitationsBody):
    root = _root(body.root)
    if bool(body.path) == (body.text is not None):
        raise EngineError(400, "Send a path or a text (one of them).")
    if body.path:
        rel = _file(root, body.path)
        text = (Path(root) / rel).read_text(errors="replace")
    else:
        rel, text = None, body.text or ""
        if len(text) > MAX_BYTES:
            raise EngineError(400, f"The text is larger than {MAX_BYTES // 1_000_000} MB.")
    out = await asyncio.to_thread(citations.check, root, text)
    return {"path": rel, **out}
