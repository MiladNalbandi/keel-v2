"""0.19.0 keel's MCP server as the gateway to the project's connected MCP servers (harness mode).

A harness step (`mcp: gateway` in content/workflows/harness.yaml) gets one MCP server: keel's own. The project's other
servers (Settings › MCP: GitHub, Jira, Sentry...) come to keel's server in its env (KEEL_MCP_GATEWAY, written by
tools/mcp.py gateway()); keel's server starts each of them, lists its tools and re-exports them as
`<server>__<tool>`, with their own descriptions and input schemas. The harness (Claude Code, Codex, Copilot CLI,
OpenCode) needs no config of its own, and the servers' secrets never reach its config files.

    harness ──► keel MCP ──┬─ keel's own tools (keel_ci_runs, keel_git_status ...)
                           ├─ github__search_issues     read-only: runs at once
                           └─ jira__create_issue        anything else: the person's OK in keel's Inbox first

A tool runs at once only when its server marks it read-only (readOnlyHint); any other waits for the person's answer in
keel's Inbox (out of the model's reach, as keel's own acting tools do), or runs at once with `--yes`. A server that does
not start or answer in time is left out; the others still work.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

import anyio

from .tools.mcp import GATEWAY_ENV, LIST_TIMEOUT, SERVER_NAME, _first_error, _server_env

log = logging.getLogger(__name__)

SEP = "__"
TEXT_MAX = 100_000        # one tool answer, as the harness reads it


def configured(environ: dict | None = None) -> list[dict]:
    """The servers keel's server is the gateway for: [{name, command, args, env, cwd, tools}] ([] when none)."""
    raw = (os.environ if environ is None else environ).get(GATEWAY_ENV) or ""
    try:
        servers = json.loads(raw) if raw else []
    except ValueError:
        log.warning("keel mcp: %s is not JSON; no gateway", GATEWAY_ENV)
        return []
    return [s for s in servers if isinstance(s, dict) and s.get("command") and SERVER_NAME.match(str(s.get("name") or ""))
            and s["name"] != "keel"]


def read_only(tool) -> bool:
    ann = getattr(tool, "annotations", None)
    return bool(ann and getattr(ann, "readOnlyHint", None) is True)


def answer_text(res) -> str:
    """A server's answer as the harness reads it: its text parts; other parts named; structured content as JSON."""
    parts = []
    for c in getattr(res, "content", None) or []:
        text = getattr(c, "text", None)
        parts.append(text if isinstance(text, str) else f"[{getattr(c, 'type', 'content')}]")
    if not parts and getattr(res, "structuredContent", None) is not None:
        parts.append(json.dumps(res.structuredContent, default=str))
    return "\n".join(parts)[:TEXT_MAX]


class Proxy:
    """One connected server's live session, held in its own task for the server's whole life."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.name = spec["name"]
        self.session = None
        self.tools: list = []
        self.error = ""
        self.ready = anyio.Event()
        self.stop = anyio.Event()

    async def hold(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self.spec["command"], args=list(self.spec.get("args") or []),
                                       env=_server_env(self.spec), cwd=self.spec.get("cwd") or None)
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    with anyio.move_on_after(LIST_TIMEOUT) as scope:
                        await session.initialize()
                        self.tools = list((await session.list_tools()).tools)
                    if scope.cancelled_caught:
                        self.error = f"no answer within {LIST_TIMEOUT}s"
                        return
                    self.session = session
                    self.ready.set()
                    await self.stop.wait()
        except Exception as exc:  # noqa: BLE001 - one server's failure leaves the others (and keel's own tools) working
            self.error = _first_error(exc)
        finally:
            self.session = None
            self.ready.set()

    def allowed(self, tool) -> bool:
        names = self.spec.get("tools")
        return names is None or tool.name in names


def _proxy_tool(proxy: Proxy, tool, ask: Callable[[str, str, dict], tuple[bool, str]] | None):
    from mcp.server.fastmcp.tools import Tool
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata
    from mcp.types import ToolAnnotations

    name = f"{proxy.name}{SEP}{tool.name}"
    reads = read_only(tool)

    async def call(arguments: dict[str, Any]) -> str:
        if proxy.session is None:
            raise RuntimeError(f"keel: the {proxy.name} MCP server is not running ({proxy.error or 'it stopped'}).")
        if not reads and ask is not None:
            ok, why = await anyio.to_thread.run_sync(lambda: ask(proxy.name, tool.name, arguments))
            if not ok:
                return f"keel: not done — {proxy.name}'s {tool.name} needs the person's OK in keel's Inbox: {why or 'they said no'}."
        res = await proxy.session.call_tool(tool.name, arguments)
        text = answer_text(res)
        if getattr(res, "isError", False):
            raise RuntimeError(text or f"{proxy.name}'s {tool.name} failed")
        return text

    class ProxyTool(Tool):
        async def run(self, arguments, context=None, convert_result=False):
            from mcp.server.fastmcp.exceptions import ToolError

            try:
                result = await call(dict(arguments or {}))
            except Exception as exc:  # noqa: BLE001
                raise ToolError(str(exc)) from exc
            return self.fn_metadata.convert_result(result) if convert_result else result

    async def placeholder() -> str:
        return ""

    ann = tool.annotations or ToolAnnotations()
    note = "" if reads else " (keel asks the person in its Inbox before this runs)"
    return ProxyTool(fn=placeholder, name=name, title=getattr(tool, "title", None),
                     description=f"[{proxy.name}] {tool.description or ''}{note}".strip(),
                     parameters=tool.inputSchema or {"type": "object", "properties": {}},
                     fn_metadata=func_metadata(placeholder), is_async=True, annotations=ann)


def attach(srv, specs: list[dict], ask: Callable[[str, str, dict], tuple[bool, str]] | None) -> None:
    """keel's server holds a session to each server for its whole life (the low-level server's lifespan, entered
    before the first request), and registers their tools before the client lists them."""
    import contextlib

    low = srv._mcp_server
    before = low.lifespan

    @contextlib.asynccontextmanager
    async def lifespan(server):
        async with before(server) as ctx, anyio.create_task_group() as tg:
            proxies = [Proxy(s) for s in specs]
            for p in proxies:
                tg.start_soon(p.hold)
            with anyio.move_on_after(LIST_TIMEOUT + 5):
                for p in proxies:
                    await p.ready.wait()
            for p in proxies:
                if p.session is None:
                    log.warning("keel mcp: %s left out of the gateway: %s", p.name, p.error or "it did not start")
                    continue
                for t in p.tools:
                    if p.allowed(t):
                        tool = _proxy_tool(p, t, ask)
                        srv._tool_manager._tools[tool.name] = tool
            try:
                yield ctx
            finally:
                for p in proxies:
                    p.stop.set()
                tg.cancel_scope.cancel()

    low.lifespan = lifespan
