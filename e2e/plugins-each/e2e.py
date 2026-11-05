#!/usr/bin/env python3
"""Each plugin alone, and each plugin off, end to end, on a throw-away keel only: the container keel-each on
127.0.0.1:8091 with its own volume keel-each-data. It never touches another keel.

    python3 e2e/plugins-each/e2e.py --image keel-v2:fin --product-image keel-v2:fin-product [--only ci,db] [--keep]
        [--name keel-each --port 8091]   (another name and port let several runs go side by side; the volume is <name>-data)

For each of keel's plugins in PLUGINS (and keel Product on the product image) it sets the plugins with
`keel-engine plugins set <name> on|off` inside the container, then restarts keel (POST /api/plugins/restart
{now: true}: keel-start starts keel again in the same container), in three ways:

  alone  only that plugin on, with the plugins it requires (ci needs git, jira needs tasks, review needs keelbot,
         product needs tasks and jira); every other plugin off
  off    that plugin off, and the plugins that require it (they cannot load without it); every other plugin on
  all    every plugin on (once at the start, once at the end)

Each time it checks:
  - keel answers /api/health; /api/plugin-host lists exactly the expected plugins and no problem
  - each plugin's api answers when it is on (not 404) and is 404 when it is off
  - keel2 mcp --admin: the plugin's MCP tools (db, git, ci) are there exactly when it is on; keel's own tools are the
    same in every run
  - the web, in headless Chromium (walk.mjs): every menu page, #/projects and the flow, with their tabs: no console
    error, no failed request, no request to an off plugin's api or web files, no link to a page that is not there.
    The page registry: a plugin's pages (what it adds alone, past the core and the plugins it needs) are there
    exactly when it is on
  - the rest of keel: the project, its workflows and agents, and a change flow on the fake model to its first gate
    (stopped after)

At the end a table per plugin: alone ok / issues, off ok / issues. Exit 1 when one check failed. Without --keep the
container, the volume and the temp folder are removed at the end.
"""

from __future__ import annotations

import argparse
import os
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import keelauth  # noqa: E402  (e2e/keelauth.py: keel's sign-in, v0.18.1)
import lib  # noqa: E402  (e2e/lib.py: a free port and unique names)
E = lib.Env.make("each")  # keel-e2e-each-<random> and a free port; --name / --port still choose by hand (checked)
NAME, VOLUME, PORT = E.name, E.volume, E.port
HERE = Path(__file__).resolve().parent
PROJECT = "shop"

PLUGINS = ["code", "git", "review", "db", "ci", "keelbot", "map", "graph", "wiki", "tasks", "jira",
           "answers", "record", "notify", "desk", "library", "links", "goals", "slack", "supervisor", "github-issues", "cluster"]
NEEDS = {"ci": ["git"], "jira": ["tasks"], "review": ["keelbot"], "product": ["tasks", "jira"],
         "desk": ["tasks"], "slack": ["tasks"], "github-issues": ["tasks"]}
