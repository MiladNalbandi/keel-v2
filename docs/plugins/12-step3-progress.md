# 12 · Step 3 progress: parts that became plugins

Branch `feat/plugin-step3` (local, on the plugin track: never merged into `main` and never tagged until the track is
identical to 0.15.1, and to what `main` adds after it). The normal image (`EDITION=full`) bakes every `plugins/*` into
`/opt/keel-v2/plugins`.

| wave | part                       | plugin folder   | needs | status                                |
| ---- | -------------------------- | --------------- | ----- | ------------------------------------- |
| 0    | Map                        | `plugins/map`   | —     | done                                  |
| A    | Wiki                       | `plugins/wiki`  | —     | done (the knowledge base stays core)  |
| A    | CI/CD                      | `plugins/ci`    | —     | done (Git is still core)              |
| A    | Tasks                      | `plugins/tasks` | —     | done (core: `TaskSink`, `inbox.card`) |
| A    | Jira                       | `plugins/jira`  | tasks | done                                  |
| B    | Database, Code Review, Git |                 |       | next                                  |
| C    | Graph, Code page, KeelBot  |                 |       | after B                               |

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
