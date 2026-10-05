"""Code actions of the feature flow (content/workflows/feature.yaml; keel v1 skills/feature and its references).

    preflight          the tree and the test command are ready; the flow gets its own branch (never the base branch)
    explore_areas      once the criteria are settled: one explorer item per area (api, web, data)
    spec_sync          the spec's criteria (added, edited, removed; the plan's ORDER applied) and the spec gate's text
    spec_freeze        approved: frontmatter status frozen, approved and frozen today; the spec is committed alone
    spec_restart       the spec gate's reject: the interview starts again (criteria handed over by another flow stay)
    amend_start        why the frozen spec needs an amendment: an agent's AMEND line, or the contract gate's note
    spec_amendment     the amendment gate's text: the dated block, why, and the criteria it touches (finished ones too)
    spec_amend_commit  approved: new criteria join, the ones it names (REOPEN) are open again, the spec is committed alone
    show_diff          what the next gate shows: keel's uncommitted change (the contract), else the branch diff
    security_scope     the security fan-out: the auditor always, the dependency triager when verify deps found something
    e2e_scope          the spec's [E2E] criteria (or a hunt's needs_e2e) and whether commands.e2e is set
    e2e_unrun          no e2e tool: the specs are written but not run; recorded for the final review and the PR body
    verify_e2e         runs commands.e2e (a failure runs once more: flaky), unless the specs are not to be run
    smoke_scope        the spec's [SMOKE] criteria or its Smoke checks section
    verify_smoke       runs smoke/*.sh and commands.smoke_e2e
    close_flow         the closing line: criteria done, ADRs written

The step ids these actions read are the feature workflow's: the plan's ORDER marker (any step), the dependency check's
output (data.security_deps_output) and the spec review (step spec_review_sync shows the reviewer's answer).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import replace
from pathlib import Path

from .. import rules
from ..tools import git, testcmd
from ..tools.agent_tools import command_env
from . import prompts, spec_check, verdict_actions
from .flow_actions import _fail, _markers, _ok

DONE = ("done", "already-met")
AC_ID = re.compile(r"[A-Z][A-Z0-9]*-[\w.]+")
DEPS_STEP = "security_deps"          # the soft verify_deps step whose output the triager reads
LAYER_LINE = r"\b([A-Z][A-Z0-9]*-[\w.]+)\**\s*\[{layer}\]\s*(.+)"


def _today() -> str:
    return datetime.date.today().isoformat()


def _slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:n].strip("-") or "feature"


def _read(root: str, rel: str | None) -> str:
    return spec_check.read_spec(root, rel)


def _gates(a) -> dict:
    g = dict(a.state.get("gates") or {"mode": "every-ac", "log": [], "skipped": {}})
    g["log"] = list(g.get("log") or [])
    g["skipped"] = dict(g.get("skipped") or {})
    return g


def spec_path(a) -> str | None:
    """The flow's spec: the one the state knows, else a changed .md with "spec" in its path, else the specs folder's
    file named after the title."""
    root = Path(a.root)
    known = a.state.get("spec")
    if known and (root / known).is_file():
        return known
    if git.is_repo(a.root):
        found = [f for f in git.dirty(a.root) if f.endswith(".md") and "spec" in f.lower() and (root / f).is_file()]
        if found:
            return max(found, key=lambda f: (root / f).stat().st_mtime)
    cfg = rules.load_config(a.root)
    for d in [((cfg.get("specs") or {}).get("dir") if isinstance(cfg.get("specs"), dict) else None), "docs/specs", "specs"]:
        if d and (root / d).is_dir():
            mine = [p for p in (root / d).glob("*.md") if _slug(a.title, 30) in p.stem]
            if mine:
                return str(max(mine, key=lambda p: p.stat().st_mtime).relative_to(root))
    return None


def merge_acs(have: list[dict], parsed: list[dict], drop: bool) -> list[dict]:
    """The criteria after the spec changed: the spec's list in its order (title and layer from the spec, status kept),
    then the ones the spec does not list. With `drop`, an unfinished AC-<n> the spec no longer lists is gone (the user
    removed it); criteria with other ids (CHG-1.2, handed over by a change flow) and finished ones always stay."""
    if not parsed:
        return [dict(x) for x in have]
    by = {x["id"]: x for x in have}
    out = [{**by[p["id"]], "layer": p["layer"], "title": p["title"]} if p["id"] in by else dict(p, status="todo")
           for p in parsed]
    listed = {p["id"] for p in parsed}
    for x in have:
        if x["id"] in listed:
            continue
        if drop and x.get("status") not in DONE and re.fullmatch(r"AC-\d+", x["id"]):
            continue
        out.append(dict(x))
    return out


def order_acs(acs: list[dict], order: str | None) -> list[dict]:
    """The plan's build order (ORDER: AC-2, AC-1, ...): the ids it names first, in that order, then the rest."""
    ids = [i.upper() for i in AC_ID.findall(str(order or "").upper())]
    first = [next(x for x in acs if x["id"].upper() == i) for i in dict.fromkeys(ids) if any(x["id"].upper() == i for x in acs)]
    return first + [x for x in acs if x not in first]


