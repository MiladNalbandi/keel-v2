"""keel v1's state file (.keel/state.json), left behind in a project keel v2 runs (0.18.64).

keel v1 kept its flow in .keel/state.json ({flow, phase, ac, spec, unlocks, ...}). While keel v1's hooks are still
installed (its Claude Code plugin, its git hooks), they refuse a commit as long as that file names a flow in a phase
that forbids commits, also when nobody runs that flow any more. keel v2 never writes the file and reads it only to
import old unlocks once (migrate.legacy_unlocks). It says plainly where a commit fails or a flow starts that a v1 flow
is left (`line`), and archives the file on request: keel_state close (MCP), POST /api/projects/{pid}/keel-state/close,
`keel2 state close`. Archived means moved to .keel/archive/state-<date>.json, never deleted.

    read     the file as keel v2 sees it: {path, flow, phase, id, title, ac, updated, active, thread} or None
    line     "a keel v1 flow (feature 012-prices, phase full-review) left .keel/state.json: ..." for an active one
    close    archive it: {archived, flow}
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

FILE = ".keel/state.json"
ARCHIVE = ".keel/archive"
# a v1 state in one of these phases names no flow that runs: its hooks block nothing
IDLE = {"", "none", "idle", "done", "closed", "shipped", "finished"}
CLOSE_HOW = "archive it with keel_state close or `keel2 state close`"


def _text(v) -> str:
    return str(v).strip() if isinstance(v, (str, int, float)) and str(v).strip() else ""


def read(root: str | Path) -> dict | None:
    """The v1 state file of the project, or None (no file). An unreadable one is still shown (it is still there), as
    {path, flow: "", error}."""
    p = Path(root) / FILE
    if not p.is_file():
        return None
    try:
        updated = datetime.datetime.fromtimestamp(p.stat().st_mtime, datetime.timezone.utc).isoformat(timespec="seconds")
    except OSError:
        updated = None
    try:
        data = json.loads(p.read_text(errors="replace"))
    except (OSError, ValueError) as exc:
        return {"path": FILE, "flow": "", "phase": "", "id": "", "title": "", "ac": "", "updated": updated, "active": False,
                "thread": "", "error": f"not readable as JSON ({type(exc).__name__})"}
    data = data if isinstance(data, dict) else {}
    flow = _text(data.get("flow"))
    phase = _text(data.get("phase"))
    ac = data.get("ac") if not isinstance(data.get("ac"), dict) else data["ac"].get("id")
    engine = data.get("engine") if isinstance(data.get("engine"), dict) else {}
    ident = next((_text(data.get(k)) for k in ("id", "flow_id", "slug", "spec", "branch") if _text(data.get(k))), "")
    title = next((_text(data.get(k)) for k in ("title", "name", "request") if _text(data.get(k))), "")
    return {"path": FILE, "flow": flow, "phase": phase, "id": ident, "title": title[:120], "ac": _text(ac),
            "updated": updated, "active": bool(flow) and phase.lower() not in IDLE, "thread": _text(engine.get("thread_id"))}


def name(info: dict) -> str:
    """The v1 flow in a few words: "feature 012-prices, phase full-review"."""
    what = " ".join(x for x in (info.get("flow") or "flow", info.get("id") or info.get("title")) if x)
    phase = info.get("phase") or "none"
    return f"{what}, phase {phase}" + (f", {info['ac']}" if info.get("ac") else "")


def line(info: dict | None) -> str:
    """What to say where a commit fails or a flow starts: "" when no v1 flow is left active."""
    if not info or not info.get("active"):
        return ""
    return f"a keel v1 flow ({name(info)}) left {FILE}: it blocks commits; {CLOSE_HOW}"


def leftover(root: str | Path) -> str:
    """line() of the project's v1 state; "" when there is none or it names no active flow. Never raises."""
    try:
        return line(read(root))
    except Exception:  # noqa: BLE001 - a note on a failure path must not fail itself
        return ""


def show(root: str | Path) -> dict:
    """The api's GET /keel-state: {v1: read() with its `line`, or None}."""
    info = read(root)
    return {"v1": {**info, "line": line(info), "name": name(info)} if info else None}


def close(root: str | Path, today: datetime.date | None = None) -> dict:
    """Archive the v1 state file: .keel/archive/state-<date>.json (-2, -3, … when that name is taken). Nothing is
    deleted. Raises FileNotFoundError when there is no file."""
    root = Path(root)
    p = root / FILE
    info = read(root)
    if info is None:
        raise FileNotFoundError(f"There is no keel v1 state file ({FILE}) in this project.")
    day = (today or datetime.date.today()).isoformat()
    folder = root / ARCHIVE
    folder.mkdir(parents=True, exist_ok=True)
    dest, n = folder / f"state-{day}.json", 2
    while dest.exists():
        dest, n = folder / f"state-{day}-{n}.json", n + 1
    p.rename(dest)
    return {"archived": dest.relative_to(root).as_posix(), "flow": name(info), "was_active": bool(info.get("active"))}
