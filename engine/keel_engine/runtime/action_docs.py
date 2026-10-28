"""What each code-step action really does, in plain words, for "explain a step" (runtime/explain.py).

One entry per action name the engine can run: `summary` (one line) and `steps` (what it runs and decides, in
order). An entry without `steps` uses the action function's own docstring instead (the feature, review and fix
helpers have good ones). A part's actions (db:*, git:*, ci:*) bring their own words: its `docs`
(keel_engine/extensions.py); all_docs() has both. tests/test_explain.py fails when an action in the dispatch
(actions.run_action, the action modules' ACTIONS tables, the compiler's own start_flow and escalate_model, the parts'
actions) has no entry, so a new action needs its words before it ships.
"""

from __future__ import annotations

import inspect
import re

from .. import extensions

# The compiler runs these itself (runtime/compiler.py code_step); they are not in actions.run_action.
COMPILER_ACTIONS = ("start_flow", "escalate_model")

DOCS: dict[str, dict] = {
    # ---------------------------------------------------------------- tests (runtime/actions.py)
    "verify_red": {
        "summary": "Runs the test of the current criterion and passes only when it fails for the right reason.",
        "steps": [
            "Finds the test command for the criterion: commands.api_test_ac / web_test_ac in .keel/config.yml with {AC} "
            "replaced, else the one keel detects (gradle, pytest, npm).",
            "Runs it in the project (15 minutes at most).",
            "No test ran (no test name holds the criterion id): fails, \"not a red state\"; the red agent gets the output.",
            "The test passes: the criterion may be met already; keel asks you (approve = already met, its test is "
            "committed and green, review and gate are skipped; reject = back to red for a stricter test).",
            "It fails from a setup problem (compile error, missing module, connection refused, the red_reject list): fails, "
            "and the red agent fixes the test setup.",
            "It fails on an assertion: red confirmed; the criterion's status becomes red.",
            "In the bug flow (a BUG-<n> criterion and no AC loop): only the bug's test runs, in its layer (a WEB "
            "reproducer runs web_test_ac); a passing test, or a failure that is not that test's (a test that was red "
            "before), is not a reproduction; Gate R shows the test file::name, the command and the failure.",
            "With the fake model (simulated checks) nothing runs: a fixed assertion failure stands in.",
        ],
    },
    "verify_green": {
        "summary": "Runs the criterion's test (or the whole suite outside the AC loop) and passes only when it passes.",
        "steps": [
            "In the AC loop: the criterion's test command; outside it: the whole suite (commands.api_test_module or the "
            "detected one).",
            "No test for the criterion ran: fails, the red test is missing.",
            "Exit code not 0: fails, \"does not pass yet\"; the output goes back to the agent before it.",
            "Exit 0: green. A whole-suite pass also writes the release verdict for this commit.",
            "In the bug flow: the bug's own test first (it must pass now), then the whole suite.",
            "0.18.28 a failure shows the failing tests and their messages first; a run that printed nothing runs once "
            "more with a JUnit report (phpunit --log-junit, pytest --junitxml) to name them.",
            "0.18.28 a whole-suite failure in a flow with criteria also says who is the likely cause: each failing test "
            "run on its own (passes alone = polluted by another test), and the branch's tests that write to the "
            "database without a rollback or cleanup, with their criterion (runtime/suite_failure.py).",
            "Simulated runs pass with \"1 passed\" and write no verdict.",
        ],
    },
    "verify_after_green": {
        "summary": "0.18.28 after a criterion's green (or \"already passes\"): the tests outside its own filter.",
        "steps": [
            "checks.after_green in .keel/config.yml: none, changed (default) or suite.",
            "changed: the branch's test files (commands.api_test_paths / web_test_paths with {PATHS}, else the runner "
            "with the paths for pytest, vitest, jest, mocha, else the module suite); and the whole suite every "
            "checks.suite_every criteria (default 3) and at the end of each lane.",
            "suite: every module suite after every criterion.",
            "A failure says \"AC-n's change broke tests outside it: ...\" with the failing tests and the output, and "
            "goes back to the criterion's green (the step's back); the criterion's own test files are unlocked there.",
            "The AC gate shows what ran (data.after_green). Simulated runs pass.",
        ],
    },
    "verify_release": {
        "summary": "Runs the whole test suite; its result is the release verdict for HEAD.",
        "steps": [
            "Runs the module test command once; a failure runs once more (loops.flaky_reruns, default 1).",
            "Failed then passed = flaky: passes with a warning and the flaky tests are kept for the PR body.",
            "Writes the release verdict (pass or fail) stamped with HEAD; push blockers read it.",
        ],
    },
    "verify_coverage": {
        "summary": "Checks coverage with commands.coverage or the coverage reports; writes the coverage verdict.",
        "steps": [
            "With coverage.reports: runs the coverage command(s), reads the jacoco/kover XML or lcov reports and measures "
            "only the lines changed since the base branch (changed_lines, changed_branches, global; 100% on "
            "security.coverage_paths).",
            "Uncovered lines become groups (same file, at most 8 lines apart) in data.coverage_groups for the cover loop.",
            "Without reports: commands.coverage must exit 0. Without that command there is no coverage gate (it passes).",
            "Writes the sha-stamped coverage verdict.",
        ],
    },
    "verify_fast": {
        "summary": "Compiles and type-checks only what the branch touched; writes the fast verdict.",
        "steps": [
            "Lists the files changed since the base branch.",
            "Runs commands.api_compile when backend files or the contract changed, commands.web_typecheck when frontend "
            "files changed, commands.contract_lint when the contract changed. The first failure stops.",
            "With boundaries.enforce on, checks the import lines of the changed files against the architecture rules "
            "(block = a failure, warn = a note).",
            "A missing command is \"not run\", not a pass.",
        ],
    },
    "verify_module": {
        "summary": "Runs the module test suites (api, and web when configured); writes the module verdict.",
        "steps": [
            "Runs the api module suite (commands.api_test_module or detected) and commands.web_test_module.",
            "A failing suite runs once more: failed then passed = flaky (a pass with a warning).",
            "No test command at all: not available (no verdict pass).",
        ],
    },
    "verify_deps": {
        "summary": "Runs the dependency audit when a manifest or lockfile changed on the branch; writes the deps verdict.",
        "steps": [
            "No manifest or lockfile changed since the base branch: passes, nothing to check.",
            "Runs commands.deps_api / deps_web in their folders; a non-zero exit is a finding.",
            "No audit command configured: not available (the push gate warns).",
        ],
    },
    "audit": {
        "summary": "Reads the branch's commits for broken test-first discipline; writes the audit verdict.",
        "steps": [
            "Every commit since the base branch: a test(...) commit may not hold production code; a feat/fix commit may "
            "not hold tests (unless commit_style is single, or it is a review/coverage/security/ship fix).",
            "No commit may add skip/only/disabled markers to tests.",
            "Every unlock needs a reason; a spike/ branch cannot ship.",
        ],
    },
    "trace": {
        "summary": "Links every criterion to its test commit and its implementation commit; writes the trace verdict.",
        "steps": [
            "Reads the commit subjects since the base branch: test(AC-n) is the red commit, feat(AC-n) or fix(AC-n) the "
            "green one.",
            "Builds the trace table (shown at the final review and in the PR body). Missing commits are reported, not "
            "refused.",
        ],
    },
    "trace_strict": {
        "summary": "Like trace, but a criterion without a test commit fails the step.",
        "steps": [
            "Same table as trace; any criterion (not already met) with no test(AC-n) commit fails the check.",
        ],
    },
    "arch": {
        "summary": "Checks the changed files' imports against the project's architecture rules; writes the arch verdict.",
        "steps": [
            "Reads boundaries.rules or architecture.rules from .keel/config.yml; without rules it is not available.",
            "Greps the import lines of the files changed on the branch; every forbidden import is a violation.",
        ],
    },
    "pr": {
        "summary": "Builds the PR body; the next gate shows it.",
        "steps": [
            "Spec extract (or the request and commit list), the trace table, the coverage verdict, accepted uncovered lines, "
            "skipped gates and ship steps, unlocks used, flaky tests.",
            "Stores it as pr_body. Nothing is pushed or opened.",
        ],
    },
    "open_pr": {
        "summary": "Opens the PR with gh, only when the gate before it was approved and the branch is pushed.",
        "steps": [
            "Needs the PR body, an approved PR gate and a GitHub token (Connections › GitHub).",
            "keel never pushes: an unpushed branch or unpushed commits are reported and no PR is opened.",
            "Runs gh pr create with the body; the PR link goes to data.pr_url.",
        ],
    },
    "review_lenses": {
        "summary": "Picks the review lenses: one reviewer per lens in the next step.",
        "steps": [
            "The lenses chosen at the opening gate, else review.lenses in .keel/config.yml, else correctness, security and "
            "performance; architecture joins when the project sets a style or rules.",
            "Writes data.review_lenses (one item per lens).",
        ],
    },
    "memory_plan": {
        "summary": "Is the knowledge base stale? Only when a file a section cites changed since the last knowledge check "
                   "(else since the branch started); then the gate asks update or skip.",
        "steps": [
            "No knowledge base: nothing runs (no librarian, no check).",
            "Current: the librarian does not run; the knowledge check does.",
            "Stale: the sections and the changed files they cite, for the gate (update is recommended; it costs tokens).",
        ],
    },
    "config_check": {
        "summary": "knowledge-refresh: are the commands in .keel/config.yml still runnable as written (0.18.57)?",
        "steps": [
            "Reads files only, runs nothing: a docker compose service a command execs into is in the compose files, a "
            "composer / npm / yarn script is in composer.json / package.json, a make target is in the Makefile, a "
            "script path exists.",
            "Soft: a stale command is listed with .keel/config.yml and its line (data.config_check_output); the flow goes on.",
        ],
    },
    "knowledge_scan": {
        "summary": "knowledge-refresh: finds the stale sections (a file they cite changed since the last knowledge check) "
                   "and the chosen ones not written yet; a refresh without a list updates only these.",
    },
    "memory_skip": {
        "summary": "The knowledge gate's skip: records the reason for the PR body; neither the librarian nor the "
                   "knowledge check runs.",
    },
    "coverage_report": {
        "summary": "Says what cover leaves behind: the latest coverage verdict and every group accepted, not covered.",
    },
    # ---------------------------------------------------------------- static checks (lint_actions.py, tools.py)
    "lint_scope": {
        "summary": "Picks the files the lint run checks: the branch diff and uncommitted files, or every tracked file.",
        "steps": [
            "data.scope diff (default): the files changed since the base branch plus the uncommitted ones; all: git ls-files.",
            "Your own uncommitted files (there when the flow started) are left out: a formatter never rewrites your work.",
            "Not a git repository: the flow stops.",
        ],
    },
    "lint_run": {
        "summary": "Runs every check tool of the project's stacks and config on those files, fixers first; writes the lint "
                   "verdict.",
        "steps": [
            "The tools declared on manual, edit, batch and pre-commit (kind check), fix tools first so their changes are "
            "checked too.",
            "A tool that is not installed is \"not available\", never a failure.",
            "A failing block or fix tool fails the check; warn tools are listed as warnings. The findings go to "
            "data.lint_findings for the fixing agent; the sha-stamped lint verdict is written.",
        ],
    },
    "lint_report": {
        "summary": "Writes the lint report: the verdict, one row per tool, what still fails and whether the flow went on "
                   "anyway.",
        "steps": ["Reads the latest lint verdict and the last run's results; the report is what the next gate shows "
                  "(data.lint_report)."],
    },
    "verify_lint": {
        "summary": "Ship's static checks: the check tools on the branch diff without fixers; writes the lint verdict.",
        "steps": [
            "Runs the check tools on the files changed since the base branch; fix tools are left out, so nothing in the "
            "tree changes.",
            "A failing block (or fix) tool fails the check; warn tools are warnings; nothing installed is \"not available\". "
            "In ship the step is soft: the final review shows a failure as an exception.",
        ],
    },
    # ---------------------------------------------------------------- commit + config (runtime/actions.py)
    "commit": {
        "summary": "Makes the real git commit for this phase, after keel's checks; refusals put the staging back.",
        "steps": [
            "Not a git repository: nothing is committed (passes).",
            "Stages everything (or only the step's paths), never keel's own files (.keel/ state, .codegraph).",
            "Unstages your own edits: files that were already changed when the flow started and that no agent touched since.",
            "Nothing staged: passes, \"Nothing to commit\".",
            "Runs the project's pre-commit tools on the staged files (runtime/tools.py), fixers first: a fix tool's "
            "changes are staged again, a block tool's failure refuses the commit with its output, a warn tool adds a line "
            "to the note. Skipped when a lint run already checked exactly this tree.",
            "Looks for secrets in the staged diff (keys, tokens, private keys); any hit refuses the commit.",
            "A new dependency in a manifest asks you first (approve keeps it; reject puts the manifest back and commits the "
            "rest).",
            "Checks every staged file's bucket against the phase's commit type (a red commit may not hold production "
            "code, a green one no tests, ...); a file you unlocked for this phase passes.",
            "Extra rules: a trivial commit may not edit existing tests; a coverage commit may only delete production lines "
            "and never touch .keel/config.yml; in a change flow an escalation trigger (contract, migration, auth, size) "
            "asks whether to become a feature flow.",
            "Commits as you (Settings › Commit author, else .keel/config.yml commit.author_name / author_email, else the "
            "project's git name, else KeelBot) with the message type(scope): title, the criterion id as the scope inside "
            "the AC loop; a subject longer than 72 characters is cut and the full title goes in the body. With Settings › "
            "KeelBot as co-author (on by default) the message ends with Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>.",
            "After the commit: the code graph syncs in the background and the push blockers are refreshed.",
        ],
    },
    "write_config": {
        "summary": "Writes .keel/config.yml from what keel detects (only when it does not exist yet).",
        "steps": [
            "An existing .keel/config.yml is left as it is.",
            "Detects the backend (pyproject), the test commands keel would use, the specs folder, plus init's answers.",
        ],
    },
    "ladder": {
        "summary": "Runs the setup ladder (build, test, boot, health ...) rung by rung and writes the runbook (docs/RUNNING.md or .keel/RUNNING.md).",
        "steps": [
            "Each rung must pass before the next one runs; the first failure stops the ladder.",
            "Every command that passed is written to the runbook (docs/RUNNING.md or .keel/RUNNING.md, as the layout says).",
            "Simulated with the fake model.",
        ],
    },
    "knowledge_check": {
        "summary": "Checks the knowledge base (docs/knowledge/ or .keel/knowledge/): citations resolve, no placeholders, the chosen sections exist; writes the memory "
                   "verdict.",
    },
    "memory_check": {
        "summary": "The old name of knowledge_check: same check, same memory verdict.",
        "steps": ["Runs knowledge_check: citations resolve, no placeholders, the chosen sections exist."],
    },
    "push_check": {
        "summary": "Checks the tree is clean and lists the push blockers; keel never pushes.",
        "steps": [
            "Uncommitted files: fails with their list.",
            "Otherwise lists the push blockers (missing or failing verdicts for HEAD) and warnings; passes either way.",
        ],
    },
    "run:": {
        "summary": "Runs one shell command in the project; exit 0 passes.",
        "steps": [
            "The command first goes through keel's shell guard for this phase (no git commit, no force push, no dependency "
            "install, no write to a file the phase may not edit); a refused command fails without running.",
            "Runs it (15 minutes at most); the last 3000 characters of the output are kept.",
        ],
    },
    # ---------------------------------------------------------------- compiler
    "start_flow": {
        "summary": "Starts another workflow's thread on this project with a seed; both threads keep the link.",
        "steps": [
            "The seed's \"$path\" values are read from this flow's state (title and request by default).",
            "A step that runs again never starts a second child.",
            "then: end finishes this flow here; otherwise it goes on.",
        ],
    },
    "escalate_model": {
        "summary": "Moves the step's agent (default the investigator) to a stronger model for the rest of the flow.",
        "steps": [
            "settings.stronger_model, else Opus for Claude, else high effort for Codex and Copilot.",
            "Sets marker ESCALATED: yes.",
        ],
    },
    # ---------------------------------------------------------------- review, diagnose, fix, change (flow_actions.py)
    "review_scope": {
        "summary": "Works out what the review reads (lens, base branch) and one item per reviewer; stops on an empty diff.",
    },
    "report": {
        "summary": "Collects every fan-out answer word for word, with a tally of their markers, for the next gate.",
    },
    "spike_report": {
        "summary": "The spike's report: a short summary first (decision, open questions before building, the angles' "
                   "conflicts, cost, spec or drop, one counted VERDICT line), then the findings and each angle's clean report.",
    },
    "investigation_note": {
        "summary": "Writes docs/investigations/<date>-<slug>.md with every hypothesis and commits it.",
    },
    "bug_intake": {
        "summary": "Gives the bug its id BUG-<n> (the flow's criterion: its test, commits and trace name it) and reads the "
                   "report's options: no_gates waives Gate R and F; needs_e2e makes the regression e2e required.",
    },
    "bug_plan": {
        "summary": "Keeps the Gate F plan's root cause, for the fix commit's body and the PR body's bug section.",
    },
    "bug_drop_red": {
        "summary": "Gate R \"reproduce differently\": drops the rejected test(BUG-<n>) commit and its files, so the next "
                   "test replaces it instead of stacking.",
    },
    "bug_reset": {
        "summary": "After two failed fixes: puts back keel's uncommitted changes (the reproducing test stays) and keeps "
                   "what was learned for the next investigation; a third reset stops the flow.",
    },
    "reset": {
        "summary": "Puts back what keel's agents changed and did not commit; your own uncommitted files stay.",
    },
    "change_size": {
        "summary": "Numbers the inline criteria CHG-<n>.<m> and recommends trivial, small or feature.",
    },
    "change_start": {
        "summary": "Starts a small change: criteria required, one gate at the end; staying small against advice is logged.",
    },
    # ---------------------------------------------------------------- feature (feature_actions.py)
    "preflight": {
        "summary": "Checks the repo has a test command and puts the flow on its own branch (feat/<slug>).",
    },
    "explore_areas": {
        "summary": "Makes one explorer item per area (api, web, data) once the criteria are settled.",
    },
    "spec_sync": {
        "summary": "Re-reads the criteria from the spec in the plan's order and prepares the spec gate's text; in a project without "
                   "web tests a [WEB] criterion becomes [API] (the gate says so).",
    },
    "spec_freeze": {
        "summary": "Freezes the approved spec (status, dates) and commits only the spec: docs: spec and plan: <title>.",
    },
    "spec_restart": {
        "summary": "Starts the interview again after the spec gate's reject (handed-over criteria stay).",
    },
    "amend_start": {
        "summary": "Takes the reason for an amendment from the agent's AMEND line or the contract gate's note.",
    },
    "spec_amendment": {
        "summary": "Prepares the amendment gate's text from the spec's newest dated block.",
    },
    "spec_amend_commit": {
        "summary": "Applies the approved amendment to the criteria lines (layer, wording) and commits it alone (docs: amend: ...); "
                   "new criteria join, reopened ones are todo.",
    },
    "amend_reorder": {
        "summary": "The amendment loop gate's first / skip: builds the named criterion first, or moves this one to the end.",
    },
    "stuck": {
        "summary": "The red / green agent said BLOCKED: builds the criteria it names first, else asks skip / edit / retry / stop "
                   "(a plain approve skips, or edits when no other criterion is left; past the send-back limit only skip / stop).",
    },
    "amend_with_note": {
        "summary": "The amendment loop gate's edit: the amendment is written again with the person's note.",
    },
    "amend_stop": {
        "summary": "The amendment loop gate's stop: the flow stops here; the commits so far stay.",
    },
    "show_diff": {
        "summary": "Prepares the diff the next gate shows: keel's uncommitted change, else the branch diff. For the "
                   "contract: each OpenAPI operation as new, changed or breaking callers (removed or narrowed) first.",
    },
    "contract_check": {
        "summary": "Before the contract gate: runs commands.codegen, then the fast check; a failure goes back to the "
                   "contract agent.",
        "steps": [
            "Nothing changed: nothing to check.",
            "Runs commands.codegen when it is set (else says the generated sides were not refreshed).",
            "Runs the fast check (api compile, web typecheck, contract lint): a contract that does not build fails "
            "here, before the gate, with the output for the contract agent (2 rounds).",
        ],
    },
    "test_check": {
        "summary": "quick: the change comes with a test, or the agent's UNTESTABLE: line says why not; neither leads "
                   "the look gate's diff with a warning (the person decides, nothing is refused).",
    },
    "project_rules": {
        "summary": "init and knowledge-refresh: finds the repo's own rules for coding agents (AGENTS.md, CLAUDE.md, "
                   ".github/copilot-instructions.md, an onboarding doc, .cursor/rules, .claude/skills, .opencode/skills), "
                   "writes the knowledge page project-rules.md and .keel/project-rules.json (which skill goes to which "
                   "agents: the api loads a test skill for test-author and the implementer).",
    },
    "thread_view": {
        "summary": "address: the look gate shows the review thread (every reply, the person's answer, the code it is on) "
                   "next to the diff, with the intent check's verdict; a change that does not do what the reviewer "
                   "asked leads the text with the reason.",
    },
    "security_scope": {
        "summary": "Makes the security items: the auditor always, the dependency triager only when verify deps found "
                   "something.",
    },
    "e2e_scope": {
        "summary": "Collects the [E2E] criteria and checks commands.e2e is set (markers E2E and TOOL).",
    },
    "e2e_unrun": {
        "summary": "No e2e command: the e2e specs are written but not run, recorded as a skipped ship step.",
        "steps": [
            "Tells the e2e agent to write the specs without running them.",
            "Adds e2e-run to data.ship_skipped with the reason (the PR body and final review show it).",
        ],
    },
    "verify_e2e": {
        "summary": "Runs the e2e command; a failure runs once more (fail then pass = flaky).",
    },
    "stack_up": {
        "summary": "Before smoke and e2e: brings the stack up (commands.services) and migrates (commands.migrate) when "
                   "the project configures them and smoke or e2e has something to run; a failure is a warning.",
    },
    "smoke_scope": {
        "summary": "Collects the [SMOKE] criteria or the spec's Smoke checks section (marker SMOKE).",
    },
    "verify_smoke": {
        "summary": "Runs every smoke/*.sh, then commands.smoke_e2e; the first failure stops.",
    },
    "full_review": {
        "summary": "After the full-diff review: records its verdict (pass, or findings accepted) and the sha it read; ship "
                   "drops its correctness lens when it passed and no production file changed since.",
    },
    "ship_ready": {
        "summary": "Prepares the \"ready to ship?\" gate: criteria done, the full-diff review, security, e2e and smoke, "
                   "what is still open, and the choices ship / diff / wrong / stop.",
        "steps": [
            "Lists the criteria with their status and how many are done.",
            "Says the full-diff review's verdict and sha, what security found (or that it was skipped), and what e2e and "
            "smoke did (ran, written but not run, nothing to do, skipped).",
            "Lists what is still open: unfinished criteria, findings let through with why, skipped steps, flaky tests, "
            "warnings.",
            "From the diff choice (a step id ending in _diff) it adds the branch diff, then the gate asks again.",
        ],
    },
    "ship_stop": {
        "summary": "The \"ready to ship?\" gate's stop: the flow stops before ship; the branch and its commits stay.",
    },
    "close_flow": {
        "summary": "Says what shipped in one line (criteria done, the ADRs this flow added).",
    },
    # ---------------------------------------------------------------- hunt, hunt-next (hunt_actions.py)
    "hunt_start": {
        "summary": "Opens a hunt run in the backlog with its mode, scope and the proposed lenses.",
        "steps": [
            "Mode auto or semi, fast, scope all | diff | paths (settings or a parent flow's seed).",
            "Proposed lenses: hunt.lenses (fast: the fast ones), or the lenses asked for (each needs a brief).",
            "Writes the lens list the confirm gate shows.",
        ],
    },
    "hunt_confirm": {
        "summary": "Turns the lenses you confirmed into one sweep item per lens and lane.",
        "steps": [
            "payload.lenses, or \"drop x\" in your note, changes the proposed set.",
            "Each item carries the lens brief, the scope, the lane rule and the candidate cap (halved when fast).",
        ],
    },
    "hunt_deps": {
        "summary": "For the security lens, runs the dependency audit first and hands its output to the hunters.",
    },
    "hunt_ingest": {
        "summary": "Turns each hunter's JSON list into candidates (UNVERIFIED) and writes candidates.md.",
        "steps": [
            "Enforces the lane by classifying every cited path; drops severity; caps per lens; merges the same file within "
            "a few lines as one candidate.",
            "Writes docs/hunts/<run>/candidates.md and one prover item per candidate (symptom and where only).",
        ],
    },
    "hunt_verdicts": {
        "summary": "Records each prover's verdict; a refused verdict goes back to the provers.",
        "steps": [
            "proven needs a recipe that ran twice and a severity; a 5xx is never below severity_floor_5xx.",
            "Refused verdicts go back at most prove_rounds times, then become unproven (or are raised to the floor).",
        ],
    },
    "hunt_group": {
        "summary": "Stores the investigator's groups: findings that share one cause.",
        "steps": ["Reads the groups list the investigator collected; every proven or suspected candidate belongs to one "
                  "group, a lone finding is its own group."],
    },
    "hunt_report": {
        "summary": "Writes docs/hunts/<run>/report.md and the recipes; refuses while any candidate has no verdict.",
        "steps": ["Refuses while any candidate has no verdict.",
                  "Writes report.md (groups by worst severity, every finding with its recipe) and repro/<file> for each "
                  "proven recipe; marks needs_e2e for user-visible ones (none in a project with no UI or HTTP layer); the "
                  "triage gate shows the summary."],
    },
    "hunt_commit": {
        "summary": "Commits only docs/hunts/<run>/ as docs(HUNT-<run>): bug hunt: N proven, M suspected. With layout "
                   "keel or external the report is in .keel/hunts/<run>/, outside git: nothing is committed.",
    },
    "hunt_close": {
        "summary": "Closes a finding or group (fixed | accepted | wontfix) with the note from the gate's answer.",
    },
    "hunt_take": {
        "summary": "hunt-next: takes the top open group (or the one picked) and starts fix (defect) or feature "
                   "(unspecified) with its recipe.",
        "steps": [
            "Refuses while any candidate has no verdict or the report folder is uncommitted.",
            "The open group with the worst severity (or the finding or group picked at the preview gate or the hunt's "
            "triage) is marked dispatched; marker FLOW = fix | feature | none.",
            "The seed's title is the lead's symptom, never the group's cause (that goes as evidence); a verdict proven on "
            "an older commit is flagged stale with the files changed since.",
        ],
    },
    "hunt_preview": {
        "summary": "hunt-next: what take would hand over (top group, severity, symptoms, a stale verdict) for the next gate; "
                   "nothing is marked.",
    },
    "hunt_map": {
        "summary": "Gives the explorer's repository map (the step before) to every hunter item.",
    },
    "hunt_stack": {
        "summary": "Before the provers: checks the stack's health (starts commands.services once when it is down); "
                   "still down, the stack gate asks recheck, prove anyway or stop.",
    },
    # ---------------------------------------------------------------- init (init_actions.py)
    "arch_detect": {
        "summary": "Detects the architecture style; low confidence asks the arch-surveyor (not in fast mode).",
        "steps": ["Scores the folder layout and imports for hexagonal, DDD, layered, MVC and feature-sliced styles.",
                  "Low confidence (and not fast): one arch-surveyor item, which ends with ARCH: <style> <confidence>."],
    },
    "arch_set": {
        "summary": "Writes architecture: {style, confidence, source} to .keel/config.yml (a style you set stays).",
        "steps": ["Takes the surveyor's ARCH line (style and confidence) when there is one, else the detection.",
                  "A style already set by hand in .keel/config.yml is left as it is."],
    },
    "git_check": {
        "summary": "Not a git repository: the git_repo gate asks init / go on without git / stop (data.git_question).",
    },
    "git_init": {
        "summary": "Runs git init in the project folder when the git_repo gate's answer was init.",
    },
    "ladder_soft": {
        "summary": "Runs the setup ladder without stopping the flow; failing rungs go to the setup doctor (marker LADDER).",
        "steps": ["Runs every rung (see ladder) and writes the runbook (docs/RUNNING.md or .keel/RUNNING.md).",
                  "Failing rungs become data.failed_rungs, one read-only setup-doctor item for all of them "
                  "(data.doctor_items) and the rung question; marker LADDER = green | failing."],
    },
    "ladder_retry": {
        "summary": "Runs the ladder again (flows written before 0.18.22; init now asks after each doctor round).",
        "steps": ["Counts one more attempt for every failing rung, then runs the ladder again like ladder_soft."],
    },
    "rung_report": {
        "summary": "Builds the rung gate's text from the setup doctor's answer: the failing rungs, the DIAGNOSIS, the "
                   "proposed fix and the commands apply_fix would run.",
        "steps": ["The commands come from the doctor's keel-fix block (data.rung_fix), corrected rung commands from its "
                  "keel-config block (data.rung_config); without either the gate offers no apply_fix.",
                  "The text is data.rung_gate_detail, with what the last applied fix did.",
                  "A fix that already failed, or 3 failed fixes on the rung: no apply_fix, and a plain approve stops "
                  "init (data.rung_gate_order)."],
    },
    "rung_apply": {
        "summary": "Applies your answer for a failing rung: apply_fix (run the proposed commands), own_fix (run your "
                   "commands), retry, or skip (setup.not_checked).",
        "steps": ["apply_fix / own_fix: each command is checked by the setup phase's command rules, then run where init "
                  "says the tools live (a compose service or the dev container); the first failure stops.",
                  "apply_fix / own_fix: the fix and its output are kept (data.rung_fixes_tried) for the doctor's next "
                  "round and the loop check.",
                  "apply_fix: when all ran, the corrected rung commands (the keel-config block, and a fix command that "
                  "is a failing rung's command corrected) are written to commands in .keel/config.yml.",
                  "skip: the rung number is added to setup.not_checked in .keel/config.yml.",
                  "Then the ladder runs again; a rung that still fails goes back to the setup doctor."],
    },
    "init_summary": {
        "summary": "Writes the hand-over's text: rungs passed / not checked, knowledge sections written or kept, what "
                   "was not checked (behaviour, a fast init's stack), and the flows to start next; and the notification's "
                   "short lines (setup checks passed / skipped, the knowledge sections written).",
    },
    # ---------------------------------------------------------------- review-pr (ticket_actions.py)
    "ticket_prep": {
        "summary": "Works out what to review (a PR or a branch, and the ticket) and plans the reviewer jobs; no model.",
        "steps": [
            "The PR (a number, #12, PR 12, its URL) or `branch: <name>` from the request's lines or data pr / branch / "
            "base; the rest of the request (or data.ticket) is the ticket.",
            "Before the run exists (the start): no PR or branch named, a PR keel cannot fetch, a closed or draft PR "
            "(data force: true reviews it anyway) or a missing branch refuses the start, so no run is left behind.",
            "A PR: gh pr view for its state, then git fetch of its head and base into refs/keel/review-pr/<n>, with "
            "keel's GitHub login (Connections › GitHub) for its own host only (github.com or the Enterprise host). "
            "When keel cannot fetch, refs fetched on the host (refs/keel/pr-<n>, refs/pull/<n>/head, origin/<head>) "
            "are reviewed instead, and the note says so.",
            "Stops on an empty diff, only lockfiles, generated or built files, or only docs.",
            "The checklist: the ticket's Acceptance criteria / Definition of done bullets (T1..) and .keel/dod.md "
            "(D1..; keel's starter list without one). Script checks on the added lines decide secrets, disabled tests, "
            "debug output and \"no database change\" (AUTO).",
            "Files ranked by risk, each with a like: neighbour at the base; one job for a small change (400 lines or "
            "less), else ticket, team, bugs and quality jobs (data.ticket_jobs).",
        ],
    },
    "ticket_build": {
        "summary": "Compiles and lints the reviewed code in a throwaway git worktree; only messages on changed files count.",
        "steps": [
            "git worktree add --detach of the reviewed ref outside the project (node_modules and vendor linked in).",
            "commands.api_compile and web_typecheck (only where the change touches), then the check tools without the "
            "fixers. On the fake model only the tools in .keel/config.yml run, no compile command.",
            "The worktree is removed afterwards. A failure that names no changed file is noted, never blamed on the change.",
        ],
    },
    "ticket_claims": {
        "summary": "Reads the reviewers' result lines and makes one check job per not-met item and blocking finding.",
        "steps": ["Lines T1/D1 | verdict | where | what | to do, F | severity | ..., Q | ..., S | ...; an item judged "
                  "twice keeps the met verdict first.", "Each claim to check becomes data.ticket_checks."],
    },
    "ticket_record": {
        "summary": "Applies the checks, decides the verdict and writes the report shown at the share gate.",
        "steps": ["A rejected finding is dropped; a rejected not-met item becomes unclear. An item no reviewer judged "
                  "becomes unclear and is named in the note.",
                  "Verdict: not done on a not-met item, a blocking bug or a compile error in a changed file; unclear "
                  "items make it need a check; quality and lint never change it.",
                  "The report puts the next steps last; the share gate asks which sections the author's text has."],
    },
    "ticket_share": {
        "summary": "Writes the text for the author from the sections you ticked; shown at the post gate, nothing posted.",
        "steps": ["The sections ticked at the share gate (payload.answers.sections; none ticked: summary, not met and "
                  "blocking), each as a short list with where and what to do.", "Nothing is sent: the post gate shows the text."],
    },
    "ticket_comment": {
        "summary": "Posts the text as one comment on the PR (gh pr comment), only after you chose post.",
        "steps": ["Never in run mode auto, never on the fake model, never for a branch without a PR; then the text is "
                  "only shown to copy."],
    },
    # ---------------------------------------------------------------- clarify (runtime/clarify_flow.py)
    "clarify_start": {
        "summary": "Asks the clarify-loop's first question: what kind of task is this (six types).",
        "steps": ["The type the read step named (TASK-TYPE) is first and recommended; the others follow in the skill's "
                  "order: New feature, Bug fix, Performance, Refactor, API integration, Architecture.",
                  "The question is a clarify form at the next gate; its answer goes to the next step."],
    },
    "clarify_type": {
        "summary": "Takes the task type and asks which sections to run (several may be ticked).",
        "steps": ["No answer (or a run mode's): the recommended type.",
                  "The six sections of the clarify-loop; the ones the read step named (SECTIONS) are ticked, all six "
                  "without that line."],
    },
    "clarify_sections": {
        "summary": "Takes the sections and writes the brief for the rounds of questions.",
        "steps": ["Shows \"Got it. Running:\" with each section ticked or not.",
                  "Requirement questions: the task type's questions; the identity probe when the task names users, "
                  "login, accounts, roles or permissions. Red-flag check: the red flags.",
                  "Neither section chosen: the rounds are skipped, the outputs come next."],
    },
    "clarify_doc": {
        "summary": "Writes the clarify document (<specs>/clarify/<date>-<slug>.md) and the result gate's choices.",
        "steps": ["From the assemble step's answer: numbered criteria (AC-1 [API] Given, when, then), the Definition of "
                  "Done, the red-flag check, the next step and the ready-to-send prompt, for the sections chosen.",
                  "The hand-over seed: the request with the bug report or prompt and the criteria as AC lines; feature "
                  "gets the criteria themselves too.",
                  "The result gate offers the keel flow that fits the task type first (feature, fix, change, small or "
                  "spike), then copy the prompt only, send back with a note, stop. Nothing is committed."],
    },
    "clarify_prompt": {
        "summary": "Shows the ready-to-send prompt to copy; the flow ends without starting another one.",
        "steps": ["The prompt from the clarify document is the step's output; no child flow starts."],
    },
}


