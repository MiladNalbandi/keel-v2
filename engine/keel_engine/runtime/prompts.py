"""The system prompt and task prompt for one agent call."""

from __future__ import annotations

import re

from ..tools import testcmd
from functools import lru_cache

from .. import config, rules

# Roles for agents that have no file in content/agents.
BUILTIN_ROLES = {
    "contract-author": "You write or update the contract for the approved spec: only the outside surface the criteria "
                       "change (HTTP routes, wire schemas, public types or interfaces). Keep it short; do not write "
                       "design documents, production code or tests. If nothing outside changes, say so and stop.",
}

GENERIC_ROLE = "You are a careful software engineer working inside a keel flow. Do the step you are given and nothing else."


@lru_cache(maxsize=64)
def _agent_file(content: str, agent: str) -> str | None:
    from pathlib import Path

    f = Path(content) / "agents" / f"{agent}.md"
    if not f.is_file():
        return None
    text = f.read_text()
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).strip()


# Steps where an agent does more than its file's role: the explorer only maps code (20 turns), but in the spec and
# triage steps it also writes the spec.
WRITING_TURNS = {("explorer", "spec"): 40, ("explorer", "triage"): 40}


def max_turns(agent: str, phase: str | None = None) -> int | None:
    """maxTurns from the agent file's front matter (explorer: 20), so one step cannot run away."""
    f = config.content_dir() / "agents" / f"{agent}.md"
    n = None
    if f.is_file():
        m = re.search(r"\A---\n.*?^maxTurns:\s*(\d+)\s*$.*?\n---\n", f.read_text(), flags=re.S | re.M)
        n = int(m.group(1)) if m else None
    more = WRITING_TURNS.get((agent, phase or ""))
    return max(n or 0, more) if more and n is not None else n


def role_text(agent: str) -> str:
    return _agent_file(str(config.content_dir()), agent) or BUILTIN_ROLES.get(agent) or GENERIC_ROLE


def skill_paths(text: str) -> str:
    """`references/x.md` in a skill → its full path in keel's skills folder, so an agent opens it instead of
    searching the whole disk for it (one did: `find / -iname acceptance-criteria.md`). A name two skills share
    stays as written."""
    found: dict[str, list[str]] = {}
    for f in sorted(config.v2_skills().glob("*/references/*.md")):
        found.setdefault(f.name, []).append(str(f))

    def full(m):
        hits = found.get(m.group(1)) or []
        return hits[0] if len(hits) == 1 else m.group(0)
    return re.sub(r"(?<![\w/])references/([\w.-]+\.md)", full, text)


def system_prompt(agent: str, skills: dict[str, str] | None) -> str:
    parts = [role_text(agent)]
    extra = (skills or {}).get(agent)
    if extra:
        parts.append("## Skills\n\n" + skill_paths(extra) + "\n\nSkill files are under "
                     f"{config.v2_skills()}/; open them by that path, never search the disk for them.")
    parts.append("Commits are made by the engine after a check, never by you. Do not run git commit.")
    return "\n\n".join(parts)


def _allowed(phase: str) -> str:
    row = rules.MATRIX.get(phase, rules.CLOSED)
    allow = [b for b, r in row.items() if b != "*" and r != "deny"]
    return ", ".join(allow) or "nothing"


