"""Install, update, roll back, remove, on and off, install from a file, and what waits for a restart
(keel_engine/marketplace/install.py, docs/plugins/13-step4-contract.md §5): all or nothing, every check, every refusal."""

import json
import shutil

import pytest

from keel_engine.marketplace import MarketError, catalog, install, local, sources
from keel_engine.pluginhost import resolver, state, store as host_store
from marketplace_kit import market, server  # noqa: F401 - fixtures
from test_pluginhost import data, image, plugin  # noqa: F401 - fixtures and helpers

WEB = {"web": {"entry": "web/index.js"}}
FILES = {"web/index.js": "export default {}"}


def entries() -> dict:
    return json.loads(state.installed_path().read_text())["plugins"]


def in_store(name: str) -> list[str]:
    return sorted(p.name for p in (state.store_dir() / name).iterdir()) if (state.store_dir() / name).is_dir() else []


def started() -> None:
    """keel starts: the resolver writes run/resolved.json with what loads now."""
    resolver.write(resolver.resolve())


def image_plugin(image_root, name: str, version: str = "1.0.0", **kw):
    return plugin(image_root, name, version, parts=WEB, files=FILES, sums=True, **kw)


# ------------------------------------------------------------------ install

def test_install_downloads_checks_and_unpacks_into_the_store(market):
    market.publish("hello", "1.0.0", permissions={"workspace": "read"})
    market.ready()
    events = []
    out = install.install("hello", by="person", notify=lambda kind, d: events.append((kind, d)))
    assert out["installed"] == [{"name": "hello", "version": "1.0.0", "from": None}] and out["turned_on"] == []
    assert out["pending_restart"] == {"pending": True, "changes": [{"name": "hello", "now": None, "next": "1.0.0"}]}
    e = entries()["hello"]
    e.pop("installed_at")
    assert e == {"version": "1.0.0", "on": True, "source": "marketplace", "sha256": market.plugins["hello"]["versions"][0]["sha256"],
                 "by": "person", "catalog": "test", "publisher": "keel", "trust": "web",
                 "permissions": {"workspace": "read"}, "previous": None}
    assert in_store("hello") == ["1.0.0"]
    assert list(install.downloads_dir().iterdir()) == []                    # the download is gone
    assert [p.name for p in resolver.resolve().plugins] == ["hello"]
    assert [k for k, _ in events] == ["plugin.install.started", "plugin.install.done"]
    assert events[1][1]["installed"][0]["version"] == "1.0.0"


def test_install_takes_the_needed_plugins_from_the_same_catalog_first(market):
    market.publish("code", "1.0.0")
    market.publish("code", "1.1.0")
    market.publish("db", "1.0.0", needs={"code": ">=1.0.0,<1.1.0"})
    market.ready()
    p = install.plan("db")
    assert [(s["name"], s["version"], s["needed_by"]) for s in p["install"]] == [("code", "1.0.0", "db"), ("db", "1.0.0", None)]
    assert p["checks"] == install.CHECKS and p["turn_on"] == []
    out = install.install("db")
    assert [i["name"] for i in out["installed"]] == ["code", "db"]
    assert [p.name for p in resolver.resolve().plugins] == ["code", "db"]


def test_a_needed_plugin_keel_has_is_used_and_turned_on(market, image):
    image_plugin(image, "code")
    host_store.set_on("code", False)
    market.publish("db", "1.0.0", needs={"code": ">=1.0.0"})
    market.ready()
    out = install.install("db")
    assert [i["name"] for i in out["installed"]] == ["db"] and out["turned_on"] == ["code"]
    assert entries()["code"]["on"] is True


@pytest.mark.parametrize("setup, why", [
    (lambda m: m.publish("db", "1.0.0", needs={"ghost": ">=1.0.0"}), "No version of Db fits this keel: needs ghost >=1.0.0"),
    (lambda m: m.publish("db", "1.0.0", sdk=2), "needs plugin SDK 2"),
])
def test_a_plugin_that_does_not_fit_is_refused(market, setup, why):
    setup(market)
    market.ready()
    with pytest.raises(MarketError, match=why) as exc:
        install.install("db")
    assert exc.value.status == 409
    assert not state.installed_path().exists()


