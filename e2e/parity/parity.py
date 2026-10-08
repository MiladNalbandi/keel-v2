#!/usr/bin/env python3
"""Parity: is a new keel the same for people as a released one? (docs/plugins/11-step3-contract.md, "The parity e2e")

    python3 e2e/parity/parity.py --a ghcr.io/miladnalbandi/keel-v2:0.15.1 --b keel-v2:dev [--keep] [--only api,web]

It starts two throw-away keels on the same small fixture project and compares them:

  keel-parity-a  127.0.0.1:8094  volume keel-parity-a-data   the image --a (the released keel)
  keel-parity-b  127.0.0.1:8095  volume keel-parity-b-data   the image --b (the new keel)

  api  every read-only GET of the api (docs/CONTRACT.md and the controllers; nothing that needs a model, a token or
       changes state) on both: the same status and the same JSON shape (keys and value types, lists by their first
       element; not the values). An endpoint that only B has is "new in B" (not a failure).
  web  e2e/parity/pages.mjs (headless Chromium through npx playwright@1) reads the menu and opens every menu page on
       both: the same menu, the same headings, tabs and views, no console error and no failed request that A does
       not have too. On a difference it saves screenshots of A and B.

At the end one parity table; exit 1 on any difference that is not on the allow list (e2e/parity/allow.yml, each entry
with a reason). Without --keep both containers and volumes are removed at the end. It never touches another keel.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
KEELS = {"a": ("keel-parity-a", "keel-parity-a-data", 8094), "b": ("keel-parity-b", "keel-parity-b-data", 8095)}
PROJECT = "shop"
# a fixed time for the fixture's commits, so both keels (and every run) see the same history
GIT_DATE = "2026-01-15T10:00:00+00:00"


def sh(*args: str, check: bool = True, cwd: Path | None = None, env: dict | None = None) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, cwd=cwd, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({r.returncode}): {(r.stderr or r.stdout)[-1500:]}")
    return r.stdout + r.stderr


# ---------------------------------------------------------------- a keel


class Keel:
    def __init__(self, side: str, image: str):
        self.side, self.image = side, image
        self.name, self.volume, self.port = KEELS[side]
        self.url = f"http://127.0.0.1:{self.port}"
        self.pid = ""

    def http(self, method: str, path: str, body: object | None = None, timeout: float = 60) -> tuple[int, str, bytes]:
        """(status, content type, body) of one call; never raises for an http status."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"content-type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers.get("content-type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("content-type", ""), e.read()

    def stream(self, path: str) -> tuple[int, str]:
        """An event stream: only the status and the content type (it never ends)."""
        try:
            with urllib.request.urlopen(self.url + path, timeout=10) as r:
                return r.status, r.headers.get("content-type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("content-type", "")

    def api(self, method: str, path: str, body: object | None = None, ok: tuple[int, ...] = (200,)):
        code, kind, raw = self.http(method, "/api" + path, body)
        if code not in ok:
            raise AssertionError(f"{self.name}: {method} /api{path} → {code}: {raw[:400].decode(errors='replace')}")
        return json.loads(raw) if raw and "json" in kind else raw.decode(errors="replace")

    def start(self, ws: Path) -> None:
        self.remove()
        sh("docker", "run", "-d", "--name", self.name, "-p", f"127.0.0.1:{self.port}:8080",
           "-v", f"{self.volume}:/data", "-v", f"{ws}:/workspace", self.image)

    def remove(self) -> None:
        sh("docker", "rm", "-f", self.name, check=False)
        sh("docker", "volume", "rm", self.volume, check=False)

    def logs(self) -> str:
        return sh("docker", "logs", "--tail", "60", self.name, check=False)[-5000:]


def wait_until(what: str, fn, timeout: float = 240.0, every: float = 2.0):
    deadline, last = time.time() + timeout, None
    while time.time() < deadline:
        try:
            last = fn()
        except Exception as e:  # noqa: BLE001 - keel is starting; keep trying
            last = e
        if last and not isinstance(last, Exception):
            return last
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what} (last: {str(last)[:300]})")


def both(fn, keels: list[Keel]) -> None:
    """fn(keel) for both keels at the same time; the first error is raised."""
    errors: list[BaseException] = []

    def run(k: Keel) -> None:
        try:
            fn(k)
        except BaseException as e:  # noqa: BLE001 - handed to the main thread
            errors.append(e)
    threads = [threading.Thread(target=run, args=(k,)) for k in keels]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]


# ---------------------------------------------------------------- the fixture project


