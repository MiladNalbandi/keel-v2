"""Which command runs a project's tests: `.keel/config.yml` commands first, otherwise detected."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
from pathlib import Path

from .. import rules


def _pytest_key(ac: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", ac.lower()).strip("_")


def ac_id(ac: str) -> str:
    """The criterion id as a Java/Kotlin/Go name can hold it: AC-006 → AC006 (placeholder {AC_ID})."""
    return re.sub(r"[^A-Za-z0-9]+", "", ac or "")


# A Maven -Dtest= or Gradle --tests filter on {AC}: a Java class or method name cannot hold the dash, so the filter
# also takes the dash-free id (v0.17.4: -Dtest=*AC-006* never matched AC006PluginSubstrateRegressionTest).
_MVN_FILTER = re.compile(r"(-Dtest=)(['\"]?)([^\s'\"]*\{AC\}[^\s'\"]*)\2")
_GRADLE_FILTER = re.compile(r"(--tests\s+)(['\"]?)([^\s'\"]*\{AC\}[^\s'\"]*)\2")


def widen(cmd: str) -> str:
    """`-Dtest='*{AC}*'` → `-Dtest='*{AC}*,*{AC_ID}*'`; `--tests '*{AC}*'` → also `--tests '*{AC_ID}*'`."""
    if "{AC_ID}" in cmd:
        return cmd
    cmd = _MVN_FILTER.sub(lambda m: f"{m[1]}{m[2]}{m[3]},{m[3].replace('{AC}', '{AC_ID}')}{m[2]}", cmd)
    return _GRADLE_FILTER.sub(lambda m: f"{m[0]} {m[1]}{m[2]}{m[3].replace('{AC}', '{AC_ID}')}{m[2]}", cmd)


# 0.18.64 (security audit F14) the criterion ids keel uses: AC-6, CHG-1.2 (a change flow's inline criteria), BUG-3 and
# the like. Only these go into a shell command; they hold no character the shell reads.
AC_IDS = re.compile(r"^(?:AC-\d+|CHG-\d+\.\d+|[A-Z]+-\d+)$", re.I)


def valid_ac(ac: str | None) -> bool:
    return bool(AC_IDS.match(str(ac or "")))


def _safe(text: str) -> str:
    """Only letters, digits, `.`, `_` and `-`, never a leading `-`: safe inside or outside quotes in any shell command."""
    return re.sub(r"[^A-Za-z0-9_.-]", "", text or "").lstrip("-")


def put(cmd: str, mark: str, value: str) -> str:
    """`cmd` with each `mark` replaced by `value`: as it is inside quotes ('*{AC}*'), shell-quoted outside them. The
    caller keeps `value` free of quotes (valid_ac, suite_failure's test names)."""
    out, i, quote = [], 0, ""
    while i < len(cmd):
        if cmd.startswith(mark, i):
            out.append(value if quote or not value else shlex.quote(value))
            i += len(mark)
            continue
        c = cmd[i]
        if quote == "'" and c == "'" or quote == '"' and c == '"':
            quote = ""
        elif not quote and c in ("'", '"'):
            quote = c
        elif c == "\\" and quote != "'" and i + 1 < len(cmd):
            out.append(cmd[i:i + 2])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def fill(cmd: str, ac: str | None) -> str:
    """A command with its placeholders filled: {AC} (AC-006), {AC_ID} (AC006), {AC_KEY} (ac_006). An id that is not
    one of keel's (valid_ac) keeps only its safe characters."""
    a = ac or ""
    if a and not valid_ac(a):
        a = _safe(a)
    cmd = widen(cmd)
    for mark, value in (("{AC_KEY}", _pytest_key(a)), ("{AC_ID}", ac_id(a)), ("{AC}", a)):
        cmd = put(cmd, mark, value)
    return cmd


def jvm(root: str) -> bool:
    """A Maven or Gradle project, where a test is a class or method whose name cannot hold a dash."""
    r = Path(root)
    backend = r / str((rules.load_config(root).get("backend") or {}).get("dir") or ".")
    return any((base / f).exists() for base in (r, backend)
               for f in ("pom.xml", "mvnw", "gradlew", "build.gradle", "build.gradle.kts"))


def name_hint(root: str, ac: str) -> str:
    """How to name the criterion's test so keel's filter finds it."""
    if jvm(root):
        return (f"Put {ac_id(ac)} in the test class or method name (for example {ac_id(ac)}RankingTest, or a method "
                f"{ac_id(ac).lower()}RanksPlayers): a Java class or method name cannot hold the dash of {ac}.")
    return f'Put the criterion id in the test\'s name (for example "{ac} ..."), so the engine can run it alone.'


def _configured(root: str, ac: str | None, layer: str) -> str | None:
    """The project's own test command from .keel/config.yml (placeholders filled), or None."""
    if not (Path(root) / ".keel" / "config.yml").is_file():
        return None
    cmds = rules.load_config(root).get("commands") or {}
    key = ("web_test_ac" if layer == "WEB" else "api_test_ac") if ac else ("web_test_module" if layer == "WEB" else "api_test_module")
    custom = cmds.get(key) or (cmds.get("unit_tests") if not ac else None)
    return fill(custom, ac) if custom else None


def command_for(root: str, ac: str | None = None, layer: str = "API") -> str | None:
    return _configured(root, ac, layer) or detected(root, ac)


# keel v1's rule (lib/ops.js): a command from .keel/config.yml runs inside its part's folder: backend commands in
# backend.dir, frontend ones in frontend.dir. So "../mvnw -f .." written for backend.dir engine/ finds the root's
# mvnw. Commands keel detects by itself are made for the repo root and run there.
FRONTEND_KEYS = {"deps_web", "web_typecheck", "web_lint", "web_test_ac", "web_test_module", "web_test_paths"}


def part_dir(root: str, part: str) -> str:
    """The relative folder of a part ("backend" / "frontend") when it exists in the repo, else "" (the root)."""
    d = str(((rules.load_config(root).get(part) or {}).get("dir")) or "").strip().strip("/")
    while d.startswith("./"):
        d = d[2:]
    if d in ("", ".") or ".." in Path(d).parts or not (Path(root) / d).is_dir():
        return ""
    return d


def dir_for_key(root: str, key: str | None) -> str:
    """Where the configured command `commands.<key>` runs, relative to the root ("" = the root)."""
    if not key or not (Path(root) / ".keel" / "config.yml").is_file():
        return ""
    return part_dir(root, "frontend" if key in FRONTEND_KEYS else "backend")


def located(root: str, ac: str | None = None, layer: str = "API") -> tuple[str | None, str]:
    """(the test command, the folder it runs in relative to the root): a configured command runs in its part's
    folder, a detected one at the root."""
    if _configured(root, ac, layer):
        return _configured(root, ac, layer), part_dir(root, "frontend" if layer == "WEB" else "backend")
    return detected(root, ac), ""


def shown(cmd: str, rel: str) -> str:
    """The command as a person runs it from the repo root."""
    return f"cd {shlex.quote(rel)} && {cmd}" if rel else cmd


# The shell could not start the command at all: retrying an agent changes nothing, the command or its folder is wrong.
CANNOT_RUN = re.compile(r"(^|\n)[^\n]*(: not found|command not found|No such file or directory)\s*($|\n)")


def cannot_run(code: int, output: str) -> bool:
    return code == 127 or (code != 0 and bool(CANNOT_RUN.search((output or "")[:2000])))


def config_commands(root: str) -> dict:
    """The detected commands in .keel/config.yml form ({AC} / {AC_KEY} placeholders), for init to write. They are made
    for detected_dir(root): init writes that folder as backend.dir, where configured commands run."""
    mark = "ZZ-ACZZ"
    d = detected_dir(root)
    base = str(Path(root) / d) if d else root
    one, all_ = _project_stack(root, mark) or _detect_in(base, mark), _project_stack(root) or _detect_in(base)
    out = {}
    if one:
        out["api_test_ac"] = one.replace(mark, "{AC}").replace(ac_id(mark), "{AC_ID}").replace(_pytest_key(mark), "{AC_KEY}")
    if all_:
        out["api_test_module"] = all_
    return out


# 0.18.17 a backend in a subfolder (backend/composer.json, backend/pom.xml, ...) was not detected at all: detection
# looks one level down when the root has nothing, if exactly one folder holds a backend build file (or several do and
# one of them has an obvious backend name). A folder with only a package.json is a frontend, not that folder.
BACKEND_FILES = ("gradlew", "build.gradle", "build.gradle.kts", "mvnw", "pom.xml", "pyproject.toml", "pytest.ini",
                 "setup.py", "composer.json")
BACKEND_NAMES = ("backend", "api", "server")
_SKIP_DIRS = {"node_modules", "build", "dist", "target", "vendor", "venv", ".venv", "__pycache__"}


def detected_dir(root: str) -> str:
    """The folder detected commands are made for, relative to the root: "" (the root) unless the root has no test
    command of its own and one subfolder is the obvious backend."""
    r = Path(root)
    if _detect_in(root) or _project_stack(root):
        return ""
    try:
        kids = sorted(p for p in r.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in _SKIP_DIRS)
    except OSError:
        return ""
    found = [p for p in kids if any((p / f).exists() for f in BACKEND_FILES) and _detect_in(str(p))]
    if len(found) > 1:
        found = [p for p in found if p.name.lower() in BACKEND_NAMES]
    return found[0].name if len(found) == 1 else ""


def _project_stack(root: str, ac: str | None = None) -> str | None:
    """0.18.17 the test command of the project's own stack pack (<root>/.keel/stacks, an api one): the project said
    how its tests run, so it comes before keel's guess from the build files."""
    from ..runtime import stacks

    key = "api_test_ac" if ac else "api_test_module"
    for d in stacks.project_defs(root):
        if str(d.get("lane") or "api") != "api":
            continue
        cmd = str(((d.get("commands") or {}) if isinstance(d.get("commands"), dict) else {}).get(key) or "").strip()
        if cmd:
            build = str((rules.load_config(root).get("backend") or {}).get("build") or "./gradlew")
            return fill(cmd.replace("{BUILD}", build), ac) if ac else cmd.replace("{BUILD}", build)
    return None


def detected(root: str, ac: str | None = None) -> str | None:
    """The test command keel finds by itself (no .keel/config.yml), runnable from the repo root: the project's own
    stack pack, the root's build files, else the one backend subfolder's (as `cd <dir> && ...`)."""
    cmd = _project_stack(root, ac) or _detect_in(root, ac)
    if cmd:
        return cmd
    d = detected_dir(root)
    sub = _detect_in(str(Path(root) / d), ac) if d else None
    return shown(sub, d) if sub else None


def _detect_in(root: str, ac: str | None = None) -> str | None:
    """The test command for the build files in one folder."""
    r = Path(root)
    if (r / "gradlew").exists():
        return fill("./gradlew -q test --tests '*{AC}*'", ac) if ac else "./gradlew -q test"
    if (r / "mvnw").exists() or (r / "pom.xml").exists():
        mvn = "./mvnw" if (r / "mvnw").exists() else "mvn"
        return fill(f"{mvn} -q test -Dtest='*{{AC}}*'", ac) if ac else f"{mvn} -q test"
    if (r / "pyproject.toml").exists() or (r / "pytest.ini").exists() or (r / "setup.py").exists():
        base = "python -m pytest -q"
        return f"{base} -k {shlex.quote(_pytest_key(ac))}" if ac else base
    comp = r / "composer.json"
    if comp.exists():
        # 0.18.17 PHP: phpunit (a PHP method name cannot hold the dash, so the filter is the dash-free id)
        try:
            doc = json.loads(comp.read_text())
        except (OSError, json.JSONDecodeError):
            doc = {}
        doc = doc if isinstance(doc, dict) else {}
        deps = {**(doc.get("require") or {}), **(doc.get("require-dev") or {})}
        if "phpunit/phpunit" in deps or (r / "phpunit.xml").exists() or (r / "phpunit.xml.dist").exists():
            return fill("vendor/bin/phpunit --filter '{AC_ID}'", ac) if ac else "vendor/bin/phpunit"
        if "test" in (doc.get("scripts") or {}):
            return fill("composer test -- --filter '{AC_ID}'", ac) if ac else "composer test"
    pkg = r / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if "test" in scripts:
            if not ac:
                return "npm test --silent"
            # Each runner names its filter differently; node:test does not know Jest's -t.
            runner = scripts["test"]
            flag = "--test-name-pattern=" if "node --test" in runner or "node  --test" in runner else "-t "
            return f"npm test --silent -- {flag}{shlex.quote(ac)}"
    return None


# A run that executed no test at all, or rejected its own options, proves nothing either way.
NO_TESTS = re.compile(r"(^|\n)\s*(ℹ|#)\s*tests 0\b|no tests? (found|ran)|collected 0 items|no test files found|"
                      # Maven Surefire and Gradle: a -Dtest / --tests filter that matched nothing
                      r"no tests (?:were )?executed|no tests matching pattern|no tests to run|no tests found for given includes|"
                      r"0 passed.*0 failed|bad option|unknown (option|argument)|unrecognized (option|argument)", re.I)


def ran_no_tests(output: str) -> bool:
    return bool(NO_TESTS.search(output or ""))


# One line per test result: node:test spec (✔ / ✖), TAP (ok N / not ok N), Jest/Vitest verbose (✓ / ✕ / √).
TEST_LINE = re.compile(r"^\s*(?:✔|✓|√|✖|✕|×|(?:not )?ok \d+\b)", re.M)
PASS_LINE = re.compile(r"^\s*(?:✔|✓|√|ok \d+\b)", re.M)


def ac_test_passed(output: str, ac: str) -> bool | None:
    """Did a test named after the AC run and pass?

    True: a passing result line names the AC. False: the output lists results per test but none of the
    passing ones names the AC (node:test reports a test file with no matching test as a pass, so a
    green run alone proves nothing). None: no per-test lines (pytest -q, gradle -q): the runner's own
    name filter decides, and a zero exit means at least one matching test ran.
    """
    output = output or ""
    if not TEST_LINE.search(output):
        return None
    names = {ac.lower(), _pytest_key(ac), ac_id(ac).lower()}
    for line in output.splitlines():
        if PASS_LINE.match(line) and any(n and n in line.lower() for n in names):
            return True
    return False


def run_split(root: str, command: str, timeout: int = 120, env: dict | None = None,
              limit: int = 50_000_000) -> tuple[int, str, str]:
    """A command whose stdout is data (gh's JSON): (exit code, stdout, stderr), kept apart and never cut at the front
    (run() keeps the tail of both mixed, which cuts a long JSON answer in the middle). Output past `limit` is an error
    (exit 125), not half a document. A timeout is exit 124."""
    posix = os.name == "posix"
    proc = subprocess.Popen(command, shell=True, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            errors="replace", env=env, start_new_session=posix)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if posix:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            _out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            err = ""
        return 124, "", f"timed out after {timeout}s\n{(err or '')[-5000:]}"
    if len(stdout or "") > limit:
        return 125, "", f"the answer is larger than {limit} characters"
    return proc.returncode, stdout or "", (stderr or "")[-20000:]


def run(root: str, command: str, timeout: int = 600, env: dict | None = None) -> tuple[int, str]:
    """Run a shell command in the repo. Returns (exit code, combined output trimmed to the tail).

    The shell runs in its own process group: on a timeout the whole group is killed (gradle and the JVM it started
    too), not only the `sh` in front of them."""
    posix = os.name == "posix"
    proc = subprocess.Popen(command, shell=True, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            errors="replace", env=env, start_new_session=posix)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, ((stdout or "") + (stderr or ""))[-20000:]
    except subprocess.TimeoutExpired:
        try:
            if posix:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:       # a grandchild outside the group still holds the pipes: give up on them
            out, err = "", ""
        return 124, f"timed out after {timeout}s\n{((out or '') + (err or ''))[-5000:]}"