def test_unknown_names_and_versions_are_refused(market):
    market.publish("hello", "1.0.0")
    market.ready()
    with pytest.raises(MarketError, match="no plugin nope in the catalogs") as exc:
        install.install("nope")
    assert exc.value.status == 404 and "Refresh the catalogs" in exc.value.hint
    with pytest.raises(MarketError, match="has no version 9.9.9") as exc:
        install.install("hello", "9.9.9")
    assert exc.value.status == 404 and exc.value.hint == "Its versions: 1.0.0."
    with pytest.raises(MarketError, match="not a plugin name"):
        install.install("../etc")


def test_a_revoked_version_is_never_installed(market):
    market.publish("hello", "1.0.0")
    market.revoked.append({"name": "hello", "version": "1.0.0", "why": "sent errors to a wrong host"})
    market.ready()
    with pytest.raises(MarketError, match="hello 1.0.0 is revoked: sent errors to a wrong host"):
        install.install("hello", "1.0.0")
    with pytest.raises(MarketError, match="No version of Hello fits"):
        install.install("hello")


def test_an_old_catalog_installs_nothing(market):
    market.publish("hello", "1.0.0")
    market.expires = "2020-01-01T00:00:00Z"
    market.ready()
    with pytest.raises(MarketError, match="The catalog test is old") as exc:
        install.install("hello")
    assert "Refresh it" in exc.value.hint


@pytest.mark.parametrize("trust, refused", [("web", True), ("code", True), ("content", False)])
def test_an_unverified_publishers_web_and_code_plugins_wait_for_the_rule(market, trust, refused):
    other = market.publisher_key.public().line()
    market.publishers["ana"] = {"title": "Ana K", "keys": [other], "verified": False}
    market.publish("notes", "1.0.0", publisher="ana", trust=trust)
    market.ready()
    if refused:
        with pytest.raises(MarketError, match="from Ana K, an unverified publisher") as exc:
            install.install("notes")
        assert "Allow unverified publishers" in exc.value.hint
        sources.write_rules({"allow_unverified": True})
    assert install.install("notes")["version"] == "1.0.0"


def test_a_plugin_that_asks_for_python_libraries_is_refused(market):
    market.publish("py", "1.0.0", extra={"requires": {"sdk": 1, "python": ["requests==2.32.0"]}})
    market.publish("lock", "1.0.0", engine="keel_plugin_lock", files={"engine/requirements.lock": "x==1 --hash=sha256:00\n"})
    market.ready()
    with pytest.raises(MarketError, match=r"asks for extra Python libraries \(requests==2.32.0\)") as exc:
        install.install("py")
    assert exc.value.more["libraries"] == ["requests==2.32.0"]
    with pytest.raises(MarketError, match=r"engine/requirements.lock"):
        install.install("lock")
    assert not state.installed_path().exists() and in_store("py") == in_store("lock") == []


@pytest.mark.parametrize("change, why", [
    ({"sha256": "0" * 64}, "does not match the catalog's sha256"),
    ({"size": 100}, "than the 100 bytes keel allows"),
    ({"catalog_permissions": {"workspace": "read"}}, "asks for other permissions than the catalog shows"),
])
def test_a_download_that_does_not_match_the_catalog_is_refused(market, change, why):
    market.publish("hello", "1.0.0", **change)
    market.ready()
    events = []
    with pytest.raises(MarketError, match=why):
        install.install("hello", notify=lambda kind, d: events.append((kind, d)))
    assert not state.installed_path().exists() and in_store("hello") == []
    assert list(install.downloads_dir().iterdir()) == []
    assert [k for k, _ in events] == ["plugin.install.started", "plugin.install.failed"]
    assert why.split(" ")[0] in events[1][1]["why"] and events[1][1]["name"] == "hello"