FIXTURE: dict[str, str] = {
    "README.md": "# shop\n\nA small web shop: a TypeScript web app and a Python api, one SQLite database.\n\n"
                 "Run the api with `python -m server.app`, the web app with `npm run dev`.\n",
    ".github/CODEOWNERS": "* @acme/shop\n/web/ @acme/web\n/server/ @acme/payments\n/db/ @acme/payments\n",
    ".gitignore": "node_modules/\n__pycache__/\n.venv/\n.codegraph/\ndata/\n",
    "package.json": json.dumps({"name": "shop-web", "version": "1.0.0", "private": True,
                                "scripts": {"dev": "vite", "build": "tsc && vite build", "test": "vitest run"},
                                "devDependencies": {"typescript": "^5.4.0", "vite": "^5.2.0", "vitest": "^1.6.0"}}, indent=2) + "\n",
    "tsconfig.json": json.dumps({"compilerOptions": {"target": "ES2022", "module": "ESNext", "strict": True}, "include": ["web"]}, indent=2) + "\n",
    "web/cart.ts": (
        "import { price, type Product } from \"./price\";\n\n"
        "export type Line = { product: Product; qty: number };\n\n"
        "/** A shopping cart: lines of products. */\n"
        "export class Cart {\n"
        "  private lines: Line[] = [];\n\n"
        "  add(product: Product, qty = 1): void {\n"
        "    const line = this.lines.find((l) => l.product.id === product.id);\n"
        "    if (line) line.qty += qty;\n"
        "    else this.lines.push({ product, qty });\n"
        "  }\n\n"
        "  total(): number {\n"
        "    return this.lines.reduce((sum, l) => sum + price(l.product) * l.qty, 0);\n"
        "  }\n"
        "}\n"),
    "web/price.ts": (
        "export type Product = { id: string; name: string; cents: number; vat: number };\n\n"
        "/** The price with VAT, in cents. */\n"
        "export function price(p: Product): number {\n"
        "  return Math.round(p.cents * (1 + p.vat));\n"
        "}\n\n"
        "export const format = (cents: number) => `€${(cents / 100).toFixed(2)}`;\n"),
    "web/checkout.ts": (
        "import { Cart } from \"./cart\";\n"
        "import { format } from \"./price\";\n\n"
        "/** Sends the cart to the api and returns the order id. */\n"
        "export async function checkout(cart: Cart): Promise<string> {\n"
        "  const res = await fetch(\"/api/orders\", { method: \"POST\", body: JSON.stringify({ total: cart.total() }) });\n"
        "  const { id } = await res.json();\n"
        "  console.log(`order ${id}: ${format(cart.total())}`);\n"
        "  return id;\n"
        "}\n"),
    "server/__init__.py": "",
    "server/app.py": (
        "\"\"\"The shop's api: orders and products.\"\"\"\n\n"
        "from server.orders import create_order, list_orders\n\n\n"
        "def handle(method: str, path: str, body: dict | None = None) -> dict:\n"
        "    if method == \"POST\" and path == \"/api/orders\":\n"
        "        return create_order(body or {})\n"
        "    if method == \"GET\" and path == \"/api/orders\":\n"
        "        return {\"orders\": list_orders()}\n"
        "    return {\"error\": \"not found\"}\n"),
    "server/orders.py": (
        "\"\"\"Orders: saved in SQLite (db/migrations).\"\"\"\n\n"
        "import sqlite3\n\n"
        "DB = \"shop.db\"\n\n\n"
        "def create_order(body: dict) -> dict:\n"
        "    with sqlite3.connect(DB) as c:\n"
        "        cur = c.execute(\"INSERT INTO orders(total_cents, status) VALUES (?, 'new')\", (int(body.get(\"total\", 0)),))\n"
        "        return {\"id\": cur.lastrowid}\n\n\n"
        "def list_orders() -> list[dict]:\n"
        "    with sqlite3.connect(DB) as c:\n"
        "        rows = c.execute(\"SELECT id, total_cents, status FROM orders ORDER BY id DESC\").fetchall()\n"
        "    return [{\"id\": r[0], \"total_cents\": r[1], \"status\": r[2]} for r in rows]\n"),
    "server/test_orders.py": (
        "from server.app import handle\n\n\n"
        "def test_unknown_path():\n"
        "    assert handle(\"GET\", \"/nope\") == {\"error\": \"not found\"}\n"),
    "db/migrations/V1__init.sql": (
        "CREATE TABLE customers (\n"
        "  id INTEGER PRIMARY KEY,\n"
        "  email TEXT NOT NULL UNIQUE,\n"
        "  name TEXT NOT NULL\n"
        ");\n\n"
        "CREATE TABLE products (\n"
        "  id TEXT PRIMARY KEY,\n"
        "  name TEXT NOT NULL,\n"
        "  cents INTEGER NOT NULL,\n"
        "  vat REAL NOT NULL DEFAULT 0.21\n"
        ");\n\n"
        "CREATE TABLE orders (\n"
        "  id INTEGER PRIMARY KEY,\n"
        "  customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,\n"
        "  total_cents INTEGER NOT NULL,\n"
        "  status TEXT NOT NULL\n"
        ");\n"),
    "db/migrations/V2__order_lines.sql": (
        "CREATE TABLE order_lines (\n"
        "  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,\n"
        "  product_id TEXT NOT NULL REFERENCES products(id),\n"
        "  qty INTEGER NOT NULL,\n"
        "  PRIMARY KEY (order_id, product_id)\n"
        ");\n\n"
        "CREATE INDEX order_lines_product ON order_lines(product_id);\n"),
    "api/openapi.yaml": (
        "openapi: 3.0.3\n"
        "info: { title: shop, version: 1.0.0 }\n"
        "paths:\n"
        "  /api/orders:\n"
        "    get:\n"
        "      summary: The orders, newest first\n"
        "      responses: { \"200\": { description: the orders } }\n"
        "    post:\n"
        "      summary: Place an order\n"
        "      responses: { \"200\": { description: the new order's id } }\n"
        "  /api/products/{id}:\n"
        "    get:\n"
        "      summary: One product\n"
        "      parameters: [{ name: id, in: path, required: true, schema: { type: string } }]\n"
        "      responses: { \"200\": { description: the product }, \"404\": { description: no such product } }\n"),
    "docs/knowledge/architecture.md": (
        "# Architecture\n\n"
        "The shop is two parts: the web app (`web/`, TypeScript) and the api (`server/`, Python).\n\n"
        "- The cart adds up prices with VAT (web/cart.ts:14).\n"
        "- Checkout posts the total to `POST /api/orders` (web/checkout.ts:5).\n"
        "- The api saves orders in SQLite (server/orders.py:8); the tables come from `db/migrations`.\n"),
    "docs/adr/0001-sqlite.md": "# 1. SQLite for orders\n\nWe keep orders in SQLite: one file, no server to run.\n",
    "docs/RUNNING.md": "# How to run the shop\n\n1. `npm install && npm run dev`\n2. `python -m server.app`\n",
}


