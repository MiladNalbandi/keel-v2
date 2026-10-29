"""KeelBot Fix mode: the files it changed, Undo, undo / redo a whole answer, Done / Keep, hand over and release (a part of helper.py)."""

from __future__ import annotations

import difflib
import json
import subprocess
from pathlib import Path

from keel_engine.tools.agent_tools import command_env
from keel_engine.runtime import db
from keel_engine.tools import git, guard, testcmd, worktrees

from .sessions import HelperError, _turn_table, add_message, fix_phase, get, is_side, workdir

DIFF_MAX = 40_000


# ------------------------------------------------------------------ Fix mode: the files KeelBot changed

def _head_bytes(root: str, rel: str) -> bytes | None:
    if not git.tracked_in_head(root, rel):
        return None
    r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=root, capture_output=True, timeout=30)
    return r.stdout if r.returncode == 0 else None


def remember_originals(sid: str, root: str, before: guard.Snapshot | None) -> list[str]:
    """After a Fix turn: each file it changed for the first time is remembered as it was before (the user's own
    uncommitted version when there was one, else HEAD, else "did not exist"). Returns the newly remembered paths."""
    if before is None:
        return []
    changed = guard.changed_since(root, before)
    if not changed:
        return []
    with db.connect() as conn:
        known = {r[0] for r in conn.execute("select path from helper_files where session_id = ?", (sid,))}
        new = []
        for rel in changed:
            if rel in known:
                continue
            original = before.content[rel] if rel in before.content else _head_bytes(root, rel)
            conn.execute("insert into helper_files (session_id, path, existed, content, at) values (?, ?, ?, ?, ?)",
                         (sid, rel, 0 if original is None else 1, original, db.now()))
            new.append(rel)
    return new


def _text(b: bytes | None) -> str:
    return "" if b is None else b.decode("utf-8", errors="replace")


def changes(sid: str) -> list[dict]:
    """The files KeelBot changed in this session, against what they were before: path, status, +/- lines, diff.
    A side session: what is not kept yet (its worktree against the last commit on its branch)."""
    s = get(sid, messages=False)
    if is_side(s):
        return worktrees.changes(s["worktree"], "HEAD") if s.get("worktree") else []
    root = s["root"]
    with db.connect() as conn:
        rows = conn.execute("select path, existed, content from helper_files where session_id = ? order by path", (sid,)).fetchall()
    # 0.18.71 only what git sees as changed in the folder now: after a switch to another branch, KeelBot's change of a file
    # that branch does not have showed as "the whole file deleted", with a commit button
    dirty = set(git.dirty(root)) if rows and git.is_repo(root) else None
    out = []
    for rel, existed, content in rows:
        if dirty is not None and rel not in dirty:
            continue
        f = Path(root) / rel
        now = f.read_bytes() if f.is_file() else None
        entry = _change(rel, content if existed else None, now)
        if entry:
            out.append(entry)
    return out


def _change(rel: str, before: bytes | None, now: bytes | None) -> dict | None:
    """One changed file as the panel and keel TUI show it: path, status, +/- lines, diff (None when it is the same)."""
    if now == before:
        return None
    status = "added" if before is None else "deleted" if now is None else "modified"
    diff = "".join(difflib.unified_diff(_text(before).splitlines(keepends=True), _text(now).splitlines(keepends=True),
                                        fromfile=f"a/{rel}" if before is not None else "/dev/null",
                                        tofile=f"b/{rel}" if now is not None else "/dev/null"))
    plus = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    minus = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return {"path": rel, "status": status, "added": plus, "removed": minus, "diff": diff[:DIFF_MAX]}


def turn_changes(sid: str, n: int) -> list[dict]:
    """0.18.20 The files one answer changed (the turn whose user message is `n`), each as it was before that turn
    against how the turn left it: keel TUI's block after an answer. Never the folder's other uncommitted files (the
    person's own, what keel init left): only what record_turn saw this turn change. [] for a turn that changed none."""
    get(sid, messages=False)                       # 404 for an unknown chat
    with db.connect() as conn:
        rows = _turn_files(conn, sid, n)
    out = []
    for rel, bx, b, ax, a, _kept in rows:
        entry = _change(rel, b if bx else None, a if ax else None)
        if entry:
            out.append(entry)
    return out


