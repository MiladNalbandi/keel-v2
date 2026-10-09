# 12 · Step 3 progress: parts that became plugins

Branch `feat/plugin-step3` (local, on the plugin track: never merged into `main` and never tagged until the track is
identical to 0.15.1, and to what `main` adds after it). The normal image (`EDITION=full`) bakes every `plugins/*` into
`/opt/keel-v2/plugins`.

| wave | part             | plugin folder     | needs             | status                                                               |
| ---- | ---------------- | ----------------- | ----------------- | -------------------------------------------------------------------- |
| 0    | Map              | `plugins/map`     | —                 | done                                                                 |
| A    | Wiki             | `plugins/wiki`    | —                 | done (the knowledge base stays core)                                 |
| A    | CI/CD            | `plugins/ci`      | git               | done (needs Git since wave B)                                        |
| A    | Tasks            | `plugins/tasks`   | —                 | done (core: `TaskSink`, `inbox.card`)                                |
| A    | Jira             | `plugins/jira`    | tasks             | done                                                                 |
| B    | Database         | `plugins/db`      | — (optional: map) | done (its keys come from its own FlowContributor)                    |
| B    | Code Review      | `plugins/review`  | keelbot           | done (its AI runs in KeelBot's sessions; needs KeelBot since wave C) |
| B    | Git              | `plugins/git`     | —                 | done (GitHub token, Workspace, opening PRs stay core)                |
| C    | KeelBot          | `plugins/keelbot` | —                 | done (approvals, its old tables and `TaskSink` stay core)            |
| C    | Graph, Code page |                   |                   | next                                                                 |

keel Product now needs `tasks` and `jira` (its delivery hands stories to them).

## Wave A check (on `fbe8cec`, images built with `INSTALL_CLIS=0`)

| what                                       | result                                                                                           |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| engine core / plugin engines / Product     | 880 · ci 17, map 35, wiki 4 · 15 passed                                                          |
| api, all source sets                       | 226 passed                                                                                       |
| web (tsc, vitest, build, plugins, product) | 390 of 390 passed, all builds ok                                                                 |
| keel-start, no-v1                          | ok, clean                                                                                        |
| e2e: Code Review / Product / plugin host   | 23 / 27 / 36 checks passed                                                                       |
| **parity e2e: 0.15.1 vs the wave A image** | **0 differences** (api 100–108 same per round, web 22 pages same per round, the flow round same) |

The parity e2e (`e2e/parity/`, see its README) also compared 0.15.1 with itself: zero differences.

**What the parity allow list covers, and why:**

- The plugin track's new endpoints.
- `/api/features.plugins`.
- What is missing in an image without the agent CLIs and CodeGraph (`allow-no-clis.yml`). The last check of the
  track must use an image built **with** the CLIs, without this file.

## Known for later

- **Core only (`EDITION=core` or `KEEL_PLUGINS=off`):** a few core links point to plugin pages, for example "Add a
  task" → `#/tasks` and the Wiki links in Flow. The full image has the plugins; the core image needs a check before
  step 6.
- **Plugin-to-plugin uses:** Jira → Tasks classes; Product → Tasks and Jira. They work because all jars share one
  `loader.path`. In step 5, when the plugins get their own repos, this needs a published API.

## Wave B check (on `f5fb049`, images built with `INSTALL_CLIS=0`)

| what                                       | result                                                                                        |
| ------------------------------------------ | --------------------------------------------------------------------------------------------- |
| engine core / plugin engines / Product     | 871 · ci, db, git, map, review, wiki all pass · 15                                            |
| api, all source sets                       | 232 passed                                                                                    |
| web                                        | 409 of 409 passed, all builds ok                                                              |
| e2e: Code Review / Product / plugin host   | 23 / 27 / 36 checks passed                                                                    |
| **parity e2e: 0.15.1 vs the wave B image** | **0 differences**, now also `keel2 mcp`'s tools/list (read-only and `--write`) in every round |

Fixed on the way: `keel2 mcp --write` listed CI/CD's acting tool after Git's (since CI/CD moved in wave A); now the
read tools come in the parts' order and the acting tools by name, exactly as 0.15.1. The parity e2e checks it.

## Wave C: KeelBot (on `9cfb2a2`, image built with `INSTALL_CLIS=0`)

KeelBot is `plugins/keelbot`. Core keeps "ask a person and wait" (approvals, the Inbox, `keel2 mcp --write`), KeelBot's
old engine tables and `TaskSink`. Code Review needs KeelBot now (its AI runs in KeelBot's sessions).

New in core for it:

- **engine `lifespan`:** a part's `lifespan` (`fn(app)` → an async context manager, or `{start, stop}`) runs as long as
  keel's app does: started in the registry's order once the engine is open, stopped in reverse before the engine and
  the bus (`keel_engine/extensions.py`). KeelBot's runner lives in it.
- **engine `open_paths`:** routes that check a key of their own need no internal token (KeelBot's
  `/helper/permissions/ask`).
- **api `agents.AgentFiles`:** a part's own agent files on the Agents page (KeelBot's `helper`, where 0.15.1 had it).
- **api `approvals.McpAskEvents`:** keel2 mcp's "Claude Code asks" notification (it was in KeelBot's event handler).

The fence: the engine allowlist is **empty** (KeelBot took `app.py`'s last line); api: `keel.api.helper` is forbidden in
core; web: two lines left (`builtins.ts` → the Code page and the Graph, wave C).

| what                                      | result                                                                         |
| ----------------------------------------- | ------------------------------------------------------------------------------ |
| engine core / plugin engines / Product    | 845 · ci 17, db 25, git 22, keelbot 40, map 35, review 15, wiki 4 · 15         |
| api, all source sets                      | 241 passed                                                                     |
| web                                       | 411 of 411 passed, all builds ok                                               |
| no-v1                                     | clean                                                                          |
| e2e: Code Review                          | 23 checks passed                                                               |
| the image with a throw-away keel          | plugin host, a chat on the fake model, an ask in the Inbox, the pages: all ok  |

The image check (a throw-away keel on the fake model, and a throw-away keel 0.15.1 for the menu): `/api/plugin-host`
lists KeelBot before Code Review; the Agents page lists `helper` where 0.15.1 did; keel's slash commands are the same
six; a chat answers on the fake model and is an agent call; `keel2 mcp --write`'s ask is a permission card in the Inbox,
KeelBot's old route answers it and the notification follows; `#/keelbot`, `/` commands, the Database and Git cards
through `keelbot.card`, the Code page's column (⌘I, its width from KeelBot's stylesheet), the Inbox's "Open KeelBot" and
`askAssistant` all work; the menu (links, text, icons) is 0.15.1's; no console errors.