def fixture(root: Path) -> Path:
    """The shop: a git repo with a web app and an api in two folders, SQL migrations, an OpenAPI file, CODEOWNERS,
    a README, a knowledge page and a decision. Two commits, a changed file and a new one (Code has history and changes
    to show), and a SQLite database next to it (ignored by git) for the Database part."""
    repo = root / PROJECT
    env = {**os.environ, "GIT_AUTHOR_DATE": GIT_DATE, "GIT_COMMITTER_DATE": GIT_DATE}

    def git(*args: str) -> None:
        sh("git", "-c", "user.name=parity", "-c", "user.email=parity@localhost", "-c", "commit.gpgsign=false", *args, cwd=repo, env=env)
    for rel, text in FIXTURE.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "The shop: web app, api, database")
    (repo / "web/price.ts").write_text(FIXTURE["web/price.ts"] + "\nexport const free = (p: { cents: number }) => p.cents === 0;\n")
    git("commit", "-q", "-am", "A free product costs nothing")
    (repo / "README.md").write_text(FIXTURE["README.md"] + "\nOrders live in SQLite.\n")
    (repo / "TODO.md").write_text("- refunds\n")
    (repo / "data").mkdir()
    with sqlite3.connect(repo / "data/shop.db") as db:
        for mig in sorted((repo / "db/migrations").glob("*.sql")):
            db.executescript(mig.read_text())
        db.execute("INSERT INTO products VALUES ('mug', 'Mug', 900, 0.21)")
    return repo


# ---------------------------------------------------------------- setup on both keels (the same calls on both)


def prepare(k: Keel) -> None:
    """keel is up and the shop is a project (added through the api), with its code graph index and its map."""
    wait_until(f"{k.name} ({k.image}) to be healthy", lambda: k.api("GET", "/health"), timeout=300)
    k.pid = k.api("POST", "/projects", {"root": f"/workspace/{PROJECT}"})["id"]

    def indexed():
        st = k.api("GET", f"/projects/{k.pid}/index")
        return st if st.get("status") in ("ready", "failed") else None
    for what, fn in (("the code graph index", indexed),
                     ("the map", lambda: "missing" not in k.api("GET", f"/projects/{k.pid}/map"))):
        try:
            wait_until(f"{k.name}: {what}", fn, timeout=180)
        except AssertionError as e:
            print(f"  ! {e}")


PLUGINS = ("ci", "db", "git", "review")


