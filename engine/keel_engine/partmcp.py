"""What a plugin's own MCP server (keel-db, keel-git, keel-ci) uses to answer a tool call.

The server runs as its own process for KeelBot or an agent (keel_engine/extensions.py server_specs). Each tool call goes
back to the engine (KEEL_PLUGIN_URL, POST /plugins/call) with the agent call's key (KEEL_PLUGIN_KEY); the engine holds
that call's project, folder and keys in memory, so no secret is in the server's process or its config.
"""

from __future__ import annotations

import os

import httpx


def call(tool: str, **args) -> str:
    """The engine's answer to one tool call, as text for the model (a refusal says why instead of failing)."""
    try:
        r = httpx.post(os.environ.get("KEEL_PLUGIN_URL", ""), timeout=120,
                       json={"key": os.environ.get("KEEL_PLUGIN_KEY", ""), "tool": tool,
                             "args": {k: v for k, v in args.items() if v not in (None, "")}})
    except httpx.HTTPError as exc:
        return f"keel's engine did not answer: {exc}"
    try:
        d = r.json()
    except ValueError:
        return f"keel's engine answered {r.status_code}."
    if r.status_code != 200:
        return f"Refused: {d.get('error') or r.status_code}" + (f" {d['hint']}" if d.get("hint") else "")
    return str(d.get("text") or "")
