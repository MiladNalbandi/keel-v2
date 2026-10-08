# 2 · The plugin package and how keel installs it

**Goal:** a person can install a plugin into a **running** keel. The Docker image is never rebuilt. The plugin is a
file from its own git repo. keel checks it, unpacks it into `/data/plugins`, and loads it when it starts.

## 2.1 The big picture

```
 ┌──────────────────────── the keel image (never rebuilt to add a plugin) ───────────────────────┐
 │                                                                                                │
 │   web (React)                api (Kotlin, Spring)                 engine (Python, LangGraph)   │
 │  ┌─────────────────┐       ┌──────────────────────────┐        ┌──────────────────────────┐  │
 │  │ shell + menu    │ HTTP  │ flows · inbox · settings │  HTTP  │ flow runner · guard      │  │
 │  │ slots           │──────▶│ connections · budget     │───────▶│ agents · workflows       │  │
 │  │ PLUGIN LOADER   │       │ PLUGIN HOST              │        │ PLUGIN HOST              │  │
 │  │  import map     │       │  plugin beans (jars)     │        │  plugin packages         │  │
 │  │  @keel/web-sdk  │       │  own Flyway per plugin   │        │  actions · MCP · hooks   │  │
 │  └───────▲─────────┘       └────────────▲─────────────┘        └────────────▲─────────────┘  │
 │          │ web/index.js                 │ api/*.jar                         │ engine/          │
 │  ┌───────┴──────────────────────────────┴───────────────────────────────────┴──────────────┐  │
 │  │ /opt/keel-v2/plugins/   plugins that came in the image   (read only)                          │  │
 │  │ /data/plugins/       plugins you installed            (a volume: survives updates)         │  │
 │  └──────────────────────────────────────────▲───────────────────────────────────────────────┘  │
 └─────────────────────────────────────────────┼──────────────────────────────────────────────────┘
                                               │ download, check sha256, check signature
                                   ┌───────────┴────────────┐
                                   │ marketplace catalog     │ → GitHub release of each plugin repo
                                   └─────────────────────────┘
```

**Three rules:**

1. **Core never imports a plugin.** A plugin uses core only through the SDK and the slots (2.6).
2. **Every part of a plugin is optional.** A plugin can be only content (a workflow and an agent), or only a web
   page, or engine + api + web + content together.
3. **A broken plugin never stops keel.** keel leaves it out, says why, and starts without it.

## 2.2 The package file

A plugin release is one file: `<name>-<version>.kplug`. It is a `tar.gz` with a fixed layout:

```
db-1.4.0.kplug
├── keel-plugin.yml          the manifest (2.3), required
├── engine/                  a Python package, e.g. engine/keel_plugin_db/__init__.py
│   └── requirements.lock    extra Python libraries, pinned with hashes (optional)
├── api/
│   ├── keel-plugin-db.jar   a thin jar: its classes + Spring auto-configuration
│   └── lib/*.jar            extra libraries it needs, relocated (shaded) (optional)
├── web/
│   ├── index.js             an ES module; default export = the plugin's web part
│   ├── *.js                 its other chunks
│   └── style.css            optional, uses keel's CSS tokens
├── content/                 workflows/, agents/, skills/, stacks/, packs/, templates/, plugin.yml (commands)
├── migrations/              V1__init.sql, V2__…: its own Flyway history
├── README.md  CHANGELOG.md  LICENSE
└── files.sha256             sha256 of every file above (keel checks it again at each start)
```

Next to it, the GitHub release has `db-1.4.0.kplug.sig` (the signature, see
[03-security.md](03-security.md)).

## 2.3 The manifest: `keel-plugin.yml`

One file replaces today's three descriptions (the engine `ADDON` dict, the api `KeelAddon` bean and `plugin.yml`).