def test_a_package_signed_by_another_key_is_refused(market):
    market.publish("hello", "1.0.0", sign_with=market.catalog_key)
    market.ready()
    with pytest.raises(MarketError, match="does not match its publisher's key") as exc:
        install.install("hello")
    assert exc.value.status == 502 and "installed nothing" in exc.value.hint


def test_a_package_that_says_it_is_another_plugin_is_refused(market):
    other = market.package("hello", "1.0.1")
    market.publish("hello", "1.0.0", file=other)
    market.ready()
    with pytest.raises(MarketError, match="says it is hello 1.0.1, the catalog says hello 1.0.0"):
        install.install("hello")


def test_install_is_all_or_nothing(market, monkeypatch):
    market.publish("code", "1.0.0")
    market.publish("db", "1.0.0", needs={"code": ">=1.0.0"}, sign_with=market.catalog_key)   # its signature is wrong
    market.ready()
    with pytest.raises(MarketError, match="signature of db 1.0.0"):
        install.install("db")
    assert not state.installed_path().exists() and in_store("code") == []   # code was downloaded, never installed
    market.plugins.clear()
    market.publish("code", "1.0.0")
    market.publish("db", "1.0.0", needs={"code": ">=1.0.0"})
    market.built = "2026-10-10T00:00:00Z"
    market.ready()
    real = host_store.install

    def fail_second(file, **kw):
        if "db" in str(file):
            raise OSError("disk full")
        return real(file, **kw)

    monkeypatch.setattr(host_store, "install", fail_second)
    with pytest.raises(MarketError, match="disk full"):
        install.install("db")
    assert not state.installed_path().exists() and in_store("code") == []   # code's unpacked folder went again


def test_installing_what_keel_has_is_refused(market, image):
    image_plugin(image, "code")
    market.publish("code", "1.0.0")
    market.publish("code", "1.1.0")
    market.publish("hello", "1.0.0")
    market.ready()
    install.install("hello")
    with pytest.raises(MarketError, match="Hello 1.0.0 is installed already"):
        install.install("hello")
    with pytest.raises(MarketError, match="Code 1.0.0 comes with keel's image"):
        install.install("code", "1.0.0")
    with pytest.raises(MarketError, match="Code 1.0.0 is installed already") as exc:
        install.install("code")
    assert exc.value.hint.startswith("Update it instead")


# ------------------------------------------------------------------ update and roll back

def test_update_keeps_the_version_before_and_drops_older_ones(market):
    market.publish("hello", "1.0.0")
    market.ready()
    install.install("hello")
    market.publish("hello", "1.1.0")
    market.built = "2026-10-10T00:00:00Z"
    market.ready()
    out = install.update("hello")
    assert out["installed"] == [{"name": "hello", "version": "1.1.0", "from": "1.0.0"}]
    e = entries()["hello"]
    assert e["version"] == "1.1.0" and e["previous"]["version"] == "1.0.0" and "previous" not in e["previous"]
    assert in_store("hello") == ["1.0.0", "1.1.0"]
    market.publish("hello", "1.2.0")
    market.built = "2026-10-11T00:00:00Z"
    market.ready()
    install.update("hello")
    assert in_store("hello") == ["1.1.0", "1.2.0"]                         # the current and the previous only
    with pytest.raises(MarketError, match="Hello 1.2.0 is the newest version that fits this keel"):
        install.update("hello")
    with pytest.raises(MarketError, match="1.0.0 is not newer than 1.2.0"):
        install.update("hello", "1.0.0")
    with pytest.raises(MarketError, match="nope is not installed"):
        install.update("nope")


