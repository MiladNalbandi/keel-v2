"""Code actions of the review, diagnose, fix and change flows.

    review_scope        review: the lens argument and the base ref → one item per reviewer (data.review_lenses)
    report              the answers of the flow's fan-out steps verbatim, plus a tally; shown at the next gate
    investigation_note  diagnose: docs/investigations/<date>-<slug>.md with every hypothesis, committed
    bug_intake          fix: the bug's id BUG-<n> as the flow's criterion (runtime/bug.py); no_gates (gates.bug_gates:
                        false) waives Gate R and F; needs_e2e from a seed
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
from . import bug, commit_style, md, spike_report, verdict_actions
from .gate_state import shallow_gates

LENSES = ["correctness", "security", "performance", "architecture", "assertions"]
# How often one gate may be sent back in a row before keel asks (settings.sendback_limit). 0.18.12: a fix question that
# keeps coming back for the same criterion ("AC-004 is blocked", "keeps failing", the amendment loop) counts toward the
# same limit, under the key "stuck:<criterion>" ("stuck:<step>" outside a criteria loop): past it keel offers only skip
# or stop, never another try (the e2e retried a blocked criterion until the driver stopped it).
SENDBACK_LIMIT = 5
STUCK = "stuck:"
CHOICE_LINE = re.compile(r"^- (\w+):")


def ac_commits(root: str, ac_id: str, base: str | None = None) -> dict:
    """One criterion's newest RED and GREEN commits, {"red": sha, "green": sha} (verdict_actions.ac_commits)."""
    return verdict_actions.ac_commits(root, ac_id, base)


def sendback_limit(settings: dict | None) -> int:
    try:
        return int((settings or {}).get("sendback_limit") or SENDBACK_LIMIT)
    except (TypeError, ValueError):
        return SENDBACK_LIMIT


def stuck_tries(state: dict, who: str | None) -> int:
    """How often the person already sent this stuck criterion (or step) around again."""
    return int((state.get("sendbacks") or {}).get(STUCK + str(who), 0) or 0)


def stuck_counted(state: dict, who: str | None, n: int | None) -> dict:
    """The state update that sets (n) or clears (None) the stuck count of a criterion or step."""
    counts = dict(state.get("sendbacks") or {})
    if n is None:
        if STUCK + str(who) not in counts:
            return {}
        counts.pop(STUCK + str(who))
    else:
        counts[STUCK + str(who)] = n
    return {"sendbacks": counts}


def past_limit(names: list[str], can_skip: bool) -> list[str]:
    """The choices left past the limit: skip (when another criterion can go first) and stop."""
    return (["skip"] if can_skip and "skip" in names else []) + ["stop"]


def offered(detail: str, names: list[str]) -> str:
    """A question's text without the "- <choice>: ..." lines of choices it no longer offers."""
    return "\n".join(line for line in str(detail).splitlines()
                     if not (m := CHOICE_LINE.match(line)) or m.group(1) in names)


