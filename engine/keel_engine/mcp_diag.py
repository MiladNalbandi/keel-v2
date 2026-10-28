"""0.18.64 tools to see what keel and an agent really did, and whether the MCP connection itself is well (a field report:
a retry lost a reject note and nobody could see what changed; a keel restart broke the client's connection).

    keel_health        is keel up, its version, and how fresh this connection is (keel2 mcp's relay starts the server
                       again after a keel restart; KEEL_MCP_RELAY_N / KEEL_MCP_RELAY_SINCE say how often and when)
    keel_reconnect     keel2 mcp's relay answers it itself (it starts the server again); without a relay it says how
    keel_agent_prompt  the exact prompt (system + task) one agent call got (api GET /jobs/{id}/prompt)
    keel_flow_diff     what a step's last run changed against the one before: its document's versions, else its agent's
                       answers (api GET /projects/{pid}/flows/{tid}/step-diff)
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from . import config
from .mcp_api import ApiError, KeelApi, _clock, _flow, _flow_of, _project, cut, q as _q, zone_name

STARTED = time.time()                  # this server's start: a new one after every reconnect
PROMPT_PAGE = 20_000                   # characters of a prompt per answer (page asks for the rest)
DIFF_MAX = 30_000


def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{s // 60} min"
    return f"{s // 3600} h {s % 3600 // 60} min"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def health(api: KeelApi, now: float | None = None) -> str:
    """keel's api and engine, their version against this server's, and this connection: when its server started, and
    (with keel2 mcp's relay) how often the relay started it again after a keel restart."""
    now = time.time() if now is None else now
    lines = []
    t0 = time.monotonic()
    try:
        h = api.get("/health") or {}
        ms = int((time.monotonic() - t0) * 1000)
        version = str(h.get("version") or "?")
        lines.append(f"keel        up · version {version} · api {api.url} answered in {ms} ms")
        lines.append(f"engine      {'up' if h.get('engine') else 'not answering (flows wait until it is back)'}"
                     + (" · fake model only" if h.get("fake") else ""))
        if version not in ("?", "dev") and version != config.VERSION:
            lines.append(f"versions    this MCP server is {config.VERSION}, keel is {version}: reconnect (keel_reconnect, or "
                         "restart the MCP client) for keel's newest tools")
    except ApiError as exc:
        lines.append(f"keel        not answering: {exc}")
    relay_n = os.environ.get("KEEL_MCP_RELAY_N", "").strip()
    since = STARTED
    conn = f"connection  this server started {_clock(_iso(since))} ({_ago(now - since)} ago)"
    if relay_n.isdigit():
        n = int(relay_n) - 1
        conn += (" · keel2 mcp's relay: " + ("no reconnect yet" if n <= 0 else
                 f"reconnected {n} time{'s' if n != 1 else ''} after keel restarted"))
        lines.append(conn)
        lines.append("reconnect   automatic: when keel restarts, the relay waits for it and starts this server again; a call "
                     "in between answers \"keel is restarting\". keel_reconnect starts it again now.")
    else:
        lines.append(conn + " · no relay")
        lines.append("reconnect   this connection has no relay (keel2 mcp without python3, KEEL_MCP_RELAY=0, or an agent's "
                     "server): after a keel restart, restart the MCP client (Claude Code: /mcp, reconnect).")
    lines.append(f"times       {zone_name()} (this computer's zone, when keel2 mcp could tell it)")
    return "\n".join(lines)


def reconnect() -> str:
    """Only reached without keel2 mcp's relay: the relay answers keel_reconnect itself and never passes it on."""
    return ("keel: this connection has no relay to reconnect it (keel2 mcp without python3, KEEL_MCP_RELAY=0, or an "
            "agent's server). Restart the MCP client's keel server instead (Claude Code: /mcp, then reconnect).")


def agent_prompt(api: KeelApi, agent_id: str, page: int = 1) -> str:
    """The prompt one agent call got, as keel kept it: the system prompt (the agent's role, skills) and the task prompt,
    each cut at keel's cap; a long one comes in pages."""
    try:
        p = api.get(f"/jobs/{_q(agent_id)}/prompt") or {}
    except ApiError as exc:
        if exc.status == 404:
            raise ApiError(f"keel kept no prompt for {agent_id}: not an agent call of a flow (keel_agents lists them), or "
                           "one that ran before keel 0.18.64 kept prompts.", 404) from exc
        raise
    text = (f"=== system ({len(p.get('system') or '')} characters"
            + (", cut by keel" if p.get("system_cut") else "") + ")\n" + str(p.get("system") or "")
            + f"\n\n=== task ({len(p.get('prompt') or '')} characters" + (", cut by keel" if p.get("prompt_cut") else "")
            + ")\n" + str(p.get("prompt") or ""))
    pages = max(1, -(-len(text) // PROMPT_PAGE))
    page = max(1, min(int(page or 1), pages))
    head = (f"agent call {agent_id} · {p.get('agent') or 'agent'} · step {p.get('step') or '?'}"
            + (f" · {p['ac']}" if p.get("ac") else "") + f" · {_clock(p.get('at'))}"
            + (f" · page {page} of {pages} (keel_agent_prompt page={page + 1} for the next)" if page < pages
               else f" · page {page} of {pages}" if pages > 1 else ""))
    return head + "\n" + text[(page - 1) * PROMPT_PAGE:page * PROMPT_PAGE]


def flow_diff(api: KeelApi, step: str | None = None, project: str | None = None, thread_id: str | None = None,
              ac: str | None = None) -> str:
    """What the last run of a step changed against the run before (a retry, a send-back, a reject): the step's document
    (the versions keel kept of the file it wrote), else its agent's answers. No step: the steps that ran more than once."""
    p = _project(api, project)
    if thread_id:
        thread = _flow_of(api, p["id"], thread_id)
        if not thread:
            raise ApiError(f"no flow {thread_id} on {p['id']} (keel_flows lists them).")
    else:
        thread, _ = _flow(api, p["id"])
        if not thread:
            raise ApiError(f"no flow on {p['id']}; pass thread_id (keel_flows lists them).")
    tid = thread["thread_id"]
    d = api.get(f"/projects/{_q(p['id'])}/flows/{_q(tid)}/step-diff", step=step, ac=ac) or {}
    if not step:
        rows = d.get("steps") or []
        if not rows:
            return f"flow {tid}: no step ran more than once yet."
        return (f"flow {tid}: steps that ran more than once (keel_flow_diff step=<id> shows what the last run changed)\n"
                + "\n".join(f"- {r.get('step')}" + (f" {r['ac']}" if r.get("ac") else "") + f": {r.get('runs')} runs"
                            + (f" · {r['kind']}" if r.get("kind") else "") for r in rows))
    if d.get("kind") == "none":
        return f"flow {tid}: {step} " + (f"({ac}) " if ac else "") + (d.get("why") or "ran once, so there is nothing to compare.")
    a, b = d.get("before") or {}, d.get("after") or {}
    what = (f"the document {d.get('path')} (version {a.get('version')} → {b.get('version')})" if d.get("kind") == "document"
            else f"the agent's answer ({a.get('agent') or 'agent'} call {a.get('id')} → {b.get('id')})")
    head = (f"flow {tid} · step {step}" + (f" · {ac}" if ac else "") + f" · {d.get('runs')} runs · {what}\n"
            f"before {_clock(a.get('at'))} · after {_clock(b.get('at'))}")
    diff = str(d.get("diff") or "")
    if not diff.strip():
        return head + "\nno change: the last two runs wrote the same text."
    return head + "\n" + cut(diff, DIFF_MAX)
