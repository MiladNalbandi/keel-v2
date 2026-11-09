"""The api's settings, from the same environment variables the Kotlin api reads (api/src/main/resources/application.yml)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    data: Path = field(default_factory=lambda: Path(_env("KEEL_DATA", "./.data")))
    internal_token: str = field(default_factory=lambda: _env("KEEL_INTERNAL_TOKEN"))
    public_url: str = field(default_factory=lambda: _env("KEEL_PUBLIC_URL"))
    allowed_hosts: str = field(default_factory=lambda: _env("KEEL_ALLOWED_HOSTS"))
    dev_origins: str = field(default_factory=lambda: _env("KEEL_DEV_ORIGINS"))
    trusted_proxies: str = field(default_factory=lambda: _env("KEEL_TRUSTED_PROXIES"))
    # The Kotlin api behind this one: every route not ported yet is passed on to it.
    back_url: str = field(default_factory=lambda: _env("KEEL_BACK_URL", "http://127.0.0.1:8081"))
    # The built web app (web/dist). Empty: the web's files are passed on to the Kotlin api, which has them in its jar.
    web_dir: str = field(default_factory=lambda: _env("KEEL_WEB_DIR"))
    host: str = field(default_factory=lambda: _env("SERVER_ADDRESS", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("KEEL_PORT", "8080")))

    @property
    def data_dir(self) -> Path:
        self.data.mkdir(parents=True, exist_ok=True)
        return self.data

    @property
    def db_file(self) -> Path:
        return self.data_dir / "keel.db"

    @property
    def web_path(self) -> Path | None:
        if not self.web_dir:
            return None
        p = Path(self.web_dir)
        return p if (p / "index.html").is_file() else None
