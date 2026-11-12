"""The fake model's answers for org-tech-lead: a plan from the team the mission text lists, or a report."""

from __future__ import annotations

import json
import os
import re

TEAM_LINE = re.compile(r"^- ([a-z][a-z0-9_-]*): (.+?); workflows: ([a-z0-9_, -]+)$", re.M)


def answer(prompt: str) -> str:
    if "Your team has ended its work on" in prompt:
        report = {"summary": "The team finished. I checked each change against its request.",
                  "done": ["Each mission's flow ended."], "decisions": ["Kept the existing style."],
                  "follow_ups": [], "risks": [],
                  "children": [{"role": r, "title": "", "ok": True, "note": "checked the diff"}
                               for r in re.findall(r"^## ([a-z][a-z0-9_-]*): ", prompt, re.M)]}
        return "The team's work looks right.\n\n```keel-org-report\n" + json.dumps(report) + "\n```\n"
    team = TEAM_LINE.findall(prompt)
    cap = int(os.environ.get("KEEL_FAKE_ORG_CHILDREN", "2"))
    title = next((ln[len("Mission: "):] for ln in prompt.splitlines() if ln.startswith("Mission: ")), "the mission")
    children = [{"role": rid, "title": f"{title} ({rid})", "request": f"Do the {rid} part of: {title}.",
                 "workflow": wfs.split(",")[0].strip()} for rid, _t, wfs in team[:cap]]
    plan = {"summary": f"Split {title} into {len(children)} part(s).", "children": children}
    return "I read the mission and the code.\n\n```keel-org\n" + json.dumps(plan) + "\n```\n"
