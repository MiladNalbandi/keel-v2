"""The Library plugin's engine part: the read tools agents and KeelBot get (library_search, library_read, the keel-library
MCP server in server.py). The documents live in the api's database (plugins/library/api); tools.py asks the api for them, with
the project of the agent call and only documents marked for agents.

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_library). This module stays light.
"""

from __future__ import annotations

from pathlib import Path

# the same version as ../../keel-plugin.yml (tests/test_library.py checks it)
VERSION = "0.1.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"   # content/plugins/library/plugin.yml: the per-project list

KEELBOT = ("The company's library is on for this project: library_search finds what its policies and rules say, library_read "
           "gives one whole document. Quote the words and name the source as library:<id>. If it says nothing, say so.")


def _call(call: dict, tool: str, args: dict) -> str:
    from .tools import call_tool

    return call_tool(call, tool, args)


ADDON = {
    "name": "library",
    "title": "Library",
    "version": VERSION,
    "content": CONTENT,
}

PART = {
    "name": "library",
    "title": "Library",
    "per_project": True,
    "read_tools": ("library_search", "library_read"),
    "mcp": {"server": "keel-library", "module": "keel_plugin_library.server", "call": _call,
            "prompt": "The company's library: library_search finds what its policies say; quote the words and name library:<id>."},
    "keelbot": {"prompt": KEELBOT, "actions": []},
}
