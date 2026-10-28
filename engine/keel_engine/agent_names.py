"""Agent names that changed, and how an old one still reaches its agent.

keel gave the four review agents names that say when each one runs. A workflow, a setting or a call that
still says the old name gets the new agent: workflows (keel's, a project's own, the marketplace's) are read through
`canonical`, per-agent dicts (models, skills, knowledge) through `canonical_keys`, so nothing that was saved under an
old name is lost. Old names are never listed as agents of their own. The api has the same table
(api/src/main/kotlin/keel/api/agents/AgentNames.kt).
"""

from __future__ import annotations

from typing import Any, Mapping

# old name -> new name
RENAMED: dict[str, str] = {
    "ac-reviewer": "criterion-reviewer",     # one acceptance criterion's red and green commits, during a flow
    "code-reviewer": "branch-reviewer",      # the whole branch diff, once, at the end of a flow
    "reviewer": "lens-reviewer",             # a diff against the spec, one lens at a time
    "ticket-reviewer": "pr-reviewer",        # someone else's pull request (the review-pr flow)
}

OLD_NAMES = frozenset(RENAMED)


def canonical(name: str | None) -> str | None:
    """The agent's current name: an old name gives its new one, any other name stays as it is."""
    if not name:
        return name
    return RENAMED.get(name, name)


def is_old(name: str | None) -> bool:
    return bool(name) and name in RENAMED


def canonical_keys(d: Mapping[str, Any] | None) -> dict[str, Any]:
    """A per-agent dict with every old-name key moved to its new name; when both are there, the new name's value wins."""
    out: dict[str, Any] = {}
    for k, v in (d or {}).items():
        if k not in RENAMED:
            out[k] = v
    for k, v in (d or {}).items():
        if k in RENAMED:
            out.setdefault(RENAMED[k], v)
    return out


def lookup(d: Mapping[str, Any] | None, agent: str | None) -> Any:
    """d[agent] under its new name, else under its old name (a value saved before the rename)."""
    if not d or not agent:
        return None
    new = canonical(agent)
    if new in d:
        return d[new]
    return next((d[old] for old, n in RENAMED.items() if n == new and old in d), None)


# 0.18.64 the words people use for a job, and the agent that does it. "planning" named no agent: planning is the
# explorer in the spec phase, and the explorer also triages, sets up (init) and asks the clarify questions, so a
# project-wide change of the explorer changes all of those. A role with a phase lets keel_agent_model change only the
# steps of that phase in one flow (thread_id). MCP tools accept these words and print what they map to; workflows and
# settings keep the agent ids.
ROLES: dict[str, tuple[str, str | None, str]] = {
    "planning": ("explorer", "spec", "reads the code and writes the spec"),
    "planner": ("explorer", "spec", "reads the code and writes the spec"),
    "spec": ("explorer", "spec", "reads the code and writes the spec"),
    "triage": ("explorer", "triage", "sizes a change"),
    "coding": ("implementer", None, "writes the code"),
    "coder": ("implementer", None, "writes the code"),
    "implementation": ("implementer", None, "writes the code"),
    "testing": ("test-author", None, "writes the failing tests first"),
    "tests": ("test-author", None, "writes the failing tests first"),
    "contract": ("contract-author", None, "writes the API contract"),
    "debugging": ("investigator", None, "finds the cause of a bug"),
    "research": ("web-researcher", None, "reads documentation on the web"),
    "docs": ("librarian", None, "writes the project knowledge"),
    "security": ("security-auditor", None, "checks the branch for security problems"),
    "final-review": ("branch-reviewer", None, "reviews the whole branch at the end"),
}


def role(name: str | None) -> dict | None:
    """{alias, agent, phase, about} for a role word (planning, coding, ...), else None."""
    key = (name or "").strip().lower().replace(" ", "-")
    if key not in ROLES:
        return None
    agent, phase, about = ROLES[key]
    return {"alias": key, "agent": agent, "phase": phase, "about": about}


def role_text(r: dict) -> str:
    """'planning = the explorer in the spec phase (reads the code and writes the spec)'."""
    where = f" in the {r['phase']} phase" if r.get("phase") else ""
    return f"{r['alias']} = the {r['agent']}{where} ({r['about']})"
