"""The Python api: the front door on :8080.

It answers the routes ported so far and passes every other one on to the Kotlin api (proxy.py), which listens on
127.0.0.1 only. Clients see one keel; each release moves more routes here (docs/plans/python-api.md).

    clients ──► :8080 this api ── auth, path checks, CSP, the web app, ported routes
                        └── any other route ──► 127.0.0.1:8081 the Kotlin api
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .auth.limiter import AuthLimiter, ClientIp
from .auth.local_auth import LocalAuth
from .auth.routes import router as auth_routes
from .auth.web_key import SignInCodes, WebKey, WebSessions
from .common import errors
from .common.api_path import clean
from .common.db import Db
from .common.web import WebApp, policy_for, web_path
from .devices.store import DeviceAddresses, DeviceStore
from .engine_parts import EngineParts, part_of
from .mcp.levels import McpTokens, guard
from .proxy import Back
from .safety.routes import router as safety_routes
from .settings import Settings

ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@dataclass
class Ctx:
    settings: Settings
    db: Db
    key: WebKey
    sessions: WebSessions
    codes: SignInCodes
    client_ip: ClientIp
    limiter: AuthLimiter
    devices: DeviceStore
    addresses: DeviceAddresses
    mcp_tokens: McpTokens
    web: WebApp | None
    back: Back
    parts: EngineParts


def make_ctx(settings: Settings, back_transport: httpx.AsyncBaseTransport | None = None,
             trusted_for_tests: set[str] | None = None, engine_transport: httpx.AsyncBaseTransport | None = None) -> Ctx:
    db = Db(settings.db_file)
    key = WebKey(settings.data_dir)
    client_ip = ClientIp(settings.trusted_proxies, trusted_for_tests)
    web_root = settings.web_path
    return Ctx(
        settings=settings,
        db=db,
        key=key,
        sessions=WebSessions(db, key),
        codes=SignInCodes(),
        client_ip=client_ip,
        limiter=AuthLimiter(client_ip),
        devices=DeviceStore(db),
        addresses=DeviceAddresses(settings.data_dir),
        mcp_tokens=McpTokens(settings.internal_token),
        web=WebApp(web_root) if web_root else None,
        back=Back(settings.back_url, back_transport),
        parts=EngineParts(settings.engine_url, settings.internal_token, engine_transport),
    )


class PageSecurity:
    """The web app's Content-Security-Policy and nosniff on every web path (common/PageSecurity.kt). A page the
    Kotlin api served keeps its own policy."""

    def __init__(self, app: ASGIApp, ctx: Ctx) -> None:
        self.app = app
        self.policy = ctx.web.policy if ctx.web else policy_for([])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        raw = scope.get("raw_path") or scope.get("path", "").encode()
        if not web_path(clean(raw.decode("latin-1").split("?", 1)[0])):
            return await self.app(scope, receive, send)

        async def send_secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                if b"content-security-policy" not in names:
                    headers.append((b"content-security-policy", self.policy.encode()))
                if b"x-content-type-options" not in names:
                    headers.append((b"x-content-type-options", b"nosniff"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_secured)


def create_app(settings: Settings | None = None, ctx: Ctx | None = None) -> FastAPI:
    ctx = ctx or make_ctx(settings or Settings())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        ctx.key.value  # made at start: a terminal app reads the file before its first request
        yield
        await ctx.back.close()
        await ctx.parts.close()
        ctx.db.close()

    # no /docs, /redoc or /openapi.json: those paths belong to the web app
    app = FastAPI(title="keel api", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.ctx = ctx
    app.state.mcp_tokens = ctx.mcp_tokens
    errors.install(app)
    app.include_router(auth_routes)
    app.include_router(safety_routes)

    # last: whatever no route above answers. The web app's files when this api has them, else the Kotlin api.
    @app.api_route("/{rest:path}", methods=ALL_METHODS, include_in_schema=False)
    async def everything_else(request: Request) -> Response:
        raw = request.scope.get("raw_path") or b""
        raw_path = raw.decode("latin-1").split("?", 1)[0] or request.url.path
        if ctx.web and request.method in ("GET", "HEAD") and web_path(clean(raw_path)):
            return ctx.web.answer(raw_path)
        # a plugin part that lives in the engine only (the Org plugin): its routes go to the engine
        path = clean(raw_path)
        if part_of(path):
            guard(request)          # an MCP call stays within its token's level (Kotlin's guard never sees these routes)
            return await ctx.parts.forward(request, path)
        return await ctx.back.forward(request, ctx.client_ip)

    # Starlette runs the middleware added last first: PageSecurity, then LocalAuth, then the routes
    app.add_middleware(LocalAuth, ctx=ctx)
    app.add_middleware(PageSecurity, ctx=ctx)
    return app
