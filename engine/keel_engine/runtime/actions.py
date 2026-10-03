"""Code-step actions: verify_red, verify_green, commit, write_config, ladder, memory_check, push_check, run:<cmd>.

In fake mode the test runs are simulated with fixed outputs (the demo has no test toolchain in the
image). Commits are real whenever the root is a git repository, so COMMIT_RULES are exercised.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .. import rules
from ..tools import git, testcmd
from ..tools.agent_tools import command_env

COMMIT_EXCLUDES = [":!.keel/state.json", ":!.keel/logs", ":!.keel/.state.json*"]


@dataclass
class ActionResult:
    ok: bool
    note: str
    detail: str = ""
    update: dict = field(default_factory=dict)


@dataclass
class ActionInput:
    root: str
    phase: str
    title: str
    ac: dict | None
    acs: list[dict]
    fake: bool
    flow: str


async def run_action(action: str, a: ActionInput) -> ActionResult:
    if action == "verify_red":
        return await verify_red(a)
    if action == "verify_green":
        return await verify_green(a)
    if action == "commit":
        return await asyncio.to_thread(commit, a)
    if action == "write_config":
        return await asyncio.to_thread(write_config, a)
    if action == "ladder":
        return await ladder(a)
    if action == "memory_check":
        return await asyncio.to_thread(memory_check, a)
    if action == "push_check":
        return await asyncio.to_thread(push_check, a)
    if action.startswith("run:"):
        return await run_command(action[4:].strip(), a)
    return ActionResult(False, f"Unknown action {action}.")


def _with_ac_status(acs: list[dict], ac_id: str | None, status: str) -> list[dict]:
    return [dict(x, status=status) if x["id"] == ac_id else dict(x) for x in acs]


async def _tests(a: ActionInput, whole_suite: bool = False) -> tuple[str | None, int, str]:
    ac_id = None if whole_suite else (a.ac or {}).get("id")
    layer = (a.ac or {}).get("layer", "API")
    cmd = testcmd.command_for(a.root, ac_id, layer)
    if not cmd:
        return None, 1, "No test command found. Add commands to .keel/config.yml."
    code, out = await asyncio.to_thread(testcmd.run, a.root, cmd, 900, command_env())
    return cmd, code, out


async def verify_red(a: ActionInput) -> ActionResult:
    ac_id = (a.ac or {}).get("id") or "the bug"
    if a.fake:
        out = f"FAILED tests for {ac_id}\nAssertionError: expected '{ac_id} done' but was None"
        kind = rules.classify_failure(out)
        return ActionResult(True, f"{ac_id}: red confirmed ({kind['kind']} failure, simulated).", out,
                            {"acs": _with_ac_status(a.acs, (a.ac or {}).get("id"), "red")})
    cmd, code, out = await _tests(a)
    if cmd is None:
        return ActionResult(False, out, out)
    if code == 0:
        return ActionResult(False, f"The tests for {ac_id} already pass, so this is not a red state.",
                            f"$ {cmd}\n{out[-3000:]}\nEither the behaviour already exists or the test asserts nothing.")
    kind = rules.classify_failure(out)
    if kind["kind"] == "setup":
        return ActionResult(False, f"The tests for {ac_id} fail from a setup problem, not an assertion (matched \"{kind['matched']}\").",
                            f"$ {cmd}\n{out[-3000:]}")
    return ActionResult(True, f"{ac_id}: red confirmed (assertion failure).", f"$ {cmd}\n{out[-3000:]}",
                        {"acs": _with_ac_status(a.acs, (a.ac or {}).get("id"), "red")})


async def verify_green(a: ActionInput) -> ActionResult:
    ac_id = (a.ac or {}).get("id")
    label = ac_id or "the suite"
    if a.fake:
        return ActionResult(True, f"{label}: green (simulated).", "1 passed",
                            {"acs": _with_ac_status(a.acs, ac_id, "green")} if ac_id else {})
    cmd, code, out = await _tests(a, whole_suite=not ac_id)
    if cmd is None:
        return ActionResult(False, out, out)
    if code != 0:
        return ActionResult(False, f"{label} does not pass yet.", f"$ {cmd}\n{out[-3000:]}")
    return ActionResult(True, f"{label}: green.", f"$ {cmd}\n{out[-1500:]}",
                        {"acs": _with_ac_status(a.acs, ac_id, "green")} if ac_id else {})


def commit(a: ActionInput) -> ActionResult:
    if not git.is_repo(a.root):
        return ActionResult(True, "Not a git repository; nothing committed.")
    ctype = rules.commit_type_for(a.phase)
    git.git(a.root, "add", "-A", "--", ".", *COMMIT_EXCLUDES)
    staged = [f for f in git.git(a.root, "diff", "--cached", "--name-only").stdout.splitlines() if f.strip()]
    if not staged:
        return ActionResult(True, "Nothing to commit.")
    cfg = rules.load_config(a.root)
    v = rules.check_commit(ctype, staged, cfg)
    if not v.ok:
        git.git(a.root, "reset", "-q")
        return ActionResult(False, f"The {ctype} commit was refused.", v.reason)
    rule = rules.COMMIT_RULES[ctype]
    ident = "" if rule.get("noId") else ((a.ac or {}).get("id") or "BUG")
    subject = (a.ac or {}).get("title") or a.title or a.flow
    if ctype == "setup":
        subject = "keel init"
    message = f"{rules.commit_prefix(ctype, ident)}: {subject}"[:200]
    author = cfg.get("commit", {})
    r = git.git(a.root, "-c", f"user.name={author.get('author_name', 'keelbot')}",
                "-c", f"user.email={author.get('author_email', 'keel.dev.bot@gmail.com')}", "commit", "-q", "-m", message)
    if r.returncode != 0:
        git.git(a.root, "reset", "-q")
        return ActionResult(False, "git commit failed.", (r.stderr or r.stdout)[-2000:])
    sha = git.head(a.root)
    return ActionResult(True, f"{message} {sha[:7] if sha else ''}".strip(), "\n".join(staged), {"git_head": sha})


def _detect(root: str) -> dict:
    r = Path(root)
    cfg: dict = {"version": 4}
    if (r / "gradlew").exists():
        cfg["commands"] = {"api_test_ac": "./gradlew -q test --tests '*{AC}*'", "api_test_module": "./gradlew -q test"}
    elif (r / "pyproject.toml").exists():
        cfg["backend"] = {"dir": "", "build": "python -m pytest"}
        cfg["commands"] = {"api_test_ac": "python -m pytest -q -k {AC_KEY}", "api_test_module": "python -m pytest -q"}
    elif (r / "package.json").exists():
        cfg["commands"] = {"api_test_ac": "npm test --silent -- -t {AC}", "api_test_module": "npm test --silent"}
    if (r / "docs" / "specs").is_dir():
        cfg["specs"] = {"dir": "docs/specs"}
    return cfg


def write_config(a: ActionInput) -> ActionResult:
    f = Path(a.root) / ".keel" / "config.yml"
    if f.exists():
        return ActionResult(True, ".keel/config.yml already exists; left as it is.")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# Written by keel v2 init. Edit freely.\n" + yaml.safe_dump(_detect(a.root), sort_keys=False))
    return ActionResult(True, "Wrote .keel/config.yml.", f.read_text())


async def ladder(a: ActionInput) -> ActionResult:
    cmd = testcmd.command_for(a.root)
    lines = ["# Running this project", "", "Written by keel init.", ""]
    if cmd:
        lines += ["## Tests", "", "```", cmd, "```", ""]
    if a.fake or not cmd:
        result = ActionResult(True, "Setup ladder: tests command found (simulated run)." if cmd else "Setup ladder: no test command found.")
    else:
        code, out = await asyncio.to_thread(testcmd.run, a.root, cmd, 900, command_env())
        result = ActionResult(code == 0, f"Setup ladder: `{cmd}` exited {code}.", out[-3000:])
    f = Path(a.root) / "docs" / "RUNNING.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(lines))
    return result


def memory_check(a: ActionInput) -> ActionResult:
    kdir = Path(a.root) / "docs" / "knowledge"
    files = sorted(kdir.glob("*.md")) if kdir.is_dir() else []
    problems = [f"{p.relative_to(a.root)} is empty" for p in files if not p.read_text().strip()]
    if not files:
        problems.append("docs/knowledge/ has no sections")
    verdict = {"sha": git.head(a.root) if git.is_repo(a.root) else None, "pass": not problems, "problems": problems,
               "at": datetime.now(timezone.utc).isoformat()}
    out = Path(a.root) / ".keel" / "memory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(verdict, indent=2) + "\n")
    if problems:
        return ActionResult(False, "The knowledge base has problems.", "\n".join(problems))
    return ActionResult(True, f"Knowledge base: {len(files)} sections, no problems.")


def push_check(a: ActionInput) -> ActionResult:
    if not git.is_repo(a.root):
        return ActionResult(True, "Not a git repository.")
    dirty = git.dirty(a.root)
    if dirty:
        return ActionResult(False, f"{len(dirty)} uncommitted file(s) before push.", "\n".join(sorted(dirty)))
    return ActionResult(True, "Working tree clean; ready to push (keel never pushes for you).")


async def run_command(cmd: str, a: ActionInput) -> ActionResult:
    v = rules.check_bash(a.phase, cmd, rules.load_config(a.root), exists=lambda rel: (Path(a.root) / rel).exists())
    if not v.ok:
        return ActionResult(False, "Command refused.", v.reason)
    code, out = await asyncio.to_thread(testcmd.run, a.root, cmd, 900, command_env())
    return ActionResult(code == 0, f"`{cmd}` exited {code}.", out[-3000:])
