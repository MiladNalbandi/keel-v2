#!/usr/bin/env python3
"""The Supervisor on a setup rung whose fix keeps failing, end to end, on a throw-away keel only (keel-e2e-supervisor-<random>
on a free port, its own volume). It never touches the person's own keel.

    python3 e2e/supervisor/e2e.py --image keel-v2:dev [--keep]

The project: a manifest (deps.toml) and a lock file (deps.lock) older than it. The unit-tests rung checks that the lock
matches; tools/lock.py --only <name> refuses a partial update (the other entries still need the old major), only
tools/lock.py --all works. The agents are the fake model (KEEL_FAKE=1), but the rungs really run
(KEEL_SIMULATE_CHECKS=0), and the Supervisor's verdict comes from a script (KEEL_FAKE_SUPERVISOR): on a rung gate it
proposes own_fix with `python3 tools/lock.py --all`, anything else it leaves to the person.

  shadow   init stops at the rung gate; the Supervisor only suggests: its row keeps the commands, the flow still waits
  loop     the person's narrow fix fails twice: the second time keel takes it off and a plain approve would stop
  act      act mode with policy rung: may: the Supervisor answers own_fix, keel runs its commands, the rung passes; at
           audit_now (only the person answers it) it asks the person and the flow waits
"""

from __future__ import annotations

import argparse
import json
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
E = lib.Env.make("supervisor")
NAME, VOLUME, PORT = E.name, E.volume, E.port
PROJECT = "parts"
CHECKS: list[str] = []
FULL = "python3 tools/lock.py --all"
NARROW = "python3 tools/lock.py --only widget"

FIXTURE = {
    "README.md": "# Parts\n\nA tiny parts list. Its dependencies are pinned in deps.lock (tools/lock.py writes it).\n",
    "deps.toml": "widget = 2\ngadget = 2\n",
    "deps.lock": "widget = 1\ngadget = 1\n",
    "tools/deps.py": (
        "def read(path):\n"
        "    out = {}\n"
        "    for line in open(path):\n"
        "        if '=' in line:\n"
        "            k, v = line.split('=', 1)\n"
        "            out[k.strip()] = int(v)\n"
        "    return out\n\n\n"
        "def write(path, deps):\n"
        "    with open(path, 'w') as f:\n"
        "        f.writelines(f'{k} = {v}\\n' for k, v in deps.items())\n"
    ),
    "tools/check_lock.py": (
        "import sys\n\nfrom deps import read\n\n"
        "want, have = read('deps.toml'), read('deps.lock')\n"
        "if want != have:\n"
        "    print(f'deps.lock is out of date: deps.toml wants {want}, the lock has {have}')\n"
        "    sys.exit(1)\n"
        "print('deps.lock matches deps.toml')\n"
    ),
    "tools/lock.py": (
        "import sys\n\nfrom deps import read, write\n\n"
        "want, have = read('deps.toml'), read('deps.lock')\n"
        "if sys.argv[1:2] == ['--only']:\n"
        "    have[sys.argv[2]] = want[sys.argv[2]]\n"
        "    if len(set(have.values())) > 1:\n"
        "        print(f'refused: {have} mixes majors; every entry moves together (run without --only)')\n"
        "        sys.exit(1)\n"
        "elif sys.argv[1:2] == ['--all']:\n"
        "    have = dict(want)\n"
        "write('deps.lock', have)\n"
        "print('wrote deps.lock', have)\n"
    ),
    ".keel/config.yml": "commands:\n  api_compile: 'true'\n  unit_tests: 'PYTHONPATH=tools python3 tools/check_lock.py'\n",
}
SCRIPT = {
    "rung": {"action": "answer", "choice": "own_fix", "commands": [FULL.replace("python3 ", "PYTHONPATH=tools python3 ")],
             "confidence": "high", "evidence": ["deps.toml:1", "deps.lock:1", "tools/lock.py:6"],
             "why": "the lock is older than the manifest and lock.py refuses a partial update: the full update is needed",
             "summary": "deps.lock is a major behind deps.toml; tools/lock.py --all brings every entry along."},
    "*": {"action": "escalate", "confidence": "low", "why": "not mine to decide", "summary": "Please decide."},
}


