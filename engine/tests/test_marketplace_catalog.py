"""The catalogs (keel_engine/marketplace/catalog.py and sources.py, docs/plugins/13-step4-contract.md §2–§4): a test
catalog on a local HTTP server, signed with a key made in the test. Read, verify, keep the last good copy, refuse a bad
signature, an old index and broken entries, search."""

import json

import pytest

from keel_engine.marketplace import MarketError, catalog, sources
from keel_engine.pluginhost import state
from marketplace_kit import market, server  # noqa: F401 - fixtures
from test_pluginhost import data, image  # noqa: F401 - fixtures


def test_a_source_is_read_verified_and_kept_on_disk(market):
    market.publish("hello", "1.0.0", permissions={"workspace": "read"})
    [official, test] = market.ready()
    assert official["id"] == "keel" and official["on"] is False
    assert test == {**test, "id": "test", "ok": True, "problem": None, "old": False, "plugins": 1, "problems": []}
    folder = state.plugins_dir() / "catalog"
    assert json.loads((folder / "test.json").read_text())["plugins"][0]["name"] == "hello"
    assert (folder / "test.json.minisig").read_text().startswith("untrusted comment: ")
    meta = json.loads((folder / "test.meta.json").read_text())
    assert meta["ok"] is True and meta["problem"] is None and meta["fetched_at"] == meta["tried_at"]
    [hit] = catalog.search("hello")["plugins"]
    assert hit == {**hit, "name": "hello", "title": "Hello", "publisher": "keel", "verified": True, "trust": "web",
                   "version": "1.0.0", "latest": "1.0.0", "fits": True, "why_not": None, "installed": None,
                   "update": None, "revoked": None, "old": False, "source": "test",
                   "permissions": {"workspace": "read"}}


def test_the_official_source_without_a_key_is_not_set_up_yet(market, tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "trust_dir", lambda: tmp_path / "no-trust")
    [official] = catalog.refresh()
    assert official["id"] == "keel" and official["url"] == sources.OFFICIAL_URL and official["official"]
    assert official["ok"] is False and official["problem"] == sources.NOT_SET_UP
    assert market.server.seen == []                                     # nothing was fetched
    assert catalog.search("")["plugins"] == []


def test_the_official_source_uses_the_key_in_the_image(tmp_path, monkeypatch, market):
    trust = tmp_path / "trust"
    trust.mkdir()
    (trust / "catalog.pub").write_text(f"untrusted comment: keel catalog\n{market.catalog_key.public().line()}\n")
    monkeypatch.setattr(sources, "trust_dir", lambda: trust)
    first = sources.read_sources()[0]
    assert first["key"] == market.catalog_key.public().line() and first["on"]
    with pytest.raises(MarketError, match="comes with keel's image"):
        sources.write_sources([{"id": "keel", "key": signing_key()}])
    assert sources.write_sources([{"id": "keel", "on": False}])[0]["on"] is False


def signing_key() -> str:
    from keel_engine.marketplace import signing

    return signing.keygen().public().line()


def test_a_bad_signature_keeps_the_last_good_copy(market):
    market.publish("hello", "1.0.0")
    market.ready()
    market.publish("other", "1.0.0")
    market.built = "2026-10-10T06:00:00Z"
    market.serve(sign_with=market.publisher_key)              # signed, but not with the catalog's key
    test = catalog.refresh()[1]
    assert test["ok"] is False and "signature does not match the source's key" in test["problem"]
    assert [h["name"] for h in catalog.search()["plugins"]] == ["hello"]   # the last good copy still searches
    market.serve()
    assert catalog.refresh()[1]["ok"] is True
    assert [h["name"] for h in catalog.search()["plugins"]] == ["hello", "other"]


def test_a_changed_copy_on_disk_is_not_used(market):
    market.publish("hello", "1.0.0")
    market.ready()
    f = state.plugins_dir() / "catalog" / "test.json"
    f.write_text(f.read_text().replace("hello plugin", "evil plugin"))
    catalog._cache.clear()
    assert catalog.search()["plugins"] == []
    assert catalog.statuses()[1]["ok"] is False


def test_an_index_older_than_the_copy_is_refused(market):
    market.publish("hello", "1.0.0")
    market.ready()
    market.built = "2026-10-01T06:00:00Z"
    market.serve()
    test = catalog.refresh()[1]
    assert test["ok"] is False and "before the copy keel has" in test["problem"]


