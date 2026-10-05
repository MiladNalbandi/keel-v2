"""Code actions of the review, diagnose, fix and change flows.

    review_scope        review: the lens argument and the base ref → one item per reviewer (data.review_lenses)
    report              the answers of the flow's fan-out steps verbatim, plus a tally; shown at the next gate
    investigation_note  diagnose: docs/investigations/<date>-<slug>.md with every hypothesis, committed
    bug_intake          fix: no_gates (gates.bug_gates: false) waives Gate R and F; needs_e2e from a seed
    reset               fix: put back what keel's agents left uncommitted (never the user's own files)
    change_size         change: inline ACs as CHG-<n>.<m>, the size keel recommends, shown at the scope gate
    change_start        change: a small change runs with one gate at the end; staying small against advice is recorded
"""

from __future__ import annotations

import datetime
import re
from dataclasses import replace
from pathlib import Path

from .. import rules
from ..tools import git
from . import verdict_actions

LENSES = ["correctness", "security", "performance", "architecture", "assertions"]
SIZES = ("trivial", "small", "feature")


def _stop(note: str, detail: str = ""):
    from .actions import ActionResult

    return ActionResult(False, note, detail, stop=True)


def _ok(note: str, detail: str = "", update: dict | None = None):
    from .actions import ActionResult

    return ActionResult(True, note, detail, update or {})


def _fail(note: str, detail: str = ""):
    from .actions import ActionResult

    return ActionResult(False, note, detail)


def _markers(a, values: dict) -> dict:
    mk = dict(a.state.get("markers") or {})
    mk[a.step or "*"] = {**(mk.get(a.step) or {}), **values}
    mk["*"] = {**(mk.get("*") or {}), **values}
    return mk


# ------------------------------------------------------------------ review

def review_scope(a):
    """keel v1 /keel:review [code|all|<lens>|ac <id>] [--base <ref>]: work out the scope before any agent starts."""
    if not git.is_repo(a.root):
        return _stop("Not a git repository: there is no diff to review.")
    cfg = rules.load_config(a.root)
    arg = " ".join(str(a.data.get("lens") or "code").split())
    low = arg.lower()
    if low.startswith("ac "):
        return _ac_scope(a, cfg, arg[3:].strip().upper())
    if low not in ["code", "all", "lint", *LENSES]:
        return _stop(f"Unknown review argument '{arg}'.",
                     f"Use code, all, lint, one lens ({', '.join(LENSES)}) or ac <AC-ID>.")
    base = str(a.data.get("base") or "").strip() or verdict_actions.base_ref(a.root, cfg)
    if not base or git.git(a.root, "rev-parse", "--verify", "-q", f"{base}^{{commit}}").returncode != 0:
        return _stop(f"The base ref {base or '(none)'} does not exist.", "Pass base: <branch or commit> to compare with.")
    files = [f for f in git.git(a.root, "diff", "--name-only", f"{base}...HEAD").stdout.splitlines() if f.strip()]
    if not files:
        return _stop(f"Nothing to review: git diff {base}...HEAD is empty.",
                     "A reviewer sent to review nothing finds something to say anyway.")
    scope = {"scope": f"git diff {base}...HEAD", "base": base, "files": files[:60]}
    lint = None
    if low == "lint" or a.data.get("lint"):
        # Static checks (lens lint, or data.lint with any lens): the stacks' check tools on the diff, reported only.
        from . import lint_actions
        lint = lint_actions.lint_review(a, base)
    if low == "lint":
        return _ok(f"Review scope: {scope['scope']} ({len(files)} file(s)); {lint[0]}.", "\n".join(files[:60]),
                   {"data": {**a.data, "review_lenses": [], "lint_report": lint[1]}})
    if low == "code":
        items = [{"id": "code", "title": "whole-branch code review", "agent": "code-reviewer", **scope}]
    else:
        lenses = _all_lenses(cfg) if low == "all" else [low]
        items = [{"id": lens, "title": f"{lens} lens", "agent": "reviewer", "lens": lens, **scope} for lens in lenses]
    data = {**a.data, "review_lenses": items, **({"lint_report": lint[1]} if lint else {})}
    return _ok(f"Review scope: {scope['scope']} ({len(files)} file(s)); " + ", ".join(i["title"] for i in items)
               + (f"; {lint[0]}" if lint else "") + ".",
               "\n".join(files[:60]), {"data": data})


