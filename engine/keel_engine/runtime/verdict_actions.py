"""Verdict actions (ported from keel v1 lib/verify.js, coverage.js, cover.js, deps.js and ops.js prBody), plus the PR.

    verify_fast       compile / typecheck what changed (commands.api_compile, web_typecheck, contract_lint) + boundaries
    verify_module     the module suites (api_test_module, web_test_module), a failing run rerun once (flaky)
    verify_release    actions.py: the whole suite, rerun once like verify_module (run_suite below)
    verify_coverage   actions.py runs the coverage command; measure() reads jacoco XML / lcov for the changed lines
    verify_deps       manifests changed since the base: the stack's audit command (commands.deps_api / deps_web)
    audit             commit composition and test integrity on the branch
    trace             AC -> test commit -> feat commit, from commit subjects; trace_strict fails an AC without a test commit
    arch              import boundaries (boundaries.rules or architecture.rules)
    pr / open_pr      the PR body; `gh pr create` only with a GitHub token and an approved gate, never a push

Every verdict goes through record_verdict() into the engine's `verdicts` table (runtime/verdicts.py), stamped with
HEAD and the working tree. A check keel cannot run here (no command, no report, no rules) is "not available": the
action passes with that reason and the verdict row says ok false, available false. It never raises.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .. import extensions, rules
from ..rules import checks
from ..tools import git, testcmd
from ..tools.agent_tools import command_env
from . import run_mode as run_modes
from . import tools, verdicts

GAP = 8               # uncovered lines closer than this belong to one group (one test can cover them)
TEST_FILE = re.compile(r"Test\.(kt|java)$|Tests\.(kt|java)$|\.(test|spec)\.(ts|tsx|js|jsx)$|(^|/)test_[^/]+\.py$|_test\.py$")
DISABLED = [(re.compile(r"^\+.*@Disabled", re.M), "@Disabled"), (re.compile(r"^\+.*@Ignore", re.M), "@Ignore"),
            (re.compile(r"^\+.*\.skip\(", re.M), ".skip("), (re.compile(r"^\+.*\.only\(", re.M), ".only("),
            (re.compile(r"^\+.*\bxit\(", re.M), "xit("), (re.compile(r"^\+.*test\.fixme", re.M), "test.fixme"),
            (re.compile(r"^\+.*assumeTrue\(false\)", re.M), "assumeTrue(false)"),
            (re.compile(r"^\+.*@pytest\.mark\.skip\b", re.M), "@pytest.mark.skip")]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def record_verdict(project: str, kind: str, ok: bool | None, detail: dict | None = None, commit: str | None = None,
                   root: str | None = None) -> dict:
    """One verdict row. ok None = the check is not available here (stored as ok false with available false).
    With `root`, the verdict is stamped with HEAD and the working tree (verdicts.fresh accepts either)."""
    detail = dict(detail or {})
    if root and git.is_repo(root):
        head, tree = verdicts.stamp(root)
        commit = commit or head
        detail.setdefault("tree", tree)
    if ok is None:
        detail["available"] = False
    v = verdicts.write(project, kind, bool(ok), detail, commit)
    return {**v, "ok": ok}


def _result(ok: bool | None, note: str, detail: str = "", update: dict | None = None):
    from .actions import ActionResult
    # "not available" passes: the flow goes on, the verdict and the note say the check did not run.
    return ActionResult(ok is not False, note, detail, update or {})


def _tail(text: str, limit: int = 3000) -> str:
    from .actions import tail
    return tail(text, limit)


# ------------------------------------------------------------------ git scope

def base_ref(root: str, cfg: dict, fallback: str | None = None) -> str | None:
    """The branch the work is compared with: base_branch from the config, else main or master, else the thread's start."""
    for ref in [cfg.get("base_branch"), "main", "master"]:
        if ref and git.git(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0:
            if git.git(root, "rev-parse", ref).stdout.strip() != git.head(root) or not fallback:
                return ref
    return fallback


def branch_files(root: str, base: str | None) -> list[str]:
    files: list[str] = []
    if base:
        r = git.git(root, "diff", "--name-only", f"{base}...HEAD")
        files = [f.strip() for f in r.stdout.splitlines() if f.strip()] if r.returncode == 0 else []
    return sorted(set(files) | set(git.dirty(root)))


def changed_lines(root: str, base: str | None) -> dict[str, list[int]]:
    """{file: [line numbers in the new version]} added or changed since base (git diff -U0 base...HEAD)."""
    r = git.git(root, "diff", "-U0", f"{base}...HEAD") if base else None
    diff = r.stdout if r is not None and r.returncode == 0 else git.git(root, "diff", "-U0", "HEAD").stdout
    out: dict[str, list[int]] = {}
    cur = None
    for line in diff.splitlines():
        m = re.match(r"^\+\+\+ b/(.+)$", line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
            continue
        h = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if h and cur:
            start, count = int(h.group(1)), 1 if h.group(2) is None else int(h.group(2))
            out[cur] += list(range(start, start + count))
    return out


def _cwd(root: str, d: str | None) -> tuple[str, str]:
    """The folder a module command runs in (backend.dir / frontend.dir), the root when it does not exist."""
    if d and (Path(root) / d).is_dir():
        return str(Path(root) / d), ""
    return root, (f"keel: {d} does not exist; ran at the repo root.\n" if d else "")


def _run(root: str, cmd: str, cwd: str | None = None, timeout: int = 1800) -> tuple[int, str]:
    return testcmd.run(cwd or root, cmd, timeout, command_env())


def _dir_of(cfg: dict, key: str) -> str | None:
    d = str(((cfg.get(key) or {}).get("dir")) or "").strip().strip("/")
    while d.startswith("./"):
        d = d[2:]
    return None if d in ("", ".") else d      # "." is the repo root: every file belongs to it


def _touched(files: list[str], d: str | None) -> bool:
    return bool(files) if not d else any(f == d or f.startswith(d + "/") for f in files)


# ------------------------------------------------------------------ flaky tests

_FAILED = [re.compile(r"^FAILED\s+(\S+)", re.M), re.compile(r"^\s*(?:✕|×|✖)\s+(.+?)\s*(?:\(\d+\s*m?s\))?$", re.M),
           re.compile(r"^(\S.*?)\s+FAILED\s*$", re.M), re.compile(r"^not ok \d+\s*-?\s*(.+)$", re.M)]


def failed_tests(output: str) -> list[str]:
    """Names of the failing tests in a runner's output (pytest, jest/vitest, gradle, TAP), at most 20."""
    seen: list[str] = []
    for pat in _FAILED:
        for m in pat.findall(output or ""):
            name = m.strip()
            if name and name not in seen:
                seen.append(name)
    return seen[:20]


def run_suite(root: str, cmd: str, label: str, reruns: int = 1, cwd: str | None = None) -> dict:
    """Run a suite; a failing run runs once more. Fail then pass = flaky: it passes, with the tests that failed."""
    code, out = _run(root, cmd, cwd)
    if code == 0 or reruns < 1:
        return {"ok": code == 0, "code": code, "out": out, "flaky": []}
    code2, out2 = _run(root, cmd, cwd)
    if code2 == 0:
        return {"ok": True, "code": 0, "out": out2, "first": out,
                "flaky": [{"label": label, "tests": failed_tests(out) or [cmd], "at": _now()}]}
    return {"ok": False, "code": code2, "out": out2, "flaky": []}


def flaky_update(a, found: list[dict]) -> dict:
    return {"flaky": list(a.state.get("flaky") or []) + found} if found else {}


def _flaky_note(found: list[dict]) -> str:
    names = [t for f in found for t in f["tests"]]
    return f" Warning: flaky, failed once and passed on the rerun: {', '.join(names[:5])}." if names else ""


# ------------------------------------------------------------------ fast + module

async def verify_fast(a):
    return await asyncio.to_thread(_fast, a)


def _fast(a):
    cfg = rules.load_config(a.root)
    cmds = cfg.get("commands") or {}
    if not git.is_repo(a.root):
        record_verdict(a.key, "fast", None, {"reason": "not a git repository: nothing to compare"})
        return _result(None, "Fast check not available: this folder is not a git repository.")
    files = branch_files(a.root, base_ref(a.root, cfg, a.base))
    api, web = _dir_of(cfg, "backend"), _dir_of(cfg, "frontend")
    contract = (cfg.get("contract") or {}).get("file")
    steps = []
    if _touched(files, api if api and (Path(a.root) / api).is_dir() else None) or contract in files:
        steps.append(("api compile", "api_compile", api))
    if web and (Path(a.root) / web).is_dir() and _touched(files, web):
        steps.append(("web typecheck", "web_typecheck", web))
    if contract in files:
        steps.append(("contract lint", "contract_lint", None))
    ran, missing, failed = [], [], None
    for label, key, d in steps:
        cmd = str(cmds.get(key) or "").strip()
        if not cmd:
            missing.append(f"{label} (commands.{key})")
            continue
        cwd, note = _cwd(a.root, d)
        code, out = _run(a.root, cmd, cwd, 900)
        ran.append({"label": label, "command": cmd, "code": code})
        if code != 0:
            failed = (label, cmd, note + out)
            break
    bad = _arch(a.root, cfg, files) if (cfg.get("boundaries") or {}).get("enforce", "off") != "off" else None
    blocked = bool(bad and bad["violations"] and (cfg.get("boundaries") or {}).get("enforce") == "block")
    detail = {"files": len(files), "steps": ran, "missing": missing}
    if bad and bad["violations"]:
        detail["boundaries"] = bad["violations"][:20]
    if failed:
        record_verdict(a.key, "fast", False, {**detail, "summary": f"{failed[0]} failed"}, root=a.root)
        return _result(False, f"Fast check: {failed[0]} failed.", f"$ {failed[1]}\n{_tail(failed[2])}")
    if blocked:
        lines = "\n".join(f"  {v['file']}:{v['line']} imports {v['target']} ({v['rule']})" for v in bad["violations"][:20])
        record_verdict(a.key, "fast", False, {**detail, "summary": "architecture boundaries crossed"}, root=a.root)
        return _result(False, "Fast check: architecture boundaries crossed.", lines)
    if not ran:
        why = ("nothing compiles for this change" if not steps else "no command configured: " + ", ".join(missing))
        record_verdict(a.key, "fast", None if missing else True, {**detail, "reason": why}, root=a.root)
        return _result(None if missing else True, f"Fast check: {why}.")
    warn = f" {len(bad['violations'])} boundary warning(s)." if bad and bad["violations"] else ""
    skipped = f" Not run: {', '.join(missing)}." if missing else ""
    record_verdict(a.key, "fast", True, {**detail, "summary": f"{len(ran)} step(s) passed"}, root=a.root)
    return _result(True, f"Fast check: {', '.join(r['label'] for r in ran)} passed.{skipped}{warn}")


async def verify_module(a):
    return await asyncio.to_thread(_module, a)


def _module(a):
    cfg = rules.load_config(a.root)
    cmds = cfg.get("commands") or {}
    reruns = int((cfg.get("loops") or {}).get("flaky_reruns", 1) or 0)
    suites = []
    api_cmd = testcmd.command_for(a.root, None, "API")
    if api_cmd:
        suites.append(("api module suite", api_cmd, _dir_of(cfg, "backend") if cmds.get("api_test_module") else None))
    web = _dir_of(cfg, "frontend")
    if web and (Path(a.root) / web).is_dir() and cmds.get("web_test_module"):
        suites.append(("web module suite", cmds["web_test_module"], web))
    if not suites:
        record_verdict(a.key, "module", None, {"reason": "no test command (commands.api_test_module / web_test_module)"}, root=a.root)
        return _result(None, "Module suites not available: no test command found. Add commands.api_test_module to .keel/config.yml.")
    if a.fake:
        return _result(True, "Module suites: green (simulated, no verdict written).")
    flaky, ran = [], []
    for label, cmd, d in suites:
        cwd, _note = _cwd(a.root, d)
        r = run_suite(a.root, cmd, label, reruns, cwd)
        ran.append({"label": label, "command": cmd, "ok": r["ok"]})
        flaky += r["flaky"]
        if not r["ok"]:
            record_verdict(a.key, "module", False, {"suites": ran, "summary": f"{label} failed",
                                                   "failed": failed_tests(r["out"])}, root=a.root)
            return _result(False, f"The {label} fails.", f"$ {cmd}\n{_tail(r['out'])}", flaky_update(a, flaky))
    record_verdict(a.key, "module", True, {"suites": ran, "flaky": flaky,
                                          "summary": f"{len(ran)} suite(s) passed" + (" (flaky)" if flaky else "")}, root=a.root)
    return _result(True, f"Module suites: {len(ran)} green.{_flaky_note(flaky)}", update=flaky_update(a, flaky))


# ------------------------------------------------------------------ coverage

def parse_jacoco(xml: str) -> dict[str, dict[int, dict]]:
    """Kover / JaCoCo XML: {package/path/File.kt: {line: {covered, branches, branches_covered}}}."""
    files: dict[str, dict[int, dict]] = {}
    for pkg in re.finditer(r"<package[^>]*name=\"([^\"]*)\"[^>]*>(.*?)</package>", xml, re.S):
        prefix = pkg.group(1).replace(".", "/")
        for sf in re.finditer(r"<sourcefile[^>]*name=\"([^\"]*)\"[^>]*>(.*?)</sourcefile>", pkg.group(2), re.S):
            lines = {}
            for ln in re.finditer(r"<line\b([^>]*)/>", sf.group(2)):
                attrs = dict(re.findall(r"(\w+)=\"(\d+)\"", ln.group(1)))
                if "nr" not in attrs:
                    continue
                mb, cb = int(attrs.get("mb", 0)), int(attrs.get("cb", 0))
                lines[int(attrs["nr"])] = {"covered": int(attrs.get("ci", 0)) > 0, "branches": mb + cb, "branches_covered": cb}
            files[(prefix + "/" if prefix else "") + sf.group(1)] = lines
    return files


def parse_lcov(text: str) -> dict[str, dict[int, dict]]:
    """LCOV: SF:path, DA:line,hits, BRDA:line,block,branch,taken."""
    files: dict[str, dict[int, dict]] = {}
    cur = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            cur = line[3:].replace("\\", "/")
            files.setdefault(cur, {})
        elif line.startswith("DA:") and cur is not None:
            nr, hits = (line[3:].split(",") + ["0"])[:2]
            e = files[cur].setdefault(int(nr), {"covered": False, "branches": 0, "branches_covered": 0})
            e["covered"] = e["covered"] or int(float(hits or 0)) > 0
        elif line.startswith("BRDA:") and cur is not None:
            parts = line[5:].split(",")
            e = files[cur].setdefault(int(parts[0]), {"covered": False, "branches": 0, "branches_covered": 0})
            e["branches"] += 1
            if len(parts) > 3 and parts[3] not in ("-", "") and int(parts[3]) > 0:
                e["branches_covered"] += 1
        elif line == "end_of_record":
            cur = None
    return files


def lookup(coverage: dict, repo_file: str) -> dict | None:
    """Coverage keys are partial or absolute paths: match them to a repo path by suffix."""
    norm = repo_file.replace("\\", "/")
    if norm in coverage:
        return coverage[norm]
    for k, v in coverage.items():
        if norm.endswith("/" + k) or k.endswith("/" + norm):
            return v
    return None


def load_report(root: str, rel: str) -> dict | None:
    p = Path(root) / rel
    if not p.is_file():
        return None
    text = p.read_text(errors="replace")
    return parse_jacoco(text) if text.lstrip().startswith("<") else parse_lcov(text)


def group(uncovered: list[str], gap: int = GAP) -> list[dict]:
    """Uncovered `file:line`s as work items: same file, lines at most `gap` apart. Largest first."""
    by_file: dict[str, list[int]] = {}
    for u in uncovered:
        m = re.match(r"^(.*):(\d+)$", str(u))
        if m:
            by_file.setdefault(m.group(1), []).append(int(m.group(2)))
    items = []
    for f, lines in by_file.items():
        lines.sort()
        run = [lines[0]]
        for n in lines[1:]:
            if n - run[-1] <= gap:
                run.append(n)
            else:
                items.append({"file": f, "lines": run})
                run = [n]
        items.append({"file": f, "lines": run})
    for it in items:
        it["key"] = f"{it['file']}:{it['lines'][0]}-{it['lines'][-1]}"
    return sorted(items, key=lambda x: (-len(x["lines"]), x["file"]))


def _pct(n: int, d: int) -> float | None:
    return round(n * 1000 / d) / 10 if d else None


def measure_app(app: str, coverage: dict, changed: dict[str, list[int]], critical: list[str], exclude: list[str]) -> dict:
    executable = covered = branches = branches_covered = 0
    uncovered, crit_uncovered = [], []
    crit_exec = crit_cov = 0
    for f, lines in changed.items():
        if exclude and rules.match_glob(f, exclude):
            continue
        cov = lookup(coverage, f)
        if not cov:
            continue
        is_crit = bool(critical) and rules.match_glob(f, critical)
        for nr in lines:
            e = cov.get(nr)
            if not e:
                continue        # not an executable line
            executable += 1
            covered += e["covered"]
            if not e["covered"]:
                uncovered.append(f"{f}:{nr}")
            if is_crit:
                crit_exec += 1
                crit_cov += e["covered"]
                if not e["covered"]:
                    crit_uncovered.append(f"{f}:{nr}")
            branches += e["branches"]
            branches_covered += e["branches_covered"]
    total = sum(len(v) for v in coverage.values())
    total_cov = sum(1 for v in coverage.values() for e in v.values() if e["covered"])
    return {"app": app, "changed_executable": executable, "changed_covered": covered, "changed_pct": _pct(covered, executable),
            "branch_pct": _pct(branches_covered, branches), "global_pct": _pct(total_cov, total), "uncovered": uncovered,
            "critical_executable": crit_exec, "critical_covered": crit_cov, "critical_uncovered": crit_uncovered}


def reports_of(cfg: dict) -> dict[str, str]:
    reports = (cfg.get("coverage") or {}).get("reports") or {}
    return {k: v for k, v in reports.items() if isinstance(v, str) and v.strip()} if isinstance(reports, dict) else {}


def measure(root: str, cfg: dict, base: str | None) -> dict:
    """keel v1 coverage.run + cover.measure: changed lines per app, the problems, and the uncovered groups."""
    cov = cfg.get("coverage") or {}
    reports = reports_of(cfg)
    critical = list(((cfg.get("security") or {}).get("coverage_paths")) or [])
    changed = changed_lines(root, base)
    results = []
    for app, dir_key in (("api", "backend"), ("web", "frontend")):
        rel = reports.get(app)
        if not rel:
            continue
        d = _dir_of(cfg, dir_key)
        scoped = {f: ls for f, ls in changed.items()
                  if not d or not (Path(root) / d).is_dir() or f == d or f.startswith(d + "/")}
        if not scoped:
            continue
        data = load_report(root, rel)
        if data is None:
            results.append({"app": app, "error": f"coverage report not found: {rel}"})
            continue
        results.append(measure_app(app, data, scoped, critical, list(((cov.get("exclude") or {}).get(app)) or [])))
    problems = []
    for r in results:
        if r.get("error"):
            problems.append(r["error"])
            continue
        want = cov.get("changed_lines")
        if r["changed_pct"] is not None and isinstance(want, (int, float)) and r["changed_pct"] < want:
            problems.append(f"{r['app']}: changed lines {r['changed_pct']}% < {want}%")
        if r["critical_executable"] and r["critical_covered"] < r["critical_executable"]:
            problems.append(f"{r['app']}: {r['critical_executable'] - r['critical_covered']} uncovered line(s) on a security path "
                            f"(security.coverage_paths needs 100%): {', '.join(r['critical_uncovered'][:5])}")
        want_b = cov.get("changed_branches")
        if r["branch_pct"] is not None and isinstance(want_b, (int, float)) and r["branch_pct"] < want_b:
            problems.append(f"{r['app']}: changed branches {r['branch_pct']}% < {want_b}%")
        g = cov.get("global")
        if isinstance(g, (int, float)) and not isinstance(g, bool) and r["global_pct"] is not None and r["global_pct"] < g:
            problems.append(f"{r['app']}: global coverage {r['global_pct']}% < {g}%")
    groups = []
    for r in results:
        if r.get("error"):
            continue
        for it in group(r["uncovered"]):
            groups.append({**it, "id": it["key"], "app": r["app"], "critical": bool(critical) and rules.match_glob(it["file"], critical),
                           "title": f"{it['file']}:{it['lines'][0]}" + (f"-{it['lines'][-1]}" if len(it["lines"]) > 1 else "")
                                    + f" ({len(it['lines'])} line{'s' if len(it['lines']) > 1 else ''})"})
    groups.sort(key=lambda x: not x["critical"])     # security paths first: one line there fails the gate
    ok = not problems and bool(results)
    summary = "; ".join(f"{r['app']} changed {'n/a' if r['changed_pct'] is None else str(r['changed_pct']) + '%'}, "
                        f"global {'n/a' if r['global_pct'] is None else str(r['global_pct']) + '%'}" for r in results if not r.get("error"))
    if not results:
        # Nothing measured: no changed line inside a measured app passes (nothing could regress).
        ok, summary = True, ("no changed lines against " + (base or "the base") if not changed else "no changed lines inside a measured app")
    for r in results:
        r.pop("uncovered", None)
    return {"base": base, "ok": ok, "apps": results, "problems": problems, "summary": summary, "groups": groups}


def coverage_from_reports(a) -> tuple[dict, object]:
    """The changed-line verdict from the configured reports; returns (measurement, ActionResult)."""
    cfg = rules.load_config(a.root)
    m = measure(a.root, cfg, base_ref(a.root, cfg, a.base))
    detail = {k: m[k] for k in ("base", "apps", "problems", "summary")}
    detail["groups"] = [{k: g[k] for k in ("key", "file", "lines", "app", "critical")} for g in m["groups"][:50]]
    if not m["ok"]:
        detail["summary"] = "; ".join(m["problems"])[:300]
    record_verdict(a.key, "coverage", m["ok"], detail, root=a.root)
    # keel v1 cover.json: decisions carry across rounds, so an accepted group is not proposed again.
    accepted = {str(x.get("key") or x.get("id")): x for x in a.data.get("coverage_accepted") or [] if isinstance(x, dict)}
    for g in m["groups"]:
        if g["key"] in accepted:
            g.update(status="skipped", decision="accept", reason=accepted[g["key"]].get("reason"))
    still = [g for g in m["groups"] if g.get("status") != "skipped"]
    # The stall fingerprint is the set of groups still open: a round that does not move it says so.
    fp = "|".join(sorted(g["key"] for g in still))
    stalled = bool(still) and fp == a.data.get("coverage_fingerprint")
    upd = {"data": {**a.data, "coverage_groups": m["groups"], "coverage_fingerprint": fp, "coverage_error": None}}
    open_groups = "\n".join(f"  {'! ' if g['critical'] else '  '}{g['title']}" for g in still[:20])
    stall = " The same lines are still uncovered as in the last round: change the approach, do not just try again." if stalled else ""
    if not m["ok"]:
        return m, _result(False, ("Coverage: " + "; ".join(m["problems"]))[:300] + stall,
                          (m["summary"] + "\n\nUncovered groups:\n" + open_groups).strip(), upd)
    more = f" {len(still)} uncovered group(s) left." if still else ""
    return m, _result(True, f"Coverage: {m['summary']}; verdict recorded.{more}", open_groups, upd)


# ------------------------------------------------------------------ deps

async def verify_deps(a):
    return await asyncio.to_thread(_deps, a)


def _deps(a):
    cfg = rules.load_config(a.root)
    if not git.is_repo(a.root):
        record_verdict(a.key, "deps", None, {"reason": "not a git repository"})
        return _result(None, "Dependency check not available: not a git repository.")
    changed = [f for f in branch_files(a.root, base_ref(a.root, cfg, a.base)) if rules.match_glob(f, checks.DEP_FILES)]
    if not changed:
        record_verdict(a.key, "deps", True, {"summary": "no manifest or lockfile changed on this branch", "manifests": []}, root=a.root)
        return _result(True, "Dependencies: no manifest or lockfile changed; nothing to check.")
    cmds = cfg.get("commands") or {}
    audits = [(lane, str(cmds.get(f"deps_{lane}") or "").strip(), _dir_of(cfg, d)) for lane, d in (("api", "backend"), ("web", "frontend"))]
    audits = [x for x in audits if x[1]]
    if not audits:
        why = "no audit command (commands.deps_api / deps_web in .keel/config.yml)"
        record_verdict(a.key, "deps", None, {"reason": why, "manifests": changed}, root=a.root)
        return _result(None, f"Dependencies: {len(changed)} manifest(s) changed, but the check is not available: {why}.",
                       "\n".join(changed))
    failed, ran = [], []
    for lane, cmd, d in audits:
        cwd, _ = _cwd(a.root, d)
        code, out = _run(a.root, cmd, cwd, 600)
        ran.append({"lane": lane, "command": cmd, "code": code})
        if code != 0:
            failed.append((lane, cmd, out))
    ok = not failed
    record_verdict(a.key, "deps", ok, {"manifests": changed, "audits": ran,
                                       "summary": "audit passed" if ok else f"{failed[0][0]} audit failed"}, root=a.root)
    if not ok:
        return _result(False, f"Dependencies: the {failed[0][0]} audit fails.", f"$ {failed[0][1]}\n{_tail(failed[0][2])}")
    return _result(True, f"Dependencies: {len(changed)} manifest(s) changed; {len(ran)} audit(s) passed.")


# ------------------------------------------------------------------ audit

async def audit(a):
    return await asyncio.to_thread(_audit, a)


def audit_problems(root: str, cfg: dict, base: str | None, unlocks: list[dict]) -> list[str]:
    """keel v1 verify.audit: test commits hold no production code, feat/fix commits no tests (paired style), no new
    skip/only/disabled markers, every unlock has a reason, spike branches do not ship."""
    problems = []
    single = (cfg.get("loops") or {}).get("commit_style") == "single" or (cfg.get("commit") or {}).get("commit_style") == "single"
    log = git.git(root, "log", f"{base}..HEAD", "--format=%H%x09%s").stdout.splitlines() if base else []
    for line in log:
        sha, _, subject = line.partition("\t")
        files = [f for f in git.git(root, "show", "--name-only", "--format=", sha).stdout.splitlines() if f.strip()]
        buckets = [rules.classify(cfg, f) for f in files]
        if subject.startswith("test(") and any(b in ("api-main", "web-src") for b in buckets):
            problems.append(f"{sha[:7]} {subject}: a test commit contains production code")
        # Repair phases (review-fix, coverage-fix, security, ship) may change code and tests together, like the guard
        # allows there; the paired separation only applies to criterion and bug commits.
        repair = re.match(r"^fix\((review|coverage|security|ship)\)", subject)
        if re.match(r"^(feat|fix)\(", subject) and not repair and not single and any(b in ("api-test", "web-test") for b in buckets):
            problems.append(f"{sha[:7]} {subject}: an implementation commit contains test files")
        diff = git.git(root, "show", "--format=", sha).stdout
        found = [name for rx, name in DISABLED if rx.search(diff)]
        if found:
            problems.append(f"{sha[:7]} {subject}: adds {', '.join(found)}")
    for u in unlocks or []:
        if not u.get("reason") and u.get("by") != "settings":
            problems.append(f"unlock of {u.get('path')} in {u.get('phase')} has no reason")
    if (git.branch(root) or "").startswith("spike/"):
        problems.append("spike branches cannot be shipped")
    return problems


def _audit(a):
    if not git.is_repo(a.root):
        record_verdict(a.key, "audit", None, {"reason": "not a git repository"})
        return _result(None, "Audit not available: not a git repository.")
    cfg = rules.load_config(a.root)
    base = base_ref(a.root, cfg, a.base)
    problems = audit_problems(a.root, cfg, base, a.unlocks)
    n = len(git.git(a.root, "log", f"{base}..HEAD", "--format=%H").stdout.split()) if base else 0
    record_verdict(a.key, "audit", not problems, {"base": base, "commits": n, "problems": problems,
                                                  "summary": f"{len(problems)} problem(s)" if problems else f"{n} commit(s) clean"}, root=a.root)
    if problems:
        return _result(False, f"Audit: {len(problems)} problem(s) on the branch.", "\n".join(problems))
    return _result(True, f"Audit: {n} commit(s) since {base or 'the start'}, no problems.")


# ------------------------------------------------------------------ trace

def trace_rows(root: str, acs: list[dict], base: str | None = None) -> list[dict]:
    """AC -> test commit (test(AC-n)) -> implementation commit (feat(AC-n) / fix(AC-n)), from the commit subjects."""
    rng = [f"{base}..HEAD"] if base else ["--max-count=500"]
    log = [l.partition("\t") for l in git.git(root, "log", *rng, "--format=%H%x09%s").stdout.splitlines() if l.strip()]
    rows = []
    for ac in acs or []:
        aid = ac["id"]
        red = next((sha for sha, _, s in log if f"test({aid})" in s), None)
        green = next((sha for sha, _, s in log if f"feat({aid})" in s or f"fix({aid})" in s), None)
        met = ac.get("status") == "already-met"
        rows.append({"id": aid, "layer": ac.get("layer", "API"), "title": ac.get("title", ""), "status": ac.get("status", "todo"),
                     "test": red[:7] if red else None, "commit": green[:7] if green else None,
                     "complete": bool(red) and (bool(green) or met)})
    return rows


def trace_table(rows: list[dict]) -> str:
    out = ["| AC | layer | status | test commit | implementation |", "|---|---|---|---|---|"]
    out += [f"| {r['id']} | {r['layer']} | {r['status']} | {r['test'] or '—'} | {r['commit'] or '—'} |" for r in rows]
    return "\n".join(out)


async def trace(a, strict: bool = False):
    return await asyncio.to_thread(_trace, a, strict)


def _trace(a, strict: bool):
    if not git.is_repo(a.root):
        record_verdict(a.key, "trace", None, {"reason": "not a git repository"})
        return _result(None, "Trace not available: not a git repository.")
    if not a.acs:
        record_verdict(a.key, "trace", None, {"reason": "this flow has no acceptance criteria"}, root=a.root)
        return _result(None, "Trace: this flow has no acceptance criteria to trace.")
    cfg = rules.load_config(a.root)
    rows = trace_rows(a.root, a.acs, base_ref(a.root, cfg, a.base))
    untested = [r["id"] for r in rows if not r["test"] and r["status"] != "already-met"]
    unbuilt = [r["id"] for r in rows if r["test"] and not r["commit"] and r["status"] != "already-met"]
    ok = not untested if strict else True
    summary = (f"{len(rows) - len(untested)}/{len(rows)} AC(s) have a test commit"
               + (f"; no test commit: {', '.join(untested)}" if untested else "")
               + (f"; no implementation commit: {', '.join(unbuilt)}" if unbuilt else ""))
    record_verdict(a.key, "trace", ok, {"rows": rows, "strict": strict, "summary": summary}, root=a.root)
    return _result(ok, f"Trace{' (strict)' if strict else ''}: {summary}.", trace_table(rows), {"data": {**a.data, "trace": rows}})


# ------------------------------------------------------------------ arch

def import_target(line: str) -> str | None:
    m = re.search(r"from\s+['\"]([^'\"]+)['\"]", line) or re.match(r"^\s*import\s+['\"]([^'\"]+)['\"]", line)
    if m:
        return m.group(1)
    m = re.match(r"^\s*import\s+([\w.*]+)", line)
    return m.group(1) if m else None


def boundary_rules(cfg: dict) -> list[dict]:
    """boundaries.rules (keel v1), or architecture.rules / architecture.layers in the same shape."""
    for block, key in (("boundaries", "rules"), ("architecture", "rules"), ("architecture", "layers")):
        got = (cfg.get(block) or {}).get(key) if isinstance(cfg.get(block), dict) else None
        if isinstance(got, list) and got:
            return [r for r in got if isinstance(r, dict) and r.get("from")]
    return []


def _arch(root: str, cfg: dict, files: list[str]) -> dict | None:
    """keel v1 archViolations: a grep over the import lines of the changed files only (cheap enough for every check)."""
    rls = boundary_rules(cfg)
    if not rls:
        return None
    out, inspected = [], 0
    for rel in files:
        if not re.search(r"\.(kt|java|ts|tsx|js|jsx)$", rel):
            continue
        try:
            text = (Path(root) / rel).read_text(errors="replace")
        except OSError:
            continue
        inspected += 1
        imports = [(l, n + 1) for n, l in enumerate(text.splitlines()) if re.match(r"^\s*import\s|^\s*from\s+['\"]", l)]
        for rule in rls:
            if not rules.match_glob(rel, [rule["from"]]):
                continue
            for line, no in imports:
                target = import_target(line)
                if not target:
                    continue
                denied = rules.match_glob(target, rule.get("deny_imports") or [])
                allowed = rules.match_glob(target, rule.get("allow_imports") or []) if rule.get("allow_imports") else False
                if denied and not allowed:
                    out.append({"file": rel, "line": no, "rule": rule.get("name") or "boundary", "target": target})
    return {"violations": out, "inspected": inspected}


async def arch(a):
    return await asyncio.to_thread(_arch_action, a)


def _arch_action(a):
    cfg = rules.load_config(a.root)
    if not boundary_rules(cfg):
        why = "no architecture rules (boundaries.rules or architecture.rules in .keel/config.yml)"
        record_verdict(a.key, "arch", None, {"reason": why}, root=a.root if git.is_repo(a.root) else None)
        return _result(None, f"Architecture check not available: {why}.")
    files = branch_files(a.root, base_ref(a.root, cfg, a.base)) if git.is_repo(a.root) else []
    r = _arch(a.root, cfg, files) or {"violations": [], "inspected": 0}
    bad = r["violations"]
    record_verdict(a.key, "arch", not bad, {"violations": bad[:50], "inspected": r["inspected"],
                                            "summary": f"{len(bad)} boundary violation(s)" if bad else f"{r['inspected']} file(s) clean"},
                   root=a.root)
    if bad:
        return _result(False, f"Architecture: {len(bad)} boundary violation(s).",
                       "\n".join(f"  {v['file']}:{v['line']} imports {v['target']} ({v['rule']})" for v in bad[:30]))
    return _result(True, f"Architecture: {r['inspected']} changed file(s) inspected, no boundary crossed.")


# ------------------------------------------------------------------ PR

def pr_body(root: str, project: str, state: dict, title: str, base: str | None, request: str = "", run_mode: str = "",
            thread_id: str = "") -> str:
    """keel v1 ops.prBody: spec extract, trace table, coverage verdict, skipped gates and ship steps, unlocks,
    accepted coverage lines, flaky tests; v0.4.1: the gates the run mode approved by itself."""
    spec = state.get("spec")
    extract = ""
    if spec and (Path(root) / spec).is_file():
        extract = "\n".join((Path(root) / spec).read_text(errors="replace").splitlines()[:12])
    rows = trace_rows(root, state.get("acs") or [], base) if git.is_repo(root) else []
    cov = verdicts.latest(project, "coverage")
    gates = state.get("gates") or {}
    data = state.get("data") or {}
    skipped = [f"- {k}: {v}" for k, v in (gates.get("skipped") or {}).items()]
    skipped += [f"- {line}" for line in gates.get("log") or []
                if "no gate here" in line or "accepted:" in line or line.startswith("escalation-override")]
    auto = [f"- {line}" for line in gates.get("log") or [] if run_modes.is_auto_line(line)]
    out = [f"# {title}", ""] + ([f"Spec: `{spec}`", ""] if spec else [])
    if extract:
        out += ["<details><summary>Spec extract</summary>", "", *tools.fenced(extract, "markdown"), "</details>", ""]
    if not extract:
        # What was asked and what the branch did (the last agent's answer is about its own step, not the branch).
        commits = git.git(root, "log", "--reverse", "--format=- %s", f"{base}..HEAD").stdout.strip() \
            if base and git.is_repo(root) else ""
        out += ["## Summary", ""] + ([request.strip()[:1500], ""] if request.strip() else []) \
            + (["Commits:", commits[:3000], ""] if commits else [])
    if rows:
        out += ["## Acceptance criteria", "", trace_table(rows), ""]
    out += ["## Coverage", ""]
    if not cov:
        out.append("No coverage verdict.")
    elif (cov.get("detail") or {}).get("available") is False:
        out.append(f"Not measured: {(cov.get('detail') or {}).get('reason') or 'not available'}.")
    else:
        d = cov.get("detail") or {}
        out.append(f"Pass — {d.get('summary') or 'coverage met'}" if cov.get("ok") else f"FAIL — {'; '.join(d.get('problems') or []) or d.get('summary')}")
    out.append("")
    out += lint_section(project)
    accepted = data.get("coverage_accepted") or []
    if accepted:
        out += ["## Uncovered lines accepted", ""] + [f"- `{x.get('key') or x.get('id')}`: {x.get('reason') or 'no reason given'}"
                                                     for x in accepted if isinstance(x, dict)] + [""]
    if skipped:
        out += ["## Skipped gates", ""] + skipped + [""]
    if auto or run_mode in ("important", "auto"):
        out += ["## Auto-approved gates", ""] + ([f"Run mode: {run_mode}.", ""] if run_mode else []) \
            + (auto or ["None so far."]) + [""]
    ship_skipped = data.get("ship_skipped") or []
    if ship_skipped:
        out += ["## Ship steps skipped", ""] + [f"- {s.get('step')}: {s.get('reason') or 'no reason given'}" for s in ship_skipped
                                                if isinstance(s, dict)] + [""]
    unlocks = [u for u in state.get("unlocks") or [] if u.get("by") != "settings" or u.get("reason")]
    if unlocks:
        out += ["## Unlocks used", ""] + [f"- `{u.get('path')}` in {u.get('phase')}: {u.get('reason') or 'no reason given'}"
                                          for u in unlocks] + [""]
    flaky = state.get("flaky") or []
    if flaky:
        out += ["## Flaky tests seen", ""] + [f"- {f.get('label')}: {', '.join(f.get('tests') or [])}" for f in flaky] + [""]
    out += extensions.pr_body_sections(thread_id)      # the parts' sections: KeelBot's commits at a gate
    out += ["---", "_Prepared by keel._"]
    return "\n".join(out)


def lint_section(project: str) -> list[str]:
    """The PR body's static checks: the lint verdict, one line per tool."""
    v = verdicts.latest(project, "lint")
    out = ["## Static checks", ""]
    if not v:
        return out + ["Not run.", ""]
    d = v.get("detail") or {}
    if d.get("available") is False:
        return out + [f"Not available: {d.get('reason') or 'no tool could run'}.", ""]
    out.append(f"Pass — {d.get('summary')}" if v.get("ok") else f"FAIL — {d.get('summary')}")
    for name, t in sorted((d.get("tools") or {}).items()):
        res = "not available" if not t.get("available", True) else ("pass" if t.get("ok") else
                                                                    "warning" if t.get("fail") == "warn" else "fail")
        out.append(f"- {name} ({t.get('on')}, {t.get('fail')}): {res}")
    return out + [""]


async def pr(a):
    return await asyncio.to_thread(_pr, a)


def _pr(a):
    cfg = rules.load_config(a.root)
    base = base_ref(a.root, cfg, a.base) if git.is_repo(a.root) else None
    body = pr_body(a.root, a.key, a.state, a.title, base, getattr(a, "request", "") or "",
                   run_modes.normalize((a.settings or {}).get("run_mode")), a.thread_id)
    return _result(True, f"PR body ready ({len(body.splitlines())} lines); it is shown at the next gate.", body, {"pr_body": body})


def github_token(keys: dict) -> str | None:
    for k in ("github", "copilot", "gh"):
        if (keys or {}).get(k):
            return keys[k]
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


async def open_pr(a):
    return await asyncio.to_thread(_open_pr, a)


def _open_pr(a):
    """`gh pr create` with the body, only with a GitHub token and after the user approved the gate before this step.
    keel never pushes: an unpushed branch is reported, not pushed."""
    body = a.state.get("pr_body") or ""
    copy = "Copy the PR body (shown at the gate) into your PR."
    if not body:
        return _result(False, "No PR body yet: run the pr step first.")
    if not a.state.get("pr_approved"):
        return _result(True, f"The PR gate was not approved; no PR opened. {copy}", body)
    if run_modes.normalize((a.settings or {}).get("run_mode")) == "auto" or a.state.get("pr_auto"):
        # Run mode auto never opens a PR: a person reads the body and opens it (keel never pushes either).
        return _result(True, f"Run mode auto: keel opens no PR by itself. {copy}", body)
    token = github_token(a.keys)
    if not token:
        return _result(True, f"No GitHub token for the engine (Connections › GitHub); no PR opened. {copy}", body)
    if not git.is_repo(a.root):
        return _result(True, f"Not a git repository; no PR opened. {copy}", body)
    branch = git.branch(a.root) or ""
    up = git.git(a.root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up.returncode != 0:
        return _result(True, f"The branch {branch} is not pushed, and keel never pushes. Push it (git push -u origin {branch}), "
                             f"then open the PR. {copy}", body)
    ahead = git.git(a.root, "rev-list", "--count", "@{u}..HEAD").stdout.strip()
    if ahead not in ("", "0"):
        return _result(True, f"{ahead} commit(s) on {branch} are not pushed, and keel never pushes. Push them, then open the PR. "
                             f"{copy}", body)
    title = Path(a.state.get("spec") or "").stem or a.title or branch
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body)
        path = f.name
    try:
        env = {**command_env(), "GH_TOKEN": token}
        cmd = f"gh pr create --title {shlex.quote(title)} --body-file {shlex.quote(path)}"
        code, out = testcmd.run(a.root, cmd, 120, env)
    finally:
        Path(path).unlink(missing_ok=True)
    if code != 0:
        return _result(True, f"gh pr create failed; no PR opened. {copy}", f"$ {cmd}\n{_tail(out, 1500)}\n\n{body}")
    url = next((l for l in out.splitlines() if l.startswith("http")), out.strip()[:200])
    return _result(True, f"PR opened: {url}", out, {"data": {**a.data, "pr_url": url}})
