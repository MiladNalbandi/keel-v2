"""Code-step actions: verify_red, verify_green, verify_release, verify_coverage, commit, write_config, ladder,
knowledge_check (memory_check is its old name), push_check, run:<cmd>.

In fake mode the test runs are simulated with fixed outputs (the demo has no test toolchain in the
image). Commits are real whenever the root is a git repository, so COMMIT_RULES are exercised.
Checks that decide a push write a verdict to the engine DB (runtime/verdicts.py): knowledge_check -> memory,
verify_release and a whole-suite verify_green -> release, verify_coverage -> coverage. Simulated runs write none.
The other verdict actions (verify_fast, verify_module, verify_deps, audit, trace, arch) and the PR (pr, open_pr) live
in runtime/verdict_actions.py; review_lenses and coverage_report in runtime/ship.py; the review, diagnose, fix and change
helpers in runtime/flow_actions.py, the feature flow's in runtime/feature_actions.py; start_flow and escalate_model are
the compiler's (they start a thread, change a model).
The hunt's backlog actions (hunt_*) are in runtime/hunt_actions.py, init's architecture and ladder-repair steps
in runtime/init_actions.py, review-pr's (ticket_*) in runtime/ticket_actions.py.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from .. import extensions, layout, rules
from ..rules import checks
from ..tools import git, testcmd
from ..tools.agent_tools import command_env
from . import blockers as push_gates
from . import choices as choices_mod
from . import bug as bug_mod
from . import feature_actions, flow_actions, knowledge, lint_actions, ship, verdict_actions, verdicts
from . import test_scope
from . import commit_style
from . import tools as tool_runner
from . import ladder as run_ladder

COMMIT_EXCLUDES = [f":!{p.rstrip('/')}" for p in git.ENGINE_FILES]
VERDICT_ACTIONS = {
    "verify_fast": verdict_actions.verify_fast, "verify_module": verdict_actions.verify_module,
    "verify_deps": verdict_actions.verify_deps, "audit": verdict_actions.audit, "trace": verdict_actions.trace,
    "trace_strict": lambda a: verdict_actions.trace(a, strict=True), "arch": verdict_actions.arch,
    "pr": verdict_actions.pr, "open_pr": verdict_actions.open_pr,
    "review_lenses": ship.review_lenses, "coverage_report": ship.coverage_report, "memory_plan": ship.memory_plan,
    "memory_skip": ship.memory_skip, "knowledge_scan": ship.knowledge_scan, **lint_actions.ACTIONS,
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
    # A failure another round in the same place cannot change (a "trivial" commit that edits existing tests): the step
    # skips its rounds and goes on to after_rounds (else asks) at once.
    final: bool = False


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
    paths: list[str] = field(default_factory=list)     # commit: stage only these paths (feature: the spec alone)
    emit: object = None                                # tool runs report here (event tool.ran, full output)
    ident: str = ""                                    # commit: the scope in "fix(<ident>):" when no criterion names it
    params: dict = field(default_factory=dict)         # a part's step `with:` (plugins/db, plugins/git ...)
    event: object = None                               # an add-on action's own events: event(kind, data), kind "<addon>.*"
    progress: object = None                            # 0.18.15 a code step's live job: progress(text, **fields) adds a step
    marker: str = ""                                   # 0.18.57 commit: keel's own path that made it (already-met): a Keel-Path line

    @property
    def key(self) -> str:
        return self.project or self.root


async def run_action(action: str, a: ActionInput) -> ActionResult:
    if action == "verify_red":
        return await verify_red(a)
    if action == "verify_green":
        return await verify_green(a)
    if action == "verify_after_green":
        return await verify_after_green(a)
    if action == "verify_release":
        return await verify_release(a)
    if action == "verify_coverage":
        return await verify_coverage(a)
    if action in VERDICT_ACTIONS:
        return await VERDICT_ACTIONS[action](a)
    if action in flow_actions.ACTIONS:
        return await asyncio.to_thread(flow_actions.ACTIONS[action], a)
    if action in feature_actions.ACTIONS:
        return await asyncio.to_thread(feature_actions.ACTIONS[action], a)
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
    if action == "config_check":
        from . import config_check
        return await asyncio.to_thread(config_check.action, a)
    if action.startswith("run:"):
        return await run_command(action[4:].strip(), a)
    if extensions.owner(action):
        # a part's step ("<part>:<what>": db:query, product:save-doc ...); a per-project part must be on
        return await extensions.run_action(action, a)
    more = _flow_actions()
    if action in more:
        return await more[action](a)
    return ActionResult(False, f"Unknown action {action}.")


def _flow_actions() -> dict:
    """The actions of single flows (hunt, hunt-next, init's extra steps, review-pr, clarify); imported late, they import
    this module."""
    from . import clarify_flow, hunt_actions, init_actions, ticket_actions
    return {**hunt_actions.ACTIONS, **init_actions.ACTIONS, **ticket_actions.ACTIONS, **clarify_flow.ACTIONS}



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


def _module_layers(root: str) -> list[str]:
    """0.18.17 the layers of the whole suite: the api module suite, and the web one when commands.web_test_module is
    set and frontend.dir exists (as verify_module). Only the api suite ran: quick looked green with 2 failing web tests."""
    cfg = rules.load_config(root)
    web = str((cfg.get("commands") or {}).get("web_test_module") or "").strip()
    return ["API", "WEB"] if web and testcmd.part_dir(root, "frontend") else ["API"]


async def _tests(a: ActionInput, whole_suite: bool = False, layer: str | None = None) -> tuple[str | None, int, str]:
    ac_id = None if whole_suite else (a.ac or {}).get("id")
    layer = layer or (a.ac or {}).get("layer", "API")
    cmd, rel = testcmd.located(a.root, ac_id, layer)
    if not cmd:
        return None, 1, "No test command found. Add commands to .keel/config.yml."
    code, out = await asyncio.to_thread(testcmd.run, str(Path(a.root) / rel) if rel else a.root, cmd, 900, command_env())
    if testcmd.cannot_run(code, out):
        out += (f"\n\nkeel could not start this command (run from {rel + '/' if rel else 'the repo root'}). "
                "Retrying will not help: check the test commands in .keel/config.yml (commands.api_test_ac / "
                "api_test_module, or web_*) and the backend.dir / frontend.dir they run in.")
    return testcmd.shown(cmd, rel), code, out


def cannot_run(a: ActionInput, cmd: str, out: str, what: str) -> ActionResult:
    """v0.17.5 a test command that could not start (wrong folder, a missing ./mvnw) is not a test result: no "red
    confirmed", no retry of the agent (it cannot fix keel's command). The person chooses: recheck, skip, stop."""
    label = (a.ac or {}).get("id") or "the suite"
    detail = f"$ {cmd}\n{tail(out, 2000)}"
    choices = ["recheck", "skip", "stop"] if a.ac else ["recheck", "stop"]
    lines = [f"keel could not start the test command for {label}, so it cannot check {what}. Sending it back to the agent "
             "will not help: the command or the folder it runs in is wrong.", "", detail, "",
             choices_mod.how_to(),
             "- recheck: run the check again (after you fixed .keel/config.yml: the test commands, backend.dir / frontend.dir)"]
    if a.ac:
        lines.append(f"- skip: move {label} to the end of the list and go on with the next criterion")
    lines.append("- stop: stop the flow here (the commits so far stay)")
    return ActionResult(False, f"The test command for {label} cannot run.", detail, ask=choices_mod.for_question({
        "type": "cannot-run", "kind": "fix", "title": f"The test command for {label} cannot run", "detail": "\n".join(lines),
        "choices": choices}, context="cannot-run"))


async def verify_red(a: ActionInput) -> ActionResult:
    if not a.ac and bug_mod.of(a.acs):
        return await verify_bug_red(a, bug_mod.current(a))
    ac_id = (a.ac or {}).get("id") or "the bug"
    if a.fake and a.ac and test_scope.is_refactor(a.ac):
        return await _refactor_baseline(a)
    if a.fake:
        out = f"FAILED tests for {ac_id}\nAssertionError: expected '{ac_id} done' but was None"
        kind = rules.classify_failure(out)
        return ActionResult(True, f"{ac_id}: red confirmed ({kind['kind']} failure, simulated).", out,
                            {"acs": _with_ac_status(a.acs, (a.ac or {}).get("id"), "red")})
    if a.ac and test_scope.is_refactor(a.ac):
        return await _refactor_baseline(a)
    reads = _source_reads(a)
    if reads:
        return reads
    cmd, code, out = await _tests(a)
    if cmd is None:
        return ActionResult(False, out, out)
    if testcmd.cannot_run(code, out):
        return cannot_run(a, cmd, out, "red")
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


def _source_reads(a: ActionInput) -> ActionResult | None:
    """A RED test that reads a source file as text (a regex over the code under test) proves its shape, not its
    behaviour: the red check sends it back to the test-author, unless the request asks for a lint-like check."""
    if not git.is_repo(a.root) or test_scope.asks_for_source_check((a.ac or {}).get("title"), a.request):
        return None
    cfg = rules.load_config(a.root)
    mine = {f for f, fp in a.preexisting.items() if git.fingerprint(a.root, f) == fp}
    changed = [f for f in git.dirty(a.root) if f not in mine and test_scope.is_test_file(cfg, f)]
    hits = test_scope.source_reads(a.root, changed)
    if not hits:
        return None
    ac_id = (a.ac or {}).get("id") or "the criterion"
    where = ", ".join(f"{h['file']}:{h['line']}" for h in hits[:3])
    detail = (test_scope.SOURCE_READ_NOTE.format(where=where) + "\n\n"
              + "\n".join(f"  {h['file']}:{h['line']}: {h['text']}" for h in hits[:10]))
    return ActionResult(False, f"{ac_id}'s test reads source code as text ({where}); write a behaviour test instead.", detail)


async def _refactor_baseline(a: ActionInput) -> ActionResult:
    """A refactor criterion has no red test: its proof is that the existing tests pass before the change (this run) and
    after it (verify_green). A suite that fails before the change cannot prove "no behaviour change": the person
    chooses (recheck after a fix, skip the criterion, stop)."""
    ac_id = a.ac["id"]
    data = dict(a.data or {})
    seen = dict(data.get("refactor_baseline") or {})
    if a.fake:
        seen[ac_id] = {"ok": True, "simulated": True}
        return ActionResult(True, f"{ac_id} is a refactor: no red test; the existing tests pass before the change (simulated).",
                            "", {"acs": _with_ac_status(a.acs, ac_id, "red"), "data": {**data, "refactor_baseline": seen}})
    ran, shown = [], []
    for layer in _module_layers(a.root):
        cmd, code, out = await _tests(a, whole_suite=True, layer=layer)
        if cmd is None:
            return ActionResult(False, out, out)
        if testcmd.cannot_run(code, out):
            return cannot_run(a, cmd, out, "the tests before the refactor")
        if code != 0:
            detail = await failure_detail(a, cmd, code, out, layer)
            lines = [f"{ac_id} is a refactor: its proof is that the existing tests pass before and after the change. They "
                     "fail before it, so keel cannot prove that nothing changed.", "", detail, "", choices_mod.how_to(),
                     "- recheck: run the tests again (after you fixed them)",
                     f"- skip: move {ac_id} to the end of the list and go on with the next criterion",
                     "- stop: stop the flow here (the commits so far stay)"]
            return ActionResult(False, f"{ac_id}: the existing tests fail before the refactor.", detail,
                                ask=choices_mod.for_question({"type": "refactor-baseline", "kind": "fix",
                                                              "title": f"The tests fail before {ac_id}'s refactor",
                                                              "detail": "\n".join(lines),
                                                              "choices": ["recheck", "skip", "stop"]}, context="refactor-baseline"))
        ran.append(cmd)
        shown.append(f"$ {cmd}\n{tail(out, 1000)}")
    seen[ac_id] = {"ok": True, "command": " && ".join(ran)}
    return ActionResult(True, f"{ac_id} is a refactor: no red test; the existing tests pass before the change.",
                        "\n".join(shown), {"acs": _with_ac_status(a.acs, ac_id, "red"), "data": {**data, "refactor_baseline": seen}})


async def verify_bug_red(a: ActionInput, bug: dict) -> ActionResult:
    """The bug flow's red check (keel v1 `keel state repro-done`): only the bug's own test runs (BUG-<n>, in its layer:
    a WEB reproducer runs the web test command), it must fail on an assertion, and the failure must be that test's: a
    test that was failing before the flow started is not a reproduction. Gate R shows the test, the command and the
    failure (`show`), and data.bug.test keeps file::name for the PR body."""
    bid = bug["id"]
    files = await asyncio.to_thread(bug_mod.changed_tests, a.root)
    if a.fake:
        out = f"AssertionError: {bid} still shows the symptom (simulated)"
        cmd = f"(simulated) the {bug.get('layer', 'API')} test command for {bid}"
        code = 1
    else:
        cmd, code, out = await _tests(replace(a, ac=bug))
        if cmd is None:
            return ActionResult(False, out, out)
        if testcmd.cannot_run(code, out):
            return cannot_run(a, cmd, out, "red")
    hint = testcmd.name_hint(a.root, bid)
    if not a.fake and (testcmd.ran_no_tests(out) or (code == 5 and "deselected" in out)):
        return ActionResult(False, f"No test for {bid} ran, so the bug is not reproduced.",
                            f"$ {cmd}\n{tail(out, 3000)}\nWrite a failing test whose name carries {bid}. {hint}")
    if code == 0:
        return ActionResult(False, f"The test for {bid} passes, so it does not reproduce the bug.",
                            f"$ {cmd}\n{tail(out, 3000)}\nThe test must fail today with the symptom the report describes.")
    failing = verdict_actions.failed_tests(out)
    if failing and not any(bug_mod.named(n, bid) for n in failing):
        return ActionResult(False, f"The failing test(s) are not {bid}'s: {', '.join(failing[:5])}. A test that was failing "
                            "before is not a reproduction.",
                            f"$ {cmd}\n{tail(out, 3000)}\nWrite a test named after {bid} that fails with the symptom. {hint}")
    kind = rules.classify_failure(out)
    if kind["kind"] == "setup":
        return ActionResult(False, f"The test for {bid} fails from a setup problem, not an assertion (matched \"{kind['matched']}\").",
                            f"$ {cmd}\n{tail(out, 3000)}")
    test = bug_mod.find_test(a.root, bid, out, files)
    sim = " (simulated)" if a.fake else ""
    show = (f"{bid} [{bug.get('layer', 'API')}] is reproduced{sim}.\n\nReproducing test: {bug_mod.shown_test(test)}\n"
            f"Command: {cmd}\n\nThe failure:\n{tail(out, 2500)}\n\n"
            "investigate: this is the bug, find its cause. reject: not this bug; the test and its commit are dropped and "
            "the reproducer writes a new one with your note. stop: stop here; the reproducing test stays on the branch.")
    info = {**(a.data.get("bug") or {}), "id": bid, "layer": bug.get("layer", "API"),
            "test": {**test, "command": cmd, "output": tail(out, 1500)}}
    acs = [dict(x, status="red", layer=bug.get("layer", x.get("layer", "API"))) if x["id"] == bid else dict(x) for x in a.acs]
    return ActionResult(True, f"{bid}: red confirmed ({bug_mod.shown_test(test)}){sim}.", f"$ {cmd}\n{tail(out, 3000)}",
                        {"acs": acs, "show": show, "data": {**a.data, "bug": info}})


async def _bug_green(a: ActionInput, bug: dict) -> ActionResult:
    """The bug flow's fix check: the bug's own test passes, then the whole suite (keel v1 green-done + the module suite:
    a fix that breaks other behaviour is a second bug)."""
    bid = bug["id"]
    cmd, code, out = await _tests(replace(a, ac=bug))
    if cmd is None:
        return ActionResult(False, out, out)
    if testcmd.cannot_run(code, out):
        return cannot_run(a, cmd, out, "green")
    if testcmd.ran_no_tests(out) or (code == 5 and "deselected" in out):
        return ActionResult(False, f"No test for {bid} ran, so the fix cannot be checked.", f"$ {cmd}\n{tail(out, 3000)}")
    if code != 0:
        return ActionResult(False, f"The test for {bid} still fails.", f"$ {cmd}\n{tail(out, 3000)}")
    whole = await _whole_suite_green(a)
    if not whole.ok:
        return whole
    acs = [dict(x, status="done") if x["id"] == bid else dict(x) for x in a.acs]
    return ActionResult(True, f"{bid}: green; {whole.note}", f"$ {cmd}\n{tail(out, 1000)}\n{whole.detail}", {**whole.update, "acs": acs})


async def verify_green(a: ActionInput) -> ActionResult:
    r = await _verify_green(a)
    if (a.ac or {}).get("id"):
        return _at_its_path(a, r)
    # 0.18.57 a run outside the criteria loop (quick, address, a trivial change, a bug's fix) is kept in data.test_run:
    # the look gate shows what it said (feature_actions.test_run_part)
    cmds = [x[2:].strip() for x in (r.detail or "").splitlines() if x.startswith("$ ")]
    run = {"ok": bool(r.ok), "note": r.note, "commands": cmds[:4], "tail": tail(r.detail or "", 1500)}
    return replace(r, update={**r.update, "data": {**(r.update.get("data") or a.data or {}), "test_run": run}})


def _changed(a: ActionInput) -> list[str]:
    """The files changed in the working tree since the last commit, without keel's own files and without the person's
    own work from before the flow (a.preexisting, still unchanged)."""
    out = git.git(a.root, "status", "--porcelain", "--untracked-files=all").stdout
    files = [line[3:].split(" -> ")[-1].strip('"') for line in out.splitlines() if len(line) > 3]
    return [f for f in files if f != ".keel" and not f.startswith(".keel/")
            and not (f in a.preexisting and git.fingerprint(a.root, f) == a.preexisting[f])]


def _named_files(a: ActionInput) -> list[str]:
    """The project files a criterion's title names by path (a folder and an extension)."""
    title = str((a.ac or {}).get("title") or "")
    return [f for f in dict.fromkeys(re.findall(r"(?:[\w.-]+/)+[\w.-]+\.\w+", title)) if (Path(a.root) / f).exists()]


def _at_its_path(a: ActionInput, r: ActionResult) -> ActionResult:
    """keel 0.18.58 a criterion that names a file at a path is green only with that file there: an agent that keel's
    rule blocked put the factory in another folder, and the criterion "tests/Factory/ProductFactory.php exists" was
    marked done."""
    if not r.ok or a.fake:
        return r
    missing = [f for f in test_scope.must_exist((a.ac or {}).get("title")) if not (Path(a.root) / f).exists()]
    if not missing:
        return r
    ac_id = a.ac["id"]
    shown = ", ".join(f"`{f}`" for f in missing)
    return ActionResult(False, f"{ac_id} asks for {shown}, and it is not there.",
                        f"{ac_id} asks for {shown} at that exact path. A file in another place does not meet the "
                        f"criterion. If keel's rule blocks the path, do not put the file somewhere else: answer "
                        f"FROZEN: {missing[0]} and the person decides.\n\n{r.detail}")


async def _verify_green(a: ActionInput) -> ActionResult:
    ac_id = (a.ac or {}).get("id")
    label = ac_id or "the suite"
    bug = bug_mod.current(a) if not ac_id else None
    if bug and a.fake:
        return ActionResult(True, f"{bug['id']}: green (simulated).", "1 passed",
                            {"acs": [dict(x, status="done") if x["id"] == bug["id"] else dict(x) for x in a.acs]})
    if bug:
        return await _bug_green(a, bug)
    if a.fake:
        return ActionResult(True, f"{label}: green (simulated).", "1 passed",
                            {"acs": _with_ac_status(a.acs, ac_id, "green")} if ac_id else {})
    if not ac_id:
        return await _whole_suite_green(a)
    if test_scope.is_refactor(a.ac):
        # a refactor's proof: the existing tests pass after the change too (they passed before it, verify_red)
        whole = await _whole_suite_green(a)
        if not whole.ok:
            return replace(whole, note=f"{ac_id} is a refactor, and the existing tests fail after it: {whole.note}")
        if git.is_repo(a.root) and not _changed(a):
            # 0.18.58 e2e: GREEN left the frozen test file alone and changed nothing; "the tests still pass" was taken
            # as done. A refactor that asks for a change is not done until something changed.
            named = test_scope.must_exist(a.ac.get("title")) or [f for f in _named_files(a)]
            how = (f" If keel's rule blocks the file it needs, answer FROZEN: {named[0]} and the person decides."
                   if named else " If keel's rule blocks the file it needs, answer FROZEN: <file> and the person decides.")
            return ActionResult(False, f"{ac_id} is a refactor, and nothing changed.",
                                f"{ac_id} asks for a change, and the working tree has none: the existing tests passing "
                                f"proves nothing yet.{how}\n\n{whole.detail}")
        return ActionResult(True, f"{ac_id} (refactor): the existing tests pass after the change too.", whole.detail,
                            {**whole.update, "acs": _with_ac_status(a.acs, ac_id, "green")})
    cmd, code, out = await _tests(a)
    if cmd is None:
        return ActionResult(False, out, out)
    if testcmd.cannot_run(code, out):
        return cannot_run(a, cmd, out, "green")
    if testcmd.ran_no_tests(out):
        return ActionResult(False, f"No test for {ac_id} ran, so it cannot be green.",
                            f"$ {cmd}\n{tail(out, 3000)}\nThe RED test for {ac_id} is missing. Rewind to its red step.")
    if code != 0:
        return ActionResult(False, f"{label} does not pass yet.",
                            await failure_detail(a, cmd, code, out, (a.ac or {}).get("layer", "API"), ac_id))
    return ActionResult(True, f"{label}: green.", f"$ {cmd}\n{tail(out, 1500)}",
                        {"acs": _with_ac_status(a.acs, ac_id, "green")})


async def failure_detail(a: ActionInput, shown_cmd: str, code: int, out: str, layer: str = "API", ac_id: str | None = None) -> str:
    """0.18.28 what a failing test run shows: the failing tests with their messages first (the runner's own report),
    then the end of the output. A run that printed nothing (phpunit exit 2, "Exit code 2 with no output") runs once more
    with a JUnit report, so the agent and the gate name the failing tests."""
    from . import suite_failure

    report = suite_failure.excerpt(out)
    extra = ""
    if not (out or "").strip() or not (report or suite_failure.failing(out)):
        cmd, rel = testcmd.located(a.root, ac_id, layer)
        cases, again = (await asyncio.to_thread(suite_failure.rerun_for_output, str(Path(a.root) / rel) if rel else a.root,
                                                cmd, command_env())) if cmd else ([], "")
        lines = [suite_failure.empty_note(shown_cmd, code)] if not (out or "").strip() else []
        if cases:
            lines += ["The JUnit report names these failures:", *[f"- {c['name']}: {c['message'][:600]}" for c in cases]]
        elif again.strip() and not (out or "").strip():
            lines += ["The second run printed:", tail(again, 2500)]
        extra = "\n".join(lines)
    parts = [f"$ {shown_cmd}", f"exit {code}"]
    if report:
        parts += ["The failures:", report]
    if extra:
        parts.append(extra)
    parts.append("The end of the output:\n" + (tail(out, 2500) if (out or "").strip() else "(nothing)"))
    return "\n".join(parts)


async def _whole_suite_green(a: ActionInput) -> ActionResult:
    """verify_green without a criterion: every module suite of the project (api, then web), each in its own folder."""
    ran, shown, layers = [], [], _module_layers(a.root)
    for layer in layers:
        cmd, code, out = await _tests(a, whole_suite=True, layer=layer)
        if cmd is None:
            return ActionResult(False, out, out)
        if testcmd.cannot_run(code, out):
            return cannot_run(a, cmd, out, "green")
        what = "web" if layer == "WEB" else "api"
        if verdict_actions.ran_no_tests(cmd, code, out):
            # 0.18.17 the whole suite with no test is not green (vitest exits 0 with "No test files found")
            await asyncio.to_thread(_release_verdict, a, False, cmd, out)
            return ActionResult(False, f"The {what} suite ran no test, so it is not green.",
                                f"$ {cmd}\n{tail(out, 3000)}\n{verdict_actions.NO_TESTS_HINT}")
        if code != 0:
            detail = await failure_detail(a, cmd, code, out, layer)
            upd = {}
            if a.acs and not a.fake and not bug_mod.of(a.acs):
                # 0.18.28 who is the likely cause: failing tests that pass alone, the branch's tests that write to the DB
                from . import suite_failure

                found = await asyncio.to_thread(suite_failure.analyse, a.root, a.base, out, command_env(), layer, a.acs)
                upd = {"suite_failure": {**found, "layer": layer, "command": cmd}}
                said = suite_failure.describe(found)
                if said:
                    detail = f"{detail}\n\n{said}"
            return ActionResult(False, f"The {what} suite does not pass yet.", detail, upd)
        ran.append(cmd)
        shown.append(f"$ {cmd}\n{tail(out, 1500 // len(layers))}")
    # The whole suite green on these files is the release verdict for the commit made from them.
    await asyncio.to_thread(_release_verdict, a, True, " && ".join(ran), "\n".join(shown))
    return ActionResult(True, "the suite: green." if len(ran) == 1 else f"the suite: green ({len(ran)} module suites).",
                        "\n".join(shown))


AFTER_GREEN = ("none", "changed", "suite")
SUITE_EVERY = 3


def after_green_plan(a: ActionInput) -> tuple[str, str]:
    """(what to run, why) after a criterion's green: "none", "changed" (the branch's test files) or "suite" (every module
    suite). checks.after_green in .keel/config.yml: none | changed (default) | suite; with changed the whole suite still
    runs every checks.suite_every criteria (default 3) and at the end of each lane."""
    checks_cfg = rules.load_config(a.root).get("checks") or {}
    mode = str(checks_cfg.get("after_green") or "changed").strip().lower()
    mode = mode if mode in AFTER_GREEN else "changed"
    if mode != "changed":
        return mode, f"checks.after_green: {mode}"
    try:
        every = max(1, int(checks_cfg.get("suite_every") or SUITE_EVERY))
    except (TypeError, ValueError):
        every = SUITE_EVERY
    ac_id = (a.ac or {}).get("id")
    loop = [x for x in a.acs if str(x.get("layer") or "API").upper() in ("API", "WEB")]
    done = sum(1 for x in loop if x.get("status") in ("done", "already-met") or x.get("id") == ac_id)
    lane = rules.ac_lane(a.ac) if a.ac else None
    open_in_lane = [x for x in loop if x.get("id") != ac_id and x.get("status") not in ("done", "already-met")
                    and rules.ac_lane(x) == lane]
    if not open_in_lane:
        return "suite", f"the last criterion of the {lane or 'api'} lane"
    if done % every == 0:
        return "suite", f"every {every} criteria (checks.suite_every)"
    return "changed", "the test files this flow touched"


def _paths_command(root: str, layer: str, files: list[str]) -> tuple[str | None, str]:
    """(a command that runs these test files, its folder): commands.<api|web>_test_paths with {PATHS}, else the module
    command for a runner that takes paths (pytest, vitest, jest, mocha), else (None, "")."""
    import shlex

    cmds = rules.load_config(root).get("commands") or {}
    rel = testcmd.part_dir(root, "frontend" if layer == "WEB" else "backend")
    mine = [f[len(rel) + 1:] if rel and f.startswith(rel + "/") else f for f in files]
    paths = " ".join(shlex.quote(f) for f in mine)
    key = "web_test_paths" if layer == "WEB" else "api_test_paths"
    if cmds.get(key):
        return str(cmds[key]).replace("{PATHS}", paths), rel
    module, mrel = testcmd.located(root, None, layer)
    if module and re.search(r"\b(pytest|vitest|jest|mocha)\b", module):
        return f"{module} {paths}", mrel
    return None, ""


async def verify_after_green(a: ActionInput) -> ActionResult:
    """0.18.28 after a criterion's green (or "already passes"): the tests outside its own filter. Each AC's check ran
    only --filter 'AC-00x', so a test that broke other tests (AC-008's committed row) stayed hidden until integration ran
    the whole suite. A failure goes back to the criterion's green with the failing tests and output; its own test files
    are unlocked there, since the fix may be in the test (a missing cleanup)."""
    ac_id = (a.ac or {}).get("id") or "the criterion"
    plan, why = after_green_plan(a)
    data = dict(a.data or {})
    seen = dict(data.get("after_green") or {})
    if plan == "none" or a.fake:
        note = f"{ac_id}: tests outside it not run ({why})" if plan == "none" else f"{ac_id}: the tests outside it pass (simulated, {why})."
        seen[ac_id] = {"ok": True, "ran": "none" if plan == "none" else plan, "why": why}
        return ActionResult(True, note, "", {"data": {**data, "after_green": seen}})
    layers = _module_layers(a.root)
    shown, failed = [], None
    for layer in layers:
        if plan == "changed":
            files = [f for f in verdict_actions.branch_files(a.root, a.base)
                     if _is_test_file(f, layer, len(layers) == 1) and (Path(a.root) / f).is_file()]
            if not files:
                continue
            cmd, rel = _paths_command(a.root, layer, files)
            if not cmd:
                cmd, rel = testcmd.located(a.root, None, layer)       # no paths command: the module suite
        else:
            cmd, rel = testcmd.located(a.root, None, layer)
        if not cmd:
            continue
        code, out = await asyncio.to_thread(testcmd.run, str(Path(a.root) / rel) if rel else a.root, cmd, 1800, command_env())
        show = testcmd.shown(cmd, rel)
        if testcmd.cannot_run(code, out):
            return cannot_run(a, show, out, "the tests outside it")
        if code != 0:
            failed = (layer, show, code, out)
            break
        shown.append(f"$ {show}\n{tail(out, 600)}")
    if failed:
        from . import suite_failure

        layer, show, code, out = failed
        names = suite_failure.failing(out)
        mine = {ac_id.lower(), testcmd.ac_id(ac_id).lower()}
        outside = [n for n in names if not any(m in n.lower() for m in mine)] or names
        detail = await failure_detail(a, show, code, out, layer)
        sus = [s for s in await asyncio.to_thread(suite_failure.suspects, a.root, a.base) if ac_id in s["acs"]]
        unlocks = [{"path": s["file"], "phase": "green", "by": "keel", "reason": f"{ac_id} broke tests outside it"} for s in sus]
        if sus:
            detail += "\n\n" + suite_failure.describe({"suspects": sus}) + ("\nIts test files are open to change in green: the "
                                                                            "fix may be the test's cleanup.")
        seen[ac_id] = {"ok": False, "ran": plan, "why": why, "failing": outside[:10]}
        note = f"{ac_id}'s change broke tests outside it: " + (", ".join(outside[:5]) or f"the {layer.lower()} suite fails")
        return ActionResult(False, note[:600], detail, {"data": {**data, "after_green": seen}, "unlocks": unlocks})
    seen[ac_id] = {"ok": True, "ran": plan, "why": why}
    ran = "the whole suite" if plan == "suite" else "the branch's test files"
    return ActionResult(True, f"{ac_id}: {ran} pass{'es' if plan == 'suite' else ''} too ({why}).", "\n".join(shown),
                        {"data": {**data, "after_green": seen}})


def _is_test_file(path: str, layer: str, any_layer: bool = False) -> bool:
    from .suite_failure import TEST_FILE

    if not TEST_FILE.search(path):
        return False
    if any_layer:
        return True
    web = re.search(r"\.(tsx?|jsx?|vue|svelte)$", path) is not None
    return web if layer == "WEB" else not web


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
    # 0.18.17 a configured command runs in the folder of its key (api_* in backend.dir, docs/CONTRACT.md), like
    # verify_module's suites: run at the repo root a PHP backend in backend/ failed with "Could not open input file:
    # vendor/bin/phpunit"
    cmd, rel = testcmd.located(a.root, None, "API")
    if cmd is None:
        out = "No test command found. Add commands to .keel/config.yml."
        return ActionResult(False, out, out)
    reruns = int((rules.load_config(a.root).get("loops") or {}).get("flaky_reruns", 1) or 0)
    show = testcmd.shown(cmd, rel)
    if callable(a.progress):
        a.progress(f"release suite: $ {show}", tool="verify_release", command=cmd)
    cwd = str(Path(a.root) / rel) if rel else None
    r = await asyncio.to_thread(verdict_actions.run_suite, a.root, cmd, "release suite", reruns, cwd)
    await asyncio.to_thread(_release_verdict, a, r["ok"], cmd, r["out"], r["flaky"])
    upd = verdict_actions.flaky_update(a, r["flaky"])
    if not r["ok"]:
        what = "ran no test" if r.get("no_tests") else "fails"
        return ActionResult(False, f"The release suite {what}.", f"$ {show}\n{tail(r['out'], 3000)}")
    return ActionResult(True, "Release suite: green; verdict recorded for this commit." + verdict_actions._flaky_note(r["flaky"]),
                        f"$ {show}\n{tail(r['out'], 1500)}", upd)


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
        if not a.fake:
            # 0.18.57 the step ran and could measure nothing: a "not available" verdict, so the final review and the PR
            # body say why instead of "not run" / "No coverage verdict" (they read like a skipped cover)
            why = "no coverage command in .keel/config.yml (commands.coverage)"
            await asyncio.to_thread(verdict_actions.record_verdict, a.key, "coverage", None, {"reason": why, "summary": why},
                                    None, a.root)
        return ActionResult(True, "No coverage command in .keel/config.yml (commands.coverage); coverage is not checked.")
    if a.fake:
        return ActionResult(True, "Coverage: passed (simulated, no verdict written).")
    code, out = await asyncio.to_thread(testcmd.run, a.root, cmd, 1800, command_env())
    head, tree = await asyncio.to_thread(verdicts.stamp, a.root)
    await asyncio.to_thread(verdicts.write, a.key, "coverage", code == 0,
                            {"command": cmd, "tree": tree, "summary": None if code == 0 else f"`{cmd}` exited {code}",
                             "output": tail(out, 1500)}, head)
    if code != 0:
        # 0.18.22 without coverage.reports there are no uncovered groups to work on: the cover loop asks at once
        # (data.coverage_no_reports) instead of measuring twice with nothing to decide in between
        why = (f"`{cmd}` failed (exit {code}), and without coverage.reports keel cannot see which lines are uncovered, "
               "so there is nothing to decide per group. To let cover write the missing tests, name the report files in "
               ".keel/config.yml, for example:\n\ncoverage:\n  reports:\n    api: build/reports/jacoco/test/jacocoTestReport.xml\n"
               "    web: coverage/lcov.info\n\ngo_on: go on to the coverage verdict (it stays failing).\n"
               "stop: end the flow here and configure coverage.reports first.")
        return ActionResult(False, f"Coverage is below what `{cmd}` accepts.", f"$ {cmd}\n{tail(out, 3000)}",
                            {"data": {**a.data, "coverage_groups": [], "coverage_no_reports": f"{why}\n\n$ {cmd}\n{tail(out, 1500)}"}})
    return ActionResult(True, "Coverage: passed; verdict recorded for this commit.", f"$ {cmd}\n{tail(out, 1500)}",
                        {"data": {**a.data, "coverage_no_reports": None}} if a.data.get("coverage_no_reports") else {})


def _refuse(a: ActionInput, note: str, detail: str, **extra) -> ActionResult:
    git.git(a.root, "reset", "-q")
    return ActionResult(False, note, detail, **extra)


def commit(a: ActionInput) -> ActionResult:
    """keel v1 `keel commit`: bucket rules plus the diff-level checks, then a real commit."""
    if not git.is_repo(a.root):
        return ActionResult(True, "Not a git repository; nothing committed.")
    if (a.settings or {}).get("run_mode") == "readonly":
        # Run mode readonly: keel commits nothing. The user switches the mode and tries again, or stops the flow.
        detail = ("This flow runs read-only (run mode readonly): agents may not edit, and keel makes no commit.\n\n"
                  "Change the run mode (manual, important or auto) and approve to try the commit again, or reject to stop the flow.")
        return ActionResult(False, "Read-only run: keel commits nothing.", detail, ask={
            "type": "readonly", "kind": "fix", "title": "Read-only run: the commit is refused", "detail": detail,
            "labels": {"approve": "Try the commit again", "reject": "Stop the flow"}})
    ctype = rules.commit_type_for(a.phase)
    if a.marker == "already-met":
        ctype = "red"               # 0.18.57 the criterion's test, committed as test(<AC>) whatever the phase says
    paths = a.paths
    lay = layout.resolve(a.root)
    if lay.hidden and paths:
        # layout keel / external: .keel/ is outside git (keel v1 records these steps instead of committing them)
        kept = [p for p in paths if p != ".keel" and not p.startswith(".keel/")]
        if not kept:
            _applied(a, "recorded", paths[0], f"{a.phase}: {a.title or a.flow}", paths)
            return ActionResult(True, f"Recorded, not committed: layout {lay.name} keeps keel's files out of git.",
                                "\n".join(paths))
        paths = kept
    git.git(a.root, "add", "-A", "--", *(paths or ["."]), *COMMIT_EXCLUDES)
    # Never sweep the user's own uncommitted work into a keel commit: a file that was already changed when the
    # flow started, and that no agent has touched since, is unstaged again.
    theirs = [f for f, fp in a.preexisting.items() if git.fingerprint(a.root, f) == fp]
    if theirs:
        git.git(a.root, "reset", "-q", "--", *theirs)
    staged = [f for f in git.git(a.root, "diff", "--cached", "--name-only").stdout.splitlines() if f.strip()]
    if not staged:
        return ActionResult(True, "Nothing to commit.")
    tool_notes, staged = _pre_commit_tools(a, staged)
    if isinstance(tool_notes, ActionResult):
        return tool_notes
    cfg = rules.load_config(a.root)
    diff = git.git(a.root, "diff", "--cached", "-U0").stdout

    # The staged diff is the last place a secret can be stopped before it is in history.
    found = checks.secrets_in_diff(diff)
    if found:
        kinds = sorted({f["why"] for f in found})
        note = f"The {ctype} commit stages what looks like a secret: {', '.join(kinds)}."
        detail = ("\n".join(f"  {f['file']}: {f['why']}" for f in found) +
                  "\nRemove it and read it from an environment variable. A fixture line may carry the marker keel:allow-secret.")
        # Every run mode stops here (runtime/run_mode.py): approve sends it back to the agent to remove it, reject stops.
        return _refuse(a, note, detail, update={"blockers": push_gates.push_blockers(a.root, a.base, found, project=a.key)},
                       ask={"type": "secrets", "kind": "fix", "title": "A secret is staged for commit",
                            "detail": f"{note}\n{detail}\n\nApprove to send it back to the agent to remove it. Reject to stop the flow.",
                            "labels": {"approve": "Send back to remove it", "reject": "Stop the flow"}})

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
                                     "deps": names, "files": files, "labels": {"approve": "Allow", "reject": "Refuse"}})

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
                           "\n".join("  " + f for f in edited) + "\nRun it as a small change flow through the AC loop instead.",
                           final=True)

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

    must, suggest = checks.escalation(cfg, staged, a.gates_log) if a.flow in checks.ESCALATING_FLOWS else ([], [])
    if must:
        why = "; ".join(t["why"] for t in must)
        detail = (f"Escalation trigger: {why}.\n\nApprove to stop this {a.flow} flow and start a feature flow with a spec. "
                  "Reject (with a reason) to stay a small change and commit; the reason covers only this trigger.")
        git.git(a.root, "reset", "-q")
        return ActionResult(False, f"Escalation trigger: {why}.", detail,
                            ask={"type": "escalate", "kind": "gate", "title": "Escalate to a feature flow?", "detail": detail,
                                 "why": why, "ids": [t["id"] for t in must]})

    rule = rules.COMMIT_RULES[ctype]
    bug = ctype in ("fix", "red") or (ctype == "e2e" and a.flow == "fix")
    named = "ci" if a.flow == "ci-fix" else {"review-fix": "review", "integration": "integration"}.get(a.phase)
    found = bug_mod.of(a.acs) if bug else None
    ident = "" if rule.get("noId") else ((a.ac or {}).get("id") or a.ident or named or
                                          (((found or {}).get("id") or "BUG") if bug else ""))
    subject = (a.ac or {}).get("title") or a.title or a.flow
    if ctype == "setup":
        subject = "keel init"
    # 0.18.55 a flow started from a ticket (a task with a Jira key): every commit names the key, so the ticket scan of
    # keel Product links the per-criterion commits too ("feat(AC-2): ... [SHOPS-4]")
    ticket = ticket_key(a) if ctype != "setup" else None
    tag = f" [{ticket}]" if ticket else ""
    # 0.18.56 an ac commit (quick, address) that holds test files only is a test change: "test(...)", not "feat(...)"
    only_tests = ctype == "ac" and bool(buckets) and all(b in test_scope.TEST_BUCKETS for _f, b in buckets)
    prefix = rules.commit_prefix("red" if only_tests else ctype, ident)
    keel_lines: list[str] = []
    # 0.18.57 the project's commit style (git.commit_template, else inferred from the branch's recent commits); keel's
    # own form then goes to a Keel-Ref trailer, which the trace and the audit read
    tpl, _src = commit_style.template(a.root, cfg)
    if tpl:
        kind = commit_style.conventional_type("red" if only_tests else ctype, [b for _f, b in buckets], staged, a.flow,
                                              refactor=bool(a.ac and test_scope.is_refactor(a.ac)))
        line = commit_style.render(tpl, kind, ident or prefix.partition("(")[2].rstrip(")"), " ".join(str(subject).split()),
                                   ticket)
        message, body = commit_style.fit(line, 72)
        if not message.startswith(prefix + ":"):
            keel_lines.append(f"{commit_style.REF}: {prefix}")
    else:
        message, body = commit_message(prefix, subject, 72 - len(tag))
        if ticket and ticket not in message:
            message += tag
    if a.marker:
        keel_lines.append(f"{commit_style.PATH}: {a.marker}")
    # test files an unlock opened for this phase (keel's own after a criterion broke tests outside it, or a person's):
    # the ship audit reads this line instead of calling the commit an implementation commit with tests in it
    opened = [f for f, b in buckets if b in test_scope.TEST_BUCKETS and rules.unlocked(a.unlocks, f, a.phase)]
    if opened and ctype not in ("red", "coverage", "smoke", "e2e") and not only_tests:
        keel_lines.append(f"{commit_style.UNLOCKED}: {', '.join(opened)}")
    if found and ctype == "fix" and a.phase == "bug-fix":
        # keel v1 fix.md: the root cause goes in the body, not just the subject
        body = "\n\n".join(x for x in (body, bug_mod.commit_body(a)) if x)
    extra = ["-m", body] if body else []
    scope = a.data.get(a.phase) if a.phase in ("e2e", "smoke") else None
    if isinstance(scope, dict) and scope.get("covers"):
        # the [E2E] / [SMOKE] criteria these tests cover: the trace finds them here (verdict_actions.trace_rows)
        extra += ["-m", "Covers: " + ", ".join(scope["covers"])]
    ident, trailer = commit_args(a.root, a.settings, cfg)
    if keel_lines:
        # one last paragraph: keel's lines with the co-author line (git reads them as trailers together)
        trailer = ["-m", "\n".join(keel_lines + trailer[1:])]
    r = git.git(a.root, *ident, "commit", "-q", "-m", message, *extra, *trailer)
    if r.returncode != 0:
        git.git(a.root, "reset", "-q")
        from . import v1_state

        # 0.18.64 keel v1's hooks refuse commits while its state file names a flow: say which one, and how to archive it
        left = v1_state.leftover(a.root)
        return ActionResult(False, "git commit failed" + (f": {left}." if left else "."), (r.stderr or r.stdout)[-2000:])
    sha = git.head(a.root)
    if sha:
        _applied(a, "commit", sha, message, staged)
    extensions.on_commit(a.root)            # the parts follow every keel commit (the code graph syncs in the background)
    tail_note = f" · tools: {'; '.join(tool_notes)}" if tool_notes else ""
    tail_note += f" · suggest: {'; '.join(t['why'] for t in suggest)} (not blocking)" if suggest else ""
    gates = a.state.get("gates") or {"mode": "every-ac", "log": list(a.gates_log), "skipped": {}}
    noted = {"gates": {**gates, "log": [*(gates.get("log") or []), *checks.suggest_lines(suggest)]}} if suggest else {}
    return ActionResult(True, f"{message} {sha[:7] if sha else ''}".strip() + tail_note, "\n".join(staged),
                        {"git_head": sha, "blockers": push_gates.push_blockers(a.root, a.base, project=a.key), **noted})


