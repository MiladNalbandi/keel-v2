"""Built-in keel workflows: content/workflows/<name>.yaml (KEEL_CONTENT in the image)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..model import Workflow, load_yaml

from ... import addons
from ...config import content_dir

ORDER = ["feature", "change", "fix", "diagnose", "review", "init", "knowledge-refresh", "cover", "ship", "hunt", "hunt-next", "lint"]


def folder() -> Path:
    return content_dir() / "workflows"


@lru_cache(maxsize=None)
def _load(name: str) -> Workflow | None:
    f = folder() / f"{name}.yaml"
    if f.is_file():
        return load_yaml(f.read_text())
    for addon, d in addons.folders("workflows"):
        f = d / f"{name}.yaml"
        if f.is_file():
            wf = load_yaml(f.read_text())
            wf.addon = addon
            return wf
    return None


def get_template(name: str) -> Workflow | None:
    wf = _load(name)
    return wf.model_copy(deep=True) if wf else None


def templates() -> list[Workflow]:
    extra = sorted(f.stem for f in folder().glob("*.yaml") if f.stem not in ORDER) if folder().is_dir() else []
    own = set(ORDER + extra)
    more = [f.stem for _a, d in addons.folders("workflows") for f in sorted(d.glob("*.yaml")) if f.stem not in own]
    return [wf for wf in (get_template(n) for n in ORDER + extra + more) if wf]


def clear_cache() -> None:
    """Forget loaded templates (tests that load add-ons)."""
    _load.cache_clear()
