"""KeelBot (plugins/keelbot): chat sessions in the Code page, run by keel's own harness under keel's rules.

A session belongs to one project and one mode. One turn is one agent run, shaped like a flow's agent step
(runtime/compiler.py `_run_agent`): the model's runner (claude, codex, copilot / opencode, an API key or the fake
model), keel's guarded tools (ToolBox), the MCP servers KeelBot may use plus the code graph, the guard context
the hook reads on every tool call, and the diff guard as the backstop for engines without a hook.

    modes   ask   read only: no edit, no new file, no command that changes files or git (the guard's readonly)
            fix   while the project's flow waits at a gate: edits under the flow's phase rules and unlocks; a command
                  that changes something waits for the person's OK (runtime/permissions.py); keel remembers each file
                  as it was before KeelBot's first change (Undo), and Done runs the checks and makes keel's commit
            edit  any time, in the project folder itself (keel 0.18.7): edits under keel's always-on rules only (secrets,
                  .git, old migrations; no flow's phase), commands that change something (git checkout, a build) asked
                  as in Fix; keel remembers each file as it was (Undo) and Keep runs the checks and makes keel's commit
            side  any time, in its own copy of the project (a git worktree on branch keel/helper/<id>, tools/worktrees.py):
                  free edits there and nowhere else, commands asked as in Fix; Keep runs the checks and commits on that
                  branch; the person hands it over (a task, a change flow on the branch) or throws it away

How a session continues: claude and codex continue their own CLI session (cheap: their context stays cached); the
API-key runner gets the earlier messages; the other CLIs get the conversation so far in the prompt.

Events (thread_id = the session id, step "helper"): helper.started, helper.step, helper.finished. The api stores
them as agent calls (agent "helper", so the budget, Live agents and Jobs count them) but never as a flow. Live, never
stored: helper.delta (the answer so far), 0.18.23 helper.thinking.delta (the model's thinking so far) and
helper.tool (each tool call as one record: name, args, status, summary, output; keel TUI's tool lines).

The parts: chat/sessions.py (sessions, messages, folders), chat/undo.py (the files KeelBot changed, Undo, Done),
chat/prompt.py (the prompt) and chat/toolevents.py (helper.tool). This module is what the routes and the tests
import: it re-exports every name of them, and holds /compact and the runner.

Its sessions live in the engine DB's helper_sessions, helper_messages and helper_files: core's runtime/migrate.py
still makes them (the old tables stay core). Its routes are routes.py; its runner starts and stops with keel's app
(the part's lifespan, __init__.py).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import tempfile
import time
import uuid
from pathlib import Path

from keel_engine import approvals, config, extensions, models, rules
from keel_engine.models import catalog
from keel_engine.models.base import AgentRequest, AgentResult
try:                                    # keel 0.18.2: live text (an older core has no deltas: KeelBot drops them)
    from keel_engine.models.stream import DeltaThrottle
except ImportError:                     # pragma: no cover
    class DeltaThrottle:                # type: ignore[no-redef]
        def __init__(self, send, *a, **k): pass
        def add(self, chunk): pass
        def flush(self): pass
        def reset(self): pass
from keel_engine.runtime.blockers import for_agents as push_blockers_for
from keel_engine.runtime import agent_knowledge, db, doc_history, guard_ctx, permissions, plugins, prompts, repeats
from keel_engine.tools import guard
from keel_engine.tools.agent_tools import ToolBox
try:                                    # keel 0.18.57: the ref an answer was made from (an older core: links stay plain)
    from keel_engine.tools import source_ref
except ImportError:                     # pragma: no cover
    source_ref = None                   # type: ignore[assignment]

from . import ASK_PATH as PART_ASK_PATH

from .chat.sessions import (  # noqa: F401 - re-exported: the routes and the tests reach them through helper
    add_grant, add_message, AGENT, create, create_folder, delete, delete_folder, earlier_prompts, export, EXPORT_DIR,
    EXPORT_ROLES, FIELDS, fix_phase, FOLDER_FIELDS, FOLDER_NAME_MAX, folders, get, get_folder, HelperError,
    hide_title, history, is_side, list_sessions, MODE_NAMES, mode_note, MODE_NOW, MODES, preview, PROMPTS_MAX,
    rename_folder, set_folder, set_mode, set_model, short_title, split_title, SUMMARY_HEAD, SUMMARY_TAIL, TITLE_ASK,
    TITLE_CHARS, TITLE_WORDS, TURN_TABLE, update, workdir, _folder_name, _same_name, _session, _TITLE_LINE,
    _turn_table,
)
from .chat.undo import (  # noqa: F401 - re-exported: the routes and the tests reach them through helper
    changes, commits_for, DIFF_MAX, done, finalize_reverts, handover, record_turn, redo_turn, release,
    remember_originals, turn_changes, undo, undo_turn, _change, _changed_by_person, _drop_turn_paths,
    _edited_since_last_turn, _has_turn_files, _head_bytes, _mark, _now_bytes, _put, _text, _turn_files, _turns,
    _undoable,
)
from .chat.prompt import (  # noqa: F401 - re-exported: the routes and the tests reach them through helper
    attach_images, build_prompt, COMPACT_SHARE, COMPACT_WINDOW, FAILURES, flow_branches, flows_in_folder, IMAGE_EXT,
    IMAGE_MAX, IMAGES_MAX, last_failures, last_mode, MODE_TEXT, READING, sees_images, SHELL_OUTPUT_MAX, STYLE,
    summary_prompt, SUMMARY_RULES, SUMMARY_TEMPLATE, TRANSCRIPT_CHARS, _failed_block, _flow_block, _images_block,
    _pointing, _shell_block, _transcript,
)
from .chat import branch_facts, folder_branch
from .chat.toolevents import (  # noqa: F401 - re-exported: the routes and the tests reach them through helper
    commands, FILE_TOOL, SHELL_TOOLS, THINKING_MAX, tool_event, TOOL_KINDS, TOOL_OUTPUT_MAX, TOOL_SUMMARY_MAX,
    _first_line, _line_count, _MORE_LINES, _STAT, _thinking_data,
)

log = logging.getLogger(__name__)

RESUMABLE = {"claude", "codex", "copilot", "opencode"}   # CLIs that continue their own session (runtime/compiler.py RESUMABLE)
HISTORY_TURNS = 12                       # earlier messages an API-key model gets
TURN_TIMEOUT = 900
SHELL_TIMEOUT = 300                      # 0.18.18 a `!command` the person runs (keel TUI), once it may run
COMPACT_RATIO = 0.85                     # ctx_tokens / ctx_window above this: compact before the next turn runs
STEP_FIELD_MAX = 24_000
COMMITS = {          # keel's commit line in KeelBot's system prompt (runtime/prompts.py system_prompt), per mode
    "edit": ("keel commits your own file changes after its checks (when the person keeps them); never git commit them "
             "yourself, and never push. Only the person's own uncommitted work may be committed with git commit, when they "
             "choose that, and the command waits for their OK."),
}
# Ask mode's refusals in its own words (the guard said "this flow runs read-only", and KeelBot looked for a flow)
ASK_LABEL = "KeelBot's Ask mode is read only"
ASK_NOTE = ("You cannot switch the mode yourself and must not try another way around it. Say so in one line, give the "
            "exact command for the person to run, and offer the ways forward as a keel-questions block: switch to Edit "
            "(recommended; keel TUI `/mode edit`, the dashboard's mode menu), a side session (`/mode side`), or run it "
            "themselves.")
ASK_PATH = PART_ASK_PATH               # where the guard hook asks (it checks the turn's key, then asks approvals.py)


async def compact(sid: str, keys: dict | None = None) -> dict:
    """Asks the model to summarise the stored conversation (0.18.19 in opencode's structured form, or to update the
    earlier summary), stores that summary as a note (never a user or helper turn) and starts a fresh engine_session: the next turn's prompt begins from the summary, not the old session.
    Nothing in helper_messages is ever deleted; ctx_tokens resets to the summary's own size.
    keys: the logins the api sends with a turn (0.18.54: without them a Claude login saved in Connections was missing)."""
    keys = dict(keys or {})
    s = get(sid, messages=False)
    model = models.effective(s["model"])
    hist = history(sid)
    root = workdir(s)
    # 0.18.19 the whole chat, as far as the model's window allows (it was the newest 6000 characters), and only in the
    # prompt: an API-key model got it a second time as its messages
    room = max(TRANSCRIPT_CHARS, int((s.get("ctx_window") or COMPACT_WINDOW) * 4 * COMPACT_SHARE))
    earlier = bool(hist) and hist[0][1].startswith(SUMMARY_HEAD)
    prompt = _transcript(hist, room) + "\n\n" + summary_prompt(update=earlier)
    req = AgentRequest(agent=AGENT, system=prompts.system_prompt(AGENT, None), prompt=prompt, root=root, phase="none",
                       model=model, toolbox=ToolBox(root, "none", agent=AGENT, readonly=True), title=s["title"],
                       step_name="compact", key=models.key_for(model["provider"], keys), keys=keys, history=[])
    res = await models.runner_for(model).run(req, lambda *_a, **_k: None)
    tin, tout, cached = int(res.tokens_in), int(res.tokens_out), int(res.tokens_cached)
    before = int(s.get("ctx_tokens") or 0)
    summary = res.text or ""
    after = max(tout, len(summary) // 4)          # what the next turn starts with: the summary, not the old chat
    add_message(sid, "note", summary, data={"compact": True, "before": before, "after": after,
                                            "tokens_in": tin, "tokens_out": tout, "tokens_cached": cached})
    # no engine session: the next turn starts a new one and gets history() — the summary and what follows — as its
    # transcript (a made-up session id would "resume" a session the engine never had, with no transcript at all)
    update(sid, engine_session=None, ctx_tokens=after)
    return get(sid, messages=False)


# ------------------------------------------------------------------ turns

def said_no(command: str, ans: dict) -> dict:
    """keel 0.18.18 the person's deny, as KeelBot reads it: their reason, and to go on from it (not to ask again)."""
    if ans.get("decision") == "allow":
        return ans
    why = str(ans.get("why") or "").strip() or approvals.DENY_WHY
    if why in (approvals.DENY_WHY, approvals.TIMEOUT_WHY) or why.startswith(("The person stopped", "KeelBot's answer")):
        note = why
    else:
        note = f"The person said no to this command: {why}"
    return {**ans, "why": f"{note} Do not run it again; go on from what they said, or offer other ways forward."}



