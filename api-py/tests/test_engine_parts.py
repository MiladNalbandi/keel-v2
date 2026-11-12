"""A plugin part that lives in the engine only (the Org plugin, issue #92): /api/org/* goes to the engine's /org/*,
after LocalAuth, with the internal token and never the caller's own credentials; everything else still goes to Kotlin."""

import httpx

from conftest import TOKEN


def test_org_routes_go_to_the_engine_with_the_internal_token(keel):
    r = keel.client.get("/api/org/orgs?limit=5", headers={"X-Keel-Token": TOKEN})
    assert r.status_code == 200 and r.json() == {"from": "engine", "path": "/org/orgs"}
    req = keel.engine.seen[-1]
    assert req.url.path == "/org/orgs" and req.url.query == b"limit=5"
    assert req.headers["x-keel-token"] == TOKEN
    assert keel.back.seen == []                     # Kotlin never saw it


def test_a_signed_in_browser_reaches_the_org_and_its_cookie_stays_here(keel):
    cookie = keel.sign_in()
    keel.engine.answers["/org/orgs/shop"] = lambda req: httpx.Response(400, json={"error": "The org has problems", "hint": "fix"})
    r = keel.client.put("/api/org/orgs/shop", json={"yaml": "roles: {}"}, headers={**keel.cookie(cookie), "X-Keel-Web": "1"})
    assert r.status_code == 400 and r.json()["error"] == "The org has problems"
    req = keel.engine.seen[-1]
    assert req.method == "PUT" and b'"roles: {}"' in req.content
    assert "cookie" not in req.headers and req.headers["x-keel-token"] == TOKEN
    # a write without the web app's header is refused before the engine sees it (LocalAuth)
    n = len(keel.engine.seen)
    assert keel.client.put("/api/org/orgs/shop", json={"yaml": ""}, headers=keel.cookie(cookie)).status_code == 403
    assert len(keel.engine.seen) == n


def test_no_credentials_no_org(keel):
    r = keel.client.get("/api/org/orgs", headers={"Host": "localhost:8080", "Origin": "http://evil.example"})
    assert r.status_code in (401, 403)
    assert keel.engine.seen == []


def test_the_engine_down_is_a_503(keel):
    keel.engine.down = True
    r = keel.client.get("/api/org/orgs", headers={"X-Keel-Token": TOKEN})
    assert r.status_code == 503 and "engine" in r.json()["error"]


def test_other_routes_still_go_to_kotlin(keel):
    r = keel.client.get("/api/organisations", headers={"X-Keel-Token": TOKEN})
    assert r.json()["from"] == "kotlin"
    assert keel.engine.seen == []


def test_an_mcp_token_keeps_its_level_on_org_routes(keel):
    read = keel.ctx.mcp_tokens.token_for("read")
    admin = keel.ctx.mcp_tokens.token_for("admin")
    ok = keel.client.get("/api/org/orgs", headers={"X-Keel-Mcp-Token": read})
    assert ok.status_code == 200
    n = len(keel.engine.seen)
    no = keel.client.post("/api/org/orgs/shop/missions", json={"role": "lead"}, headers={"X-Keel-Mcp-Token": read})
    assert no.status_code == 403 and "admin level" in no.json()["error"]
    assert len(keel.engine.seen) == n                 # refused before the engine saw it
    yes = keel.client.post("/api/org/orgs/shop/missions", json={"role": "lead"}, headers={"X-Keel-Mcp-Token": admin})
    assert yes.status_code == 200 and keel.engine.seen[-1].method == "POST"
