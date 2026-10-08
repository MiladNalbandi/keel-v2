# 5 · Migration in safe steps

**Rule for every step:** it is a normal keel release. After it, `ghcr.io/miladnalbandi/keel-v2:latest` does
**everything it did before**, with the same data, and `:product-beta` still works. Nobody has to do anything when they
update. Each step has its own tests, and the "upgrade test" (below) runs in all of them.

```
  0.14  Code Review released (now, another session)
   │
  0.15  STEP 1  one plugin host, proven with keel Product          ◀── start here
   │            (+ the fence: core may not import plugins)
  0.16  STEP 2  core services and slots (still one repo)
   │
  0.17 ┐
  0.18 ├ STEP 3  each part becomes a plugin folder in this repo;
  0.19 ┘         :latest bakes them in, so nothing changes for people
   │
  0.20  STEP 4  marketplace, signed install, agents can ask
   │
  0.21+ STEP 5  plugins move to their own repos, one by one
   │
  1.0   STEP 6  a :core image next to :latest
   │
        STEP 7  open to other publishers
```

## The upgrade test (in every step)

A CI job starts the **previous** release with a `/data` volume full of data (flows, tasks, Jira link, database
connections, review drafts, KeelBot sessions, Product initiatives), stops it, starts the **new** image on the same
volume, and checks every page and api still shows the same data. It also runs the existing e2e scripts
(`e2e/review/e2e.py`, `product/e2e/e2e.py`).

---

## Step 1 · One plugin host, proven with keel Product (0.15.0)

**What:**

1. **The manifest and the resolver.** `keel-plugin.yml` (02, 2.3) and `keel-engine plugins resolve` (02, 2.5). The
   resolver reads `/opt/keel/plugins` and `/data/plugins`, checks `requires`, `files.sha256` and names, and writes
   `run/resolved.json` and `run/problems.json`. `KEEL_ADDONS` keeps working.
2. **keel-start becomes a small supervisor.** Resolver → engine → api with `PropertiesLauncher` and `loader.path`.
   Exit code 75 = start again. A start that is not healthy in 90 s falls back to `last-good.json`. `KEEL_PLUGINS=off`
   = safe mode.
3. **Engine:** plugin folders go on `sys.path` inside the engine (not `PYTHONPATH`); `addons.loaded()` can be reloaded.
4. **api:** a general `PluginSchema` (Product's `ProductSchema`, for any plugin, from `migrations/` or
   `classpath:db/<name>`); a `requires` check; a resource handler for `/plugins/<name>/<version>/web/**`;
   `/api/features` lists web entries.
5. **web:** `addons.ts` loads plugin web parts with `import(url)`; `index.html` gets the import map; the first
   `@keel/web-sdk` exports what Product's pages use today (api client, `ui`, `state`, `routes`, `Markdown`).
6. **Product becomes a real package.** `product/` builds `product-0.x.kplug` (engine package, thin api jar, web
   bundle, content, migrations). The product edition image is now "the normal image + that package in
   `/opt/keel/plugins`". `productBootJar` and the `import.meta.glob` path go away.
7. **The fence.** Tests that fail if core imports a plugin: `import-linter` (Python), ArchUnit (Kotlin), ESLint
   `no-restricted-imports` (web). Today's couplings (the list in [01-today.md](01-today.md#14-blockers-ranked-hardest-first))
   go into an **allowlist** that may only get shorter.
8. **One version source.** A test that `config.py`, `build.gradle.kts`, `keel2`, `pyproject.toml` and
   `web/package.json` agree.

**Tests:**

- Install `product-0.x.kplug` from a file into the normal `:latest` image, restart, and run `product/e2e/e2e.py`: all green.
- Start with a broken plugin (bad import, wrong `requires`, a migration that fails, a bean that throws): keel starts,
  the plugin is listed under problems.
- Start with no plugins: `java` runs with `PropertiesLauncher` and an empty `loader.path`, and all current api tests pass.
- The upgrade test from 0.14.

**Why this is first:**

- **It removes the biggest risks first.** The hard questions are all here: can the api load a jar from `/data`
  (`PropertiesLauncher`)? Can the browser load a plugin's code at run time with one shared React (import map)? Can a
  plugin keep its own migrations? Can keel restart itself safely? If one of these does not work, we learn it now, before
  we move anything.
- **Product is already separate.** It has its own packages, its own Flyway history, its own web entry and its own e2e
  test. It uses all four parts (engine, api, web, content). It is the perfect first passenger.
- **No risk for today's users.** Product is beta and only in the `:product-beta` image. The dev features people use
  every day do not move in this step.
- **Everything later reuses it.** Steps 3–7 are "do the same for the next part".
- **The fence stops new couplings** while we work on the old ones.

## Step 2 · Core services and slots (0.16.0)

