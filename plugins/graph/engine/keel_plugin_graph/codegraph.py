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
import re
import shutil
import subprocess
import threading
from pathlib import Path

from keel_engine import config
from keel_engine.tools import git

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
# CodeGraph's own words for a database it cannot open or lock where it is.
DB_TROUBLE = re.compile(r"sqlite|disk i/o|database is locked|readonly database|locking protocol|SQLITE_", re.I)

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


def mcp_spec(root: str) -> dict | None:
    """The code graph's MCP server (`codegraph serve --mcp`, cwd = the project) when keel indexed this folder and the
    index is ready; None otherwise (agents then find their way with grep)."""
    from keel_engine.runtime import scan

    if not root or not binary() or not has_index(root):
        return None
    try:
        if not scan.ready(root):
            return None
    except Exception as exc:  # the engine DB is busy or gone: no graph this time, never a failed step
        log.info("code graph readiness unknown for %s: %s", root, exc)
        return None
    return server_spec(root)


# ------------------------------------------------------------------ the index, at a project scan (keel's runtime/scan.py)

def _move_index(project: str, root: str) -> str:
    """Keep the index under $KEEL_DATA/index/<project> and link <root>/.codegraph to it."""
    target = config.data_dir() / "index" / re.sub(r"[^A-Za-z0-9_.-]", "_", project)
    link = Path(root) / DIR
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link, ignore_errors=True)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    return str(target)


def index(project: str, root: str, rebuild: bool = False) -> dict:
    """{ok, files, symbols, index_dir, error}: codegraph init, sync or (rebuild) index. Blocking (subprocesses); the
    scan runs it in a worker thread. If CodeGraph cannot keep its SQLite database in the project (a file system without
    the locks it needs), the index moves to $KEEL_DATA/index/<project> and <project>/.codegraph becomes a link to it."""
    if not binary():
        return {"ok": False, "error": "CodeGraph is not installed in this image (agents use grep instead)."}
    git.exclude(root, [f"{DIR}/"], "keel v2 code graph index")
    index_dir = str((Path(root) / DIR).resolve())

    def attempt() -> subprocess.CompletedProcess:
        if not has_index(root):
            return run(root, "init", "-y", root)
        return run(root, "index", "-q", root) if rebuild else run(root, "sync", "-q", root)

    try:
        r = attempt()
        if r.returncode != 0 and DB_TROUBLE.search(r.stderr + r.stdout):
            log.warning("codegraph cannot keep its index in %s; moving it to the data folder", root)
            index_dir = _move_index(project, root)
            r = attempt()
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Indexing took longer than {INDEX_TIMEOUT}s and was stopped.", "index_dir": index_dir}
    except OSError as exc:
        return {"ok": False, "error": f"Could not run codegraph: {exc}", "index_dir": index_dir}
    if r.returncode != 0:
        why = [x for x in (r.stderr or r.stdout).splitlines() if x.strip()][-3:]
        return {"ok": False, "error": "codegraph failed: " + (" ".join(why) or f"exit {r.returncode}")[:500], "index_dir": index_dir}
    st = status(root)
    if st and st.get("initialized") is False:
        return {"ok": False, "error": "codegraph finished but wrote no index.", "index_dir": index_dir}
    return {"ok": True, "files": int(st.get("fileCount") or 0), "symbols": int(st.get("symbols") or 0), "index_dir": index_dir}
