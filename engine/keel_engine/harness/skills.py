"""Skills and agent types for the harness (#101), the way a coding agent knows them:

- a skill is a folder with SKILL.md (front matter `name`, `description`) and, often, references/*.md. The model sees
  each skill's name and description in the Skill tool; it loads one (its SKILL.md, or one of its files) only when the
  task needs it, so the context stays small. Skills come from keel's library (content/skills) and from the project's own
  .claude/skills and .keel/skills; a project skill goes through the agent's read rules like any project file;
- an agent type is one of keel's agents (content/agents, the add-ons', the plugins'): Task runs a helper as that agent,
  with its role text and its turn limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .. import config

_FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
MAX_LISTED = 80


def front(text: str) -> dict:
    m = _FRONT.match(text or "")
    out: dict = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip("\"'")
    return out


def body(text: str) -> str:
    return _FRONT.sub("", text or "", count=1).strip()


@dataclass
class Skill:
    name: str
    description: str
    folder: Path
    project: bool          # the project's own (its files go through the read rules)


def find_skills(root: str) -> dict[str, Skill]:
    """keel's library first, then the project's own (a project skill with a library name replaces it here)."""
    found: dict[str, Skill] = {}
    places = [(config.v2_skills(), False), (Path(root) / ".claude" / "skills", True), (Path(root) / ".keel" / "skills", True)]
    for base, project in places:
        if not base.is_dir():
            continue
        for f in sorted(base.glob("*/SKILL.md")):
            try:
                meta = front(f.read_text(errors="replace"))
            except OSError:
                continue
            name = meta.get("name") or f.parent.name
            found[name] = Skill(name, meta.get("description") or "", f.parent, project)
    return found


def skill_list(skills: dict[str, Skill]) -> str:
    rows = [f"- {s.name}: {s.description[:240]}" for s in list(skills.values())[:MAX_LISTED]]
    return "\n".join(rows) or "(none)"


def load_skill(box, skills: dict[str, Skill], name: str, file: str | None = None) -> str:
    """A skill's SKILL.md (or one of its files): what the model reads when it uses the skill."""
    s = skills.get(name)
    if s is None:
        return f"ERROR: no skill called {name!r}. Skills: {', '.join(skills) or 'none'}."
    target = (s.folder / (file or "SKILL.md")).resolve()
    if s.folder.resolve() not in target.parents and target != (s.folder / "SKILL.md").resolve():
        return f"ERROR: {file} is not a file of the skill {name}."
    if s.project:
        from .. import rules

        rel = target.relative_to(Path(box.root).resolve()).as_posix()
        v = rules.check_read(rel, box.cfg, full=target, ranged=True, phase=box.phase)
        if not v.ok:
            return box._refuse("Skill", rel, v.reason)
    if not target.is_file():
        files = sorted(p.relative_to(s.folder).as_posix() for p in s.folder.rglob("*") if p.is_file())
        return f"ERROR: the skill {name} has no {file}. Its files: {', '.join(files)}."
    text = target.read_text(errors="replace")
    files = sorted(p.relative_to(s.folder).as_posix() for p in s.folder.rglob("*") if p.is_file() and p.name != "SKILL.md")
    more = f"\n\n(The skill's other files, for Skill with file=…: {', '.join(files)})" if files and not file else ""
    return f"<skill name=\"{name}\"{f' file=\"{file}\"' if file else ''}>\n{body(text) if not file else text}\n</skill>{more}"


@dataclass
class AgentType:
    name: str
    description: str
    role: str
    writes: bool           # its file gives it Write or Edit
    max_turns: int | None


def agent_types() -> dict[str, AgentType]:
    from .. import addons

    out: dict[str, AgentType] = {}
    for f in addons.agent_files():
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        meta = front(text)
        tools = {t.strip() for t in (meta.get("tools") or "").strip("[]").split(",")}
        turns = meta.get("maxTurns")
        out[f.stem] = AgentType(f.stem, meta.get("description") or "", body(text), bool({"Write", "Edit"} & tools),
                                int(turns) if turns and turns.isdigit() else None)
    return out
