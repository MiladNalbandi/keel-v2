"""MCP level tokens and the level guard for the routes this api answers itself (mcp/McpLevels.kt).

The tokens are an HMAC of the internal token per level, so the Kotlin api and this one make the same ones. The
guard here covers only this api's own routes; a route passed on to the Kotlin api is still judged there.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from fastapi import Request

from ..common.errors import Forbidden
from ..common.support import random_hex, same

LEVELS = ["read", "write", "admin"]
TOKEN_HEADER = "x-keel-mcp-token"


def rank(level: str) -> int:
    return LEVELS.index(level)


class McpTokens:
    def __init__(self, internal_token: str):
        self._tokens = {
            level: hmac.new(internal_token.encode(), f"keel-mcp:{level}".encode(), hashlib.sha256).hexdigest()
            if internal_token
            else random_hex(32)
            for level in LEVELS
        }

    def token_for(self, level: str) -> str:
        return self._tokens[level]

    def level_of(self, token: str | None) -> str | None:
        """The level a token proves (None: not one of ours). Every token is compared, in constant time."""
        if not token:
            return None
        found = None
        for level, t in self._tokens.items():
            if same(token, t):
                found = level
        return found


# (method, path regex) lists for this api's own non-GET routes, as in McpLevelGuard. What no list names needs admin.
# #98 the Org plugin's routes (engine_parts.py passes /api/org/* on): what its MCP tools send. A check of YAML changes
# nothing (read); saving an org, a new mission, stopping and accepting one are write (keel_org_save, _stop_mission and
# _accept ask the person first); deleting an org stays admin.
READ_POSTS: list[tuple[str, re.Pattern[str]]] = [
    ("POST", re.compile(r"/api/org/check")),
]
WRITE: list[tuple[str, re.Pattern[str]]] = [
    ("PUT", re.compile(r"/api/org/orgs/[^/]+")),
    ("POST", re.compile(r"/api/org/orgs/[^/]+/missions")),
    ("POST", re.compile(r"/api/org/missions/[^/]+/(stop|accept)")),
]


def need(method: str, path: str) -> str:
    if method in ("GET", "HEAD", "OPTIONS"):
        return "read"
    p = path.rstrip("/")
    if any(m == method and r.fullmatch(p) for m, r in READ_POSTS):
        return "read"
    if any(m == method and r.fullmatch(p) for m, r in WRITE):
        return "write"
    return "admin"


def guard(request: Request) -> None:
    """A FastAPI dependency on every route of this api: keeps an MCP call within its token's level."""
    path = request.state.keel_path if hasattr(request.state, "keel_path") else request.url.path
    if not path.startswith("/api/") or path == "/api/mcp/token":
        return
    who = getattr(request.state, "who", None)
    via = request.headers.get("x-keel-via")
    if who is not None and who != "mcp" and via != "mcp":
        return
    if via != "mcp" and request.headers.get(TOKEN_HEADER) is None:
        return
    tokens: McpTokens = request.app.state.mcp_tokens
    has = tokens.level_of(request.headers.get(TOKEN_HEADER)) or "read"
    needed = need(request.method, path)
    if rank(has) < rank(needed):
        flag = "--admin" if needed == "admin" else "--write"
        raise Forbidden(
            f"This needs the MCP {needed} level: run keel2 mcp {flag}",
            f"This MCP server is at the {has} level; a level comes with a token from keel, not from the client.",
        )
