"""Subscription-mode runners: claude, codex, copilot (Copilot CLI) and opencode.

Each turns its CLI's stream into steps, like Yegi's activity.py: text, thinking, tool, write, edit,
result, answer. CLI agents edit files directly, so the runtime's diff guard is what enforces the
phase rules for them.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from .. import config
from ..tools import git, mcp
from ..tools.agent_tools import unified_diff
from .base import AgentRequest, AgentResult, Emit, ModelError
from .cli import claude_login_env, copilot_login_env, find, run_cli, safe_env

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


def _short(text: str, n: int = 300) -> str:
    return " ".join(str(text or "").split())[:n]


def _rel(root: str, path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return path


def tool_step(emit: Emit, root: str, name: str, args: dict):
    args = args or {}
    path = str(args.get("file_path") or args.get("path") or args.get("filePath") or "")
    rel = _rel(root, path) if path else ""
    lname = name.lower()
    if lname in ("write", "create"):
        emit("write", f"Write {rel}", path=rel, diff=unified_diff(rel, "", str(args.get("content", ""))))
    elif lname in ("edit", "multiedit", "str_replace", "patch"):
        edits = args.get("edits") or [{"old_string": args.get("old_string") or args.get("oldString", ""),
                                       "new_string": args.get("new_string") or args.get("newString", "")}]
        diff = "".join(unified_diff(rel, e.get("old_string", ""), e.get("new_string", "")) for e in edits)
        emit("edit", f"Edit {rel}", path=rel, diff=diff)
    elif name.startswith("mcp__"):
        _, server, tool = (name.split("__", 2) + ["", ""])[:3]
        emit("tool", f"{server} {tool} {_short(json.dumps(args), 120)}".strip(), tool=tool, server=server)
    else:
        target = args.get("command") or path or args.get("pattern") or args.get("query") or ""
        emit("tool", f"{name} {_short(target, 200)}".strip(), tool=name, path=rel or None)


class ClaudeStream:
    """claude -p --output-format stream-json --verbose"""

    def __init__(self, emit: Emit, root: str):
        self.emit, self.root, self.tools, self.result = emit, root, {}, {}
        self.inputs: dict = {}
        self.refusals: list[dict] = []

    def line(self, raw: str):
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            return
        t = ev.get("type")
        if t == "result":
            self.result = ev
        elif t == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "text" and b.get("text", "").strip():
                    self.emit("text", b["text"].strip())
                elif b.get("type") == "thinking" and b.get("thinking", "").strip():
                    self.emit("thinking", b["thinking"].strip())
                elif b.get("type") == "tool_use":
                    self.tools[b.get("id")] = b.get("name", "")
                    self.inputs[b.get("id")] = b.get("input") or {}
                    tool_step(self.emit, self.root, b.get("name", ""), b.get("input") or {})
        elif t == "user":
            for b in ev.get("message", {}).get("content", []):
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                name = self.tools.get(b.get("tool_use_id"), "")
                content = b.get("content")
                text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict)) if isinstance(content, list) else str(content or "")
                reason = hook_refusal(text) if b.get("is_error") else None
                if reason:
                    args = self.inputs.get(b.get("tool_use_id")) or {}
                    p = str(args.get("file_path") or args.get("path") or args.get("notebook_path") or "")
                    rel = _rel(self.root, p) if p else ""
                    item = {"tool": name, "path": rel, "reason": reason}
                    if args.get("command"):
                        item["command"] = str(args["command"])[:500]
                    self.refusals.append(item)
                    self.emit("guard", reason, tool=name, path=rel or None, ok=False)
                    continue
                if name in ("Write", "Edit", "MultiEdit", "TodoWrite"):
                    continue
                self.emit("error" if b.get("is_error") else "result", _short(text, 2000) or "(no output)", tool=name, ok=not b.get("is_error"))


class ClaudeCLIRunner:
    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult:
        model = req.model.get("model") or "sonnet"
        argv = [find("claude"), "-p", "--output-format", "stream-json", "--verbose", "--model", model,
                "--no-session-persistence", "--permission-mode", "acceptEdits"]
        if req.model.get("effort"):
            argv += ["--effort", req.model["effort"]]
        allowed = list(CLAUDE_TOOLS)
        cfg_path, mcp_allowed = mcp.claude_mcp_config(req.mcp_specs, req.tools_allow, req.workdir)
        if cfg_path:
            argv += ["--mcp-config", cfg_path, "--strict-mcp-config"]
            allowed += mcp_allowed
        argv += ["--allowedTools", ",".join(allowed)]
        if req.system:
            argv += ["--system-prompt", req.system]
        home = config.keel_home()
        if (home / ".claude-plugin").is_dir() or (home / "hooks" / "hooks.json").is_file():
            # keel v1's hooks enforce the phase rules on every tool call, reading .keel/state.json,
            # which the runtime writes before this step starts.
            argv += ["--plugin-dir", str(home)]
        else:
            emit("text", f"keel is not installed at {home}: the live guard is off; the after-step diff guard still applies.")
        stream = ClaudeStream(emit, req.root)
        env = safe_env({**claude_login_env(req.keys), "KEEL_BIN": str(home / "bin" / "keel")})
        await run_cli("claude", argv, stdin=req.prompt, cwd=req.root, env=env, timeout=req.timeout, on_line=stream.line)
        res = stream.result
        if not res:
            raise ModelError("claude printed no result.")
        if res.get("is_error"):
            raise ModelError(f"claude reported an error: {_short(res.get('result'), 500)}")
        usage = res.get("usage") or {}
        tin = int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))
        tout = int(usage.get("output_tokens", 0))
        text = res.get("result", "")
        emit("answer", _short(text, 4000))
        return AgentResult(text=text, tokens_in=tin, tokens_out=tout, cost_usd=float(res.get("total_cost_usd") or 0.0),
                           data={"refusals": stream.refusals} if stream.refusals else {})


def codex_line(emit: Emit, root: str, ev: dict, state: dict):
    t = ev.get("type")
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
    if it == "command_execution":
        if not done:
            emit("tool", f"Bash {_short(item.get('command'), 200)}", tool="Bash")
        else:
            code = item.get("exit_code")
            emit("result" if code in (0, None) else "error", _short(item.get("aggregated_output"), 2000) or f"exit {code}",
                 tool="Bash", ok=code in (0, None))
    elif not done:
        return
    elif it == "agent_message" and item.get("text", "").strip():
        state["text"] = item["text"].strip()
        emit("text", state["text"])
    elif it == "reasoning" and item.get("text", "").strip():
        emit("thinking", item["text"].strip())
    elif it == "file_change":
        for c in item.get("changes", []):
            kind = "write" if c.get("kind") == "add" else "edit"
            rel = _rel(root, c.get("path", ""))
            emit(kind, f"{'Write' if kind == 'write' else 'Edit'} {rel}", path=rel)
    elif it == "mcp_tool_call":
        emit("tool", f"{item.get('server', '')} {item.get('tool', '')}".strip(), tool=item.get("tool"), server=item.get("server"))


class CodexCLIRunner:
    async def run(self, req: AgentRequest, emit: Emit) -> AgentResult:
        argv = [find("codex"), "exec", "-", "--json", "-s", "workspace-write", "--skip-git-repo-check", "-C", req.root]
        if req.model.get("model"):
            argv += ["-m", req.model["model"]]
        if req.model.get("effort"):
            argv += ["-c", f"model_reasoning_effort={req.model['effort']}"]
        for s in mcp.servers_for(req.mcp_specs, req.tools_allow):
            argv += ["-c", f"mcp_servers.{s['name']}.command={json.dumps(s['command'])}",
                     "-c", f"mcp_servers.{s['name']}.args={json.dumps(list(s.get('args') or []))}"]
        state: dict = {}

        def on_line(raw: str):
            try:
                codex_line(emit, req.root, json.loads(raw), state)
            except json.JSONDecodeError:
                pass

        prompt = f"{req.system}\n\n{req.prompt}" if req.system else req.prompt
        await run_cli("codex", argv, stdin=prompt, cwd=req.root, env=safe_env(), timeout=req.timeout, on_line=on_line)
        if state.get("error"):
            raise ModelError(f"codex reported an error: {_short(state['error'], 500)}")
        usage = state.get("usage") or {}
        tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        text = state.get("text", "")
        emit("answer", _short(text, 4000))
        # Subscription use has no per-token price; the plan pays for it.
        return AgentResult(text=text, tokens_in=tin, tokens_out=tout)


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

        def on_line(raw: str):
            if raw.strip():
                lines.append(raw)
                emit("text", raw.strip())

        await run_cli("copilot", argv, cwd=req.root, env=env, timeout=req.timeout, on_line=on_line)
        text = "\n".join(lines)
        emit("answer", _short(text, 4000))
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

        def on_line(raw: str):
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                return
            part = ev.get("part") or {}
            t = ev.get("type") or part.get("type")
            if t == "text" and part.get("text", "").strip():
                state["text"].append(part["text"].strip())
                emit("text", part["text"].strip())
            elif t in ("tool_use", "tool"):
                st = part.get("state") or {}
                tool_step(emit, req.root, part.get("tool", "tool"), st.get("input") or {})
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
        emit("answer", _short(text, 4000))
        return AgentResult(text=text, tokens_in=state["in"], tokens_out=state["out"], cost_usd=state["cost"], premium_requests=1)


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
