"""The pass-through to the Kotlin api, the web app's files and the path reading."""

from __future__ import annotations

import httpx
from conftest import TOKEN, make_keel

from keel_api.common.api_path import ambiguous, clean

INTERNAL = {"X-Keel-Token": TOKEN}


def test_a_route_not_ported_yet_reaches_the_kotlin_api_as_it_came(keel):
    r = keel.client.put("/api/projects/p1/settings?x=1&y=%20z", headers={**INTERNAL, "X-Keel-Via": "tui"},
                        json={"a": 1})
    assert r.json()["from"] == "kotlin"
    sent = keel.back.seen[-1]
    assert sent.method == "PUT"
    assert sent.url.raw_path == b"/api/projects/p1/settings?x=1&y=%20z"
    assert sent.headers["host"] == "localhost:8080"
    assert sent.headers["x-keel-token"] == TOKEN and sent.headers["x-keel-via"] == "tui"
    assert sent.content == b'{"a":1}'
    # a direct call from this computer stays direct for the Kotlin api: no proxy header
    assert "x-forwarded-for" not in sent.headers


def test_the_kotlin_api_learns_the_real_client_and_never_a_forged_one(tmp_path):
    k = make_keel(tmp_path, client=("192.168.1.50", 40000))
    with k.client as c:
        c.get("/api/health", headers={"X-Forwarded-For": "10.0.0.9", "Cf-Connecting-Ip": "10.0.0.8"})
    sent = k.back.seen[-1]
    assert sent.headers["x-forwarded-for"] == "192.168.1.50"
    assert "cf-connecting-ip" not in sent.headers


def test_every_cookie_and_status_of_the_kotlin_answer_comes_back(keel):
    keel.back.answers["/api/health"] = lambda req: httpx.Response(
        409, headers=[("set-cookie", "a=1"), ("set-cookie", "b=2"), ("x-keel-thing", "y")], json={"error": "no", "hint": None})
    r = keel.client.get("/api/health")
    assert r.status_code == 409 and r.json() == {"error": "no", "hint": None}
    assert r.headers.get_list("set-cookie") == ["a=1", "b=2"]
    assert r.headers["x-keel-thing"] == "y"


def test_the_live_stream_is_passed_on_as_it_comes(keel):
    async def events():
        yield b": connected\n\n"
        yield b"event: notification\ndata: {}\n\n"

    keel.back.answers["/api/events"] = lambda req: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=events())
    with keel.client.stream("GET", "/api/events?project=*", headers=INTERNAL) as r:
        assert r.headers["content-type"] == "text/event-stream"
        assert b"".join(r.iter_bytes()) == b": connected\n\nevent: notification\ndata: {}\n\n"


def test_while_the_kotlin_api_is_down_keel_says_so(keel):
    keel.back.down = True
    r = keel.client.get("/api/health")
    assert r.status_code == 503 and r.json()["error"] == "keel's api is not ready yet"


def test_the_web_app_is_served_with_a_single_page_fallback(keel):
    assert keel.client.get("/").headers["cache-control"] == "no-cache"
    assert keel.client.get("/settings/access").text == keel.client.get("/").text
    a = keel.client.get("/assets/app.js")
    assert a.status_code == 200 and a.headers["cache-control"] == "max-age=31536000, public, immutable"
    for missing in ["/assets/gone.js", "/favicon.ico"]:
        r = keel.client.get(missing)
        assert r.status_code == 404 and r.json() == {"error": "Not found", "hint": missing}
    # never the web app: plugin web files and keel v1's old dashboard go to the Kotlin api
    assert keel.client.get("/plugins/tasks/1.0.0/web/index.js").json()["from"] == "kotlin"
    assert keel.client.get("/keel-v1/").json()["from"] == "kotlin"
    # a change on a web path is the Kotlin api's to answer (it says the method is not allowed)
    assert keel.client.post("/settings", json={}).json()["from"] == "kotlin"
    assert keel.back.seen[-1].url.path == "/settings"


def test_without_a_web_folder_the_kotlin_api_serves_the_web_app(tmp_path):
    k = make_keel(tmp_path, web_dir="")
    with k.client as c:
        assert c.get("/").json()["from"] == "kotlin"


def test_paths_are_read_as_spring_reads_them():
    assert clean("/api;x/projects") == "/api/projects"
    assert clean("/%61pi/projects") == "/api/projects"
    assert clean("/api//projects/./a/../b/") == "/api/projects/b/"
    assert clean("/a\\b") == "/a/b"
    assert clean("/%zz") == "/%zz"
    assert not ambiguous("/api/projects/p1")
    assert ambiguous("/api/projects/%2E")
    assert ambiguous("/api/./projects") and ambiguous("/api/../x") and ambiguous("/api;x")  # httpx cleans these before sending
