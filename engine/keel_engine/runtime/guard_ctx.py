"""The guard context: what keel's PreToolUse hook (keel_engine.hook) needs to judge one tool call.

    {root, phase, ac: {id, layer} | null, lane, unlocks: [{path, phase, by?, reason?, at?}], agent, thread,
     knowledge_allowed: [section] | null, knowledge_strict: bool, readonly: bool, read_tools: {server: [tool]}}

flow (0.18.64, a flow's agents only): the flow in words, '"<title>" (thread <id>)'; the hook's refusal of a commit names it.

readonly: the run mode is readonly (runtime/run_mode.py): every edit, write, commit and changing shell command is refused.

read_tools: the read tools of the parts' MCP servers, the plugins' too (keel-ci): they pass in every phase. The hook runs
without the plugins loaded, so the engine writes them here.

ask: {url, key, session} in KeelBot's Fix and side modes: a shell command that changes something waits for the person's
OK (runtime/permissions.py asks at `url`, the full address; keel_engine/approvals.py waits); the key only asks, it
cannot answer.

readonly_label, readonly_note (keel 0.18.18, KeelBot's Ask mode): a read-only refusal in its own words, and what to do
instead (runtime/run_mode.py relabel).

no_repeat (keel 0.18.18, KeelBot): the same failing shell command is refused while nothing changed (runtime/repeats.py;
the engine writes the failures into failed.json next to this file).

push_blockers: [{gate, why, fix}] when the run started (runtime/blockers.py for_agents); while not empty the hook refuses
an agent's `git push` / `gh pr create` (keel v1 gates.pushBlockers). keel's own push action is not an agent call.

others, others_off, outside_refused (keel 0.18.64, a flow's agents): the other projects registered in this keel
[{id, name, root}] the agent may read but never change (runtime/other_projects.py); whether the rule other-projects is
off for this project; and whether the CLI refuses a path outside its folders itself (claude -p, with no reason the model
can use), so keel refuses such a read first, in plain words. Absent: the run was not told (KeelBot), nothing changes.

knowledge_*: the agent's knowledge setting (runtime/agent_knowledge.py); with strict on, reading a docs/knowledge
section the agent was not given is refused.

The engine writes it into the run's scratch folder (outside the project, mode 0600) before a CLI agent
runs, and rewrites the same file when an unlock is granted while that agent runs (runtime/service.py
`add_unlocks`). The hook reads it on every tool call, so a rewrite counts from the next call.

0.18.64 the agent runs as the same user as keel, so it could rewrite this file (and turn its phase rules off). What
keel does about that:
    1. the hook refuses every tool call and shell command that writes keel's files in the run's folder (this file, its
       checksum, the hook's settings, mcp.json, the command wrappers) or names $KEEL_GUARD_CTX (hook.control_refusal);
    2. next to the file the engine writes its sha256 (guard.json.sum); a context that does not match it is not used:
       the hook fails closed (only reads pass);
    3. the engine keeps the sha256 of every file it wrote there (SEALED) and checks them when the CLI ends
       (models/cli.py run_cli): a changed file fails the step, so its work is not taken.
A key the agent cannot read is not possible here: the hook is the agent CLI's child, and whatever reaches it (its
environment, its arguments, a file) the agent's own shell can read too (/proc/<pid>/environ, the settings file).
So 2 only catches a careless rewrite; 1 and 3 are the protection, and keel's commit step checks every commit's files
against the phase again (rules.check_commit), whatever the hook allowed.

Stdlib only: the hook imports this module once per tool call.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ENV = "KEEL_GUARD_CTX"
FILE = "guard.json"
SUM = ".sum"            # guard.json.sum: the sha256 of guard.json as the engine wrote it
# The tools Claude Code runs the hook for (opencode's plugin maps its own names onto these). 0.18.64 Grep, Glob and LS
# too: claude's Grep searched .env files (its ripgrep reads hidden files), and the hook never saw it.
MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash|Read|Grep|Glob|LS|mcp__.*"
# keel's own files in the run's folder: no agent reads, writes or names them (hook.control_refusal)
CONTROL_FILES = (FILE, FILE + SUM, "keel-guard.json", "mcp.json")
CONTROL_DIRS = ("keel-bin", "opencode")
# the variables that name those files in the agent's environment
CONTROL_VARS = (ENV, "KEEL_SHIM_DIR", "OPENCODE_CONFIG_DIR")
# path -> sha256 of every control file this engine wrote (seal); run_cli checks them when the CLI ends (broken)
SEALED: dict[str, str] = {}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seal(path: str | Path) -> str:
    """Keeps the sha256 of a control file the engine just wrote; returns the path."""
    p = os.path.realpath(str(path))
    if len(SEALED) > 4000:              # the runs that are over: their folders are gone
        for old in [k for k in SEALED if not os.path.exists(k)]:
            SEALED.pop(old, None)
    try:
        SEALED[p] = digest(Path(p).read_bytes())
    except OSError:
        pass
    return str(path)


def broken(folder: str | Path, forget: bool = False) -> list[str]:
    """The sealed files in `folder` (a run's scratch folder) that are not what the engine wrote: changed or gone.
    `forget`: drop them from SEALED afterwards (the run is over)."""
    home = os.path.realpath(str(folder))
    out = []
    for p, want in list(SEALED.items()):
        if not (p == home or p.startswith(home + os.sep)):
            continue
        try:
            ok = digest(Path(p).read_bytes()) == want
        except OSError:
            ok = False
        if not ok:
            out.append(p)
        if forget:
            SEALED.pop(p, None)
    return sorted(out)


def control_path(ctx_path: str | None, full: str | Path) -> bool:
    """Is `full` one of keel's files in the run's folder (the folder of the guard context `ctx_path`)?"""
    if not ctx_path:
        return False
    home = os.path.realpath(os.path.dirname(ctx_path))
    p = os.path.realpath(str(full))
    if os.path.dirname(p) == home and os.path.basename(p) in CONTROL_FILES:
        return True
    return any(p == os.path.join(home, d) or p.startswith(os.path.join(home, d) + os.sep) for d in CONTROL_DIRS)


def run_folder(ctx_path: str | None, full: str | Path) -> bool:
    """Is `full` in the run's folder at all (a write there is refused: keel's files live there)?"""
    if not ctx_path:
        return False
    home = os.path.realpath(os.path.dirname(ctx_path))
    p = os.path.realpath(str(full))
    return p == home or p.startswith(home + os.sep)


def hook_argv() -> list[str]:
    """The hook as an argv. `-I`: the project folder (the hook's cwd) is not on sys.path, so no file an agent
    writes there can stand in for a module the hook imports."""
    return [sys.executable, "-I", "-m", "keel_engine.hook", "pre-tool"]


def hook_command() -> str:
    """The shell command in Claude Code's settings. Any failure to start becomes exit 2 (a refusal): Claude Code
    lets a call through on other exit codes, and a guard that cannot run must not allow anything."""
    return f'"{sys.executable}" -I -m keel_engine.hook pre-tool || exit 2'


def claude_settings(path: str | Path, timeout: int = 10) -> str:
    """Writes the `--settings` file that loads keel's hook (and nothing else) into a Claude Code run. `timeout`: how long
    one hook call may take (KeelBot's Fix mode waits for the person's OK on a command)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"hooks": {"PreToolUse": [{
        "matcher": MATCHER,
        "hooks": [{"type": "command", "command": hook_command(), "timeout": timeout}],
    }]}}))
    return seal(p)


