#!/usr/bin/env python3
"""keel 0.18.65 Settings › Review, end to end, on a throw-away keel only (keel-e2e-reviewsettings-<random> on a free
port, its own volume). It never touches the person's own keel.

    python3 e2e/reviewsettings/e2e.py --image keel-v2:dev [--keep] [--out DIR]

The workspace holds one small git project (shop) that keel knows (.keel/config.yml) with a branch that changes a price
function, and the Code Review plugin on. Headless Chromium (e2e/reviewsettings/walk.mjs) opens Settings › Review: two
reviewers and keel's lenses; it picks three reviewers, adds the lens "Money rounding" on reviewer C, and opens the
Review page of the branch: the text above Find problems names the three reviewers. Then this script checks the files in
the project: .keel/lenses/money-rounding.md in the lens format, and .keel/config.yml's `review:` block with three
reviewers and the lens on C, the rest of the file as it was. "Back to keel's default" takes the block out again.
Screenshots go to --out. The container and the volume are removed at the end (--keep: they stay).
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

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import keelauth  # noqa: E402  (e2e/keelauth.py: keel's sign-in)
import lib  # noqa: E402  (e2e/lib.py: a free port and unique names)
E = lib.Env.make("reviewsettings")
NAME, VOLUME, PORT = E.name, E.volume, E.port
CHECKS: list[str] = []
CONFIG = "version: 4\n# the shop's own settings\nknowledge:\n  build: false\n"

FILES = {
    "README.md": "# Shop\n\nA tiny shop: prices with tax, in Python.\n",
    ".keel/config.yml": CONFIG,
    "shop/price.py": "def with_tax(cents: int, rate: float) -> int:\n    return round(cents * (1 + rate))\n",
    "tests/test_price.py": "from shop.price import with_tax\n\n\ndef test_tax():\n    assert with_tax(1000, 0.21) == 1210\n",
}
CHANGE = {
    "shop/price.py": "def with_tax(cents: int, rate: float) -> int:\n    return round(cents * (1 + rate))\n\n\n"
                     "def discount(cents: int, percent: float) -> float:\n    return cents * (1 - percent / 100)\n",
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
    """One git project on a branch that changes a file against main."""
    ws = root / "ws"
    repo = ws / "shop"
    git = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false"]
    for rel, text in FILES.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    sh(*git, "init", "-q", "-b", "main", cwd=repo)
    sh(*git, "add", "-A", cwd=repo)
    sh(*git, "commit", "-q", "-m", "The shop", cwd=repo)
    sh(*git, "switch", "-q", "-c", "feat/discount", cwd=repo)
    for rel, text in CHANGE.items():
        (repo / rel).write_text(text)
    sh(*git, "commit", "-q", "-am", "A discount", cwd=repo)
    # the container's keel runs as another user: it writes .keel/ there
    sh("chmod", "-R", "a+rwX", str(ws))
    return ws


def start(image: str, ws: Path) -> None:
    clean()
    sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data",
       "-v", f"{ws}:/workspace", "-e", "KEEL_FAKE=1", image)
    wait_until(f"{NAME} ({image}) to be healthy", lambda: api("GET", "/health"), timeout=300)
    api("PUT", "/help/welcome", {"seen": True, "how": "e2e"})     # 0.18.66: the first-run welcome would cover the page


def walk(out: Path, shop: dict) -> dict:
    print("   the web, in headless Chromium")
    cmd = ["npx", "-y", "-p", "playwright@1", "node", str(HERE / "walk.mjs"), f"http://127.0.0.1:{PORT}", str(out),
           shop["name"], "branch:feat/discount"]
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
    tmp = Path(tempfile.mkdtemp(prefix="reviewsettings_e2e_"))
    out = Path(a.out) if a.out else tmp / "out"
    out.mkdir(parents=True, exist_ok=True)
    t0, ok = time.time(), False
    try:
        ws = workspace(tmp)
        repo = ws / "shop"
        start(a.image, ws)
        projects = wait_until("the project", lambda: (p := api("GET", "/projects")) and p, timeout=60)
        shop = next(p for p in projects if p["name"] == "shop")
        api("PUT", f"/projects/{shop['id']}/plugins/review", {"enabled": True})
        v = api("GET", f"/projects/{shop['id']}/review-setup")
        check(len(v["config"]["reviewers"]) == 2 and v["configured"], "Settings › Review starts with keel's two reviewers")
        walk(out, shop)

        lens = repo / ".keel/lenses/money-rounding.md"
        check(lens.is_file(), "the new lens is a file in the project: .keel/lenses/money-rounding.md")
        text = lens.read_text()
        check(text.startswith("# Money rounding\n") and "\n## Checklist\n\n- amounts are integers in cents" in text,
              "it has the lens format: a title, a description line, a ## Checklist")
        raw = yaml.safe_load((repo / ".keel/config.yml").read_text())
        revs = raw["review"]["reviewers"]
        check(len(revs) == 3 and "money-rounding" in revs[2]["lenses"],
              ".keel/config.yml has review: with three reviewers and the lens on C")
        check(raw["review"].get("depth") == "full" and raw["review"].get("find_runs") == "classic",
              ".keel/config.yml has the depth (full) and what Find problems runs (classic)")
        check(raw["knowledge"] == {"build": False} and "# the shop's own settings" in (repo / ".keel/config.yml").read_text(),
              "the rest of .keel/config.yml is as it was")
        api("PUT", f"/projects/{shop['id']}/review-setup", {"reset": True})
        raw = yaml.safe_load((repo / ".keel/config.yml").read_text())
        check("review" not in raw, "Back to keel's default takes the review block out again")
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
