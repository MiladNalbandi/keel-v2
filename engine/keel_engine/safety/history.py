"""A project's safety history: one JSON line per tool call keel's guard judged, each line holding the hash of the
line before it (from keel-gate's audit log).

    append(path, record)    adds one line (an flock keeps the chain in order if two processes ever append at once)
    read(path)              every line
    verify(path)            (True, "N lines, chain intact") or (False, the first line that does not fit and why)

Each line: {"seq", "at", "prev", "hash", "thread", "step", "kind", "tool", "text", "path", "action", "tier",
"rule", "reason", ...} (safety/recorder.py writes them). `hash` = sha256(prev + the line without its hash, as
canonical JSON). Changing, removing or reordering a line breaks every hash after it. Secret values in arguments are masked before they are written.

The file lives in keel's data folder ($KEEL_DATA/history/<project>.jsonl), outside every project. The engine writes
it, as keel's own user: in keel's image the agents (their own user) cannot read or change it. In native mode they run
as the person. The chain shows a line changed by hand; it is no signature (whoever rewrites the whole file can chain
it again).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .secret_data import PATTERNS

GENESIS = "0" * 64
MAX_TEXT = 2000
FOLDER = "history"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def line_hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + canonical(body)).encode()).hexdigest()


def mask(value: Any) -> Any:
    """Secret values out, long text cut: the history is read on a web page."""
    if isinstance(value, str):
        out = value
        for rx, _why in PATTERNS:
            out = rx.sub("[secret]", out)
        return out[:MAX_TEXT]
    if isinstance(value, dict):
        return {str(k): mask(v) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return [mask(v) for v in value[:50]]
    return value


def path_for(data_dir: str | Path, project_id: str) -> Path:
    """The history file of one project: $KEEL_DATA/history/<project id>.jsonl (an odd id is hashed, never a path)."""
    pid = str(project_id or "")
    name = pid if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", pid) and ".." not in pid else \
        "p-" + hashlib.sha256(pid.encode()).hexdigest()[:24]
    return Path(data_dir) / FOLDER / f"{name}.jsonl"


def _tail(f) -> tuple[int, str]:
    """The seq and hash of the last line of an open file (read from the end: the file only grows)."""
    f.seek(0, os.SEEK_END)
    size = f.tell()
    if not size:
        return 0, GENESIS
    block, data = 4096, b""
    pos = size
    while pos > 0:
        step = min(block, pos)
        pos -= step
        f.seek(pos)
        data = f.read(step) + data
        lines = [x for x in data.split(b"\n") if x.strip()]
        if len(lines) > 1 or pos == 0:
            break
    last = [x for x in data.split(b"\n") if x.strip()][-1]
    rec = json.loads(last)
    return int(rec["seq"]), str(rec["hash"])


def append(path: str | Path, record: dict) -> dict:
    """Adds one line to the chain and returns it. Raises OSError when the file cannot be written."""
    import fcntl

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "r+b") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            seq, prev = _tail(f)
            body = {k: mask(v) if isinstance(v, (str, dict, list)) else v for k, v in record.items()
                    if k not in ("seq", "prev", "hash")}
            rec = {**body, "seq": seq + 1, "prev": prev}
            rec["hash"] = line_hash(prev, rec)
            f.seek(0, os.SEEK_END)
            f.write((json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode())
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return rec


def read(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"broken": line[:200]})
    return out


def verify(path: str | Path) -> tuple[bool, str]:
    """(True, "N lines, chain intact") or (False, the first line that does not fit the chain and why)."""
    prev, seq = GENESIS, 0
    p = Path(path)
    if not p.exists():
        return True, "0 lines, nothing recorded yet"
    for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            return False, f"line {n} is not JSON"
        got = rec.pop("hash", "")
        if rec.get("prev") != prev:
            return False, f"line {n} does not follow line {n - 1}: a line was removed, added or moved"
        if rec.get("seq") != seq + 1:
            return False, f"line {n} has number {rec.get('seq')} after {seq}"
        if line_hash(prev, rec) != got:
            return False, f"line {n} was changed after it was written"
        prev, seq = got, rec["seq"]
    return True, f"{seq} lines, chain intact"
