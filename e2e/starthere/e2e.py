#!/usr/bin/env python3
"""keel 0.18.66 Help › Start here, Settings › Access, the update notice and the first-run welcome, end to end, on a
throw-away keel only (keel-e2e-starthere-<random> on a free port, its own volume). It never touches a person's keel.

    python3 e2e/starthere/e2e.py --image keel-v2:dev [--keep] [--out DIR]

The workspace holds one small git project (my-app); keel runs on the fake model (KEEL_FAKE=1). The release check reads
a fake release JSON (KEEL_RELEASE_CHECK_URL=file:///opt/fake-release/release.json, a folder mounted read-only): never
GitHub. The api part checks GET /api/updates before and after Check now, /api/health's `latest`, /api/access,
/api/help/start and the welcome flag. Headless Chromium (e2e/starthere/walk.mjs, through `npx -y -p playwright@1`)
walks the first open (the welcome and the update notice), the welcome's five steps to "keel is ready" (a reload does
not show it again), Start here (the steps, the tool tabs, Copy, the dot that turns green on the first MCP call, a guide
and the search), Settings › Access (its four parts, the old #/settings/devices link) and the notice once per version,
at 1440 px and 390 px (nothing scrolls sideways). Screenshots go to --out (default: a temp folder, removed when the run
passed). The container and the volume are removed at the end (--keep: they stay).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import keelauth  # noqa: E402  (e2e/keelauth.py: keel's sign-in)
import lib  # noqa: E402  (e2e/lib.py: a free port and unique names)
E = lib.Env.make("starthere")
NAME, VOLUME, PORT = E.name, E.volume, E.port
CHECKS: list[str] = []

APP = {
    "README.md": "# my-app\n\nA tiny app: prices with tax, in Python.\n",
    "app/__init__.py": "",
    "app/price.py": "def with_tax(cents: int, rate: float) -> int:\n    return round(cents * (1 + rate))\n",
    "tests/__init__.py": "",
    "tests/test_price.py": "from app.price import with_tax\n\n\ndef test_tax():\n    assert with_tax(1000, 0.21) == 1210\n",
}

RELEASE = {
    "tag_name": "v0.18.99", "name": "keel v2 0.18.99", "draft": False, "prerelease": False,
    "html_url": "https://github.com/MiladNalbandi/keel-v2/releases/tag/v0.18.99", "published_at": "2026-10-28T10:00:00Z",
    "body": "## What's new\n\n- Settings › Access: where keel answers, devices, AI tools and updates\n"
            "- Help › Start here: five steps with what keel sees\n- A notice when a newer keel is out\n"
            "- The first-run welcome with the tour\n- one more line that the notice leaves out\n",
}


def sh(*args: str, check: bool = True, cwd: Path | None = None) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({r.returncode}): {(r.stderr or r.stdout)[-1500:]}")
    return r.stdout + r.stderr


def http(method: str, path: str, body: object | None = None, headers: dict | None = None, timeout: float = 30) -> tuple[int, str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method,
                                 headers={**({"content-type": "application/json"} if data else {}),
                                          **(headers if headers is not None else keelauth.headers(NAME))})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def api(method: str, path: str, body: object | None = None, headers: dict | None = None):
    code, text = http(method, "/api" + path, body, headers)
    if code != 200:
        raise AssertionError(f"{method} /api{path} → {code}: {text[:500]}")
    return json.loads(text) if text else None


def check(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError("FAILED: " + what)
    CHECKS.append(what)
    print("  ✓", what)


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


def clean() -> None:
    sh("docker", "rm", "-f", NAME, check=False)
    keelauth.forget(NAME)
    sh("docker", "volume", "rm", VOLUME, check=False)


def workspace(root: Path) -> Path:
    ws = root / "ws"
    repo = ws / "my-app"
    git = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false"]
    for rel, text in APP.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    sh(*git, "init", "-q", "-b", "main", cwd=repo)
    sh(*git, "add", "-A", cwd=repo)
    sh(*git, "commit", "-q", "-m", "The my-app project", cwd=repo)
    return ws


def start(image: str, ws: Path, rel: Path) -> None:
    clean()
    sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data",
       "-v", f"{ws}:/workspace", "-v", f"{rel}:/opt/fake-release:ro", "-e", "KEEL_FAKE=1", "-e", "KEEL_BIND=127.0.0.1",
       "-e", "KEEL_RELEASE_CHECK_URL=file:///opt/fake-release/release.json", image)
    wait_until(f"{NAME} ({image}) to be healthy", lambda: api("GET", "/health"), timeout=300)


def walk(out: Path, mcp_token: str) -> dict:
    print("   the web, in headless Chromium")
    cmd = ["npx", "-y", "-p", "playwright@1", "node", str(HERE / "walk.mjs"), f"http://127.0.0.1:{PORT}", str(out)]
    env = {**os.environ, "KEEL_SIGNIN": keelauth.signin_url(f"http://127.0.0.1:{PORT}", NAME), "KEEL_MCP_TOKEN": mcp_token}
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, env=env)
    line = next((x for x in reversed(r.stdout.splitlines()) if x.startswith("{")), "")
    if not line:
        raise AssertionError(f"walk.mjs gave no answer ({r.returncode}): {(r.stderr or r.stdout)[-2000:]}")
    res = json.loads(line)
    (out / "walk.json").write_text(json.dumps(res, indent=1))
    for c in res["checks"]:
        print(("  ✓ " if c["ok"] else "  ✗ ") + c["what"] + ("" if c["ok"] else f"\n      {c['detail']}"))
    CHECKS.extend(c["what"] for c in res["checks"] if c["ok"])
    if any(not c["ok"] for c in res["checks"]):
        raise AssertionError(f"FAILED: web check(s); details in {out / 'walk.json'}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="keel-v2:dev")
    ap.add_argument("--keep", action="store_true", help="leave the keel running (and its volume)")
    ap.add_argument("--out", default="", help="where the screenshots and results go (default: a temp folder)")
    a = ap.parse_args()
    E.keep = a.keep
    tmp = Path(tempfile.mkdtemp(prefix="starthere_e2e_"))
    out = Path(a.out) if a.out else tmp / "out"
    out.mkdir(parents=True, exist_ok=True)
    rel = tmp / "release"
    rel.mkdir()
    (rel / "release.json").write_text(json.dumps(RELEASE))
    rel.chmod(0o755)
    (rel / "release.json").chmod(0o644)
    t0, ok = time.time(), False
    try:
        start(a.image, workspace(tmp), rel)
        projects = wait_until("the project", lambda: api("GET", "/projects") or None, timeout=60)
        check(any(p["name"] == "my-app" for p in projects), "keel found the project my-app")
        version = api("GET", "/health")["version"]
        u = api("GET", "/updates")
        check(u["latest"] is None and u["check_daily"] is True and u["error"] is None,
              f"before a check: nothing kept, check every day on (keel {version})")
        u = api("POST", "/updates/check")
        check(u["latest"] == "0.18.99" and u["newer"] is True and "Settings › Access" in u["notes"],
              f"Check now reads the fake release: 0.18.99 is newer than {u['current']}; command `{u['command']}`")
        check(api("GET", "/health").get("latest") == "0.18.99", "GET /api/health says the newer version (keel_health uses it)")
        acc = api("GET", "/access")
        check(acc["mode"] == "local" and acc["bind"] == "127.0.0.1", f"GET /api/access: this computer only ({acc['bind']})")
        s = api("GET", "/help/start")
        check(s["projects"] == 1 and not s["mcp"] and s["welcome"]["seen"] is False and s["flow"] is None,
              "GET /api/help/start: one project, no AI tool yet, no flow, the welcome not seen")
        tok = api("POST", "/mcp/token", {"level": "write"})["token"]
        walk(out, tok)
        check(api("GET", "/help/welcome")["seen"] is True, "keel remembers the welcome (a new browser does not show it again)")
        seen = api("GET", "/mcp/clients")["clients"]
        check(any(c["client"] == "claude-code" and c["level"] == "write" for c in seen),
              "keel lists the MCP client the walk's first call came from (claude-code, write)")
        print(f"\nPASSED: {len(CHECKS)} checks in {time.time() - t0:.0f} s; screenshots in {out}")
        ok = True
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"\n{e}", file=sys.stderr)
        print(sh("docker", "logs", "--tail", "60", NAME, check=False)[-5000:], file=sys.stderr)
        return 1
    finally:
        if not (a.keep and ok):
            clean()
        shutil.rmtree(tmp / "ws", ignore_errors=True)
        if ok and not a.out:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