def limit_note(n: int, limit: int, who: str) -> str:
    return (f"{who} came back {n} time(s) after another try (the limit is {limit}, settings.sendback_limit): keel offers "
            "no more tries.")
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
    # 0.18.65 no base named: the canonical remote's base, and keel init's setup commits at the branch's bottom are not
    # the branch's change (runtime/bases.py branch_scope)
    from . import bases

    named = str(a.data.get("base") or "").strip()
    found = bases.branch_scope(a.root, cfg) if not named else {"base": named, "setup": [], "warning": None}
    base = found["base"]
    if not base or git.git(a.root, "rev-parse", "--verify", "-q", f"{base}^{{commit}}").returncode != 0:
        return _stop(f"The base ref {base or '(none)'} does not exist.", "Pass base: <branch or commit> to compare with.")
    files = [f for f in git.git(a.root, "diff", "--name-only", f"{base}...HEAD").stdout.splitlines() if f.strip()]
    if not files:
        return _stop(f"Nothing to review: git diff {base}...HEAD is empty.",
                     "A reviewer sent to review nothing finds something to say anyway.")
    scope = {"scope": f"git diff {base}...HEAD", "base": base, "files": files[:60]}
    said = [x for x in (found.get("warning"), bases.setup_note(found.get("setup") or [])) if x]
    if said:
        scope["note"] = " ".join(said)
    lint = None
    if low == "lint" or a.data.get("lint"):
        # Static checks (lens lint, or data.lint with any lens): the stacks' check tools on the diff, reported only.
        from . import lint_actions
        lint = lint_actions.lint_review(a, base)
    if low == "lint":
        return _ok(f"Review scope: {scope['scope']} ({len(files)} file(s)); {lint[0]}.", "\n".join(files[:60]),
                   {"data": {**a.data, "review_lenses": [], "lint_report": lint[1]}})
    if low == "code":
        items = [{"id": "code", "title": "whole-branch code review", "agent": "branch-reviewer", **scope}]
    else:
        lenses = _all_lenses(cfg) if low == "all" else [low]
        items = [{"id": lens, "title": f"{lens} lens", "agent": "lens-reviewer", "lens": lens, **scope} for lens in lenses]
    data = {**a.data, "review_lenses": items, **({"lint_report": lint[1]} if lint else {})}
    return _ok(f"Review scope: {scope['scope']} ({len(files)} file(s)); " + ", ".join(i["title"] for i in items)
               + (f"; {lint[0]}" if lint else "") + "." + (f" {scope['note']}" if scope.get("note") else ""),
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
    item = {"id": ac_id, "title": f"{ac_id} review", "agent": "criterion-reviewer", "ac": ac_id, "green": green}
    if single:
        item["single_commit"] = True
    else:
        item["red"] = red
    return _ok(f"Review scope: {ac_id} ({'single commit ' + green if single else f'red {red}, green {green}'}).",
               update={"data": {**a.data, "review_lenses": [item]}})


# ------------------------------------------------------------------ the AC gate's evidence

_AC_REVIEW = re.compile(r"^\s*(?:\*\*|__|`)?\s*AC-REVIEW\s*:\s*(?:\*\*|__)?\s*(pass|findings)\b", re.I | re.M)
REVIEW_MAX = 4000
STAT_LINES = 25


def ac_commit(root: str, prefix: str, ac_id: str, since: str | None = None) -> tuple[str, str] | None:
    """(short sha, subject) of the newest commit that is "<prefix>(<ac id>)" (test = RED, feat = GREEN, fix too), since
    `since` (the flow's start) when given; None when there is none. 0.18.64 read as the trace reads it
    (commit_style.ref_of): with a project commit style the subject is the project's and keel's form is the Keel-Ref
    trailer, so the gate said "no test(AC-1) commit found" for commits the reviewer was shown."""
    if not git.is_repo(root):
        return None
    rng = [f"{since}..HEAD"] if since and git.git(root, "rev-parse", "--verify", "-q", f"{since}^{{commit}}").returncode == 0 else []
    out = git.git(root, "log", "--format=%h%x1f%s%x1f%b%x1e", "-n", "300", *rng).stdout or ""
    heads = [f"{prefix}({ac_id})"] + ([f"fix({ac_id})"] if prefix == "feat" else [])
    for entry in out.split("\x1e"):
        sha, _, rest = entry.strip("\n").partition("\x1f")
        subject, _, body = rest.partition("\x1f")
        ref = commit_style.ref_of(subject, body)
        if sha and any(ref.startswith(h) for h in heads):
            return sha, subject
    return None


def ac_evidence(root: str, ac: dict, acs: list[dict], review: str | None, note: str = "", since: str | None = None) -> str:
    """What the AC gate shows (keel v1's AC gate: the board, the review word for word), as Markdown sections: the
    criterion, its RED commit and test files, its GREEN commit and diffstat, the criterion-reviewer's verdict and answer, and
    the board."""
    ac_id = ac["id"]
    parts = [md.section("Criterion", f"{md.code(ac_id)} [{ac.get('layer', 'API')}] {md.safe_words(ac.get('title', ''))}")]
    commits = []
    red = ac_commit(root, "test", ac_id, since)
    if red:
        files = [f for f in git.git(root, "show", "--name-only", "--format=", red[0]).stdout.splitlines() if f.strip()]
        commits.append(f"**RED**: {md.code(red[0])} {md.code(red[1])}")
        commits.append("Test files:\n\n" + md.bullets([md.code(f) for f in files[:15]]) if files else "(no files in this commit)")
    else:
        commits.append(f"**RED**: no {md.code(f'test({ac_id})')} commit found")
    green = ac_commit(root, "feat", ac_id, since)
    if green:
        stat = [x.rstrip() for x in git.git(root, "show", "--stat", "--format=", green[0]).stdout.splitlines() if x.strip()]
        commits.append(f"**GREEN**: {md.code(green[0])} {md.code(green[1])}")
        if len(stat) > STAT_LINES:
            stat = stat[:STAT_LINES - 1] + [f" ... {len(stat) - STAT_LINES} more", stat[-1]]
        commits.append(md.fence("\n".join(x.strip() for x in stat), "text") if stat else "(no files in this commit)")
    else:
        commits.append(f"**GREEN**: no {md.code(f'feat({ac_id})')} commit found")
    parts.append(md.section("Commits", *commits))
    text = (review or "").strip()
    if text:
        m = _AC_REVIEW.findall(text)
        verdict = m[-1].lower() if m else "no verdict line"
        body = text if len(text) <= REVIEW_MAX else text[:REVIEW_MAX] + "\n... (cut)"
        parts.append(md.section("Review", f"**AC-REVIEW: {verdict}**", "The criterion-reviewer's answer:", md.demote(body)))
    else:
        parts.append(md.section("Review", "**AC-REVIEW: no review ran for this criterion**"))
    done = [a["id"] for a in acs if a.get("status") in ("done", "already-met") and a["id"] != ac_id]
    todo = [a["id"] for a in acs if a.get("status") not in ("done", "already-met") and a["id"] != ac_id]
    ids = lambda xs: ", ".join(md.code(x) for x in xs) or "-"      # noqa: E731
    board = [f"Board: done {ids(done)} · now {md.code(ac_id)} · todo {ids(todo)}"]
    if note:
        board.append(f"Last step: {md.safe_words(note)}")
    parts.append(md.section("Board", *board))
    return md.doc(*parts)


# ------------------------------------------------------------------ the AC gate's code (the web's diff, 0.18.64)

# Each file's diff stops at FILE_LINES lines or FILE_BYTES, all diffs together at TOTAL_BYTES: the gate waits on a page,
# and a criterion that rewrote a big file must not send megabytes to it. MAX_FILES per commit.
FILE_LINES, FILE_BYTES, TOTAL_BYTES, MAX_FILES = 400, 60_000, 400_000, 80
MIN_LEFT = 200          # less than this left of TOTAL_BYTES: the next files come without text
SECRET_NOTE = "secret file: not shown"     # 0.18.64 a committed .env or key: its counts only, never its text
_STATUS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "added", "T": "modified"}


