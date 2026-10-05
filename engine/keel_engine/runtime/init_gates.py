"""keel init's two pauses with real content: the three questions (keel v1 skills/init step 2) and the setup plan.

The questions are asked once, with the defaults keel detected; the user approves the defaults or answers in their
own words. The answers are written to .keel/config.yml (init:), where the librarians read the chosen sections.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..tools import testcmd

SECTIONS = ["architecture", "domain", "conventions", "data", "integrations"]
SECTION_HELP = {
    "architecture": "modules, layers and how a request flows through them",
    "domain": "the business words and rules",
    "conventions": "how code is written here (naming, errors, tests)",
    "data": "tables, migrations and how data is accessed",
    "integrations": "outside services, queues, files and their contracts",
}
COMPOSE = ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml",
           "deploy/docker-compose.yml", "deploy/compose.yml", "docker/docker-compose.yml")


def compose_file(root: str) -> str | None:
    return next((c for c in COMPOSE if (Path(root) / c).is_file()), None)


def existing_sections(root: str) -> list[str]:
    k = Path(root) / "docs" / "knowledge"
    return [s for s in SECTIONS if (k / f"{s}.md").is_file()]


def defaults(root: str, fast: bool = False) -> dict:
    """keel's answers when the user gives none. A fast init writes no knowledge base unless the user names sections."""
    return {"runs_on": "dev container" if (Path(root) / ".devcontainer").is_dir() else "this machine",
            "services": f"use {compose_file(root)}" if compose_file(root) else "none",
            "knowledge_sections": [] if fast else list(SECTIONS)}


def questions(root: str, fast: bool = False) -> str:
    d = defaults(root, fast)
    have = existing_sections(root)
    lines = ["Three questions before keel sets this project up. Approve to use the defaults (in brackets), or answer "
             "in your own words with “Use my answers”, for example: “1 dev container · 2 I run them myself · "
             "3 conventions, data”.", "",
             f"1. Where does the app run while you develop?  this machine / dev container / decide later   [{d['runs_on']}]"]
    comp = compose_file(root)
    if comp:
        lines.append(f"2. {comp} exists. Use it for services (databases, queues)?  use it / I run them myself   [use it]")
    else:
        lines.append("2. No compose file found. Services?  none / I run them myself / generate one   [none]")
    lines.append("3. Which knowledge sections should keel write?  (more sections = more tokens now, better agents later)   "
                 + ("[none: this is a fast init; name the ones you want, conventions and data help agents most]" if fast
                    else "[all five]"))
    for s in SECTIONS:
        lines.append(f"   - {s}: {SECTION_HELP[s]}" + ("  (exists)" if s in have else ""))
    return "\n".join(lines)


def answers(root: str, text: str | None, fast: bool = False) -> dict:
    """The defaults, changed by what the user wrote (free text: words are matched, not a strict format)."""
    a = defaults(root, fast)
    t = (text or "").lower()
    if not t.strip():
        return a
    if "dev container" in t or "devcontainer" in t:
        a["runs_on"] = "dev container"
    elif "later" in t:
        a["runs_on"] = "decide later"
    elif "machine" in t or "local" in t:
        a["runs_on"] = "this machine"
    if "myself" in t or "my own" in t:
        a["services"] = "I run them myself"
    elif "generate" in t:
        a["services"] = "generate a compose file"
    named = [s for s in SECTIONS if re.search(rf"\b{s}\b", t)]
    if re.search(r"\b(no|none) (knowledge|sections)\b|sections?:?\s*none", t):
        a["knowledge_sections"] = []
    elif named:
        a["knowledge_sections"] = named
    a["said"] = text.strip()[:500]
    return a


def plan(root: str, init: dict) -> str:
    cmds = testcmd.config_commands(root)
    cfg_exists = (Path(root) / ".keel" / "config.yml").is_file()
    secs = init.get("knowledge_sections") or []
    lines = ["keel will do this, in this order. Approve to start; send back to change it.", "",
             f"1. Write .keel/config.yml" + (" — it exists, so it is left as it is" if cfg_exists else ":"),]
    if not cfg_exists:
        block = {"version": 4, **({"commands": cmds} if cmds else {}), "init": {k: v for k, v in init.items() if k != "said"}}
        lines += ["   " + l for l in yaml.safe_dump(block, sort_keys=False).splitlines()]
    lines += ["2. Run the setup ladder (install, build, tests, services up) and write docs/RUNNING.md with every "
              "command that passed.",
              f"   test command: {cmds.get('api_test_module') or 'none found — the ladder will say so'}",
              f"   one criterion: {cmds.get('api_test_ac') or '-'}",
              f"   services: {init.get('services')}",
              (f"3. Write the knowledge sections {', '.join(secs)} under docs/knowledge/ (one librarian each, every claim "
               "with a file:line citation), at the same time as the ladder." if secs else
               "3. No knowledge sections (you chose none)."),
              "4. Check that every citation in docs/knowledge/ points at a real line.",
              "5. Commit it all as chore(setup): keel init. Nothing is pushed."]
    return "\n".join(lines)
