# 7 · Step 1 contract: the plugin host

The shared spec for step 1 ([05-migration.md](05-migration.md#step-1--one-plugin-host-proven-with-keel-product-0150)).
Five parts are built in parallel against it. When a part needs something here to change, the integrator changes this
file first. Spikes that show the risky bits work: [06-spikes.md](06-spikes.md).

```
 keel-start ──▶ keel-engine plugins resolve ──▶ $KEEL_DATA/plugins/run/{resolved.json, env}
     │                                                   │
     ├─ engine  (reads KEEL_PLUGIN_PATHS, KEEL_PLUGIN_ADDONS from run/env)
     ├─ api     (java -Dloader.path=$KEEL_PLUGIN_LOADER_PATH … PropertiesLauncher; reads run/resolved.json)
     └─ web     (GET /api/features → plugins[].web.entry → import(url); shared React through the import map)
```

## 1. Words and numbers

| name                | value                                                                                          |
| ------------------- | ---------------------------------------------------------------------------------------------- |
| plugin SDK major    | `1` (engine: `keel_engine.pluginhost.SDK = 1`)                                                 |
| plugin name         | `^[a-z][a-z0-9-]{0,31}$`                                                                       |
| image plugins       | `$KEEL_PLUGINS_IMAGE` (default `/opt/keel-v2/plugins`): `<root>/<name>/<version>/keel-plugin.yml` |
| installed plugins   | `$KEEL_DATA/plugins/store/<name>/<version>/keel-plugin.yml`                                    |
| choices of a person | `$KEEL_DATA/plugins/installed.json`                                                            |
| written at start    | `$KEEL_DATA/plugins/run/resolved.json`, `run/env`, `last-good.json`                            |
| `KEEL_PLUGINS`      | `on` (default): image + installed · `image`: image only (safe mode) · `off`: none at all       |
| package file        | `<name>-<version>.kplug` = `tar.gz` of the plugin folder (no top folder inside)                |

## 2. `keel-plugin.yml` (schema 1, the fields step 1 uses)

```yaml
schema: 1
name: product
title: keel Product
version: 0.1.0-beta.1
publisher: keel
summary: Initiatives, teams and product docs …
requires:
  sdk: 1 # required; must equal the host's SDK major
  keel: ">=0.15.0,<1.0.0" # optional; same syntax as keel_engine.addons.satisfies
  plugins: { tasks: ">=0.1.0" } # optional; each must resolve, on, with a fitting version
parts: # every part optional; paths relative to the plugin folder
  engine: { path: engine, package: keel_product }
  api: { jars: [api/keel-plugin-product.jar], lib: api/lib } # lib: optional folder of jars
  web: { entry: web/index.js, css: [web/style.css] }
  content: content
  migrations: migrations # optional; the api runs them (section 6)
per_project: false
```

`files.sha256` (optional, next to the manifest): lines `<sha256-hex>  <relative path>`, written by the packer; the
resolver and the installer check every listed file.

## 3. `installed.json` (what the person chose)

```json
{
  "plugins": {
    "product": {
      "version": "0.1.0-beta.1",
      "on": true,
      "source": "file",
      "sha256": "<of the .kplug>",
      "installed_at": "2026-10-08T18:00:00Z",
      "by": "cli"
    },
    "map": { "on": false }
  }
}
```

- An entry with `version` points to `store/<name>/<version>/`; it wins over an image plugin of the same name.
- An entry with only `"on": false` turns an image plugin off.
- No file = every image plugin on, nothing installed.

## 4. The resolver: `keel-engine plugins resolve`

Reads the image root and the store, applies `installed.json` and `KEEL_PLUGINS`, checks, orders, writes.

Checks (a plugin that fails one is left out, with the reason; plugins that need it are left out too):

1. `keel-plugin.yml` parses, `schema: 1`, the name is valid and equals its folder name, the version equals its folder.
2. `files.sha256` (when present): every file matches.
3. `requires.sdk == 1`; `requires.keel` fits `keel_engine.config.VERSION`.
4. `requires.plugins`: each is resolved and its version fits. No cycles.
5. Engine package names, and later action prefixes, are not used twice.

`run/resolved.json` (plugins in dependency order):

```json
{
  "sdk": 1,
  "keel": "0.15.0",
  "mode": "on",
  "resolved_at": "2026-10-08T18:00:00Z",
  "plugins": [
    {
      "name": "product",
      "title": "keel Product",
      "version": "0.1.0-beta.1",
      "source": "image",
      "dir": "/opt/keel-v2/plugins/product/0.1.0-beta.1",
      "engine": {
        "path": "/opt/keel-v2/plugins/product/0.1.0-beta.1/engine",
        "package": "keel_product"
      },
      "api": { "jars": ["/opt/…/api/keel-plugin-product.jar"], "lib": null },
      "web": { "entry": "web/index.js", "css": ["web/style.css"] },
      "content": "/opt/…/content",
      "migrations": null,
      "requires": { "sdk": 1, "keel": ">=0.15.0,<1.0.0" }
    }
  ],
  "problems": [
    {
      "name": "x",
      "version": "1.0.0",
      "dir": "…",
      "error": "needs plugin SDK 2, this keel has 1"
    }
  ]
}
```

`run/env` (POSIX sh, values single-quoted, sourced by keel-start):

```sh
KEEL_PLUGIN_PATHS='/opt/…/engine:/data/plugins/store/x/1.0.0/engine'
KEEL_PLUGIN_ADDONS='keel_product,keel_plugin_x'
KEEL_PLUGIN_LOADER_PATH='/opt/…/api/keel-plugin-product.jar,/data/…/api/lib'
```

Other subcommands:

- `keel-engine plugins install <file.kplug> [--off]`: unpacks a package into the store after checking its manifest
  and `files.sha256`. It refuses absolute paths, `..`, links and devices. It adds the `installed.json` entry
  (`source: "file"`) and prints "restart keel to load it".
- `keel-engine plugins list [--json]`: what would load, and the problems.
- `keel-engine plugins set <name> on|off`.

`--only <file>` (used by keel-start's fallback): resolve only the plugins (name + version) listed in that file; the
others go to problems with "left out: keel did not start with it last time".

## 5. Engine loading

- At start, the engine adds each `KEEL_PLUGIN_PATHS` folder to `sys.path` (inside the engine only).
- The add-on list is `KEEL_PLUGIN_ADDONS` plus `KEEL_ADDONS` (no duplicates). Everything else in
  `keel_engine/addons.py` stays the same.
- `models/cli.py` `project_env` drops `PYTHONPATH`, `KEEL_PLUGIN_PATHS` and `KEEL_PLUGIN_ADDONS` from project and agent
  commands.

## 6. api

- `keel.api.pluginhost.PluginHost` reads `$KEEL_DATA/plugins/run/resolved.json` once at start. With no file, there
  are no plugins (dev runs without keel-start).
- `GET /api/plugin-host` returns `{ sdk, mode, plugins: [{ name, title, version, source, parts: ["engine","api","web",…] }], problems: [...] }`.
- `GET /api/features` gets one more field, `plugins: [{ name, title, version, web: { entry, css } | null }]`, with
  absolute urls (`/plugins/<name>/<version>/web/index.js`).
- `GET /plugins/<name>/<version>/web/**` serves that folder for resolved plugins only, with
  `Cache-Control: public, max-age=31536000, immutable`. Everything else answers 404. `plugins` joins `NOT_WEB`.
- **Migrations:** a `FlywayMigrationStrategy` bean runs core's migrations first. Then, for each resolved plugin with
  `migrations`, it runs Flyway on `filesystem:<dir>` with table `<name>_schema_history`, `baselineOnMigrate(true)`,
  `baselineVersion("0")`. This happens before any bean that uses the database. Product keeps its own `ProductSchema`
  and has no `migrations` part.
- `POST /api/plugin-host/restart`: only when `KEEL_SUPERVISED=1`, else 409. It answers 202
  `{ "restarting": true }`, then exits the api with code **75** about 0.5 s later.

## 7. web

- `@keel/web-sdk` = `web/src/sdk/index.ts` (a Vite and TypeScript alias). It re-exports what plugin pages may use: at
  least everything `product/web` imports from `web/src` today, plus `definePlugin` and the types
  `AddonWeb`/`AddonPageProps`.
- `main.tsx` sets `window.__keel = { React, ReactDOM, ReactDOMClient, jsxRuntime, sdk }` before anything renders.
- The build writes the shim modules for `react`, `react-dom`, `react-dom/client`, `react/jsx-runtime` and
  `@keel/web-sdk` under `dist/assets/sdk/` with content-hashed names. `index.html` has the import map for these five
  names.
- `addons.ts` loads an add-on's web part from `features.plugins[].web.entry` with `import()` at run time, and adds its
  css links. The build-time `import.meta.glob` of `product/web` goes away. Pages, menu and lazy loading stay as they are.
- `product/web` imports only from `@keel/web-sdk` and `react`. `npm run build:product` (in `web/`) builds it as an ES
  library into `product/web/dist/` (`index.js`, `style.css`), with the five shared names external. Product's tests
  still run in web's vitest.

## 8. Runtime and packaging

- `api/build.gradle.kts`: `productPluginJar` makes `build/libs/keel-plugin-product.jar`, a thin jar of product's
  classes and resources. `productBootJar` goes away.
- `product/keel-plugin.yml` is the manifest. Its version equals `keel_product.VERSION` and `PRODUCT_VERSION` (a test
  checks this).
- `product/build-plugin.sh <out-dir>` stages engine/, api/, web/, content/, the manifest and `files.sha256`, and writes
  `<out-dir>/product-<version>.kplug` plus the unpacked folder `<out-dir>/product/<version>/`.
- **Dockerfile:**
  - The web and api stages always build core only.
  - With `EDITION=product`, the product stage builds the unpacked plugin into `/opt/keel-v2/plugins/product/<version>/`.
  - `/opt/keel-product` goes away. `ENV KEEL_PLUGINS_IMAGE=/opt/keel-v2/plugins`.
- **`docker/keel-start` (a supervisor loop):**
  1. resolve (if it fails: start with no plugins and say why)
  2. source `run/env`
  3. start the engine with the plugin env, and wait for its health
  4. start the api with `KEEL_SUPERVISED=1`: `java -Dloader.path=$KEEL_PLUGIN_LOADER_PATH -cp /opt/api/app.jar org.springframework.boot.loader.launch.PropertiesLauncher`
  5. after a healthy start, write `last-good.json`
  6. `wait -n`. Exit code 75 = stop both and go back to 1. Any other code stops the container, as today.
  7. If the api is not healthy within 90 s and the plugin set differs from `last-good.json`: start again with
     `--only last-good.json`.
- `keel2`: `--safe` sets `KEEL_PLUGINS=image`; `KEEL_PLUGINS` passes through.

## 9. Fence and versions

Tests that fail when core imports a plugin:

- engine: `keel_product` and the future plugin modules;
- api: `keel.product` and the future plugin packages;
- web: `product/web` and the future plugin components.

Today's couplings sit in an allowlist file per side. It may only shrink: a new import fails, and so does an entry that
is no longer used. Another test checks that `engine/keel_engine/config.py`, `engine/pyproject.toml`,
`api/build.gradle.kts`, `keel2` and `web/package.json` have the same version.

## 10. Who owns which files (parallel work)

| part       | owns                                                                                                                                                                                                                                               |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| engine     | `engine/keel_engine/pluginhost/**`, `engine/keel_engine/main.py`, `addons.py`, `models/cli.py`, `engine/tests/test_pluginhost*.py`                                                                                                                 |
| api        | `api/src/main/kotlin/keel/api/pluginhost/**`, `addons/Addons.kt`, `common/AppConfig.kt`, `api/src/test/kotlin/keel/api/PluginHost*`                                                                                                                |
| web        | `web/**` except `web/package.json` version, `product/web/**`                                                                                                                                                                                       |
| runtime    | `docker/keel-start`, `Dockerfile`, `api/build.gradle.kts`, `product/keel-plugin.yml`, `product/build-plugin.sh`, `product/engine/keel_product/__init__.py`, `product/api/**` (version only), `.github/workflows/ci.yml`, `keel2` (not its version) |
| fence      | `engine/tests/test_fence.py`, `engine/tests/fence_allowlist.txt`, `engine/tests/test_versions.py`, `api/src/test/kotlin/keel/api/FenceTest.kt` (+ allowlist), `web/src/test/fence.test.ts` (+ allowlist), `web/package.json` version               |
| integrator | this file, merges, e2e scripts (`product/e2e`, `e2e/`), the final checks                                                                                                                                                                           |