def _edited_since_last_turn(conn, s: dict, root: Path, paths: list[str]) -> list[str]:
    """Of these files, the ones the person changed after KeelBot's last (not undone) answer left them: undoing the file
    would write the old text over their edit (undo_turn checks the same). A file with no turn record is not checked."""
    sid = s["id"]
    _turn_table(conn)
    reverted = {n for t in _turns(get(sid)["messages"]) if t["data"].get("reverted") for n in t["ns"]}
    out = []
    for rel in paths:
        rows = conn.execute("select n, after_exists, after from helper_turn_files where session_id = ? and path = ? "
                            "order by n desc", (sid, rel)).fetchall()
        last = next((r for r in rows if r[0] not in reverted), None)
        if last and _now_bytes(root, rel) != (last[2] if last[1] else None):
            out.append(rel)
    return out


def undo(sid: str, path: str | None = None) -> list[dict]:
    """Put one file (or every file) back as it was before KeelBot changed it."""
    s = get(sid, messages=False)
    if is_side(s):
        if not s.get("worktree"):
            raise HelperError(409, "This side session was handed over; its worktree is gone.")
        if path and not any(c["path"] == path for c in changes(sid)):
            raise HelperError(404, f"KeelBot did not change {path} in this chat.")
        try:
            left = worktrees.undo(s["worktree"], "HEAD", path)
        except worktrees.WorktreeError as exc:
            raise HelperError(400, str(exc), exc.hint) from exc
        _drop_turn_paths(sid, [path] if path else None)
        return left
    root = Path(s["root"])
    with db.connect() as conn:
        q = "select path, existed, content from helper_files where session_id = ?" + (" and path = ?" if path else "")
        rows = conn.execute(q, (sid, path) if path else (sid,)).fetchall()
        if path and not rows:
            raise HelperError(404, f"KeelBot did not change {path} in this chat.")
        mine = _edited_since_last_turn(conn, s, root, [r[0] for r in rows])
        if mine:
            raise HelperError(409, f"You changed {', '.join(mine[:5])} after KeelBot's last answer, so keel did not undo it.",
                              "Undo your own change first, or keep the file as it is.")
        for rel, existed, content in rows:
            f = root / rel
            if existed:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(content or b"")
            else:
                f.unlink(missing_ok=True)
            conn.execute("delete from helper_files where session_id = ? and path = ?", (sid, rel))
    _drop_turn_paths(sid, [path] if path else None)
    return changes(sid)


# ------------------------------------------------------------------ 0.18.19 undo / redo a whole answer
#
# As opencode's /undo and /redo (from opencode (MIT), core/src/session/revert.ts): undo marks the last turn (the person's
# message and KeelBot's answer) as reverted, puts back every file that turn changed and gives its question back for the
# prompt; redo puts the turn back while no new turn was sent; a new turn makes the revert final. keel keeps each
# turn's files in helper_turn_files (this plugin's own table): the file as it was before the turn and after it.

def _now_bytes(root: Path, rel: str) -> bytes | None:
    f = root / rel
    return f.read_bytes() if f.is_file() else None


def _put(root: Path, rel: str, content: bytes | None) -> None:
    """A file as it was: written back, or deleted when it did not exist (only this one file, no git command)."""
    f = root / rel
    if content is None:
        f.unlink(missing_ok=True)
        parent = f.parent
        while parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    else:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content)


def record_turn(sid: str, n: int, root: str, before: guard.Snapshot | None) -> list[str]:
    """0.18.19 After a Fix, Edit or side turn: each file it changed, as it was before the turn (the person's own unsaved
    version when there was one, else HEAD, else "did not exist") and as it is now (after the guard put back what it
    refused). `n` is the turn's user message. Returns the paths."""
    if before is None:
        return []
    base = Path(root)
    changed = list(guard.changed_since(root, before))
    # a file that was dirty before and is back to HEAD now: the turn changed it too (changed_since leaves it out)
    changed += [rel for rel, old in before.content.items() if rel not in changed and _now_bytes(base, rel) != old]
    rows = []
    for rel in sorted(set(changed)):
        old = before.content[rel] if rel in before.content else _head_bytes(root, rel)
        new = _now_bytes(base, rel)
        if old != new:
            rows.append((sid, n, rel, 0 if old is None else 1, old, 0 if new is None else 1, new))
    if rows:
        with db.connect() as conn:
            _turn_table(conn)
            conn.executemany("insert or replace into helper_turn_files (session_id, n, path, before_exists, before, "
                             "after_exists, after) values (?, ?, ?, ?, ?, ?, ?)", rows)
    return [r[2] for r in rows]