def _section(text: str, *names: str) -> str:
    sec = spec_check.sections(text)
    return next((v for k, v in sec.items() if any(k.startswith(n) for n in names)), "")


def _criteria_lines(acs: list[dict]) -> list[str]:
    return [f"- {x['id']} [{x.get('layer', 'API')}] {x.get('title', '')}" + (f" ({x['status']})" if x.get("status") in DONE else "")
            for x in acs]


# ------------------------------------------------------------------ preflight

def preflight(a):
    """keel v1 `keel preflight`: the tree is a git repository with a test command, and the flow works on its own
    branch. On the base branch (base_branch, main or master) it creates feat/<slug> (settings.branch_pattern; the api
    usually made it already); on any other branch it stays, so the commits of a flow that handed over stay too.
    data.no_gates waives the optional gates (integration, e2e, smoke); the spec and criteria gates are never waived."""
    notes, update = [], {}
    gates = _gates(a)
    if a.data.get("no_gates"):
        for phase in ("integration", "e2e", "smoke"):
            gates["skipped"][phase] = "gates waived (no_gates option)"
        gates["log"].append("optional gates waived: no_gates option")
        update["gates"] = gates
        notes.append("integration, e2e and smoke gates waived")
    if not git.is_repo(a.root):
        return _ok("; ".join(["Not a git repository: no branch of its own", *notes]), update=update)
    if not a.fake and not testcmd.command_for(a.root, None, "API"):
        return _fail("Not ready: no test command found.",
                     "Add commands (api_test_ac, api_test_module) to .keel/config.yml, or run the init flow first.")
    cfg = rules.load_config(a.root)
    cur = git.branch(a.root) or ""
    bases = {b for b in (cfg.get("base_branch"), "main", "master") if b}
    if cur in bases and a.settings.get("own_branch", True) is not False:
        first = str(a.settings.get("branch_pattern") or "feat/{slug}").replace("{slug}", _slug(a.title)).replace("{flow}", "feature")
        name, n = first, 2
        while git.git(a.root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}").returncode == 0:
            name, n = f"{first}-{n}", n + 1
        r = git.git(a.root, "checkout", "-q", "-b", name)
        if r.returncode != 0:
            return _fail(f"Could not create the branch {name}.", (r.stderr or r.stdout)[-1500:])
        update["branch"] = name
        notes.append(f"own branch {name} (from {cur})")
    else:
        notes.append(f"on branch {cur}" + (" (handed over: its commits stay)" if a.state.get("parent") else ""))
    if a.preexisting:
        notes.append(f"{len(a.preexisting)} file(s) of yours were already changed; they stay out of keel's commits")
    if a.acs:
        done = sum(1 for x in a.acs if x.get("status") in DONE)
        notes.append(f"{len(a.acs)} criteria handed over ({done} done): no interview")
    return _ok("ready: " + "; ".join(notes), update=update)


# ------------------------------------------------------------------ the spec and the plan