def _bucket(cfg: dict, path: str) -> str:
    """test | prod | generated | lock | secret: lock and generated files (minified, snapshots, built output too) come
    folded; 0.18.64 a secret file (rules.classify: protected-env, a .env or a key) too, and its diff is never sent."""
    from . import test_scope, ticket_actions

    if rules.classify(cfg, path) == "protected-env":
        return "secret"
    noise = ticket_actions.noise_of(cfg, path)
    if noise == "lockfile":
        return "lock"
    if noise:
        return "generated"
    return "test" if test_scope.is_test_file(cfg, path) else "prod"


def _cut_diff(text: str, budget: int) -> tuple[str, bool, int]:
    """A file's diff from its first hunk (no `diff --git`, `index`, `---`/`+++` lines), cut at FILE_LINES lines,
    FILE_BYTES or `budget`; and how many lines it has uncut."""
    lines = text.splitlines()
    while lines and not lines[0].startswith(("@@", "Binary files")):
        lines.pop(0)
    out, size, cut = [], 0, False
    for line in lines:
        if len(out) >= FILE_LINES or size + len(line) + 1 > min(FILE_BYTES, budget):
            cut = True
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out), cut, len(lines)


def _commit_files(root: str, cfg: dict, sha: str) -> list[dict]:
    """One commit's files: path (old_path when renamed), status, +/- counts, bucket, binary. No diff text yet."""
    status = git.git(root, "show", "--format=", "--name-status", "-M", "-z", sha).stdout.split("\0")
    nums = git.git(root, "show", "--format=", "--numstat", "-M", "-z", sha).stdout.split("\0")
    counts: dict[str, tuple[str, str]] = {}
    i = 0
    while i < len(nums):
        head = nums[i].strip("\n")
        i += 1
        if not head:
            continue
        added, removed, path = (head.split("\t", 2) + ["", ""])[:3]
        if not path:          # a rename: "added\tremoved\t" then the old and the new path
            path = nums[i + 1] if i + 1 < len(nums) else ""
            i += 2
        counts[path] = (added, removed)
    files, i = [], 0
    while i < len(status):
        code = status[i].strip("\n")
        i += 1
        if not code:
            continue
        old = None
        if code[0] in "RC":
            old, path = status[i], status[i + 1] if i + 1 < len(status) else status[i]
            i += 2
        else:
            path = status[i] if i < len(status) else ""
            i += 1
        a, r = counts.get(path, ("0", "0"))
        binary = a == "-" or r == "-"
        f = {"path": path, "status": _STATUS.get(code[0], "modified"), "added": 0 if binary else int(a or 0),
             "removed": 0 if binary else int(r or 0), "bucket": _bucket(cfg, path), "binary": binary}
        if code[0] == "R" and old:
            f["old_path"] = old
        files.append(f)
    return files


