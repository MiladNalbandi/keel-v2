#!/usr/bin/env python3
"""keel 0.18.64 the AC gate shows its criterion's code, end to end, on a throw-away keel only (keel-e2e-acgate-<random>
on a free port, its own volume). It never touches the person's own keel.

    python3 e2e/acgate/e2e.py --image keel-v2:dev [--keep] [--out DIR]

The workspace holds one small git project (shop). A feature flow with one criterion runs on the fake model
(KEEL_FAKE=1); every pause before it is approved until the flow waits at its AC gate. The api's
GET /projects/{pid}/flows/{tid}/ac-code answers the criterion's RED and GREEN commits with their diffs. Headless
Chromium (e2e/acgate/walk.mjs, through `npx -y -p playwright@1`) checks the gate card: the code section and its summary,
the RED / GREEN / Both tabs switch, a diff shows with coloured added lines, the Steps column is centered at 1600 px wide
and nothing scrolls the page sideways at 390 px. Screenshots go to --out (default: a temp folder, removed when the run
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
E = lib.Env.make("acgate")
NAME, VOLUME, PORT = E.name, E.volume, E.port
CHECKS: list[str] = []
AC = {"id": "AC-1", "layer": "API", "title": "An order total adds the tax"}

SHOP = {
    "README.md": "# Shop\n\nA tiny shop: orders and prices with tax, in Python.\n",
    "shop/__init__.py": "",
    "shop/price.py": "def with_tax(cents: int, rate: float) -> int:\n    return round(cents * (1 + rate))\n",
    "tests/__init__.py": "",
    "tests/test_price.py": "from shop.price import with_tax\n\n\ndef test_tax():\n    assert with_tax(1000, 0.21) == 1210\n",
}


def sh(*args: str, check: bool = True, cwd: Path | None = None) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({r.returncode}): {(r.stderr or r.stdout)[-1500:]}")
    return r.stdout + r.stderr


def http(method: str, path: str, body: object | None = None, timeout: float = 30) -> tuple[int, str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method,
                                 headers={**({"content-type": "application/json"} if data else {}), **keelauth.headers(NAME)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def api(method: str, path: str, body: object | None = None):
    code, text = http(method, "/api" + path, body)
    if code != 200:
        raise AssertionError(f"{method} /api{path} → {code}: {text[:500]}")
    try:
        return json.loads(text) if text else None
    except ValueError:
        return text


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
    """One git project at /workspace/shop."""
    ws = root / "ws"
    repo = ws / "shop"
    git = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false"]
    for rel, text in SHOP.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    sh(*git, "init", "-q", "-b", "main", cwd=repo)
    sh(*git, "add", "-A", cwd=repo)
    sh(*git, "commit", "-q", "-m", "The shop project", cwd=repo)
    return ws


def start(image: str, ws: Path) -> None:
    clean()
    sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data",
       "-v", f"{ws}:/workspace", "-e", "KEEL_FAKE=1", image)
    wait_until(f"{NAME} ({image}) to be healthy", lambda: api("GET", "/health"), timeout=300)


def thread(pid: str, tid: str) -> dict:
    th = api("GET", f"/projects/{pid}/flows/{tid}")
    return th.get("thread", th) if isinstance(th, dict) else {}


def to_ac_gate(pid: str, tid: str) -> dict:
    """Approve every pause until the flow waits at an AC gate (a waiting question with `ac`)."""
    seen: list[str] = []
    for _ in range(40):
        th = wait_until("the flow to pause", lambda: (t := thread(pid, tid)) and t.get("status") != "running" and t, timeout=300)
        w = th.get("waiting") or {}
        if th.get("status") != "waiting":
            raise AssertionError(f"the flow ended ({th.get('status')}: {th.get('error')}) before an AC gate; it passed {seen}")
        if w.get("ac"):
            return th
        seen.append(w.get("step") or "?")
        api("POST", f"/threads/{tid}/resume", {"decision": "approve"})
        time.sleep(1)
    raise AssertionError(f"never reached an AC gate: {seen}")


def walk(out: Path, shop: dict, tid: str) -> dict:
    print("   the web, in headless Chromium")
    cmd = ["npx", "-y", "-p", "playwright@1", "node", str(HERE / "walk.mjs"), f"http://127.0.0.1:{PORT}", str(out),
           shop["name"], tid]
    env = {**os.environ, "KEEL_SIGNIN": keelauth.signin_url(f"http://127.0.0.1:{PORT}", NAME)}
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
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
    tmp = Path(tempfile.mkdtemp(prefix="acgate_e2e_"))
    out = Path(a.out) if a.out else tmp / "out"
    out.mkdir(parents=True, exist_ok=True)
    t0, ok = time.time(), False
    try:
        start(a.image, workspace(tmp))
        projects = wait_until("the project", lambda: api("GET", "/projects") or None, timeout=60)
        shop = next(p for p in projects if p["name"] == "shop")
        api("PUT", "/settings/general", {"cheaper_model": {"provider": "fake", "mode": "api", "model": "fake"}})
        res = api("POST", f"/projects/{shop['id']}/flows", {
            "workflow_id": "feature", "title": "Order totals with tax", "allow_fake": True, "allow_dirty": True, "acs": [AC]})
        tid = res.get("thread_id") or (res.get("thread") or {}).get("thread_id")
        check(bool(tid), "the feature flow started on the fake model with one criterion")
        th = to_ac_gate(shop["id"], tid)
        check(th["waiting"]["ac"] == AC["id"], f"it waits at the AC gate of {AC['id']}: {th['waiting'].get('title')}")
        code = api("GET", f"/projects/{shop['id']}/flows/{tid}/ac-code?ac={AC['id']}")
        red, green = code.get("red") or {}, code.get("green") or {}
        check(code.get("commits", 0) >= 1 and bool(red or green),
              f"the api answers the criterion's code: {code.get('commits')} commit(s), {code.get('files')} file(s), "
              f"RED {red.get('short')} “{red.get('subject')}”, GREEN {green.get('short')} “{green.get('subject')}”")
        files = (red.get("files") or []) + (green.get("files") or [])
        check(any(f.get("diff") for f in files), "at least one file comes with its diff text")
        walk(out, shop, tid)
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
