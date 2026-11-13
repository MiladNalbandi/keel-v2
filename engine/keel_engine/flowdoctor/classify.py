"""What a flow's crash was, in words a person acts on, and what keel may do about it by itself (#101 part 3).

classify(error) -> Crash {kind, title, fix, repair, transient}. `repair` is what the doctor tries before it asks:
  wait     the provider was busy, limited or not reachable: wait, then run the step again (after the harness's own retries)
  fresh    the conversation got too long for the model: run the step again in a new session
  git-lock a git command left .git/index.lock behind: remove it when it is stale, then run the step again
  None     nothing keel can fix by itself: the flow pauses and asks (Retry after you fixed it, or Stop)
"""

from __future__ import annotations

import re
import traceback
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Crash:
    kind: str
    title: str
    fix: str
    repair: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


# (kind, pattern on the error's text, lower case, title, what the person can do, repair). The first match wins.
RULES: list[tuple[str, str, str, str, str | None]] = [
    ("disk-full", r"no space left|enospc|disk is full|disk quota",
     "The disk is full", "Free disk space on the computer keel runs on, then retry.", None),
    ("git-lock", r"index\.lock|another git process seems to be running",
     "git was locked by a lock file", "keel removes a stale .git/index.lock by itself; retry if it is still there.", "git-lock"),
    ("billing", r"credit balance|insufficient_quota|billing|payment required|\b402\b",
     "The provider account has no credit", "Add credit or raise the limit at the provider, then retry.", None),
    ("no-key", r"\bno (\w+ )?(api )?key\b|no key for|key is missing|api key (is )?not set|add (the|an) .*key|"
               r"is not set up|no connection called|no local model server",
     "No API key for this model", "Add the key in Settings › Models & keys, then retry.", None),
    ("auth", r"\b401\b|\b403\b|invalid (x-)?api[ -]?key|incorrect api key|did not accept the (api )?key|authentication|"
             r"unauthori[sz]ed|permission denied|bad credentials|forbidden",
     "The provider refused the key", "Check the key in Settings › Models & keys (Test says what is wrong), then retry.", None),
    ("model-not-found", r"model\b.*\b(not found|does not exist|is not available|unknown)|unknown model|no such model|"
                        r"\b404\b.*model|does not know this model",
     "The model is not available", "Pick another model for this agent (Agents, or Settings › Models & keys), then retry.", None),
    ("context", r"prompt is too long|context (length|window)|maximum context|too many tokens|context_length_exceeded|"
                r"input is too long|request too large|\b413\b",
     "The conversation got too long for the model", "keel runs the step again in a new session.", "fresh"),
    ("rate-limit", r"\b429\b|rate[ _-]?limit|too many requests|quota exceeded|requests per",
     "The provider's rate limit was reached", "keel waits and runs the step again.", "wait"),
    ("overloaded", r"\b529\b|overloaded|\b50[0234]\b|internal server error|bad gateway|service unavailable|"
                   r"server error|upstream",
     "The provider was overloaded or failed", "keel waits and runs the step again.", "wait"),
    ("network", r"could not reach|connect(ion)? ?(error|refused|reset|aborted|timed out)|connecterror|readtimeout|timed out|timeout|"
                r"temporary failure in name resolution|name or service not known|network is unreachable|"
                r"remoteprotocolerror|server disconnected|eof occurred",
     "The provider could not be reached", "keel waits and runs the step again.", "wait"),
    ("refusal", r"\brefus(ed|al)\b.*(model|policy|safety)|stop_reason.*refusal|content filter|content_filter",
     "The model refused the request", "Change the request, or pick another model for this agent, then retry.", None),
    ("max-turns", r"used all its turns|max[_ ]turns|turn limit|out of turns",
     "The agent ran out of turns", "Retry continues its session; a smaller request or a stronger model helps.", None),
    ("stuck", r"same call \d+ times",
     "The agent repeated the same call", "Retry with a note, or pick a stronger model for this agent.", None),
]
_COMPILED = [(k, re.compile(p), t, f, r) for k, p, t, f, r in RULES]
# exceptions that mean a bug in keel itself (not the model, not the project)
BUG_TYPES = {"KeyError", "TypeError", "AttributeError", "IndexError", "NameError", "UnboundLocalError", "AssertionError",
             "ZeroDivisionError", "RecursionError", "ImportError", "ModuleNotFoundError", "NotImplementedError"}


def text_of(error: BaseException | str | None) -> str:
    if error is None:
        return ""
    if isinstance(error, BaseException):
        hint = getattr(error, "hint", "") or ""
        return f"{type(error).__name__}: {error}{(' ' + hint) if hint else ''}"
    return str(error)


# a provider error's HTTP status (keel's harness keeps it on the ProviderError a ModelError comes from)
BY_STATUS = {401: "auth", 402: "billing", 403: "auth", 404: "model-not-found", 413: "context", 429: "rate-limit",
             500: "overloaded", 502: "overloaded", 503: "overloaded", 504: "overloaded", 529: "overloaded"}


def status_of(error) -> int | None:
    for e in (error, getattr(error, "__cause__", None)):
        st = getattr(e, "status", None) or getattr(e, "status_code", None)
        if isinstance(st, int):
            return st
    return None


def classify(error: BaseException | str | None) -> Crash:
    text = text_of(error)
    low = text.lower()
    by_status = BY_STATUS.get(status_of(error) or 0)
    if by_status:
        for kind, _pat, title, fix, repair in _COMPILED:
            if kind == by_status:
                # a 400/413 that says the conversation is too long stays "context"; the status names the rest
                return Crash(kind, title, fix, repair)
    for kind, pat, title, fix, repair in _COMPILED:
        if pat.search(low):
            return Crash(kind, title, fix, repair)
    name = type(error).__name__ if isinstance(error, BaseException) else (text.split(":", 1)[0].strip() if ":" in text else "")
    if name in BUG_TYPES:
        return Crash("keel-bug", "A bug in keel", "Retry; if it happens again, the improvement report has the place in "
                                                  "keel's code to report.", None)
    return Crash("unknown", "The step failed", "Read the error; retry when it is fixed, or stop.", None)


def where(error: BaseException | None) -> str | None:
    """The innermost place in keel's own code an exception came from ("runtime/compiler_code.py:48 in run"), or None."""
    if not isinstance(error, BaseException) or not error.__traceback__:
        return None
    spot = None
    for fr in traceback.extract_tb(error.__traceback__):
        f = fr.filename.replace("\\", "/")
        if "/keel_engine/" in f or "/keel_plugin_" in f:
            tail = f.split("/keel_engine/", 1)[1] if "/keel_engine/" in f else "keel_plugin_" + f.split("/keel_plugin_", 1)[1]
            spot = f"{tail}:{fr.lineno} in {fr.name}"
    return spot
