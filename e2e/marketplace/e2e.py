#!/usr/bin/env python3
"""The marketplace end to end (docs/plugins/13-step4-contract.md §11): find, install, update, roll back and remove a
signed plugin in a running keel, the refusals, and an agent that asks. Only on a throw-away keel: the container
keel-mkt on 127.0.0.1:8096 with the volume keel-mkt-data. It never touches another keel.

    python3 e2e/marketplace/e2e.py --image keel-v2:dev [--keep] [--no-web]

It needs Docker, python3 (no packages), uv (it runs tools/keel-plugin with `uv run`; --keel-plugin gives another
command) and, for the web part, Node with npx (headless Chromium through `npx -y -p playwright@1`).

  the catalog  a temp folder served from this computer by python's http.server (a thread, a free port); keel reaches it
               at http://host.docker.internal:<port> (KEEL_MARKETPLACE_ALLOW_HTTP=1, tests only). Test keys made with
               `keel-plugin keygen`: catalog (signs the index), publisher (signs the packages), other (a wrong key)
  hello        e2e/marketplace/fixtures/hello: a workflow and an agent, a web page "Hello", an engine route /hello/ping;
               packed with `keel-plugin pack` and signed with `keel-plugin sign` as 1.0.0, 1.1.0 and 1.2.0 (1.2.0 asks
               for one more permission); `keel-plugin index` builds and signs v1/index.json
  keel-mkt     the image under test with the fake model (KEEL_FAKE=1), a small git project at /workspace/hello-repo

The steps: the catalog as a source; needs_plugins (a workflow that needs hello refuses to start and asks); search;
install 1.0.0, restart keel (in the same container), hello loaded (its engine route, web part, workflow and agent);
update to 1.1.0; update to 1.2.0 refused (more permissions) and asked in the Inbox: denied, asked again, the web
(Control › Plugins and the Inbox card), approved; roll back; remove; the refusals (a bad index signature, a bad package
signature, a wrong sha256, an old index, a revoked version); an agent asks through keel's MCP server, a person approves
in the Inbox, keel restarts and hello is loaded; with the rule agents_may_ask off the agent is told to ask the person.
Without --keep the container, the volume and the temp folder are removed at the end.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import os
import platform
import queue
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

NAME, VOLUME, PORT = "keel-mkt", "keel-mkt-data", 8096
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "hello"
PLUGIN, TITLE = "hello", "Hello"
SOURCE_ID, SOURCE_TITLE = "e2e", "e2e catalog"
PERMS = {"1.0.0": "  workspace: read\n", "1.1.0": "  workspace: read\n",
         "1.2.0": "  workspace: read\n  network:\n    - api.example.com\n"}
MORE = "+ network: api.example.com"
ASK_IN_PLUGINS = "Ask the person to install it in Control › Plugins"
MARKET_TOOLS = ["keel_marketplace_search", "keel_plugin_info", "keel_plugins_installed", "keel_plugin_request"]
CHECKS: list[str] = []
TOKEN = secrets.token_hex(16)   # keel's internal token in this throw-away keel: the test calls the engine route with it
KP: list[str] = []


def sh(*args: str, check: bool = True, cwd: Path | None = None, env: dict | None = None) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, cwd=cwd, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:6])} … failed ({r.returncode}): {(r.stderr or r.stdout)[-1500:]}")
    return r.stdout + r.stderr


def http(method: str, path: str, body: object | None = None, timeout: float = 60) -> tuple[int, dict, str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method,
                                 headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode(errors="replace")


def call(method: str, path: str, body: object | None = None, timeout: float = 60):
    """(status, the JSON answer or the text) of one api call; never raises for a status."""
    code, headers, text = http(method, "/api" + path, body, timeout)
    try:
        return code, json.loads(text) if text and "json" in headers.get("content-type", "") else text
    except ValueError:
        return code, text


def api(method: str, path: str, body: object | None = None, ok: tuple[int, ...] = (200,), timeout: float = 60):
    code, res = call(method, path, body, timeout)
    if code not in ok:
        raise AssertionError(f"{method} /api{path} → {code}: {str(res)[:600]}")
    return res


def check(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError("FAILED: " + what)
    CHECKS.append(what)
    print("  ✓", what, flush=True)


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


def kp(*args: str, cwd: Path | None = None) -> str:
    """tools/keel-plugin (its tests run in CI); KEEL_MARKETPLACE_ALLOW_HTTP=1 lets its index name http download urls."""
    return sh(*KP, *args, cwd=cwd, env={**os.environ, "KEEL_MARKETPLACE_ALLOW_HTTP": "1"})


# ---------------------------------------------------------------- the throw-away keel


def exists(kind: str, name: str) -> bool:
    fmt = ["ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"] if kind == "container" else \
        ["volume", "ls", "--filter", f"name=^{name}$", "--format", "{{.Name}}"]
    return name in sh("docker", *fmt, check=False).split()


def wait_for_the_name(minutes: float) -> None:
    """keel-mkt is a fixed name: when a container of that name is there (another run of this e2e, or a --keep), wait
    until it is gone; it is never removed here. The volume alone, with no container using it, is a leftover of an
    earlier run: it goes."""
    deadline = time.time() + minutes * 60
    while exists("container", NAME):
        if time.time() > deadline:
            raise SystemExit(f"{NAME} is still there after {minutes:.0f} minutes: remove it (docker rm -f {NAME}) "
                             "when nobody uses it, then run again")
        print(f"{NAME} exists (another run?): waiting for it to go …", flush=True)
        time.sleep(15)
    if exists("volume", VOLUME) and not sh("docker", "ps", "-a", "--filter", f"volume={VOLUME}", "-q", check=False).strip():
        sh("docker", "volume", "rm", VOLUME, check=False)


def start(image: str, ws: Path) -> None:
    args = ["docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data",
            "-v", f"{ws}:/workspace", "-e", "KEEL_MARKETPLACE_ALLOW_HTTP=1", "-e", "KEEL_FAKE=1",
            "-e", "KEEL_FAKEONREALPROJECTS=true", "-e", f"KEEL_INTERNAL_TOKEN={TOKEN}"]
    if platform.system() == "Linux":   # Docker Desktop has host.docker.internal; Linux needs it named
        args.append("--add-host=host.docker.internal:host-gateway")
    sh(*args, image)
    wait_until(f"{NAME} ({image}) to be healthy", lambda: api("GET", "/health"), timeout=300)


def clean() -> None:
    sh("docker", "rm", "-f", NAME, check=False)
    sh("docker", "volume", "rm", VOLUME, check=False)


def installed() -> dict:
    return api("GET", "/plugins/installed")


def mine(view: dict | None = None) -> dict | None:
    """hello in GET /api/plugins/installed, or None."""
    return next((p for p in (view or installed())["plugins"] if p["name"] == PLUGIN), None)


def container() -> tuple[str, str, str]:
    """(status, restart count, started at) of keel-mkt."""
    out = sh("docker", "inspect", "-f", "{{.State.Status}} {{.RestartCount}} {{.State.StartedAt}}", NAME).split()
    return out[0], out[1], out[2]


def restart_and_wait(why: str) -> None:
    """POST /api/plugins/restart {now: true}: the api ends with code 75 and keel-start starts keel again, in the same
    container. keel is back when it answers and nothing waits for a restart any more."""
    before = container()
    res = api("POST", "/plugins/restart", {"now": True}, ok=(202,))
    check(res.get("restarting") is True, f"Restart keel ({why}): the api says it restarts")
    time.sleep(3)
    wait_until("keel to come back after the restart",
               lambda: api("GET", "/health") and installed()["restart"]["pending"] is False, timeout=300)
    after = container()
    check(after[0] == "running" and after[1] == "0" and after[2] == before[2],
          "keel is back in the same container (no docker restart): nothing waits for a restart now")


def engine_get(path: str) -> tuple[int, object]:
    """An engine route inside the container (127.0.0.1:8090, keel's internal token), as the api calls it."""
    out = sh("docker", "exec", NAME, "curl", "-s", "-w", "\n%{http_code}", "-H", f"X-Keel-Token: {TOKEN}",
             f"http://127.0.0.1:8090{path}", check=False).rstrip("\n")
    text, _, code = out.rpartition("\n")
    try:
        return int(code), json.loads(text) if text.strip() else None
    except ValueError:
        return int(code) if code.isdigit() else 0, text


def host_plugins() -> dict[str, str]:
    """name → version of what keel's plugin host loaded at its last start."""
    return {p["name"]: p["version"] for p in api("GET", "/plugin-host")["plugins"]}


def waiting(kind: str = "plugin-install") -> list[dict]:
    return [a for a in api("GET", "/approvals?status=waiting") if a.get("kind") == kind]


def decide(aid: str, decision: str) -> dict:
    return api("POST", f"/approvals/{aid}/decide", {"decision": decision}, timeout=300)


# ---------------------------------------------------------------- keel's MCP server (what agents and keel2 mcp use)


class Mcp:
    """keel's MCP server inside the container over stdio (python -m keel_engine.mcp, read-only: what agents get)."""

    def __init__(self):
        args = ["docker", "exec", "-i", NAME, "/opt/engine/.venv/bin/python", "-m", "keel_engine.mcp"]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(x) for x in self.proc.stdout], daemon=True).start()
        self.n = 0
        self.ask("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "marketplace-e2e", "version": "1"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def ask(self, method: str, params: dict, timeout: float = 120) -> dict:
        self.n += 1
        self.send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self.lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise AssertionError(f"keel's MCP server: {method} failed: {msg['error']}")
                return msg.get("result") or {}
        raise AssertionError(f"keel's MCP server gave no answer to {method}")

    def tools(self) -> list[str]:
        return [t["name"] for t in self.ask("tools/list", {}).get("tools", [])]

    def tool(self, name: str, arguments: dict) -> str:
        res = self.ask("tools/call", {"name": name, "arguments": arguments}, timeout=300)
        return "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def mcp(*calls: tuple[str, dict]) -> tuple[list[str], list[str]]:
    """keel's MCP server: its tools, and each call's answer (one server for all of them)."""
    m = Mcp()
    try:
        return m.tools(), [m.tool(name, args) for name, args in calls]
    finally:
        m.close()


# ---------------------------------------------------------------- the test catalog (served from this computer)


class Quiet(SimpleHTTPRequestHandler):
    served: list[str] = []

    def log_message(self, *args) -> None:  # noqa: D102 - no access log in the e2e's output
        pass

    def do_GET(self) -> None:  # noqa: N802
        Quiet.served.append(self.path)
        super().do_GET()


class Catalog:
    """A marketplace folder (publishers/, plugins/, revoked.yml), test keys, the hello packages and the served folder
    (v1/index.json and its .minisig, files/<package> and its .minisig)."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.keys = tmp / "keys"
        self.mkt = tmp / "marketplace"
        self.served = tmp / "catalog"
        self.files = self.served / "files"
        self.v1 = self.served / "v1"
        for d in (self.mkt / "publishers", self.mkt / "plugins", self.files, self.v1):
            d.mkdir(parents=True, exist_ok=True)
        handler = functools.partial(Quiet, directory=str(self.served))
        self.server = ThreadingHTTPServer(("0.0.0.0", 0), handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://host.docker.internal:{self.port}"
        self.url = f"{self.base}/v1/index.json"

    def keygen(self) -> None:
        for name in ("catalog", "publisher", "other"):
            kp("keygen", name, "--dir", str(self.keys))
        self.catalog_pub = self.pub("catalog")

    def pub(self, name: str) -> str:
        return (self.keys / f"{name}.pub").read_text().splitlines()[1].strip()

    def listing(self) -> None:
        (self.mkt / "publishers" / "e2e.yml").write_text(
            f"name: e2e\ntitle: keel e2e\nkeys:\n  - {self.pub('publisher')}\nverified: true\n")
        (self.mkt / "plugins" / f"{PLUGIN}.yml").write_text(
            f"name: {PLUGIN}\ntitle: {TITLE}\npublisher: e2e\nrepo: https://github.com/keel-studio/keel-plugin-hello\n"
            "category: other\nsummary: A test plugin of the marketplace e2e - a workflow, an agent, a page and one "
            "engine route.\ntags:\n  - test\n  - e2e\ntrust: code\n")

    def package(self, version: str) -> Path:
        """hello <version>: the fixture with this version (and its permissions), packed, linted and signed."""
        src = self.tmp / f"src-{version}"
        shutil.copytree(FIXTURE, src, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        m = src / "keel-plugin.yml"
        text = re.sub(r"^version: .*$", f"version: {version}", m.read_text(), flags=re.M)
        text = re.sub(r"^permissions:\n(?:  .*\n)+", "permissions:\n" + PERMS[version], text, flags=re.M)
        m.write_text(text)
        init = src / "engine" / "keel_plugin_hello" / "__init__.py"
        init.write_text(init.read_text().replace('VERSION = "1.0.0"', f'VERSION = "{version}"'))
        kp("pack", str(src), "--out", str(self.files))
        file = self.files / f"{PLUGIN}-{version}.kplug"
        lint = kp("lint", str(file))
        if "ok: no problems found" not in lint:
            raise AssertionError(f"keel-plugin lint {file.name}: {lint}")
        kp("sign", str(file), "--key-file", str(self.keys / "publisher.key"))
        (self.files / f"{file.name}.json").write_text(json.dumps(
            {"url": f"{self.base}/files/{file.name}", "released": dt.date.today().isoformat()}))
        return file

    def index(self, days: int = 14, revoked: tuple[tuple[str, str], ...] = ()) -> dict:
        """keel-plugin index: v1/index.json from the marketplace files and the packages, signed with the catalog key."""
        lines = "".join(f"  - name: {n}\n    version: \"{v}\"\n    why: the e2e revokes it\n" for n, v in revoked)
        (self.mkt / "revoked.yml").write_text("revoked:\n" + lines if lines else "revoked: []\n")
        time.sleep(1.1)   # each index is built later than the one before (keel refuses an older one)
        kp("index", str(self.mkt), "--releases", str(self.files), "--key-file", str(self.keys / "catalog.key"),
           "--out", str(self.v1), "--days", str(days))
        return json.loads((self.v1 / "index.json").read_text())

    def stop(self) -> None:
        self.server.shutdown()


def refresh() -> dict:
    """Read the test catalog now: its status in Sources."""
    res = api("POST", f"/marketplace/refresh?source={SOURCE_ID}", timeout=120)
    return next(s for s in res["sources"] if s["id"] == SOURCE_ID)


def hit() -> dict | None:
    res = api("GET", f"/marketplace?q={PLUGIN}")
    return next((h for h in res["plugins"] if h["name"] == PLUGIN), None)


# ---------------------------------------------------------------- the steps


def repo(ws: Path) -> None:
    p = ws / "hello-repo"
    p.mkdir(parents=True)
    (p / "README.md").write_text("# hello-repo\n\nThe marketplace e2e's project.\n")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "first"]):
        sh("git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false", *args, cwd=p)


def setup(cat: Catalog) -> str:
    print("1. the test catalog: keys, hello 1.0.0 / 1.1.0 / 1.2.0, the signed index, a source of keel")
    cat.keygen()
    cat.listing()
    for v in PERMS:
        cat.package(v)
    index = cat.index()
    versions = [v["version"] for p in index["plugins"] for v in p["versions"]]
    check(versions == ["1.2.0", "1.1.0", "1.0.0"] and (cat.v1 / "index.json.minisig").is_file(),
          "keel-plugin packed and signed hello 1.0.0, 1.1.0 and 1.2.0, and built and signed the index")
    pid = api("POST", "/projects", {"root": "/workspace/hello-repo"})["id"]
    code, _ = engine_get("/hello/ping")
    check(code == 404 and PLUGIN not in host_plugins(), "before the install keel has no hello (its engine route is a 404)")
    res = api("PUT", "/plugins/sources", {"sources": [{"id": SOURCE_ID, "title": SOURCE_TITLE, "url": cat.url,
                                                        "key": cat.catalog_pub}]})
    check([s["id"] for s in res["sources"]] == ["keel", SOURCE_ID], "PUT /api/plugins/sources adds the test catalog")
    src = refresh()
    check(src["ok"] and src["plugins"] == 1 and not src["problem"] and "/v1/index.json.minisig" in Quiet.served,
          "keel read the catalog and its signature from the host: it verifies with the source's key, 1 plugin")
    return pid


def needs_plugins(pid: str) -> None:
    print("2. needs_plugins: a workflow that needs hello, while hello is not loaded")
    yaml = ("name: needs hello\nkeel_rules: false\nneeds_plugins: [hello]\nsteps:\n"
            "  - { id: wait, kind: gate, name: wait here, phase: review }\n")
    wf = api("POST", f"/projects/{pid}/workflows/import", {"yaml": yaml})["workflow"]
    code, res = call("POST", f"/projects/{pid}/flows", {"workflow_id": wf["id"], "title": "Needs hello",
                                                        "allow_fake": True, "allow_dirty": True}, timeout=120)
    check(code == 409 and res.get("missing") == [PLUGIN] and len(res.get("requests") or []) == 1,
          f"starting it answers 409 with missing [hello] and a request ({str(res.get('error'))[:60]})")
    rid = res["requests"][0]
    req = next((a for a in waiting() if a["id"] == rid), None)
    check(req is not None and req["payload"]["name"] == PLUGIN and req["payload"]["source"] == "workflow"
          and req["title"].startswith(f"Install {TITLE}"), f"the request waits in the Inbox: \"{req and req['title']}\"")
    check(decide(rid, "deny")["status"] == "denied" and mine() is None,
          "denied: the request ends and nothing is installed")


def search() -> None:
    print("3. search")
    h = hit()
    check(h is not None and h["version"] == "1.2.0" and h["trust"] == "code" and h["installed"] is None
          and h["source"] == SOURCE_ID and h["fits"], "search finds hello: trust code, newest 1.2.0, not installed")
    one = api("GET", f"/marketplace/{PLUGIN}")
    check([v["version"] for v in one["versions"]] == ["1.2.0", "1.1.0", "1.0.0"] and one["plan"]["install"][0]["name"] == PLUGIN
          and one["checks"], "the plugin's page lists its three versions, the install plan and the checks keel runs")
    tools, (found,) = mcp(("keel_marketplace_search", {"q": PLUGIN}))
    check(tools[-4:] == MARKET_TOOLS, "keel's MCP server lists the four marketplace tools last")
    check(found.startswith(f"{PLUGIN}  {TITLE}") or f"\n{PLUGIN}  {TITLE}" in found,
          "keel_marketplace_search finds hello for an agent")


def loaded_checks(pid: str, version: str) -> None:
    check(host_plugins().get(PLUGIN) == version, f"/api/plugin-host lists hello {version}")
    p = mine()
    check(p and p["status"] == "loaded" and p["from"] == "marketplace" and p["version"] == version,
          f"Installed: hello {version}, from the marketplace, loaded")
    code, body = engine_get("/hello/ping")
    check(code == 200 and body == {"pong": "hello", "version": version}, f"its engine route /hello/ping answers ({version})")
    web = next((x["web"] for x in api("GET", "/features").get("plugins") or [] if x["name"] == PLUGIN), None)
    code, headers, js = http("GET", web["entry"]) if web else (0, {}, "")
    check(code == 200 and "javascript" in headers.get("content-type", "") and "definePlugin" in js
          and f"/{version}/" in web["entry"], f"its web part is served ({web and web['entry']})")
    code, addons = engine_get("/addons")
    mine_addon = next((x for x in (addons or {}).get("addons", []) if x["name"] == PLUGIN), {}) if code == 200 else {}
    check(mine_addon.get("version") == version and mine_addon.get("workflows") == ["hello-flow"]
          and mine_addon.get("agents") == ["hello-agent"], "the engine loaded its content: the workflow and the agent")
    wfs = api("GET", f"/projects/{pid}/workflows")
    check("hello-flow" in {w["id"] for w in wfs}, "its workflow hello-flow is listed in the project's workflows")


def install(pid: str, cat: Catalog) -> None:
    print("4. install 1.0.0, restart, loaded")
    Quiet.served.clear()
    res = api("POST", "/plugins/install", {"name": PLUGIN, "version": "1.0.0"}, timeout=300)
    check(res["version"] == "1.0.0" and [i["name"] for i in res["installed"]] == [PLUGIN]
          and res["pending_restart"]["pending"], "POST /api/plugins/install installs hello 1.0.0; it waits for a restart")
    check({"/files/hello-1.0.0.kplug", "/files/hello-1.0.0.kplug.minisig"} <= set(Quiet.served),
          "keel downloaded the package and its signature from the catalog")
    view = installed()
    p = mine(view)
    check(p["status"] == "restart" and p["permissions"] == {"workspace": "read"} and view["restart"]["pending"],
          "Installed: hello waits for a restart, with its permissions; the restart banner is due")
    restart_and_wait("after the install")
    loaded_checks(pid, "1.0.0")


def update(pid: str, web: bool, out: Path) -> None:
    print("5. update to 1.1.0; 1.2.0 asks for more permissions")
    res = api("POST", f"/plugins/{PLUGIN}/update", {"version": "1.1.0"}, timeout=300)
    p = mine()
    check(res["version"] == "1.1.0" and p["version"] == "1.1.0" and p["previous"] == "1.0.0" and p["status"] == "restart",
          "update to 1.1.0: installed, 1.0.0 kept for roll back, it waits for a restart")
    code, res = call("POST", f"/plugins/{PLUGIN}/update", {"version": "1.2.0"}, timeout=300)
    check(code == 409 and MORE in (res.get("more") or []) and len(res.get("requests") or []) == 1,
          f"update to 1.2.0 is refused: it asks for more permissions ({MORE})")
    first = res["requests"][0]
    req = next((a for a in waiting() if a["id"] == first), None)
    check(req is not None and req["title"] == f"Update {TITLE} to 1.2.0?" and req["payload"].get("update") is True
          and MORE in (req["payload"].get("more") or []), f"it became an Inbox request: \"{req and req['title']}\"")
    check(decide(first, "deny")["status"] == "denied" and mine()["version"] == "1.1.0",
          "denied: hello stays 1.1.0")
    code, res = call("POST", f"/plugins/{PLUGIN}/update", {"version": "1.2.0"}, timeout=300)
    second = (res.get("requests") or [None])[0] if isinstance(res, dict) else None
    check(code == 409 and second and second != first, "asked again: a new request waits")
    if web:
        web_checks(out)
    check(decide(second, "approve")["status"] == "approved", "a person approves the new permissions in the Inbox")
    p = mine()
    check(p["version"] == "1.2.0" and p["permissions"].get("network") == ["api.example.com"] and p["previous"] == "1.1.0",
          "hello 1.2.0 is installed with the approved permission; 1.1.0 kept for roll back")


def web_checks(out: Path) -> None:
    print("   the web: Control › Plugins and the Inbox (headless Chromium)")
    cmd = ["npx", "-y", "-p", "playwright@1", "node", str(HERE / "web.mjs"), f"http://127.0.0.1:{PORT}", str(out),
           PLUGIN, SOURCE_TITLE]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    line = next((x for x in reversed(r.stdout.splitlines()) if x.startswith("{")), "")
    if not line:
        raise AssertionError(f"web.mjs gave no answer ({r.returncode}): {(r.stderr or r.stdout)[-2000:]}")
    res = json.loads(line)
    for c in res["checks"]:
        check(c["ok"], c["what"] + (f" — {c['detail']}" if not c["ok"] else ""))
    check(not res["console"], "no console errors" + (f": {res['console'][:3]}" if res["console"] else ""))


def rollback(pid: str) -> None:
    print("6. roll back")
    res = api("POST", f"/plugins/{PLUGIN}/rollback", timeout=120)
    check(res["version"] == "1.1.0" and res["from"] == "1.2.0" and mine()["version"] == "1.1.0",
          "roll back: from 1.2.0 to the kept 1.1.0")
    restart_and_wait("after the roll back")
    code, body = engine_get("/hello/ping")
    check(host_plugins().get(PLUGIN) == "1.1.0" and code == 200 and body.get("version") == "1.1.0",
          "after the restart keel runs hello 1.1.0")


def remove(pid: str) -> None:
    print("7. remove")
    res = api("DELETE", f"/plugins/{PLUGIN}?data=delete", timeout=120)
    p = mine()
    check(res["removed"] == "1.1.0" and res["data"] == "delete" and p and p["status"] == "removed",
          "remove: hello runs until the restart (status removed)")
    restart_and_wait("after the removal")
    code, _ = engine_get("/hello/ping")
    wfs = {w["id"] for w in api("GET", f"/projects/{pid}/workflows")}
    check(PLUGIN not in host_plugins() and mine() is None and code == 404 and "hello-flow" not in wfs,
          "after the restart hello is gone: no plugin, no route, no workflow")
    check("hello" not in sh("docker", "exec", NAME, "ls", "/data/plugins/store", check=False).split(),
          "its files are gone from /data/plugins/store")


def refusals(cat: Catalog) -> None:
    print("8. refusals")
    index, sig = (cat.v1 / "index.json").read_bytes(), (cat.v1 / "index.json.minisig").read_bytes()
    # a changed index with the old signature
    (cat.v1 / "index.json").write_bytes(index.replace(b"A test plugin", b"A changed plugin", 1))
    src = refresh()
    check(not src["ok"] and "signature" in (src["problem"] or ""),
          f"an index whose signature does not match is refused: \"{(src['problem'] or '')[:70]}\"")
    h = hit()
    check(h is not None and "A test plugin" in h["summary"], "keel keeps its last good copy: search still finds hello")
    (cat.v1 / "index.json").write_bytes(index)
    (cat.v1 / "index.json.minisig").write_bytes(sig)
    check(refresh()["ok"], "the good index reads again")

    pkg = cat.files / f"{PLUGIN}-1.0.0.kplug"
    good_pkg, good_sig = pkg.read_bytes(), Path(f"{pkg}.minisig").read_bytes()
    # the package signed by another key
    kp("sign", str(pkg), "--key-file", str(cat.keys / "other.key"))
    code, res = call("POST", "/plugins/install", {"name": PLUGIN, "version": "1.0.0"}, timeout=300)
    check(code == 502 and "signature" in res.get("error", "") and mine() is None,
          f"a package signed by another key is refused (502): \"{res.get('error', '')[:70]}\"")
    Path(f"{pkg}.minisig").write_bytes(good_sig)
    # one byte changed: the sha256 is not the catalog's
    bad = bytearray(good_pkg)
    bad[len(bad) // 2] ^= 0xFF
    pkg.write_bytes(bytes(bad))
    code, res = call("POST", "/plugins/install", {"name": PLUGIN, "version": "1.0.0"}, timeout=300)
    check(code == 502 and "sha256" in res.get("error", "") and mine() is None,
          f"a package whose sha256 is not the catalog's is refused (502): \"{res.get('error', '')[:70]}\"")
    pkg.write_bytes(good_pkg)
    check(not any(sh("docker", "exec", NAME, "sh", "-c", "ls -A /data/plugins/downloads 2>/dev/null", check=False).split()),
          "the refused downloads were deleted")

    cat.index(days=-1)
    src = refresh()
    h = hit()
    check(src["old"] and h is not None and h["old"], "an expired index: search still works and says the catalog is old")
    code, res = call("POST", "/plugins/install", {"name": PLUGIN, "version": "1.0.0"}, timeout=120)
    check(code == 409 and "old" in res.get("error", "") and mine() is None,
          f"install from an expired index is refused (409): \"{res.get('error', '')[:70]}\"")

    cat.index(revoked=((PLUGIN, "1.0.0"),))
    src = refresh()
    one = api("GET", f"/marketplace/{PLUGIN}")
    v100 = next(v for v in one["versions"] if v["version"] == "1.0.0")
    check(src["ok"] and not src["old"] and v100["revoked"] and not v100["fits"] and one["version"] == "1.2.0",
          "a revoked version: the catalog marks 1.0.0 revoked; the newest that fits stays 1.2.0")
    code, res = call("POST", "/plugins/install", {"name": PLUGIN, "version": "1.0.0"}, timeout=120)
    check(code == 409 and "revoked" in res.get("error", "") and mine() is None,
          f"install of the revoked version is refused (409): \"{res.get('error', '')[:70]}\"")
    cat.index()
    check(refresh()["ok"] and hit()["fits"], "a fresh index: everything fits again")


def agent_asks(pid: str) -> None:
    print("9. an agent asks, a person approves in the Inbox")
    reason = "The review needs the hello plugin's agent to greet the team."
    _, (answer,) = mcp(("keel_plugin_request", {"name": PLUGIN, "reason": reason}))
    check(answer.startswith(f"Asked a person to install {PLUGIN}"), f"keel_plugin_request: \"{answer[:80]}…\"")
    req = next((a for a in waiting() if a["payload"]["name"] == PLUGIN), None)
    check(req is not None and req["payload"]["source"] == "agent" and req["payload"]["reason"] == reason
          and req["title"] == f"Install {TITLE} 1.2.0?", f"a plugin-install approval waits: \"{req and req['title']}\"")
    check(mine() is None, "nothing is installed while it waits (agents cannot install)")
    check(decide(req["id"], "approve")["status"] == "approved", "POST /api/approvals/{id}/decide approve")
    p = mine()
    check(p and p["version"] == "1.2.0" and p["status"] == "restart", "hello 1.2.0 is installed; it waits for a restart")
    restart_and_wait("after the approved install")
    code, body = engine_get("/hello/ping")
    check(host_plugins().get(PLUGIN) == "1.2.0" and code == 200 and body.get("version") == "1.2.0",
          "after the restart hello 1.2.0 is loaded")

    print("10. the rule agents_may_ask off")
    check(api("PUT", "/plugins/rules", {"agents_may_ask": False})["agents_may_ask"] is False, "the rule is off")
    _, (answer,) = mcp(("keel_plugin_request", {"name": "other-plugin", "reason": "an agent wants it"}))
    check(answer.startswith(ASK_IN_PLUGINS), f"keel_plugin_request answers: \"{answer[:70]}\"")
    code, res = call("POST", "/plugins/requests", {"name": "other-plugin", "reason": "an agent wants it", "source": "agent"})
    check(code == 409 and ASK_IN_PLUGINS in str(res.get("hint")), "POST /api/plugins/requests is refused (409)")
    check(not [a for a in waiting() if a["payload"]["name"] == "other-plugin"], "no request was opened")
    api("PUT", "/plugins/rules", {"agents_may_ask": True})


def main() -> int:
    ap = argparse.ArgumentParser(description="The marketplace end to end on a throw-away keel (keel-mkt, port 8096).")
    ap.add_argument("--image", required=True, help="the image under test, e.g. keel-v2:dev")
    ap.add_argument("--keep", action="store_true", help="leave keel-mkt running at the end (the catalog server stops)")
    ap.add_argument("--no-web", action="store_true", help="skip the web checks (no Node)")
    ap.add_argument("--keel-plugin", default="", help="the keel-plugin command (default: uv run --project tools/keel-plugin keel-plugin)")
    ap.add_argument("--wait", type=float, default=30, help="minutes to wait when keel-mkt exists already (default 30)")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    KP[:] = shlex.split(a.keel_plugin) if a.keel_plugin else \
        ["uv", "run", "--quiet", "--project", str(ROOT / "tools" / "keel-plugin"), "keel-plugin"]
    wait_for_the_name(a.wait)
    tmp = Path(tempfile.mkdtemp(prefix="e4_marketplace-"))
    cat = None
    started = time.time()
    try:
        repo(tmp / "ws")
        cat = Catalog(tmp)
        print(f"the catalog: {tmp / 'catalog'} on {cat.base}; starting {NAME} from {a.image}")
        start(a.image, tmp / "ws")
        pid = setup(cat)
        needs_plugins(pid)
        search()
        install(pid, cat)
        update(pid, not a.no_web, tmp / "web")
        rollback(pid)
        remove(pid)
        refusals(cat)
        agent_asks(pid)
        print(f"\nPASSED: {len(CHECKS)} checks ({(time.time() - started) / 60:.1f} min)")
        return 0
    except (Exception, SystemExit) as e:  # noqa: BLE001
        print(f"\n{e}", file=sys.stderr)
        print(sh("docker", "logs", "--tail", "80", NAME, check=False)[-6000:], file=sys.stderr)
        return 1
    finally:
        if cat:
            cat.stop()
        if a.keep:
            print(f"{NAME} stays on http://127.0.0.1:{PORT} (the catalog server has stopped); remove it with: "
                  f"docker rm -f {NAME}; docker volume rm {VOLUME}")
        else:
            clean()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
