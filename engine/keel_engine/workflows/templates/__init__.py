"""Built-in keel workflows, stored as YAML next to this file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..model import Workflow, load_yaml

HERE = Path(__file__).parent
ORDER = ["feature", "change", "fix", "init", "knowledge-refresh"]


@lru_cache(maxsize=None)
def _load(name: str) -> Workflow | None:
    f = HERE / f"{name}.yaml"
    return load_yaml(f.read_text()) if f.is_file() else None


def get_template(name: str) -> Workflow | None:
    wf = _load(name)
    return wf.model_copy(deep=True) if wf else None


def templates() -> list[Workflow]:
    return [get_template(n) for n in ORDER]
