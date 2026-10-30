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
import re
import shlex
import tempfile
from pathlib import Path

from .. import extensions, rules
from ..rules import checks
from ..tools import git, testcmd
from ..tools.agent_tools import command_env
from . import run_mode as run_modes
from . import tools, verdicts
from . import bug
from . import commit_style
from . import test_scope

GAP = 8               # uncovered lines closer than this belong to one group (one test can cover them)
TEST_FILE = checks.TEST_FILE
DISABLED = checks.DISABLED


from ..clock import utc_iso as _now


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

def _exists(root: str, ref: str) -> bool:
    return git.git(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0


def remote_of(root: str, name: str) -> str | None:
    """0.18.57 the remote-tracking ref of a plain local branch name: its upstream (main@{upstream}), else origin/<name>;
    None for an explicit ref (origin/main, refs/..., a tag or a sha) or a branch with no remote copy. 0.18.65 the
    project's canonical remote (runtime/bases.py) is asked first when .keel/config.yml names it (git.remote), and
    comes before origin."""
    from . import bases

    if not name or "/" in name or not git.git(root, "show-ref", "--verify", "-q", f"refs/heads/{name}").returncode == 0:
        return None
    canonical = bases.canonical_remote(root)
    if canonical and bases.setting(rules.load_config(root)) == canonical and _exists(root, f"{canonical}/{name}"):
        return f"{canonical}/{name}"
    up = git.git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{name}@{{upstream}}")
    if up.returncode == 0 and up.stdout.strip() and _exists(root, up.stdout.strip()):
        return up.stdout.strip()
    for r in dict.fromkeys([canonical, "origin"]):
        if r and _exists(root, f"{r}/{name}"):
            return f"{r}/{name}"
    return None


def flow_base(a, cfg: dict) -> str | None:
    """0.18.74 where this flow's own work starts: on a branch that already had commits of its own when the flow started
    (a flow that continues a pull request's branch), the flow's start (data.own_since); else base_ref. A field report:
    a flow on a PR branch reviewed, audited and traced all 41 commits of the branch, not its own."""
    own = (getattr(a, "data", None) or {}).get("own_since")
    if own and git.git(a.root, "rev-parse", "--verify", "-q", f"{own}^{{commit}}").returncode == 0:
        return own
    return base_ref(a.root, cfg, a.base)


def base_ref(root: str, cfg: dict, fallback: str | None = None) -> str | None:
    """The branch the work is compared with: base_branch from the config, else main or master, else the thread's start.

    0.18.57 a plain branch name is compared through its remote-tracking ref when there is one (origin/main): a local
    main that is behind or diverged made commits merged upstream through a PR look like this branch's own work (the
    audit refused them, and every retry refused them again). An explicit ref in the config (origin/x, a tag) is kept.
    0.18.65 first the project's base as runtime/bases.py resolves it: the canonical remote's copy (base_branch may be
    <remote>/<branch>, and a base_branch with no local branch is compared with the remote's copy)."""
    from . import bases

    # a full ref (refs/heads/main) is the person's explicit choice: kept as it is
    pinned = str(cfg.get("base_branch") or "").startswith("refs/")
    first = bases.resolve(root, cfg).get("ref") if git.is_repo(root) and not pinned else None
    for ref in dict.fromkeys([first, cfg.get("base_branch"), "main", "master"]):
        if not ref:
            continue
        ref = remote_of(root, ref) or ref
        if _exists(root, ref):
            if git.git(root, "rev-parse", ref).stdout.strip() != git.head(root) or not fallback:
                return ref
    return fallback


FETCH_TIMEOUT = 30


def _base_remote(root: str, cfg: dict) -> tuple[str, str | None]:
    """0.18.65 (the local base's name, its remote copy or None) for the fetch, the drift and the audit
    (runtime/bases.py resolve: base_branch may be <remote>/<branch>)."""
    from . import bases

    r = bases.resolve(root, cfg) if git.is_repo(root) else {}
    name = r.get("name") or str(cfg.get("base_branch") or "main").removeprefix("refs/heads/")
    return name, r.get("remote")


def fetch_base(root: str, cfg: dict) -> str:
    """Bring the base's remote-tracking ref up to date before the ship checks read it (no prompt, FETCH_TIMEOUT s). A
    failed fetch (offline, no access) is a note, never a failure. Returns the note ("" when nothing was fetched)."""
    _name, remote = _base_remote(root, cfg)
    if not remote:
        return ""
    where, _, branch = remote.partition("/")
    if not (git.plain_name(where) and git.plain_name(branch)):
        return f"did not fetch {remote}: not a remote branch name"
    r = git.git(root, "fetch", "--quiet", "--no-tags", "--end-of-options", where,
                f"+refs/heads/{branch}:refs/remotes/{remote}", timeout=FETCH_TIMEOUT)
    return "" if r.returncode == 0 else f"could not fetch {remote} ({(r.stderr or r.stdout).strip()[:120]}); compared with the last fetched copy"


def base_drift(root: str, cfg: dict) -> str | None:
    """'main is 3 behind origin/main — keel compares with origin/main' (or 'diverged from', with the counts) when the
    local base branch differs from its remote-tracking ref; None when they agree or there is no remote copy. 0.18.65
    the remote copy is the canonical remote's (runtime/bases.py), and may have another name (local master, github/main)."""
    from . import bases

    if not git.is_repo(root):
        return None
    r = bases.resolve(root, cfg)
    name, remote, ahead, behind = r.get("local"), r.get("remote"), r.get("ahead"), r.get("behind")
    if not name or not remote or ahead is None or (not ahead and not behind):
        return None
    how = (f"diverged from {remote} ({ahead} local-only commit(s), {behind} behind)" if ahead and behind else
           f"{behind} behind {remote}" if behind else f"{ahead} ahead of {remote} (local-only commits)")
    return f"{name} is {how} — keel compares with {remote}"


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


# 0.18.17 a suite that ran no test proves nothing: `npx vitest run` exits 0 with "No test files found" and a flow
# ended done with no frontend test at all.
NO_TESTS_HINT = ("keel: no test ran, so this suite is not green. Write the tests, or fix the test command "
                 "(commands.*_test_module in .keel/config.yml, backend.dir / frontend.dir) so it finds them.")


def ran_no_tests(cmd: str, code: int, out: str) -> bool:
    """vitest/jest "No test files found", phpunit "No tests executed!", pytest "no tests ran" or its exit code 5."""
    return testcmd.ran_no_tests(out) or (code == 5 and "pytest" in cmd)


def run_suite(root: str, cmd: str, label: str, reruns: int = 1, cwd: str | None = None) -> dict:
    """Run a suite; a failing run runs once more. Fail then pass = flaky: it passes, with the tests that failed.
    A run with no test fails at once ("no_tests"): a rerun finds none either."""
    code, out = _run(root, cmd, cwd)
    if ran_no_tests(cmd, code, out):
        return {"ok": False, "code": code, "out": f"{out}\n{NO_TESTS_HINT}", "flaky": [], "no_tests": True}
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
    files = branch_files(a.root, flow_base(a, cfg))
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
        if callable(a.progress):
            a.progress(f"{label}: $ {cmd}", tool="verify", command=cmd)
        r = run_suite(a.root, cmd, label, reruns, cwd)
        ran.append({"label": label, "command": cmd, "ok": r["ok"]})
        flaky += r["flaky"]
        if not r["ok"]:
            record_verdict(a.key, "module", False, {"suites": ran, "summary": f"{label} failed",
                                                   "failed": failed_tests(r["out"])}, root=a.root)
            what = "ran no test" if r.get("no_tests") else "fails"
            return _result(False, f"The {label} {what}.", f"$ {cmd}\n{_tail(r['out'])}", flaky_update(a, flaky))
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


EXCERPT_MOST = 10


def excerpt(root: str, rel: str, lines: list[int], most: int = EXCERPT_MOST) -> str:
    """The code of an uncovered group: its lines with one line around, at most `most` lines, the uncovered ones marked
    with ">" ("" when the file cannot be read)."""
    try:
        text = (Path(root) / rel).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    if not text or not lines:
        return ""
    marked = set(lines)
    start = max(1, min(lines) - 1)
    end = min(len(text), max(lines) + 1, start + most - 1)
    width = len(str(end))
    out = [f"{'>' if n in marked else ' '} {n:>{width}} | {text[n - 1]}" for n in range(start, end + 1)]
    if max(lines) > end:
        out.append(f"  … {sum(1 for n in lines if n > end)} more uncovered line(s) up to {max(lines)}")
    return "\n".join(out)


def group_text(root: str, rel: str, lines: list[int], critical: bool) -> str:
    """What the decide gate shows for one group: the code (uncovered lines marked) and, on a security path, why it
    matters."""
    code = excerpt(root, rel, lines)
    parts = [f"Uncovered (marked >) in {rel}:\n{code}" if code else f"{rel}: lines {', '.join(map(str, lines[:20]))} (the file "
             "could not be read)"]
    if critical:
        parts.append("Security path (security.coverage_paths): every line here must be covered, 100% is needed; "
                     "accept leaves the coverage gate failing.")
    return "\n\n".join(parts)


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


def measure(root: str, cfg: dict, base: str | None, fresh_after: float | None = None) -> dict:
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
        # 0.18.75 a report the coverage command did not write in this run is an old one: it measures old code
        f = Path(root) / rel
        if fresh_after and f.is_file() and f.stat().st_mtime < fresh_after - 2:
            results.append({"app": app, "error": f"coverage report {rel} is older than this run: the coverage command "
                                                 "did not write it, so it measures old code"})
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
            crit = bool(critical) and rules.match_glob(it["file"], critical)
            groups.append({**it, "id": it["key"], "app": r["app"], "critical": crit,
                           "title": f"{it['file']}:{it['lines'][0]}" + (f"-{it['lines'][-1]}" if len(it["lines"]) > 1 else "")
                                    + f" ({len(it['lines'])} line{'s' if len(it['lines']) > 1 else ''})"
                                    + (" · security path" if crit else ""),
                           "show": group_text(root, it["file"], it["lines"], crit)})
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


def coverage_from_reports(a, fresh_after: float | None = None) -> tuple[dict, object]:
    """The changed-line verdict from the configured reports; returns (measurement, ActionResult). [fresh_after]: the time
    the coverage command started; an older report counts as not written."""
    cfg = rules.load_config(a.root)
    m = measure(a.root, cfg, flow_base(a, cfg), fresh_after)
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
    changed = [f for f in branch_files(a.root, flow_base(a, cfg)) if rules.match_glob(f, checks.DEP_FILES)]
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
    from . import security_actions

    # 0.18.22 an audit that exits non-zero blocks only at or above security.deps.fail_on (default high); one whose
    # output names no severity still blocks (keel cannot tell)
    threshold = security_actions.fail_on(cfg)
    failed, ran, below = [], [], []
    for lane, cmd, d in audits:
        cwd, _ = _cwd(a.root, d)
        code, out = _run(a.root, cmd, cwd, 600)
        worst = security_actions.worst_severity(out) if code != 0 else None
        ran.append({"lane": lane, "command": cmd, "code": code, **({"worst": worst} if code != 0 else {})})
        if code != 0 and security_actions.blocks(worst, threshold):
            failed.append((lane, cmd, out))
        elif code != 0:
            below.append(f"{lane}: worst {worst}")
    ok = not failed
    under = f"; below fail_on {threshold}: {', '.join(below)}" if below else ""
    record_verdict(a.key, "deps", ok, {"manifests": changed, "audits": ran, "fail_on": threshold,
                                       "summary": ("audit passed" + under) if ok else f"{failed[0][0]} audit failed"}, root=a.root)
    if not ok:
        return _result(False, f"Dependencies: the {failed[0][0]} audit fails (fail_on {threshold}).",
                       f"$ {failed[0][1]}\n{_tail(failed[0][2])}")
    return _result(True, f"Dependencies: {len(changed)} manifest(s) changed; {len(ran)} audit(s) passed{under}.")


# ------------------------------------------------------------------ audit

async def audit(a):
    return await asyncio.to_thread(_audit, a)


def audit_problems(root: str, cfg: dict, base: str | None, unlocks: list[dict], upstream: str | None = None) -> list[str]:
    """keel v1 verify.audit: test commits hold no production code, feat/fix commits no tests (paired style), no new
    skip/only/disabled markers, every unlock has a reason, spike branches do not ship.

    0.18.57 a commit keel made on its own paths is read as keel meant it: its Keel-Ref trailer stands for the subject
    (a project's commit style), a commit with Keel-Path: already-met (or skip) is accepted as it is, and test files a
    Keel-Unlocked trailer names were opened for that phase by an unlock (which has its own reason, checked below)."""
    problems = []
    single = (cfg.get("loops") or {}).get("commit_style") == "single" or (cfg.get("commit") or {}).get("commit_style") == "single"
    for sha, subject, body in reversed(_branch_log(root, base) if base else []):
        if upstream and upstream != base and git.git(root, "merge-base", "--is-ancestor", sha, upstream).returncode == 0:
            continue            # 0.18.57 already on the remote base (merged upstream): not this branch's work
        ref = commit_style.ref_of(subject, body)
        own = commit_style.trailers(body).get(commit_style.PATH) in commit_style.OWN_PATHS
        files = [f for f in git.git(root, "show", "--name-only", "--format=", sha).stdout.splitlines() if f.strip()]
        buckets = [rules.classify(cfg, f) for f in files]
        if ref.startswith("test(") and any(b in ("api-main", "web-src") for b in buckets):
            problems.append(f"{sha[:7]} {subject}: a test commit contains production code")
        # Repair phases (review-fix, coverage-fix, security, ship) may change code and tests together, like the guard
        # allows there; the paired separation only applies to criterion and bug commits.
        repair = re.match(r"^fix\((review|coverage|security|ship)\)", ref)
        opened = commit_style.unlocked_files(body)
        tests = [f for f, b in zip(files, buckets) if b in ("api-test", "web-test") and f not in opened]
        if re.match(r"^(feat|fix)\(", ref) and not repair and not single and not own and tests:
            problems.append(f"{sha[:7]} {subject}: an implementation commit contains test files")
        diff = git.git(root, "show", "--format=", sha).stdout
        found = [name for rx, name in DISABLED if rx.search(diff)]
        if found:
            problems.append(f"{sha[:7]} {subject}: adds {', '.join(found)}")
    for u in unlocks or []:
        if not u.get("reason") and u.get("by") != "settings":
            problems.append(f"unlock of {u.get('path')} in {u.get('phase')} has no reason")
    if (git.branch(root) or "").startswith("spike/"):
        problems.append(SPIKE)
    return problems


SPIKE = "spike branches cannot be shipped"


def _audit(a):
    if not git.is_repo(a.root):
        record_verdict(a.key, "audit", None, {"reason": "not a git repository"})
        return _result(None, "Audit not available: not a git repository.")
    cfg = rules.load_config(a.root)
    fetched = fetch_base(a.root, cfg)
    _name, upstream = _base_remote(a.root, cfg)
    base = (a.data or {}).get("audit_base") or flow_base(a, cfg)
    found = audit_problems(a.root, cfg, base, a.unlocks, upstream)
    # 0.18.57 the problems a person accepted at this gate (with their reason) are not asked again: the history the
    # audit reads cannot change by running it again, so "approve" has to change what it checks
    accepted = (a.data or {}).get("audit_accepted") or {}
    problems = [p for p in found if p not in accepted]
    n = len(git.git(a.root, "log", f"{base}..HEAD", "--format=%H").stdout.split()) if base else 0
    kept = [p for p in found if p in accepted]
    record_verdict(a.key, "audit", not problems, {"base": base, "commits": n, "problems": problems,
                                                  **({"accepted": {p: accepted[p] for p in kept}} if kept else {}),
                                                  "summary": f"{len(problems)} problem(s)" if problems else
                                                  f"{n} commit(s) clean" + (f", {len(kept)} accepted" if kept else "")},
                   root=a.root)
    if problems:
        from .actions import ActionResult

        note = f"Audit: {len(problems)} problem(s) on the branch."
        listed = "\n".join(problems)
        # Another run of the audit, or another round of an agent, cannot rewrite the branch's history: the person
        # accepts these exact problems (with a reason, recorded and shown in the PR body) or stops the flow
        only_stop = SPIKE in problems
        detail = (f"{listed}\n\nThese are in the branch's history: running the audit again finds them again.\n"
                  + ("A spike branch is never shipped: stop, and build it again in a feature flow." if only_stop else
                     "Accept them to go on (your reason is recorded with the audit verdict), or stop the flow and fix "
                     "the commits yourself (git rebase), then resume."))
        drift = base_drift(a.root, cfg)
        ask = {"type": "audit", "kind": "fix", "title": f"The audit found {len(problems)} problem(s) in the branch's commits",
               "detail": detail, "problems": problems, "base": base,
               "labels": {"approve": "Accept these and go on", "reject": "Stop the flow"}}
        if fetched:
            ask["detail"] += f"\n\nNote: {fetched}."
        if upstream and base != upstream and not only_stop:
            # the base it compared with is not the remote one: the commits may be upstream work, not this branch's
            ask["detail"] = (f"The audit compared with {base}, not {upstream}" + (f" ({drift})" if drift else "")
                             + f": commits already merged on {upstream} look like this branch's own work.\n\n" + ask["detail"])
            ask.update(choices=["remote", "accept", "stop"], upstream=upstream, choice_info=[
                {"name": "remote", "label": f"Compare with {upstream} instead (recommended)", "why": "none", "default": True,
                 "description": f"run the audit again against {upstream}: commits already there are not checked"},
                {"name": "accept", "label": "Accept these and go on", "why": "optional",
                 "description": "these exact problems are accepted with your reason (recorded with the verdict)"},
                {"name": "stop", "label": "Stop the flow", "why": "optional", "description": "fix the commits yourself, then resume"}])
        elif drift:
            ask["detail"] += f"\n\nNote: {drift}."
        if only_stop:
            ask.update(options=["reject"], labels={"reject": "Stop the flow"})
        return ActionResult(False, note, listed, ask=ask)
    tail = f" ({len(kept)} accepted at the audit gate)" if kept else ""
    return _result(True, f"Audit: {n} commit(s) since {base or 'the start'}, no problems{tail}.")


# ------------------------------------------------------------------ trace

def _branch_log(root: str, base: str | None) -> list[tuple[str, str, str]]:
    """(sha, subject, body) of the branch's commits, newest first."""
    rng = [f"{base}..HEAD"] if base else ["--max-count=500"]
    out = git.git(root, "log", *rng, "--format=%H%x1f%s%x1f%b%x1e").stdout or ""
    rows = []
    for entry in out.split("\x1e"):
        parts = entry.strip("\n").split("\x1f")
        if parts and parts[0].strip():
            rows.append((parts[0].strip(), parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""))
    return rows


def ac_commits(root: str, ac_id: str, base: str | None = None) -> dict:
    """{"red": sha, "green": sha} of one criterion: its newest test(AC-n) and feat(AC-n) / fix(AC-n) commits (None when
    there is none). The AC reviewer is given these two (content/agents/criterion-reviewer.md)."""
    if not ac_id or not git.is_repo(root):
        return {"red": None, "green": None}
    log = [(sha, commit_style.ref_of(s, b), b) for sha, s, b in _branch_log(root, base)]
    return {"red": next((sha for sha, s, _b in log if f"test({ac_id})" in s), None),
            "green": next((sha for sha, s, _b in log if f"feat({ac_id})" in s or f"fix({ac_id})" in s), None)}


OWN_STEP_LAYERS = {"E2E": "e2e", "SMOKE": "smoke"}     # criteria built by their own step, not by RED/GREEN


# keel 0.18.72 where a test that names a criterion may live when the project sets no e2e (smoke) folder, or none there
# names it: a field report's [E2E] criteria were tested under tests/ and the strict trace said "no test commit" anyway
TEST_PATHS = (":(glob)**/tests/**", ":(glob)**/test/**", ":(glob)**/e2e/**", ":(glob)**/*Test.*", ":(glob)**/*_test.*",
              ":(glob)**/*.spec.*", ":(glob)**/*.test.*", ":(glob)**/test_*", ":(exclude,glob)**/*.md")


def _named_test(root: str, folder: str | None, ac_id: str) -> str | None:
    """The first tracked file under the e2e (or smoke) folder that names this criterion id; else (0.18.72) the first
    test file anywhere that names it."""
    for paths in ([folder.rstrip("/") + "/"] if folder else [], list(TEST_PATHS)):
        if not paths:
            continue
        r = git.git(root, "grep", "-l", "-w", "-F", "-e", ac_id, "--", *paths)
        found = next((f.strip() for f in (r.stdout or "").splitlines() if f.strip()), None)
        if found:
            return found
    return None


def trace_rows(root: str, acs: list[dict], base: str | None = None) -> list[dict]:
    """AC -> test commit (test(AC-n)) -> implementation commit (feat(AC-n) / fix(AC-n)), from the commit subjects.
    An [E2E] or [SMOKE] criterion: the e2e / smoke commit whose "Covers:" line names it, and the test file under the
    e2e / smoke folder that names its id (that file is what makes it complete)."""
    log = [(sha, commit_style.ref_of(s, b), b) for sha, s, b in _branch_log(root, base)]
    cfg = None
    rows = []
    for ac in acs or []:
        aid = ac["id"]
        layer = str(ac.get("layer") or "API").upper()
        row = {"id": aid, "layer": ac.get("layer", "API"), "title": ac.get("title", ""), "status": ac.get("status", "todo")}
        if layer in OWN_STEP_LAYERS:
            cfg = cfg or rules.load_config(root)
            named = re.compile(rf"(?m)^Covers:.*(?<![\w-]){re.escape(aid)}(?![\w.-])")
            sha = next((sha for sha, _s, body in log if named.search(body)), None)
            test = _named_test(root, (cfg.get(OWN_STEP_LAYERS[layer]) or {}).get("dir"), aid)
            red = next((c for c, s, _b in log if f"test({aid})" in s), None)
            sha = sha or red                    # 0.18.72 an [E2E] criterion built test first has its test(<id>) commit
            # a smoke check is often a plain script with no id in it: its smoke commit's Covers: line is enough
            done = bool(test) or bool(red) or (layer == "SMOKE" and bool(sha))
            rows.append({**row, "test": sha[:7] if sha else None, "commit": None, "file": test, "complete": done})
            continue
        red = next((sha for sha, s, _b in log if f"test({aid})" in s), None)
        green = next((sha for sha, s, _b in log if f"feat({aid})" in s or f"fix({aid})" in s), None)
        met = ac.get("status") == "already-met"
        if not red and green and test_scope.is_refactor(ac):
            # 0.18.58 a refactor has no test commit of its own: the existing tests, run before and after, prove it
            rows.append({**row, "test": "existing tests", "commit": green[:7], "complete": True})
            continue
        rows.append({**row, "test": red[:7] if red else None, "commit": green[:7] if green else None,
                     "complete": bool(red) and (bool(green) or met)})
    return rows


def trace_table(rows: list[dict]) -> str:
    out = ["| AC | layer | status | test commit | implementation |", "|---|---|---|---|---|"]
    for r in rows:
        if str(r.get("layer") or "").upper() in OWN_STEP_LAYERS:
            impl = f"test file {r['file']}" if r.get("file") else "no test names it"
        else:
            impl = r["commit"] or "—"
        out.append(f"| {r['id']} | {r['layer']} | {r['status']} | {r['test'] or '—'} | {impl} |")
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
    rows = trace_rows(a.root, a.acs, flow_base(a, cfg))
    own = [r for r in rows if str(r.get("layer") or "").upper() in OWN_STEP_LAYERS]
    loop = [r for r in rows if r not in own]
    untested = [r["id"] for r in loop if not r["test"] and r["status"] != "already-met"]
    # an [E2E] criterion is tested when a test under the e2e folder names its id ([SMOKE]: or its smoke commit does)
    untested += [r["id"] for r in own if not r["complete"]]
    unbuilt = [r["id"] for r in loop if r["test"] and not r["commit"] and r["status"] != "already-met"]
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
    files = branch_files(a.root, flow_base(a, cfg)) if git.is_repo(a.root) else []
    r = _arch(a.root, cfg, files) or {"violations": [], "inspected": 0}
    bad = r["violations"]
    record_verdict(a.key, "arch", not bad, {"violations": bad[:50], "inspected": r["inspected"],
                                            "summary": f"{len(bad)} boundary violation(s)" if bad else f"{r['inspected']} file(s) clean"},
                   root=a.root)
    if bad:
        return _result(False, f"Architecture: {len(bad)} boundary violation(s).",
                       "\n".join(f"  {v['file']}:{v['line']} imports {v['target']} ({v['rule']})" for v in bad[:30]))
    return _result(True, f"Architecture: {r['inspected']} changed file(s) inspected, no boundary crossed.")


# ------------------------------------------------------------------ what the flow's reviews left behind

UNREACHABLE = re.compile(r"\b(?:not|un)[ -]?reachable\b", re.I)


def results_of(data: dict, step: str) -> list[dict]:
    """A fan-out step's answers (data["<id>_results"]), under its own id or an include's prefix (ship_review)."""
    for k, v in data.items():
        if (k == f"{step}_results" or k.endswith(f"_{step}_results")) and isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]
    return []


