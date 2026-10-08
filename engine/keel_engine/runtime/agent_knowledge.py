"""Which project knowledge each agent uses (plan 5c).

    knowledge: {sections: [architecture, domain, ...], code_graph: bool, memory: bool, strict: bool, hints: bool}

The default is the `knowledge:` block in the agent's front matter (content/agents/<agent>.md); the API sends the
project's value (its override merged over that default) in StartThread.agents. The task prompt lists the allowed
sections that exist; the code graph MCP server is only handed to agents with code_graph on; memory off means a
repeated step starts fresh; strict makes the guard refuse other sections (rules.check_knowledge), else the agent is
only told; hints (off by default, see FALLBACK) gives the agent keel's own "where to look" lookups in the code graph
(runtime/graph_hints.py), which cost no tool call.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from .. import addons, config, rules

SECTIONS = rules.KNOWLEDGE_SECTIONS
CODEGRAPH = "codegraph"   # the MCP server name of the code graph (stage 5b)

# One line per section for the prompt: what an agent finds there.
PURPOSE = {
    "architecture": "where code lives, its layers and the style it follows",
    "domain": "what the system is for and the words the business uses",
    "conventions": "how code here is written: naming, errors, tests",
    "data": "tables, migrations and how data is stored",
    "integrations": "everything outside this process and what stands in for it in tests",
    "journeys": "what users do end to end, and where it stops",
}

# Agents with no file in content/agents (prompts.BUILTIN_ROLES) and the fallback for any other agent.
BUILTIN = {"contract-author": {"sections": ["domain", "integrations"], "code_graph": False}}
# hints is off by default: measured 2026-10-06 on ludus (Helper Ask, Haiku, 3 questions x 2 runs each way) it saved no
# tokens (153k with, 150k without) and the answers named the same code. Turn it on per agent in the Agents drawer.
FALLBACK = {"sections": ["architecture"], "code_graph": False, "memory": True, "strict": False, "hints": False}


def normalize(raw: dict | None, base: dict | None = None) -> dict:
    """A full setting: fields missing from `raw` come from `base` (else FALLBACK); unknown section names are dropped."""
    base = {**FALLBACK, **(base or {})}
    raw = raw or {}
    sections = raw.get("sections", base["sections"])
    return {"sections": [s for s in SECTIONS if s in (sections or [])],
            "code_graph": bool(raw.get("code_graph", base["code_graph"])),
            "memory": bool(raw.get("memory", base["memory"])),
            "strict": bool(raw.get("strict", base["strict"])),
            "hints": bool(raw.get("hints", base["hints"]))}


@lru_cache(maxsize=64)
def _front_matter(path: str, mtime: float) -> dict:
    m = re.match(r"\A---\n(.*?)\n---\n", Path(path).read_text(), flags=re.S)
    try:
        fm = yaml.safe_load(m.group(1)) if m else None
    except yaml.YAMLError:
        fm = None
    return fm if isinstance(fm, dict) else {}


def default_for(agent: str) -> dict:
    """The agent's default from its front matter (or the builtin table)."""
    f = addons.agent_path(agent)
    if f.is_file():
        k = _front_matter(str(f), f.stat().st_mtime).get("knowledge")
        if isinstance(k, dict):
            return normalize(k)
    return normalize(BUILTIN.get(agent))


def for_agent(agent: str, sent: dict | None) -> dict:
    """What the agent uses in this thread: StartThread.agents[agent].knowledge over the default."""
    k = ((sent or {}).get(agent) or {}).get("knowledge")
    return normalize(k, default_for(agent)) if isinstance(k, dict) else default_for(agent)


def files(root: str, sections: list[str]) -> list[Path]:
    """The allowed section files that exist in the project."""
    d = Path(root) / "docs" / "knowledge"
    return [d / f"{s}.md" for s in sections if (d / f"{s}.md").is_file()]


def tokens(root: str, sections: list[str]) -> int:
    """Rough token cost of reading the sections whole: file size / 4."""
    return sum(f.stat().st_size for f in files(root, sections)) // 4


def has_codegraph(specs: list[dict] | None, allow: list[str] | None) -> bool:
    return any((s or {}).get("name") == CODEGRAPH for s in specs or []) or \
        any(str(a).split(":")[1:2] == [CODEGRAPH] for a in allow or [])


def filter_mcp(specs: list[dict] | None, allow: list[str] | None, k: dict) -> tuple[list[dict], list[str]]:
    """The code graph server and its allow entries stay only when the agent's code_graph is on."""
    specs, allow = list(specs or []), list(allow or [])
    if k.get("code_graph"):
        return specs, allow
    return ([s for s in specs if (s or {}).get("name") != CODEGRAPH],
            [a for a in allow if str(a).split(":")[1:2] != [CODEGRAPH]])


def prompt_block(root: str, k: dict, graph: bool) -> str:
    """The "Knowledge you can use" lines of the task prompt. Short on purpose: it is in every call."""
    lines = []
    have = files(root, k["sections"])
    if have:
        lines.append("Knowledge you can use (keel's notes on this project; read only the part you need and trust its "
                     "file:line citations):")
        lines += [f"- {f}: {PURPOSE[f.stem]}" for f in have]
    elif k["sections"]:
        lines.append(f"Knowledge you can use: none of your sections ({', '.join(k['sections'])}) exist in "
                     "docs/knowledge/ yet, so read the code.")
    if graph and k["code_graph"]:
        lines.append("Code graph: codegraph_search finds where a class, function or route is (file:line, no code); then "
                     "read only those lines (Read with offset and limit) instead of whole files. codegraph_callers, "
                     "codegraph_callees and codegraph_impact list who calls what and what a change touches.")
    if k["memory"]:
        lines.append("Memory: if you ran this step before, you continue that session (or get its summary); do not redo "
                     "finished work.")
    return "\n".join(lines)
