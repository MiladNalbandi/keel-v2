"""Engine DB migrations, run at every engine start (Engine.open). Each one is idempotent.

    thread_unlocks          unlocks granted through POST /threads/{id}/unlocks; the thread's next step merges them
                            into its graph state, and the guards of running agents get them at once
    legacy_unlock_imports   projects whose keel v0.3 `.keel/` state file had unlocks, imported once into a thread
                            (keel no longer writes or reads that file otherwise)
    verdicts                one row per check run (memory, release, coverage, ...), stamped with the commit it saw;
                            replaces keel v1's .keel/{release,coverage,security,memory}.json (runtime/verdicts.py)
    project_map             the latest map of each project (runtime/mapper.py), in the shape the web's Map page draws
    project_index           each project's code-graph index: idle | indexing | ready | failed (runtime/scan.py)
    hunt_runs               one row per bug hunt (runtime/hunt.py): scope, mode, lenses, sweep record, report path
    hunt_candidates         every finding of a hunt with its verdict, severity, group, dispatch and close; never deleted
    hunt_groups             findings that share one cause: the cause in a sentence and the lead finding
    hunt_recipes            a proven finding's repro recipe (the file the fix flow's reproducer gets, never the claim)
    helper_sessions         KeelBot's chat sessions (runtime/helper.py): project, mode, model, the CLI's own session
                            id to continue, status and what they used
    helper_messages         each session's messages in order: the person's, KeelBot's answers (with the turn's call id)
    helper_files            Fix mode: each file KeelBot changed, as it was before its first change (Undo, Done)
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
    """create table if not exists hunt_runs (
      project text not null, run text not null, root text, thread_id text, at text not null, sha text, branch text,
      scope_json text, mode text, fast integer not null default 0, lenses_json text, swept_json text, gates_json text,
      stack_json text, report text, candidates_report text, next_id integer not null default 1,
      next_group integer not null default 1, updated_at text not null, primary key (project, run)
    )""",
    """create table if not exists hunt_candidates (
      project text not null, run text not null, id text not null, lens text, lane text, also_json text, title text,
      where_json text, symptom text, impact text, claim text, repro_hint text, kind text, status text not null,
      severity text, group_id text, group_role text, evidence text, proved_at text, proved_sha text, needs_e2e integer,
      e2e_spec text, dispatch_json text, close_json text, at text not null, primary key (project, run, id)
    )""",
    """create table if not exists hunt_groups (
      project text not null, run text not null, id text not null, cause text not null, lead text not null,
      at text not null, primary key (project, run, id)
    )""",
    """create table if not exists hunt_recipes (
      project text not null, run text not null, candidate text not null, file text not null, body text not null,
      runs integer, at text not null, primary key (project, run, candidate)
    )""",
    # KeelBot (runtime/helper.py): one row per chat session, one per message; a turn's steps are agent steps in the api
    """create table if not exists helper_sessions (
      id text primary key, project text not null, root text not null, mode text not null, title text not null,
      model_json text not null, engine_session text, status text not null, error text, thread_id text,
      tokens_in integer not null default 0, tokens_out integer not null default 0, tokens_cached integer not null default 0,
      cost_usd real not null default 0, turns integer not null default 0, created_at text not null, updated_at text not null,
      grants_json text not null default '[]', phase text, worktree text, branch text, base_sha text
    )""",
    """create index if not exists helper_sessions_project on helper_sessions (project, updated_at)""",
    # Fix mode: each file KeelBot changed, as it was before its first change (Undo puts it back; Done commits)
    """create table if not exists helper_files (
      session_id text not null, path text not null, existed integer not null, content blob, at text not null,
      primary key (session_id, path)
    )""",
    """create table if not exists helper_messages (
      session_id text not null, n integer not null, role text not null, text text not null, call_id text,
      data_json text, at text not null, primary key (session_id, n)
    )""",
]


# columns added to a table after it first shipped: (table, column, declaration); `create table if not exists` keeps an
# older table as it was, so these are added when missing
COLUMNS = [
    ("helper_sessions", "grants_json", "text not null default '[]'"),
    ("helper_sessions", "phase", "text"),
    ("helper_sessions", "worktree", "text"),
    ("helper_sessions", "branch", "text"),
    ("helper_sessions", "base_sha", "text"),
]


async def migrate(conn) -> None:
    for sql in MIGRATIONS:
        await conn.execute(sql)
    for table, column, decl in COLUMNS:
        cur = await conn.execute(f"pragma table_info({table})")
        if column not in {row[1] for row in await cur.fetchall()}:
            await conn.execute(f"alter table {table} add column {column} {decl}")
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
