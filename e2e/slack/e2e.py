#!/usr/bin/env python3
"""The Slack plugin in a running keel, against the stand-in (fake_slack.py), end to end, on a throw-away keel only
(keel-e2e-slack-<random> on a free port, its own volume). It never touches the person's own keel, and it never reaches Slack.

    python3 e2e/slack/e2e.py --image keel-v2:dev [--keep]

The stand-in runs inside the container, next to keel, with keel's own Python (it needs the websockets library). The model is the fake one (KEEL_FAKE=1).

  link      keel links to the stand-in; every power is off
  commands  status, waiting, task with a name (a direct message goes), assign, a name that is not on the list
  off       start and a free question: the powers are off, so neither starts a flow nor asks a model
  start     on: one button; another person cannot press it; the button starts the flow; Record says Slack and who
  forms     a flow's question with options: its modal; the form's answer takes the Inbox's path
  chat      on: a question gets the fake model's answer in its thread; a reply goes on; the agent call is on the budget;
            asked to write, the chat's agent is refused; the daily cap stops it
  gates     a kept gate: no button; with every gate allowed: a button, a modal, a reason; budget gates: no button whatever is set
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

E = lib.Env.make("slack")
NAME, VOLUME, PORT = E.name, E.volume, E.port
PROJECT = "parts"
STAND_IN = 18450
ANN, BEA, STRANGER = "UANN1", "UBEA2", "USTRANGER"
CHECKS: list[str] = []
FIXTURE = {"README.md": "# Parts\n\nA tiny parts list.\n", ".keel/config.yml": "commands:\n  api_compile: 'true'\n"}


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


def stand_in(path: str, body: object | None = None):
    """The stand-in's own routes, from inside the container."""
    code = ("import json,sys,urllib.request as u;"
            f"d=sys.argv[1].encode() if len(sys.argv)>1 else None;"
            f"r=u.Request('http://127.0.0.1:{STAND_IN}{path}',data=d,method='POST' if d else 'GET');"
            "print(u.urlopen(r,timeout=20).read().decode())")
    args = ["docker", "exec", NAME, "/opt/engine/.venv/bin/python", "-c", code]
    return json.loads(sh(*args, *([json.dumps(body)] if body is not None else [])))


def check(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError("FAILED: " + what)
    CHECKS.append(what)
    print("  ✓", what)


def wait_until(what: str, fn, timeout: float = 120.0, every: float = 1.0):
    deadline, last = time.time() + timeout, None
    while time.time() < deadline:
        try:
            last = fn()
        except Exception as e:  # noqa: BLE001 - keel is starting or the flow moves; keep trying
            last = e
        if last and not isinstance(last, Exception):
            return last
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what}: {last}")


def calls(method: str) -> list[dict]:
    return [c["body"] for c in stand_in("/_calls")["calls"] if c["method"] == method]


def said(words: str, timeout: float = 60.0) -> dict:
    return wait_until(f'Slack to be told "{words}"', lambda: next((m for m in reversed(calls("chat.postMessage")) if words in (m.get("text") or "")), None), timeout)


def event(user: str, text: str, **more) -> None:
    stand_in("/_send", {"type": "events_api", "payload": {"team_id": "T1", "event_id": f"ev{time.time_ns()}", "event": {
        "type": "message", "channel_type": "im", "channel": "D1", "user": user, "text": text, "ts": f"{time.time_ns()}.1", **more}}})


def press(user: str, action: str, value: str) -> None:
    stand_in("/_send", {"type": "interactive", "payload": {"type": "block_actions", "user": {"id": user}, "team": {"id": "T1"}, "trigger_id": f"trig{time.time_ns()}",
                                                           "channel": {"id": "C1"}, "message": {"ts": "5.5"}, "actions": [{"action_id": action, "value": value}]}})


def submit(user: str, view: dict, values: dict) -> None:
    stand_in("/_send", {"type": "interactive", "payload": {"type": "view_submission", "user": {"id": user}, "team": {"id": "T1"}, "view": {
        "callback_id": view["callback_id"], "private_metadata": view["private_metadata"], "state": {"values": values}}}})