def explore_areas(a):
    """One explorer per area once the criteria are settled (keel v1: criteria first, explorers second): api for [API]
    criteria (or when there is no [WEB] one), web for [WEB] ones, data always. Each item names its criteria."""
    open_ = [x for x in a.acs if x.get("status") not in DONE] or list(a.acs)
    lines = {layer: [f"{x['id']} [{x.get('layer', 'API')}] {x.get('title', '')}" for x in open_ if x.get("layer", "API") == layer]
             for layer in ("API", "WEB")}
    every = lines["API"] + lines["WEB"]
    items = []
    if lines["API"] or not lines["WEB"]:
        items.append({"id": "api", "title": "api: the server side", "area": "api", "criteria": lines["API"] or every})
    if lines["WEB"]:
        items.append({"id": "web", "title": "web: the frontend", "area": "web", "criteria": lines["WEB"]})
    items.append({"id": "data", "title": "data: tables, migrations and data shape", "area": "data", "criteria": every})
    return _ok("explorers for " + ", ".join(i["id"] for i in items), update={"data": {**a.data, "explore_areas": items}})


def spec_sync(a):
    """The criteria as the spec now lists them, in the plan's order, and what the spec gate shows."""
    spec = spec_path(a)
    text = _read(a.root, spec)
    seeded = bool((a.data.get("seed") or {}).get("acs"))
    acs = merge_acs(a.acs, prompts.parse_acs(text), drop=not seeded)
    order = ((a.state.get("markers") or {}).get("*") or {}).get("ORDER")
    acs = order_acs(acs, order)
    lines = [f"Spec: {spec or '(no spec file found)'}", "", "Criteria, in build order:", *_criteria_lines(acs)]
    plan = _section(text, "plan")
    if plan:
        lines += ["", "Plan (files per criterion, contract delta, test layer):", plan[:2500]]
    lines += ["", spec_check.describe(spec_check.check(text, acs))]
    answer = a.data.get("spec_gate_answer") or {}
    if a.step.startswith("spec_review") and answer.get("choice") == "review" and (a.state.get("last_answer") or "").strip():
        lines += ["", "Spec review (you asked for it), word for word:", a.state["last_answer"].strip()[:6000]]
    lines += ["", "Choose one (approve with payload {\"choice\": \"<name>\"} and a note):",
              "- approve: freeze the spec (status frozen, approved and frozen today), commit it, go to the contract",
              "- edit: change the criteria your note names, then come back here",
              "- rewrite: rewrite the section your note names (goal, validation, auth, edge cases, out of scope)",
              "- review: an adversarial read first (vague, untestable or overlapping criteria), shown here",
              "- order: the criteria are right but the plan under them is not: the plan is written again",
              "- reject: the spec misses the intent: the interview starts again with what was missing"]
    upd = {"acs": acs, "show": "\n".join(lines)}
    if spec:
        upd["spec"] = spec
    return _ok(f"spec {spec or 'not found'}: {len(acs)} criteria" + (f", order {order}" if order else ""), update=upd)


def _frontmatter(text: str, values: dict) -> str:
    """Set these keys in the spec's front matter (added when the spec has none)."""
    m = re.match(r"\A---\n(.*?)\n---\n?", text, re.S)
    lines = m.group(1).splitlines() if m else []
    for k, v in values.items():
        row = f"{k}: {v}"
        at = next((n for n, line in enumerate(lines) if re.match(rf"^{re.escape(k)}\s*:", line)), None)
        if at is None:
            lines.append(row)
        else:
            lines[at] = row
    body = text[m.end():] if m else text
    return "---\n" + "\n".join(lines) + "\n---\n" + body.lstrip("\n")


def spec_freeze(a):
    """keel v1: on approval, status frozen and approved/frozen dated today, then `keel commit docs SPEC "spec and plan"`.
    Only the spec is committed. From here specs/ is refused in red, green and gate: a change is a dated amendment."""
    from .actions import commit

    spec = spec_path(a)
    if not spec:
        return _fail("There is no spec file to freeze.", "Write the spec under docs/specs/ first.")
    f = Path(a.root) / spec
    day = _today()
    f.write_text(_frontmatter(f.read_text(errors="replace"), {"status": "frozen", "approved": day, "frozen": day}))
    r = commit(replace(a, phase="spec", title=f"spec and plan: {a.title}", paths=[spec]))
    if not r.ok:
        return r
    gates = _gates(a)
    gates["log"].append(f"spec frozen: {spec} ({len(a.acs)} criteria)")
    return _ok(f"spec frozen ({day}); {r.note}", update={**r.update, "spec": spec, "gates": gates,
                                                          "data": {**a.data, "spec_frozen": day}})


