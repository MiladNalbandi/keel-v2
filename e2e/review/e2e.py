#!/usr/bin/env python3
"""The Code Review plugin end to end: a throw-away keel (keel-lab, port 8099) from an image, a fake GitHub and GitLab
on the host (fake_host.py, port 8097), and two scratch repos whose origin is github.com / gitlab.test, rewritten by
git's insteadOf to bare repos with the pull request refs. No real token, no real host, the fake model for keel's AI.

    python3 e2e/review/e2e.py --image keel-v2:0.14.0-review-dev [--keep]

It walks what a reviewer does: the list (to review, assigned, mine), one pull request (files, threads, checks, diff),
go to declaration and find usages, a line comment and Submit (request changes), a reply and resolve, Approve, merging
your own pull request (and not someone else's), checking one out, keel's overview and checked findings, and a GitLab
merge request approved with a line comment. Without --keep it removes keel-lab, its volume and the fake host at the end.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

NAME, VOLUME, PORT, HOST_PORT = "keel-lab", "keel-lab-data", 8099, 8097
BASE = f"http://127.0.0.1:{PORT}/api"
HERE = Path(__file__).resolve().parent
CHECKS: list[str] = []


def sh(*a: str, cwd: Path | None = None, check: bool = True) -> str:
    r = subprocess.run(list(a), cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(a)}: {r.stderr or r.stdout}")
    return r.stdout


def git(root: Path, *a: str) -> str:
    return sh("git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false", *a, cwd=root)


def call(method: str, path: str, body: object | None = None, ok: tuple[int, ...] = (200,)):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text, code, kind = r.read().decode(), r.status, r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        text, code, kind = e.read().decode(), e.code, e.headers.get("content-type", "")
    if code not in ok:
        raise AssertionError(f"{method} {path} → {code}: {text[:500]}")
    return json.loads(text) if "json" in kind and text else text


def check(cond: bool, what: str):
    if not cond:
        raise AssertionError("FAILED: " + what)
    CHECKS.append(what)
    print("  ✓", what)


def wait(what: str, fn, timeout: float = 90, every: float = 1.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what} (last: {str(last)[:300]})")


def repos(ws: Path) -> tuple[str, str]:
    """shop: main + feat/paging (2 commits); bare remotes with refs/pull/7, refs/pull/8 and refs/merge-requests/3."""
    shop = ws / "shop"
    shop.mkdir()
    (shop / "src").mkdir()
    (shop / "src/Prefs.kt").write_text("class Prefs {\n    fun pageSize(user: String) = 20\n}\n")
    (shop / "src/Orders.kt").write_text("class Orders(val prefs: Prefs) {\n    fun list(user: String) = prefs.pageSize(user)\n}\n")
    git(shop, "init", "-q", "-b", "main")
    git(shop, "add", "-A")
    git(shop, "commit", "-qm", "first")
    git(shop, "switch", "-qc", "feat/paging")
    (shop / "src/Prefs.kt").write_text("class Prefs {\n    private val cache = mutableMapOf<String, Int>()\n    fun savePageSize(user: String, size: Int) {\n"
                                       "        cache[user] = size\n    }\n    fun pageSize(user: String) = cache[user] ?: 20\n}\n")
    git(shop, "commit", "-qam", "Save the page size")
    (shop / "src/test").mkdir()
    (shop / "src/test/PrefsTest.kt").write_text("class PrefsTest {\n    fun saves() { Prefs().savePageSize(\"a\", 50) }\n}\n")
    git(shop, "add", "-A")
    git(shop, "commit", "-qm", "Test it")
    head = git(shop, "rev-parse", "HEAD").strip()
    git(shop, "switch", "-q", "main")
    for path, refs in (("acme/shop", ["feat/paging:refs/pull/7/head", "feat/paging:refs/pull/8/head"]), ("group/shop", ["feat/paging:refs/merge-requests/3/head"])):
        bare = ws / "remotes" / f"{path}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        sh("git", "init", "-q", "--bare", str(bare))
        git(shop, "push", "-q", str(bare), "main", "feat/paging", *refs)
    gl = ws / "shop-gl"
    shutil.copytree(shop, gl)
    git(shop, "remote", "add", "origin", "https://github.com/acme/shop.git")
    git(shop, "config", "url.file:///workspace/remotes/.insteadOf", "https://github.com/")
    git(gl, "remote", "add", "origin", "https://gitlab.test/group/shop.git")
    git(gl, "config", "url.file:///workspace/remotes/.insteadOf", "https://gitlab.test/")
    git(shop, "branch", "-q", "-D", "feat/paging")            # the reviewer does not have the branch yet
    return head, str(ws)


def state_file(ws: Path, head: str) -> Path:
    st = {"me": "me",
          "github": {"acme/shop": {
              "7": {"number": 7, "title": "Save the page size", "author": "ana", "branch": "feat/paging", "base": "main", "head": head, "state": "open",
                    "reviewers": ["me"], "assignees": ["me"], "body": "Closes ORD-88: the page size is kept per user.",
                    "threads": [{"id": "PRRT_1", "path": "src/Prefs.kt", "line": 6, "side": "RIGHT", "resolved": False,
                                 "comments": [{"id": 11, "author": "bo", "body": "Should 20 come from config?"}]}]},
              "8": {"number": 8, "title": "My paging", "author": "me", "branch": "feat/paging", "base": "main", "head": head, "state": "open"}}},
          "gitlab": {"group/shop": {"3": {"number": 3, "title": "Save the page size", "author": "ana", "branch": "feat/paging", "base": "main", "head": head,
                                          "state": "open", "reviewers": ["me"]}}}}
    f = ws / "fake-host.json"
    f.write_text(json.dumps(st))
    return f


def host_calls(f: Path, method: str, end: str) -> list[dict]:
    return [c for c in json.loads(f.read_text())["calls"] if c["method"] == method and c["path"].endswith(end)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    ws = Path(tempfile.mkdtemp(prefix="keel-review-e2e-"))
    fake = None
    try:
        head, _ = repos(ws)
        sf = state_file(ws, head)
        log = open(ws / "fake-host.log", "w")
        fake = subprocess.Popen([sys.executable, str(HERE / "fake_host.py"), "--port", str(HOST_PORT), "--state", str(sf)],
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        sh("docker", "rm", "-f", NAME, check=False)
        sh("docker", "volume", "rm", VOLUME, check=False)
        sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data", "-v", f"{ws}:/workspace",
           "--add-host", "host.docker.internal:host-gateway",
           "-e", f"KEEL_REVIEW_GITHUBAPI=http://host.docker.internal:{HOST_PORT}", "-e", f"KEEL_REVIEW_GITLABAPI=http://host.docker.internal:{HOST_PORT}/api/v4",
           args.image)
        wait("keel-lab", lambda: _ok("/health"), timeout=180, every=2)
        print(f"keel-lab on http://127.0.0.1:{PORT}, repos in {ws}")

        print("1. the plugin, the token, GitLab")
        gh = call("POST", "/projects", {"root": "/workspace/shop"})["id"]
        gl = call("POST", "/projects", {"root": "/workspace/shop-gl"})["id"]
        call("GET", f"/projects/{gh}/review/prs", ok=(409,))
        check(True, "the Review window is off until the plugin is on")
        for p in (gh, gl):
            call("PUT", f"/projects/{p}/plugins/review", {"enabled": True})
        call("PUT", "/secrets/GITHUB_REPO_TOKEN", {"value": "gh-e2e-token"})
        call("PUT", "/gitlab", {"url": "https://gitlab.test", "token": "gl-e2e-token"})
        check("review" in [x["name"] for x in call("GET", f"/projects/{gh}/plugins") if x["enabled"]], "Code Review is on for both projects")

        print("2. the list")
        lst = call("GET", f"/projects/{gh}/review/prs?filter=review")
        check(lst["host"]["kind"] == "github" and [x["number"] for x in lst["prs"]] == [7], "to review: #7")
        check([x["number"] for x in call("GET", f"/projects/{gh}/review/prs?filter=assigned")["prs"]] == [7], "assigned to me: #7")
        check([x["number"] for x in call("GET", f"/projects/{gh}/review/prs?filter=mine")["prs"]] == [8], "mine: #8")

        print("3. one pull request")
        v = call("GET", f"/projects/{gh}/review/view?key=pr:7")
        check(sorted(f["path"] for f in v["files"]) == ["src/Prefs.kt", "src/test/PrefsTest.kt"], "its files, fetched from the remote (never built)")
        check(v["threads"][0]["comments"][0]["body"] == "Should 20 come from config?", "the thread on its line")
        check(len(v["checks"]) == 2 and all(c["state"] == "success" for c in v["checks"]), "CI checks 2/2")
        d = call("GET", f"/projects/{gh}/review/diff?key=pr:7&path=src/Prefs.kt")
        check("+    fun savePageSize(user: String, size: Int) {" in d["diff"], "the diff")
        decl = call("GET", f"/projects/{gh}/review/definition?key=pr:7&symbol=savePageSize")["places"]
        check(decl and decl[0]["path"] == "src/Prefs.kt" and decl[0]["line"] == 3, "go to declaration in the PR's code")
        uses = call("GET", f"/projects/{gh}/review/usages?key=pr:7&symbol=savePageSize")["places"]
        check({u["path"] for u in uses} == {"src/Prefs.kt", "src/test/PrefsTest.kt"}, "find usages, with the test")

        print("4. comment, submit, reply, resolve, approve")
        call("POST", f"/projects/{gh}/review/drafts", {"key": "pr:7", "path": "src/Prefs.kt", "line": 4, "body": "issue (blocking): save it in the repository too"})
        sent = call("POST", f"/projects/{gh}/review/submit", {"key": "pr:7", "event": "REQUEST_CHANGES", "body": "One blocker."})
        check(sent["posted"] == 1 and host_calls(sf, "POST", "/pulls/7/reviews")[-1]["body"]["event"] == "REQUEST_CHANGES", "one review with the line comment")
        t = [x for x in sent["view"]["threads"] if x["id"] == "PRRT_1"][0]
        call("POST", f"/projects/{gh}/review/threads/{t['id']}/reply", {"key": "pr:7", "body": "Yes, PagingProperties"})
        r = call("POST", f"/projects/{gh}/review/threads/{t['id']}/resolve", {"key": "pr:7", "resolved": True})
        check([x for x in r["threads"] if x["id"] == "PRRT_1"][0]["resolved"], "a reply and the thread resolved")
        call("POST", f"/projects/{gh}/review/submit", {"key": "pr:7", "event": "APPROVE", "body": ""})
        check(host_calls(sf, "POST", "/pulls/7/reviews")[-1]["body"]["event"] == "APPROVE", "approve with one click")

        print("5. merge your own, not someone else's")
        call("POST", f"/projects/{gh}/review/merge", {"key": "pr:7"}, ok=(409,))
        check(not host_calls(sf, "PUT", "/pulls/7/merge"), "#7 (ana's) is not merged by keel")
        m = call("POST", f"/projects/{gh}/review/merge", {"key": "pr:8", "method": "squash", "delete_branch": False})
        check(m["merged"] and m["view"]["state"] == "merged", "#8 (mine) merged with squash")

        print("6. check out")
        co = call("POST", f"/projects/{gh}/review/checkout", {"key": "pr:7"})
        check(co["branch"] == "feat/paging" and sh("git", "-C", str(ws / "shop"), "branch", "--show-current").strip() == "feat/paging", "#7 checked out to run it")

        print("7. keel's AI (the fake model)")
        call("POST", f"/projects/{gh}/review/ai/overview", {"key": "pr:7"})
        o = wait("the overview", lambda: (x := call("GET", f"/projects/{gh}/review/ai?key=pr:7")["overview"]) and x["status"] != "running" and x)
        check(o["status"] == "done" and o["result"]["summary"], f"overview: {o['result']['summary'][:60]}")
        call("POST", f"/projects/{gh}/review/ai/findings", {"key": "pr:7"})
        f = wait("the findings", lambda: (x := call("GET", f"/projects/{gh}/review/ai?key=pr:7")["findings"]) and x["status"] != "running" and x, timeout=150)
        res = f["result"]
        check(f["status"] == "done" and len(res["findings"]) == 1 and res["findings"][0]["check"] == "confirmed", "one finding, checked again and confirmed")
        check(len(res["rejected"]) == 1, "one claim rejected by the check (kept with why)")
        st = call("POST", f"/projects/{gh}/review/ai/findings/{res['findings'][0]['id']}", {"key": "pr:7", "decision": "dismissed", "why": "fixed in the next PR"})
        check(st["decisions"][res["findings"][0]["id"]]["decision"] == "dismissed", "dismissed with a reason")

        print("8. a GitLab merge request")
        g = call("GET", f"/projects/{gl}/review/prs?filter=review")
        check(g["host"]["kind"] == "gitlab" and [x["number"] for x in g["prs"]] == [3], "GitLab: !3 to review")
        call("GET", f"/projects/{gl}/review/view?key=pr:3")
        call("POST", f"/projects/{gl}/review/drafts", {"key": "pr:3", "path": "src/Prefs.kt", "line": 4, "body": "Save it"})
        call("POST", f"/projects/{gl}/review/submit", {"key": "pr:3", "event": "APPROVE", "body": "Good"})
        check(host_calls(sf, "POST", "/merge_requests/3/discussions") and host_calls(sf, "POST", "/merge_requests/3/approve"), "a line discussion and the approval")
        print(f"\nPASSED: {len(CHECKS)} checks")
        return 0
    except Exception as e:
        print(f"\n{e}", file=sys.stderr)
        print(sh("docker", "logs", "--tail", "40", NAME, check=False)[-3000:], file=sys.stderr)
        return 1
    finally:
        if not args.keep:
            sh("docker", "rm", "-f", NAME, check=False)
            sh("docker", "volume", "rm", VOLUME, check=False)
            if fake:
                fake.terminate()
            shutil.rmtree(ws, ignore_errors=True)
        else:
            print(f"keel-lab stays on http://127.0.0.1:{PORT} (fake host pid {fake.pid if fake else '-'}, repos {ws}). "
                  f"Remove: docker rm -f {NAME}; docker volume rm {VOLUME}; kill {fake.pid if fake else ''}")


def _ok(path: str):
    try:
        return call("GET", path)
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
