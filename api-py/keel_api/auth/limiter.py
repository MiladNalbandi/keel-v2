"""Wrong sign-in tries per client, and who the client is behind keel2's proxies (auth/AuthLimiter.kt, ClientIp)."""

from __future__ import annotations

import ipaddress
import threading
from collections import deque
from enum import Enum
from pathlib import Path

from ..common.support import now_ms
from .caller import Caller


class Kind(Enum):
    SIGN_IN = (20, 200)  # the web key, the sign-in cookie, a device token, a sign-in code
    PAIR = (5, 30)  # a phone's pairing code: guessable in principle, so far fewer tries

    @property
    def per_client(self) -> int:
        return self.value[0]

    @property
    def total(self) -> int:
        return self.value[1]


WINDOW_MS = 60_000
MAX_KEYS = 10_000


class ClientIp:
    def __init__(self, trusted_proxies: str = "", trusted_for_tests: set[str] | None = None):
        self._listed = [x.strip() for x in trusted_proxies.split(",") if x.strip()]
        self.trusted_for_tests = trusted_for_tests
        self._configured: set[str] | None = None

    def of(self, caller: Caller) -> str:
        remote = caller.remote
        if not self.trusted_proxy(remote):
            return remote
        cf = (caller.header("cf-connecting-ip") or "").strip()
        if cf:
            return cf[:64]
        xff = caller.header("x-forwarded-for")
        if xff is not None:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                return parts[-1][:64]
        return remote

    def direct(self, caller: Caller) -> bool:
        """A call made on this computer itself, not passed on by a proxy."""
        return (
            loopback(caller.remote)
            and caller.header("cf-connecting-ip") is None
            and caller.header("x-forwarded-for") is None
        )

    def trusted_proxy(self, ip: str) -> bool:
        if loopback(ip):
            return True
        trusted = self.trusted_for_tests if self.trusted_for_tests is not None else self.configured
        return normal(ip) in trusted

    @property
    def configured(self) -> set[str]:
        if self._configured is None:
            gw = container_gateway()
            self._configured = {normal(x) for x in self._listed + ([gw] if gw else [])}
        return self._configured


def loopback(ip: str) -> bool:
    return ip in ("127.0.0.1", "::1", "0:0:0:0:0:0:0:1") or ip.startswith("127.")


def normal(ip: str) -> str:
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return ip


def container_gateway() -> str | None:
    if not Path("/.dockerenv").exists() and not Path("/run/.containerenv").exists():
        return None
    try:
        return gateway_of(Path("/proc/net/route").read_text().splitlines())
    except OSError:
        return None


def gateway_of(route_lines: list[str]) -> str | None:
    """The default route's gateway in /proc/net/route's form (hex, little-endian), as 172.17.0.1."""
    for line in route_lines[1:]:
        cols = line.split()
        if len(cols) > 2 and cols[1] == "00000000" and cols[2] != "00000000":
            try:
                n = int(cols[2], 16)
            except ValueError:
                return None
            return ".".join(str((n >> (8 * i)) & 0xFF) for i in range(4))
    return None


class AuthLimiter:
    def __init__(self, client_ip: ClientIp):
        self.client_ip = client_ip
        self._fails: dict[str, deque[int]] = {}
        self._lock = threading.Lock()

    def blocked(self, kind: Kind, caller: Caller, now: int | None = None) -> bool:
        if self.client_ip.direct(caller):
            return False
        now = now_ms() if now is None else now
        ip = self.client_ip.of(caller)
        return self._count(f"{kind.name}|{ip}", now) >= kind.per_client or self._count(f"{kind.name}|*", now) >= kind.total

    def fail(self, kind: Kind, caller: Caller, now: int | None = None) -> None:
        if self.client_ip.direct(caller):
            return
        now = now_ms() if now is None else now
        ip = self.client_ip.of(caller)
        with self._lock:
            if len(self._fails) > MAX_KEYS:
                for k in [k for k, q in self._fails.items() if not any(t > now - WINDOW_MS for t in q)]:
                    del self._fails[k]
            for k in (f"{kind.name}|{ip}", f"{kind.name}|*"):
                q = self._fails.setdefault(k, deque())
                q.append(now)
                while len(q) > 1000:
                    q.popleft()

    @staticmethod
    def retry_after() -> int:
        return WINDOW_MS // 1000

    def clear(self) -> None:
        with self._lock:
            self._fails.clear()

    def _count(self, key: str, now: int) -> int:
        with self._lock:
            q = self._fails.get(key)
            if q is None:
                return 0
            while q and q[0] <= now - WINDOW_MS:
                q.popleft()
            return len(q)


