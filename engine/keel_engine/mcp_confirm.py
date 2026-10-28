"""0.18.64 a confirmation a client without forms can give.

A tool that asks first (CONFIRM, a part's confirm=True, an action that publishes) asked the person through MCP
elicitation, a form some clients cannot show; then the only way was `keel2 mcp --yes`, which skips every confirmation.
Now such a client gets the question as text and a one-time token instead: the tool does nothing and answers "This needs
your OK: <what>. Call again with confirm: "<token>" within 5 minutes". The token is bound to the tool and its arguments
(the same call), works once and ends after TTL seconds. Elicitation, where it works, and --yes stay as they were.

    first call (no form)  ──► nothing done, token t (tool + arguments, 5 minutes, once)
    second call + confirm: t ──► the same tool, the same arguments: done
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import inspect
import json
import secrets
import threading
import time
from typing import Annotated

from pydantic import Field

TTL = 300                   # seconds a token works
PARAM = "confirm"           # the argument the second call sends it in
# the token the current tool call was given (set by with_confirm, read by the server's act())
GIVEN: contextvars.ContextVar[str | None] = contextvars.ContextVar("keel_confirm", default=None)


class Tokens:
    """The open tokens of one MCP server: token -> (tool, its arguments' key, when it ends)."""

    def __init__(self, clock=None):
        self._open: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()
        self.clock = clock or (lambda: time.monotonic())      # looked up at each call (tests move the clock)

    @staticmethod
    def key(name: str, args: tuple, kw: dict, skip: type | tuple = ()) -> str:
        """The call's fingerprint: the tool and its arguments (`skip`: objects that are not arguments, the api client)."""
        plain = [x for x in args if not (skip and isinstance(x, skip))]
        raw = json.dumps([name, plain, kw], sort_keys=True, default=repr)
        return hashlib.sha256(raw.encode()).hexdigest()

    def issue(self, name: str, key: str) -> str:
        now = self.clock()
        token = "ok-" + secrets.token_urlsafe(9)
        with self._lock:
            self._open = {t: v for t, v in self._open.items() if v[2] > now}       # the old ones go
            self._open[token] = (name, key, now + TTL)
        return token

    def take(self, token: str, name: str, key: str) -> bool:
        """True once for a token of this tool and these arguments that has not ended; it is used up either way."""
        with self._lock:
            got = self._open.pop((token or "").strip(), None)
        return bool(got and got[0] == name and got[1] == key and got[2] > self.clock())


def ask_text(name: str, what: str, token: str) -> str:
    """The answer of a tool that needs the person's OK, for a client that cannot show a form. It keeps 0.16.0's words
    ("cannot ask you to confirm ...", --yes) so a client or a script that read them still finds them."""
    return (f"keel: this MCP client cannot ask you to confirm {what} in a form, so nothing was done yet. This needs your "
            f'OK: {what}. Ask the person; when they say yes, call {name} again with the same arguments and confirm: '
            f'"{token}" within {TTL // 60} minutes (it works once). Or run keel2 mcp with --yes to allow it without '
            "asking, or do it in keel's dashboard.")


def bad_text(name: str, what: str, token: str) -> str:
    return (f"keel: that confirm token is not valid for this call (used already, older than {TTL // 60} minutes, or for "
            f"other arguments), so nothing was done. This needs your OK: {what}. When the person says yes, call {name} "
            f'again with the same arguments and confirm: "{token}" within {TTL // 60} minutes.')


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
                                      "the person said yes. Never make one up.")])
    params = [p for p in sig.parameters.values() if p.kind != inspect.Parameter.VAR_KEYWORD]
    wrapped.__signature__ = sig.replace(parameters=params + [extra])
    return wrapped
