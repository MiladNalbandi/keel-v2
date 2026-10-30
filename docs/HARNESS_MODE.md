# Harness mode (keel 0.19.0)

keel is the control room; your coding harness does the work.

```
keel (dashboard, flow page, review panel, settings)
   │  "plan this"  "build the plan"  "review this change"  "fix finding #3"
   ▼
Claude Code │ Codex │ Copilot CLI │ OpenCode      ← the default model's harness, one session per flow
```

The harness is the CLI of the project's **default model** (Settings › Models): Claude Code for `claude`, Codex for
`codex`, Copilot CLI for `copilot`, OpenCode for `copilot` in `opencode` mode. An `api` model, a local model or the
fake model has no harness: those projects run keel's own flows as before.

## What runs where

| Work | Harness mode (default) | keel's flows |
|---|---|---|
| New work: change, feature, bug (and quick, small, implement) | the `harness` flow | the flow you picked |
| Review › Find problems | one harness session reads the change, then the checker gives each serious claim a second look | the review flow, or classic reviewers |
| Review › Fix | the `harness` flow, no plan step, intent `review-fix` | the fix flow of Settings › Review |

Settings › General › **Who does the work**: `build_runs` (harness | flow), `harness_plan` (plan first: yes | no),
`fix_runs` (harness | flow). Settings › Review › How a review runs: **Your coding harness** (`find_runs: harness` in
`.keel/config.yml`), the review flow, or classic. The start dialog has **Run keel's own flow** for one start; the
API and MCP take `options.engine: flow` (or `harness`).

## The harness flow

`content/workflows/harness.yaml`:

1. **preflight**: the flow's own branch (as every flow). A project without a test command keel knows starts anyway:
   the harness runs its tests itself.
2. **plan** (plan mode on): the harness reads the project and writes a plan: goal, acceptance criteria, files, tests,
   risks. Read only; it ends with `PLAN: ready` or `PLAN: blocked <why>`.
3. **approve the plan**: approve (build it), change (your note goes back to the same session, which plans again),
   reject (stop, nothing changed).
4. **build**: the **same session** builds the plan, tests first. It does not commit.
5. **keel runs the project's tests**: a failure goes back to the same session once, with the output.
6. **look at the change**: the harness's summary and the diff; commit, more (a note back to the session), reject.
7. **commit** (keel's secret scan first), then the PR body and the PR gate as in ship.

Every agent step marked `session: thread` continues one harness session (`claude --resume`, `codex exec resume`,
`copilot --session-id`, `opencode run -s`). The flow page shows the harness and the command that opens that session in
your own terminal (for Claude Code with keel's config folder: `CLAUDE_CONFIG_DIR=… claude --resume <id>`).

keel's guard stays on inside the harness: the hook (Claude Code, OpenCode, Codex 0.150+) or the command wrappers
(Copilot) refuse what the phase does not allow, and keel makes the commits.

## MCP: keel is the gateway

A harness step (`mcp: gateway`) gets **one** MCP server: keel's own. keel's server starts the project's connected
servers (Settings › MCP) and re-exports their tools as `<server>__<tool>` (in Claude Code: `mcp__keel__github__…`).

```
harness ──► keel MCP ──┬─ keel's own read tools (keel_ci_runs, keel_ci_failure, keel_git_status, keel_pr_status ...)
                       ├─ github__search_issues      read-only (the server says so): runs at once
                       └─ jira__create_issue         any other: waits for your OK in keel's Inbox (10 minutes)
```

- One setup for all four harnesses; the servers' secrets stay in keel (keel's server gets them in its env, never the
  harness's config).
- Which servers: the ones Tools › Who may use what gives the harness agent (`harness-planner`, `harness-builder`),
  else every server the project has on. A server that does not start is left out; the others work.
- keel's own write tools (commit, push, PR, CI rerun) are never given to the harness: keel makes the commits.
- Your own terminal's `keel2 mcp` has no gateway yet (0.19.0: flows only).

## Going back to keel's flows

Settings › General › Who does the work › New work: **keel's flows**, and Settings › Review › How a review runs:
**The review flow**. Nothing else changes; every flow is still there.
