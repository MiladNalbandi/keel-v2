"""The Org plugin's workflow actions. They run inside a mission's flow and talk to the org through its tables only.

    org:check      before the plan gate: the same checks as org:delegate, and what the gate shows (each mission, to
                   whom, its workflow and budget), so you approve exactly what will be given out.
    org:delegate   reads the plan step's ```keel-org``` block and makes one child mission per item, after checking
                   the links (this role may delegate to that role), the workflow (that role may run it), the org's
                   limits (children, depth) and the budget. The org runner starts them when the flow ends.
    org:report     reads the review step's ```keel-org-report``` block (or its whole text) as the mission's report.
    org:close      keeps the person's answer at the report gate (accept / send_back, and why) for the runner.

A flow proves which mission it runs with the key the runner gave it at the start (data.org.key): a flow someone else
started cannot give work out in a mission's name.
"""

from __future__ import annotations

import json
import re

from keel_engine.runtime.actions import ActionInput, ActionResult

from . import config, store

TEXT_MAX = 20_000


def block(text: str | None, name: str) -> dict | None:
    """The last ```<name> fenced JSON block in an agent's answer, as a dict (None: there is none, or it is not JSON)."""
    if not text:
        return None
    found = re.findall(r"```" + re.escape(name) + r"[ \t]*\n(.*?)```", text, re.S)
    for raw in reversed(found):
        try:
            v = json.loads(raw)
        except ValueError:
            continue
        if isinstance(v, dict):
            return v
    return None


def _mission(inp: ActionInput) -> tuple[dict | None, str]:
    o = inp.data.get("org") if isinstance(inp.data, dict) else None
    if not isinstance(o, dict) or not o.get("mission"):
        return None, "This flow does not run an org mission (no data.org): start it from the Org page."
    m = store.get(str(o["mission"]))
    if not m:
        return None, f"There is no mission {o['mission']}."
    if not m.get("run_key") or o.get("key") != m["run_key"]:
        return None, "This flow did not start this mission (its key does not match): only the org runner starts missions."
    return m, ""


def _org(m: dict) -> tuple[config.Org | None, str]:
    row = store.org_row(m["org_id"])
    if not row:
        return None, f"The org {m['org_id']} was deleted."
    try:
        return config.parse(row["yaml"]), ""
    except config.OrgError as exc:
        return None, str(exc)


def _text(inp: ActionInput, default_step: str) -> str:
    step = str((inp.params or {}).get("from") or default_step)
    return str((inp.data or {}).get(f"{step}_text") or "")


NO_BLOCK = ("End the plan with a ```keel-org JSON block: {\"summary\": ..., \"children\": [{\"role\", \"title\", "
            "\"request\", \"workflow\"}]} (an empty children list when no one else is needed).")
RULES_HINT = "Change the plan so each mission goes to a role this one links to, with a workflow that role may run."


def _plan(inp: ActionInput, m: dict, org: config.Org) -> tuple[dict | None, list[tuple], list[str]]:
    """The plan's ```keel-org block checked against the org: (plan, rows, problems). plan None: there is no block."""
    plan = block(_text(inp, "plan"), "keel-org")
    if plan is None:
        return None, [], ["The plan has no ```keel-org block with the team's missions."]
    items = plan.get("children") or []
    if not isinstance(items, list):
        return plan, [], ["children in the ```keel-org block is not a list."]
    problems: list[str] = []
    if items and len(items) > org.limits.children:
        problems.append(f"{len(items)} missions is more than this org allows at once ({org.limits.children}).")
    if items and m["depth"] + 1 > org.limits.depth:
        problems.append(f"The org is {org.limits.depth} levels deep at most: this mission cannot give work further down.")
    rows = []
    for n, it in enumerate(items):
        if not isinstance(it, dict):
            problems.append(f"children[{n}] is not an object.")
            continue
        role_id = str(it.get("role") or "")
        role = org.roles.get(role_id)
        if role is None:
            problems.append(f"children[{n}]: there is no role {role_id!r} (the team is {', '.join(config.team(org, m['role'])) or 'empty'}).")
            continue
        if not config.may(org, m["role"], role_id, "delegate"):
            problems.append(f"children[{n}]: {m['role']} may not give work to {role_id} (no delegate link).")
            continue
        wf = str(it.get("workflow") or role.first_flow() or "")
        if wf not in role.workflows:
            problems.append(f"children[{n}]: {role_id} may not run {wf!r} (its workflows: {', '.join(role.workflows)}).")
            continue
        title = str(it.get("title") or "").strip()
        request = str(it.get("request") or "").strip()
        if not title or not request:
            problems.append(f"children[{n}]: a mission needs a title and a request.")
            continue
        cap = it.get("cap_tokens")
        rows.append((role_id, role, wf, title, request, cap if isinstance(cap, int) and cap > 0 else None))
    return plan, rows, problems


