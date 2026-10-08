# 11 · Step 3 contract: each part becomes a plugin folder

Step 3 moves the parts out of core into `plugins/<name>/` in this repo, each with the layout of an external plugin. The
normal image bakes them all in, so for people keel stays **identical to v0.15.1** (Milad's rule: the plugin track is
done only when every page, flow and part works as in 0.15.1, with every plugin built and tested). Branch:
`feat/plugin-step3`, on top of step 2. Local until Milad decides about `plugin-base`.

```
 plugins/<name>/                         the same layout as an external plugin (02-plugin-package.md)
   keel-plugin.yml                       the manifest (schema 1)
   engine/keel_plugin_<name>/            Python package: ADDON dict (+ the PART keys from step 2)
   engine/tests/
   api/src/main/kotlin/…                 Kotlin, compiled against keel's api, packed as a thin jar
   api/src/main/resources/…              (+ META-INF/spring/…AutoConfiguration.imports when it has beans)
   api/src/test/kotlin/…                 runs with keel's test support (ApiTest, StubEngine)
   web/index.tsx                         ES module: definePlugin({ name, setup }) registers pages and slots
   web/test/
   content/                              workflows, agents, skills, plugin.yml (KeelBot commands)
   migrations/                           only for new tables (old ones stay in core's V1–V14)
```

## Wave 0: build machinery + the first part (Map)

**Build, for every folder in `plugins/` (and `product/`, which keeps its place):**

| what    | how                                                                                                                                                                                                                                                                       |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| api     | `api/build.gradle.kts` finds `../plugins/*/api` and makes, per plugin, the source sets `<name>` and `<name>Test` and the tasks `<name>PluginJar` → `build/libs/keel-plugin-<name>.jar` and `<name>Test`. Aggregates: `pluginJars`, `pluginTest`. Product keeps its tasks. |
| web     | `web/plugin.vite.config.ts` (generic: `KEEL_PLUGIN=<name>`). `npm run build:plugin -- <name>` writes `plugins/<name>/web/dist/`; `npm run build:plugins` builds all. vitest includes `../plugins/*/web/**/*.test.{ts,tsx}`.                                               |
| engine  | `scripts/test-plugins.sh` runs each `plugins/<name>/engine/tests` with that plugin's engine folder on `PYTHONPATH`.                                                                                                                                                       |
| package | `scripts/build-plugin.sh <plugin-dir> <out> [--no-build]`: generic, like `product/build-plugin.sh`. It reads the manifest's parts and writes `<out>/<name>/<version>/` and `<out>/<name>-<version>.kplug`. `product/build-plugin.sh` calls it.                            |
| image   | `EDITION=full` (the new default) bakes **every** `plugins/*` into `/opt/keel-v2/plugins`. `product` = full + keel Product. `core` = none. Old values: `dev` means `full`.                                                                                                 |
| CI      | `ci.yml` builds and tests every plugin: the api `pluginTest`, `scripts/test-plugins.sh`, and the web vitest (which includes them).                                                                                                                                        |

**Web loading changes:** keel loads every resolved plugin's web part **at start**, not only when one of its pages
opens. The part's `setup()` registers its pages and slots, which keeps the menu, the slots and the order exactly as
today. While parts load, the menu shows core's pages; a part's pages appear when it has loaded. Product's old shape
(`{ name, pages }` plus `features.screens`) keeps working.

**Engine:** a moved part is an add-on. Its line leaves `keel_engine/builtins.py`, its package is `keel_plugin_<name>`,
and `extensions._addon_part` also reads the module's `PART` dict. The resolver puts its folder on `sys.path` and its
package in `KEEL_PLUGIN_ADDONS`.

**api:** a moved part keeps its Kotlin package names for now (`keel.api.<part>`), so keel's component scan finds its
beans when its jar is on `loader.path`. No change of behaviour. Own packages (`keel.plugin.<name>`) and
auto-configuration come in step 5, when the plugins get their own repos.

**Map, the first part** (`plugins/map/`, id `map`, version `1.0.0`, requires `sdk: 1`):

- engine `keel_engine/plugins/map` + `runtime/mapper.py` + `runtime/sqlschema.py` → `keel_plugin_map`;
- api: the map endpoints of `KnowledgeController` (`/map`, `/map/rebuild`) → `keel.api.map.MapController` in the plugin;
  `EngineClient`'s map methods are replaced by the generic `get`/`post`;
- web: `pages/Map.tsx` (+ what only Map uses) → `plugins/map/web/`. The ER tab's query panel comes from the Database
  part through a slot `map.er.query` (Database still in core until its wave; it registers that slot);
- tests move with the code;
- the full image shows the Map page exactly as in 0.15.1.

## The parity e2e (built in parallel)

`e2e/parity/` starts two throw-away keels on the same fixture project:

- A = the published `ghcr.io/miladnalbandi/keel-v2:0.15.1`;
- B = the new full image.

Then:

1. **api:** a list of GET endpoints (every read endpoint of `docs/CONTRACT.md` that needs no model) is called on both.
   Status codes and JSON shapes (keys and types, not values) must match. Endpoints that only B has are listed apart.
2. **web:** a headless Chromium (`npx -y -p playwright@1`, as `docs/gif/record.js` already does) opens every menu page
   on both. It checks the menu (groups, labels, order), that each page renders its main landmarks, and that the
   console has no errors. On a difference it saves screenshots of A and B.
3. It prints one parity table and fails on any difference that is not on an allow list (with a reason per entry).

## Waves after wave 0

| wave | parts                            | notes                                                              |
| ---- | -------------------------------- | ------------------------------------------------------------------ |
| A    | Wiki (page), Tasks + Jira, CI/CD | Tasks owns the `inbox.card` slot for its cards                     |
| B    | Database, Code Review, Git       | Database registers `map.er.query`; Review needs the assistant slot |
| C    | Graph, Code page, KeelBot        | KeelBot takes `app.py`'s last fence line with it                   |

After each wave: all unit tests, the e2e (Code Review, Product, plugin host) and the parity e2e must be green.
