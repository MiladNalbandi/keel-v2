#!/usr/bin/env python3
"""keel 0.18.64 an unsent answer to a waiting question is kept, end to end, on a throw-away keel only
(keel-e2e-drafts-<random> on a free port, its own volume). It never touches the person's own keel.

    python3 e2e/drafts/e2e.py --image keel-v2:dev [--keep] [--out DIR]

The workspace holds two small git projects (shop, notes), so the web can switch between them. The clarify flow runs on
the fake model (KEEL_FAKE=1) in shop and waits at its first question ("What kind of task is this?"). Headless Chromium
(e2e/drafts/walk.mjs, through `npx -y -p playwright@1`) picks an option and writes a note, switches to notes and back,
opens the Inbox and comes back, reloads the page: the answer is still there each time, with "Draft kept". Then it sends
the answer: the engine gets the picked option and the note, the next question shows empty and nothing is kept any
more. Screenshots go to --out (default: a temp folder, removed when the run passed). The container and the volume are
removed at the end (--keep: they stay).
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
E = lib.Env.make("drafts")
NAME, VOLUME, PORT = E.name, E.volume, E.port
CHECKS: list[str] = []
NOTE = "Keep the file small: only the name and the price."  # the note walk.mjs writes

PROJECTS = {
    "shop": {
        "README.md": "# Shop\n\nA tiny shop: prices with tax, in Python.\n",
        "shop/price.py": "def with_tax(cents: int, rate: float) -> int:\n    return round(cents * (1 + rate))\n",
        "tests/test_price.py": "from shop.price import with_tax\n\n\ndef test_tax():\n    assert with_tax(1000, 0.21) == 1210\n",
    },
    "notes": {
        "README.md": "# Notes\n\nA tiny notes list.\n",
        "notes/store.py": "NOTES: list[str] = []\n\n\ndef add(text: str) -> None:\n    NOTES.append(text)\n",
    },
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
    """A folder with two git projects: keel finds each child repo of /workspace (api ProjectScanner)."""
    ws = root / "ws"
    git = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false"]
    for name, files in PROJECTS.items():
        repo = ws / name
        for rel, text in files.items():
            f = repo / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        sh(*git, "init", "-q", "-b", "main", cwd=repo)
        sh(*git, "add", "-A", cwd=repo)
        sh(*git, "commit", "-q", "-m", f"The {name} project", cwd=repo)
    return ws


def start(image: str, ws: Path) -> None:
    clean()
    sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data",
       "-v", f"{ws}:/workspace", "-e", "KEEL_FAKE=1", image)
    wait_until(f"{NAME} ({image}) to be healthy", lambda: api("GET", "/health"), timeout=300)


def waiting(pid: str, tid: str) -> dict | None:
    th = api("GET", f"/projects/{pid}/flows/{tid}")
    th = th.get("thread", th) if isinstance(th, dict) else {}
    return th.get("waiting") if th.get("status") == "waiting" else None


def walk(out: Path, shop: dict, notes: dict, tid: str) -> dict:
    print("   the web, in headless Chromium")
    cmd = ["npx", "-y", "-p", "playwright@1", "node", str(HERE / "walk.mjs"), f"http://127.0.0.1:{PORT}", str(out),
           notes["id"], shop["name"], notes["name"], tid]
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
    tmp = Path(tempfile.mkdtemp(prefix="drafts_e2e_"))
    out = Path(a.out) if a.out else tmp / "out"
    out.mkdir(parents=True, exist_ok=True)
    t0, ok = time.time(), False
    try:
        start(a.image, workspace(tmp))
        projects = wait_until("both projects", lambda: (p := api("GET", "/projects")) and len(p) >= 2 and p, timeout=60)
        by = {p["name"]: p for p in projects}
        check({"shop", "notes"} <= set(by), f"keel found both projects ({', '.join(sorted(by))})")
        shop, notes = by["shop"], by["notes"]
        api("PUT", "/settings/general", {"cheaper_model": {"provider": "fake", "mode": "api", "model": "fake"}})
        res = api("POST", f"/projects/{shop['id']}/flows", {
            "workflow_id": "clarify", "title": "Export the price list", "allow_fake": True, "allow_dirty": True,
            "request": "Add an export of the price list as a CSV file."})
        tid = res.get("thread_id") or (res.get("thread") or {}).get("thread_id")
        check(bool(tid), "the clarify flow started on the fake model")
        w = wait_until("the clarify flow's first question", lambda: waiting(shop["id"], tid), timeout=300)
        check(w.get("kind") == "clarify" and bool(w.get("questions")), f"it waits at a question: {w.get('title')}")
        first_id = w.get("id")
        picked = walk(out, shop, notes, tid).get("picked") or "?"
        after = wait_until("the next question", lambda: (n := waiting(shop["id"], tid)) and n.get("id") != first_id and n,
                           timeout=120)
        check(True, f"the engine took the answer and asks the next question: {after.get('title')}")
        said = json.dumps(api("GET", f"/projects/{shop['id']}/flows/{tid}/decisions"), ensure_ascii=False)
        check(NOTE in said and picked in said, f"the flow's decisions hold the picked option ({picked}) and the note")
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
