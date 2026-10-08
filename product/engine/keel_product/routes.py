"""keel Product's engine routes (mounted by keel_engine/addons.py): the api asks for a new presentation after the
documents changed outside a flow (a new decision, a plan version)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import deck as deck_mod
from . import docs

router = APIRouter(prefix="/product", tags=["product"])


class DeckIn(BaseModel):
    root: str
    initiative: dict
    docs: dict = Field(default_factory=dict)
    versions: dict = Field(default_factory=dict)
    recommend: str = ""


@router.post("/deck")
def rebuild_deck(body: DeckIn) -> dict:
    root = Path(body.root)
    if not root.is_dir() or not (root / ".git").exists():
        raise HTTPException(400, "root is not the product repo")
    d = body.docs
    try:
        html = deck_mod.render(body.initiative, brief=d.get("brief") or "", impact_repos=d.get("impact_repos") or [],
                               impact=d.get("impact") or "", memo=d.get("decision") or "", recommend=body.recommend,
                               plan=d.get("plan"), versions=body.versions)
        return docs.write(str(root), str(body.initiative.get("id") or ""), "deck", html, ext="html")
    except docs.DocError as e:
        raise HTTPException(400, str(e)) from e