def test_a_pre_release_installs_and_updates_to_its_release(market):
    market.publish("product", "0.1.0-beta.1", category="product")
    market.ready()
    assert install.install("product")["version"] == "0.1.0-beta.1"
    assert in_store("product") == ["0.1.0-beta.1"]
    market.publish("product", "0.1.0")
    market.built = "2026-10-10T00:00:00Z"
    market.ready()
    assert install.update("product")["installed"] == [{"name": "product", "version": "0.1.0", "from": "0.1.0-beta.1"}]
    assert [(p.name, p.version) for p in resolver.resolve().plugins] == [("product", "0.1.0")]


def test_update_is_refused_when_it_asks_for_more_permissions(market):
    market.publish("hello", "1.0.0", permissions={"workspace": "read", "network": ["api.github.com"]})
    market.ready()
    install.install("hello")
    market.publish("hello", "1.1.0", permissions={"workspace": "write", "network": ["api.github.com", "gitlab.com"],
                                                  "secrets": ["gitlab"]})
    market.built = "2026-10-10T00:00:00Z"
    market.ready()
    with pytest.raises(MarketError, match="asks for more permissions than 1.0.0") as exc:
        install.update("hello")
    assert exc.value.status == 409
    assert exc.value.more == {"more": ["+ network: gitlab.com", "+ secrets: gitlab", "+ workspace: write (was read)"],
                              "installed": "1.0.0", "version": "1.1.0"}
    assert entries()["hello"]["version"] == "1.0.0"
    assert install.update("hello", allow_more_permissions=True)["version"] == "1.1.0"


def test_more_permissions_counts_only_what_is_added():
    more = install.more_permissions
    assert more({"workspace": "write"}, {"workspace": "read"}) == []
    assert more({}, {"workspace": "read", "pages": True, "tables": ["db_x"]}) == ["+ pages: True", "+ tables: db_x",
                                                                                  "+ workspace: read"]
    assert more({"agent_tools": "read"}, {"agent_tools": "act"}) == ["+ agent_tools: act (was read)"]
    assert more({"secrets": ["a", "b"]}, {"secrets": ["b"]}) == []
    assert more({"flows": "start"}, {"flows": "start"}) == []


def test_rollback_goes_to_the_kept_version_and_back(market):
    market.publish("hello", "1.0.0")
    market.ready()
    install.install("hello")
    with pytest.raises(MarketError, match="hello has no earlier version"):
        install.rollback("hello")
    market.publish("hello", "1.1.0")
    market.built = "2026-10-10T00:00:00Z"
    market.ready()
    install.update("hello")
    started()
    out = install.rollback("hello")
    assert (out["version"], out["from"]) == ("1.0.0", "1.1.0")
    assert out["pending_restart"]["changes"] == [{"name": "hello", "now": "1.1.0", "next": "1.0.0"}]
    e = entries()["hello"]
    assert (e["version"], e["previous"]["version"], e["source"]) == ("1.0.0", "1.1.0", "marketplace")
    assert install.rollback("hello")["version"] == "1.1.0"
    assert in_store("hello") == ["1.0.0", "1.1.0"]


def test_rollback_refuses_a_revoked_version(market):
    market.publish("hello", "1.0.0")
    market.publish("hello", "1.1.0")
    market.ready()
    install.install("hello", "1.0.0")
    install.update("hello")
    market.revoked.append({"name": "hello", "version": "1.0.0", "why": "a bad bug"})
    market.built = "2026-10-10T00:00:00Z"
    market.ready()
    with pytest.raises(MarketError, match="Hello 1.0.0 is revoked: a bad bug"):
        install.rollback("hello")


def test_an_image_plugin_updates_from_the_marketplace_and_rolls_back_to_the_image(market, image):
    image_plugin(image, "code")
    market.publish("code", "1.1.0")
    market.ready()
    install.update("code")
    assert entries()["code"]["previous"] == {"version": "1.0.0", "source": "image"}
    assert [(p.name, p.version, p.source) for p in resolver.resolve().plugins] == [("code", "1.1.0", "marketplace")]
    install.rollback("code")
    assert "version" not in entries()["code"] and entries()["code"]["previous"]["version"] == "1.1.0"
    assert [(p.name, p.version, p.source) for p in resolver.resolve().plugins] == [("code", "1.0.0", "image")]
    install.rollback("code")                                                # and forward again
    assert entries()["code"]["version"] == "1.1.0"


