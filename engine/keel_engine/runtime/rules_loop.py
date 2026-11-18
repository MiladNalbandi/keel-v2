"""0.40 (#201) The loop guard: an agent that keel's rules block the same way again and again stops, and the person decides.

An agent blocked by the same rule BLOCKS times in one step loops ("I cannot do this", another try, the same refusal)
and the flow never ends. So the third refusal of the same kind stops the step (the harness ends the agent's turn), and
the step's next run asks the person first:

    allow   Allow it this once        the step runs again in Safety only, this one time (keel-gate's T3 stays refused)
    safety  Switch this flow to Safety only   from now on, for this flow only (not the project's default)
    retry   Try the step again        when the rule is one Safety only keeps (a secret, T3): nothing to lift
    stop    Stop the flow

Works for both rules modes: in Safety only only the safety rules can block, so only retry and stop are offered.
Stdlib only, plus keel_engine.rules (the hook imports it on every tool call).
"""

from __future__ import annotations

import re

BLOCKS = 3
STOP = ("keel stopped this step: the same rule blocked you {n} times. Do not try again or another way around it. End "
        "your turn now with one line: what you wanted to do and why. keel asks the person what to do.")
_T3 = re.compile(r"so keel never runs it \([\w.-]+, T3\)")


def key(reason: str, path: str = "", command: str = "") -> str:
    """The rule a refusal comes from, without what it was about: the same rule on another file (or with another number
    in the command) is the same block."""
    k = str(reason or "")
    for x in (command, path):
        if x and len(x) > 2:
            k = k.replace(x, "…")
    k = re.sub(r"`[^`]*`|\"[^\"]*\"", "…", k)
    k = re.sub(r"\S+/\S+", "…", k)        # paths
    return re.sub(r"\d+", "#", k).strip()[:300]


class Counter:
    """One agent run's refusals by rule; `loop` is the third of a kind (None until then)."""

    def __init__(self, blocks: int = BLOCKS):
        self.blocks = blocks
        self.seen: dict[str, int] = {}
        self.loop: dict | None = None

    def add(self, tool: str, path: str, reason: str, command: str | None = None) -> dict | None:
        """The loop when this refusal makes it (once per run), else None."""
        k = key(reason, path, command or "")
        n = self.seen[k] = self.seen.get(k, 0) + 1
        if n >= self.blocks and self.loop is None:
            self.loop = {"tool": tool, "path": path or "", "command": command or "", "reason": str(reason)[:1000],
                         "count": n, "t3": bool(_T3.search(str(reason)))}
            return self.loop
        return None


def lifted(loop: dict, phase: str, cfg: dict, root: str) -> bool:
    """Would Safety only let this call through? False for keel-gate's T3 and for the rules Safety only keeps (a secret,
    keel's keys, its rule files, git settings ...)."""
    from .. import rules

    if loop.get("t3") or rules.safety_only(cfg):
        return False
    so = rules.with_mode(cfg, rules.SAFETY_ONLY)
    tool, path, command = str(loop.get("tool") or ""), str(loop.get("path") or ""), str(loop.get("command") or "")
    try:
        if command:
            from ..hook import catastrophic

            v = rules.check_bash(phase, command, so, root=root)
            return v.ok and catastrophic("Bash", {"command": command}, so, root) is None
        if re.search(r"write|edit", tool, re.I) and path:
            return rules.check_edit(phase, path, so).ok
        if re.search(r"read", tool, re.I) and path:
            return rules.check_read(path, so).ok
    except Exception:  # noqa: BLE001 - when keel cannot tell, it does not offer to lift the rule
        return False
    return not re.search(r"grep|search", tool, re.I)     # a Grep is refused for a secret file only


def choices(loop: dict) -> list[str]:
    return ["allow", "safety", "stop"] if loop.get("lifted") else ["retry", "stop"]


def question(step_id: str, step_name: str, agent: str, loop: dict) -> dict:
    """The pause the step's next run makes (kind failure: no run mode answers it)."""
    what = loop.get("command") or loop.get("path") or loop.get("tool") or "a call"
    n = int(loop.get("count") or BLOCKS)
    info = []
    if loop.get("lifted"):
        info += [{"name": "allow", "label": "Allow it this once",
                  "description": f"{step_name} runs again in Safety only this one time: keel's phase rules and rule packs "
                                 "are off for it; anything that cannot be undone is still refused"},
                 {"name": "safety", "label": "Switch this flow to Safety only",
                  "description": "from now on this flow keeps only keel's safety rules (the project's default stays)"}]
    else:
        info.append({"name": "retry", "label": "Try the step again",
                     "description": "this rule is one keel always keeps (it protects data, secrets or keel itself): the "
                                    "agent tries again and is told to do it another way; your note goes with it"})
    info.append({"name": "stop", "label": "Stop the flow", "description": "stop the flow here (the commits so far stay)"})
    info = [{**c, "default": k == 0} for k, c in enumerate(info)]
    kept = "" if loop.get("lifted") else (" keel-gate refuses it in any mode: it cannot be undone." if loop.get("t3") else
                                          " It is one of the rules Safety only keeps.")
    detail = (f"keel's rules blocked {agent or 'the agent'} {n} times in {step_name}, the same way each time, so keel "
              f"stopped the step instead of letting it try again.\n\nWhat it tried: `{str(what)[:300]}`\n"
              f"Why keel refused: {str(loop.get('reason') or '')[:600]}{kept}")
    return {"step": step_id, "kind": "fix", "type": "rules-loop", "title": f"The same rule blocked {step_name} {n} times",
            "detail": detail, "options": ["approve", "reject"], "choices": [c["name"] for c in info], "choice_info": info}