def _applied(a: ActionInput, kind: str, ref: str, subject: str, files: list[str]) -> None:
    """v0.15.9 what a flow applied on top of the repo (api DocHistoryService.applied): a commit, or files recorded."""
    if not a.thread_id:
        return
    from ..events import bus
    from . import doc_history

    # the committed text first (spec_freeze rewrites the spec and commits it in one step): the version the api stamps
    # with this commit's sha must be the one that went into it, not the text from before the step
    doc_history.after_step(lambda t, step=None, data=None: bus.emit(t, a.thread_id, a.project or "", step=step, data=data),
                           a.root, doc_history._LAST.get(a.root, {}), "keel", kind, a.phase)
    bus.emit("keel.applied", a.thread_id, a.project or "", data={"kind": kind, "sha": ref if kind == "commit" else None,
                                                                 "ref": ref, "subject": subject, "files": list(files)})


def _pre_commit_tools(a: ActionInput, staged: list[str]):
    """The project's `on: pre-commit` tools on the staged files (runtime/tools.py): a fixer's changes are staged again,
    a block failure refuses the commit with the trimmed output (the code step's retry hands it to the agent before it),
    a warn failure is one line in the commit note. Skipped when a lint run already checked exactly these files.
    Returns (note lines, staged files), or (a refusal, staged files)."""
    lint_tree = a.data.get("lint_tree")
    if lint_tree and lint_tree == git.worktree_tree(a.root):
        return ["checked by the lint run"], staged
    pc = tool_runner.pre_commit(a.root, staged, include_stacks=not a.fake, emit=a.emit,
                                restage=lambda: git.git(a.root, "add", "-A", "--", *staged))
    if not pc["ran"]:
        return [], staged
    if not pc["ok"]:
        names = ", ".join(r["name"] for r in tool_runner.failing(pc["ran"], ("block",)))
        return _refuse(a, f"Static checks refused the commit: {names}.",
                       "\n\n".join(pc["problems"]) + "\n\nFix what they report; the commit runs them again."), staged
    if pc["fixed"]:
        staged = [f for f in git.git(a.root, "diff", "--cached", "--name-only").stdout.splitlines() if f.strip()]
    fixed = [r["name"] for r in pc["ran"] if r["fail"] == "fix" and r["available"]]
    missing = [r["name"] for r in pc["ran"] if not r["available"]]
    notes = ([f"{', '.join(fixed)} re-staged"] if fixed else []) + pc["warnings"] + \
        ([f"not installed: {', '.join(missing)}"] if missing else [])
    passed = len(pc["ran"]) - len(missing)
    return notes or [f"{passed} check(s) passed"], staged


