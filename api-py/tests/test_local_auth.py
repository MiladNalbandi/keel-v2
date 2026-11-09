"""The front door's checks: the cases of the Kotlin api's LocalAuthApiTest, against this api."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat

from conftest import TOKEN, make_keel

from keel_api.auth.local_auth import HINT, SIGN_IN
from keel_api.auth.routes import safe_next
from keel_api.auth.web_key import Check
from keel_api.common.support import now_ms, sha256_hex
from keel_api.mcp.levels import McpTokens


def test_a_caller_that_never_signed_in_is_refused_and_told_how_to_sign_in(keel):
    r = keel.client.get("/api/projects")
    assert r.status_code == 401
    assert r.headers["x-keel-auth"] == "sign-in"
    assert r.json() == {"error": SIGN_IN, "hint": HINT}
    assert keel.back.seen == []


def test_a_path_in_another_form_is_still_an_api_path(keel):
    for odd in ["/api;x/projects", "/api//projects", "/api/%2e%2e/api/projects", "/api/projects%2f1"]:
        r = keel.client.get(odd)
        assert r.status_code == 400, odd
        assert r.json()["error"] == "This path is written in an unusual way"
    # an encoded letter is an api path all the same: it needs a credential
    assert keel.client.get("/%61pi/projects").status_code == 401
    assert keel.back.seen == []


def test_health_the_web_app_and_the_sign_in_link_need_nothing(keel):
    assert keel.client.get("/api/health").json()["from"] == "kotlin"
    assert keel.client.get("/").status_code == 200
    assert keel.client.get("/flow/123").text.startswith("<!doctype html>")
    r = keel.client.get("/web/login?code=nope")
    assert r.status_code == 302 and r.headers["location"] == "/?signin=expired"
    assert keel.client.post("/api/devices/pair", json={}).json()["from"] == "kotlin"


def test_keel_only_answers_on_its_own_host_names(tmp_path):
    k = make_keel(tmp_path, allowed_hosts="keel.example")
    with k.client as c:
        assert c.get("/", headers={"Host": "evil.example"}).status_code == 403
        assert c.get("/", headers={"Host": "evil.example"}).json()["error"] == "This keel only answers on localhost"
        for ok in ["localhost:8080", "127.0.0.1:8080", "[::1]:8080", "192.168.1.20:8080", "keel.example", "a.localhost"]:
            assert c.get("/", headers={"Host": ok}).status_code == 200, ok
        (k.ctx.settings.data_dir / "run").mkdir(exist_ok=True)
        (k.ctx.settings.data_dir / "run" / "addresses.json").write_text('[{"url": "https://box.tail1.ts.net", "kind": "tailscale"}]')
        assert c.get("/", headers={"Host": "box.tail1.ts.net"}).status_code == 200


def test_a_one_time_code_signs_a_browser_in_once_with_a_strict_cookie(keel):
    code = keel.code()
    r = keel.client.get(f"/web/login?code={code}&next=/flow?x=1")
    assert r.status_code == 302 and r.headers["location"] == "/flow?x=1"
    cookie = r.headers["set-cookie"]
    name = "keel_" + sha256_hex(keel.ctx.key.value)[:8]
    assert cookie.startswith(f"{name}=ks_")
    assert "; Path=/; Max-Age=2592000; Expires=" in cookie and cookie.endswith("; HttpOnly; SameSite=Strict")
    again = keel.client.get(f"/web/login?code={code}")
    assert again.headers["location"] == "/?signin=expired"


def test_only_keel_itself_can_make_a_code(keel):
    sid = keel.sign_in()
    r = keel.client.post("/api/web/code", headers={**keel.cookie(sid), "X-Keel-Web": "1"})
    assert r.status_code == 403
    assert r.json() == {"error": "Only keel2 can make a sign-in code", "hint": "Run keel2 open in a terminal."}


def test_the_browser_reads_with_its_cookie_and_changes_only_from_keels_page(keel):
    sid = keel.sign_in()
    c = keel.cookie(sid)
    assert keel.client.get("/api/projects", headers=c).json()["from"] == "kotlin"
    r = keel.client.post("/api/projects", headers=c, json={})
    assert r.status_code == 403 and r.json()["error"] == "This change did not come from keel's own page"
    r = keel.client.post("/api/projects", headers={**c, "X-Keel-Web": "1", "Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"] == "This change came from another site (https://evil.example)"
    ok = keel.client.post("/api/projects", headers={**c, "X-Keel-Web": "1", "Origin": "http://localhost:8080"}, json={})
    assert ok.json()["from"] == "kotlin"
    assert keel.client.get("/api/web/me", headers=c).json() == {"signed_in": True, "via": "browser"}


def test_keel_never_answers_a_cors_preflight(keel):
    r = keel.client.options("/api/projects", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.status_code == 401
    assert "access-control-allow-origin" not in r.headers


def test_a_terminal_app_signs_in_with_the_web_key(keel):
    bearer = {"Authorization": f"Bearer {keel.ctx.key.value}"}
    assert keel.client.get("/api/web/me", headers=bearer).json() == {"signed_in": True, "via": "person"}
    r = keel.client.get("/api/projects", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 403 and r.json()["error"] == "Wrong sign-in key"
    r = keel.client.get("/api/projects", headers={"X-Keel-Token": "nope"})
    assert r.status_code == 403 and r.json()["error"] == "Wrong X-Keel-Token"


def test_a_cookie_or_the_web_key_never_lifts_an_mcp_call_above_its_level(keel):
    read = keel.ctx.mcp_tokens.token_for("read")
    r = keel.client.post("/api/web/sign-out", headers={"X-Keel-Mcp-Token": read})
    assert r.status_code == 403 and r.json()["error"] == "This needs the MCP admin level: run keel2 mcp --admin"
    sid = keel.sign_in()
    r = keel.client.post("/api/web/sign-out", headers={**keel.cookie(sid), "X-Keel-Web": "1", "X-Keel-Via": "mcp"})
    assert r.status_code == 403
    r = keel.client.get("/api/web/me", headers={"X-Keel-Mcp-Token": read})
    assert r.json() == {"signed_in": True, "via": "mcp"}
    assert keel.client.get("/api/projects", headers={"X-Keel-Mcp-Token": "nope"}).status_code == 403


def test_mcp_tokens_are_the_kotlin_apis_own(keel):
    want = hmac.new(TOKEN.encode(), b"keel-mcp:write", hashlib.sha256).hexdigest()
    assert McpTokens(TOKEN).token_for("write") == want
    assert McpTokens(TOKEN).level_of(want) == "write"


def test_signing_out_ends_this_session_and_clears_the_cookie(keel):
    sid = keel.sign_in()
    other = keel.sign_in()
    r = keel.client.post("/api/web/sign-out", headers={**keel.cookie(sid), "X-Keel-Web": "1"})
    assert r.json() == {"ok": True}
    assert "=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT" in r.headers["set-cookie"]
    assert keel.client.get("/api/projects", headers=keel.cookie(sid)).status_code == 401
    assert keel.client.get("/api/projects", headers=keel.cookie(other)).status_code == 200
    r = keel.client.post("/api/web/sign-out-everywhere", headers={**keel.cookie(other), "X-Keel-Web": "1"})
    assert r.json() == {"ok": True, "ended": 1}


def test_a_session_ends_30_days_after_its_last_use_and_each_use_moves_the_end(keel):
    s = keel.ctx.sessions
    t0 = now_ms()
    sid = s.open(now=t0, host="localhost:8080")
    assert s.check(sid, now=t0 + 60_000, host="localhost:8080") is Check.YES
    assert s.check(sid, now=t0 + 2 * 3600_000, host="localhost:8080") is Check.MOVED
    day = 24 * 3600_000
    assert s.check(sid, now=t0 + 30 * day + 3600_000, host="localhost:8080") is not Check.NO  # the end moved by two hours
    assert s.check(sid, now=t0 + 70 * day, host="localhost:8080") is Check.NO


def test_over_https_the_cookie_is_a_secure_host_cookie(keel):
    r = keel.client.get(f"/web/login?code={keel.code()}", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["set-cookie"].startswith(f"__Host-{keel.ctx.key.cookie_name}=ks_")
    assert "; Secure; HttpOnly; SameSite=Strict" in r.headers["set-cookie"]


def test_a_cookie_from_before_the_web_key_itself_is_swapped_for_a_session(keel):
    r = keel.client.get("/api/projects", headers=keel.cookie(keel.ctx.key.value))
    assert r.status_code == 200
    assert r.headers["set-cookie"].startswith(f"{keel.ctx.key.cookie_name}=ks_")


def test_a_session_works_only_on_the_host_and_port_it_was_opened_on(keel):
    sid = keel.sign_in()
    assert keel.client.get("/api/projects", headers={**keel.cookie(sid), "Host": "localhost:9090"}).status_code == 401
    assert keel.client.get("/api/projects", headers=keel.cookie(sid)).status_code == 200


def test_keels_page_has_a_csp_with_scripts_only_from_keel(keel):
    csp = keel.client.get("/").headers["content-security-policy"]
    assert "script-src 'self' 'sha256-" in csp and "frame-ancestors 'none'" in csp
    assert keel.client.get("/").headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in keel.client.get("/api/health").headers


def test_the_web_key_is_kept_in_a_file_only_its_owner_reads(keel):
    f = keel.ctx.key.file
    assert len(f.read_text().strip()) == 64
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_after_signing_in_keel_only_goes_to_its_own_pages():
    assert safe_next("/flow?x=1") == "/flow?x=1"
    for bad in ["//evil.com", "https://evil.com", "/%09/evil.com", "/\\evil.com", "/%2f%2fevil.com", "evil", "/%zz", None]:
        assert safe_next(bad) == "/", bad


def test_a_paired_phone_signs_in_with_its_device_token(keel):
    token = "kd_" + "a" * 64
    keel.ctx.db.update(
        "INSERT INTO devices (id, name, platform, token_hash, created_at) VALUES ('dev-1', 'Pixel', 'android', ?, '2026-01-01T00:00:00Z')",
        (sha256_hex(token),),
    )
    assert keel.client.get("/api/web/me", headers={"Authorization": f"Bearer {token}"}).json()["via"] == "device"
    assert keel.ctx.db.one("SELECT last_seen_at FROM devices WHERE id = 'dev-1'")[0] is not None
    r = keel.client.post("/internal/events", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    r = keel.client.get("/api/projects", headers={"Authorization": "Bearer kd_" + "b" * 64})
    assert r.status_code == 403 and r.json()["error"] == "This device was removed"


def test_wrong_tries_from_the_network_are_limited_and_a_right_key_still_passes(tmp_path):
    k = make_keel(tmp_path, client=("192.168.1.50", 40000))
    with k.client as c:
        for _ in range(20):
            assert c.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 403
        r = c.get("/api/projects", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 429 and r.headers["retry-after"] == "60"
        assert c.get("/api/projects", headers={"Authorization": f"Bearer {k.ctx.key.value}"}).status_code == 200


def test_a_direct_call_from_this_computer_is_never_limited(keel):
    for _ in range(25):
        assert keel.client.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 403