```yaml
schema: 1
name: db # id: [a-z][a-z0-9-]{0,31}. Prefix of all its names (2.7)
title: Database
version: 1.4.0 # semver; "-beta.1" is a pre-release
publisher: keel # must match the key that signed the file
summary: Connect a database. You get its tables and an ER diagram. Agents get read-only SQL tools.
repo: https://github.com/keel-studio/keel-plugin-db
license: MIT

requires:
  sdk: 1 # the plugin SDK major version core must offer (2.6)
  keel: ">=0.16.0,<1.0.0" # optional extra limit on keel's version
  plugins:
    code: ">=1.0.0,<2.0.0" # must be installed and on
optional:
  map: ">=1.0.0" # works better with it: the ER tab also shows in Map

parts:
  engine: { path: engine, package: keel_plugin_db }
  api: { jars: [api/keel-plugin-db.jar], lib: api/lib }
  web: { entry: web/index.js, css: [web/style.css] }
  content: content
  migrations: migrations # runs in its own history table: db_schema_history

per_project: true # people switch it on per project (today's Tools › Plugins)

contributes:
  screens: [] # whole pages in the menu
  slots: # pieces inside other pages (2.6)
    - { slot: code.activity, id: db, title: Database }
    - { slot: connections.kind, id: database }
    - { slot: step.card, match: "db:*" }
  actions: [db:query, db:check, db:change, db:migrate]
  mcp:
    server: keel-db
    module: keel_plugin_db.mcp
    read: [db_connections, db_schema, db_query]
  events: { emits: ["db.*"], listens: [] }
  connections:
    - kind: database
      title: Database
      fields:
        [{ name: url, secret: true }, { name: env, choices: [dev, test, prod] }]
  settings:
    - {
        key: rowLimit,
        type: int,
        default: 500,
        title: Most rows a query returns,
      }

permissions: # shown before install (03-security.md)
  secrets: [database]
  network: [from-connections]
  workspace: read
  agent_tools: read
  tables: [db_connection, db_snapshot]
```

## 2.4 Where plugins live on disk

```
/opt/keel-v2/plugins/                       in the image (read only): the "keel set" bundled in :latest
  code/1.0.0/   git/1.2.0/   …

/data/plugins/                           on the keel-data volume (keel can write here)
  installed.json      the truth: name, version, source (image|catalog|file), on, sha256, approved by, when
  last-good.json      the set that started fine last time
  store/db/1.4.0/     unpacked package, read only after unpack
  store/db/1.3.2/     the version before, kept for "roll back"
  data/db/            the plugin's own files (the SDK gives it this folder, nothing else)
  keys/               publisher keys the person chose to trust
  run/                files keel-start writes at each start (env, resolved.json, problems)
  downloads/          temporary
```

Today the only writable, lasting place in the container is `/data` (`/opt` belongs to root; `keel2` re-creates the
container on restart). So every installed plugin goes to `/data/plugins`.

A plugin in `/data` with the **same name** as one in `/opt` wins when its version is newer. "Reset to the image's
version" removes the `/data` copy.

## 2.5 How each layer loads a plugin (without a rebuild)

All three layers read one file that a small **resolver** writes before they start.

```
 keel-start (the container's main process)
   │
   ├─ 1. resolver  (keel-engine plugins resolve)
   │       reads installed.json + every keel-plugin.yml
   │       checks: files.sha256 · requires (sdk, keel, plugins) · names and prefixes
   │       writes run/resolved.json  (the plugins that will load, in dependency order)
   │       writes run/problems.json  (the ones left out, and why)
   │
   ├─ 2. engine   KEEL_PLUGIN_PATHS=…/db/1.4.0/engine,…   (added to sys.path inside the engine only)
   │              KEEL_ADDONS=keel_product,keel_plugin_db,…
   │
   ├─ 3. api      java -Dloader.path=…/db/1.4.0/api,…/db/1.4.0/api/lib
   │                   -cp app.jar org.springframework.boot.loader.launch.PropertiesLauncher
   │              core runs each plugin's migrations, then Spring finds its auto-configuration
   │
   └─ 4. web      GET /api/features lists each plugin's web entry
                  the browser loads /plugins/db/1.4.0/web/index.js with import()
```

### Engine (Python)

- The resolver gives the engine a list of folders. The engine adds them to `sys.path` **inside its own process**. We
  do **not** use `PYTHONPATH`, because today it leaks into project commands (`models/cli.py:96`).
