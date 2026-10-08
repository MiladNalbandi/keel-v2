#!/usr/bin/env python3
"""keel Product end to end: a throw-away keel (the product edition image) and two scratch repos.

    python3 product/e2e/e2e.py --image keel-v2:0.13.0-product-beta [--keep]          the fake model (repeatable)
    python3 product/e2e/e2e.py --real claude --keep                                  a real model, on a running keel-lab
    python3 product/e2e/e2e.py --running                                             the fake model, on a running keel-lab
                                                                                     (e2e/plugins installs Product into it)

It starts the container keel-lab (port 8099, volume keel-lab-data), makes two git repos with CODEOWNERS, and walks one
initiative through every stage over the HTTP api, the way a person clicks it: keel's questions, the brief (sent back
once), the impact (read only, one analyst per repo), the decision (a disagreement, then Go), the plan (agreed), the
hand-off to keel Tasks, the release, the outcome and the lesson for the team. Every step checks what keel wrote.
Without --keep it removes the container and the volume at the end; with --keep they stay for a browser walk-through.
The fake run uses no model, login or token. --real uses the keel-lab that is already running (with a login copied into
its own volume) and its model; the checks then look at the structure, not at the fake model's words.
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

NAME, VOLUME, PORT = "keel-lab", "keel-lab-data", 8099
BASE = f"http://127.0.0.1:{PORT}/api"
CHECKS: list[str] = []


def sh(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {r.stderr or r.stdout}")
    return r.stdout


def call(method: str, path: str, body: object | None = None, ok: tuple[int, ...] = (200,)) -> object:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode()
            code = r.status
            kind = r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        text, code, kind = e.read().decode(), e.code, e.headers.get("content-type", "")
    if code not in ok:
        raise AssertionError(f"{method} {path} → {code}: {text[:600]}")
    return json.loads(text) if "json" in kind and text else text


def check(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError("FAILED: " + what)
    CHECKS.append(what)
    print("  ✓", what)


def wait_until(what: str, fn, timeout: float = 120.0, every: float = 1.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what} (last: {str(last)[:300]})")


def repo(root: Path, name: str, files: dict[str, str]) -> Path:
    p = root / name
    p.mkdir(parents=True)
    for rel, text in files.items():
        f = p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "first"]):
        sh("git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false", *args, cwd=p)
    return p


def start(image: str, ws: Path) -> None:
    sh("docker", "rm", "-f", NAME, check=False)
    sh("docker", "volume", "rm", VOLUME, check=False)
    sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data", "-v", f"{ws}:/workspace", image)

    def healthy():
        try:
            return call("GET", "/health")
        except Exception:
            return None
    wait_until("keel-lab to start", healthy, timeout=180, every=2)


REAL = {"on": False}


def STAGE_WAIT() -> float:  # noqa: N802
    return 1200.0 if REAL["on"] else 120.0


def host_path(source: str) -> Path:
    """A bind mount's folder on this computer. Docker Desktop reports /host_mnt/<path> for it (its VM's view)."""
    p = Path(source)
    if not p.exists() and source.startswith("/host_mnt/"):
        p = Path(source[len("/host_mnt"):])
    return p


def real_workspace(ws: Path) -> Path:
    """The running keel-lab mounts its own workspace: the scratch repos go there."""
    mounts = json.loads(sh("docker", "inspect", "-f", "{{json .Mounts}}", NAME))
    host = host_path(next(m["Source"] for m in mounts if m["Destination"] == "/workspace"))
    for name in ("web-shop", "payments-api"):
        if not (host / name).exists():
            shutil.copytree(ws / name, host / name)
    shutil.rmtree(ws, ignore_errors=True)
    return host


def answer_questions(iid: str, stage: str) -> None:
    """A real model may ask again (a second round): answer with the recommended options until the gate itself waits."""
    for _ in range(3):
        d = wait_until(f"{iid} to wait at the {stage}", lambda: when(iid, lambda x: x["stage"].get("waiting") or x["initiative"]["status"] == "failed"),
                       timeout=STAGE_WAIT())
        w = d["stage"].get("waiting") or {}
        if w.get("kind") != "clarify":
            return
        call("POST", f"/initiatives/{iid}/approve", {"answers": {}})


def detail(iid: str) -> dict:
    return call("GET", f"/initiatives/{iid}")  # type: ignore[return-value]


def when(iid: str, ok) -> dict | None:
    """The initiative now, when ok(it) holds; None to wait more."""
    d = detail(iid)
    return d if ok(d) else None


def waiting(iid: str, stage: str, answer: bool = True) -> dict:
    def at():
        d = detail(iid)
        if d["initiative"]["status"] == "failed":
            raise AssertionError(f"{iid} failed at the {d['initiative']['stage']}: {d['history'][0]['text']}")
        return d if d["initiative"]["stage"] == stage and d["stage"].get("waiting") else None
    d = wait_until(f"{iid} to wait at the {stage}", at, timeout=STAGE_WAIT(), every=1.0 if not REAL["on"] else 5.0)
    if answer and (d["stage"]["waiting"] or {}).get("kind") == "clarify":
        answer_questions(iid, stage)
        d = wait_until(f"{iid} to wait at the {stage}", at, timeout=STAGE_WAIT(), every=1.0 if not REAL["on"] else 5.0)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="the product edition image (the fake run starts keel-lab from it)")
    ap.add_argument("--real", metavar="PROVIDER", help="use the running keel-lab and this provider's subscription login (claude, codex)")
    ap.add_argument("--model", default="", help="with --real: the model name (default: the provider's default)")
    ap.add_argument("--keep", action="store_true", help="leave keel-lab running for a browser walk-through")
    ap.add_argument("--running", action="store_true", help="the fake run on the keel-lab that is already running (left running)")
    args = ap.parse_args()
    ws = Path(tempfile.mkdtemp(prefix="keel-product-e2e-"))
    try:
        print(f"scratch repos in {ws}")
        repo(ws, "web-shop", {"README.md": "# web-shop\n", "src/price.ts": "export const price = (n: number) => `$${n}`;\n",
                              ".github/CODEOWNERS": "* @acme/web\n"})
        repo(ws, "payments-api", {"README.md": "# payments-api\n", "src/Prices.kt": "fun price(n: Int) = n\n",
                                  ".github/CODEOWNERS": "* @acme/payments\n/docs/ @acme/web\n"})
        if args.real:
            REAL.update(on=True)
            ws = real_workspace(ws)
            call("PUT", "/settings/general", {"default_model": {"provider": args.real, "mode": "subscription", "model": args.model or "default"}})
            print(f"using the running {NAME} with {args.real}")
        elif args.running:
            ws = real_workspace(ws)
            print(f"using the running {NAME} with the fake model")
        else:
            if not args.image:
                ap.error("--image is needed for the fake run")
            print(f"starting {NAME} from {args.image}")
            start(args.image, ws)

        print("1. keel Product is on, as an add-on")
        f = call("GET", "/features")
        check(f["mode"] == "both" and any(a["name"] == "product" for a in f["addons"]), "features: mode both, the product add-on is installed")
        check({s["id"] for s in f["screens"]} >= {"initiatives", "teams"}, "the Product pages are in the menu")
        info = call("GET", "/product")
        check(info["root"].endswith("keel-product"), "the product repo lives in keel's data")

        print("2. repos and teams (from CODEOWNERS)")
        web = call("POST", "/projects", {"root": "/workspace/web-shop"})["id"]
        pay = call("POST", "/projects", {"root": "/workspace/payments-api"})["id"]
        imp = call("POST", "/teams/import-codeowners")
        check(set(imp["teams"]) >= {"web", "payments"}, f"CODEOWNERS gave the teams web and payments ({imp['paths']} paths)")
        call("PATCH", "/teams/payments", {"lead": "Bo", "capacity_days": 12})
        call("PUT", "/teams/payments/pages/definition-of-ready", {"text": "A story has criteria and a contract before it starts."})
        owners = {r["project_id"]: r["teams"] for r in call("GET", "/initiatives/repos")}
        check("payments" in owners[pay] and "web" in owners[web], "each repo has its team")

        print("3. a new idea: keel asks first")
        d = call("POST", "/initiatives", {"title": "Euro prices", "idea": "EU visitors see USD prices. Show euro to them.",
                                          "why_now": "EU launch in Q1", "owner": "Mia", "repos": [web, pay], "start": True})
        iid = d["initiative"]["id"]
        check(iid.startswith("INI-") and d["initiative"]["stage"] == "brief", f"{iid} was created and its brief started")
        d = waiting(iid, "brief", answer=False)
        w = d["stage"]["waiting"]
        asked = w["kind"] == "clarify"
        check(asked or REAL["on"], f"keel asks {len(w.get('questions') or [])} question(s) before the brief")
        if asked:
            call("POST", f"/initiatives/{iid}/approve", {"answers": {w["questions"][0]["id"]: "EU and UK visitors"}})
            answer_questions(iid, "brief")

        print("4. the brief: sent back once, then approved")
        d = wait_until("the brief v1", lambda: when(iid, lambda x: x["docs"].get("brief") and x["stage"].get("waiting")),
                       timeout=STAGE_WAIT())
        check(d["docs"]["brief"]["version"] == 1 and "metric" in d["docs"]["brief"]["text"].lower(), "keel wrote the brief v1 (with its metric)")
        if asked:
            check(any(q["role"] == "keel-asked" and q["answer"] == "EU and UK visitors" for q in d["questions"]), "the answer is kept on the Questions tab")
        call("POST", f"/initiatives/{iid}/send-back", {"note": "Add the price rounding rule."})
        d = wait_until("the brief v2", lambda: when(iid, lambda x: x["docs"]["brief"]["version"] == 2 and x["stage"].get("waiting")),
                       timeout=STAGE_WAIT())
        check("rounding" in d["docs"]["brief"]["text"].lower(), "the brief v2 follows the note")
        call("POST", f"/initiatives/{iid}/approve", {"why": "good"})

        print("5. the impact: one analyst per repo, read only")
        d = waiting(iid, "impact")
        repos = d["docs"]["impact"]["data"]["repos"]
        check({r["repo"] for r in repos} == {web, pay}, "the impact covers both repos")
        check({r["team"] for r in repos} == {"web", "payments"}, "each repo's impact names its team")
        check(sh("git", "status", "--porcelain", cwd=ws / "payments-api") == "" if not REAL["on"] else True, "the repos were only read (no change)")
        call("POST", f"/initiatives/{iid}/approve")

        print("6. the decision: memo, presentation, a disagreement, Go")
        d = waiting(iid, "decision")
        check(d["docs"]["decision"]["version"] == 1, "keel wrote the decision memo")
        deck = call("GET", f"/initiatives/{iid}/deck")
        check(isinstance(deck, str) and "<html" in deck.lower() and "Euro" in deck, "the presentation is a page of its own")
        dis = call("POST", f"/initiatives/{iid}/disagreements", {"reason": "Option A is cheaper", "author": "Bo"})
        call("POST", f"/initiatives/{iid}/disagreements/{dis['id']}/settle", {"outcome": "B stays: A cannot do the UK"})
        call("POST", f"/initiatives/{iid}/decide", {"choice": "go", "option": "B", "why": "the UK too"})

        print("7. the plan: epics per team, stories with criteria")
        d = waiting(iid, "plan")
        plan = d["plan"]
        check(plan["ok"] and {e["team"] for e in plan["epics"]} == {"web", "payments"}, "a checked plan with an epic per team")
        stories = [s for e in plan["epics"] for s in e["stories"]]
        check(all(s["criteria"] for s in stories) and plan["critical_path"], f"{len(stories)} stories with criteria and a critical path")
        check(d["initiative"]["option"] == "B", "the plan follows option B")
        call("POST", f"/initiatives/{iid}/approve")

        print("8. delivery: the stories go to keel Tasks")
        wait_until("delivery", lambda: detail(iid)["initiative"]["stage"] == "delivery", timeout=STAGE_WAIT())
        sent = call("POST", f"/initiatives/{iid}/handoff", {"target": "tasks"})
        check(len(sent["tasks"]) == len(stories) and not sent["skipped"], f"{len(sent['tasks'])} tasks were created")
        tasks = call("GET", f"/projects/{pay}/tasks")["tasks"]
        check(tasks and all("```keel-criteria" in t["description"] for t in tasks), "each task carries its criteria for keel's flow")
        board = [b for b in call("GET", "/initiatives") if b["id"] == iid][0]
        check(board["progress"] == {"done": 0, "total": len(stories)}, "the board shows the delivery progress")

        print("9. release and outcome")
        call("POST", f"/initiatives/{iid}/released")
        call("POST", f"/initiatives/{iid}/outcome", {"metric": "conversion 2.5% (was 2.1%)"})
        d = waiting(iid, "outcome")
        check(d["docs"]["outcome"]["version"] == 1 and len(d["docs"]["outcome"]["text"]) > 50, "keel wrote the outcome against the brief's metric")
        call("POST", f"/initiatives/{iid}/approve")
        d = wait_until("done", lambda: when(iid, lambda x: x["initiative"]["stage"] == "done"), timeout=STAGE_WAIT())
        sug = call("GET", "/teams/payments")["suggestions"]
        check(any(s["page"] == "lessons" for s in sug), "the team got a lesson to accept")

        print("10. the records")
        kinds = {e["kind"] for e in d["history"]}
        check({"created", "run", "version", "sent_back", "approved", "decision", "handoff", "released"} <= kinds, "the history has every step")
        check({r["stage"] for r in d["runs"]} == {"brief", "impact", "decision", "plan", "outcome"}, "one run per stage")
        log = sh("docker", "exec", NAME, "git", "-C", "/data/keel-product", "log", "--oneline")
        check(f"{iid}: brief v2" in log and f"{iid}: plan v1" in log, "every document version is a commit in the product repo")
        call("PUT", "/settings/general", {"keel_mode": "dev"})
        call("GET", "/initiatives", ok=(409,))
        check(True, "with keel in Dev mode the product api says it is off")
        call("PUT", "/settings/general", {"keel_mode": "auto"})
        print(f"\nPASSED: {len(CHECKS)} checks")
        return 0
    except Exception as e:
        print(f"\n{e}", file=sys.stderr)
        print(sh("docker", "logs", "--tail", "60", NAME, check=False)[-4000:], file=sys.stderr)
        return 1
    finally:
        if not args.keep and not args.real and not args.running:
            sh("docker", "rm", "-f", NAME, check=False)
            sh("docker", "volume", "rm", VOLUME, check=False)
            shutil.rmtree(ws, ignore_errors=True)
        else:
            print(f"keel-lab stays on http://127.0.0.1:{PORT} (repos in {ws}); remove it with: docker rm -f {NAME}; docker volume rm {VOLUME}")


if __name__ == "__main__":
    sys.exit(main())
