"""keel's PreToolUse hook: `python -m keel_engine.hook pre-tool`.

Claude Code (and the opencode plugin keel generates) runs it before Edit, Write, MultiEdit, NotebookEdit,
Bash, Read and MCP tool calls, with the call as JSON on stdin ({tool_name, tool_input, cwd}). What the
phase allows comes from the guard context named by KEEL_GUARD_CTX (runtime/guard_ctx.py) and the
project's `.keel/config.yml`; the decision is keel's rules (check_edit, check_read, check_bash).

    allow   exit 0, nothing printed
    deny    exit 2, "[keel guard] <reason>" on stderr (Claude Code hands that to the model as the tool's error)

Fails closed: with no readable context, or on any error, only read-only tools pass.
Imports stay light (stdlib + keel_engine.rules, no langgraph or fastapi): this runs once per tool call.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from . import rules
from .runtime import permissions, run_mode
from .runtime.guard_ctx import ENV

MARKER = "[keel guard]"
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_ONLY = {"Read", "Glob", "Grep", "LS"}

# Serena's editing tools change files, so they follow the same phase rules as Edit (keel v1 checkMcp).
SERENA_EDIT = re.compile(r"(replace_symbol_body|insert_after_symbol|insert_before_symbol|insert_at_line|delete_lines|"
                         r"replace_lines|replace_regex|create_text_file|rename_symbol|write_memory)", re.I)
WRITEISH = re.compile(r"(write|create|insert|update|delete|replace|edit|apply|execute|run|commit|merge|push)", re.I)
# The read tools of keel's parts' servers (each part's read_tools, keel_engine/extensions.py; a plugin's server is its
# own module, keel_plugin_<name>.server): they change nothing (a change is a button for the person), so they are not
# judged by name ("ci_runs" lists runs). Only these exact tools of these servers pass. The registry reads only the
# parts' light declarations, so the hook stays light. Each plugin's tests check the list against its server.
# A plugin's part (keel-ci, keel-db, keel-git) is not loaded in the hook: the guard context names its read tools
# (`more`, from the engine).
def plugin_read_tool(name: str, more: dict | None = None) -> bool:
    from .extensions import read_tools

    parts = name.split("__", 2)
    if len(parts) != 3:
        return False
    if parts[2] in read_tools().get(parts[1], set()):
        return True
    named = (more or {}).get(parts[1]) if isinstance(more, dict) else None
    return isinstance(named, list) and parts[2] in named


class NoContext(Exception):
    pass


def load_context(path: str | None) -> dict:
    if not path:
        raise NoContext(f"{ENV} is not set")
    try:
        ctx = json.loads(Path(path).read_text())
    except OSError as exc:
        raise NoContext(f"the guard context {path} cannot be read ({exc.strerror or exc})") from None
    except ValueError:
        raise NoContext(f"the guard context {path} is not valid JSON") from None
    if not isinstance(ctx, dict) or not isinstance(ctx.get("phase"), str) or not isinstance(ctx.get("root"), str):
        raise NoContext(f"the guard context {path} has no root or phase")
    if not Path(ctx["root"]).is_dir():
        raise NoContext(f"the project folder {ctx['root']} in the guard context does not exist")
    ctx["_path"] = path      # the run's own folder also holds its list of reads (already_read)
    return ctx


def _path_arg(ti: dict) -> str:
    return str(ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or ti.get("filePath") or "")


def _locate(root: str, path: str) -> tuple[str, Path]:
    """(repo-relative path, or the absolute one when it is outside the project; the full path)."""
    p = Path(path)
    full = p if p.is_absolute() else Path(root) / p
    try:
        return full.resolve().relative_to(Path(root).resolve()).as_posix(), full
    except (ValueError, OSError):
        return str(full), full


def check_mcp(name: str, ti: dict, phase: str, cfg: dict, edit) -> str | None:
    """MCP tools (keel v1 checkMcp): serena's edits are edits; other write-ish tools need `mcp.allow` in a flow."""
    if re.search("serena", name, re.I) and SERENA_EDIT.search(name):
        if re.search("write_memory", name, re.I):
            return "Serena memories are off inside keel flows: the spec, the flow state and git are the record."
        file = ti.get("relative_path") or ti.get("file_path") or ti.get("path") or ti.get("filepath")
        if not file:
            return f'{name} did not name a file, so keel cannot check it against phase "{phase}".'
        reason = edit(str(file))
        return f"via {name}: {reason}" if reason else None
    if phase in ("", "none"):
        return None
    if any(a and a in name for a in (cfg.get("mcp") or {}).get("allow") or []):
        return None
    # Judged on the tool's own name (mcp__<server>__<tool>), so a server called "runner" does not make every tool write-ish.
    if WRITEISH.search(name.split("__", 2)[-1]):
        return (f'MCP tool "{name}" can change things and is not on keel\'s allowlist for phase "{phase}". '
                f"Add it to `mcp.allow` in .keel/config.yml if it is safe.")
    return None


