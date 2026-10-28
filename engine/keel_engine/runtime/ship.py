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
import re

from .. import extensions, rules
from ..tools import git
from . import choices as choices_mod
from . import blockers, bug, knowledge, review_config, run_mode, verdict_actions, verdicts
from . import lenses as lens_files

# keel 0.18.65 consumers-config (content/review-lenses/consumers-config.md): what a consumer that runs the new next to
# the old meets, shared keys and routes, public types against their contract, the CI config
DEFAULT_LENSES = ["correctness", "security", "performance", "consumers-config"]
BANDS = {
    "deferred": "Deferred, not avoided: skipping moves the work; the push stays blocked until a fresh verdict for HEAD",
    "optional": "Optional: nothing downstream demands it; skipping is a real choice with a real cost",
}
VERDICT_ROWS = ["fast", "module", "lint", "release", "coverage", "deps", "audit", "trace", "arch", "memory"]
# Production code: the buckets keel's rules know, plus any other file that is not a test, a doc or keel's own.
PROD_BUCKETS = {"api-main", "web-src", "migration"}
_TESTISH = re.compile(r"(^|/)(tests?|__tests__|e2e|smoke|specs?)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$|Tests?\.\w+$")
_DOCISH = re.compile(r"\.(md|txt|rst|adoc)$|^docs/|^\.keel/|^\.claude/|^\.github/")


def is_production(cfg: dict, path: str) -> bool:
    bucket = rules.classify(cfg, path)
    if bucket in PROD_BUCKETS:
        return True
    return bucket == "other" and not _TESTISH.search(path) and not _DOCISH.search(path)


def production_changed(root: str, cfg: dict, sha: str) -> list[str] | None:
    """The production files changed since `sha` (committed or not); None when the sha is unknown here."""
    r = git.git(root, "diff", "--name-only", f"{sha}..HEAD")
    if r.returncode != 0:
        return None
    files = {f.strip() for f in r.stdout.splitlines() if f.strip()} | set(git.dirty(root))
    return sorted(f for f in files if is_production(cfg, f))


def _result(ok, note, detail="", update=None):
    return verdict_actions._result(ok, note, detail, update)


# ------------------------------------------------------------------ actions

async def review_lenses(a):
    return await asyncio.to_thread(_lenses, a)


def _lenses(a):
    """The lenses chosen at the opening gate (data.lenses_chosen), else review.lenses from .keel/config.yml, else
    correctness, security, performance and (0.18.65) consumers-config; architecture joins when the project sets an
    architecture style or rules. A lens with a lens file (the project's .keel/lenses/<name>.md, else keel's
    content/review-lenses/<name>.md) gives its reviewer that file's checklist in its item."""
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
    data = {k: v for k, v in a.data.items() if k != "lens_dropped"}
    dropped = "" if isinstance(chosen, list) and chosen else _drop_correctness(a, cfg, seen)
    if dropped:
        seen = [x for x in seen if x != "correctness"]
        data["lens_dropped"] = dropped
    scope = _lens_scope(a, cfg)
    items = [{"id": lens, "title": f"{lens} lens", "lens": lens, **_checklist(a.root, lens), **scope} for lens in seen]
    return _result(True, f"Review lenses ({why}): {', '.join(seen) or 'none'}." + (f" {dropped}." if dropped else ""),
                   update={"data": {**data, "review_lenses": items}})


def _checklist(root: str, lens: str) -> dict:
    """A ship lens's checklist from its lens file (runtime/lenses.py), or nothing (keel's lens-reviewer knows the lens)."""
    f = lens_files.load(lens, root)
    return {"lens_title": f["title"], "checklist": lens_files.flat(f["checklist"])[:3000]} if f and f["checklist"] else {}


def _lens_scope(a, cfg: dict) -> dict:
    """What each ship reviewer reads (as the review flow's items: review_scope): the branch diff against its base, the
    files it touches, and the spec. Without it a reviewer guesses the base."""
    if not git.is_repo(a.root):
        return {}
    base = verdict_actions.base_ref(a.root, cfg, a.base)
    if not base:
        return {}
    files = [f for f in git.git(a.root, "diff", "--name-only", f"{base}...HEAD").stdout.splitlines() if f.strip()]
    out = {"scope": f"git diff {base}...HEAD", "base": base, "files": files[:60]}
    if len(files) > 60:
        out["files_more"] = len(files) - 60
    if a.state.get("spec"):
        out["spec"] = a.state["spec"]
    return out


