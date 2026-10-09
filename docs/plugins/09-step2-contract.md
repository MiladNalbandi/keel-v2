# 9 · Step 2 contract: core services and slots

The shared spec for step 2 ([05-migration.md](05-migration.md#step-2--core-services-and-slots-0160)). It is built on
`feat/plugin-step2`, which starts from `feat/plugin-host` (step 1). **Nothing moves out of the repo yet.** We only
change _how_ parts talk, so that in step 3 each part can move into its own plugin folder. For people, keel works exactly
as before; all tests and e2e stay green.

The rule from step 1 still holds: **core never imports a plugin part.** At the end of step 2 the fence allowlists
(engine 21, api 4, web 21) must be much shorter. The only lines left point to one place per side, the **built-in list**
(see 3, 5).

```
            before                                          after step 2
  core ──imports──▶ helper, plugins/db|git|ci,     core ──calls──▶ extension points (registry, hooks, slots, beans)
                    codegraph, tasks, review…                  ▲
                                                   built-in parts register themselves (one list per side)
```

## 1. Approvals: one core place for "ask a person and wait"

Today the only broker is inside KeelBot (`runtime/helper.py` HelperRunner, in memory). The guard's ask mode,
`keel2 mcp --write` and the Inbox "permission" cards all go through it.

**Engine**

- `keel_engine/approvals.py` (core) owns the waiting questions:
  - `ask(kind, project, title, command, *, source, thread_id=None, session=None, path="") -> answer` (async)
  - a blocking twin for the hook and ToolBox
  - `answer(id, decision, why)`, `pending(project)`, `asked(id)` (for `keel2 mcp` polling)
  - timeouts as today (`permissions.ASK_TIMEOUT`)
- It emits events `approval.asked` and `approval.answered`. The payload has id, kind, project, title, command, source,
  thread_id and decision.
- HelperRunner keeps only what is KeelBot's: session keys, "always" grants per session, dropping a session's questions.
  It uses `approvals` for the waiting itself.
- `hook.py`, `permissions.py`, `tools/agent_tools.py` and the `/plugins/ask` route call `approvals`, not the helper.
- Engine routes: `POST /approvals/{id}` (answer, internal token) and `GET /approvals?project=`. The old
  `/helper/permissions*` routes stay as aliases.

**api**

- `V15__approvals.sql` (V14 until the port of 0.15.4, whose V14 is `thread_hidden`) creates the table `approvals`. Columns: `id` (pk), `project_id`, `kind`, `source`, `title`,
  `detail`, `payload_json`, `status` (`waiting|approved|denied|expired|closed`), `decision`, `why`, `requested_by`,
  `created_at`, `decided_at`, `decided_by`.
- `keel.api.approvals.ApprovalService` stores `approval.*` events, and also approvals made by the api itself (the
  marketplace's `plugin-install` in step 4).
  - `decide(id, decision, why)`: for an engine-owned kind it forwards to engine `POST /approvals/{id}`; then it marks
    the row.
- `GET /api/approvals?status=waiting&project=` and `POST /api/approvals/{id}/decide {decision, why}`.
- The old answer endpoints (`/api/projects/{pid}/helper/permissions/{qid}`, `/api/plugins/asks/{qid}`) delegate to it.

## 2. Inbox sources (api)

```kotlin
interface InboxSource {            // keel.api.inbox
    val kind: String               // "approvals", "tasks", …
    fun items(pid: String?): List<InboxItem>
    fun waiting(pid: String?): Int
}
```

- `InboxService` merges core's own items (waiting flow threads) with every `InboxSource` bean, and no longer reads
  `task_inbox` or calls the engine's `/helper/permissions`.
- `ApprovalInboxSource` (kind `approvals`) replaces the "permission" cards. It keeps `kind: "permission"` on items that
  came from a permission ask, so the web shows them as today.
- `TaskInboxSource` lives in `keel.api.tasks` and moves the task and jira-manual items out of InboxService.
- `ProjectService`'s waiting count asks the Inbox, not `SELECT … FROM task_inbox`.

## 3. Engine: one registry of parts, and hooks

`keel_engine/extensions.py` (core) lists **parts**. A part is a built-in module or a loaded add-on/plugin, described
by a dict like `ADDON`:

| key             | meaning                                                                                                                                                                                                  |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`, `title` | id and name                                                                                                                                                                                              |
| `actions`       | `{"<name>:x": fn}` (plus `params` for the workflow builder)                                                                                                                                              |
| `read_tools`    | MCP tool names agents may call without asking (today `hook.PLUGIN_READ_TOOLS`)                                                                                                                           |
| `mcp`           | `{"server": "keel-db", "module": "keel_engine.plugins.server", "args": ["db"]}`                                                                                                                          |
| `router`        | a FastAPI router (today's per-plugin routes in `app.py`)                                                                                                                                                 |
| `keelbot`       | `{"prompt": str, "actions": [...]}`: what KeelBot is told about it                                                                                                                                       |
| `docs`          | action docs (today in `action_docs.py`)                                                                                                                                                                  |
| `errors`        | exception types its routes turn into 4xx (DbError, GitError, CiError)                                                                                                                                    |
| `hooks`         | `on_scan(root, pid)`, `on_commit(root)`, `on_thread_start(root, thread)`, `prompt_context(agent, root, pid, query) -> str`, `mcp_specs(agent, root) -> list`, `pr_body_sections(thread_id) -> list[str]` |

- **Built-ins** are named in exactly one file, `keel_engine/builtins.py`: a tuple of module paths (`keel_engine.plugins.db`,
  `…git`, `…ci`, the code graph, the map, KeelBot's PR-body section). It is the engine's only fence allowlist entry.
  In step 3 each line moves into a plugin folder.
- Add-ons/plugins loaded by `addons.py` (`KEEL_PLUGIN_ADDONS`, `KEEL_ADDONS`) become parts too. Their `ADDON` dict
  may use the same keys.
- What changes, with the same behaviour:
  - `plugins/__init__.py` `NAMES/SERVERS/TITLES` and the prefix tests (`actions.py:119`, `compiler.py:1109`,
    `validate.py`) use the registry;
  - `hook.PLUGIN_READ_TOOLS` too;
  - the per-plugin routes in `app.py` become each part's `router`;
  - `mcp_server.py`'s per-plugin tools and `keelbot.py`'s per-plugin prompts come from the parts;
  - the code graph's MCP server, hints and syncs (`compiler.py:410-420`, `prompts.py:106-113`, `service.py:291`,
    `actions.py:403`, `scan.py:171-179`) are called through hooks;
  - `verdict_actions.helper_section` becomes KeelBot's `pr_body_sections`.
- The knowledge base (librarian, `knowledge_check`, blocker, guard rule) **stays core** (plan decision).

## 4. api core services

- **Workspace:** `keel.api.workspace.Workspace` holds the git helper flows need (today `RepoService.git()` and
  `base()`). `RepoService`, `FlowService`, `WorkspaceDoctor`, `KnowledgeService` and `Helper` use it.
- **FlowContributor:**
  ```kotlin
  interface FlowContributor {            // keel.api.flow
      fun keys(pid: String): Map<String, String> = emptyMap()      // secrets an agent call may use
      fun settings(pid: String): Map<String, Any?> = emptyMap()    // sent to the engine with a flow
      fun templateOn(pid: String, plugin: String): Boolean? = null // null = not mine
  }
  ```
  `FlowService` and `WorkflowService` ask every `FlowContributor` bean instead of `PluginService`. `PluginService`
  implements it.
- **EngineEventHandler:**
  ```kotlin
  interface EngineEventHandler { val prefix: String; fun handle(event: StoredEvent) }   // keel.api.events
  ```
  `EventService` keeps core's own side effects and hands each event to the handlers whose prefix matches. The
  `helper.*` and `index.done` cases move into handlers in `keel.api.helper` and `keel.api.projects`. The web's fixed
  event list (`api.ts` `ENGINE_EVENT_TYPES`) gets one generic channel, so plugin events reach the web.
- **ConnectionKind** and **SettingsSection:**
  - Beans that describe a connection kind (`database`, `jira`, `gitlab`; `github` is core) and a settings section
    (keys, types, defaults), listed at `GET /api/connections/kinds` and `GET /api/settings/sections`.
  - A plugin's own settings are stored under `plugins.<name>.<key>` in the `settings` table, read and written through
    `SettingsService.plugin(name)`.
  - Today's fields (`ciOnFailure`, `pushPr`, `branchPattern`, `commitAuthor`, …) **stay where they are** in step 2.
    They move with their part in step 3.

## 5. Web: page registry and slots

`@keel/web-sdk` gets:

```ts
registerPage({ id, label, group, order, needsProject?, aliases?, component })     // a menu page
registerSlot(slot, { id, title?, order?, component?, ...data })                   // a piece in another page
useSlot(slot): SlotItem[]                                                         // React hook, live
askAssistant(text, context?)                                                      // "ask KeelBot", without importing it
```

- **Slots in step 2:**
  - `connections.kind` (Connections page)
  - `jobs.tab` (Jobs: Pipelines)
  - `tools.card` (Tools: the plugin cards, Jira's MCP catalog)
  - `settings.section`
  - `workflow.actions` (the action picker, instead of the `/^(db|git):/` regex)
  - `code.activity`, `code.tab` (the Code page's side bar and tabs; Git, Database, Code Review)
  - `assistant` (KeelBot answers `askAssistant`)
  - `inbox.card` waits until step 3.
- **Slots added with the port of 0.15.2–0.15.4** (main's releases on the plugin track; KeelBot and the Code page are
  plugins, so core's shell, settings and key cheat sheet name no part):

  | slot               | where (core)                                                   | a piece                                                         | who uses it                                       |
  | ------------------ | -------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------- |
  | `nav.badge`        | the menu's link of page `page`, and its folded-menu icon       | `{ id, page, component: ({ kind: "nav" \| "rail" }) }`          | KeelBot: its new answers on KeelBot               |
  | `shell.watch`      | the shell mounts it once, on every page; it shows nothing      | `{ id, component }`                                             | KeelBot: counts new answers, plays its sound      |
  | `keys.area`        | the key cheat sheet (⌘/ or ?), among keel's areas by `order`   | `{ id, title, pages?, order, rows(keymap) → KeyRow[] }`          | Code (40), Code Review (50), KeelBot (60)         |
  | `notes.setting`    | the bell's drawer › Settings, after the sound                  | `{ id, component }`                                             | KeelBot: its own sound's switch and test button   |
  | `settings.browser` | Settings › This browser, after the mascot                       | `{ id, component }`                                             | KeelBot: its own sound's switch                   |

  keel's own key areas are Everywhere 10, the menu 20, the launcher 30, Flow 70, Workflows 80 and the diagrams 90
  (`src/keys.ts`). The `assistant` item may also carry `count` (a component): the count on its button in the Code page
  (KeelBot's new answers). The SDK also shares `FOCUS_KEYS` and `FOCUS_EVENT` (Focus mode's key and event: the menu key
  F6 leaves Focus mode), `isTyping`, `modalOpen`, `MarkdownView`, `ProviderIcon`, `useWide` and `playKeelBot`.
- **Built-ins** are named in one file, `web/src/builtins.ts`, which imports each part's registration module. Each part
  gets a `register.ts` next to its code, for example `components/helper/register.ts` and `pages/repo/register.ts`.
  `App.tsx`, `routes.ts`, `Shell`/`NavIcons` and the core pages read the registries; they no longer import the parts.
- `ScreenId` becomes `string` with the core ids as constants. Routes, aliases (`#/code`, `#/keelbot`) and the menu
  order stay exactly as today.

## 6. Who owns which files

| part           | owns                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **engine**     | `keel_engine/extensions.py`, `builtins.py`, `plugins/**`, `app.py`, `runtime/actions.py`, `compiler.py`, `prompts.py`, `agent_knowledge.py` (code-graph parts), `scan.py`, `service.py`, `verdict_actions.py`, `ship.py`, `action_docs.py`, `keelbot.py`, `mcp_server.py`, `workflows/validate.py`, `tools/codegraph.py`, `tools/mcp.py`, `runtime/mapper.py`, `runtime/helper.py` **lines that build MCP servers and prompts only**, `hook.py` **`PLUGIN_READ_TOOLS` only**, engine tests for these                                                                                                                      |
| **approvals**  | `keel_engine/approvals.py`, `runtime/helper.py` **ask broker only**, `runtime/permissions.py`, `hook.py` **ask path only**, `tools/agent_tools.py` (ask), `models/cli_runners.py` (ask), `runtime/guard_ctx.py` (ask), api `approvals/**`, `inbox/**`, `events/EventService.kt` + handlers, `tasks/TaskInboxSource.kt`, `projects/ProjectService.kt` (waiting count), `plugins/Plugins.kt` **ask endpoints only**, `helper/Helper.kt` **permission endpoints only**, `V15__approvals.sql`, web `pages/Inbox.tsx`, `inboxApi.ts`, web event channel (`api.ts` `ENGINE_EVENT_TYPES`, `state.tsx` event wiring), their tests |
| **api core**   | api `workspace/**`, `repo/RepoService.kt`, `flow/FlowService.kt`, `workflows/WorkflowService.kt`, `plugins/Plugins.kt` **FlowContributor only**, `doctor/**`, `knowledge/KnowledgeService.kt`, `helper/Helper.kt` **Workspace use only**, `settings/**`, `connections/**`, their tests                                                                                                                                                                                                                                                                                                                                    |
| **web**        | everything in `web/src` except the approvals part's files; `product/web` unchanged unless a type moves                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **integrator** | this file, merges, fence allowlists (they must only shrink), e2e, docs                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

When two parts must touch the same file, each changes only its own lines. The integrator merges them.
