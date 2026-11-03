"""Built-in keel workflows: content/workflows/<name>.yaml (KEEL_CONTENT in the image)."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from ..model import Workflow, WorkflowError, load_yaml

from ... import addons
from ...config import content_dir

log = logging.getLogger("keel.templates")
ORDER = ["feature", "change", "quick", "address", "small", "implement", "spike", "clarify", "fix", "diagnose", "review", "review-pr", "init", "knowledge-refresh", "cover", "ship", "hunt", "hunt-next", "lint",
         "plugin", "harness", "design-review"]


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
            # docs/plugins/02-plugin-package.md: a broken plugin never stops keel. One bad file took /templates down
            # with it, so every workflow page failed. It is left out, and the log says why.
            try:
                wf = load_yaml(f.read_text())
            except WorkflowError as exc:
                log.warning("the workflow %s of %s does not load, so it is left out: %s", name, addon, exc)
                return None
            wf.addon = addon
            return wf
    return None


def get_template(name: str) -> Workflow | None:
    wf = _load(name)
    return wf.model_copy(deep=True) if wf else None


def templates() -> list[Workflow]:
    """keel's own workflows, then the add-ons' and the plugins' (addons.folders): each name once, keel's first (a plugin
    cannot replace one of keel's workflows)."""
    extra = sorted(f.stem for f in folder().glob("*.yaml") if f.stem not in ORDER) if folder().is_dir() else []
    own = set(ORDER + extra)
    more = [f.stem for _a, d in addons.folders("workflows") for f in sorted(d.glob("*.yaml")) if f.stem not in own]
    more = list(dict.fromkeys(more))
    out: list[Workflow] = []
    for wf in (get_template(n) for n in ORDER + extra + more):
        if wf and not any(w.id == wf.id for w in out):   # an id inside the file cannot take one either
            out.append(wf)
    return out


def clear_cache() -> None:
    """Forget loaded templates (tests that load add-ons)."""
    _load.cache_clear()
