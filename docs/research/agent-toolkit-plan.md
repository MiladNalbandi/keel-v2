# keel as the agent's toolkit: research and plan

Research for [#234](https://github.com/MiladNalbandi/keel-v2/issues/234), 2026-11-25. Nothing here is built; this is a
plan to decide on.

**The question.** How should keel answer an agent's "how do we do X in this project?" (commit style, branch names,
tests, code style, where things live, who works on what), and which flows should do the repeated work, so that an
agent with keel is smarter and cheaper than one without, and several agents on one keel are more efficient together?

## Answer in short

1. **Add one small tool, `keel_howto(topic)`, and make `commit` its first topic.** keel already knows most answers
   (commit style, branch pattern, test commands, the repo's rule files) but only uses them inside its own flows. No
   MCP tool gives them to a connected agent.
2. **Answer from facts, not from a model.** Read configs, linters, CI and git history; name the source and how sure
   keel is. Written rules from a person win over anything detected. This works on a new project with no model key.
3. **Give agents a small tool set.** `--write` lists 131 tools, about 28k tokens. An `--agent` set of about 10 tools
   costs about a tenth of that.
4. **For several agents: show, do not control.** One shared memory that agents can write to (with citations), a
   "who works on what" view, and advisory claims that expire. No locks that block.

How sure: **high** on 1 and 3 (keel's own test report and vendor guidance agree), **medium** on 2 and 4 (good
sources, but no public measurement of a "conventions tool" as such; see "Not found").

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
  3 agents through keel took 7.6 min and 90k tokens, one plain agent 17 s and 20k tokens [K1].

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
confidence (one search summary names a commit tool that adapts to the history; not opened). That is the gap
`keel_howto` fills.

## 2. keel today

### What keel already has

| Need | In keel today | Where |
|---|---|---|
| Commit style | A template (`git.commit_template`) or a style inferred from the last 20 human commits, but only when they share a ticket prefix; used for keel's own commits only | `engine/keel_engine/runtime/commit_style.py` |
| Branch names | `branch_pattern` (default `feat/{slug}`, `fix/` for fixes) | `runtime/bug.py`, api `FlowService` |
| Test and lint commands | The project's keel settings (`commands:`, `tools:`; today in `.keel/config.yml`) and stacks | `keel_stacks`, `keel_stack` |
| The repo's own rule files | Finds `AGENTS.md`, `CLAUDE.md`, Copilot / Cursor / Windsurf rules and skills at init and refresh | `runtime/project_rules.py` |
| Knowledge pages | Six sections with `path:line` citations, written by a model in init / knowledge-refresh; opt-in | `keel_knowledge`, `content/templates/knowledge/` |
| Facts | A `facts` table with REST create / change / delete | api `KnowledgeService`; no MCP write tool |
| Isolation | A worktree per parallel flow; "one owner per task" refuses a start on the same ticket, branch or files | `docs/GUIDE.md` |
| Phase rules | What may be edited and committed in each phase | `keel_explain`, `keel_rules` |
| Tool levels | read / write / admin and `--allow t1,t2` | `mcp_levels.py` |

### What is missing

- **No tool answers a convention question.** The commit style lives in code that only keel's own commit step calls.
  `keel_git_commit(message)` takes any message as it is.
- **The inferred style is narrow.** It is used only with a ticket prefix. A repo like keel itself
  (`fix(mcp): … (#220)`, no co-author line) gets no answer.
- **Knowledge needs a model.** `keel_knowledge` is empty on a new project, and agents cannot add to it [K1].
- **Agents cannot see each other.** `keel_live` shows no connected sessions ([#219](https://github.com/MiladNalbandi/keel-v2/issues/219));
  work orders go to the wrong agent ([#217](https://github.com/MiladNalbandi/keel-v2/issues/217)).
- **No on-demand test run** with the command keel itself uses [K1].

### What is too much

- **131 tools at `--write`, about 28k tokens**; 69 at read, about 10k [K1]. That is over the "10+ tools or 10k
  tokens" line where vendors say to load tools on demand [S14].
- **Person's tools in the agent's list**: 37 of the 141 tools are marked "person's tool" in the test report (gates,
  settings, projects, models, marketplace) and only 48 "helps build" [K1].
- **Long descriptions**: `keel_start_flow` is 2.8k characters [K1].
- **Several ways to do one thing**: `keel_approve_gate` / `keel_resume` / `keel_answer` / `keel_resume_flow`;
  `keel_read_file` / `keel_read_project_file`.
- **Gates on tiny tasks**: minutes with keel against seconds without [K1].

## 3. Ranked features

Effort: S = days, M = about a week, L = more. "Gain" is what the agent saves or gets right. The example answers
below are illustrations: their counts, file names and line numbers are made up.

| # | Feature | Gain for the agent | Effort |
|---|---|---|---|
| 1 | `keel_howto(topic="commit")` | Commits in the project's style on the first try; no reading of docs or `git log` | S |
| 2 | `keel_howto` topics `branch`, `test`, `style`, `pr`, `where` | One tool for every "how do we do X here" | M |
| 3 | `keel2 mcp --agent` (about 10 tools) and shorter descriptions | About 25k fewer tokens in every session | S |
| 4 | `keel_brief()` | One call replaces reading `AGENTS.md`, `CONTRIBUTING`, conventions and configs | S |
| 5 | `keel_remember(fact, cite)` and recall inside `howto` / `brief` | What one agent learns, every later agent knows | M |
| 6 | `keel_who()` and advisory `keel_claim(paths, ttl)` | No two agents on the same files; no repeated work | M |
| 7 | `keel_test(scope)` | Tests run with keel's own command; short failing output | M |
| 8 | Export to `AGENTS.md` | Agents without MCP get the same answers | S |

### Where the data lives

Since 2026-11-26 keel's rule is: by default keel creates no `.keel` folder and changes nothing in the user's project;
its config, rules and state live in keel's own storage ([#246](https://github.com/MiladNalbandi/keel-v2/issues/246)).
This plan follows it:

- keel only **reads** the project (configs, CI files, git history) to find the conventions.
- What it learns (the scan's answers, remembered facts, claims) is kept in keel's own storage, per project.
- The rule makes the MCP answer more important: with no keel files in the repo, asking keel is how an agent gets
  what keel knows.

### 1. `keel_howto(topic="commit")` (first)

```text
call    keel_howto(topic="commit")

answer  commit style (keel-v2)
        format    type(scope): subject (#issue)      e.g. fix(mcp): …  (#220)
        types     feat, fix, docs, test, chore       scope: optional, the part (engine, mcp, web)
        subject   lower case, no full stop, under 72 characters
        trailers  none: no Co-Authored-By line (written rule: AGENTS.md:12)
        examples  "fix(mcp): keel_flow_doctor reads /flow-doctor/report, not /api/api/... (#220)"
                  "chore: version 0.40.1 (keel_flow_doctor fix, MCP test report)"
        source    AGENTS.md (written) + 37 of the last 40 commits · sure: high
        check     keel_howto(topic="commit", check="<your message>")
```

```text
call    keel_howto(topic="commit", check="Added new feature.")
answer  not in the project's style: no type prefix; starts with a capital; ends with a full stop.
        try: "feat: add <what>"
```

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
topics.

### 3. A small agent set

`keel2 mcp --agent`: `keel_brief`, `keel_howto`, `keel_status`, `keel_read_gate`, `keel_search`, `keel_remember`,
`keel_who`, `keel_test`, `keel_start_flow`, and the five `keel_session_*` tools only inside a session flow. The
server's `instructions` say when to ask: "Call `keel_brief` once at the start. Call `keel_howto` before you commit,
name a branch, run tests or open a PR." Also cut `keel_start_flow`'s description to a few lines; the long catalog
is already what the call without a type returns.

### 4. `keel_brief()`

```text
keel-v2 · Python engine (uv), Kotlin api (Gradle), React web (Vite) · base main
rules for agents   AGENTS.md, CONTRIBUTING.md (keel_howto has the short answers)
test               engine: uv run pytest -q · web: npm test -- --run        (keel_howto topic=test)
where              engine/ flows and MCP · api/ REST · web/ dashboard · plugins/<name>/ · content/ workflows
right now          2 flows: feat/safety-only (agent a1, engine/…), fix/doctor-404 (agent b2, mcp_*.py)
facts              3 remembered (keel_howto and keel_search show them)
```

No model needed: `project_rules.discover`, the stacks, the config and the flow list already hold all of it.

### 5. Shared memory agents can write to

```text
call    keel_remember(fact="web tests need Node 20; Node 26 breaks localStorage in jsdom",
                      topic="test", cite="web/package.json:7")
answer  saved (fact 14, by claude-code, checked: the citation exists). Shown in keel_howto(topic="test").
```

Rules taken from Copilot Memory [S26]: a fact needs a citation or is marked `unverified`; keel checks the citation
when it shows the fact (keel already has a citation check); facts not confirmed for 90 days drop out; a person can
edit or delete them on the Knowledge page. The `facts` table and its REST routes exist; only the MCP tool and the
checks are new.

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
asks for it and is off by default. It writes a short, marked block into `AGENTS.md` (commands and non-standard rules only, no
overview, under 60 lines), so agents that never connect still get the answers. The ETH study says instructions are
followed and overviews are not worth their tokens [S8].

## 4. Flows: what to use and what to change

| Flow | Change | Why |
|---|---|---|
| **init** | Add a model-free step `conventions_scan` after `project_rules`: it saves the answers (commit, branch, test, style, pr, each with source and confidence) in keel's own storage, not in the project | `keel_howto` then works on day one, with no model key |
| **knowledge-refresh** | Run the scan again; check every remembered fact's citation; mark changed answers | Stops stale answers, the main failure of written files [S11] |
| **quick / implement / harness (session)** | Put the 5-line brief in the work order instead of long standing text; point to `keel_howto` | keel agents used 26–32k tokens against about 20k plain; the work-order text is most of the gap [K1] |
| **ship** | Use the `pr` answer for the PR title and body (also fixes "no `Closes #N`", [#207](https://github.com/MiladNalbandi/keel-v2/issues/207)); check commit subjects against the style | One place decides the style for keel's commits and the agent's |
| **review / review-pr** | Give the reviewers the `howto` answers as the rule list | Reviewers stop guessing the conventions |
| **after parallel flows** | A hint in `keel_status` / `keel_who`: "3 finished branches; `keel_branch_sync` merges them in order" | The test's parallel run ended with a CHANGELOG conflict [K1] |
| **quick** for tiny tasks | Keep one gate at most; no change needed beyond #218 | Minutes against seconds [K1] |

No new flow is needed. The heavy, repeated work (finding the rules, running the tests, bringing branches together)
moves into steps keel already runs.

## 5. First spec suggestions

### Spec A: `keel_howto`, topic `commit`

**Context.** keel infers a commit style for its own commits only (`commit_style.py`). Connected agents guess, or read
docs and `git log` each time.

**Goal.** A connected agent learns the project's commit style in one call of under 150 tokens and can check a draft.

**Acceptance criteria**

1. `keel_howto(topic="commit")` answers at the read level, with no model, in under 300 ms on a repo with 10k commits.
2. The answer has: format, types seen, subject rules, trailers, two real examples, source, and `sure: high | medium | low`.
3. Order of sources: a written rule (the project's keel setting `git.commit_template`, or a commit section in the repo's
   agent docs), then tool config (commitlint, commitizen, a commit-msg hook), then history (the last 50 human,
   non-merge commits; keel's own commits are left out as today).
4. History gives a style only when at least 60% of the commits share it; else the answer says "mixed" and shows the
   two most common forms.
5. Styles without a ticket prefix are recognised (today they are dropped).
6. Trailers: the answer says whether co-author and sign-off lines are used, from the bodies of the same commits.
7. `check="<message>"` returns "ok" or each rule the message breaks and one corrected form.
8. No convention found: the answer says so and suggests one; it never invents a rule.
9. An unknown topic lists the topics. Tool errors say how to fix the call.
10. keel's own commit step and `keel_howto` read the same function, so they cannot disagree.

**Design.** A new `runtime/conventions.py` with `commit(root, cfg) -> dict`; `commit_style.infer` moves behind it and
keeps its behaviour for keel's commits. `mcp_more.howto()` formats the dict. The result is cached per HEAD in keel's
own storage for the project; nothing is written into the project folder.

**Test plan.** Unit tests on fixture repos: Conventional Commits, ticket prefix, mixed history, empty repo, commitlint
config, a written rule that disagrees with history (the written rule wins). One MCP test for the call and the
`check` path. Measure the answer's tokens.

### Spec B: the agent tool set and `keel_brief`

**Context.** `--write` costs about 28k tokens of tool definitions; most tools are for the person.

**Goal.** An agent connects with about 10 tools and gets the project in one call.

**Acceptance criteria**

1. `keel2 mcp --agent` registers only the agent set (a named list in `mcp_levels.py`); `--print-config` shows it.
2. The set's definitions are under 3k tokens; a test fails when they grow past that.
3. The server `instructions` name `keel_brief` and `keel_howto` and when to call them, in under 400 characters.
4. `keel_brief()` answers with no model, in under 60 lines: stack, rule files found, test commands, where things
   live, flows running now.
5. It works before init (from `project_rules.discover` and the files) and says what init would add.
6. No description in the agent set is longer than 600 characters.
7. The read, write and admin levels are unchanged.

**Test plan.** A unit test for the list and the token budget; `keel_brief` on a bare repo, an initialised repo and a
repo with running flows; one run with a real client to confirm the smaller list.

**How to judge both.** Repeat the three-task run from the 0.40.0 report [K1] with and without the two tools and
compare tokens, wall time and the hidden checks. If the keel agents do not read `docs/CONVENTIONS.md` any more and
use fewer tokens, it works.

## 6. Not found, and guesses

- **No public numbers on MCP tool usage per server or tool**, and no measurement of a "conventions over MCP" tool. The
  expected gain is our inference from the sources above, not a measured fact.
- **No measured evidence that agents break commit conventions** in particular. A study of 33k agent PRs names CI
  failures, duplicates and "agent misalignment" as rejection reasons (abstract only) [S28].
- **Not opened:** Cursor / Codex / Windsurf support for MCP resources and prompts; Cursor's tool limit; Beads' Gas
  Town; mem0, Letta, Zep; convention-mining papers; the full text of the ETH paper (abstract and summaries read).
- **Read through a summarising fetch tool:** most web quotes. Spot-check a quote before it is used in public text.
- **Guess:** the 60% threshold, the 90-day expiry and the 3k-token budget are starting values, to be tuned in the test
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