# ------------------------------------------------------------------ remove, on and off

def test_remove_refuses_an_image_plugin_and_a_needed_one(market, image):
    image_plugin(image, "code")
    market.publish("git", "1.0.0", needs={"code": ">=1.0.0"})
    market.publish("db", "1.0.0", needs={"git": ">=1.0.0"})
    market.ready()
    install.install("db")
    with pytest.raises(MarketError, match="Code came with keel's image: it cannot be removed, only turned off"):
        install.remove("code")
    with pytest.raises(MarketError, match="Git is needed by Db") as exc:
        install.remove("git")
    assert exc.value.more == {"needed_by": ["db"]}
    with pytest.raises(MarketError, match="data must be keep or delete"):
        install.remove("db", "drop")
    with pytest.raises(MarketError, match="nope is not installed"):
        install.remove("nope")


def test_remove_keeps_or_deletes_the_plugins_data_folder(market):
    market.publish("hello", "1.0.0")
    market.publish("notes", "1.0.0")
    market.ready()
    install.install("hello")
    install.install("notes")
    for n in ("hello", "notes"):
        (state.plugins_dir() / "data" / n).mkdir(parents=True)
        (state.plugins_dir() / "data" / n / "x.txt").write_text("mine")
    started()
    out = install.remove("hello")
    assert out == {**out, "removed": "1.0.0", "data": "keep", "back_to_image": None}
    assert out["pending_restart"]["changes"] == [{"name": "hello", "now": "1.0.0", "next": None}]
    assert "hello" not in entries() and in_store("hello") == []
    assert (state.plugins_dir() / "data/hello/x.txt").read_text() == "mine"
    install.remove("notes", "delete")
    assert not (state.plugins_dir() / "data/notes").exists() and (state.plugins_dir() / "data/hello").exists()


def test_removing_a_marketplace_copy_goes_back_to_the_image(market, image):
    image_plugin(image, "code")
    market.publish("code", "1.1.0")
    market.ready()
    install.update("code")
    out = install.remove("code")
    assert out["back_to_image"] == "1.0.0" and entries()["code"] == {"on": True}
    assert [(p.name, p.version) for p in resolver.resolve().plugins] == [("code", "1.0.0")]


def test_off_takes_the_dependents_along_and_on_brings_the_needs(market, image):
    image_plugin(image, "code")
    market.publish("git", "1.0.0", needs={"code": ">=1.0.0"})
    market.publish("review", "1.0.0", needs={"git": ">=1.0.0"})
    market.ready()
    install.install("review")
    out = install.set_on("code", False)
    assert out["also"] == ["git", "review"]
    assert [entries()[n]["on"] for n in ("code", "git", "review")] == [False, False, False]
    out = install.set_on("review", True)
    assert sorted(out["also"]) == ["code", "git"]
    assert [p.name for p in resolver.resolve().plugins] == ["code", "git", "review"]
    with pytest.raises(MarketError, match="no plugin ghost"):
        install.set_on("ghost", True)


# ------------------------------------------------------------------ install from a file

