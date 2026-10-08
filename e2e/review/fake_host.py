#!/usr/bin/env python3
"""A small fake GitHub (REST + the GraphQL review threads) and GitLab (REST v4 under /api/v4) for the Code Review
plugin's end-to-end test. Run it on the host; keel's api in the container reaches it at host.docker.internal.

    python3 e2e/review/fake_host.py --port 8097 --state state.json

The state file says which pull requests exist (written by e2e.py); everything the api sends is appended to calls.
Tokens: github "gh-e2e-token", gitlab "gl-e2e-token" (test values, not real credentials).
"""

from __future__ import annotations

import argparse
import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

GH_TOKEN = "gh-e2e-token"   # test value
GL_TOKEN = "gl-e2e-token"   # test value
LOCK = threading.Lock()
IDS = itertools.count(5000)


class State:
    def __init__(self, path: str):
        self.path = path
        self.data = json.load(open(path))
        self.data.setdefault("calls", [])

    def save(self):
        json.dump(self.data, open(self.path, "w"), indent=1)


def gh_pr(repo: str, p: dict) -> dict:
    return {"number": p["number"], "title": p["title"], "user": {"login": p["author"]}, "body": p.get("body", ""),
            "state": "closed" if p["state"] == "merged" else p["state"], "merged": p["state"] == "merged", "draft": False,
            "updated_at": "2026-10-08T10:00:00Z", "html_url": f"https://github.com/{repo}/pull/{p['number']}",
            "head": {"ref": p["branch"], "sha": p["head"], "repo": {"full_name": repo}}, "base": {"ref": p["base"]},
            "requested_reviewers": [{"login": x} for x in p.get("reviewers", [])], "assignees": [{"login": x} for x in p.get("assignees", [])],
            "mergeable": True, "mergeable_state": "clean"}


def gl_mr(project: str, p: dict) -> dict:
    return {"iid": p["number"], "title": p["title"], "author": {"username": p["author"]}, "description": p.get("body", ""),
            "state": "opened" if p["state"] == "open" else p["state"], "draft": False, "updated_at": "2026-10-08T10:00:00Z",
            "web_url": f"https://gitlab.test/{project}/-/merge_requests/{p['number']}", "source_branch": p["branch"], "target_branch": p["base"],
            "sha": p["head"], "source_project_id": 1, "target_project_id": 1, "reviewers": [{"username": x} for x in p.get("reviewers", [])],
            "assignees": [{"username": x} for x in p.get("assignees", [])], "detailed_merge_status": "mergeable",
            "diff_refs": {"base_sha": "0" * 40, "start_sha": "0" * 40, "head_sha": p["head"]}}


