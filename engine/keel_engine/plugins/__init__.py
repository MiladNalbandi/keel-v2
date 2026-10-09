"""keel's built-in parts that become plugins. Each declares itself in a PART dict that keel_engine/extensions.py reads
(keel_engine/builtins.py names the modules); core never imports them:

    git/              what content/plugins/<name>/plugin.yml promises (runtime/plugins.py loads those): workflow steps,
                      read tools for agents (server.py), routes, KeelBot's words and keel2 mcp's tools
    graph/            the code graph (tools/codegraph.py): its index, MCP server, hints and the Graph page's routes

The map, CI/CD and Database left in step 3: they are the plugins plugins/map, plugins/ci and plugins/db (packages
keel_plugin_map, keel_plugin_ci and keel_plugin_db), loaded as add-ons.

A project turns db, git and ci on (Tools › Plugins); the api then sends `plugins: [...]` with every flow and KeelBot
turn, and the plugin's secrets in the call's keys (memory only): `db:<connection>` → {"name","kind","url","env"} as
JSON (the Database plugin reads them), `github` → the token. What they share:

    PluginError         a refusal (status, message, hint): the app answers it as a 4xx, a tool call as a "Refused" text
    github_token(keys)  the GitHub token a call carries
"""

from __future__ import annotations

from ..extensions import PartError as PluginError

__all__ = ["PluginError", "github_token"]


def github_token(keys: dict | None) -> str | None:
    from ..runtime.verdict_actions import github_token as token

    return token(keys or {})
