"""Code-step actions: verify_red, verify_green, verify_release, verify_coverage, commit, write_config, ladder,
knowledge_check (memory_check is its old name), push_check, run:<cmd>.

In fake mode the test runs are simulated with fixed outputs (the demo has no test toolchain in the
image). Commits are real whenever the root is a git repository, so COMMIT_RULES are exercised.
Checks that decide a push write a verdict to the engine DB (runtime/verdicts.py): knowledge_check -> memory,
verify_release and a whole-suite verify_green -> release, verify_coverage -> coverage. Simulated runs write none.
The other verdict actions (verify_fast, verify_module, verify_deps, audit, trace, arch) and the PR (pr, open_pr) live
in runtime/verdict_actions.py; review_lenses and coverage_report in runtime/ship.py; the review, diagnose, fix and change
helpers in runtime/flow_actions.py; start_flow and escalate_model are the compiler's (they start a thread, change a model).
The hunt's backlog actions (hunt_*) are in runtime/hunt_actions.py, init's architecture and ladder-repair steps
in runtime/init_actions.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .. import rules
from ..rules import checks
from ..tools import codegraph, git, testcmd
from ..tools.agent_tools import command_env
from . import blockers as push_gates
from . import flow_actions, knowledge, ship, verdict_actions, verdicts
from . import ladder as run_ladder

COMMIT_EXCLUDES = [f":!{p.rstrip('/')}" for p in git.ENGINE_FILES]
VERDICT_ACTIONS = {
    "verify_fast": verdict_actions.verify_fast, "verify_module": verdict_actions.verify_module,
    "verify_deps": verdict_actions.verify_deps, "audit": verdict_actions.audit, "trace": verdict_actions.trace,
    "trace_strict": lambda a: verdict_actions.trace(a, strict=True), "arch": verdict_actions.arch,
    "pr": verdict_actions.pr, "open_pr": verdict_actions.open_pr,
    "review_lenses": ship.review_lenses, "coverage_report": ship.coverage_report,
}


@dataclass
class ActionResult:
    ok: bool
    note: str
    detail: str = ""
    update: dict = field(default_factory=dict)
    # A question for the user instead of a plain failure: {type: deps|escalate, kind, title, detail, ...}.
    # The code step turns it into an interrupt and runs the step again with the answer applied.
    ask: dict | None = None
    # A refusal no retry can change (nothing to review, an unknown argument): the flow stops and says why.
    stop: bool = False


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
    init: dict = field(default_factory=dict)           # keel init's answers (runs_on, services, knowledge_sections)
    project: str = ""                                  # the api's project id: verdicts are stored under it
    item: dict | None = None                           # a for_each loop's current item
    data: dict = field(default_factory=dict)           # the flow's data lists (state.data)
    keys: dict = field(default_factory=dict)           # the thread's logins (open_pr reads a GitHub token), memory only
    state: dict = field(default_factory=dict)          # a copy of the flow state (the pr action reads spec, gates, unlocks)
    step: str = ""                                     # the code step running the action (markers it sets go there)
    request: str = ""                                  # what the user asked for, in their words
    settings: dict = field(default_factory=dict)       # the thread's settings (StartThread.settings: a flow's options)
    thread_id: str = ""                                # the thread running this action (a hunt run records it)

    @property
    def key(self) -> str:
        return self.project or self.root


async def run_action(action: str, a: ActionInput) -> ActionResult:
    if action == "verify_red":
        return await verify_red(a)
    if action == "verify_green":
        return await verify_green(a)
    if action == "verify_release":
        return await verify_release(a)
    if action == "verify_coverage":
        return await verify_coverage(a)
    if action in VERDICT_ACTIONS:
        return await VERDICT_ACTIONS[action](a)
    if action in flow_actions.ACTIONS:
        return await asyncio.to_thread(flow_actions.ACTIONS[action], a)
    if action == "commit":
        return await asyncio.to_thread(commit, a)
    if action == "write_config":
        return await asyncio.to_thread(write_config, a)
    if action == "ladder":
        return await ladder(a)
    if action in ("knowledge_check", "memory_check"):
        return await asyncio.to_thread(knowledge_check, a)
    if action == "push_check":
        return await asyncio.to_thread(push_check, a)
    if action.startswith("run:"):
        return await run_command(action[4:].strip(), a)
    more = _flow_actions()
    if action in more:
        return await more[action](a)
    return ActionResult(False, f"Unknown action {action}.")


def _flow_actions() -> dict:
    """The actions of single flows (hunt, hunt-next, init's extra steps); imported late, they import this module."""
    from . import hunt_actions, init_actions
    return {**hunt_actions.ACTIONS, **init_actions.ACTIONS}



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
    if not ac_id:
        # The whole suite green on these files is the release verdict for the commit made from them.
        await asyncio.to_thread(_release_verdict, a, True, cmd, out)
    return ActionResult(True, f"{label}: green.", f"$ {cmd}\n{tail(out, 1500)}",
                        {"acs": _with_ac_status(a.acs, ac_id, "green")} if ac_id else {})


def _release_verdict(a: ActionInput, ok: bool, cmd: str, out: str, flaky: list | None = None) -> dict:
    head, tree = verdicts.stamp(a.root)
    detail = {"command": cmd, "tree": tree, "summary": None if ok else "the test suite failed", "output": tail(out, 1500)}
    if flaky:
        detail.update(flaky=flaky, summary="passed on the rerun (flaky)")
    return verdicts.write(a.key, "release", ok, detail, head)


async def verify_release(a: ActionInput) -> ActionResult:
    """The whole test suite (the module command); its result is the release verdict, pass or fail.
    A failing run runs once more (loops.flaky_reruns, default 1): failed then passed is flaky, a pass with a warning."""
    if a.fake:
        return ActionResult(True, "Release suite: green (simulated, no verdict written).", "1 passed")
    cmd = testcmd.command_for(a.root, None, "API")
    if cmd is None:
        out = "No test command found. Add commands to .keel/config.yml."
        return ActionResult(False, out, out)
    reruns = int((rules.load_config(a.root).get("loops") or {}).get("flaky_reruns", 1) or 0)
    r = await asyncio.to_thread(verdict_actions.run_suite, a.root, cmd, "release suite", reruns)
    await asyncio.to_thread(_release_verdict, a, r["ok"], cmd, r["out"], r["flaky"])
    upd = verdict_actions.flaky_update(a, r["flaky"])
    if not r["ok"]:
        return ActionResult(False, "The release suite fails.", f"$ {cmd}\n{tail(r['out'], 3000)}")
    return ActionResult(True, "Release suite: green; verdict recorded for this commit." + verdict_actions._flaky_note(r["flaky"]),
                        f"$ {cmd}\n{tail(r['out'], 1500)}", upd)


async def verify_coverage(a: ActionInput) -> ActionResult:
    """`commands.coverage` from .keel/config.yml; exit 0 = the coverage gate passes. Without it there is no coverage gate.

    With coverage.reports (jacoco/kover XML or lcov, keel v1's keys), the reports decide instead: the lines changed since
    the base branch, coverage.changed_lines / changed_branches / global, 100% on security.coverage_paths; the uncovered
    lines become groups (same file, at most 8 lines apart) in state.data.coverage_groups for a cover loop."""
    cfg = rules.load_config(a.root)
    cmd = (cfg.get("commands") or {}).get("coverage")
    if verdict_actions.reports_of(cfg):
        if a.fake:
            return ActionResult(True, "Coverage: passed (simulated, no verdict written).")
        # The reports come from commands.coverage, else commands.coverage_<app> (keel's config template) in the app's dir.
        runs = [(cmd, a.root)] if cmd else [
            (c, str(Path(a.root) / d) if d and (Path(a.root) / d).is_dir() else a.root)
            for app, key in (("api", "backend"), ("web", "frontend")) if app in verdict_actions.reports_of(cfg)
            for c, d in [(str((cfg.get("commands") or {}).get(f"coverage_{app}") or "").strip(), verdict_actions._dir_of(cfg, key))] if c]
        for cmd, cwd in runs:
            code, out = await asyncio.to_thread(testcmd.run, cwd, cmd, 1800, command_env())
            if code != 0:
                await asyncio.to_thread(verdict_actions.record_verdict, a.key, "coverage", False,
                                        {"command": cmd, "summary": f"`{cmd}` exited {code}", "output": tail(out, 1500)}, None, a.root)
                err = f"`{cmd}` exited {code}, so there is no report to read.\n\n$ {cmd}\n{tail(out, 2000)}"
                return ActionResult(False, f"The coverage command `{cmd}` failed.", f"$ {cmd}\n{tail(out, 3000)}",
                                    {"data": {**a.data, "coverage_groups": [], "coverage_error": err}})
        _m, r = await asyncio.to_thread(verdict_actions.coverage_from_reports, a)
        return r
    if not cmd:
        return ActionResult(True, "No coverage command in .keel/config.yml (commands.coverage); coverage is not checked.")
    if a.fake:
        return ActionResult(True, "Coverage: passed (simulated, no verdict written).")
    code, out = await asyncio.to_thread(testcmd.run, a.root, cmd, 1800, command_env())
    head, tree = await asyncio.to_thread(verdicts.stamp, a.root)
    await asyncio.to_thread(verdicts.write, a.key, "coverage", code == 0,
                            {"command": cmd, "tree": tree, "summary": None if code == 0 else f"`{cmd}` exited {code}",
                             "output": tail(out, 1500)}, head)
    if code != 0:
        return ActionResult(False, f"Coverage is below what `{cmd}` accepts.", f"$ {cmd}\n{tail(out, 3000)}")
    return ActionResult(True, "Coverage: passed; verdict recorded for this commit.", f"$ {cmd}\n{tail(out, 1500)}")


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
                       update={"blockers": push_gates.push_blockers(a.root, a.base, found, project=a.key)})

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
    bug = ctype in ("fix", "red") or (ctype == "e2e" and a.flow == "fix")
    ident = "" if rule.get("noId") else ((a.ac or {}).get("id") or ("review" if a.phase == "review-fix" else "BUG" if bug else ""))
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
    codegraph.sync_later(a.root)            # the code graph follows every keel commit (best effort, in the background)
    return ActionResult(True, f"{message} {sha[:7] if sha else ''}".strip(), "\n".join(staged),
                        {"git_head": sha, "blockers": push_gates.push_blockers(a.root, a.base, project=a.key)})


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
    if (r / "pyproject.toml").exists():
        cfg["backend"] = {"dir": "", "build": "python -m pytest"}
    # The same commands keel uses without a config (node:test needs --test-name-pattern, not Jest's -t).
    cmds = testcmd.config_commands(root)
    if cmds:
        cfg["commands"] = cmds
    if (r / "docs" / "specs").is_dir():
        cfg["specs"] = {"dir": "docs/specs"}
    return cfg


