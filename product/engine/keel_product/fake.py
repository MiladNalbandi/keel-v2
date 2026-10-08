"""The fake model's answers for keel Product's agents (KEEL_FAKE=1, tests and the repeatable end-to-end run). They
follow the workflows' formats: keel-questions, BEGIN-DOC/END-DOC documents, RISK/RECOMMEND markers and the plan JSON."""

from __future__ import annotations

import json
import re

QUESTIONS = [
    {"id": "who", "question": "Which visitors should see the new behaviour first?",
     "options": [{"label": "Everyone in the target group", "recommended": True}, {"label": "Only logged-in visitors"}]},
    {"id": "metric", "question": "How will you know it worked?",
     "options": [{"label": "Checkout conversion in the target group", "recommended": True}, {"label": "Fewer support tickets"}]},
]


def _title(req) -> str:
    t = req.title or "the initiative"
    return re.sub(r"^INI-\d+\s*·\s*", "", t).split(" · ")[0].strip() or t


def _pairs(prompt: str) -> list[tuple[str, str]]:
    """'Repos: web-shop (web), payments-api (payments)' in the step's instructions → [(repo, team)]."""
    m = re.search(r"Repos:\s*(.+?)(?:\s+A note from|\s*$)", prompt or "", re.S)
    found = re.findall(r"([\w.-]+)\s*\(([\w-]+)\)", m.group(1)) if m else []
    return found or [("app", "team")]


def brief(req):
    fb = req.feedback or ""
    if not fb:
        return None, "", "Before I write the brief I need two answers.\n```keel-questions\n" + json.dumps(QUESTIONS) + "\n```", {}
    revised = "" if fb.startswith("Answers to your questions:") else f"\n\n_Revised after the product owner's note: {fb.strip()[:200]}_"
    doc = f"""BEGIN-DOC
## Problem
Visitors in the target group cannot do what "{_title(req)}" promises, so some of them leave before they buy.

## Users
Visitors in the target group, on the web shop.

## Outcome metric
Checkout conversion in the target group, 2.1% → 2.6%, measured 4 weeks after the release against the 4 weeks before.

## Guardrails
- Refund rate stays under 1.5%
- No change for everyone else

## In scope
- The web shop's product, cart and checkout pages

## Not in scope
- The mobile app (later)

## Open questions
- Legal: may we change this for one group only? (Legal)
{revised}
END-DOC"""
    return None, "", doc, {}


def impact(req):
    item = req.item or {}
    repo = item.get("id") or "repo"
    risk = "medium" if any(k in repo for k in ("api", "pay", "db")) else "low"
    unknown = "UNKNOWN: which external service gives the data (not in the code)" if risk == "medium" else ""
    doc = f"""BEGIN-DOC
## Parts it touches
- README.md:1 (the entry point of {repo})

## APIs
- {'GET /prices gets a new field (API v2)' if risk == 'medium' else 'none'}

## Data
- {'a new table: rates (migration)' if risk == 'medium' else 'none'}

## Events
- none

## Risk
{risk}: {'a public API and a migration' if risk == 'medium' else 'screens only'}

## Unknowns
{unknown or '- none'}

## Estimate
{'4–7' if risk == 'medium' else '2–4'} developer days
END-DOC
RISK: {risk}"""
    return None, "", doc, {}


def memo(req):
    note = f"\n\n_Revised after the note: {req.feedback.strip()[:200]}_" if req.feedback else ""
    doc = f"""BEGIN-DOC
## Value
Checkout conversion in the target group, 2.1% → 2.6%.

## Cost
6–11 developer days in total (confidence: medium).

## Time
About 2 weeks; the critical path is the API contract and the backend.

## Risk
- A migration on a big table: add it online, fill it in batches.

## Options
- **A · MVP**: the web shop only. 6–11 days, 2 weeks, medium risk, most of the gain.
- **B · Full**: also the mobile app. 15–25 days, 5 weeks, high risk, a little more gain.
- **C · Not now**: nothing changes; conversion stays low.

I recommend A: most of the gain for the least risk.{note}
END-DOC
RECOMMEND: A"""
    return None, "", doc, {}


def team_plan(req):
    pairs = _pairs(req.prompt)
    teams = sorted({t for _r, t in pairs})
    lines = [f"- {t}: its epic covers {', '.join(r for r, tt in pairs if tt == t)}" for t in teams]
    return None, "", "Team plan\n" + "\n".join(lines) + "\nOrder: the contract first, then the teams in parallel, then the release.", {}


def stories(req):
    pairs = _pairs(req.prompt)
    teams: dict[str, list[str]] = {}
    for repo, team in pairs:
        teams.setdefault(team, []).append(repo)
    epics, first_contract = [], None
    for n, (team, repos) in enumerate(sorted(teams.items())):
        code = re.sub(r"[^A-Z]", "", team.upper())[:4] or f"T{n}"
        stories_ = []
        if first_contract is None:
            first_contract = f"{code}-S0"
            stories_.append({"id": first_contract, "title": "Agree the API contract", "repo": repos[0],
                             "criteria": ["AC-1 [API] The contract describes the new field and its errors"],
                             "depends_on": [], "estimate_days": [1, 1], "tasks": ["Write the OpenAPI change"]})
        for k, repo in enumerate(repos, 1):
            stories_.append({"id": f"{code}-S{k}", "title": f"Change {repo}", "repo": repo,
                             "criteria": [f"AC-1 [API] {repo} returns the new field", f"AC-2 [WEB] The page shows it in {repo}"],
                             "depends_on": [first_contract] if first_contract != f"{code}-S{k}" else [],
                             "estimate_days": [2, 4], "tasks": [f"Change {repo}", "Add the tests"]})
        epics.append({"id": code, "team": team, "title": f"{team} work", "stories": stories_})
    return None, "", "Stories\n```json\n" + json.dumps({"epics": epics}, indent=2) + "\n```", {}


def outcome(req):
    doc = """BEGIN-DOC
## Result
The target was 2.1% → 2.6%. It is now as the product owner entered it; it moved in the right direction.

## Guardrails
- Refund rate: held

## What we learned
- The contract-first order let the teams work at the same time.

## Next
- Close, and look at option B next quarter.
END-DOC"""
    return None, "", doc, {}


BY_STEP = {
    ("product-manager", "brief"): brief,
    ("impact-analyst", "one impact analyst per repo"): impact,
    ("product-manager", "decision memo"): memo,
    ("planner", "team plan"): team_plan,
    ("story-writer", "stories"): stories,
    ("product-manager", "outcome"): outcome,
}


def answer(req):
    fn = BY_STEP.get((req.agent, req.step_name))
    return fn(req) if fn else None
