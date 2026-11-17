#!/usr/bin/env python3
"""0.32 a fair re-test of the code graph for KeelBot: the same questions with the code graph off and on, on a running
keel, and what each answer cost. The code graph stays off by default (agent_knowledge.FALLBACK); turn it on for the
helper agent only when this shows it pays (the research: it helps most on questions that span several files, and when it
is offered next to grep, never forced first: reports/Coding agent harness best practices.md).

    KEEL_URL=http://127.0.0.1:8080 KEEL_KEY=<api key> scripts/measure-code-graph.py --project shop [--runs 2]
        [--question "..."] ...

It changes the helper agent's knowledge setting (Agents page) for the run and puts it back at the end. The project's
code graph index must be ready (Code page: Index ready); it says so when it is not."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

QUESTIONS = [
    "Where is the price of an order computed, and which functions call it?",
    "What happens, step by step, when a user checks out? Name the files and functions.",
    "Which tests cover the tax rounding, and where is the rounding done?",
]


def call(method: str, path: str, body=None):
    url = os.environ.get("KEEL_URL", "http://127.0.0.1:8080").rstrip("/") + "/api" + path
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + os.environ.get("KEEL_KEY", ""),
                                          "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path}: {e.code} {e.read()[:300].decode('utf8', 'replace')}")


def ask(pid: str, question: str) -> dict:
    s = call("POST", f"/projects/{pid}/helper/sessions", {"mode": "ask", "title": question[:60]})
    call("POST", f"/projects/{pid}/helper/sessions/{s['id']}/turn", {"text": question})
    t0 = time.time()
    while time.time() - t0 < 600:
        time.sleep(3)
        s = call("GET", f"/projects/{pid}/helper/sessions/{s['id']}")
        if not s.get("busy") and s.get("status") != "running":
            break
    last = next((m for m in reversed(s.get("messages") or []) if m.get("role") == "helper"), {})
    d = last.get("data") or {}
    call("DELETE", f"/projects/{pid}/helper/sessions/{s['id']}")
    return {"in": d.get("tokens_in", 0), "cached": d.get("tokens_cached", 0), "out": d.get("tokens_out", 0),
            "cost": d.get("cost_usd", 0.0), "s": round((d.get("ms") or 0) / 1000, 1),
            "answer": (last.get("text") or "").strip().splitlines()[0][:80] if last.get("text") else "(no answer)"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--question", action="append")
    a = ap.parse_args()
    pid, questions = a.project, a.question or QUESTIONS
    idx = call("GET", f"/projects/{pid}/index")
    if idx.get("status") != "ready":
        print(f"The code graph index of {pid} is {idx.get('status')}: {idx.get('error') or 'build it first (Code page, Rebuild)'}",
              file=sys.stderr)
        return 2
    agent = next((x for x in call("GET", f"/projects/{pid}/agents") if x.get("id") == "helper"), None)
    before = (agent or {}).get("knowledge")
    rows = []
    try:
        for graph in (False, True):
            k = dict(before or {"sections": ["architecture"], "memory": True, "strict": False, "hints": False})
            k["code_graph"] = graph
            call("PUT", f"/projects/{pid}/agents/helper", {"knowledge": k})
            for q in questions:
                for _ in range(a.runs):
                    rows.append({"graph": graph, "q": q, **ask(pid, q)})
    finally:
        if before is not None:
            call("PUT", f"/projects/{pid}/agents/helper", {"knowledge": before})
    print(f"{'graph':<6} {'new':>8} {'cached':>8} {'out':>6} {'cost':>7} {'s':>6}  question / answer")
    for r in rows:
        print(f"{'on' if r['graph'] else 'off':<6} {r['in']:>8} {r['cached']:>8} {r['out']:>6} {r['cost']:>7.3f} {r['s']:>6}  "
              f"{r['q'][:50]} / {r['answer']}")
    for graph in (False, True):
        mine = [r for r in rows if r["graph"] == graph]
        total = sum(r["in"] + r["out"] + r["cached"] // 10 for r in mine)
        print(f"code graph {'on' if graph else 'off'}: {total} tokens (new + out + a tenth of cached), "
              f"${sum(r['cost'] for r in mine):.3f}, {len(mine)} answers")
    print("Read the answers too: fewer tokens with a worse answer is no win.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