def security_summary(state: dict) -> tuple[str, list[str]]:
    """(result, lines): what the feature flow's security step found, for the ship-ready gate, the final review and the
    PR body. result is "clean", "findings", "skipped" or "not run"; the lines say what each pipeline reported, what the
    dependency triage judged unreachable, and the findings the person let through (with why)."""
    data = state.get("data") or {}
    gates = state.get("gates") or {}
    skip = next((s for s in data.get("ship_skipped") or [] if isinstance(s, dict) and s.get("step") == "security"), None)
    if skip:
        return "skipped", [f"- skipped: {skip.get('reason') or 'no reason given'}"]
    if (gates.get("skipped") or {}).get("security"):
        return "skipped", [f"- skipped: {gates['skipped']['security']}"]
    results = results_of(data, "security")
    if not results:
        return "not run", ["- not run in this flow (no security step ran before ship)"]
    from .findings import blocking
    lines, found = [], 0
    for r in results:
        got = blocking(str(r.get("text") or ""))
        found += len(got)
        lines.append(f"- {r.get('title') or r.get('item')}: " + (f"{len(got)} blocking finding(s)" if got else "clean"))
        if str(r.get("item")) == "deps":
            for line in str(r.get("text") or "").splitlines():
                if UNREACHABLE.search(line):
                    lines.append(f"  - triaged unreachable: {line.strip(' -*')[:240]}")
    let = [f"  - let through: {text} — why: {why}" for _who, text, why in dismissed_items(data, "security")]
    lines += let
    return ("findings" if found or let else "clean"), lines