def sh(*args: str, check: bool = True, cwd: Path | None = None) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({r.returncode}): {(r.stderr or r.stdout)[-1500:]}")
    return r.stdout + r.stderr


def api(method: str, path: str, body: object | None = None, ok: tuple[int, ...] = (200,)):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api{path}", data=data, method=method,
                                 headers={**({"content-type": "application/json"} if data else {}), **keelauth.headers(NAME)})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            code, text = r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode(errors="replace")
    if code not in ok:
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
        except Exception as e:  # noqa: BLE001 - keel is starting or the flow moves; keep trying
            last = e
        if last and not isinstance(last, Exception):
            return last
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what} (last: {str(last)[:400]})")


def fixture(root: Path) -> Path:
    repo = root / PROJECT
    for rel, text in FIXTURE.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false"]
    sh(*git, "init", "-q", "-b", "main", cwd=repo)
    sh(*git, "add", "-A", cwd=repo)
    sh(*git, "commit", "-q", "-m", "Parts: a manifest and its lock", cwd=repo)
    return repo


def start(image: str, repo: Path, script: Path) -> None:
    E.cleanup()
    E._done = False
    sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data",
       "-v", f"{repo}:/workspace", "-v", f"{script}:/e2e/supervisor.json:ro",
       "-e", "KEEL_FAKE=1", "-e", "KEEL_SIMULATE_CHECKS=0", "-e", "KEEL_FAKE_SUPERVISOR=/e2e/supervisor.json",
       "-e", f"KEEL_PROJECT_NAME={PROJECT}", image)
    wait_until(f"{NAME} ({image}) to be healthy", lambda: api("GET", "/health"), timeout=300)


def thread(pid: str, tid: str) -> dict:
    th = api("GET", f"/projects/{pid}/flows/{tid}")
    return th.get("thread") or {}


def waiting_at(pid: str, tid: str, step: str, timeout: float = 300) -> dict:
    def at():
        th = thread(pid, tid)
        if th.get("status") in ("failed", "stopped", "done"):
            raise SystemExit(f"the flow ended ({th.get('status')}) before {step}: {th.get('note')}")
        return th if (th.get("waiting") or {}).get("step") == step else None
    return wait_until(f"the flow to wait at {step}", at, timeout=timeout)


def to(pid: str, tid: str, step: str) -> dict:
    """Approve every pause until the flow waits at `step` (as the person would)."""
    for _ in range(20):
        th = wait_until("the flow to pause", lambda: (t := thread(pid, tid)).get("status") in ("waiting", "done", "failed",
                                                                                             "stopped") and t)
        w = (th.get("waiting") or {}).get("step")
        if th.get("status") != "waiting" or w == step:
            return th
        api("POST", f"/threads/{tid}/resume", {"decision": "approve"})
        time.sleep(1)
    raise AssertionError(f"never reached {step}")


def rows(pid: str, tid: str, step: str) -> list[dict]:
    got = api("GET", f"/projects/{pid}/supervisor/decisions?threadId={tid}&limit=100") or {}
    return [r for r in got.get("decisions") or [] if r.get("step") == step and r.get("thread_id") == tid]


def answer(tid: str, choice: str, why: str = "") -> None:
    api("POST", f"/threads/{tid}/resume", {"decision": "approve", "why": why, "payload": {"choice": choice}})


