# keel as the agent's toolkit: research and plan

Research for [#234](https://github.com/MiladNalbandi/keel-v2/issues/234), 2026-11-25. Nothing here is built; this is a
plan to decide on. Revised 2026-11-26 after a review, and for keel 0.41.1.

**The question.** How should keel answer an agent's "how do we do X in this project?" (commit style, branch names,
tests, code style, where things live, who works on what), and which flows should do the repeated work, so that an
agent with keel is smarter and cheaper than one without, and several agents on one keel are more efficient together?

## Answer in short

1. **Grow `keel_commit_style` into one tool, `keel_howto(topic)`.** Since 0.41.0 keel answers the commit question
   over MCP, but thinly: no scope, issue number, trailers, source or check. Branch names, test commands, code style
   and PR rules are still only used inside keel's own flows.
2. **Answer from facts, not from a model.** Read configs, linters, CI and git history; name the source and how sure
   keel is. Written rules from a person win over anything detected. This works on a new project with no model key.
3. **Give agents a small tool set.** `--write` lists 131 tools, about 28k tokens in a client that loads them all. An
   `--agent` set of 12 tools is about a tenth of that. Clients that load tools on demand, like Claude Code, gain less.
4. **For several agents: show, do not control.** One shared memory that agents can write to (with citations), a
   "who works on what" view, and advisory claims that expire. No locks that block.

How sure: **high** on 1 (keel's own test report and vendor guidance agree), **medium** on 2, 3 and 4 (good sources,
but nothing measured yet: there is no public measurement of a "conventions tool", and the token gain of 3 depends
on the client; see "Not found").

## 1. What the research says

### 1.1 How agents get project knowledge today

| Agent | Mechanism | Loaded when | Stated limit |
|---|---|---|---|
| Claude Code | `CLAUDE.md` tree, `.claude/rules` with `paths:`, auto memory, skills, `/init` | root files at launch, path rules and sub-folder files on demand | "target under 200 lines"; memory: first 200 lines or 25KB [S1] |
| Codex | `AGENTS.md` from root down to the working folder, skills | once per run | 32 KiB combined [S2] |
| Cursor | `.cursor/rules/*.mdc` (always / by description / by glob / manual), `AGENTS.md` | per rule type | "under 500 lines" [S3] |
| Copilot agent | `copilot-instructions.md`, `*.instructions.md` with `applyTo`, `AGENTS.md`, Copilot Memory | every request / on file match | memory facts expire after 90 days by default [S4] |
| Aider | repo map (ranked symbols), `CONVENTIONS.md` via `--read` | every request | map budget 1k tokens by default [S5] |
| Devin, OpenHands, Windsurf, Gemini CLI | `AGENTS.md` plus triggered knowledge, skills or rules | always-on or by trigger | "keep it concise" [S6] |

`AGENTS.md` is the common ground: "used by over 60k open-source projects", stewarded by the Agentic AI Foundation
under the Linux Foundation, read by Codex, Cursor, Copilot, Jules, Gemini CLI, Devin, Aider and others [S7].

**What works**

- Short, concrete instructions that the code cannot show. Agents follow them: in the ETH Zurich study a tool named in
  the file was used about 1.6 times per task, against under 0.01 when it was not named [S8].
- Loading on demand: path-scoped rules, skills, triggers. Anthropic's rule is "find the smallest set of high-signal
  tokens" and to fetch context "just in time" [S9].
- Enforcement outside the prompt: "If something has to happen every time, make it a hook" [S10].

**What fails**

- Context files as a general fix: "providing context files does not generally improve task success rates", while
  "increasing inference cost by over 20% on average". Repository overviews did not help agents find files. The files
  are "useful for specifying non-standard coding practices" [S8].
- Long files: "Longer files consume more context and reduce adherence"; `CLAUDE.md` is "context, not enforced
  configuration"; two rules that disagree may be picked "arbitrarily" [S1].
- Stale files: a study of 2,303 agent context files found they grow by additions and are rarely pruned [S11].
- Every agent re-reads the same files. In keel's own test, 7 of 8 agents read `docs/CONVENTIONS.md` themselves [K1].

### 1.2 MCP tools: what makes one easy for an agent