def decide(tool: str, ti: dict, ctx: dict) -> str | None:
    """None to allow the call, else the reason it is refused."""
    root, phase = ctx["root"], ctx.get("phase") or "none"
    lane, unlocks = ctx.get("lane"), list(ctx.get("unlocks") or [])
    cfg = rules.load_config(root)

    def knowledge(text: str) -> str | None:
        # strict per-agent knowledge (5c): a section the agent was not given is not read, by Read or by a shell command
        v = rules.check_knowledge(text, ctx.get("agent") or "", ctx.get("knowledge_allowed"), bool(ctx.get("knowledge_strict")))
        return None if v.ok else v.reason

    readonly = bool(ctx.get("readonly"))

    def edit(path: str) -> str | None:
        if readonly:
            return run_mode.READONLY_EDIT
        rel, full = _locate(root, path)
        if ctx.get("confine") and Path(rel).is_absolute():
            return f"{full} is outside {root}: this side session changes only its own copy of the project (its worktree)."
        v = rules.check_edit(phase, rel, cfg, exists=full.exists(), lane=lane, unlocks=unlocks)
        return None if v.ok else v.reason

    if readonly and tool in WRITE_TOOLS:
        return run_mode.READONLY_EDIT
    if tool in WRITE_TOOLS:
        path = _path_arg(ti)
        return edit(path) if path else None
    if tool == "Read":
        path = _path_arg(ti)
        if not path:
            return None
        rel, full = _locate(root, path)
        v = rules.check_read(rel, cfg)
        refused = knowledge(rel) if v.ok else v.reason
        return refused or already_read(ctx.get("_path"), rel, full, ti.get("offset"), ti.get("limit"))
    if tool == "Bash":
        refused = knowledge(str(ti.get("command") or "")) or (run_mode.readonly_bash(str(ti.get("command") or "")) if readonly else None)
        if refused:
            return refused
        v = rules.check_bash(phase, str(ti.get("command") or ""), cfg,
                             exists=lambda rel: (Path(root) / rel).exists(), unlocks=unlocks)
        if not v.ok:
            return v.reason
        # a run that may ask (KeelBot's Fix and side modes): a command that changes something waits for the person's OK
        # (runtime/permissions.py asks at the context's URL; keel_engine/approvals.py does the waiting)
        if ctx.get("ask") and permissions.needs_ask(str(ti.get("command") or "")):
            ok, why = permissions.ask_engine(ctx["ask"], "command", str(ti.get("command") or ""))
            return None if ok else why
        return None
    if tool.startswith("mcp__"):
        if plugin_read_tool(tool, ctx.get("read_tools")):
            return None
        if readonly and (WRITEISH.search(tool.split("__", 2)[-1]) or (re.search("serena", tool, re.I) and SERENA_EDIT.search(tool))):
            return run_mode.READONLY_MCP
        return check_mcp(tool, ti, phase, cfg, edit)
    return None