def _drop_correctness(a, cfg: dict, lenses: list[str]) -> str:
    """keel v1 ship step 5: the correctness lens is dropped when the full-diff review (data.full_review, written after
    the feature flow's code_review) passed and no production file changed since its sha. keel 0.18.65: only when the
    project asks for it (review.drop_correctness, Settings › Review; off by default): a field report showed a passed
    full-diff review that missed what a second correctness look would see. Returns why, or ""."""
    if not review_config.effective(a.root)["drop_correctness"]:
        return ""
    full = a.data.get("full_review") if isinstance(a.data.get("full_review"), dict) else {}
    sha = str(full.get("sha") or "")
    if "correctness" not in lenses or full.get("verdict") != "pass" or not sha or not git.is_repo(a.root):
        return ""
    changed = production_changed(a.root, cfg, sha)
    if changed is None or changed:
        return ""
    return f"correctness lens dropped: the full-diff review passed at {sha[:7]} and no production file changed since"


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


# ------------------------------------------------------------------ the knowledge update: asked, not assumed

async def memory_plan(a):
    return await asyncio.to_thread(_memory_plan, a)


MEMORY_CHOICES = [
    "- update: update the stale sections (recommended: a file they cite changed). A librarian re-reads the cited code "
    "and rewrites only the claims that cite the changed files: it costs tokens",
    "- skip: keep the knowledge base as it is this time; say why (the PR body records it). Neither the librarian nor "
    "the knowledge check runs",
]


def _memory_plan(a):
    """keel v1 ship step 8: the knowledge base is optional and the person's call. No knowledge base: nothing runs. Nothing
    stale (no file a section cites changed since the last knowledge check, else since the branch started): the
    librarian does not run, the check does. Stale: the gate asks update / skip (update recommended), with the sections
    and the files that made them stale. data.memory_plan {state, ask, run, check, text}; data.memory_choice for the PR."""
    if not knowledge.enabled(a.root):
        # 0.18.57 the project notes are off (knowledge.build: off, or a new project that never built them): no
        # librarian, no question, no check
        plan = {"state": "off", "ask": "no", "run": "no", "check": "no", "text": ""}
        choice = {"choice": "off", "reason": "project notes are off (knowledge.build: off)"}
        return _result(True, "Project notes are off: the knowledge step is skipped (the Wiki's Build them turns them on).",
                       update={"data": {**a.data, "memory_plan": plan, "memory_choice": choice}})
    sel = knowledge.selection(a.root)
    if sel is None:
        plan = {"state": "none", "ask": "no", "run": "no", "check": "no", "text": ""}
        choice = {"choice": "none", "reason": "the project has no knowledge base"}
        return _result(True, "No knowledge base: nothing to update (the knowledge refresh flow builds one).",
                       update={"data": {**a.data, "memory_plan": plan, "memory_choice": choice}})
    v = verdicts.latest(a.key, "memory")
    cfg = rules.load_config(a.root)
    since = (v or {}).get("commit") if v and v.get("ok") else None
    since = since or (verdict_actions.base_ref(a.root, cfg, a.base) if git.is_repo(a.root) else None)
    found = knowledge.stale(a.root, since)
    missing = [n for n in sel if not knowledge.section_file(a.root, n).is_file()]
    if not found and not missing:
        plan = {"state": "current", "ask": "no", "run": "no", "check": "yes", "text": ""}
        return _result(True, "Knowledge base current: no file a section cites changed; the librarian does not run.",
                       update={"data": {**a.data, "memory_plan": plan, "memory_choice": {"choice": "current"}}})
    lines = [f"- {s['name']}: cites {', '.join(f'`{f}`' for f in s['files'][:8])}" + (" …" if len(s["files"]) > 8 else "")
             for s in found] + [f"- {n}: chosen at init, not written yet" for n in missing]
    names = [s["name"] for s in found] + missing
    text = "\n".join(lines)
    show = "\n".join([f"The knowledge base is stale: {len(names)} section(s) cite files this branch changed"
                      + (f" since the last knowledge check ({str(since)[:7]})" if since else "") + ".", "", *lines, "",
                      choices_mod.how_to(note=False, plain="updates"), *MEMORY_CHOICES])
    plan = {"state": "stale", "ask": "yes", "run": "yes", "check": "yes", "text": text, "sections": names}
    return _result(True, f"Knowledge base stale: {', '.join(names)}.",
                   update={"show": show, "data": {**a.data, "memory_plan": plan, "memory_choice": {"choice": "update", "sections": names}}})