class Handler(BaseHTTPRequestHandler):
    state: State

    def log_message(self, *a):
        pass

    def _send(self, code: int, body):
        data = b"" if code == 204 else json.dumps(body).encode()
        self.send_response(code)
        if code != 204:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _body(self) -> bytes:
        """The request body: Content-Length, or chunked (Java's HttpClient sends chunked bodies)."""
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            out = b""
            while True:
                size = int(self.rfile.readline().strip().split(b";")[0] or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    return out
                out += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _handle(self):
        u = urlparse(self.path)
        raw = self._body()
        body = json.loads(raw) if raw else None
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        with LOCK:
            s = self.state
            s.data["calls"].append({"method": self.command, "path": u.path, "query": u.query, "body": body})
            if u.path.startswith("/api/v4/"):
                if self.headers.get("PRIVATE-TOKEN") != GL_TOKEN:
                    return self._send(401, {"message": "401 Unauthorized"})
                code, res = self.gitlab(u.path[len("/api/v4"):], q, body)
            else:
                if self.headers.get("Authorization") != f"Bearer {GH_TOKEN}":
                    return self._send(401, {"message": "Bad credentials"})
                code, res = self.github(u.path, q, body)
            s.save()
        self._send(code, res)

    do_GET = do_POST = do_PUT = do_DELETE = _handle

    # ---- GitHub
    def github(self, path: str, q: dict, body):
        s = self.state.data
        if path == "/user":
            return 200, {"login": s["me"]}
        if path == "/graphql":
            return self.graphql(body)
        parts = path.split("/")
        if len(parts) < 4 or parts[1] != "repos":
            return 404, {"message": "Not Found"}
        repo = f"{parts[2]}/{parts[3]}"
        prs = s["github"].get(repo)
        if prs is None:
            return 404, {"message": "Not Found"}
        rest = "/" + "/".join(parts[4:])
        cmd = self.command
        if rest == "/pulls":
            head = q.get("head", "").split(":")[-1] or None
            return 200, [gh_pr(repo, p) for p in prs.values() if p["state"] == "open" and (head is None or p["branch"] == head)]
        seg = rest.strip("/").split("/")
        if seg[0] == "pulls" and len(seg) >= 2:
            p = prs.get(seg[1])
            if not p:
                return 404, {"message": "Not Found"}
            if len(seg) == 2:
                return 200, gh_pr(repo, p)
            if seg[2] == "reviews":
                if cmd == "GET":
                    return 200, p.setdefault("reviews", [])
                for c in body.get("comments", []):
                    p.setdefault("threads", []).append({"id": f"PRRT_{next(IDS)}", "path": c["path"], "line": c["line"], "side": c.get("side", "RIGHT"),
                                                        "resolved": False, "comments": [{"id": next(IDS), "author": s["me"], "body": c["body"]}]})
                state = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}.get(body.get("event"), "COMMENTED")
                p.setdefault("reviews", []).append({"user": {"login": s["me"]}, "state": state})
                if body.get("body"):
                    p.setdefault("conversation", []).append({"id": next(IDS), "author": s["me"], "body": body["body"]})
                return 200, {"id": next(IDS), "html_url": f"https://github.com/{repo}/pull/{p['number']}#review"}
            if seg[2] == "comments" and len(seg) == 5 and seg[4] == "replies":
                for t in p.get("threads", []):
                    if str(t["comments"][0]["id"]) == seg[3]:
                        t["comments"].append({"id": next(IDS), "author": s["me"], "body": body["body"]})
                        return 201, {"id": t["comments"][-1]["id"]}
                return 404, {"message": "Not Found"}
            if seg[2] == "merge":
                if body.get("sha") != p["head"]:
                    return 409, {"message": "Head branch was modified"}
                p["state"] = "merged"
                return 200, {"merged": True, "message": "Pull Request successfully merged"}
        if seg[0] == "commits" and len(seg) == 3:
            if seg[2] == "check-runs":
                return 200, {"check_runs": [{"name": "build", "conclusion": "success", "status": "completed", "html_url": None},
                                            {"name": "test", "conclusion": "success", "status": "completed", "html_url": None}]}
            return 200, {"statuses": []}
        if seg[0] == "issues" and len(seg) == 3 and seg[2] == "comments":
            p = prs.get(seg[1]) or {}
            return 200, [{"id": c["id"], "user": {"login": c["author"]}, "body": c["body"], "created_at": "2026-10-08T09:00:00Z"} for c in p.get("conversation", [])]
        if seg[0] == "git" and seg[1] == "refs":
            return 204, None
        return 404, {"message": "Not Found"}

    def graphql(self, body):
        s = self.state.data
        qtext = body.get("query", "")
        v = body.get("variables") or {}
        if "resolveReviewThread" in qtext or "unresolveReviewThread" in qtext:
            for prs in s["github"].values():
                for p in prs.values():
                    for t in p.get("threads", []):
                        if t["id"] == v.get("id"):
                            t["resolved"] = "unresolve" not in qtext
                            return 200, {"data": {"x": {"thread": {"id": t["id"], "isResolved": t["resolved"]}}}}
            return 200, {"errors": [{"message": "not found"}]}
        p = s["github"].get(f"{v.get('o')}/{v.get('r')}", {}).get(str(v.get("n")))
        nodes = [{"id": t["id"], "isResolved": t["resolved"], "isOutdated": False, "path": t["path"], "line": t["line"], "originalLine": t["line"],
                  "diffSide": t["side"], "comments": {"nodes": [{"id": f"C{c['id']}", "databaseId": c["id"], "body": c["body"], "createdAt": "2026-10-08T09:00:00Z",
                                                                  "url": None, "author": {"login": c["author"]}} for c in t["comments"]]}}
                 for t in (p or {}).get("threads", [])]
        return 200, {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": nodes}}}}}

    # ---- GitLab
    def gitlab(self, path: str, q: dict, body):
        s = self.state.data
        if path == "/user":
            return 200, {"username": s["me"]}
        parts = path.split("/")
        if len(parts) < 3 or parts[1] != "projects":
            return 404, {"message": "404 Not Found"}
        project = unquote(parts[2])
        mrs = s.get("gitlab", {}).get(project)
        if mrs is None:
            return 404, {"message": "404 Project Not Found"}
        seg = parts[3:]
        if seg == ["merge_requests"]:
            src = q.get("source_branch")
            return 200, [gl_mr(project, p) for p in mrs.values() if p["state"] == "open" and (not src or p["branch"] == src)]
        if seg and seg[0] == "merge_requests" and len(seg) >= 2:
            p = mrs.get(seg[1])
            if not p:
                return 404, {"message": "404 Not found"}
            if len(seg) == 2:
                return 200, gl_mr(project, p)
            what = seg[2]
            if what == "pipelines":
                return 200, [{"id": 7, "status": "success", "web_url": None}]
            if what == "approvals":
                return 200, {"approved_by": [{"user": {"username": x}} for x in p.get("approved_by", [])]}
            if what == "approve":
                p.setdefault("approved_by", []).append(s["me"])
                return 201, {}
            if what == "unapprove":
                return 201, {}
            if what == "notes":
                p.setdefault("conversation", []).append({"id": next(IDS), "author": s["me"], "body": body["body"]})
                return 201, {}
            if what == "discussions" and len(seg) == 3:
                if self.command == "POST":
                    pos = body["position"]
                    t = {"id": str(next(IDS)), "path": pos["new_path"], "line": pos.get("new_line") or pos.get("old_line"), "side": "RIGHT",
                         "resolved": False, "comments": [{"id": next(IDS), "author": s["me"], "body": body["body"]}]}
                    p.setdefault("threads", []).append(t)
                    return 201, {"id": t["id"]}
                out = [{"id": t["id"], "notes": [{"id": c["id"], "body": c["body"], "author": {"username": c["author"]}, "created_at": "2026-10-08T09:00:00Z",
                                                  "system": False, "resolvable": True, "resolved": t["resolved"],
                                                  "position": {"new_path": t["path"], "old_path": t["path"], "new_line": t["line"]} if i == 0 else None}
                                                 for i, c in enumerate(t["comments"])]} for t in p.get("threads", [])]
                out += [{"id": f"c{c['id']}", "notes": [{"id": c["id"], "body": c["body"], "author": {"username": c["author"]},
                                                          "created_at": "2026-10-08T09:00:00Z", "system": False, "resolvable": False}]}
                        for c in p.get("conversation", [])]
                return 200, out
        return 404, {"message": "404 Not found"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--state", required=True)
    a = ap.parse_args()
    Handler.state = State(a.state)
    ThreadingHTTPServer(("0.0.0.0", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
