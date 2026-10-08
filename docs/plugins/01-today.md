# 1 · keel today: what is where, and what is tied together

This page maps keel v2 **as it is now** (0.13.0 on `main`, plus Code Review from `feat/review-plugin`). It answers
three questions for every part we want to turn into a plugin:

1. Where is its code? (api, engine, web, content, tables)
2. Who calls whom?
3. What stops us from moving it out today? (a **blocker**)

Paths: "api" = `api/src/main/kotlin/keel/api`, "eng" = `engine/keel_engine`, "web" = `web/src`.

```
               today: one image, one repo, everything built together

   web (React, one bundle)          api (Kotlin, one fat jar)          engine (Python, one venv)
  ┌──────────────────────────┐    ┌───────────────────────────┐     ┌───────────────────────────┐
  │ App.tsx: static PAGES    │    │ every package in one jar  │     │ app.py: every route       │
  │ routes.ts: fixed ScreenId│───▶│ Flyway V1..V13, one       │────▶│ plugins/{db,git,ci} fixed │
  │ api.ts: one big client   │    │ history for everyone      │     │ codegraph + knowledge in  │
  │ addons.ts: product/web   │    │ KeelAddon beans (Product) │     │ every agent call          │
  │   found at BUILD time    │    │   found at START time     │     │ KEEL_ADDONS (Product)     │
  └──────────────────────────┘    └───────────────────────────┘     └───────────────────────────┘
          ▲ built in                      ▲ built in                         ▲ installed in
          └────────────── Dockerfile: EDITION=dev | product ─────────────────┘
```

## 1.1 The parts, one by one

### Code page (screen `repo`, label "Code")

| layer  | where                                                                                                                                                                |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| api    | `repo/RepoController.kt:14-81` (`/api/projects/{pid}/repo/{tree,file,raw,search,changes,diff,commit,commits,history,update-from-base}`), `RepoService`, `RepoSearch` |
| web    | `pages/Repo.tsx`, `pages/repo/{Ide,Explorer,Editor,Scm,Branch,Search,QuickOpen,KeelView,icons,model}`                                                                |
| tables | none                                                                                                                                                                 |
| engine | none of its own                                                                                                                                                      |

**Important:** `RepoService.git()` and `base()` are used by **core**: `flow/FlowService.kt:12,144` (lines 266-556),
`doctor/WorkspaceDoctor.kt:11,49`, `helper/Helper.kt:80`, `knowledge/KnowledgeService.kt:37`. So the git helper is
really a core service. Only the **page** (the IDE) can move.
`components/Code.tsx` is a shared code renderer (used by `Markdown`, `StepView`, `Flow`). It stays core.

### Git (a Code plugin today: `content/plugins/git`)

| layer   | where                                                                                                                                      |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| api     | `plugins/Git.kt:28-86` (`/api/projects/{pid}/git/*`), calls engine `/plugins/git/{op}`                                                     |
| engine  | `plugins/git/{core,tools,actions}.py`, routes `app.py:800-822`, MCP server `keel-git`, actions `git:branch/sync/push/pr/pr-checks/cleanup` |
| web     | `components/plugins/GitPanel.tsx` (in `Scm.tsx:8`), `GitHubToken.tsx`, `GitCard` in `helper/Actions.tsx:414-470`                           |
| content | `content/plugins/git/plugin.yml` (commands commit, pr, sync, branch)                                                                       |
| tables  | none                                                                                                                                       |
| secret  | `GITHUB_REPO_TOKEN` (`Plugins.kt:131-141`), **also used by core** `ship.yaml` to open the pull request (`verdict_actions.py:799-830`)      |

### Database (`content/plugins/db`)

| layer  | where                                                                                                                                              |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| api    | `plugins/Database.kt:42-208` (`/api/projects/{pid}/db/*`)                                                                                          |
| engine | `plugins/db/*` (sqlglot, psycopg, pymysql: `engine/pyproject.toml:19-21`), MCP `keel-db`, actions `db:query/check/change/migrate`                  |
| web    | `Databases.tsx` (in `Connections.tsx:13,390`), `DbTool.tsx` (Code page, `Ide.tsx:28`), `QueryPanel.tsx` (**inside Map**, `Map.tsx:9`), `QueryCard` |
| tables | `db_connections` in **core** migration `V11__plugins.sql:12-27`                                                                                    |
| secret | `db.<id>` in the secret store                                                                                                                      |

