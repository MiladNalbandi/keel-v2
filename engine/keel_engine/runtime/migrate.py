"""Engine DB migrations, run at every engine start (Engine.open). Each one is idempotent.

    thread_unlocks          unlocks granted through POST /threads/{id}/unlocks; the thread's next step merges them
                            into its graph state, and the guards of running agents get them at once
    legacy_unlock_imports   projects whose keel v0.3 `.keel/` state file had unlocks, imported once into a thread
                            (keel no longer writes or reads that file otherwise)
    verdicts                one row per check run (memory, release, coverage, ...), stamped with the commit it saw;
                            replaces keel v1's .keel/{release,coverage,security,memory}.json (runtime/verdicts.py)
    project_map             the latest map of each project (runtime/mapper.py), in the shape the web's Map page draws
    project_index           each project's code-graph index: idle | indexing | ready | failed (runtime/scan.py)
"""

from __future__ import annotations

import json
from pathlib import Path

from .state import normalize_unlocks

MIGRATIONS = [
    """create table if not exists thread_unlocks (
      thread_id text not null, path text not null, phase text not null, by text not null, reason text, at text not null,
      primary key (thread_id, path, phase)
    )""",
    """create table if not exists legacy_unlock_imports (
      root text primary key, thread_id text not null, unlocks integer not null, at text not null
    )""",
    """create table if not exists verdicts (
      project text not null, kind text not null, ok integer not null, detail_json text, "commit" text, at text not null
    )""",
    "create index if not exists verdicts_by_kind on verdicts (project, kind)",
    """create table if not exists project_map (
      project text primary key, "commit" text, json text not null, at text not null
    )""",
    """create table if not exists project_index (
      project text primary key, root text, status text not null, files integer, symbols integer, indexed_at text,
      error text, detail_json text, updated_at text not null
    )""",
]


async def migrate(conn) -> None:
    for sql in MIGRATIONS:
        await conn.execute(sql)
    await conn.commit()


def legacy_unlocks(root: str, thread_id: str) -> list[dict]:
    """Unlocks in the state file keel v0.3 kept in the project (keel v1's format), when they belong to this thread.

    They do when the file names this thread (keel v0.3 wrote it for the thread that ran last) or names no keel v2
    thread at all (keel v1 wrote it), and its flow is still active: a finished flow's unlocks are not handed on.
    """
    try:
        data = json.loads((Path(root) / ".keel" / "state.json").read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not data.get("flow"):
        return []
    engine = data.get("engine") if isinstance(data.get("engine"), dict) else {}
    if engine.get("thread_id") and engine["thread_id"] != thread_id:
        return []
    return normalize_unlocks(data.get("unlocks"), str(data.get("phase") or "none"), "import")