def _all_lenses(cfg: dict) -> list[str]:
    """`all` = review.lenses (default correctness, security, performance) plus architecture when a style is set:
    the same set ship runs."""
    lenses = list((cfg.get("review") or {}).get("lenses") or ["correctness", "security", "performance"])
    style = (cfg.get("architecture") or {}).get("style") or (cfg.get("arch") or {}).get("style")
    if style and "architecture" not in lenses:
        lenses.append("architecture")
    return lenses


def _ac_scope(a, cfg: dict, ac_id: str):
    """One criterion: its RED (test(<id>)) and GREEN (feat(<id>)) commits; a single-commit project has only GREEN."""
    if not re.fullmatch(r"[A-Z]+-[\w.]+", ac_id):
        return _stop(f"'{ac_id}' is not an acceptance criterion id (for example AC-3).")

    def sha(prefix: str) -> str | None:
        out = git.git(a.root, "log", "--format=%h", "--fixed-strings", f"--grep={prefix}({ac_id})").stdout.split()
        return out[0] if out else None

    red, green = sha("test"), sha("feat")
    single = (cfg.get("loops") or {}).get("commit_style") == "single"
    missing = ([] if green else [f"feat({ac_id})"]) + ([] if single or red else [f"test({ac_id})"])
    if missing:
        return _stop(f"{ac_id}: no {' and no '.join(missing)} commit found.",
                     "Reviewing half a criterion reads as a clean result; finish the criterion first.")
    item = {"id": ac_id, "title": f"{ac_id} review", "agent": "ac-reviewer", "ac": ac_id, "green": green}
    if single:
        item["single_commit"] = True
    else:
        item["red"] = red
    return _ok(f"Review scope: {ac_id} ({'single commit ' + green if single else f'red {red}, green {green}'}).",
               update={"data": {**a.data, "review_lenses": [item]}})


# ------------------------------------------------------------------ report

def report(a):
    """Every fan-out answer of this flow word for word (one section each) and a tally of their markers. When the step
    just before was a single agent (a ranking, a fix plan), its answer comes first. Shown at the next gate."""
    data = a.data
    keys = [k for k, v in data.items() if k.endswith("_results") and isinstance(v, list)]
    out: list[str] = []
    prev = a.state.get("current")
    if prev and f"{prev}_results" not in data and (a.state.get("last_answer") or "").strip():
        out += [a.state["last_answer"].strip(), ""]
    if data.get("lint_report"):
        out += ["## Static checks", "", str(data["lint_report"]).strip(), ""]
    for key in keys:
        for r in data[key]:
            out += [f"## {r.get('title') or r.get('item')}", "", str(r.get("text") or "").strip() or "(no answer)", ""]
    tally = _tally([r for k in keys for r in data[k]])
    if tally:
        out += ["---", tally]
    text = "\n".join(out).strip() or "Nothing to report."
    return _ok(tally or "report ready", update={"show": text, "data": {**data, "report": text}})


def _tally(results: list[dict]) -> str:
    """`2 of 4 blocking` for reviews; `ROOT-CAUSE: 1 confirmed, 2 unconfirmed` for investigations."""
    if not results:
        return ""
    bad = sum(1 for r in results if _blocking(r.get("markers") or {}))
    lines = []
    if any(set(r.get("markers") or {}) & {"BLOCKING", "CODE-REVIEW", "AC-REVIEW"} for r in results):
        lines.append(f"{bad} of {len(results)} blocking")
    counts: dict[str, dict[str, int]] = {}
    for r in results:
        for name, value in (r.get("markers") or {}).items():
            counts.setdefault(name, {}).setdefault(str(value), 0)
            counts[name][str(value)] += 1
    for name, by in counts.items():
        lines.append(f"{name}: " + ", ".join(f"{n} {v}" for v, n in by.items()))
    missing = sum(1 for r in results if not r.get("markers"))
    if missing and counts:
        lines.append(f"{missing} answer(s) without a verdict line")
    return "\n".join(lines)