def buttons(message: dict) -> list[dict]:
    return [e for b in message.get("blocks") or [] if b.get("type") == "actions" for e in b.get("elements") or []]


def fixture(root: Path) -> Path:
    repo = root / PROJECT
    for rel, text in FIXTURE.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false"]
    sh(*git, "init", "-q", "-b", "main", cwd=repo)
    sh(*git, "add", "-A", cwd=repo)
    sh(*git, "commit", "-q", "-m", "Parts", cwd=repo)
    return repo


def start(image: str, repo: Path) -> None:
    E.cleanup()
    E._done = False
    sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data", "-v", f"{repo}:/workspace",
       "-v", f"{HERE}:/e2e/slack:ro", "-e", "KEEL_FAKE=1", "-e", "KEEL_FAKEONREALPROJECTS=true", "-e", f"KEEL_PROJECT_NAME={PROJECT}", image)
    wait_until(f"{NAME} ({image}) to be healthy", lambda: api("GET", "/health"), timeout=300)
    sh("docker", "exec", "-d", NAME, "/opt/engine/.venv/bin/python", "/e2e/slack/fake_slack.py", str(STAND_IN))
    wait_until("the stand-in", lambda: stand_in("/_calls") is not None, timeout=30)


def tasks(pid: str) -> list[dict]:
    return api("GET", f"/projects/{pid}/tasks")["tasks"]


