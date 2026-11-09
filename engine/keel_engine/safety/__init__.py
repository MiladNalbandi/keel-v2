"""keel's safety tiers and history (from keel-gate, a tool-call safety harness ported from keel's own guard).

keel's rules (keel_engine/rules) say what a phase, a project and a run allow. On top of them, the guard asks how bad
the worst outcome of a call could be, after reading the call the way a shell would (quotes, $IFS, base64, hex and
printf escapes, eval, sh -c, heredocs into a shell, $(echo rm) as the program):

    T0  reads only          runs
    T1  can be undone       runs
    T2  hard to undo        asks the person when the run can ask (KeelBot); else runs, and the history shows it
    T3  catastrophic        refused, always (built-in rule `catastrophic`, which a project may switch off)

    judge(tool, tool_input, cwd)   the Risk of one call, or None for a tool the tiers do not know (MCP tools)
    history                         every decision, per project, hash-chained (safety/history.py)

Stdlib only: the hook imports it on every tool call.
"""

from __future__ import annotations

from .tiers import Risk, path_risk, shell_risk
from .tier import Tier

RULE = "catastrophic"
REFUSAL_TAIL = "Do not try another way to do this. Stop and tell the person what you wanted to do and why."

SHELL = {"Bash"}
EDIT = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ = {"Read"}
SEARCH = {"Grep", "Glob", "LS"}


def judge(tool: str, ti: dict, cwd: str | None = None) -> Risk | None:
    if tool in SHELL:
        return shell_risk(str(ti.get("command") or ""), cwd)
    if tool in EDIT:
        return path_risk(str(ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""), "edit")
    if tool in READ:
        return path_risk(str(ti.get("file_path") or ti.get("path") or ""), "read")
    if tool in SEARCH:
        return Risk(Tier.T0, "It only reads.", "builtin.read-only")
    return None


def refusal(risk: Risk) -> str:
    return f"{risk.reason} This cannot be undone, so keel never runs it ({risk.rule}, T3). {REFUSAL_TAIL}"


__all__ = ["RULE", "Risk", "Tier", "judge", "refusal"]