def dismissed_items(data: dict, step: str | None = None) -> list[tuple[str, str, str]]:
    """Every blocking finding the person let through as (who, finding, why): one reason per review, or one per finding
    when the findings gate kept them per item ({text, why|reason, decision}). `step`: only that review's (an id suffix)."""
    out = []
    for d in data.get("dismissed_findings") or []:
        if not isinstance(d, dict) or (step and not str(d.get("step") or "").endswith(step)):
            continue
        who = str(d.get("name") or d.get("step"))
        for f in d.get("findings") or []:
            if isinstance(f, dict):
                out.append((who + (f", {f['decision']}" if f.get("decision") else ""), str(f.get("text") or f.get("finding")),
                            str(f.get("why") or f.get("reason") or d.get("why") or "no reason given")))
            else:
                out.append((who, str(f), str(d.get("why") or "no reason given")))
    return out


def dismissed_lines(data: dict, step: str | None = None) -> list[str]:
    return [f"- blocking finding dismissed ({who}): {text} — why: {why}" for who, text, why in dismissed_items(data, step)]


def nonblocking_lines(data: dict) -> list[str]:
    """data.nonblocking_findings (kept by the findings gate): strings, or {lens|name|step, text|finding|findings}."""
    out = []
    for n in data.get("nonblocking_findings") or []:
        if isinstance(n, dict):
            who = n.get("lens") or n.get("name") or n.get("step")
            texts = n.get("findings") if isinstance(n.get("findings"), list) else [n.get("text") or n.get("finding")]
            out += [f"- {who + ': ' if who else ''}{t.get('text') if isinstance(t, dict) else t}" for t in texts if t]
        elif str(n).strip():
            out.append(f"- {n}")
    return out


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
    # 0.18.65 keel init's setup commits at the branch's bottom are not this change's (runtime/bases.py own_base)
    from . import bases

    own, setup = bases.own_base(root, base) if base and git.is_repo(root) else (base, [])
    if not extract:
        # What was asked and what the branch did (the last agent's answer is about its own step, not the branch).
        commits = git.git(root, "log", "--reverse", "--format=- %s", f"{own}..HEAD").stdout.strip() \
            if own and git.is_repo(root) else ""
        out += ["## Summary", ""] + ([request.strip()[:1500], ""] if request.strip() else []) \
            + (["Commits:", commits[:3000], ""] if commits else [])
    out += bug.section(state)           # a bug flow: symptom, root cause, reproducing test, hunt findings it closes
    if setup:
        files = [f for f in git.git(root, "diff", "--name-only", f"{base}...{setup[-1]}").stdout.splitlines() if f.strip()]
        out += ["## keel setup (not this change)", "", bases.setup_note(setup), ""] \
            + [f"- `{f}`" for f in files[:20]] + ([f"- … {len(files) - 20} more"] if len(files) > 20 else []) + [""]
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
    out += ci_section(project)
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
    out += security_section(state)
    dismissed = dismissed_lines(data)
    if dismissed:
        out += ["## Blocking findings let through", ""] + dismissed + [""]
    out += memory_section(data)
    unlocks = [u for u in state.get("unlocks") or [] if u.get("by") != "settings" or u.get("reason")]
    if unlocks:
        out += ["## Unlocks used", ""] + [f"- `{u.get('path')}` in {u.get('phase')}: {u.get('reason') or 'no reason given'}"
                                          for u in unlocks] + [""]
    warned = [w for w in data.get("warnings") or [] if isinstance(w, dict)]
    if warned:
        out += ["## Warnings", ""] + [f"- {w.get('text')}" for w in warned] + [""]
    flaky = state.get("flaky") or []
    if flaky:
        out += ["## Flaky tests seen", ""] + [f"- {f.get('label')}: {', '.join(f.get('tests') or [])}" for f in flaky] + [""]
    out += extensions.pr_body_sections(thread_id)      # the parts' sections: KeelBot's commits at a gate
    out += ["---", "_Prepared by keel._"]
    return "\n".join(out)