# v0.16.0 which MCP level may run an action outside a flow (keel_run_action; mcp_server.py): read only looks, write
# runs checks and reports, admin changes the project's settings. Everything else needs a flow's criteria and state:
# "flow-only" (also the default for an action not named here; tests/test_explain.py keeps the list current).
ACTION_LEVELS: dict[str, str] = {
    **{n: "read" for n in ("audit", "trace", "trace_strict", "arch", "arch_detect", "coverage_report", "lint_scope",
                           "lint_report", "review_lenses")},
    **{n: "write" for n in ("verify_release", "verify_coverage", "verify_fast", "verify_deps", "verify_lint", "lint_run",
                            "knowledge_check", "memory_check", "push_check", "pr")},
    **{n: "admin" for n in ("arch_set", "open_pr")},
}


def action_level(name: str) -> str:
    return ACTION_LEVELS.get(name, "flow-only")


def function_for(name: str):
    """The Python function behind an action name (None for the compiler's own and for run:)."""
    from . import actions
    if name in actions.VERDICT_ACTIONS:
        return actions.VERDICT_ACTIONS[name]
    from . import feature_actions, flow_actions
    for table in (flow_actions.ACTIONS, feature_actions.ACTIONS, actions._flow_actions()):
        if name in table:
            return table[name]
    return getattr(actions, "knowledge_check" if name == "memory_check" else name, None) if re.fullmatch(r"\w+", name) else None