def plugins_on(k: Keel) -> None:
    """What a person does next: turn on the project's plugins (CI/CD, Database, Git, Code Review) and add the shop's
    SQLite database as a connection."""
    for name in PLUGINS:
        k.api("PUT", f"/projects/{k.pid}/plugins/{name}", {"enabled": True, "scope": "project"})
    k.api("POST", f"/projects/{k.pid}/db/connections", {"name": "shop", "url": "sqlite:data/shop.db", "env": "local"})


FAKE = {"provider": "fake", "mode": "api", "model": "fake"}
# the rounds, in order: as keel starts (plugins off), the plugins on, then a flow that waits at a gate
ROUNDS = ("off", "on", "flow")


def where(k: Keel) -> tuple:
    """Where the project's flow is: status, phase, step and what it waits for."""
    th = k.api("GET", f"/projects/{k.pid}/flow").get("thread") or {}
    w = th.get("waiting") or {}
    return th.get("status"), th.get("phase"), th.get("current"), w.get("kind"), w.get("title")


def run_flow(k: Keel) -> list[tuple]:
    """A person runs a flow: "change" with keel's fake model (no login, no token; it writes example files) on the
    shop. It waits at the scope gate, is approved as "small", runs the test-first loop and waits at the AC gate.
    Returns where it was at each stop."""
    k.api("PUT", "/settings/general", {"cheaper_model": FAKE})  # every agent on the fake model, the same on both
    k.api("POST", f"/projects/{k.pid}/flows", {"workflow_id": "change", "title": "Refunds for orders", "allow_fake": True,
                                               "allow_dirty": True, "acs": [{"id": "AC-1", "layer": "API", "title": "A refund marks the order refunded"}]})
    stops = [wait_until(f"{k.name}: the flow to stop at a gate", lambda: (s := where(k))[0] != "running" and s, timeout=300)]
    if stops[0][0] == "waiting":
        tid = k.api("GET", f"/projects/{k.pid}/flow")["thread"]["thread_id"]
        k.api("POST", f"/threads/{tid}/resume", {"decision": "approve", "payload": {"choice": "small"}})
        stops.append(wait_until(f"{k.name}: the flow to stop again",
                                lambda: (s := where(k))[0] != "running" and s != stops[0] and s, timeout=300))
    return stops


# ---------------------------------------------------------------- the allow list


def load_allow(paths: list[Path]) -> list[dict]:
    """allow.yml: a list of { what, why, detail? } (a small YAML subset: '- key: value' starts an entry, '  key: value'
    adds to it, '# ' starts a comment, values may be quoted)."""
    entries: list[dict] = []
    for path in paths:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            # a comment: a line that starts with #, or " # " after a value (#/graph in an id is not one)
            text = "" if line.lstrip().startswith("#") else re.split(r"\s#(?:\s|$)", line, maxsplit=1)[0].rstrip()
            if not text.strip():
                continue
            start = text.lstrip().startswith("- ")
            body = text.lstrip()[2:] if start else text.strip()
            if ":" not in body:
                raise SystemExit(f"{path}:{n}: expected 'key: value'")
            key, value = (x.strip() for x in body.split(":", 1))
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if start:
                entries.append({"file": path.name, "line": n, "used": 0})
            elif not entries:
                raise SystemExit(f"{path}:{n}: an entry starts with '- '")
            entries[-1][key] = value
    for e in entries:
        if not e.get("what") or not e.get("why"):
            raise SystemExit(f"{e['file']}:{e['line']}: each entry needs what and why")
    return entries


def allowed(entries: list[dict], ident: str, details: list[str]) -> str | None:
    """Why this difference is allowed, or None. The entries whose what (a glob) matches the id, with or without its
    [round], count: one without a detail allows all of it; else every line must match the detail (a glob) of one."""
    bare = re.sub(r" \[\w+\]$", "", ident)
    hits = [e for e in entries if fnmatch.fnmatchcase(ident, e["what"]) or fnmatch.fnmatchcase(bare, e["what"])]
    whole = next((e for e in hits if not e.get("detail")), None)
    if whole:
        whole["used"] += 1
        return whole["why"]
    used = []
    for line in details:
        e = next((e for e in hits if fnmatch.fnmatchcase(line, e["detail"])), None)
        if e is None:
            return None
        used.append(e)
    for e in {id(e): e for e in used}.values():
        e["used"] += 1
    return "; ".join(dict.fromkeys(e["why"] for e in used)) or None


# ---------------------------------------------------------------- api parity


