"""A test marketplace for the engine's marketplace tests: test keys made in the test, plugin packages built with the
plugin host's test helpers, and a signed catalog served by a local HTTP server on 127.0.0.1
(KEEL_MARKETPLACE_ALLOW_HTTP=1, as docs/plugins/13-step4-contract.md §2 allows for tests only)."""

from __future__ import annotations

import hashlib
import http.server
import json
import shutil
import threading
from pathlib import Path

import pytest
import yaml

from keel_engine.marketplace import catalog, signing, sources
from keel_engine.pluginhost import SDK
from test_pluginhost import write_sums
from test_pluginhost_install import kplug


class _Files(http.server.BaseHTTPRequestHandler):
    """GET from the server's `files` {path: bytes}; `redirects` {path: location} answer 302."""

    def do_GET(self):  # noqa: N802 - http.server's name
        srv = self.server
        path = self.path.split("?", 1)[0]
        srv.seen.append(path)
        if path in srv.redirects:
            self.send_response(302)
            self.send_header("Location", srv.redirects[path])
            self.end_headers()
            return
        body = srv.files.get(path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        if path not in srv.no_length:              # without it the body ends when the connection closes
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class Market:
    """One test catalog: its key, the publisher keel (verified) with its key, the plugins and versions published."""

    def __init__(self, root: Path, server):
        self.root = root
        self.server = server
        self.catalog_key = signing.keygen()
        self.publisher_key = signing.keygen()
        self.publishers = {"keel": {"title": "keel", "keys": [self.publisher_key.public().line()], "verified": True}}
        self.plugins: dict[str, dict] = {}
        self.revoked: list[dict] = []
        self.built = "2026-10-09T06:00:00Z"
        self.expires = "2099-01-01T00:00:00Z"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def index_url(self) -> str:
        return self.url + "/v1/index.json"

    def package(self, name: str, version: str, *, permissions: dict | None = None, needs: dict | None = None,
                title: str | None = None, publisher: str = "keel", extra: dict | None = None,
                files: dict | None = None, engine: str | None = None) -> Path:
        """A .kplug of a small plugin with a web part (and an engine package when `engine` names one)."""
        d = self.root / "src" / f"{name}-{version}"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        parts: dict = {"web": {"entry": "web/index.js"}}
        all_files = {"web/index.js": f"export default {{ name: '{name}' }}\n", **(files or {})}
        if engine:
            parts["engine"] = {"path": "engine", "package": engine}
            all_files[f"engine/{engine}/__init__.py"] = f'ADDON = {{"name": "{name}", "version": "{version}"}}\n'
        for rel, text in all_files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text)
        doc = {"schema": 1, "name": name, "title": title or name.title(), "version": version, "publisher": publisher,
               "requires": {"sdk": SDK, **({"plugins": needs} if needs else {})}, "parts": parts}
        if permissions is not None:
            doc["permissions"] = permissions
        doc.update(extra or {})
        (d / "keel-plugin.yml").write_text(yaml.safe_dump(doc, sort_keys=False))
        write_sums(d)
        (self.root / "pkg").mkdir(parents=True, exist_ok=True)
        return kplug(self.root / "pkg" / f"{name}-{version}.kplug", d)

    def publish(self, name: str, version: str, *, trust: str = "web", publisher: str = "keel", category: str = "code",
                permissions: dict | None = None, needs: dict | None = None, title: str | None = None,
                sign_with=None, catalog_permissions: dict | None = None, size: int | None = None,
                sha256: str | None = None, sdk: int = SDK, keel: str | None = None, file: Path | None = None,
                **package) -> dict:
        """Build, sign and serve a package, and add its version to the index (call serve() after)."""
        file = file or self.package(name, version, permissions=permissions, needs=needs, title=title,
                                    publisher=publisher, **package)
        data = file.read_bytes()
        path = f"/pkg/{name}-{version}.kplug"
        self.server.files[path] = data
        self.server.files[path + ".minisig"] = signing.sign(data, sign_with or self.publisher_key, file.name).encode()
        requires: dict = {"sdk": sdk, "plugins": dict(needs or {})}
        if keel:
            requires["keel"] = keel
        v = {"version": version, "released": "2026-10-09", "requires": requires, "url": self.url + path,
             "sha256": sha256 or hashlib.sha256(data).hexdigest(), "size": size or len(data),
             "permissions": catalog_permissions if catalog_permissions is not None else dict(permissions or {})}
        entry = self.plugins.setdefault(name, {
            "name": name, "title": title or name.title(), "publisher": publisher, "category": category,
            "summary": f"The {name} plugin for tests.", "tags": [name, "test"],
            "repo": f"https://github.com/keel-studio/keel-plugin-{name}", "trust": trust, "versions": []})
        entry["versions"].append(v)
        return v

    def index(self) -> dict:
        return {"format": 1, "built": self.built, "expires": self.expires, "publishers": self.publishers,
                "plugins": list(self.plugins.values()), "revoked": list(self.revoked)}

    def serve(self, doc: dict | None = None, *, sign_with=None, raw: bytes | None = None) -> None:
        """Serve the index (this document, or the built one) and its .minisig."""
        raw = raw if raw is not None else json.dumps(doc if doc is not None else self.index()).encode()
        self.server.files["/v1/index.json"] = raw
        self.server.files["/v1/index.json.minisig"] = signing.sign(raw, sign_with or self.catalog_key, "index.json").encode()

    def source(self) -> dict:
        return {"id": "test", "title": "Test catalog", "url": self.index_url, "key": self.catalog_key.public().line(),
                "on": True}

    def add_source(self) -> None:
        """The test catalog as a source; the official one off (it has no key in a checkout, and tests never go out)."""
        sources.write_sources([{"id": "keel", "on": False}, self.source()])

    def ready(self) -> list[dict]:
        """Serve the index, add the source and read it."""
        self.serve()
        self.add_source()
        return catalog.refresh()


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Files)
    srv.files, srv.redirects, srv.seen, srv.no_length = {}, {}, [], set()
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def market(tmp_path, monkeypatch, image, server):
    """A test catalog on a local server, http allowed for 127.0.0.1; KEEL_DATA is tmp_path/data (conftest), the image's
    plugin root an empty tmp_path/image (test_pluginhost's image fixture)."""
    monkeypatch.setenv("KEEL_MARKETPLACE_ALLOW_HTTP", "1")
    catalog._cache.clear()
    return Market(tmp_path / "market", server)