def docstring(name: str) -> str:
    """The function's docstring, unwrapped into one paragraph per blank-line block ("" when it has none)."""
    fn = function_for(name)
    doc = inspect.getdoc(fn) if fn else None
    if fn and not doc:
        # Many actions are thin async wrappers: the docstring sits on the sync function they hand to a thread.
        mod = inspect.getmodule(fn)
        try:
            m = re.fullmatch(r"(?s)async def \w+\(\w+[^)]*\)[^:]*:\s*return await asyncio\.to_thread\((\w+), \w+\)\s*",
                             inspect.getsource(fn).strip() + "\n")
        except (OSError, TypeError):
            m = None
        inner = getattr(mod, m.group(1), None) if mod and m else None
        doc = inspect.getdoc(inner) if inner else None
    return "\n\n".join(" ".join(p.split()) for p in (doc or "").split("\n\n") if p.strip())


def dispatch_names() -> list[str]:
    """Every action name the engine dispatches: actions.run_action's own names, the action tables, the compiler's."""
    from . import actions, feature_actions, flow_actions
    src = inspect.getsource(actions.run_action)
    own = re.findall(r'action == "([\w:]+)"', src) + [n for grp in re.findall(r"action in \(([^)]*)\)", src)
                                                      for n in re.findall(r'"([\w:]+)"', grp)]
    if 'startswith("run:")' in src:
        own.append("run:")
    names = [*own, *actions.VERDICT_ACTIONS, *flow_actions.ACTIONS, *feature_actions.ACTIONS, *actions._flow_actions(),
             *COMPILER_ACTIONS, *extensions.action_names()]
    return list(dict.fromkeys(names))


def all_docs() -> dict[str, dict]:
    """keel's own entries and the parts' (their `docs`)."""
    return {**DOCS, **extensions.docs()}


def describe(name: str) -> dict:
    """{name, summary, steps, known} for one action of a code step (`run:<cmd>` keeps its command)."""
    key = "run:" if name.startswith("run:") else name
    entry = DOCS.get(key) or extensions.docs().get(key)
    if not entry:
        return {"name": name, "known": False, "summary": f"Unknown action {name}: the step fails with \"Unknown action\".",
                "steps": []}
    steps = list(entry.get("steps") or [])
    if not steps:
        doc = docstring(key)
        steps = [doc] if doc else []
    out = {"name": name, "known": True, "summary": entry["summary"], "steps": steps}
    if key == "run:":
        out["command"] = name[4:].strip()
    return out