PER_PROJECT = ["ci", "db", "git", "review"]
# one GET of each plugin's api: it answers (not 404) when the plugin is on, 404 when it is off (answers is content only: no api)
ROUTE = {
    "code": "/projects/{pid}/repo/tree", "git": "/projects/{pid}/git/status", "review": "/projects/{pid}/review/prs",
    "db": "/projects/{pid}/db/connections", "ci": "/projects/{pid}/ci/runs", "keelbot": "/projects/{pid}/helper/sessions",
    "map": "/projects/{pid}/map", "graph": "/projects/{pid}/graph", "wiki": "/projects/{pid}/wiki",
    "tasks": "/projects/{pid}/tasks", "jira": "/projects/{pid}/jira", "product": "/product",
    "record": "/projects/{pid}/record/flows", "notify": "/plugins/notify/channels", "desk": "/projects/{pid}/desk/requests",
    "library": "/projects/{pid}/library/docs", "links": "/plugins/links/linear", "goals": "/projects/{pid}/goals",
    "slack": "/plugins/slack", "supervisor": "/supervisor/switch", "github-issues": "/projects/{pid}/github-issues",
    "cluster": "/plugins/cluster/history",
}
# the paths of each plugin's api, for the walk: a page must not ask an off plugin
WEB_ROUTES = {
    "code": r"^/api/projects/[^/]+/repo(/|$)", "git": r"^/api/projects/[^/]+/git(/|$)",
    "review": r"^/api/projects/[^/]+/review(/|$)|^/api/gitlab(/|$)", "db": r"^/api/projects/[^/]+/db(/|$)",
    "ci": r"^/api/projects/[^/]+/ci(/|$)", "keelbot": r"^/api/projects/[^/]+/helper(/|$)",
    "map": r"^/api/projects/[^/]+/map(/|$)", "graph": r"^/api/projects/[^/]+/(graph|index)(/|$)",
    "wiki": r"^/api/projects/[^/]+/wiki(/|$)", "tasks": r"^/api/projects/[^/]+/tasks(/|$)|^/api/(tasks|inbox/tasks)(/|$)",
    "jira": r"^/api/projects/[^/]+/(jira|mcp-catalog)(/|$)", "product": r"^/api/(product|initiatives)(/|$)",
    "answers": r"^/api/plugins/answers(/|$)", "record": r"^/api/projects/[^/]+/record(/|$)", "notify": r"^/api/plugins/notify(/|$)",
    "desk": r"^/api/projects/[^/]+/desk(/|$)", "library": r"^/api/projects/[^/]+/library(/|$)", "links": r"^/api/plugins/links(/|$)",
    "goals": r"^/api/projects/[^/]+/goals(/|$)", "slack": r"^/api/plugins/slack(/|$)",
    "supervisor": r"^/api/supervisor(/|$)|^/api/projects/[^/]+/(supervisor|flows/[^/]+/supervisor)(/|$)",
    "github-issues": r"^/api/projects/[^/]+/github-issues(/|$)", "cluster": r"^/api/plugins/cluster(/|$)",
}
MCP_TOOLS = {
    "git": ["keel_git_status", "keel_pr_status", "keel_git_commit", "keel_git_push", "keel_pr_create"],
    "ci": ["keel_ci_runs", "keel_ci_failure", "keel_ci_rerun"],
    "db": ["keel_db_schema", "keel_db_query", "keel_db_change", "keel_db_connections"],
    "review": ["keel_review_prs", "keel_review_files", "keel_review_diff", "keel_review_findings", "keel_review_notes",
               "keel_review_explain", "keel_review_comment", "keel_review_reply", "keel_review_resolve", "keel_review_submit"],
    "supervisor": ["keel_supervisor", "keel_supervisor_set"],
}
FIXTURE = {
    "README.md": "# Shop\n\nA tiny shop: prices with tax, in Python.\n\nRun the tests: `python -m pytest -q`\n",
    "shop/__init__.py": "",
    "shop/price.py": "def with_tax(cents: int, rate: float) -> int:\n    return round(cents * (1 + rate))\n",
    "tests/test_price.py": "from shop.price import with_tax\n\n\ndef test_tax_is_added():\n    assert with_tax(1000, 0.21) == 1210\n",
}

RESULTS: list[dict] = []   # {plugin, way, ok, issues, checks}


def sh(*args: str, check: bool = True, cwd: Path | None = None) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({r.returncode}): {(r.stderr or r.stdout)[-1500:]}")
    return r.stdout + r.stderr


def http(method: str, path: str, body: object | None = None, timeout: float = 30) -> tuple[int, str, str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method,
                                 headers={**({"content-type": "application/json"} if data else {}), **keelauth.headers(NAME)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("content-type", ""), r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read().decode(errors="replace")


def api(method: str, path: str, body: object | None = None, ok: tuple[int, ...] = (200,)):
    code, kind, text = http(method, "/api" + path, body)
    if code not in ok:
        raise AssertionError(f"{method} /api{path} → {code}: {text[:400]}")
    return json.loads(text) if text and "json" in kind else text


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


def exists(name: str) -> bool:
    return bool(sh("docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}", check=False).strip())


def clean() -> None:
    sh("docker", "rm", "-f", NAME, check=False)
    keelauth.forget(NAME)
    sh("docker", "volume", "rm", VOLUME, check=False)


def fixture(root: Path) -> Path:
    repo = root / PROJECT
    for rel, text in FIXTURE.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    g = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false"]
    sh(*g, "init", "-q", "-b", "main", cwd=repo)
    sh(*g, "add", "-A", cwd=repo)
    sh(*g, "commit", "-q", "-m", "The shop", cwd=repo)
    return repo


def start(image: str, ws: Path) -> None:
    clean()
    sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data",
       "-v", f"{ws}:/workspace", "-e", "KEEL_FAKE=1", "-e", f"KEEL_PROJECT_NAME={PROJECT}", image)
    wait_until(f"{NAME} ({image}) to be healthy", lambda: api("GET", "/health"), timeout=300)


def set_plugins(on: set[str], every: list[str]) -> None:
    """keel-engine plugins set for every plugin, then a restart of keel in the same container."""
    script = " && ".join(f"keel-engine plugins set {n} {'on' if n in on else 'off'} >/dev/null" for n in every)
    sh("docker", "exec", NAME, "sh", "-c", script)
    before = sh("docker", "inspect", "-f", "{{.RestartCount}}", NAME).strip()
    api("POST", "/plugins/restart", {"now": True}, ok=(202,))
    # the old api goes away first (keel-start starts keel again when it ended with 75), then the new one answers
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2)
            time.sleep(0.5)
        except Exception:  # noqa: BLE001 - it is down: the restart began
            break
    wait_until("keel to come back after the restart", lambda: api("GET", "/health"), timeout=300)
    after = sh("docker", "inspect", "-f", "{{.State.Status}} {{.RestartCount}}", NAME).split()
    if after != ["running", before]:
        raise AssertionError(f"the container did not keep running through the restart: {after}")