async def knowledge_scan(a):
    return await asyncio.to_thread(_knowledge_scan, a)


def _knowledge_scan(a):
    """knowledge-refresh: which sections are stale (a file they cite changed since the last knowledge check, else since
    the base branch) and which chosen ones are not written yet. data.knowledge_stale {sections, text}: the librarians
    are told the changed files, and a refresh started without a list refreshes only these sections.

    0.18.57 the refresh is a request for the notes: it turns them on (knowledge.build: on in .keel/config.yml, which
    the memory commit takes along), so agents read what the librarians write."""
    turned_on = knowledge.set_build(a.root, True)
    v = verdicts.latest(a.key, "memory")
    since = (v or {}).get("commit") if v and v.get("ok") else None
    if not since and git.is_repo(a.root):
        since = verdict_actions.base_ref(a.root, rules.load_config(a.root), a.base)
    found = knowledge.stale(a.root, since)
    chosen = knowledge.selection(a.root) or []
    missing = [n for n in chosen if not knowledge.section_file(a.root, n).is_file()]
    lines = [f"- {s['name']}: these cited files changed: {', '.join(f'`{f}`' for f in s['files'][:12])}" for s in found] + \
        [f"- {n}: not written yet (write it from the code)" for n in missing]
    names = [s["name"] for s in found] + missing
    note = (f"stale: {', '.join(names)}" if names else "no section is stale (no file a section cites changed)")
    if turned_on:
        note += "; project notes turned on (knowledge.build: on)"
    return _result(True, note, update={"data": {**a.data, "knowledge_stale": {"sections": names, "text": "\n".join(lines)
                                                                              or "(no section is stale: refresh only what you were given)"}}})


async def memory_skip(a):
    return await asyncio.to_thread(_memory_skip, a)


def _memory_skip(a):
    """The knowledge gate's skip: the reason goes to data.memory_choice (the PR body prints it). The knowledge check does
    not run either (0.18.57): on a stale knowledge base it fails, and its librarian fix rounds are the update the person
    just skipped (a real run spent $0.64 and 3.5 minutes there after "skip")."""
    ans = next((v for k, v in a.data.items() if k.endswith("memory_gate_answer") and isinstance(v, dict)), {})
    why = str(ans.get("why") or "").strip() or "no reason given"
    plan = {**(a.data.get("memory_plan") or {}), "run": "no", "check": "no"}
    return _result(True, f"Knowledge update skipped: {why}",
                   update={"data": {**a.data, "memory_plan": plan, "memory_choice": {"choice": "skip", "reason": why}}})


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
              "{\"skip\": {\"<name>\": \"<reason>\"}} (or answer the skip question: payload.answers.skip and "
              "payload.reasons {\"<name>\": \"<reason>\"}); to pick the review lenses, add {\"lenses\": [\"correctness\", ...]}."]
    if has_loop(steps, after):
        lines += ["", "The criteria's gates for this flow only: add {\"gate_mode\": \"every-ac\" | \"end-of-lane\" | \"end\"} "
                  "(default: the project's gate mode)."]
    return "\n".join(lines)


GATE_MODES = {"every-ac": "a gate after every criterion", "end-of-lane": "one gate after the last criterion of each lane",
              "end": "one gate after the last criterion"}


def has_loop(steps: list, after: int) -> bool:
    """Does the menu at `after` decide about a per-criterion loop (feature's options menu)? Then it can set the AC gates."""
    return any(s.per_ac for s in covered(steps, after))


