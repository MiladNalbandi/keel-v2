"""What a harness session knows before its first turn, as a coding agent expects to (#101):

- the project's instructions: CLAUDE.md, AGENTS.md, .claude/CLAUDE.md and CLAUDE.local.md at the project's root, with
  their `@path` imports (one level, inside the project); a folder's own CLAUDE.md or AGENTS.md comes with the first Read
  of a file in that folder;
- the git state: the branch, what changed (git status), the last commits;
- the environment: the folder, the date.

Every file goes through the agent's read rules first: a file keel's rules keep from the agent never reaches it here.
"""

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path

NOTE_FILES = ("CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
FOLDER_FILES = ("CLAUDE.md", "AGENTS.md")
NOTE_MAX = 20_000
_IMPORT = re.compile(r"^@(\S+)\s*$", re.M)


def _readable(box, rel: str) -> str | None:
    """A project file's text when the agent may read it, else None."""
    from .. import rules

    full = Path(box.root) / rel
    if not full.is_file():
        return None
    if not rules.check_read(rel, box.cfg, full=full, ranged=True, phase=box.phase).ok:
        return None
    try:
        return full.read_text(errors="replace")[:NOTE_MAX]
    except OSError:
        return None


def _with_imports(box, rel: str, text: str) -> str:
    """`@docs/style.md` on a line of its own: that file's text follows, once (inside the project, under the read rules)."""
    out = [text]
    for m in _IMPORT.finditer(text):
        target = (Path(rel).parent / m.group(1)).as_posix()
        r = box._resolve(target)
        if not r:
            continue
        body = _readable(box, r[1])
        if body:
            out.append(f"\n<imported from=\"{r[1]}\">\n{body}\n</imported>")
    return "".join(out)


def project_notes(box) -> str:
    parts = []
    for rel in NOTE_FILES:
        body = _readable(box, rel)
        if body and body.strip():
            parts.append(f"<project-instructions source=\"{rel}\">\n{_with_imports(box, rel, body).strip()}\n</project-instructions>")
    return "\n\n".join(parts)


def git_state(root: str) -> str:
    def git(*args: str) -> str:
        try:
            r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return r.stdout.rstrip() if r.returncode == 0 else ""      # status --short starts with a meaningful space

    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if not branch:
        return ""
    status = git("status", "--short").splitlines()
    shown = "\n".join(status[:30]) + (f"\n… ({len(status) - 30} more)" if len(status) > 30 else "")
    log = git("log", "--oneline", "-5")
    return (f"<git>\nBranch: {branch}\nChanged files:\n{shown or '(none)'}\nRecent commits:\n{log or '(none)'}\n</git>")


def environment(root: str) -> str:
    return f"<environment>\nProject folder: {root}\nToday: {date.today().isoformat()}\nPlatform: linux\n</environment>"


def session_start(box) -> str:
    """The text the first message of a new session begins with."""
    return "\n\n".join(x for x in (environment(box.root), git_state(box.root), project_notes(box)) if x)


class FolderNotes:
    """A folder's own CLAUDE.md / AGENTS.md, given once, with the first Read of a file in that folder (or below it)."""

    def __init__(self, box):
        self.box = box
        self.seen: set[str] = {"."}             # the root's notes come with the session's start

    def for_file(self, rel: str) -> str:
        parts = []
        folder = Path(rel).parent
        chain = [folder, *folder.parents]
        for d in reversed(chain):
            key = d.as_posix()
            if key in self.seen:
                continue
            self.seen.add(key)
            for name in FOLDER_FILES:
                body = _readable(self.box, (d / name).as_posix())
                if body and body.strip():
                    parts.append(f"<folder-instructions source=\"{(d / name).as_posix()}\">\n{body.strip()}\n</folder-instructions>")
        return "\n\n".join(parts)
