"""The diff guard: after an agent step, put back every file the phase does not allow.

CLI agents write files directly, so the tool-level guard cannot see them. This is the backstop:
compare `git status` before and after the step and revert what MATRIX[phase] denies. Files that
were already dirty before the step keep their earlier content.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import rules
from . import git


@dataclass
class Snapshot:
    dirty: dict[str, str]
    content: dict[str, bytes | None]


def snapshot(root: str) -> Snapshot | None:
    if not git.is_repo(root):
        return None
    d = git.dirty(root)
    content = {}
    for rel in d:
        f = Path(root) / rel
        content[rel] = f.read_bytes() if f.is_file() else None
    return Snapshot(d, content)


def _changed_since(root: str, before: Snapshot) -> list[str]:
    now = git.dirty(root)
    changed = []
    for rel in now:
        if rel in before.dirty:
            f = Path(root) / rel
            cur = f.read_bytes() if f.is_file() else None
            if cur == before.content.get(rel):
                continue
        changed.append(rel)
    # A file that was dirty before and is now clean was reverted by the agent; that is allowed.
    return changed


def _restore(root: str, rel: str, before: Snapshot):
    f = Path(root) / rel
    if rel in before.content:
        old = before.content[rel]
        if old is None:
            f.unlink(missing_ok=True)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(old)
        return
    if git.tracked_in_head(root, rel):
        git.git(root, "checkout", "HEAD", "--", rel)
    else:
        f.unlink(missing_ok=True)
        # leave no empty folders behind
        parent = f.parent
        while parent != Path(root) and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


def guard_diff(root: str, phase: str, before: Snapshot | None, cfg: dict | None = None,
               lane: str | None = None, unlocks: list[dict] | None = None) -> list[dict]:
    """Revert disallowed changes. Returns [{path, bucket, reason}] for each file put back."""
    if before is None or not phase or phase == "none":
        return []
    cfg = cfg or rules.load_config(root)
    refused = []
    for rel in _changed_since(root, before):
        existed = (rel in before.content and before.content[rel] is not None) or \
                  (rel not in before.content and git.tracked_in_head(root, rel))
        v = rules.check_edit(phase, rel, cfg, exists=existed, lane=lane, unlocks=unlocks)
        if v.ok:
            continue
        _restore(root, rel, before)
        refused.append({"path": rel, "bucket": v.bucket, "reason": v.reason})
    return refused
