# keel 0.40.0: MCP tools, testing features and several agents at once — test report

Tested on 2026-11-24 ([#215](https://github.com/MiladNalbandi/keel-v2/issues/215)). keel 0.40.0, native mode (`keel2 start --native`),
fake model only (no model API key on the test box), no GitHub token, small throw-away git repos.

The question for every tool and feature: **does it help an agent build better and faster?** keel is a tool an agent connects
to; it is not there to manage agents. With several agents at once, keel should make them more efficient together.

## Summary

- **139 of 141 MCP tools were called** (69 read, 62 write, 10 admin); 108 work as described, 17 stop at the person's OK
  (expected) and were checked up to there, 9 work in part, 5 do not work (4 need a model key, 1 was broken:
  `keel_flow_doctor`, [#220](https://github.com/MiladNalbandi/keel-v2/issues/220), fixed in this PR), 2 were not called on purpose.
  Most calls answer in 20–250 ms.
- **keel's testing loop works end to end**: keel runs the project's tests after each build, sends a failure back with the
  output, stops for the person when the same failure comes back, and commits only after the tests pass and the person
  approves.
- **Rules mode and the gateway hold**: Safety only and the gateway's write tools wait for the person's OK in the Inbox;
  read-only gateway tools pass at once.
- **Several agents at once: keel keeps them apart, but it does not make them faster yet.**
  - Good: every parallel flow gets its own git worktree; keel runs the tests itself; each task gets one clean commit.
  - Bad: agents got each other's work orders ([#217](https://github.com/MiladNalbandi/keel-v2/issues/217)).
  - No shared knowledge: every agent read the same conventions file itself.
  - Results: 3 agents through keel took 7.6 min and 90k tokens. 3 plain agents took ~25 s and 60k tokens. One plain
    agent doing all 3 tasks took 17 s and 20k tokens.
- **14 bugs filed**: [#217](https://github.com/MiladNalbandi/keel-v2/issues/217) to [#230](https://github.com/MiladNalbandi/keel-v2/issues/230). One of them ([#220](https://github.com/MiladNalbandi/keel-v2/issues/220), `keel_flow_doctor` 404) is fixed in this PR.
  This PR also adds tests for the gaps found.

## How it was tested

- keel 0.40.0 natively, on a throw-away test box. Projects:
  - `calc-pass`: tests pass.
  - `calc-fail`: a real bug, `mul` adds instead of multiplying.
  - `shop` copies for the multi-agent runs.
  - `tmp-x` for add/remove project.
- **Agents.** The agents were Claude Code agents (Sonnet), each with its own long-lived `keel2 mcp --write` process
  (an MCP stdio connection through a small bridge, one process = one keel session, `clientInfo.name = "claude-code"`).
  keel was not set up as an MCP server in Claude Code's own config, because a separate `claude` process could not sign
  in on the test box. Effect: the agents did not load keel's tool list into their context (see "Tool list size").
- **Timing.** Every call was timed at the client.
- **The person.** A "person bot" (REST, the dashboard's routes) approved plan and done gates within ~2 s and stopped
  flows at the PR gate (no remote). Real gates wait for a human, so real runs take longer.
- **Hidden checks.** The multi-agent runs were graded with 4 checks the agents did not see:
  - the behaviour of all 3 functions;
  - half-up rounding, which `round()` gets wrong;
  - `ShopError` on bad input;
  - one CHANGELOG line per change.

## 1. Several agents at once

The project had three small tasks: `discount()` in pricing, `Cart.remove()` and `Inventory.restock()`. Each task needs its
own test and a CHANGELOG line, and the project's `docs/CONVENTIONS.md` sets the rules for money, rounding and errors.

| run | agents | wall time | tokens (all agents) | tool calls | hidden checks | did they get in each other's way? |
|---|---|---|---|---|---|---|
| plain, 1 agent, 3 tasks | 1 | 17 s | 20k | 4 | 4/4 | – |
| plain, 3 agents, one folder | 3 | ~25 s | 60k | 17 | 4/4 | yes: one agent's CHANGELOG line went into another's commit; one agent could not stage its line twice because HEAD kept moving |
| keel, 1 agent, 3 flows | 1 | 4.6 min | 31k | 14 | 4/4 | – (90 s lost waiting in `keel_session_next` after a finished flow, [#218](https://github.com/MiladNalbandi/keel-v2/issues/218)) |
| keel, 3 agents, 3 flows at once | 3 | 7.6 min | 90k | 43 | 2/4 after merging the 3 branches | not while working (own worktrees), but: work orders went to the wrong agent ([#217](https://github.com/MiladNalbandi/keel-v2/issues/217)), ~4–5 min lost; a CHANGELOG conflict at merge; the agent that built another's flow missed its CHANGELOG line |

What keel did well with several agents:
- **Isolation.** The 2nd and 3rd flow on the same project got their own worktree and branch, so no agent saw another's
  half-done files. In the plain run, agents saw and committed each other's files.
- **Tests that keel runs itself.** One agent reported "all pass" although its own test run had not started (no
  `python` on the box). keel ran the project's test command itself before the commit, so the report did not matter.
- **One clean commit per task**, each made after keel's tests and the person's OK.

What keel did not do (yet):
- **No shared knowledge.**
  - `keel_knowledge` is empty on a new project. Only an init flow with a model fills it, and no tool lets an agent add
    a fact for the others.
  - 7 of the 8 agents read `docs/CONVENTIONS.md` themselves (the one that was handed another agent's work orders did not). No agent used `keel_search` or `keel_read_file`: on a small
    project, plain file reads were faster.
- **No view of the other agents.** `keel_live` said "no agent is running" while 3 sessions worked
  ([#219](https://github.com/MiladNalbandi/keel-v2/issues/219)). Only `keel_flows` showed the other flows.
- **Work orders not tied to the agent.** `keel_session_next` hands out the oldest work order of the project
  ([#217](https://github.com/MiladNalbandi/keel-v2/issues/217)). The agents only finished after they were told to treat
  the orders as a shared pool, and the context of a task was lost when another agent did it.
- **No help to bring parallel branches together.** keel had three branches, and the CHANGELOG lines conflicted.
  `keel_branch_sync` exists, but nothing points an agent to it after a parallel run.
- **Overhead per agent.** keel agents used 26–32k tokens against ~20k for plain agents. The work-order text and the
  session protocol make up most of the difference, and keel's gates make each task take minutes, not seconds.

**Verdict for the goal.** On this small project keel made several agents *safer* together: isolation, tests and clean
commits. It did not make them more *efficient*. The biggest gains would come from fixing #217 and #218, and from a shared
memory that agents can write to (see section 4).

## 2. keel's testing features

| feature | works? | speed | notes |
|---|---|---|---|
| keel runs the project's tests after a build | yes | ~2 s | The command from `.keel/config.yml` (`uvx pytest -q`); keel runs it, not the agent. |
| A failing test sends the flow back to fix | yes | at once | The fix work order has the failing output (`assert 5 == 6 … test_mul`). |
| The same failure again stops for the person | yes | – | Gate "keel runs the project's tests keeps failing"; approve = try again. keel runs the tests once more, then the next order says "SAME FAILURE as the last try (2 rounds in a row) … find a different cause". |
| Results shown in the flow / UI | yes, partly | – | Timeline, Events panel and `keel_read_gate` show each run. In the Flow page's stream, 3 failed runs and 1 passing run are folded into one "tests pass ×4" card ([#229](https://github.com/MiladNalbandi/keel-v2/issues/229)). Checked in Chromium at 1400 px and 390 px; no console errors. |
| Tests pass → done gate → keel commits | yes | commit < 1 s after OK | 7 flows (6 on the shop copies, plus the fixed `calc-fail`). The author comes from git config; no co-author line with the setting off. |
| While the flow waits for the person, the agent learns it | no | 90 s per wait | `keel_session_next` waits the full time and says "Nothing yet" ([#218](https://github.com/MiladNalbandi/keel-v2/issues/218)). |
| A test command that cannot run | no | – | Comes back as "fix the failing tests" (known: [#205](https://github.com/MiladNalbandi/keel-v2/issues/205)). |
| PR gate after the commit | partly | – | "Skip" ends the flow, and the PR body has no `Closes #N` (known: [#207](https://github.com/MiladNalbandi/keel-v2/issues/207)). |

## 3. Rules mode and the MCP gateway

| feature | works? | speed | notes |
|---|---|---|---|
| `keel_rules_mode` read | yes | 130–190 ms | Shows normal / safety-only and what each means. |
| Switch to Safety only from MCP | yes | 30 ms | Nothing changes; a card waits in the Inbox. |
| Agent sends the token back alone | refused | 10 ms | "the person has not approved … in keel's Inbox yet"; the agent cannot switch it alone. |
| After the person's OK + token | yes | 190 ms | Applies from the flow's next agent call. |
| Back to normal | yes | 170 ms | Asks nothing (it makes the rules stricter). |
| Old cards | no | – | Each call adds a card; none is closed after the switch ([#221](https://github.com/MiladNalbandi/keel-v2/issues/221)). |
| Gateway re-exports `<server>__<tool>` | yes | – | The description starts with `[server]`; a write tool says "(keel asks the person in its Inbox before this runs)". Only when keel starts the MCP server with `KEEL_MCP_GATEWAY` (keel's harness steps). A connected Claude Code does not get the project's servers this way. |
| Gateway read-only tool | yes | 4 ms | Runs at once. |
| Gateway write tool, person approves | yes | ~3 s | Runs exactly once. |
| Gateway write tool, person denies | yes | ~3 s | Does not run; but the answer reads like "still waiting" ([#226](https://github.com/MiladNalbandi/keel-v2/issues/226)). |
| Gateway write tool, nobody answers | slow | up to 10 min | Blocks the call ([#227](https://github.com/MiladNalbandi/keel-v2/issues/227)). |

## 4. Missing or weak for an agent

Missing tools an agent would use:
- **A shared memory an agent can write to**, e.g. `keel_remember(fact, area)` to add to the project's knowledge, read by
  every later agent. Today `keel_knowledge` is read-only and empty until an init flow runs with a model.
- **A project brief without a model**: the project's own rules files (AGENTS.md, CLAUDE.md, CONTRIBUTING, docs/CONVENTIONS)
  in one call, so each agent does not search for them.
- **"Who works on what"**: connected sessions, their flows and the files they change (`keel_live` today shows only
  keel's own jobs, [#219](https://github.com/MiladNalbandi/keel-v2/issues/219)), and a way to claim an area so two agents
  do not pick the same files.
- **Run the project's tests on demand** with the same command keel uses at its check step. One agent could not run its
  tests (no `python`) and guessed.
- **Bring parallel branches together**: after several flows, merge them in order and settle append-only files like
  CHANGELOG.md. Today it is `keel_branch_sync` one by one, and nothing suggests it.
- **`keel_session_next(flow=…)`**, so an agent can wait for its own flow.

Confusing or slow:
- **Tool list size.**
  - `--write` lists 131 tools: about 114 KB of schema, roughly 28k tokens if a client loads them all. Read level is
    69 tools, about 10k tokens.
  - `keel_start_flow`'s description alone is 2.8k characters.
  - A small "agent" set (status, read_gate, search, read_file, knowledge, flows, session_*, start_flow, about 15
    tools) would save most of that for every agent.
- Two ways to ask the person: a token at once, or a call that blocks up to 10 minutes
  ([#227](https://github.com/MiladNalbandi/keel-v2/issues/227)).
- Asking the person before checking that the call can work, in about 11 tools
  ([#222](https://github.com/MiladNalbandi/keel-v2/issues/222)).
- `keel_session_next` waiting while nothing can come ([#218](https://github.com/MiladNalbandi/keel-v2/issues/218)).

## 5. Every tool

"For an agent":
- **helps build**: an agent uses it while it builds.
- **info**: useful to know.
- **person's tool**: mostly for the person or operator (gates, settings, projects).

The speed is the median of all calls, measured at the client.

### Read level (`keel2 mcp`, 69 tools)

| tool | works? | speed (median) | for an agent | notes |
|---|---|---|---|---|
| `keel_status` | yes | 145 ms | helps build | Flow, step, gate text and usage in one call; the best first call in a flow. |
| `keel_read_gate` | yes | 141 ms | helps build | Whole gate text, paged; shows keel's failing test output. |
| `keel_flow_doctor` | no | 19 ms | info | Always 404: it calls /api/api/flow-doctor/report (#220, fixed in this PR). |
| `keel_projects` | yes | 22 ms | info | One line per project. |
| `keel_timeline` | yes | 181 ms | helps build | Steps and agent runs; shows each test run and commit. `thread_id` is ignored (project-wide). |
| `keel_next` | yes | 26 ms | info | Clear when no flow runs. |
| `keel_explain` | yes | 25 ms | helps build | What the phase lets an agent change; useful before editing. |
| `keel_rules` | yes | 81 ms | helps build | Rules in plain words; long but complete. |
| `keel_dashboard` | yes | 18 ms | person's tool | A link for the person. |
| `keel_db_connections` | yes | 38 ms | helps build | Clear message when the plugin is off or there is no connection. |
| `keel_db_schema` | yes | 139 ms | helps build | Not tested with a real database (none on the box). |
| `keel_db_query` | yes | 46 ms | helps build | Not tested with a real database. |
| `keel_git_status` | yes | 80 ms | helps build | Branch, ahead/behind, changes. |
| `keel_pr_status` | partly | 29 ms | info | Needs a GitHub token; clear message without it. |
| `keel_ci_runs` | partly | 32 ms | info | Needs a GitHub token; clear message. |
| `keel_ci_failure` | partly | 26 ms | helps build | Needs a GitHub token; clear message. |
| `keel_review_prs` | yes | 58 ms | info | Clear when there is no GitHub/GitLab remote. |
| `keel_review_files` | yes | 112 ms | helps build | Branch review works without a remote. |
| `keel_review_diff` | yes | 108 ms | helps build |  |
| `keel_review_notes` | yes | 249 ms | helps build | Slowest read tool in Code Review (~250-480 ms). |
| `keel_review_threads` | yes | 81 ms | info | Needs a PR; clear message for a branch. |
| `keel_review_thread` | yes | 77 ms | info | Needs a PR. |
| `keel_review_conversation` | yes | 74 ms | info |  |
| `keel_review_file` | yes | 102 ms | helps build | File at head/base with line numbers. |
| `keel_review_symbol` | yes | 105 ms | helps build | Definition and uses at the review's head. |
| `keel_review_diagrams` | yes | 107 ms | info | Text diagrams of the change. |
| `keel_supervisor` | yes | 46 ms | person's tool | Parked plugin. |
| `keel_flows` | yes | 29 ms | helps build | The only tool where agents saw each other's flows in the multi-agent run. |
| `keel_inbox` | yes | 37 ms | person's tool | What waits for the person; stale MCP cards pile up here (#221). |
| `keel_live` | partly | 23 ms | helps build | Says "no agent is running" while 3 connected sessions work (#219). |
| `keel_budget` | yes | 48 ms | info |  |
| `keel_specs` | yes | 48 ms | helps build |  |
| `keel_knowledge` | yes | 38 ms | helps build | Empty on a new project (all sections "missing") until an init flow runs with a model; no tool lets an agent add to it. |
| `keel_search` | yes | 49 ms | helps build | Text + code graph; fast (~50 ms). No agent in the multi-agent run used it (files were small). |
| `keel_read_file` | yes | 24 ms | helps build | Line numbers, secret files refused. |
| `keel_spec_check` | yes | 49 ms | helps build |  |
| `keel_citations_check` | yes | 48 ms | helps build |  |
| `keel_read_project_file` | yes | 22 ms | helps build | Refused when the other project's rule is off; clear. |
| `keel_actions` | yes | 84 ms | info | Each action with its level. |
| `keel_run_action` | yes | 1 ms | info | Unknown action: clear message. |
| `keel_workflows` | yes | 152 ms | info | ~0.5 s on first call (reads 20+ workflows). |
| `keel_agents` | yes | 40 ms | info | Does not list connected sessions. |
| `keel_agent` | yes | 43 ms | info |  |
| `keel_keelbot_chats` | yes | 27 ms | info |  |
| `keel_keelbot_changes` | yes | 26 ms | info |  |
| `keel_format` | yes | 1 ms | helps build | Good, short templates. |
| `keel_workflow_check` | yes | 240 ms | info | Line-by-line errors. |
| `keel_rule_check` | yes | 59 ms | info | Shows what the pack would have refused. |
| `keel_agent_definitions` | yes | 123 ms | info |  |
| `keel_models` | yes | 143 ms | info |  |
| `keel_skills` | yes | 70 ms | helps build |  |
| `keel_skill` | yes | 33 ms | helps build |  |
| `keel_stacks` | yes | 67 ms | helps build |  |
| `keel_stack` | yes | 63 ms | helps build |  |
| `keel_stack_suggest` | yes | 102 ms | helps build |  |
| `keel_stack_check` | yes | 120 ms | info |  |
| `keel_diagram` | yes | 19 ms | info |  |
| `keel_flow_document` | yes | 19 ms | info |  |
| `keel_agent_prompt` | yes | 6 ms | info |  |
| `keel_flow_diff` | yes | 20 ms | info |  |
| `keel_health` | yes | 20 ms | info | 7 ms api round trip. |
| `keel_reconnect` | yes | 1 ms | info | Our bridge has no relay; clear message. |
| `keel_state` | yes | 27 ms | info |  |
| `keel_fetch_remotes` | yes | 69 ms | info |  |
| `keel_ci_report` | yes | 144 ms | info |  |
| `keel_marketplace_search` | yes | 111 ms | info | No catalog key on the box. |
| `keel_plugin_info` | yes | 11 ms | info |  |
| `keel_plugins_installed` | yes | 312 ms | info | ~0.3 s. |
| `keel_plugin_request` | yes | 70 ms | person's tool | 404: no catalogs on the box; clear. |

### Write level (`--write`, +62 tools)

| tool | works? | speed (median) | for an agent | notes |
|---|---|---|---|---|
| `keel_approve_gate` | yes | 472 ms | person's tool | Decides a gate (the person bot used the REST route instead). |
| `keel_resume` | yes | 19 ms | person's tool |  |
| `keel_start_flow` | yes | 1.2 s | helps build | 1-3 s. A 2nd/3rd flow on the same project gets its own worktree automatically (good for parallel agents). Description is 2.8k chars, the longest. |
| `keel_answer` | yes | 35 ms | person's tool |  |
| `keel_edit_gate` | yes | 257 ms | person's tool | The note reached the session's re-plan order. |
| `keel_restore_doc` | asked | 28 ms | person's tool | Asks first, then "no stored version" (#222); double space (#228). |
| `keel_ci_rerun` | asked | 15.1 s | info | Blocks ~15 s for the person, then "no GitHub token" (#222, #227). |
| `keel_db_change` | asked | 27 ms | helps build | Asks first, then "no database connection" (#222). |
| `keel_git_commit` | yes | 302.3 s | info | Blocks until the person answers, up to 10 min (601 s measured) (#227). Commit author from git config only. |
| `keel_git_push` | asked | 27 ms | person's tool | Only the first call (no remote). |
| `keel_pr_create` | asked | 27.1 s | person's tool | Blocks 27 s, then "feature branch" error (#222, #227). |
| `keel_review_comment` | yes | 167 ms | helps build | Draft kept in keel. |
| `keel_review_reply` | asked | 15 ms | info | Not confirmed: no remote/token. |
| `keel_review_resolve` | asked | 14 ms | info | Not confirmed: no remote/token. |
| `keel_review_explain` | no | 325 ms | helps build | Needs a model key (fake model on the box); clear message. |
| `keel_review_findings` | no | 484 ms | helps build | Needs a model key; clear message. |
| `keel_review_submit` | asked | 16 ms | person's tool | Not confirmed: no remote/token. |
| `keel_review_draft` | yes | 37 ms | helps build | Edit and delete work. |
| `keel_review_move_drafts` | yes | 120 ms | info | Needs a PR; clear message. |
| `keel_review_viewed` | yes | 156 ms | info |  |
| `keel_review_base` | yes | 224 ms | info |  |
| `keel_review_check` | yes | 127 ms | helps build | Needs a PR thread; clear message. |
| `keel_review_button` | yes | 117 ms | info | Needs a PR thread. |
| `keel_review_finding` | partly | 480 ms | helps build | Accepts a finding id that does not exist (#225). |
| `keel_review_address` | asked | 15 ms | helps build | Not confirmed: no PR thread. |
| `keel_review_fix_finding` | asked | 16 ms | helps build | Not confirmed: no finding. |
| `keel_review_checkout` | asked | 99 ms | person's tool | Asks first, then 409 on a branch review (#222). |
| `keel_review_merge` | n/t | – | person's tool | Not called: it writes to GitHub. |
| `keel_supervisor_set` | yes | 42 ms | person's tool | Parked plugin. |
| `keel_stop_flow` | yes | 198 ms | person's tool | Strict: needs the person's OK in the Inbox; works after it. |
| `keel_stop_flows` | yes | 39 ms | person's tool | By id; strict ask. |
| `keel_run_mode` | yes | 162 ms | person's tool |  |
| `keel_rules_mode` | yes | 40 ms | person's tool | Read works. Safety only waits for the person's OK in the Inbox; a token the agent sends back alone is refused ("the person has not approved … yet"). Back to normal asks nothing. Every call adds a card (#221). |
| `keel_resume_flow` | yes | 270 ms | person's tool |  |
| `keel_agent_start` | no | 98 ms | helps build | Needs a model key; clear message. Refuses a side agent on the same files as a flow (good for parallel work, not tested live). |
| `keel_keelbot_ask` | no | 149 ms | info | Needs a model key; clear message. |
| `keel_keelbot_keep` | asked | 32 ms | info | Asks first, then 404 unknown chat (#222). |
| `keel_keelbot_undo` | asked | 34 ms | info | Asks first, then 404 (#222). |
| `keel_workflow_create` | yes | 120 ms | person's tool | Saves under another id than the YAML's (#224). |
| `keel_rule_create` | yes | 85 ms | person's tool |  |
| `keel_agent_create` | yes | 68 ms | person's tool |  |
| `keel_agent_model` | yes | 209 ms | person's tool | Asks the person even when nothing changes. |
| `keel_skill_create` | yes | 46 ms | info |  |
| `keel_skill_import` | asked | 33 ms | info | Asks first, then 400 on a bad host (#222). |
| `keel_skill_attach` | yes | 42 ms | info |  |
| `keel_skill_market_install` | asked | 33 ms | info | No catalog key on the box: asks first, then 404 (#222). |
| `keel_stack_create` | yes | 133 ms | info |  |
| `keel_stack_remove` | yes | 73 ms | info | Strict ask. |
| `keel_stack_off` | yes | 175 ms | info | No ask (easy to undo). |
| `keel_stack_market_install` | asked | 31 ms | info | No catalog key: asks first, then 404 (#222). |
| `keel_reject_with_document` | yes | 40 ms | person's tool | Clear "nothing waits" without a clarify flow; the real path needs a model. |
| `keel_handover` | yes | 36 ms | person's tool | Same: real path needs a model. |
| `keel_branch_sync` | yes | 53 ms | helps build | Could merge parallel agents' branches; no agent used it. |
| `keel_ci_run` | yes | 284 ms | helps build | Clear "no CI config". |
| `keel_base_branch` | yes | 271 ms | info |  |
| `keel_release_check` | yes | 155 ms | person's tool |  |
| `keel_session_open` | yes | 42 ms | helps build | All three agents had the same client name; keel cannot tell them apart in messages. |
| `keel_session_next` | partly | 7 ms (max 90.3 s) | helps build | Hands orders by project, not by the agent that started the flow (#217); waits the full time when the flow is done or waits for the person (#218). |
| `keel_session_plan` | yes | 47 ms | helps build | Refuses a plan without "PLAN: ready" with a clear hint. |
| `keel_session_todo` | yes | 11 ms | helps build | Shown on the Flow page. |
| `keel_session_done` | yes | 42 ms | helps build | keel then runs the tests itself: a wrong "all pass" from an agent did not matter. |
| `keel_task_create` | partly | 71 ms | info | Hidden type values, names a missing tool (#228). |

### Admin level (`--admin`, +10 tools)

| tool | works? | speed (median) | for an agent | notes |
|---|---|---|---|---|
| `keel_delete_run` | asked | 33 ms | person's tool | After the OK: 409 "can still be resumed" (#222). |
| `keel_add_project` | yes | 12 ms | person's tool | Only folders keel already sees; else `keel2 add` + one restart (12 s). |
| `keel_remove_project` | yes | 32 ms | person's tool | Strict ask. |
| `keel_pause` | yes | 94 ms | person's tool | Strict ask. |
| `keel_focus` | yes | 40 ms | person's tool |  |
| `keel_connect_github` | n/t | – | person's tool | Not started: device sign-in for the person. |
| `keel_edit_workflow` | partly | 43 ms | person's tool | With the YAML's id: wrong reason after the OK (#224). |
| `keel_rule_install` | yes | 183 ms | person's tool |  |
| `keel_rule_edit` | partly | 51 ms | person's tool | Ignores `pack=` for off/on; card says "switch off None" (#223). |
| `keel_start_options` | yes | 43 ms | person's tool |  |

## 6. Bugs filed

| # | bug |
|---|---|
| [#217](https://github.com/MiladNalbandi/keel-v2/issues/217) | Several agents on one project: `keel_session_next` hands each agent another agent's work order |
| [#218](https://github.com/MiladNalbandi/keel-v2/issues/218) | `keel_session_next` waits the full time when the flow is finished or waits for the person |
| [#219](https://github.com/MiladNalbandi/keel-v2/issues/219) | `keel_live` says "no agent is running" while connected sessions work |
| [#220](https://github.com/MiladNalbandi/keel-v2/issues/220) | `keel_flow_doctor` always 404 (`/api/api/…`), fixed in this PR |
| [#221](https://github.com/MiladNalbandi/keel-v2/issues/221) | Every call needing the person's OK adds an Inbox card; none is closed |
| [#222](https://github.com/MiladNalbandi/keel-v2/issues/222) | keel asks the person before checking that the call can work |
| [#223](https://github.com/MiladNalbandi/keel-v2/issues/223) | `keel_rule_edit` ignores `pack=` and asks to "switch off None" |
| [#224](https://github.com/MiladNalbandi/keel-v2/issues/224) | `keel_workflow_create` saves under another id; `keel_edit_workflow` gives a wrong reason |
| [#225](https://github.com/MiladNalbandi/keel-v2/issues/225) | `keel_review_finding` accepts a finding id that does not exist |
| [#226](https://github.com/MiladNalbandi/keel-v2/issues/226) | Gateway: a "no" reads like "still waiting"; the card does not name the agent |
| [#227](https://github.com/MiladNalbandi/keel-v2/issues/227) | Part and gateway tools block up to 10 min for the person's OK |
| [#228](https://github.com/MiladNalbandi/keel-v2/issues/228) | Small tool description errors (`keel_task_create`, `keel_restore_doc`) |
| [#229](https://github.com/MiladNalbandi/keel-v2/issues/229) | Flow page: failed test runs folded into one "tests pass ×4" card |
| [#230](https://github.com/MiladNalbandi/keel-v2/issues/230) | `keel2 folders` says the main folder is "not known" while keel runs natively |

Seen again from an earlier run: [#205](https://github.com/MiladNalbandi/keel-v2/issues/205),
[#207](https://github.com/MiladNalbandi/keel-v2/issues/207), [#208](https://github.com/MiladNalbandi/keel-v2/issues/208).

## 7. Tests added in this PR

- `engine/tests/test_mcp.py::test_flow_doctor_reads_the_api_route_once_prefixed`: fails before the one-line fix, passes after it (#220).
- `engine/tests/test_mcp_gateway.py::test_a_write_tool_runs_once_after_the_persons_ok_and_not_at_all_after_a_no`: the
  gateway's Inbox path in process (read-only never asks; allow runs once; deny never runs).
- `engine/tests/test_session_flow.py::test_two_sessions_on_two_parallel_flows_each_hold_a_different_work_order`: two
  connected sessions and two flows on one project; no order is handed out twice. It documents today's hand-out order (#217).
- `engine/tests/test_session_flow.py::test_tests_that_keep_failing_stop_for_the_person_then_the_session_hears_it_is_the_same_failure`:
  keel's test loop with a connected session, as seen live.

## 8. Not tested, and why

- **Needs a model API key** (the box has only the fake model): KeelBot, `keel_agent_start`, the feature/clarify flows,
  `keel_review_explain` / `keel_review_findings`, the gateway inside a real harness step. Only their error text was
  checked (clear: "add an API key").
- **Writes to GitHub or needs a token:** `keel_review_merge`, `keel_connect_github` (its device sign-in was not
  started), and these tools only up to the person's OK: `keel_git_push`, `keel_pr_create`, `keel_ci_rerun`,
  `keel_review_reply` / `resolve` / `submit`, PR status and CI runs.
- **A real database:** the database tools were tested only up to "no connection".
- **Docker mode:** only native mode was tested.
- **keel as a native MCP server in Claude Code's own config:** a bridge process was used instead (see "How it was tested").
- **Larger projects:** on a bigger code base, `keel_search` and the code graph may save more reads than here.
- **Real human gate times:** the person bot answered in ~2 s.