KEELBOT = ("KeelBot", "keel.dev.bot@gmail.com")
COAUTHOR = f"Co-Authored-By: {KEELBOT[0]} <{KEELBOT[1]}>"
PLACEHOLDER_EMAILS = {"keel@localhost"}       # the image's system git identity (Dockerfile): nobody's name
_AUTHOR = re.compile(r"\s*([^<>]+?)\s*<\s*([^<>\s]+@[^<>\s]+)\s*>\s*")


def commit_ident(root: str, settings: dict | None, cfg: dict) -> tuple[str, str]:
    """Who a keel commit is by: the setting commit_author ("Name <email>"), else .keel/config.yml commit.author_name /
    author_email, else the project's git name (never the image's placeholder), else KeelBot itself."""
    m = _AUTHOR.fullmatch(str((settings or {}).get("commit_author") or ""))
    if m:
        return m[1], m[2]
    c = cfg.get("commit") or {}
    name, email = str(c.get("author_name") or "").strip(), str(c.get("author_email") or "").strip()
    if name and email and email != KEELBOT[1]:
        return name, email
    name = git.git(root, "config", "user.name").stdout.strip()
    email = git.git(root, "config", "user.email").stdout.strip()
    if name and email and email not in PLACEHOLDER_EMAILS and email != KEELBOT[1]:
        return name, email
    return KEELBOT


