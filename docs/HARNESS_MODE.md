# The harness flow: plan and build in one session

keel 0.19.0 handed this work to a coding-agent app (Claude Code, Codex, Copilot CLI, OpenCode). Since 0.26.0 (#101)
keel runs every agent in **its own harness**, on the model provider's API with your API key, so the harness flow runs
there too. Nothing sends work to it by a setting any more: you start it by name.

```
keel (dashboard, flow page, review panel)
   │  "plan this"  "build the plan"  "review this change"
   ▼
keel's harness ── one session per flow ── the default model's API (Anthropic, OpenAI, GitHub Models, Vertex, local)
```

## What runs where

| Work | How to get it |
|---|---|
| Plan, approve, then build in one session | start the `harness` flow (the start dialog, `keel_start_flow` with `engine: "harness"`, or the API's `workflow_id: harness`) |
| The same, done by your own connected Claude Code / OpenCode session | Start a flow › Run with: Connected session (`keel_start_flow` with `engine: "session"`); see [Run with a connected session](#run-with-a-connected-session) |
| New work in keel's own flows | change, feature, fix, quick, small, implement (the default) |
| Review › Find problems in one session | Settings › Review › How a review runs: **One session** (`find_runs: harness` in `.keel/config.yml`) |

`keel_start_flow` with `engine: "harness"` and a build flow (change, quick, small, implement, feature, fix) starts the
harness flow with that intent; its answer says so. `engine: "flow"` (or none) runs the flow you asked for.

## The harness flow

`content/workflows/harness.yaml`:

1. **preflight**: the flow's own branch (as every flow). A project without a test command keel knows starts anyway:
   the agent runs its tests itself.
2. **plan** (plan mode on): the agent reads the project and writes a plan: goal, acceptance criteria, files, tests,
   risks. Read only; it ends with `PLAN: ready` or `PLAN: blocked <why>`.
3. **approve the plan**: approve (build it), change (your note goes back to the same session, which plans again),
   reject (stop, nothing changed).
4. **build**: the **same session** builds the plan, tests first. It does not commit.
5. **keel runs the project's tests**: a failure goes back to the same session once, with the output.
6. **look at the change**: the agent's summary and the diff; commit, more (a note back to the session), reject.
7. **commit** (keel's secret scan first), then the PR body and the PR gate as in ship.

Every agent step marked `session: thread` continues one session of keel's harness (its conversation is kept in
`$KEEL_DATA/harness/sessions`). keel's guard checks every edit, read, shell command and MCP call before it runs, and keel
makes the commits.

## MCP: keel is the gateway

A harness step (`mcp: gateway`) gets **one** MCP server: keel's own. keel's server starts the project's connected
servers (Settings › MCP) and re-exports their tools as `<server>__<tool>`.

```
agent ──► keel MCP ──┬─ keel's own read tools (keel_ci_runs, keel_ci_failure, keel_git_status, keel_pr_status ...)
                     ├─ github__search_issues      read-only (the server says so): runs at once
                     └─ jira__create_issue         any other: waits for your OK in keel's Inbox (10 minutes)
```

- The servers' secrets stay in keel: keel's server gets them in its environment, never the agent.
- Which servers: the ones Tools › Who may use what gives the harness agents (`harness-planner`, `harness-builder`),
  else every server the project has on. A server that does not start is left out; the others work.
- keel's own write tools (commit, push, PR, CI rerun) are never given to the agent: keel makes the commits.

## Run with a connected session

You already have your own Claude Code or OpenCode session open. It can do the harness flow's plan and build for keel,
with its own login and its own tools, while keel stays the control room (the plan gate, keel's test run, the checks
after each step, the done gate, the commit). keel never starts that session and never touches its login.

```
your Claude Code / OpenCode ── keel's MCP server (keel2 mcp --write) ── keel
     keel_session_open            registers it (the client's own name)       Start a flow › Run with: Connected session
     keel_session_next            waits for a work order (long poll)          the flow: "waits for the connected session"
     keel_session_todo            its todo list while it works                the Flow page shows it live
     keel_session_plan / _done    its report                                  the step ends; plan gate, keel's tests, done gate
```

**Connect it** (once): add keel's MCP server at the write level, the same command as Help › Start here, step 3.

- Claude Code: `claude mcp add keel -- keel2 mcp --write`
- OpenCode: `opencode.json` → `"mcp": { "keel": { "type": "local", "command": ["keel2", "mcp", "--write"], "enabled": true } }`

Then tell the session: *"Work for keel: call keel_session_open, then keel_session_next, and follow its work orders."*
Help › Start here has these with a Copy button.

**Start the flow**: Start a flow › the harness flow (or a build flow it stands in for: change, quick, small, implement,
feature, fix) › **Run with: Connected session · <client>**. The choice is there only while a session is connected (it
was seen in the last 2 minutes; every `keel_session_next` call counts). From MCP: `keel_start_flow` with
`engine: "session"`. The flow keeps `data.runner: session`.

**What happens**

1. The plan step calls no model: it publishes a **work order** (`kind: plan`) with what keel's own harness agent would
   be told (the request, the intent note, the plan format, the `PLAN: ready | blocked` line) and waits. No tokens are
   used while it waits.
2. The session's `keel_session_plan(flow, plan, todos)` ends the step: the plan is `data.plan_text`, so the plan gate,
   a change note and the goldfish review work as before. A plan without its `PLAN:` line is refused.
3. Approve → a `build` work order with the approved plan (and your notes after "change" or "more").
4. `keel_session_done(flow, summary, status)` ends the build; keel runs the project's tests. A failure is a `fix` work
   order with the failing output (one round, as for keel's harness). `status: blocked` skips the test run and shows
   why at the done gate.
5. The done gate, the commit and the PR steps are unchanged. keel makes the commit.

A report is refused, with a plain reason, when the flow does not wait for the session (it runs, it waits for you, it
ended), when it waits for another kind of report, or when another connected session took the work order.

**Safety, honestly**: the session runs its own tools, so keel cannot check them before they run, as it does for its
own harness. keel checks the change **after each step**: its diff guard puts back what the step's phase does not
allow (a plan step changes no file; `.keel/`, secrets and keel's never-touch list in every phase), the project's edit
tools run on the changed files, and a HEAD that moved (the session committed) is said, with the committed files the
phase would not allow. All of it shows at the done gate (`data.session_findings`). The session is told never to
commit, push or switch branches.

**A session that is gone**: a step that waits while no session was seen for 10 minutes goes to the Inbox (and the Flow
page says so): **keep waiting**, **run this step on keel's harness** (your API key; the next step goes back to the
session), or **stop the flow**.

The engine's routes (the api passes `/api/sessions*` on; every POST needs the MCP write level):
`GET /sessions?project=`, `POST /sessions/open`, `POST /sessions/{id}/next`,
`POST /sessions/flows/{tid}/plan | todo | done`. The flow's state carries `runner: "session"` and `session`
(`client`, `session`, `connected`, `order`, `todos`, `log`, `findings`, `gone`). Code: `runtime/sessions.py`,
`runtime/session_steps.py`, `runtime/session_service.py`, `mcp_session.py`; tests: `tests/test_session_flow.py`,
`tests/test_mcp_session.py`.
