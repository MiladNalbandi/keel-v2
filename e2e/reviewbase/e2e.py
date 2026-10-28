#!/usr/bin/env python3
"""keel 0.18.65 the stale-base field report (R3, R5), end to end, on a throw-away keel only (keel-e2e-reviewbase-<random>
on a free port, its own volume). It never touches the person's own keel.

    python3 e2e/reviewbase/e2e.py --image keel-v2:dev [--keep] [--out DIR]

The workspace holds one git project (shop) with two bare remotes that disagree: origin (master: A, O1) and github
(main: A, M1, M2, M3). The local master (A, O1, X1) is 3 behind and 2 ahead of github/main; the folder is on feat/x, one
file on top of github/main. .keel/config.yml says base_branch: master and git.remote: github.

Headless Chromium (e2e/reviewbase/walk.mjs, through `npx -y -p playwright@1`) checks, stage review: the Review page's
branch review compares against github/main (1 file), says the local master is 3 behind and 2 ahead, and "Compare with"
your local master shows 4 files and says why; the classic Code › Review shows the same warning. Stage base: with
base_branch: main (no local main), Start a flow asks (use github/main, track it, pick another), and Settings › General ›
Base branch shows what keel compares with. Screenshots go to --out.
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
E = lib.Env.make("reviewbase")
NAME, VOLUME, PORT = E.name, E.volume, E.port
CHECKS: list[str] = []
GIT = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false"]


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


def commit(repo: Path, rel: str, text: str, msg: str) -> None:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    sh(*GIT, "add", rel, cwd=repo)
    sh(*GIT, "commit", "-q", "-m", msg, cwd=repo)


def workspace(root: Path) -> Path:
    """/workspace/shop with origin and github as bare repos in /workspace/remotes (their URLs as the container sees them)."""
    ws = root / "ws"
    repo, remotes = ws / "shop", ws / "remotes"
    repo.mkdir(parents=True)
    remotes.mkdir()
    sh(*GIT, "init", "-q", "-b", "master", cwd=repo)
    commit(repo, "README.md", "# Shop\n", "A")
    origin, github = remotes / "origin.git", remotes / "github.git"
    sh(*GIT, "init", "-q", "--bare", "-b", "master", str(origin), cwd=ws)
    sh(*GIT, "init", "-q", "--bare", "-b", "main", str(github), cwd=ws)
    sh(*GIT, "push", "-q", str(github), "master:main", cwd=repo)
    commit(repo, "old/copy.txt", "an older copy of a change\n", "O1")
    sh(*GIT, "remote", "add", "origin", str(origin), cwd=repo)
    sh(*GIT, "push", "-q", "-u", "origin", "master", cwd=repo)
    up = ws / "up"
    sh(*GIT, "clone", "-q", str(github), str(up), cwd=ws)
    for i in (1, 2, 3):
        commit(up, f"landed/m{i}.txt", f"{i}\n", f"M{i}")
    sh(*GIT, "push", "-q", "origin", "main", cwd=up)
    shutil.rmtree(up)
    sh(*GIT, "remote", "add", "github", str(github), cwd=repo)
    sh(*GIT, "fetch", "-q", "github", cwd=repo)
    sh(*GIT, "symbolic-ref", "refs/remotes/github/HEAD", "refs/remotes/github/main", cwd=repo)
    commit(repo, "local.txt", "only here\n", "X1")
    sh(*GIT, "checkout", "-q", "--no-track", "-b", "feat/x", "github/main", cwd=repo)
    commit(repo, "feature.txt", "the change\n", "feat: the change")
    # the URLs as the container sees the workspace
    sh(*GIT, "remote", "set-url", "origin", "/workspace/remotes/origin.git", cwd=repo)
    sh(*GIT, "remote", "set-url", "github", "/workspace/remotes/github.git", cwd=repo)
    (repo / ".keel").mkdir()
    (repo / ".keel/config.yml").write_text("# keel\nbase_branch: master\ngit:\n  remote: github\n")
    return ws


def start(image: str, ws: Path) -> None:
    clean()
    sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data",
       "-v", f"{ws}:/workspace", "-e", "KEEL_FAKE=1", image)
    wait_until(f"{NAME} ({image}) to be healthy", lambda: api("GET", "/health"), timeout=300)


def walk(out: Path, shop: dict, stage: str) -> dict:
    print(f"   the web, in headless Chromium ({stage})")
    cmd = ["npx", "-y", "-p", "playwright@1", "node", str(HERE / "walk.mjs"), f"http://127.0.0.1:{PORT}", str(out),
           shop["name"], shop["id"], stage]
    env = {**os.environ, "KEEL_SIGNIN": keelauth.signin_url(f"http://127.0.0.1:{PORT}", NAME)}
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    line = next((x for x in reversed(r.stdout.splitlines()) if x.startswith("{")), "")
    if not line:
        raise AssertionError(f"walk.mjs gave no answer ({r.returncode}): {(r.stderr or r.stdout)[-2000:]}")
    res = json.loads(line)
    (out / f"walk-{stage}.json").write_text(json.dumps(res, indent=1))
    for c in res["checks"]:
        print(("  ✓ " if c["ok"] else "  ✗ ") + c["what"] + ("" if c["ok"] else f"\n      {c['detail']}"))
    CHECKS.extend(c["what"] for c in res["checks"] if c["ok"])
    if any(not c["ok"] for c in res["checks"]):
        raise AssertionError(f"FAILED: web check(s); details in {out / f'walk-{stage}.json'}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="keel-v2:dev")
    ap.add_argument("--keep", action="store_true", help="leave the keel running (and its volume)")
    ap.add_argument("--out", default="", help="where the screenshots and results go (default: a temp folder)")
    a = ap.parse_args()
    E.keep = a.keep
    tmp = Path(tempfile.mkdtemp(prefix="reviewbase_e2e_"))
    out = Path(a.out) if a.out else tmp / "out"
    out.mkdir(parents=True, exist_ok=True)
    t0, ok = time.time(), False
    try:
        ws = workspace(tmp)
        start(a.image, ws)
        projects = wait_until("the project", lambda: api("GET", "/projects") or None, timeout=60)
        shop = next(p for p in projects if p["name"] == "shop")
        api("PUT", f"/projects/{shop['id']}/plugins/review", {"enabled": True})
        v = api("GET", f"/projects/{shop['id']}/review/view?key=branch:feat/x")
        check(v["base"] == "github/main" and [f["path"] for f in v["files"]] == ["feature.txt"],
              f"the api's branch review compares against {v['base']}: {[f['path'] for f in v['files']]}")
        check("3 commits behind and 2 ahead of github/main" in (v.get("base_warning") or ""), f"and warns: {v.get('base_warning')}")
        walk(out, shop, "review")
        # base_branch names a branch with no local copy: the start asks, Settings shows it
        (ws / "shop/.keel/config.yml").write_text("# keel\nbase_branch: main\ngit:\n  remote: github\n")
        sc = api("GET", f"/projects/{shop['id']}/start-check")
        check((sc.get("base_missing") or {}).get("remote") == "github/main", f"the start check says: {(sc.get('base_missing') or {}).get('text')}")
        code, text = http("POST", f"/api/projects/{shop['id']}/flows",
                          {"workflow_id": "feature", "title": "Totals", "allow_fake": True, "allow_dirty": True})
        check(code == 409 and "names no local branch" in text, f"a start without a base is asked first ({code}): {text[:160]}")
        walk(out, shop, "base")
        cfg = (ws / "shop/.keel/config.yml").read_text()
        check("base_branch: github/main" in cfg, f"the person's choice is in .keel/config.yml: {cfg.strip().splitlines()[1]}")
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
