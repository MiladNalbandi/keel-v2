"""keel's PreToolUse hook: `python -m keel_engine.hook pre-tool`.

Claude Code, codex (its hooks, 0.150+) and the opencode plugin keel generates run it before Edit, Write, MultiEdit,
NotebookEdit, Bash, Read and MCP tool calls, with the call as JSON on stdin ({tool_name, tool_input, cwd}); codex's
own tool names (shell, exec_command, apply_patch) are read as Bash and Edit. The command wrappers keel puts on codex's
and copilot's PATH (keel_engine/shim.py) ask the same `decide` as Bash. What the
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
from .rules import checks as _checks  # noqa: F401  (rules.checks for the content check)
from .runtime import other_projects, permissions, run_mode
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


SHELL_NAMES = {"shell", "exec_command", "local_shell", "unified_exec", "container.exec", "Shell", "run_shell_command"}
PATCH_NAMES = {"apply_patch", "ApplyPatch", "patch"}


def _shell_command(ti: dict) -> str:
    cmd = ti.get("command") if "command" in ti else ti.get("cmd")
    if isinstance(cmd, list):
        words = [str(w) for w in cmd]
        # ["bash", "-lc", "<script>"]: the script is the command
        if len(words) >= 3 and words[1] in ("-c", "-lc") and words[0].rsplit("/", 1)[-1] in ("bash", "sh", "zsh"):
            return words[-1]
        import shlex

        return shlex.join(words)
    return str(cmd or "")


def patch_files(text: str) -> dict[str, str]:
    """{file: the lines the patch adds} for an apply_patch envelope (`*** Update File: x`) or a unified diff."""
    out: dict[str, list[str]] = {}
    cur = None
    for line in str(text or "").split("\n"):
        m = re.match(r"^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*(.+?)\s*$", line) or \
            re.match(r"^\*\*\*\s+Move to:\s*(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
            continue
        m = re.match(r"^\+\+\+\s+(?:b/)?(.+?)\s*$", line)
        if m:
            cur = None if m.group(1) == "/dev/null" else m.group(1)
            if cur:
                out.setdefault(cur, [])
            continue
        if cur and line.startswith("+") and not line.startswith("+++"):
            out[cur].append(line[1:])
    return {f: "\n".join(lines) for f, lines in out.items()}


def _patch_text(ti: dict) -> str:
    for v in [ti.get("input"), ti.get("patch"), ti.get("patchText"), ti.get("command"), *ti.values()]:
        if isinstance(v, list):
            v = next((x for x in v if isinstance(x, str) and ("*** Begin Patch" in x or "\n+++ " in x)), None)
        if isinstance(v, str) and ("*** Begin Patch" in v or "+++ " in v or "*** Update File" in v):
            return v
    return ""


def normalize(tool: str, ti: dict) -> list[tuple[str, dict]]:
    """codex's tool calls in Claude's terms: its shell is Bash, a patch is one Edit per file (with the added text)."""
    if tool in SHELL_NAMES:
        cmd = _shell_command(ti)
        if cmd.lstrip().startswith("apply_patch") or "*** Begin Patch" in cmd:
            return normalize("apply_patch", {"input": cmd})
        return [("Bash", {"command": cmd})]
    if tool in PATCH_NAMES:
        files = patch_files(_patch_text(ti))
        if not files:
            return [("Edit", {"file_path": "", "_unreadable_patch": True})]
        return [("Edit", {"file_path": f, "new_string": added}) for f, added in files.items()]
    return [(tool, ti)]


def check_mcp(name: str, ti: dict, phase: str, cfg: dict, edit) -> str | None:
    """MCP tools (keel v1 checkMcp): serena's edits are edits; other write-ish tools need `mcp.allow` in a flow."""
    v = rules.pack_verdict(cfg, "mcp", phase, name)
    if v:
        return v.reason
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
    if any(a and a in name for a in (cfg.get("mcp") or {}).get("allow") or []) or rules.is_off(cfg, "mcp-writes"):
        return None
    # Judged on the tool's own name (mcp__<server>__<tool>), so a server called "runner" does not make every tool write-ish.
    if WRITEISH.search(name.split("__", 2)[-1]):
        return (f'MCP tool "{name}" can change things and is not on keel\'s allowlist for phase "{phase}". '
                f"Add it to `mcp.allow` in .keel/config.yml if it is safe.")
    return None


