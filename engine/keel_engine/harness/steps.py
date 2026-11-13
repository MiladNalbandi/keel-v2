"""What the person sees of a harness run: each thing the agent does becomes one step (the kinds the runtime already
forwards as agent.step events and the web shows): text, thinking, read, write, edit, tool, guard, error, answer. Fields
stay small, so the event bus and the web stay fast.
"""

from __future__ import annotations

import json

from .providers.base import Emit

FIELD_MAX = 20_000
READ_LINES = 400
OUTPUT_LINES = 300


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


def diff_stat(diff: str) -> str:
    add = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    rem = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return f"+{add} -{rem}"


def tool_step(emit: Emit, box, call: dict, out: str, ms: int, writes_before: int) -> None:
    """One tool call as a step. A refusal by keel's rules is a guard step; a file change carries its diff."""
    name, args = call["name"], call.get("input") or {}
    path = args.get("file_path") or args.get("path")
    if out.startswith("REFUSED"):
        emit("guard", out, tool=name, path=path, ok=False, ms=ms,
             command=str(args["command"])[:500] if args.get("command") else None)
        return
    ok = not out.startswith("ERROR")
    if name in ("Write", "Edit") and ok and len(box.writes) > writes_before:
        w = box.writes[-1]
        diff = w.get("diff") or ""
        kind = "write" if w.get("new") else "edit"
        emit(kind, f"{name} {w['path']} ({diff_stat(diff)})", path=w["path"], diff=cap(diff), ms=ms, ok=True)
        return
    if name == "Read" and ok:
        emit("read", head_lines(out, READ_LINES), path=path, ms=ms, ok=True)
        return
    if name.startswith("mcp__"):
        _, server, tool = (name.split("__", 2) + ["", ""])[:3]
        emit("tool", cap(json.dumps(args, ensure_ascii=False), 2000) if args else "", tool=tool, server=server,
             output=head_lines(out, OUTPUT_LINES), ms=ms, ok=ok)
        return
    if name in ("Bash", "RunTests"):
        ok = ok and (out.startswith("exit 0") or "\nexit 0\n" in out[:400])
    target = args.get("command") or args.get("pattern") or args.get("url") or args.get("description") or path or args.get("ac") or ""
    emit("tool", cap(str(target), 4000), tool=name, path=path, output=head_lines(out, OUTPUT_LINES), ms=ms, ok=ok)


def todo_text(items: list[dict]) -> str:
    mark = {"completed": "[x]", "in_progress": "[~]", "pending": "[ ]"}
    return "Task list:\n" + "\n".join(f"- {mark.get(t.get('status'), '[ ]')} {t.get('content', '')}" for t in items)
