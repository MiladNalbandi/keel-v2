"""Verdicts: the result of a check (knowledge, release suite, coverage, ...) for one project, stamped with the commit it saw.

    write(project, kind, ok, detail, commit)    one row per run; the history stays
    latest(project, kind) -> {kind, ok, detail, commit, at} | None

They live in the engine DB (table `verdicts`), not in the project: keel v1 wrote `.keel/<kind>.json` files instead.
`detail` may carry `tree` (the git tree the check ran on), so a check that ran on uncommitted files is still current for
the commit made from exactly those files (see fresh()).
"""

from __future__ import annotations

import json

from ..tools import git
from . import db

KINDS = ("memory", "release", "coverage", "deps", "security", "fast", "module", "audit", "trace", "arch")


def write(project: str, kind: str, ok: bool, detail: dict | None = None, commit: str | None = None) -> dict:
    at = db.now()
    with db.connect() as conn:
        conn.execute('insert into verdicts (project, kind, ok, detail_json, "commit", at) values (?,?,?,?,?,?)',
                     (project, kind, 1 if ok else 0, json.dumps(detail or {}), commit, at))
    return {"kind": kind, "ok": bool(ok), "detail": detail or {}, "commit": commit, "at": at}


def latest(project: str, kind: str) -> dict | None:
    with db.connect() as conn:
        r = conn.execute('select ok, detail_json, "commit", at from verdicts where project = ? and kind = ? '
                         "order by rowid desc limit 1", (project, kind)).fetchone()
    if not r:
        return None
    return {"kind": kind, "ok": bool(r[0]), "detail": db.loads(r[1], {}), "commit": r[2], "at": r[3]}


def all_latest(project: str) -> dict[str, dict]:
    return {k: v for k in KINDS if (v := latest(project, k))}


def fresh(v: dict, head: str | None, head_tree: str | None) -> bool:
    """Is the verdict about the code at HEAD? Same commit, or the same tree (it ran on the files HEAD now holds)."""
    if not v:
        return False
    if head and v.get("commit") == head:
        return True
    tree = (v.get("detail") or {}).get("tree")
    return bool(tree and head_tree and tree == head_tree)


def stamp(root: str) -> tuple[str | None, str | None]:
    """(HEAD, the tree of the working files as the next commit would hold them) for a verdict written now."""
    if not git.is_repo(root):
        return None, None
    return git.head(root), git.worktree_tree(root)