def _with_diff(root: str, sha: str, f: dict, budget: int) -> dict:
    """The file with its diff text (`diff`, `lines`: the diff's whole length, `truncated`)."""
    paths = [f["old_path"], f["path"]] if f.get("old_path") else [f["path"]]
    raw = git.git(root, "show", "--format=", "-M", "--no-color", "--no-ext-diff", sha, "--", *paths).stdout or ""
    text, cut, total = _cut_diff(raw, budget)
    return {**f, "diff": text, "lines": total, "truncated": cut}


def ac_code(root: str, ac_id: str, since: str | None = None, sha: str | None = None, path: str | None = None) -> dict:
    """What the web's AC gate shows of the criterion's code: its RED and GREEN commits ({sha, short, subject, files,
    added, removed} or None, found as ac_evidence finds them), each file with its status, +/- counts, a bucket (test,
    prod, generated, lock) and its unified diff (cut, `truncated`). Lock files, generated files and binaries come
    folded: no diff text (`folded`). With `sha` (one of the two commits) and `path`: that one file with its diff, also
    a folded one (the web's Show)."""
    out: dict = {"ac": ac_id, "red": None, "green": None, "commits": 0, "files": 0, "added": 0, "removed": 0,
                 "truncated": False, "branch": None,
                 "limits": {"file_lines": FILE_LINES, "file_bytes": FILE_BYTES, "total_bytes": TOTAL_BYTES}}
    if not git.is_repo(root):
        return out
    cfg = rules.load_config(root)
    budget = TOTAL_BYTES
    paths: set[str] = set()
    for key, prefix in (("red", "test"), ("green", "feat")):
        found = ac_commit(root, prefix, ac_id, since)
        if not found:
            continue
        full = git.git(root, "rev-parse", "--verify", "-q", f"{found[0]}^{{commit}}").stdout.strip() or found[0]
        files = _commit_files(root, cfg, full)
        if sha and path:
            if full.startswith(sha) or found[0].startswith(sha):
                f = next((x for x in files if x["path"] == path), None)
                if f and not f["binary"] and f["bucket"] != "secret":
                    return {"ac": ac_id, "sha": full, "file": _with_diff(root, full, f, FILE_BYTES)}
            continue
        shown, more = [], max(0, len(files) - MAX_FILES)
        for f in files[:MAX_FILES]:
            if f["bucket"] == "secret":
                shown.append({**f, "diff": None, "folded": True, "note": SECRET_NOTE})
            elif f["binary"] or f["bucket"] in ("lock", "generated"):
                shown.append({**f, "diff": None, "folded": True})
            elif budget < MIN_LEFT:
                shown.append({**f, "diff": None, "truncated": True})
                out["truncated"] = True
            else:
                f = _with_diff(root, full, f, budget)
                budget -= len(f["diff"])
                out["truncated"] = out["truncated"] or f["truncated"]
                shown.append(f)
        out[key] = {"sha": full, "short": found[0], "subject": found[1], "files": shown,
                    "added": sum(f["added"] for f in files), "removed": sum(f["removed"] for f in files),
                    **({"more_files": more} if more else {})}
        out["commits"] += 1
        out["added"] += out[key]["added"]
        out["removed"] += out[key]["removed"]
        out["truncated"] = out["truncated"] or bool(more)
        paths.update(f["path"] for f in files)
    if sha and path:
        return {"ac": ac_id, "sha": sha, "file": None}
    out["files"] = len(paths)
    branch = git.git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    out["branch"] = branch if branch and branch != "HEAD" else None
    return out


