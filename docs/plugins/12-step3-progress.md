# 12 · Step 3 progress: parts that became plugins

Branch `feat/plugin-step3` (local, on the plugin track: never merged into `main` and never tagged until the track is
identical to 0.15.1, and to what `main` adds after it). The normal image (`EDITION=full`) bakes every `plugins/*` into
`/opt/keel-v2/plugins`.

| wave | part        | plugin folder     | needs             | status                                                                            |
| ---- | ----------- | ----------------- | ----------------- | --------------------------------------------------------------------------------- |
| 0    | Map         | `plugins/map`     | —                 | done                                                                              |
| A    | Wiki        | `plugins/wiki`    | —                 | done (the knowledge base stays core)                                              |
| A    | CI/CD       | `plugins/ci`      | git               | done (needs Git since wave B)                                                     |
| A    | Tasks       | `plugins/tasks`   | —                 | done (core: `TaskSink`, `inbox.card`)                                             |
| A    | Jira        | `plugins/jira`    | tasks             | done                                                                              |
| B    | Database    | `plugins/db`      | — (optional: map) | done (its keys come from its own FlowContributor)                                 |
| B    | Code Review | `plugins/review`  | keelbot           | done (its AI runs in KeelBot's sessions; needs KeelBot since wave C)              |
| B    | Git         | `plugins/git`     | —                 | done (GitHub token, Workspace, opening PRs stay core)                             |
| C    | Graph       | `plugins/graph`   | —                 | done (the scan, the agents' `code_graph` setting and the CodeGraph CLI stay core) |
| C    | Code page   | `plugins/code`    | —                 | done (`RepoService`, `Workspace`, keel's docs and memory stay core)               |
| C    | KeelBot     | `plugins/keelbot` | —                 | done (approvals, its old tables and `TaskSink` stay core)                         |

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
- **Core only, wave C:** without the Graph plugin `/index` and `/graph*` answer 404, so the Code page's index tag, the
  launcher's symbol search and KeelBot's @symbols find nothing. Without the Code plugin KeelBot's @ file list is empty
  and ⌘K has no code preview. They call over HTTP (no imports), so nothing breaks, but the core image check must cover
  them.
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
core; web: with the Graph and the Code page merged too, the web allowlist is empty as well.

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

## Wave C check (on `6e599a5`, the full image built the normal way: with the agent CLIs and CodeGraph)

Every part is a plugin now: `builtins.py` and `builtins.ts` name none, and all three fence allowlists (engine, api,
web) are empty.

| what                                         | result                                                                    |
| -------------------------------------------- | ------------------------------------------------------------------------- |
| engine core / plugin engines / Product       | 826 · ci 17, db 25, git 22, graph 28, keelbot 40, map 36, review 15, wiki 4 · 15 |
| api, all source sets                         | 243 passed                                                                |
| web                                          | 416 of 416 passed, all builds ok                                          |
| no-v1                                        | clean                                                                     |
| e2e: Code Review / Product / plugin host     | 23 / 27 / 36 checks passed                                                |
| **parity e2e: 0.15.1 vs the wave C image**   | **0 differences**, now **without** `allow-no-clis.yml` (web 28 pages same in each round) |

The first parity run with the CLIs found one difference: Copilot's model list had 29 models on 0.15.1 and 30 on the new
image. Copilot CLI 1.0.94 (installed at build time) added `claude-haiku-5.5`; keel reads the list from the CLI. The
allow list now has one narrow entry for it: only the number of models from a CLI may differ.

## Port of 0.15.2–0.15.4 (main merged into the track)

The track forked from 0.15.1 (`eabb525`); `main` released 0.15.2, 0.15.3 and 0.15.4 after it (29 commits, up to
`0048f6b`). They are merged **into** the track (never the other way), and each change went where that code lives now:

| main's change                                                                  | on the track                                                                                                                                                         |
| ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| KeelBot: chats kept, the chat list with folders, new answers, its own sound    | `plugins/keelbot`: engine `routes.py` (the `/helper/folders` routes) and `helper.py`, api `Helper.kt`, web `ChatList.tsx`, `chats.ts`, `unread.tsx`, `HelperPanel.tsx`, `helper.css`; tests too |
| the engine tables `helper_folders` and `helper_sessions.folder`                | core `runtime/migrate.py` (the track keeps KeelBot's old tables core); its migration test stays core                                                                 |
| the Git log, Recent files (⌘E), more IntelliJ keys, a branch's "Show in the log" | `plugins/code`: api `RepoLog` / `RepoLogController` (in `keel.api.repo`, on core's `RepoService`), web `Log.tsx`, `gitLog.ts`, `gitLogApi.ts`, `Recent.tsx`, the `IDE_KEYS` table in `model.ts` |
| Code Review's keys                                                             | stay in `plugins/review/web/keymap.ts`; the chord names (Tab, Home, End, Delete, ⌫) are core's `src/keys.ts`                                                         |
| Flow (step details, Resume, Stop asks, run history), Jobs and Live agents (Finished, search), the model picker, the key cheat sheet, the menu by keyboard, Markdown, notifications, scrolling | core, as on main (`FlowService` / `FlowController`, `Jobs.kt`, `WorkflowService`, `RunHistory.tsx`, `KeySheet.tsx`, `keys.ts`, `ModelPicker.tsx`, `ProviderIcon.tsx`, …) |
| versions                                                                        | 0.15.4 everywhere (also `web/package.json`, which the track's version test checks)                                                                                   |

**The Flyway clash.** 0.15.4 shipped `V14__thread_hidden.sql`; the track had `V14__approvals.sql` (never released). main's
V14 stays exactly as released, and the track's became `V15__approvals.sql`. `MigrationUpgradeTest` (api) migrates a
database with 0.15.4's history (V1–V14) to the track: only V15 runs, the rows stay, a second start changes nothing. A
real check did the same with the images: keel 0.15.4 started on a fresh volume, then `keel-v2:port` on that volume
started, applied V15, and `/api/approvals` answered.

**Core never imports a plugin.** main's core files imported KeelBot (`Shell.tsx`, `Notifications.tsx`, `Settings.tsx`:
`helper/unread`) and the Code page (`Shell.tsx`, `keys.ts`: `pages/repo/model`, `review/keymap`). They use extension
points instead; the new slots are in the SDK and in [09-step2-contract.md](09-step2-contract.md#5-web-page-registry-and-slots):

| slot / SDK                       | core's place                                          | KeelBot / Code / Code Review put there                                          |
| -------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------- |
| `nav.badge`                      | a menu link and its folded-menu icon                  | KeelBot: its count of new answers on KeelBot                                    |
| `shell.watch`                    | mounted once by the shell, shows nothing              | KeelBot: watches `helper.finished`, plays its sound                             |
| `keys.area`                      | the key cheat sheet, by order                         | Code (40), Code Review (50), KeelBot (60)                                       |
| `notes.setting`                  | the bell's drawer › Settings                          | KeelBot: its sound's switch and test                                            |
| `settings.browser`               | Settings › This browser                               | KeelBot: its sound's switch                                                     |
| the assistant's `count`          | the Code page's assistant button                      | KeelBot: the same count                                                         |
| `FOCUS_KEYS`, `FOCUS_EVENT`      | `src/keys.ts` (F6 leaves Focus mode)                  | Code: Focus mode                                                                |
| `isTyping`, `modalOpen`, `MarkdownView`, `ProviderIcon`, `useWide`, `playKeelBot`, `KeyArea`, `KeyRow` | SDK exports                     | Code, KeelBot                                                                   |

The fence: all three allowlists (engine, api, web) stay **empty**; the api fence also forbids the Git log's classes in
core (`RepoLog`, `RepoLogController`, `GitLog`, `LogCommit`, `LogRef`, `RefItem`, `RefsView`).

**Tests moved with the code:** `test_helper_folders.py` (the folder routes: the KeelBot plugin; the table's migration:
core), `HelperFoldersApiTest` (keelbot), `RepoLogApiTest` (code), `keelbot-chats.test.tsx` (keelbot), `gitlog.test.tsx`
(code), and the Code keys of main's `keys.test.tsx` (code). New: `shellslots.test.tsx` and a part's `keys.area` (core),
KeelBot's keys in the sheet (keelbot), `MigrationUpgradeTest` (api).

**The parity e2e** now also asks main's new read-only GETs (Finished and the search on `/api/jobs`, `/api/jobs/count`,
`/repo/refs`, `/repo/log` with its filters and errors, `/helper/folders`, a deleted or unknown flow, the run history's
new fields), 122 endpoints per round (132 in the flow round), and compares with **0.15.4**.

### Check (on the port, the full image built the normal way: with the agent CLIs and CodeGraph)

| what                                         | result                                                                                   |
| -------------------------------------------- | ---------------------------------------------------------------------------------------- |
| engine core / plugin engines / Product       | 827 · ci 17, db 25 (+8 skipped), git 22, graph 28, keelbot 43, map 36, review 15, wiki 4 · 15 |
| api, all source sets                         | 258 passed                                                                               |
| web (tsc, vitest, build, plugins, product)   | 486 of 486 passed, all builds ok                                                         |
| no-v1                                        | clean                                                                                    |
| e2e: Code Review / Product / plugin host     | 23 / 27 / 36 checks passed                                                               |
| **parity e2e: 0.15.4 vs the port's image**   | **0 differences** (api 122 / 122 / 132 same, mcp 2 / 2 / 2, web 28 pages same in each round, the flow round same) |

The only allowed difference is `/api/features.plugins` (the plugin host). The Copilot model count entry matched nothing
this time (0.15.4's image has the same CLI); it stays, because the CLIs are installed at their newest version when an
image is built.
