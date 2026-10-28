"""keel 0.18.18 the same failing command, again: refused while nothing changed (KeelBot's turns).

A person asked KeelBot to check out main while the folder had uncommitted changes. `git checkout main` failed ("Your
local changes ... would be overwritten"), and KeelBot ran the very same command again and again. Running it unchanged
can only fail the same way, so keel refuses it until something changed, and says what to do instead: read the error
and offer the person the ways forward.

"Nothing changed" is the folder's git state when the command failed: HEAD, `git status` and the content of every
changed file. A command that failed outside a git folder is never refused (keel cannot tell what changed).

The failures sit in `failed.json` in the run's own scratch folder (next to its guard context), so the hook (claude,
opencode) and keel's ToolBox (API-key models) read the same list:

    remember(folder, root, command, output)   after a command failed
    seed(folder, notes)                       a new run starts with the failures of the run before (KeelBot's turns)
    refusal(folder, root, command)            before a command runs: the reason it is refused, else None

Stdlib only: the hook imports this module.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from ..tools.git import git

FILE = "failed.json"
KEEP = 20


def _norm(command: str) -> str:
    return " ".join(str(command or "").split())


def fingerprint(root: str) -> str:
    """The folder's git state: HEAD, the status and the changed files' content; "" outside git (or on any error)."""
    try:
        head = git(root, "rev-parse", "HEAD", timeout=20)
        st = git(root, "status", "--porcelain=v1", "-uall", "-z", timeout=20, text=False)
        if st.returncode != 0:
            return ""
        h = hashlib.sha256(head.stdout.encode() + b"\0" + st.stdout)
        for entry in st.stdout.split(b"\0"):
            rel = entry[3:].decode(errors="replace") if len(entry) > 3 else ""
            f = Path(root) / rel
            if rel and f.is_file() and f.stat().st_size < 5_000_000:
                h.update(rel.encode() + b"\0" + f.read_bytes())
        return h.hexdigest()
    except (OSError, subprocess.SubprocessError):
        return ""


def first_line(output: str) -> str:
    """What a failed command said, in one line: its first error line, else its first line."""
    lines = [ln.strip() for ln in str(output or "").splitlines() if ln.strip() and not ln.startswith("exit ")]
    hit = next((ln for ln in lines if ln.lower().startswith(("error", "fatal", "aborting"))), None)
    return (hit or (lines[0] if lines else ""))[:300]


def _load(folder: str | Path) -> dict:
    try:
        return json.loads((Path(folder) / FILE).read_text())
    except (OSError, ValueError):
        return {}


def _save(folder: str | Path, seen: dict) -> None:
    while len(seen) > KEEP:
        seen.pop(next(iter(seen)))
    try:
        f = Path(folder) / FILE
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(seen))
        tmp.replace(f)
    except OSError:
        pass


# keel 0.18.63 a network error says nothing about the command: the same call may well work a moment later
_TRANSIENT = ("timed out", "timeout", "tls handshake", "connection reset", "connection refused", "temporary failure in "
              "name resolution", "could not resolve host", "network is unreachable", "502 bad gateway",
              "503 service unavailable", "504 gateway time")


def transient(output: str) -> bool:
    """True when a command failed on the network (a timeout, a TLS handshake, a reset), not on what it was asked."""
    low = str(output or "").lower()
    return any(t in low for t in _TRANSIENT)


def remember(folder: str | Path | None, root: str, command: str, output: str = "") -> dict:
    """Notes a failed command and the folder's state now. Answers the note ({command, said, state}), so a caller can
    keep it past this run (KeelBot's next turn: `seed`). A network error is not noted: running it again is fine."""
    if not _norm(command) or transient(output):
        return {}
    note = {"state": fingerprint(root), "said": first_line(output)}
    if folder:
        seen = _load(folder)
        seen[_norm(command)] = note
        _save(folder, seen)
    return {"command": _norm(command), **note}


def seed(folder: str | Path | None, notes: list[dict]) -> None:
    """A new run starts with the failures of the one before (KeelBot: its last answer's), each with its state: a repeat
    is refused only while the folder is still as it was then."""
    if not folder:
        return
    seen = _load(folder)
    for n in notes or []:
        if n.get("command") and n.get("state"):
            seen[_norm(n["command"])] = {"state": n["state"], "said": n.get("said", "")}
    _save(folder, seen)


def refusal(folder: str | Path | None, root: str, command: str) -> str | None:
    """Why this command does not run again: it failed a moment ago and nothing in the folder changed since."""
    if not folder:
        return None
    was = _load(folder).get(_norm(command))
    if not was or not was.get("state") or was["state"] != fingerprint(root):
        return None
    said = f" It said: {was['said']}" if was.get("said") else ""
    return (f"This exact command failed before and nothing in the folder changed since, so it would fail the same "
            f"way.{said} Do not run it again unchanged. Read the error, say in one line why it failed, and offer the "
            "person the ways forward as a keel-questions block (the safe one recommended); after their answer, do that.")