def commit_args(root: str, settings: dict | None, cfg: dict) -> tuple[list[str], list[str]]:
    """A keel commit's `-c user.name -c user.email` options, and its last paragraph: the KeelBot co-author line, unless
    the setting commit_coauthor is off (it is on by default) or KeelBot is the author already."""
    name, email = commit_ident(root, settings, cfg)
    coauthor = (settings or {}).get("commit_coauthor") is not False and email != KEELBOT[1]
    return ["-c", f"user.name={name}", "-c", f"user.email={email}"], (["-m", COAUTHOR] if coauthor else [])


TICKET = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,29}-\d{1,9}$")
_TITLE_TICKET = re.compile(r"^\s*([A-Z][A-Z0-9_]{0,29}-\d{1,9})\s*:\s")


def ticket_key(a: ActionInput) -> str | None:
    """The ticket a flow works on: data.ticket (the api passes a task's Jira key), else a "SHOPS-4: ..." flow title (how
    a task names its flow). None when there is none."""
    given = str((a.data or {}).get("ticket") or "").strip()
    if TICKET.match(given):
        return given.upper()
    m = _TITLE_TICKET.match(a.title or "")
    if m:
        return m.group(1)
    # 0.18.57 else the branch name (feature/SHOPS-1234-euro-prices)
    return commit_style.ticket_from_branch(git.branch(a.root)) if git.is_repo(a.root) else None