def gate_mode_question(chosen: str | None = None) -> dict:
    """keel v1's start picker: the AC gates for this flow only (the project's gates_mode stays the default). `chosen`
    (B10: an answer before a skip menu asked again): that mode is marked, and kept when this one is not answered."""
    why = "Only this flow; the project's gate mode setting stays as it is. Unanswered: " + \
        (f"{chosen}, as you answered before." if chosen in GATE_MODES else "the project's setting.")
    return {"id": "gate_mode", "question": "When should keel stop for the criteria's gates in this flow?", "why": why,
            "options": [{"label": k, "description": v, "recommended": k == chosen} for k, v in GATE_MODES.items()],
            "multiple": False}


def gate_mode_of(payload: dict) -> str | None:
    """payload.gate_mode, or the form's answers.gate_mode, when it names a gate mode."""
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    mode = payload.get("gate_mode") or answers.get("gate_mode")
    mode = mode[0] if isinstance(mode, list) and mode else mode
    return str(mode) if str(mode or "") in GATE_MODES else None


def skip_question(units: list[dict]) -> dict:
    """The skip menu as a question a form can show (0.19.0): tick the steps to skip, each with its band; nothing is
    ticked by default (every step runs). The answer comes back as payload.answers.skip with payload.reasons."""
    # B9 each ticked step takes its own reason (a form shows a field per ticked option: payload.reasons), and the
    # question has no "own words": words typed there were read as one more step's name
    return {"id": "skip", "question": "Which steps should keel skip this time?",
            "why": "Every skip needs a reason: it is shown again at the final review and printed in the PR body.",
            "options": [{"label": u["name"], "description": BANDS[u["band"]], "recommended": False, "why": "required"}
                        for u in units],
            "multiple": True, "own_words": False}


def skip_payload(payload: dict, names: list[str] | set[str] | None = None) -> dict:
    """payload.answers.skip (the steps ticked) with payload.reasons {step: why} as today's payload.skip map; a
    payload.skip already there wins for the steps it names. With the skippable `names` (B9): a ticked "<step>: <reason>"
    is that step with its reason, and words that name no step (a client's own-words box) are the reason of the ticked
    steps that have none, instead of a step keel does not know."""
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    ticked = answers.get("skip")
    shared = str(payload.get("reason") or payload.get("why") or "").strip()
    raw = payload.get("skip")
    if isinstance(raw, (str, list)) and raw not in ("", []):
        # 0.18.57 the simple forms: skip: "doc_updates" or skip: ["doc_updates", "lint"] with one shared reason
        # (payload.reason, or the answer's why); before, a list of names was dropped and every step ran
        names_in = [raw] if isinstance(raw, str) else raw
        listed = {str(x).strip(): shared or None for x in names_in if isinstance(x, str) and str(x).strip()}
        listed.update({str(x.get("step") or x.get("name") or ""): x.get("reason") or shared or None
                       for x in names_in if isinstance(x, dict)})
        payload = {**payload, "skip": listed}
    elif isinstance(raw, dict) and shared:
        payload = {**payload, "skip": {k: v if str(v or "").strip() else shared for k, v in raw.items()}}
    if ticked in (None, "", []):
        return payload
    ticked = [ticked] if isinstance(ticked, str) else [str(x) for x in ticked if str(x).strip()]
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), dict) else {}
    reasons = {str(k): v for k, v in reasons.items() if str(v or "").strip()}
    if shared:
        reasons = {**{str(t).partition(":")[0].strip(): shared for t in ticked}, **reasons}
    have = payload.get("skip")
    have = {str(x.get("step") or x.get("name") or ""): x.get("reason") for x in have if isinstance(x, dict)} \
        if isinstance(have, list) else dict(have) if isinstance(have, dict) else {}
    if names is None:
        skip = {name: reasons.get(name) for name in ticked}
        return {**payload, "skip": {**skip, **have}}
    known = set(names)
    skip: dict = {}
    words: list[str] = []
    for x in ticked:
        name, _, said = x.partition(":")
        if x in known:
            skip[x] = reasons.get(x)
        elif name.strip() in known and said.strip():
            skip[name.strip()] = reasons.get(name.strip()) or said.strip()
        else:
            words.append(x.strip())
    why = "; ".join(w for w in words if w)
    if why and skip and any(not str(v or "").strip() for v in skip.values()):
        skip = {k: v if str(v or "").strip() else why for k, v in skip.items()}
    elif words:
        skip.update({w: reasons.get(w) for w in words})       # still a name keel does not know: parse_skips says so
    return {**payload, "skip": {**skip, **have}}


