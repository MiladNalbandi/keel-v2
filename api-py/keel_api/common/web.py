"""The built web app: its files with a single-page-app fallback (common/AppConfig.kt WebConfig) and the page's
Content-Security-Policy (common/PageSecurity.kt)."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from starlette.responses import FileResponse, Response

from .api_path import clean, first_part
from .errors import error_response

# First path parts that are never the web app: the api, the engine's internal calls, plugin web files, keel v1's
# old dashboard. They answer 404 instead of index.html.
NOT_WEB = {"api", "internal", "plugins", "keel-v1"}

NO_CACHE = "no-cache"
IMMUTABLE = "max-age=31536000, public, immutable"

_INLINE_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.DOTALL | re.IGNORECASE)
_SRC = re.compile(r"\bsrc\s*=", re.IGNORECASE)


def web_path(path: str) -> bool:
    return first_part(path) not in NOT_WEB


def inline_script_hashes(html: str) -> list[str]:
    out: list[str] = []
    for m in _INLINE_SCRIPT.finditer(html):
        attrs, text = m.group(1), m.group(2)
        if _SRC.search(attrs) or not text:
            continue
        h = "sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()
        if h not in out:
            out.append(h)
    return out


def policy_for(script_hashes: list[str]) -> str:
    return "; ".join([
        "default-src 'self'",
        "script-src 'self'" + "".join(f" '{h}'" for h in script_hashes),
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: blob: https:",
        "media-src 'self' https://github.com https://*.githubusercontent.com",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


class WebApp:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.index = self.root / "index.html"
        self.policy = policy_for(inline_script_hashes(self.index.read_text(encoding="utf-8")))

    def answer(self, raw_path: str) -> Response:
        path = clean(raw_path)
        rel = path.lstrip("/")
        if rel in ("", "index.html"):
            return self._file(self.index, NO_CACHE)
        if rel.startswith("assets/"):
            found = self._find(rel)
            return self._file(found, IMMUTABLE) if found else self._missing(rel)
        found = self._find(rel)
        if found:
            return self._file(found, NO_CACHE)
        # a path with a file extension is a real file request: never answered with index.html
        if "." in rel.rsplit("/", 1)[-1]:
            return self._missing(rel)
        return self._file(self.index, NO_CACHE)

    def _find(self, rel: str) -> Path | None:
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            return None
        return p if p.is_file() else None

    @staticmethod
    def _file(p: Path, cache: str) -> Response:
        return FileResponse(p, headers={"Cache-Control": cache})

    @staticmethod
    def _missing(rel: str) -> Response:
        return error_response(404, "Not found", f"/{rel}")
