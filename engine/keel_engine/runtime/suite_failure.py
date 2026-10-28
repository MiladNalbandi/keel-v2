"""keel 0.18.28 when the whole suite fails (integration, a review fix, the check after a criterion's green): the real
failing output, and who is the likely cause.

A real run: every criterion was green with its own filter (--filter 'AC-00x'); at integration the whole suite ran for the
first time and two old tests failed, a unique-key duplicate and a row that should not exist. AC-008's test committed a
row in setUp (it needed a second connection) and never deleted it, so the shared dev database kept it and every rerun
failed the same way. The integration agent saw "Exit code 2 with no output; need details." and could not change tests.

    excerpt(out)                   the failing tests and their messages (phpunit, pytest, jest/vitest, gradle, go)
    failing(out)                   the failing tests' names
    empty_note(cmd, code)          what an exit code means when the command printed nothing
    junit_failures(path)           the failures a JUnit XML report names (phpunit --log-junit)
    rerun_for_output(...)          the suite again with a JUnit report, when the first run printed no failure
    suspects(root, base)           the branch's new or changed test files that write to the database without cleaning up
    alone(...)                     each failing test run on its own: passing alone but failing in the suite = pollution
    analyse(...)                   all of it: {failing, alone, pollution, suspects, likely_ac, evidence}
"""

from __future__ import annotations

import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import rules
from ..tools import git, testcmd

MAX_ALONE = 5            # failing tests keel runs on their own, at most
ALONE_TIMEOUT = 300

# one failing test per match: phpunit "1) App\\Tests\\FooTest::testBar", pytest "FAILED tests/x.py::test_y",
# jest/vitest "✕ name" / "× name" / "FAIL path", gradle "FooTest > bar() FAILED", go "--- FAIL: TestX", TAP "not ok 3 - x"
_FAILING = [
    re.compile(r"^\s*\d+\)\s+([\w\\]+(?:::|->)\w+)", re.M),
    re.compile(r"^FAILED\s+(\S+)", re.M),
    re.compile(r"^\s*(?:✕|×|✖)\s+(.+?)\s*(?:\(\d+\s*m?s\))?$", re.M),
    re.compile(r"^(\S.*?)\s+FAILED\s*$", re.M),
    re.compile(r"^--- FAIL:\s+(\S+)", re.M),
    re.compile(r"^not ok \d+\s*-?\s*(.+)$", re.M),
]
# where a runner's failure report starts
_REPORT = re.compile(r"^(?:There (?:was|were) \d+ (?:errors?|failures?|risky tests?):|=+ FAILURES =+|=+ ERRORS =+|"
                     r"=+ short test summary info =+|\s*● .+|FAIL\s+\S+|--- FAIL:|FAILURE: Build failed)", re.M)
PHPUNIT_CODES = {1: "some tests failed", 2: "an error or an exception stopped phpunit (a broken test, a database or "
                    "configuration problem), or a bad option", 255: "PHP crashed (a fatal error)"}


def failing(out: str) -> list[str]:
    seen: list[str] = []
    for pat in _FAILING:
        for m in pat.findall(out or ""):
            name = str(m).strip()
            if name and name not in seen and len(name) < 300:
                seen.append(name)
    return seen[:30]


def excerpt(out: str, limit: int = 3000) -> str:
    """The failure report of a runner's output (where its "There were 2 errors:" / "FAILURES" part starts), else ""."""
    m = _REPORT.search(out or "")
    if not m:
        return ""
    text = (out or "")[m.start():].strip()
    return text if len(text) <= limit else text[:limit].rstrip() + f"\n… ({len(text) - limit} more characters in the log)"


def empty_note(cmd: str, code: int) -> str:
    why = PHPUNIT_CODES.get(code) if "phpunit" in cmd or "composer" in cmd or "bin/console" in cmd else None
    return (f"The command exited {code} and printed nothing" + (f" (for phpunit, exit {code}: {why})" if why else "")
            + ". keel ran it again with a JUnit report to name the failing tests.")


def junit_failures(path: str) -> list[dict]:
    """[{name, message}] of the failing and erroring testcases of a JUnit XML report ([] when it is not there)."""
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return []
    out = []
    for case in tree.iter("testcase"):
        bad = next((c for c in case if c.tag in ("failure", "error")), None)
        if bad is None:
            continue
        cls = case.get("class") or case.get("classname") or ""
        name = f"{cls}::{case.get('name')}" if cls else str(case.get("name"))
        msg = (bad.get("message") or bad.text or "").strip()
        out.append({"name": name, "message": msg[:1500]})
    return out[:30]