### CI/CD (`content/plugins/ci`)

| layer   | where                                                                                                    |
| ------- | -------------------------------------------------------------------------------------------------------- |
| api     | `plugins/Ci.kt:32-134`, a `@Scheduled` watcher every 2 min that starts the `ci-fix` flow (`Ci.kt:69-83`) |
| engine  | `plugins/ci/*`, MCP tools `ci_runs`, `ci_failure`, actions `ci:status/wait/logs/rerun`                   |
| web     | `Pipelines.tsx` (inside **Jobs**, `Jobs.tsx:10`), `CiCard`                                               |
| content | `ci/plugin.yml`, `ci/workflows/ci-fix.yaml` (uses `git:push`, so **CI needs Git**)                       |
| tables  | `ci_seen` (`V12__ci_seen.sql`, core history)                                                             |
| setting | `ciOnFailure` lives in **core** `Settings.kt:39`                                                         |

### Code Review (branch `feat/review-plugin`, shipping as 0.14.0)

| layer   | where                                                                                                                               |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| api     | `review/*`: `ReviewController` (`/api/projects/{pid}/review/*`), `CodeHost` with `GitHubHost` and `GitLabHost`, `ReviewAiService`   |
| engine  | only fake answers in core `models/fake.py`                                                                                          |
| web     | `components/review/*` (8 files), `reviewApi.ts`, `styles/review.css`, `GitLabConnection.tsx`, changes in `Ide.tsx`, `repo/model.ts` |
| tables  | `review_drafts`, `review_viewed`, `review_runs`, `review_decisions` (`V13__review.sql`, core history)                               |
| depends | `PluginService`, `RepoService`, `ProjectService`, `EventHub`, and **KeelBot**: `ReviewAiService` drives `HelperService` sessions    |

### KeelBot (screen `helper`)

| layer   | where                                                                                                                                       |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| api     | `helper/Helper.kt` (`/api/projects/{pid}/helper/*`), uses 11 core services                                                                  |
| engine  | `runtime/helper.py` (HelperRunner), `keelbot.py`, `permissions.py`; routes `app.py:583-717`; started in the app lifespan (`app.py:372,386`) |
| web     | `pages/Helper.tsx`, `components/helper/*`, `styles/helper.css`; **embedded in the Code page** (`Ide.tsx:7`, ⌘I)                             |
| content | `content/agents/helper.md`, `content/plugins/core/plugin.yml` (its slash commands)                                                          |
| tables  | engine DB `helper_sessions`, `helper_messages`, `helper_files` (`runtime/migrate.py:67-97`)                                                 |

**Important:** KeelBot holds keel's **only "ask a person and wait" broker** (`helper.py:487-581`). The guard's ask
mode (`hook.py:149`, `permissions.py:45`), `keel2 mcp --write` (`app.py:746-756`) and the Inbox "permission" cards
(`InboxService.kt:109-131`) all go through it. It is kept **in memory**, so it is lost on restart.

### Map

Shows System, Modules and Database (ER) levels, from folders, SQL migrations and the OpenAPI contract
(`runtime/mapper.py`, `sqlschema.py`). api `knowledge/KnowledgeController.kt:41-45`, routes `app.py:535-542`,
web `pages/Map.tsx` + `components/er/*` (shared with Graph), engine DB `project_map`. Nothing in flows uses it.
**The easiest part to move.**

### Graph (CodeGraph)

The **page** (`pages/Graph.tsx`, `components/graph/*`) is easy to move. But the **code index** is used by every agent
call: `compiler.py:410-420,461` adds the `codegraph serve --mcp` server, `prompts.py:106-113` adds hints,
`service.py:291` and `actions.py:403` sync the index. The CodeGraph CLI is installed in the image
(`Dockerfile:74-80`). Agents have a `knowledge.code_graph` setting (`AgentCatalog.kt:21-57`).

### Wiki (knowledge)

The **page** (`pages/Wiki.tsx`, `WorkflowMap.tsx`) and the refresh button (`WikiRefresh.kt`) can move. The **knowledge
base** cannot: the librarian, `knowledge_check` (`actions.py:113,534-546`), the push blocker (`blockers.py:110-120`),
the guard (`rules/__init__.py:322-326`) and the `init.yaml` / `ship.yaml` workflows use it. `memory.py` is agent
memory, not the Wiki, and is core.