def spec_restart(a):
    """The spec gate's reject (keel v1: not a patch, a restart of the interview). Criteria handed over by another
    flow stay; the rest go, so the explorer may ask its questions again."""
    seeded = bool((a.data.get("seed") or {}).get("acs"))
    acs = list(a.acs) if seeded else [x for x in a.acs if x.get("status") in DONE]
    return _ok("the interview starts again" + (" (the handed-over criteria stay)" if seeded else ""),
               update={"acs": acs, "clarify": {}, "clarify_rounds": 0, "spec_revisions": 0})


# ------------------------------------------------------------------ amendments

def amend_start(a):
    """Why the frozen spec needs an amendment: the AMEND line of the agent that hit it (red, green), else the note of
    the contract gate ("this breaks a consumer"). The line is used once: it is cleared from the markers."""
    mk = {k: dict(v) for k, v in (a.state.get("markers") or {}).items() if isinstance(v, dict)}
    said = [(k, v["AMEND"]) for k, v in mk.items() if k != "*" and v.get("AMEND")]
    ac = a.state.get("ac")
    if said:
        frm, text = said[-1]
        reason = f"{ac + ' · ' if ac else ''}{frm}: {text}"
    else:
        frm = "contract"
        why = ((a.data.get("contract_gate_answer") or {}).get("why") or "").strip()
        reason = f"contract gate: {why or 'the contract shows that the spec assumed something untrue'}"
    for v in mk.values():
        v.pop("AMEND", None)
    amend = {"from": frm, "ac": ac, "reason": reason[:2000], "at": _today()}
    return _ok(f"spec amendment needed: {reason}"[:300], update={"markers": mk, "data": {**a.data, "amend": amend}})


def _last_amendment(text: str) -> str:
    m = re.search(r"(?mi)^##\s+amendments\s*$", text or "")
    if not m:
        return ""
    rest = text[m.end():]
    end = re.search(r"(?m)^##\s", rest)
    sec = rest[:end.start()] if end else rest
    blocks = re.split(r"(?m)^(?=###\s)", sec)
    blocks = [b.strip() for b in blocks if b.strip().startswith("###")]
    return blocks[-1] if blocks else ""


def _reopen(a) -> list[str]:
    said = str((a.state.get("markers") or {}).get("*", {}).get("REOPEN") or "")
    return [] if re.fullmatch(r"\s*(none|no|-)?\s*\.?", said, re.I) else [i.upper() for i in AC_ID.findall(said.upper())]


def spec_amendment(a):
    """The amendment gate's text (keel v1: write the dated block, then stop before committing it)."""
    spec = spec_path(a)
    block = _last_amendment(_read(a.root, spec))
    if not block:
        return _fail("The amendment is not in the spec.",
                     f"Add it to {spec or 'the spec'} under ## Amendments as a dated block: ### <date> — <criteria>, then "
                     "Was:, Now:, Why:. Never edit the frozen criteria in place.")
    amend = a.data.get("amend") or {}
    reopen = _reopen(a)
    done = [x for x in a.acs if x.get("status") in DONE or x.get("status") == "green"]
    lines = ["The frozen spec needs an amendment.", f"Why: {amend.get('reason') or 'not recorded'}", "",
             f"The block in {spec}:", block[:4000], ""]
    if done:
        lines.append("Criteria already finished under the old wording: " + ", ".join(x["id"] for x in done))
        lines.append("Reopened by this amendment: " + (", ".join(reopen) if reopen else
                     "none (the block should say why they may ship under the old wording)"))
    contract = str(((a.state.get("markers") or {}).get("*") or {}).get("CONTRACT") or "").lower().startswith("yes")
    if contract or amend.get("from") == "contract":
        lines.append("The API shape moves: the contract is done again before the loop goes on.")
    lines += ["", "Choose one (approve with payload {\"choice\": \"<name>\"} and a note):",
              "- approve: commit the amendment, then back to the criteria (the contract first when the API shape moved)",
              "- context: tell keel what it was missing; the block is written again and comes back here",
              "- change: the block is close but wrong; say how",
              "- rebuild: the spec itself is wrong; a new feature flow starts on this branch with what was learned "
              "(the commits stay)"]
    return _ok("amendment ready for the gate", update={"show": "\n".join(lines)})


