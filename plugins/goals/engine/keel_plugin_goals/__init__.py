"""The Goals plugin's engine part (plugins/goals), issue #98: only its tools in keel's own MCP server (keel2 mcp). The
goals themselves live in its api part (keel.api.goals, the Kotlin jar): the tools call keel's api, under its MCP levels
(GoalsMcpRoutes names the write routes).

keel loads it as a plugin part (KEEL_PLUGIN_ADDONS=keel_plugin_goals, written by `keel-engine plugins resolve`). Its
content (the goals-plan workflow and the goals-planner agent) comes with the plugin, as before.
"""

from __future__ import annotations

from pathlib import Path

# the same version as ../../keel-plugin.yml (tests/test_goals_mcp.py checks it)
VERSION = "0.1.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"


def _keel_mcp(server, api, guard, write: bool) -> None:
    from .mcp_tools import register

    register(server, api, guard, write)


ADDON = {
    "name": "goals",
    "title": "Goals",
    "version": VERSION,
    "content": CONTENT,
}

PART = {
    "name": "goals",
    "title": "Goals",
    "keel_mcp": _keel_mcp,
}
