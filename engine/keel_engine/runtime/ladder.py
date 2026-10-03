"""The run ladder (keel v1 skills/init/references/ladder.md): twelve rungs, cheapest first.

    rung = {n, name, cmd, status: pass|fail|fixing|waiting|skipped, detail?}

Each rung must pass before the next runs; rungs after a failure stay "waiting". A rung with no
command is "skipped". Rungs 7-11 are optional: they run only when `.keel/config.yml` names their
command. Simulated mode (fake models) runs nothing and is deterministic: a rung with a command
passes, one without is skipped. On a retry the rung that failed last time shows as "fixing" while
it runs again. The result goes to `.keel/ladder.json` ({at, rungs}).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import config, rules
from ..tools import testcmd

NAMES = ["Toolchain", "Dependencies resolve", "Frontend dependencies", "Both apps compile", "Frontend typecheck",
         "Unit tests", "A container-backed test", "Services up and healthy", "API boots and is healthy",
         "Frontend serves", "Smoke check", "Hook self-test"]
OPTIONAL = {7, 8, 9, 10, 11}
CONFIG_KEY = {2: "deps_api", 3: "deps_web", 4: "api_compile", 5: "web_typecheck", 6: "unit_tests", 7: "testcontainers_test",
              8: "services", 9: "api_health_check", 10: "web_health_check", 11: "smoke"}
FILE = "ladder.json"


def _custom(root: str) -> dict:
    """Commands from the project's own .keel/config.yml (not keel's defaults)."""
    if not (Path(root) / ".keel" / "config.yml").is_file():
        return {}
    return rules.load_config(root).get("commands") or {}


def _toolchain(root: str) -> list[str]:
    r = Path(root)
    need = ["git"]
    if (r / "gradlew").exists() or (r / "pom.xml").exists():
        need.append("java")
    if (r / "package.json").exists():
        need.append("node")
    if (r / "pyproject.toml").exists() or (r / "setup.py").exists():
        need.append("python3")
    return need


def plan(root: str) -> list[dict]:
    """Every rung with the command it would run (None = skipped), nothing run."""
    r = Path(root)
    c = _custom(root)
    cmds: dict[int, str | None] = {n: c.get(k) for n, k in CONFIG_KEY.items()}
    if not cmds[2]:
        if (r / "gradlew").exists():
            cmds[2] = "./gradlew -q help"
        elif (r / "uv.lock").exists():
            cmds[2] = "uv sync --frozen"
    if not cmds[3] and (r / "package.json").exists():
        lock = {"pnpm-lock.yaml": "pnpm install --frozen-lockfile", "yarn.lock": "yarn install --frozen-lockfile",
                "package-lock.json": "npm ci"}
        cmds[3] = next((v for f, v in lock.items() if (r / f).exists()), None)
    if not cmds[4] and (r / "pyproject.toml").exists() and (r / "src").is_dir():
        cmds[4] = "python3 -m compileall -q src"
    if not cmds[6]:
        cmds[6] = c.get("api_test_module") or testcmd.command_for(root)
    out = [{"n": 1, "name": NAMES[0], "cmd": "check: " + ", ".join(_toolchain(root)), "status": "waiting"}]
    for n in range(2, 12):
        out.append({"n": n, "name": NAMES[n - 1], "cmd": cmds.get(n), "status": "waiting"})
    keel = config.keel_home() / "bin" / "keel"
    out.append({"n": 12, "name": NAMES[11], "cmd": f"{keel} doctor --hooks" if keel.is_file() else None, "status": "waiting"})
    for rung in out:
        if not rung["cmd"]:
            rung["status"] = "skipped"
            rung["detail"] = ("optional: no commands." + CONFIG_KEY[rung["n"]] + " in .keel/config.yml") if rung["n"] in OPTIONAL \
                else ("keel is not installed at " + str(config.keel_home()) if rung["n"] == 12 else "no command for this stack")
    return out


def previous(root: str) -> list[dict]:
    try:
        return json.loads((Path(root) / ".keel" / FILE).read_text()).get("rungs") or []
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def save(root: str, rungs: list[dict]):
    d = Path(root) / ".keel"
    d.mkdir(parents=True, exist_ok=True)
    (d / FILE).write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rungs": rungs}, indent=2) + "\n")


def run(root: str, simulate: bool, runner: Callable[[str, str], tuple[int, str]]) -> tuple[bool, list[dict]]:
    """Run the ladder. `runner(root, cmd) -> (exit code, output)`. Returns (all required rungs passed, rungs)."""
    rungs = plan(root)
    failed_before = {r["n"] for r in previous(root) if r.get("status") in ("fail", "fixing")}
    ok = True
    for rung in rungs:
        if rung["status"] == "skipped":
            continue
        if not ok:
            rung["detail"] = "waits for the rung that failed"
            continue
        if rung["n"] in failed_before:
            rung["status"] = "fixing"
            save(root, rungs)
        if rung["n"] == 1:
            missing = [t for t in _toolchain(root) if not simulate and not shutil.which(t)]
            rung["status"], rung["detail"] = ("fail", "missing: " + ", ".join(missing)) if missing else ("pass", "found")
        elif simulate:
            rung["status"], rung["detail"] = "pass", "simulated"
        else:
            code, out = runner(root, rung["cmd"])
            rung["status"] = "pass" if code == 0 else "fail"
            rung["detail"] = f"exit {code}" + (f": {out.strip()[-400:]}" if code else "")
        if rung["status"] == "fail":
            ok = False
    save(root, rungs)
    return ok, rungs
