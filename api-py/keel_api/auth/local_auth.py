"""keel's front door proves every caller: the same checks, in the same order, as auth/LocalAuth.kt.

 1. the Host must be keel's own (localhost, an IP address, KEEL_PUBLIC_URL's host, KEEL_ALLOWED_HOSTS, keel2's
    registered addresses): a web page cannot reach keel through DNS rebinding;
 2. the path is read the way the controllers read it; an api path written with `;`, `//` or an encoded `/` is refused
    (400); outside /api and /internal nothing more is needed, and neither is it for GET /api/health;
 3. X-Keel-Mcp-Token = an MCP level token;  4. X-Keel-Token = the internal token;
 5. Authorization: Bearer <web key> (a terminal app) or kd_… (a paired phone);
 6. the sign-in cookie (a browser's session): a GET passes, any other method also needs X-Keel-Web: 1 and, when the
    browser sends one, an Origin that is keel's own;
 7. anything else: 401 with X-Keel-Auth: sign-in.
A wrong token is refused (403), never treated as "no token". Wrong web keys, cookies and device tokens are counted per
client: too many in a minute → 429 for the next wrong one (a right one always passes).

It runs as a raw ASGI middleware, so it reads the path as it came on the wire, before anything decodes it.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import anyio
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..common import api_path
from ..common.errors import body
from ..common.support import same
from ..devices.store import PREFIX as DEVICE_PREFIX
from .caller import Caller
from .limiter import Kind
from .web_key import Check, host_of

INTERNAL_HEADER = "x-keel-token"
MCP_HEADER = "x-keel-mcp-token"
WEB_HEADER = "x-keel-web"
AUTH_HEADER = "X-Keel-Auth"
SIGN_IN = "keel needs you to sign in"
HINT = "Run keel2 open in a terminal: it opens keel signed in. (keel2 open --print prints the link.)"
RELOAD = "Reload keel's page. If it keeps happening, run keel2 open."
DEVICE_GONE = "This device was removed"
DEVICE_GONE_HINT = "Pair it again in keel: Settings › Access"
SAFE = {"GET", "HEAD", "OPTIONS"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"}
OPEN_GETS = {"/api/health"}
OPEN_POSTS = {"/api/devices/pair"}
_IPV4 = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3}){3}")
_IPV6 = re.compile(r"[0-9a-f:.]+")


@dataclass
class Decision:
    who: str | None = None  # set: the request passes
    device: str | None = None
    status: int = 0
    error: str = ""
    hint: str | None = None
    sign_in: bool = False
    retry_after: int | None = None
    set_cookies: list[str] = field(default_factory=list)


class LocalAuth:
    def __init__(self, app: ASGIApp, ctx) -> None:
        self.app = app
        self.ctx = ctx

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        caller = Caller.of(scope)
        d = await anyio.to_thread.run_sync(self.decide, caller)
        if d.who is None and d.status:
            headers = {}
            if d.sign_in:
                headers[AUTH_HEADER] = "sign-in"
            if d.retry_after is not None:
                headers["Retry-After"] = str(d.retry_after)
            return await JSONResponse(body(d.error, d.hint), status_code=d.status, headers=headers)(scope, receive, send)
        state = scope.setdefault("state", {})
        state["keel_path"] = caller.path
        if d.who is not None:
            state["who"] = d.who
        if d.device is not None:
            state["device"] = d.device
        if not d.set_cookies:
            return await self.app(scope, receive, send)

        async def send_with_cookies(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"set-cookie", c.encode("latin-1")) for c in d.set_cookies
                ]
            await send(message)

        await self.app(scope, receive, send_with_cookies)

    # ------------------------------------------------------------------ the decision (sync: it reads the database)

    def decide(self, caller: Caller) -> Decision:
        ctx = self.ctx
        if not self.host_allowed(caller.server_name):
            return refuse(403, "This keel only answers on localhost",
                          "Open it as http://localhost:<port>. For another name, set KEEL_PUBLIC_URL or KEEL_ALLOWED_HOSTS.")
        path = caller.path
        if not api_path.is_api(path):
            return Decision()
        if api_path.ambiguous(caller.raw_path):
            return refuse(400, "This path is written in an unusual way",
                          "Use the plain path, for example /api/projects: no ';', '//', '.' segments or encoded '/' or '.'.")
        if self.open(caller.method, path):
            return Decision()
        t = caller.header(MCP_HEADER)
        if t is not None:
            if ctx.mcp_tokens.level_of(t) is not None:
                return Decision(who="mcp")
            return refuse(403, "Wrong MCP token",
                          "keel made new tokens: start the MCP server again (your client's /mcp, or keel2 mcp).")
        t = caller.header(INTERNAL_HEADER)
        if t is not None:
            if ctx.settings.internal_token and same(t, ctx.settings.internal_token):
                return Decision(who="internal")
            return refuse(403, "Wrong X-Keel-Token", HINT)
        a = caller.header("authorization")
        if a is not None:
            bearer = a[len("Bearer "):].strip() if a.startswith("Bearer ") else ""
            if bearer.startswith(DEVICE_PREFIX):
                dev = ctx.devices.by_token(bearer)
                if dev is None:
                    return self.wrong_try(caller, refuse(403, DEVICE_GONE, DEVICE_GONE_HINT))
                if path.startswith("/internal/"):
                    return refuse(403, "A device cannot call keel's internal routes", None)
                ctx.devices.seen(dev, ctx.client_ip.of(caller))
                return Decision(who="device", device=dev.id)
            if ctx.key.matches(bearer):
                return Decision(who="person")
            return self.wrong_try(caller, refuse(403, "Wrong sign-in key", HINT))
        cookie = ctx.key.cookie_of(caller)
        if cookie is not None:
            cookies: list[str] = []
            if self.signed_in(cookie, caller, cookies):
                if caller.method in SAFE:
                    return Decision(who="browser", set_cookies=cookies)
                if caller.header(WEB_HEADER) != "1":
                    return refuse(403, "This change did not come from keel's own page", RELOAD)
                origin = caller.header("origin")
                if origin is not None and not self.origin_allowed(origin, caller):
                    return refuse(403, f"This change came from another site ({origin})", RELOAD)
                return Decision(who="browser", set_cookies=cookies)
            return self.wrong_try(caller, refuse(401, SIGN_IN, HINT, sign_in=True))
        return refuse(401, SIGN_IN, HINT, sign_in=True)

    def wrong_try(self, caller: Caller, answer: Decision) -> Decision:
        lim = self.ctx.limiter
        if lim.blocked(Kind.SIGN_IN, caller):
            return refuse(429, "Too many wrong sign-in tries", "Wait a minute and try again.", retry_after=lim.retry_after())
        lim.fail(Kind.SIGN_IN, caller)
        return answer

    def signed_in(self, cookie: str, caller: Caller, set_cookies: list[str]) -> bool:
        sessions = self.ctx.sessions
        c = sessions.check(cookie, host=host_of(caller))
        if c is Check.YES:
            return True
        if c is Check.MOVED:
            set_cookies.append(sessions.cookie(caller, cookie))
            return True
        # a cookie from before 0.18.64 held the web key itself: swapped for a session once
        if self.ctx.key.matches(cookie):
            set_cookies.append(sessions.cookie(caller, sessions.open(host=host_of(caller))))
            return True
        return False

    @staticmethod
    def open(method: str, path: str) -> bool:
        p = path.rstrip("/")
        return (method in ("GET", "HEAD") and p in OPEN_GETS) or (method == "POST" and p in OPEN_POSTS)

    def host_allowed(self, host: str | None) -> bool:
        if host is None:
            return False
        h = host.strip().lower().removeprefix("[").removesuffix("]")
        if h in LOCAL_HOSTS or h.endswith(".localhost"):
            return True
        if ip_literal(h):
            return True
        return h in self.extra_hosts or h in self.ctx.addresses.hosts()

    @property
    def extra_hosts(self) -> set[str]:
        s = self.ctx.settings
        from_url = []
        if s.public_url.strip():
            try:
                h = urlsplit(s.public_url.strip()).hostname
            except ValueError:
                h = None
            if h:
                from_url.append(h)
        return {x.strip().lower() for x in s.allowed_hosts.split(",") + from_url if x.strip()}

    def origin_allowed(self, origin: str, caller: Caller) -> bool:
        o = origin.strip().rstrip("/").lower()
        port = caller.server_port
        base = f"{caller.scheme}://{caller.server_name}".lower()
        default = (caller.scheme == "http" and port == 80) or (caller.scheme == "https" and port == 443)
        own = base if default else f"{base}:{port}"
        if o == own:
            return True
        s = self.ctx.settings
        listed = [x.strip().rstrip("/").lower() for x in s.dev_origins.split(",") + [s.public_url] + list(self.ctx.addresses.origins())]
        return o in {origin_of(x) for x in listed if x}


def refuse(status: int, error: str, hint: str | None, sign_in: bool = False, retry_after: int | None = None) -> Decision:
    return Decision(status=status, error=error, hint=hint, sign_in=sign_in, retry_after=retry_after)


def ip_literal(h: str) -> bool:
    """192.168.1.20, ::1, fe80::1: an address, not a name (a name can be rebound to 127.0.0.1; an address cannot)."""
    if _IPV4.fullmatch(h) and all(int(p) <= 255 for p in h.split(".")):
        return True
    return ":" in h and bool(_IPV6.fullmatch(h))


def origin_of(url: str) -> str:
    try:
        u = urlsplit(url)
        if not u.scheme or u.hostname is None:
            return url
        host = u.hostname
        if ":" in host:
            try:
                ipaddress.ip_address(host)
                host = f"[{host}]"
            except ValueError:
                pass
        return f"{u.scheme}://{host}" if u.port is None else f"{u.scheme}://{host}:{u.port}"
    except ValueError:
        return url