@pytest.mark.parametrize("change, why", [
    ({"format": 2}, "this keel reads format 1"),
    ({"expires": None}, "no valid built and expires dates"),
])
def test_an_index_keel_cannot_read_is_refused(market, change, why):
    market.publish("hello", "1.0.0")
    market.add_source()
    market.serve({**market.index(), **change})
    test = catalog.refresh()[1]
    assert test["ok"] is False and why in test["problem"]
    market.serve(raw=b"not json")
    assert "not valid JSON" in catalog.refresh()[1]["problem"]


def test_an_expired_index_still_searches_and_says_it_is_old(market):
    market.publish("hello", "1.0.0")
    market.expires = "2020-01-01T00:00:00Z"
    test = market.ready()[1]
    assert test["ok"] is True and test["old"] is True
    [hit] = catalog.search()["plugins"]
    assert hit["old"] is True


def test_revoked_versions_are_never_offered(market):
    market.publish("hello", "1.0.0")
    market.publish("hello", "1.1.0")
    market.revoked.append({"name": "hello", "version": "1.1.0", "why": "sent errors to a wrong host"})
    market.ready()
    [hit] = catalog.search()["plugins"]
    assert (hit["latest"], hit["version"]) == ("1.1.0", "1.0.0")


def test_a_broken_entry_is_left_out_and_the_rest_is_used(market):
    market.publish("hello", "1.0.0")
    doc = market.index()
    good = doc["plugins"][0]
    bad_version = {**good["versions"][0], "version": "1.0"}
    doc["plugins"] = [
        {**good, "versions": [*good["versions"], bad_version, {**good["versions"][0], "version": "2.0.0", "sha256": "xyz"},
                              {**good["versions"][0], "version": "3.0.0", "url": "http://example.org/x.kplug"}]},
        {**good, "name": "Bad_Name"},
        {**good, "name": "nobody", "publisher": "ghost"},
        {**good, "name": "weird", "trust": "root"},
        {**good, "name": "empty", "versions": []},
        {**good, "name": "hello"},
    ]
    market.add_source()
    market.serve(doc)
    test = catalog.refresh()[1]
    assert test["ok"] is True and test["plugins"] == 1
    problems = "\n".join(test["problems"])
    for why in ("version '1.0' is not a version like 1.0.0 or 0.1.0-beta.1", "hello 2.0.0: sha256 is not 64 hex digits", "hello 3.0.0: 'http://example.org",
                "'Bad_Name' does not follow keel's name rule", "its publisher 'ghost' is not listed",
                "trust 'root' is not content, web or code", "empty: it has no valid version", "hello is listed twice"):
        assert why in problems, why
    [hit] = catalog.search()["plugins"]
    assert hit["name"] == "hello" and hit["latest"] == "1.0.0"


def test_pre_releases_are_versions_and_sort_before_their_release(market):
    for v in ("0.1.0-beta.1", "1.0.0-beta.10", "1.0.0", "1.0.0-beta.2"):
        market.publish("product", v, category="product")
    market.ready()
    [hit] = catalog.search("product", "product")["plugins"]
    assert (hit["latest"], hit["version"]) == ("1.0.0", "1.0.0")
    _index, entry = catalog.lookup("product")
    assert [v.version for v in entry.versions] == ["1.0.0", "1.0.0-beta.10", "1.0.0-beta.2", "0.1.0-beta.1"]
    assert catalog.newer("1.0.0", "1.0.0-beta.1") and catalog.newer("1.0.0-beta.2", "1.0.0-alpha")
    assert catalog.newer("1.0.0-beta.10", "1.0.0-beta.2") and not catalog.newer("1.0.0+build.5", "1.0.0")


def test_search_matches_every_word_in_name_title_summary_and_tags(market):
    market.publish("db", "1.0.0", title="Database", category="code")
    market.publish("wiki", "1.0.0", title="Wiki", category="knowledge")
    market.publish("dbx", "1.0.0", title="A database tool", category="code")
    market.ready()
    names = lambda q="", c=None: [h["name"] for h in catalog.search(q, c)["plugins"]]
    assert names() == ["dbx", "db", "wiki"]                  # by title
    assert names("db") == ["db", "dbx"]                      # the exact name first
    assert names("DATABASE") == ["dbx", "db"]
    assert names("wiki test") == ["wiki"]                    # a tag
    assert names("wiki nothing") == []
    assert names(c="knowledge") == ["wiki"]
    assert catalog.search()["categories"] == list(catalog.CATEGORIES)


