"""The marketplace's engine routes (docs/plugins/13-step4-contract.md §5), `needs_plugins` (§8), the command line
(`keel-engine plugins search | get | update | rollback | remove`) and the four MCP tools (§7)."""

import json
import shutil

import httpx
import pytest

from keel_engine import mcp_server
from keel_engine.marketplace import install
from keel_engine.pluginhost import resolver, state
from keel_engine.pluginhost.cli import main
from keel_engine.workflows.model import from_dict
from marketplace_kit import market, server  # noqa: F401 - fixtures
from test_pluginhost import data, image, plugin  # noqa: F401 - fixtures and helpers

WEB = {"web": {"entry": "web/index.js"}}
FILES = {"web/index.js": "export default {}"}


def events(client, prefix="plugin.install."):
    return [(e["type"], e["data"]) for e in client.bus.recent if e["type"].startswith(prefix)]


# ------------------------------------------------------------------ the routes

def test_search_one_plugin_and_refresh(client, market):
    market.publish("code", "1.0.0", title="Code")
    market.publish("hello", "1.0.0", permissions={"workspace": "read"})
    market.publish("hello", "1.1.0", permissions={"workspace": "read"}, needs={"code": ">=1.0.0"})
    market.serve()
    market.add_source()
    r = client.post("/marketplace/refresh")
    assert r.status_code == 200 and [s["id"] for s in r.json()["sources"]] == ["keel", "test"]
    body = client.get("/marketplace/search", params={"q": "hello"}).json()
    assert set(body) == {"plugins", "sources", "categories"}
    [hit] = body["plugins"]
    assert (hit["name"], hit["version"], hit["installed"]) == ("hello", "1.1.0", None)
    assert client.get("/marketplace/search", params={"category": "knowledge"}).json()["plugins"] == []
    one = client.get("/marketplace/plugins/hello").json()
    assert [v["version"] for v in one["versions"]] == ["1.1.0", "1.0.0"]
    assert one["versions"][0] == {**one["versions"][0], "fits": True, "why_not": None, "revoked": None,
                                  "requires": {"sdk": 1, "plugins": {"code": ">=1.0.0"}}, "permissions": {"workspace": "read"}}
    assert one["needs"] == {"code": ">=1.0.0"} and one["checks"] == install.CHECKS and one["refused"] is None
    assert [(s["name"], s["needed_by"]) for s in one["plan"]["install"]] == [("code", "hello"), ("hello", None)]
    r = client.get("/marketplace/plugins/nope")
    assert r.status_code == 404 and r.json() == {"error": "There is no plugin nope in the catalogs keel reads.",
                                                 "hint": "Refresh the catalogs (Control › Plugins › Sources and rules), "
                                                         "or check the name."}


def test_a_plugin_keel_would_refuse_shows_why(client, market):
    market.publish("hello", "1.0.0")
    market.expires = "2020-01-01T00:00:00Z"
    market.ready()
    one = client.get("/marketplace/plugins/hello").json()
    assert one["plan"] is None and one["refused"]["error"].startswith("The catalog test is old")
    assert one["old"] is True


