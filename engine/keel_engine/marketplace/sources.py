"""The catalogs keel reads and the person's rules (docs/plugins/13-step4-contract.md §2 and §4).

    $KEEL_DATA/plugins/sources.json   {"sources": [{id, title, url, key, on}]}: the official one is always there
    $KEEL_DATA/plugins/rules.json     {agents_may_ask, allow_unverified, check_daily, restart_when_idle}
    <content>/trust/catalog.pub       the official catalog's public key (only public keys are in the image)
    <content>/trust/keel.pub          the keel publisher's public key

Until the real keys exist the official source has no key: it shows "not set up yet" and nothing breaks. A person may
add more sources (an https URL and a public key) and turn any source off; the official one's address and key come
with keel's image and cannot be changed.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from .. import config
from ..addons import NAME
from ..pluginhost import state
from . import MarketError
from .signing import PublicKey, SignatureError

log = logging.getLogger("keel.marketplace")

OFFICIAL_ID = "keel"
OFFICIAL_TITLE = "keel marketplace"
OFFICIAL_URL = "https://keel-studio.github.io/keel-marketplace/v1/index.json"
NOT_SET_UP = "not set up yet: this keel has no key for the official catalog"
RULES = {"agents_may_ask": True, "allow_unverified": False, "check_daily": True, "restart_when_idle": False}
HTTP_HOSTS = ("127.0.0.1", "localhost", "host.docker.internal")
ALLOW_HTTP = "KEEL_MARKETPLACE_ALLOW_HTTP"


# ------------------------------------------------------------------ addresses

def url_problem(url) -> str | None:
    """None when keel may read this address: https, or http to this computer when KEEL_MARKETPLACE_ALLOW_HTTP=1
    (tests only); else why not, in plain words."""
    if not isinstance(url, str) or not url.strip():
        return "the address is empty"
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        _ = parts.port
    except ValueError:
        return f"'{url}' is not an address"
    if parts.username or parts.password:
        return "the address must not hold a user name or a password"
    if not host:
        return f"'{url}' has no host"
    if parts.scheme == "https":
        return None
    if parts.scheme == "http" and host in HTTP_HOSTS and os.environ.get(ALLOW_HTTP) == "1":
        return None
    if parts.scheme == "http":
        return f"'{url}' is http: keel downloads plugins and catalogs only over https"
    return f"'{url}' is not an https address"


def check_url(url, what: str) -> str:
    why = url_problem(url)
    if why:
        raise MarketError(400, f"The {what} address is refused: {why}.", "Use an https:// address.")
    return url.strip()


# ------------------------------------------------------------------ the keys in the image

def trust_dir() -> Path:
    return config.content_dir() / "trust"


def _image_key(file: str) -> PublicKey | None:
    f = trust_dir() / file
    if not f.is_file():
        return None
    try:
        return PublicKey.parse(f.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SignatureError) as exc:
        log.warning("marketplace: %s is not a public key (%s), so it is not used", f, exc)
        return None


def official_key() -> PublicKey | None:
    """The official catalog's key (content/trust/catalog.pub), or None before Milad made the real keys."""
    return _image_key("catalog.pub")


def keel_publisher_key() -> PublicKey | None:
    """The keel publisher's key (content/trust/keel.pub): trusted for the publisher keel of the official catalog."""
    return _image_key("keel.pub")


# ------------------------------------------------------------------ sources.json

def sources_path() -> Path:
    return state.plugins_dir() / "sources.json"


def rules_path() -> Path:
    return state.plugins_dir() / "rules.json"


