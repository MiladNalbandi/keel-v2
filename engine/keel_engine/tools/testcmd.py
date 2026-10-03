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
            return f"npm test --silent -- -t {shlex.quote(ac)}" if ac else "npm test --silent"
    return None


def run(root: str, command: str, timeout: int = 600, env: dict | None = None) -> tuple[int, str]:
    """Run a shell command in the repo. Returns (exit code, combined output trimmed to the tail)."""
    try:
        p = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True, timeout=timeout, env=env)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out[-20000:]
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, f"timed out after {timeout}s\n{out[-5000:]}"
