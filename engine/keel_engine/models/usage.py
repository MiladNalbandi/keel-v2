"""What each subscription provider says is used and what remains (the usage dashboard and the pause rule).

Sources, each labelled so the web never shows a number without saying where it came from:

  claude   the `rate_limit_event` lines of claude's stream-json (every run): 5-hour and 7-day windows,
           utilization 0..1 and resetsAt. Free, but only "as of the last run". Anthropic's /api/oauth/usage is
           deliberately not used (undocumented, rate-limits itself).
  codex    `codex app-server` (JSON-RPC over stdio): initialize, initialized, account/rateLimits/read. Its
           rateLimits.primary / .secondary windows carry usedPercent, windowDurationMins, resetsAt (unix seconds).
           Read-only; the login is the auth.json in CODEX_HOME.
  copilot  GitHub's quota data for the token (GET api.github.com/copilot_internal/user): quota_snapshots.
           premium_interactions {entitlement, remaining, percent_remaining, unlimited}, quota_reset_date_utc.
           Unofficial: the api says so on the card and falls back to the manual cap when it fails.

A window is {window, label, used_pct (0..1 or None), resets_at (unix seconds or None), status?, used?, cap?, remaining?}.
The engine keeps the latest windows per provider in memory (its own runs, live reads, and what the api sends with a
thread), so the runtime can pause before an agent when a window is nearly used up. Tokens are never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

CODEX_TIMEOUT = 10
COPILOT_TIMEOUT = 8
COPILOT_URL = "https://api.github.com/copilot_internal/user"
LIVE_MAX_AGE = 300          # seconds: before an agent step, a codex/copilot reading older than this is read again
LABELS = {"five_hour": "5-hour", "seven_day": "weekly", "seven_day_opus": "weekly Opus", "seven_day_sonnet": "weekly Sonnet",
          "month": "monthly"}
NAMES = {"claude": "Claude", "codex": "Codex", "copilot": "Copilot"}

# provider -> {"windows": [...], "source": str, "at": unix seconds}
LATEST: dict[str, dict] = {}
# (thread, step, provider, window, resets_at) -> the pause question, so a resume finds the same question (same title)
QUESTIONS: dict[tuple, dict] = {}


def reset() -> None:
    LATEST.clear()
    QUESTIONS.clear()


def _num(v) -> float | None:
    try:
        return None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


def _epoch(v) -> int | None:
    """Unix seconds from seconds, milliseconds or an ISO date; None when it is none of them."""
    n = _num(v)
    if n is not None:
        return int(n / 1000) if n > 10_000_000_000 else int(n)
    if isinstance(v, str) and v.strip():
        try:
            return int(datetime.fromisoformat(v.strip().replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def window(name: str, used_pct: float | None, resets_at=None, **extra) -> dict:
    w = {"window": name, "label": LABELS.get(name, name.replace("_", " ")),
         "used_pct": None if used_pct is None else round(max(0.0, min(float(used_pct), 1.0)), 4), "resets_at": _epoch(resets_at)}
    w.update({k: v for k, v in extra.items() if v is not None})
    return w


# ------------------------------------------------------------------ claude: rate_limit_event

def claude_windows(info) -> list[dict]:
    """Windows from one `rate_limit_event` rate_limit_info. Shapes differ between CLI versions; unknown ones give []."""
    if not isinstance(info, dict):
        return []
    status = info.get("status") if isinstance(info.get("status"), str) else None
    out: dict[str, dict] = {}
    unified = info.get("unifiedWindows") or info.get("unified_windows")
    if isinstance(unified, dict):
        for name, w in unified.items():
            if isinstance(w, dict):
                out[str(name)] = window(str(name), _num(w.get("utilization")), w.get("resetsAt") or w.get("resets_at"))
    kind = info.get("rateLimitType") or info.get("rate_limit_type")
    if isinstance(kind, str) and kind:
        util = _num(info.get("utilization"))
        if util is None and status == "rejected":
            util = 1.0                      # refused: that window is used up
        if util is not None or kind not in out:
            prev = out.get(kind, {})
            out[kind] = window(kind, util if util is not None else prev.get("used_pct"),
                               info.get("resetsAt") or info.get("resets_at") or prev.get("resets_at"))
        if status:
            out[kind]["status"] = status
        if info.get("isUsingOverage") is True:
            out[kind]["overage"] = True
    return list(out.values())


# ------------------------------------------------------------------ codex: app-server

def codex_windows(result) -> list[dict]:
    """Windows from account/rateLimits/read's result: rateLimits.primary (5 hours) and .secondary (weekly)."""
    snap = (result or {}).get("rateLimits") if isinstance(result, dict) else None
    if not isinstance(snap, dict):
        return []
    out = []
    for key in ("primary", "secondary"):
        w = snap.get(key)
        if not isinstance(w, dict) or _num(w.get("usedPercent")) is None:
            continue
        mins = _num(w.get("windowDurationMins"))
        name = {300: "five_hour", 10080: "seven_day"}.get(int(mins) if mins else -1) or (f"{int(mins)}m" if mins else key)
        out.append(window(name, _num(w["usedPercent"]) / 100, w.get("resetsAt"),
                          status="rejected" if snap.get("rateLimitReachedType") else None))
    return out