def _blocking(mk: dict) -> bool:
    return mk.get("BLOCKING") == "yes" or mk.get("CODE-REVIEW") == "findings" or mk.get("AC-REVIEW") == "findings"


# ------------------------------------------------------------------ diagnose

def investigation_note(a):
    """keel v1 diagnose "record unresolved": the note keeps every hypothesis, killed ones too, out of the source tree."""
    day = datetime.date.today().isoformat()
    slug = re.sub(r"[^a-z0-9]+", "-", (a.title or "investigation").lower()).strip("-")[:50] or "investigation"
    folder = Path(a.root) / "docs" / "investigations"
    path = folder / f"{day}-{slug}.md"
    n = 2
    while path.exists():
        path, n = folder / f"{day}-{slug}-{n}.md", n + 1
    why = ((a.state.get("markers") or {}).get("*") or {}).get("WHY") or ""
    symptom = (a.request or "").strip()
    body = [f"# Investigation: {a.title}", "", f"Date: {day}", "Status: unresolved — nothing confirmed a cause.", "",
            "## Symptom", "", symptom or a.title, "", "## Hypotheses and evidence", "",
            "Every hypothesis is kept, the killed ones too: they stop the next investigation retracing the same ground.", "",
            str(a.data.get("report") or "No findings were recorded.").strip(), ""]
    if why:
        body += ["## Why it was left unresolved", "", why, ""]
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body))
    rel = str(path.relative_to(a.root))
    from .actions import commit

    r = commit(replace(a, phase="close", title=f"investigation note: {a.title}"))
    if not r.ok:
        return r
    return _ok(f"Investigation note {rel}; {r.note}", update={**r.update, "data": {**a.data, "note_path": rel}})


# ------------------------------------------------------------------ fix

