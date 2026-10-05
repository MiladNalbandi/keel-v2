"""Ship and cover helpers (keel v1 skills/ship and skills/cover, as engine pieces the workflow YAML uses).

    review_lenses     code action: the ship reviewers' lenses -> state.data.review_lenses (one reviewer per lens)
    coverage_report   code action: the coverage verdict and every accepted (not covered) group, as the step's note
    skip_units        the skippable steps after the opening gate, by band (always runs / deferred / optional)
    skip_menu_detail  the opening gate's text; parse_skips reads the answer (every skip needs a reason)
    final_report      the final review gate's text: exceptions first, then the verdict table for HEAD, trace, diff

A skipped step is recorded in state.data.ship_skipped ([{step, band, reason}]); the PR body prints it
(verdict_actions.pr_body) and the final review shows it again, with "push gate still outstanding" for a deferred one.
"""

from __future__ import annotations

import asyncio

from .. import rules
from ..tools import git
from . import blockers, verdict_actions, verdicts

DEFAULT_LENSES = ["correctness", "security", "performance"]
BANDS = {
    "deferred": "Deferred, not avoided: skipping moves the work; the push stays blocked until a fresh verdict for HEAD",
    "optional": "Optional: nothing downstream demands it; skipping is a real choice with a real cost",
}
VERDICT_ROWS = ["fast", "module", "lint", "release", "coverage", "deps", "audit", "trace", "arch", "memory"]


def _result(ok, note, detail="", update=None):
    return verdict_actions._result(ok, note, detail, update)


# ------------------------------------------------------------------ actions

async def review_lenses(a):
    return await asyncio.to_thread(_lenses, a)


def _lenses(a):
    """The lenses chosen at the opening gate (data.lenses_chosen), else review.lenses from .keel/config.yml, else
    correctness, security and performance; architecture joins when the project sets an architecture style or rules."""
    cfg = rules.load_config(a.root)
    chosen = a.data.get("lenses_chosen")
    conf = (cfg.get("review") or {}).get("lenses") if isinstance(cfg.get("review"), dict) else None
    if isinstance(chosen, list) and chosen:
        lenses, why = [str(x).strip() for x in chosen], "chosen at the opening gate"
    else:
        lenses = [str(x).strip() for x in conf] if isinstance(conf, list) and conf else list(DEFAULT_LENSES)
        why = "review.lenses" if isinstance(conf, list) and conf else "keel's default lenses"
        arch = (cfg.get("architecture") or {}) if isinstance(cfg.get("architecture"), dict) else {}
        if (arch.get("style") or verdict_actions.boundary_rules(cfg)) and "architecture" not in lenses:
            lenses.append("architecture")
    seen: list[str] = []
    for lens in lenses:
        if lens and lens not in seen:
            seen.append(lens)
    items = [{"id": lens, "title": f"{lens} lens"} for lens in seen]
    return _result(True, f"Review lenses ({why}): {', '.join(seen) or 'none'}.", update={"data": {**a.data, "review_lenses": items}})


async def coverage_report(a):
    return await asyncio.to_thread(_coverage_report, a)


def _coverage_report(a):
    """What cover leaves behind: the latest coverage verdict and every group accepted rather than covered."""
    v = verdicts.latest(a.key, "coverage")
    accepted = [x for x in a.data.get("coverage_accepted") or [] if isinstance(x, dict)]
    if not v:
        head = "Coverage: no verdict (no coverage configured, or the check was simulated)."
    elif (v.get("detail") or {}).get("available") is False:
        head = f"Coverage: not measured ({(v.get('detail') or {}).get('reason') or 'not available'})."
    else:
        d = v.get("detail") or {}
        head = (f"Coverage passes: {d.get('summary') or 'threshold met'}." if v.get("ok")
                else f"Coverage still fails: {d.get('summary') or '; '.join(d.get('problems') or [])}.")
    lines = [f"- {x.get('key') or x.get('id')}: {x.get('reason') or 'no reason given'}" for x in accepted]
    note = head + (f" {len(accepted)} group(s) accepted, not covered." if accepted else "")
    return _result(True, note, "\n".join(["Accepted, not covered:", *lines]) if lines else "")


# ------------------------------------------------------------------ the opening skip menu

def unit_of(step) -> str:
    return step.group or step.id


def covered(steps: list, after: int) -> list:
    """The steps a skip menu at `after` decides about: the ones after it, up to the next skip menu (feature's opening
    menu stops where ship's own begins)."""
    out = []
    for s in steps[after + 1:]:
        if s.skip_menu:
            break
        out.append(s)
    return out


def skip_units(steps: list, after: int) -> list[dict]:
    """The skippable units after step `after` (up to the next skip menu), in order: {name, band, steps: [step ids]}."""
    out: dict[str, dict] = {}
    for s in covered(steps, after):
        if s.skippable:
            u = out.setdefault(unit_of(s), {"name": unit_of(s), "band": s.skippable, "steps": []})
            u["steps"].append(s.id)
    return list(out.values())


def skip_menu_detail(steps: list, after: int) -> str:
    units = skip_units(steps, after)
    skippable = {sid for u in units for sid in u["steps"]}
    always = [s.name for s in covered(steps, after) if s.id not in skippable]
    lines = ["Which steps run this time? Skipping a step is not the same as removing its gate: every skip needs a reason, "
             "is shown again at the final review and printed in the PR body.", "",
             "Always runs: " + (", ".join(always) or "nothing else")]
    for band in ("deferred", "optional"):
        mine = [u for u in units if u["band"] == band]
        if mine:
            lines += ["", BANDS[band] + ":"] + [f"  - {u['name']}" for u in mine]
    lines += ["", "Approve runs everything (the default). To skip, approve with payload "
              "{\"skip\": {\"<name>\": \"<reason>\"}}; to pick the review lenses, add {\"lenses\": [\"correctness\", ...]}."]
    return "\n".join(lines)