def spec_amend_commit(a):
    """The approved amendment, committed alone (docs: amend: ...): criteria the block adds join the list, the ones the
    amendment reopens (REOPEN) are open again. Marker CONTRACT: yes when the contract must change first."""
    from .actions import commit

    spec = spec_path(a)
    text = _read(a.root, spec)
    acs = merge_acs(a.acs, prompts.parse_acs(text), drop=False)
    reopen = [i for i in _reopen(a) if any(x["id"].upper() == i for x in acs)]
    acs = [dict(x, status="todo") if x["id"].upper() in reopen else x for x in acs]
    amend = a.data.get("amend") or {}
    reason = str(amend.get("reason") or "spec amended")
    r = commit(replace(a, phase="spec", title=f"amend: {reason.split(': ', 1)[-1]}", paths=[spec] if spec else []))
    if not r.ok:
        return r
    gates = _gates(a)
    gates["log"].append(f"spec amended: {reason}"[:300] + (f" · reopened {', '.join(reopen)}" if reopen else ""))
    said = str(((a.state.get("markers") or {}).get("*") or {}).get("CONTRACT") or "").lower()
    contract = amend.get("from") == "contract" or said.startswith("yes")
    data = {**a.data, "amendments": list(a.data.get("amendments") or []) + [{**amend, "reopen": reopen, "commit": r.update.get("git_head")}]}
    new = [x["id"] for x in acs if x["id"] not in {y["id"] for y in a.acs}]
    note = f"amendment committed: {r.note}" + (f"; new criteria {', '.join(new)}" if new else "") + \
           (f"; reopened {', '.join(reopen)}" if reopen else "") + ("; the contract is next" if contract else "")
    return _ok(note, update={**r.update, "acs": acs, "gates": gates, "data": data,
                             "markers": _markers(a, {"CONTRACT": "yes" if contract else "no"})})


# ------------------------------------------------------------------ gates' views

def show_diff(a):
    """What the next gate shows: keel's uncommitted change (the contract delta: new, changed, removed), else the
    branch diff since the flow started (the integration gate)."""
    if not git.is_repo(a.root):
        return _ok("Not a git repository: no diff to show.", update={"show": "Not a git repository: no diff to show."})
    mine = {f for f, fp in a.preexisting.items() if git.fingerprint(a.root, f) == fp}
    dirty = {f: xy for f, xy in git.dirty(a.root).items() if f not in mine}
    if dirty:
        tracked = [f for f, xy in dirty.items() if "?" not in xy]
        out = [f"Uncommitted change ({len(dirty)} file(s)), not committed until you approve:", ""]
        if tracked:
            out.append(git.git(a.root, "diff", "HEAD", "--", *tracked).stdout)
        for f in sorted(x for x, xy in dirty.items() if "?" in xy):
            p = Path(a.root) / f
            body = p.read_text(errors="replace") if p.is_file() else ""
            out += [f"new file {f}:", "\n".join("+" + line for line in body.splitlines()[:200]), ""]
        text = "\n".join(out)
    else:
        base = a.base or "HEAD"
        stat = git.git(a.root, "diff", "--stat", f"{base}..HEAD").stdout.strip()
        text = f"Branch diff since the flow started ({base[:7]}..HEAD):\n{stat or 'no change'}\n\n" + \
               git.git(a.root, "diff", f"{base}..HEAD").stdout
    text = text.strip()
    if len(text) > 12000:
        text = text[:12000] + "\n… (cut; see the repository for the rest)"
    return _ok(f"diff ready ({len(dirty)} uncommitted file(s))" if dirty else "branch diff ready", update={"show": text})