REVIEW_SHARE = 5     # a role with a review flow keeps a fifth of what is left for it


def _share(m: dict, n: int, inp: ActionInput | None = None, org: config.Org | None = None) -> int | None:
    """Each team member's budget: what the mission has left after its earlier flows and this plan so far, less what the
    role keeps for its review, split evenly."""
    if not m.get("cap_tokens") or not n:
        return None
    u = ((inp.state or {}).get("usage") or {}) if inp else {}
    now = int(u.get("tokens_in") or 0) + int(u.get("tokens_out") or 0)
    left = max(0, int(m["cap_tokens"]) - int(m.get("used_tokens") or 0) - now)
    role = org.roles.get(m["role"]) if org else None
    if role is not None and role.review:
        left -= left // REVIEW_SHARE
    return left // n


def check(inp: ActionInput) -> ActionResult:
    """Before the plan gate: the plan checked against the org, and what the gate shows you (data.<gate>_detail): each
    mission it will give out, to whom, with which workflow and budget."""
    m, why = _mission(inp)
    if not m:
        return ActionResult(False, why, stop=True)
    org, why = _org(m)
    if not org:
        return ActionResult(False, why, stop=True)
    plan, rows, problems = _plan(inp, m, org)
    if plan is None:
        return ActionResult(False, problems[0], NO_BLOCK, final=True)
    if problems:
        return ActionResult(False, "The plan breaks the org's rules: " + " ".join(problems), RULES_HINT, final=True)
    gate = str((inp.params or {}).get("gate") or "plan_gate")
    share = _share(m, len(rows), inp, org)
    lines = [str(plan.get("summary") or "").strip() or "(no summary)", ""]
    if rows:
        lines.append(f"It gives out {len(rows)} mission(s):")
        for n, (role_id, role, wf, title, request, cap) in enumerate(rows, 1):
            budget = min(x for x in (cap, share) if x) if (cap or share) else None
            lines.append(f"{n}. {role.title or role_id} ({role_id}) · {wf}{f' · {budget:,} tokens' if budget else ''}: {title}")
            lines.append("   " + request[:600].replace("\n", "\n   "))
    else:
        lines.append("It gives out nothing: the lead reports from its own work.")
    text = _text(inp, "plan")
    body = re.sub(r"```keel-org[ \t]*\n.*?```", "", text, flags=re.S).strip()
    if body:
        lines += ["", "The lead's plan:", body[:4000]]
    data = {**(inp.data or {}), f"{gate}_detail": "\n".join(lines)}
    return ActionResult(True, f"The plan keeps to the org's rules ({len(rows)} mission(s)).", update={"data": data})


def delegate(inp: ActionInput) -> ActionResult:
    m, why = _mission(inp)
    if not m:
        return ActionResult(False, why, stop=True)
    org, why = _org(m)
    if not org:
        return ActionResult(False, why, stop=True)
    round_mark = f"{m['id']}#r{m['send_backs']}"
    made = [c for c in store.children(m["id"]) if c.get("source") == round_mark]
    if made:
        return ActionResult(True, f"Already gave out {len(made)} mission(s) in this round.")
    plan, rows, problems = _plan(inp, m, org)
    if plan is None:
        return ActionResult(False, problems[0], NO_BLOCK, final=True)
    if problems:
        return ActionResult(False, "The plan breaks the org's rules: " + " ".join(problems), RULES_HINT, final=True)
    summary = str(plan.get("summary") or "").strip()
    if summary:
        store.update(m["id"], note=summary[:2000])
    if not rows:
        store.note(m["id"], "plan", "The plan needs no one else: the mission reports from its own work.")
        return ActionResult(True, "No missions to give out.")
    share = _share(m, len(rows), inp, org)
    out = []
    for role_id, role, wf, title, request, cap in rows:
        child_cap = min(x for x in (cap, share) if x) if (cap or share) else None
        c = store.add(org_id=m["org_id"], role=role_id, project_id=m["project_id"], title=title, request=request,
                      # a role that leads its own team plans first (its team is in its request), like a mission you give
                      parent_id=m["id"], source=round_mark, workflow=wf, stage="plan" if wf == role.plan else "work",
                      status="proposed" if role.autonomy == "watch" else "queued", cap_tokens=child_cap, depth=m["depth"] + 1)
        store.note(c["id"], "made", f"{m['role']} gave this to {role_id} ({wf}).", {"parent": m["id"]})
        out.append(f"{role_id}: {title}")
    store.note(m["id"], "delegated", f"Gave out {len(out)} mission(s).", {"missions": out})
    return ActionResult(True, f"Gave out {len(out)} mission(s): " + "; ".join(out))