def _read_json(path: Path) -> dict:
    """A JSON object file; {} when there is none or it does not read (a broken file is logged, the defaults are used)."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        log.warning("marketplace: %s does not read (%s), so keel uses the defaults", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def official() -> dict:
    key = official_key()
    return {"id": OFFICIAL_ID, "title": OFFICIAL_TITLE, "url": OFFICIAL_URL, "key": key.line() if key else None,
            "on": True, "official": True}


def _added(item) -> dict | None:
    """One source a person added, as sources.json keeps it; None when it is broken (then it is not used)."""
    if not isinstance(item, dict) or item.get("id") == OFFICIAL_ID:
        return None
    try:
        return _checked(item)
    except MarketError as exc:
        log.warning("marketplace: source %r in sources.json is not used: %s", item.get("id"), exc)
        return None


def _checked(item: dict) -> dict:
    sid = item.get("id")
    if not isinstance(sid, str) or not NAME.match(sid):
        raise MarketError(400, f"The source id {sid!r} is not valid.", "Use a-z, 0-9 and '-', starting with a letter.")
    url = check_url(item.get("url"), f"source {sid}'s")
    try:
        key = PublicKey.parse(str(item.get("key") or ""))
    except SignatureError as exc:
        raise MarketError(400, f"The key of source {sid} is not a minisign public key: {exc}.",
                          "Paste the second line of the catalog's .pub file.") from exc
    title = item.get("title")
    on = item.get("on", True)
    if not isinstance(on, bool):
        raise MarketError(400, f"Source {sid}: 'on' must be true or false.")
    return {"id": sid, "title": title.strip() if isinstance(title, str) and title.strip() else sid, "url": url,
            "key": key.line(), "on": on, "official": False}


def read_sources() -> list[dict]:
    """Every source: the official one first, then the ones a person added. Each: {id, title, url, key, on, official}."""
    data = _read_json(sources_path())
    items = data.get("sources") if isinstance(data.get("sources"), list) else []
    first = official()
    stored = next((s for s in items if isinstance(s, dict) and s.get("id") == OFFICIAL_ID), {})
    if isinstance(stored.get("on"), bool):
        first["on"] = stored["on"]
    out, seen = [first], {OFFICIAL_ID}
    for item in items:
        s = _added(item)
        if s and s["id"] not in seen:
            out.append(s)
            seen.add(s["id"])
    return out


def source(sid: str) -> dict | None:
    return next((s for s in read_sources() if s["id"] == sid), None)


def write_sources(items) -> list[dict]:
    """Replace the sources a person added (and the official one's on/off) with this list; returns read_sources()."""
    if not isinstance(items, list):
        raise MarketError(400, "sources must be a list of {id, title, url, key, on}.")
    first = official()
    keep_on = True
    added: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            raise MarketError(400, "Each source is an object {id, title, url, key, on}.")
        if item.get("id") == OFFICIAL_ID:
            for k in ("url", "key"):
                if item.get(k) not in (None, first[k]):
                    raise MarketError(400, f"The official source's {k} comes with keel's image and cannot be changed.",
                                      "Add another source with its own id instead.")
            if not isinstance(item.get("on", True), bool):
                raise MarketError(400, "The official source: 'on' must be true or false.")
            keep_on = item.get("on", True)
            continue
        s = _checked(item)
        if any(a["id"] == s["id"] for a in added):
            raise MarketError(400, f"The source id {s['id']} is used twice.")
        added.append(s)
    stored = [{"id": OFFICIAL_ID, "on": keep_on}] + [{k: s[k] for k in ("id", "title", "url", "key", "on")} for s in added]
    state.write_atomic(sources_path(), json.dumps({"sources": stored}, indent=2) + "\n")
    return read_sources()


# ------------------------------------------------------------------ rules.json

def read_rules() -> dict:
    data = _read_json(rules_path())
    return {k: data[k] if isinstance(data.get(k), bool) else default for k, default in RULES.items()}


def write_rules(changes) -> dict:
    """Change some of the four rules (true or false each); returns all four."""
    if not isinstance(changes, dict):
        raise MarketError(400, "The rules are an object of true or false values.")
    unknown = sorted(set(changes) - set(RULES))
    if unknown:
        raise MarketError(400, f"Unknown rule: {', '.join(unknown)}.", f"The rules are {', '.join(RULES)}.")
    bad = sorted(k for k, v in changes.items() if not isinstance(v, bool))
    if bad:
        raise MarketError(400, f"The rule {', '.join(bad)} must be true or false.")
    rules = {**read_rules(), **changes}
    state.write_atomic(rules_path(), json.dumps(rules, indent=2) + "\n")
    return rules
