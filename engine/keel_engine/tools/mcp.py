"""MCP servers: list their tools (POST /mcp/tools) and hand allowed tools to agents."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

LIST_TIMEOUT = 30


def keel_server_spec() -> dict:
    """keel v2's own MCP server, read-only: an agent can read the flow but never approve its own gate."""
    return {"name": "keel", "command": sys.executable, "args": ["-m", "keel_engine.mcp", "--read-only"],
            "env": {"KEEL_API_URL": config.api_url()}}


def _server_env(spec: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TERM", "TMPDIR", "USER", "SHELL")}
    env.update(spec.get("env") or {})
    return env


async def list_tools(spec: dict) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=spec["command"], args=list(spec.get("args") or []),
                                   env=_server_env(spec), cwd=spec.get("cwd") or None)

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [{"name": t.name, "description": t.description or ""} for t in result.tools]

    try:
        tools = await asyncio.wait_for(go(), timeout=LIST_TIMEOUT)
        return {"ok": True, "tools": tools}
    except asyncio.TimeoutError:
        return {"ok": False, "tools": [], "error": f"No answer from {spec.get('name')} within {LIST_TIMEOUT}s."}
    except BaseException as exc:  # an MCP failure can arrive wrapped in an ExceptionGroup
        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        return {"ok": False, "tools": [], "error": _first_error(exc)}


def _first_error(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"[:500]


def parse_allow(allow: list[str] | None) -> dict[str, set[str] | None]:
    """["mcp:keel:keel_next", "mcp:serena:*", "mcp:x"] -> {"keel": {"keel_next"}, "serena": None, "x": None}.
    None means every tool of that server."""
    out: dict[str, set[str] | None] = {}
    for item in allow or []:
        parts = item.split(":")
        if len(parts) < 2 or parts[0] != "mcp":
            continue
        server = parts[1]
        tool = parts[2] if len(parts) > 2 else "*"
        if tool == "*" or out.get(server, set()) is None:
            out[server] = None
        else:
            out.setdefault(server, set()).add(tool)
    return out


def servers_for(specs: list[dict], allow: list[str] | None) -> list[dict]:
    """The servers a step may use. keel's own server is added when allowed and not configured."""
    wanted = parse_allow(allow)
    by_name = {s["name"]: s for s in specs or []}
    if "keel" in wanted and "keel" not in by_name:
        by_name["keel"] = keel_server_spec()
    elif "keel" in by_name:
        # The api's seeded entry: the server finds the api through KEEL_API_URL (its env is otherwise filtered).
        spec = by_name["keel"]
        by_name["keel"] = {**spec, "env": {"KEEL_API_URL": config.api_url(), **(spec.get("env") or {})}}
    return [by_name[n] for n in wanted if n in by_name]


async def langchain_tools(specs: list[dict], allow: list[str] | None) -> tuple[list, object | None]:
    """LangChain tools for the allowed MCP tools, named mcp__<server>__<tool>."""
    servers = servers_for(specs, allow)
    if not servers:
        return [], None
    from langchain_mcp_adapters.client import MultiServerMCPClient

    wanted = parse_allow(allow)
    conns = {s["name"]: {"command": s["command"], "args": list(s.get("args") or []), "transport": "stdio",
                         "env": _server_env(s), **({"cwd": s["cwd"]} if s.get("cwd") else {})} for s in servers}
    client = MultiServerMCPClient(conns)
    out = []
    for s in servers:
        try:
            tools = await asyncio.wait_for(client.get_tools(server_name=s["name"]), timeout=LIST_TIMEOUT)
        except Exception as exc:
            log.warning("MCP server %s gave no tools: %s", s["name"], _first_error(exc))
            continue
        names = wanted.get(s["name"])
        for t in tools:
            if names is None or t.name in names:
                t.name = f"mcp__{s['name']}__{t.name}"
                out.append(t)
    return out, client


def claude_mcp_config(specs: list[dict], allow: list[str] | None, folder: str) -> tuple[str | None, list[str]]:
    """Write a Claude Code --mcp-config file. Returns (path, allowedTools entries)."""
    import json

    servers = servers_for(specs, allow)
    if not servers:
        return None, []
    wanted = parse_allow(allow)
    cfg = {"mcpServers": {s["name"]: {"command": s["command"], "args": list(s.get("args") or []), "env": s.get("env") or {}}
                          for s in servers}}
    path = Path(folder) / "mcp.json"
    path.write_text(json.dumps(cfg))
    allowed = []
    for s in servers:
        names = wanted.get(s["name"])
        allowed += [f"mcp__{s['name']}"] if names is None else [f"mcp__{s['name']}__{n}" for n in sorted(names)]
    return str(path), allowed
