"""The code steps of the hunt and hunt-next workflows (content/workflows/hunt.yaml, hunt-next.yaml).

    hunt_start     opens a run in the backlog: mode auto|semi, scope, fast, the proposed lenses (settings or seed)
    hunt_confirm   the lens set the user confirmed at the gate -> one sweep item per lens and lane
    hunt_deps      the security lens runs the dependency audit first (always, not only when a manifest changed)
    hunt_ingest    each hunter's JSON list -> candidates (lane enforced, severity dropped, duplicates merged);
                   writes candidates.md (UNVERIFIED) and the prover items (symptom only)
    hunt_verdicts  each prover's verdict -> the backlog; a refused verdict sends that candidate back to the provers
    hunt_group     the investigator's groups (findings that share one cause)
    hunt_report    report.md + recipes; refuses while any candidate has no verdict
    hunt_commit    commits docs/hunts/<run>/ as docs(HUNT-<run>)
    hunt_close     closes a finding or group (fixed | accepted | wontfix + a note) from a gate's answer
    hunt_take      hunt-next: the top open group -> fix (defect) or feature (unspecified), with the recipe

Steps branch on the marker LEFT (verdicts: none | some) and FLOW (take: fix | feature | none), set as the latest
value of that marker ("*").
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .. import rules
from ..rules import checks
from ..tools import git, testcmd
from ..tools.agent_tools import command_env
from . import hunt
from . import verdict_actions as va
from .actions import ActionInput, ActionResult

MODES = ("auto", "semi")


def _data(a: ActionInput, **changes) -> dict:
    return {**a.data, **changes}


def _marker(a: ActionInput, name: str, value: str, step: str) -> dict:
    mk = dict(a.state.get("markers") or {})
    mk[step] = {**(mk.get(step) or {}), name: value}
    mk["*"] = {**(mk.get("*") or {}), name: value}
    return mk


def _hunt(a: ActionInput) -> dict:
    return dict(a.data.get("hunt") or {})


def _options(a: ActionInput) -> dict:
    """The hunt's start options: the thread's settings, the flow options (the API's `options`, kept in data), else the
    seed a parent flow passed (init's audit-now)."""
    seed = a.data.get("seed") if isinstance(a.data.get("seed"), dict) else {}
    out = {}
    for k in ("mode", "scope", "lenses", "fast", "run"):
        for src in (a.settings, a.data, seed):
            v = src.get(f"hunt_{k}", src.get(k))
            if v not in (None, "", []):
                break
        out[k] = v if v not in (None, "", []) else None
    return out


def _lens_list(v) -> list[str]:
    if isinstance(v, str):
        v = v.split(",")
    return [str(x).strip() for x in v or [] if str(x).strip()]


def _health(a: ActionInput, cfg: dict, key: str) -> str:
    cmd = (cfg.get("commands") or {}).get(key)
    if not cmd:
        return "unknown"
    if a.fake:
        return "unknown (simulated)"
    code, _ = testcmd.run(a.root, cmd, 15, command_env())
    return "ok" if code == 0 else "down"


def _lens_detail(cfg: dict, run: dict, proposed: list[str]) -> str:
    pairs = hunt.sweep_pairs(cfg, proposed)
    lines = [f"Hunt {run['run']} ({run['mode']}{', fast' if run['fast'] else ''}), scope {run['scope']['mode']}"
             + (f": {', '.join(run['scope']['paths'])}" if run["scope"].get("paths") else "") + ".", "",
             "A hunt changes no code. Hunters propose candidates, a prover tries to reproduce each one against the running "
             "stack, and only what reproduces gets a severity. The report and a backlog are what it leaves.", "",
             "Proposed lenses (one hunter per lens and lane):"]
    lines += [f"- {lens} [{', '.join(hunt.lanes_for(cfg, lens))}]: {hunt.LENS_HELP.get(lens, '')}" for lens in proposed]
    off = [x for x in cfg["lenses"] if x not in proposed]
    if off:
        lines.append(f"Not looked through: {', '.join(off)}.")
    lines += ["", f"{len(pairs)} hunter(s). Confirm to sweep with these. To change the set, answer with the lenses you want "
              "(payload.lenses), or name them in your note; \"drop x, y\" removes lenses."]
    if run["mode"] == "semi":
        lines.append("Semi mode: the flow also stops after the sweep (candidates page) and after the provers.")
    return "\n".join(lines)


# ------------------------------------------------------------------ start + lenses

async def hunt_start(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_start, a)


def _start(a: ActionInput) -> ActionResult:
    if _hunt(a).get("run") and hunt.get_run(a.key, _hunt(a)["run"]):
        return ActionResult(True, f"hunt {_hunt(a)['run']} is open")
    cfg = hunt.settings(a.root)
    kcfg = rules.load_config(a.root)
    opt = _options(a)
    mode = str(opt.get("mode") or "semi").lower()
    if mode not in MODES:
        return ActionResult(False, f"A hunt runs in auto or semi mode, not {mode}.", "settings.mode: auto | semi")
    fast = bool(opt.get("fast"))
    configured = list(cfg["lenses"])
    proposed = [x for x in cfg["fast_lenses"] if x in configured] if fast else configured
    if opt.get("lenses"):
        proposed = _lens_list(opt["lenses"])
        unknown = [x for x in proposed if not hunt.brief(a.root, x)]
        if unknown:
            return ActionResult(False, f"No brief for lens(es): {', '.join(unknown)}.",
                                f"Known lenses: {', '.join(configured)}. A project's own lens needs .keel/lenses/<name>.md.")
    scope_raw = str(opt.get("scope") or ("diff" if fast else cfg["default_scope"])).strip()
    scope = {"mode": scope_raw if scope_raw in ("all", "diff") else "paths",
             "paths": [] if scope_raw in ("all", "diff") else [p.strip() for p in scope_raw.split(",") if p.strip()],
             "base": va.base_ref(a.root, kcfg, a.base) if git.is_repo(a.root) else None}
    stack = {"api": _health(a, kcfg, "api_health_check"), "web": _health(a, kcfg, "web_health_check")}
    run = hunt.new_run(a.key, a.root, thread_id=a.thread_id, mode=mode, fast=fast, scope=scope, configured=configured,
                       proposed=proposed, stack=stack)
    info = {"run": run["run"], "mode": mode, "fast": fast, "scope": scope["mode"],
            "prove_concurrency": max(1, int(cfg["prove_concurrency"] or 4)), "report_dir": cfg["report_dir"]}
    data = _data(a, hunt=info, proposed_lenses=proposed, confirm_lenses_detail=_lens_detail(cfg, run, proposed))
    return ActionResult(True, f"hunt {run['run']} started ({mode}{', fast' if fast else ''}), scope {scope['mode']}: "
                              f"{len(proposed)} lens(es) proposed", update={"data": data})


def _scope_text(a: ActionInput, run: dict) -> str:
    scope = run["scope"] or {}
    if scope.get("mode") == "paths":
        return "only these paths: " + ", ".join(scope.get("paths") or [])
    if scope.get("mode") == "diff":
        files = va.branch_files(a.root, scope.get("base")) if git.is_repo(a.root) else []
        if files:
            return f"the files changed since {scope.get('base') or 'the start'}: " + ", ".join(files[:60]) + \
                   (f" (+{len(files) - 60} more)" if len(files) > 60 else "")
        return "the branch diff (it is empty: look at the most recently changed code instead)"
    return "the whole project"


async def hunt_confirm(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_confirm, a)


def _confirm(a: ActionInput) -> ActionResult:
    info = _hunt(a)
    run = hunt.get_run(a.key, info.get("run"))
    if not run:
        return ActionResult(False, "No hunt run is open for this flow.")
    cfg = hunt.settings(a.root)
    proposed = list(a.data.get("proposed_lenses") or (run["lenses"] or {}).get("proposed") or [])
    answer = a.data.get("confirm_lenses_answer") or {}
    payload, why = answer.get("payload") or {}, str(answer.get("why") or "").lower()
    chosen = _lens_list(payload.get("lenses")) if payload.get("lenses") else None
    if chosen is None and why:
        named = [x for x in {*cfg["lenses"], *cfg["lens_lanes"]} if x in why]
        if named and any(w in why for w in ("drop", "without", "skip", "remove", "no ")):
            chosen = [x for x in proposed if x not in named]
        elif named:
            chosen = sorted(named, key=lambda x: (proposed + cfg["lenses"] + sorted(cfg["lens_lanes"])).index(x))
    confirmed = chosen if chosen is not None else proposed
    dropped = [x for x in confirmed if not hunt.brief(a.root, x)]
    confirmed = [x for x in confirmed if x not in dropped] or proposed
    lenses = {**(run["lenses"] or {}), "confirmed": confirmed, "confirmed_by": "user", "confirmed_at": hunt.db.now()}
    hunt.update_run(a.key, run["run"], lenses=lenses)
    cap = int(cfg["max_candidates_per_lens"])
    cap = max(1, -(-cap // 2)) if run["fast"] else cap
    scope = _scope_text(a, run)
    items = []
    for pair in hunt.sweep_pairs(cfg, confirmed):
        lens, lane = pair.split(":", 1)
        items.append({"id": pair, "lens": lens, "lane": lane, "run": run["run"], "scope": scope, "cap": cap,
                      "brief": hunt.brief(a.root, lens) or "",
                      "rules": (f"Cite only files in the {lane} lane; a batch citing the other lane is thrown away whole. "
                                if lane != "both" else "This lens reads both sides on purpose. ")
                               + f"At most {cap} candidates. Never a severity. Write nothing into the project; scripts go "
                               "in your scratch folder. End with one ```json list and the line FINDINGS: <n>."})
    note = f"lenses confirmed: {', '.join(confirmed)}; {len(items)} hunter(s)"
    if dropped:
        note += f"; no brief for {', '.join(dropped)}, left out"
    return ActionResult(True, note, update={"data": _data(a, sweep_items=items, confirm_lenses_answer=None)})


async def hunt_deps(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_deps, a)


def _deps(a: ActionInput) -> ActionResult:
    """The security lens starts from the dependency audit's output (the dependency-triager needs scanner output, not
    prose). Run every time: a whole-project hunt rarely has a changed manifest."""
    items = [dict(x) for x in a.data.get("sweep_items") or []]
    if not any(x.get("lens") == "security" for x in items):
        return ActionResult(True, "no security lens: the dependency audit is not needed")
    cfg = rules.load_config(a.root)
    cmds = cfg.get("commands") or {}
    audits = [(lane, str(cmds.get(f"deps_{lane}") or "").strip(), va._dir_of(cfg, d)) for lane, d in (("api", "backend"), ("web", "frontend"))]
    audits = [x for x in audits if x[1]]
    if not audits:
        scan = {"api": "not available: no audit command (commands.deps_api / deps_web in .keel/config.yml)"}
        va.record_verdict(a.key, "deps", None, {"reason": scan["api"], "by": "hunt"}, root=a.root if git.is_repo(a.root) else None)
        note = "dependency audit not available: no commands.deps_api / deps_web"
    elif a.fake:
        scan = {lane: "simulated: no audit ran" for lane, _c, _d in audits}
        note = "dependency audit simulated"
    else:
        scan, ran, failed = {}, [], []
        for lane, cmd, d in audits:
            cwd, _ = va._cwd(a.root, d)
            code, out = va._run(a.root, cmd, cwd, 600)
            ran.append({"lane": lane, "command": cmd, "code": code})
            scan[lane] = f"$ {cmd} (exit {code})\n{va._tail(out, 2500)}"
            if code != 0:
                failed.append(lane)
        va.record_verdict(a.key, "deps", not failed, {"audits": ran, "by": "hunt", "summary": "audit passed" if not failed
                                                      else f"{failed[0]} audit failed"}, root=a.root)
        note = f"dependency audit: {len(ran)} ran" + (f", {', '.join(failed)} reported problems" if failed else ", clean")
    for x in items:
        if x.get("lens") == "security":
            x["dependency_scan"] = scan.get(x.get("lane")) or "; ".join(f"{k}: {v}" for k, v in scan.items())
    return ActionResult(True, note, update={"data": _data(a, sweep_items=items)})


# ------------------------------------------------------------------ sweep -> candidates

async def hunt_ingest(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_ingest, a)


def _ingest(a: ActionInput) -> ActionResult:
    info = _hunt(a)
    run = hunt.get_run(a.key, info.get("run"))
    if not run:
        return ActionResult(False, "No hunt run is open for this flow.")
    by_pair: dict[str, list[dict]] = {}
    for c in a.data.get("candidates") or []:
        if c.get("from_item"):
            by_pair.setdefault(c["from_item"], []).append({k: v for k, v in c.items() if k not in ("id", "from_item")})
    said = {r["item"]: (r.get("markers") or {}).get("FINDINGS") for r in a.data.get("sweep_results") or []}
    lines, added, merged, refused, dropped = [], 0, 0, 0, 0
    for item in a.data.get("sweep_items") or []:
        pair = item["id"]
        got = by_pair.get(pair, [])
        n_said = str(said.get(pair) or "").split()[0] if said.get(pair) else ""
        if not got and n_said.isdigit() and int(n_said) > 0:
            swept = dict(hunt.get_run(a.key, run["run"])["swept"] or {})
            swept[pair] = {"at": hunt.db.now(), "count": 0, "refused": f"said FINDINGS: {n_said} but gave no JSON list"}
            hunt.update_run(a.key, run["run"], swept=swept)
            lines.append(f"{pair}: refused, said {n_said} finding(s) but gave no JSON list")
            refused += 1
            continue
        r = hunt.ingest(a.key, a.root, run["run"], pair, got)
        if r["refused"]:
            refused += 1
            lines.append(f"{pair}: refused whole: {r['refused']}")
            continue
        added += len(r["added"])
        merged += len(r["merged"])
        dropped += r["dropped_severity"]
        lines.append(f"{pair}: {len(r['added'])} new" + (f", merged into {', '.join(r['merged'])}" if r["merged"] else ""))
    page = hunt.write_candidates_page(a.key, a.root, run["run"])
    cfg = hunt.settings(a.root)
    waiting = [c for c in hunt.candidates(a.key, run["run"]) if c["status"] == "candidate"]
    items = [hunt.prove_item(c, run["run"], cfg["report_dir"]) for c in waiting]
    note = f"{added} candidate(s), {merged} merged as corroboration" + (f", {refused} batch(es) refused" if refused else "")
    if dropped:
        note += f"; dropped a hunter's severity on {dropped}: only a prover's verdict sets one"
    detail = "\n".join([f"Hunt {run['run']}: the sweep proposed {len(waiting)} candidate(s). Nothing is verified yet.",
                        f"Read {page} to decide whether to spend the provers on them.", "", *lines, "",
                        "prove: one prover per candidate, with the symptom only. stop: end here; the backlog keeps them."])
    return ActionResult(True, note + f"; {page}", "\n".join(lines),
                        {"data": _data(a, prove_items=items, prove_round=0, sweep_gate_detail=detail)})


# ------------------------------------------------------------------ prove -> verdicts

async def hunt_verdicts(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_verdicts, a)


def _verdict_of(a: ActionInput, cid: str) -> dict | None:
    got = [v for v in a.data.get("verdicts") or [] if v.get("from_item") == cid or str(v.get("id") or "").upper() == cid]
    if got:
        return got[-1]
    # No JSON: an unproven or false verdict may still be read from the PROOF marker, with the answer as the evidence.
    for r in a.data.get("prove_results") or []:
        if r.get("item") == cid:
            word = ((r.get("markers") or {}).get("PROOF") or "").split()[0:1]
            if word and word[0].lower() in ("unproven", "false", "proven"):
                return {"verdict": word[0].lower(), "evidence": (r.get("text") or "").strip()[:3000]}
    return None


def _verdicts(a: ActionInput) -> ActionResult:
    info = _hunt(a)
    run = hunt.get_run(a.key, info.get("run"))
    if not run:
        return ActionResult(False, "No hunt run is open for this flow.")
    cfg = hunt.settings(a.root)
    rounds = int(a.data.get("prove_round") or 0) + 1
    force = rounds >= int(cfg.get("prove_rounds") or 3)
    refusals = []
    for item in a.data.get("prove_items") or []:
        v = _verdict_of(a, item["id"])
        why = hunt.record_verdict(a.key, a.root, run["run"], item["id"], v, force=force) if v else \
            f"{item['id']}: no verdict came back (end with a ```json list holding one verdict, and PROOF: <verdict>)"
        if why and force and not v:
            hunt.record_verdict(a.key, a.root, run["run"], item["id"], {
                "verdict": "unproven", "evidence": f"No verdict after {rounds} rounds of proving; recorded as unproven."})
            why = None
        if why:
            refusals.append(why)
    cands = hunt.candidates(a.key, run["run"])
    left = [c for c in cands if c["status"] == "candidate"]
    c = hunt.counts(cands)
    if left:
        items = [hunt.prove_item(x, run["run"], cfg["report_dir"]) for x in left]
        feedback = ("Your verdict was refused; prove again and fix this:\n" + "\n".join(f"- {r}" for r in refusals))[:3000]
        return ActionResult(True, f"round {rounds}: {len(left)} candidate(s) still have no verdict; back to the provers",
                            "\n".join(refusals), {"data": _data(a, prove_items=items, prove_round=rounds, verdicts=[]),
                                                  "markers": _marker(a, "LEFT", "some", "verdicts"), "feedback": feedback})
    proven = [x for x in cands if x["status"] == "proven"]
    group_items = []
    if len(proven) >= 2:
        group_items = [{"id": "proven", "run": run["run"],
                        "instructions": ("These findings are proven. Find the ones that share ONE cause (the same defect "
                                         "showing up as different symptoms) by reading the code they cite. Answer with one "
                                         "```json list of groups: [{\"members\": [\"F-001\", \"F-004\"], \"lead\": \"F-001\", "
                                         "\"cause\": \"one sentence\"}]. A finding with a cause of its own needs no group; "
                                         "an empty list [] is a real answer. Change no file."),
                        "findings": [{"id": x["id"], "lens": x["lens"], "severity": x["severity"], "where": x["where"][:3],
                                      "symptom": x["symptom"][:240], "claim": (x.get("claim") or "")[:200]} for x in proven]}]
    detail = (f"Hunt {run['run']}: every candidate has a verdict: {c['proven']} proven, {c['unproven']} unproven, "
              f"{c['false']} false.\n\nreport: group shared causes and render the report. stop: end here.")
    return ActionResult(True, f"verdicts: {c['proven']} proven, {c['unproven']} unproven, {c['false']} false",
                        "\n".join(refusals), {"data": _data(a, prove_items=[], prove_round=rounds, verdicts=[],
                                                            group_items=group_items, verdicts_gate_detail=detail),
                                              "markers": _marker(a, "LEFT", "none", "verdicts")})


# ------------------------------------------------------------------ group + report + commit

async def hunt_group(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_group, a)


def _group(a: ActionInput) -> ActionResult:
    info = _hunt(a)
    run = hunt.get_run(a.key, info.get("run"))
    if not run:
        return ActionResult(False, "No hunt run is open for this flow.")
    made, skipped = [], []
    for g in a.data.get("groups") or []:
        members = g.get("members") or g.get("ids") or g.get("findings") or []
        gid, why = hunt.add_group(a.key, run["run"], members if isinstance(members, list) else [], str(g.get("cause") or ""),
                                  g.get("lead"))
        (made if gid else skipped).append(gid or f"{', '.join(map(str, members))}: {why}")
    note = f"{len(made)} group(s)" + (f": {', '.join(made)}" if made else "") + (f"; {len(skipped)} left out" if skipped else "")
    return ActionResult(True, note, "\n".join(skipped), {"data": _data(a, groups=[])})


def _triage_detail(a: ActionInput, run_id: str, extra: str = "") -> str:
    cands = hunt.candidates(a.key, run_id)
    open_ = sorted(hunt.open_findings(cands), key=lambda c: (-hunt.rank(c.get("severity")), c["id"]))
    run = hunt.get_run(a.key, run_id)
    lines = ([extra, ""] if extra else []) + [f"Hunt {run_id}: report {run.get('report') or 'not rendered'}.",
                                              f"{len(open_)} proven finding(s) open."]
    grps = {g["id"]: g for g in hunt.groups(a.key, run_id)}
    seen = set()
    for c in open_:
        key = c.get("group") or c["id"]
        if key in seen:
            continue
        seen.add(key)
        what = grps[key]["cause"] if key in grps else c["title"]
        lines.append(f"- {key} · {c.get('severity')} · {c['kind']} · {what}")
    lines += ["", "take: start hunt-next for the top group (a fix flow for a defect, a feature flow for unspecified).",
              "close: payload {id: F-001 or G-01, as: fixed | accepted | wontfix} and a note saying why.",
              "stop: end here; the backlog stays for hunt-next."]
    return "\n".join(lines)


async def hunt_report(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_report, a)


def _report(a: ActionInput) -> ActionResult:
    info = _hunt(a)
    run = hunt.get_run(a.key, info.get("run"))
    if not run:
        return ActionResult(False, "No hunt run is open for this flow.")
    rel, note = hunt.write_report(a.key, a.root, run["run"])
    if not rel:
        return ActionResult(False, "The report refuses to render.", note)
    return ActionResult(True, note, update={"data": _data(a, triage_detail=_triage_detail(a, run["run"]))})


async def hunt_commit(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_commit, a)


def _commit(a: ActionInput) -> ActionResult:
    """docs(HUNT-<run>): only the run's report folder is staged; whatever else is in the tree stays untouched."""
    run = hunt.get_run(a.key, _hunt(a).get("run"))
    if not run or not run.get("report"):
        return ActionResult(False, "No report to commit yet.")
    if not git.is_repo(a.root):
        return ActionResult(True, "Not a git repository; the report is not committed.")
    folder = str(Path(run["report"]).parent)
    git.git(a.root, "add", "--", folder)
    staged = [f for f in git.git(a.root, "diff", "--cached", "--name-only", "--", folder).stdout.splitlines() if f.strip()]
    if not staged:
        return ActionResult(True, "The report is already committed.")
    found = checks.secrets_in_diff(git.git(a.root, "diff", "--cached", "-U0", "--", folder).stdout)
    if found:
        git.git(a.root, "reset", "-q", "--", folder)
        return ActionResult(False, "The hunt report holds what looks like a secret; not committed.",
                            "\n".join(f"  {f['file']}: {f['why']}" for f in found))
    c = hunt.counts(hunt.candidates(a.key, run["run"]))
    cfg = rules.load_config(a.root)
    author = cfg.get("commit", {})
    msg = f"docs(HUNT-{run['run']}): bug hunt: {c['proven']} proven, {c['unproven']} suspected"
    r = git.git(a.root, "-c", f"user.name={author.get('author_name', 'keelbot')}",
                "-c", f"user.email={author.get('author_email', 'keel.dev.bot@gmail.com')}", "commit", "-q", "-m", msg, "--", folder)
    if r.returncode != 0:
        git.git(a.root, "reset", "-q", "--", folder)
        return ActionResult(False, "git commit failed.", (r.stderr or r.stdout)[-2000:])
    sha = git.head(a.root)
    return ActionResult(True, f"{msg} {(sha or '')[:7]}".strip(), "\n".join(staged), {"git_head": sha})


# ------------------------------------------------------------------ close + take

async def hunt_close(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_close, a)


def _close(a: ActionInput) -> ActionResult:
    """From the answer of the gate before it: hunt-next's close gate (choice = the disposition) or the hunt's triage
    (choice close, payload {id, as}). The note is the answer's text (or payload.note) and is required."""
    for key in ("close_gate_answer", "triage_answer"):
        ans = a.data.get(key) or {}
        choice = ans.get("choice")
        if choice in hunt.DISPOSITIONS or choice == "close":
            break
    else:
        return ActionResult(True, "nothing to close")
    payload = ans.get("payload") or {}
    as_ = choice if choice in hunt.DISPOSITIONS else payload.get("as")
    nxt = (a.data.get("next") or {}).get("hunt") or {}
    target = payload.get("id") or nxt.get("group") or nxt.get("lead")
    seed = a.data.get("seed") if isinstance(a.data.get("seed"), dict) else {}
    run_id = payload.get("run") or nxt.get("run") or _hunt(a).get("run") or seed.get("run")
    note = str(ans.get("why") or payload.get("note") or "").strip()
    ok, msg = hunt.close(a.key, run_id, str(target or ""), as_, note, git.head(a.root) if git.is_repo(a.root) else None)
    if not ok:
        return ActionResult(False, msg, msg)
    upd = {key: None}
    run = hunt.get_run(a.key, run_id)
    if key == "triage_answer" and run:
        upd["triage_detail"] = _triage_detail(a, run["run"], msg)
    return ActionResult(True, msg, update={"data": _data(a, **upd)})


async def hunt_take(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_take, a)


def _take(a: ActionInput) -> ActionResult:
    seed = a.data.get("seed") if isinstance(a.data.get("seed"), dict) else {}
    run_id = a.settings.get("hunt_run") or a.settings.get("run") or seed.get("run")
    r = hunt.take(a.key, a.root, run_id)
    if not r["ok"]:
        return ActionResult(False, "hunt-next refuses: " + r["why"], r["why"])
    if not r.get("flow"):
        return ActionResult(True, r["note"], update={"markers": _marker(a, "FLOW", "none", "take")})
    s = r["seed"]
    detail = "\n".join([f"Handed {r['group'] or r['lead']} to a {r['flow']} flow: {s['title']}",
                        f"Severity {r['severity']} · findings {', '.join(r['members'])}" +
                        (" · a regression end-to-end test is required" if s["needs_e2e"] else ""), "",
                        "When the fix merges (or you decide), close it:",
                        "fixed / accepted / wontfix, with a note (the PR or commit, or why). later: end here and close it "
                        "from the Hunt page."])
    return ActionResult(True, r["note"], json.dumps(s, indent=2)[:3000],
                        {"data": _data(a, next=s, close_gate_detail=detail),
                         "markers": _marker(a, "FLOW", r["flow"], "take")})


ACTIONS = {
    "hunt_start": hunt_start, "hunt_confirm": hunt_confirm, "hunt_deps": hunt_deps, "hunt_ingest": hunt_ingest,
    "hunt_verdicts": hunt_verdicts, "hunt_group": hunt_group, "hunt_report": hunt_report, "hunt_commit": hunt_commit,
    "hunt_close": hunt_close, "hunt_take": hunt_take,
}