def mcp_tools() -> list[str]:
    """keel2 mcp --admin's tools/list (the server inside the container, over stdio)."""
    proc = subprocess.Popen(["docker", "exec", "-i", NAME, "/opt/engine/.venv/bin/python", "-m", "keel_engine.mcp", "--admin"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [lines.put(x) for x in proc.stdout], daemon=True).start()

    def send(msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def answer(want: int) -> dict:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                msg = json.loads(lines.get(timeout=max(0.1, deadline - time.time())))
            except (queue.Empty, ValueError):
                continue
            if msg.get("id") == want:
                return msg
        raise AssertionError("keel2 mcp gave no answer")
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "each", "version": "1"}}})
        answer(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return [t["name"] for t in answer(2).get("result", {}).get("tools", [])]
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


def flow_to_gate(pid: str) -> str:
    """A change flow on the fake model to its first gate; stopped after. Returns where it stopped."""
    fake = {"provider": "fake", "mode": "api", "model": "fake"}
    api("PUT", "/settings/general", {"cheaper_model": fake})
    res = api("POST", f"/projects/{pid}/flows", {"workflow_id": "change", "title": "Prices in euros", "allow_fake": True,
                                                 "allow_dirty": True, "acs": [{"id": "AC-1", "layer": "API", "title": "A price shows in euros"}]})
    tid = res.get("thread_id") or (res.get("thread") or {}).get("thread_id")

    def where():
        th = api("GET", f"/projects/{pid}/flows/{tid}")
        th = th.get("thread", th)
        return th.get("status") if th.get("status") not in ("running", "starting", None) else None
    status = wait_until("the change flow to stop at its first gate", where, timeout=300)
    api("POST", f"/threads/{tid}/stop", ok=(200, 202, 204, 409))
    return f"{status} ({tid})"


def walk(out: Path, label: str, off: set[str], extra: list[str], pid: str) -> dict:
    routes = {n: WEB_ROUTES[n] for n in sorted(off)}
    cmd = ["npx", "-y", "-p", "playwright@1", "node", str(HERE / "walk.mjs"), f"http://127.0.0.1:{PORT}",
           str(out / label), json.dumps(routes), *extra]
    # the walk opens this project (the product image has a second one, keel Product's own keel-product)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env={**os.environ, "KEEL_WALK_PROJECT": pid,
                       "KEEL_SIGNIN": keelauth.signin_url(f"http://127.0.0.1:{PORT}", NAME)})
    line = next((x for x in reversed(r.stdout.splitlines()) if x.startswith("{")), "")
    if not line:
        raise AssertionError(f"walk.mjs gave no answer ({r.returncode}): {(r.stderr or r.stdout)[-2000:]}")
    res = json.loads(line)
    (out / f"walk-{label}.json").write_text(json.dumps(res, indent=1))
    return res


class Run:
    """One way (alone / off / all) of one plugin: its checks and issues."""

    def __init__(self, plugin: str, way: str):
        self.plugin, self.way, self.checks, self.issues = plugin, way, 0, []

    def check(self, ok: bool, what: str, detail: str = "") -> None:
        if ok:
            self.checks += 1
            print("  ✓", what)
        else:
            self.issues.append(f"{what}: {detail}" if detail else what)
            print("  ✗", what, f"\n      {detail[:1500]}" if detail else "")