- The engine imports each package and reads its `ADDON` dict, as `addons.py` does today. The dict gets new, optional
  hooks: `mcp`, `on_scan`, `on_commit`, `on_thread_start`, `prompt_context`, `pr_body_sections`, `listens`,
  `migrations` (engine tables).
- **Extra Python libraries:** `engine/requirements.lock` has exact versions and hashes. At install time keel runs
  `uv pip install --target /data/plugins/store/<n>/<v>/site --require-hashes --no-deps -r requirements.lock`. This needs
  PyPI. For the first-party plugins, their libraries (sqlglot, psycopg, pymysql) stay in core's venv until 1.0, so
  nothing changes for them.

### api (Kotlin, Spring Boot 3.3.5)

- Today keel-start runs `java -jar app.jar`. That uses `JarLauncher`, which cannot add jars from outside.
- Every Spring Boot jar also has `PropertiesLauncher`. It reads `loader.path`. So keel-start changes **once** to
  `java -Dloader.path=<plugin jars> -cp app.jar org.springframework.boot.loader.launch.PropertiesLauncher`. Without
  plugins, `loader.path` is empty and keel works as before. (We prove this in step 1, [05-migration.md](05-migration.md).)
- A plugin jar is **thin**: its own classes and `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`,
  as Product's api part has today. It is compiled against `keel-api-sdk` (`compileOnly`). Extra libraries go in
  `api/lib/` and must be relocated (shaded) so they do not clash with core's.
- **Migrations:** core's `PluginSchema` runs `migrations/` of each plugin with Flyway, table `<name>_schema_history`,
  **before** the plugin's beans start. This is Product's `ProductSchema` made general. Before the first migration of a
  plugin version, keel copies `/data/keel.db` to `/data/backups/` (SQLite online backup).
- All plugins share one class loader. There is **no sandbox** in the JVM. See [03-security.md](03-security.md).

### Web (React)

- The api serves `/plugins/<name>/<version>/web/**` from the plugin folder, with immutable caching. Today the api serves
  only `classpath:/static` (`common/AppConfig.kt:33-70`), so this is a new, small resource handler.
- Core's `index.html` gets an **import map**. It points `react`, `react-dom`, `react/jsx-runtime` and `@keel/web-sdk`
  to files that core's own build puts in `/assets/sdk/`. Core uses the same files, so there is **one React** in the page.
- A plugin's web part is built with Vite as an ES library, with those four names marked `external`.
- `index.js` default-exports a description:

```ts
import { definePlugin } from "@keel/web-sdk";
import { DbTool } from "./DbTool";
import { QueryCard } from "./QueryCard";

export default definePlugin({
  name: "db",
  pages: {}, // whole pages (contributes.screens)
  slots: {
    "code.activity": {
      db: { title: "Database", icon: "db", component: DbTool },
    },
    "step.card": { "db:*": QueryCard },
    "connections.kind": { database: DatabaseForm },
  },
});
```

- `/api/features` (it exists today) lists, for each plugin that is on: name, version, web entry, css, integrity
  hash, screens and slots. `addons.ts` changes from `import.meta.glob` (build time) to `import(url)` (run time).
- Plugin pages that import core source by path (Product does: `../../web/src/api`) move to `@keel/web-sdk`.

### Content

Core's content loader searches core's `content/` first, then each plugin's `content/`. A plugin cannot replace a core
workflow, agent or skill with the same name. `addons.py` already works this way for agents.

## 2.6 What core gives plugins: the SDK and the slots

A plugin may use **only** these. They have one version number, the **SDK major** (`requires.sdk: 1`). keel keeps an
SDK major working for a long time, so a plugin does not need a new release for every keel minor. Today Product needs
`>=0.13.0,<0.15.0` and must re-release for each keel minor. That stops.

| package                     | what is inside                                                                                                                                                                                                      |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `keel-plugin-sdk` (Python)  | `ActionInput`, `ActionResult`, `run_command`, `commit`, `run_mode`, `emit`, `ask_person`, `plugin_data_dir`, `keys_for`                                                                                             |
| `keel-api-sdk` (Kotlin jar) | `KeelPlugin`, `InboxSource`, `ApprovalService`, `FlowContributor`, `ConnectionKind`, `SettingsSection`, `EngineEventHandler`, scoped `PluginSecrets`, `Workspace` (git), `Projects`, `Flows.start`, `Notifications` |
| `@keel/web-sdk` (npm)       | `definePlugin`, the api client, `useApp`, `useFeatures`, ui parts (`Panel`, `Pill`, `Markdown`, `Code`, `DiffView`, `ResultTable`), the diagram kit (`components/er`), `askAssistant()`                             |

