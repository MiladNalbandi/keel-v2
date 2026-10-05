"""CodeGraph (@colbymchenry/codegraph, MIT): a local index of a project's symbols, calls and imports that agents query
through its MCP server instead of grep and whole-file reads.

    codegraph init -y <root>        first index (writes <root>/.codegraph/codegraph.db)
    codegraph index -q <root>       full rebuild
    codegraph sync -q <root>        incremental update since the last index (cheap)
    codegraph status --json <root>  {initialized, fileCount, nodeCount, nodesByKind, journalMode, ...}
    codegraph serve --mcp --path <root> --no-watch     the MCP server agents get

Everything here is best effort: no binary, a timeout or a broken index never fails a flow (agents fall back to grep).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

DIR = ".codegraph"
INDEX_TIMEOUT = int(os.environ.get("KEEL_CODEGRAPH_TIMEOUT", "900"))
SYNC_TIMEOUT = 120
# The MCP tools agents see. Measured on ludus (Haiku explorer, 2+2 runs): codegraph_explore returns 10-17k characters
# per call even with maxFiles 1-2 and agents still read the files afterwards, so it cost ~38% MORE tokens than no
# graph. The lookups are small (search: locations only; callers/callees/impact: lists), so those are the default.
# Lookup-only was measured too (2 runs: 196k, 158k vs 95k, 155k without): no saving, so every agent's code_graph is
# off by default in content/agents; turn it on per agent in the Agents drawer.
MCP_TOOLS = os.environ.get("KEEL_CODEGRAPH_TOOLS", "search,callers,callees,impact")
QUIET = {"CODEGRAPH_TELEMETRY": "0", "DO_NOT_TRACK": "1", "CODEGRAPH_NO_UPDATE_CHECK": "1"}

_syncing: set[str] = set()
_lock = threading.Lock()


def binary() -> str | None:
    return os.environ.get("KEEL_CODEGRAPH_BIN") or shutil.which("codegraph")


def env() -> dict:
    # The CLI commands run in the foreground and never leave a background daemon behind.
    return {**os.environ, **QUIET, "CODEGRAPH_NO_DAEMON": "1"}


def run(root: str, *args: str, timeout: int = INDEX_TIMEOUT) -> subprocess.CompletedProcess:
    exe = binary()
    if not exe:
        raise FileNotFoundError("codegraph is not installed")
    return subprocess.run([exe, *args], cwd=root, capture_output=True, text=True, timeout=timeout, env=env())


def has_index(root: str) -> bool:
    return (Path(root) / DIR / "codegraph.db").is_file()


def status(root: str) -> dict:
    """`codegraph status --json`, plus `symbols` (everything but files and imports). {} when it cannot tell."""
    try:
        r = run(root, "status", "--json", root, timeout=60)
        data = json.loads(r.stdout[r.stdout.find("{"):]) if "{" in r.stdout else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    kinds = data.get("nodesByKind") if isinstance(data.get("nodesByKind"), dict) else {}
    nodes = int(data.get("nodeCount") or 0)
    data["symbols"] = sum(int(v or 0) for k, v in kinds.items() if k not in ("file", "import")) if kinds else nodes
    return data


def sync(root: str) -> bool:
    if not binary() or not has_index(root):
        return False
    try:
        return run(root, "sync", "-q", root, timeout=SYNC_TIMEOUT).returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("codegraph sync in %s failed: %s", root, exc)
        return False


def sync_later(root: str) -> None:
    """Bring the index up to date in the background (flow start, after a keel commit). One sync per folder at a time."""
    if not binary() or not has_index(root):
        return
    with _lock:
        if root in _syncing:
            return
        _syncing.add(root)

    def go():
        try:
            sync(root)
        finally:
            with _lock:
                _syncing.discard(root)

    threading.Thread(target=go, name="codegraph-sync", daemon=True).start()


def server_spec(root: str) -> dict:
    """The MCP server entry. Not CODEGRAPH_NO_DAEMON: parallel agents on one project share one server process
    (with it, a second `serve --mcp` exits on the writer lock)."""
    return {"name": "codegraph", "command": binary() or "codegraph", "args": ["serve", "--mcp", "--path", root, "--no-watch"],
            "cwd": root, "env": {**QUIET, "CODEGRAPH_NO_DAEMON": "0", "CODEGRAPH_MCP_TOOLS": MCP_TOOLS}}
