"""The bug hunt's backlog (keel v1 lib/hunt.js, ported): runs, candidates, groups and recipes in the engine DB.

A hunt changes no code. It sweeps the project with one hunter per lens and lane, proves every candidate against
the running stack, groups the proven ones that share a cause and renders a report. The backlog outlives the flow:
hunt-next drains it one group at a time into a fix or feature flow.

    hunt_runs        one per hunt: scope, mode (auto|semi), fast, the lens set, which lens/lane pairs were swept
    hunt_candidates  F-001, F-002 ... status candidate -> proven | unproven | false; closed fixed | accepted | wontfix
    hunt_groups      G-01 ...: one cause, a lead finding, the others are its symptoms
    hunt_recipes     the recipe a proven verdict came with (what the fix flow's reproducer gets, never the claim)

The refusals are the point and live here, not in prompts: no ingest outside a hunter's lane, no severity from a
hunter, no proven verdict without a recipe that ran twice, no 5xx below the rubric's floor, no report while a
candidate has no verdict, no hand-over while the report is uncommitted.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .. import config as engine_config
from .. import rules
from ..tools import git
from . import db

SEVERITIES = ["low", "moderate", "high", "critical"]
VERDICTS = ["proven", "unproven", "false"]
DISPOSITIONS = ["fixed", "accepted", "wontfix"]
KINDS = ["defect", "unspecified"]
# A recipe is something to run, never a test: writing the regression test is the fix flow's reproducer's job.
RECIPE_EXTS = (".sh", ".http", ".sql", ".md", ".probe.ts")
GATES = ["sweep", "prove"]

DEFAULTS: dict = {
    "lenses": ["exploration", "observability", "security", "behavioral", "technical", "concurrency", "idempotency",
               "contract-drift", "reachability", "test-integrity", "data-migration"],
    "lens_lanes": {
        "exploration": ["api", "web"], "observability": ["api", "web"], "security": ["api", "web"],
        "behavioral": ["web", "api"], "technical": ["api", "web"], "concurrency": ["api"], "idempotency": ["api", "web"],
        "contract-drift": ["both"], "reachability": ["api", "web"], "test-integrity": ["api", "web"],
        "data-migration": ["api"], "messaging": ["api"],
    },
    "dedup_line_window": 10,
    "severity_rubric": {
        "critical": "data loss or corruption; cross-tenant exposure; authentication or authorization bypass; a silent wrong write",
        "high": "a 5xx on a documented path; a lost update under ordinary concurrency; a retry that creates duplicates",
        "moderate": "a wrong status code with otherwise correct behaviour; missing validation with no exploit path",
        "low": "cosmetic; unreachable in the current code",
    },
    "severity_floor_5xx": "high",
    "fast_lenses": ["exploration", "observability", "security", "technical", "contract-drift", "reachability"],
    "default_scope": "diff",
    "max_candidates_per_lens": 12,
    "prove_concurrency": 4,
    "prove_rounds": 3,
    "report_dir": "docs/hunts",
}

LENS_HELP = {
    "exploration": "drives the running stack and holds what comes back against what the contract promised",
    "observability": "causes a known failure and checks what the system recorded about it",
    "security": "what an actor can reach that they should not: authorization, tenancy, input, secrets",
    "behavioral": "controls that look live and are inert; errors that leak internals; state that should be cleared",
    "technical": "correctness in code paths no test took: transactions, catches, upserts, unbounded queries",
    "concurrency": "what breaks when two callers arrive at once",
    "idempotency": "what breaks when the same caller arrives twice",
    "contract-drift": "the disagreement between the contract, the server and the client",
    "reachability": "does a plain happy-path call to every route answer at all, right now",
    "test-integrity": "tests that pass while production is broken",
    "data-migration": "what the schema permits that the domain does not",
    "messaging": "messages consumed twice, lost, or stuck behind a poisoned one",
}


# ------------------------------------------------------------------ config

def settings(root: str) -> dict:
    """The hunt block of .keel/config.yml over keel's defaults (lens_lanes merged per lens)."""
    own = (rules.load_config(root) or {}).get("hunt") or {}
    out = {**DEFAULTS, **{k: v for k, v in own.items() if v is not None}}
    out["lens_lanes"] = {**DEFAULTS["lens_lanes"], **(own.get("lens_lanes") or {})}
    out["severity_rubric"] = {**DEFAULTS["severity_rubric"], **(own.get("severity_rubric") or {})}
    # A lane with no files in the repo (no web code, say) gets no hunter: it would only search until it runs out of turns.
    present = lanes_present(root)
    if present:
        out["lens_lanes"] = {lens: [ln for ln in lanes if ln == "both" or ln in present] or lanes
                             for lens, lanes in out["lens_lanes"].items()}
    return out


def lanes_present(root: str) -> set[str]:
    """The lanes (api, web) that have at least one tracked file; empty when it cannot tell."""
    r = git.git(root, "ls-files")
    if r.returncode != 0:
        return set()
    cfg = rules.load_config(root)
    return {lane for f in r.stdout.splitlines() if (lane := rules.LANE_OF.get(rules.classify(cfg, f)))}


def rank(severity: str | None) -> int:
    s = str(severity or "").lower()
    return SEVERITIES.index(s) if s in SEVERITIES else -1


def lanes_for(cfg: dict, lens: str) -> list[str]:
    return list((cfg.get("lens_lanes") or {}).get(lens) or ["both"])