**Slots** are named places in a page where a plugin can put a piece of UI or logic.

| slot                                    | owner              | example user                      |
| --------------------------------------- | ------------------ | --------------------------------- |
| `nav.page`                              | core               | Map, Graph, Wiki, Tasks, KeelBot  |
| `connections.kind`                      | core               | Database, Jira, GitLab            |
| `settings.section`                      | core               | CI/CD (`ciOnFailure`), Git        |
| `inbox.card` (by kind)                  | core               | Tasks, plugin-install requests    |
| `step.card` (by action)                 | core               | `db:*`, `git:*`, `ci:*` results   |
| `jobs.tab`                              | core               | CI/CD pipelines                   |
| `flow.side`                             | core               | Wiki panel on the Flow page       |
| `assistant`                             | core               | KeelBot answers `askAssistant()`  |
| `code.activity`, `code.tab`, `code.scm` | **Code** plugin    | Git, Code Review, Database, CI/CD |
| `keelbot.command`, `keelbot.card`       | **KeelBot** plugin | Git `/commit`, Database `/query`  |

A plugin that **owns** slots is a **host**: Code hosts Git, Code Review, Database and CI/CD. This is how "Code has
its own plugins" works.

**Engine hooks** (in the `ADDON` dict): `actions`, `router`, `fake`, `mcp` (an MCP server for agents, reached through
the existing per-call key and `/plugins/call`), `on_scan`, `on_commit`, `on_thread_start`, `prompt_context` (Graph uses
it for hints and the code-graph server), `pr_body_sections` (KeelBot uses it for its commits), `listens`.

## 2.7 Names: a plugin stays in its own box

keel checks these at install and at start:

| thing          | rule                                                      | example (`db`)                  |
| -------------- | --------------------------------------------------------- | ------------------------------- |
| actions        | start with `<name>:`                                      | `db:query`                      |
| events         | start with `<name>.`                                      | `db.query.done`                 |
| MCP tools      | start with `<name>_`                                      | `db_schema`                     |
| new tables     | start with `<name>_`                                      | `db_snapshot`                   |
| api routes     | `/api/plugins/<name>/…` or `/api/projects/{pid}/<name>/…` | `/api/projects/{pid}/db/schema` |
| web screen ids | listed in the manifest                                    | —                               |
| settings       | stored under `plugin.<name>.*`                            | `plugin.db.rowLimit`            |
| secrets        | `plugin.<name>.<connection>.<field>`                      | `plugin.db.prod.url`            |

Old names stay as they are (`db_connections`, `ci_seen`, `review_*`, `tasks`), see 2.10.

## 2.8 Install, update, remove

```
  person / agent          web                  api (plugin host)                     /data/plugins
  ──────────────          ───                  ─────────────────                     ─────────────
  "Install Database" ──▶  shows permissions
                          and needs
                          [Install] ────────▶  1 read the catalog entry (signed)
                                               2 resolve needs: sdk, keel range, Code ≥1.0
                                               3 download db-1.4.0.kplug (+ missing needs)
                                               4 check sha256 == catalog
                                               5 check signature (publisher key)
                                               6 check manifest: names, prefixes, slots
                                               7 unpack ───────────────────────────▶  store/db/1.4.0/
                                               8 extra Python libraries (if any)
                                               9 write installed.json (on) ───────▶  installed.json
                          "restart needed" ◀── 10 content only? load it now, done
                          [Restart keel] ────▶ 11 wait until no agent step runs, exit code 75
                                                   keel-start: resolver, engine, api again
                                                   api runs db's migrations
                          menu shows Database ◀── 12 healthy → "On"
                                                   not healthy in 90 s → start again with
                                                   last-good.json, mark db "failed at start"
```