def test_a_version_that_does_not_fit_says_why(market):
    market.publish("future", "1.0.0", sdk=2)
    market.publish("newer", "1.0.0", keel=">=9.0.0")
    market.publish("needy", "1.0.0", needs={"ghost": ">=1.0.0"})
    market.ready()
    hits = {h["name"]: h for h in catalog.search()["plugins"]}
    assert hits["future"]["fits"] is False and hits["future"]["why_not"] == "needs plugin SDK 2, this keel has 1"
    assert hits["newer"]["why_not"].startswith("needs keel >=9.0.0, this is keel ")
    assert hits["needy"]["why_not"] == "needs ghost >=1.0.0, which is neither installed nor in this catalog"


def test_a_source_that_is_off_or_down_is_shown_not_used(market):
    market.publish("hello", "1.0.0")
    market.ready()
    sources.write_sources([{"id": "keel", "on": False}, {**market.source(), "on": False}])
    assert catalog.search()["plugins"] == [] and catalog.statuses()[1]["on"] is False
    sources.write_sources([{"id": "keel", "on": False}, {**market.source(), "url": market.url + "/gone/index.json"}])
    test = catalog.refresh()[1]
    assert test["ok"] is False and "answered 404" in test["problem"]


# ------------------------------------------------------------------ sources, rules, addresses

def test_sources_are_checked_before_they_are_kept(market):
    with pytest.raises(MarketError, match="not a minisign public key"):
        sources.write_sources([{**market.source(), "key": "nope"}])
    with pytest.raises(MarketError, match="source id 'Bad' is not valid"):
        sources.write_sources([{**market.source(), "id": "Bad"}])
    with pytest.raises(MarketError, match="used twice"):
        sources.write_sources([market.source(), market.source()])
    got = sources.write_sources([market.source()])
    assert [s["id"] for s in got] == ["keel", "test"] and got[1]["title"] == "Test catalog"
    (state.plugins_dir() / "sources.json").write_text("{broken")
    assert [s["id"] for s in sources.read_sources()] == ["keel"]          # a broken file: only the official one


def test_rules_have_defaults_and_take_only_the_four(market):
    assert sources.read_rules() == {"agents_may_ask": True, "allow_unverified": False, "check_daily": True,
                                    "restart_when_idle": False}
    assert sources.write_rules({"agents_may_ask": False})["agents_may_ask"] is False
    assert sources.read_rules()["agents_may_ask"] is False
    with pytest.raises(MarketError, match="Unknown rule: install_alone"):
        sources.write_rules({"install_alone": True})
    with pytest.raises(MarketError, match="must be true or false"):
        sources.write_rules({"check_daily": "yes"})


def test_http_is_only_for_this_computer_in_tests(monkeypatch):
    monkeypatch.delenv("KEEL_MARKETPLACE_ALLOW_HTTP", raising=False)
    assert sources.url_problem("https://keel-studio.github.io/x") is None
    assert "only over https" in sources.url_problem("http://127.0.0.1:9/x")
    monkeypatch.setenv("KEEL_MARKETPLACE_ALLOW_HTTP", "1")
    for ok in ("http://127.0.0.1:9/x", "http://localhost/x", "http://host.docker.internal:8/x"):
        assert sources.url_problem(ok) is None, ok
    assert "only over https" in sources.url_problem("http://example.org/x")
    assert "user name or a password" in sources.url_problem("https://me:pw@example.org/x")
    assert "not an https address" in sources.url_problem("ftp://example.org/x")
    assert sources.url_problem("") == "the address is empty"


def test_downloads_stop_above_the_limit_and_refuse_a_redirect_to_http(market):
    market.server.files["/big"] = b"x" * 5000
    with pytest.raises(MarketError, match="more than the 100 bytes"):
        catalog.download(market.url + "/big", limit=100)
    assert catalog.download(market.url + "/big", limit=5000) == b"x" * 5000
    market.server.no_length.add("/big")                     # no size given: keel counts and stops
    with pytest.raises(MarketError, match="bigger than the 100 bytes keel allows here, so keel stopped"):
        catalog.download(market.url + "/big", limit=100)
    market.server.redirects["/moved"] = "http://example.org/evil"
    with pytest.raises(MarketError, match="sent on to an address keel refuses"):
        catalog.download(market.url + "/moved", limit=100)
    market.server.redirects["/local"] = market.url + "/big"
    assert len(catalog.download(market.url + "/local", limit=5000)) == 5000
    with pytest.raises(MarketError, match="cannot reach 127.0.0.1:1"):
        catalog.download("http://127.0.0.1:1/x", limit=10, timeout=2)
