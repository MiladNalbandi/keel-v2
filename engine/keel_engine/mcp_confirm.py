"""0.18.64 a confirmation a client without forms can give.

A tool that asks first (CONFIRM, a part's confirm=True, an action that publishes) asked the person through MCP
elicitation, a form some clients cannot show; then the only way was `keel2 mcp --yes`, which skips every confirmation.
Now such a client gets the question as text and a one-time token instead: the tool does nothing and answers "This needs
the person's OK: <what>". The token is bound to the tool and its arguments (the same call), works once and ends after
TTL seconds. Elicitation, where it works, and --yes stay as they were.

The model reads that answer, so it could send the token back by itself. So keel also puts the question in its Inbox
(engine approvals, POST /api/projects/{pid}/plugins/ask): the person approves it there, out of the model's reach. For
the dangerous kinds ([OUT_OF_BAND]: deletes, pushes, stops, model changes) the token alone does nothing: the Inbox's
approval decides (KEEL_MCP_CONFIRM=token: the token alone, as before). For the others the token works as before, and
an approval in the Inbox counts too.

    first call (no form)  ──► nothing done; token t (tool + arguments, once) + a question in keel's Inbox
    second call + confirm: t ──► the Inbox's answer: approved → done; denied → not done; still waiting → not yet
                                 (a dangerous kind), or done on the token (the others)
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import inspect
import json
import os
import secrets
import threading
import time
from typing import Annotated

from pydantic import Field

TTL = 300                   # seconds a token works
INBOX_TTL = 600             # one with a question in keel's Inbox: as long as the question waits (permissions.ASK_TIMEOUT)
PARAM = "confirm"           # the argument the second call sends it in
# the token the current tool call was given (set by with_confirm, read by the server's act())
GIVEN: contextvars.ContextVar[str | None] = contextvars.ContextVar("keel_confirm", default=None)

# The dangerous kinds: deletes, pushes, stops, model changes. A client without forms confirms them in keel's Inbox
# only; the token the model gets is not enough (it could send it back by itself).
OUT_OF_BAND = {"keel_delete_run", "keel_remove_project", "keel_stack_remove", "keel_keelbot_undo",     # deletes
               "keel_git_push",                                                                       # pushes
               "keel_stop_flow", "keel_stop_flows", "keel_pause",                                     # stops
               "keel_agent_model",                                                                    # model changes
               "keel_db_change"}                                                                      # data changes
OUT_OF_BAND_ACTIONS = {"open_pr", "pr"}     # keel_run_action actions that publish (they push a branch)


def token_only() -> bool:
    """KEEL_MCP_CONFIRM=token: the token alone confirms every kind again (keel 0.18.64's first way)."""
    return os.environ.get("KEEL_MCP_CONFIRM", "").strip().lower() == "token"


def out_of_band(name: str, action: str | None = None) -> bool:
    """True when this call needs the person's approval in keel's Inbox (the token alone is not enough)."""
    if token_only():
        return False
    return name in OUT_OF_BAND or (name == "keel_run_action" and action in OUT_OF_BAND_ACTIONS)


class Tokens:
    """The open tokens of one MCP server: token -> (tool, its arguments' key, when it ends, its Inbox question)."""

    def __init__(self, clock=None):
        self._open: dict[str, tuple[str, str, float, str | None]] = {}
        self._lock = threading.Lock()
        self.clock = clock or (lambda: time.monotonic())      # looked up at each call (tests move the clock)

    @staticmethod
    def key(name: str, args: tuple, kw: dict, skip: type | tuple = ()) -> str:
        """The call's fingerprint: the tool and its arguments (`skip`: objects that are not arguments, the api client)."""
        plain = [x for x in args if not (skip and isinstance(x, skip))]
        raw = json.dumps([name, plain, kw], sort_keys=True, default=repr)
        return hashlib.sha256(raw.encode()).hexdigest()

    def issue(self, name: str, key: str, question: str | None = None) -> str:
        now = self.clock()
        token = "ok-" + secrets.token_urlsafe(9)
        with self._lock:
            self._open = {t: v for t, v in self._open.items() if v[2] > now}       # the old ones go
            self._open[token] = (name, key, now + (INBOX_TTL if question else TTL), question)
        return token

    def look(self, token: str, name: str, key: str) -> tuple[bool, str | None]:
        """(valid for this tool and these arguments and not ended, its Inbox question); the token stays open."""
        with self._lock:
            got = self._open.get((token or "").strip())
        ok = bool(got and got[0] == name and got[1] == key and got[2] > self.clock())
        return ok, (got[3] if ok else None)

    def take(self, token: str, name: str, key: str) -> bool:
        """True once for a token of this tool and these arguments that has not ended; it is used up either way."""
        with self._lock:
            got = self._open.pop((token or "").strip(), None)
        return bool(got and got[0] == name and got[1] == key and got[2] > self.clock())


def _minutes(question: str | None) -> int:
    return (INBOX_TTL if question else TTL) // 60


def ask_text(name: str, what: str, token: str, question: str | None = None, inbox_only: bool = False) -> str:
    """The answer of a tool that needs the person's OK, for a client that cannot show a form. It keeps 0.16.0's opening
    words ("cannot ask you to confirm ...") so a client or a script that read them still finds them. 0.18.64 it no
    longer offers `--yes` (the model read it as a way around the person), and says to never confirm by itself."""
    head = (f"keel: this MCP client cannot ask you to confirm {what} in a form, so nothing was done yet. This needs the "
            f"person's OK: {what}. Ask the person; never confirm by yourself.")
    again = f'call {name} again with the same arguments and confirm: "{token}" within {_minutes(question)} minutes (it works once)'
    if inbox_only:
        return (f"{head} keel put the question in its Inbox ({question}): the person approves it there (the dashboard, "
                f"keel TUI or the phone app). After they approved it, {again}. The token alone does not do it.")
    if question:
        return (f"{head} keel also put the question in its Inbox ({question}). Only when the person said yes (here, or "
                f"by approving it in keel's Inbox), {again}.")
    return f"{head} Only when the person said yes, {again}."


def bad_text(name: str, what: str, token: str, question: str | None = None, inbox_only: bool = False) -> str:
    return ("keel: that confirm token is not valid for this call (used already, too old, or for other arguments), so "
            "nothing was done. " + ask_text(name, what, token, question, inbox_only).removeprefix("keel: "))


def waiting_text(name: str, what: str, question: str) -> str:
    return (f"keel: the person has not approved {what} in keel's Inbox yet ({question}), so nothing was done. Ask them "
            f"to approve it there; never confirm by yourself. Then call {name} again with the same token.")


def denied_text(what: str, why: str = "") -> str:
    return f"keel: not done — the person said no to {what} in keel's Inbox" + (f" ({why})." if why else ".")


def no_inbox_text(what: str, why: str) -> str:
    return (f"keel: this MCP client cannot ask you to confirm {what} in a form, and keel could not put the question in "
            f"its Inbox ({why}), so nothing was done. This needs the person: they confirm it in keel's dashboard, or "
            "from a client that can show forms. Never confirm by yourself.")


def with_confirm(fn):
    """The tool with one more argument, `confirm` (the token keel gave), for the client's schema; its value is in GIVEN
    while the tool runs. `fn` is async (build_server's reg makes a plain tool async first)."""
    sig = inspect.signature(fn, eval_str=True)
    if PARAM in sig.parameters:
        return fn

    @functools.wraps(fn)
    async def wrapped(*a, **kw):
        given = GIVEN.set(kw.pop(PARAM, None))
        try:
            return await fn(*a, **kw)
        finally:
            GIVEN.reset(given)

    extra = inspect.Parameter(PARAM, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Annotated[
        str | None, Field(description="Only when keel asked for the person's OK and gave a token: that token, after "
                                      "the person said yes (or approved it in keel's Inbox). Never make one up and "
                                      "never confirm by yourself.")])
    params = [p for p in sig.parameters.values() if p.kind != inspect.Parameter.VAR_KEYWORD]
    wrapped.__signature__ = sig.replace(parameters=params + [extra])
    return wrapped