def base_warning(root: str) -> str | None:
    """The base drift line (verdict_actions.base_drift) after a fetch, or None; never raises."""
    try:
        cfg = rules.load_config(root)
        verdict_actions.fetch_base(root, cfg)
        return verdict_actions.base_drift(root, cfg)
    except Exception:            # noqa: BLE001 - a warning never stops the ship
        return None


def names_skip(payload: dict) -> set[str] | None:
    """The step names an answer asks to skip (an empty set: it says skip nothing), or None when it says nothing about
    skipping at all (no answers.skip, no skip)."""
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    if "skip" not in answers and "skip" not in payload:
        return None
    out: set[str] = set()
    for raw in (answers.get("skip"), payload.get("skip")):
        if isinstance(raw, str) and raw.strip():
            out.add(raw.partition(":")[0].strip())
        elif isinstance(raw, dict):
            out.update(str(k) for k in raw)
        elif isinstance(raw, list):
            out.update(str(x.get("step") or x.get("name") or "") if isinstance(x, dict) else str(x).partition(":")[0].strip()
                       for x in raw)
    return {x for x in out if x}


def parse_skips(payload: dict, units: list[dict]) -> tuple[list[dict], list[str] | None, str | None]:
    """(skips [{step, band, reason}], lenses or None, problem or None) from the opening gate's answer."""
    raw = payload.get("skip") or {}
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        shared = str(payload.get("reason") or payload.get("why") or "").strip() or None
        raw = {str(x.get("step") or x.get("name") or "") if isinstance(x, dict) else str(x).strip():
               (x.get("reason") or shared) if isinstance(x, dict) else shared for x in raw}
    if not isinstance(raw, dict):
        return [], None, "skip must map a step name to a reason"
    by = {u["name"]: u for u in units}
    out = []
    for name, reason in raw.items():
        if name not in by:
            return [], None, f"{name} cannot be skipped here (skippable: {', '.join(by) or 'none'})"
        if not str(reason or "").strip():
            # the whole answer is refused (nothing is skipped, nothing runs yet): the gate asks again with this
            return [], None, (f"skipping {name} needs a reason: nothing was skipped. Answer again with a reason per "
                              f"step (skip: {{\"{name}\": \"<reason>\"}}) or one for all (skip: [\"{name}\"], why: "
                              "\"<reason>\"); skip: [] runs every step")
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


def final_report(root: str, key: str, state: dict, title: str, thread_id: str = "") -> str:
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
    exc += verdict_actions.dismissed_lines(data)
    for line in gates.get("log") or []:
        if "no gate here" in line or "accepted:" in line or " go on after " in line:
            exc.append(f"- gate: {line}")
        elif run_mode.is_auto_line(line):
            exc.append(f"- gate auto-approved: {line}")
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
    out += bug.section(state)           # a bug flow: symptom, root cause, reproducing test, hunt findings it closes
    out += _spec_part(root, state)
    out += [f"## Verdicts for HEAD {str(head or 'not a git repository')[:7]}", "", "| check | result | summary |", "|---|---|---|"]
    for kind in VERDICT_ROWS:
        res, summary = _mark(verdicts.latest(key, kind), head, tree)
        out.append(f"| {kind} | {res} | {summary} |")
    sec, sec_lines = verdict_actions.security_summary(state)
    out.append(f"| security | {sec} | {'; '.join(x.strip(' -') for x in sec_lines if not x.startswith('  '))[:200] or '—'} |")
    out.append("")
    out += _reviews_part(root, cfg, state)
    rounds = state.get("review_rounds") or {}
    if rounds:
        out += ["## Review fix rounds", ""] + [f"- {k}: {v}" for k, v in rounds.items()] + [""]
    rows = data.get("trace") or (verdict_actions.trace_rows(root, state.get("acs") or [], base) if repo and state.get("acs") else [])
    if rows:
        out += ["## Acceptance criteria", "", verdict_actions.trace_table(rows), ""]
    out += extensions.pr_body_sections(thread_id)      # the parts' sections: KeelBot's commits at a gate
    if state.get("spec"):
        out += [f"Spec: `{state['spec']}`", ""]
    if repo and base:
        stat = git.git(root, "diff", "--stat", f"{base}...HEAD").stdout.strip()
        if stat:
            out += ["## Diff", "", "```", verdict_actions._tail(stat, 2500), "```", ""]
        files = files_to_open(root, cfg, base)
        if files:
            out += ["Files a reviewer should open (the largest production changes):", ""] + [f"- `{f}` (+{a} -{d})" for f, a, d in files] + [""]
    out += ["Approve: final approve (the knowledge base is updated next, then the PR body). "
            "Reject with what to change: the implementer makes the change and ship runs again from verify."]
    return "\n".join(out)


