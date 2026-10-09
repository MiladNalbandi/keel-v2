"""Environment settings (see docs/CONTRACT.md, Environment variables). Read on each call so tests can change them."""

from __future__ import annotations

import os
from pathlib import Path

VERSION = "0.15.6"   # keel v2 release; a test keeps it equal to KEEL2_VERSION in keel2


def data_dir() -> Path:
    d = Path(os.environ.get("KEEL_DATA") or "./.data").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def api_url() -> str:
    return os.environ.get("KEEL_API_URL", "http://127.0.0.1:8080").rstrip("/")


def internal_token() -> str:
    return os.environ.get("KEEL_INTERNAL_TOKEN", "")


def fake() -> bool:
    return os.environ.get("KEEL_FAKE", "0") == "1"


def fake_delay() -> float:
    """Seconds between fake agent steps, so a demo looks alive. 0 in tests."""
    try:
        return float(os.environ.get("KEEL_FAKE_DELAY", "0"))
    except ValueError:
        return 0.0


def host() -> str:
    return os.environ.get("KEEL_ENGINE_HOST", "127.0.0.1")


def port() -> int:
    return int(os.environ.get("KEEL_ENGINE_PORT", "8090"))


def workspace() -> Path:
    """Where the project is mounted: /workspace, or its real path with `keel2 start --docker`."""
    return Path(os.environ.get("KEEL_WORKSPACE") or "/workspace")


def content_dir() -> Path:
    """keel v2's own content (agents, skills, stacks, packs, templates): KEEL_CONTENT, else /opt/keel-v2/content,
    else <repo>/content (dev)."""
    if os.environ.get("KEEL_CONTENT"):
        return Path(os.environ["KEEL_CONTENT"])
    image = Path("/opt/keel-v2/content")
    if image.is_dir():
        return image
    return Path(__file__).resolve().parents[2] / "content"


def v2_skills() -> Path:
    """keel v2's skills: content_dir()/skills."""
    return content_dir() / "skills"
