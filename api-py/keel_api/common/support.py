"""Small helpers the Kotlin api keeps in common/Hash.kt, common/Support.kt (Time) and common/OwnerOnly.kt."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone
from email.utils import formatdate
from pathlib import Path


def sha256_hex(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def same(a: str, b: str) -> bool:
    """Constant-time compare of two secrets (Hash.constantTimeEquals)."""
    return hmac.compare_digest(a.encode(), b.encode())


def random_hex(n_bytes: int) -> str:
    return os.urandom(n_bytes).hex()


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso(epoch_ms: int) -> str:
    """Java's ISO_INSTANT: seconds always, the fraction only when it is not zero (in groups of three digits)."""
    dt = datetime.fromtimestamp(epoch_ms // 1000, timezone.utc)
    ms = epoch_ms % 1000
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{ms:03d}Z" if ms else f"{base}Z"


def http_date(epoch_ms: int) -> str:
    """RFC 1123 in GMT, as Spring's HttpHeaders.formatDate writes it."""
    return formatdate(epoch_ms / 1000, usegmt=True)


def create_owner_only(path: Path, data: bytes) -> None:
    """Creates [path] (it must not exist) readable by the owner only from the first byte, and writes [data]."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
