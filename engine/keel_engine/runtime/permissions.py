"""Permission cards for KeelBot's Fix mode: a command that changes something waits for the person's OK.

Who asks: keel's PreToolUse hook (claude, and opencode through its plugin) and keel's own ToolBox (API-key models).
How: an HTTP call to the URL in the run's ask settings `{url, key, session}` (KeelBot names its
`POST /helper/permissions/ask`, which checks the turn's key and asks keel_engine/approvals.py). It waits until the
person answers in the panel or the Inbox (Allow once, Always for this command, Deny with a reason), or until
ASK_TIMEOUT. The call carries the turn's own ask key: it can only ask; answering takes keel's internal token, which no
agent has.

Stdlib only (plus run_mode, itself stdlib): the hook imports this on every tool call.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import run_mode

ASK_TIMEOUT = 600           # seconds a command waits for the person
HOOK_TIMEOUT = ASK_TIMEOUT + 60


def needs_ask(command: str) -> bool:
    """A command that changes nothing (ls, cat, grep, git status / diff / log ...) runs without a card."""
    return run_mode.readonly_bash(command or "") is not None


def granted(command: str, grants: list[str] | None) -> bool:
    """"Always" was said for this exact command (or for a `prefix *` grant)."""
    cmd = (command or "").strip()
    for g in grants or []:
        g = str(g).strip()
        if cmd == g or (g.endswith(" *") and (cmd == g[:-2] or cmd.startswith(g[:-1]))):
            return True
    return False


def ask_engine(ask: dict | None, kind: str, command: str, path: str = "") -> tuple[bool, str]:
    """(allowed, why not). No ask settings: nothing to ask, the rules alone decide. `url` is where to ask, in full."""
    a = ask or {}
    url, key, sid = a.get("url"), a.get("key"), a.get("session")
    if not (url and key and sid):
        return True, ""
    body = json.dumps({"session": sid, "key": key, "kind": kind, "command": str(command)[:4000], "path": path}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=ASK_TIMEOUT + 30) as r:
            ans = json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"keel could not ask the person about this command ({type(exc).__name__}), so it does not run."
    if ans.get("decision") == "allow":
        return True, ""
    return False, str(ans.get("why") or "The person said no to this command.")
