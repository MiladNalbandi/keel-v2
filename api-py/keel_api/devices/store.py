"""Paired phones (table devices) and the addresses keel2 registered (run/addresses.json): what the front door needs
from devices/Devices.kt to prove a device token and to know keel's own host names."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..common.db import Db
from ..common.support import iso, now_ms, same, sha256_hex

PREFIX = "kd_"
SEEN_EVERY_MS = 60_000


def looks_like(token: str | None) -> bool:
    return token is not None and token.startswith(PREFIX) and len(token) == len(PREFIX) + 64


@dataclass
class Device:
    id: str
    name: str
    platform: str
    created_at: str
    last_seen_at: str | None
    last_ip: str | None


class DeviceStore:
    def __init__(self, db: Db):
        self.db = db
        self._last_write: dict[str, int] = {}
        self._lock = threading.Lock()

    def by_token(self, token: str) -> Device | None:
        if not looks_like(token):
            return None
        h = sha256_hex(token)
        row = self.db.one("SELECT * FROM devices WHERE token_hash = ?", (h,))
        if row is None or not same(row["token_hash"], h):
            return None
        return Device(row["id"], row["name"], row["platform"], row["created_at"], row["last_seen_at"], row["last_ip"])

    def seen(self, device: Device, ip: str | None, now: int | None = None) -> None:
        """Records a call (time and address), at most once a minute per device."""
        now = now_ms() if now is None else now
        with self._lock:
            last = self._last_write.get(device.id)
            if last is not None and now - last < SEEN_EVERY_MS and ip == device.last_ip:
                return
            self._last_write[device.id] = now
        self.db.update("UPDATE devices SET last_seen_at = ?, last_ip = ? WHERE id = ?", (iso(now), ip, device.id))


class DeviceAddresses:
    """Read only here: the Kotlin api writes the file (PUT /api/devices/addresses); a change is seen by its mtime."""

    def __init__(self, data_dir: Path):
        self.file = data_dir / "run" / "addresses.json"
        self._seen: tuple[float, list[str]] | None = None

    def urls(self) -> list[str]:
        try:
            mtime = self.file.stat().st_mtime
        except OSError:
            return []
        if self._seen is None or self._seen[0] != mtime:
            try:
                items = json.loads(self.file.read_text())
                urls = [i["url"] for i in items if isinstance(i, dict) and isinstance(i.get("url"), str)]
            except (OSError, ValueError):
                urls = []
            self._seen = (mtime, urls)
        return self._seen[1]

    def hosts(self) -> set[str]:
        out = set()
        for u in self.urls():
            try:
                h = urlsplit(u).hostname
            except ValueError:
                h = None
            if h:
                out.add(h.lower())
        return out

    def origins(self) -> set[str]:
        return {u.lower() for u in self.urls()}