def report(inp: ActionInput) -> ActionResult:
    m, why = _mission(inp)
    if not m:
        return ActionResult(False, why, stop=True)
    text = _text(inp, "review")
    rep = block(text, "keel-org-report")
    if rep is None:
        rep = {"summary": text.strip()[:TEXT_MAX] or "(the review wrote nothing)"}
    clean = {k: rep[k] for k in ("summary", "done", "decisions", "follow_ups", "risks", "children") if k in rep}
    store.update(m["id"], report=clean)
    store.note(m["id"], "report", "The report is written.")
    gate = str((inp.params or {}).get("gate") or "accept")
    data = {**(inp.data or {}), f"{gate}_detail": report_text(clean)}
    return ActionResult(True, "The report is kept for the mission.", update={"data": data})


def report_text(rep: dict) -> str:
    """The report as the report gate shows it."""
    lines = [str(rep.get("summary") or "").strip()]
    for label, key in (("Done", "done"), ("Decided", "decisions"), ("Next", "follow_ups"), ("Risks", "risks")):
        items = [str(x) for x in rep.get(key) or [] if str(x).strip()]
        if items:
            lines += ["", f"{label}:"] + [f"- {x}" for x in items]
    kids = [c for c in rep.get("children") or [] if isinstance(c, dict)]
    if kids:
        lines += ["", "Checked:"] + [f"- {'✓' if c.get('ok', True) else '✗'} {c.get('role')}{': ' + str(c['note']) if c.get('note') else ''}"
                                     for c in kids]
    return "\n".join(lines).strip()


def close(inp: ActionInput) -> ActionResult:
    m, why = _mission(inp)
    if not m:
        return ActionResult(False, why, stop=True)
    gate = str((inp.params or {}).get("gate") or "accept")
    ans = (inp.data or {}).get(f"{gate}_answer") or {}
    if not ans and ((inp.data or {}).get("org") or {}).get("level") == "team":
        # a team's mission: no person was asked (lead-review's branch went around the gate); its boss checks the report
        return ActionResult(True, "No one is asked here: the report goes up to the boss, who checks it in its own review.")
    choice = str(ans.get("choice") or "accept")
    store.update(m["id"], decision={"choice": choice, "why": str(ans.get("why") or "")[:2000]})
    return ActionResult(True, f"The person chose {choice}.")


ACTIONS = {"org:check": check, "org:delegate": delegate, "org:report": report, "org:close": close}
PARAMS = {"org:check": {"from": "optional", "gate": "optional"}, "org:delegate": {"from": "optional"}, "org:report": {"from": "optional", "gate": "optional"}, "org:close": {"gate": "optional"}}
DOCS = {
    "org:check": {"summary": "check the plan against the org's rules and show it at the plan gate",
                  "steps": ["read the plan's keel-org block", "check each mission against the links, workflows and limits",
                            "write what the plan gate shows: each mission, to whom, its workflow and budget"]},
    "org:delegate": {"summary": "give the plan's missions to the team, after checking the org's links and limits",
                     "steps": ["read the plan's keel-org block", "check each mission against the links, workflows and limits",
                               "make the child missions; the org runner starts them"]},
    "org:report": {"summary": "keep the review's report for the mission", "steps": ["read the keel-org-report block"]},
    "org:close": {"summary": "keep the person's answer to the report", "steps": ["read the gate's answer"]},
}