- **No install scripts.** Installing only unpacks files. A plugin's code runs only when keel starts it.
- **Restart:** today, if the engine or the api stops, the container stops (`wait -n` in `docker/keel-start`). We change
  keel-start into a small loop: exit code **75** means "start again with the new plugin set". Any other code stops the
  container as today. A restart takes about 30 s. Flows keep their state (LangGraph checkpoints in
  `/data/checkpoints.db`); a running agent step is waited for, or the person chooses "restart now" and the step runs again.
- **Content-only plugins** load without a restart (the engine re-reads content).
- **Update:** the new version is unpacked next to the old one. At the next restart keel switches. The old one is kept
  for **Roll back**. If the new version has new migrations, roll back warns that its tables may not fit.
- **Updates never install by themselves.** keel checks once a day. An update that asks for a **new permission** goes to
  the Inbox for approval ([04-marketplace.md](04-marketplace.md)).
- **Turn off:** the plugin stays installed, its data stays, it does not load at the next start.
- **Remove:** two choices. _Keep its data_ (default): tables and `data/<name>/` stay, a new install finds them.
  _Delete its data_: drops the tables listed in `permissions.tables` and its history table, and deletes
  `data/<name>/`. keel makes a backup first.
- **Safe mode:** `keel2 start --safe` (env `KEEL_PLUGINS=off`) starts with no plugins from `/data`. This is the way out
  if something goes very wrong.

### Per-project switch

Today (v0.10) Tools › Plugins turns db, git and ci on per project (`project_plugins` table, `'*'` = every project). This
stays. A plugin with `per_project: true` is installed for the whole keel, and each project chooses if it is on. A
plugin without it is on for every project.

## 2.9 Dependencies between plugins

```
            keel core (sdk 1)
     ┌──────────┬──────┴──────┬──────────┬────────┐
     │          │             │          │        │
   Code      KeelBot        Wiki       Graph    Tasks ◀── Jira
  ┌──┴───┬──────────┬───────┐                      ▲
 Git   Review   Database   CI/CD                    │
  ▲                          │                 keel Product (needs Tasks and Jira)
  └──────── needs ───────────┘
                 Map ‧‧‧ optional ‧‧‧ Database (ER tab in Map)
```

- `requires.plugins`: must be installed **and** on. The install dialog adds the missing ones ("Also installs Code").
- `optional`: used if it is there. Slots make this natural: Database's ER tab shows in Map only when Map is there.
- **One version of a plugin** at a time.
- **Conflict** (two plugins need ranges that do not meet): keel refuses and names both.
- **Turn off or remove** a plugin that others need: keel shows them and turns them off or removes them too.
- **No cycles.** The catalog rejects them.
- **Core never needs a plugin.** A fence test checks this (step 1).

## 2.10 Data and migrations of parts that move out

The tables of Tasks, Jira, Database, CI/CD and Code Review were made by **core** migrations (V7, V11, V12, V13). Flyway
does not like removed migrations. So:

1. **Core keeps V1–V13 forever.** These tables stay in every keel, even without the plugin. They are small and empty.
2. Each moved plugin starts its **own** history with `V1__baseline.sql` that uses `CREATE TABLE IF NOT EXISTS`, with
   the same shape. It works on an old database (tables exist) and on a new one.
3. New tables of a plugin always come from its own history and use its prefix.
4. Engine tables (KeelBot's `helper_*`, Map's `project_map`, Graph's `project_index`) follow the same idea in the
   engine database: core keeps creating them, new ones come from the plugin's `migrations` hook.

People who update keel lose no data and do nothing.

## 2.11 Version numbers

| what                 | example    | who sets it                                         |
| -------------------- | ---------- | --------------------------------------------------- |
| keel core            | `0.16.0`   | keel releases (engine `config.VERSION`, api, keel2) |
| plugin SDK major     | `1`        | core; raised only when an SDK change breaks plugins |
| each plugin          | `db 1.4.0` | its own repo, its own tags                          |
| catalog index format | `v1`       | the marketplace repo                                |

Today the version is written in four places (`config.py:8`, `build.gradle.kts:12`, `keel2:31`, `pyproject.toml:3`) and
`web/package.json` is not in step. Step 1 adds a test that checks all of them, and Product's two copies become one
(its manifest).
