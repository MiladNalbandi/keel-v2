"""Reading a project's safety history. The engine writes it (engine/keel_engine/safety/history.py has the format and
the writer); the two must agree on the file's place and on the hash of a line.

    path_for(data_dir, project_id)    $KEEL_DATA/history/<project id>.jsonl
    read(path)                        every line, oldest first
    verify(path)                      (True, "N lines, chain intact") or (False, the first line that does not fit and why)
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
FOLDER = "history"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def line_hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + canonical(body)).encode()).hexdigest()


def path_for(data_dir: str | Path, project_id: str) -> Path:
    pid = str(project_id or "")
    name = pid if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", pid) and ".." not in pid else \
        "p-" + hashlib.sha256(pid.encode()).hexdigest()[:24]
    return Path(data_dir) / FOLDER / f"{name}.jsonl"


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
