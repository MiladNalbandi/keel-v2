"""The improvement report (#101 part 3): what crashed in keel's flows, how often, what the doctor fixed by itself, and
what to improve, from the crash log. Plain rules, no model call: GET /flow-doctor/report and keel_flow_doctor.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import crashlog

# what to improve, per kind, when it keeps happening (the report's advice)
ADVICE = {
    "auth": "A key was refused {n}×. Check it in Settings › Models & keys (Test says why), or replace it.",
    "no-key": "Agents ran without a key {n}×. Add the provider's API key in Settings › Models & keys.",
    "billing": "The provider account ran out of credit {n}×. Add credit, or set a cheaper model for the busy agents.",
    "model-not-found": "A model was not available {n}×. Pick a current model for the agents that use it.",
    "rate-limit": "The rate limit was hit {n}×. Run fewer flows at once (Settings › Resources › agents at once), or "
                  "raise the limit at the provider.",
    "overloaded": "The provider was overloaded {n}×. keel waits and retries; a fallback model on another provider "
                  "keeps flows going.",
    "network": "The provider could not be reached {n}×. Check this computer's network, proxy and DNS.",
    "context": "Conversations got too long {n}×. Smaller requests, or a model with a larger window, help.",
    "max-turns": "Agents ran out of turns {n}×. Split the work into smaller requests, or give those agents more turns.",
    "stuck": "Agents repeated the same call {n}×. A stronger model for those agents usually helps.",
    "refusal": "The model refused {n}×. Change those requests, or use another model for them.",
    "disk-full": "The disk was full {n}×. Free space, or move keel's data volume to a larger disk.",
    "git-lock": "git lock files got in the way {n}×. Make sure no other git program works in the project at the same time.",
    "keel-bug": "keel itself failed {n}×. Report it with the places below (a GitHub issue with this report).",
    "unknown": "Steps failed {n}× for other reasons. Read the examples below.",
}


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


# a crash's last line says how it ended
ENDS = {"repaired": "repaired", "retried": "retried", "asked": "waiting", "stopped": "stopped", "failed": "failed",
        "retrying": "repairing"}


def incidents(rows: list[dict]) -> list[dict]:
    """One crash per flow, step and kind (its lines: the crash, keel's repairs, the pause, the person's answer), in the
    order they began; each with its last line's fields and `end`: repaired | retried (the person's Retry ran it) |
    waiting | stopped | failed | repairing."""
    out: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("thread") or "", r.get("step") or "", r.get("kind") or "unknown")
        have = out.get(key)
        if have and have["end"] in ("repaired", "retried") and r.get("outcome") in ("retrying", "asked", "failed"):
            key = (*key, r.get("at"))      # the same step crashed again later: a new crash
            have = None
        first = have["first_at"] if have else r.get("at")
        tries = (have or {}).get("tries", 0) + (1 if r.get("outcome") == "retrying" else 0)
        out[key] = {**(have or {}), **r, "first_at": first, "tries": tries, "end": ENDS.get(r.get("outcome"), "failed")}
    return sorted(out.values(), key=lambda c: c.get("first_at") or 0)


def build(days: int = 7, now: float | None = None) -> dict:
    now = now or time.time()
    since = now - max(1, int(days)) * 86_400
    crashes = incidents(crashlog.read(since))
    kinds: dict[str, dict] = {}
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for c in crashes:
        by_kind[c.get("kind") or "unknown"].append(c)
    for kind, items in by_kind.items():
        ends = Counter(c["end"] for c in items)
        last = items[-1]
        spots = Counter(c["where"] for c in items if c.get("where"))
        kinds[kind] = {
            "kind": kind, "title": last.get("title") or kind, "fix": last.get("fix") or "", "count": len(items),
            **{e: ends.get(e, 0) for e in ("repaired", "retried", "waiting", "stopped", "failed")},
            "last_at": iso(last.get("at")),
            "steps": [f"{w} › {s}" for (w, s), _n in Counter((c.get("workflow") or "?", c.get("step") or "?")
                                                             for c in items).most_common(5)],
            "providers": [p for p, _n in Counter(c.get("provider") for c in items if c.get("provider")).most_common(3)],
            "where": [f"{w} ({n}×)" for w, n in spots.most_common(5)],
            "example": {k: last.get(k) for k in ("error", "thread", "project", "workflow", "step", "agent", "provider", "model")
                        if last.get(k)},
            "advice": ADVICE.get(kind, ADVICE["unknown"]).format(n=len(items)),
        }
    ordered = sorted(kinds.values(), key=lambda k: (-k["count"], k["kind"]))
    out = {
        "days": int(days), "since": iso(since), "until": iso(now), "total": len(crashes),
        **{e: sum(k[e] for k in ordered) for e in ("repaired", "retried", "waiting", "stopped", "failed")},
        "flows": len({c.get("thread") for c in crashes if c.get("thread")}),
        "kinds": ordered,
        "recent": [_public(c) for c in crashes[-30:]][::-1],
    }
    out["markdown"] = markdown(out)
    return out


def _public(r: dict) -> dict:
    return {**{k: r.get(k) for k in ("thread", "project", "workflow", "step", "agent", "provider", "model", "kind", "title",
                                     "outcome", "end", "action", "tries", "where", "error") if r.get(k) is not None},
            "at": iso(r.get("at"))}


def markdown(rep: dict) -> str:
    """The report as Markdown: to paste into an issue or a note."""
    lines = [f"# keel flow doctor: the last {rep['days']} days", ""]
    if not rep["total"]:
        lines.append("No crashes. Every flow ran without one.")
        return "\n".join(lines) + "\n"
    lines.append(f"{rep['total']} crashes in {rep['flows']} flows: {rep['repaired']} repaired by keel, {rep['retried']} "
                 f"went on after your Retry, {rep['waiting']} wait for you, {rep['stopped']} stopped, {rep['failed']} failed.")
    lines += ["", "## What to improve", ""]
    lines += [f"{i}. **{k['title']}** ({k['count']}×): {k['advice']}" for i, k in enumerate(rep["kinds"], 1)]
    for k in rep["kinds"]:
        lines += ["", f"## {k['title']} ({k['kind']}, {k['count']}×)", ""]
        lines.append(f"- Repaired by keel: {k['repaired']} · after your Retry: {k['retried']} · waiting: {k['waiting']} · "
                     f"stopped: {k['stopped']} · failed: {k['failed']} · last: {k['last_at']}")
        if k["steps"]:
            lines.append("- Steps: " + ", ".join(k["steps"]))
        if k["providers"]:
            lines.append("- Providers: " + ", ".join(k["providers"]))
        if k["where"]:
            lines.append("- In keel's code: " + ", ".join(f"`{w}`" for w in k["where"]))
        err = (k["example"].get("error") or "").strip().splitlines()
        if err:
            lines += ["", "```", *err[:8], "```"]
    return "\n".join(lines) + "\n"
