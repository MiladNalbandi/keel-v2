"""Where plugins live on disk, the KEEL_PLUGINS switch, and installed.json (what the person chose)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import PluginError

MODES = ("on", "image", "off")


def image_root() -> Path:
    """The plugins that came in the image: $KEEL_PLUGINS_IMAGE (default /opt/keel-v2/plugins), <root>/<name>/<version>/."""
    return Path(os.path.abspath(os.environ.get("KEEL_PLUGINS_IMAGE") or "/opt/keel-v2/plugins"))


def plugins_dir() -> Path:
    """$KEEL_DATA/plugins (not created here: listing must not write anything)."""
    return Path(os.path.abspath(os.environ.get("KEEL_DATA") or "./.data")) / "plugins"


def store_dir() -> Path:
    """Installed plugins: store/<name>/<version>/."""
    return plugins_dir() / "store"


def run_dir() -> Path:
    """What the resolver writes at each start: resolved.json and env."""
    return plugins_dir() / "run"


def installed_path() -> Path:
    return plugins_dir() / "installed.json"


def mode() -> str:
    """KEEL_PLUGINS: on (default, image + installed), image (image only, safe mode) or off (no plugins)."""
    m = (os.environ.get("KEEL_PLUGINS") or "on").strip().lower()
    if m not in MODES:
        raise PluginError(f"KEEL_PLUGINS must be on, image or off, not '{m}'")
    return m


def now() -> str:
    """UTC time as installed.json and resolved.json write it: 2026-10-08T18:00:00Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_installed() -> dict:
    """installed.json as {"plugins": {name: entry}}; no file means nothing chosen. A broken file is an error, so
    nothing quietly turns a plugin back on."""
    f = installed_path()
    if not f.is_file():
        return {"plugins": {}}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PluginError(f"{f} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.setdefault("plugins", {}), dict):
        raise PluginError(f"{f}: 'plugins' must be an object of name → entry")
    return data


def write_atomic(path: Path, text: str) -> None:
    """Write a file whole or not at all: a temp file next to it, then a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_installed(data: dict) -> None:
    write_atomic(installed_path(), json.dumps(data, indent=2, sort_keys=False) + "\n")
