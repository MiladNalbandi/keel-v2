"""Which command runs a project's tests: `.keel/config.yml` commands first, otherwise detected."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

from .. import rules


def _pytest_key(ac: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", ac.lower()).strip("_")


def command_for(root: str, ac: str | None = None, layer: str = "API") -> str | None:
    cfg = rules.load_config(root)
    cmds = cfg.get("commands") or {}
    key = ("web_test_ac" if layer == "WEB" else "api_test_ac") if ac else ("web_test_module" if layer == "WEB" else "api_test_module")
    custom = cmds.get(key) or (cmds.get("unit_tests") if not ac else None)
    cfg_file = Path(root) / ".keel" / "config.yml"
    if custom and cfg_file.is_file():
        return custom.replace("{AC_KEY}", _pytest_key(ac or "")).replace("{AC}", ac or "")
    r = Path(root)
    if (r / "gradlew").exists():
        return f"./gradlew -q test --tests '*{ac}*'" if ac else "./gradlew -q test"
    if (r / "pyproject.toml").exists() or (r / "pytest.ini").exists() or (r / "setup.py").exists():
        base = "python -m pytest -q"
        return f"{base} -k {shlex.quote(_pytest_key(ac))}" if ac else base
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
    names = {ac.lower(), _pytest_key(ac)}
    for line in output.splitlines():
        if PASS_LINE.match(line) and any(n and n in line.lower() for n in names):
            return True
    return False


def run(root: str, command: str, timeout: int = 600, env: dict | None = None) -> tuple[int, str]:
    """Run a shell command in the repo. Returns (exit code, combined output trimmed to the tail)."""
    try:
        p = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out[-20000:]
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, f"timed out after {timeout}s\n{out[-5000:]}"
