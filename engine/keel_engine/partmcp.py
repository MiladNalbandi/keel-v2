"""What a plugin's own MCP server (keel-db, keel-git, keel-ci) uses to answer a tool call.

The server runs as its own process for KeelBot or an agent (keel_engine/extensions.py server_specs). Each tool call goes
back to the engine (KEEL_PLUGIN_URL, POST /plugins/call) with the agent call's key (KEEL_PLUGIN_KEY); the engine holds
that call's project, folder and keys in memory, so no secret is in the server's process or its config.
"""

from __future__ import annotations

import contextvars
import os

import httpx

# 0.37 keel's own harness runs a plugin's server inside the engine (tools/mcp.py inproc_tools): the call's key is here
# then, and a tool call goes straight to extensions.call (no process, no HTTP)
INPROC_KEY: contextvars.ContextVar[str | None] = contextvars.ContextVar("keel_plugin_key", default=None)


def call(tool: str, **args) -> str:
    """The engine's answer to one tool call, as text for the model (a refusal says why instead of failing)."""
    key = INPROC_KEY.get()
    if key is not None:
        from . import extensions

        try:
            return str(extensions.call(key, tool, {k: v for k, v in args.items() if v not in (None, "")}) or "")
        except extensions.errors() as exc:          # as POST /plugins/call answers it (app.py part_error)
            hint = getattr(exc, "hint", "") or ""
            return f"Refused: {exc}" + (f" {hint}" if hint else "")
        except Exception as exc:  # noqa: BLE001 - the model reads what went wrong, as the route's 500 said
            return f"keel's engine could not answer: {type(exc).__name__}: {str(exc)[:300]}"
    try:
        # a tool that asks the person first (db_change) waits up to permissions.ASK_TIMEOUT (10 minutes) for the answer
        r = httpx.post(os.environ.get("KEEL_PLUGIN_URL", ""), timeout=660,
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
