"""Ready-made orgs: the YAML files in templates/, shown in Settings › Plugins › Marketplace › Orgs and on the Org page's
"New org". Each one is a starting point: the person picks it, can change any line, and saves it as their own org.

    all_templates()    every template, in the order of ORDER: id, title, summary, its size and its chart's roles and links
    one(tid)           one template with its YAML, or None

A template is checked like any org (config.parse) and names only workflows keel has (tests/test_org.py checks both), so
the one a person picks saves without a problem. It names no project: the person's org works on any project until they
add `projects:`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from . import config

DIR = Path(__file__).resolve().parent / "templates"

# id, title, the one line under it; the order they show in
ORDER: list[tuple[str, str]] = [
    ("solo", "You and two agents, no lead: a developer and a scout. You give each mission straight to the one who does it."),
    ("small-team", "A tech lead with a frontend and a backend engineer. You approve the lead's plan and accept its report."),
    ("bug-squad", "A triage lead with a fixer and a test writer: give it a bug, get it fixed with a test that proves it."),
    ("startup", "You are the founder: a CTO splits the work between a web lead and an API lead, each with its own engineers."),
    ("maintainer", "For an open-source project: a triage lead gives each issue to a fixer, a PR reviewer or a docs writer."),
    ("quality", "A quality lead with a test writer, a bug hunter and a linter, to harden one part of the code."),
]


def _levels(org: config.Org) -> int:
    """How many levels deep the org is: the person, then each level of the roles it gives work to."""
    deepest = 0
    for rid in config.humans(org) or list(org.roles)[:1]:
        seen, row, depth = {rid}, [rid], 1
        while row:
            nxt = [t for r in row for t in config.team(org, r) if t not in seen]
            seen.update(nxt)
            row = nxt
            depth += 1 if nxt else 0
        deepest = max(deepest, depth)
    return deepest


def shape(org: config.Org) -> dict:
    """The roles and links in the Org page's shape (routes._view), so its chart draws a template as it draws an org."""
    return {
        "roles": [{"id": rid, "title": r.title or ("You" if r.human else rid), "human": r.human, "agent": r.agent,
                   "workflows": r.workflows, "plan": r.plan, "review": r.review, "autonomy": r.autonomy,
                   "team": config.team(org, rid), "boss": config.boss(org, rid)} for rid, r in org.roles.items()],
        "links": [{"from": link.src, "to": link.dst, "can": list(link.can)} for link in org.links],
    }


@lru_cache(maxsize=1)
def _load() -> tuple[dict, ...]:
    out = []
    for tid, summary in ORDER:
        text = (DIR / f"{tid}.yaml").read_text(encoding="utf-8")
        org = config.parse(text)
        people = len(config.humans(org))
        out.append({"id": tid, "title": org.name or tid, "summary": summary, "people": people,
                    "agents": len(org.roles) - people, "levels": _levels(org),
                    "workflows": sorted({w for r in org.roles.values() for w in r.workflows}), **shape(org), "yaml": text})
    return tuple(out)


def all_templates() -> list[dict]:
    return [dict(t) for t in _load()]


def one(tid: str) -> dict | None:
    return next((dict(t) for t in _load() if t["id"] == tid), None)
