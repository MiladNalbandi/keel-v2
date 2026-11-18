"""MCP servers: list their tools (POST /mcp/tools) and hand allowed tools to agents."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from .. import config, extensions

log = logging.getLogger(__name__)

LIST_TIMEOUT = 30
SERVER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")     # the api's rule for a server's name (McpService)


PLUGIN_ENV = ("KEEL_PLUGIN_PATHS", "KEEL_PLUGIN_ADDONS")


def keel_server_env() -> dict:
    """What keel's own MCP server needs: the api, and the engine's plugins (their tools in it, like the CI/CD plugin's).
    Only that process gets the plugin variables: a project's commands never see them (models/cli.py project_env).
    v0.18.1 and the api's read token (KEEL_MCP_TOKEN): the api answers no caller without one (api auth/LocalAuth). Agents
    get keel's server read-only, so a read token gives them nothing they did not have."""
    env = {"KEEL_API_URL": config.api_url(), **{k: os.environ[k] for k in PLUGIN_ENV if os.environ.get(k)}}
    if tok := config.mcp_token("read"):
        env["KEEL_MCP_TOKEN"] = tok
    return env


def keel_server_spec() -> dict:
    """keel v2's own MCP server, read-only: an agent can read the flow but never approve its own gate."""
    return {"name": "keel", "command": sys.executable, "args": ["-P", "-m", "keel_engine.mcp", "--read-only"],
            "env": keel_server_env()}


SCOPE_ENV = "KEEL_MCP_PROJECTS"


def scoped(specs: list[dict], allow: list[str] | None, projects: list[str]) -> list[dict]:
    """0.18.64 keel's own server for a flow's agents, kept to `projects` (the flow's own first, then the ones its rule
    other-projects lets it read): every tool's project, flow and agent call must be one of them (mcp_api.KeelApi). The
    other servers are unchanged; without keel's server allowed, nothing changes."""
    if not projects or "keel" not in parse_allow(allow):
        return specs
    base = next((s for s in specs if s.get("name") == "keel"), None) or keel_server_spec()
    env = {**(base.get("env") or {}), SCOPE_ENV: ",".join(projects), "KEEL_PROJECT": projects[0]}
    return [s for s in specs if s.get("name") != "keel"] + [{**base, "env": env}]


GATEWAY_ENV = "KEEL_MCP_GATEWAY"


def gateway(specs: list[dict], allow: list[str] | None, connected: list[str]) -> tuple[list[dict], list[str]]:
    """0.19.0 harness mode (a step with `mcp: gateway`): the project's connected MCP servers (`connected`: Settings ›
    MCP, keel's own excluded) reach the agent through keel's own server only. keel's server gets them in its env
    (GATEWAY_ENV, JSON: each server's spec, and `tools`: the ones the Tools page allows, None for all) and re-exports their
    tools (keel_engine/mcp_gateway.py); the agent's own config names keel's server alone. The servers the agent's allow
    names (Tools › Who may use what), else every connected one. Returns (specs, allow) for the agent call."""
    import json

    wanted = parse_allow(allow)
    by_name = {s["name"]: s for s in specs or []}
    names = [n for n in connected if n != "keel" and n in by_name]
    picked = [n for n in names if n in wanted] or names
    rest = [a for a in allow or [] if a.split(":")[1:2] not in [[n] for n in picked] and a not in picked]
    if not any(a == "keel" or a.startswith("mcp:keel") for a in rest):
        rest.append("mcp:keel:*")
    keel = by_name.get("keel") or keel_server_spec()
    out = [s for s in specs or [] if s["name"] not in picked and s["name"] != "keel"]
    if picked:
        via = [{**by_name[n], "tools": None if wanted.get(n) is None else sorted(wanted[n])} for n in picked]
        keel = {**keel, "env": {**(keel.get("env") or {}), GATEWAY_ENV: json.dumps(via)}}
    return out + [keel], rest


def codegraph_server_spec(root: str) -> dict | None:
    """The code graph's MCP server when keel indexed this folder and the index is ready (the parts' mcp_specs hook,
    keel_engine/extensions.py); None otherwise (agents then find their way with grep)."""
    return next((s for s in extensions.mcp_specs(extensions.Agent(""), root) if s.get("name") == "codegraph"), None)


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
    """["mcp:keel:keel_next", "mcp:serena:*", "mcp:x", "jira"] -> {"keel": {"keel_next"}, "serena": None, "x": None,
    "jira": None}. None means every tool of that server."""
    out: dict[str, set[str] | None] = {}
    for item in allow or []:
        parts = item.split(":")
        if len(parts) == 1 and SERVER_NAME.match(parts[0]):
            out[parts[0]] = None          # a bare server name (Tools › "Who may use what" saves these): all its tools
            continue
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
        # The api's seeded entry: the server finds the api through KEEL_API_URL and the plugins through the plugin
        # variables (its env is otherwise filtered).
        spec = by_name["keel"]
        by_name["keel"] = {**spec, "env": {**keel_server_env(), **(spec.get("env") or {})}}
    return [by_name[n] for n in wanted if n in by_name]


# 0.35 a server's tool list is kept for a while: listing starts the server's process (keel's own, the code graph's, one per
# plugin), which cost every agent call and KeelBot turn seconds. The tools themselves hold no session (the adapter opens a
# new one for each call), so a kept list is as good as a new one. The key is the server's whole start (command, args,
# env, folder): a new token or another project lists again.
LIST_TTL = 300.0
_LISTED: dict[str, tuple[float, list]] = {}


def _list_key(conn: dict) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(conn, sort_keys=True, default=str).encode()).hexdigest()


