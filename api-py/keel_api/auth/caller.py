"""What the auth code needs to know about a request, read from the ASGI scope the way Tomcat reads it."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from starlette.datastructures import Headers
from starlette.requests import cookie_parser

from ..common.api_path import clean


@dataclass
class Caller:
    method: str
    raw_path: str
    scheme: str
    remote: str
    headers: Headers

    @classmethod
    def of(cls, scope: dict) -> "Caller":
        raw = scope.get("raw_path")
        raw_path = raw.decode("latin-1") if isinstance(raw, (bytes, bytearray)) else scope.get("path", "")
        client = scope.get("client")
        return cls(
            method=scope.get("method", "GET"),
            raw_path=raw_path.split("?", 1)[0],
            scheme=scope.get("scheme", "http"),
            remote=client[0] if client else "",
            headers=Headers(scope=scope),
        )

    @cached_property
    def path(self) -> str:
        return clean(self.raw_path)

    @cached_property
    def _host(self) -> tuple[str, int]:
        h = (self.headers.get("host") or "").strip()
        default = 443 if self.scheme == "https" else 80
        if h.startswith("["):
            end = h.find("]")
            name, rest = (h[: end + 1], h[end + 1 :]) if end >= 0 else (h, "")
            port = rest[1:] if rest.startswith(":") else ""
        elif ":" in h:
            name, port = h.rsplit(":", 1)
        else:
            name, port = h, ""
        return name, int(port) if port.isdigit() else default

    @property
    def server_name(self) -> str:
        return self._host[0]

    @property
    def server_port(self) -> int:
        return self._host[1]

    @cached_property
    def cookies(self) -> dict[str, str]:
        return cookie_parser(self.headers.get("cookie", ""))

    def header(self, name: str) -> str | None:
        return self.headers.get(name)