def decide(tool: str, ti: dict, ctx: dict) -> str | None:
    """None to allow the call, else the reason it is refused."""
    calls = normalize(tool, ti)
    if len(calls) != 1 or calls[0][0] != tool:
        if any(t.get("_unreadable_patch") for _n, t in calls):
            return "no file could be read out of this patch, so keel cannot check it. Name the files in the patch."
        for name, t in calls:
            refused = decide(name, t, ctx)
            if refused:
                return refused
        return None
    root, phase = ctx["root"], ctx.get("phase") or "none"
    lane, unlocks = ctx.get("lane"), list(ctx.get("unlocks") or [])
    cfg = rules.load_config(root)

    def knowledge(text: str) -> str | None:
        # strict per-agent knowledge (5c): a section the agent was not given is not read, by Read or by a shell command
        v = rules.check_knowledge(text, ctx.get("agent") or "", ctx.get("knowledge_allowed"), bool(ctx.get("knowledge_strict")))
        return None if v.ok else v.reason

    readonly = bool(ctx.get("readonly"))

    def ro(reason: str) -> str:
        # keel 0.18.18 KeelBot's Ask mode says it in its own words (it is no flow), and what to do instead
        return run_mode.relabel(reason, ctx.get("readonly_label") or "", ctx.get("readonly_note") or "")

    def edit(path: str) -> str | None:
        if readonly:
            return ro(run_mode.READONLY_EDIT)
        rel, full = _locate(root, path)
        if ctx.get("confine") and Path(rel).is_absolute():
            return f"{full} is outside {root}: this side session changes only its own copy of the project (its worktree)."
        v = rules.check_edit(phase, rel, cfg, exists=full.exists(), lane=lane, unlocks=unlocks)
        return None if v.ok else v.reason

    def content(path: str) -> str | None:
        # keel v1 H16/H6 at write time: what this call adds may not be a secret or a disabled test
        rel, full = _locate(root, path)
        if tool == "Write":
            old = None
            try:
                old = full.read_text(errors="replace") if full.is_file() else None
            except OSError:
                pass
            added = rules.checks.added_text(old, str(ti.get("content") or ""))
        elif tool == "MultiEdit":
            added = "\n".join(rules.checks.added_text(str(e.get("old_string") or ""), str(e.get("new_string") or ""))
                              for e in ti.get("edits") or [] if isinstance(e, dict))
        elif tool == "NotebookEdit":
            added = str(ti.get("new_source") or "")
        else:
            added = rules.checks.added_text(str(ti.get("old_string") or ""), str(ti.get("new_string") or ti.get("content") or ""))
        v = rules.check_content(rel, added, cfg)
        return None if v.ok else v.reason

    # v0.18.1 keel's own keys (rules.keel_key_file): no tool reads, searches, lists or writes them
    keyed = _path_arg(ti)
    if keyed and tool != "Bash" and rules.keel_key_file(_locate(root, keyed)[1]):
        return "This is keel's own key folder: agents never read, search or change it."
    # keel 0.18.58 keel's own installed code: no agent reads it to get around a rule (rules.keel_engine_file)
    if keyed and tool != "Bash" and rules.keel_engine_file(_locate(root, keyed)[1], root):
        return rules.ENGINE_REFUSAL
    if tool == "Bash" and rules.keel_engine_words(str(ti.get("command") or ""), root):
        return rules.ENGINE_REFUSAL
    # keel 0.18.64 the other projects registered in this keel (runtime/other_projects.py): read only, their secrets
    # stay out. `others` is absent when the run was not told (KeelBot): then nothing changes here.
    others = ctx.get("others") if isinstance(ctx.get("others"), list) else None
    if readonly and tool in WRITE_TOOLS:
        return ro(run_mode.READONLY_EDIT)
    if tool in WRITE_TOOLS:
        path = _path_arg(ti)
        got = other_projects.find(others, _locate(root, path)[1]) if path and others else None
        if got and Path(_locate(root, path)[0]).is_absolute():
            return other_projects.read_only(path, got[0])
        return (edit(path) or content(path)) if path else None
    if tool == "Read":
        path = _path_arg(ti)
        if not path:
            return None
        rel, full = _locate(root, path)
        ranged = ti.get("offset") is not None or ti.get("limit") is not None
        if others is not None and Path(rel).is_absolute():
            got = other_projects.find(others, full)
            if got:
                refused = other_projects.check_read(got[0], got[1], full, ranged=ranged)
                if refused or rules.is_off(cfg, "read-twice"):
                    return refused
                return already_read(ctx.get("_path"), str(full), full, ti.get("offset"), ti.get("limit"))
            # claude -p refuses a path outside its folders with no reason the model can use: keel says it first
            if ctx.get("outside_refused") and not other_projects.allowed_outside(os.path.realpath(full), _scratch(ctx)):
                return other_projects.outside(path, bool(ctx.get("others_off")))
        v = rules.check_read(rel, cfg, full=full, ranged=ranged, phase=phase)
        refused = knowledge(rel) if v.ok else v.reason
        if refused or rules.is_off(cfg, "read-twice"):
            return refused
        return already_read(ctx.get("_path"), rel, full, ti.get("offset"), ti.get("limit"))
    if tool == "Bash":
        refused = knowledge(str(ti.get("command") or ""))
        if not refused and readonly:
            why = run_mode.readonly_bash(str(ti.get("command") or ""))
            refused = ro(why) if why else None
        if not refused and ctx.get("no_repeat") and ctx.get("_path"):
            # keel 0.18.18 KeelBot: the same failing command, again, while nothing changed (runtime/repeats.py)
            from .runtime import repeats
            refused = repeats.refusal(Path(ctx["_path"]).parent, root, str(ti.get("command") or ""))
        if not refused and others is not None:
            refused = other_projects.check_command(str(ti.get("command") or ""), root, others, cwd=ctx.get("_cwd"),
                                                   outside_refused=bool(ctx.get("outside_refused")),
                                                   off=bool(ctx.get("others_off")), extra=_scratch(ctx))
        if refused:
            return refused
        v = rules.check_bash(phase, str(ti.get("command") or ""), cfg, root=root,
                             exists=lambda rel: (Path(root) / rel).exists(), unlocks=unlocks,
                             push_blockers=ctx.get("push_blockers"))
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
            return ro(run_mode.READONLY_MCP)
        return check_mcp(tool, ti, phase, cfg, edit)
    return None


def _scratch(ctx: dict) -> list[str]:
    """The run's own folder (its guard context is there) and claude's config home (it saves a long tool output there
    and reads it back): a read there is no read outside the project."""
    return ([str(Path(ctx["_path"]).parent)] if ctx.get("_path") else []) + \
        ([os.environ["CLAUDE_CONFIG_DIR"]] if os.environ.get("CLAUDE_CONFIG_DIR") else [])


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
        if path and rules.keel_key_file(path):
            return "This is keel's own key folder: agents never read, search or change it."
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
    if isinstance(call.get("cwd"), str):
        ctx["_cwd"] = call["cwd"]     # where the agent's shell is now (a cd stays for its next commands)
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