# ------------------------------------------------------------------ report

def report(a):
    """Every fan-out answer of this flow word for word (one section each) and a tally of their markers. When the step
    just before was a single agent (a ranking, a fix plan), its answer comes first. Shown at the next gate."""
    data = a.data
    keys = [k for k, v in data.items() if k.endswith("_results") and isinstance(v, list)]
    out: list[str] = []
    prev = a.state.get("current")
    # the step's kept answer (keep: true, up to 60 000 characters) before last_answer, which is cut at 8 000: a spike's
    # findings were cut there, and the feature it seeds read only that much
    whole = str(data.get(f"{prev}_text") or "").strip() if prev else ""
    if prev and f"{prev}_results" not in data and (whole or (a.state.get("last_answer") or "").strip()):
        out += [whole or a.state["last_answer"].strip(), ""]
    if data.get("lint_report"):
        out += ["## Static checks", "", str(data["lint_report"]).strip(), ""]
    for key in keys:
        for r in data[key]:
            # 0.18.23 without the agent's tool-call log and narration; an ANGLE REPORT block when it wrote one (the raw
            # answer stays in data.<step>_results and the agent's own log)
            out += [f"## {r.get('title') or r.get('item')}", "", spike_report.angle_text(str(r.get("text") or "")) or "(no answer)", ""]
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
    """The bug report: keel v1 `keel state ac BUG-<n> --current`: the bug's id is the flow's criterion, so its test, its
    commits (test(BUG-<n>), fix(BUG-<n>)) and trace --strict name it. keel v1 --no-gates (data.no_gates, or
    gates.bug_gates: false in .keel/config.yml) waives Gate R and Gate F, recorded in the gate log and the PR body. A
    hunt seed's needs_e2e makes the regression e2e mandatory."""
    cfg = rules.load_config(a.root)
    why = None
    if a.data.get("no_gates"):
        why = "no_gates option"
    elif (cfg.get("gates") or {}).get("bug_gates") is False:
        why = "gates.bug_gates: false"
    update: dict = {"markers": _markers(a, {"E2E": "yes" if a.data.get("needs_e2e") else "no"}), **bug.register(a)}
    found = bug.of(update["acs"]) or {}
    notes = [f"bug report noted as {found.get('id')} [{found.get('layer', 'API')}]"]
    if why:
        gates = shallow_gates(a.state)
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


