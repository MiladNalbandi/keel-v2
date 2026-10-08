# 10 · Step 2 report: core services and slots

**Branch:** `feat/plugin-step2`, on top of `feat/plugin-host` (step 1). Local only, not pushed. It is on the plugin
track: it is never merged into `main` and never tagged until the track is finished and identical to v0.15.1.

Step 2 changes **how** parts talk, so that in step 3 each part can move out into its own plugin folder. For people,
keel looks and works the same.

## What changed

```
 before: core ──imports──▶ KeelBot, db/git/ci, code graph, map, tasks, review …
 after:  core ──calls──▶ registry · hooks · slots · beans  ◀──registers── each part (one built-in list per side)
```

| side   | new in core                                                                                                                                                                                                                                                                                                               |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| engine | `extensions.py`, one registry of parts. `builtins.py`, the one list of built-ins (db, git, ci, graph, map, KeelBot). Hooks: `on_scan`, `on_commit`, `on_thread_start`, `prompt_context`, `mcp_specs`, `pr_body_sections`. Each part declares its actions, docs, KeelBot words, read tools, MCP server, routes and errors. |
| all    | **Approvals:** one core "ask a person and wait" broker (`approvals.py`), the `approvals` table (V14), `/api/approvals`, and Inbox cards from it. KeelBot keeps only its own session rules.                                                                                                                                |
| api    | `InboxSource` (Tasks gives its own Inbox items) and `EngineEventHandler` (events by prefix; KeelBot and index handlers live in their parts). `Workspace` (the git helper flows need), `FlowContributor`, `ConnectionKind`, `SettingsSection`, and plugin settings under `plugins.<name>.*`.                               |
| web    | `registerPage`, `registerSlot`, `useSlot` and `askAssistant` in `@keel/web-sdk`. `builtins.ts`, the one list. Slots: `connections.kind`, `jobs.tab`, `tools.card`, `settings.section`, `workflow.actions`, `code.activity`, `code.tab`, `assistant`, `launcher.source`. One generic event channel.                        |

## The fence: core imports of plugin parts

| side   | before step 2 | now | what is left                                                                                        |
| ------ | ------------- | --- | --------------------------------------------------------------------------------------------------- |
| engine | 21            | 1   | `app.py` → KeelBot's routes and runner (they move with KeelBot in step 3)                           |
| api    | 4             | 0   | nothing                                                                                             |
| web    | 21            | 12  | 11 lines from `builtins.ts` (the one list), plus `Inbox.tsx → tasksApi` (`inbox.card` slot, step 3) |

## Tests (on `3fabd08`)

| what                                    | result                                      |
| --------------------------------------- | ------------------------------------------- |
| engine (pytest)                         | 907 passed                                  |
| Product engine                          | 15 passed                                   |
| api (test + productTest)                | 218 passed                                  |
| web (tsc, vitest, build, build:product) | 369 of 369 passed, both builds ok           |
| keel-start, no-v1                       | all checks, clean                           |
| e2e: Code Review (normal image)         | 23 checks passed                            |
| e2e: Product (Product image)            | 27 checks passed                            |
| e2e: plugin host (6 scenarios)          | 36 checks passed, incl. upgrade from 0.15.1 |

On a very busy machine (load above 20), some slow UI tests time out; run again, all pass.

## Notes for step 3

- Parts still import each other in a few places (the fence does not check these):
  - KeelBot and Map use the Database part's `QueryPanel`;
  - KeelBot uses the Code page's `rankFiles`;
  - Review uses the Code page's `reviewHash` and `nameOf`;
  - `GitLabConnection` uses `reviewApi`.

  These become plugin-to-plugin needs, or move into the SDK.

- `HelperRunner` still emits `helper.permission*` next to `approval.*`: KeelBot's panel and the notifications use them.
- `inbox.card` (Tasks, and later plugin-install requests) comes in step 3.
- The new endpoints (`/api/approvals`, `/api/connections/kinds`, `/api/settings/sections`, plugin settings) still need
  a line in `docs/CONTRACT.md`.