# ------------------------------------------------------------------ security

def security_scope(a):
    """keel v1 phase 5.5: two pipelines in parallel. The security auditor reads the branch diff against the spec;
    the dependency triager runs only when verify deps reported something (its output is the triager's input)."""
    cfg = rules.load_config(a.root)
    base = verdict_actions.base_ref(a.root, cfg, a.base) if git.is_repo(a.root) else None
    items = [{"id": "audit", "title": "security audit of the branch diff", "agent": "security-auditor",
              "scope": f"git diff {base}...HEAD" if base else "the branch diff", "spec": spec_path(a) or "(no spec file)"}]
    res = ((a.state.get("markers") or {}).get(DEPS_STEP) or {}).get("RESULT")
    out = str(a.data.get(f"{DEPS_STEP}_output") or "")
    if res == "fail" and out:
        items.append({"id": "deps", "title": "dependency triage", "agent": "dependency-triager", "advisories": out[:6000]})
    return _ok("security: " + " and ".join(i["title"] for i in items), update={"data": {**a.data, "security_items": items}})


# ------------------------------------------------------------------ e2e and smoke

def _tagged(text: str, layer: str) -> list[str]:
    text = re.sub(r"```.*?(```|\Z)", "", text or "", flags=re.S)
    return [f"{m.group(1)} [{layer}] {m.group(2).strip().strip('*').strip()}"
            for m in re.finditer(LAYER_LINE.format(layer=layer), text)]


def e2e_scope(a):
    """keel v1 phase 7: the [E2E] criteria (a hunt's needs_e2e adds the journey that broke). Before delegating, is
    commands.e2e set? Without it the gate asks (keel v1 e2e-tool-missing): either way the specs are written."""
    cfg = rules.load_config(a.root)
    crit = _tagged(_read(a.root, spec_path(a)), "E2E")
    if not crit and a.data.get("needs_e2e"):
        crit = [f"the journey that broke: {a.title}"]
    cmd = str((cfg.get("commands") or {}).get("e2e") or "").strip()
    note = (f"Run them with: {cmd}" if cmd else
            "No e2e command is configured: write the specs, do not run them, and say so in your answer.")
    data = {**a.data, "e2e": {"criteria": crit, "command": cmd or None, "run": bool(cmd), "note": note},
            "e2e_tool_detail": "No e2e command is configured (commands.e2e in .keel/config.yml), so the E2E specs cannot "
                               "run here.\n\nCriteria:\n" + "\n".join(f"- {c}" for c in crit) +
                               "\n\nChoose one (approve with payload {\"choice\": \"<name>\"}):\n"
                               "- write: the specs are written and committed but not run; the final review and the PR "
                               "body say so (an unrun test must not read as a passing one)\n"
                               "- check: you set commands.e2e now; keel looks again"}
    mk = _markers(a, {"E2E": "yes" if crit else "no", "TOOL": "yes" if cmd else "no"})
    return _ok(f"e2e: {len(crit)} criteria" + ("" if crit else ", nothing to write") + (f"; runs `{cmd}`" if cmd else "; no e2e command"),
               update={"data": data, "markers": mk})


def e2e_unrun(a):
    e2e = {**(a.data.get("e2e") or {}), "run": False,
           "note": "No e2e command is configured: write the specs, do not run them, and say so in your answer."}
    skipped = [x for x in a.data.get("ship_skipped") or [] if x.get("step") != "e2e-run"] + [
        {"step": "e2e-run", "band": "optional", "reason": "no e2e command (commands.e2e): the @e2e specs are written, not run"}]
    gates = _gates(a)
    gates["log"].append("e2e: specs written without running them (no e2e command)")
    return _ok("e2e specs will be written, not run", update={"data": {**a.data, "e2e": e2e, "ship_skipped": skipped}, "gates": gates})


