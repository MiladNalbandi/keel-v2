"""The environment of a project's own commands and agents' shells, and private files (#101: what was left of cli.py when
the coding-agent CLIs went)."""

from __future__ import annotations

import os
from pathlib import Path
import sys

KEEL_PYTHON_VARS = ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "KEEL_PLUGIN_PATHS", "KEEL_PLUGIN_ADDONS")


def project_env(env: dict) -> dict:
    """The environment a project's own commands and an agent's shell see: without keel's internal Python. keel's venv
    was first on PATH (so `python -m pytest` ran keel's Python, which has no pytest) and UV_PROJECT_ENVIRONMENT pointed
    at it (a project's `uv sync` would have installed into keel). keel's own children use sys.executable directly.
    PYTHONPATH and the plugin variables are keel's too: a project's Python must not import keel's plugins."""
    out = {k: v for k, v in env.items() if k not in KEEL_PYTHON_VARS and not k.startswith("UV_")}
    own = {os.path.join(sys.prefix, "bin"), os.path.join(os.environ.get("VIRTUAL_ENV") or sys.prefix, "bin")}
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix) and out.get("PATH"):
        out["PATH"] = os.pathsep.join(p for p in out["PATH"].split(os.pathsep) if p and p.rstrip("/") not in own)
    return out


def write_private(path: Path | str, text: str) -> None:
    """0.18.64 a file only its owner reads (an MCP server's secrets): created 0600, never world-readable for a moment
    (write then chmod left a window). An older file keeps its inode: it is made 0600 before it is written."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(fd, 0o600)
        f.write(text)
