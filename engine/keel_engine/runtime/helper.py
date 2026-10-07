"""KeelBot: chat sessions in the Repo page, run by keel's own harness under keel's rules.

A session belongs to one project and one mode. One turn is one agent run, shaped like a flow's agent step
(runtime/compiler.py `_run_agent`): the model's runner (claude, codex, copilot / opencode, an API key or the fake
model), keel's guarded tools (ToolBox), the MCP servers KeelBot may use plus the code graph, the guard context
the hook reads on every tool call, and the diff guard as the backstop for engines without a hook.

    modes   ask   read only: no edit, no new file, no command that changes files or git (the guard's readonly)
            fix   while the project's flow waits at a gate: edits under the flow's phase rules and unlocks; a command
                  that changes something waits for the person's OK (runtime/permissions.py); keel remembers each file
                  as it was before KeelBot's first change (Undo), and Done runs the checks and makes keel's commit
            side  any time, in its own copy of the project (a git worktree on branch keel/helper/<id>, tools/worktrees.py):
                  free edits there and nowhere else, commands asked as in Fix; Keep runs the checks and commits on that
                  branch; the person hands it over (a task, a change flow on the branch) or throws it away

How a session continues: claude and codex continue their own CLI session (cheap: their context stays cached); the
API-key runner gets the earlier messages; the other CLIs get the conversation so far in the prompt.

Events (thread_id = the session id, step "helper"): helper.started, helper.step, helper.finished. The api stores
them as agent calls (agent "helper", so the budget, Live agents and Jobs count them) but never as a flow.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import difflib
import json
import logging
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .. import config, models, rules
from ..models import catalog
from ..models.base import AgentRequest, AgentResult
from ..tools import git, guard, mcp, testcmd, worktrees
from ..tools.agent_tools import ToolBox, command_env
from . import agent_knowledge, db, graph_hints, guard_ctx, keelbot, permissions, plugins, prompts

log = logging.getLogger(__name__)

MODES = ("ask", "fix", "side")
AGENT = "helper"
RESUMABLE = {"claude", "codex"}          # CLIs that continue their own session (runtime/compiler.py RESUMABLE)
TRANSCRIPT_CHARS = 6000                  # the conversation so far, for the engines that cannot continue a session
HISTORY_TURNS = 12                       # earlier messages an API-key model gets
TURN_TIMEOUT = 900
STEP_FIELD_MAX = 24_000
MODE_TEXT = {
    "ask": "Mode: Ask. Read only: you change nothing (no edits, no new files, no command that changes files or git).",
    "fix": ("Mode: Fix at a gate. The flow waits for the person at a gate, and the person asked you to change something. "
            "Edit only what the request needs, with the smallest change; keel's guard refuses files the flow's phase "
            "({phase}) does not allow. Reading, searching and running the tests need no OK; any other command that changes "
            "something waits for the person's OK. Never commit: when the person presses Done, keel runs the checks and "
            "commits your change. End with one short line per file you changed."),
    "side": ("Mode: Side session. You work in your own copy of the project: a git worktree on branch {branch}, not the "
             "main folder. Edit freely there, but only there (keel refuses a file outside it); nothing you do touches the "
             "main folder or a flow running in it. Reading, searching and running the tests need no OK; any other command "
             "that changes something waits for the person's OK. Never commit: the person keeps your work (keel runs the "
             "checks and commits it on the branch), hands it over as a task or a flow, or throws it away. End with one "
             "short line per file you changed."),
}
DIFF_MAX = 40_000

FIELDS = ("id", "project", "root", "mode", "title", "model_json", "engine_session", "status", "error", "thread_id",
          "tokens_in", "tokens_out", "tokens_cached", "cost_usd", "turns", "created_at", "updated_at", "grants_json", "phase",
          "worktree", "branch", "base_sha")


class HelperError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


# ------------------------------------------------------------------ sessions and messages (the engine DB)

def _session(row) -> dict:
    s = dict(zip(FIELDS, row))
    s["model"] = db.loads(s.pop("model_json"), {})
    s["grants"] = db.loads(s.pop("grants_json"), [])
    return s


def fix_phase(flow: dict | None) -> str:
    """The phase a Fix chat works in. At a gate the flow's own phase often lets only notes change (phase "gate" at the
    AC gate), yet a fix there is the work under review: so KeelBot takes the phase of the nearest earlier step whose
    rules let code change (at the AC gate "green"; the api sends `phases_before`, nearest first), else the flow's own."""
    f = flow or {}
    phase = str(f.get("phase") or "none")
    for ph in [phase, *(f.get("phases_before") or [])]:
        if ph and rules.edits_code(str(ph)):
            return str(ph)
    return phase


