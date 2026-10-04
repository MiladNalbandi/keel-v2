"""Subscription-mode runners: claude, codex, copilot (Copilot CLI) and opencode.

Each turns its CLI's stream into steps, like Yegi's activity.py: text, thinking, tool, write, edit,
result, answer. CLI agents edit files directly, so the runtime's diff guard is what enforces the
phase rules for them.
"""

from __future__ import annotations

import asyncio
import json
import uuid
import dataclasses
import re
import shutil
import subprocess
import time
from pathlib import Path

from .. import config
from ..tools import git, mcp
from ..tools.agent_tools import unified_diff
from .base import AgentRequest, AgentResult, Emit, ModelError
from ..runtime import prompts
from .cli import result_usage, claude_login_env, codex_login_env, copilot_login_env, find, run_cli, safe_env

# keel v1's PreToolUse hook exits 2 with "keel: <reason>" on stderr; Claude Code returns that to the
# model as an error tool_result naming the hook.
HOOK_REFUSAL = re.compile(r"keel:\s*(.+)", re.S)


def hook_refusal(text: str) -> str | None:
    low = text.lower()
    if "hook" not in low and "blocked" not in low:
        return None
    m = HOOK_REFUSAL.search(text)
    return " ".join(m.group(1).split())[:600] if m else None

CLAUDE_TOOLS = ["Read", "Edit", "Write", "Bash", "Glob", "Grep"]
# Taken out of the model's tool list, so it does not spend turns on calls that print mode refuses anyway:
# no skills are installed for the agent, sub-agents would hide their tokens and edits, the web is not part of a step.
CLAUDE_HIDDEN = ["Skill", "Task", "Agent", "WebFetch", "WebSearch", "ToolSearch",
                 "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskOutput", "TaskStop"]

# Step sizes: the event bus and the web stay small and fast.
FIELD_MAX = 20_000
READ_LINES = 400
OUTPUT_LINES = 300
BEFORE_MAX = 2_000_000          # files bigger than this are not read for a diff

# Line-number prefixes of file views: Claude's Read ("     1→" or "1\t"), opencode's read ("00001| ").
LINE_NO = re.compile(r"^ *\d+(?:→|\t|\| ?)")
REMINDER = re.compile(r"\s*<system-reminder>.*?</system-reminder>\s*", re.S)


def _short(text: str, n: int = 300) -> str:
    return " ".join(str(text or "").split())[:n]


def cap(text, n: int = FIELD_MAX) -> str:
    """Bound a step field, keeping newlines."""
    text = str(text or "")
    return text if len(text) <= n else text[:n] + f"\n… ({len(text) - n} more characters)"


def head_lines(text, n: int) -> str:
    lines = str(text or "").splitlines()
    out = "\n".join(lines[:n])
    if len(lines) > n:
        out += f"\n… ({len(lines) - n} more lines)"
    return cap(out)


def strip_line_numbers(text: str) -> str:
    """Drop `     1→` / `1\t` / `00001| ` prefixes when every line of the view has one."""
    text = REMINDER.sub("\n", str(text or "")).strip("\n")
    text = re.sub(r"^\s*<file>\n|\n\s*</file>.*$", "", text, flags=re.S)
    lines = text.split("\n")
    if lines and all(LINE_NO.match(line) for line in lines if line.strip()):
        lines = [LINE_NO.sub("", line, count=1) for line in lines]
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines)


def result_text(content) -> str:
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type", "text") == "text")
    return str(content or "")


