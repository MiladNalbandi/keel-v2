"""keel's Helper: chat sessions in the Repo page, run by keel's own harness under keel's rules.

A session belongs to one project and one mode. One turn is one agent run, shaped like a flow's agent step
(runtime/compiler.py `_run_agent`): the model's runner (claude, codex, copilot / opencode, an API key or the fake
model), keel's guarded tools (ToolBox), the MCP servers the Helper may use plus the code graph, the guard context
the hook reads on every tool call, and the diff guard as the backstop for engines without a hook.

    modes   ask   read only: no edit, no new file, no command that changes files or git (the guard's readonly)

How a session continues: claude and codex continue their own CLI session (cheap: their context stays cached); the
API-key runner gets the earlier messages; the other CLIs get the conversation so far in the prompt.

Events (thread_id = the session id, step "helper"): helper.started, helper.step, helper.finished. The api stores
them as agent calls (agent "helper", so the budget, Live agents and Jobs count them) but never as a flow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
import uuid
from pathlib import Path

from .. import models, rules
from ..models import catalog
from ..models.base import AgentRequest, AgentResult
from ..tools import guard, mcp
from ..tools.agent_tools import ToolBox
from . import agent_knowledge, db, guard_ctx, plugins, prompts

log = logging.getLogger(__name__)

MODES = ("ask",)
AGENT = "helper"
RESUMABLE = {"claude", "codex"}          # CLIs that continue their own session (runtime/compiler.py RESUMABLE)
TRANSCRIPT_CHARS = 6000                  # the conversation so far, for the engines that cannot continue a session
HISTORY_TURNS = 12                       # earlier messages an API-key model gets
TURN_TIMEOUT = 900
STEP_FIELD_MAX = 24_000
MODE_TEXT = {
    "ask": "Mode: Ask. Read only: you change nothing (no edits, no new files, no command that changes files or git).",
}

FIELDS = ("id", "project", "root", "mode", "title", "model_json", "engine_session", "status", "error", "thread_id",
          "tokens_in", "tokens_out", "tokens_cached", "cost_usd", "turns", "created_at", "updated_at")


class HelperError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


# ------------------------------------------------------------------ sessions and messages (the engine DB)

def _session(row) -> dict:
    s = dict(zip(FIELDS, row))
    s["model"] = db.loads(s.pop("model_json"), {})
    return s


def create(project: str, root: str, mode: str = "ask", model: dict | None = None, title: str = "",
           thread_id: str | None = None) -> dict:
    if mode not in MODES:
        raise HelperError(400, f"Unknown Helper mode {mode!r}.", f"Use one of: {', '.join(MODES)}.")
    if not Path(root).is_dir():
        raise HelperError(400, f"The project folder {root} does not exist.")
    sid = "h_" + uuid.uuid4().hex[:16]
    now = db.now()
    with db.connect() as conn:
        conn.execute(f"insert into helper_sessions ({', '.join(FIELDS)}) values ({', '.join('?' * len(FIELDS))})",
                     (sid, project, str(Path(root).resolve()), mode, title.strip()[:120] or "New chat",
                      json.dumps(models.effective(model)), None, "idle", None, thread_id, 0, 0, 0, 0.0, 0, now, now))
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
            raise HelperError(404, f"No Helper session {sid}.")
        s = _session(row)
        if messages:
            msgs = conn.execute("select n, role, text, call_id, data_json, at from helper_messages where session_id = ? order by n",
                                (sid,)).fetchall()
            s["messages"] = [{"n": n, "role": role, "text": text, "call_id": call_id, "data": db.loads(dj, {}), "at": at}
                             for n, role, text, call_id, dj, at in msgs]
    return s


def update(sid: str, **fields) -> dict:
    allowed = {k: v for k, v in fields.items() if k in ("title", "model_json", "engine_session", "status", "error",
                                                       "tokens_in", "tokens_out", "tokens_cached", "cost_usd", "turns")}
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


def delete(sid: str) -> None:
    get(sid, messages=False)
    with db.connect() as conn:
        conn.execute("delete from helper_messages where session_id = ?", (sid,))
        conn.execute("delete from helper_sessions where id = ?", (sid,))


def add_message(sid: str, role: str, text: str, call_id: str | None = None, data: dict | None = None) -> int:
    with db.connect() as conn:
        n = (conn.execute("select coalesce(max(n), 0) from helper_messages where session_id = ?", (sid,)).fetchone()[0] or 0) + 1
        conn.execute("insert into helper_messages (session_id, n, role, text, call_id, data_json, at) values (?, ?, ?, ?, ?, ?, ?)",
                     (sid, n, role, text, call_id, json.dumps(data or {}), db.now()))
        conn.execute("update helper_sessions set updated_at = ? where id = ?", (db.now(), sid))
    return n


def history(sid: str, before_n: int | None = None) -> list[tuple[str, str]]:
    """Earlier messages as [(user|assistant, text)], oldest first."""
    msgs = get(sid)["messages"]
    out = [("user" if m["role"] == "user" else "assistant", m["text"]) for m in msgs
           if m["role"] in ("user", "helper") and (before_n is None or m["n"] < before_n)]
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
        line = f"{'Person' if role == 'user' else 'Helper'}: {text.strip()}"
        if used + len(line) > TRANSCRIPT_CHARS:
            break
        parts.append(line)
        used += len(line)
    return ("The conversation so far (oldest first):\n" + "\n\n".join(reversed(parts))) if parts else ""


def build_prompt(*, mode: str, root: str, question: str, know: dict, graph: bool, flow: dict | None,
                 mentions: list[dict] | None, selection: dict | None, open_file: str | None, transcript: str) -> str:
    parts = [MODE_TEXT[mode], f"The project folder: {root}"]
    block = agent_knowledge.prompt_block(root, know, graph)
    if block:
        parts.append(block)
    ctx = plugins.context_files(root)
    if ctx:
        parts.append("Read these when they help: " + ", ".join(ctx))
    for extra in (_flow_block(flow), transcript, _pointing(mentions or [], selection, open_file)):
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

    def busy(self, sid: str) -> bool:
        t = self.tasks.get(sid)
        return bool(t and not t.done())

    async def turn(self, sid: str, body: dict) -> dict:
        s = get(sid, messages=False)
        text = str(body.get("text") or "").strip()
        if not text:
            raise HelperError(400, "The message is empty.")
        if self.busy(sid):
            raise HelperError(409, "The Helper is still answering in this session.", "Wait for the answer, or stop it.")
        if body.get("model"):
            s = set_model(sid, body["model"])
        question, command = plugins.expand(s["root"], text)
        n = add_message(sid, "user", text, data={k: v for k, v in {"command": command, "mentions": body.get("mentions"),
                                                                    "selection": body.get("selection")}.items() if v})
        if s["title"] == "New chat" and s["turns"] == 0:
            update(sid, title=text.splitlines()[0][:80])
        call_id = uuid.uuid4().hex
        update(sid, status="running", error=None)
        self.tasks[sid] = asyncio.create_task(self._run(sid, n, question, body, call_id))
        return {"session": sid, "call_id": call_id, "n": n, "command": command}

    async def stop(self, sid: str) -> dict:
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
        project, root, mode = s["project"], s["root"], s["mode"]
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
        toolbox = ToolBox(root, "none", cfg=cfg, on_refuse=on_refuse, agent=AGENT, knowledge=know, readonly=mode == "ask")
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
            graph = await asyncio.to_thread(mcp.codegraph_server_spec, root)
            sent = list(body.get("mcp") or [])
            specs = sent + ([graph] if graph and not any(x.get("name") == graph["name"] for x in sent) else [])
            allow = list(body.get("tools_allow") or []) + ([f"mcp:{graph['name']}:*"] if graph else [])
            mcp_specs, tools_allow = agent_knowledge.filter_mcp(specs, allow, know)
            transcript = "" if (resume or model.get("mode") == "api" or provider == "fake") else _transcript(hist)
            prompt = build_prompt(mode=mode, root=root, question=question, know=know,
                                  graph=agent_knowledge.has_codegraph(mcp_specs, tools_allow), flow=body.get("flow"),
                                  mentions=body.get("mentions"), selection=body.get("selection"), open_file=body.get("open_file"),
                                  transcript=transcript)
            with tempfile.TemporaryDirectory(prefix="keel-helper-") as tmp:
                req = AgentRequest(agent=AGENT, system=prompts.system_prompt(AGENT, body.get("skills")), prompt=prompt,
                                   root=root, phase="none", model=model, toolbox=toolbox, title=s["title"], step_name="helper",
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
        # the backstop for engines without keel's hook: in Ask nothing may change, whatever the engine did
        put_back = guard.guard_diff(root, "none", before, cfg, None, None, mode == "ask")
        for r in put_back:
            emit("guard", f"Put back {r['path']}: the Helper's Ask mode changes nothing.", path=r["path"], ok=False)
        used = getattr(res, "__dict__", {}) if res else {}
        tin, tout = int(used.get("tokens_in", 0)), int(used.get("tokens_out", 0))
        cached = int(used.get("tokens_cached", 0))
        cost = float(used.get("cost_usd", 0.0))
        if res and not cost and model.get("mode") == "api" and provider != "fake":
            cost = catalog.cost_usd(provider, model.get("model", ""), tin, tout)
        answer = (res.text if res else "") or ("" if status == "done" else err or "")
        if status == "done" and not answer.strip():
            answer = "(The Helper gave no answer.)"
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