def _turns(msgs: list[dict]) -> list[dict]:
    """The turns of a chat in order: its user message's n and text, and the n of each message that belongs to it (the
    user message, then KeelBot's answer or the note a stopped or failed turn left: the messages with a call id)."""
    out: list[dict] = []
    for m in msgs:
        if m["role"] == "user":
            out.append({"n": m["n"], "text": m["text"], "ns": [m["n"]], "data": m.get("data") or {}})
        elif out and m.get("call_id") and m["role"] in ("helper", "note"):
            out[-1]["ns"].append(m["n"])
    return out


def _mark(conn, sid: str, ns: list[int], **flags) -> None:
    """Sets (a value) or clears (None) flags in these messages' data."""
    for n in ns:
        row = conn.execute("select data_json from helper_messages where session_id = ? and n = ?", (sid, n)).fetchone()
        data = db.loads(row[0], {}) if row else {}
        for k, v in flags.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        conn.execute("update helper_messages set data_json = ? where session_id = ? and n = ?", (json.dumps(data), sid, n))


def _undoable(sid: str) -> tuple[dict, Path]:
    s = get(sid)
    # an Ask chat that never changed a file has nothing to undo; one switched to Ask from Edit, Fix or Side (0.18.19)
    # can still take back the answers that changed files, as it can still keep or undo its files
    if s["mode"] == "ask" and not is_side(s) and not _has_turn_files(sid):
        raise HelperError(400, "Ask mode changes nothing, so there is nothing to undo.",
                          "In Edit, Fix or a side chat, /undo takes KeelBot's last answer back.")
    if is_side(s) and not s.get("worktree"):
        raise HelperError(409, "This side session was handed over; its worktree is gone.")
    return s, Path(workdir(s))


def _has_turn_files(sid: str) -> bool:
    with db.connect() as conn:
        _turn_table(conn)
        return conn.execute("select 1 from helper_turn_files where session_id = ? limit 1", (sid,)).fetchone() is not None


def _turn_files(conn, sid: str, n: int) -> list[tuple]:
    _turn_table(conn)
    return conn.execute("select path, before_exists, before, after_exists, after, kept from helper_turn_files "
                        "where session_id = ? and n = ? order by path", (sid, n)).fetchall()


def _changed_by_person(root: Path, rows: list[tuple], want: str) -> list[str]:
    """The files that are not as the turn left them (want "after") or as undo left them (want "before")."""
    out = []
    for rel, bx, b, ax, a, _kept in rows:
        expect = (a if ax else None) if want == "after" else (b if bx else None)
        if _now_bytes(root, rel) != expect:
            out.append(rel)
    return out


def undo_turn(sid: str) -> dict:
    """0.18.19 Undo KeelBot's last answer: every file that turn changed goes back to what it was before it (Edit and Fix:
    the project folder; side: its worktree), its question and answer stay stored, marked reverted, and the question
    comes back. Each call takes one more turn back. Refused, with nothing changed, when the person changed one of those
    files after the turn, or keel kept (committed) one of them. Answers {n, text, files, messages}."""
    s, root = _undoable(sid)
    live = [t for t in _turns(s["messages"]) if not t["data"].get("reverted")]
    if not live:
        raise HelperError(409, "Nothing to undo: KeelBot has no answer in this chat that is not undone already.")
    t = live[-1]
    with db.connect() as conn:
        rows = _turn_files(conn, sid, t["n"])
        kept = [r[0] for r in rows if r[5]]
        if kept:
            raise HelperError(409, f"keel kept (committed) {', '.join(kept[:5])} already, so that answer cannot be undone.",
                              "Change the file in a new answer, or revert the commit yourself.")
        mine = _changed_by_person(root, rows, "after")
        if mine:
            raise HelperError(409, f"You changed {', '.join(mine[:5])} after that answer, so keel did not undo it.",
                              "Undo your own change first, or keep the files as they are and ask KeelBot again.")
        for rel, bx, b, _ax, _a, _k in rows:
            _put(root, rel, b if bx else None)
        _mark(conn, sid, t["ns"], reverted=True)
        conn.execute("update helper_sessions set updated_at = ? where id = ?", (db.now(), sid))
    return {"n": t["n"], "text": t["text"], "files": [r[0] for r in rows], "messages": t["ns"]}