def verify_e2e(a):
    """keel v1 `keel verify e2e`: the E2E command, a failure runs once more (fail then pass = flaky, a warning)."""
    e2e = a.data.get("e2e") or {}
    said = str(((a.state.get("markers") or {}).get("*") or {}).get("E2E-RESULT") or "")
    if not e2e.get("run") or not e2e.get("command"):
        return _ok("E2E specs written; not run (no e2e command).")
    if a.fake:
        return _ok("E2E: passed (simulated).")
    r = verdict_actions.run_suite(a.root, e2e["command"], "e2e", 1)
    if not r["ok"]:
        return _fail("The E2E specs fail.", f"$ {e2e['command']}\n{verdict_actions._tail(r['out'])}" +
                     (f"\nThe e2e author said E2E-RESULT: {said}." if said else ""))
    return _ok("E2E: passed." + verdict_actions._flaky_note(r["flaky"]), update=verdict_actions.flaky_update(a, r["flaky"]))


def smoke_scope(a):
    """keel v1 phase 6: the [SMOKE] criteria or a filled Smoke checks section; nothing there, no smoke phase."""
    text = _read(a.root, spec_path(a))
    checks = _tagged(text, "SMOKE")
    section = _section(text, "smoke")
    if not checks and section and not re.fullmatch(r"(?is)\s*(none|n/?a|-|tbd)?\.?\s*", section):
        checks = [line.strip("-* ").strip() for line in section.splitlines() if line.strip()][:10]
    return _ok(f"smoke: {len(checks)} check(s)", update={"data": {**a.data, "smoke": {"checks": checks}},
                                                          "markers": _markers(a, {"SMOKE": "yes" if checks else "no"})})


def verify_smoke(a):
    """keel v1 `keel smoke`: every smoke/*.sh (BASE_URL and API_URL from e2e.web_url / e2e.api_url), then
    commands.smoke_e2e; the first failure stops."""
    if a.fake:
        return _ok("Smoke: passed (simulated).")
    cfg = rules.load_config(a.root)
    env = command_env()
    urls = cfg.get("e2e") if isinstance(cfg.get("e2e"), dict) else {}
    env.update({k: str(v) for k, v in (("BASE_URL", urls.get("web_url")), ("API_URL", urls.get("api_url"))) if v})
    cmds = [f"bash {p.relative_to(a.root)}" for p in sorted((Path(a.root) / "smoke").glob("*.sh"))]
    extra = str((cfg.get("commands") or {}).get("smoke_e2e") or "").strip()
    if extra:
        cmds.append(extra)
    if not cmds:
        return _fail("No smoke check to run.", "Write smoke/<name>.sh (and one @smoke test for commands.smoke_e2e).")
    for cmd in cmds:
        code, out = testcmd.run(a.root, cmd, 300, env)
        if code != 0:
            return _fail(f"Smoke check failed: {cmd}", f"$ {cmd}\n{verdict_actions._tail(out)}")
    return _ok(f"Smoke: {len(cmds)} check(s) passed." + ("" if extra else " (commands.smoke_e2e is not set: no @smoke test ran)"))


# ------------------------------------------------------------------ close

def close_flow(a):
    """keel v1 phase 9: what shipped in one line (criteria done, the ADRs this flow added)."""
    done = sum(1 for x in a.acs if x.get("status") in DONE)
    adrs = []
    if git.is_repo(a.root) and a.base:
        adrs = [f for f in git.git(a.root, "diff", "--name-only", f"{a.base}..HEAD", "--", "docs/adr").stdout.splitlines() if f.strip()]
    note = f"{done}/{len(a.acs)} criteria done; " + (f"ADR: {', '.join(adrs)}" if adrs else "no ADR (no decision had a real alternative)")
    if done < len(a.acs):
        note += "; some criteria are not done: look at the board and the gate log"
    return _ok(note)


ACTIONS = {"preflight": preflight, "explore_areas": explore_areas, "spec_sync": spec_sync, "spec_freeze": spec_freeze,
           "spec_restart": spec_restart, "amend_start": amend_start, "spec_amendment": spec_amendment,
           "spec_amend_commit": spec_amend_commit, "show_diff": show_diff, "security_scope": security_scope,
           "e2e_scope": e2e_scope, "e2e_unrun": e2e_unrun, "verify_e2e": verify_e2e, "smoke_scope": smoke_scope,
           "verify_smoke": verify_smoke, "close_flow": close_flow}