def bug_intake(a):
    """The bug report: keel v1 --no-gates (data.no_gates, or gates.bug_gates: false in .keel/config.yml) waives Gate R
    and Gate F, recorded in the gate log and the PR body. A hunt seed's needs_e2e makes the regression e2e mandatory."""
    cfg = rules.load_config(a.root)
    why = None
    if a.data.get("no_gates"):
        why = "no_gates option"
    elif (cfg.get("gates") or {}).get("bug_gates") is False:
        why = "gates.bug_gates: false"
    update: dict = {"markers": _markers(a, {"E2E": "yes" if a.data.get("needs_e2e") else "no"})}
    notes = ["bug report noted"]
    if why:
        gates = dict(a.state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
        gates["skipped"] = {**(gates.get("skipped") or {}), "gate-r": f"bug gates waived ({why})",
                            "gate-f": f"bug gates waived ({why})"}
        gates["log"] = list(gates.get("log") or []) + [f"bug gates waived: {why}"]
        update["gates"] = gates
        notes.append(f"Gate R and Gate F waived ({why})")
    if a.data.get("recipe"):
        notes.append("the reproducer gets the recipe")
    if a.data.get("needs_e2e"):
        notes.append("a regression e2e test is required")
    return _ok("; ".join(notes), update=update)


def reset(a):
    """keel v1 `keel state phase reset`: what keel's agents changed and did not commit goes back to HEAD, so the next
    try starts clean. The user's own uncommitted files (dirty when the flow started, untouched since) stay."""
    if not git.is_repo(a.root):
        return _ok("Not a git repository; nothing to put back.")
    mine = {f for f, fp in a.preexisting.items() if git.fingerprint(a.root, f) == fp}
    back = []
    for rel in sorted(set(git.dirty(a.root)) - mine):
        if git.tracked_in_head(a.root, rel):
            git.git(a.root, "checkout", "HEAD", "--", rel)
        else:
            (Path(a.root) / rel).unlink(missing_ok=True)
        back.append(rel)
    note = f"reset: put back {len(back)} uncommitted file(s)" if back else "reset: nothing uncommitted to put back"
    return _ok(note, "\n".join(back))


# ------------------------------------------------------------------ change

def change_size(a):
    """keel v1 change triage: trivial (no test could notice), small (1 to change.max_inline_acs criteria, CHG-<n>.<m>),
    or a feature. The triage agent's SIZE marker is its advice; too many criteria always recommend a feature."""
    cfg = rules.load_config(a.root)
    limit = int((cfg.get("change") or {}).get("max_inline_acs") or 3)
    acs = _change_ids(a.root, a.acs)
    said = str(((a.state.get("markers") or {}).get("*") or {}).get("SIZE") or "").lower().split(" ")[0]
    size = said if said in SIZES else ("trivial" if not acs else "small")
    triggers = [f"{len(acs)} criteria (limit {limit} for a small change)"] if len(acs) > limit else []
    if triggers:
        size = "feature"
    lines = [f"Proposed: {size}", ""]
    reason = (a.state.get("last_answer") or "").strip()
    if reason:
        lines += [reason[:1500], ""]
    if acs:
        lines += ["Criteria:"] + [f"- {x['id']} [{x.get('layer', 'API')}] {x.get('title', '')}" for x in acs] + [""]
    if triggers:
        lines += ["Escalation trigger: " + "; ".join(triggers), ""]
    lines += ["trivial: one refactor commit; existing tests stay untouched.",
              "small: the test-first loop per criterion, one gate at the end.",
              "feature: hand the criteria to a feature flow (spec, contract, the full ship).",
              f"Choosing small against a {size} recommendation needs a reason: it is recorded as an escalation override."
              if size == "feature" else ""]
    update = {"acs": acs, "show": "\n".join(lines).strip(),
              "data": {**a.data, "change": {"recommended": size, "triggers": triggers, "limit": limit}},
              "markers": _markers(a, {"SIZE": size})}
    return _ok(f"triage: {size}" + (f" ({len(acs)} criteria)" if acs else ""), update=update)


def _change_ids(root: str, acs: list[dict]) -> list[dict]:
    """Inline criteria are CHG-<n>.<m>: n is one more than the highest change number in the history."""
    if not acs or all(str(x["id"]).startswith("CHG-") for x in acs):
        return [dict(x) for x in acs]
    used = re.findall(r"CHG-(\d+)\.", git.git(root, "log", "--format=%s").stdout) if git.is_repo(root) else []
    n = max([int(u) for u in used] or [0]) + 1
    return [dict(x, id=f"CHG-{n}.{m}") for m, x in enumerate(acs, start=1)]


def change_start(a):
    """A small change: criteria are required, the gate comes once at the end (keel v1's default for a small change),
    and choosing small against a feature recommendation is logged as an escalation override (shown in the PR body)."""
    info = a.data.get("change") or {}
    if not a.acs:
        return _fail("A small change needs 1 to 3 acceptance criteria; triage wrote none.",
                     "List the criteria (- **AC-1** [API] <what must be true>), or choose trivial.")
    gates = dict(a.state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
    gates["mode"] = "end"
    gates["log"] = list(gates.get("log") or [])
    note = f"small change: {len(a.acs)} criteria, one gate at the end"
    if info.get("recommended") == "feature":
        why = ((a.state.get("markers") or {}).get("*") or {}).get("WHY") or "chose small at the scope gate"
        gates["log"].append(f"escalation-override: {why}")
        note += f"; stays small: {why}"
    return _ok(note, update={"gates": gates})


ACTIONS = {"review_scope": review_scope, "report": report, "investigation_note": investigation_note,
           "bug_intake": bug_intake, "reset": reset, "change_size": change_size, "change_start": change_start}