def commit_message(prefix: str, subject: str, limit: int = 72) -> tuple[str, str]:
    """A short subject line (whole words, at most `limit` chars) and, when it was cut, the rest of the criterion as the
    body ("…" and the words that did not fit): 0.18.57 the body never repeats the subject."""
    return commit_style.fit(f"{prefix}: {' '.join(str(subject).split())}", limit)


def revert_manifests(root: str, files: list[str]):
    """Put manifest files back as they are in HEAD (a new one is removed)."""
    for rel in files:
        if git.tracked_in_head(root, rel):
            git.git(root, "checkout", "HEAD", "--", rel)
        else:
            (Path(root) / rel).unlink(missing_ok=True)


def _detect(root: str, init: dict | None = None) -> dict:
    from . import init_gates

    r = Path(root)
    cfg: dict = {"version": 4}
    if (r / "pyproject.toml").exists():
        cfg["backend"] = {"dir": "", "build": "python -m pytest"}
    # The same commands keel uses without a config (node:test needs --test-name-pattern, not Jest's -t).
    # 0.18.57 with init's answers: the backend commands run in the compose service chosen, the services start command
    cmds = init_gates.answered_commands(root, init, testcmd.config_commands(root))
    if cmds:
        cfg["commands"] = cmds
    # 0.18.17 the commands were found in one backend subfolder: written as backend.dir, so they run there
    sub = testcmd.detected_dir(root)
    if sub:
        cfg["backend"] = {**(cfg.get("backend") or {}), "dir": sub}
    # where keel keeps its files (keel_engine/layout.py), written down so the choice never changes by itself
    cfg["layout"] = layout.default_name(r, (r / ".keel").is_symlink(), layout.raw_config(r))
    if cfg["layout"] == "repo" and (r / "docs" / "specs").is_dir():
        cfg["specs"] = {"dir": "docs/specs"}
    return cfg


