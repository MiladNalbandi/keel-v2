"""keel-plugin index: the catalog index of format 1 (13-step4-contract.md section 3), built from a marketplace folder
and the releases, signed with the catalog key, and the checks that leave a version out."""

from __future__ import annotations

import datetime as dt
import functools
import http.server
import json
import shutil
import threading
from pathlib import Path

import pytest
from conftest import REPO, set_manifest, write

from keel_plugin import catalog, minisign, template
from keel_plugin.manifest import file_sha256
from keel_plugin.package import pack

MARKET = REPO / "docs/plugins/marketplace-repo"


class Keys:
    def __init__(self):
        self.catalog = minisign.SecretKey.generate()
        self.keel = minisign.SecretKey.generate()
        self.other = minisign.SecretKey.generate()


@pytest.fixture
def keys() -> Keys:
    return Keys()


def marketplace(root: Path, keys: Keys) -> Path:
    write(root / "publishers/keel.yml", "\n".join([
        "name: keel", "title: keel", "verified: true", "contact: https://example.com",
        f"keys: [{keys.keel.public().line()}]"]) + "\n")
    write(root / "publishers/ana.yml", f"name: ana\ntitle: Ana K\nverified: false\nkeys:\n  - {keys.other.public().line()}\n")
    write(root / "plugins/hello.yml", "\n".join([
        "name: hello", 'title: "Hello"', "publisher: keel", "repo: https://github.com/keel-studio/keel-plugin-hello",
        "category: other", 'summary: "Says hello."', "tags: [demo, hello]", "trust: content"]) + "\n")
    write(root / "plugins/notes.yml", "\n".join([
        "name: notes", "title: Notes", "publisher: ana", "repo: https://github.com/ana-k/keel-plugin-notes",
        "category: knowledge", "summary: Notes for a project.", "trust: content"]) + "\n")
    write(root / "revoked.yml", 'revoked:\n  - { name: hello, version: "1.0.0", why: "sent errors to a wrong host" }\n')
    return root


def release(tmp: Path, out: Path, version: str, key: minisign.SecretKey | None, name: str = "hello",
            sidecar: dict | None = None, perms: str = "{ workspace: read }") -> Path:
    """A signed hello-<version>.kplug in out/<name>/v<version>/ (key None: no signature)."""
    src = template.new(name, tmp / f"src-{name}-{version}", publisher="keel")
    set_manifest(src, "version: 0.1.0", f'version: "{version}"')
    set_manifest(src, "permissions: {}", f"permissions: {perms}")
    kplug, _, _ = pack(src, out / name / f"v{version}")
    if key is not None:
        kplug.with_name(kplug.name + ".minisig").write_text(minisign.sign(kplug.read_bytes(), key, kplug.name))
    if sidecar is not None:
        kplug.with_name(kplug.name + ".json").write_text(json.dumps(sidecar))
    return kplug


@pytest.fixture
def built(tmp_path, keys, run, monkeypatch):
    """A marketplace with good and bad releases, indexed by the CLI: (marketplace, out folder, stdout, exit code)."""
    root = marketplace(tmp_path / "market", keys)
    rel = tmp_path / "releases"
    release(tmp_path, rel, "1.0.0", keys.keel, sidecar={
        "url": "https://github.com/keel-studio/keel-plugin-hello/releases/download/v1.0.0/hello-1.0.0.kplug",
        "released": "2026-10-01T10:00:00Z"})
    release(tmp_path, rel, "1.1.0", keys.keel, perms="{ workspace: read, secrets: [database] }")
    release(tmp_path, rel, "1.2.0", keys.other)                       # not the publisher's key
    bad = release(tmp_path, rel, "1.3.0", keys.keel)                  # changed after signing
    bad.write_bytes(bad.read_bytes() + b"\0")
    release(tmp_path, rel, "1.4.0", None)                             # no signature
    release(tmp_path, rel, "1.0.0", keys.keel, name="other")          # not in plugins/
    release(tmp_path, rel, "0.10.0", keys.keel)                       # sorts below 1.0.0
    monkeypatch.setenv("PB_CATALOG_KEY", keys.catalog.text())
    code, out, err = run("index", root, "--releases", rel, "--key-env", "PB_CATALOG_KEY", "--out", tmp_path / "site/v1")
    assert keys.catalog.text() not in out + err
    return root, tmp_path / "site/v1", out, code


