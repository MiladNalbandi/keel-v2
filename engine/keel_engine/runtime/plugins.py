"""Helper plugins: folders with a plugin.yml that add slash commands and context to keel's Helper.

    content/plugins/<name>/plugin.yml           keel's own (always on)
    <project>/.keel/plugins/<name>/plugin.yml   the project's own (a command with the same name replaces keel's)

    name: core
    description: ...
    commands:                 /name in the Helper sends the template instead; {{args}} is the rest of the line
      - {name: explain, description: ..., prompt: "Explain {{args}} ..."}
    context: [docs/ARCHITECTURE.md]     project files the Helper is pointed at in every prompt

Text only for now (commands and context), so a project's plugins are safe to load. A plugin that is broken is listed
with its problem and never used.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .. import config

NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
ARGS = re.compile(r"\{\{\s*args\s*\}\}")
MAX_PROMPT = 6000


def _dirs(root: str | None) -> list[tuple[str, Path]]:
    out = [("keel", config.content_dir() / "plugins")]
    if root:
        out.append(("project", Path(root) / ".keel" / "plugins"))
    return out


def _one(source: str, file: Path) -> dict:
    p = {"name": file.parent.name, "description": "", "source": source, "path": str(file), "commands": [], "context": [],
         "problems": []}
    try:
        raw = yaml.safe_load(file.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        p["problems"].append(f"plugin.yml does not parse: {str(exc)[:200]}")
        return p
    if not isinstance(raw, dict):
        p["problems"].append("plugin.yml must be a mapping")
        return p
    p["name"] = str(raw.get("name") or p["name"])
    p["description"] = str(raw.get("description") or "")
    for c in raw.get("commands") or []:
        if not isinstance(c, dict):
            p["problems"].append("a command must be a mapping with name and prompt")
            continue
        name, prompt = str(c.get("name") or ""), c.get("prompt")
        if not NAME.match(name):
            p["problems"].append(f"command name {name!r}: lowercase letters, digits and dashes, starting with a letter")
        elif not isinstance(prompt, str) or not prompt.strip():
            p["problems"].append(f"command /{name} has no prompt")
        elif len(prompt) > MAX_PROMPT:
            p["problems"].append(f"command /{name}: the prompt is longer than {MAX_PROMPT} characters")
        else:
            p["commands"].append({"name": name, "description": str(c.get("description") or ""), "prompt": prompt.strip(),
                                  "plugin": p["name"], "source": source})
    for rel in raw.get("context") or []:
        rel = str(rel)
        if rel.startswith("/") or ".." in Path(rel).parts:
            p["problems"].append(f"context {rel!r} must be a path inside the project")
        else:
            p["context"].append(rel)
    return p


def load(root: str | None) -> list[dict]:
    """Every plugin, keel's first, then the project's, each with its problems."""
    found = []
    for source, d in _dirs(root):
        if d.is_dir():
            found += [_one(source, f) for f in sorted(d.glob("*/plugin.yml"))]
    return found


def commands(root: str | None) -> list[dict]:
    """The slash commands in name order; a project command replaces keel's command of the same name."""
    by: dict[str, dict] = {}
    for p in load(root):
        for c in p["commands"]:
            by[c["name"]] = c
    return [by[k] for k in sorted(by)]


def context_files(root: str | None) -> list[str]:
    """The context files the plugins name that exist in the project."""
    out: list[str] = []
    for p in load(root):
        for rel in p["context"]:
            if root and (Path(root) / rel).is_file() and rel not in out:
                out.append(rel)
    return out


def expand(root: str | None, text: str) -> tuple[str, str | None]:
    """`/name the rest` → (the command's prompt with {{args}} = the rest, name); other text comes back unchanged."""
    m = re.match(r"^/([a-z][a-z0-9-]*)(?:\s+(.*))?$", text.strip(), re.S)
    if not m:
        return text, None
    cmd = next((c for c in commands(root) if c["name"] == m.group(1)), None)
    if not cmd:
        return text, None
    args = (m.group(2) or "").strip() or "the selected lines or the open file"
    return ARGS.sub(lambda _m: args, cmd["prompt"]), cmd["name"]
