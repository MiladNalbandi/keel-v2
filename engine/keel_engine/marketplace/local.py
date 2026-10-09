"""What this keel has: the plugins in the image and the installed ones (installed.json), and what its last start
loaded (run/resolved.json). Read only; the plugin host's resolver stays the one that decides what loads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..addons import NAME
from ..pluginhost import PluginError, manifest, state
from ..pluginhost.manifest import Manifest
from ..pluginhost.resolver import version_key
from . import MarketError

IMAGE, MARKETPLACE, FILE = "image", "marketplace", "file"


@dataclass
class Had:
    """One plugin keel has: the version the person chose (installed.json), else the image's."""
    name: str
    version: str
    source: str             # image | marketplace | file
    on: bool
    dir: Path
    entry: dict             # its installed.json entry ({} for an image plugin nobody changed)
    image_version: str | None = None

    _manifest: Manifest | None = None
    _read: bool = False

    @property
    def manifest(self) -> Manifest | None:
        """Its keel-plugin.yml, or None when it does not read (the resolver says why)."""
        if not self._read:
            self._read = True
            try:
                self._manifest = manifest.read(self.dir)
            except PluginError:
                self._manifest = None
        return self._manifest

    @property
    def title(self) -> str:
        return self.manifest.title if self.manifest else self.name

    @property
    def needs(self) -> dict[str, str]:
        return dict(self.manifest.plugins) if self.manifest else {}


def _folders(path: Path) -> list[Path]:
    try:
        return sorted(p for p in path.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def image_versions() -> dict[str, Path]:
    """name → the newest version folder of each plugin in the image."""
    out: dict[str, Path] = {}
    for name_dir in _folders(state.image_root()):
        versions = sorted(_folders(name_dir), key=lambda p: version_key(p.name))
        if versions:
            out[name_dir.name] = versions[-1]
    return out


def installed_entries() -> dict[str, dict]:
    """installed.json's entries. A broken file stops every change (nothing quietly turns a plugin back on)."""
    try:
        return {k: v for k, v in state.read_installed()["plugins"].items() if isinstance(v, dict)}
    except PluginError as exc:
        raise MarketError(500, f"keel's plugin choices do not read: {exc}",
                          "Fix or remove $KEEL_DATA/plugins/installed.json, then try again.") from None


def have() -> dict[str, Had]:
    """Every plugin keel has, by name: an installed version wins over the image's; "on": false turns it off."""
    image = image_versions()
    entries = installed_entries()
    out: dict[str, Had] = {}
    for name in sorted(set(image) | set(entries)):
        if not NAME.match(name):
            continue
        e = entries.get(name, {})
        on = e.get("on", True) is not False
        v = e.get("version")
        img = image[name].name if name in image else None
        if isinstance(v, str) and manifest.VERSION.match(v):
            out[name] = Had(name, v, str(e.get("source") or FILE), on, state.store_dir() / name / v, e, img)
        elif name in image:
            out[name] = Had(name, image[name].name, IMAGE, on, image[name], e, img)
    return out


def loaded() -> dict[str, str] | None:
    """name → version of the plugins keel's last start loaded (run/resolved.json); None when keel never resolved."""
    f = state.run_dir() / "resolved.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return {str(p["name"]): str(p["version"]) for p in data.get("plugins") or []}
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
        return None