# 0.37 (#190) a plugin's own server (keel-git, keel-db, keel-ci ...: extensions.server_specs "inproc") runs inside the
# engine for keel's harness: its FastMCP tools are built once per engine, and a call runs the tool's function in a thread
# with the agent call's key (partmcp.INPROC_KEY), which answers through extensions.call. Before, each agent call or
# KeelBot turn started the server's Python only to list its tools (~0.6 s; the kept list never matched: the key is in
# the env) and each tool call started it again (~0.6 s against ~0.01 s for `git status` in Bash).
_INPROC: dict[str, tuple[object, list]] = {}


class InprocTool:
    """What the harness needs of an MCP tool (harness/tools.py _mcp_tool): name, description, schema, annotations, ainvoke."""

    def __init__(self, server, tool, name: str, key: str):
        self._server, self._tool, self._key = server, tool.name, key
        self.name = name
        self.description = tool.description or tool.name
        self.args_schema = dict(tool.inputSchema or {"type": "object", "properties": {}})
        ann = tool.annotations.model_dump(exclude_none=True) if getattr(tool, "annotations", None) else {}
        self.metadata = {"annotations": ann}

    async def ainvoke(self, args: dict) -> str:
        from .. import partmcp

        def run() -> str:
            token = partmcp.INPROC_KEY.set(self._key)
            try:
                got = asyncio.run(self._server.call_tool(self._tool, dict(args or {})))
            finally:
                partmcp.INPROC_KEY.reset(token)
            blocks = got[0] if isinstance(got, tuple) else got
            return "\n".join(str(getattr(b, "text", "") or "") for b in blocks or [])

        return await asyncio.to_thread(run)      # a tool may wait for the person (db_change): never on the engine's loop


async def inproc_tools(spec: dict) -> list[InprocTool]:
    import importlib

    module = str(spec["inproc"]["module"])
    if module not in _INPROC:
        server = importlib.import_module(module).build()
        _INPROC[module] = (server, list(await server.list_tools()))
    server, listed = _INPROC[module]
    return [InprocTool(server, t, t.name, str(spec["inproc"]["key"])) for t in listed]


async def langchain_tools(specs: list[dict], allow: list[str] | None,
                          timing: dict | None = None) -> tuple[list, object | None]:
    """LangChain tools for the allowed MCP tools, named mcp__<server>__<tool>. 0.35 the servers start side by side (not
    one after another), a list from the last LIST_TTL seconds is used again, and `timing` gets each server's seconds
    (0 for a kept list)."""
    servers = servers_for(specs, allow)
    if not servers:
        return [], None
    import time

    from langchain_mcp_adapters.client import MultiServerMCPClient

    wanted = parse_allow(allow)
    conns = {s["name"]: {"command": s["command"], "args": list(s.get("args") or []), "transport": "stdio",
                         "env": _server_env(s), **({"cwd": s["cwd"]} if s.get("cwd") else {})} for s in servers}
    client = MultiServerMCPClient(conns)

    inproc = {s["name"]: s for s in servers if s.get("inproc")}

    async def listed(name: str) -> list:
        if name in inproc:
            t0 = time.monotonic()
            try:
                tools = await inproc_tools(inproc[name])
            except Exception as exc:  # noqa: BLE001 - a part that cannot build its server costs its tools, not the run
                log.warning("keel's %s tools did not load: %s", name, exc)
                tools = []
            if timing is not None:
                timing[name] = time.monotonic() - t0
            return tools
        key = _list_key(conns[name])
        now = time.monotonic()
        kept = _LISTED.get(key)
        if kept and now - kept[0] < LIST_TTL:
            if timing is not None:
                timing[name] = 0.0
            return kept[1]
        t0 = time.monotonic()
        try:
            tools = await asyncio.wait_for(client.get_tools(server_name=name), timeout=LIST_TIMEOUT)
        except Exception as exc:
            log.warning("MCP server %s gave no tools: %s", name, _first_error(exc))
            tools = None
        if timing is not None:
            timing[name] = time.monotonic() - t0
        if tools is None:
            return []
        _LISTED[key] = (time.monotonic(), list(tools))
        return tools

    got = await asyncio.gather(*(listed(s["name"]) for s in servers))
    out = []
    for s, tools in zip(servers, got):
        names = wanted.get(s["name"])
        for t in tools:
            if names is None or t.name in names:
                if isinstance(t, InprocTool):
                    t.name = f"mcp__{s['name']}__{t.name}"      # a new object each call already
                else:   # a copy: the kept list keeps the server's own names
                    out.append(t.model_copy(update={"name": f"mcp__{s['name']}__{t.name}"}))
                    continue
                out.append(t)
    return out, client


