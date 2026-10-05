"""Built-in keel workflows: content/workflows/<name>.yaml (KEEL_CONTENT in the image)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..model import Workflow, load_yaml

from ...config import content_dir

ORDER = ["feature", "change", "fix", "diagnose", "review", "init", "knowledge-refresh", "cover", "ship", "hunt", "hunt-next"]


def folder() -> Path:
    return content_dir() / "workflows"


@lru_cache(maxsize=None)
def _load(name: str) -> Workflow | None:
    f = folder() / f"{name}.yaml"
    return load_yaml(f.read_text()) if f.is_file() else None


def get_template(name: str) -> Workflow | None:
    wf = _load(name)
    return wf.model_copy(deep=True) if wf else None


def templates() -> list[Workflow]:
    extra = sorted(f.stem for f in folder().glob("*.yaml") if f.stem not in ORDER) if folder().is_dir() else []
    return [wf for wf in (get_template(n) for n in ORDER + extra) if wf]