# Every read-only GET of the api (docs/CONTRACT.md and the controllers). Nothing here needs a model or a token, and
# nothing changes state. {pid} the shop, {wid} a workflow, {sid} a skill, {sha} HEAD, {node} a code graph group.
# "stream:" = an event stream (only its status and content type). Those ending in "nope" ask for what does not exist:
# the error's status and shape must match too.
ENDPOINTS = [
    # keel
    "/api/health", "/api/features", "/api/providers/models", "/api/projects", "stream:/api/events?project={pid}",
    "/api/connections", "/api/github", "/api/gitlab", "/api/limits", "/api/usage/providers", "/api/settings/general",
    "/api/mcp-servers", "/api/notifications?limit=50", "/api/notification-settings", "/api/inbox", "/api/inbox/count",
    "/api/inbox?project={pid}", "/api/jobs?limit=50", "/api/jobs?project={pid}&status=done", "/api/quality",
    "/api/library", "/api/library?project={pid}", "/api/plugins", "/api/workflows/{wid}", "/api/workflows/{wid}/export",
    "/api/skills/{sid}", "/api/skills/{sid}?project={pid}",
    # added by the plugin track (steps 1 and 2): "new in B"
    "/api/approvals", "/api/plugin-host", "/api/connections/kinds", "/api/settings/sections", "/api/settings/plugins/git",
    "/api/projects/{pid}/settings/plugins/git",
    # the project
    "/api/projects/{pid}", "/api/projects/{pid}/index", "/api/projects/{pid}/flow", "/api/projects/{pid}/flows",
    "/api/projects/{pid}/runs", "/api/projects/{pid}/runs?workflow={wid}", "/api/projects/{pid}/estimate?workflow_id={wid}&acs=3",
    "/api/projects/{pid}/workflows", "/api/projects/{pid}/agents", "/api/projects/{pid}/skills", "/api/projects/{pid}/stacks",
    "/api/projects/{pid}/mcp-allow", "/api/projects/{pid}/budget", "/api/projects/{pid}/budget/now", "/api/projects/{pid}/caps",
    "/api/projects/{pid}/caps/left", "/api/projects/{pid}/settings", "/api/projects/{pid}/tasks", "/api/projects/{pid}/jira",
    "/api/projects/{pid}/mcp-catalog", "/api/projects/{pid}/plugins", "/api/projects/{pid}/hunts",
    "/api/projects/{pid}/keel-docs", "/api/projects/{pid}/memory",
    # Code (repo)
    "/api/projects/{pid}/repo", "/api/projects/{pid}/repo/tree?depth=4", "/api/projects/{pid}/repo/tree?dir=web&depth=1",
    "/api/projects/{pid}/repo/file?path=web/cart.ts", "/api/projects/{pid}/repo/file?path=README.md",
    "/api/projects/{pid}/repo/raw?path=README.md", "/api/projects/{pid}/repo/files", "/api/projects/{pid}/repo/search?q=price",
    "/api/projects/{pid}/repo/search?q=pri.e&regex=true&include=*.ts", "/api/projects/{pid}/repo/changes",
    "/api/projects/{pid}/repo/diff?path=README.md&against=head", "/api/projects/{pid}/repo/diff?path=web/price.ts&against=base",
    "/api/projects/{pid}/repo/diff?path=web/price.ts&sha={sha}", "/api/projects/{pid}/repo/commit?sha={sha}",
    "/api/projects/{pid}/repo/commits?limit=30", "/api/projects/{pid}/repo/commits?range=branch",
    "/api/projects/{pid}/repo/history?path=web/price.ts",
    # Map, Graph, Wiki
    "/api/projects/{pid}/map", "/api/projects/{pid}/graph", "/api/projects/{pid}/graph/search?q=Cart",
    "/api/projects/{pid}/graph/node?id={node}&depth=1", "/api/projects/{pid}/wiki", "/api/projects/{pid}/wiki/page?id=kb:architecture",
    "/api/projects/{pid}/wiki/page?id=kb:domain", "/api/projects/{pid}/wiki/page?id=wf:{wid}", "/api/projects/{pid}/wiki/page?id=runbook",
    "/api/projects/{pid}/wiki/page?id=adr:0001-sqlite.md",
    # KeelBot
    "/api/projects/{pid}/helper/sessions", "/api/projects/{pid}/helper/commands", "/api/projects/{pid}/helper/permissions",
    # the plugins (off at first, so 409; on in the second round)
    "/api/projects/{pid}/db/connections", "/api/projects/{pid}/db/suggest", "/api/projects/{pid}/db/schema",
    "/api/projects/{pid}/git/status", "/api/projects/{pid}/git/branches", "/api/projects/{pid}/git/branch?name=main",
    "/api/projects/{pid}/git/log", "/api/projects/{pid}/git/pr", "/api/projects/{pid}/ci/runs",
    "/api/projects/{pid}/review/prs", "/api/projects/{pid}/review/branch",
    # what does not exist
    "/api/nope", "/api/projects/nope", "/api/workflows/nope", "/api/skills/nope", "/api/jobs/nope", "/api/tasks/nope",
    "/api/quality/runs/nope", "/api/logins/nope", "/api/threads/nope/history", "/api/projects/{pid}/hunts/nope",
    "/api/projects/{pid}/helper/sessions/nope", "/api/projects/{pid}/wiki/page?id=nope",
    "/api/projects/{pid}/repo/file?path=../outside.txt", "/api/projects/{pid}/repo/search?q=(&regex=true",
    # the web app itself
    "/",
]