def task_prompt(*, agent: str, phase: str, step_name: str, title: str, root: str, ac: dict | None,
                acs: list[dict], feedback: str | None, index: int = 0, spec: str | None = None,
                section: str | None = None, unlocks: list[dict] | None = None, request: str = "") -> str:
    lines = [f"Project folder: {root}", f"Flow: {title}", f"Step: {step_name} (keel phase: {phase})",
             "How this works: the keel engine runs the tests, makes the commits and moves between phases after you. "
             "Do not run keel commands, do not run git commit, reset, checkout or stash, and do not read or change "
             "anything under .keel/. Do only what this step asks, then stop with a short summary (a few lines).",
             f"Files you may change in this phase: {_allowed(phase)}. Anything else is put back automatically.",
             "Read with care for tokens: find what you need with grep -n (or the code index tools, when you have "
             "them) and read only those lines; never read a large file whole, and never read the same file twice."]
    if request:
        lines.append("What the user asked for:\n" + request)
    from pathlib import Path as _P
    if (_P(root) / "docs" / "knowledge").is_dir() and not section:
        lines.append("keel's memory of this project is in docs/knowledge/ (start with index.md if it exists, then only the "
                     "section you need). Read it before exploring the code and trust its file:line citations.")
    mine = [u["path"] for u in unlocks or [] if u.get("phase") == phase]
    if mine:
        lines.append("Unlocked for this phase by the user: " + ", ".join(mine))
    if section:
        lines.append(f"Knowledge section: {section}. Rewrite docs/knowledge/{section}.md from the code, "
                     "with a file:line citation behind every claim. Touch no other file.")
    if agent == "librarian":
        tpl = config.content_dir() / "templates" / "knowledge"
        lines.append(f"Section templates: {tpl}/<section>.md (read only the ones you write). Do not read keel's own "
                     "source code; everything you need is in the project and these templates.")
        chosen = ((rules.load_config(root) or {}).get("init") or {}).get("knowledge_sections")
        if phase == "setup" and not section and isinstance(chosen, list):
            lines.append("Knowledge sections the user chose: " + (", ".join(chosen) if chosen else "none — write nothing")
                         + ". Write only these, one file each under docs/knowledge/.")
        if phase == "memory" and not section:
            lines.append("At the end of a flow: update only the knowledge sections this branch changes (see git diff "
                         "against the base branch), and only with facts from this branch. Keep each section short.")
    if spec:
        lines.append(f"Spec: {spec}")
    if acs:
        lines.append("Acceptance criteria:")
        lines += [f"- {a['id']} [{a.get('layer', 'API')}] {a.get('title', '')} ({a.get('status', 'todo')})" for a in acs]
    if ac:
        lines.append(f"Current criterion: {ac['id']} [{ac.get('layer', 'API')}] {ac.get('title', '')}")
    if phase in ("spec", "triage") and feedback and spec:
        lines.append(f"The spec {spec} exists from the last try and was sent back with the note below. Change that "
                     "file to answer the note. Read only what the note needs; do not explore the project again.")
    if phase in ("spec", "triage") and not acs:
        lines.append("Keep the reading short: the request, the README, docs/knowledge/ if it exists, and the files the "
                     "request names. Write the spec file early (before half of your turns are used), then improve it; "
                     "a spec that exists beats a perfect map of the code.")
        lines.append("Before the criteria: if something the code cannot answer would change them (what \"user\" means, where a "
                     "filter runs, two reasonable behaviours), write NO spec and end your answer with one ```keel-questions "
                     "block (max 4 questions, 2-4 options each, the recommended one first, valid JSON; the format is in the "
                     "spec-clarify skill). The user clicks and you continue this conversation. Ask only what changes the criteria.")
        lines.append("Each criterion is a behaviour a test can check from the outside (what a caller or user sees), "
                     "not a step of the work: \"imports are updated\" or \"all calls use the new name\" are steps, and so "
                     "is \"behaviour stays the same\" when the existing tests already prove it. A small change usually "
                     "needs one to three criteria.")
        lines.append("Write the spec under docs/specs/ with numbered criteria, one per line, in this form:\n"
                     "- **AC-1** [API] <what must be true>\n"
                     "Write real criteria for what the user asked for. If the request is too unclear to write any, write no "
                     "criteria and say in one or two sentences what you need to know.")
    test_cmd = testcmd.command_for(root, ac["id"], ac.get("layer", "API")) if ac else None
    if phase == "red":
        lines.append("Write the failing test for the current criterion only. Put the criterion id in the test's name "
                     f"(for example \"{(ac or {}).get('id', 'AC-1')} ...\"), so the engine can run it alone. Do not write production code.")
        if test_cmd:
            lines.append(f"The engine runs it with: {test_cmd}  (run it once yourself to see it fail for the right reason).")
    if phase == "green":
        lines.append("Write the minimum production code that makes the current criterion's test pass. Tests are frozen.")
        later = [a["id"] for a in acs or [] if ac and a.get("status") in (None, "todo") and a["id"] != ac["id"]]
        if later:
            lines.append(f"Do not build behaviour for later criteria ({', '.join(later)}): each gets its own failing test "
                         "first. Edge cases they name (ties, empty input, errors) are not this step's job unless this "
                         "criterion's test needs them.")
        if test_cmd:
            lines.append(f"The engine checks it with: {test_cmd}  (run it to confirm, then stop).")
    if index and not section:
        lines.append(f"You are copy {index + 1} of a parallel step; take a different angle from the others.")
    if feedback and feedback.startswith("Answers to your questions:"):
        lines.append(feedback)
    elif feedback:
        lines.append(f"This was sent back. Reason:\n{feedback}")
    return "\n".join(lines)


AC_LINE = re.compile(r"\b(AC-\d+)\b\**\s*\[(API|WEB)\]\s*(.+)", re.I)


PLACEHOLDER = re.compile(r"^\s*<[^>]*>\s*$|what must be true|^\s*(tbd|todo|\.\.\.|…)\s*$", re.I)


def parse_acs(text: str) -> list[dict]:
    """Numbered criteria from a spec. Examples in code blocks and template placeholders such as
    "<behaviour>" are not criteria (an agent may quote an old template while explaining)."""
    seen, out = set(), []
    text = re.sub(r"```.*?(```|\Z)", "", text or "", flags=re.S)
    # A criterion may wrap over indented follow-up lines; join them so the title is the whole sentence.
    joined, out_lines = [], text.splitlines()
    for line in out_lines:
        if joined and AC_LINE.search(joined[-1]) and line.startswith((" ", "\t")) and line.strip() \
                and not re.match(r"\s*([-*+]|\d+\.)\s", line):
            joined[-1] = joined[-1].rstrip() + " " + line.strip()
        else:
            joined.append(line)
    text = "\n".join(joined)
    for m in AC_LINE.finditer(text):
        aid = m.group(1).upper()
        title = m.group(3).strip().strip("*").strip()
        if aid in seen or PLACEHOLDER.search(title) or len(title) < 3:
            continue
        seen.add(aid)
        out.append({"id": aid, "layer": m.group(2).upper(), "title": title, "status": "todo"})
    return out
