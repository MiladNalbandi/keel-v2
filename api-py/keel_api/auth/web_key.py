"""The web key, browser sessions and one-time sign-in codes: the same files, rows and cookies as auth/WebSignIn.kt,
so a browser signed in by the Kotlin api stays signed in here and the other way round."""

from __future__ import annotations

import os
import threading
from enum import Enum
from pathlib import Path

from ..common.db import Db
from ..common.support import create_owner_only, http_date, iso, now_ms, random_hex, same, sha256_hex
from .caller import Caller


class WebKey:
    """32 random bytes in $KEEL_DATA/run/web-key (0600), made once; the cookie's name carries the key's id."""

    def __init__(self, data_dir: Path):
        self.file = data_dir / "run" / "web-key"
        self._value: str | None = None
        self._lock = threading.Lock()

    @property
    def value(self) -> str:
        if self._value is None:
            with self._lock:
                if self._value is None:
                    self._value = self._load()
        return self._value

    @property
    def cookie_name(self) -> str:
        return "keel_" + sha256_hex(self.value)[:8]

    @property
    def secure_cookie_name(self) -> str:
        return "__Host-" + self.cookie_name

    def matches(self, candidate: str | None) -> bool:
        return bool(candidate) and same(candidate, self.value)

    def cookie_of(self, caller: Caller) -> str | None:
        c = caller.cookies
        return c.get(self.secure_cookie_name) or c.get(self.cookie_name)

    def _load(self) -> str:
        f = self.file
        if f.is_file():
            text = f.read_text().strip()
            if len(text) >= 32:
                return text
        f.parent.mkdir(parents=True, exist_ok=True)
        made = random_hex(32)
        tmp = f.with_name("web-key.tmp")
        tmp.unlink(missing_ok=True)
        create_owner_only(tmp, (made + "\n").encode())
        os.replace(tmp, f)
        return made


class Check(Enum):
    NO = "no"
    YES = "yes"
    MOVED = "moved"


class WebSessions:
    """A browser's own session id (`ks_` + 64 hex), kept as its SHA-256 in web_sessions and bound to host:port."""

    PREFIX = "ks_"
    TTL_MS = 30 * 24 * 3600 * 1000
    MOVE_EVERY_MS = 3600 * 1000

    def __init__(self, db: Db, key: WebKey):
        self.db = db
        self.key = key

    def open(self, now: int | None = None, host: str | None = None) -> str:
        now = now_ms() if now is None else now
        sid = self.PREFIX + random_hex(32)
        self.db.update("DELETE FROM web_sessions WHERE expires_ms < ?", (now,))
        self.db.update(
            "INSERT INTO web_sessions(id_hash, created_at, last_seen_at, expires_ms, host) VALUES (?, ?, ?, ?, ?)",
            (sha256_hex(sid), iso(now), iso(now), now + self.TTL_MS, host),
        )
        return sid

    def check(self, sid: str | None, now: int | None = None, host: str | None = None) -> Check:
        now = now_ms() if now is None else now
        if sid is None or not sid.startswith(self.PREFIX) or len(sid) != len(self.PREFIX) + 64:
            return Check.NO
        h = sha256_hex(sid)
        row = self.db.one("SELECT expires_ms, host FROM web_sessions WHERE id_hash = ?", (h,))
        if row is None:
            return Check.NO
        until, bound = int(row[0]), row[1]
        if until < now:
            self.db.update("DELETE FROM web_sessions WHERE id_hash = ?", (h,))
            return Check.NO
        if host is not None:
            own = bound
            if own is None:
                if self.db.update("UPDATE web_sessions SET host = ? WHERE id_hash = ? AND host IS NULL", (host, h)) == 1:
                    own = host
                else:
                    r = self.db.one("SELECT host FROM web_sessions WHERE id_hash = ?", (h,))
                    own = r[0] if r else None
            if own != host:
                return Check.NO
        if now + self.TTL_MS - until < self.MOVE_EVERY_MS:
            return Check.YES
        self.db.update(
            "UPDATE web_sessions SET last_seen_at = ?, expires_ms = ? WHERE id_hash = ?", (iso(now), now + self.TTL_MS, h)
        )
        return Check.MOVED

    def end(self, sid: str | None) -> None:
        if sid is not None:
            self.db.update("DELETE FROM web_sessions WHERE id_hash = ?", (sha256_hex(sid),))

    def end_all(self) -> int:
        return self.db.update("DELETE FROM web_sessions")

    def cookie(self, caller: Caller, value: str, max_age_s: int | None = None) -> str:
        """The Set-Cookie value, written as Spring's ResponseCookie.toString writes it."""
        max_age_s = self.TTL_MS // 1000 if max_age_s is None else max_age_s
        secure = https(caller)
        name = self.key.secure_cookie_name if secure else self.key.cookie_name
        expires = now_ms() + max_age_s * 1000 if max_age_s > 0 else 0
        parts = [f"{name}={value}", "Path=/", f"Max-Age={max_age_s}", f"Expires={http_date(expires)}"]
        if secure:
            parts.append("Secure")
        parts += ["HttpOnly", "SameSite=Strict"]
        return "; ".join(parts)


def host_of(caller: Caller) -> str:
    """The address a request came to, as a session is bound to it: the Host header's name and port."""
    return f"{caller.server_name.lower()}:{caller.server_port}"


def https(caller: Caller) -> bool:
    return caller.scheme == "https" or (caller.header("x-forwarded-proto") or "").strip().lower() == "https"


class SignInCodes:
    """One-time sign-in codes: 60 seconds, used once. keel2 asks for one and opens the browser with it."""

    TTL_MS = 60_000
    MAX = 50

    def __init__(self) -> None:
        self._codes: dict[str, int] = {}
        self._lock = threading.Lock()

    def make(self, now: int | None = None) -> str:
        now = now_ms() if now is None else now
        with self._lock:
            for k in [k for k, until in self._codes.items() if until < now]:
                del self._codes[k]
            if len(self._codes) >= self.MAX:
                self._codes.clear()
            code = random_hex(16)
            self._codes[code] = now + self.TTL_MS
            return code

    def use(self, code: str | None, now: int | None = None) -> bool:
        if not code or not code.strip():
            return False
        now = now_ms() if now is None else now
        with self._lock:
            until = self._codes.pop(code.strip(), None)
        return until is not None and until >= now