# In the flow round, besides the above: the flow's thread {tid} and its newest agent call {job}.
FLOW_ENDPOINTS = [
    "/api/projects/{pid}/flows/{tid}", "/api/threads/{tid}/history", "/api/jobs/{job}", "/api/jobs/{job}/steps?after=0",
    "/api/jobs?project={pid}&status=running", "/api/projects/{pid}/runs?limit=5",
]

ID_KEY = re.compile(r"^([0-9a-f]{7,64}|[0-9a-f-]{32,36}|\d+|\d{4}-\d{2}-\d{2}.*|[a-z]+:[0-9a-f]{16,64})$")


def variables(k: Keel) -> dict[str, str]:
    """Values for the endpoints' {names}, read from this keel."""
    v = {"pid": k.pid}
    for name, fn in (("wid", lambda: k.api("GET", f"/projects/{k.pid}/workflows")[0]["id"]),
                     ("sid", lambda: k.api("GET", f"/projects/{k.pid}/skills")[0]["id"]),
                     ("sha", lambda: k.api("GET", f"/projects/{k.pid}/repo/commits")[0]["sha"]),
                     ("node", lambda: k.api("GET", f"/projects/{k.pid}/graph")["groups"][0]["id"]),
                     ("tid", lambda: k.api("GET", f"/projects/{k.pid}/flow")["thread"]["thread_id"]),
                     ("job", lambda: k.api("GET", f"/jobs?project={k.pid}&limit=1")[0]["id"])):
        try:
            v[name] = fn()
        except Exception:  # noqa: BLE001 - this keel cannot give it; the other one may
            pass
    return v


