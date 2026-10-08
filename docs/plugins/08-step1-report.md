# 8 · Step 1 report: the plugin host

**Branch:** `feat/plugin-host` (from v0.15.0, with v0.15.1 merged; e2e green at `6a260bf`). Not pushed, not released.

## What keel can do now

```
 a plugin file (.kplug)                        keel (one image, never rebuilt)
 ──────────────────────                        ───────────────────────────────
 product-0.1.0-beta.1.kplug  245 KB  ──install──▶  /data/plugins/store/product/0.1.0-beta.1/
   keel-plugin.yml   (the manifest)                     │
   engine/keel_product (Python)                         │  restart (api exits 75, keel-start starts again)
   api/keel-plugin-product.jar (Kotlin, thin)           ▼
   web/index.js + style.css (62 KB, no React)    resolver → run/resolved.json + run/env
   content/ (5 workflows, 4 agents)                ├ engine: sys.path + KEEL_PLUGIN_ADDONS
   files.sha256                                    ├ api:    java -Dloader.path=… PropertiesLauncher
                                                   └ web:    import(url) + import map (one React)
```

- **One plugin host.** It reads plugins from the image (`/opt/keel-v2/plugins`) and from `/data/plugins`. It checks
  each one (manifest, sha256, plugin SDK 1, keel version, needs, cycles), puts them in order, and starts keel with
  them. A broken plugin is left out with the reason, and keel still starts.
- **Install from a file:** `keel-engine plugins install <file.kplug>`, then a restart from the api
  (`POST /api/plugin-host/restart`). The container keeps running.
- **Safe mode:** `keel2 start --safe` (`KEEL_PLUGINS=image`) leaves out what was installed into `/data`. With
  `KEEL_PLUGINS=off`, keel loads no plugin at all.
- **Fallback:** if the api does not start with a new set of plugins, keel-start starts again with the last set that
  worked (`last-good.json`).
- **keel Product is a real plugin.** The Product image is now "the normal image + the Product plugin folder".
  keel's own jar and web bundle hold no Product code.
- **The fence.** Tests fail when core imports a plugin. Today's couplings are in allowlists that may only shrink:
  engine 21, api 4, web 21. Another test keeps one version everywhere.

## Commands

```bash
docker exec <keel> keel-engine plugins list
docker exec <keel> keel-engine plugins install /tmp/product-0.1.0-beta.1.kplug
docker exec <keel> keel-engine plugins set product off
curl -X POST http://127.0.0.1:<port>/api/plugin-host/restart
product/build-plugin.sh out/            # builds out/product-<version>.kplug
```

## Tests

| what                            | result                                                                              |
| ------------------------------- | ----------------------------------------------------------------------------------- |
| engine (pytest)                 | 883 passed (83 new: resolver, installer, loader; plus fence and versions) |
| api (gradle test + productTest) | 195 passed (plugin host, migrations, web files, restart, fence) |
| web (tsc + vitest)              | 354 of 354 passed (on a very busy machine some slow UI tests time out, the same on `main`) |
| keel-start (stub tests)         | 10 of 10: restart 75, exit codes, fallback, resolver failure |
| e2e: Code Review (normal image) | 23 checks passed |
| e2e: Product (Product image)    | 27 checks passed |
| e2e: plugin host (6 scenarios)  | 36 checks passed: core-only, image, install from a file (+ Product's 27 checks on it), safe mode, broken plugins, upgrade from the published 0.15.1 |

## Found and fixed on the way

- **Restart race (2 causes):** the api's restart thread was a daemon. When Spring stopped, the JVM could end by itself
  with code 0 before it exited with 75, and keel-start then stopped the container. First fix: `isDaemon = false`.
  Not enough: a new thread copies the daemon flag of the thread that makes it, and Tomcat's request threads are
  daemons. Now the thread sets the flag itself. A test asks for the restart from a daemon thread; in the real image
  the api exited with 75 in 10 of 10 tries (before: 2 of 3 wrong).
- **The real reason with `--only`:** when keel starts again with the last good set, a plugin that fails its own
  checks (needs plugin SDK 2) now says why, not only "keel did not start with it last time".
- **keel-start stub test:** more time margin, so it does not fail on a busy machine.
- **e2e on a Mac:** Docker Desktop reports bind mounts as `/host_mnt/…`; Product's e2e (`--running`) and the
  upgrade scenario now use the real path.

## Choices made where the plan's questions had no answer yet

| question                              | chosen (for now)                                                |
| ------------------------------------- | --------------------------------------------------------------- |
| knowledge base and code graph         | the knowledge base stays core; Graph will add context to agents |
| git helper, GitHub connection, PR     | core                                                            |
| Quality, Skill hub, Jobs, Live, Tools | core for now                                                    |
| images                                | `:latest` = core + the keel set; `:core` at 1.0                 |
| restart after install                 | a call or a button, not automatic                               |
| signing                               | Ed25519 (step 4)                                                |
| other publishers                      | content-only first; unverified code off by default              |
| CodeGraph CLI                         | stays in the image until Graph moves (step 3)                   |
| Python libraries of plugins           | from PyPI at install time, pinned with hashes (step 4)          |

## Next

- **Step 2:** the core services and slots. Approvals (instead of KeelBot's in-memory ask broker), Workspace,
  FlowContributor, ContextProvider, InboxSource, ConnectionKind, SettingsSection, web slots. The fence allowlists
  shrink.
- **Step 3:** each part becomes a plugin folder in this repo, easy ones first (Map, Wiki page, Tasks + Jira, …).
- **Step 4:** the marketplace, signed install, and agents asking through the Inbox.