INIT_RUN_ONLY = ("said", "write_sections", "layout", "notes_cost")   # init answers not kept in the config's init: block


def write_config(a: ActionInput) -> ActionResult:
    """.keel/config.yml from the approved plan, and (keel 0.18.22) the setup: block the plan proposed for a project stack
    of a language keel's image lacks (runtime/stack_setup.py)."""
    from . import stack_setup

    r = _write_config(a)
    try:
        added = stack_setup.write_proposals(a.root)
    except OSError as exc:
        return ActionResult(r.ok, f"{r.note} Could not add the setup: block to a stack: {exc}", r.detail, r.update)
    if not added:
        return r
    stack_setup.SYSTEM.request(a.root)          # install what the new block lists (in keel's container)
    return ActionResult(r.ok, f"{r.note} Added a setup: block to {', '.join(added)}.", r.detail, r.update)


def _notes_build(a: ActionInput, cfg: dict | None) -> str | None:
    """0.18.57 the project notes' switch (knowledge.build) init writes: on when the person ticked sections to write;
    else a new config says off, and a re-run leaves what the config says (someone may have turned them off)."""
    from . import knowledge

    said = knowledge.build_setting(cfg) if cfg is not None else None
    if (a.init or {}).get("write_sections"):
        return None if said == "on" else "on"
    return None if said else ("on" if knowledge.enabled(a.root) else "off")