def security_section(state: dict) -> list[str]:
    """The PR body's security result (the feature flow's auditor and dependency triage), or that it did not run."""
    res, lines = security_summary(state)
    return [f"## Security: {res}", "", *lines, ""]


def memory_section(data: dict) -> list[str]:
    """The PR body's knowledge-base line: updated (which sections), skipped (why), current, or none (ship's memory_plan)."""
    m = data.get("memory_choice") if isinstance(data.get("memory_choice"), dict) else None
    if not m:
        return []
    said = {"update": "updated: " + ", ".join(m.get("sections") or []) if m.get("sections") else "updated",
            "skip": f"skipped: {m.get('reason') or 'no reason given'}",
            "current": "current: no file a section cites changed", "none": "none: the project has no knowledge base",
            "off": "off: the project notes are off (knowledge.build: off)"}
    return ["## Knowledge base", "", said.get(m.get("choice"), str(m.get("choice"))), ""]


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


def ci_section(project: str) -> list[str]:
    """0.18.65 the PR body's CI line: what the project's own CI jobs said on CI's versions (the ci verdict), one line
    per run that did not pass. Nothing when keel never ran them."""
    v = verdicts.latest(project, "ci")
    if not v:
        return []
    d = v.get("detail") or {}
    out = ["## CI jobs on CI's versions (run by keel)", "", str(d.get("summary") or d.get("reason") or "")]
    for r in d.get("runs") or []:
        if isinstance(r, dict) and r.get("result") in ("failed", "could not run"):
            out.append(f"- {r.get('name')} {r.get('runtime') or ''} {r.get('version') or ''}".rstrip()
                       + f": {r.get('result')}" + (f" ({r.get('why')})" if r.get("why") else ""))
    return out + [""]