| Rule | Source |
|---|---|
| Few workflow tools, not one tool per API call: "More tools don't always lead to better outcomes." | [S12] |
| Group related operations behind one tool with a parameter (GitHub did this with a `method` parameter). | [S13] |
| Tool definitions cost context before any work: 58 tools were about 55k tokens; use tool search at 10+ tools or over 10k tokens. | [S14] |
| Ship a small default set: GitHub's default went from 101 tools (64.6k tokens) to 52 (30.3k), because "Most users never customize their configuration". | [S13] |
| Short, high-signal answers with a `concise` / `detailed` switch (72 against 206 tokens in Anthropic's example). | [S12] |
| Names with a prefix, parameters that cannot be misread (`user_id`, not `user`). | [S12] |
| Errors that say how to fix the call, returned as tool results (`isError`), so the model can retry. | [S15] |
| Use the server's `instructions` as the index: Claude Code loads only tool names and server instructions at session start and finds the rest on demand. | [S16] |
| Annotations (`readOnlyHint` …) are hints; clients treat them as untrusted. | [S15] |

Existing servers show the two shapes. **Context7** has 2 tools (find the library, then ask the docs) [S17].
**Serena** has 53 tools (29 on by default) with symbol tools, memories and an `onboarding` tool [S18]. The official
**git** server has 14 tools, one per git verb, and **memory** has 9 for a knowledge graph [S19]. None of the servers we
read answers "what is this project's commit style".

**What agents call.** Public traces say shell, read and edit are the bulk of all calls (not opened by us, search
summaries only) [S20]. So a conventions tool must be cheaper and more clearly useful than `cat CONTRIBUTING.md`:
one call, under about 150 tokens, with the exact answer.

### 1.3 Several agents on one repo

| Pattern | Who | What happened |
|---|---|---|
| One git worktree per agent | Claude Code, Cursor, Codex app | Standard for isolation. Each worktree needs its own install; merging comes later [S21]. |
| Lock files in git, no boss | Anthropic's C compiler (16 agents) | Worked for separate tasks. On one big task "every agent would hit the same bug" and they overwrote each other [S22]. |
| Shared task list with claims | Claude Code agent teams | "Task claiming uses file locking"; status "can lag"; "Two teammates editing the same file leads to overwrites" [S23]. |
| Flat shared lock file | Cursor research | Failed: "Agents would hold locks for too long, or forget to release them entirely." [S24] |
| Advisory file leases with a time limit | `mcp_agent_mail` | Reservation is granted and the conflict is returned with it; the lease expires (`ttl_seconds`) [S25]. |
| Shared memory with citations | Copilot Memory | Facts cite code and are checked before use: PR merge rate 90% with memory against 83% without [S26]. |

Lessons for a tool that does not control agents:

- Claims need a **time limit** and must be **advisory** (warn, do not block) [S24][S25].
- **Derive status from git**, do not trust agents to update it [S23].
- **Share decisions and facts**, not only messages; give each fact a citation and check it when it is read [S26].
- More agents cost more tokens; they pay off only when the work is truly separate [S23]. keel's own run agrees:
  3 agents through keel took 7.6 min and 90k tokens, 3 plain agents about 25 s and 60k, and one plain agent doing
  all three tasks 17 s and 20k. About 4–5 of the 7.6 minutes were lost to one bug (#217) [K1].

### 1.4 Detected or written by people?

| Convention | Signals keel can read | Reliability (our judgement) |
|---|---|---|
| Commit format | commitlint / commitizen / release config, commit hooks, then `git log` | config: high; history: medium |
| Trailers (co-author, sign-off) | `git log` bodies, hooks | medium |
| Branch names | keel's `branch_pattern`, existing branches, CI branch filters | medium |
| Test, lint, build commands | keel's project settings, stacks, `package.json`, `Makefile`, CI YAML | high (CI is what really runs) |
| Code style | `.editorconfig`, linter and formatter configs | high when a config exists |
| PR rules | PR template, `CONTRIBUTING.md`, recent merged PR titles | medium |
| "We never do X here" | nothing: a person must write it | – |

The evidence points one way. In the ETH study developer-written files did better than generated ones (the paper says
by 7% on average, though neither moved success much), and generated ones helped only when no other docs existed
[S8]. And: "Never send an LLM to do a linter's job" [S27]. So: **detect from files that
machines enforce, cite the file, and let a person's written rule override everything.**

### 1.5 Tools that already serve conventions

| Tool | What it does | Detected or written | Source |
|---|---|---|---|
| Packmind (Apache-2.0) | Turns a team playbook into the files each agent reads (`CLAUDE.md`, `.cursor/rules`, `AGENTS.md`); an onboarding command drafts first standards from the codebase and people approve them | written, with a detected first draft | [S29] |
| Ruler | One `.ruler/` folder, copied into each agent's own file (`ruler apply`) | written | [S30] |
| Central "standards" MCP servers | One hosted server holds the organisation's standards as Markdown; agents fetch them on request. The reason given: with files, "AI ignored the standards entirely" and "Updating one standard meant chasing down 15 copies across repos". No measured result is given | written | [S31] |
| Commit-message MCP servers (for example `generate_commit_message`, `git_commit_and_push`) | Write the message for the agent from the diff, in one fixed Conventional Commits form | neither: a fixed format | [S32] |

The existing tools either copy written rules into files, or write commits in one fixed format. In this search we
found none that answers "what is *this* repo's style?" from the repo's own configs and history, with a source and a
confidence (one search summary names a commit tool that adapts to the history; not opened). keel's
`keel_commit_style` (0.41.0) is a first step into that gap; `keel_howto` is the rest.

## 2. keel today (0.41.1)

### What keel already has

| Need | In keel today | Where |
|---|---|---|
| Commit style for agents | `keel_commit_style` (0.41.0, [#237](https://github.com/MiladNalbandi/keel-v2/issues/237)): the configured `git.commit_template`, else the style at least half of the last 20 human commits share (ticket or not), plus 8 recent subjects. keel's own commit step uses a narrower rule on purpose (`infer`: ticket styles only, so its subjects keep the criterion id) | `runtime/commit_style.py` (`for_agent`, `team_style`, `infer`), `mcp_more.py` |
| Branch names | `branch_pattern` (default `feat/{slug}`, `fix/` for fixes) | `runtime/bug.py`, api `FlowService` |
| Test and lint commands | The project's keel settings (`commands:`, `tools:`; today in `.keel/config.yml`) and stacks | `keel_stacks`, `keel_stack` |
| The repo's own rule files | Finds `AGENTS.md`, `CLAUDE.md`, Copilot / Cursor / Windsurf rules and skills at init and refresh | `runtime/project_rules.py` |
| Knowledge pages | Six sections with `path:line` citations, written by a model in init / knowledge-refresh; opt-in | `keel_knowledge`, `content/templates/knowledge/` |
| Facts | A `facts` table with REST create / change / delete | api `KnowledgeService`; no MCP write tool |
| Isolation | A worktree per parallel flow; "one owner per task" refuses a start on the same ticket, branch or files | `docs/GUIDE.md` |
| Phase rules | What may be edited and committed in each phase | `keel_explain`, `keel_rules` |
| Tool levels | read / write / admin and `--allow t1,t2` | `mcp_levels.py` |

### What is missing

- **Only the commit question has a tool.** Branch names, test commands, code style, PR rules and "where does this go"
  are still only used inside keel's own flows. `keel_git_commit(message)` takes any message as it is.
- **The commit answer is thin.** On keel-v2 itself `keel_commit_style` answers `{type}: {subject}` and 8 subjects. The
  scope (`fix(mcp): …`) and the issue number (`… (#220)`) are lost in the style line. It gives no count and no
  confidence, says nothing about trailers (co-author, sign-off), reads no tool config (commitlint), and cannot check
  a draft.
- **Knowledge needs a model.** `keel_knowledge` is empty on a new project, and agents cannot add to it [K1].
- **Agents cannot see each other.** `keel_live` shows no connected sessions ([#219](https://github.com/MiladNalbandi/keel-v2/issues/219));
  work orders go to the wrong agent ([#217](https://github.com/MiladNalbandi/keel-v2/issues/217)).
- **No on-demand test run** with the command keel itself uses [K1].

### What is too much

- **131 tools at `--write`, about 28k tokens** if a client loads them all; 69 at read, about 10k [K1]. That is over
  the "10+ tools or 10k tokens" line where vendors say to load tools on demand [S14].
- **Person's tools in the agent's list**: 37 of the 141 tools are marked "person's tool" in the test report (gates,
  settings, projects, models, marketplace) and only 48 "helps build" [K1].
- **Long descriptions**: `keel_start_flow` is 2.8k characters [K1].
- **Several ways to do one thing**: `keel_approve_gate` / `keel_resume` / `keel_answer` / `keel_resume_flow`;
  `keel_read_file` / `keel_read_project_file`. A `keel_howto` next to `keel_commit_style` would add one more, so the
  plan replaces the old tool.
- **Gates on tiny tasks**: minutes with keel against seconds without [K1].

### A warning from keel's own numbers

keel measured help that the agent did not ask for. On 2026-10-06, pre-fetched "where to look" hints from the code
graph saved no tokens (153k with, 150k without) and the answers named the same code, so hints are off by default
(`runtime/agent_knowledge.py`). Two lessons for this plan: answer only when the agent asks, and measure before
calling anything a gain.

## 3. Ranked features

Effort: S = days, M = about a week, L = more. "Gain" is what the agent saves or gets right. The example answers
below are illustrations: their counts, file names and line numbers are made up.

Order: #1 first because the commit question was asked for first, and #2 next because it is the same tool. #3 and #4
are small and can be built before #2. #5 to #7 need more new code, and #6 needs two bugs fixed first.

| # | Feature | Gain for the agent | Effort |
|---|---|---|---|
| 1 | `keel_howto(topic="commit")`, which replaces `keel_commit_style` | Scope, issue number and trailers right on the first try; a draft can be checked; the answer says where it comes from | S |
| 2 | `keel_howto` topics `branch`, `test`, `style`, `pr`, `where` | One tool for every "how do we do X here" | M |
| 3 | `keel2 mcp --agent` (12 tools) and shorter descriptions | Up to about 25k fewer tokens per session in a client that loads every tool; a shorter list to choose from in the others | S |
| 4 | `keel_brief()` | One call replaces looking for `AGENTS.md`, `CONTRIBUTING`, the CI file and the test command | S |
| 5 | `keel_remember(fact, cite)` and recall inside `howto` / `brief` | What one agent learns, every later agent knows | M |
| 6 | `keel_who()` and advisory `keel_claim(paths, ttl)` | No two agents on the same files; no repeated work | M |
| 7 | `keel_test(scope)` | Tests run with keel's own command; short failing output | M |
| 8 | Export to `AGENTS.md` (only on request) | Agents without MCP get the same answers | S |

### Where the data lives

Since 2026-11-26 keel's rule is: by default keel creates no `.keel` folder and changes nothing in the user's project;
its config, rules and state live in keel's own storage ([#246](https://github.com/MiladNalbandi/keel-v2/issues/246)).
This plan follows it:

| Data | Where | New storage work |
|---|---|---|
| `keel_howto` and `keel_brief` answers | Nowhere: computed on each call from the project's files and git history (read only) | none |
| A person's written rule (for example `git.commit_template`) | keel's project settings, wherever #246 puts them | none here; it comes with #246 |
| Remembered facts | The api's `facts` table, which exists | an MCP tool and the citation check |
| Claims | A new api table and REST route | yes, counted in #6's M |

The rule makes the MCP answer more important: with no keel files in the repo, asking keel is how an agent gets what
keel knows.

### 1. `keel_howto(topic="commit")` (first)

```text
call    keel_howto(topic="commit")

answer  commit style (keel-v2)
        format    type(scope): subject (#issue)     scope: 9 of 50 · (#issue): 31 of 50, both optional
        types     feat, fix, docs, test, chore
        subject   lower case, no full stop, under 72 characters
        trailers  none: no Co-Authored-By or Signed-off-by line in the last 50 commits
        examples  "fix(mcp): keel_flow_doctor reads /flow-doctor/report, not /api/api/... (#220)"
                  "chore: version 0.40.1 (keel_flow_doctor fix, MCP test report)"
        source    47 of the last 50 commits; no git.commit_template, no commitlint config · sure: high
        see also  CONTRIBUTING.md:31 "Commits" (keel points to it, it does not parse it)
        check     keel_howto(topic="commit", check="<your message>")
```

```text
call    keel_howto(topic="commit", check="Added new feature.")
answer  not in the project's style: no type prefix; starts with a capital; ends with a full stop.
        template: "feat: add <what>"
```

The check only informs. keel refuses nothing here, and the agent commits as it likes.

On a project with nothing to learn from: `no commit convention found (3 commits, no config). Suggest: Conventional
Commits "type: subject".` An honest "not found" is a useful answer.

### 2. More topics, same tool

```text
keel_howto(topic="branch")  →  pattern feat/{slug}, fix/{slug} · base main · ticket in the name: no
                               source: keel settings + 18 of 20 remote branches · sure: medium
keel_howto(topic="test")    →  all: cd engine && uv run pytest -q · one test: … -k <name>
                               needs: uv on PATH · source: keel settings + .github/workflows/ci.yml · sure: high
keel_howto(topic="style")   →  formatter ruff (pyproject.toml), line 128 · run: uv run ruff format <files>
keel_howto(topic="pr")      →  title like the commit · body must say "Closes #N" · base main · source: CONTRIBUTING.md:31
keel_howto(topic="where", about="a new MCP tool")
                            →  engine/keel_engine/mcp_server.py (register) + mcp_levels.py TOOLS (level)
                               source: knowledge/architecture.md + code graph · sure: medium
```

One tool with a `topic` list follows the "group related operations" rule [S13]. Unknown topic: the answer lists the
topics. The `where` topic needs the architecture page (written by a model) or the code graph, so it comes last and
only if a test shows it saves reads (see the warning in section 2).

### 3. A small agent set

`keel2 mcp --agent` is a named list of 12 tools, used inside the level you start it with:

- read level: `keel_brief`, `keel_howto`, `keel_status`, `keel_read_gate`, `keel_search`, `keel_flows`
- with `--write`: also `keel_start_flow` and the five `keel_session_*` tools

`keel_remember`, `keel_who` and `keel_test` join the list when they are built; `keel_who` then takes the place of
`keel_flows`, which today is the only tool where agents see each other's flows [K1]. The server's `instructions` say
when to ask: "Call `keel_brief` once at the start. Call `keel_howto` before you commit, name a branch, run tests or
open a PR." Also cut `keel_start_flow`'s description to a few lines; the long catalog is already what the call
without a type returns.

Why a name and not `--allow t1,t2`: one word people can copy, kept up to date by keel as tools land, and shown in
`--print-config` and Help. A hand-written list of 12 names goes stale.

How much it saves depends on the client. One that loads every tool definition pays about 28k tokens for `--write`
today [K1]. Claude Code loads only tool names and server instructions first and finds the rest on demand [S16], so
there the gain is a shorter list and fewer wrong picks, not 25k tokens.

### 4. `keel_brief()`

```text
shop · Kotlin api (Gradle), React web (Vite) · base main
rules for agents   AGENTS.md, CONTRIBUTING.md, .cursor/rules/testing.mdc
test               api: ./gradlew test · web: npm test -- --run          (keel_howto topic=test has more)
right now          2 flows: feat/euro-prices (green, AC-2), fix/rounding (review)
ask                keel_howto before you commit, name a branch, run tests or open a PR
```

Each line has a source, and none needs a model:

| Line | From | Before init? |
|---|---|---|
| stack, base branch | stack detection from the project's files, git | yes |
| rule files for agents | `project_rules.discover` (titles and paths only) | yes |
| test commands | keel's project settings (`commands:`) | no: before init the brief points to the CI file instead |
| right now | the flow list (`keel_flows` data); agents and files after [#219](https://github.com/MiladNalbandi/keel-v2/issues/219) | yes |

There is no "where things live" line in the first version. It would need the architecture page, which a model
writes, and it is the kind of overview that did not help in the ETH study [S8] or in keel's own hints test. The brief
is commands and pointers, not a description of the code.

### 5. Shared memory agents can write to

```text
call    keel_remember(fact="web tests need Node 20; Node 26 breaks localStorage in jsdom",
                      topic="test", cite="web/package.json:7")
answer  saved (fact 14, by claude-code, checked: the citation exists). Shown in keel_howto(topic="test").
```

Rules taken from Copilot Memory: a fact needs a citation or is marked `unverified`; keel checks the citation when it
shows the fact (keel already has a citation check) [S26]; facts not confirmed for 90 days drop out, which is
Copilot's default [S4]; a person can edit or delete them on the Knowledge page. The `facts` table and its REST routes
exist; only the MCP tool and the checks are new.

### 6. Who works on what, and advisory claims

```text
call    keel_who()
answer  a1 claude-code · flow feat/safety-only (green, AC-2) · worktree …/safety-only · files engine/keel_engine/rules/*
        b2 codex       · flow fix/doctor-404 (review)       · files engine/keel_engine/mcp_diag.py
        claims: b2 holds web/src/pages/Flow* until 15:40 ("flow page refactor")

call    keel_claim(paths=["engine/keel_engine/mcp_*.py"], minutes=60, why="howto tool")
answer  claimed until 16:05. Note: b2's flow changed mcp_diag.py 4 min ago (not blocked; talk to b2 or pick other files).
```

Status comes from git and the flows, not from what agents report. A claim only warns, always expires, and ends with
its flow. This needs [#217](https://github.com/MiladNalbandi/keel-v2/issues/217) and
[#219](https://github.com/MiladNalbandi/keel-v2/issues/219) fixed first.

### 7. `keel_test(scope)`

```text
call    keel_test(scope="changed")
answer  uv run pytest -q tests/test_mcp.py  ·  1 failed, 41 passed (3.2 s)
        FAILED tests/test_mcp.py::test_howto_commit - assert 'feat' in ''
```

### 8. Export

`keel2 export agents-md` is the one part of this plan that writes into the repo, so it runs only when the person
asks for it and is off by default. It writes a short, marked block into `AGENTS.md` (commands and non-standard rules
only, no overview, under 60 lines), so agents that never connect still get the answers. The ETH study says
instructions are followed and overviews are not worth their tokens [S8]. To decide: #246 allows an opt-in export of
keel's config; a block inside the user's own `AGENTS.md` is a bigger edit than that and needs its own yes.

## 4. Flows: what to use and what to change

| Flow | Change | Why |
|---|---|---|
| **init** | Its hand-over shows the `keel_howto` answers (commit, branch, test). The person corrects one once, and the correction is saved as a written rule in keel's settings | A written rule beats a detected one; no model key is needed for any of it |
| **knowledge-refresh** | Check every remembered fact's citation; list `keel_howto` answers that changed since the last refresh | Stops stale answers, the main failure of written files [S11] |
| **quick / implement / harness (session)** | For keel's own flow agents: put the 5-line brief in the work order instead of long standing text, and point to `keel_howto` | keel agents used 26–32k tokens against about 20k plain; the work-order text is most of the gap [K1] |
| **ship** | Use the `pr` answer for the PR title and body (also fixes "no `Closes #N`", [#207](https://github.com/MiladNalbandi/keel-v2/issues/207)); show commit subjects that do not fit the style as a note at the gate, never as a refusal | The agent and keel's ship step read the same answer |
| **review / review-pr** | Give the reviewers the `howto` answers as the rule list | Reviewers stop guessing the conventions |
| **after parallel flows** | A hint in `keel_status` / `keel_who`: "3 finished branches; `keel_branch_sync` merges them in order" | The test's parallel run ended with a CHANGELOG conflict [K1] |
| **quick** for tiny tasks | Nothing: it already has one gate. Only [#218](https://github.com/MiladNalbandi/keel-v2/issues/218) (the wait after a finished flow) costs time | Minutes against seconds [K1] |

No new flow is needed. The heavy, repeated work (finding the rules, running the tests, bringing branches together)
moves into steps keel already runs.

## 5. First spec suggestions

### Spec A: `keel_howto`, topic `commit`

**Context.** keel 0.41.0 has `keel_commit_style` (#237). It gives the configured template or the style most recent
commits share, and 8 recent subjects. It loses the scope and the issue number, has no count, confidence or trailers,
reads no commitlint config, cannot check a draft, and covers only commits.

**Goal.** `keel_howto(topic="commit")` replaces it: one call, at most 1,000 characters, that tells a connected agent
the full commit form of this project and where the answer comes from, and can check a draft.

**Acceptance criteria**

1. `keel_howto(topic="commit")` answers at the read level, with no model, in under 300 ms on a repo with 10k commits.
   It computes the answer on each call from the last 50 human, non-merge commits (keel's own commits are left out,
   as today). Nothing is cached or stored.
2. The answer has: format, types seen, subject rules, trailers, two real examples, source, and
   `sure: high | medium | low`. It is at most 1,000 characters; a test measures it.
3. Order of sources: the person's `git.commit_template` setting; then tool config that enforces a format
   (commitlint, commitizen, a commit-msg hook: named, and its rules shown when keel can read them, such as
   commitlint's `type-enum`); then history.
4. Agent docs (`AGENTS.md`, `CONTRIBUTING.md`) are not parsed for rules. When one has a heading with "commit" in it,
   the answer points to the file and line.
5. History gives a style only when at least 60% of the commits share it; else the answer says "mixed" and shows the
   two most common forms.
6. The style keeps what today's answer loses: an optional scope (`type(scope):`) and an issue or ticket reference at
   the end (`(#220)`), each with how many of the commits have it.
7. Trailers: the answer says whether co-author and sign-off lines are used, from the bodies of the same commits.
8. `check="<message>"` returns "ok", or each rule the message breaks and a template with placeholders
   (`feat: add <what>`). It only informs: nothing is refused and `keel_git_commit` does not change.
9. No convention found: the answer says so and suggests one; it never invents a rule.
10. An unknown topic lists the topics. Tool errors say how to fix the call.
11. `keel_commit_style` is removed in the same change (tool list, README and GUIDE), so there is one way to ask.
12. keel's own commit step does not change: `infer` and the `test(AC-2): …` subjects stay, and a test pins them. The
    two views differ on purpose, because the trace and the audit read keel's form.
13. After the call, a clean project folder has no new file (the test of #246).

**Design.** A new `runtime/conventions.py` with `commit(root, cfg) -> dict`, built on `commit_style.recent_subjects`
and `_style_of` (extended for the scope and the trailing reference). The engine's read action `commit_style` becomes
`howto`; `mcp_more.howto()` formats the dict.

**Out of scope.** Making keel's own commits follow the team's style through the `Keel-Ref` trailer. It touches the
trace and the audit and needs its own decision.

**Test plan.** Unit tests on fixture repos: Conventional Commits with and without scope, issue references, ticket
prefix, mixed history, empty repo, commitlint config, a `git.commit_template` that disagrees with history (the
setting wins). One MCP test for the call and the `check` path, one for the 1,000 characters, one that keel's own
subjects are unchanged, and the clean-folder check.

### Spec B: the agent tool set and `keel_brief`

**Context.** `--write` lists 131 tools, about 114 KB of schema [K1]; most are for the person. An agent also has no
single call that names the project's rule files and commands.

**Goal.** An agent connects with 12 tools and gets the project's commands and rule files in one call.

**Acceptance criteria**

1. `--agent` is a named list in `mcp_levels.py`, applied inside the level. `keel2 mcp --agent` (read) registers
   `keel_brief`, `keel_howto`, `keel_status`, `keel_read_gate`, `keel_search`, `keel_flows`. `keel2 mcp --write
   --agent` adds `keel_start_flow` and the five `keel_session_*` tools. `--read-only` still wins over everything.
2. `--print-config` and Help show the `--agent` lines. `--allow` and the read, write and admin levels work as before.
3. The 12 definitions together are under 12 KB of schema (about 3k tokens by the bytes / 4 rule the test report
   used); a test fails when they grow past that.
4. The server `instructions` name `keel_brief` and `keel_howto` and when to call them, in under 400 characters.
5. No description in the agent set is longer than 600 characters.
6. `keel_brief()` answers with no model, in at most 60 lines, and every line comes from a source in the table of
   section 3.4.
7. Before init it shows: stack, base branch, rule files, flows, and a pointer to the CI file in place of test
   commands. After init it adds the test commands from keel's settings.
8. It has no "where things live" text.
9. After the call, a clean project folder has no new file.

**Not in this spec.** `keel_remember`, `keel_who` and `keel_test`; they join the list when they are built.

**Test plan.** A unit test for the two lists and the size budget; `keel_brief` on a bare repo, an initialised repo
and a repo with running flows; one run with a real client to confirm the smaller list.

**How to judge both.** Repeat the three-task run from the 0.40.0 report [K1], three keel agents with and without the
two tools, once in a client that loads every tool definition and once in Claude Code. Compare tokens, wall time and
the hidden checks. If the agents stop reading `docs/CONVENTIONS.md` themselves and use fewer tokens, it works. If
not, stop here.

## 6. Not found, and guesses

- **No public numbers on MCP tool usage per server or tool**, and no measurement of a "conventions over MCP" tool. The
  expected gain is our inference from the sources above, not a measured fact.
- **The token gain of the small tool set is not measured.** The 28k is an estimate from the schema size, and the
  agents in keel's test did not load the tool list at all [K1].
- **No measured evidence that agents break commit conventions** in particular. A study of 33k agent PRs names CI
  failures, duplicates and "agent misalignment" as rejection reasons (abstract only) [S28].
- **Not opened:** Cursor / Codex / Windsurf support for MCP resources and prompts; Cursor's tool limit; Beads' Gas
  Town; mem0, Letta, Zep; convention-mining papers.
- **Read through a summarising fetch tool:** most web quotes. A second reader checked [S8] (full text), [S13], [S14],
  [S24] and [S26] against the pages; spot-check any other quote before it is used in public text.
- **Guess:** the 60% threshold, the 1,000 characters and the 12 KB budget are starting values, to be tuned in the test
  run.
- keel has a feature freeze since 2026-11-18; everything here is new features, so it needs a decision first.

## Sources

Web sources were read on 2026-11-24 and 2026-11-25. "older" = more than a year old.

- [K1] keel 0.40.0 MCP test report, `docs/test-report-mcp-0.40.0.md` (2026-11-24)
- [S1] Claude Code docs, memory: https://code.claude.com/docs/en/memory (undated, current)
- [S2] Codex docs, AGENTS.md: https://learn.chatgpt.com/docs/agent-configuration/agents-md (undated)
- [S3] Cursor docs, rules: https://cursor.com/docs/context/rules (undated)
- [S4] GitHub docs, repository instructions and Copilot Memory:
  https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions ,
  https://docs.github.com/en/copilot/concepts/agents/copilot-memory (undated)
- [S5] Aider docs: https://aider.chat/docs/repomap.html , https://aider.chat/docs/usage/conventions.html (undated, likely older)
- [S6] https://docs.devin.ai/product-guides/knowledge , https://docs.openhands.dev/overview/skills ,
  https://docs.devin.ai/desktop/cascade/memories , https://geminicli.com/docs/cli/gemini-md/ (2026-06-18)
- [S7] AGENTS.md: https://agents.md/ (undated)
- [S8] Gloaguen et al., "Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?",
  https://arxiv.org/abs/2602.11988 (v1 2026-02-12, v3 2026-09-29)
- [S9] Anthropic, "Effective context engineering for AI agents":
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents (2025-09-29, older)
- [S10] Claude Code docs, best practices: https://code.claude.com/docs/en/best-practices (undated)
- [S11] "Agent READMEs: An Empirical Study of Context Files for Agentic Coding": https://arxiv.org/html/2511.12884v1 (2025-11-17, older)
- [S12] Anthropic, "Writing effective tools for agents": https://www.anthropic.com/engineering/writing-tools-for-agents (2025-09-11, older)
- [S13] GitHub MCP server: https://github.com/github/github-mcp-server/discussions/1182 (2025-10-06, older),
  https://github.blog/changelog/2025-10-29-github-mcp-server-now-comes-with-server-instructions-better-tools-and-more/ (older);
  Claude docs, define tools: https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools (undated)
- [S14] Anthropic, "Advanced tool use": https://www.anthropic.com/engineering/advanced-tool-use (2025-11-24, older)
- [S15] MCP specification, tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools (read by us);
  a newer version 2026-07-28 exists: https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- [S16] Claude Code docs, MCP: https://code.claude.com/docs/en/mcp (undated)
- [S17] Context7: https://github.com/upstash/context7 (undated)
- [S18] Serena tools: https://oraios.github.io/serena/01-about/035_tools.html (undated)
- [S19] MCP reference servers: https://github.com/modelcontextprotocol/servers/tree/main/src/git ,
  https://github.com/modelcontextprotocol/servers/tree/main/src/memory (undated)
- [S20] SWE-chat, https://arxiv.org/pdf/2604.20779 (2026); UW TraceLab,
  https://tracelab.cs.washington.edu/exp/tool_calls/tool_call_counts (search summaries only, not opened)
- [S21] Worktrees: https://code.claude.com/docs/en/worktrees.md , https://cursor.com/docs/configuration/worktrees ,
  https://learn.chatgpt.com/docs/environments/git-worktrees (undated)
- [S22] Anthropic, "Building a C compiler with a team of parallel Claudes":
  https://www.anthropic.com/engineering/building-c-compiler (2026-02-05)
- [S23] Claude Code docs, agent teams: https://code.claude.com/docs/en/agent-teams (undated, experimental feature)
- [S24] Cursor, "Scaling long-running autonomous coding": https://cursor.com/blog/scaling-agents (2026-01-14)
- [S25] mcp_agent_mail: https://github.com/Dicklesworthstone/mcp_agent_mail (undated; first two thirds of the README read)
- [S26] GitHub, "Building an agentic memory system for GitHub Copilot":
  https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/ (January 2026)
- [S27] HumanLayer, "Writing a good CLAUDE.md": https://www.humanlayer.dev/blog/writing-a-good-claude-md (2025-11-25, one year old)
- [S28] Study of agent-authored pull requests: https://arxiv.org/abs/2601.15195 (2026-01-21, abstract only)
- [S29] Packmind: https://github.com/PackmindHub/packmind (undated; its MCP docs page returned 404)
- [S30] Ruler: https://github.com/intellectronica/ruler (search summary only, not opened)
- [S31] Turing, "Solving AI coding chaos with MCP": https://www.turing.com/blog/solving-ai-coding-chaos-with-mcp (2025-11-12, older);
  Corbat coding-standards-mcp: https://socket.dev/npm/package/@corbat-tech/coding-standards-mcp (search summary only)
- [S32] git-mcp-server: https://github.com/surajfale/git-mcp-server (undated, a small project)