def sweep_pairs(cfg: dict, lenses: list[str]) -> list[str]:
    """Every lens:lane pair a sweep owes: one hunter each."""
    return [f"{lens}:{lane}" for lens in lenses for lane in lanes_for(cfg, lens)]


def lane_of(root: str, cited: str) -> str | None:
    """Which lane a cited "path:line" belongs to, through the classifier the guards use (None: neither lane)."""
    rel = re.sub(r":\d+(?:[-:]\d+)?$", "", str(cited).strip())
    return rules.LANE_OF.get(rules.classify(rules.load_config(root), rel))


def brief(root: str, lens: str) -> str | None:
    """A lens brief: the project's own .keel/lenses/<lens>.md first, else keel's references/lenses.md section."""
    own = Path(root) / ".keel" / "lenses" / f"{lens}.md"
    if own.is_file():
        return own.read_text(errors="replace").strip()
    ref = engine_config.content_dir() / "skills" / "hunt" / "references" / "lenses.md"
    try:
        text = ref.read_text()
    except OSError:
        return None
    m = re.search(rf"^## {re.escape(lens)}\s*\n(.*?)(?=^---\s*$|^## |\Z)", text, re.S | re.M)
    # the probe script a brief names is beside lenses.md; the hunter gets its full path
    return m.group(1).strip().replace("references/reachability-probe.sh", str(ref.parent / "reachability-probe.sh")) if m else None


# ------------------------------------------------------------------ store

RUN_JSON = ("scope", "lenses", "swept", "gates", "stack")
RUN_COLS = ["project", "run", "root", "thread_id", "at", "sha", "branch", "scope_json", "mode", "fast", "lenses_json",
            "swept_json", "gates_json", "stack_json", "report", "candidates_report", "next_id", "next_group", "updated_at"]
CAND_JSON = ("also", "where", "dispatch", "close")
CAND_COLS = ["project", "run", "id", "lens", "lane", "also_json", "title", "where_json", "symptom", "impact", "claim",
             "repro_hint", "kind", "status", "severity", "group_id", "group_role", "evidence", "proved_at", "proved_sha",
             "needs_e2e", "e2e_spec", "dispatch_json", "close_json", "at"]


def _decode(cols: list[str], row, json_keys) -> dict:
    out = {}
    for k, v in zip(cols, row):
        if k.endswith("_json") and k[:-5] in json_keys:
            out[k[:-5]] = db.loads(v, None)
        else:
            out[k] = v
    return out


def _encode(fields: dict, json_keys) -> dict:
    return {(f"{k}_json" if k in json_keys else k): (json.dumps(v) if k in json_keys else v) for k, v in fields.items()}


def _run_from(row) -> dict:
    r = _decode(RUN_COLS, row, RUN_JSON)
    r["fast"] = bool(r["fast"])
    return r


def _cand_from(row) -> dict:
    c = _decode(CAND_COLS, row, CAND_JSON)
    c["group"] = c.pop("group_id")
    c["needs_e2e"] = None if c["needs_e2e"] is None else bool(c["needs_e2e"])
    c["also"] = c.get("also") or []
    c["where"] = c.get("where") or []
    return c


def _next_run_id(conn, project: str, day: str) -> str:
    taken = [r[0] for r in conn.execute("select run from hunt_runs where project = ? and run like ?", (project, f"{day}-%"))]
    nums = [int(x.rsplit("-", 1)[1]) for x in taken if x.rsplit("-", 1)[1].isdigit()]
    return f"{day}-{(max(nums) + 1 if nums else 1):02d}"


def new_run(project: str, root: str, *, thread_id: str, mode: str, fast: bool, scope: dict, configured: list[str],
            proposed: list[str], stack: dict | None = None) -> dict:
    sha = git.head(root) if git.is_repo(root) else None
    branch = git.branch(root) if git.is_repo(root) else None
    at = db.now()
    lenses = {"configured": configured, "proposed": proposed, "confirmed": None, "confirmed_by": None, "confirmed_at": None}
    # --auto: the semi gates are approved by the engine, and the report says which gates no human saw.
    gates = {g: ({"decision": "approve", "by": "model", "at": at} if mode == "auto" else None) for g in GATES}
    with db.connect() as conn:
        run = _next_run_id(conn, project, at[:10])
        row = {"project": project, "run": run, "root": root, "thread_id": thread_id, "at": at, "sha": sha, "branch": branch,
               "scope": scope, "mode": mode, "fast": int(fast), "lenses": lenses, "swept": {}, "gates": gates,
               "stack": stack or {}, "report": None, "candidates_report": None, "next_id": 1, "next_group": 1, "updated_at": at}
        enc = _encode(row, RUN_JSON)
        conn.execute(f"insert into hunt_runs ({', '.join(RUN_COLS)}) values ({', '.join('?' * len(RUN_COLS))})",
                     [enc[c] for c in RUN_COLS])
    return get_run(project, run)


def get_run(project: str, run: str | None = None) -> dict | None:
    """One run; without an id, the project's latest."""
    with db.connect() as conn:
        if run:
            row = conn.execute(f"select {', '.join(RUN_COLS)} from hunt_runs where project = ? and run = ?", (project, run)).fetchone()
        else:
            row = conn.execute(f"select {', '.join(RUN_COLS)} from hunt_runs where project = ? order by at desc, run desc limit 1",
                               (project,)).fetchone()
    return _run_from(row) if row else None