def _spec_part(root: str, state: dict) -> list[str]:
    """keel v1 ship step 7, "the spec": its goal, the criteria, and every amendment with its Was: line (a criterion
    agreed late reads like one agreed at the start unless it is said)."""
    from . import feature_actions

    spec = state.get("spec")
    text = feature_actions._read(root, spec) if spec else ""
    acs = state.get("acs") or []
    if not text and not acs:
        return []
    out = ["## The spec", ""]
    goal = feature_actions._section(text, "goal", "context", "request", "summary", "problem").strip() if text else ""
    if goal:
        out += ["Goal:", goal[:800], ""]
    if acs:
        done = sum(1 for x in acs if x.get("status") in feature_actions.DONE)
        out += [f"Criteria ({done}/{len(acs)} done):", *[f"- {x['id']} [{x.get('layer', 'API')}] {x.get('title', '')} ({x.get('status') or 'todo'})"
                                                         for x in acs], ""]
    blocks = feature_actions._amendment_blocks(text) if text else []
    if blocks:
        out += [f"Amendments ({len(blocks)}), each with what it was:", ""]
        for b in blocks:
            out += [b[:1500], ""]
    return out


def _reviews_part(root: str, cfg: dict, state: dict) -> list[str]:
    """Security, the full-diff review (verdict, sha, whether the correctness lens was dropped because of it), the spec
    walk (behaviour no criterion asks for), and the findings nobody fixed: non-blocking ones and dismissed ones."""
    data = state.get("data") or {}
    out: list[str] = []
    res, lines = verdict_actions.security_summary(state)
    out += [f"## Security: {res}", "", *lines, ""]
    full = data.get("full_review") if isinstance(data.get("full_review"), dict) else {}
    if full:
        out += ["## Full-diff review", "", f"- verdict: {full.get('verdict')} at {str(full.get('sha') or '?')[:7]}"]
        out.append(f"- {data['lens_dropped']}" if data.get("lens_dropped") else "- the correctness lens ran at ship (not dropped)")
        out.append("")
    walk = next((str(v) for k, v in data.items() if k.endswith("spec_walk_text") and str(v or "").strip()), "")
    if walk:
        out += ["## Spec walk: behaviour no criterion asks for", "", verdict_actions._tail(walk.strip(), 2500), ""]
    docs = next((str(v) for k, v in data.items() if k.endswith("doc_updates_text") and str(v or "").strip()), "")
    if docs:
        out += ["## Docs this change made wrong (edited before verify)", "", verdict_actions._tail(docs.strip(), 1500), ""]
    nb = verdict_actions.nonblocking_lines(data)
    if nb:
        out += [f"## Non-blocking findings, not fixed ({len(nb)})", "", *nb, ""]
    return out


def files_to_open(root: str, cfg: dict, base: str, n: int = 8) -> list[tuple[str, int, int]]:
    """The production files with the most changed lines since base: what a reviewer should actually open."""
    rows = []
    for line in git.git(root, "diff", "--numstat", f"{base}...HEAD").stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and is_production(cfg, parts[2]):
            rows.append((parts[2], int(parts[0]), int(parts[1])))
    return sorted(rows, key=lambda r: -(r[1] + r[2]))[:n]