def create(project: str, root: str, mode: str = "ask", model: dict | None = None, title: str = "",
           thread_id: str | None = None, flow: dict | None = None) -> dict:
    if mode not in MODES:
        raise HelperError(400, f"Unknown KeelBot mode {mode!r}.", f"Use one of: {', '.join(MODES)}.")
    if not Path(root).is_dir():
        raise HelperError(400, f"The project folder {root} does not exist.")
    if mode == "fix" and not thread_id:
        raise HelperError(400, "Fix mode needs the flow that waits.", "Open it while a flow of this project waits at a gate.")
    sid = "h_" + uuid.uuid4().hex[:16]
    wt: dict = {}
    if mode == "side":
        short = sid[2:10]
        try:
            wt = worktrees.add(str(Path(root).resolve()), f"helper-{short}", f"keel/helper/{short}")
        except worktrees.WorktreeError as exc:
            raise HelperError(409, str(exc), exc.hint) from exc
    now = db.now()
    with db.connect() as conn:
        conn.execute(f"insert into helper_sessions ({', '.join(FIELDS)}) values ({', '.join('?' * len(FIELDS))})",
                     (sid, project, str(Path(root).resolve()), mode, title.strip()[:120] or "New chat",
                      json.dumps(models.effective(model)), None, "idle", None, thread_id, 0, 0, 0, 0.0, 0, now, now, "[]",
                      fix_phase(flow) if mode == "fix" else None, wt.get("path"), wt.get("branch"), wt.get("base")))
    return get(sid)


def list_sessions(project: str, limit: int = 50) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(f"select {', '.join(FIELDS)} from helper_sessions where project = ? order by updated_at desc limit ?",
                            (project, limit)).fetchall()
    return [_session(r) for r in rows]


def get(sid: str, messages: bool = True) -> dict:
    with db.connect() as conn:
        row = conn.execute(f"select {', '.join(FIELDS)} from helper_sessions where id = ?", (sid,)).fetchone()
        if not row:
            raise HelperError(404, f"No KeelBot session {sid}.")
        s = _session(row)
        if messages:
            msgs = conn.execute("select n, role, text, call_id, data_json, at from helper_messages where session_id = ? order by n",
                                (sid,)).fetchall()
            s["messages"] = [{"n": n, "role": role, "text": text, "call_id": call_id, "data": db.loads(dj, {}), "at": at}
                             for n, role, text, call_id, dj, at in msgs]
    return s


def update(sid: str, **fields) -> dict:
    allowed = {k: v for k, v in fields.items() if k in ("title", "model_json", "engine_session", "status", "error",
                                                       "tokens_in", "tokens_out", "tokens_cached", "cost_usd", "turns", "phase")}
    if allowed:
        sets = ", ".join(f"{k} = ?" for k in allowed)
        with db.connect() as conn:
            conn.execute(f"update helper_sessions set {sets}, updated_at = ? where id = ?", (*allowed.values(), db.now(), sid))
    return get(sid, messages=False)


def set_model(sid: str, model: dict) -> dict:
    """Another model for the next turn. A different provider cannot continue the CLI's own session: it starts fresh
    (with the conversation so far in the prompt)."""
    s = get(sid, messages=False)
    m = models.effective(model)
    same = (s["model"] or {}).get("provider") == m.get("provider")
    return update(sid, model_json=json.dumps(m), **({} if same else {"engine_session": None}))