def run(image: str, tmp: Path) -> None:
    repo = fixture(tmp / "ws")
    script = tmp / "supervisor.json"
    script.write_text(json.dumps(SCRIPT))
    start(image, repo, script)
    pid = wait_until("the project at /workspace", lambda: (api("GET", "/projects") or [None])[0], timeout=60)["id"]
    api("PUT", f"/projects/{pid}/plugins/supervisor", {"enabled": True, "scope": "project"})
    api("POST", "/supervisor/switch", {"on": True})
    sw = api("POST", f"/projects/{pid}/supervisor/switch", {"on": True})
    check(bool(sw.get("on")), "the Supervisor is on keel-wide and for the project")

    print("\nshadow: it only suggests")
    res = api("POST", f"/projects/{pid}/flows", {"workflow_id": "init", "title": "Get to know the parts",
                                                 "allow_fake": True, "allow_dirty": True})
    tid = res.get("thread_id") or (res.get("thread") or {}).get("thread_id")
    api("POST", f"/projects/{pid}/flows/{tid}/supervisor", {"on": True, "mode": "shadow"})
    th = to(pid, tid, "rung_gate")
    w = th.get("waiting") or {}
    check(w.get("step") == "rung_gate" and "deps.lock is out of date" in (w.get("detail") or ""),
          "the unit-tests rung really ran and failed (the lock is older than the manifest)")
    check("own_fix" in (w.get("choices") or []), "the rung gate offers own_fix")
    row = wait_until("the Supervisor's look at the rung gate", lambda: next(iter(rows(pid, tid, "rung_gate")), None))
    check(row["kind"] == "rung" and row["code"] == "policy-escalate" and row["choice"] == "own_fix",
          f"shadow: it proposes own_fix and leaves the rung gate to you ({row['code']})")
    check(row.get("commands") == SCRIPT["rung"]["commands"], "its log keeps the commands it proposes")
    check((thread(pid, tid).get("waiting") or {}).get("step") == "rung_gate", "the flow still waits: it did not act")

    print("\nloop: the same failing fix is not offered again")
    narrow = NARROW.replace("python3 ", "PYTHONPATH=tools python3 ")
    answer(tid, "own_fix", narrow)
    th = waiting_at(pid, tid, "rung_gate")
    check("refused" in (th.get("waiting") or {}).get("detail", "") and "keel stops the loop" not in th["waiting"]["detail"],
          "the narrow fix fails once: the gate shows what it printed")
    answer(tid, "own_fix", narrow)
    time.sleep(2)
    th = waiting_at(pid, tid, "rung_gate")
    w = th["waiting"]
    check("keel stops the loop" in (w.get("detail") or "") and (w.get("choices") or [""])[0] == "stop"
          and "apply_fix" not in w.get("choices", []),
          "the same fix failed again: keel stops the loop, a plain approve means stop")

    print("\nact: it answers own_fix, the rung passes, and it leaves audit_now to you")
    api("POST", f"/projects/{pid}/flows/{tid}/supervisor", {"on": False})
    # 0.18.64 rung: may lets it run commands without you: the api takes it only with these words
    api("POST", f"/projects/{pid}/flows/{tid}/supervisor", {"on": True, "mode": "act", "policy": {"rung": "may"},
                                                            "i_understand": "commands run without you"})
    applied = wait_until("the Supervisor's answer", lambda: next(
        (r for r in rows(pid, tid, "rung_gate") if r.get("outcome") == "applied"), None), timeout=120)
    check(applied["choice"] == "own_fix", "act: it answered the rung gate with own_fix")
    th = waiting_at(pid, tid, "audit_now", timeout=300)
    check((repo / "deps.lock").read_text() == FIXTURE["deps.toml"], "keel ran its commands: deps.lock matches deps.toml")
    rung = next((r for r in th.get("ladder") or [] if "unit" in str(r.get("name", "")).lower()), {})
    check(rung.get("status") == "pass", "the unit-tests rung passes now")
    note = wait_until("its look at audit_now", lambda: next(iter(rows(pid, tid, "audit_now")), None))
    check(note["code"] == "never-kind" and note["outcome"] == "escalated", "audit_now: only you answer it, so it asks you")
    check((thread(pid, tid).get("waiting") or {}).get("step") == "audit_now", "the flow waits for you at audit_now")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="a full or product image with the supervisor plugin inside")
    ap.add_argument("--keep", action="store_true", help="leave the keel running (and its volume)")
    a = ap.parse_args()
    E.keep = a.keep
    tmp = Path(tempfile.mkdtemp(prefix="supervisor_e2e_"))
    t0 = time.time()
    try:
        run(a.image, tmp)
        print(f"\nPASSED: {len(CHECKS)} checks in {time.time() - t0:.0f} s")
        return 0
    except (Exception, SystemExit) as e:  # noqa: BLE001
        print(f"\n{e}", file=sys.stderr)
        print(sh("docker", "logs", "--tail", "80", NAME, check=False)[-6000:], file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