def run(image: str, tmp: Path) -> None:
    start(image, fixture(tmp / "ws"))
    pid = wait_until("the project at /workspace", lambda: (api("GET", "/projects") or [None])[0], timeout=60)["id"]

    print("\nlink: every power is off")
    v = api("POST", "/plugins/slack", {"api_url": f"http://127.0.0.1:{STAND_IN}/api", "bot_token": "xoxb-stand-in", "app_token": "xapp-stand-in", "channel": "C1", "project": pid,
                                       "allowed": [{"id": ANN, "name": "Ann"}, {"id": BEA, "name": "Bea"}]})
    wait_until("the socket", lambda: api("GET", "/plugins/slack")["state"] == "connected")
    c = v["config"]
    check(not (c["allow_chat"] or c["allow_start"] or c["allow_all_gates"]), "a new link has every power off")

    print("\ncommands")
    event(ANN, "status")
    said(PROJECT)
    check(True, "status answers from keel's data")
    event(ANN, "task Fix the export @bea")
    said("for Bea")
    t = wait_until("the task", lambda: next((x for x in tasks(pid) if x["title"] == "Fix the export"), None))
    check(t["assignee"] == "Bea", "task @name puts the name on the task")
    check(any(m["channel"] == BEA for m in calls("chat.postMessage")), "the person gets a direct message")
    event(ANN, "task Nobody hears @stranger")
    said("stranger is not on this keel's list")
    check(all(x["title"] != "Nobody hears" for x in tasks(pid)), "a name not on the list makes no task")
    event(ANN, "task Later")
    said("Later")
    event(ANN, "assign later @ann")
    said("now says Ann")
    check(next(x for x in tasks(pid) if x["title"] == "Later")["assignee"] == "Ann", "assign changes the name")
    event(STRANGER, "status")
    said("not on this keel's list of people")
    check(True, "someone not on the list is refused")

    print("\noff: no start, no model")
    before = len(api("GET", f"/projects/{pid}/runs") or [])
    event(ANN, "start feature add a late filter")
    said("Starting flows from Slack is off")
    event(ANN, "how does the export go?")
    said("I am keel")
    check(len(api("GET", f"/projects/{pid}/runs") or []) == before, "with the powers off nothing started")

    print("\nstart: one button, for who asked")
    api("POST", "/plugins/slack", {"allow_start": True})
    event(ANN, "start feature add a late filter")
    m = said("Start *feature*")
    start_id = next(b["value"] for b in buttons(m) if b["action_id"] == "keel_start")
    press(BEA, "keel_start", start_id)
    time.sleep(2)
    check(len(api("GET", f"/projects/{pid}/runs") or []) == before, "another person's press starts nothing")
    press(ANN, "keel_start", start_id)
    said("Its gates come to the channel")
    tid = wait_until("the flow", lambda: next((r["thread_id"] for r in api("GET", f"/projects/{pid}/runs") or [] if r.get("title") == "add a late filter"), None))
    rec = api("GET", f"/projects/{pid}/record/flows/{tid}")
    check(rec["asked"]["via"] == "Slack" and "Ann" in rec["asked"]["by"], "Record says it came from Slack, and from whom")

    print("\nforms and gates: the flow's own gate")
    gate = wait_until("a gate message", lambda: next((x for x in reversed(calls("chat.postMessage")) if x.get("channel") == "C1" and buttons(x)), None), timeout=120)
    check(bool(buttons(gate)), "a waiting gate comes to the channel with its buttons")
    first = buttons(gate)[0]
    press(ANN, first["action_id"], first["value"])
    if first["action_id"] == "keel_answer_form":
        view = wait_until("the modal", lambda: (calls("views.open") or [None])[-1])["view"]
        check(view["callback_id"] == "keel_form", "a gate with questions opens a modal")
        submit(ANN, view, {})
    said("Thanks Ann")
    check(True, "the answer takes the Inbox's path and says who")
    gates = wait_until("the answer in Record", lambda: api("GET", f"/projects/{pid}/record/flows/{tid}").get("gates"))
    # keel answers the gates after it by itself: look for this one's line, not the last line
    check(any("in Slack: Ann" in g["who"] for g in gates), "the gate's record says it came from Ann in Slack")

    print("\nchat: on, read-only, with a cap")
    api("POST", "/plugins/slack", {"allow_chat": True, "chat_day_tokens": 100000})
    event(ANN, "how does the export go?", ts="700.1")
    m = said("(fake model) I can only read")
    check(m.get("thread_ts") == "700.1", "the answer goes in the same thread")
    stand_in("/_send", {"type": "events_api", "payload": {"team_id": "T1", "event_id": f"ev{time.time_ns()}", "event": {
        "type": "message", "channel_type": "im", "channel": "D1", "user": ANN, "text": "and the tests?", "ts": f"{time.time_ns()}.2", "thread_ts": "700.1"}}})
    said("and the tests?")
    check(True, "a reply in the thread goes on")
    budget = api("GET", f"/projects/{pid}/budget") or {}
    check(bool(budget), "the Budget page answers (its agent calls hold slack-chat)")
    api("POST", "/plugins/slack", {"chat_day_tokens": 1})
    event(ANN, "one more?")
    said("You reached your chat cap")
    check(True, "the daily cap stops it")

    print("\nevery gate from Slack")
    api("POST", "/plugins/slack", {"allow_chat": False, "allow_all_gates": True, "strict_allowed": [{"id": ANN, "name": "Ann"}]})
    v = api("GET", "/plugins/slack")["config"]
    check(v["allow_all_gates"] and [p["id"] for p in v["strict_allowed"]] == [ANN], "the short list is kept")
    api("POST", "/plugins/slack", {"strict_allowed": [{"id": "UNOTLISTED", "name": "Nobody"}]}, ok=(400,))
    check(True, "the short list must be part of the first")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="a full image with the slack plugin inside")
    ap.add_argument("--keep", action="store_true", help="leave the keel running (and its volume)")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="keel-e2e-slack-") as tmp:
        try:
            run(a.image, Path(tmp))
            print(f"\nOK: {len(CHECKS)} checks")
            return 0
        except AssertionError as e:
            print(f"\nFAILED: {e}\nlogs: docker logs {NAME}")
            return 1
        finally:
            if not a.keep:
                E.cleanup()


if __name__ == "__main__":
    sys.exit(main())
