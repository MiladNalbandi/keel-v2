"""keel's built-in parts that become plugins. Each declares itself in a PART dict that keel_engine/extensions.py reads
(keel_engine/builtins.py names the modules); core never imports them:

    db/, git/, ci/    what content/plugins/<name>/plugin.yml promises (runtime/plugins.py loads those): workflow steps,
                      read tools for agents (server.py), routes, KeelBot's words and keel2 mcp's tools
    graph/            the code graph (tools/codegraph.py): its index, MCP server, hints and the Graph page's routes

The map left in step 3: it is the plugin plugins/map (package keel_plugin_map), loaded as an add-on.

A project turns db, git and ci on (Tools › Plugins); the api then sends `plugins: [...]` with every flow and KeelBot
turn, and the plugin's secrets in the call's keys (memory only): `db:<connection>` → {"name","kind","url","env"} as
JSON, `github` → the token. What they share:

    PluginError         a refusal (status, message, hint): the app answers it as a 4xx, a tool call as a "Refused" text
    connections(keys)   the database connections a call carries
    github_token(keys)  the GitHub token a call carries
"""

from __future__ import annotations

import json

from ..extensions import PartError as PluginError

__all__ = ["PluginError", "connections", "github_token"]


def connections(keys: dict | None) -> dict[str, dict]:
    """{name: {name, kind, url, env}} from the call's keys `db:<name>`."""
    out = {}
    for k, v in (keys or {}).items():
        if k.startswith("db:") and v:
            try:
                d = json.loads(v)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(d, dict) and d.get("url"):
                out[k[3:]] = {**d, "name": d.get("name") or k[3:]}
    return out


def github_token(keys: dict | None) -> str | None:
    from ..runtime.verdict_actions import github_token as token

    return token(keys or {})