def parse_skips(payload: dict, units: list[dict]) -> tuple[list[dict], list[str] | None, str | None]:
    """(skips [{step, band, reason}], lenses or None, problem or None) from the opening gate's answer."""
    raw = payload.get("skip") or {}
    if isinstance(raw, list):
        raw = {str(x.get("step") or x.get("name") or ""): x.get("reason") for x in raw if isinstance(x, dict)}
    if not isinstance(raw, dict):
        return [], None, "skip must map a step name to a reason"
    by = {u["name"]: u for u in units}
    out = []
    for name, reason in raw.items():
        if name not in by:
            return [], None, f"{name} cannot be skipped here (skippable: {', '.join(by) or 'none'})"
        if not str(reason or "").strip():
            return [], None, f"skipping {name} needs a reason"
        out.append({"step": name, "band": by[name]["band"], "reason": str(reason).strip()})
    lenses = payload.get("lenses")
    lenses = [str(x) for x in lenses if str(x).strip()] if isinstance(lenses, list) else None
    return out, lenses, None


# ------------------------------------------------------------------ the final review

def _mark(v: dict | None, head: str | None, tree: str | None) -> tuple[str, str]:
    if not v:
        return "not run", "—"
    d = v.get("detail") or {}
    if d.get("available") is False:
        return "not available", d.get("reason") or "—"
    res = "pass" if v.get("ok") else "FAIL"
    fresh = "" if not (head or tree) or verdicts.fresh(v, head, tree) else f" (stale: for {str(v.get('commit') or 'other files')[:7]})"
    return res + fresh, str(d.get("summary") or "—")[:200]


def final_report(root: str, key: str, state: dict, title: str) -> str:
    """keel v1 ship step 7: the whole picture, the parts that make it look worse first."""
    data = state.get("data") or {}
    gates = state.get("gates") or {}
    repo = git.is_repo(root)
    head, tree = verdicts.stamp(root) if repo else (None, None)
    cfg = rules.load_config(root)
    base = verdict_actions.base_ref(root, cfg, state.get("base_head")) if repo else None

    exc: list[str] = []
    for s in data.get("ship_skipped") or []:
        tail = " — its push gate is still outstanding" if s.get("band") == "deferred" else ""
        exc.append(f"- skipped: {s.get('step')} ({s.get('band')}): {s.get('reason') or 'no reason given'}{tail}")
    for x in data.get("coverage_accepted") or []:
        if isinstance(x, dict):
            exc.append(f"- coverage accepted, not covered: `{x.get('key') or x.get('id')}`: {x.get('reason') or 'no reason given'}")
    for d in data.get("dismissed_findings") or []:
        for f in d.get("findings") or []:
            exc.append(f"- blocking finding dismissed ({d.get('name') or d.get('step')}): {f} — why: {d.get('why') or 'no reason given'}")
    for line in gates.get("log") or []:
        if "no gate here" in line or "accepted:" in line or " go on after " in line:
            exc.append(f"- gate: {line}")
    for k, v in (gates.get("skipped") or {}).items():
        exc.append(f"- gate skipped: {k}: {v}")
    for u in state.get("unlocks") or []:
        if u.get("by") != "settings" or u.get("reason"):
            exc.append(f"- unlock: `{u.get('path')}` in {u.get('phase')}: {u.get('reason') or 'no reason given'}")
    for f in state.get("flaky") or []:
        exc.append(f"- flaky: {f.get('label')}: {', '.join(f.get('tests') or [])}")
    lint = verdicts.latest(key, "lint")
    if lint and not lint.get("ok") and (lint.get("detail") or {}).get("available") is not False \
            and (not (head or tree) or verdicts.fresh(lint, head, tree)):
        exc.append(f"- static checks fail: {(lint.get('detail') or {}).get('summary') or 'see the lint verdict'}")
    if repo:
        for b in blockers.push_blockers(root, base, project=key):
            if b["gate"] == "knowledge":
                continue        # the knowledge update and its check run after this approval (ship's memory steps)
            exc.append(f"- push blocker, {b['gate']}: {b['why']}")

    out = [f"# Final review: {title}", ""]
    out += [f"## Exceptions ({len(exc)})", ""] + (exc or ["None: nothing skipped, accepted, dismissed or blocking."]) + [""]
    out += ["The knowledge update and its check run after you approve.", ""]
    out += [f"## Verdicts for HEAD {str(head or 'not a git repository')[:7]}", "", "| check | result | summary |", "|---|---|---|"]
    for kind in VERDICT_ROWS:
        res, summary = _mark(verdicts.latest(key, kind), head, tree)
        out.append(f"| {kind} | {res} | {summary} |")
    out.append("")
    rounds = state.get("review_rounds") or {}
    if rounds:
        out += ["## Review fix rounds", ""] + [f"- {k}: {v}" for k, v in rounds.items()] + [""]
    rows = data.get("trace") or (verdict_actions.trace_rows(root, state.get("acs") or [], base) if repo and state.get("acs") else [])
    if rows:
        out += ["## Acceptance criteria", "", verdict_actions.trace_table(rows), ""]
    if state.get("spec"):
        out += [f"Spec: `{state['spec']}`", ""]
    if repo and base:
        stat = git.git(root, "diff", "--stat", f"{base}...HEAD").stdout.strip()
        if stat:
            out += ["## Diff", "", "```", verdict_actions._tail(stat, 2500), "```", ""]
    out += ["Approve: final approve (the knowledge base is updated next, then the PR body). "
            "Reject with what to change: the implementer makes the change and ship runs again from verify."]
    return "\n".join(out)