async def pr(a):
    return await asyncio.to_thread(_pr, a)


def _pr(a):
    cfg = rules.load_config(a.root)
    base = base_ref(a.root, cfg, a.base) if git.is_repo(a.root) else None
    body = pr_body(a.root, a.key, a.state, a.title, base, getattr(a, "request", "") or "",
                   run_modes.normalize((a.settings or {}).get("run_mode")), a.thread_id)
    return _result(True, f"PR body ready ({len(body.splitlines())} lines); it is shown at the next gate.", body, {"pr_body": body})


def github_token(keys: dict):
    """The GitHub login of a call (a str with .host: tools/git_auth.py), or None. keys["github"] (Connections ›
    GitHub, its host keys["github_host"]), else the Copilot login, else GH_TOKEN / GITHUB_TOKEN."""
    from ..tools import git_auth

    return git_auth.login(keys)


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
        from ..tools import git_auth

        env = git_auth.gh_env(a.root, token)
        cmd = f"gh pr create --title {shlex.quote(title)} --body-file {shlex.quote(path)}"
        code, out = testcmd.run(a.root, cmd, 120, env)
    finally:
        Path(path).unlink(missing_ok=True)
    if code != 0:
        return _result(True, f"gh pr create failed; no PR opened. {copy}", f"$ {cmd}\n{_tail(out, 1500)}\n\n{body}")
    url = next((l for l in out.splitlines() if l.startswith("http")), out.strip()[:200])
    return _result(True, f"PR opened: {url}", out, {"data": {**a.data, "pr_url": url}})