def redo_turn(sid: str) -> dict:
    """0.18.19 Redo: the earliest undone turn that is not final comes back (its files as that turn left them, its
    messages no longer reverted), as opencode's /redo moves the revert one turn on. Refused
    when the person changed one of those files after the undo. Answers {n, text, files, messages}."""
    s, root = _undoable(sid)
    undone = [t for t in _turns(s["messages"]) if t["data"].get("reverted") and not t["data"].get("final")]
    if not undone:
        raise HelperError(409, "Nothing to redo: no answer was undone since the last message.",
                          "/redo puts back what /undo took, until you send a new message.")
    t = undone[0]
    with db.connect() as conn:
        rows = _turn_files(conn, sid, t["n"])
        mine = _changed_by_person(root, rows, "before")
        if mine:
            raise HelperError(409, f"You changed {', '.join(mine[:5])} after the undo, so keel did not redo it.",
                              "Undo your own change first, or ask KeelBot again.")
        for rel, _bx, _b, ax, a, _k in rows:
            _put(root, rel, a if ax else None)
        _mark(conn, sid, t["ns"], reverted=None)
        conn.execute("update helper_sessions set updated_at = ? where id = ?", (db.now(), sid))
    return {"n": t["n"], "text": t["text"], "files": [r[0] for r in rows], "messages": t["ns"]}


def finalize_reverts(sid: str) -> int:
    """0.18.19 A new turn makes the undone turns final: they stay stored and shown as reverted, but redo cannot bring
    them back (their newer files are dropped), and the CLI's own session, which still holds them, is not continued
    (the next turn starts a new one with the conversation without them). Answers how many turns became final."""
    s = get(sid)
    undone = [t for t in _turns(s["messages"]) if t["data"].get("reverted") and not t["data"].get("final")]
    if not undone:
        return 0
    with db.connect() as conn:
        _turn_table(conn)
        for t in undone:
            _mark(conn, sid, t["ns"], final=True)
            conn.execute("delete from helper_turn_files where session_id = ? and n = ?", (sid, t["n"]))
        conn.execute("update helper_sessions set engine_session = null where id = ?", (sid,))
    return len(undone)


def _drop_turn_paths(sid: str, paths: list[str] | None, kept: bool = False) -> None:
    """0.18.19 A file undone on its own (/undo <path>, or every file) leaves the turns' records (undo_turn skips it); a
    kept (committed) one is marked kept, so no undo_turn puts it back behind keel's commit."""
    with db.connect() as conn:
        _turn_table(conn)
        if kept:
            conn.executemany("update helper_turn_files set kept = 1 where session_id = ? and path = ?",
                             [(sid, p) for p in paths or []])
        elif paths is None:
            conn.execute("delete from helper_turn_files where session_id = ?", (sid,))
        else:
            conn.executemany("delete from helper_turn_files where session_id = ? and path = ?", [(sid, p) for p in paths])


