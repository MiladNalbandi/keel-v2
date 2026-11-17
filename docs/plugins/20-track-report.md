# 20 · The plugin track: the final proof

**Branch:** `feat/plugin-step4` (the plugin track). Local only, not pushed, never merged into `main`, never tagged.

**What the track is:** keel **0.16.1** (main merged into the track, see
[12-step3-progress.md](12-step3-progress.md#port-of-01570161-main-merged-into-the-track)) **+ plugin steps 1–7**:

- every part is a plugin: `plugins/{map,wiki,ci,tasks,jira,db,review,git,graph,code,keelbot}` and `product/`;
- the marketplace (find, install, update, roll back, remove, agents ask);
- each plugin can live in its own repo, with `plugins.lock`;
- the core image (`:core`);
- other publishers (`keel-plugin dev`, the template repo, the guide).

**The rule:** the plugin build must work **the same** as the current release. The plugin work may only **add**. Each
addition is in `e2e/parity/allow.yml`, with its reason.

> Parity is checked against **0.16.1, the current release**. (The rule said 0.15.1 when that was the current release.)

## The result in one picture

```
 unit suites ──────────── all green (engine, plugins, Product, api, web, keel-plugin, scripts)
      │
 full image keel-v2:fin ─ parity with 0.16.1 ── PASSED (0 differences beyond allow.yml)
      │                   plugins-each ─────── every plugin alone, off, all: PASSED
      │                   review, marketplace, core (plugins off), repos: PASSED
      │
 product image ────────── Product e2e, plugin host e2e, plugins-each's Product part: PASSED
      │
 core image ───────────── core e2e: PASSED
      │
 native ───────────────── native update test, native build + start from it: PASSED
```

One small keel bug was found and fixed, and three bugs in the new plugins-each test (below). One flake of keel 0.16.1's own code went on the allow list, with proof.

## What was fixed

| what | why | fix |
| ---- | --- | --- |
| an agent could not ask for a plugin | `keel_plugin_request` is a **read**-level MCP tool (agents run `keel2 mcp --read-only`). 0.16.1's level guard came with the merge and treats every POST as a write, so `POST /api/plugins/requests` answered 403. The marketplace e2e found it ("an agent asks"). | `McpLevelGuard` lets this one route pass at the read level. It only opens an Inbox request; a person approves it, and the rule "agents may ask" still decides. New test: `McpLevelsApiTest` "an agent at the read level may ask for a plugin, never install one". |
| plugins-each failed on 0.16.1's own requests | The test project has no GitHub remote, so CI/CD's runs and Git's pull request answer 400. 0.16.1 does the same (parity lists them as notes). | `e2e/plugins-each/walk.mjs` does not count these two known 400s (and their console line). Every other failed request still fails the run. |
| plugins-each: Product's pages looked "missing" | `#/initiatives` and `#/teams` are add-on screens (`/api/features` → `screens`), opened by the add-on loader as in 0.16.1, not pages in the registry. The walk only knew the registry. | The walk counts the add-on screens as pages (and as Product's own pages). A test fix, not a keel fix. |
| plugins-each: a flow link opened the wrong project | The product image has a second project (`keel-product`, Product's own). The walk chose the first project, the flow was in `shop`, so `GET /api/projects/keel-product/flows/<id>` → 404. | The walk opens the e2e's project (`KEEL_WALK_PROJECT`); the e2e picks `shop` by id. A test fix. New options `--name` and `--port` let several runs go side by side. |

```
 agent (keel2 mcp --read-only)
   │ keel_plugin_request("db", reason)
   ▼
 POST /api/plugins/requests ── McpLevelGuard ── before: needs "write" → 403
   │                                            now:    needs "read"  → ok
   ▼
 Inbox card "an agent asks for db" ── a person says yes ── install ── restart
```

## The parity table

`python3 e2e/parity/parity.py --a ghcr.io/miladnalbandi/keel-v2:0.16.1 --b keel-v2:fin` (the final image, built the
normal way: agent CLIs and CodeGraph inside).

```
               same  different  allowed  new in B  skipped
  api [off]     138          0        2        12        4
  mcp [off]       3          0        3         0        0
  layout [off]    4          0        0         0        0
  web [off]      26          0        3         0        0
  api [on]      138          0        2        12        4
  mcp [on]        3          0        3         0        0
  layout [on]     4          0        0         0        0
  web [on]       26          0        3         0        0
  flow            2          0        0         0        0
  api [flow]    159          0        1        12        2
  mcp [flow]      3          0        3         0        0
  layout [flow]   4          0        0         0        0
  web [flow]     26          0        3         0        0

PASSED: 0 difference(s) not on the allow list (6.5 min)
```

- **mcp**: `keel2 mcp` at read, `--write` and `--admin`: 0.16.1's tools in 0.16.1's order, then the four marketplace
  tools at the end (allowed). `keel_actions` is the same.
- **layout**: both projects (`shop` with keel files in `docs/`, `notes` with the new default `.keel/`): the same layout,
  the same files on disk, the same `.git/info/exclude`.
- **flow**: a `change` flow on the fake model on each project stops at the same gates.
- **new in B**: the 12 new endpoints of steps 1–4 (plugin host, approvals, kinds, sections, plugin settings, the
  marketplace). **allowed**: `/api/features` lists the plugins' web parts; the Settings › Plugins menu entry and page;
  the model probe (next part).
- **skipped**: endpoints that need a value neither keel has (a spec page; a file's history before a flow wrote one).

### The flake, and the proof

Parity ran **4 times** on the track's images:

| run | image | result |
| --- | ----- | ------ |
| 1 | first `fin` | PASSED |
| 2 | `fin` again (with the api fix) | 4 differences: codex's model list (8 on A, 3 on B) and Copilot's label on the Connections page |
| 3 | the same, run again (a flake?) | 3 differences: Copilot's label, **the other way round** (A had the CLI's, B the built-in one) |
| 4 | the same, with the two allow entries | PASSED |

Why: keel asks the CLIs for their models (`codex debug models`, `copilot --help`) and waits **4 seconds**. On a busy
computer a cold CLI is slower. Then keel shows its **built-in** list for 10 minutes. This is 0.16.1's own code
(`models/catalog.py` is the same on the track), and it happens on A and on B. The proof:

| | 0.16.1 | `keel-v2:fin` |
| --- | --- | --- |
| claude | 2.1.197 | 2.1.197 |
| codex | 0.162.1 | 0.162.1 |
| copilot | 1.0.98 | 1.0.98 |
| opencode | 1.19.0 | 1.19.0 |
| codegraph | 1.6.2 | 1.6.2 |
| `codex debug models` | 8 models | 8 models |

So the two new allow entries (`allow.yml`) name only these lines: codex's `efforts` count and the Copilot model
button's label. The other allow entry for `/api/providers/models` (a newer CLI may list more models) matched nothing
this time: the CLIs are the same.

## Each plugin alone, off and with all

`python3 e2e/plugins-each/e2e.py --image keel-v2:fin --product-image keel-v2:fin-product` on the throw-away keel
`keel-each` (127.0.0.1:8091). For each plugin it turns plugins on and off with `keel-engine plugins set`, restarts keel
in the same container, then checks: `/api/plugin-host` (exactly the expected plugins, no problem), every plugin's api
(answers when on, 404 when off), the MCP tools of db, git and ci (there only when on; keel's own tools always the
same), the project, a `change` flow to its first gate, and the web in headless Chromium (every menu page with its tabs:
no console error, no failed request, no request to an off plugin, no link to a missing page; a plugin's pages there
only when it is on).

- **alone** = only this plugin on (and what it needs: ci → git, jira → tasks, review → keelbot, product → tasks + jira)
- **off** = this plugin off (and what needs it); all others on

Full image `keel-v2:fin` (run of 2026-10-15, 54.8 min for both phases; the walk already had fix 2 below):

| plugin  | alone            | off              | differences found                                          |
| ------- | ---------------- | ---------------- | ---------------------------------------------------------- |
| code    | ok (20 checks)   | ok (26 checks)   | none                                                       |
| git     | ok (21 checks)   | ok (26 checks)   | none                                                       |
| review  | ok (26 checks)   | ok (26 checks)   | none (alone = with keelbot)                                |
| db      | ok (21 checks)   | ok (26 checks)   | none                                                       |
| ci      | ok (26 checks)   | ok (26 checks)   | none (alone = with git)                                    |
| keelbot | ok (21 checks)   | ok (26 checks)   | none (off = review off too)                                |
| map     | ok (22 checks)   | ok (26 checks)   | none                                                       |
| graph   | ok (23 checks)   | ok (26 checks)   | none                                                       |
| wiki    | ok (24 checks)   | ok (26 checks)   | none                                                       |
| tasks   | ok (25 checks)   | ok (26 checks)   | none (off = jira off too)                                  |
| jira    | ok (26 checks)   | ok (26 checks)   | none (alone = with tasks)                                  |
| all on  | ok (19 + 26)     |                  | none                                                       |
| core    | ok (20 checks)   |                  | none (every plugin off)                                    |

A plugin's own pages (the page registry): code → `repo`, keelbot → `helper`, map → `map`, graph → `graph`, wiki → `wiki`,
tasks → `tasks`. Git, db, review, ci and jira have no page of their own: they add slots and views to other pages.

Product image `keel-v2:fin-product` (`--only product`, on `2bd1d9f`; 198 checks, 0 issues, 15.6 min). Here "off" means
every other plugin of the product image is on.

| plugin  | alone          | off            | differences found                                   |
| ------- | -------------- | -------------- | --------------------------------------------------- |
| tasks   | ok (21 checks) | ok (23 checks) | none (off = jira and product off too)               |
| jira    | ok (22 checks) | ok (23 checks) | none (alone = with tasks; off = product off too)    |
| product | ok (22 checks) | ok (23 checks) | none (alone = with tasks and jira)                  |
| all on  | ok (20 + 23)   |                | none                                                |
| core    | ok (21 checks) |                | none (every plugin off)                             |

Product's own pages are `initiatives` and `teams` (add-on screens), there only when Product is on. The earlier Product
runs found only the two test bugs (fixes 3 and 4 above); no keel difference.

## Every check

| suite | result |
| ----- | ------ |
| engine (`uv run pytest`) | 977 passed |
| plugin engines (`scripts/test-plugins.sh`) | ci 17, db 27 (+8 skipped), git 22, graph 28, keelbot 43, map 36, review 15, wiki 4: all passed |
| Product engine | 15 passed |
| api (`test pluginTest productTest pluginJars productPluginJar`) | 316 passed (core 214, code 20, keelbot 17, jira 15, tasks 14, review 9, wiki 7, product 5, db 4, ci 3, graph 3, map 3, git 2); after fix 1: core 215, so 317 |
| web (tsc, vitest, build, build:plugins, build:product) | 83 files, 551 of 551 tests; tsc and the three builds ok |
| tools/keel-plugin | 185 passed |
| `scripts/no-v1.sh --strict` | clean |
| `docker/tests/keel-start.test.sh` | all checks passed |
| `docker/tests/keel2.test.sh` | all checks passed |
| `scripts/test-keel2-native.sh` (native update, fake releases) | all checks passed |

| e2e (image) | result |
| ----------- | ------ |
| parity vs 0.16.1 (`fin`) | PASSED (see the table above; 4 runs, the flake explained) |
| plugins-each, full image (`fin`) | PASSED: every plugin alone and off (table above) |
| review (`fin`) | PASSED, 23 checks |
| marketplace (`fin`) | first FAILED at "an agent asks" (403, fix 1); after the fix PASSED, 69 checks |
| core with `KEEL_PLUGINS=off` (`fin`) | PASSED, 37 checks |
| repos (plugin repos, their CI and release, `plugins.lock`, a `PLUGINS=lock` image) | PASSED, 50 checks |
| Product e2e (`fin-product`) | PASSED, 27 checks |
| plugin host (`e2e/plugins`: core-only, image, install, safe, broken, upgrade from 0.14.0-product-beta) | PASSED, 36 checks |

| e2e (image) | result |
| ----------- | ------ |
| core image `keel-v2:fin-core` (`e2e/core --only core`) | PASSED, 37 checks, first try: no plugin inside, no plugin page, "Start with a set" with its four sets, every core page without errors or dead links, the launcher and key sheet without plugin pieces. Image 3.7 GB (978 MB packed) |
| native mode (`scripts/build-native.sh`, a real `keel2 start --native` with a temp `KEEL2_HOME` on its own port) | PASSED: the native package holds the 11 plugins; `/api/plugin-host` lists them with no problems; the plugin apis answer; `keel2 mcp` lists the db, git and ci tools; `--native --core` is refused with a clear message; `keel2 stop` stops it all |

## What is left only for Milad

These steps go outside this computer (keys, GitHub, releases). Nothing here did them.

1. **Keys and secrets:** `docs/plugins/tools/setup-signing-keys.sh --org` (try `--dry-run` first). It stores the
   catalog key and the publisher key as GitHub secrets (the publisher key also as keel-studio's organisation secret
   `KEEL_PUBLISHER_KEY`) and writes only the public keys. Commit `content/trust/*.pub` and `publishers/keel.yml`.
2. **The token** `KEEL_STUDIO_TOKEN` (Contents: read and write on the `keel-plugin-*` repos), with `gh secret set`.
3. **The marketplace repo** `keel-studio/keel-marketplace`: fill it with [marketplace-repo/](marketplace-repo/), and
   turn on **GitHub Pages** (Source: GitHub Actions). The repos must be public.
4. **The plugin repos and the template:** `docs/plugins/tools/split-plugin.sh <name> <dir> --push` per plugin (it only
   prints the push commands), and `split-plugin.sh --template … --push`, then mark the template repo as a template.
5. **First releases:** tag `v1.0.0` in each plugin repo (its `release.yml` signs and uploads the `.kplug`), then run
   the marketplace's `sync.yml` once.
6. **Switch to `plugins.lock`:** `scripts/update-plugins-lock.sh <name>…`, commit the lock; when every plugin has a
   release, build with `PLUGINS=lock` (a build argument in `docker.yml`), and later remove `plugins/` from keel-v2.
7. **Publish `:core`:** `docker.yml`'s `core` job pushes `:<version>-core` (and `:core`) on a release tag. The first
   tag with the track makes it.

## Open decisions

1. **The defaults** in [README.md](README.md#open-questions-for-milad), questions 1–5 and 7–10 (knowledge in core,
   Code's split, the parts not in your list, `:latest` vs `:core` at 1.0, signing, other publishers, restart, the
   CodeGraph CLI, plugins' Python libraries). The track uses the defaults of
   [15-step4-report.md](15-step4-report.md#the-defaults-contract-0-milad-can-change-them).
2. **`keel2 start --native --core` is refused** ("--core is for the Docker image"). Native mode always has every plugin;
   `--safe` or `KEEL_PLUGINS=off` gives fewer. Is that right, or should native mode get a core-only release too?
3. **MCP tools whose route is a plugin's:** without the Code, Wiki or Graph plugin, `keel_read_file`, `keel_specs`
   with a path, `keel_knowledge` with a section and `keel_search` for symbols answer the api's **404**. They are still
   listed (0.16.1's list must stay the same with every plugin on). Keep it so, or hide them / give a clearer message
   when the plugin is off?
4. **Agents of plugins on the Agents page:** step 7 shows the agents of plugins installed from the marketplace or a
   file; the image's plugins keep 0.16.1's list exactly (so Product's image does not change). Show all plugins' agents
   later? (Content-only plugins load their workflows and agents since step 7.)

## Disk

The run built one image at a time and removed each one after its tests. Free space on the data disk: 58 GB at the start of the run, 38 GB at the end (all test images, containers, volumes and scratch folders removed; only the released 0.16.1 and 0.15.7 images stay).

## keel Product in a native try-out keel (a local catalog)

Milad's native try-out keel (`KEEL_NAME=keel-plugins`, port 8188) did not show keel Product: Product is not inside
the native package (only the 11 plugins are), and the public catalog is not set up yet. So it got a **local signed
catalog**, made the way the real one is made:

```
~/.keel2/keel-plugins/local-catalog/
  keys/        local-catalog.key + .pub, local-publisher.key + .pub   (keel-plugin keygen; mode 0600, never in a repo)
  market/      publishers/keel.yml (the local publisher key), plugins/product.yml, revoked.yml
  releases/    product-0.1.0-beta.1.kplug + .minisig (product/build-plugin.sh, keel-plugin sign)
  www/         v1/index.json + .minisig (keel-plugin index, signed with the local catalog key) and the release files,
               served by `python3 -m http.server 8390 --bind 127.0.0.1` (pid in server.pid)
```

- keel reads it as the source **"Local catalog (this Mac)"** (`http://127.0.0.1:8390/v1/index.json` + the local
  catalog key), added through `PUT /api/plugins/sources`. http to this computer needs `KEEL_MARKETPLACE_ALLOW_HTTP=1`,
  so keel was restarted once with it (with Milad's OK); the **Restart keel** button keeps it, but a `keel2 restart`
  must set it again: `KEEL_MARKETPLACE_ALLOW_HTTP=1 KEEL_NAME=keel-plugins ~/.keel2/keel-plugins/keel2 restart`.
- The Marketplace shows **keel Product 0.1.0-beta.1** with the **beta** label, "by keel ✓", **Runs code in keel** and
  **needs Tasks, Jira**. Its install dialog lists Product and "Also turns on what it needs: jira, tasks" (both are in
  the package, off). It was **not installed**: Milad installs plugins himself.
- With this catalog the Marketplace's "keel set" lists only what the catalog lists (Product). The real catalog will
  list all twelve.
- When the public catalog exists (Milad's one-time steps), remove this source in Sources and rules and stop the server.
