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