def _junit_cmd(cmd: str, report: str) -> str | None:
    """The command with a JUnit report option, for the runners that take one; None when keel does not know how."""
    if "phpunit" in cmd:
        return f"{cmd} --log-junit {report}"
    if re.search(r"\bcomposer\s+(?:run(?:-script)?\s+)?test\b", cmd):
        return f"{cmd} -- --log-junit {report}" if " -- " not in cmd else f"{cmd} --log-junit {report}"
    if "pytest" in cmd:
        return f"{cmd} --junitxml={report}"
    return None


def rerun_for_output(cwd: str, cmd: str, env: dict | None, timeout: int = 1800) -> tuple[list[dict], str]:
    """The suite once more with a JUnit report: ([{name, message}], the output). ([], "") when the runner takes none."""
    with tempfile.TemporaryDirectory(prefix="keel-junit-") as tmp:
        report = str(Path(tmp) / "report.xml")
        again = _junit_cmd(cmd, report)
        if not again:
            return [], ""
        _code, out = testcmd.run(cwd, again, timeout, env)
        return junit_failures(report), out


# ------------------------------------------------------------------ suspects: the branch's tests that write to the DB

TEST_FILE = re.compile(r"(?:^|/)(?:tests?|spec|__tests__|src/test)/|(?:Test|Tests|_test|\.test|\.spec|IT)\.\w+$", re.I)
DB_WRITE = re.compile(r"\b(?:INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b|->(?:persist|flush|insert|executeStatement|"
                      r"executeQuery|exec)\s*\(|\.(?:save|saveAndFlush|insert|create|bulkCreate|executeUpdate|execute)\s*\(|"
                      r"\bDB::(?:table|insert|statement)\b|\bdb\.session\.(?:add|commit)\b|\bcursor\.execute\b", re.I)
COMMITS = re.compile(r"->commit\s*\(|\.commit\s*\(|DriverManager::getConnection|new\s+PDO\s*\(|getConnection\s*\(\s*\)\s*->"
                     r"|second connection|autocommit", re.I)
CLEANUP = re.compile(r"rollBack|rollback|DAMA\\\\?DoctrineTestBundle|RefreshDatabase|DatabaseTransactions|ResetDatabase|"
                     r"@Transactional|@Sql\(|\bTRUNCATE\b|purge\(|tearDown[\s\S]{0,400}?(?:DELETE|delete|remove|truncate|"
                     r"executeStatement)|afterEach[\s\S]{0,300}?(?:delete|truncate|destroy)|@AfterEach|cleanUp[\s\S]{0,400}?"
                     r"(?:DELETE|delete|remove)", re.I)
AC_ID = re.compile(r"\bAC-?(\d{1,4})\b", re.I)


def _norm_ac(n: str) -> str:
    return f"AC-{n}"


def suspects(root: str, base: str | None) -> list[dict]:
    """The branch's new or changed test files that write to the database and do not clean up, most likely first:
    [{file, acs, writes, commits, cleanup}]. A file that also commits on its own connection ranks first."""
    if not git.is_repo(root):
        return []
    r = git.git(root, "diff", "--name-status", f"{base}...HEAD") if base else None
    rows = [ln.split("\t") for ln in (r.stdout.splitlines() if r is not None and r.returncode == 0 else [])]
    files = [p[-1] for p in rows if p and p[0][:1] in ("A", "M", "R")]
    files += [f for f in git.dirty(root) if f not in files]
    out = []
    for f in files:
        if not TEST_FILE.search(f):
            continue
        path = Path(root) / f
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        d = git.git(root, "diff", f"{base}...HEAD", "--", f) if base else None
        added = "\n".join(ln[1:] for ln in (d.stdout.splitlines() if d is not None and d.returncode == 0 else text.splitlines())
                          if ln.startswith("+") and not ln.startswith("+++")) or text
        writes = [ln.strip()[:160] for ln in added.splitlines() if DB_WRITE.search(ln)][:5]
        if not writes:
            continue
        log = git.git(root, "log", "--format=%s", f"{base}..HEAD", "--", f) if base else None
        acs = sorted({_norm_ac(n) for n in AC_ID.findall((log.stdout if log is not None else "") + "\n" + f + "\n" + added)},
                     key=lambda x: int(x.split("-")[1]))
        out.append({"file": f, "acs": acs, "writes": writes, "commits": bool(COMMITS.search(added)),
                    "cleanup": bool(CLEANUP.search(text))})
    out = [s for s in out if not s["cleanup"]] + [s for s in out if s["cleanup"]]
    return sorted(out, key=lambda s: (s["cleanup"], not s["commits"]))


# ------------------------------------------------------------------ alone: does a failing test pass on its own?