class HelperRunner:
    """Runs turns in the background (one at a time per session) and stops them."""

    def __init__(self, bus):
        self.bus = bus
        self.tasks: dict[str, asyncio.Task] = {}
        # Fix and side: the running turn's ask key per session (it can only ask). The waiting itself is core's
        # (keel_engine/approvals.py); KeelBot keeps its keys, its "always" grants and the cards of its own panel.
        self.ask_keys: dict[str, str] = {}
        self.approvals = approvals.of(bus)
        self.commands: set[str] = set()          # 0.18.18 sessions whose person's `!command` runs now
        # a turn that was accepted but has no task yet (it awaits a compact first), and a Done that runs the checks and
        # commits: both make the chat busy, so a second message queues instead of starting a second answer
        self.starting: set[str] = set()
        self.committing: set[str] = set()
        # keel 0.18.18 the running turn's view of keel per session (its flows): the cards warn when a flow works in the
        # folder, and a flow's own branch is not KeelBot's to check out
        self.turn_keel: dict[str, dict] = {}
        # 0.18.28 messages the person sent while an answer ran: sent in order once it ends (or at once after a stop)
        self.queued: dict[str, list[dict]] = {}

    # ---- permission cards (runtime/permissions.py) --------------------------------------------------------------

    def _check(self, sid: str, key: str, command: str) -> tuple[dict | None, dict | None]:
        """(the session that asks, or an answer at once)."""
        if not key or self.ask_keys.get(sid) != key:
            return None, {"decision": "deny", "why": "This KeelBot turn may not ask (it ended, or the key is wrong)."}
        try:
            s = get(sid, messages=False)
        except HelperError:
            return None, {"decision": "deny", "why": "The KeelBot chat is gone."}
        branch = self._flow_branch(sid, command)
        if branch:
            return None, {"decision": "deny", "why": f"{branch} is the branch of a keel flow that runs or waits: the flow "
                          "owns it (in its own worktree), so KeelBot does not check it out or change it. Offer the person "
                          "another way (a side session, or wait until the flow is finished)."}
        if permissions.granted(command, s["grants"]):
            return None, {"decision": "allow"}
        return s, None

    def _flow_branch(self, sid: str, command: str) -> str:
        """The flow branch this command checks out, switches to, resets or deletes, else ""."""
        if not permissions.changes_tree(command) and " branch " not in f" {command} ":
            return ""
        words = set(str(command).replace("=", " ").split())
        return next((b for b in sorted(flow_branches(self.turn_keel.get(sid))) if b in words), "")

    def _warning(self, s: dict, command: str) -> str:
        """What the person should know before they say yes: the command throws work away, or a flow works in this
        folder and the command changes its files."""
        out = [permissions.destructive(command)]
        if s["mode"] in ("edit", "fix") and permissions.changes_tree(command):
            busy = flows_in_folder(self.turn_keel.get(s["id"]))
            if busy:
                out.append(f"A flow works in this folder (\"{busy[0].get('title')}\" {busy[0].get('status')}): this "
                           "changes the files it works on.")
        return " ".join(w for w in out if w)

    def _open(self, s: dict, kind: str, command: str, path: str = "") -> str:
        """Asks approvals; KeelBot's own panel gets helper.permission, and helper.permission.answered when it ends."""
        sid = s["id"]

        def answered(q: dict, said: str, ans: dict):
            if said == "always" and not permissions.destructive(q["command"]):     # one that throws work away: every time
                add_grant(sid, q.get("always") or q["command"])
            self.bus.emit("helper.permission.answered", sid, q["project"], step="helper",
                          data={"id": q["id"], "decision": ans.get("decision"), "why": ans.get("why", "")})

        q = self.approvals.open(kind, s["project"], s["title"], command, source="keelbot", thread_id=s["thread_id"],
                                session=sid, path=path, on_answer=answered,
                                extra={"warning": self._warning(s, command),
                                       # what "always" keeps; none for a command that throws work away (asked every time)
                                       "always": "" if permissions.destructive(command) else permissions.always_pattern(command)})
        self.bus.emit("helper.permission", sid, s["project"], step="helper", data=q)
        return q["id"]

    async def ask(self, sid: str, key: str, kind: str, command: str, path: str = "") -> dict:
        """The hook's question (POST /helper/permissions/ask): waits for the person, at most ASK_TIMEOUT."""
        s, now = self._check(sid, key, command)
        if now:
            return now
        return said_no(command, await self.approvals.wait(self._open(s, kind, command, path)))

    def ask_blocking(self, sid: str, key: str, command: str) -> tuple[bool, str]:
        """The ToolBox's question (API-key models run their tools in a worker thread)."""
        s, now = self._check(sid, key, command)
        ans = now or said_no(command, self.approvals.wait_blocking(self._open(s, "command", command)))
        return ans.get("decision") == "allow", ans.get("why", "")

    def answer(self, qid: str, decision: str, why: str = "") -> dict:
        """The person's answer: once | always (this command, for the rest of the chat) | deny (with a reason)."""
        try:
            return self.approvals.answer(qid, decision, why)
        except approvals.ApprovalError as exc:
            raise HelperError(exc.status, str(exc), exc.hint) from exc

    def pending(self, project: str | None = None) -> list[dict]:
        return self.approvals.pending(project)

    def _drop_questions(self, sid: str, why: str):
        self.approvals.close(session=sid, why=why)

    def busy(self, sid: str) -> bool:
        t = self.tasks.get(sid)
        return bool(t and not t.done()) or sid in self.commands or sid in self.starting or sid in self.committing

    async def run_command(self, sid: str, command: str, timeout: int = SHELL_TIMEOUT) -> dict:
        """0.18.18 keel TUI's `!command`: the person's own command, in the chat's folder (a side session's worktree),
        through keel's guard as KeelBot's own commands go (ToolBox.run_command): the rules first, then in Edit, Fix and
        side a command that changes something waits for the person's OK on a permission card; Ask runs only what
        changes nothing. Answers {command, exit, output, refused}; nothing is stored in the chat."""
        s = get(sid, messages=False)
        cmd = (command or "").strip()
        if self.busy(sid):
            raise HelperError(409, "KeelBot is still working in this chat.", "Wait for it, or stop it, then run the command.")
        root, mode = workdir(s), s["mode"]
        cfg = rules.load_config(root)
        refused: list[str] = []

        def on_refuse(tool: str, path: str, reason: str, command: str | None = None):
            refused.append(reason)

        if mode == "ask":
            toolbox = ToolBox(root, "none", cfg=cfg, on_refuse=on_refuse, agent=AGENT, readonly=True)
        else:
            key = secrets.token_urlsafe(24)
            self.ask_keys[sid] = key
            toolbox = ToolBox(root, (s.get("phase") or "none") if mode == "fix" else "none", cfg=cfg, on_refuse=on_refuse,
                              agent=AGENT, confine=mode in ("side", "edit"),
                              asker=lambda c: self.ask_blocking(sid, key, c))
        self.commands.add(sid)
        try:
            said = await asyncio.to_thread(toolbox.run_command, cmd, timeout)
        finally:
            self.commands.discard(sid)
            self.ask_keys.pop(sid, None)
            self._drop_questions(sid, "The command ended.")
        if said.startswith("REFUSED:"):
            why = refused[-1] if refused else said[len("REFUSED:"):].strip()
            if mode == "ask":
                why = (f"{why} KeelBot's Ask mode is read only: /mode edit runs it (a command that changes something "
                       "waits for your OK), or run it in your own terminal.")
            return {"command": cmd, "exit": None, "output": "", "refused": why}
        head, _, out = said.partition("\n")
        code = int(head.split()[1]) if head.startswith("exit ") and head.split()[1].lstrip("-").isdigit() else None
        return {"command": cmd, "exit": code, "output": out, "refused": None}

    async def turn(self, sid: str, body: dict) -> dict:
        s = get(sid, messages=False)
        text = str(body.get("text") or "").strip()
        if not text:
            raise HelperError(400, "The message is empty.")
        if self.busy(sid) and body.get("interrupt"):
            # 0.18.28 Esc / Ctrl+C with a new message: the running answer stops, this one goes now
            await self.stop(sid)
        if self.busy(sid):
            # 0.18.28 no "still answering" refusal: the message waits and goes after this answer
            q = self.queued.setdefault(sid, [])
            q.append({k: v for k, v in body.items() if k != "interrupt"})
            self.bus.emit("helper.queued", sid, s["project"], step="helper",
                          data={"session": sid, "text": text[:2000], "position": len(q)})
            return {"session": sid, "queued": True, "position": len(q), "text": text}
        self.starting.add(sid)      # busy from here: nothing below may let a second message start its own answer
        try:
            if s["mode"] == "side" and not s.get("worktree"):
                raise HelperError(409, "This side session was handed over; its worktree is gone.", "Start a new chat.")
            if finalize_reverts(sid):         # 0.18.19 a new message makes /undo final (no /redo any more)
                s = get(sid, messages=False)
            if s["ctx_window"] and s["ctx_tokens"] / s["ctx_window"] >= COMPACT_RATIO:
                # 0.18.54 with the turn's logins; a summary that fails is a note, and the answer still goes (it was a
                # 500 the TUI showed as "The engine refused the request")
                try:
                    s = await compact(sid, body.get("keys"))
                except models.ModelError as exc:
                    add_message(sid, "note", f"Could not compact this chat: {exc}", data={"compact_failed": True})
            if body.get("model"):
                s = set_model(sid, body["model"])
            question, command = plugins.expand(s["root"], text, body.get("plugins"))
            # 0.18.18 the images the person named: the ones the model can see go with the turn, the others say why not
            att = attach_images(s, models.effective(s["model"]), list(body.get("images") or []))
            body["images_ok"] = att["images"]
            n = add_message(sid, "user", text, data={k: v for k, v in {"command": command, "mentions": body.get("mentions"),
                                                                        "selection": body.get("selection"),
                                                                        "shell": body.get("shell"), "images": att["images"],
                                                                        "images_skipped": att["skipped"]}.items() if v})
            if s["title"] == "New chat" and s["turns"] == 0:
                # named by the question until the first answer gives its own short title (0.18.19, _run)
                body["auto_title"] = update(sid, title=short_title(text))["title"]
            call_id = uuid.uuid4().hex
            update(sid, status="running", error=None)
            self.tasks[sid] = asyncio.create_task(self._run(sid, n, question, body, call_id))
            return {"session": sid, "call_id": call_id, "n": n, "command": command, "images": att["images"],
                    "images_skipped": att["skipped"]}
        finally:
            self.starting.discard(sid)

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

        # the answer as it is written: helper.delta events (the text so far, every 80 ms; never stored)
        # 0.18.19 a new chat's first answer ends with its title line: the person never sees it
        auto = str(body.get("auto_title") or "")
        live = DeltaThrottle(lambda d: ev("helper.delta", {**d, "text": hide_title(d.get("text", ""))} if auto else d))
        # 0.18.23 KeelBot's thinking as the model writes it: helper.thinking.delta (the turn's thinking so far, every
        # 80 ms; a live event the api never stores), apart from the answer. Kept with the answer's data for a reload
        # (capped), never in the conversation the model gets next turn (history() reads the text only).
        think = DeltaThrottle(lambda d: ev("helper.thinking.delta", d))
        thought: dict = {"text": "", "first": None, "last": None, "live": False, "gap": False}

        def add_thought(chunk: str):
            if not chunk:
                return
            now = time.monotonic()
            thought["first"] = thought["first"] or now
            thought["last"] = now
            if thought["gap"] and thought["text"]:
                chunk = "\n\n" + chunk.lstrip()
            thought["gap"] = False
            thought["text"] += chunk
            think.add(chunk)
        # keel 0.18.18 what failed or was refused in this turn: the next turn's prompt starts from it, and failed.json
        # (runtime/repeats.py, in the turn's scratch folder) lets the hook and the ToolBox refuse an exact repeat
        failures: list[dict] = []
        scratch: dict = {"dir": None}
        # 0.18.57 what the answer is made from: the branch (or commit) of the folder it works in, and the files it read at
        # another ref (`git show <ref>:<path>`); stored with the answer, its file:line links open the file there
        source: dict = {}
        read_at: dict[str, str] = {}

        def note_failure(kind: str, text: str, extra: dict):
            if extra.get("ok") is not False:
                return
            if kind == "tool" and extra.get("tool") in ("Bash", "run_command") and text:
                note = repeats.remember(scratch["dir"], root, str(text), str(extra.get("output") or ""))
                failures.append({"command": str(text)[:300], "said": note.get("said", ""), "state": note.get("state", "")})
            elif kind == "guard" and text:
                failures.append({"command": str(extra.get("command") or "")[:300], "said": str(text)[:300]})

        def emit(kind: str, text: str = "", **extra):
            if kind == "delta":
                think.flush()
                live.add(str(text))
                return
            if kind == "thinking_delta":
                thought["live"] = True
                add_thought(str(text))
                return
            if kind == "thinking" and not thought["live"]:
                add_thought(str(text))           # an engine that sends whole thinking only (Codex, opencode)
                think.flush()
            elif kind != "thinking":
                thought["gap"] = True            # a tool call or a message between: the next thinking is a new block
            live.reset()
            try:
                note_failure(kind, str(text), extra)
            except Exception:  # noqa: BLE001  (a note for the next turn: never the turn's failure)
                log.debug("KeelBot could not note a failure", exc_info=True)
            counter["n"] += 1
            if auto and kind in ("text", "answer"):
                text = split_title(str(text))[0]
            data = {"n": counter["n"], "kind": kind, "text": str(text)[:STEP_FIELD_MAX]}
            data.update({k: (v[:STEP_FIELD_MAX] if isinstance(v, str) else v) for k, v in extra.items() if v is not None})
            ev("helper.step", data)
            # the same tool call as one tidy record for keel TUI's tool lines (a live event: never stored,
            # never in the next turn's history)
            if kind in TOOL_KINDS:
                ev("helper.tool", tool_event(counter["n"], kind, str(text), extra))
                if source_ref is not None and kind == "tool":
                    try:
                        read_at.update(source_ref.read_refs(str(extra.get("command") or text)))
                    except Exception:  # noqa: BLE001  (where a file was read from: never the turn's failure)
                        log.debug("KeelBot could not read the refs of a command", exc_info=True)

        def on_refuse(tool: str, path: str, reason: str, command: str | None = None):
            emit("guard", reason, tool=tool, path=path or None, ok=False, command=command)

        # v0.15.9 keel's file history: what KeelBot changes in specs, knowledge, ADRs or the runbook
        def doc_emit(type_: str, step: str | None = None, data: dict | None = None):
            self.bus.emit(type_, sid, project, step=step or "helper", call_id=call_id, data=data or {})

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
                              ask={"url": f"http://127.0.0.1:{config.port()}{ASK_PATH}", "key": key, "session": sid},
                              asker=lambda command: self.ask_blocking(sid, key, command))
        elif mode in ("side", "edit"):
            # no flow's phase: only keel's always-on rules (secrets, .git, old migrations), and nothing outside the worktree
            # (side) or the project folder (edit); a command that changes something waits for the person's OK
            phase, unlocks, ac = "none", [], None
            key = secrets.token_urlsafe(24)
            self.ask_keys[sid] = key
            toolbox = ToolBox(root, "none", cfg=cfg, on_refuse=on_refuse, agent=AGENT, knowledge=know, confine=True,
                              ask={"url": f"http://127.0.0.1:{config.port()}{ASK_PATH}", "key": key, "session": sid},
                              asker=lambda command: self.ask_blocking(sid, key, command))
        else:
            phase, unlocks, ac = "none", [], None
            toolbox = ToolBox(root, "none", cfg=cfg, on_refuse=on_refuse, agent=AGENT, knowledge=know, readonly=True)
            toolbox.readonly_label, toolbox.readonly_note = ASK_LABEL, ASK_NOTE
        self.turn_keel[sid] = dict(body.get("keel") or {})
        resumable = model.get("mode") != "api" and provider in RESUMABLE
        session = s["engine_session"] if resumable else None
        resume = bool(session)
        if resumable and not session and provider in ("claude", "copilot"):
            session = str(uuid.uuid4())
        hist: list = []
        res: AgentResult | None = None
        status, err = "done", None
        before = None
        plugin_key = None
        t0 = time.monotonic()
        effort = catalog.effort_for(model)       # what the runner passes on (None: the engine's own default)
        docs_before: dict | None = None      # None: the turn failed before the setup read the docs
        read_branch, branch_moved = "", None    # 0.18.57 the folder's branch this answer reads, and a switch to show

        def on_session(new: str):
            update(sid, engine_session=new)

        # everything that awaits sits in the try: a stop can come at any moment and is recorded the same way
        try:
            # the setup sits in the try as well: an error or a Stop here must end the turn the same way (status, keys)
            if mode in ("fix", "side", "edit"):
                # keel v1's push gate for KeelBot's own `git push` / `gh pr create` (rules.push_gate)
                toolbox.push_blockers = await asyncio.to_thread(push_blockers_for, root, flow.get("base_head"), project)
            hist = history(sid, before_n=n)
            ev("helper.started", {"agent": AGENT, "provider": provider, "model": model.get("model"), "mode": model.get("mode"),
                                  "effort": effort,
                                  "phase": f"helper-{mode}", "session": sid, "n": n})
            docs_before = doc_history.before_step(doc_emit, root, None, "helper")
            before = await asyncio.to_thread(guard.snapshot, root)
            if source_ref is not None:
                source = await asyncio.to_thread(source_ref.checkout_ref, root)
            # the parts' own servers (the code graph's: the project's index, same paths), keel_engine/extensions.py
            specs, allow = await asyncio.to_thread(extensions.with_part_servers, list(body.get("mcp") or []),
                                                   list(body.get("tools_allow") or []), extensions.Agent(AGENT, know),
                                                   s["root"])
            # the project's plugins (Control › Plugins): their read tools, through one key for this turn
            on = extensions.enabled({"plugins": body.get("plugins")})
            if on:
                plugin_key = extensions.open_call(project=project, root=root, keys=keys, plugins=on, who="keelbot",
                                                  bus=self.approvals.bus, thread=s.get("thread_id"), session=sid)
                specs += extensions.server_specs(on, plugin_key)
                allow += extensions.allow_entries(on)
            mcp_specs, tools_allow = agent_knowledge.filter_mcp(specs, allow, know)
            before_failed = await asyncio.to_thread(last_failures, sid, n)
            was = await asyncio.to_thread(last_mode, sid, n)
            # 0.18.57 the branch the project folder is on now, and whether a flow moved it since the last answer
            if folder_branch.watched(s):
                read_branch = await asyncio.to_thread(folder_branch.current, root)
                seen = await asyncio.to_thread(folder_branch.last_seen, sid, n)
                branch_moved = await asyncio.to_thread(folder_branch.switch, root, seen, read_branch, body.get("keel"))
                if branch_moved:
                    ev("helper.branch", {**branch_moved, "session": sid, "n": n})
            # 0.18.62 "what does this branch do?": the branch's exact git facts and the answer's order
            facts = await asyncio.to_thread(branch_facts.facts, root) if branch_facts.asks_about_branch(question) else None
            transcript = "" if (resume or model.get("mode") == "api" or provider == "fake") else _transcript(hist)
            prompt = build_prompt(mode=mode, root=root, question=question, know=know, branch=s.get("branch") or "", pid=project,
                                  graph=agent_knowledge.has_codegraph(mcp_specs, tools_allow), flow=body.get("flow"),
                                  mentions=body.get("mentions"), selection=body.get("selection"), open_file=body.get("open_file"),
                                  transcript=transcript, keel={**(body.get("keel") or {}), "plugins": on},
                                  shell=body.get("shell"), images=body.get("images_ok"), failed=before_failed, was=was,
                                  title=bool(auto), folder=folder_branch.prompt_lines(read_branch, branch_moved)
                                  + branch_facts.prompt_lines(facts))
            with tempfile.TemporaryDirectory(prefix="keel-helper-") as tmp:
                scratch["dir"] = tmp
                toolbox.no_repeat = tmp
                repeats.seed(tmp, before_failed)        # "fix it" with nothing changed: the same command stays refused
                req = AgentRequest(agent=AGENT, system=prompts.system_prompt(AGENT, body.get("skills"), COMMITS.get(mode)),
                                   prompt=prompt,
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
        think.flush()
        self.ask_keys.pop(sid, None)
        self.turn_keel.pop(sid, None)
        scratch["dir"] = None
        extensions.close_call(plugin_key)
        self._drop_questions(sid, "KeelBot's answer ended.")
        # the backstop for engines without keel's hook: in Ask nothing may change; in Fix only what the phase allows
        if mode == "ask":
            for r in guard.guard_diff(root, "none", before, cfg, None, None, True):
                emit("guard", f"Put back {r['path']}: KeelBot's Ask mode changes nothing.", path=r["path"], ok=False)
        elif mode in ("side", "edit"):
            for r in guard.guard_diff(root, "none", before, cfg, None, None, False):
                emit("guard", f"Put back {r['path']}: {r['reason']}", path=r["path"], ok=False)
            if mode == "edit":
                remember_originals(sid, root, before)          # Undo and Keep, as in Fix
        else:
            for r in guard.guard_diff(root, phase, before, cfg, rules.ac_lane(ac) if ac else None, unlocks,
                                      flow.get("run_mode") == "readonly"):
                emit("guard", f"Put back {r['path']}: {r['reason']}", path=r["path"], ok=False)
            remember_originals(sid, root, before)
        turn_files: list[str] = []
        if mode != "ask":
            try:                                               # 0.18.19 this turn's files, for /undo of the whole answer
                turn_files = record_turn(sid, n, root, before)
            except Exception:  # noqa: BLE001  (a record for /undo: never the turn's failure)
                log.warning("KeelBot could not record turn %s of %s for /undo", n, sid, exc_info=True)
        used = getattr(res, "__dict__", {}) if res else {}
        tin, tout = int(used.get("tokens_in", 0)), int(used.get("tokens_out", 0))
        cached = int(used.get("tokens_cached", 0))
        cost = float(used.get("cost_usd", 0.0))
        if res and not cost and model.get("mode") == "api" and provider != "fake":
            cost = catalog.cost_usd(provider, model.get("model", ""), tin, tout)
        if res and not cost and int(used.get("premium_requests", 0)):
            # 0.18.16 Copilot costs premium requests, not tokens (it showed $0 before)
            cost = catalog.premium_cost(provider, model.get("model", ""), int(used.get("premium_requests", 0)))
        answer = (res.text if res else "") or ("" if status == "done" else err or "")
        if read_branch:
            # 0.18.57 the folder moved while this answer ran: the answer may mix both branches (the next turn says so)
            now_branch = await asyncio.to_thread(folder_branch.current, root)
            if now_branch and now_branch != read_branch:
                during = await asyncio.to_thread(folder_branch.switch, root, read_branch, now_branch, body.get("keel"))
                branch_moved = {**(during or {}), "during": True}
                ev("helper.branch", {**branch_moved, "session": sid, "n": n})
        named = ""
        if auto and status == "done":
            answer, named = split_title(answer)
        if status == "done" and not answer.strip():
            answer = "(KeelBot gave no answer.)"
        if named and get(sid, messages=False)["title"] == auto:
            update(sid, title=named)            # before the answer is stored: keel TUI reads the chat once it is
        add_message(sid, "helper" if status == "done" else "note", answer, call_id=call_id,
                    data={"status": status, "mode": mode, "provider": provider, "model": model.get("model"),
                          **({"effort": effort} if effort else {}), "tokens_in": tin, "tokens_out": tout,
                          "tokens_cached": cached, "cost_usd": round(cost, 6), "ms": int((time.monotonic() - t0) * 1000),
                          # 0.18.20 the files this answer changed (keel TUI lists only these after it), and its turn
                          "turn": n, "files": turn_files,
                          **({"source": {**source, **({"paths": dict(read_at)} if read_at else {})}} if source or read_at else {}),
                          **({"failed": failures[-10:]} if failures else {}),
                          # 0.18.57 the branch this answer read, and the switch the chat's banner shows
                          **({"folder_branch": read_branch} if read_branch else {}),
                          **({"branch_switch": branch_moved} if branch_moved else {}),
                          **_thinking_data(thought, t0)})
        cur = get(sid, messages=False)
        update(sid, status="idle" if status != "failed" else "failed", error=err if status == "failed" else None,
               tokens_in=cur["tokens_in"] + tin, tokens_out=cur["tokens_out"] + tout, tokens_cached=cur["tokens_cached"] + cached,
               cost_usd=round(cur["cost_usd"] + cost, 6), turns=cur["turns"] + 1, ctx_tokens=tin + tout + cached)
        if docs_before is not None:
            doc_history.after_step(doc_emit, root, docs_before, "keelbot", "KeelBot", None, "helper")
        ev("helper.finished", {"agent": AGENT, "status": status, "tokens_in": tin, "tokens_out": tout, "tokens_cached": cached,
                               "cost_usd": round(cost, 6), "premium_requests": int(used.get("premium_requests", 0)),
                               "result": answer[:2000], "session": sid, "n": n, "files": turn_files})
        self.tasks.pop(sid, None)
        self.send_queued(sid)

    def send_queued(self, sid: str) -> None:
        """0.18.28 the next message the person sent while the answer ran (in order), now that it ended."""
        q = self.queued.get(sid) or []
        if not q:
            self.queued.pop(sid, None)
            return
        nxt = q.pop(0)

        async def go():
            try:
                await self.turn(sid, nxt)
            except Exception:  # noqa: BLE001 - a queued message that cannot go is logged, the chat stays usable
                log.warning("KeelBot could not send the queued message of %s", sid, exc_info=True)

        asyncio.get_running_loop().create_task(go())

    def queue_of(self, sid: str) -> list[str]:
        return [str(b.get("text") or "") for b in self.queued.get(sid) or []]

    async def flow_fix(self, *, project: str, root: str, model: dict | None, keys: dict | None, text: str,
                       thread: str | None = None, title: str = "") -> dict:
        """0.18.28 a flow's gate asks KeelBot to fix what it cannot (the suite fails at integration): a new Edit chat in
        the flow's folder, its turn waited for. The person sees it among KeelBot's chats. {session, status, answer}."""
        sess = create(project, root, mode="edit", model=model, title=title or "Fix the failing suite")
        sid = sess["id"]
        await self.turn(sid, {"text": text, "keys": dict(keys or {}), "flow": {"thread_id": thread} if thread else {}})
        task = self.tasks.get(sid)
        if task:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - the flow checks the suite again either way
                pass
        now = get(sid)
        last = next((m for m in reversed(now.get("messages") or []) if m.get("role") != "user"), None)
        return {"session": sid, "status": (last or {}).get("data", {}).get("status") or now.get("status"),
                "answer": (last or {}).get("text") or ""}

