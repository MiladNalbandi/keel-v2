"""The diff guard: after an agent step, put back every file the phase does not allow.

CLI agents write files directly, so the tool-level guard cannot see them. This is the backstop:
compare `git status` before and after the step and revert what MATRIX[phase] denies. Files that
were already dirty before the step keep their earlier content.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .. import rules
from ..rules import checks as _checks  # noqa: F401  (rules.checks.added_text)
from . import git


@dataclass
class Snapshot:
    dirty: dict[str, str]
    content: dict[str, bytes | None]
    gitmeta: dict[str, tuple[bytes, int]] = field(default_factory=dict)     # 0.21.3 git_meta before the step


def snapshot(root: str) -> Snapshot | None:
    if not git.is_repo(root):
        return None
    d = git.dirty(root)
    content = {}
    for rel in d:
        f = Path(root) / rel
        content[rel] = f.read_bytes() if f.is_file() else None
    return Snapshot(d, content, git_meta(root))


# ------------------------------------------------------------------ 0.21.3 the repo's own git settings
#
# An agent can write .git/config (core.fsmonitor, core.hooksPath, an ext:: remote), a hook or .git/info/ with a script,
# and `git status` never shows .git/. keel's own git turns those off (tools/git.py SAFE_ARGS, the api's Proc), but the
# person's git on their computer runs them. So every step's snapshot keeps them, and guard_diff puts back what the step
# changed there and reports it as a refusal: a fsmonitor or a hook written by an agent never survives its step.

GIT_META = ("config", "config.worktree", "hooks", "info")
GIT_META_MAX = 2_000_000            # bytes per file (a hook bigger than this is kept as it is)


def git_dirs(root: str) -> list[str]:
    """The repo's git folder and its common folder (the same one outside a git worktree), absolute."""
    r = git.git(root, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
    if r.returncode != 0:
        return []
    return list(dict.fromkeys(line.strip() for line in r.stdout.splitlines() if line.strip()))


def git_meta(root: str) -> dict[str, tuple[bytes, int]]:
    """{absolute path: (bytes, mode)} of the repo's config, config.worktree, hooks/ and info/ files."""
    out: dict[str, tuple[bytes, int]] = {}
    for d in git_dirs(root):
        for name in GIT_META:
            p = os.path.join(d, name)
            files = [p] if os.path.isfile(p) else [os.path.join(b, f) for b, _ds, fs in os.walk(p) for f in fs]
            for f in files:
                try:
                    if os.path.islink(f) or os.path.getsize(f) > GIT_META_MAX:
                        continue
                    out[f] = (Path(f).read_bytes(), os.stat(f).st_mode & 0o777)
                except OSError:
                    continue
    return out


def _git_meta_now(root: str, before: dict[str, tuple[bytes, int]]) -> dict[str, tuple[bytes, int] | None]:
    """The files of git_meta now, and every one of `before` that is gone (None)."""
    now: dict[str, tuple[bytes, int] | None] = dict(git_meta(root))
    for f in before:
        now.setdefault(f, None)
    # a link or a file too big that a step put in place counts too: it was not there before
    for d in git_dirs(root):
        for name in GIT_META:
            p = os.path.join(d, name)
            for f in ([p] if os.path.lexists(p) and not os.path.isdir(p) else
                      [os.path.join(b, x) for b, _ds, fs in os.walk(p) for x in fs]):
                if f not in now and f not in before:
                    now[f] = (b"", -1)
    return now


def _as_shared(mode: int) -> int:
    """A mode as keel's own sharing leaves it (agent_user.share before every agent command: group rw, and group x where
    the owner has x). Those bits are keel's doing, not the step's, so they never count as a change."""
    return mode if mode < 0 else mode | 0o060 | (0o010 if mode & 0o100 else 0)


def restore_git_meta(root: str, before: dict[str, tuple[bytes, int]] | None) -> list[dict]:
    """Puts back the repo's git settings as `before` had them. Returns [{path, bucket, reason}] for each file put
    back (path relative to `root` where it is inside it)."""
    if before is None:
        return []
    refused = []
    for f, got in sorted(_git_meta_now(root, before).items()):
        want = before.get(f)
        if got is not None and want is not None and got[0] == want[0] and _as_shared(got[1]) == _as_shared(want[1]):
            continue
        if got is None and want is None:
            continue
        try:
            if os.path.lexists(f):
                os.unlink(f)            # not written in place: the agent's file may not be keel's to write
            if want is not None:
                os.makedirs(os.path.dirname(f), exist_ok=True)
                with open(f, "wb") as fh:
                    fh.write(want[0])
                os.chmod(f, want[1])
        except OSError:
            pass
        try:
            rel = os.path.relpath(f, root)
            shown = rel if not rel.startswith("..") else f
        except ValueError:
            shown = f
        what = "added" if want is None else "removed" if got is None else "changed"
        refused.append({"path": shown, "bucket": "git-settings",
                        "reason": f"{shown}: the step {what} the repo's own git settings (config, hooks, info); keel put "
                                  "them back. A hook or an fsmonitor there would run on the next git call."})
    return refused


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


def changed_since(root: str, before: Snapshot | None) -> list[str]:
    """The files an agent step changed (created, edited or deleted) since the snapshot."""
    return _changed_since(root, before) if before is not None else []


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


def _old_text(root: str, rel: str, before: Snapshot) -> str | None:
    if rel in before.content:
        old = before.content[rel]
        return old.decode(errors="replace") if old is not None else None
    if git.tracked_in_head(root, rel):
        r = git.git(root, "show", f"HEAD:{rel}")
        return r.stdout if r.returncode == 0 else None
    return None


def content_refusal(root: str, rel: str, before: Snapshot, cfg: dict) -> str | None:
    """keel v1 H16/H6 for every engine: a changed file whose new lines hold a secret or a disabled-test marker."""
    f = Path(root) / rel
    try:
        if not f.is_file() or f.stat().st_size > 2_000_000:
            return None
        new = f.read_text(errors="replace")
    except OSError:
        return None
    v = rules.check_content(rel, rules.checks.added_text(_old_text(root, rel, before), new), cfg)
    return None if v.ok else v.reason


def guard_diff(root: str, phase: str, before: Snapshot | None, cfg: dict | None = None,
               lane: str | None = None, unlocks: list[dict] | None = None, readonly: bool = False) -> list[dict]:
    """Revert disallowed changes. Returns [{path, bucket, reason}] for each file put back.

    `readonly` (run mode readonly): every change is put back, in any phase, unlocks or not. A file whose new lines hold
    a secret or a disabled test is put back too (rules.check_content), in any phase."""
    if before is None:
        return []
    refused = restore_git_meta(root, before.gitmeta) if before.gitmeta else []
    if (not phase or phase == "none") and not readonly:
        return refused
    cfg = cfg or rules.load_config(root)
    for rel in _changed_since(root, before):
        existed = (rel in before.content and before.content[rel] is not None) or \
                  (rel not in before.content and git.tracked_in_head(root, rel))
        v = rules.check_edit(phase, rel, cfg, exists=existed, lane=lane, unlocks=unlocks)
        if readonly:
            v = rules.Verdict(False, v.bucket, f"{rel}: this flow runs read-only (run mode readonly); no file may change.")
        if v.ok:
            why = content_refusal(root, rel, before, cfg)
            if not why:
                continue
            v = rules.Verdict(False, "content", why)
        _restore(root, rel, before)
        refused.append({"path": rel, "bucket": v.bucket, "reason": v.reason})
    return refused