### Tickets: Tasks and Jira

| layer     | where                                                                                                                                                                                    |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| api       | `tasks/*` (TaskService, TaskStore, TaskMachine, JiraSync `@Scheduled`), `jira/*`                                                                                                         |
| engine    | nothing                                                                                                                                                                                  |
| web       | `pages/Tasks.tsx`, `tasksApi.ts`, `components/JiraCard.tsx`                                                                                                                              |
| tables    | `tasks`, `task_events`, `task_inbox`, `jira_connections` (`V7__tasks_jira.sql`, core history)                                                                                            |
| core → it | `InboxService.kt:12-13,89,108,202-214` reads `task_inbox`; `ProjectService.kt:153` counts it; `Helper.kt:218` "make a task"; `Inbox.tsx:58-107`; `Connections.tsx:12`; `Tools.tsx:54-62` |

Flows do **not** import tasks. They reach it only through the `EngineEventStored` Spring event, which is already a
good seam. **keel Product depends on Tasks and Jira** (`product/api/.../DeliveryService.kt:6-12`).

### keel Product (already an add-on)

The model for every future plugin. It already has:

- engine package `keel_product` with an `ADDON` dict (`name`, `version`, `requires`, `content`, `actions`, `fake`, `router`)
- api auto-configuration jar part (`META-INF/spring/...AutoConfiguration.imports`), a `KeelAddon` bean, and **its own
  Flyway history** (`product_schema_history`, prefix `P`, `ProductAutoConfiguration.kt:44-57`)
- web `product/web/index.tsx` with lazy pages
- its own content (5 workflows, 4 agents) and its own e2e test

But it is still **built into** a special image: `productBootJar` merges it into the api jar, and `addons.ts` finds
its web pages with `import.meta.glob` **at build time**. Its pages import core source files by relative path
(`../../web/src/api`). The api side has no `requires` check.

## 1.2 Core parts (they stay)

| area                     | api                                      | engine                                               | tables                                                 |
| ------------------------ | ---------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------ |
| flows                    | `flow/*`                                 | `runtime/service.py`, `compiler.py`, `actions.py`    | `threads`, `events`; engine `keel_threads`, `verdicts` |
| workflows                | `workflows/*`                            | `workflows/*`                                        | `workflows`, `workflow_versions`, `workflow_folders`   |
| agents, stacks, skills   | `agents/*`, `stacks/*`, `skills/*`       | `models/*`, `prompts.py`, `stacks.py`                | `custom_agents`, `custom_skills`                       |
| guard and gates          | —                                        | `tools/guard.py`, `hook.py`, `rules/`, `run_mode.py` | —                                                      |
| settings                 | `settings/*`                             | —                                                    | `settings`, `kv`                                       |
| connections and secrets  | `connections/*` (AES-GCM, `master.key`)  | —                                                    | `secrets`                                              |
| budget                   | `budget/*`                               | `models/usage.py`                                    | `caps`, `provider_usage`                               |
| Inbox, notifications     | `inbox/*`, `notifications/*`, `events/*` | `events/`                                            | `notifications`                                        |
| projects                 | `projects/*`                             | `runtime/scan.py`                                    | `projects`                                             |
| jobs, live, quality, MCP | `jobs/*`, `quality/*`, `mcp/*`           | `runtime/evals.py`, `tools/mcp.py`, `mcp_server.py`  | `agent_calls`, `quality_*`, `mcp_servers`              |

## 1.3 Three extension systems exist today, none loads at runtime

| system                                    | what it has                                                                     | what is missing                                                                                             |
| ----------------------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| engine `addons.py` (v0.13)                | `KEEL_ADDONS`, version check, action prefix, content, router, fake, emit events | load from a folder at runtime, listen to events, hooks (scan, MCP, prompts, commit, PR body), engine tables |
| api `keel.api.addons` (v0.13)             | `KeelAddon` beans, `/api/features`, own Flyway history (Product)                | load a jar at runtime, `requires` check, slots for Inbox / connections / settings                           |
| web `addons.ts` (v0.13)                   | lazy pages, menu groups from `/api/features`                                    | load a module at runtime, shared React, an SDK, slots other than whole pages                                |
| `plugin.yml` + `keel.api.plugins` (v0.10) | per-project on/off (`project_plugins`), commands, context, per-call MCP keys    | the code is a **closed set** (`NAMES = ("db","git","ci")`, `plugins/__init__.py:26`)                        |