def write_config(a: ActionInput) -> ActionResult:
    f = Path(a.root) / ".keel" / "config.yml"
    if f.exists():
        return ActionResult(True, ".keel/config.yml already exists; left as it is.")
    f.parent.mkdir(parents=True, exist_ok=True)
    cfg = _detect(a.root)
    if a.init:
        cfg["init"] = {k: v for k, v in a.init.items() if k != "said"}
    f.write_text("# Written by keel v2 init. Edit freely.\n" + yaml.safe_dump(cfg, sort_keys=False))
    return ActionResult(True, "Wrote .keel/config.yml.", f.read_text())


async def ladder(a: ActionInput) -> ActionResult:
    def runner(root: str, cmd: str) -> tuple[int, str]:
        return testcmd.run(root, cmd, 900, command_env())

    ok, rungs = await asyncio.to_thread(run_ladder.run, a.root, a.fake, runner, bool(a.settings.get("fast")))
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


def knowledge_check(a: ActionInput) -> ActionResult:
    """keel v1's memory check: citations resolve, no placeholders, chosen sections exist. Writes the memory verdict."""
    r = knowledge.check(a.root)
    checked = [s for s in r["sections"] if not s.get("not_selected")]
    verdicts.write(a.key, "memory", r["pass"], {
        "problems": r["problems"], "selected": r["selected"], "content": knowledge.content_hash(a.root),
        "sections": [{k: s[k] for k in ("name", "missing", "citations", "proofs", "unverified")} for s in checked],
        "summary": None if r["pass"] else f"{len(r['problems'])} problem(s) in docs/knowledge/"}, git.head(a.root) if git.is_repo(a.root) else None)
    if not r["pass"]:
        return ActionResult(False, f"The knowledge base has {len(r['problems'])} problem(s).", "\n".join(r["problems"]))
    cites = sum(s["citations"] for s in checked)
    return ActionResult(True, f"Knowledge base: {len(checked)} section(s), {cites} citation(s), no problems.")