# the characters a test name keel puts into a command may have (test_x[1], tests/a.py::t, Foo.bar-baz)
TEST_NAME = re.compile(r"^(?!-)[A-Za-z0-9_:./\[\]-]+$")


def _short(name: str) -> str:
    """The method part of a test name, as a filter takes it ("App\\Tests\\FooTest::testBar" -> "testBar")."""
    for sep in ("::", "->", " > ", "."):
        if sep in name:
            name = name.rsplit(sep, 1)[-1]
    return re.sub(r"[()\s].*$", "", name).strip() or name


def one_test_command(root: str, layer: str, name: str) -> tuple[str | None, str]:
    """(the command that runs one test, its folder): commands.api_test_one ({TEST}) when configured, else the criterion
    command (api_test_ac) with the test's short name where the criterion goes."""
    cmds = rules.load_config(root).get("commands") or {}
    key = "web_test_one" if layer == "WEB" else "api_test_one"
    rel = testcmd.part_dir(root, "frontend" if layer == "WEB" else "backend")
    short = _short(name)
    if not TEST_NAME.match(short):
        return None, ""          # 0.18.64 (security audit F14) a name from the test output is never shell code
    if cmds.get(key):
        return testcmd.put(str(cmds[key]), "{TEST}", short), rel
    raw = cmds.get("web_test_ac" if layer == "WEB" else "api_test_ac")
    if raw:
        cmd = testcmd.widen(str(raw))
        for mark in ("{AC_KEY}", "{AC_ID}", "{AC}"):
            cmd = testcmd.put(cmd, mark, short)
        return cmd, rel
    cmd = testcmd.detected(root, "ZZKEELZZ")
    return (testcmd.put(testcmd.put(cmd, "ZZKEELZZ", short), "zzkeelzz", short) if cmd else None), ""


def alone(root: str, names: list[str], env: dict | None, layer: str = "API") -> list[dict]:
    """Each failing test (at most MAX_ALONE) run on its own: [{name, command, passed}]."""
    out = []
    for name in names[:MAX_ALONE]:
        cmd, rel = one_test_command(root, layer, name)
        if not cmd:
            continue
        code, text = testcmd.run(str(Path(root) / rel) if rel else root, cmd, ALONE_TIMEOUT, env)
        ran = not testcmd.ran_no_tests(text) and not testcmd.cannot_run(code, text)
        out.append({"name": name, "command": testcmd.shown(cmd, rel), "passed": code == 0 and ran, "ran": ran})
    return out


def analyse(root: str, base: str | None, out: str, env: dict | None = None, layer: str = "API",
            acs: list[dict] | None = None, run_alone: bool = True) -> dict:
    """The whole picture of a suite failure, for the gate and the agent that fixes it."""
    names = failing(out)
    solo = alone(root, names, env, layer) if run_alone and names else []
    sus = suspects(root, base)
    known = {a.get("id") for a in acs or []}
    pollution = any(x["passed"] for x in solo)
    likely = next((a for s in sus if not s["cleanup"] for a in s["acs"] if not known or a in known), None)
    evidence = []
    if pollution:
        evidence.append("passes alone, fails in the suite: " + ", ".join(x["name"] for x in solo if x["passed"]))
    for s in sus[:3]:
        evidence.append(f"{s['file']} ({', '.join(s['acs']) or 'no criterion'}) writes to the database"
                        + (" on its own committed connection" if s["commits"] else "")
                        + ("" if s["cleanup"] else " and never cleans up") + f": {s['writes'][0]}")
    return {"failing": names, "alone": solo, "pollution": pollution, "suspects": sus, "likely_ac": likely,
            "evidence": evidence}


def describe(a: dict) -> str:
    """The analysis as Markdown lines for a gate and an agent's prompt."""
    lines = []
    if a.get("failing"):
        lines += ["Failing tests:", *[f"- {n}" for n in a["failing"][:15]], ""]
    if a.get("alone"):
        lines += ["Each failing test on its own:", *[f"- {x['name']}: {'passes' if x['passed'] else 'fails' if x['ran'] else 'did not run'}"
                                                    f" (`{x['command']}`)" for x in a["alone"]], ""]
    if a.get("pollution"):
        lines += ["A test that passes alone but fails in the whole suite is polluted: another test leaves data behind "
                  "(a row in the shared database, a file, a cache).", ""]
    if a.get("suspects"):
        lines += ["The branch's tests that write to the database:", *[
            f"- `{s['file']}` ({', '.join(s['acs']) or 'no criterion'}): {'cleans up' if s['cleanup'] else 'no rollback or cleanup'}"
            + ("; commits on its own connection" if s["commits"] else "") + f"; e.g. `{s['writes'][0]}`" for s in a["suspects"][:5]], ""]
    return "\n".join(lines).strip()