Cut the couplings from [01-today.md](01-today.md#14-blockers-ranked-hardest-first), still inside this repo. Nothing
moves yet; only the way parts talk changes.

| new in core                                                                                           | replaces                                                                                                         |
| ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **Approvals** service + `approvals` table + `InboxSource`                                             | KeelBot's in-memory ask broker; used by the guard, `keel2 mcp --write`, KeelBot, and later install requests      |
| **Workspace** (git helper)                                                                            | `RepoService.git()/base()` used by flows, doctor, knowledge                                                      |
| **FlowContributor** (keys, settings, template filter)                                                 | `PluginService` inside `FlowService` and `WorkflowService`                                                       |
| **ContextProvider** engine hooks (`prompt_context`, `mcp`, `on_scan`, `on_commit`, `on_thread_start`) | code graph and hints hard-wired in `compiler.py`, `prompts.py`, `scan.py`                                        |
| **pr_body_sections** hook                                                                             | `verdict_actions.helper_section` importing KeelBot                                                               |
| **ConnectionKind**, **SettingsSection** (namespaced settings)                                         | Jira / Database / GitLab sections in `Connections.tsx`; `ciOnFailure` etc. in `Settings.kt` (values copied once) |
| **EngineEventHandler** by prefix, one generic SSE channel                                             | `helper.*` / `index.done` cases in `EventService.kt`, the fixed list in `api.ts:183-191`                         |
| one engine **registry** of actions, MCP servers, routes                                               | `NAMES = ("db","git","ci")`, the prefix tests, per-plugin routes in `app.py`                                     |
| web **slots** and a page registry (02, 2.6)                                                           | the fixed `ScreenId` union, `PAGES` in `App.tsx`, plugin imports in core pages                                   |
| SDK packages v1 (`keel-plugin-sdk`, `keel-api-sdk`, `@keel/web-sdk`) inside the repo                  | parts importing core internals                                                                                   |

**Tests:** all existing tests; new unit tests for each service; the allowlist from step 1 gets much shorter; the upgrade test.

## Step 3 · Each part becomes a plugin folder in this repo (0.17 – 0.19)

Each part moves to `plugins/<name>/` with exactly the layout of an external plugin (engine/, api/, web/, content/,
migrations/, `keel-plugin.yml`). CI builds a `.kplug` for each. The `:latest` image bakes them into `/opt/keel/plugins`
and turns them all on. **For people, nothing changes**: the same pages, the same data.

Order: easy and low-risk first, the most tied-in last.

| order | plugin              | why here                                                                                   |
| ----- | ------------------- | ------------------------------------------------------------------------------------------ |
| 1     | **Map**             | a pure view; nothing in flows uses it                                                      |
| 2     | **Wiki** (the page) | page + refresh button only; the knowledge base stays core                                  |
| 3     | **Tasks**, **Jira** | flows do not import them; needs `InboxSource` (step 2); Product then needs them as plugins |
| 4     | **CI/CD**           | needs Git's actions; its watcher uses `Flows.start` from the SDK                           |
| 5     | **Database**        | its ER tab moves into Map through a slot                                                   |
| 6     | **Code Review**     | new in 0.14; needs the `assistant` slot instead of `HelperService`                         |
| 7     | **Git**             | the core Workspace keeps what flows need; the panel, actions and MCP move                  |
| 8     | **Graph**           | page + a `ContextProvider`; flows work without it, with less context                       |
| 9     | **Code** (the page) | a host plugin with slots for Git, Review, Database, CI/CD                                  |
| 10    | **KeelBot**         | the most tied-in; the Approvals service (step 2) must be in place                          |

**Tests per plugin:** its own tests run from its folder (`pytest`, Gradle `test`, `vitest`); the full image e2e; the
upgrade test; a "core only" CI job starts keel with `KEEL_PLUGINS=off` and checks that flows, workflows, agents,
Inbox, budget and settings work.

## Step 4 · Marketplace, signed install, agents can ask (0.20.0)

- The marketplace repo with `sync` and `check-pr` workflows, `index.json` + signature on GitHub Pages.
- The api marketplace client, install / update / roll back / remove, restart button, rules.
- The web Plugins page (Installed, Marketplace, Sources and rules) and the core-only welcome.
- First-party plugins are released as signed GitHub release assets **from this repo** (tags `plugin-db-v1.4.0`).
- MCP tools `keel_marketplace_search`, `keel_plugin_info`, `keel_plugins_installed`, `keel_plugin_request`; the
  `plugin-install` approval card; the `needs_plugins` workflow field.

**Tests:** install, update, roll back and remove from a test catalog (a local HTTP server with a test key); a bad
signature, a wrong sha256, an expired index and a revoked version are all refused; an agent request creates an Inbox
card and does not install; an approved request installs and the waiting flow goes on.

**Outward actions (ask the user first):** creating the marketplace repo, turning on GitHub Pages, making the keys and
putting them into GitHub Actions secrets.

## Step 5 · Plugins move to their own repos (0.21 and later)

One plugin at a time: `git filter-repo` keeps its history; the new repo builds against the published SDK packages and
releases its own versions. This repo then **pulls** the plugins the `:latest` image bakes in from a lock file:

```
# plugins.lock  (this repo)
code    1.0.0  sha256:…
git     1.2.0  sha256:…
review  1.0.3  sha256:…
```

The Dockerfile downloads these release assets, checks sha256 and signatures, and unpacks them into `/opt/keel/plugins`.
No token is needed (public releases).

**Outward actions (ask first):** creating each repo, publishing the SDK packages (PyPI, npm, Maven / GitHub Packages).

## Step 6 · A core image (1.0)

- Publish `ghcr.io/miladnalbandi/keel-v2:core` (no bundled plugins). `:latest` stays "core + the keel set".
- `keel2 start --core` uses it. The first start shows the "Start with a set" page.
- If the CodeGraph CLI moved into the Graph plugin (open question), the core image no longer installs it.
- Then decide: should `:latest` become core-only with the set picker? (An open question; not before 1.0.)

## Step 7 · Open to other publishers

The `keel-plugin` CLI (new, dev, lint, keygen, pack), the template repo, the publishing guide, the verified-publisher
review. Content-only community plugins first; code plugins from unverified publishers stay off by default.

---

## What "light" really means here

- **Code and UI:** core becomes much smaller. The ten parts above leave the core repo, the core bundle and the core
  menu.
- **The image:** most of its size is the JDK, Python, Node and the agent CLIs. These stay. The plugin split saves the
  CodeGraph CLI (if it moves into Graph) and, after 1.0, the Python libraries of Database. So `:core` is lighter, but
  not tiny.
- **Startup and memory:** a core-only keel loads fewer beans, routes and web code.
