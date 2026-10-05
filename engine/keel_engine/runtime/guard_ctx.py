"""The guard context: what keel's PreToolUse hook (keel_engine.hook) needs to judge one tool call.

    {root, phase, ac: {id, layer} | null, lane, unlocks: [{path, phase, by?, reason?, at?}], agent, thread,
     knowledge_allowed: [section] | null, knowledge_strict: bool, readonly: bool}

readonly: the run mode is readonly (runtime/run_mode.py): every edit, write, commit and changing shell command is refused.

knowledge_*: the agent's knowledge setting (runtime/agent_knowledge.py); with strict on, reading a docs/knowledge
section the agent was not given is refused.

The engine writes it into the run's scratch folder (outside the project, mode 0600) before a CLI agent
runs, and rewrites the same file when an unlock is granted while that agent runs (runtime/service.py
`add_unlocks`). The hook reads it on every tool call, so a rewrite counts from the next call.

Stdlib only: the hook imports this module once per tool call.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ENV = "KEEL_GUARD_CTX"
FILE = "guard.json"
# The tools Claude Code runs the hook for (opencode's plugin maps its own names onto these).
MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash|Read|mcp__.*"


def hook_argv() -> list[str]:
    """The hook as an argv. `-I`: the project folder (the hook's cwd) is not on sys.path, so no file an agent
    writes there can stand in for a module the hook imports."""
    return [sys.executable, "-I", "-m", "keel_engine.hook", "pre-tool"]


def hook_command() -> str:
    """The shell command in Claude Code's settings. Any failure to start becomes exit 2 (a refusal): Claude Code
    lets a call through on other exit codes, and a guard that cannot run must not allow anything."""
    return f'"{sys.executable}" -I -m keel_engine.hook pre-tool || exit 2'


def claude_settings(path: str | Path) -> str:
    """Writes the `--settings` file that loads keel's hook (and nothing else) into a Claude Code run."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"hooks": {"PreToolUse": [{
        "matcher": MATCHER,
        "hooks": [{"type": "command", "command": hook_command(), "timeout": 10}],
    }]}}))
    return str(p)


def write_context(path: str | Path, **ctx) -> str:
    """Atomic write (a hook reading at the same moment sees the old file or the new one, never half), mode 0600."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".guard.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(ctx, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return str(p)


def merge(have: list[dict], new: list[dict]) -> list[dict]:
    seen = {(u.get("path"), u.get("phase")) for u in have}
    return list(have) + [u for u in new if (u.get("path"), u.get("phase")) not in seen]


class GuardFile:
    """One live context file. `add_unlocks` rewrites it; the engine calls that for every run of the thread."""

    def __init__(self, path: str | Path, **ctx):
        self.path = str(path)
        self.ctx = {**ctx, "unlocks": list(ctx.get("unlocks") or [])}
        write_context(self.path, **self.ctx)

    def add_unlocks(self, new: list[dict]):
        merged = merge(self.ctx["unlocks"], new)
        if len(merged) != len(self.ctx["unlocks"]):
            self.ctx["unlocks"] = merged
            write_context(self.path, **self.ctx)


def context_for(req) -> dict:
    """The context of one AgentRequest (its ToolBox carries the lane and the unlocks)."""
    tb = req.toolbox
    ac = req.ac or None
    k = getattr(req, "knowledge", None)
    return {"root": str(Path(req.root).resolve()), "phase": req.phase or "none",
            "ac": {"id": ac.get("id"), "layer": ac.get("layer", "API")} if ac else None,
            "lane": getattr(tb, "lane", None), "unlocks": list(getattr(tb, "unlocks", None) or []),
            "agent": req.agent, "thread": getattr(req, "thread", "") or "",
            "knowledge_allowed": list(k["sections"]) if k else None, "knowledge_strict": bool(k and k.get("strict")),
            "readonly": bool(getattr(tb, "readonly", False))}


def ensure(req, folder: str | None = None) -> str:
    """The run's context file: the one the engine wrote for it, else a new `<scratch>/guard.json` from the request."""
    if getattr(req, "guard_ctx", ""):
        return req.guard_ctx
    return write_context(Path(folder or scratch(req)) / FILE, **context_for(req))


def scratch(req) -> str:
    """The run's scratch folder for keel's files; never inside the project (an agent could edit them there)."""
    folder = req.workdir
    if folder:
        try:
            Path(folder).resolve().relative_to(Path(req.root).resolve())
            folder = ""
        except ValueError:
            pass
    return folder or tempfile.mkdtemp(prefix="keel-run-")
