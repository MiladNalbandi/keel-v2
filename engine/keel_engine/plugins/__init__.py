"""keel's built-in parts that become plugins. Each declared itself in a PART dict that keel_engine/extensions.py reads
(keel_engine/builtins.py names the modules); core never imports them. None is left here.

The map, CI/CD, Database, Git and the code graph left in step 3: they are the plugins plugins/map, plugins/ci,
plugins/db, plugins/git and plugins/graph (packages keel_plugin_map, keel_plugin_ci, keel_plugin_db, keel_plugin_git
and keel_plugin_graph), loaded as add-ons; CI/CD, Database and Git each with its own MCP server module
(keel_plugin_<name>.server).

A project turns db, git and ci on (Tools › Plugins); the api then sends `plugins: [...]` with every flow and KeelBot
turn, and the plugin's secrets in the call's keys (memory only): `db:<connection>` → {"name","kind","url","env"} as
JSON (the Database plugin reads them), `github` → the token. What they share:

    PluginError         a refusal (status, message, hint): the app answers it as a 4xx, a tool call as a "Refused" text
"""

from __future__ import annotations

from ..extensions import PartError as PluginError

__all__ = ["PluginError"]