def test_the_index_has_format_1_and_is_signed_by_the_catalog_key(built, keys, run):
    _root, out_dir, out, code = built
    assert code == 0
    index_file = out_dir / "index.json"
    sig = (out_dir / "index.json.minisig").read_text()
    minisign.verify(index_file.read_bytes(), sig, [keys.catalog.public()])
    assert "\tfile:index.json" in sig.splitlines()[2]
    with pytest.raises(minisign.SignatureError):
        minisign.verify(index_file.read_bytes(), sig, [keys.keel.public()])
    code, _, _ = run("verify", index_file, "--pub", keys.catalog.public().line())
    assert code == 0
    index = json.loads(index_file.read_text())
    assert list(index) == ["format", "built", "expires", "publishers", "plugins", "revoked"]
    assert index["format"] == 1
    built_at = dt.datetime.strptime(index["built"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
    assert dt.datetime.strptime(index["expires"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC) - built_at == dt.timedelta(days=14)
    assert index["publishers"] == {
        "ana": {"title": "Ana K", "keys": [keys.other.public().line()], "verified": False},
        "keel": {"title": "keel", "keys": [keys.keel.public().line()], "verified": True},
    }
    assert index["revoked"] == [{"name": "hello", "version": "1.0.0", "why": "sent errors to a wrong host"}]
    assert "index: 1 plugins, 3 versions, 4 left out" in out


def test_each_version_has_its_file_facts(built, tmp_path):
    _root, out_dir, _out, _code = built
    (hello,) = json.loads((out_dir / "index.json").read_text())["plugins"]
    assert [k for k in hello] == ["name", "title", "publisher", "category", "summary", "tags", "repo", "trust",
                                  "versions"]
    assert hello["name"] == "hello" and hello["title"] == "Hello" and hello["tags"] == ["demo", "hello"]
    assert hello["trust"] == "code"     # the yml said content, the package has an engine part
    assert [v["version"] for v in hello["versions"]] == ["1.1.0", "1.0.0", "0.10.0"]
    v110, v100, _ = hello["versions"]
    kplug = tmp_path / "releases/hello/v1.1.0/hello-1.1.0.kplug"
    assert v110 == {
        "version": "1.1.0",
        "released": v110["released"],
        "requires": {"sdk": 1},
        "url": "https://github.com/keel-studio/keel-plugin-hello/releases/download/v1.1.0/hello-1.1.0.kplug",
        "sha256": file_sha256(kplug),
        "size": kplug.stat().st_size,
        "permissions": {"workspace": "read", "secrets": ["database"]},
    }
    assert len(v110["sha256"]) == 64
    assert v100["released"] == "2026-10-01"
    assert v100["url"].endswith("/releases/download/v1.0.0/hello-1.0.0.kplug")


def test_versions_that_fail_a_check_are_left_out_and_named(built):
    _root, _out_dir, out, _code = built
    assert "left out hello-1.2.0.kplug left out: the file was signed with key" in out
    assert "left out hello-1.3.0.kplug left out: " in out and "does not match" in out
    assert "left out hello-1.4.0.kplug left out: there is no signature hello-1.4.0.kplug.minisig" in out
    assert "left out other-1.0.0.kplug left out: there is no plugins/other.yml" in out
    assert "note     hello: trust raised from content to code" in out
    assert "note     notes: no release yet, so it is not in the index" in out


def test_strict_fails_when_a_version_is_left_out(tmp_path, keys, run, monkeypatch):
    root = marketplace(tmp_path / "m", keys)
    release(tmp_path, tmp_path / "r", "1.0.0", keys.keel)
    monkeypatch.setenv("PB_CATALOG_KEY", keys.catalog.text())
    args = ("index", root, "--releases", tmp_path / "r", "--key-env", "PB_CATALOG_KEY", "--strict")
    assert run(*args)[0] == 0
    assert (root / "v1/index.json").is_file() and (root / "v1/index.json.minisig").is_file()
    release(tmp_path, tmp_path / "r", "1.1.0", keys.other)
    code, out, _ = run(*args)
    assert code == 1 and "1 left out" in out


def test_only_and_no_sign(tmp_path, keys, run, monkeypatch):
    root = marketplace(tmp_path / "m", keys)
    release(tmp_path, tmp_path / "r", "1.0.0", keys.keel)
    monkeypatch.setenv("PB_CATALOG_KEY", keys.catalog.text())
    assert run("index", root, "--releases", tmp_path / "r", "--key-env", "PB_CATALOG_KEY", "--out", tmp_path / "o")[0] == 0
    assert (tmp_path / "o/index.json.minisig").exists()
    code, _, _ = run("index", root, "--releases", tmp_path / "r", "--no-sign", "--only", "notes", "--out", tmp_path / "o")
    assert code == 0 and not (tmp_path / "o/index.json.minisig").exists()   # no old signature next to a new index
    assert json.loads((tmp_path / "o/index.json").read_text())["plugins"] == []


def test_the_key_is_needed_before_any_work(tmp_path, keys, run, monkeypatch):
    root = marketplace(tmp_path / "m", keys)
    monkeypatch.delenv("PB_NO_KEY", raising=False)
    code, _, err = run("index", root, "--releases", tmp_path / "nothing", "--key-env", "PB_NO_KEY")
    assert code == 1 and "PB_NO_KEY is not set" in err
    assert not (root / "v1").exists()


# ---------------------------------------------------------------- the url map (downloads)

@pytest.fixture
def server(tmp_path):
    """A local HTTP server for a folder: (base URL, folder)."""
    folder = tmp_path / "served"
    folder.mkdir()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", folder
    httpd.shutdown()


def test_a_url_map_is_downloaded_and_checked(tmp_path, keys, run, monkeypatch, server):
    base, folder = server
    root = marketplace(tmp_path / "m", keys)
    kplug = release(tmp_path, tmp_path / "r", "1.0.0", keys.keel)
    shutil.copy(kplug, folder)
    shutil.copy(kplug.with_name(kplug.name + ".minisig"), folder)
    urlmap = write(tmp_path / "releases.json", json.dumps([
        {"url": f"{base}/hello-1.0.0.kplug", "released": "2026-10-09"},
        {"url": f"{base}/missing-1.0.0.kplug"}]))
    monkeypatch.setenv("PB_CATALOG_KEY", keys.catalog.text())
    args = ("index", root, "--releases", urlmap, "--key-env", "PB_CATALOG_KEY", "--out", tmp_path / "o")
    # http is only for tests: KEEL_MARKETPLACE_ALLOW_HTTP=1 and a local host (section 2)
    monkeypatch.delenv("KEEL_MARKETPLACE_ALLOW_HTTP", raising=False)
    code, out, _ = run(*args)
    assert code == 0 and "must be an https:// URL" in out
    assert json.loads((tmp_path / "o/index.json").read_text())["plugins"] == []
    monkeypatch.setenv("KEEL_MARKETPLACE_ALLOW_HTTP", "1")
    code, out, _ = run(*args)
    assert code == 0, out
    assert "missing-1.0.0.kplug left out: it cannot be downloaded" in out
    (hello,) = json.loads((tmp_path / "o/index.json").read_text())["plugins"]
    assert hello["versions"][0]["url"] == f"{base}/hello-1.0.0.kplug"
    assert hello["versions"][0]["sha256"] == file_sha256(kplug)
    assert hello["versions"][0]["released"] == "2026-10-09"


def test_urls_follow_section_2():
    assert catalog.url_problem("https://github.com/x/y/releases/download/v1/a.kplug") is None
    assert catalog.url_problem("http://github.com/a.kplug", allow_http=True)
    assert catalog.url_problem("http://127.0.0.1:8000/a.kplug") is not None
    assert catalog.url_problem("http://127.0.0.1:8000/a.kplug", allow_http=True) is None
    assert catalog.url_problem("http://host.docker.internal/a.kplug", allow_http=True) is None
    assert catalog.url_problem("file:///etc/passwd", allow_http=True)


# ---------------------------------------------------------------- the marketplace files

def test_check_names_broken_marketplace_files(tmp_path, keys, run):
    root = marketplace(tmp_path / "m", keys)
    assert run("index", root, "--check")[0] == 0
    write(root / "publishers/keel.yml", "name: keel\ntitle: keel\nverified: true\nkeys: []\n")
    write(root / "plugins/bad.yml", "name: bad\ntitle: Bad\npublisher: nobody\nrepo: http://example.com\n"
                                    "category: games\nsummary: x\ntrust: root\n")
    write(root / "revoked.yml", "revoked:\n  - { name: hello, version: 1.0 }\n")
    code, out, _ = run("index", root, "--check")
    assert code == 1
    for why in ["keys must list at least one minisign public key", "the publisher 'nobody' has no file",
                "repo must be a https://github.com/<owner>/<repo> URL", "category must be one of",
                "trust must be one of content, web, code", "revoked[0] must have a plugin name, a version"]:
        assert why in out, why
    code, _, err = run("index", root, "--releases", tmp_path, "--no-sign")
    assert code == 1 and "the marketplace files have errors" in err


def test_version_order():
    versions = ["1.0.0", "0.10.0", "1.0.0-beta.2", "1.0.0-beta.10", "0.9.1", "1.0.0-alpha"]
    assert sorted(versions, key=catalog.version_key) == [
        "0.9.1", "0.10.0", "1.0.0-alpha", "1.0.0-beta.2", "1.0.0-beta.10", "1.0.0"]


def test_the_marketplace_repo_lists_the_eleven_plugins_and_product(tmp_path, keys, run):
    """docs/plugins/marketplace-repo, with the key setup-signing-keys.sh would write."""
    copy = tmp_path / "market"
    shutil.copytree(MARKET, copy)
    code, out, _ = run("index", copy, "--check")
    assert code == 1 and "publishers/keel.yml: keys must list at least one" in out   # until the keys are made
    pub = copy / "publishers/keel.yml"
    pub.write_text(pub.read_text().replace("keys: []", f"keys: [{keys.keel.public().line()}]"))
    code, out, _ = run("index", copy, "--check")
    assert code == 0, out
    assert "warning" not in out
    assert "marketplace: 1 publishers, 12 plugins, 0 revoked, 0 errors" in out
    cat = catalog.read_catalog(copy)
    assert sorted(cat.plugins) == ["ci", "code", "db", "git", "graph", "jira", "keelbot", "map", "product", "review",
                                   "tasks", "wiki"]
    for name, p in cat.plugins.items():
        assert p["repo"] == f"https://github.com/keel-studio/keel-plugin-{name}"
        assert p["publisher"] == "keel"
        assert p["trust"] == "code"     # every one has an engine or an api part
        manifest = (REPO / ("product" if name == "product" else f"plugins/{name}") / "keel-plugin.yml").read_text()
        assert f"title: {p['title']}\n" in manifest


def test_index_of_the_marketplace_repo_with_a_release(tmp_path, keys, run, monkeypatch):
    copy = tmp_path / "market"
    shutil.copytree(MARKET, copy)
    pub = copy / "publishers/keel.yml"
    pub.write_text(pub.read_text().replace("keys: []", f"keys: [{keys.keel.public().line()}]"))
    release(tmp_path, tmp_path / "r", "1.0.0", keys.keel, name="map")
    monkeypatch.setenv("PB_CATALOG_KEY", keys.catalog.text())
    code, out, _ = run("index", copy, "--releases", tmp_path / "r", "--key-env", "PB_CATALOG_KEY")
    assert code == 0, out
    index = json.loads((copy / "v1/index.json").read_text())
    (m,) = index["plugins"]
    assert m["name"] == "map" and m["category"] == "knowledge"
    assert m["versions"][0]["url"] == \
        "https://github.com/keel-studio/keel-plugin-map/releases/download/v1.0.0/map-1.0.0.kplug"
    assert "note     db: no release yet" in out