def write_shims(folder: str | Path) -> str:
    """keel's command wrappers (keel_engine/shim.py) in `folder`: put it first on the PATH of a CLI with no hook keel can
    load (copilot, older codex). Returns the folder."""
    from ..shim import NAMES

    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        p = d / name
        p.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I -m keel_engine.shim {name} "$@"\n')
        p.chmod(0o755)
        seal(p)
    return str(d)


def shim_env(folder: str, path: str | None = None) -> dict:
    """PATH (wrappers first) and KEEL_SHIM_DIR for an agent CLI's environment."""
    return {"PATH": folder + os.pathsep + (path if path is not None else os.environ.get("PATH", "")), "KEEL_SHIM_DIR": folder}


def codex_hook_args(timeout: int = 10) -> list[str]:
    """codex 0.150+ runs Claude-shaped PreToolUse hooks from its config: keel's hook for every tool, given with -c so
    nothing is written into CODEX_HOME. The trust flag: keel writes this hook itself for this run (codex asks a person
    to trust a hook it found in a config file)."""
    entry = '[{matcher=".*",hooks=[{type="command",command=%s,timeout=%d}]}]' % (json.dumps(hook_command()), timeout)
    return ["-c", f"hooks.PreToolUse={entry}", "--dangerously-bypass-hook-trust"]


def _atomic(p: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".guard.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_context(path: str | Path, **ctx) -> str:
    """Atomic write (a hook reading at the same moment sees the old file or the new one, never half), mode 0600; with
    its sha256 next to it (guard.json.sum), written first: a hook that reads between the two sees a mismatch and
    reads the pair again (load_context)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(ctx).encode()
    _atomic(Path(str(p) + SUM), digest(data).encode())
    _atomic(p, data)
    seal(str(p) + SUM)
    return seal(p)


def read_checked(path: str | Path) -> bytes:
    """The context's bytes when they match the sha256 the engine wrote next to them; ValueError when not (changed by
    someone else, or no checksum). A rewrite in progress is read again once."""
    for n in range(3):
        if n:
            time.sleep(0.05)
        data = Path(path).read_bytes()
        try:
            want = Path(str(path) + SUM).read_text().strip()
        except OSError:
            want = ""
        if want and digest(data) == want:
            return data
    raise ValueError("its checksum does not match what keel wrote")


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


def read_tools() -> dict[str, list[str]]:
    """{MCP server: its read tools} of every part the engine loaded, plugins too (keel_engine/extensions.py). The hook
    runs without the plugins (a project's environment never names them), so it reads them here."""
    from .. import extensions

    return {server: sorted(tools) for server, tools in extensions.read_tools().items()}


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
            "readonly": bool(getattr(tb, "readonly", False)), "ask": getattr(tb, "ask", None),
            "confine": bool(getattr(tb, "confine", False)), "read_tools": read_tools(),
            "push_blockers": list(getattr(tb, "push_blockers", None) or []),
            # keel 0.18.64 the other projects (a flow's agents only: KeelBot's ToolBox is not told, others is None)
            **({"others": list(tb.others), "others_off": bool(getattr(tb, "others_off", False)),
                "outside_refused": (req.model or {}).get("provider") == "claude" and (req.model or {}).get("mode", "api") != "api"}
               if getattr(tb, "others", None) is not None else {}),
            # 0.18.64 the flow in words ("Prices in euros" (thread t-1)): a refused commit names it
            **({"flow": tb.flow} if getattr(tb, "flow", "") else {}),
            # keel 0.18.18 KeelBot's only (absent for a flow's agents)
            **{k: v for k, v in (("readonly_label", getattr(tb, "readonly_label", "")),
                                 ("readonly_note", getattr(tb, "readonly_note", "")),
                                 ("no_repeat", bool(getattr(tb, "no_repeat", None)))) if v}}


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
