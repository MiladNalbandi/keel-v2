"""The system prompt and task prompt for one agent call."""

from __future__ import annotations

import re
from functools import lru_cache

from .. import config, rules

# Roles for agents keel v1 has no file for.
BUILTIN_ROLES = {
    "contract-author": "You write or update the API contract (OpenAPI or interface notes) for the approved spec. "
                       "Only the contract and notes; no production code, no tests.",
}

GENERIC_ROLE = "You are a careful software engineer working inside a keel flow. Do the step you are given and nothing else."


@lru_cache(maxsize=64)
def _agent_file(home: str, agent: str) -> str | None:
    from pathlib import Path

    f = Path(home) / "agents" / f"{agent}.md"
    if not f.is_file():
        return None
    text = f.read_text()
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).strip()


def role_text(agent: str) -> str:
    return _agent_file(str(config.keel_home()), agent) or BUILTIN_ROLES.get(agent) or GENERIC_ROLE


def system_prompt(agent: str, skills: dict[str, str] | None) -> str:
    parts = [role_text(agent)]
    extra = (skills or {}).get(agent)
    if extra:
        parts.append("## Skills\n\n" + extra)
    parts.append("Commits are made by the engine after a check, never by you. Do not run git commit.")
    return "\n\n".join(parts)


def _allowed(phase: str) -> str:
    row = rules.MATRIX.get(phase, rules.CLOSED)
    allow = [b for b, r in row.items() if b != "*" and r != "deny"]
    return ", ".join(allow) or "nothing"


def task_prompt(*, agent: str, phase: str, step_name: str, title: str, root: str, ac: dict | None,
                acs: list[dict], feedback: str | None, index: int = 0, spec: str | None = None) -> str:
    lines = [f"Project folder: {root}", f"Flow: {title}", f"Step: {step_name} (keel phase: {phase})",
             f"Files you may change in this phase: {_allowed(phase)}. Anything else is put back automatically."]
    if spec:
        lines.append(f"Spec: {spec}")
    if acs:
        lines.append("Acceptance criteria:")
        lines += [f"- {a['id']} [{a.get('layer', 'API')}] {a.get('title', '')} ({a.get('status', 'todo')})" for a in acs]
    if ac:
        lines.append(f"Current criterion: {ac['id']} [{ac.get('layer', 'API')}] {ac.get('title', '')}")
    if phase in ("spec", "triage") and not acs:
        lines.append("Write the spec under docs/specs/ with numbered criteria, one per line, in this form:\n"
                     "- **AC-1** [API] <what must be true>")
    if phase == "red":
        lines.append("Write the failing test for the current criterion only. Name the test after the criterion id. "
                     "Do not write production code.")
    if phase == "green":
        lines.append("Write the minimum production code that makes the current criterion's test pass. Tests are frozen.")
    if index:
        lines.append(f"You are copy {index + 1} of a parallel step; take a different angle from the others.")
    if feedback:
        lines.append(f"This was sent back. Reason:\n{feedback}")
    return "\n".join(lines)


AC_LINE = re.compile(r"\b(AC-\d+)\b\**\s*\[(API|WEB)\]\s*(.+)", re.I)


def parse_acs(text: str) -> list[dict]:
    seen, out = set(), []
    for m in AC_LINE.finditer(text or ""):
        aid = m.group(1).upper()
        if aid in seen:
            continue
        seen.add(aid)
        out.append({"id": aid, "layer": m.group(2).upper(), "title": m.group(3).strip().strip("*").strip(), "status": "todo"})
    return out