def push_check(a: ActionInput) -> ActionResult:
    if not git.is_repo(a.root):
        return ActionResult(True, "Not a git repository.", update={"blockers": []})
    found = push_gates.push_blockers(a.root, a.base, project=a.key)
    warned = push_gates.push_warnings(a.root, a.base)
    notes = [f"{b['gate']}: {b['why']} (fix: {b['fix']})" for b in found] + [f"warning, {w['gate']}: {w['why']}" for w in warned]
    dirty = git.dirty(a.root)
    if dirty:
        return ActionResult(False, f"{len(dirty)} uncommitted file(s) before push.", "\n".join(sorted(dirty)), {"blockers": found})
    if found:
        # The board shows them; keel never pushes.
        return ActionResult(True, f"Working tree clean; {len(found)} push blocker(s): " + ", ".join(b["gate"] for b in found) + ".",
                            "\n".join(notes), {"blockers": found})
    return ActionResult(True, "Working tree clean; ready to push (keel never pushes for you)." +
                        (f" {len(warned)} warning(s)." if warned else ""), "\n".join(notes), {"blockers": []})


async def run_command(cmd: str, a: ActionInput) -> ActionResult:
    v = rules.check_bash(a.phase, cmd, rules.load_config(a.root), exists=lambda rel: (Path(a.root) / rel).exists())
    if not v.ok:
        return ActionResult(False, "Command refused.", v.reason)
    code, out = await asyncio.to_thread(testcmd.run, a.root, cmd, 900, command_env())
    return ActionResult(code == 0, f"`{cmd}` exited {code}.", tail(out, 3000))