def shape(v):
    """The JSON's shape: keys and value types, lists by their first element. Keys that are data (ids, shas, times,
    numbers) become <id>, so only their place counts."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return ["list", shape(v[0]) if v else None]
    out: dict = {}
    for key, value in v.items():
        out.setdefault("<id>" if ID_KEY.match(str(key)) else key, shape(value))
    return out


def kind(s) -> str:
    if isinstance(s, dict):
        return "an object"
    if isinstance(s, list):
        return "an empty list" if s[1] is None else "a list"
    return {"null": "null"}.get(s, f"a {s}")


def compare_shape(a, b, path: str = "$") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = [f"{path}.{key}: only in A" for key in a if key not in b]
        out += [f"{path}.{key}: only in B" for key in b if key not in a]
        for key in a:
            if key in b:
                out += compare_shape(a[key], b[key], f"{path}.{key}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if a[1] is None and b[1] is None:
            return []
        if a[1] is None or b[1] is None:
            return [f"{path}: {kind(a)} in A, {kind(b)} in B"]
        return compare_shape(a[1], b[1], path + "[0]")
    return [] if a == b else [f"{path}: {kind(a)} in A, {kind(b)} in B"]


def fetch(k: Keel, path: str) -> dict:
    if path.startswith("stream:"):
        code, ctype = k.stream(path[len("stream:"):])
        return {"status": code, "type": ctype.split(";")[0].strip(), "shape": None}
    code, ctype, raw = k.http("GET", path)
    ctype = ctype.split(";")[0].strip()
    body = None
    if "json" in ctype and raw:
        try:
            body = shape(json.loads(raw))
        except ValueError:
            ctype += " (not valid JSON)"
    return {"status": code, "type": ctype, "shape": body}


def api_parity(a: Keel, b: Keel, phase: str, endpoints: list[str]) -> list[dict]:
    """Every endpoint on both keels: same, different (and what differs) or new in B."""
    va, vb = variables(a), variables(b)
    rows = []
    for template in endpoints:
        names = re.findall(r"\{(\w+)\}", template)
        missing = [n for n in names if n not in va and n not in vb]
        ident = f"api GET {template.removeprefix('stream:')} [{phase}]"
        if missing:
            rows.append({"id": ident, "verdict": "skipped", "details": [f"no value for {', '.join(missing)} on either keel"]})
            continue
        # each keel with its own values (a flow's thread id differs), or the other's when it has none
        paths = []
        for own, other in ((va, vb), (vb, va)):
            path = template
            for n in names:
                path = path.replace("{" + n + "}", urllib.parse.quote(own.get(n) or other[n], safe=":/"))
            paths.append(path)
        ra, rb = fetch(a, paths[0]), fetch(b, paths[1])
        details: list[str] = []
        verdict = "same"
        # A has no such GET (404, or 405 when another method has the path) and B answers: new in B
        if ra["status"] in (404, 405) and rb["status"] < 400 and not template.endswith("nope"):
            verdict, details = "new", [f"{rb['status']} {rb['type']} in B, {ra['status']} in A"]
        elif ra["status"] != rb["status"]:
            verdict = "different"
            details = [f"status {ra['status']} in A, {rb['status']} in B" + (" (missing in B)" if rb["status"] == 404 else "")]
        elif ra["type"] != rb["type"]:
            verdict, details = "different", [f"content type {ra['type'] or '-'} in A, {rb['type'] or '-'} in B"]
        elif ra["shape"] is not None or rb["shape"] is not None:
            details = compare_shape(ra["shape"], rb["shape"])
            verdict = "different" if details else "same"
        rows.append({"id": ident, "verdict": verdict, "details": details})
    return rows


# ---------------------------------------------------------------- web parity


def web_parity(a: Keel, b: Keel, out: Path, phase: str) -> list[dict]:
    """e2e/parity/pages.mjs on both keels; its web.json becomes rows like the api's."""
    where = out / f"web-{phase}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)
    cmd = ["npx", "-y", "-p", "playwright@1", "node", str(HERE / "pages.mjs"), a.url, b.url, str(where)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    report = where / "web.json"
    if not report.exists():
        raise RuntimeError(f"pages.mjs did not finish ({r.returncode}): {(r.stderr or r.stdout)[-3000:]}")
    data = json.loads(report.read_text())
    rows = [{"id": f"web {item['what']} [{phase}]", "verdict": item["verdict"], "details": item["details"],
             "shots": item.get("shots") or [], "page": item["page"]} for item in data["items"]]
    rows += [{"id": f"web [{phase}] {note}", "verdict": "note", "details": []} for note in data.get("notes", [])]
    return rows


# ---------------------------------------------------------------- the table


def judge(rows: list[dict], allow: list[dict]) -> None:
    for row in rows:
        if row["verdict"] in ("different", "new"):
            why = allowed(allow, row["id"], row["details"])
            if why:
                row["allowed"] = why
                if row["verdict"] == "different":
                    row["verdict"] = "allowed"


def table(groups: list[tuple[str, list[dict]]], a: Keel, b: Keel, verbose: bool = False) -> int:
    print(f"\nparity   A = {a.image}\n         B = {b.image}\n")
    print(f"  {'':10} {'same':>6} {'different':>10} {'allowed':>8} {'new in B':>9} {'skipped':>8}")
    failures = 0
    rank = {"different": 3, "allowed": 2, "same": 1}
    for name, rows in groups:
        failures += sum(1 for r in rows if r["verdict"] == "different")
        # the web counts pages (and the menu, the frame, the launcher): a page is as bad as its worst difference
        units: dict[str, str] = {}
        for i, r in enumerate(rows):
            key = r.get("page") or str(i)
            if r["verdict"] in rank and rank[r["verdict"]] < rank.get(units.get(key, ""), 0):
                continue
            units[key] = r["verdict"]
        count = {v: sum(1 for u in units.values() if u == v) for v in ("same", "different", "allowed", "new", "skipped")}
        print(f"  {name:10} {count['same']:>6} {count['different']:>10} {count['allowed']:>8} {count['new']:>9} {count['skipped']:>8}")
    every = [r for _, rows in groups for r in rows]
    for title, verdict in (("DIFFERENT (not on the allow list)", "different"), ("allowed differences", "allowed"),
                           ("new in B", "new"), ("skipped", "skipped"), ("notes", "note")):
        hits = [r for r in every if r["verdict"] == verdict]
        if not hits:
            continue
        print(f"\n{title}:")
        for r in hits:
            why = f"   <- {r['allowed']}" if r.get("allowed") else ("   (not on the allow list)" if verdict == "new" else "")
            print(f"  {r['id']}{why}")
            # the details of what fails always; of what is allowed with --verbose
            if verdict in ("different", "skipped") or (verdict == "allowed" and verbose):
                for d in r["details"][:12]:
                    print(f"      {d}")
                if len(r["details"]) > 12:
                    print(f"      ... and {len(r['details']) - 12} more")
                for shot in r.get("shots", []):
                    print(f"      screenshot: {shot}")
    return failures


# ---------------------------------------------------------------- main


def lacks_programs(a: Keel, b: Keel) -> list[str]:
    """Programs A's image has and B's has not (an image built with INSTALL_CLIS=0 has no agent CLIs and no CodeGraph)."""
    def found(k: Keel) -> set[str]:
        try:
            have = {m["name"] for m in k.api("GET", "/connections")["machine"] if m.get("ok")}
            if k.api("GET", f"/projects/{k.pid}/index").get("available"):
                have.add("codegraph")
            return have
        except Exception:  # noqa: BLE001
            return set()
    return sorted(found(a) - found(b))


def main() -> int:
    ap = argparse.ArgumentParser(description="Is keel B the same for people as keel A?")
    ap.add_argument("--a", required=True, help="the released image, e.g. ghcr.io/miladnalbandi/keel-v2:0.15.1")
    ap.add_argument("--b", required=True, help="the new image")
    ap.add_argument("--only", default="api,web", help="api, web or both (the default)")
    ap.add_argument("--allow", action="append", default=[], help="another allow list, besides e2e/parity/allow.yml")
    ap.add_argument("--out", default=str(HERE / "out"), help="where web.json and the screenshots go")
    ap.add_argument("--keep", action="store_true", help="leave both keels running (8094, 8095) to look at them")
    ap.add_argument("--verbose", action="store_true", help="print what differs for the allowed differences too")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # progress shows at once, also in a log file
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    allow = load_allow([HERE / "allow.yml", *map(Path, args.allow)])
    out = Path(args.out).resolve()
    a, b = Keel("a", args.a), Keel("b", args.b)
    tmp = Path(tempfile.mkdtemp(prefix="keel-parity-"))
    started = time.time()
    groups: list[tuple[str, list[dict]]] = []
    try:
        print(f"fixture project in {tmp}")
        fixture(tmp / "src")
        for k in (a, b):  # each keel its own copy: keel writes into the project (.codegraph)
            shutil.copytree(tmp / "src", tmp / f"ws-{k.side}", symlinks=True)
            k.start(tmp / f"ws-{k.side}")
        print(f"started {a.name} ({a.image}) on {a.port} and {b.name} ({b.image}) on {b.port}")
        both(prepare, [a, b])
        lacks = lacks_programs(a, b)
        if lacks:
            print(f"  ! B's image lacks programs that A's has: {', '.join(lacks)} (built with INSTALL_CLIS=0?).\n"
                  f"    Compare like with like, or add --allow e2e/parity/allow-no-clis.yml")
        for phase in ROUNDS:
            endpoints = ENDPOINTS
            if phase == "on":
                print("turning on the plugins (CI/CD, Database, Git, Code Review) and adding the shop's database")
                both(plugins_on, [a, b])
            if phase == "flow":
                print("running a flow on both: change, fake model, approved at the scope gate, until the AC gate")
                stops: dict[str, list] = {}
                both(lambda k: stops.__setitem__(k.side, run_flow(k)), [a, b])
                endpoints = ENDPOINTS + FLOW_ENDPOINTS
                same = stops["a"] == stops["b"]
                groups.append(("flow", [{"id": "flow change: where it stopped", "verdict": "same" if same else "different",
                                         "details": [] if same else [f"A: {stops['a']}", f"B: {stops['b']}"]}]))
            if "api" in only:
                print(f"api [{phase}]: {len(endpoints)} endpoints")
                groups.append((f"api [{phase}]", api_parity(a, b, phase, endpoints)))
            if "web" in only:
                print(f"web [{phase}]: the menu and every page (headless Chromium)")
                groups.append((f"web [{phase}]", web_parity(a, b, out, phase)))
        for _, rows in groups:
            judge(rows, allow)
        failures = table(groups, a, b, args.verbose)
        unused = [e for e in allow if not e["used"]]
        if unused:
            print("\nallow list entries that matched nothing (remove them when the difference is gone for good):")
            for e in unused:
                print(f"  {e['file']}:{e['line']}  {e['what']}")
        print(f"\n{'FAILED' if failures else 'PASSED'}: {failures} difference(s) not on the allow list"
              f" ({(time.time() - started) / 60:.1f} min)")
        return 1 if failures else 0
    except Exception as e:  # noqa: BLE001
        print(f"\n{e}", file=sys.stderr)
        for k in (a, b):
            print(f"--- {k.name} logs\n{k.logs()}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            print(f"\nkept: {a.url} (A) and {b.url} (B); remove them with\n"
                  f"  docker rm -f {a.name} {b.name} && docker volume rm {a.volume} {b.volume}")
        else:
            for k in (a, b):
                k.remove()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