def test_install_answers_and_sends_its_events(client, market):
    market.publish("hello", "1.0.0")
    market.ready()
    r = client.post("/marketplace/install", json={"name": "hello"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out == {**out, "name": "hello", "version": "1.0.0", "title": "Hello", "turned_on": [],
                   "installed": [{"name": "hello", "version": "1.0.0", "from": None}]}
    assert out["pending_restart"]["pending"] is True
    got = events(client)
    assert [k for k, _ in got] == ["plugin.install.started", "plugin.install.done"]
    assert got[0][1] == {"name": "hello", "version": "1.0.0", "update": False, "plugins": ["hello"]}
    e = next(e for e in client.bus.recent if e["type"] == "plugin.install.done")
    assert (e["thread_id"], e["project_id"], e["step"]) == ("marketplace", "", "plugin")
    r = client.post("/marketplace/install", json={"name": "hello"})
    assert r.status_code == 409
    assert r.json() == {"error": "Hello 1.0.0 is installed already."}
    assert events(client)[-1] == ("plugin.install.failed", {"name": "hello", "version": None,
                                                           "why": "Hello 1.0.0 is installed already."})
    assert client.post("/marketplace/install", json={}).status_code == 400


def test_the_installed_plugins_routes(client, market, image):
    plugin(image, "code", "1.0.0", parts=WEB, files=FILES, sums=True)
    market.publish("hello", "1.0.0", needs={"code": ">=1.0.0"})
    market.ready()
    assert client.post("/marketplace/install", json={"name": "hello"}).status_code == 200
    resolver.write(resolver.resolve())
    view = client.get("/marketplace/installed").json()
    assert set(view) == {"plugins", "pending_restart", "problems", "mode"}
    assert [(p["name"], p["from"], p["status"]) for p in view["plugins"]] == [("code", "image", "loaded"),
                                                                              ("hello", "marketplace", "loaded")]
    assert view["pending_restart"] == {"pending": False, "changes": []}
    market.publish("hello", "1.1.0", needs={"code": ">=1.0.0"}, permissions={"secrets": ["gitlab"]})
    market.built = "2026-10-10T00:00:00Z"
    market.ready()
    r = client.post("/marketplace/installed/hello/update", json={})
    assert r.status_code == 409 and r.json()["more"] == ["+ secrets: gitlab"]
    assert r.json()["installed"] == "1.0.0" and r.json()["version"] == "1.1.0"
    r = client.post("/marketplace/installed/hello/update", json={"allow_more_permissions": True, "by": "milad"})
    assert r.status_code == 200 and r.json()["version"] == "1.1.0"
    assert json.loads(state.installed_path().read_text())["plugins"]["hello"]["by"] == "milad"
    r = client.post("/marketplace/installed/hello/rollback")
    assert r.json() == {**r.json(), "name": "hello", "version": "1.0.0", "from": "1.1.0"}
    r = client.put("/marketplace/installed/code", json={"on": False})
    assert r.json() == {**r.json(), "name": "code", "on": False, "also": ["hello"]}
    assert r.json()["pending_restart"]["pending"] is True
    r = client.delete("/marketplace/installed/code")
    assert r.status_code == 409 and "only turned off" in r.json()["error"]
    r = client.delete("/marketplace/installed/hello", params={"data": "delete"})
    assert r.status_code == 200 and r.json()["removed"] == "1.0.0" and r.json()["data"] == "delete"
    assert client.delete("/marketplace/installed/hello", params={"data": "drop"}).status_code == 400


def test_install_file_route(client, market):
    file = market.package("hello", "1.0.0")
    dest = state.plugins_dir().parent / "hello-1.0.0.kplug"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(file, dest)
    r = client.post("/marketplace/install-file", json={"path": str(dest)})
    assert r.status_code == 200 and r.json()["source"] == "file"
    r = client.post("/marketplace/install-file", json={"path": str(file)})
    assert r.status_code == 400 and "data folder only" in r.json()["error"]


def test_sources_and_rules_routes(client, market):
    body = client.get("/marketplace/sources").json()
    assert [s["id"] for s in body["sources"]] == ["keel"] and body["sources"][0]["official"] is True
    r = client.put("/marketplace/sources", json={"sources": [{"id": "keel", "on": False}, market.source()]})
    assert r.status_code == 200
    test = r.json()["sources"][1]
    assert test == {**test, "id": "test", "url": market.index_url, "key": market.catalog_key.public().line(),
                    "on": True, "official": False, "ok": False}
    r = client.put("/marketplace/sources", json={"sources": [{**market.source(), "key": "nope"}]})
    assert r.status_code == 400 and "not a minisign public key" in r.json()["error"]
    assert client.get("/marketplace/rules").json()["agents_may_ask"] is True
    r = client.put("/marketplace/rules", json={"agents_may_ask": False, "restart_when_idle": True})
    assert r.json() == {"agents_may_ask": False, "allow_unverified": False, "check_daily": True, "restart_when_idle": True}
    r = client.put("/marketplace/rules", json={"agents_may_ask": "no"})
    assert r.status_code == 400 and r.json()["error"] == "The rule agents_may_ask must be true or false."


def test_the_sets_say_what_is_missing_and_what_is_off(client, image):
    plugin(image, "code", "1.0.0", parts=WEB, files=FILES, sums=True)
    plugin(image, "git", "1.0.0", parts=WEB, files=FILES, sums=True)
    install.set_on("git", False)
    sets = {s["id"]: s for s in client.get("/marketplace/sets").json()["sets"]}
    assert list(sets) == ["developer", "review", "knowledge", "tickets"]
    assert sets["developer"]["plugins"] == ["code", "git", "review", "db", "ci", "graph", "keelbot"]
    assert sets["review"] == {**sets["review"], "title": "Review", "missing": ["review", "keelbot"], "off": ["git"]}
    assert sets["tickets"]["plugins"] == ["tasks", "jira"] and sets["knowledge"]["plugins"] == ["wiki", "map", "graph"]


def test_the_routes_need_the_internal_token(client, monkeypatch):
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN", "t0k")
    assert client.get("/marketplace/rules").status_code == 401
    assert client.get("/marketplace/rules", headers={"X-Keel-Token": "t0k"}).status_code == 200


def test_the_catalogs_are_read_while_keel_runs(market, monkeypatch):
    import asyncio

    from keel_engine.marketplace import catalog, routes

    market.publish("hello", "1.0.0")
    market.serve()
    market.add_source()
    calls = []
    monkeypatch.setattr(catalog, "refresh", lambda **kw: calls.append(kw))

    async def run():
        task = asyncio.create_task(routes.refresher(first=0, every=0.01))
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(run())
    assert calls == []                                   # KEEL_API_URL=off (the tests): it never goes out by itself
    monkeypatch.setenv("KEEL_API_URL", "http://127.0.0.1:9")
    asyncio.run(run())
    assert calls and calls[0] == {"older_than": catalog.FRESH}
    calls.clear()
    from keel_engine.marketplace import sources

    sources.write_rules({"check_daily": False})
    asyncio.run(run())
    assert calls == []


# ------------------------------------------------------------------ needs_plugins

def needing(names):
    return from_dict({"name": "Needs db", "keel_rules": False, "needs_plugins": names,
                      "steps": [{"id": "a", "kind": "gate", "name": "Look"}]})


def thread_body(repo, wf):
    return {"project_id": "demo", "root": str(repo), "workflow": wf.model_dump(), "title": "t",
            "models": {"default": {"provider": "fake", "mode": "api", "model": "fake"}}}


def test_needs_plugins_is_validated_and_kept():
    from keel_engine.workflows.model import dump_yaml
    from keel_engine.workflows.validate import validate, validate_yaml

    wf = needing(["db", "git"])
    assert validate(wf) == [] and wf.model_dump()["needs_plugins"] == ["db", "git"]
    assert "needs_plugins:\n- db\n- git\n" in dump_yaml(wf)
    assert "needs_plugins" not in needing(None).model_dump() and "needs_plugins" not in needing([]).model_dump()
    assert validate(needing(["Bad Name"])) == ["needs_plugins: 'Bad Name' is not a plugin name (a-z, 0-9 and '-')."]
    assert validate_yaml("name: x\nneeds_plugins: db\nsteps: [{id: a, kind: gate, name: a}]\n")["ok"] is False


def test_a_workflow_that_needs_a_plugin_keel_did_not_load_is_refused(client, repo, image):
    r = client.post("/threads", json=thread_body(repo, needing(["db", "git"])))
    assert r.status_code == 409
    assert r.json() == {"error": "This workflow needs db and git.", "hint": "Install it in Control › Plugins, then "
                        "restart keel.", "missing": ["db", "git"], "workflow": "needs-db"}
    plugin(image, "db", "1.0.0", parts=WEB, files=FILES, sums=True)
    plugin(image, "git", "1.0.0", parts=WEB, files=FILES, sums=True)
    r = client.post("/threads", json=thread_body(repo, needing(["db", "git"])))
    assert r.status_code == 409 and r.json()["error"] == "This workflow needs Db and Git."   # had, not loaded yet
    resolver.write(resolver.resolve())                                                     # keel started with them
    r = client.post("/threads", json=thread_body(repo, needing(["db", "git"])))
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ the command line

def test_the_command_line_finds_installs_updates_rolls_back_and_removes(market, capsys):
    market.publish("hello", "1.0.0")
    market.publish("hello", "1.1.0", permissions={"secrets": ["jira"]})
    market.ready()
    assert main(["search", "hel"]) == 0
    out = capsys.readouterr().out
    assert "hello" in out and "Hello · web · 1.1.0" in out and "The hello plugin for tests." in out
    assert main(["search", "--json", "nothing-like-this"]) == 0
    assert json.loads(capsys.readouterr().out)["plugins"] == []
    assert main(["get", "hello@1.0.0"]) == 0
    out = capsys.readouterr().out
    assert "installed hello 1.0.0" in out and "restart keel to use it" in out
    assert json.loads(state.installed_path().read_text())["plugins"]["hello"]["by"] == "cli"
    assert main(["update", "hello"]) == 1
    err = capsys.readouterr().err
    assert "asks for more permissions than 1.0.0" in err and "  + secrets: jira" in err
    assert main(["update", "hello", "--accept-permissions"]) == 0
    assert "updated hello to 1.1.0" in capsys.readouterr().out
    assert main(["rollback", "hello"]) == 0
    assert "hello goes back from 1.1.0 to 1.0.0" in capsys.readouterr().out
    (state.plugins_dir() / "data" / "hello").mkdir(parents=True)
    assert main(["remove", "hello", "--delete-data"]) == 0
    assert "removed hello 1.0.0; its data was deleted" in capsys.readouterr().out
    assert not (state.plugins_dir() / "data" / "hello").exists()
    assert main(["get", "nope"]) == 1
    assert "no plugin nope in the catalogs keel reads. Refresh the catalogs (Control" in capsys.readouterr().err


# ------------------------------------------------------------------ the MCP tools

class MarketApi:
    """The api routes the marketplace's MCP tools call, recording the requests."""

    def __init__(self, may_ask=True, request_status=200):
        self.may_ask = may_ask
        self.request_status = request_status
        self.posts: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "POST" and path == "/api/plugins/requests":
            self.posts.append(json.loads(req.content))
            if self.request_status != 200:
                return httpx.Response(self.request_status, json={"error": "Agents may not ask now."})
            return httpx.Response(200, json={"id": "a_41", "joined": len(self.posts) > 1})
        routes = {
            "/api/marketplace": {"plugins": [{"name": "db", "title": "Database", "trust": "code", "installed": None,
                                              "version": "1.0.0", "summary": "Connect a database."}],
                                 "sources": [{"id": "keel", "on": True, "problem": "not set up yet", "old": False}]},
            "/api/marketplace/db": {"name": "db", "title": "Database", "publisher": "keel", "publisher_title": "keel",
                                    "verified": True, "trust": "code", "category": "code", "summary": "Connect it.",
                                    "installed": None, "versions": [
                                        {"version": "1.0.0", "released": "2026-10-09",
                                         "requires": {"sdk": 1, "keel": ">=0.15.4", "plugins": {"code": ">=1.0.0"}},
                                         "permissions": {"secrets": ["database"], "workspace": "read"},
                                         "fits": True, "revoked": None}],
                                    "plan": {"install": [{"name": "code", "version": "1.0.0", "needed_by": "db"},
                                                         {"name": "db", "version": "1.0.0", "needed_by": None}],
                                             "turn_on": []}},
            "/api/plugins": {"plugins": [{"name": "code", "version": "1.0.0", "title": "Code", "from": "image",
                                          "on": True, "status": "loaded", "problems": []}],
                             "restart": {"pending": True, "scheduled": False}},
            "/api/plugins/rules": {"agents_may_ask": self.may_ask},
            "/api/projects": [{"id": "shop", "name": "shop", "root": "/workspace/shop"}],
            "/api/projects/shop/plugins": [{"name": "db", "enabled": True}, {"name": "git", "enabled": False}],
        }
        if req.method == "GET" and path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404, json={"error": f"No route {path}"})


def api_of(stub):
    return mcp_server.KeelApi("http://keel.test", transport=httpx.MockTransport(stub))


@pytest.fixture
def cwd_root(monkeypatch):
    monkeypatch.delenv("KEEL_PROJECT", raising=False)
    monkeypatch.chdir("/")


def test_the_mcp_tools_search_and_show_one_plugin(cwd_root):
    api = api_of(MarketApi())
    text = mcp_server.marketplace_search(api, "data")
    assert "source keel: not set up yet" in text
    assert "db  Database · trust code · not installed · version 1.0.0" in text and "  Connect a database." in text
    info = mcp_server.plugin_info(api, "db")
    assert "db  Database · publisher keel (verified) · trust code · category code" in info
    assert ("  1.0.0  2026-10-09 · keel >=0.15.4 · needs code >=1.0.0 · permissions secrets: database; "
            "workspace: read · fits") in info
    assert "an install adds: code 1.0.0 (needed by db), db 1.0.0" in info
    assert "Agents cannot install plugins" in info
    with pytest.raises(mcp_server.ApiError, match="not a plugin name"):
        mcp_server.plugin_info(api, "../threads")


def test_the_mcp_tool_lists_what_keel_has_and_what_is_on_in_the_project(cwd_root):
    text = mcp_server.plugins_installed(api_of(MarketApi()))
    assert "  code 1.0.0  Code · from image · on · loaded" in text
    assert "changes wait for a restart of keel." in text and "on in shop: db" in text


def test_the_request_tool_only_asks(cwd_root):
    stub = MarketApi()
    api = api_of(stub)
    text = mcp_server.plugin_request(api, "db", "read the sessions schema")
    assert "request a_41" in text and "waits in keel's Inbox" in text
    assert stub.posts == [{"name": "db", "reason": "read the sessions schema", "source": "agent", "project": "shop"}]
    assert "joined the request that already waits" in mcp_server.plugin_request(api, "db", "me too", "1.0.0")
    assert stub.posts[1]["version"] == "1.0.0"
    assert mcp_server.plugin_request(api, "db", "  ").startswith("Give a reason")
    off = api_of(MarketApi(may_ask=False))
    assert mcp_server.plugin_request(off, "db", "why not") == mcp_server.ASK_IN_PLUGINS
    refused = api_of(MarketApi(request_status=409))
    assert mcp_server.plugin_request(refused, "db", "why not").startswith(mcp_server.ASK_IN_PLUGINS)


async def test_the_request_tool_is_there_in_read_only_mode(cwd_root):
    from mcp.shared.memory import create_connected_server_and_client_session

    stub = MarketApi()
    srv = mcp_server.build_server(write=False, api=api_of(stub))
    async with create_connected_server_and_client_session(srv) as session:
        res = await session.call_tool("keel_plugin_request", {"name": "db", "reason": "the flow needs the schema"})
        assert not res.isError and "request a_41" in res.content[0].text
        tools = {t.name: t for t in (await session.list_tools()).tools}
    assert tools["keel_marketplace_search"].annotations.readOnlyHint is True
    assert tools["keel_plugin_request"].annotations.destructiveHint is False
    assert not any("install" in n and n != "keel_plugins_installed" for n in tools)        # no install tool