def add_grant(sid: str, command: str) -> list[str]:
    """"Always" for this command in this session."""
    s = get(sid, messages=False)
    grants = list(s["grants"])
    if command not in grants:
        grants.append(command)
        with db.connect() as conn:
            conn.execute("update helper_sessions set grants_json = ? where id = ?", (json.dumps(grants), sid))
    return grants


def delete(sid: str) -> None:
    """A side session's worktree and branch go too (throw away)."""
    s = get(sid, messages=False)
    if s.get("worktree"):
        worktrees.remove(s["root"], s["worktree"], s.get("branch"))
    with db.connect() as conn:
        conn.execute("delete from helper_files where session_id = ?", (sid,))
        conn.execute("delete from helper_messages where session_id = ?", (sid,))
        conn.execute("delete from helper_sessions where id = ?", (sid,))


def add_message(sid: str, role: str, text: str, call_id: str | None = None, data: dict | None = None) -> int:
    with db.connect() as conn:
        n = (conn.execute("select coalesce(max(n), 0) from helper_messages where session_id = ?", (sid,)).fetchone()[0] or 0) + 1
        conn.execute("insert into helper_messages (session_id, n, role, text, call_id, data_json, at) values (?, ?, ?, ?, ?, ?, ?)",
                     (sid, n, role, text, call_id, json.dumps(data or {}), db.now()))
        conn.execute("update helper_sessions set updated_at = ? where id = ?", (db.now(), sid))
    return n


