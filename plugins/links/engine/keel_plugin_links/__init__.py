"""The Links plugin's engine part: tools for agents and KeelBot in another system. First system: Linear (linear.py). They read with
the key the person added (it reaches this process in memory only, with each agent call: the api's LinksKeys); a tool that writes asks
the person in keel's Inbox first and shows the exact words (keel_engine/approvals.py).

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_links). This module stays light.
"""

from __future__ import annotations

from pathlib import Path

# the same version as ../../keel-plugin.yml (tests/test_links.py checks it)
VERSION = "0.1.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"

KEELBOT = ("Linear is linked to this project: links_search and links_issue read its issues. links_comment and links_create_issue write, "
           "and each one waits for the person's OK in keel's Inbox; say so when you use one.")


def _call(call: dict, tool: str, args: dict) -> str:
    from .tools import call_tool

    return call_tool(call, tool, args)


ADDON = {
    "name": "links",
    "title": "Links",
    "version": VERSION,
    "content": CONTENT,
}

PART = {
    "name": "links",
    "title": "Links",
    "per_project": True,
    "read_tools": ("links_search", "links_issue"),
    "act_tools": ("links_comment", "links_create_issue"),
    "mcp": {"server": "keel-links", "module": "keel_plugin_links.server", "call": _call,
            "prompt": "Linear: links_search and links_issue read it. links_comment and links_create_issue wait for the person's OK."},
    "keelbot": {"prompt": KEELBOT, "actions": []},
}