Note the **name clash**: "plugin" today means the per-project switch (v0.10). In the new plan a plugin is a package
installed into keel. Both meet: an installed plugin can still be switched on per project (see
[02-plugin-package.md](02-plugin-package.md#per-project-switch)).

## 1.4 Blockers, ranked (hardest first)

| #   | blocker                                                                      | where                                                                                                                                           | how we cut it                                               |
| --- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| 1   | the only "ask a person" broker lives in KeelBot, in memory                   | `helper.py:487-581`, `InboxService.kt:109-133`, `app.py:746-756`, `hook.py:149`                                                                 | core **Approvals** service + table + `InboxSource`          |
| 2   | code graph and knowledge are in every agent call and prompt                  | `compiler.py:410-420`, `prompts.py:101-132`, `agent_knowledge.py`, `scan.py:171-179`                                                            | core **ContextProvider** hooks; knowledge base stays core   |
| 3   | `RepoService.git()/base()` used by flows                                     | `FlowService.kt:144`, `WorkspaceDoctor.kt:49`                                                                                                   | move to a core **Workspace** service                        |
| 4   | `PluginService` inside flows and workflows (keys, settings, template filter) | `FlowService.kt:149,197,224,378`, `WorkflowService.kt:36,70`                                                                                    | **FlowContributor** interface                               |
| 5   | the engine plugin set is closed and spread around                            | `plugins/__init__.py:26-28`, `actions.py:119`, `compiler.py:1109`, `hook.py:39`, `app.py:719-823`, `mcp_server.py:333-552`, `keelbot.py:95-129` | one **registry** from manifests                             |
| 6   | Inbox reads `task_inbox` directly                                            | `InboxService.kt:12-13,89,108`, `ProjectService.kt:153`                                                                                         | `InboxSource` from the Tasks plugin                         |
| 7   | web pages are a fixed list; core pages import plugin components              | `App.tsx:14-50`, `routes.ts:3-19`, `Ide.tsx:7,28`, `Connections.tsx:12-14`, `Jobs.tsx:10`, `Map.tsx:9`, `Inbox.tsx`                             | **slots** + runtime web loader                              |
| 8   | plugin settings are core settings fields; unknown keys are rejected          | `Settings.kt:31-48`, `Settings.tsx:65-79`                                                                                                       | a settings namespace per plugin                             |
| 9   | plugin tables sit in core's Flyway history                                   | V7, V11, V12, V13; engine `migrate.py:40-97`                                                                                                    | freeze them in core; new tables in the plugin's own history |
| 10  | event special cases and a fixed web event list                               | `EventService.kt:87-105,166-175`, `api.ts:183-191`                                                                                              | event handlers by prefix; one generic SSE channel           |
| 11  | plugins import each other's web code                                         | `HelperPanel.tsx:17` (Git), `Actions.tsx:12` (DB), `Map.tsx:9` (DB), `GitPanel.tsx:11-21` (KeelBot)                                             | slots and the `askAssistant` event                          |
| 12  | the image can only add code at build time                                    | `docker/keel-start` (`java -jar`, `wait -n`), `Dockerfile` (`/opt` owned by root)                                                               | resolver + `PropertiesLauncher` + supervisor loop           |

Small bugs seen on the way (not fixed here, listed as follow-ups):

- `mcp_server.py:342` takes a title from `{"db","git"}[name]`, so a `ci` tool raises `KeyError` instead of "turn it on".
- `Plugins.kt:106` sends the GitHub token even when the Git plugin is off.
- `models/cli.py:96` (`project_env`) does not strip `PYTHONPATH`, so an add-on path leaks into project commands.

## 1.5 What we can reuse

- Product's whole pattern: `ADDON` dict, `KeelAddon` bean, own Flyway history, lazy web pages.
- The per-call key + MCP-over-HTTP callback (`/plugins/call`): already generic.
- `plugin.yml` commands and context: already data-driven.
- The `EngineEventStored` Spring event: Tasks and Product already listen to it.
- The plugin step "ask first" (`ask` type `plugin`): a generic gate.
- `CodeHost` (GitHub/GitLab) in Code Review: a clean interface.
- The Inbox "dependency" kind: a flow already knows how to wait for something missing.