def test_install_file_takes_a_kplug_from_keels_data_folder(market, tmp_path):
    file = market.package("hello", "1.0.0", permissions={"pages": True})
    with pytest.raises(MarketError, match="from its data folder only"):
        install.install_file(str(file))
    inside = state.plugins_dir().parent / "uploads" / file.name
    inside.parent.mkdir(parents=True)
    shutil.copy(file, inside)
    out = install.install_file("uploads/" + file.name)            # relative to keel's data folder
    assert out["installed"] == [{"name": "hello", "version": "1.0.0", "from": None}] and out["source"] == "file"
    e = entries()["hello"]
    assert (e["source"], e["permissions"], e["previous"]) == ("file", {"pages": True}, None)
    with pytest.raises(MarketError, match="installed already"):
        install.install_file(str(inside))
    assert install.install_file(str(inside), force=True)["version"] == "1.0.0"
    with pytest.raises(MarketError, match="There is no file"):
        install.install_file(str(inside) + ".nope")
    py = market.package("py", "1.0.0", extra={"requires": {"sdk": 1, "python": {"requests": "==2.32"}}})
    shutil.copy(py, inside.parent / py.name)
    with pytest.raises(MarketError, match=r"Python libraries \(requests ==2.32\)"):
        install.install_file(str(inside.parent / py.name))


# ------------------------------------------------------------------ what waits for a restart, the installed list

def test_pending_restart_compares_the_next_start_with_the_last(market, image):
    image_plugin(image, "code")
    assert install.pending_restart() == {"pending": True, "changes": [{"name": "code", "now": None, "next": "1.0.0"}]}
    started()
    assert install.pending_restart() == {"pending": False, "changes": []}
    install.set_on("code", False)
    assert install.pending_restart()["changes"] == [{"name": "code", "now": "1.0.0", "next": None}]
    install.set_on("code", True)
    assert install.pending_restart()["pending"] is False
    state.installed_path().write_text("{broken")
    assert install.pending_restart()["problem"]


def test_the_installed_list_says_where_each_plugin_is(market, image):
    image_plugin(image, "code")
    image_plugin(image, "map")
    market.publish("hello", "1.0.0", permissions={"workspace": "read"})
    market.publish("hello", "1.1.0", permissions={"workspace": "read"})
    market.publish("gone", "1.0.0")
    market.revoked.append({"name": "hello", "version": "1.0.0", "why": "a bad bug"})
    market.ready()
    started()                                                       # code and map loaded
    install.set_on("map", False)
    market.revoked.clear()
    market.built = "2026-10-10T00:00:00Z"
    market.ready()
    install.install("hello", "1.0.0")
    install.install("gone")
    started()
    install.remove("gone")
    market.revoked.append({"name": "hello", "version": "1.0.0", "why": "a bad bug"})
    market.built = "2026-10-11T00:00:00Z"
    market.ready()
    view = install.installed_view()
    rows = {p["name"]: p for p in view["plugins"]}
    assert list(rows) == ["code", "hello", "map", "gone"]
    assert rows["code"] == {**rows["code"], "from": "image", "status": "loaded", "on": True, "parts": ["web"],
                            "can_remove": False, "update": None}
    assert rows["map"]["status"] == "off"
    assert rows["hello"] == {**rows["hello"], "from": "marketplace", "status": "loaded", "update": "1.1.0",
                             "revoked": {"version": "1.0.0", "why": "a bad bug", "fixed": "1.1.0"},
                             "trust": "web", "catalog": "test", "permissions": {"workspace": "read"}, "can_remove": True}
    assert rows["gone"]["status"] == "removed"
    assert view["pending_restart"]["changes"] == [{"name": "gone", "now": "1.0.0", "next": None}]
    assert view["mode"] == "on"
    shutil.rmtree(state.store_dir() / "hello" / "1.0.0" / "web")
    rows = {p["name"]: p for p in install.installed_view()["plugins"]}
    assert rows["hello"]["status"] == "left out" and "web" in rows["hello"]["problems"][0]


def test_the_lock_lets_one_change_run_at_a_time(market):
    with install._locked():
        with install._locked():                                    # the same thread may take it again
            assert (state.plugins_dir() / ".lock").exists()


def test_local_have_reads_the_image_and_installed_json(image):
    image_plugin(image, "code", "1.0.0")
    image_plugin(image, "code", "1.2.0")
    have = local.have()
    assert (have["code"].version, have["code"].source, have["code"].title) == ("1.2.0", "image", "Code")
    assert local.loaded() is None
    assert catalog.lookup("code") is None