def short_title(text: str, limit: int = 80) -> str:
    """A chat's title from its first message: the first line, cut at a word with "…" when it is long."""
    line = " ".join(text.strip().splitlines()[0].split()) if text.strip() else "New chat"
    if len(line) <= limit:
        return line
    cut = line[:limit - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut[limit // 2:] else cut).rstrip(" ,.;:-") + "…"


def workdir(s: dict) -> str:
    """Where a session's agent works: a side session's worktree, else the project folder."""
    return s.get("worktree") or s["root"]


def history(sid: str, before_n: int | None = None) -> list[tuple[str, str]]:
    """Earlier messages as [(user|assistant, text)], oldest first."""
    msgs = get(sid)["messages"]
    out = [("user" if m["role"] == "user" else "assistant", m["text"]) for m in msgs
           if m["role"] in ("user", "helper") and (before_n is None or m["n"] < before_n)]
    return out


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
    if s["mode"] == "side":
        return worktrees.changes(s["worktree"], "HEAD") if s.get("worktree") else []
    root = s["root"]
    with db.connect() as conn:
        rows = conn.execute("select path, existed, content from helper_files where session_id = ? order by path", (sid,)).fetchall()
    out = []
    for rel, existed, content in rows:
        f = Path(root) / rel
        now = f.read_bytes() if f.is_file() else None
        before = content if existed else None
        if now == before:
            continue
        status = "added" if before is None else "deleted" if now is None else "modified"
        diff = "".join(difflib.unified_diff(_text(before).splitlines(keepends=True), _text(now).splitlines(keepends=True),
                                            fromfile=f"a/{rel}" if before is not None else "/dev/null",
                                            tofile=f"b/{rel}" if now is not None else "/dev/null"))
        plus = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        minus = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        out.append({"path": rel, "status": status, "added": plus, "removed": minus, "diff": diff[:DIFF_MAX]})
    return out


def undo(sid: str, path: str | None = None) -> list[dict]:
    """Put one file (or every file) back as it was before KeelBot changed it."""
    s = get(sid, messages=False)
    if s["mode"] == "side":
        if not s.get("worktree"):
            raise HelperError(409, "This side session was handed over; its worktree is gone.")
        if path and not any(c["path"] == path for c in changes(sid)):
            raise HelperError(404, f"KeelBot did not change {path} in this chat.")
        try:
            return worktrees.undo(s["worktree"], "HEAD", path)
        except worktrees.WorktreeError as exc:
            raise HelperError(400, str(exc), exc.hint) from exc
    root = Path(s["root"])
    with db.connect() as conn:
        q = "select path, existed, content from helper_files where session_id = ?" + (" and path = ?" if path else "")
        rows = conn.execute(q, (sid, path) if path else (sid,)).fetchall()
        if path and not rows:
            raise HelperError(404, f"KeelBot did not change {path} in this chat.")
        for rel, existed, content in rows:
            f = root / rel
            if existed:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(content or b"")
            else:
                f.unlink(missing_ok=True)
            conn.execute("delete from helper_files where session_id = ? and path = ?", (sid, rel))
    return changes(sid)


def done(sid: str, flow: dict, emit=None, message: str = "", commit: dict | None = None) -> dict:
    """Run the checks, then keel's commit of only the files KeelBot changed (runtime/actions.py `commit`: the phase's
    commit rules, secrets, new dependencies, pre-commit tools). The flow's timeline gets a helper.commit event."""
    from . import actions          # late: actions imports most of the runtime

    s = get(sid, messages=False)
    if s["mode"] not in ("fix", "side"):
        raise HelperError(400, "Only a Fix chat or a side session has changes to commit.")
    if s["mode"] == "side" and not s.get("worktree"):
        raise HelperError(409, "This side session was handed over; its worktree is gone.")
    root = workdir(s)
    files = [c["path"] for c in changes(sid)]
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
        conn.execute("delete from helper_files where session_id = ?", (sid,))
    where = f" on branch {s['branch']}" if s["mode"] == "side" else ""
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
    if s["mode"] != "side":
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


# ------------------------------------------------------------------ the prompt

def _pointing(mentions: list[dict], selection: dict | None, open_file: str | None) -> str:
    lines = []
    for m in mentions or []:
        kind, value = str(m.get("kind") or ""), str(m.get("value") or "")
        if not value:
            continue
        where = f" ({m['file']}:{m['line']})" if m.get("file") and m.get("line") else ""
        lines.append(f"- {kind or 'item'} {value}{where}")
    if selection and selection.get("path") and selection.get("text"):
        lo, hi = selection.get("from"), selection.get("to")
        span = f":{lo}-{hi}" if lo and hi and lo != hi else (f":{lo}" if lo else "")
        lines.append(f"- selected lines {selection['path']}{span}:\n```\n{str(selection['text'])[:4000]}\n```")
    if open_file:
        lines.append(f"- the file open in the editor: {open_file}")
    return ("The person points at:\n" + "\n".join(lines)) if lines else ""


def _flow_block(flow: dict | None) -> str:
    """The flow that runs or waits in this project: its title, phase, spec, criteria and the waiting gate."""
    if not flow or not flow.get("title"):
        return ""
    lines = [f"A flow {flow.get('status') or 'runs'} in this project: \"{flow['title']}\" (phase {flow.get('phase') or '?'})."]
    if flow.get("spec"):
        lines.append(f"Its spec: {flow['spec']}")
    acs = flow.get("acs") or []
    if acs:
        lines.append("Its acceptance criteria:")
        lines += [f"- {a.get('id')} [{a.get('layer', '')}] {a.get('title', '')} ({a.get('status', '')})" for a in acs[:30]]
    w = flow.get("waiting") or {}
    if w.get("title"):
        lines.append(f"It waits for the person at a gate: {w['title']}.")
        if w.get("detail"):
            lines.append(f"The gate shows:\n{str(w['detail'])[:3000]}")
    return "\n".join(lines)


def _transcript(hist: list[tuple[str, str]]) -> str:
    """The conversation so far, newest kept, for engines that cannot continue their own session."""
    parts, used = [], 0
    for role, text in reversed(hist):
        line = f"{'Person' if role == 'user' else 'KeelBot'}: {text.strip()}"
        if used + len(line) > TRANSCRIPT_CHARS:
            break
        parts.append(line)
        used += len(line)
    return ("The conversation so far (oldest first):\n" + "\n\n".join(reversed(parts))) if parts else ""


def build_prompt(*, mode: str, root: str, question: str, know: dict, graph: bool, flow: dict | None,
                 mentions: list[dict] | None, selection: dict | None, open_file: str | None, transcript: str,
                 branch: str = "", pid: str = "", keel: dict | None = None) -> str:
    where = f"Your folder (the worktree): {root}" if mode == "side" else f"The project folder: {root}"
    parts = [MODE_TEXT[mode].format(phase=fix_phase(flow), branch=branch or "?"), where]
    block = agent_knowledge.prompt_block(root, know, graph)
    if block:
        parts.append(block)
    if pid and know.get("hints", False):
        # keel's own lookups in the code graph: the places to read first, at no tool call (runtime/graph_hints.py)
        hints = graph_hints.where_to_look(pid, question, mentions=mentions, open_file=open_file, selection=selection)
        if hints:
            parts.append(hints)
    ctx = plugins.context_files(root)
    if ctx:
        parts.append("Read these when they help: " + ", ".join(ctx))
    for extra in (_flow_block(flow), keelbot.keel_block(keel, question), transcript,
                  _pointing(mentions or [], selection, open_file)):
        if extra:
            parts.append(extra)
    parts.append(f"Question: {question.strip()}")
    return "\n\n".join(parts)


# ------------------------------------------------------------------ turns

class HelperRunner:
    """Runs turns in the background (one at a time per session) and stops them."""

    def __init__(self, bus):
        self.bus = bus
        self.tasks: dict[str, asyncio.Task] = {}
        # Fix mode: the running turn's ask key per session (it can only ask), and the questions waiting for the person
        self.ask_keys: dict[str, str] = {}
        self.questions: dict[str, dict] = {}
        self.answers: dict[str, concurrent.futures.Future] = {}

    # ---- permission cards (runtime/permissions.py) --------------------------------------------------------------

    def _ask(self, sid: str, key: str, kind: str, command: str, path: str = "") -> tuple[str | None, dict | None]:
        """(question id to wait for, or an answer at once)."""
        if not key or self.ask_keys.get(sid) != key:
            return None, {"decision": "deny", "why": "This KeelBot turn may not ask (it ended, or the key is wrong)."}
        try:
            s = get(sid, messages=False)
        except HelperError:
            return None, {"decision": "deny", "why": "The KeelBot chat is gone."}
        if permissions.granted(command, s["grants"]):
            return None, {"decision": "allow"}
        qid = "p_" + uuid.uuid4().hex[:12]
        q = {"id": qid, "session": sid, "project": s["project"], "thread_id": s["thread_id"], "kind": kind, "command": command,
             "path": path, "title": s["title"], "at": db.now()}
        self.questions[qid] = q
        self.answers[qid] = concurrent.futures.Future()
        self.bus.emit("helper.permission", sid, s["project"], step="helper", data=q)
        return qid, None

    def _settle(self, qid: str, answer: dict):
        fut = self.answers.pop(qid, None)
        q = self.questions.pop(qid, None)
        if fut and not fut.done():
            fut.set_result(answer)
        if q:
            self.bus.emit("helper.permission.answered", q["session"], q["project"], step="helper",
                          data={"id": qid, "decision": answer.get("decision"), "why": answer.get("why", "")})

    async def ask(self, sid: str, key: str, kind: str, command: str, path: str = "") -> dict:
        """The hook's question (POST /helper/permissions/ask): waits for the person, at most ASK_TIMEOUT."""
        qid, now = self._ask(sid, key, kind, command, path)
        if now:
            return now
        try:
            return await asyncio.wait_for(asyncio.wrap_future(self.answers[qid]), permissions.ASK_TIMEOUT)
        except asyncio.TimeoutError:
            self._settle(qid, {"decision": "deny", "why": "Nobody answered in 10 minutes, so the command did not run."})
            return {"decision": "deny", "why": "Nobody answered in 10 minutes, so the command did not run."}

    def ask_blocking(self, sid: str, key: str, command: str) -> tuple[bool, str]:
        """The ToolBox's question (API-key models run their tools in a worker thread)."""
        qid, now = self._ask(sid, key, "command", command)
        ans = now
        if qid:
            try:
                ans = self.answers[qid].result(timeout=permissions.ASK_TIMEOUT)
            except concurrent.futures.TimeoutError:
                ans = {"decision": "deny", "why": "Nobody answered in 10 minutes, so the command did not run."}
                self._settle(qid, ans)
        return (ans or {}).get("decision") == "allow", (ans or {}).get("why", "")

    def answer(self, qid: str, decision: str, why: str = "") -> dict:
        """The person's answer: once | always (this command, for the rest of the chat) | deny (with a reason)."""
        q = self.questions.get(qid)
        if not q:
            raise HelperError(404, "That question was answered already, or its command ended.")
        if decision not in ("once", "always", "deny"):
            raise HelperError(400, "Answer once, always or deny.")
        if decision == "always":
            add_grant(q["session"], q["command"])
        self._settle(qid, {"decision": "deny" if decision == "deny" else "allow",
                           "why": (why or "The person said no to this command.") if decision == "deny" else ""})
        return {"id": qid, "decision": decision}

    def pending(self, project: str | None = None) -> list[dict]:
        return [q for q in self.questions.values() if not project or q["project"] == project]

    def _drop_questions(self, sid: str, why: str):
        for qid in [k for k, q in self.questions.items() if q["session"] == sid]:
            self._settle(qid, {"decision": "deny", "why": why})

    def busy(self, sid: str) -> bool:
        t = self.tasks.get(sid)
        return bool(t and not t.done())

    async def turn(self, sid: str, body: dict) -> dict:
        s = get(sid, messages=False)
        text = str(body.get("text") or "").strip()
        if not text:
            raise HelperError(400, "The message is empty.")
        if self.busy(sid):
            raise HelperError(409, "KeelBot is still answering in this session.", "Wait for the answer, or stop it.")
        if s["mode"] == "side" and not s.get("worktree"):
            raise HelperError(409, "This side session was handed over; its worktree is gone.", "Start a new chat.")
        if body.get("model"):
            s = set_model(sid, body["model"])
        question, command = plugins.expand(s["root"], text)
        n = add_message(sid, "user", text, data={k: v for k, v in {"command": command, "mentions": body.get("mentions"),
                                                                    "selection": body.get("selection")}.items() if v})
        if s["title"] == "New chat" and s["turns"] == 0:
            update(sid, title=short_title(text))
        call_id = uuid.uuid4().hex
        update(sid, status="running", error=None)
        self.tasks[sid] = asyncio.create_task(self._run(sid, n, question, body, call_id))
        return {"session": sid, "call_id": call_id, "n": n, "command": command}

    async def stop(self, sid: str) -> dict:
        self._drop_questions(sid, "The person stopped KeelBot.")
        t = self.tasks.get(sid)
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        return get(sid, messages=False)

    async def close(self):
        for sid in list(self.tasks):
            await self.stop(sid)

    async def _run(self, sid: str, n: int, question: str, body: dict, call_id: str):
        s = get(sid, messages=False)
        project, mode = s["project"], s["mode"]
        root = workdir(s)               # a side session works in its worktree, never the project folder
        model = models.effective(s["model"])
        provider = model["provider"]
        keys = dict(body.get("keys") or {})
        counter = {"n": 0}

        def ev(type_: str, data: dict):
            self.bus.emit(type_, sid, project, step="helper", call_id=call_id, data=data)

        def emit(kind: str, text: str = "", **extra):
            counter["n"] += 1
            data = {"n": counter["n"], "kind": kind, "text": str(text)[:STEP_FIELD_MAX]}
            data.update({k: (v[:STEP_FIELD_MAX] if isinstance(v, str) else v) for k, v in extra.items() if v is not None})
            ev("helper.step", data)

        def on_refuse(tool: str, path: str, reason: str, command: str | None = None):
            emit("guard", reason, tool=tool, path=path or None, ok=False)

        know = agent_knowledge.for_agent(AGENT, body.get("agents"))
        cfg = rules.load_config(root)
        flow = body.get("flow") or {}
        if mode == "fix":
            # the flow's phase, criterion and unlocks decide what may change; commands that change something are asked
            ac = flow.get("ac") or None
            phase, unlocks = fix_phase(flow), list(flow.get("unlocks") or [])
            if phase != s.get("phase"):
                update(sid, phase=phase)
            key = secrets.token_urlsafe(24)
            self.ask_keys[sid] = key
            toolbox = ToolBox(root, phase, cfg=cfg, lane=rules.ac_lane(ac) if ac else None, ac=(ac or {}).get("id"),
                              ac_layer=(ac or {}).get("layer", "API"), on_refuse=on_refuse, unlocks=unlocks, agent=AGENT,
                              knowledge=know, readonly=flow.get("run_mode") == "readonly",
                              ask={"url": f"http://127.0.0.1:{config.port()}", "key": key, "session": sid},
                              asker=lambda command: self.ask_blocking(sid, key, command))
        elif mode == "side":
            # no flow's phase: only keel's always-on rules (secrets, .git, old migrations), and nothing outside the worktree
            phase, unlocks, ac = "none", [], None
            key = secrets.token_urlsafe(24)
            self.ask_keys[sid] = key
            toolbox = ToolBox(root, "none", cfg=cfg, on_refuse=on_refuse, agent=AGENT, knowledge=know, confine=True,
                              ask={"url": f"http://127.0.0.1:{config.port()}", "key": key, "session": sid},
                              asker=lambda command: self.ask_blocking(sid, key, command))
        else:
            phase, unlocks, ac = "none", [], None
            toolbox = ToolBox(root, "none", cfg=cfg, on_refuse=on_refuse, agent=AGENT, knowledge=know, readonly=True)
        resumable = model.get("mode") != "api" and provider in RESUMABLE
        session = s["engine_session"] if resumable else None
        resume = bool(session)
        if resumable and not session and provider == "claude":
            session = str(uuid.uuid4())
        hist = history(sid, before_n=n)
        res: AgentResult | None = None
        status, err = "done", None
        before = None
        t0 = time.monotonic()
        ev("helper.started", {"agent": AGENT, "provider": provider, "model": model.get("model"), "mode": model.get("mode"),
                              "phase": f"helper-{mode}", "session": sid, "n": n})

        def on_session(new: str):
            update(sid, engine_session=new)

        # everything that awaits sits in the try: a stop can come at any moment and is recorded the same way
        try:
            before = await asyncio.to_thread(guard.snapshot, root)
            graph = await asyncio.to_thread(mcp.codegraph_server_spec, s["root"])     # the project's index (same paths)
            sent = list(body.get("mcp") or [])
            specs = sent + ([graph] if graph and not any(x.get("name") == graph["name"] for x in sent) else [])
            allow = list(body.get("tools_allow") or []) + ([f"mcp:{graph['name']}:*"] if graph else [])
            mcp_specs, tools_allow = agent_knowledge.filter_mcp(specs, allow, know)
            transcript = "" if (resume or model.get("mode") == "api" or provider == "fake") else _transcript(hist)
            prompt = build_prompt(mode=mode, root=root, question=question, know=know, branch=s.get("branch") or "", pid=project,
                                  graph=agent_knowledge.has_codegraph(mcp_specs, tools_allow), flow=body.get("flow"),
                                  mentions=body.get("mentions"), selection=body.get("selection"), open_file=body.get("open_file"),
                                  transcript=transcript, keel=body.get("keel"))
            with tempfile.TemporaryDirectory(prefix="keel-helper-") as tmp:
                req = AgentRequest(agent=AGENT, system=prompts.system_prompt(AGENT, body.get("skills")), prompt=prompt,
                                   root=root, phase=phase, model=model, toolbox=toolbox, title=s["title"], step_name="helper",
                                   ac=ac,
                                   mcp_specs=mcp_specs, tools_allow=tools_allow, key=models.key_for(provider, keys),
                                   workdir=tmp, timeout=int(body.get("timeout") or TURN_TIMEOUT), keys=keys, session=session,
                                   resume=resume, on_session=on_session, thread=sid, knowledge=know,
                                   history=hist[-HISTORY_TURNS:] if model.get("mode") == "api" or provider == "fake" else [])
                gfile = guard_ctx.GuardFile(Path(tmp) / guard_ctx.FILE, **guard_ctx.context_for(req))
                req.guard_ctx = gfile.path
                if session and not resume:
                    update(sid, engine_session=session)
                res = await models.runner_for(model).run(req, emit)
        except asyncio.CancelledError:
            status, err = "stopped", "Stopped."
        except Exception as exc:  # the panel shows it; the session stays usable
            status, err = "failed", f"{exc}{(' ' + exc.hint) if getattr(exc, 'hint', '') else ''}"
            emit("error", err[:2000], ok=False)
        self.ask_keys.pop(sid, None)
        self._drop_questions(sid, "KeelBot's answer ended.")
        # the backstop for engines without keel's hook: in Ask nothing may change; in Fix only what the phase allows
        if mode == "ask":
            for r in guard.guard_diff(root, "none", before, cfg, None, None, True):
                emit("guard", f"Put back {r['path']}: KeelBot's Ask mode changes nothing.", path=r["path"], ok=False)
        elif mode == "side":
            for r in guard.guard_diff(root, "none", before, cfg, None, None, False):
                emit("guard", f"Put back {r['path']}: {r['reason']}", path=r["path"], ok=False)
        else:
            for r in guard.guard_diff(root, phase, before, cfg, rules.ac_lane(ac) if ac else None, unlocks,
                                      flow.get("run_mode") == "readonly"):
                emit("guard", f"Put back {r['path']}: {r['reason']}", path=r["path"], ok=False)
            remember_originals(sid, root, before)
        used = getattr(res, "__dict__", {}) if res else {}
        tin, tout = int(used.get("tokens_in", 0)), int(used.get("tokens_out", 0))
        cached = int(used.get("tokens_cached", 0))
        cost = float(used.get("cost_usd", 0.0))
        if res and not cost and model.get("mode") == "api" and provider != "fake":
            cost = catalog.cost_usd(provider, model.get("model", ""), tin, tout)
        answer = (res.text if res else "") or ("" if status == "done" else err or "")
        if status == "done" and not answer.strip():
            answer = "(KeelBot gave no answer.)"
        add_message(sid, "helper" if status == "done" else "note", answer, call_id=call_id,
                    data={"status": status, "provider": provider, "model": model.get("model"), "tokens_in": tin, "tokens_out": tout,
                          "tokens_cached": cached, "cost_usd": round(cost, 6), "ms": int((time.monotonic() - t0) * 1000)})
        cur = get(sid, messages=False)
        update(sid, status="idle" if status != "failed" else "failed", error=err if status == "failed" else None,
               tokens_in=cur["tokens_in"] + tin, tokens_out=cur["tokens_out"] + tout, tokens_cached=cur["tokens_cached"] + cached,
               cost_usd=round(cur["cost_usd"] + cost, 6), turns=cur["turns"] + 1)
        ev("helper.finished", {"agent": AGENT, "status": status, "tokens_in": tin, "tokens_out": tout, "tokens_cached": cached,
                               "cost_usd": round(cost, 6), "premium_requests": int(used.get("premium_requests", 0)),
                               "result": answer[:2000], "session": sid, "n": n})
        self.tasks.pop(sid, None)


def commands(root: str | None) -> list[dict]:
    """The slash commands the panel offers (runtime/plugins.py)."""
    return [{k: c[k] for k in ("name", "description", "plugin", "source")} for c in plugins.commands(root)]