def _write_config(a: ActionInput) -> ActionResult:
    f = Path(a.root) / ".keel" / "config.yml"
    stored = {k: v for k, v in (a.init or {}).items() if k not in INIT_RUN_ONLY}
    if f.exists():
        # 0.18.22 a re-run records the new answers (init: block only; the rest of the file is left as it is)
        text = f.read_text()
        cfg = yaml.safe_load(text) or {}
        build = _notes_build(a, cfg if isinstance(cfg, dict) else None) if a.init else None
        if not a.init or not isinstance(cfg, dict) or (cfg.get("init") == stored and not build):
            return ActionResult(True, ".keel/config.yml already exists; left as it is.")
        head = "".join(line + "\n" for line in text.splitlines() if line.startswith("#"))
        cfg["init"] = stored
        if build:
            cfg["knowledge"] = {**(cfg.get("knowledge") if isinstance(cfg.get("knowledge"), dict) else {}), "build": build}
        # 0.18.57 an answer that runs the tools in a compose service (or starts the services) changes the commands too
        from . import init_gates

        have = cfg.get("commands") if isinstance(cfg.get("commands"), dict) else {}
        cmds = init_gates.answered_commands(a.root, a.init, have)
        if cmds != have:
            cfg["commands"] = cmds
        f.write_text(head + yaml.safe_dump(cfg, sort_keys=False))
        return ActionResult(True, ".keel/config.yml exists; its init: block now has your answers"
                            + (" and the commands they change." if cmds != have else "."), f.read_text())
    f.parent.mkdir(parents=True, exist_ok=True)
    cfg = _detect(a.root, a.init)
    if (a.init or {}).get("layout") in layout.LAYOUTS:
        cfg["layout"] = a.init["layout"]
        if cfg["layout"] != "repo":
            cfg.pop("specs", None)
    if a.init:
        cfg["init"] = stored
    # 0.18.57 the project notes: off unless the person asked for them (Wiki › Build them turns them on later)
    cfg["knowledge"] = {"build": _notes_build(a, None) or "off"}
    f.write_text("# Written by keel v2 init. Edit freely.\n"
                 "# layout: where keel keeps its files: keel (.keel/, out of git), repo (docs/, committed) or external.\n"
                 + yaml.safe_dump(cfg, sort_keys=False))
    layout.hide(a.root)
    return ActionResult(True, "Wrote .keel/config.yml.", f.read_text())