def already_read(ctx_path: str | None, rel: str, full: Path, offset, limit) -> str | None:
    """A second Read of the same unchanged file and range in one agent run costs tokens and tells nothing new.

    The run's reads are listed next to its guard context (one context file per run). A changed file (mtime/size) or
    another line range is read again. This rule saves tokens, it is not a safety rule: any error here allows the read.
    """
    if not ctx_path:
        return None
    try:
        st = full.stat()
        seen_file = Path(ctx_path).with_name("reads.json")
        seen = json.loads(seen_file.read_text()) if seen_file.is_file() else {}
        key = f"{rel}|{offset or ''}|{limit or ''}"
        now = [st.st_mtime_ns, st.st_size]
        if seen.get(key) == now:
            return (f"You already read {rel} in this step and it has not changed since: use what you read. "
                    "To see another part, read a different range (offset/limit).")
        seen[key] = now
        tmp = seen_file.with_name(seen_file.name + ".tmp")
        tmp.write_text(json.dumps(seen))
        tmp.replace(seen_file)
    except Exception:  # noqa: BLE001
        return None
    return None


def fail_closed(tool: str, ti: dict, why: str) -> str | None:
    """No context: read-only tools pass (secrets stay blocked), everything else is refused."""
    if tool in READ_ONLY:
        path = _path_arg(ti)
        v = rules.check_read(path, rules.make_config()) if tool == "Read" and path else None
        return None if v is None or v.ok else v.reason
    return f"{why}, so keel cannot check {tool or 'this tool'} against the flow's phase. Only reads are allowed until it is fixed."


def deny(reason: str) -> int:
    sys.stderr.write(f"{MARKER} {reason}\n")
    return 2


def pre_tool(raw: str, ctx_path: str | None) -> int:
    try:
        call = json.loads(raw) if raw.strip() else {}
        if not isinstance(call, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:
        return deny(f"keel could not read this tool call ({exc}).")
    tool = str(call.get("tool_name") or "")
    ti = call.get("tool_input") if isinstance(call.get("tool_input"), dict) else {}
    try:
        ctx = load_context(ctx_path)
    except NoContext as exc:
        reason = fail_closed(tool, ti, str(exc))
        return deny(reason) if reason else 0
    try:
        reason = decide(tool, ti, ctx)
    except Exception as exc:  # a broken guard refuses; it never waves a call through
        reason = fail_closed(tool, ti, f"the guard failed ({type(exc).__name__}: {exc})")
    return deny(reason) if reason else 0


def self_test(root: str) -> tuple[bool, str]:
    """Run the hook the way an agent's CLI does, with a context in phase red: an edit of a production file must be
    refused (exit 2) and a read allowed (exit 0). Ladder rung 12."""
    import subprocess
    import tempfile

    from .runtime.guard_ctx import hook_argv, write_context

    root = str(Path(root).resolve())
    cfg = rules.load_config(root)
    tries = [f"{cfg['backend'].get('dir') or 'src'}/src/main/KeelGuardCheck.kt", f"{cfg['frontend'].get('dir') or 'web'}/src/KeelGuardCheck.tsx"]
    target = next((t for t in tries if not rules.check_edit("red", t, cfg).ok), ".env")
    with tempfile.TemporaryDirectory(prefix="keel-guard-test-") as tmp:
        ctx = write_context(Path(tmp) / "guard.json", root=root, phase="red", ac=None, lane=None, unlocks=[], agent="self-test", thread="")

        def run(tool: str, ti: dict) -> subprocess.CompletedProcess:
            return subprocess.run(hook_argv(), input=json.dumps({"tool_name": tool, "tool_input": ti, "cwd": root}), cwd=root,
                                  env={**os.environ, ENV: ctx}, capture_output=True, text=True, timeout=30)
        try:
            edit = run("Edit", {"file_path": str(Path(root) / target)})
            read = run("Read", {"file_path": str(Path(root) / "README.md")})
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"the guard hook did not run: {exc}"
    if edit.returncode != 2 or MARKER not in edit.stderr:
        return False, f"an edit of {target} in phase red was not refused (exit {edit.returncode}): {(edit.stderr or edit.stdout).strip()[-300:]}"
    if read.returncode != 0:
        return False, f"a read was refused (exit {read.returncode}): {read.stderr.strip()[-300:]}"
    return True, f"refused an edit of {target} in phase red, allowed a read"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["pre-tool"]:
        return deny("usage: python -m keel_engine.hook pre-tool")
    return pre_tool(sys.stdin.read(), os.environ.get(ENV))


if __name__ == "__main__":
    sys.exit(main())
