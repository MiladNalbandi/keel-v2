"""The web sign-in routes (auth/WebSignInController.kt)."""

from __future__ import annotations

import re
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..common.errors import Forbidden
from ..mcp.levels import guard
from .caller import Caller
from .limiter import Kind
from .web_key import SignInCodes, host_of

router = APIRouter(dependencies=[Depends(guard)])


def _ctx(request: Request):
    return request.app.state.ctx


@router.post("/api/web/code")
def code(request: Request) -> dict:
    """A one-time sign-in code, only for keel2 and keel's scripts (the internal token)."""
    if getattr(request.state, "who", None) != "internal":
        raise Forbidden("Only keel2 can make a sign-in code", "Run keel2 open in a terminal.")
    c = _ctx(request).codes.make()
    return {"code": c, "path": f"/web/login?code={c}", "expires_in": SignInCodes.TTL_MS // 1000}


@router.get("/web/login")
def login(request: Request, code: str | None = None, next: str | None = None) -> Response:
    """The link keel2 opens: a fresh code sets the sign-in cookie and goes to keel's page; a used or old one says so."""
    ctx = _ctx(request)
    caller = Caller.of(request.scope)
    if ctx.codes.use(code):
        cookie = ctx.sessions.cookie(caller, ctx.sessions.open(host=host_of(caller)))
        return Response(status_code=302, headers={"Location": safe_next(next), "Set-Cookie": cookie})
    # wrong codes count like wrong keys, checked after the code, so a right code always signs in
    if code is not None and code.strip():
        if ctx.limiter.blocked(Kind.SIGN_IN, caller):
            return Response(status_code=429, headers={"Retry-After": str(ctx.limiter.retry_after())})
        ctx.limiter.fail(Kind.SIGN_IN, caller)
    return Response(status_code=302, headers={"Location": "/?signin=expired"})


@router.post("/api/web/sign-out")
def sign_out(request: Request) -> Response:
    """Signs this browser out: its session ends and the cookie goes; other browsers stay signed in."""
    ctx = _ctx(request)
    caller = Caller.of(request.scope)
    ctx.sessions.end(ctx.key.cookie_of(caller))
    return JSONResponse({"ok": True}, headers={"Set-Cookie": ctx.sessions.cookie(caller, "", 0)})


@router.post("/api/web/sign-out-everywhere")
def sign_out_everywhere(request: Request) -> Response:
    """Signs every browser out; a terminal app's web key and paired phones stay."""
    ctx = _ctx(request)
    n = ctx.sessions.end_all()
    return JSONResponse({"ok": True, "ended": n}, headers={"Set-Cookie": ctx.sessions.cookie(Caller.of(request.scope), "", 0)})


@router.get("/api/web/me")
def me(request: Request) -> dict:
    return {"signed_in": True, "via": getattr(request.state, "who", None)}


_BROKEN_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")


def safe_next(nxt: str | None) -> str:
    """Only a path on this keel ("/flow?x=1"), never another site ("//evil", "https://…")."""
    n = (nxt or "").strip()
    # what the browser will see: it decodes %XX and drops tab and newline, so "/%09/evil.com" becomes "//evil.com"
    seen = n
    for _ in range(3):
        s = seen.replace("+", "%2B")
        if _BROKEN_ESCAPE.search(s):
            return "/"
        seen = unquote(s, encoding="utf-8", errors="replace")
    if any(ch < " " or ch == "\x7f" or ch == "\\" for ch in seen):
        return "/"
    if n.startswith("/") and seen.startswith("/") and not seen.startswith("//") and "://" not in seen:
        return n
    return "/"
