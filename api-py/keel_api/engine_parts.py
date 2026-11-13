"""A plugin part's routes that live in keel's engine, passed on to it: /api/<part>/<rest> → <engine>/<part>/<rest>.

The Org plugin is Python only (plugins/org): no Kotlin jar answers its routes; neither does the flow doctor (#101). LocalAuth has checked the caller
already; the engine is asked with the internal token, and the caller's own credentials never reach it.

    PARTS     the parts whose routes the engine answers, by their path prefix under /api
"""

from __future__ import annotations

import httpx
from starlette.requests import Request
from starlette.responses import Response

from .common.errors import error_response

PARTS = ("org", "flow-doctor")   # #101 part 3 the flow doctor's report and crash log
# what the engine must not see from the caller: its credentials and the hop headers
DROP = {"cookie", "authorization", "x-keel-token", "x-keel-mcp-token", "host", "content-length", "connection",
        "transfer-encoding", "keep-alive", "upgrade", "te", "trailer", "proxy-authorization", "proxy-connection"}
KEEP_OUT = {"content-type", "cache-control"}
TIMEOUT = httpx.Timeout(120.0, connect=3.0)


def part_of(path: str) -> str | None:
    """The part an /api path belongs to, when the engine answers it (/api/org/orgs → org)."""
    if not path.startswith("/api/"):
        return None
    head = path[len("/api/"):].split("/", 1)[0]
    return head if head in PARTS else None


class EngineParts:
    def __init__(self, url: str, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.token = token
        self.client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=TIMEOUT, follow_redirects=False,
                                        transport=transport)

    async def close(self) -> None:
        await self.client.aclose()

    async def forward(self, request: Request, path: str) -> Response:
        target = path[len("/api"):]
        qs = request.scope.get("query_string") or b""
        if qs:
            target += "?" + qs.decode("latin-1")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP}
        headers["X-Keel-Token"] = self.token
        try:
            r = await self.client.request(request.method, target, headers=headers, content=await request.body())
        except httpx.TransportError:
            return error_response(503, "keel's engine is not answering", "It is starting or restarting: try again in a few seconds.")
        out = {k: v for k, v in r.headers.items() if k.lower() in KEEP_OUT}
        return Response(r.content, status_code=r.status_code, headers=out)