# 0.18.64 a `keys` entry "mcp_env:<server>:<NAME>" is that server's env value (the api sends it again after a restart,
# like a login): set_keys moves it out of the logins
KEY_PREFIX = "mcp_env:"


def strip_env(specs: list[dict] | None) -> tuple[list[dict], dict[str, dict]]:
    """0.18.64 the MCP servers as a flow stores them (checkpoints.db keel_threads): each server's env names
    (`env_names`), never their values, which come back as {server: {NAME: value}} for memory only, like the logins."""
    stored, values = [], {}
    for s in specs or []:
        env = s.get("env") or {}
        rest = {k: v for k, v in s.items() if k != "env"}
        if env:
            rest["env_names"] = sorted(env)
            values[s["name"]] = {k: str(v) for k, v in env.items()}
        stored.append(rest)
    return stored, values


def with_env(specs: list[dict] | None, values: dict[str, dict] | None) -> list[dict]:
    """The stored servers with their env again: the values kept in memory (a name with no value, after an engine
    restart, is left out). A server stored before 0.18.64 still has its `env` and keeps it."""
    out = []
    for s in specs or []:
        names = s.get("env_names")
        if names is None:
            out.append(dict(s))
            continue
        got = (values or {}).get(s["name"]) or {}
        env = {k: got[k] for k in names if k in got}
        out.append({**{k: v for k, v in s.items() if k != "env_names"}, **({"env": env} if env else {})})
    return out


def env_from_keys(keys: dict | None) -> tuple[dict, dict[str, dict]]:
    """(the logins without the MCP env entries, {server: {NAME: value}} from the "mcp_env:<server>:<NAME>" entries)."""
    logins, values = {}, {}
    for k, v in (keys or {}).items():
        if k.startswith(KEY_PREFIX) and k.count(":") >= 2:
            server, name = k[len(KEY_PREFIX):].split(":", 1)
            values.setdefault(server, {})[name] = str(v)
        else:
            logins[k] = v
    return logins, values


def write_private(path: Path | str, text: str) -> None:
    from ..models.env import write_private as write

    write(path, text)


ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def env_by_name(servers: list[dict], base: dict | None = None) -> tuple[dict, dict[str, list[str]], dict[str, dict]]:
    """0.18.64 the MCP servers' env for a CLI that starts them from its own environment (codex's `env_vars`), so no value
    is on its command line (any process on the machine reads that). Returns (the values for the CLI's environment, the
    names each server takes from there, what must stay in a server's own table). A name that two servers give different
    values, or that the CLI's environment (`base`: PATH, HOME...) already has with another value, stays in that server's
    own table: one environment holds one value per name."""
    shared: dict[str, str] = {}
    names: dict[str, list[str]] = {}
    inline: dict[str, dict] = {}
    for s in servers:
        for k, v in (s.get("env") or {}).items():
            if not ENV_NAME.match(k):
                continue
            v = str(v)
            if (k in shared and shared[k] != v) or (base and k in base and base[k] != v):
                inline.setdefault(s["name"], {})[k] = v
                continue
            shared[k] = v
            names.setdefault(s["name"], []).append(k)
    return shared, names, inline


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
    # 0.18.64 the servers' env holds their secrets: the file is the owner's only from its first byte
    write_private(path, json.dumps(cfg))
    from ..runtime import guard_ctx

    guard_ctx.seal(path)        # 0.18.64 checked when the CLI ends (it names keel's server and its token)
    allowed = []
    for s in servers:
        names = wanted.get(s["name"])
        allowed += [f"mcp__{s['name']}"] if names is None else [f"mcp__{s['name']}__{n}" for n in sorted(names)]
    return str(path), allowed
