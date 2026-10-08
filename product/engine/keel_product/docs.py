"""keel Product's documents: versioned Markdown in the product repo, one folder per initiative.

    initiatives/INI-12/brief-v1.md, brief-v2.md, impact-v1.md, decision-v1.md, plan-v1.json, plan-v1.md, deck-v1.html

Every save is a new version and a git commit in the product repo, so the history shows who changed what and when.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

DOC_KINDS = ("brief", "impact", "decision", "plan", "deck", "outcome")
ID = re.compile(r"^INI-\d{1,6}$")
BEGIN, END = "BEGIN-DOC", "END-DOC"
LOCK_TRIES, LOCK_WAIT = 6, 0.25
QUESTIONS = re.compile(r"```[ \t]*keel-questions[ \t]*\n.*?\n[ \t]*```", re.S | re.I)


class DocError(ValueError):
    pass


def folder(root: str, initiative: str) -> Path:
    if not ID.match(initiative or ""):
        raise DocError(f"not an initiative id: {initiative!r}")
    return Path(root) / "initiatives" / initiative


def versions(root: str, initiative: str, kind: str) -> list[int]:
    d = folder(root, initiative)
    found = [int(m.group(1)) for f in d.glob(f"{kind}-v*.*") if (m := re.match(rf"^{kind}-v(\d+)\.", f.name))] if d.is_dir() else []
    return sorted(set(found))


def next_version(root: str, initiative: str, kind: str) -> int:
    have = versions(root, initiative, kind)
    return (have[-1] + 1) if have else 1


def extract(text: str) -> str:
    """The document in an agent's answer: between BEGIN-DOC and END-DOC, else the whole answer without its questions."""
    text = text or ""
    if BEGIN in text:
        body = text.split(BEGIN, 1)[1]
        body = body.split(END, 1)[0]
        return body.strip()
    return QUESTIONS.sub("", text).strip()


def write(root: str, initiative: str, kind: str, text: str, ext: str = "md", version: int | None = None,
          message: str | None = None) -> dict:
    """Write one version and commit it. Returns {kind, version, path, sha}."""
    if kind not in DOC_KINDS:
        raise DocError(f"unknown document kind: {kind!r}")
    v = version or next_version(root, initiative, kind)
    d = folder(root, initiative)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{kind}-v{v}.{ext}"
    f.write_text(text if text.endswith("\n") else text + "\n")
    rel = str(f.relative_to(root))
    sha = commit(root, [rel], message or f"{initiative}: {kind} v{v}")
    return {"kind": kind, "version": v, "path": rel, "sha": sha}


def commit(root: str, paths: list[str], message: str) -> str | None:
    """git add + commit in the product repo (keel's own identity); None when it is not a git repo."""
    if not (Path(root) / ".git").exists():
        return None

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-c", "user.name=keel", "-c", "user.email=keel@localhost", "-c", "commit.gpgsign=false",
                               *args], cwd=root, capture_output=True, text=True, timeout=30)

    # Two stage flows (or a flow and the api) may commit at the same moment: git's index.lock makes one of them wait.
    for attempt in range(LOCK_TRIES):
        r = git("add", "--", *paths)
        if r.returncode == 0:
            r = git("commit", "-q", "-m", message, "--", *paths)
        if r.returncode == 0:
            out = git("rev-parse", "HEAD").stdout.strip()
            return out or None
        if "index.lock" not in (r.stderr or "") or attempt == LOCK_TRIES - 1:
            return None
        time.sleep(LOCK_WAIT * (attempt + 1))
    return None
