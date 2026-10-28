# keel v2 guide

The [README](../README.md) is the short version. This page has the details.

- [How a flow works](#how-a-flow-works)
- [Flows in detail](#flows-in-detail)
- [Web research](#web-research)
- [Rules](#rules)
- [KeelBot](#keelbot)
- [keel TUI (keel2 tui)](#keel-tui-keel2-tui)
- [Thinking effort](#thinking-effort)
- [Plugins and the Marketplace](#plugins-and-the-marketplace)
- [Stacks](#stacks)
- [Plugins: Database, Git and CI/CD](#plugins-database-git-and-cicd)
- [Quality](#quality)
- [Inbox and run modes](#inbox-and-run-modes)
- [Usage and caps](#usage-and-caps)
- [Tasks and Jira](#tasks-and-jira)
- [Logins](#logins)
- [Signing in](#signing-in)
- [Use keel from Claude Code or Claude Desktop (MCP)](#use-keel-from-claude-code-or-claude-desktop-mcp)
- [keel2 commands](#keel2-commands)
- [Where keel keeps its files (layouts)](#where-keel-keeps-its-files-layouts)
- [Native mode (no Docker)](#native-mode-no-docker)
- [Docker mode](#docker-mode)
- [Several project folders](#several-project-folders)
- [Git worktree projects](#git-worktree-projects)
- [Problems](#problems)
- [Folders in the container](#folders-in-the-container)
- [Without the install script](#without-the-install-script)
- [Develop keel v2](#develop-keel-v2)

## How a flow works

```
 you: "show each player's rank"
   │
   ▼
 spec ─◆─ red ─ green ─◆─ … (one loop per criterion) … ─ review ─◆─ done
        │     │       │                                          │
        │     │       └ you approve each criterion               └ you approve the end
        │     └ an agent writes the smallest code that passes; keel commits it
        └ you approve the criteria; an agent writes a failing test, keel checks it fails, then commits it
```

- The flow is a [LangGraph](https://github.com/langchain-ai/langgraph) graph; the agents are LangChain or CLI agents.
- keel runs the tests and makes the commits itself, on its own branch (`feat/…`). It never pushes, and it never
  commits files you changed yourself.
- keel's commits are by you, and end with `Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>`, so GitHub shows both.
  Settings › Git: **KeelBot as co-author** (on by default) and **Commit author** (`Name <email>`; empty: the
  project's git name — in Docker there is none, so set it there, or the commits are by KeelBot alone).
- Every step, file change, command and token is visible: **Live agents**, **Jobs**, **Budget**. A code step (the
  setup ladder, a test suite, a commit) is a job too: agent "keel", no model, no tokens. The Flow page shows the line
  it is at ("rung 4/12 · Both apps compile: $ npm run build"), then "passed" or "failed (exit 1)".
- **The Flow page** (0.18.11): each block shows what really ran (model and effort, time, cost, the first line of its
  answer, ×N tries). Phases fold to one line with a summary. The criterion loop has one row per criterion with its
  tries and why you sent it back. **Needs you** is pinned on top. The canvas follows the live step until you scroll
  (**Jump to live** brings it back). A gate card says what you approve, the cost so far, the proof, and what happens
  after **Approve** and after **Send back**; in the card **A** approves and **R** opens the reason box.
- **Choices are cards** (0.18.13): every gate with named choices shows one card per choice, with what it does, which
  one a plain approve takes, and whether it needs a reason. Keys 1–9 pick. A question can take several answers
  (tick each that applies).
- **A stuck criterion** gets a ladder: the same agent with the exact failure → a different agent (investigator) → a
  stronger model once → you, with real choices (retry with a note, skip, edit the spec, stop). After 5 send-backs or
  retries in a row keel asks "go on anyway / stop". A test command that cannot start is said plainly. "No tests found"
  is never green.
- **The spec's build order**: when a spec says `**ORDER**: AC-6, AC-2, …`, keel builds the criteria in that order. An
  amendment that changed nothing never loops: you get **build another criterion first**, **skip this one for now**,
  **edit the spec** or **stop**.
- **Several flows at once**: while a flow runs in the project folder, the next one runs in a worktree of its own
  (a copy of the project on its own branch, under `.keel/worktrees/`), so neither touches the other's files. Press
  **Start** on three tasks, or **Start another flow** on the Flow page. The Flow page then shows a board: each flow,
  the files two flows both change, the branches that will not merge cleanly, and an order to merge them in. The
  Inbox groups their gates by flow. On top of the Flow page, a tab for each flow that runs or waits switches between
  them, and **History** lists every flow of the project (all, or one workflow's); **Open** shows any of them with all
  its details.
- **A KeelBot chat in the project folder** (0.18.57): a flow on its own branch checks that branch out in the project
  folder. When a KeelBot Ask or Edit chat read the folder in the last 30 minutes, **Start a flow** says so and runs
  the new flow in its own copy (a worktree) instead — unless the project's tests cannot run in a worktree; then it
  says why. Pick **The project folder** to run there anyway: the chat then shows a banner ("a flow switched this
  folder to `feat/…` at 14:02") and KeelBot's next answer says the branch changed.
- When something needs you, keel plays a sound, shows a pop-up and its mascot jumps.
- keel v2 has its own guard, rules, agents, skills and MCP server. [keel v1](https://github.com/MiladNalbandi/keel) is a
  separate project and is not in the image.

## Flows in detail

| Flow | What it does |
|---|---|
| **feature** | web check (and web docs when needed) → spec (with questions) ◆ → plan → contract ◆ → per criterion: failing test, code, review ◆ → integration, security, full review, e2e, smoke → ship |
| **change** | small change without a spec: trivial (one commit) or 1–3 criteria; too big → hands over to feature |
| **quick** | one agent makes the change and a test → keel runs the tests (2 fix rounds) → your one look ◆ → one commit; no spec, no ship |
| **address** | a reviewer's comment ("Address it") or a change to test code only: one agent changes the code and its tests → keel runs the tests (2 fix rounds) → a check against the reviewer's thread → your one look ◆ (the thread next to the diff) → one commit; no test-first loop |
| **implement** | the usual order, not test-first: the code → its tests → keel runs them; pass → one commit, done; fail → a fix and the tests again (2 rounds); no gate |
| **small** | a short plan with 1–3 criteria ◆ → per criterion a failing test and the code → one review (its blocking findings fixed, 2 rounds) → your one look ◆; no ship |
| **fix** | reproduce the bug as a failing test ◆ → find the cause (parallel investigators) ◆ → fix → regression test → ship |
| **diagnose** | a bug you cannot reproduce yet: 3–4 guesses, one investigator each → hand to fix or feature, or write a note ◆ |
| **spike** | research before building: frame → web docs → one researcher per angle → findings with one recommendation ◆ → a feature flow, or drop |
| **clarify** | ask before you code (the clarify-loop of your ai-coding-toolkit): task type ◆ → sections ◆ → rounds of questions from the code ◆ → a document in keel's spec format with criteria, a definition of done and a prompt ◆ → the flow that fits, or the prompt only |
| **review** | read-only review of the branch, one reviewer per lens; the report word for word ◆ |
| **review-pr** | someone else's PR or branch against a ticket and your definition of done: build and lint in a throwaway worktree, each checklist item met / not met with evidence, bugs checked twice; you pick what the author sees ◆, nothing is posted unless you choose post |
| **lint** | run the project's formatters and linters (from its stacks and `.keel/config.yml`) → fix the findings without changing behaviour (2 rounds) → `chore(lint)` commit → report ◆ |
| **cover** | measure coverage of the changed lines → per gap: test / delete / accept with a reason ◆ |
| **ship** | which steps run ◆ → verify, static checks, release tests, coverage, deps, audit, trace, lens reviewers, spec walk → final review ◆ → knowledge → PR body ◆ (never pushes) |
| **hunt** · **hunt-next** | read-only bug hunt: hunters per lens, provers reproduce each candidate, a report; the top bug goes to fix (or feature) |
| **init** · **knowledge-refresh** | set a project up (architecture, setup checks, knowledge) · refresh `docs/knowledge/` |
| **plugin** | spec ◆ → `keel-plugin new` → each criterion test first ◆ → `keel-plugin lint` and the plugin's tests → review ◆ |

◆ = keel stops and waits for you. The flows are YAML files in `content/workflows/`; the **Workflows** page edits them.
A workflow can sit in a **Folder** (type a name above the blocks; the list groups by folder), and the page shows how
often it ran and opens its last run. A workflow does not need agents: a few code steps (`run: npm test`) and a gate
automate a task without any model. KeelBot writes one for you when you ask.

**Start a flow › How big is it?** lists quick, implement, small, change and feature with their steps and estimate, and
marks the one that fits your words (or fix for a bug). You pick. In keel TUI: `/flow [workflow] [idea…]`.

Each flow below has a short picture and what it asks you.

### feature

```
 web_check ─ WEB? ─yes─ web_docs ─┐
            └─no──────────────────┴─ spec ─ explore ─ plan ─◆ spec gate ─◆ options
   ─ contract ─◆─ per criterion: red ─ green ─ the tests outside it ─ AC review ─◆
   ─ integration ◆ ─ security ─ full review ─ e2e ◆ ─ smoke ◆ ─ ship ─ ADR ─ done
```

- **The spec's layout** (every flow that writes a spec: feature, clarify, a spike or a fix handing over): it starts
  with six sections you can read first. **In plain words** (a short story for someone who does not know the code),
  **For engineers** (the change per layer, the design choices, compatibility, how it fails), **What will change**
  (one line per file), **Payload changes** and **Database changes** (always there; "none" with the reason when
  nothing changes; else before and after, the migration with its rollback and deploy order), **Architecture** (an
  ASCII request path, each box new, changed or unchanged). Then the usual sections, Goal to Definition of done.
- **web_check**: does the spec need facts from outside the repository (a vendor API, a webhook, a library, a
  standard)? Only on yes does **web_docs** run (see [Web research](#web-research)).
- **The interview** (its own step, before the spec): the explorer asks you questions when the request leaves something open. Several
  answers can be ticked; the recommended ones come first. If your answer is a question ("what do you mean", "?",
  "explain"), keel does not take it as the answer and does not pick the default for you: it asks that question again
  once, in plain words, with what it decides and each option as an example (0.18.28). Still not clear: keel writes it
  down as an open question.
  <!-- 0.18.21: the interview with lens questions -->
- **Spec gate**: **approve**, **edit** (a note on what to change), **rewrite** (one section), **review** (a reviewer
  reads the spec first), **order** (plan the build order again), **reject** (the interview starts again).
- **Options**: which optional phases run (integration gate, security, e2e, smoke).
- **Contract gate** (only when the plan changes the API contract): approve, change, or amend the spec.
- **AC gate** per criterion. An agent may say the spec needs an amendment: you approve it, or change it.
- **The tests outside a criterion** (0.18.28): a criterion's own check runs only its own tests. After its green,
  keel also runs the tests outside it, so a test that breaks other tests (a row it leaves in the shared database) is
  found at that criterion, not at integration. Set it in `.keel/config.yml`:

  ```yaml
  checks:
    after_green: changed   # none | changed (default) | suite
    suite_every: 3         # with changed: the whole suite every 3 criteria, and at the end of each lane
  ```

  `changed` runs the branch's test files (`commands.api_test_paths` / `web_test_paths` with `{PATHS}`, else the
  runner with the paths for pytest, vitest, jest and mocha, else the whole suite). A failure says "AC-8's change
  broke tests outside it: …" with the failing tests and the output, and goes back to that criterion's green; its own
  test files may change there (the fix is often the test's cleanup). The AC gate shows what ran.
- **"Already passes"**: a criterion whose test passes before any code. When the run mode approves it, the AC
  reviewer still reads the test (isolation, cleanup, assertions). When you approve it, keel does not stop again for
  it. Either way the tests outside it run, and the full-diff review lists it as "not reviewed per AC" and reads its
  test.
- **Integration**: the whole suite runs. When it fails, the gate shows the failing tests and their messages (a run
  that printed nothing runs again with a JUnit report). keel runs each failing test alone: one that passes alone was
  broken by another test's leftovers. It lists the branch's tests that write to the database without a rollback or
  cleanup, with their criterion. Then you choose (the recommended one first):

  ```
   suite fails ─▶ failing tests alone? ─ pass ─▶ pollution: whose test writes and never cleans up?
        │                                                │
        ▼                                                ▼
   ◆ send_back_ac   AC-8 opens again at green with the failure and your note (its test may change)
   ◆ keelbot_fix    KeelBot fixes it in an Edit chat (it may change tests), then keel checks again
   ◆ retry          check again, after you fixed it yourself
   ◆ stop
  ```

  A plain send-back with a reason takes the first choice with your reason; it never just stops the flow.
- **Security**: a security auditor reads the diff (plus a dependency triager when `verify deps` failed).
  <!-- 0.18.21: per-finding security decisions -->
- **e2e** and **smoke** run for criteria tagged `[E2E]` or `[SMOKE]`. No e2e command: keel asks whether to write the
  specs without running them.
- Then the **ship** part (below). <!-- 0.18.21: the ready-to-ship stop -->

#### The spec

The spec has numbered criteria (`AC-1 [API] …`), a UI mockup with four states (default, empty, loading, error) for each
`[WEB]` criterion, a request path (each hop marked new or changed) for each `[API]` one, a Migration section for a
schema change, Validation and security rules, Out of scope, Smoke checks and Open questions. A journey that needs a
browser is tagged `[E2E]`; a check that must also pass after a deploy, `[SMOKE]`.

<!-- 0.18.21: the spec layout: Goal, User stories, Interview answers, Findings, ACs with [API]/[WEB]/[E2E]/[SMOKE], Definition of done, and so on -->

### change

```
 triage ─◆ scope: trivial ─ change ─ commit ─ ship
                │ small ── per criterion: red ─ green ─◆ ── ship
                └ feature ─► starts a feature flow with the criteria
```

- **Scope gate**: trivial, small or feature (the triage's choice is recommended). Trivial means no test could notice
  a difference. A change that rests on outside docs the repository does not hold is a feature, so its flow does the
  web research.

### quick

```
 preflight ─ implement (change + a test) ─ run the tests (2 fix rounds) ─ diff ─◆ look ─ commit ─ done
```

- **Look**: you see the diff once; approve, or send it back with a reason.

### address

```
 preflight ─ change (code and tests) ─ run the tests (2 fix rounds) ─ diff ─ intent check ─ thread view ─◆ look ─ commit ─ done
                       ▲                                                              │ missed (1 round)
                       └──────────────────────────────────────────────────────────────┘
```

- The request is the review thread as it is (the reviewer's words, every reply, your own reply, file:line and the
  code), marked as data. The agent builds what the reviewer asked for (the structure or behaviour the thread names, not
  a smaller stand-in) where the project keeps code of that kind.
- **Intent check**: a reviewer agent compares the diff with the thread. "Does not do what the reviewer asked" goes back
  to the agent once with the reason; after that it is yours to decide. The look shows the thread next to the diff, and
  a miss first.
- No reply of yours in the thread: Address it asks "What should the fix be?" first (the reviewer's words above it).
- What "Address it" on a review thread starts (Settings › Code Review › Address it picks another flow), and what keel
  suggests when every file a request names is a test: there is no new behaviour for a failing test to describe.
- A change with no behaviour change (an extract into a helper, a rename) adds no test: the tests passing is the proof.

### implement

```
 preflight ─ implement (code) ─ tests ─ run them ─ pass ─ commit ─ done
                                            └ fail ─ fix ─ run again (2 rounds)
```

- No gate. keel asks only when the tests still fail after the rounds.

### small

```
 preflight ─ plan (1–3 criteria) ─◆ ─ per criterion: red ─ green ─ review (2 fix rounds) ─ diff ─◆ look ─ done
```

- **Plan gate**: approve the 1–3 criteria, or send the plan back. Criteria you write in the request are used as they are.
- **Look**: one look at the whole diff.

### fix

```
 bug report ─ reproduce ─◆ R ─ hypotheses ─ investigators ─ plan ─◆ F ─ fix ─ regression e2e ─ ship
                                              │ no cause: a stronger model once, then diagnose
```

- **Gate R**: the failing test that shows the bug: **investigate** or **stop** (the test stays on the branch).
- **Gate F**: the cause and the fix plan: **fix**, or **feature** (it was a missing requirement).

### diagnose

```
 frame (3–4 guesses) ─ one investigator per guess ─ rank + recipe ─◆ ─ fix │ feature │ note
```

- **Diagnosis gate**: **fix** (starts a fix flow with the recipe), **feature** (starts a feature flow with the
  evidence), or **unresolved** (keel writes a note).

### spike

```
 frame (2–4 angles) ─ web docs ─ one researcher per angle ─ findings + recommendation ─◆ ─ spec │ drop
```

- **Spike result**: **spec** starts a feature flow from the findings; **drop** ends it. Send it back with a note to
  research again from "frame". The feature flow starts in the spike's run mode at that moment.
- **A critic of your own** (a copy of spike with a critic step): give the critic `markers: [GAPS]` and a branch after
  it, `when: {marker: GAPS, lte: 0, step: critic}, no: research, rounds: 3`. While gaps remain and rounds are left
  the branch loops back, also when the critic is a reviewer agent (0.18.28: a reviewer used to stop at its findings
  gate). A `when` compares numbers with `gt`, `gte`, `lt`, `lte`; a key keel does not know is an error.

### clarify

The clarify-loop skill from your own ai-coding-toolkit plugin, as a keel flow: its questions, words and templates.
The rule: never start any coding task without answering these questions first.

```
 read the request + code ─◆ task type ─◆ sections ─┬─ rounds of questions ─◆ answers ─┐ (until NO_QUESTIONS, 4 at most)
                                                   └────────── no section asks ────────┴─ assemble ─ document
   ─ spec check (sent back once) ─◆ result ─ feature │ fix │ change │ small │ spike │ prompt only │ send back │ stop
```

- **Task type**: New feature, Bug fix, Performance, Refactor, API integration or Architecture. The one your request
  suggests is first and recommended (the explorer read the request and the code first).
- **Sections** (tick several): 1 requirement questions, 2 acceptance criteria, 3 definition of done, 4 red-flag
  check, 5 ready-to-send prompt, 6 next step. The sensible ones are ticked. 2 and 3 are always in the document.
- **Your answers** (rounds of at most 6 questions): each question has 2 to 4 options from your code (file:line), the
  recommended one first, and you can answer in your own words. What it asks per type:
  - New feature: what to build, which module, the pattern to follow, the stack, the requirements, the constraints.
  - Bug fix: symptom, expected behavior, steps to reproduce, the error, recent changes, what you already tried.
  - Performance: what is slow, the measured latency, the target, stack and volume, profiling data.
  - Refactor: what to refactor, the goal, the public interface that must not change, the pattern, the tests.
  - API integration: which API, what you need from it, the auth, your HTTP client pattern, error handling.
  - Architecture: what you design, the key requirements, stack and constraints, team and timeline, your options.
  - A task that names users, login, accounts, roles or permissions also gets the identity question (A named
    entities, B authentication, C authorization only). Section 4 asks about the red flags that may hold.
- **Clarify result**: the document `<specs>/clarify/<date>-<slug>.md` (`docs/specs` or `.keel/specs`, not
  committed) is a keel spec that passes the spec check: In plain words, For engineers, What will change, Payload
  changes, Database changes, Architecture, then the spec's sections, the numbered criteria (`AC-1 [API] Given …, when …, then …`), the
  definition of done, and the ready-to-send prompt as the last appendix. Hand it over to the flow that fits (feature
  for a new feature or an API integration, fix for a bug, change or small for a refactor, change or spike for
  performance, spike or feature for architecture): the criteria and the answers are its seed. Or copy the prompt
  only (no flow starts), send it back with a note, or stop.
- **Citations** (0.18.64): the spec check (here and in the feature flow) also reads every `path:line`, `path:10-20`
  or `path#L10` the document cites. A file that does not exist, or a line past its end ("src/orders.py:88-95 does
  not exist: the file has 40 lines"), sends the document back once like any missing section. From an MCP client:
  `keel_spec_check(path)` and `keel_citations_check(path or text)`.

### review

```
 scope ─ one reviewer per lens (correctness, security, performance, architecture, assertions) ─ report ─◆
```

- **Review report**: read it; send it back to review again. Nothing is changed.

### review-pr

<!-- 0.18.21: the review-pr flow: what it does, what it asks, and its ASCII diagram -->

### lint

```
 scope ─ run the checks ─ fail ─ fix (no behaviour change) ─ run again (2 rounds) ─ chore(lint) commit ─ report ─◆
```

### cover

```
 measure ─ fails ─◆ per group: test │ delete │ accept ─ write ─ review ─ commit ─ measure again ─ report
```

- **Per group**: **test** (write a test), **delete** (the lines cannot be reached), **accept** (with a reason).
- When coverage cannot be measured: **try again** or **go on**.

### ship

```
 ◆ which steps ─ verify ─ lint ─ release ─ cover ─ deps ─ audit ─ trace ─ lens reviewers ─ spec walk
   ─◆ final review ─ knowledge update ─ PR body ─◆ open the PR (never pushes)
```

- **Which steps run**: optional and deferred steps can be skipped (static checks, release tests, coverage, deps, the
  reviewers).
- **Final review**: every verdict on one page; send it back to fix.
- **Open the PR**: the PR body; keel never pushes.

### hunt and hunt-next

```
 hunt:      start ─◆ lenses ─ deps ─ one hunter per lens ─ provers ─ group ─ report ─ commit ─◆ triage
 hunt-next: take the top group ─ defect ─► fix flow │ unspecified ─► feature flow │ nothing open ─► end
```

- **Lenses**: tick the lenses to hunt with (or stop). In semi mode keel also stops after the candidates and after the
  verdicts.
- **Triage**: **take** the top group (starts hunt-next), **close** a finding (fixed, accepted or won't fix, with a note),
  or **stop**.

### init

```
 discover ─ architecture ─◆ questions ─◆ setup plan ─ write config
   ─ ladder + knowledge ─ setup-doctor ─ ladder again ─◆ a rung keeps failing ─ knowledge check ─ commit
   ─◆ audit now: hunt │ skip ─◆ hand over
```

- **Questions**: where the app runs while you develop, how services run (when there is no compose file), and whether
  keel builds the **project notes** (0.18.57: off unless you tick **Build project notes (≈$4–6, recommended for large
  repos)**, or single sections; the cost comes from your models and the size of the repo). The other defaults are
  pre-selected. See [Project notes](#project-notes-opt-in).
- **Setup plan**: the commands keel will use, before it writes `.keel/config.yml`.
- **A rung keeps failing**: **fix**, **exclude** it, or **accept** it. One setup-doctor looks at all failing rungs and
  first looks for a cause they share; its next round continues the same session.
  <!-- 0.18.21: the init doctor fix -->
- **Audit now**: start a bug hunt, **build the project notes** (starts knowledge-refresh), or skip.

### knowledge-refresh

```
 one librarian per section ─ knowledge check ─ commit
```

- No gate. The check accepts a citation like `.../owner/Pet.java:46`; real problems go back to the writer.
- Starting it is a request for the notes: it turns them on (`knowledge.build: on`).

### Project notes (opt-in)

The project notes are the knowledge base the librarians write (`architecture`, `domain`, `conventions`, `data`,
`integrations` under `.keel/knowledge/` or `docs/knowledge/`). Since 0.18.57 they are **off for a new project**: in a
test with real models, agents that were given the notes rarely opened them and answered no better, while writing
them cost about $4–6 per project.

- **Off** (`knowledge: {build: off}` in `.keel/config.yml`): no flow runs a librarian, agents get no knowledge paths,
  and ship skips its knowledge step (the PR body says "off"). The Wiki and the Map say **Project notes are off —
  Build them** with the expected cost.
- **Build them** (Wiki, or **notes** at the end of init, or the knowledge-refresh flow): five librarians write the
  notes, and they are on from then.
- **On**: agents get the paths of the sections they are given (Agents page), and ship asks to update a section when
  a file it cites changed. **Turn notes off** in the Wiki stops both; the notes stay where they are (**Turn on** gives
  them back).
- A project that already had notes keeps using them until you turn them off.
- The repo's own rules for coding agents (`project-rules.md`, `.keel/project-rules.json`: AGENTS.md, CLAUDE.md,
  `.claude/skills`) are found at init and knowledge-refresh either way: that is cheap and uses no model.

### plugin

```
 spec ─◆ ─ keel-plugin new ─ per criterion: red ─ green ─ review ─◆ ─ keel-plugin lint ─ the plugin's tests ─◆ review
```

## Web research

**web-researcher** (0.18.20) is a read-only agent for outside docs: APIs, libraries, webhooks, standards. It searches
first, then reads the best sources (developer portal, API reference, webhook pages, changelog, OpenAPI, SDKs on
GitHub, Postman, Stack Overflow). It tries at least five sources before it calls a fact unconfirmed. Its answer has
the confirmed facts (URL + quote), every URL it tried, what a person must supply, and `CONFIDENCE`.

```
 spike:    frame ─ web_docs ─ researchers per angle (they cite its URLs) ─ findings (confirmed / not confirmed, sources)
 feature:  web_check ─ WEB: yes ─ web_docs ─ interview ─ spec      (WEB: no ─ interview ─ spec)
```

- **spike**: the web step always runs. The findings list their sources and say what is confirmed and what is not.
- **feature** (0.18.21): `web_check` decides first. WEB is yes when the request or the code it touches depends on a
  third-party API, webhook, library or standard whose details the repository does not hold. It is no for work inside
  the code (a refactor, a page) and for a request that already carries web research (a spike's findings). On yes,
  `web_docs` runs; the interview asks only what it could not confirm, and the spec cites what is confirmed. An
  unconfirmed detail goes under **Unverified and open questions** with what a person must supply (a sample payload, a
  sandbox account, a docs page), never an invented field name.
- **change**: a change whose criteria rest on outside docs is triaged as a feature, so the feature flow does the
  research.
- **Web tools**: an agent whose `tools:` line names WebSearch or WebFetch gets them (Claude Code, opencode). On Codex,
  Copilot and API keys it reads the web with `curl -sL`. Other agents get no web tools.
- A read-only run can read the web (`curl -sL`, `wget -qO-`) but not download files: `curl -o`, `curl -O` and
  `wget <url>` are refused.

## Rules

keel checks every agent with rules. **Build › Rules** shows all of them in plain words.

```
 an agent wants to act ──► keel's hook checks it ──► allowed ──► it runs
                              │                                     │
                              └─► refused: the agent reads why      ▼
                                                        after the step: keel compares the files
                                                        and puts back what the phase does not allow
```

- **Always on**: `.env` files, keel's own rule files (`.keel/config.yml`, `.keel/rules/`), old migrations, force push,
  `--no-verify`, commits and history rewrites inside a flow, secrets and switched-off tests in an edit, and keel's
  own sign-in keys. These never switch off.
- **You can switch off**: generated files, build output, `-x test`, new packages, reading a file twice, big-file
  reads, the push gate, MCP tools that change things. Off is saved in `.keel/config.yml` (`rules.off`).
- **Your lists** (`.keel/config.yml`): protected files, generated files, build output, shell commands, `mcp.allow`,
  the big-file limit (`read_guard_max_lines`, default 2000 lines; 0 = off), the push gate, architecture boundaries.
  The page edits them in place; only the `guards`, `mcp`, `rules` and `boundaries` parts are rewritten.
- **Rule packs**: your own rules, one YAML file each in `.keel/rules/`, shared with the team through git. **Library**
  installs one (strict secrets, no new dependencies, migrations only in a flow, frontend can't touch the API, Spring,
  Node, Python); **New rule** writes one with a form or as YAML; **Check and try it** says which of the project's recent
  tool calls it would have refused. Export and Import move a pack between projects. The Marketplace has rule packs too.

```yaml
schema: 1
name: no-deploys          # the id and the file name: .keel/rules/no-deploys.yaml
title: No deploys
version: 1.0.0
rules:
  - id: no-kubectl
    kind: bash            # bash | edit | read | mcp
    match: [kubectl apply]
    phases: all           # all | in-flow | none | [green, refactor]
    reason: Deploys are a person's job.
```

The whole format (publisher, summary, `requires`, every field of a rule) is in
[docs/rules/pack-format.md](rules/pack-format.md). A pack file is shaped like a plugin's manifest, so the same file can
later come from the Marketplace; the Rules page shows where each pack came from (keel's library, your own, the
marketplace) and its version.

**Which engine checks what, when.** Each rule on the page shows one sign per engine:

| Sign | Means |
|---|---|
| ✓ | checked **before** the action: it never happens |
| ◐ | checked before for git, gh, the package managers and file printers only |
| ↺ | checked **after the step**: keel puts the file back |
| ✗ | not checked by this engine |

Claude, OpenCode, API-key models and Codex 0.150+ are checked before every action. Copilot has no hook keel can load:
its shell commands go through keel's command wrappers, and its file edits are only checked after each step. The model
picker says so when you choose Copilot or Codex.

**Rules now** on the Flow page says, for the phase that runs: what agents may change (✓), what they may not (✗), what
never (🔒), the files you allowed, your rule packs, which rules stopped something (how often, before the action or after
the step), how well the running engine is checked, and the last command an agent saw fail.

**When keel cannot read the rules** (no guard context, or an error in the hook) it fails closed: only reads pass, and
`.env` files and build output stay refused. Everything else waits until it is fixed.

## KeelBot

Open the Code page and press **⌘I** (or the KeelBot button at the bottom of the left bar), or open **KeelBot** in the
menu. In the terminal, KeelBot is the main view of [keel TUI](#keel-tui-keel2-tui). Ask about the project in plain
words. KeelBot reads the code, the map and the project notes (when they are on), and every answer links to the lines
(`file:line` opens the editor there). Answers stream in as they are written (Claude CLI, API keys; Codex, OpenCode and
Copilot send the answer when it is done).

KeelBot also knows keel itself: the workflows this project can run and its flows, the step a flow is at and its
newest jobs. It gives you buttons; nothing starts, stops or is saved until you press one.

- **Which workflow?** Ask "how do I add a weekly report page?": KeelBot names the workflow that fits and why, and
  gives a **Start the flow** button with a title (you can change it) and the request every agent gets.
- **About a running flow**: "why does Euro prices wait?", "how far is the fix?", "what did it cost?", "what did the
  doctor find?".
- **Act on flows** (you press): **stop flows**, **approve or send back a gate**, **change a flow's run mode**, or
  **answer a flow's question** (KeelBot proposes the answer as a button).
- **A workflow, rule pack, agent, skill or stack of your own**: "make a workflow that runs the linters and the tests,
  then stops for a look". KeelBot writes it with only the steps the task needs; keel checks it on the spot and shows
  what it runs. **Save** it, or **Ask KeelBot to fix it** when the check finds a problem.
- **It asks you to choose** with a question card, never as a prose "A or B?".
- **It reads a gate's choices first** (0.18.28): KeelBot is told what each choice of the waiting gate really does,
  whether the reason is used, and what a plain approve or send-back does there. It never says "send it back with
  this reason" where a send-back only stops the flow. Its suggestions name the gate by its step ("verify the suite
  + commit"), not by the flow's title.
- **Type while it answers**: your message waits, shown dim ("will send after this answer"), and goes when the answer
  ends. **Esc** stops the answer and sends your message now (in keel TUI also **Ctrl+C** while a message waits).
- **A start button with a run mode**: ask "start it in important mode" and the button carries it.
- **The code graph tools are off for KeelBot** (0.18.57): they added about 2.3k tokens to every call and the answers
  were no better. The graph index and the Graph page stay; turn the tools on in **Agents › KeelBot (helper) › Code
  graph tools**. **Where to look** (the same drawer) gives KeelBot keel's own lookups in the graph at no tool call;
  it is off by default too (before 0.18.57 the switch did not reach KeelBot).
- **The folder's branch** (0.18.57): an Ask or Edit chat knows which branch the project folder is on. When a flow
  switches it (a flow on its own branch, started in the project folder), the chat shows a banner ("a flow switched
  this folder to `feat/euro-prices` at 14:02") and KeelBot's next answer starts by saying so. A side session has its
  own copy and branch.

- **More room**: hide keel's menu with ‹ next to the bell (or ⌘\; ☰ brings it back); drag KeelBot's left edge to make it wider; **Focus** (in
  the status bar at the bottom, or ⇧⌘\) hides everything but the code; ⤢ in the panel (or **KeelBot** in the menu)
  opens KeelBot alone on its own page, where a `file:line` link opens the Code page at that line. The status bar also
  shows the code graph index and **↻ Rebuild**.
- **Point at things**: select lines and press **Ask** (or ⌘I); type `@` for a file, a symbol or a criterion of the
  running flow; the open file always goes along.
- **Commands**: type `/` — `/explain`, `/where`, `/review`, `/plan`, `/gate` (what the waiting gate asks), `/test`. A
  project can add its own in `.keel/plugins/<name>/plugin.yml` (see `plugins/keelbot/content/plugins/core/plugin.yml`).

**Four modes.** The mode decides what KeelBot may change. Changing the mode keeps the chat, and KeelBot is told what
changed.

| Mode | What KeelBot may do |
|---|---|
| **Ask** (default) | read only: no edit, no new file, no command that changes files or git, whatever the model. Asked to change something, it says Ask is read only and offers Edit (recommended), Side, or the exact command to run yourself |
| **Edit** (0.18.7) | change files and run commands in the project folder itself, with no flow |
| **Fix** | only while a flow waits at a gate: change things inside the rules of the flow's phase |
| **Side** | work in KeelBot's own copy of the project (a git worktree on its own branch); nothing touches your folder or a running flow |

- In every mode keel's always-on rules hold (no secrets, no `.git`, no applied migration), and KeelBot never commits
  or pushes. **Keep** runs the checks and makes keel's commit of only KeelBot's files; **Undo** puts a file back. The
  panel lists every changed file with its diff.
- In Fix mode **Done** runs the tests and lets keel commit only those files; if the tests fail, one click hands the
  failure back to KeelBot. In Side mode, after **Keep**: **Make a task**, **Start a flow on the branch** (a change flow
  writes the tests for it), or **Throw away**.
- **Permission cards**: a command that changes something (`git checkout`, a build, an install) waits for your OK in
  the panel, keel TUI or the Inbox: **Allow once**, **Allow `git checkout *` in this chat** (the command's family),
  **Deny**, or **Deny, and say why** (KeelBot goes on from your reason). Commands that throw work away
  (`git reset --hard`, `git checkout -- .`, `git restore`, `git clean`, `git stash drop`, `git branch -D`, `rm -r`) always
  ask, with a warning; "in this chat" never covers them. When a flow works in the folder, the card says that
  switching branches changes its files. Esc on a card says no.
- **The failure rule** (0.18.18): KeelBot never tries a failed command again unchanged. It says in one line why it
  failed (it quotes the error) and offers the ways forward as a card, the safe one first. Example: "check out main"
  with uncommitted work → stash, check out main, pull, stash pop (recommended) · commit them first · discard them ·
  leave it · your own words. keel refuses an identical retry while nothing in the folder changed, also in the next
  turn. Every KeelBot button that fails offers **Ask KeelBot to fix it**.
- **Chats**: a chat lives in keel's backend, so it goes on in keel TUI, the dashboard or on another computer. Chats name
  themselves after the first answer (3–6 words); a name you give is kept. When a chat's context is above 85% before a
  turn, keel compacts it by itself: a summary of the chat in a fresh session (every message stays stored).
- **Costs**: each answer shows its tokens and time; the budget bar counts them. Claude and Codex continue their own
  session, so a follow-up question is cheap (the context stays cached).
- The model is the `helper` agent's (Agents page); change it, and its thinking effort, for one chat in the panel.

## keel TUI (keel2 tui)

keel TUI is keel in your terminal, the way Claude Code is. Run `keel2` (or `keel2 tui`) while keel runs. It starts on
the project of the folder you are in, and on the KeelBot chat the dashboard has open. Switch the project or the chat
in one, and the other follows. To keep a window on its own project and chat while you work on two at once, turn
**Follow other keel windows** off: `/follow off` (or `/settings`) in keel TUI, Settings › General › This browser in the
dashboard. That window then follows no other and moves no other. What you do in keel TUI shows in the dashboard as
"via keel TUI".

```
 keel v2 0.18.21 · ✓ claude · today 120k $0.84 · month 2.1M $14.20      ← keel itself: version, logins, spend
 ─────────────────────────────────────────────────────────────────
 KeelBot's answers, flow events and command output scroll here
 ─────────────────────────────────────────────────────────────────
 › Ask KeelBot, or / for commands
 [?] ask · shop · main · Refunds · ◆ 1 waiting here · ctx 41%            ← where you are
```

- **Modes**: the first word of the bottom line is KeelBot's mode: `[?] ask`, `[=/] edit`, `[-{] fix`, `[-<] side`.
  **Shift+Tab** or **Alt+Tab** switches to the next one (fix only while a gate waits), or `/mode <ask|edit|fix|side>`.
- **Typing**: Enter sends. **Alt/Option+Enter** or **Ctrl+J** adds a line (Shift+Enter where the terminal reports it).
  A paste of many lines becomes `[pasted 120 lines #1]` and goes to KeelBot whole. Tab completes commands and their
  arguments. `@` opens a picker over files, symbols and the flow's criteria (`@AC-2`).
- **History**: in an empty prompt **↑ / ↓** walk your earlier prompts of the project, newest first (the last 50, kept
  by keel, so they are the same after a restart and on another computer).
- **$EDITOR**: `/editor` or **Ctrl+X E** opens the prompt in `$VISUAL` / `$EDITOR`; what you save comes back.
- **!commands**: `!git status` runs a shell command in the project folder through keel's guard (a command that changes
  something waits for your OK; Ask mode only reads). Its output goes with your next question, once. `!` alone drops it.
- **Images**: a prompt that names an image (`shot.png`, an @mention, or a pasted image) sends it with the question to
  a model that can see it (Claude Code). Otherwise keel says why not.
- **Changed files**: after an answer in Edit, Fix or Side, a block lists the files that answer changed, with their
  diffs; an answer that changed nothing shows no block (0.18.21). `/changes` lists every file KeelBot changed in the
  chat. `/keep [path|all]` runs the checks, then keel commits.
- **/undo /redo**: `/undo` undoes KeelBot's last answer: every file it changed goes back, and your question comes back
  into the prompt. `/undo` again goes one answer further back; `/redo` brings it back until you send something new. It
  never runs git commands that throw work away, and it refuses (naming the files) when you changed them since.
  `/undo <file>` or `/undo all` puts only files back.
- **/compact**: KeelBot writes a real summary (objective, requirements, decisions, work state, next move, relevant
  files, exact commands and errors) and goes on from it in a fresh session; every message stays stored. A second
  `/compact` updates the summary. The **context meter** in the bottom line (`ctx 41%`) is yellow from 70%, red from 85%.
- **/chats** is a picker (title, last message, age, size; type to filter). **/rename <name>** names the open chat.
  **/export** writes it as Markdown into `.keel/chats/<id>.md`. **/new** starts a new chat.
- **Select to copy**: drag with the mouse and the text is copied ("copied N characters"); a double-click copies a word
  (a path or a URL is one word). `/copy` copies the last answer, `/copy 2` the block before it. Copying goes through the
  terminal (OSC 52), so it also works through docker exec and ssh, but not in macOS Terminal: there, `/mouse off` and
  select. Shift/Option + drag gives your terminal's own selection.
- **Permission cards**: KeelBot's questions are a choice right after the answer: Allow once, Allow … in this chat,
  Deny, Deny and say why (see [KeelBot](#keelbot)). KeelBot's buttons (start a flow, git, CI, database) are Yes / No.
- **The failure rule**: a failed command is not tried again unchanged; KeelBot offers the ways forward as a choice.
  After a failed button keel asks "It did not work. Find a fix?"; `/fix` sends the last error to KeelBot.
- **Working**: a spinner with a keel word (*Charting…*, *Tacking…*), seconds and tokens. **Esc** stops the turn.
  "✓ Finished in 4.2s · claude/sonnet · high" ends each turn.
- **Screen**: keel's name on top and the prompt at the bottom stay; the transcript scrolls (PgUp / PgDn, Shift+↑↓, the
  wheel, Home, End). Panels: **Ctrl+F** the flow rail, **Ctrl+A** the agents, **Ctrl+N** the Inbox, **Ctrl+O** follow
  the agent at work. Click an agent to follow it, or "◆ waiting" to answer it. `/clear` keeps the start screen.
- **Tool calls**: each one KeelBot makes is one quiet line, `⚙ keel_status [project=app]` or `$ git status --short`,
  its result folded under it (`└ 220 lines`, or the error in red); **Ctrl+O** or a click opens the whole output.
  Under the answer: `■ Ask · Claude Sonnet 5.5 · 7.6s`, the turn's time.
- **Sidebar** (a terminal 120 columns wide or more): the chat's context and cost, the flow and what waits, KeelBot's
  MCP servers, the files the answer changed. **Ctrl+B** or `/sidebar` hides or shows it; keel TUI keeps the choice
  (`~/.config/keel2/tui.json`, or `KEEL_TUI_SETTINGS`).

**Commands** (`/help` lists them all, grouped):

| Group | Commands |
|---|---|
| Flows | `/project`, `/flow [workflow] [idea…]`, `/flows`, `/resume [n]`, `/stop [all\|n]`, `/run [manual\|important\|auto\|readonly]`, `/status`, `/next`, `/workflows`, `/create <workflow\|rule\|agent\|skill\|stack> [what]` |
| Gates | `/inbox`, `/approve [n]`, `/reject [n] <why>`, `/answer [choice \| q1=answer …]` |
| Look | `/jobs`, `/agent [n \| new <task>]`, `/live`, `/steps`, `/timeline`, `/diagram …`, `/rules`, `/skills`, `/skill …`, `/stacks`, `/stack …`, `/plugins`, `/budget`, `/dashboard` |
| KeelBot | `/model`, `/effort`, `/chats`, `/rename`, `/export`, `/compact`, `/fix`, `/new`, `/mode`, `/changes`, `/keep`, `/undo`, `/redo` |
| Screen | `/help`, `/panel`, `/clear`, `/copy [n]`, `/mouse [on\|off]`, `/follow [on\|off]`, `/settings`, `/editor`, `/quit` |

`/agent new <task>` starts an agent of your own in its own worktree and branch; a notice says when it is finished.
Plugins add commands, panels and whole modes (the Git plugin: `/git`, Ctrl+G; Code Review: `/review`). KeelBot's own
commands (`/gate`, `/where` …) go to KeelBot as they are.

Options: `keel2 tui -c /status -c /next` prints answers without opening the app; `--inline` (or `KEEL_TUI_INLINE=1`)
keeps the transcript in the terminal's scrollback; `--no-mouse` gives the mouse back to the terminal; `--profile`
times the start, API calls and frames. Colours: `NO_COLOR`, `KEEL_TUI_THEME=light`, and your own
colours for single roles (below).

### keel TUI colours

keel TUI draws every piece of text with a **role** (the person's message, a gate that waits, a hint ...), and a palette says how each role looks.
`KEEL_TUI_THEME=keel|dark|light|auto` (or `/theme keel|dark|light|auto` while it runs) picks the palette:

- `keel` is the default (also with `KEEL_TUI_THEME` unset or `terminal`): keel's own look for a dark or unknown
  background. Green `#2fa84f` (ok, recommended, success, diff add), amber `#f2b84b` (gates, warnings), warm grey
  `#9a958c` (meta, dim, faint, comments), violet `#a48bff` (accent, headings, suggestions, code, keywords; a fixed
  shade, so it does not depend on your terminal's magenta), the terminal's blue for the person's bar, paths and links,
  red for errors. ok, gate, recommended and warn are bold.
- If `COLORFGBG` or the background the terminal reports says it is light, the default is `light` instead.
- `dark` and `light` are the older palettes on the terminal's own colours. `auto` follows the background (dark or
  light; colours that read on both when it is not known).

To change single roles on top of any palette, set `KEEL_TUI_COLORS`:

```
KEEL_TUI_COLORS="ok=#3cc463,gate=#e0a13a,recommended=ansi:green"
```

- Each entry is `role=colour`, separated by commas. A colour is `#rgb`, `#rrggbb`, an ANSI colour name (`red`,
  `cyanBright` ...) or `ansi:<name>`; the terminal's own theme decides the exact shade of an ANSI name.
- Roles: `person`, `keelbot`, `heading`, `accent`, `gate`, `question`, `option`, `recommended`, `chosen`, `suggest`,
  `hint`, `detail`, `meta`, `border`, `ok`, `warn`, `bad`, `code`, `path`, `link`, `run`, `add`, `del`. Also
  accepted: `success` (= `ok`), `warning` (= `warn`), `waiting` (= `gate`), `error` (= `bad`).
- Style flags join the colour with `+`: `ok=#3cc463+bold`, `ok=#3cc463+bold+underline`; flags are `bold`, `underline`,
  `italic`, `dim`. A flag alone keeps the palette's colour (`ok=+bold`). `+nobold` removes the bold a role has by default
  (`gate=+nobold`). In the settings file a role can also be an object: `{"ok": {"color": "#3cc463", "bold": true}}`.
  The flags reach every place that draws the role (status line, banner, notes, gates). `NO_COLOR` gives plain text, no bold.
- The status line uses them: the flow `done`, `AC 3/3` and `live` are `ok`; a flow that waits and `◆ n waiting` are `gate`.
- A bad entry is ignored and keel TUI prints one quiet warning at the start.
- The same in the settings file `~/.config/keel2/tui.json`: `{ "colors": { "ok": "#3cc463", "gate": "ansi:yellow" } }`.
  The environment wins over the file.
- `/theme` (no word) lists the active overrides and `/theme keel` or `/theme light` keeps them. `NO_COLOR` still turns all colour off.

## Thinking effort

Each agent, and KeelBot, has a **thinking effort** next to its model: how hard the model thinks before it acts. More
effort is slower and uses more tokens; less is quicker and cheaper. **default** leaves it to the engine.

- **Where to set it**: the Agents page (per agent, per project), KeelBot's model picker (for one chat), keel TUI
  (`/model` asks for the effort after the model; `/effort high`, `/effort default`), and MCP (`keel_agent_model`, or
  `keel_agent_create` with `effort`; both ask you first).
- **Where you see it**: next to the model everywhere: Agents, Jobs, Live agents, the Flow page, KeelBot's header and
  each answer ("sonnet · high"), keel TUI's job lines and "✓ Finished … · claude/sonnet · high", and `keel_agents`.
  Each agent call keeps the effort it ran with, so the history shows it too.
- **Which models have it**:

| Engine | Efforts | How keel passes it |
|---|---|---|
| Claude Code (subscription) | low, medium, high, xhigh, max (not Haiku) | `claude --effort` |
| Claude API key | low, medium, high, xhigh, max (not Haiku) | `output_config.effort` |
| Codex CLI and OpenAI key | what each model offers (from Codex's model list) | `-c model_reasoning_effort=…` / `reasoning_effort` |
| Copilot through OpenCode | low, medium, high (newer GPT also xhigh; Gemini none) | `opencode run --variant` |
| Copilot CLI, GitHub Models key | none | — |
| Fake model | low, medium, high (only noted, for tests) | — |

A model without an effort setting shows "no effort setting", and keel ignores an effort it cannot pass on.

## Plugins and the Marketplace

From 0.17.0 keel is a small **core** with **plugins**. The core runs flows, workflows, agents, gates, the Inbox,
the budget and connections. Each other part is a plugin with its own version:

| Plugin | What it adds | Needs |
|---|---|---|
| **Code** | the Code page (the IDE) | |
| **Git**, **Code Review**, **Database**, **CI/CD** | their panels in Code, their workflow steps and agent tools | Code (CI/CD also Git) |
| **KeelBot** | the chat (⌘I) and its commands | |
| **Map**, **Graph**, **Wiki** | their pages | |
| **Tasks**, **Jira** | the task board and Jira tickets | Jira needs Tasks |
| **keel Product** (beta) | initiatives and teams | Tasks and Jira |

**Settings › Plugins** (`#/settings/plugins`) has these tabs:

- **Installed:** every plugin with its version and an On switch. Turning a plugin on or off restarts keel (about 30 s).
  A plugin that others need goes off together with them, and one that needs others turns them on. Database, Git,
  CI/CD and Code Review also have a **This project** switch: they are on only for the projects you choose.
- **Marketplace:** things you can add, with who made them, what they may do ("Runs code in keel"), and what they
  need. keel checks each package's signature before it installs. The switch at the top finds **Plugins · Workflows ·
  Rules · Skills · Stacks**: preview (a workflow's steps, what a rule pack stops and in which phases), install, update
  and uninstall. **Publish** a workflow from Build › Workflows. The public marketplace opens soon; until then it shows
  the catalogs you add.
- **Skills** (0.18.6): know-how an agent reads before it works. **From keel** lists keel's own skills (in every
  project already): **Agents…** picks which agents load each. A catalog's skill opens with its whole text and keel's
  review (its size in tokens, its sections); **Install** asks which agents should load it; **Update** asks before it
  replaces a copy you changed; **Remove** takes it away. A skill only adds words to a prompt: keel's rules still decide
  what an agent may do. The same from keel TUI (`/skills`, `/skill attach <id> <agent…>`, `/skill install <name>`,
  `/create skill <what it should teach>`) and from MCP (`keel_skills`, `keel_skill_attach`, `keel_skill_market_install`,
  `keel_skill_create`); KeelBot gives a **Save the skill** card.
- **Stacks** (0.18.9): how a project builds and tests (its test layers, commands and tools). **From keel** lists
  keel's own stacks: they are used wherever they are detected, and a keel pack has **Install pack**. A catalog's stack
  opens with its YAML, every command it runs and keel's check against your project; **Install** saves it as
  `.keel/stacks/<name>.yml`; **Update** and **Remove** as for skills.
- **Sources and rules:** the catalogs keel reads (add one with its URL and public key), and, for plugin authors, a
  `.kplug` file from a path in `/data` (keel marks it **unsigned**).

Click a plugin to see its page: its parts (engine, api, web, content), permissions, versions and what it needs. For
Database, Git, CI/CD and Code Review it also shows what each adds (KeelBot tools, workflow blocks, commands) and a
**Projects** list with a switch per project.
Agents cannot install plugins; they can only **ask**, and you approve in the Inbox.

Start keel with fewer plugins:

| Command | What you get |
|---|---|
| `keel2 start` | keel with every plugin (the `:latest` image) |
| `keel2 start --core` | keel's core only (the `:core` image); add plugins in Settings › Plugins |
| `keel2 start --safe` | only the plugins that came with keel, none you installed (when a plugin breaks keel) |

Make your own plugin: [docs/plugins/17-publish-your-plugin.md](plugins/17-publish-your-plugin.md), or let keel build it:
the **plugin** workflow writes its spec (you approve), scaffolds it with `keel-plugin new`, builds each criterion test
first, then runs `keel-plugin lint` and the plugin's tests (`scripts/plugin-check.sh`) and ends at a review gate. A
plugin can also add commands, panels and full-screen modes to keel TUI (`contributes.tui`; data only, no plugin code
runs in the terminal app). How it all works: [docs/plugins/README.md](plugins/README.md).

## Real projects: containers, commit style, one owner per task

**A toolchain that lives only in a container.** When `.keel/config.yml` runs the tests through a service
(`api_test_module: docker compose exec -T app vendor/bin/phpunit`), keel:

- sets up a flow's worktree with the project's own dependency command (`commands.deps_api` / `deps_web`), never a
  stack's bare `composer install` on keel's side; with no such command and the tests in a container it runs nothing;
- checks the toolchain before a flow's first step: `commands.preflight` when it is set, else the test runner's
  `--version` through the same service. A failure stops at a gate that says what failed and the command, with
  *I started the container, check again*, *Run in the project folder* (a worktree flow), *Skip the preflight* and *Stop*;
- reads the service's compose volumes: a container that mounts only the project folder runs its commands on the
  folder's code, so flows run in the project folder (Start a flow says why: "tests run in the container `app`, which
  sees only the project folder") and a second flow waits until the first one ends.

**Commit style.** `git.commit_template` in `.keel/config.yml` sets keel's subjects, with `{ticket}`, `{type}`,
`{scope}` and `{subject}` (for example `{ticket}: {type}: {subject}`). Without one keel follows the ticket style most
of the branch's recent commits share (its own commits do not count), else writes its own `feat(AC-2): ...`. `{type}`
comes from what changed (only tests: test; a refactor criterion: refactor; the fix flow: fix; only docs: docs; else
feat), `{ticket}` from the flow's ticket or the branch name (`feature/SHOPS-1234-euro-prices`). With another style keel
adds `Keel-Ref: feat(AC-2)` so the trace and the audit still find each criterion's commits. A cut subject's body holds
only the words that did not fit.

**The ship audit and keel's own commits.** "Mark as already met" commits the criterion's test as `test(AC-n)` with
`Keel-Path: already-met`; test files keel's unlock opened in green are named in `Keel-Unlocked:`. The audit accepts
both. It compares with the base branch's remote copy (`origin/main`, fetched first) when there is one, so commits
merged upstream are never read as the branch's own, and the ship menu says when the local base is behind or
diverged. At the audit gate *Accept these and go on* records your reason for exactly the problems listed (the audit
does not ask about them again); a plain retry of a history check says it finds the same thing until the branch changes.

**Uncommitted files at a start.** Editor and tool folders (`.serena/`, `.idea/`, `.vscode/`, `.fleet/`,
`.DS_Store`, `.claude/settings.local.json`) never block a start. For any other untracked path the refusal offers
**Ignore this path** (it adds the path to `.git/info/exclude`, on this computer only); keel TUI's `/flow` and MCP
`keel_start_flow(ignore=[...])` offer the same.

**One owner per task.** Start a flow, MCP `keel_start_flow`, Tasks › Start and KeelBot side sessions refuse while
another flow or side agent of the project works on the same ticket, branch or files, and say who ("flow SHOPS-1234:
Euro prices at red", "side agent h_..."). *Start anyway* (`force`) starts it all the same.

**Stale configuration.** `keel2 doctor <project>`, the project's doctor (`GET /api/projects/{pid}/doctor/config`) and
knowledge-refresh check that the configured commands can still run: a compose service a command execs into is in the
compose files, a composer / npm script is in composer.json / package.json, a make target is in the Makefile. Each stale
one is listed with `.keel/config.yml` and its line.

## Stacks

**Build › Stacks** shows how each project builds and tests. A stack is one YAML file: its lane (api or web), the
files that show a project uses it, its test layers, its commands (test one criterion, every test, coverage, static
checks, migrate, compile) and its tools (formatters, linters, pre-commit).

- **New stack** asks step by step: lane, the files that detect it, test layers, commands, tools (and a `setup:` block,
  below). keel reads your
  project first (package.json scripts, Gradle, Maven, pyproject, composer.json, go.mod, a Makefile, pre-commit) and
  fills each step; you change what you like. Then keel checks it (nothing is saved yet) and **Save the stack** writes
  `.keel/stacks/<name>.yml`.
- **Remove** takes away a stack the project added (its own, an installed pack, one from the marketplace), after a yes.
  keel's own stacks cannot be removed (other projects use them): **Turn off for this project** stops keel using one
  here (`stacks: {ignore: [...]}` in `.keel/config.yml`); **Turn on** brings it back.
- **Find stacks** opens Marketplace › Stacks.
- Test filters: `{AC}` is the criterion id (`AC-006`), `{AC_ID}` the same without the dash (`AC006`) for Java class
  names. Maven `-Dtest` and Gradle `--tests` filters written with `{AC}` also match `AC006…`.
- **Languages** (0.18.28): a stack brings the syntax colours for its files. `languages:` names highlight.js languages
  with their file endings; without it keel takes them from `detect.extensions`. The Code page loads them when the
  project opens, and again when a stack changes.

  ```yaml
  languages:
    - {name: php, ext: [php, phtml]}
    - {name: twig, ext: [twig]}
    - {name: yaml, ext: [neon]}
  ```

  Outside any stack keel still colours PHP, XML/HTML, CSS, SCSS, Go, Rust, Ruby, C#, C/C++, Swift, INI/TOML,
  Dockerfile, Makefile, Gradle, GraphQL and nginx files; each loads the first time a file of its type opens.

### What a stack needs: `setup:`

A stack names commands. It cannot install a language. keel's image has Java, Node and Python, nothing else. A stack
that needs more says so in a `setup:` block:

```yaml
setup:
  apt: [php8.3-cli, php8.3-xml, php8.3-mbstring, php8.3-sqlite3, php8.3-intl, php8.3-curl, unzip, composer]
  run: []                                   # after apt, as keel's own user (not root)
  each_worktree: [composer install -n --no-progress]
```

- `apt`: system packages (Debian and Ubuntu names). keel installs them as root in its container, in the background,
  when it starts, when a project is added and when a stack is saved. keel does not wait for it to start. The dashboard
  shows what it does ("setup: installing php8.3-cli for billing-router…").
- A stack that comes with the project (its own `.keel/stacks`, e.g. a cloned repo) never runs its `setup:` commands on
  its own. keel asks in the Inbox ("Run the setup commands of stack X?") and shows the exact commands; until you
  approve, the status says "needs approval". The approval is for those exact commands: change one and keel asks again.
  keel's own stacks and a stack you install from the marketplace need no question.
- After `keel2 update`, a restart or a restore the container is new, so the packages are gone. keel installs them
  again at start. The apt cache is root-owned (`/var/cache/keel-apt`, outside the data volume, so keel's user
  cannot plant packages in it), so a new container downloads the packages again.
- `run`: setup commands that do not need root, after apt, once per container. They run as keel's own user, for
  example a tool installed under keel's home. They never run as root: a system package goes in `apt`.
- `each_worktree`: commands keel runs in the stack's folder (`backend.dir` for an api stack, `frontend.dir` for a web
  stack) when a flow starts in a new worktree, once per worktree. A new worktree has no git-ignored folders, so
  `vendor/` or `node_modules/` must be installed again. keel also runs them when the folder has `composer.json` but no
  `vendor/`, or `package.json` but no `node_modules/` (the stack's `dependency_files`).
- The flow shows this as its first step, **set up the stack**, with the output. A flow that needs a package waits
  until keel has installed it. If it fails, the flow stops at a gate: **I fixed it, try again**, **Skip the setup** or
  **Stop the flow**. keel never answers this gate by itself.
- keel's Symfony pack and Go stack have a `setup:` block. When init finds a project stack for PHP, Go, Ruby or Rust
  without one, its plan proposes the block, and keel writes it after you approve the plan.
- The setup check (init's first rung) names the stack: "php not found; stack php-symfony lists setup.apt; keel
  installs it at start".
- Native mode (`keel2 start --native`) never installs anything on your computer. keel says which programs are missing
  and which packages to install.
- Agents cannot run keel's root helper (`keel-stack-setup`): the guard refuses it. To add a package, add it to
  `setup.apt`.

The same from keel TUI (`/stacks`, `/stack <name>`, `/stack remove <name>`, `/stack off <name>`, `/stack install
<name>`, `/create stack`) and MCP (`keel_stacks`, `keel_stack`, `keel_stack_suggest`, `keel_stack_check`,
`keel_stack_create`, `keel_stack_remove`, `keel_stack_off`, `keel_stack_market_install`). Ask KeelBot "write a stack
for this project": it gives a **Save the stack** card, checked by keel first.

## Plugins: Database, Git and CI/CD

Turn them on in **Settings › Plugins**: in **Installed**, the plugin's **This project** switch (or **For every
project**); its own page lists every project with its switch. Nothing new to learn: they add to the
pages you use.

- **Database**: add the project's database in **Settings › Databases** (keel suggests what docker compose,
  `.env.example` or Spring's config name; the password is saved encrypted). Then:
  - ask KeelBot data questions ("which players have no score?") — it reads the schema, runs one read-only query and
    shows the rows; `/sql` does it directly;
  - **Map › Database (ER) › Query**: click a table for its first rows, or write SQL;
  - workflows get `db:query`, `db:check` (a data check: `expect: none`), `db:change` and `db:migrate` blocks — a
    workflow of code steps only checks your data with no model and no tokens.
  A change of data (INSERT, UPDATE, DELETE) runs only on a **local** or **test** database: keel counts the rows first,
  then you press **Run it**. Staging and prod are read only for everyone. Schema changes belong in your migrations.
  keel refuses functions that reach the server (files, other sessions, settings, SQL run from a string such as
  `query_to_xml` or `dblink`), MySQL's executable comments (`/*! … */`) and file access (`INTO OUTFILE`, `LOAD DATA`),
  and masks secret columns for agents also under another name. These rules are a second line: still give each
  connection a **least-privilege database role** (a read-only user where agents only read, never a superuser). A
  SQLite file must be inside the project folder.
- **Git**: connect GitHub in **Settings › Connections › GitHub**: press **Sign in with GitHub**, open the link (github.com/login/device)
  and type the one-time code keel shows (there is a Copy button and an **Open GitHub** button). keel notices by itself
  when you allow access, and the card says "connected as <login> · scopes · host". Under it, **or paste a token** still
  works. **Sign out** removes keel's token. For GitHub Enterprise, type your host in the host field first. keel runs
  `gh auth login --web --scopes repo,read:org,workflow` inside its container (the image has `gh`; the token is saved
  encrypted, never shown); without `gh`, set `KEEL_GITHUB_CLIENT_ID` to your company's public OAuth App client id (device
  flow on) and keel uses GitHub's standard device flow. The same token serves the Git plugin, a flow's PR step, Code
  Review, CI/CD and Tasks. Then **Code › Source control** shows the branch against its
  remote and main, **Push**, **Update from main**, **Switch branch**, **New branch**, a commit box, and the pull
  request with its CI checks and review comments (**Ask KeelBot to address the comments**). KeelBot reads git and the
  PR (`/commit`, `/pr`, `/sync`, `/branch` draft the next step) and gives buttons; workflows get `git:sync`,
  `git:push`, `git:pr`, `git:pr-checks` (wait for CI), `git:branch` and `git:cleanup`. keel never force-pushes and never
  pushes to main.
  Under **Branches** (a tree: **Local** with the current branch on top, then `main`, then folders by prefix such as
  `feature/`, `fix/` and `chore/`, then **other**; one group per remote such as `origin`; folders open and close and
  keep their state per project; the filter searches every group, opens the folders with matches and counts them), click a branch (for example
  `feat/test`) to open it as a tab: the files it changed since it left main (click one for its diff), its commits that
  main does not have, **Switch to feat/test**, and **Ask KeelBot to review it**. Switching keeps your uncommitted work;
  when git refuses because of uncommitted changes, keel offers **Stash my changes and switch**.
- **Database tool (Code page)**: with the Database plugin on, the Code page gets a **Database** activity, like the
  database window of an IntelliJ IDE: every connection as a tree of tables and columns (keys, types, foreign keys).
  Click a table for its data (and its structure); press the console icon on a connection for a query console
  (⌘↵ runs the statement under the cursor). Several connections side by side: a local, a test and a read-only prod.
- **CI/CD**: keel watches the pipelines on GitHub Actions (with the GitHub token) and tells you when one fails.
  **Run › Jobs › Pipelines** shows the runs; open a failed one for its failed jobs and the end of the log, then
  **Fix it** (the ci-fix flow: read the failure, fix, commit, push, wait for CI), **Run the failed jobs again**, or
  **Ask KeelBot why** (`/ci`). **Settings › General › Git › When CI fails**: tell me, start the fix flow by itself, or nothing.
- **Claude Code**: `keel2 mcp` adds the read tools (`keel_db_query`, `keel_git_status`, `keel_ci_failure` …);
  `keel2 mcp --write` also the acting ones, and each waits for your **Allow** in keel's Inbox.
- **Code Review in the terminal** (0.18.6): with the Code Review plugin on, keel TUI (`keel2 tui`) has a **review mode**.
  `/review` opens the branch you are on, `/review 12` pull request #12; it takes the whole screen (keel's header and
  status line stay) until **Esc**. ↑↓ and **Enter** open a changed file's diff, with the threads, your drafts and keel's
  findings under their lines; `]` `[` go to the next file, `}` `{` to the next change. Keys: **c** a line comment (a
  draft), **r** reply, **x** resolve, **v** viewed, **e** / **f** ask keel to explain the change or find problems, **o** /
  **F** read them, **s** submit (approve, request changes or comment: keel asks first), **p** another pull request,
  **?** all keys. `/review prs` lists the open pull requests. `keel2 mcp` has the same: `keel_review_prs`,
  `keel_review_files`, `keel_review_diff`, `keel_review_notes`, and with `--write` `keel_review_comment`,
  `keel_review_explain`, `keel_review_findings`, `keel_review_reply`, `keel_review_resolve`, `keel_review_submit` (the
  last three post to GitHub or GitLab, so your client asks you first). Any plugin can add a mode like this:
  [docs/plugins/25-tui-plugins.md](plugins/25-tui-plugins.md).

## Quality

**Build › Quality** shows how well keel's flows work with each model. keel runs small eval projects (in
`content/evals/`) through the flows you pick, in run mode **auto**, one case at a time, and scores each run from 0 to
100: did it reach the end, how often was something sent back, and did it stay within the token estimate. Pick one or
two models and press **Run now**, or turn on **Every night at …**. When a score falls by 15 points or more since the
run before, the page shows it in red: check the last change to a prompt, an agent, a skill or a model. The runs' tokens
count in the budget.

## Inbox and run modes

**Inbox** lists everything that waits for you in every project: gates, the explorer's questions, a check that keeps
failing, a budget pause, a new dependency, a KeelBot command that needs your OK. Answer it there or open its flow.
Choices show as cards with their meaning; a reason box opens only for a choice that needs one.

Each flow has a run mode. Pick it in *Start a flow* (the default is in Settings), and change it on the Flow page while
the flow runs (keel TUI: `/run`; MCP: `keel_run_mode`; or ask KeelBot). To start a flow in a mode from elsewhere:
keel TUI `/flow feature --mode important <idea>`, MCP `keel_start_flow(..., run_mode="important")`, or KeelBot's start
button (ask "start it in important mode"). A flow that a flow starts (a spike's feature) gets the mode its parent has
at that moment. (0.18.28: a flow started from those places ran in the project's default, and the start form could
put the default back over the mode you picked.)

| Run mode | keel decides by itself |
|---|---|
| **manual** (default) | nothing: it stops at every gate |
| **important** | a criterion's gate, when its checks pass and its review is clean |
| **auto** | every gate it can decide (plain approve, the default choice, the recommended answers); each is logged and listed in the final review and the PR body; it never opens a PR |
| **readonly** | nothing; agents cannot edit, write or commit anything (or download files) |

In every mode keel still stops for the token cap, a new dependency, a secret in a commit, a check that keeps failing,
a stuck criterion, and anything only you can answer. It never pushes.

## Usage and caps

keel counts every agent call: tokens, money, model and effort. **Budget** shows today, this month, per flow and per
agent, and the caps. The bar on top of every page shows each provider's fullest window. keel TUI's top line shows the
tokens and money of today and this month; `/budget` and `keel_budget` show the rest.

- **Real numbers**: failed, cancelled and out-of-turns calls count too. A long step counts while it runs (Claude Code
  and opencode report tokens as they go). A resumed Claude session is counted once.
- **Plan usage**: for each provider that is set up, what its plan says is used and what remains (5 hours, week,
  month), with where the number came from and how old it is. The Claude card shows the live weekly number. Before a
  subscription agent, keel warns at 80% of a plan window and pauses at 95%.
- **Copilot** costs its premium requests × the model's multiplier × $0.04 (models included in the plan cost $0). Set
  your own multipliers and prices in `prices.json` in keel's data folder.

```
 Start a flow ─ keel's estimate ─ cap = high end + 30% (when above the Settings cap)
      │
      ▼
 the flow runs ─ tokens reach the cap ─◆ cap gate ─ approve ─ the cap goes up by at least half of what the flow used
                                                └ stop
```

- **Token cap per flow**: Settings (default 500,000 tokens). **When a flow reaches its cap**: pause (default), go on
  with a cheaper model, or stop. A flow started without a cap (MCP, keel TUI, the API, a KeelBot button) gets the
  estimate's high end plus 30%, and says so.
- **Your own caps** (Budget): per day, per flow, per step, and API spend per month. **Accounts**: tokens in the last 5
  hours for a Claude or Codex subscription, Copilot premium requests this month (300 by default), and USD spent on API
  keys this month (100 by default). 0 = not set.

## Tasks and Jira

**Tasks** is a board of your work: your own tasks, or the tickets of a Jira board or query (Settings › Connections › Jira, per
project: Jira Cloud with email + API token, or Server / Data Center with a personal access token).

Press **Start** on a task and keel runs a flow for it (bug → fix, story → feature, task → change). It moves the Jira
ticket to In progress, to In review when the PR opens (and asks the reviewers on GitHub and in Jira), and to Testing
(PP) when the PR is approved. Then the Inbox asks you to confirm PP testing and the release. When keel cannot reach
Jira, the Inbox asks you to move the real ticket by hand. Without Jira, tasks stay in keel with their history.
Details: [CONTRACT.md](CONTRACT.md), "v0.5.0: tasks and Jira".

## Logins

The image has claude, codex, copilot and opencode installed. Nothing from your computer is copied in. Each login is
saved encrypted in keel's own database (the `keel-data` volume), so it survives restarts and updates.

| Tool | Log in from the dashboard | Or paste |
|---|---|---|
| Claude (subscription) | sign in on claude.com, paste the code it shows | `claude setup-token` on your computer |
| Codex (ChatGPT) | open the device link, type the code | `~/.codex/auth.json` |
| GitHub Copilot | open github.com/login/device, type the code | a token with "Copilot Requests" |
| OpenCode (Copilot) | — (OpenCode keeps its own login) | `docker exec -it keel-v2 opencode auth login`, pick GitHub Copilot |

From the terminal: `keel2 token <claude|codex|copilot|anthropic|openai|github>`, and `keel2 tokens` to list them. A long
token is often shown over two lines; send it in one piece from the clipboard: `pbpaste | keel2 token claude` (Linux:
`wl-paste | keel2 token claude`). keel checks the token before it saves it and says what is wrong. Settings › Models & logins shows a
subscription as ready only when keel has its login; OpenCode only when its own login file (`opencode auth login`, where
keel's agents run) has a GitHub Copilot login. In native mode keel's agents use their own home, so your own
Claude Code login is not used.

## Signing in

keel answers only you. There is no password: `keel2 start` and `keel2 open` open keel in your browser already signed
in. A browser that is not signed in shows one screen: "Run keel2 open in a terminal".

```bash
keel2 open            # open keel signed in (a link that works once, for 60 seconds)
keel2 open --print    # print that link, to open it in another browser
keel2 auth key        # print the key, for your own scripts: Authorization: Bearer <key>
keel2 auth reset      # a new key: every browser signs in again (keel restarts)
```

Other programs on your computer, and web pages, cannot change anything in keel. keel2, keel TUI, keel's MCP server and
keel's own engine bring their own keys. keel's agents never read those keys (a rule that cannot be switched off).

## Use keel from your phone

keel's Android app shows your flows, gates and inbox on your phone. keel runs on your computer; the phone needs a way
to reach it, and each phone pairs once.

```bash
keel2 start --wifi        # reachable on your Wi-Fi: http://<this computer's address>:<port> (plain http in your network)
keel2 share tailscale     # https on your tailnet (tailscale serve); keel itself stays on 127.0.0.1
keel2 share tunnel        # a public https address (a Cloudflare quick tunnel); keel2 asks first
keel2 share               # what is shared now
keel2 share off           # stop sharing (or: keel2 share off tailscale | tunnel)
```

Then open **Settings › Devices › Pair a phone** in the dashboard and scan the QR code with the app. The code works
once, for 10 minutes. The list shows every paired phone with when it was last seen; **Remove** signs a phone out at
once.

- `--wifi` is remembered by `keel2 restart`; a `keel2 start` without it is this computer only again. `KEEL_BIND=0.0.0.0`
  (or one address) does the same. When keel2 shows the wrong Wi-Fi address, set `KEEL_LAN_IP`.
- Wi-Fi is plain http: others on the same network could read the traffic. Tailscale gives https and works anywhere.
- A tunnel's address is public and changes each time it starts. Anyone with it reaches only the sign-in and the
  pairing; everything else needs your sign-in or a paired phone, and wrong tries are slowed down (429).
- The tunnel uses `cloudflared` when it is installed, else Docker's `cloudflare/cloudflared` image.

## Use keel from Claude Code or Claude Desktop (MCP)

keel has an MCP server with three levels; each includes the one below:

| level | how | adds |
|---|---|---|
| read (default) | `keel2 mcp` | status, timeline, next step, phase rules, dashboard links, flows, Inbox, live agents, budget, specs (with history), knowledge, search, read a file, engine actions, keel's rules, workflows, agents (one agent's steps live), KeelBot's chats and changes, diagrams in ASCII (`keel_diagram`), skills, stacks, the formats and checks that save nothing (`keel_format`, `keel_workflow_check`, `keel_rule_check`, `keel_stack_check`), keel's spec check on a spec or clarify file (`keel_spec_check`) and the check of every `path:line` it cites (`keel_citations_check`, also on a text), the marketplace search, the Database, Git and CI/CD read tools |
| write | `keel2 mcp --write` | decide or send back a gate, answer the Inbox (several answers, a choice and a reason; questions in a form when the client can show one), start a flow (no type: every flow with what it is for and its inputs, like ticket or pr for review-pr), stop and resume flows, set a run mode, start an agent, ask KeelBot (`keel_keelbot_ask`, also `mode=edit`) and keep or undo its changes, create workflows, rule packs, agents, skills and stacks, attach skills, set an agent's model and effort, restore a spec, git / PR / CI / database writes, Code Review comments and submits |
| admin | `keel2 mcp --admin` | delete flows, pause (`keel_pause`; `keel_resume` goes on), add and remove projects, switch the project the dashboard and keel TUI show (`keel_focus`), connect GitHub with a one-time code (`keel_connect_github`: it returns the code and the link, never the token), change workflows, install and change rules |

```bash
keel2 mcp --print-config                                 # the Claude Code line and Claude Desktop JSON per level
claude mcp add keel-v2 -- "$(command -v keel2)" mcp --write
```

Pushes, database changes, deletes, stops and saves ask you first in the client; a client that cannot ask is refused
unless you add `--yes`. `--allow keel_status,keel_approve_gate` keeps only those tools. `keel_answer(ask_person=true)`
asks you through the client's own form when it has one. Everything the client changes shows on the flow's timeline as
"via MCP · <client>: <tool>". keel's own agents always get the read level.

When a flow asks you something (the interview, a decision per finding, "ready to ship?"), `keel_status` and
`keel_inbox` show each question with its options and the recommended one. A client that can show forms asks you right
there when you approve (`keel_answer`): pick one, tick several, or write your own words.

A gate with named choices lists each one with what it does and whether a reason is used, and what a plain approve
or reject does there; pass the name as `choice` to `keel_approve_gate` or `keel_answer`. `keel_status` shows up to
4,000 characters of a gate's text and then says how much is left; `keel_read_gate(project, flow, page)` returns the
whole gate (findings, the failing output, at a spec gate the spec), page by page ("page 1 of 3", with the next
call). `keel_edit_gate(notes)` takes the gate's edit choice (the spec gate's **edit**, the contract's **change**) or
sends it back where the gate takes that.

keel's MCP server runs every tool off its event loop, keeps stdout for the protocol only (a stray print goes to
stderr) and answers a tool's own error as text, so a slow call never makes a client drop keel's tools (0.18.28).

| keel v1 MCP | keel v2 MCP |
|---|---|
| keel_status, keel_projects, keel_timeline, keel_next, keel_explain | the same names |
| keel_dashboard (flow, map, er) | keel_dashboard (flow, map, er) |
| — | keel_flows, keel_inbox, keel_live, keel_budget, keel_specs, keel_knowledge, keel_search, keel_read_file, keel_spec_check, keel_citations_check, keel_actions (read); write and admin tools (v1 has none) |

keel v1 is a separate project; its MCP server can be added like any other: `keel2 start --with-keel-v1 <keel
checkout>`, then turn on "keel v1 (optional)" in Tools.

**xal**: `clients/xal/` is a keel plugin for [xal](https://github.com/xal-sh/xal), a terminal coding harness, with
`/keel:status`, `/keel:flow`, `/keel:approve`, `/keel:watch`, `/keel:diagram` and more (it signs in with `keel2 auth key`).

## keel in Muxy

[Muxy](https://github.com/muxy-app/muxy) is a macOS terminal with project split panes and a notification center.

- `keel2 muxy hook` tells Muxy about every question that waits and every flow that is done or failed, one
  notification each, never twice: `keel · shop: feature waits for you`, the question, the choices, and where to answer
  (`keel2 tui` or the dashboard's Inbox). It only reads keel. `--project <name>` keeps one project, `--test` sends a
  sample, `--install` starts it at login (a LaunchAgent; it asks first), `--uninstall` removes it.
- `keel2 muxy workspace [folder]` opens the folder in Muxy with three panes: `keel2 tui` on the left, keel's live
  events (`keel2 muxy hook --feed`) and a shell on the right. It also writes `.muxy/layouts/keel.json`, so Muxy's top
  bar › Layouts › keel brings the same panes back.

Muxy's notifications have no buttons yet, so you answer in keel2 tui or the dashboard. What Muxy has, what it lacks,
and a proposal for buttons: [muxy.md](muxy.md), [muxy-proposal.md](muxy-proposal.md).

## keel2 commands

`keel2` starts and manages keel. `keel2 help` lists everything.

| Command | What it does |
|---|---|
| `keel2` | in a terminal while keel runs: keel TUI; otherwise the help (`KEEL_TUI_DEFAULT=0`: always the help) |
| `keel2 start [folder]` | start keel for a project (default: this folder) and open the browser |
| `keel2 start --docker [dir]` | the same, and the project's tests may use your Docker (Testcontainers, compose) |
| `keel2 start --native [dir]` | keel on this computer without Docker (see [Native mode](#native-mode-no-docker)) |
| `keel2 start --core` · `--safe` · `--with-keel-v1 <checkout>` | core only · only the image's plugins · also keel v1's MCP server |
| `keel2 stop` · `restart` | stop keel / start it again with the same project, options and image (they ask when agents work; `--force` does not) |
| `keel2 add <folder…>` · `remove <folder…>` · `folders` | more project folders (see [Several project folders](#several-project-folders)) |
| `keel2 status` | is keel running, for which project, on which port, which edition |
| `keel2 tui` | keel TUI (see [keel TUI](#keel-tui-keel2-tui)) |
| `keel2 open [--print]` | the dashboard, signed in |
| `keel2 auth key` · `auth reset` | print the sign-in key · make a new one |
| `keel2 start --wifi` · `share tailscale\|tunnel\|off` | use keel from your phone (see [Use keel from your phone](#use-keel-from-your-phone)) |
| `keel2 logs` | follow keel's log |
| `keel2 doctor` | check Docker, the port, the image and the project, and say how to fix problems |
| `keel2 update [--rollback]` | the newest image (native: release) and a restart; `--rollback` goes back one version (native) |
| `keel2 token <tool>` · `tokens` | save a login or key · list them (hints only) |
| `keel2 backup [file]` · `restore <file>` | keel's data to a .tgz file and back |
| `keel2 sql` | open keel's SQLite database |
| `keel2 mcp [--write\|--admin]` | keel's MCP server (see [MCP](#use-keel-from-claude-code-or-claude-desktop-mcp)) |
| `keel2 plugins list\|install\|remove\|search\|update\|rollback\|safe` | manage plugins from the terminal |
| `keel2 muxy hook [--project <name>] [--test\|--install\|--uninstall]` / `keel2 muxy workspace [folder]` | keel in Muxy (see [keel in Muxy](#keel-in-muxy)) |
| `keel2 uninstall [--all]` | remove the container (`--all`: also keel's data, after you confirm) |
| `keel2 version` | keel2's version and the image |

Settings (environment): `PORT` (default 8080; a busy port moves to the next free one), `KEEL_NAME`, `KEEL_VOLUME`,
`KEEL_IMAGE`, `KEEL_OPEN=0` (no browser), `KEEL_DOCKER=1`, `KEEL_PLUGINS=on|image|off`, `KEEL_BIND`, `KEEL_LAN_IP`. Get a new `keel2` script with
`curl -fsSL https://raw.githubusercontent.com/MiladNalbandi/keel-v2/main/install.sh | bash`.

## Where keel keeps its files (layouts)

keel reads keel v1's `layout:` from `.keel/config.yml`: `repo` (specs in `docs/specs` or `specs`, `docs/knowledge`,
`docs/adr`, `docs/RUNNING.md`, committed with your code), `keel` (all of it in `.keel/`, which keel hides from git in
`.git/info/exclude`) or `external` (`.keel` is a link to a folder outside the repo; add that folder too:
`keel2 add <folder>`). A new project gets `keel`. A project that has keel files in `docs/` keeps them there. A project
with both `.keel/specs` and `docs/specs` shows both in the Wiki and in Code › keel files. KeelBot's exported chats go to
`.keel/chats/`.

keel keeps every version of these files in its own database, because `.keel/` is not in git: each Wiki page of a
spec, a knowledge section or a decision has a **History** (who changed it, in which step, a diff, Restore), Code ›
keel files shows a file that went missing with a **Restore** button, and the Flow page lists what keel applied
(branch, commits, files).

In the **Wiki** (0.17.3) every menu section (Specs, Knowledge base, Workflows, Runbook, Decisions) opens and closes
with its ▾/▸ title; a closed one shows how many pages it has, keel remembers your choice per project in this browser,
and the section of the page you read stays open (on a phone, the same in the Page dropdown). Each spec has a pill:
**built**, **in progress**, **stopped** or **not built**. A spec's page shows its **Build status**: the flow that built
it (a link), the flow that builds it now (its phase, the gate it waits at, **Open the flow**), **Resume** for a stopped
flow (only the last stopped flow can be resumed; otherwise it says why), and **Start a flow from this spec** (the
feature flow, started from this spec and its criteria, with the estimate first). keel knows this from the flows that
wrote the spec (its History) and the flows started from it.

<!-- 0.18.21: the wiki page layout -->

## Native mode (no Docker)

```bash
keel2 start --native ~/Projects/shop        # Java 21, uv and git on this computer; Node 20+ for the agent CLIs
```

keel runs on your computer instead of in a container: the engine and the api start from `~/.keel2/<KEEL_NAME>/`
(the release is downloaded and checked once; `keel2 update` gets the next one, `keel2 update --rollback` goes back).
Every other command works the same: `status`, `stop`, `logs`, `doctor`, `mcp`, `tui`, `add`, `backup` … (keel TUI uses
this computer's Node 20+).

**Agents run as you**: they can read what you can (your SSH keys, other projects, your network). In Docker they only
see the project folders. Docker stays the default; use native mode only on a computer and projects you trust. keel
gives the agent CLIs a home of their own under its data folder, so your own `~/.claude`, `~/.codex` and
`~/.copilot` are never touched.

## Docker mode

The image has JDK 21, Node 20, Python 3.12 and the Docker CLI (compose, buildx). `keel2 start --docker` mounts your
Docker socket and the project at the **same path** as on your computer, so Testcontainers and compose bind mounts
work. Docker access is as strong as root on your computer: use it only for projects you trust.

## Several project folders

`keel2 start <folder>` gives keel one folder. To add projects from other places, name them, several at once:

```bash
keel2 add ~/Projects/api ~/Work/web ~/Projects/clients   # each a git repo, or a folder of git repos
keel2 folders                                            # the main folder and the added ones
keel2 remove ~/Work/web                                  # off the list; keel keeps its history
```

The list is kept per keel in `~/.config/keel2/<KEEL_NAME>.folders`. If keel runs, `add` and `remove` restart it once
(they ask first when agents are working), with the image keel runs now (only `keel2 update` takes a new one). Each
added folder is mounted at the **same path** as on your computer, and every git repo in it becomes a project (a folder
of repos: each repo directly inside it). More repos later? All projects › Add projects lets you pick them. A folder
that no longer exists is skipped (`keel2 folders` marks it).

**Set keel up for a project you add** (0.18.55). A project keel does not know yet has no `.keel/config.yml`. After
**Add projects** (and the first time a project keel found by itself opens on the Flow page) keel offers the
[init](#init) flow:

```
 ┌ Set up keel for shop ──────────────────────────────────────────────────────┐
 │ init finds the stack and the test commands, and writes .keel/config.yml    │
 │ and the project knowledge.                                                 │
 │ [ Set up now (recommended) ]  [ Later ]                                    │
 └────────────────────────────────────────────────────────────────────────────┘
```

- **Set up now** starts init and opens it; it stops at its first question for you. **Later** puts the card away.
- Until init has written the config, the Flow page shows a line with **Run init**, All projects marks the project
  **not set up**, and **Start a flow** or a task's **Start** ask once: **Run init first (recommended)** or
  **Start anyway**. Nothing is blocked.
- keel TUI: the start box says `/flow init`, `/flow` lists init first, and another flow asks the same. `/flow init`
  needs no idea. MCP: `keel_start_flow` puts a line about init first for such a project.

**A second keel** (`KEEL_NAME=keel-lab PORT=8097 ./keel2 start …`) gets its own data: the volume is `<KEEL_NAME>-data` (`keel-data` only for the default `keel-v2`; `KEEL_VOLUME` sets another). keel2 refuses to start a keel, restore a backup or delete data on a volume another running keel uses: two keels on one volume would write one database at the same time.

## Git worktree projects

A git worktree (or a submodule) has a `.git` **file**, not a folder: it points to its repository's git folder
somewhere else. Inside keel's container that path did not exist, so git said "not a git repository".

```
 ~/Projects/shop/.git/                  ← the repository's git folder
 ~/Projects/shop-feature/.git (a file)  → "gitdir: ~/Projects/shop/.git/worktrees/shop-feature"
        │
 keel2 start / add ─ mounts both, at the same path as on your computer ─ git works in the container
```

- From 0.18.17, `keel2 start` and `keel2 add` also mount the repository's git folder, at the same path. keel2 says so:
  "… is a git worktree or submodule: its repository … is mounted too".
- Get the new `keel2` script for this (`curl -fsSL https://raw.githubusercontent.com/MiladNalbandi/keel-v2/main/install.sh | bash`),
  then `keel2 restart`.
- When keel still cannot see the repository, the message says which folder keel checked, what git said (a folder keel
  cannot see, git's "dubious ownership"…) and the fix.
- Native mode needs nothing: keel runs on your computer and sees every path.

## Problems

Run `keel2 doctor` first. The most common ones:

| You see | Do this |
|---|---|
| "Docker is not running" | start Docker Desktop (keel2 starts it on a Mac) |
| "not a git repository" | say yes when keel2 offers `git init`; for a git worktree, see [Git worktree projects](#git-worktree-projects) |
| port 8080 is busy | nothing: keel2 uses the next free port and tells you |
| an agent "is not logged in" | Settings › Models & logins › Set up login |
| your tests start containers | `keel2 start --docker <folder>` |
| "Sign in to keel" in the browser | `keel2 open` |
| copying in keel TUI does nothing (macOS Terminal) | `/mouse off`, then select |

All commands: `keel2 help` (logs, tokens, backup, restore, uninstall …).

## Folders in the container

| Folder | What |
|---|---|
| `/workspace` | your project (one git repo, or a folder of repos); its real path with `--docker` |
| the same path as on your computer | each folder added with `keel2 add`, and a worktree's repository git folder |
| `/data` | keel's database, flow checkpoints, encrypted logins, `prices.json` |
| `/opt/keel-v2/content` | keel v2's agents, skills, stacks, packs, templates and workflows |
| `/opt/keel-v2/cli` | keel TUI (`keel-tui.mjs`) |
| `/opt/keel-v1-optional` | only with `--with-keel-v1`: that keel v1 checkout, read-only, for its MCP server |

## Without the install script

```bash
docker run -d --name keel-v2 -p 127.0.0.1:8080:8080 \
  -v /path/to/your/project:/workspace -v keel-data:/data \
  ghcr.io/miladnalbandi/keel-v2
```

## Develop keel v2

| Part | Tech | Run | Tests |
|---|---|---|---|
| `engine/` | Python 3.12, FastAPI, LangGraph, LangChain, MCP | `uv run keel-engine` (:8090) | `uv run pytest` |
| `api/` | Kotlin, Spring Boot 3, SQLite, Flyway | `./gradlew bootRun` (:8080) | `./gradlew test` |
| `web/` | React, TypeScript, Vite | `npm run dev` (:5173) | `npm test` |
| `cli/` | keel TUI: TypeScript, Ink, esbuild | `npm run build`, then `KEEL_TUI_JS=cli/dist/keel-tui.mjs keel2 tui` | `npm test` |

How the parts talk: [CONTRACT.md](CONTRACT.md).

```bash
docker build -t keel-v2 .                                 # everything is installed in the image
docker build --build-arg INSTALL_CLIS=0 -t keel-v2:slim . # without the agent CLIs
npx -y -p playwright@1 node docs/gif/record.js            # record the README's GIF from a running keel
scripts/no-v1.sh                                          # lists anything that still depends on keel v1 (CI: 0)
```

A version tag (`v*`) builds amd64 + arm64 images (on GitHub's Intel and ARM machines at the same time) and pushes them
to `ghcr.io/miladnalbandi/keel-v2` (and to Docker Hub when the `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets are
set). Dependabot (`.github/dependabot.yml`) opens a pull request every week for each part's dependencies, the Docker
base images and the GitHub Actions.
