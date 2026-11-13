"""keel's crash log (#101 part 3): one JSON line per crash of a flow, what it was and what the doctor did.

`$KEEL_DATA/doctor/crashes.jsonl`, append-only; at 5 MB it moves to crashes.1.jsonl (one old file is kept). A line:
  {at, thread, project, workflow, step, agent?, provider?, model?, kind, title, fix, error, where?, outcome, action?, attempt?}
outcome: repaired (the doctor's repair worked) | retrying (a repair is being tried) | asked (the flow waits for the
person) | retried (the person's Retry ran it again) | stopped (the person stopped it) | failed (the flow ended failed).
Secrets never get in: the error text goes through the secret scanner first, and is cut to 2,000 characters.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .. import config

MAX_BYTES = 5_000_000
ERROR_MAX = 2_000
_lock = threading.Lock()


def path() -> Path:
    return Path(config.data_dir()) / "doctor" / "crashes.jsonl"


def _clean(text: str) -> str:
    try:
        from ..rules.checks import PATTERNS

        for rx, _why in PATTERNS:
            text = rx.sub("•••", text)
    except Exception:  # noqa: BLE001 - a scanner problem never loses the line
        pass
    return text[:ERROR_MAX]


def record(**entry) -> dict:
    """Append one line; never raises (a crash log that fails must not fail the flow)."""
    line = {"at": round(time.time(), 3), **{k: v for k, v in entry.items() if v is not None}}
    if "error" in line:
        line["error"] = _clean(str(line["error"]))
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            if p.exists() and p.stat().st_size > MAX_BYTES:
                os.replace(p, p.with_name("crashes.1.jsonl"))
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return line


def read(since: float = 0, limit: int | None = None) -> list[dict]:
    """The lines since `since` (epoch seconds), oldest first; the old file too."""
    out: list[dict] = []
    p = path()
    for f in (p.with_name("crashes.1.jsonl"), p):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and float(row.get("at") or 0) >= since:
                out.append(row)
    out.sort(key=lambda r: r.get("at") or 0)
    return out[-limit:] if limit else out


def clear() -> int:
    """Remove both files; how many lines there were."""
    n = len(read())
    p = path()
    for f in (p, p.with_name("crashes.1.jsonl")):
        try:
            f.unlink()
        except OSError:
            pass
    return n