def _rel(root: str, path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return path


def _full(root: str, path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path(root) / p


def read_file(root: str, path: str) -> str | None:
    try:
        f = _full(root, path)
        if not f.is_file() or f.stat().st_size > BEFORE_MAX:
            return None
        return f.read_text(errors="replace")
    except OSError:
        return None


def patch_diff(rel: str, hunks: list[dict], new_file: bool = False) -> str:
    """A unified diff from Claude Code's structuredPatch ([{oldStart, oldLines, newStart, newLines, lines}])."""
    out = [f"--- {'/dev/null' if new_file else 'a/' + rel}\n", f"+++ b/{rel}\n"]
    for h in hunks:
        out.append(f"@@ -{h.get('oldStart', 0)},{h.get('oldLines', 0)} +{h.get('newStart', 0)},{h.get('newLines', 0)} @@\n")
        out.extend(f"{line}\n" for line in h.get("lines") or [])
    return "".join(out)


def apply_edits(old: str, edits: list[dict]) -> str | None:
    new = old
    for e in edits:
        a, b = e.get("old_string", ""), e.get("new_string", "")
        if not a or a not in new:
            return None
        new = new.replace(a, b) if e.get("replace_all") else new.replace(a, b, 1)
    return new


def diff_stat(diff: str) -> str:
    add = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    rem = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return f"+{add} -{rem}"


def _edits(args: dict) -> list[dict]:
    if args.get("edits"):
        return [{"old_string": e.get("old_string") or e.get("oldString", ""), "new_string": e.get("new_string") or e.get("newString", ""),
                 "replace_all": e.get("replace_all") or e.get("replaceAll")} for e in args["edits"] if isinstance(e, dict)]
    return [{"old_string": args.get("old_string") or args.get("oldString", ""), "new_string": args.get("new_string") or args.get("newString", ""),
             "replace_all": args.get("replace_all") or args.get("replaceAll")}]


def _path_arg(args: dict) -> str:
    return str(args.get("file_path") or args.get("path") or args.get("filePath") or args.get("notebook_path") or "")


class Steps:
    """Turns one finished tool call into one step, as the contract says:

    read  -> path, text = file content (first 400 lines)
    write / edit -> path, diff = unified diff, text = one-line summary
    tool  -> tool (and server for MCP), text = the command or arguments, output = first 300 lines, ok, ms
    """

    def __init__(self, emit: Emit, root: str):
        self.emit, self.root = emit, root
        self.pending: dict[str, dict] = {}

    # -- paired streams (Claude): tool_use first, tool_result later
    def start(self, call_id: str, name: str, args: dict):
        args = args or {}
        call = {"name": name, "args": args, "t0": time.monotonic()}
        if name.lower() in ("write", "create", "edit", "multiedit", "str_replace") and _path_arg(args):
            call["before"] = read_file(self.root, _path_arg(args))   # the file as it was before the tool ran
        self.pending[call_id] = call

    def finish(self, call_id: str, output: str, ok: bool, structured=None) -> dict | None:
        call = self.pending.pop(call_id, None)
        if call is None:
            return None
        self.done(call["name"], call["args"], output, ok, ms_since(call["t0"]), before=call.get("before"),
                  structured=structured)
        return call

    def flush(self):
        """Tool calls that never got a result (the run ended or was cut): show what was asked."""
        for call_id in list(self.pending):
            call = self.pending.pop(call_id)
            self.done(call["name"], call["args"], None, True, ms_since(call["t0"]), before=call.get("before"))

    # -- one finished call
    def done(self, name: str, args: dict, output: str | None, ok: bool, ms: int | None = None, *, before: str | None = None,
             structured=None, diff: str | None = None):
        args = args or {}
        lname = name.lower()
        path = _path_arg(args)
        rel = _rel(self.root, path) if path else ""
        st = structured if isinstance(structured, dict) else {}
        if lname == "read" and path:
            if not ok:
                self.emit("read", head_lines(output, OUTPUT_LINES), path=rel, ok=False, ms=ms)
                return
            content = (st.get("file") or {}).get("content") if isinstance(st.get("file"), dict) else None
            text = content if isinstance(content, str) else strip_line_numbers(output or "")
            self.emit("read", head_lines(text, READ_LINES), path=rel, ok=True, ms=ms)
        elif lname in ("write", "create") and path:
            if not ok:
                self.emit("write", f"Write {rel} failed", path=rel, output=head_lines(output, OUTPUT_LINES), ok=False, ms=ms)
                return
            content = str(args.get("content", ""))
            if diff is None:
                if st.get("structuredPatch") and st.get("type") == "update":
                    diff = patch_diff(rel, st["structuredPatch"])
                else:
                    old = st.get("originalFile") if isinstance(st.get("originalFile"), str) else before
                    diff = unified_diff(rel, old or "", content)
            self.emit("write", f"Write {rel} ({diff_stat(diff)})", path=rel, diff=cap(diff), ok=True, ms=ms)
        elif lname in ("edit", "multiedit", "str_replace", "patch") and (path or diff):
            if not ok:
                self.emit("edit", f"Edit {rel} failed", path=rel, output=head_lines(output, OUTPUT_LINES), ok=False, ms=ms)
                return
            if diff is None and st.get("structuredPatch"):
                diff = patch_diff(rel, st["structuredPatch"])
            if diff is None:
                old = st.get("originalFile") if isinstance(st.get("originalFile"), str) else before
                new = apply_edits(old, _edits(args)) if old is not None else None
                if new is not None:
                    diff = unified_diff(rel, old, new)
                else:   # the file is gone or changed: show the replaced fragments
                    diff = "".join(unified_diff(rel, e["old_string"] or "\n", e["new_string"]) for e in _edits(args))
            self.emit("edit", f"Edit {rel} ({diff_stat(diff)})", path=rel, diff=cap(diff), ok=True, ms=ms)
        elif name.startswith("mcp__"):
            _, server, tool = (name.split("__", 2) + ["", ""])[:3]
            self.emit("tool", cap(json.dumps(args, ensure_ascii=False), 2000) if args else "", tool=tool, server=server,
                      output=None if output is None else head_lines(output, OUTPUT_LINES), ok=ok, ms=ms)
        else:
            target = args.get("command") or args.get("cmd") or path or args.get("pattern") or args.get("query") or args.get("url") or ""
            if isinstance(target, list):
                target = " ".join(map(str, target))
            tool = "Bash" if lname in ("bash", "shell", "exec_command") else name
            self.emit("tool", cap(target, 4000), tool=tool, path=rel or None,
                      output=None if output is None else head_lines(output, OUTPUT_LINES), ok=ok, ms=ms)


class ClaudeStream:
    """claude -p --output-format stream-json --verbose

    tool_use blocks are held until their tool_result (paired by id), so each tool call is one step with
    its output. Edit/Write diffs come from Claude Code's own structuredPatch when the stream carries it
    (`tool_use_result`), else from the file as it was when the tool_use arrived.
    """

    def __init__(self, emit: Emit, root: str):
        self.emit, self.root, self.tools, self.result = emit, root, {}, {}
        self.inputs: dict = {}
        self.refusals: list[dict] = []
        self.steps = Steps(emit, root)

    def line(self, raw: str):
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            return
        t = ev.get("type")
        if t == "result":
            self.steps.flush()
            self.result = ev
        elif t == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "text" and b.get("text", "").strip():
                    self.emit("text", cap(b["text"].strip()))
                elif b.get("type") == "thinking" and b.get("thinking", "").strip():
                    self.emit("thinking", cap(b["thinking"].strip()))
                elif b.get("type") == "tool_use":
                    self.tools[b.get("id")] = b.get("name", "")
                    self.inputs[b.get("id")] = b.get("input") or {}
                    self.steps.start(b.get("id"), b.get("name", ""), b.get("input") or {})
        elif t == "user":
            blocks = [b for b in ev.get("message", {}).get("content", []) if isinstance(b, dict) and b.get("type") == "tool_result"]
            # Claude Code puts the structured result of a lone tool call next to the message.
            structured = ev.get("tool_use_result") if len(blocks) == 1 else None
            for b in blocks:
                call_id = b.get("tool_use_id")
                name = self.tools.get(call_id, "")
                text = result_text(b.get("content"))
                reason = hook_refusal(text) if b.get("is_error") else None
                if reason:
                    self.steps.pending.pop(call_id, None)
                    args = self.inputs.get(call_id) or {}
                    p = _path_arg(args)
                    rel = _rel(self.root, p) if p else ""
                    item = {"tool": name, "path": rel, "reason": reason}
                    if args.get("command"):
                        item["command"] = str(args["command"])[:500]
                    self.refusals.append(item)
                    self.emit("guard", reason, tool=name, path=rel or None, ok=False)
                    continue
                if name == "TodoWrite":
                    self.steps.pending.pop(call_id, None)
                    continue
                if call_id not in self.steps.pending:
                    self.emit("tool", "", tool=name or "tool", output=head_lines(text, OUTPUT_LINES), ok=not b.get("is_error"))
                    continue
                self.steps.finish(call_id, text, not b.get("is_error"), structured)


NO_SESSION = re.compile(r"no conversation found|session .{0,40}not found|invalid session", re.I)


def claude_home() -> str:
    """claude's config folder on /data: its sessions survive a keel restart (the home folder does not)."""
    d = config.data_dir() / "agent-home" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    marker = d / ".claude.json"
    if not marker.exists():
        marker.write_text('{"hasCompletedOnboarding": true}')
    return str(d)


class ClaudeCLIRunner:
    async def _fresh(self, req: AgentRequest, emit: Emit) -> AgentResult:
        """The saved session is gone (cleaned up, or another machine): start a new one for this step."""
        emit("text", "Could not continue the earlier session for this step; starting a new one.")
        new = str(uuid.uuid4())
        if req.on_session:
            req.on_session(new)
        return await self.run(dataclasses.replace(req, session=new, resume=False), emit)

    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult:
        model = req.model.get("model") or "sonnet"
        argv = [find("claude"), "-p", "--output-format", "stream-json", "--verbose", "--model", model,
                "--permission-mode", "acceptEdits"]
        # The session is kept (on /data, see claude_home) so this step can continue it after a restart or send-back.
        if req.session:
            argv += ["--resume", req.session] if req.resume else ["--session-id", req.session]
        if req.model.get("effort"):
            argv += ["--effort", req.model["effort"]]
        turns = prompts.max_turns(req.agent, req.phase)
        if turns:
            argv += ["--max-turns", str(turns)]
        allowed = list(CLAUDE_TOOLS)
        cfg_path, mcp_allowed = mcp.claude_mcp_config(req.mcp_specs, req.tools_allow, req.workdir)
        if cfg_path:
            argv += ["--mcp-config", cfg_path, "--strict-mcp-config"]
            allowed += mcp_allowed
        argv += ["--allowedTools", ",".join(allowed), "--disallowedTools", ",".join(CLAUDE_HIDDEN)]
        if req.system:
            argv += ["--system-prompt", req.system]
        home = config.keel_home()
        if (home / "bin" / "keel").is_file():
            # Only keel v1's guard (its PreToolUse hook) is loaded, not the whole plugin: the plugin's start-up
            # text teaches keel v1's own workflow (keel state ..., .keel/ files), which the v2 engine owns, and its
            # after-edit hooks slow every tool call. The guard reads .keel/state.json, written before this step.
            settings = Path(req.workdir or req.root) / "keel-guard.json"
            settings.write_text(json.dumps({"hooks": {"PreToolUse": [{
                "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash|Read|mcp__.*",
                "hooks": [{"type": "command", "command": f'"{home / "bin" / "keel"}" hook pre-tool', "timeout": 10}],
            }]}}))
            argv += ["--settings", str(settings)]
        else:
            emit("text", f"keel is not installed at {home}: the live guard is off; the after-step diff guard still applies.")
        stream = ClaudeStream(emit, req.root)
        env = safe_env({**claude_login_env(req.keys), "KEEL_BIN": str(home / "bin" / "keel"), "CLAUDE_CONFIG_DIR": claude_home()})
        try:
            await run_cli("claude", argv, stdin=req.prompt, cwd=req.root, env=env, timeout=req.timeout, on_line=stream.line)
        except ModelError as exc:
            if req.resume and NO_SESSION.search(str(exc) + json.dumps(stream.result or {})):
                return await self._fresh(req, emit)
            raise
        res = stream.result
        if req.resume and res and res.get("is_error") and NO_SESSION.search(json.dumps(res)):
            return await self._fresh(req, emit)
        if not res:
            raise ModelError("claude printed no result.")
        if res.get("is_error"):
            raise ModelError(f"claude reported an error: {_short(res.get('result'), 500)}", usage=result_usage(res))
        usage = res.get("usage") or {}
        tin = int(usage.get("input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))
        cached = int(usage.get("cache_read_input_tokens", 0))
        tout = int(usage.get("output_tokens", 0))
        text = res.get("result", "")
        emit("answer", cap(text))
        return AgentResult(text=text, tokens_in=tin, tokens_out=tout, tokens_cached=cached, cost_usd=float(res.get("total_cost_usd") or 0.0),
                           data={"refusals": stream.refusals} if stream.refusals else {})


def file_diff(root: str, rel: str, added: bool = False) -> str:
    """The working-tree diff of one file (codex's file_change items name the files, not the change)."""
    if not added and git.is_repo(root):
        try:
            r = subprocess.run(["git", "diff", "--no-color", "HEAD", "--", rel], cwd=root, capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        except (OSError, subprocess.TimeoutExpired):
            pass
    return unified_diff(rel, "", read_file(root, rel) or "")


def codex_line(emit: Emit, root: str, ev: dict, state: dict):
    t = ev.get("type")
    if t == "thread.started" and ev.get("thread_id"):
        state["session"] = ev["thread_id"]
        return
    if t == "turn.completed":
        state["usage"] = ev.get("usage") or {}
        return
    if t in ("error", "turn.failed"):
        state["error"] = ev.get("message") or (ev.get("error") or {}).get("message") or json.dumps(ev)[:300]
        return
    if t not in ("item.started", "item.completed"):
        return
    item, done = ev.get("item") or {}, t == "item.completed"
    it = item.get("type")
    started = state.setdefault("started", {})
    if not done:
        if item.get("id"):
            started[item["id"]] = time.monotonic()
        return
    t0 = started.pop(item.get("id"), None)
    ms = ms_since(t0) if t0 else None
    if it == "command_execution":
        code = item.get("exit_code")
        out = item.get("aggregated_output")
        ok = code in (0, None) and item.get("status") != "failed"
        emit("tool", cap(item.get("command") or "", 4000), tool="Bash",
             output=head_lines(out if out else (f"exit {code}" if code not in (0, None) else ""), OUTPUT_LINES), ok=ok, ms=ms)
    elif it == "agent_message" and item.get("text", "").strip():
        state["text"] = item["text"].strip()
        emit("text", cap(state["text"]))
    elif it == "reasoning" and item.get("text", "").strip():
        emit("thinking", cap(item["text"].strip()))
    elif it == "file_change":
        for c in item.get("changes", []):
            rel = _rel(root, c.get("path", ""))
            if c.get("kind") == "delete":
                emit("edit", f"Delete {rel}", path=rel, diff=cap(file_diff(root, rel)), ok=True)
                continue
            kind = "write" if c.get("kind") == "add" else "edit"
            diff = file_diff(root, rel, added=kind == "write")
            emit(kind, f"{'Write' if kind == 'write' else 'Edit'} {rel} ({diff_stat(diff)})", path=rel, diff=cap(diff),
                 ok=item.get("status") != "failed")
    elif it == "mcp_tool_call":
        res = item.get("result") or {}
        out = result_text(res.get("content")) if isinstance(res, dict) else str(res)
        err = item.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        args = item.get("arguments")
        emit("tool", cap(json.dumps(args, ensure_ascii=False) if args else "", 2000), tool=item.get("tool"), server=item.get("server"),
             output=head_lines(err or out, OUTPUT_LINES), ok=not err and item.get("status") != "failed", ms=ms)
    elif it == "web_search":
        emit("tool", cap(item.get("query") or ""), tool="WebSearch", ok=True, ms=ms)


class CodexCLIRunner:
    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult:
        argv = [find("codex"), "exec", "--json", "-s", "workspace-write", "--skip-git-repo-check", "-C", req.root]
        if req.model.get("model"):
            argv += ["-m", req.model["model"]]
        if req.model.get("effort"):
            argv += ["-c", f"model_reasoning_effort={req.model['effort']}"]
        for s in mcp.servers_for(req.mcp_specs, req.tools_allow):
            argv += ["-c", f"mcp_servers.{s['name']}.command={json.dumps(s['command'])}",
                     "-c", f"mcp_servers.{s['name']}.args={json.dumps(list(s.get('args') or []))}"]
        # Sessions live in CODEX_HOME on /data; continue this step's session when there is one.
        argv += ["resume", req.session, "-"] if (req.session and req.resume) else ["-"]
        state: dict = {}

        def on_line(raw: str):
            try:
                codex_line(emit, req.root, json.loads(raw), state)
            except json.JSONDecodeError:
                pass

        prompt = f"{req.system}\n\n{req.prompt}" if req.system else req.prompt
        try:
            await run_cli("codex", argv, stdin=prompt, cwd=req.root, env=safe_env(codex_login_env(req.keys)), timeout=req.timeout, on_line=on_line)
        except ModelError as exc:
            if req.session and req.resume and NO_SESSION.search(str(exc)):
                emit("text", "Could not continue the earlier session for this step; starting a new one.")
                return await self.run(dataclasses.replace(req, session=None, resume=False), emit)
            raise
        if state.get("session") and req.on_session:
            req.on_session(state["session"])
        if state.get("error"):
            raise ModelError(f"codex reported an error: {_short(state['error'], 500)}")
        usage = state.get("usage") or {}
        cached = int(usage.get("cached_input_tokens", 0))
        tin, tout = max(int(usage.get("input_tokens", 0)) - cached, 0), int(usage.get("output_tokens", 0))
        text = state.get("text", "")
        emit("answer", cap(text))
        # Subscription use has no per-token price; the plan pays for it.
        return AgentResult(text=text, tokens_in=tin, tokens_out=tout, tokens_cached=cached)


class CopilotCLIRunner:
    """`copilot -p` prints plain text, so each non-empty line is a text step and tokens are estimated."""

    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult:
        prompt = f"{req.system}\n\n{req.prompt}" if req.system else req.prompt
        argv = [find("copilot"), "-p", prompt, "--allow-all-tools", "--no-color"]
        if req.model.get("model"):
            argv += ["--model", req.model["model"]]
        servers = mcp.servers_for(req.mcp_specs, req.tools_allow)
        if servers:
            cfg = {"mcpServers": {s["name"]: {"type": "local", "command": s["command"], "args": list(s.get("args") or []), "tools": ["*"]}
                                  for s in servers}}
            argv += ["--additional-mcp-config", json.dumps(cfg)]
        login = copilot_login_env(req.keys) or ({"GH_TOKEN": req.key, "COPILOT_GITHUB_TOKEN": req.key} if req.key else {})
        env = safe_env(login)
        lines: list[str] = []
        para: list[str] = []

        def flush():
            if any(x.strip() for x in para):
                emit("text", cap("\n".join(para).strip("\n")))
            para.clear()

        def on_line(raw: str):
            lines.append(raw)
            if raw.strip():
                para.append(raw.rstrip())
                if len(para) >= 60:
                    flush()
            else:
                flush()     # a blank line ends a paragraph: one Markdown step per paragraph, newlines kept

        await run_cli("copilot", argv, cwd=req.root, env=env, timeout=req.timeout, on_line=on_line)
        flush()
        text = "\n".join(lines).strip()
        emit("answer", cap(text))
        return AgentResult(text=text, tokens_in=len(prompt) // 4, tokens_out=len(text) // 4, premium_requests=1)


class OpenCodeRunner:
    """`opencode run --format json -m github-copilot/<model>`: one JSON event per line."""

    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult:
        prompt = f"{req.system}\n\n{req.prompt}" if req.system else req.prompt
        model = req.model.get("model") or "gpt-5"
        argv = [find("opencode"), "run", "--format", "json", "-m", f"github-copilot/{model}", prompt]
        note = await asyncio.to_thread(ensure_opencode_adapter, req.root)
        if note:
            emit("text", note)
        state = {"text": [], "in": 0, "out": 0, "cost": 0.0}
        steps = Steps(emit, req.root)

        def on_line(raw: str):
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                return
            part = ev.get("part") or {}
            t = ev.get("type") or part.get("type")
            if t == "text" and part.get("text", "").strip():
                state["text"].append(part["text"].strip())
                emit("text", cap(part["text"].strip()))
            elif t == "reasoning" and part.get("text", "").strip():
                emit("thinking", cap(part["text"].strip()))
            elif t in ("tool_use", "tool"):
                opencode_tool(steps, part)
            elif t == "step_finish":
                tok = part.get("tokens") or {}
                state["in"] += int(tok.get("input", 0))
                state["out"] += int(tok.get("output", 0))
                state["cost"] += float(part.get("cost") or 0)
            elif t == "error":
                state["error"] = json.dumps(ev.get("error") or ev)[:400]

        env = safe_env({"KEEL_BIN": str(config.keel_home() / "bin" / "keel")})
        await run_cli("opencode", argv, cwd=req.root, env=env, timeout=req.timeout, on_line=on_line)
        if state.get("error"):
            raise ModelError(f"opencode reported an error: {state['error']}")
        text = "\n".join(state["text"])
        emit("answer", cap(text))
        return AgentResult(text=text, tokens_in=state["in"], tokens_out=state["out"], cost_usd=state["cost"], premium_requests=1)


def opencode_tool(steps: "Steps", part: dict):
    """One opencode tool part: {tool, state: {status, input, output, error, metadata: {diff?}, time: {start, end}}}.
    Only finished calls become steps (opencode also reports pending/running states)."""
    st = part.get("state") or {}
    status = st.get("status")
    if status in ("pending", "running"):
        return
    tm = st.get("time") or {}
    ms = int(tm["end"] - tm["start"]) if isinstance(tm.get("end"), (int, float)) and isinstance(tm.get("start"), (int, float)) else None
    meta = st.get("metadata") or {}
    ok = status != "error"
    output = st.get("output") if ok else (st.get("error") or st.get("output"))
    if not isinstance(output, str):
        output = json.dumps(output, ensure_ascii=False) if output is not None else None
    diff = meta.get("diff") if isinstance(meta.get("diff"), str) and meta.get("diff").strip() else None
    name = part.get("tool", "tool")
    if "_" in name and not name.startswith("mcp__"):
        server, tool = name.split("_", 1)      # opencode's own tools have no "_"; MCP tools are <server>_<tool>
        steps.emit("tool", cap(json.dumps(st.get("input") or {}, ensure_ascii=False), 2000), tool=tool, server=server,
                   output=None if output is None else head_lines(output, OUTPUT_LINES), ok=ok, ms=ms)
        return
    steps.done(name, st.get("input") or {}, output, ok, ms, diff=diff)


def ms_since(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def opencode_adapter_present(root: str) -> bool:
    return any((Path(root) / ".opencode" / d / "keel.js").is_file() for d in ("plugin", "plugins"))


def ensure_opencode_adapter(root: str) -> str | None:
    """Install keel's opencode adapter into <root>/.opencode/ when it is missing. Returns a note when it could not.

    The installed files are kept out of git (`.git/info/exclude`), so they never show up in a diff,
    a guard check or a commit.
    """
    if opencode_adapter_present(root):
        return None
    installer = config.keel_home() / "opencode" / "install.mjs"
    if not installer.is_file():
        return f"keel's opencode adapter was not found at {installer}: the live guard is off; the after-step diff guard still applies."
    node = shutil.which("node")
    if not node:
        return "node is not installed, so keel's opencode adapter could not be installed: the live guard is off."
    try:
        p = subprocess.run([node, str(installer)], cwd=root, capture_output=True, text=True, timeout=120, env=safe_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Installing keel's opencode adapter failed: {exc}"
    if p.returncode != 0:
        return f"Installing keel's opencode adapter failed: {(p.stderr or p.stdout).strip()[-400:]}"
    _exclude(root, ".opencode/")
    return None


def _exclude(root: str, pattern: str):
    if not git.is_repo(root):
        return
    r = git.git(root, "rev-parse", "--git-path", "info/exclude")
    if r.returncode != 0:
        return
    f = Path(root) / r.stdout.strip()
    try:
        have = f.read_text() if f.is_file() else ""
        if pattern not in have.splitlines():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(have + ("" if not have or have.endswith("\n") else "\n") + pattern + "\n")
    except OSError:
        pass