def done(sid: str, flow: dict, emit=None, message: str = "", commit: dict | None = None,
         paths: list[str] | None = None) -> dict:
    """Run the checks, then keel's commit of only the files KeelBot changed (runtime/actions.py `commit`: the phase's
    commit rules, secrets, new dependencies, pre-commit tools). The flow's timeline gets a helper.commit event.
    keel 0.18.3 `paths`: keep only these of its files (keel TUI's /keep <path>); the others stay changes to keep or undo."""
    from keel_engine.runtime import actions          # late: actions imports most of the runtime

    s = get(sid, messages=False)
    # 0.18.19 a chat switched to Ask keeps what it changed before (and a side session its worktree)
    if s["mode"] not in ("fix", "side", "edit") and not (is_side(s) or changes(sid)):
        raise HelperError(400, "Only a Fix, Edit or side chat has changes to commit.")
    if is_side(s) and not s.get("worktree"):
        raise HelperError(409, "This side session was handed over; its worktree is gone.")
    root = workdir(s)
    files = [c["path"] for c in changes(sid)]
    if paths:
        unknown = [p for p in paths if p not in files]
        if unknown:
            raise HelperError(404, f"KeelBot did not change {', '.join(unknown[:5])} in this chat.", "/changes lists its files.")
        files = [f for f in files if f in paths]
    if not files:
        return {"ok": False, "step": "changes", "error": "Nothing to commit: KeelBot changed no file in this chat."}
    # the module's tests (the criterion's own test is one of them): the change works and nothing else broke
    ac = flow.get("ac") or None
    cmd = testcmd.command_for(root, None, (ac or {}).get("layer", "API")) or testcmd.command_for(root)
    if cmd:
        code, out = testcmd.run(root, cmd, env=command_env())
        if code != 0:
            return {"ok": False, "step": "checks", "command": cmd, "error": "The checks failed, so keel did not commit.",
                    "output": out[-6000:]}
    # "fix(AC-2): <what the person wrote>" at a criterion's gate, else "fix(helper): ..."; the chat's title by default
    subject = " ".join((message or s["title"]).split())[:120]
    a = actions.ActionInput(root=root, phase=fix_phase(flow), title=subject, ac=None, ident=(ac or {}).get("id") or "helper",
                            acs=list(flow.get("acs") or []), fake=False, flow=str(flow.get("workflow") or "helper"),
                            unlocks=list(flow.get("unlocks") or []), deps=list(flow.get("deps") or []), project=s["project"],
                            paths=files, thread_id=s["thread_id"] or "",
                            settings={"run_mode": flow.get("run_mode") or "manual",
                                      **{k: v for k, v in (commit or {}).items() if k in ("commit_author", "commit_coauthor")}})
    res = actions.commit(a)
    if not res.ok:
        return {"ok": False, "step": "commit", "error": res.note, "output": res.detail or ""}
    sha = git.head(root)
    with db.connect() as conn:
        conn.executemany("delete from helper_files where session_id = ? and path = ?", [(sid, f) for f in files])
    _drop_turn_paths(sid, files, kept=True)          # 0.18.19 a kept file: no undo_turn puts it back behind the commit
    where = f" on branch {s['branch']}" if is_side(s) else ""
    note = f"keel committed KeelBot's change{where}: {res.note}" + (f" (checks: {cmd})" if cmd else " (no test command found)")
    subject = git.git(root, "log", "-1", "--format=%s").stdout.strip()
    add_message(sid, "note", note, data={"status": "committed", "sha": sha, "files": files, "subject": subject})
    if emit:
        emit("helper.commit", s["thread_id"] or sid, s["project"], {"session": sid, "sha": sha, "message": res.note,
                                                                    "files": files, "checks": cmd})
    return {"ok": True, "sha": sha, "message": res.note, "files": files, "checks": cmd}


def handover(sid: str) -> dict:
    """What a side session hands over (a task or a change flow): its branch, the commits kept on it, what is not kept
    yet, and its last answer."""
    s = get(sid)
    if not is_side(s):
        raise HelperError(400, "Only a side session can be handed over.")
    if not s.get("worktree"):
        raise HelperError(409, "This side session was handed over already.")
    answers = [m["text"] for m in s["messages"] if m["role"] == "helper"]
    asked = [m["text"] for m in s["messages"] if m["role"] == "user"]
    return {"session": sid, "title": s["title"], "branch": s["branch"], "base": s["base_sha"], "worktree": s["worktree"],
            "commits": worktrees.commits(s["worktree"], s["base_sha"]), "uncommitted": [c["path"] for c in changes(sid)],
            "asked": asked[:20], "answer": answers[-1] if answers else ""}


def release(sid: str) -> dict:
    """Hand a side session's branch over: its worktree goes, the branch and its commits stay (a change flow takes it)."""
    h = handover(sid)
    if h["uncommitted"]:
        raise HelperError(409, f"{len(h['uncommitted'])} file(s) are not kept yet: {', '.join(h['uncommitted'][:5])}.",
                          "Keep them (the checks run, keel commits on the branch) or undo them first.")
    s = get(sid, messages=False)
    worktrees.remove(s["root"], s["worktree"], None)
    with db.connect() as conn:
        conn.execute("update helper_sessions set worktree = null, updated_at = ? where id = ?", (db.now(), sid))
    add_message(sid, "note", f"Handed over: branch {h['branch']} with {len(h['commits'])} commit(s); its worktree is gone.",
                data={"status": "handed", "branch": h["branch"]})
    return h


def commits_for(thread_id: str) -> list[dict]:
    """The commits Done made for a flow (its Fix chats), oldest first: for the PR body."""
    if not thread_id:
        return []
    with db.connect() as conn:
        rows = conn.execute("select m.data_json, m.text, s.id, s.root from helper_messages m join helper_sessions s on s.id = m.session_id "
                            "where s.thread_id = ? and m.role = 'note' order by m.at", (thread_id,)).fetchall()
    out = []
    for dj, text, sid, root in rows:
        d = db.loads(dj, {})
        if d.get("status") == "committed" and d.get("sha"):
            subject = d.get("subject") or git.git(root, "log", "-1", "--format=%s", d["sha"]).stdout.strip() or text
            out.append({"sha": d["sha"], "subject": subject, "files": d.get("files") or [], "session": sid})
    return out