async def codex_rate_limits(codex_home: str | None = None, timeout: float = CODEX_TIMEOUT, binary: str | None = None) -> dict:
    """Ask `codex app-server` for the account's rate limits. Killed on exit, whatever happens."""
    from .cli import find, safe_env
    try:
        argv = [binary] if binary else [find("codex"), "app-server"]
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    env = safe_env({"CODEX_HOME": codex_home} if codex_home else None)
    try:
        proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL, env=env, start_new_session=True)
    except OSError as exc:
        return {"ok": False, "error": f"could not start codex app-server: {exc}"}

    async def talk() -> dict:
        assert proc.stdin and proc.stdout
        for msg in ({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "keel", "version": "0.4"}}},
                    {"method": "initialized"},
                    {"id": 2, "method": "account/rateLimits/read", "params": None}):
            proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()
        async for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == 2:
                if msg.get("error"):
                    err = msg["error"]
                    return {"ok": False, "error": str(err.get("message") if isinstance(err, dict) else err)[:300]}
                res = msg.get("result") or {}
                snap = res.get("rateLimits") if isinstance(res, dict) else None
                return {"ok": True, "windows": codex_windows(res),
                        "plan": snap.get("planType") if isinstance(snap, dict) else None}
        return {"ok": False, "error": "codex app-server closed without an answer"}

    try:
        return await asyncio.wait_for(talk(), timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"codex app-server did not answer within {int(timeout)}s"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except asyncio.TimeoutError:
                pass


# ------------------------------------------------------------------ copilot: GitHub quota data (unofficial)

def copilot_windows(body) -> list[dict]:
    snaps = (body or {}).get("quota_snapshots") if isinstance(body, dict) else None
    p = snaps.get("premium_interactions") if isinstance(snaps, dict) else None
    if not isinstance(p, dict):
        return []
    reset = body.get("quota_reset_date_utc") or body.get("quota_reset_date")
    if p.get("unlimited") is True:
        return [window("month", 0.0, reset, unlimited=True)]
    cap, left = _num(p.get("entitlement")), _num(p.get("remaining") if p.get("remaining") is not None else p.get("quota_remaining"))
    pct_left = _num(p.get("percent_remaining"))
    used_pct = 1 - pct_left / 100 if pct_left is not None else ((cap - left) / cap if cap and left is not None else None)
    return [window("month", used_pct, reset, cap=cap, remaining=left, used=(cap - left) if cap is not None and left is not None else None,
                   overage=int(p["overage_count"]) if _num(p.get("overage_count")) else None)]


async def copilot_quota(token: str, timeout: float = COPILOT_TIMEOUT, url: str = COPILOT_URL) -> dict:
    if not token:
        return {"ok": False, "error": "no GitHub token"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, headers={"Authorization": f"token {token}", "Accept": "application/json",
                                          "User-Agent": "keel", "X-GitHub-Api-Version": "2025-04-01"})
        if r.status_code != 200:
            return {"ok": False, "error": f"GitHub answered {r.status_code}"}
        body = r.json()
        windows = copilot_windows(body)
        if not windows:
            return {"ok": False, "error": "GitHub sent no premium request quota for this token"}
        return {"ok": True, "windows": windows, "plan": body.get("copilot_plan")}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


# ------------------------------------------------------------------ what the engine knows now

def record(provider: str, windows: list[dict], source: str, at: float | None = None) -> dict | None:
    """Keep the newest windows of a provider (merged by window name). Returns the stored entry."""
    if not windows:
        return None
    at = time.time() if at is None else at
    prev = LATEST.get(provider)
    if prev and prev["at"] > at:
        return prev
    merged = {w["window"]: w for w in (prev or {}).get("windows", [])}
    merged.update({w["window"]: w for w in windows})
    LATEST[provider] = {"windows": list(merged.values()), "source": source, "at": at}
    return LATEST[provider]


def absorb(items) -> None:
    """Windows the api sends with a thread: [{provider, window, used_pct, resets_at (iso or seconds), fetched_at, source}]."""
    for it in items or []:
        if not isinstance(it, dict) or not it.get("provider") or not it.get("window"):
            continue
        at = _epoch(it.get("fetched_at")) or time.time()
        record(str(it["provider"]), [window(str(it["window"]), _num(it.get("used_pct")), it.get("resets_at"))],
               str(it.get("source") or "keel"), at)


async def read_live(provider: str, key: str | None) -> dict:
    """POST /providers/usage: read one provider now. claude has no live source (its numbers come from runs)."""
    t0 = time.time()
    if provider == "codex":
        home = None
        if key:
            from .cli import codex_login_env
            home = codex_login_env({"codex_auth": key}).get("CODEX_HOME")
        res = await codex_rate_limits(home or os.environ.get("CODEX_HOME"))
        source = "codex app-server"
    elif provider == "copilot":
        res = await copilot_quota(key or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "")
        source = "GitHub (unofficial)"
    elif provider == "claude":
        entry = LATEST.get("claude")
        if not entry:
            return {"ok": False, "provider": provider, "windows": [], "error": "No Claude run yet: the numbers arrive with the next run."}
        return {"ok": True, "provider": provider, "windows": entry["windows"], "source": entry["source"], "at": iso(entry["at"])}
    else:
        return {"ok": False, "provider": provider, "windows": [], "error": f"No usage source for {provider}."}
    if res.get("ok"):
        record(provider, res["windows"], source, t0)
    return {"provider": provider, "source": source, "at": iso(t0), "windows": [], **res}


async def refresh_before_step(model: dict, keys: dict | None) -> None:
    """Before a codex/copilot subscription agent: read the provider again when what keel knows is old. Never raises."""
    provider = model.get("provider")
    if model.get("mode") == "api" or provider not in ("codex", "copilot"):
        return
    entry = LATEST.get(provider)
    if entry and time.time() - entry["at"] < LIVE_MAX_AGE:
        return
    key = (keys or {}).get("codex_auth" if provider == "codex" else "copilot")
    if not key and provider == "codex":
        return                      # no keel login: the user's own codex setup is not read behind their back
    try:
        await read_live(provider, key)
    except Exception as exc:  # the pause rule is a help, not a gate on reading it
        log.info("could not read %s usage before a step: %s", provider, type(exc).__name__)


def fullest(provider: str, now: float | None = None) -> dict | None:
    """The most used window of a provider that has not reset yet (a refused window counts as full)."""
    now = time.time() if now is None else now
    best = None
    for w in (LATEST.get(provider) or {}).get("windows", []):
        if w.get("resets_at") and w["resets_at"] <= now:
            continue                # that window has reset since
        pct = 1.0 if w.get("status") == "rejected" else w.get("used_pct")
        if pct is None:
            continue
        if best is None or pct > best[0]:
            best = (pct, w)
    return {**best[1], "used_pct": best[0]} if best else None


def until(resets_at: int | None, now: float | None = None) -> str:
    if not resets_at:
        return ""
    mins = max(0, int((resets_at - (time.time() if now is None else now)) // 60))
    if mins < 60:
        return f"{mins} min"
    h, m = divmod(mins, 60)
    if h < 48:
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{h // 24} days"


def headline(provider: str, w: dict, now: float | None = None) -> str:
    """e.g. "Claude 5-hour window 96% used, resets in 41 min"."""
    name = NAMES.get(provider, provider.title())
    label = "monthly premium requests" if w.get("window") == "month" else f"{w.get('label') or w.get('window')} window"
    text = f"{name} {label} {round((w.get('used_pct') or 0) * 100)}% used"
    left = until(w.get("resets_at"), now)
    return f"{text}, resets in {left}" if left else text