async def ladder(a: ActionInput) -> ActionResult:
    def runner(root: str, cmd: str) -> tuple[int, str]:
        return testcmd.run(root, cmd, 900, command_env())

    # each rung shows live on the step's job (a.progress is safe to call from the ladder's thread)
    progress = a.progress if callable(a.progress) else None
    ok, rungs = await asyncio.to_thread(run_ladder.run, a.root, a.fake, runner, bool(a.settings.get("fast")), progress)
    lines = ["# Running this project", "", "Written by keel init. Every command below passed the run ladder.", ""]
    for r in rungs:
        if r["status"] == "pass" and r["n"] != 1:
            lines += [f"## {r['n']}. {r['name']}", "", *tool_runner.fenced(r["cmd"]), ""]
    unchecked = [r for r in rungs if r["status"] == "skipped" and "setup." in str(r.get("detail") or "")]
    if unchecked:
        # keel v1 writes a rung the user accepted (or left out) into the runbook as not checked
        lines += ["## Not checked", "", *[f"- {r['n']}. {r['name']}: {r.get('detail')}" for r in unchecked], ""]
    f = Path(a.root) / layout.resolve(a.root).runbook
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
        "summary": None if r["pass"] else f"{len(r['problems'])} problem(s) in {knowledge.knowledge_dir(a.root)}/"}, git.head(a.root) if git.is_repo(a.root) else None)
    if not r["pass"]:
        return ActionResult(False, f"The knowledge base has {len(r['problems'])} problem(s).", "\n".join(r["problems"]))
    cites = sum(s["citations"] for s in checked)
    from .init_actions import arch_from_knowledge       # init_actions imports this module
    arch_note = arch_from_knowledge(a.root)
    return ActionResult(True, f"Knowledge base: {len(checked)} section(s), {cites} citation(s), no problems."
                        + (f" {arch_note}." if arch_note else ""))


def push_check(a: ActionInput) -> ActionResult:
    if not git.is_repo(a.root):
        return ActionResult(True, "Not a git repository.", update={"blockers": []})
    found = push_gates.push_blockers(a.root, a.base, project=a.key)
    warned = push_gates.push_warnings(a.root, a.base, project=a.key)
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