def update_run(project: str, run: str, **fields):
    fields["updated_at"] = db.now()
    enc = _encode(fields, RUN_JSON)
    with db.connect() as conn:
        conn.execute(f"update hunt_runs set {', '.join(f'{k} = ?' for k in enc)} where project = ? and run = ?",
                     [*enc.values(), project, run])


def candidates(project: str, run: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(f"select {', '.join(CAND_COLS)} from hunt_candidates where project = ? and run = ? order by id",
                            (project, run)).fetchall()
    return [_cand_from(r) for r in rows]


def update_candidate(project: str, run: str, cid: str, **fields):
    if "group" in fields:
        fields["group_id"] = fields.pop("group")
    if "needs_e2e" in fields and fields["needs_e2e"] is not None:
        fields["needs_e2e"] = int(bool(fields["needs_e2e"]))
    enc = _encode(fields, CAND_JSON)
    with db.connect() as conn:
        conn.execute(f"update hunt_candidates set {', '.join(f'{k} = ?' for k in enc)} where project = ? and run = ? and id = ?",
                     [*enc.values(), project, run, cid])


def groups(project: str, run: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("select id, cause, lead, at from hunt_groups where project = ? and run = ? order by id",
                            (project, run)).fetchall()
    return [dict(zip(("id", "cause", "lead", "at"), r)) for r in rows]


def recipes(project: str, run: str) -> dict[str, dict]:
    with db.connect() as conn:
        rows = conn.execute("select candidate, file, body, runs, at from hunt_recipes where project = ? and run = ?",
                            (project, run)).fetchall()
    return {r[0]: dict(zip(("candidate", "file", "body", "runs", "at"), r)) for r in rows}


def counts(cands: list[dict]) -> dict:
    out = {s: 0 for s in ["candidate", *VERDICTS, *DISPOSITIONS]}
    for c in cands:
        out[c["status"]] = out.get(c["status"], 0) + 1
        if c.get("close"):
            out[c["close"]["as"]] += 1
    return out


def open_findings(cands: list[dict]) -> list[dict]:
    """Proven, not closed, not dispatched (a group goes out as one unit)."""
    sent = {c["group"] for c in cands if c.get("dispatch") and c.get("group")}
    return [c for c in cands if c["status"] == "proven" and not c.get("close") and not c.get("dispatch")
            and not (c.get("group") and c["group"] in sent)]


# ------------------------------------------------------------------ sweep: ingest

def _spot(w: str) -> tuple[str, int | None]:
    m = re.match(r"^(.*?):(\d+)", str(w))
    return (m.group(1), int(m.group(2))) if m else (str(w), None)


def duplicate_of(found: list[dict], cand: dict, window: int) -> dict | None:
    """Same file and a line within the window: the same finding seen again (corroboration, not a second bug)."""
    spots = [_spot(w) for w in cand.get("where") or []]
    for f in found:
        for w in f.get("where") or []:
            file, line = _spot(w)
            if any(s[0] == file and (s[1] is None or line is None or abs(s[1] - line) <= window) for s in spots):
                return f
    return None


def check_candidate(c, n: int) -> str | None:
    at = f"candidate[{n}]"
    if not isinstance(c, dict):
        return f"{at} is not an object"
    if not isinstance(c.get("title"), str) or not c["title"].strip():
        return f"{at}.title is required"
    where = c.get("where") or c.get("at")
    if not isinstance(where, list) or not where or any(not isinstance(w, str) or not w.strip() for w in where):
        return f'{at}.where must be a non-empty list of "path:line"'
    if not isinstance(c.get("symptom"), str) or not c["symptom"].strip():
        return f"{at}.symptom is required: what a user or caller observes"
    if c.get("kind") and c["kind"] not in KINDS:
        return f"{at}.kind must be one of {', '.join(KINDS)}"
    return None


def ingest(project: str, root: str, run: str, pair: str, items: list[dict]) -> dict:
    """One hunter's batch (lens:lane). Refused whole when over the cap, malformed, or citing the other lane.
    New candidates get F-nnn ids; a duplicate of a known finding records this lens on it instead."""
    cfg = settings(root)
    r = get_run(project, run)
    lens, _, lane = pair.partition(":")
    lane = lane or "both"
    confirmed = (r["lenses"] or {}).get("confirmed") or []
    out = {"pair": pair, "added": [], "merged": [], "dropped_severity": 0, "refused": None, "count": len(items)}
    if lens not in confirmed:
        out["refused"] = f"{lens} is not one of the confirmed lenses ({', '.join(confirmed) or 'none'})"
    cap = int(cfg["max_candidates_per_lens"])
    cap = max(1, -(-cap // 2)) if r["fast"] else cap
    if not out["refused"] and len(items) > cap:
        out["refused"] = f"{len(items)} candidates, over the cap of {cap}: a lens returning that many has stopped judging"
    for n, c in enumerate(items if not out["refused"] else []):
        bad = check_candidate(c, n)
        if bad:
            out["refused"] = bad
            break
    if not out["refused"] and lane != "both":
        # The lane is enforced, not requested: every cited path is classified, one outside the lane refuses the batch.
        for n, c in enumerate(items):
            off = [w for w in (c.get("where") or c.get("at")) if (lane_of(root, w) or lane) != lane]
            if off:
                out["refused"] = f"candidate[{n}] cites a file outside the {lane} lane: {', '.join(off)}"
                break
    swept = dict(r["swept"] or {})
    if out["refused"]:
        swept[pair] = {"at": db.now(), "count": len(items), "refused": out["refused"]}
        update_run(project, run, swept=swept)
        return out
    known = candidates(project, run)
    next_id = int(r["next_id"])
    window = int(cfg["dedup_line_window"])
    with db.connect() as conn:
        for c in items:
            dup = duplicate_of(known, c, window)
            if dup:
                if pair not in dup["also"] and f"{dup['lens']}:{dup['lane']}" != pair:
                    dup["also"] = dup["also"] + [pair]
                    conn.execute("update hunt_candidates set also_json = ? where project = ? and run = ? and id = ?",
                                 (json.dumps(dup["also"]), project, run, dup["id"]))
                out["merged"].append(dup["id"])
                continue
            if c.get("severity"):
                out["dropped_severity"] += 1      # a severity is a measurement, and nothing has measured anything yet
            cid = f"F-{next_id:03d}"
            next_id += 1
            row = {"project": project, "run": run, "id": cid, "lens": lens, "lane": lane, "also": [],
                   "title": c["title"].strip()[:120], "where": [str(w) for w in (c.get("where") or c.get("at"))],
                   "symptom": c["symptom"].strip(), "impact": str(c.get("impact") or "").strip() or None,
                   "claim": str(c.get("claim") or "").strip() or None, "repro_hint": str(c.get("repro_hint") or "").strip() or None,
                   "kind": c.get("kind") if c.get("kind") in KINDS else "defect", "status": "candidate", "severity": None,
                   "group_id": None, "group_role": None, "evidence": None, "proved_at": None, "proved_sha": None,
                   "needs_e2e": None, "e2e_spec": None, "dispatch": None, "close": None, "at": db.now()}
            enc = _encode(row, CAND_JSON)
            conn.execute(f"insert into hunt_candidates ({', '.join(CAND_COLS)}) values ({', '.join('?' * len(CAND_COLS))})",
                         [enc[k] for k in CAND_COLS])
            known.append({**row, "group": None})
            out["added"].append(cid)
    swept[pair] = {"at": db.now(), "count": len(items), "added": len(out["added"]), "merged": len(out["merged"])}
    update_run(project, run, swept=swept, next_id=next_id)
    return out


# ------------------------------------------------------------------ prove

def prove_item(c: dict, run: str, report_dir: str) -> dict:
    """What a prover is given: the symptom and where, never the hunter's claim (a prover told the theory confirms it)."""
    return {"id": c["id"], "lens": c["lens"], "where": c["where"], "symptom": c["symptom"], "kind": c["kind"],
            "recipe_file": f"{c['id']}.sh (or .http .sql .md .probe.ts; never a test file)",
            "copied_to": f"{report_dir}/{run}/repro/"}


def _recipe(v: dict, cid: str) -> tuple[str, str]:
    rec = v.get("recipe")
    if isinstance(rec, dict):
        return str(rec.get("file") or f"{cid}.sh").strip(), str(rec.get("body") or "")
    if isinstance(rec, str):
        return f"{cid}.sh", rec
    return f"{cid}.sh", ""


def record_verdict(project: str, root: str, run: str, cid: str, v: dict, *, force: bool = False) -> str | None:
    """Record a prover's verdict. Returns why it was refused (the candidate stays a candidate), or None.

    force (the last round): a proven verdict that still has no usable recipe becomes unproven, and a 5xx filed below
    the floor is raised to it, so the report can render; both say so in the evidence."""
    cfg = settings(root)
    c = next((x for x in candidates(project, run) if x["id"] == cid), None)
    if not c:
        return f"no finding {cid} in hunt {run}"
    if c["status"] != "candidate":
        return None
    verdict = str(v.get("verdict") or "").strip().lower()
    evidence = str(v.get("evidence") or "").strip()
    if verdict not in VERDICTS:
        return f"{cid}: the verdict must be proven, unproven or false (got {verdict or 'nothing'})"
    if not evidence:
        return f"{cid}: every verdict needs evidence: what was run, and what came back"
    fields: dict = {"status": verdict, "evidence": evidence[:4000], "proved_at": db.now(),
                    "proved_sha": git.head(root) if git.is_repo(root) else None}
    if verdict == "proven":
        file, body = _recipe(v, cid)
        runs = int(v.get("runs") or 0) if str(v.get("runs") or "0").isdigit() else 0
        severity = str(v.get("severity") or "").strip().lower()
        why = None
        if not body.strip():
            why = f"{cid}: a proven verdict needs a recipe: the command that makes it fail on demand"
        elif not file.endswith(RECIPE_EXTS) or re.search(r"\.(spec|test)\.[jt]sx?$", file):
            why = f"{cid}: the recipe {file} must be {', '.join(RECIPE_EXTS)}, never a test file"
        elif runs < 2:
            why = f"{cid}: a proven verdict means the recipe produced the symptom twice (runs: {runs})"
        if why and not force:
            return why
        if why:
            fields.update(status="unproven", evidence=f"{evidence}\n\nRecorded as unproven: {why}."[:4000])
        else:
            if severity not in SEVERITIES:
                if not force:
                    return f"{cid}: a proven verdict needs a severity ({' | '.join(SEVERITIES)}), judged against the rubric"
                severity = "moderate"
            floor = str(cfg.get("severity_floor_5xx") or "high").lower()
            shows_5xx = bool(re.search(r"\b5\d\d\b", f"{evidence}\n{body}") or re.search(r"internal server error", f"{evidence}\n{body}", re.I))
            if shows_5xx and rank(severity) < rank(floor):
                if not force:
                    return (f"{cid} shows a 5xx, so it cannot be {severity}: the rubric puts it at {floor} "
                            f"(\"{cfg['severity_rubric'].get(floor, '')}\"). Record {floor} or above.")
                fields["evidence"] = f"{evidence}\n\nSeverity raised from {severity} to {floor}: it shows a 5xx."[:4000]
                severity = floor
            fields["severity"] = severity
            name = Path(file).name
            with db.connect() as conn:
                conn.execute("insert or replace into hunt_recipes values (?,?,?,?,?,?,?)",
                             (project, run, cid, name, body, runs, db.now()))
    update_candidate(project, run, cid, **fields)
    return None


# ------------------------------------------------------------------ group

def add_group(project: str, run: str, members: list[str], cause: str, lead: str | None = None) -> tuple[str | None, str | None]:
    """One cause, many symptoms. Only proven findings, each in one group. Returns (group id, None) or (None, why)."""
    ids = [str(m).strip().upper() for m in members if str(m).strip()]
    if len(ids) < 2:
        return None, "a group needs two or more findings"
    if not str(cause or "").strip():
        return None, "a group needs its cause in one sentence"
    have = {c["id"]: c for c in candidates(project, run)}
    for cid in ids:
        c = have.get(cid)
        if not c:
            return None, f"no finding {cid}"
        if c["status"] != "proven":
            return None, f"{cid} is {c['status']}, not proven: only proven findings share a cause"
        if c.get("group"):
            return None, f"{cid} already belongs to {c['group']}"
    lead = str(lead or ids[0]).upper()
    if lead not in ids:
        return None, f"the lead {lead} must be one of the grouped findings"
    r = get_run(project, run)
    gid = f"G-{int(r['next_group']):02d}"
    with db.connect() as conn:
        conn.execute("insert into hunt_groups values (?,?,?,?,?,?)", (project, run, gid, cause.strip()[:300], lead, db.now()))
    for cid in ids:
        update_candidate(project, run, cid, group=gid, group_role="cause" if cid == lead else "symptom")
    update_run(project, run, next_group=int(r["next_group"]) + 1)
    return gid, None


# ------------------------------------------------------------------ e2e coverage

def e2e_coverage(root: str, c: dict) -> str | None:
    """The end-to-end spec that mentions this finding or a file it cites, or None (keel v1 e2eCoverage: crude on
    purpose, a wrong yes is worse than a wrong no)."""
    cfg = rules.load_config(root) or {}
    base = Path(root) / ((cfg.get("e2e") or {}).get("dir") or "e2e")
    if not base.is_dir():
        return None
    names = [n for n in (Path(_spot(w)[0]).stem for w in c.get("where") or []) if len(n) > 3]
    for f in sorted(base.rglob("*")):
        if "node_modules" in f.parts or not re.search(r"\.(spec|test)\.[tj]sx?$", f.name):
            continue
        text = f.read_text(errors="replace")
        if c["id"] in text or any(n in text for n in names):
            return str(f.relative_to(root))
    return None


# ------------------------------------------------------------------ report

def report_dir(root: str, run: str) -> Path:
    return Path(root) / settings(root)["report_dir"] / run


def _where(c: dict) -> str:
    return ", ".join(f"`{w}`" for w in c["where"])


def _head(c: dict) -> str:
    lane = f":{c['lane']}" if c.get("lane") and c["lane"] != "both" else ""
    return f"### {c['id']} · {c.get('severity') or ('unmeasured' if c['status'] == 'candidate' else '—')} · {c['lens']}{lane} · {c['title']}"


def _block(c: dict, rec: dict | None, role: str | None) -> list[str]:
    def pad(k):
        return f"`{k.ljust(9)}`"
    out = [f"{pad('Where')} {_where(c)}", f"{pad('What')} {c['symptom']}"]
    if c.get("impact"):
        out.append(f"{pad('Impact')} {c['impact']}")
    if rec:
        out.append(f"{pad('Proof')} `repro/{rec['file']}` (ran {rec.get('runs') or '?'} times) — proved at `{str(c.get('proved_sha') or '')[:7]}`")
    elif c["status"] == "candidate":
        out.append(f"{pad('Proof')} none yet — nobody has tried to reproduce this")
    if c.get("evidence"):
        out.append(f"{pad('Evidence')} {c['evidence']}")
    if c.get("severity"):
        out.append(f"{pad('Severity')} {c['severity']}")
    if c.get("claim"):
        out.append(f"{pad('Claim')} {c['claim']} _(the hunter's theory, not a verdict)_")
    if c.get("also"):
        out.append(f"{pad('Also by')} {', '.join(c['also'])}")
    if role:
        out.append(f"{pad('Role')} {role} of its group")
    if c.get("needs_e2e"):
        out.append(f"{pad('E2E')} no end-to-end spec references this finding")
    if c.get("close"):
        out.append(f"{pad('Closed')} {c['close']['as']} — {c['close']['note']}")
    return [f"- {x}" for x in out]


def _banners(run: dict, cfg: dict) -> list[str]:
    out = []
    lenses = run["lenses"] or {}
    if run["fast"]:
        looked = lenses.get("confirmed") or lenses.get("proposed") or []
        off = [x for x in cfg["lenses"] if x not in looked]
        out += ["", f"> **This was a fast hunt.** It looked through {', '.join(looked)} only"
                + (f", and never through {', '.join(off)}." if off else "."),
                "> Everything reported was still proved; fast narrows what is examined, never whether a finding is "
                "verified. A short report is not a clean bill of health."]
    if lenses.get("confirmed_by") == "model":
        out += ["", "> **The lens set was confirmed by the model, not reviewed.**"]
    unseen = [g for g in GATES if ((run["gates"] or {}).get(g) or {}).get("by") == "model"]
    if unseen:
        out += ["", f"> **{len(unseen)} of {len(GATES)} gates were approved without a person** ({', '.join(unseen)}), because "
                "this hunt ran in auto mode. Everything reported was still proved; no one checked whether the right "
                "things were looked for."]
    return out


def _intro(run: dict) -> list[str]:
    scope = run["scope"] or {}
    paths = f" ({', '.join(scope.get('paths') or [])})" if scope.get("paths") else ""
    stack = run.get("stack") or {}
    return [f"Run `{run['run']}` · scope `{scope.get('mode', 'all')}`{paths} · `{str(run.get('sha') or '')[:7]}` on "
            f"`{run.get('branch') or '?'}` · started {run['at']}", "",
            f"Lenses: {', '.join((run['lenses'] or {}).get('confirmed') or []) or 'none confirmed'}", "",
            f"Stack at the start: api {stack.get('api', 'unknown')} · web {stack.get('web', 'unknown')}"]


def render_candidates(project: str, root: str, run_id: str) -> str:
    """What the hunters proposed, before anything was measured. Stamped UNVERIFIED throughout."""
    run, cfg = get_run(project, run_id), settings(root)
    cands = candidates(project, run_id)
    waiting = [c for c in cands if c["status"] == "candidate"]
    pairs = sweep_pairs(cfg, (run["lenses"] or {}).get("confirmed") or [])
    swept = run["swept"] or {}
    out = [f"# Candidates — hunt {run_id}", "",
           "> **UNVERIFIED — proposed, not reproduced.** Nothing on this page has been run against anything. No",
           "> severity here has been measured, because measuring is the next step.", ""]
    out += _intro(run) + _banners(run, cfg)
    out += ["", f"Swept {sum(1 for p in pairs if p in swept and not swept[p].get('refused'))} of {len(pairs)} lens/lane pair(s)."]
    missing = [p for p in pairs if p not in swept]
    if missing:
        out.append(f"Not swept: {', '.join(missing)}.")
    for p in pairs:
        if (swept.get(p) or {}).get("refused"):
            out.append(f"Refused batch {p}: {swept[p]['refused']}.")
    out += ["", "| | count |", "|---|---|", f"| awaiting a verdict | {len(waiting)} |",
            f"| already decided | {len(cands) - len(waiting)} |", ""]
    if not waiting:
        out.append("No candidates are awaiting a verdict.")
    by_pair: dict[str, list[dict]] = {}
    for c in waiting:
        by_pair.setdefault(f"{c['lens']}:{c['lane']}", []).append(c)
    for key in sorted(by_pair):
        out += ["", f"## {key}", ""]
        for c in by_pair[key]:
            out += [_head(c), "", *_block(c, None, None), ""]
    out += ["", "---", "", "Every candidate goes to one prover. The report refuses to render until each has a verdict."]
    return "\n".join(out) + "\n"


def render(project: str, root: str, run_id: str) -> str:
    """The report, rendered from the backlog (never written by a model), so a second render is byte-identical."""
    run, cfg = get_run(project, run_id), settings(root)
    cands, grps, recs = candidates(project, run_id), groups(project, run_id), recipes(project, run_id)
    c = counts(cands)

    def by_sev(x):
        return (-rank(x.get("severity")), x["id"])
    proven = [x for x in cands if x["status"] == "proven"]
    ungrouped = sorted([x for x in proven if not x.get("group")], key=by_sev)
    suspected = [x for x in cands if x["status"] == "unproven"]
    rejected = [x for x in cands if x["status"] == "false"]
    closed = [x for x in cands if x.get("close")]
    out = [f"# Bug hunt — {run['at'][:10]}", ""] + _intro(run) + _banners(run, cfg)
    out += ["", "| status | count |", "|---|---|", f"| proven | {c['proven']} |", f"| suspected — not reproduced | {c['unproven']} |",
            f"| rejected — not a bug | {c['false']} |", f"| closed | {len(closed)} |",
            f"| **open, proven, not yet dispatched** | **{len(open_findings(cands))}** |", "",
            "Only proven findings carry a severity. A suspected finding has none because nothing measured it, not "
            "because it is harmless.", "", "## Severity rubric", ""]
    out += [f"- **{s}**: {cfg['severity_rubric'].get(s, '')}" for s in reversed(SEVERITIES)]
    out.append(f"- A proven finding whose evidence shows a 5xx is never below **{cfg['severity_floor_5xx']}**.")
    if proven:
        out += ["", "## Proven"]
        for g in grps:
            members = [x for x in cands if x.get("group") == g["id"]]
            if not members:
                continue
            lead = next((x for x in members if x["id"] == g["lead"]), members[0])
            worst = sorted(members, key=by_sev)[0]
            out += ["", f"### {g['id']} · {worst.get('severity') or '—'} · {g['cause']}", "",
                    f"{len(members)} findings, one cause. Fix the cause; carry the symptoms as regression criteria.", "",
                    _head(lead), "", *_block(lead, recs.get(lead["id"]), "cause")]
            rest = sorted([x for x in members if x is not lead], key=by_sev)
            if rest:
                out += ["", "| symptom | lens | severity | where | proved by |", "|---|---|---|---|---|"]
                out += [f"| {s['id']} | {s['lens']} | {s.get('severity') or '—'} | {_where(s)} | "
                        f"{('`repro/' + recs[s['id']]['file'] + '`') if s['id'] in recs else '—'} |" for s in rest]
        for x in ungrouped:
            out += ["", _head(x), "", *_block(x, recs.get(x["id"]), None)]
    if suspected:
        out += ["", "## Suspected — proposed, not reproduced", "",
                "A prover tried and could not make these fail. They are kept because unproven is not disproven. They "
                "carry no severity and are not handed over."]
        for x in suspected:
            out += ["", _head(x), "", *_block(x, None, None)]
    if rejected:
        out += ["", "## Rejected — not a bug", ""]
        out += [f"**{x['id']}** · {x['lens']} · {_where(x)} — {x.get('evidence') or ''}\n" for x in rejected]
    if closed:
        out += ["", "## Closed", "", "| id | as | note | at |", "|---|---|---|---|"]
        out += [f"| {x['id']} | {x['close']['as']} | {x['close']['note']} | {x['close']['at'][:10]} |" for x in closed]
    out += ["", "---", "", f"Rendered by keel from hunt `{run_id}`. Do not hand-edit; the next render replaces it."]
    return "\n".join(out) + "\n"


def write_candidates_page(project: str, root: str, run_id: str) -> str:
    d = report_dir(root, run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidates.md").write_text(render_candidates(project, root, run_id))
    rel = str((d / "candidates.md").relative_to(root))
    update_run(project, run_id, candidates_report=rel)
    return rel


def write_report(project: str, root: str, run_id: str) -> tuple[str | None, str]:
    """Renders report.md and copies each recipe beside it. Refuses while any candidate has no verdict.
    Returns (relative path, note) or (None, why it refused)."""
    cands = candidates(project, run_id)
    waiting = [c["id"] for c in cands if c["status"] == "candidate"]
    if waiting:
        return None, (f"{len(waiting)} finding(s) still have no verdict: {', '.join(waiting)}. A report is not a list of "
                      "guesses; every candidate needs a prover's verdict first.")
    for c in cands:
        if c["status"] == "proven":
            spec = e2e_coverage(root, c)
            update_candidate(project, run_id, c["id"], needs_e2e=not spec, e2e_spec=spec)
    d = report_dir(root, run_id)
    if (d / "repro").is_dir():
        shutil.rmtree(d / "repro")
    recs = recipes(project, run_id)
    for rec in recs.values():
        (d / "repro").mkdir(parents=True, exist_ok=True)
        (d / "repro" / rec["file"]).write_text(rec["body"] if rec["body"].endswith("\n") else rec["body"] + "\n")
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text(render(project, root, run_id))
    rel = str((d / "report.md").relative_to(root))
    update_run(project, run_id, report=rel)
    c = counts(candidates(project, run_id))
    return rel, f"wrote {rel} and {len(recs)} recipe(s): {c['proven']} proven, {c['unproven']} suspected, {c['false']} rejected"


def report_committed(root: str, run: dict) -> tuple[bool, str]:
    if not run.get("report"):
        return False, "the report has not been rendered yet"
    if not git.is_repo(root):
        return True, ""
    folder = str(Path(run["report"]).parent)
    dirty = git.git(root, "status", "--porcelain", "--", folder).stdout.strip()
    if dirty:
        return False, f"{folder}/ is not committed"
    return True, ""


# ------------------------------------------------------------------ take + close

def take(project: str, root: str, run_id: str | None = None) -> dict:
    """The top open group (worst severity first; a group goes as one unit) and the flow it goes to.

    {ok: False, why} while any candidate has no verdict or the report is uncommitted; {ok: True, flow: None} when
    nothing is open; else {ok, flow: fix|feature, seed, members, ...} and the members are marked dispatched."""
    run = get_run(project, run_id)
    if not run:
        return {"ok": False, "why": "No hunt on this project yet. Run the hunt flow first."}
    cands = candidates(project, run["run"])
    waiting = [c["id"] for c in cands if c["status"] == "candidate"]
    if waiting:
        return {"ok": False, "why": f"Hunt {run['run']}: {len(waiting)} candidate(s) have no verdict yet ({', '.join(waiting[:8])}). "
                                    "Prove them first: the report and the hand-over wait for every verdict."}
    ok, why = report_committed(root, run)
    if not ok:
        return {"ok": False, "why": f"Hunt {run['run']}: {why}. The fix flow starts from a clean tree; commit the report first."}
    open_ = sorted(open_findings(cands), key=lambda c: (-rank(c.get("severity")), c["id"]))
    if not open_:
        return {"ok": True, "flow": None, "run": run["run"], "note": "Nothing open: every proven finding is handed over or closed."}
    top = open_[0]
    grp = next((g for g in groups(project, run["run"]) if g["id"] == top.get("group")), None)
    members = [c for c in cands if grp and c.get("group") == grp["id"]] or [top]
    lead = next((c for c in members if grp and c["id"] == grp["lead"]), top)
    rec = recipes(project, run["run"]).get(lead["id"])
    folder = settings(root)["report_dir"]
    flow = "feature" if lead["kind"] == "unspecified" else "fix"
    recipe = f"{folder}/{run['run']}/repro/{rec['file']}\n\n{rec['body']}".strip() if rec else None
    symptoms = [f"{c['id']}: {c['symptom']}" for c in [lead, *[m for m in members if m is not lead]]]
    title = (f"{grp['id']}: {grp['cause']}" if grp else f"{lead['id']}: {lead['title']}")[:200]
    # The reproducer gets the symptom and the recipe, never the claim: a test written from the theory confirms the theory.
    seed = {"title": title, "request": lead["symptom"], "recipe": recipe, "symptoms": symptoms,
            "needs_e2e": any(bool(c.get("needs_e2e")) for c in members),
            "hunt": {"run": run["run"], "group": grp["id"] if grp else None, "lead": lead["id"], "findings": [c["id"] for c in members]}}
    at = db.now()
    for c in members:
        update_candidate(project, run["run"], c["id"], dispatch={"flow": flow, "at": at})
    return {"ok": True, "flow": flow, "run": run["run"], "group": grp["id"] if grp else None, "lead": lead["id"],
            "members": [c["id"] for c in members], "severity": lead.get("severity"), "seed": seed,
            "note": f"{title} -> {flow} flow ({len(members)} finding(s), severity {lead.get('severity')})"}


def close(project: str, run_id: str | None, key: str, as_: str, note: str, sha: str | None = None) -> tuple[bool, str]:
    """Close a finding or a whole group (G-nn) as fixed | accepted | wontfix, with a note. Never deletes."""
    run = get_run(project, run_id)
    if not run:
        return False, "No hunt to close a finding in."
    as_ = str(as_ or "").strip().lower()
    if as_ not in DISPOSITIONS:
        return False, f"Close it as one of {', '.join(DISPOSITIONS)} (got {as_ or 'nothing'})."
    if not str(note or "").strip():
        return False, "Closing needs a note (the PR or commit for fixed, the reason for accepted or wontfix): the record is the point."
    key = str(key or "").strip().upper()
    cands = candidates(project, run["run"])
    members = [c for c in cands if c.get("group") == key] if key.startswith("G-") else [c for c in cands if c["id"] == key]
    if not members:
        return False, f"No finding or group {key or '(none named)'} in hunt {run['run']}."
    todo = [c for c in members if not c.get("close")]
    if not todo:
        return False, f"{key} is already closed as {members[0]['close']['as']}."
    at = db.now()
    for c in todo:
        update_candidate(project, run["run"], c["id"], close={"as": as_, "note": note.strip()[:1000], "at": at, "sha": sha})
    left = len(open_findings(candidates(project, run["run"])))
    return True, f"Closed {key} as {as_} ({len(todo)} finding(s)); {left} still open."


def child_finished(project: str, root: str | None, data: dict, status: str, title: str, workflow_id: str) -> str | None:
    """A fix/feature flow that hunt-next started has ended. done: close its group as fixed, with the branch and the commit
    in the note (keel does not merge; the note says so). failed or stopped: the group is open again (no longer
    dispatched), so the next hunt-next takes it. Returns what was done, or None for a flow no hunt started."""
    seed = data.get("seed") if isinstance(data.get("seed"), dict) else {}
    link = seed.get("hunt") if isinstance(seed.get("hunt"), dict) else {}
    run_id, target = link.get("run"), link.get("group") or link.get("lead")
    if not run_id or not target or not get_run(project, run_id):
        return None
    sha = git.head(root) if root and git.is_repo(root) else None
    if status == "done":
        branch = git.branch(root) if root and git.is_repo(root) else None
        note = (f"{workflow_id} flow \"{title}\" finished on branch {branch or '?'} at {str(sha or '?')[:7]} "
                f"(keel does not merge: merge it, then this is fixed in main).")
        ok, msg = close(project, run_id, str(target), "fixed", note, sha)
        return msg if ok else None
    key = str(target).upper()
    cands = candidates(project, run_id)
    members = [c for c in cands if c.get("group") == key] if key.startswith("G-") else [c for c in cands if c["id"] == key]
    for c in members:
        if not c.get("close"):
            update_candidate(project, run_id, c["id"], dispatch=None)
    return f"{key} is open again: the {workflow_id} flow ended {status}."


# ------------------------------------------------------------------ read API (engine GET /projects/{p}/hunts)

def summary(project: str, run: dict) -> dict:
    cands = candidates(project, run["run"])
    return {"run": run["run"], "at": run["at"], "sha": run.get("sha"), "branch": run.get("branch"), "mode": run.get("mode"),
            "fast": run["fast"], "scope": run.get("scope"), "lenses": run.get("lenses"), "thread_id": run.get("thread_id"),
            "counts": counts(cands), "open": len(open_findings(cands)), "report": run.get("report"),
            "candidates_report": run.get("candidates_report")}


def list_view(project: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(f"select {', '.join(RUN_COLS)} from hunt_runs where project = ? order by at desc, run desc",
                            (project,)).fetchall()
    return [summary(project, _run_from(r)) for r in rows]


def run_view(project: str, run_id: str) -> dict | None:
    run = get_run(project, run_id)
    if not run:
        return None
    recs = recipes(project, run_id)
    cands = [{**c, "recipe": recs.get(c["id"])} for c in candidates(project, run_id)]
    out = {**summary(project, run), "swept": run.get("swept"), "gates": run.get("gates"), "stack": run.get("stack"),
           "candidates": cands, "groups": groups(project, run_id), "report_markdown": None, "candidates_markdown": None}
    for key, rel in (("report_markdown", run.get("report")), ("candidates_markdown", run.get("candidates_report"))):
        f = Path(run.get("root") or "") / rel if rel and run.get("root") else None
        if f and f.is_file():
            out[key] = f.read_text(errors="replace")
    return out
