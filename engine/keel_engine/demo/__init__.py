"""The bundled demo project: a tiny Python package (`scores`) with pytest tests and docs/specs/.

Copied to `$KEEL_DATA/demo` and turned into a git repo when no workspace is mounted, so a fresh
install has something to run a flow on.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .. import config

TEMPLATE = Path(__file__).parent / "repo"
RENAMES = {"gitignore": ".gitignore"}


def create_demo(dest: str | Path | None = None, *, force: bool = False) -> Path:
    """Copy the demo to dest (default $KEEL_DATA/demo) and git init it. Idempotent unless force."""
    dest = Path(dest) if dest else config.data_dir() / "demo"
    if dest.exists() and (dest / ".git").exists() and not force:
        return dest
    if dest.exists() and force:
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for src in TEMPLATE.rglob("*"):
        if src.is_dir() or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(TEMPLATE)
        rel = rel.with_name(RENAMES.get(rel.name, rel.name))
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest / rel)
    ident = ["-c", "user.name=keelbot", "-c", "user.email=keel.dev.bot@gmail.com"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(["git", *ident, "commit", "-q", "-m", "chore: demo project"], cwd=dest, check=True)
    return dest


def workspace_missing() -> bool:
    import os

    ws = os.environ.get("KEEL_WORKSPACE")
    return not ws or not Path(ws).is_dir() or not any(Path(ws).iterdir())
