"""Mirror a v2 thread into keel v1's files, so keel v1's CLI and dashboard can read v2 projects.

    <root>/.keel/state.json        keel v1 state schema (flow, phase, acs, gates, ...)
    <root>/.keel/logs/events.jsonl {at, kind: tool|agent|phase|gate|guard, ...}

Both writes never raise: the flow matters more than its mirror.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ..rules import EMPTY_STATE

log = logging.getLogger(__name__)

CAP_BYTES = 2 * 1024 * 1024
KINDS = {"tool", "agent", "phase", "gate", "guard"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def state_json(values: dict, flow: str, thread_id: str, step_name: str | None = None) -> dict:
    s = copy.deepcopy(EMPTY_STATE)
    acs = values.get("acs") or []
    s.update({
        "flow": flow,
        "phase": values.get("phase") or "none",
        "spec": values.get("spec"),
        "branch": values.get("branch"),
        "current": values.get("ac"),
        "acs": {a["id"]: {"layer": a.get("layer", "API"), "status": a.get("status", "todo"), "title": a.get("title", "")} for a in acs},
    })
    gates = values.get("gates") or {}
    s["gates"] = {"mode": gates.get("mode", "every-ac"), "bug_gates": True, "skipped": gates.get("skipped", {}), "log": list(gates.get("log", []))}
    stall = values.get("stall") or {}
    s["stall"] = {"fingerprint": stall.get("fingerprint"), "count": stall.get("count", 0), "step": stall.get("step", 0)}
    s["last_failure"] = values.get("last_failure")
    cur = next((a for a in acs if a["id"] == values.get("ac")), None)
    if cur:
        s["lane"] = "web" if str(cur.get("layer", "API")).upper() == "WEB" else "api"
    s["unlocks"] = [{"path": u.get("path"), "phase": u.get("phase"), **{k: u[k] for k in ("by", "reason", "at") if u.get(k)}}
                    for u in values.get("unlocks") or []]
    s["deps"] = list(values.get("deps") or [])
    s["blockers"] = list(values.get("blockers") or [])
    if values.get("ladder"):
        s["setup"] = {"rungs": list(values["ladder"])}
    if values.get("status") in ("done", "stopped") and values.get("phase") in (None, "none"):
        s["flow"] = None
    s["engine"] = {"thread_id": thread_id, "step": values.get("current"), "step_name": step_name, "status": values.get("status"), "updated_at": now()}
    return s


def read_state(root: str | None) -> dict:
    try:
        data = json.loads((Path(root) / ".keel" / "state.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def write_state(root: str | None, data: dict) -> bool:
    if not root or not Path(root).is_dir():
        return False
    try:
        d = Path(root) / ".keel"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".state.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(d / "state.json")
        return True
    except OSError as exc:
        log.warning("could not write .keel/state.json: %s", exc)
        return False


def append_log(root: str | None, entry: dict) -> bool:
    if not root or entry.get("kind") not in KINDS or not Path(root).is_dir():
        return False
    try:
        d = Path(root) / ".keel" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "events.jsonl"
        if f.exists() and f.stat().st_size >= CAP_BYTES:
            f.replace(d / "events.1.jsonl")
        with f.open("a") as fh:
            fh.write(json.dumps({"at": now(), **entry}) + "\n")
        return True
    except OSError:
        return False


def short(value, limit: int = 80) -> str:
    s = " ".join(str(value or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def log_entry(event: dict) -> dict | None:
    """The v1 events.jsonl line for an EngineEvent, or None when v1 has no kind for it."""
    t, d = event["type"], event.get("data") or {}
    if t == "agent.started":
        return {"kind": "agent", "agent": f"keel:{d.get('agent')}", "verdict": "started", "phase": d.get("phase")}
    if t == "agent.finished":
        return {"kind": "agent", "agent": f"keel:{d.get('agent', '')}".rstrip(":"), "verdict": d.get("status"), "ok": d.get("status") == "done"}
    if t == "agent.step" and d.get("kind") == "tool":
        return {"kind": "tool", "tool": d.get("tool") or "tool", "arg": short(d.get("text")), "ok": d.get("ok", True), "ms": d.get("ms")}
    if t == "gate.decided":
        return {"kind": "gate", "gate": d.get("gate") or event.get("step"), "verdict": d.get("decision"), "detail": short(d.get("why"), 120) or None}
    if t == "guard.refused":
        return {"kind": "guard", "tool": d.get("tool", "diff-guard"), "arg": short(d.get("path")), "reason": short(d.get("reason"), 160)}
    if t == "step.started" and d.get("phase_changed"):
        return {"kind": "phase", "from": d.get("from"), "to": d.get("phase"), "flow": d.get("flow")}
    return None
