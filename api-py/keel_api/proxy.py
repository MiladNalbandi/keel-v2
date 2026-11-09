"""Passes a request this api does not answer yet on to the Kotlin api, as it is, and streams the answer back (SSE too).

The Kotlin api still checks every request itself (its own LocalAuth). It sees this api as the caller (127.0.0.1), so the
real client goes in X-Forwarded-For, exactly as keel2's proxies would send it; a direct call from this computer stays
direct (no proxy header), so the Kotlin api never counts its wrong tries or limits it.
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from .auth.caller import Caller
from .common.errors import error_response

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
    "transfer-encoding", "upgrade",
}
# set again here: the length of the body this api sends, and who the client is
DROP_IN = HOP_BY_HOP | {"content-length", "x-forwarded-for", "cf-connecting-ip"}


class Back:
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            timeout=httpx.Timeout(None, connect=3.0),
            follow_redirects=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def forward(self, request: Request, client_ip) -> Response:
        caller = Caller.of(request.scope)
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in DROP_IN]
        if not client_ip.direct(caller):
            headers.append(("x-forwarded-for", client_ip.of(caller)))
        target = caller.raw_path
        qs = request.scope.get("query_string") or b""
        if qs:
            target += "?" + qs.decode("latin-1")
        content = await request.body()
        req = self.client.build_request(request.method, target, headers=headers, content=content)
        try:
            upstream = await self.client.send(req, stream=True)
        except httpx.TransportError:
            return error_response(503, "keel's api is not ready yet", "It is starting or restarting: try again in a few seconds.")

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            except httpx.TransportError:
                return
            finally:
                await upstream.aclose()

        out = StreamingResponse(stream(), status_code=upstream.status_code)
        out.raw_headers = [
            (k.encode("latin-1"), v.encode("latin-1"))
            for k, v in upstream.headers.multi_items()
            if k.lower() not in HOP_BY_HOP
        ]
        return out