def scenario(plugin: str, way: str, on: set[str], every: list[str], pid: str, out: Path, core_tools: list[str] | None,
             owners: dict[str, set[str]]) -> tuple[Run, dict]:
    print(f"\n{plugin} {way}: on = {', '.join(sorted(on)) or '(none)'}")
    run = Run(plugin, way)
    off = set(every) - on
    try:
        set_plugins(on, every)
    except AssertionError as e:
        run.check(False, "the plugins set and keel restarted", str(e))
        RESULTS.append(run.__dict__)
        return run, {}
    h = api("GET", "/health")
    run.check(bool(h), f"keel answers /api/health ({h.get('version', '?')})")
    host = api("GET", "/plugin-host")
    names = {p["name"] for p in host["plugins"]}
    run.check(names == on and not host.get("problems"), "/api/plugin-host lists exactly the expected plugins, no problem",
              f"loaded {sorted(names)}, problems {host.get('problems')}")
    for name in every:
        if name not in ROUTE:
            continue
        code, _, text = http("GET", "/api" + ROUTE[name].replace("{pid}", pid))
        if name in on:
            run.check(code != 404 and code < 500, f"{name}'s api answers ({code})", text[:200])
        else:
            # /api/plugins/slack is also keel's own POST /api/plugins/{name}, so with slack off a GET there is 405
            run.check(code == 404 or (name == "slack" and code == 405), f"{name}'s api is 404 when it is off ({code})", text[:200])
    tools = mcp_tools()
    for name, names_ in MCP_TOOLS.items():
        if name not in every:
            continue
        have = [t for t in names_ if t in tools]
        if name in on:
            run.check(have == names_, f"{name}'s MCP tools are there", f"missing {sorted(set(names_) - set(have))}")
        else:
            run.check(not have, f"{name}'s MCP tools are gone", f"still {have}")
    plugin_tools = {t for ts in MCP_TOOLS.values() for t in ts}
    core = [t for t in tools if t not in plugin_tools]
    if core_tools is not None:
        run.check(core == core_tools, "keel's own MCP tools are the same as with every plugin on",
                  f"only now: {sorted(set(core) - set(core_tools))}; gone: {sorted(set(core_tools) - set(core))}")
    project = api("GET", f"/projects/{pid}")
    api("GET", f"/projects/{pid}/workflows")
    api("GET", f"/projects/{pid}/agents")
    run.check(project.get("id") == pid, "the project opens; its workflows and agents answer")
    try:
        where = flow_to_gate(pid)
        run.check(where.startswith("waiting"), f"a change flow on the fake model runs to its first gate: {where}")
        link = [f"#/flow/{where.split('(')[1].rstrip(')')}"]
    except AssertionError as e:
        run.check(False, "a change flow on the fake model runs to its first gate", str(e))
        link = []
    res = walk(out, f"{plugin}-{way}", off, link, pid)
    bad = [c for c in res["checks"] if not c["ok"]]
    run.check(not bad, f"the web: {len(res['checks'])} pages and checks, no console error, failed request, off plugin's "
              "route or dead link", " || ".join(f"{c['what']}: {c['detail']}" for c in bad))
    pages = set(res["pages"])
    # a plugin's own pages are there exactly when it is on
    for name, own in owners.items():
        if not own:
            continue
        if name in on:
            run.check(own <= pages, f"{name}'s pages are registered ({', '.join(sorted(own))})", f"missing {sorted(own - pages)}")
        else:
            run.check(not (own & pages), f"{name}'s pages are gone", f"still {sorted(own & pages)}")
    for href in res["menu"]:
        first = href.removeprefix("#/").split("/")[0]
        if first not in pages and first != "projects":
            run.check(False, "every menu link opens a registered page", href)
    RESULTS.append(run.__dict__)
    return run, {"pages": pages, "tools": core}


def closure(names: list[str]) -> set[str]:
    out: set[str] = set()
    todo = list(names)
    while todo:
        n = todo.pop()
        if n not in out:
            out.add(n)
            todo += NEEDS.get(n, [])
    return out


def dependents(name: str, every: list[str]) -> set[str]:
    return {n for n in every if name in closure([n]) and n != name}