def revert_red(root: str, ac_id: str, since: str | None = None) -> tuple[bool, str] | None:
    """keel 0.18.69 a skipped criterion's RED test commit (test(<id>), since the flow's start, with no GREEN after it) is
    reverted by a commit of its own, so the next criteria's suite runs do not fail on a test nobody can make pass now (a
    field report: after a skip every later ac_suite failed on it). The test comes back when the criterion is built again.
    None when there is nothing to revert; else (reverted, what happened) for the gate log."""
    if not git.is_repo(root):
        return None
    red = ac_commit(root, "test", ac_id, since)
    if not red or ac_commit(root, "feat", ac_id, since):
        return None
    sha, subject = red
    r = git.git(root, "revert", "--no-edit", "--no-commit", sha)
    if r.returncode != 0:
        git.git(root, "revert", "--abort")
        return False, f"{ac_id}: its RED commit {sha} could not be reverted ({(r.stderr or r.stdout).strip()[:160]}); its test stays"
    msg = (f"revert({ac_id}): skipped, its test comes back when it is built again\n\nThis reverts {sha} ({subject}).\n"
           f"Keel-Ref: revert({ac_id})")
    from .actions import commit_args

    who, trailer = commit_args(root, None, rules.load_config(root))
    c = git.git(root, *who, "commit", "-q", "--no-verify", "-m", msg, *trailer)
    if c.returncode != 0:
        git.git(root, "revert", "--abort")
        git.git(root, "reset", "-q", "--hard", "HEAD")
        return False, f"{ac_id}: its RED commit {sha} could not be reverted ({(c.stderr or c.stdout).strip()[:160]}); its test stays"
    return True, f"{ac_id}: its RED test commit {sha} reverted, so the next criteria's runs do not fail on it"


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
    reason = triage_reason(a.state.get("last_answer") or "")
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


# 0.18.57 what the triage agent says about itself, not about the change (a resumed session: "Same session — keeping
# the criteria from before."); the scope gate showed it above the criteria
_CHATTER = re.compile(r"^\W*(?:same session|this session|new session|still the same session|continuing|resuming|"
                      r"picking up|as before|keeping the (?:criteria|same)|ok(?:ay)?\b|sure\b|got it|understood|alright)",
                      re.I)
_CRITERION = re.compile(r"\b(?:AC-\d+|CHG-\d+\.\d+)\b[*:.)\s\u2013\u2014-]*\[(?:API|WEB|E2E|SMOKE)\]", re.I)
_MARKER = re.compile(r"^\W*(?:SIZE|WHY)\W*:", re.I)
_CRITERIA_HEAD = re.compile(r"^\W*(?:#+\s*)?(?:acceptance\s+)?criteria\W*$", re.I)


def triage_reason(text: str) -> str:
    """The triage answer as the scope gate shows it: why this size and the files, without its own criteria lines (the
    gate lists the criteria once, with keel's CHG ids), its marker lines, a criteria heading left empty, and its talk
    about itself (tool log, "I'll …", "Same session — keeping the criteria …")."""
    out = [line for line in spike_report.clean(text).splitlines()
           if not (_CRITERION.search(line) or _MARKER.match(line) or _CRITERIA_HEAD.match(line)
                   or (_CHATTER.match(line) and len(line) < 240))]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


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
    gates = shallow_gates(a.state)
    gates["mode"] = "end"
    gates["log"] = list(gates.get("log") or [])
    note = f"small change: {len(a.acs)} criteria, one gate at the end"
    if info.get("recommended") == "feature":
        why = ((a.state.get("markers") or {}).get("*") or {}).get("WHY") or "chose small at the scope gate"
        gates["log"].append(f"escalation-override: {why}")
        note += f"; stays small: {why}"
    return _ok(note, update={"gates": gates})


ACTIONS = {"review_scope": review_scope, "report": report, "spike_report": spike_report.spike_report, "investigation_note": investigation_note,
           "bug_intake": bug_intake, "reset": reset, "change_size": change_size, "change_start": change_start,
           "bug_plan": bug.bug_plan, "bug_drop_red": bug.bug_drop_red, "bug_reset": bug.bug_reset}
