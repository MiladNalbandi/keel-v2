from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from keel_api.app import Ctx, create_app, make_ctx
from keel_api.settings import Settings

# The Kotlin api's own schema: this api must work on the tables it made.
MIGRATIONS = Path(__file__).resolve().parents[2] / "api" / "src" / "main" / "resources" / "db" / "migration"
TOKEN = "test-token"
INDEX = '<!doctype html><html><head><script type="importmap">{"imports":{}}</script>' \
        '<script type="module" src="/assets/app.js"></script></head><body></body></html>'


def migrate(db_file: Path) -> None:
    files = sorted(MIGRATIONS.glob("V*__*.sql"), key=lambda p: int(p.name[1:].split("__")[0]))
    assert files, f"no migrations in {MIGRATIONS}"
    c = sqlite3.connect(db_file)
    for f in files:
        c.executescript(f.read_text())
    c.close()


@dataclass
class BackStub:
    """Stands in for the Kotlin api: records what reaches it and answers {"from": "kotlin"}."""

    seen: list[httpx.Request] = field(default_factory=list)
    down: bool = False
    answers: dict = field(default_factory=dict)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        await request.aread()
        self.seen.append(request)
        made = self.answers.get(request.url.path)
        r = made(request) if made is not None else httpx.Response(200, json={"from": "kotlin", "path": request.url.raw_path.decode()})
        if not r.is_stream_consumed:
            return r
        # a real server streams its answer; httpx reads a Response made from bytes at once, so stream it again
        body = r.content

        async def chunks():
            yield body

        return httpx.Response(r.status_code, headers=r.headers.multi_items(), content=chunks())


class EngineStub:
    """keel's engine for the routes of its plugin parts: records each request, answers what `answers` says."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.down = False
        self.answers: dict = {}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        await request.aread()
        self.seen.append(request)
        made = self.answers.get(request.url.path)
        return made(request) if made is not None else httpx.Response(200, json={"from": "engine", "path": request.url.path})


@dataclass
class Keel:
    client: TestClient
    ctx: Ctx
    back: BackStub
    engine: EngineStub

    def code(self) -> str:
        r = self.client.post("/api/web/code", headers={"X-Keel-Token": TOKEN})
        assert r.status_code == 200, r.text
        return r.json()["code"]

    def sign_in(self, client: TestClient | None = None) -> str:
        """A browser session cookie value."""
        c = client or self.client
        r = c.get(f"/web/login?code={self.code()}")
        assert r.status_code == 302, r.text
        return r.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]

    def cookie(self, value: str) -> dict[str, str]:
        return {"Cookie": f"{self.ctx.key.cookie_name}={value}"}


def make_keel(tmp_path: Path, client=("127.0.0.1", 50000), **extra) -> Keel:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    if not (data / "keel.db").exists():
        migrate(data / "keel.db")
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True, exist_ok=True)
    (web / "index.html").write_text(INDEX)
    (web / "assets" / "app.js").write_text("console.log(1)")
    opts = dict(data=data, internal_token=TOKEN, web_dir=str(web), public_url="", allowed_hosts="", dev_origins="",
                trusted_proxies="")
    opts.update(extra)
    back = BackStub()
    engine = EngineStub()
    ctx = make_ctx(Settings(**opts), httpx.MockTransport(back.handle), trusted_for_tests=set(),
                   engine_transport=httpx.MockTransport(engine.handle))
    tc = TestClient(create_app(ctx=ctx), base_url="http://localhost:8080", client=client, follow_redirects=False)
    return Keel(tc, ctx, back, engine)


@pytest.fixture
def keel(tmp_path):
    k = make_keel(tmp_path)
    with k.client:
        yield k