def phase(image: str, every: list[str], targets: list[str], ws: Path, out: Path, owners: dict[str, set[str]]) -> None:
    print(f"\n==== {image}: {len(every)} plugins")
    start(image, ws)
    projects = wait_until("the project at /workspace", lambda: api("GET", "/projects") or None, timeout=60)
    pid = next((p["id"] for p in projects if p["id"] == PROJECT), projects[0]["id"])
    for name in PER_PROJECT:
        if name in every:
            api("PUT", f"/projects/{pid}/plugins/{name}", {"enabled": True, "scope": "project"})
    # every plugin on (as the image starts), then core only: the base the pages are counted against
    run, all_on = scenario("(all)", "all", set(every), every, pid, out, None, {})
    core_tools = all_on.get("tools")
    run, core = scenario("(none)", "core", set(), every, pid, out, core_tools, {})
    base = core.get("pages", set())
    # alone, the plugins with no needs first: a plugin's own pages = alone − core − the pages of the plugins it needs
    order = sorted(targets, key=lambda n: len(closure([n])))
    for name in order:
        _, got = scenario(name, "alone", closure([name]), every, pid, out, core_tools, owners)
        need_pages = set().union(*(owners.get(n, set()) for n in closure([name]) - {name}))
        owners[name] = got.get("pages", set()) - base - need_pages
        print(f"    {name}'s own pages: {', '.join(sorted(owners[name])) or '(none: slots and views only)'}")
    for name in order:
        scenario(name, "off", set(every) - {name} - dependents(name, every), every, pid, out, core_tools, owners)
    scenario("(all)", "all", set(every), every, pid, out, core_tools, owners)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="keel-v2:fin", help="the normal image (the plugins in PLUGINS inside)")
    ap.add_argument("--product-image", default="keel-v2:fin-product", help="the product image ('' to skip Product)")
    ap.add_argument("--only", default="", help="only these plugins (comma separated; product for keel Product)")
    ap.add_argument("--keep", action="store_true", help="leave keel-each running at the end")
    ap.add_argument("--name", default=None, help="the container's name (its volume is <name>-data); default: keel-e2e-each-<random>")
    ap.add_argument("--port", type=int, default=None, help="the port on 127.0.0.1 (default: a free one)")
    a = ap.parse_args()
    global NAME, VOLUME, PORT
    if a.name or a.port:
        E.retarget(a.name, None, a.port)
        NAME, VOLUME, PORT = E.name, E.volume, E.port
    E.keep = a.keep
    sys.stdout.reconfigure(line_buffering=True)
    if exists(NAME):
        print(f"{NAME} exists already: it may be another run's. Remove it first (docker rm -f {NAME}) when it is not.")
        return 2
    only = [s.strip() for s in a.only.split(",") if s.strip()]
    tmp = Path(tempfile.mkdtemp(prefix="fp_each_"))
    out = tmp / "out"
    out.mkdir()
    t0 = time.time()
    owners: dict[str, set[str]] = {}
    try:
        targets = [p for p in PLUGINS if not only or p in only]
        if targets:
            phase(a.image, PLUGINS, targets, fixture(tmp / "ws"), out, owners)
        if a.product_image and (not only or "product" in only):
            every = PLUGINS + ["product"]
            # the pages of tasks and jira (Product needs them) come from the first phase, or from this one
            phase(a.product_image, every, [p for p in ("tasks", "jira", "product") if p not in owners or p == "product"],
                  fixture(tmp / "ws-product"), out, owners)
    except Exception as e:  # noqa: BLE001
        print(f"\nthe run broke: {e}", file=sys.stderr)
        print(sh("docker", "logs", "--tail", "60", NAME, check=False)[-4000:], file=sys.stderr)
        RESULTS.append({"plugin": "(run)", "way": "broke", "checks": 0, "issues": [str(e)]})
    finally:
        if not a.keep:
            clean()
    print(f"\n{'plugin':10} {'alone':28} {'off':28}")
    for name in [*PLUGINS, "product"]:
        rows = {r["way"]: r for r in RESULTS if r["plugin"] == name}
        if not rows:
            continue
        cell = lambda r: "-" if not r else (f"ok ({r['checks']} checks)" if not r["issues"] else f"{len(r['issues'])} issue(s)")
        print(f"{name:10} {cell(rows.get('alone')):28} {cell(rows.get('off')):28}")
    for r in RESULTS:
        if r["plugin"].startswith("("):
            print(f"{r['plugin']} {r['way']}: " + ("ok" if not r["issues"] else f"{len(r['issues'])} issue(s)") + f" ({r['checks']} checks)")
    bad = [r for r in RESULTS if r["issues"]]
    for r in bad:
        print(f"\n{r['plugin']} {r['way']}:")
        for i in r["issues"]:
            print(f"  - {i[:2000]}")
    (out / "results.json").write_text(json.dumps(RESULTS, indent=1, default=sorted))
    total = sum(r["checks"] for r in RESULTS)
    print(f"\n{'FAILED' if bad else 'PASSED'}: {total} checks, {sum(len(r['issues']) for r in bad)} issue(s) "
          f"in {(time.time() - t0) / 60:.1f} min; results in {out}")
    if not bad:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
