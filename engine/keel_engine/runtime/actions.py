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
from ..rules import checks
from ..tools import git, testcmd
from ..tools.agent_tools import command_env
from . import blockers as push_gates
from . import ladder as run_ladder

COMMIT_EXCLUDES = [":!.keel/state.json", ":!.keel/logs", ":!.keel/.state.json*"]


@dataclass
class ActionResult:
    ok: bool
    note: str
    detail: str = ""
    update: dict = field(default_factory=dict)
    # A question for the user instead of a plain failure: {type: deps|escalate, kind, title, detail, ...}.
    # The code step turns it into an interrupt and runs the step again with the answer applied.
    ask: dict | None = None


@dataclass
class ActionInput:
    root: str
    phase: str
    title: str
    ac: dict | None
    acs: list[dict]
    fake: bool
    flow: str
    deps: list[str] = field(default_factory=list)      # dependencies the user approved (keel v1 state.deps)
    gates_log: list[str] = field(default_factory=list)
    base: str | None = None                            # HEAD when the thread started: the branch diff starts here
    unlocks: list[dict] = field(default_factory=list)
    preexisting: dict = field(default_factory=dict)    # the user's uncommitted files at start: {path: fingerprint}


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



def tail(text: str, limit: int) -> str:
    """The end of a command's output, cut at a line start, saying how much was left out."""
    text = text or ""
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    cut = cut[cut.find("\n") + 1:] if "\n" in cut else cut
    hidden = text[: len(text) - len(cut)].count("\n")
    return f"… {hidden} earlier lines not shown\n{cut}"

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
    if testcmd.ran_no_tests(out):
        return ActionResult(False, f"No test for {ac_id} ran, so this is not a red state.",
                            f"$ {cmd}\n{tail(out, 3000)}\nWrite a failing test whose name contains {ac_id}.")
    if code == 0:
        named = testcmd.ac_test_passed(out, ac_id) if a.ac else None
        if named is False:
            return ActionResult(False, f"No test for {ac_id} ran, so this is not a red state.",
                                f"$ {cmd}\n{tail(out, 3000)}\nWrite a failing test whose name contains {ac_id}.")
        if a.ac:
            # keel v1's "already-met": the AC's own test ran and passed, so code from earlier criteria covers it.
            # Retrying the red agent cannot change that; the user decides (approve = mark as already met).
            return ActionResult(False, f"{ac_id} already passes.", f"$ {cmd}\n{tail(out, 3000)}", ask={
                "type": "already-met", "kind": "gate", "title": f"{ac_id} already passes",
                "detail": (f"The test for {ac_id} ran and passed before any new code was written: the code from earlier "
                           f"criteria already covers it.\n\nApprove to mark {ac_id} as already met: its test is committed "
                           f"as test({ac_id}) and its green, review and gate steps are skipped.\nReject to send it back to "
                           f"the red step with your note, to write a stricter test that fails today.\n\n"
                           f"$ {cmd}\n{tail(out, 1500)}")})
        return ActionResult(False, f"The tests for {ac_id} already pass, so this is not a red state.",
                            f"$ {cmd}\n{tail(out, 3000)}\nEither the behaviour already exists or the test asserts nothing.")
    kind = rules.classify_failure(out)
    if kind["kind"] == "setup":
        return ActionResult(False, f"The tests for {ac_id} fail from a setup problem, not an assertion (matched \"{kind['matched']}\").",
                            f"$ {cmd}\n{tail(out, 3000)}")
    return ActionResult(True, f"{ac_id}: red confirmed (assertion failure).", f"$ {cmd}\n{tail(out, 3000)}",
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
    if ac_id and testcmd.ran_no_tests(out):
        return ActionResult(False, f"No test for {ac_id} ran, so it cannot be green.",
                            f"$ {cmd}\n{tail(out, 3000)}\nThe RED test for {ac_id} is missing. Rewind to its red step.")
    if code != 0:
        return ActionResult(False, f"{label} does not pass yet.", f"$ {cmd}\n{tail(out, 3000)}")
    return ActionResult(True, f"{label}: green.", f"$ {cmd}\n{tail(out, 1500)}",
                        {"acs": _with_ac_status(a.acs, ac_id, "green")} if ac_id else {})


def _refuse(a: ActionInput, note: str, detail: str, **extra) -> ActionResult:
    git.git(a.root, "reset", "-q")
    return ActionResult(False, note, detail, **extra)


def commit(a: ActionInput) -> ActionResult:
    """keel v1 `keel commit`: bucket rules plus the diff-level checks, then a real commit."""
    if not git.is_repo(a.root):
        return ActionResult(True, "Not a git repository; nothing committed.")
    ctype = rules.commit_type_for(a.phase)
    git.git(a.root, "add", "-A", "--", ".", *COMMIT_EXCLUDES)
    # Never sweep the user's own uncommitted work into a keel commit: a file that was already changed when the
    # flow started, and that no agent has touched since, is unstaged again.
    theirs = [f for f, fp in a.preexisting.items() if git.fingerprint(a.root, f) == fp]
    if theirs:
        git.git(a.root, "reset", "-q", "--", *theirs)
    staged = [f for f in git.git(a.root, "diff", "--cached", "--name-only").stdout.splitlines() if f.strip()]
    if not staged:
        return ActionResult(True, "Nothing to commit.")
    cfg = rules.load_config(a.root)
    diff = git.git(a.root, "diff", "--cached", "-U0").stdout

    # The staged diff is the last place a secret can be stopped before it is in history.
    found = checks.secrets_in_diff(diff)
    if found:
        kinds = sorted({f["why"] for f in found})
        return _refuse(a, f"The {ctype} commit stages what looks like a secret: {', '.join(kinds)}.",
                       "\n".join(f"  {f['file']}: {f['why']}" for f in found) +
                       "\nRemove it and read it from an environment variable. A fixture line may carry the marker keel:allow-secret.",
                       update={"blockers": push_gates.push_blockers(a.root, a.base, found)})

    # A new dependency outlives the branch: a human approves it (keel v1 refuses; v2 asks).
    if a.phase not in (None, "", "none"):
        adds = checks.unapproved_additions(diff, a.deps)
        if adds:
            names = sorted({checks.dependency_name(d["line"]) for d in adds})
            files = sorted({d["file"] for d in adds})
            detail = ("This commit adds " + ("a dependency" if len(adds) == 1 else "dependencies") + " to a manifest:\n" +
                      "\n".join(f"  {d['file']}: {d['line']}" for d in adds) +
                      "\n\nApprove to keep it and commit. Reject to put the manifest back as it was and commit the rest.")
            git.git(a.root, "reset", "-q")
            return ActionResult(False, f"New dependency needs approval: {', '.join(names)}.", detail,
                                ask={"type": "deps", "kind": "fix", "title": "Approve new dependency", "detail": detail,
                                     "deps": names, "files": files})

    # A file the user unlocked for this phase passes the bucket rule too; otherwise "allow this file"
    # would only move the refusal from the edit to the commit. (keel v1 checks unlocks at edit time only.)
    checked = [f for f in staged if not rules.unlocked(a.unlocks, f, a.phase)] or staged[:0]
    v = rules.check_commit(ctype, checked, cfg) if checked else rules.CommitVerdict(True)
    if not v.ok:
        return _refuse(a, f"The {ctype} commit was refused.", v.reason)
    buckets = [(f, rules.classify(cfg, f)) for f in staged]

    if rules.COMMIT_RULES[ctype].get("trivial"):
        edited = [f for f, b in buckets if b in ("api-test", "web-test", "e2e") and git.tracked_in_head(a.root, f)]
        if edited:
            return _refuse(a, "This is not a trivial change: it edits existing tests.",
                           "\n".join("  " + f for f in edited) + "\nRun it as a small change flow through the AC loop instead.")

    if ctype == "coverage":
        added = []
        for f, b in buckets:
            if b in ("api-main", "web-src"):
                num = git.git(a.root, "diff", "--cached", "--numstat", "--", f).stdout.strip().split("\t")
                if num and num[0].isdigit() and int(num[0]) > 0:
                    added.append(f)
        if added:
            return _refuse(a, "A coverage commit may only delete unreachable production lines, not add any.",
                           "\n".join("  " + f for f in added) + "\nIf the code must change to be testable, that is an AC, not a coverage fix.")
        if ".keel/config.yml" in staged:
            return _refuse(a, "A coverage commit may not edit .keel/config.yml.",
                           "Raising coverage cannot include lowering the threshold.")

    if a.flow == "change" and not any(line.startswith("escalation-override") for line in a.gates_log):
        must = [t for t in checks.triggers(cfg, staged) if t["must"]]
        if must:
            why = "; ".join(t["why"] for t in must)
            detail = (f"Escalation trigger: {why}.\n\nApprove to stop this change flow and start a feature flow with a spec. "
                      "Reject (with a reason) to stay a small change and commit.")
            git.git(a.root, "reset", "-q")
            return ActionResult(False, f"Escalation trigger: {why}.", detail,
                                ask={"type": "escalate", "kind": "gate", "title": "Escalate to a feature flow?", "detail": detail,
                                     "why": why})

    rule = rules.COMMIT_RULES[ctype]
    ident = "" if rule.get("noId") else ((a.ac or {}).get("id") or ("review" if a.phase == "review-fix" else "BUG"))
    subject = (a.ac or {}).get("title") or a.title or a.flow
    if ctype == "setup":
        subject = "keel init"
    message, body = commit_message(rules.commit_prefix(ctype, ident), subject)
    author = cfg.get("commit", {})
    extra = ["-m", body] if body else []
    r = git.git(a.root, "-c", f"user.name={author.get('author_name', 'keelbot')}",
                "-c", f"user.email={author.get('author_email', 'keel.dev.bot@gmail.com')}", "commit", "-q", "-m", message, *extra)
    if r.returncode != 0:
        git.git(a.root, "reset", "-q")
        return ActionResult(False, "git commit failed.", (r.stderr or r.stdout)[-2000:])
    sha = git.head(a.root)
    return ActionResult(True, f"{message} {sha[:7] if sha else ''}".strip(), "\n".join(staged),
                        {"git_head": sha, "blockers": push_gates.push_blockers(a.root, a.base)})


def commit_message(prefix: str, subject: str, limit: int = 72) -> tuple[str, str]:
    """A short subject line (whole words, at most `limit` chars) and the full criterion as the body when it was cut."""
    text = " ".join(str(subject).split())
    first = f"{prefix}: {text}"
    if len(first) <= limit:
        return first, ""
    room = limit - len(prefix) - 3
    cut = text[:room].rsplit(" ", 1)[0].rstrip(",;:.") or text[:room]
    return f"{prefix}: {cut}…", text


def revert_manifests(root: str, files: list[str]):
    """Put manifest files back as they are in HEAD (a new one is removed)."""
    for rel in files:
        if git.tracked_in_head(root, rel):
            git.git(root, "checkout", "HEAD", "--", rel)
        else:
            (Path(root) / rel).unlink(missing_ok=True)


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
    def runner(root: str, cmd: str) -> tuple[int, str]:
        return testcmd.run(root, cmd, 900, command_env())

    ok, rungs = await asyncio.to_thread(run_ladder.run, a.root, a.fake, runner)
    lines = ["# Running this project", "", "Written by keel init. Every command below passed the run ladder.", ""]
    for r in rungs:
        if r["status"] == "pass" and r["n"] != 1:
            lines += [f"## {r['n']}. {r['name']}", "", "```", r["cmd"], "```", ""]
    f = Path(a.root) / "docs" / "RUNNING.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(lines))
    passed = sum(r["status"] == "pass" for r in rungs)
    skipped = sum(r["status"] == "skipped" for r in rungs)
    sim = " (simulated)" if a.fake else ""
    if ok:
        return ActionResult(True, f"Setup ladder{sim}: {passed} passed, {skipped} skipped.", "", {"ladder": rungs})
    bad = next(r for r in rungs if r["status"] == "fail")
    return ActionResult(False, f"Setup ladder: rung {bad['n']} ({bad['name']}) failed.", f"$ {bad['cmd']}\n{bad.get('detail', '')}",
                        {"ladder": rungs})


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
        return ActionResult(True, "Not a git repository.", update={"blockers": []})
    found = push_gates.push_blockers(a.root, a.base)
    dirty = git.dirty(a.root)
    if dirty:
        return ActionResult(False, f"{len(dirty)} uncommitted file(s) before push.", "\n".join(sorted(dirty)), {"blockers": found})
    if found:
        # Verdicts are keel v1's to produce (keel verify ...); the board shows them, keel never pushes.
        return ActionResult(True, f"Working tree clean; {len(found)} push blocker(s): " + ", ".join(b["gate"] for b in found) + ".",
                            "\n".join(f"{b['gate']}: {b['why']} (fix: {b['fix']})" for b in found), {"blockers": found})
    return ActionResult(True, "Working tree clean; ready to push (keel never pushes for you).", update={"blockers": []})


async def run_command(cmd: str, a: ActionInput) -> ActionResult:
    v = rules.check_bash(a.phase, cmd, rules.load_config(a.root), exists=lambda rel: (Path(a.root) / rel).exists())
    if not v.ok:
        return ActionResult(False, "Command refused.", v.reason)
    code, out = await asyncio.to_thread(testcmd.run, a.root, cmd, 900, command_env())
    return ActionResult(code == 0, f"`{cmd}` exited {code}.", tail(out, 3000))
