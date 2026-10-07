"""keel's plugins: folders with a plugin.yml.

    content/plugins/<name>/plugin.yml           keel's own: core is always on; an `installable` one (db, git) is on
                                                only for the projects that turned it on (the api sends `plugins`)
    <project>/.keel/plugins/<name>/plugin.yml   the project's own: text only (a command with the same name replaces keel's)

    name: db
    title: Database
    description: ...
    installable: true          keel's own only: off until a project turns it on (Tools › Plugins)
    needs: [database]          what the person sets up (Connections)
    tools: {server: keel-db, read: [db_schema, ...]}    the MCP server keel runs for KeelBot and agents (plugins/)
    actions: [db:query, ...]   workflow code steps it adds
    shows_in: [map, ...]       the pages it adds to
    commands:                  /name in KeelBot sends the template instead; {{args}} is the rest of the line
      - {name: sql, description: ..., prompt: "..."}
    context: [docs/ARCHITECTURE.md]     project files KeelBot is pointed at in every prompt

A project's plugin is text only (commands and context), so it is safe to load; tools and actions come only from keel's
own. A plugin that is broken is listed with its problem and never used.
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
    p = {"name": file.parent.name, "title": "", "description": "", "source": source, "path": str(file), "commands": [],
         "context": [], "installable": False, "needs": [], "tools": {}, "actions": [], "shows_in": [], "problems": []}
    try:
        raw = yaml.safe_load(file.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        p["problems"].append(f"plugin.yml does not parse: {str(exc)[:200]}")
        return p
    if not isinstance(raw, dict):
        p["problems"].append("plugin.yml must be a mapping")
        return p
    p["name"] = str(raw.get("name") or p["name"])
    p["title"] = str(raw.get("title") or p["name"])
    p["description"] = str(raw.get("description") or "")
    if source == "keel":
        # tools and actions run keel's own code: only keel's plugins may name them
        p["installable"] = bool(raw.get("installable"))
        p["needs"] = [str(x) for x in raw.get("needs") or []]
        tools = raw.get("tools") or {}
        if isinstance(tools, dict) and tools.get("server"):
            p["tools"] = {"server": str(tools["server"]), "read": [str(x) for x in tools.get("read") or []]}
        p["actions"] = [str(x) for x in raw.get("actions") or []]
        p["shows_in"] = [str(x) for x in raw.get("shows_in") or []]
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


def load(root: str | None, enabled: list[str] | None = None) -> list[dict]:
    """The plugins in use, keel's first, then the project's, each with its problems: keel's installable plugins only
    when `enabled` names them."""
    found = []
    for source, d in _dirs(root):
        if d.is_dir():
            found += [_one(source, f) for f in sorted(d.glob("*/plugin.yml"))]
    on = set(enabled or [])
    return [p for p in found if not p["installable"] or p["name"] in on]


def catalog() -> list[dict]:
    """keel's installable plugins (Tools › Plugins), with what each adds."""
    d = config.content_dir() / "plugins"
    found = [_one("keel", f) for f in sorted(d.glob("*/plugin.yml"))] if d.is_dir() else []
    return [{k: v for k, v in p.items() if k not in ("path", "source")} for p in found if p["installable"]]


def commands(root: str | None, enabled: list[str] | None = None) -> list[dict]:
    """The slash commands in name order; a project command replaces keel's command of the same name."""
    by: dict[str, dict] = {}
    for p in load(root, enabled):
        for c in p["commands"]:
            by[c["name"]] = c
    return [by[k] for k in sorted(by)]


def context_files(root: str | None, enabled: list[str] | None = None) -> list[str]:
    """The context files the plugins name that exist in the project."""
    out: list[str] = []
    for p in load(root, enabled):
        for rel in p["context"]:
            if root and (Path(root) / rel).is_file() and rel not in out:
                out.append(rel)
    return out


def expand(root: str | None, text: str, enabled: list[str] | None = None) -> tuple[str, str | None]:
    """`/name the rest` → (the command's prompt with {{args}} = the rest, name); other text comes back unchanged."""
    m = re.match(r"^/([a-z][a-z0-9-]*)(?:\s+(.*))?$", text.strip(), re.S)
    if not m:
        return text, None
    cmd = next((c for c in commands(root, enabled) if c["name"] == m.group(1)), None)
    if not cmd:
        return text, None
    args = (m.group(2) or "").strip() or "the selected lines or the open file"
    return ARGS.sub(lambda _m: args, cmd["prompt"]), cmd["name"]
