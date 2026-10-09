# 15 · Step 4 report: the marketplace, signed install, agents can ask

**Branch:** `feat/plugin-step4`, on top of `plugin-base` (0.15.4 + steps 1–3). Local only, not pushed. It is on the
plugin track: it is never merged into `main` and never tagged until the track is finished and the same as the latest
release. The spec is [13-step4-contract.md](13-step4-contract.md); publishing is [14-publishing.md](14-publishing.md).

Step 4 only **adds**. For people who do not open the new page, keel looks and works like 0.15.4 (the parity e2e
checks it, below).

## What keel can do now

```
 a catalog (GitHub Pages)                keel (one image, never rebuilt)
 ────────────────────────                ───────────────────────────────
 v1/index.json  + .minisig   ──read──▶   check the catalog's signature (the source's key)
   hello 1.0.0 · 1.1.0 · 1.2.0           search: Control › Plugins › Marketplace, or an agent's tool
 hello-1.2.0.kplug + .minisig ─install─▶ download → sha256 → the publisher's signature → /data/plugins/store
                                           │
                                           ▼  "Restart keel" (the same container, about 10 s)
                                         loaded: engine routes, web page, workflows, agents
```

- **Find.** Control › Plugins › Marketplace searches the catalogs. Each card shows the trust level (content, web or
  code), the publisher, the newest version that fits this keel, and its permissions.
- **Install.** keel downloads the file, checks its sha256, checks its signature with the publisher's key, and checks
  that the manifest asks for the same permissions as the catalog. Nothing runs at install time. The plugin loads after
  a restart.
- **Update.** A newer version installs the same way. When it asks for **more permissions**, keel does not install it:
  it opens a request in the Inbox with the new permissions ("Update Hello to 1.2.0?"). A person approves it there.
- **Roll back.** keel keeps the version before. Roll back goes back to it.
- **Remove.** The plugin runs until the restart, then it is gone. Its tables stay; `data=delete` also removes its
  folder in `/data`.
- **Agents can ask, never install.** Four new tools in keel's MCP server, after all the old ones:
  `keel_marketplace_search`, `keel_plugin_info`, `keel_plugins_installed`, `keel_plugin_request`. A request becomes a
  `plugin-install` card in the Inbox: "Install Hello 1.2.0?", with the agent's reason, the trust level and the
  permissions. **Approve and install**, or **Deny**.
- **A workflow can say what it needs** (`needs_plugins: [db]`). When a needed plugin is not loaded, keel does not start
  the flow: it answers 409 and opens an install request in the Inbox.
- **Restart.** A banner "Changes wait for a restart" with a **Restart keel** button. keel-start starts keel again in
  the same container. The rule "restart by itself when no agent step runs" exists and is off.
- **keel refuses**: a catalog whose signature does not match (it keeps its last good copy), a package signed by another
  key, a package whose sha256 is not the catalog's, an old (expired) catalog, a revoked version, an unverified
  publisher's web or code plugin, and a plugin that asks for Python libraries.

## The defaults (contract §0; Milad can change them)

| question          | default in step 4                                                                                             |
| ----------------- | ------------------------------------------------------------------------------------------------------------- |
| images            | `:latest` stays core + the keel set; step 6 adds `:core`                                                      |
| signing           | Ed25519 in the minisign format; private keys only in GitHub Actions secrets                                   |
| other publishers  | content-only plugins from anyone; web and code plugins of an unverified publisher only with the rule on (off) |
| restart           | a **Restart keel** button; the rule "restart when idle" exists and is off                                     |
| CodeGraph CLI     | stays in the image                                                                                            |
| Python libraries  | not in step 4: a plugin that asks for them is refused with a clear message                                    |
| private catalogs  | later (a source has a URL and a key, no token yet)                                                            |
| a flow that waits | later: `keel_plugin_request` never blocks; `needs_plugins` refuses the start and asks                         |

## What Milad must do before the real marketplace works

The details are in [14-publishing.md](14-publishing.md), "What Milad must do once". In short:

1. **Make the keys:** `docs/plugins/tools/setup-signing-keys.sh` (try it first with `--dry-run`). It stores the two
   secret keys in GitHub secrets and writes only the public keys. Commit the three public key files on `plugin-base`.
2. **Make the token** `KEEL_STUDIO_TOKEN` (Contents: Read and write on the `keel-plugin-*` repos) and store it with
   `gh secret set`.
3. **Fill the marketplace repo** `keel-studio/keel-marketplace` with the files in
   [marketplace-repo/](marketplace-repo/).
4. **Turn on GitHub Pages** for it (Source: GitHub Actions). The repos must be public.
5. **First release:** push step 1's commit to `plugin-base`, then run `sync.yml` once by hand.

Until then the official catalog says "not set up yet", and nothing breaks.

## Checks

On the images built from this branch: `keel-v2:s4` (built the normal way, with the agent CLIs and CodeGraph) and
`keel-v2:s4-product` (`INSTALL_CLIS=0`, `EDITION=product`).

| what                                   | result                                                                                  |
| -------------------------------------- | --------------------------------------------------------------------------------------- |
| engine (pytest)                        | 923 passed; of them 118 for the marketplace and the MCP tools                           |
| api (test, the plugins' tests)         | 180 + 91 passed; two new tests (`MarketplaceApiTest`, `PluginHostApiTest`)              |
| web (vitest)                           | the marketplace tests 13 of 13; the whole suite 498 of 499 (note below)                 |
| `tools/keel-plugin` (pytest)           | 128 passed (with the signature test vector)                                             |
| **e2e: the marketplace** (new)         | **69 checks passed** (`e2e/marketplace/e2e.py`, about 1 minute)                         |
| e2e: Code Review (normal image)        | 23 checks passed                                                                        |
| e2e: Product (Product image)           | 27 checks passed                                                                        |
| e2e: plugin host (6 scenarios)         | 36 checks passed, incl. Product installed from its `.kplug` and the upgrade from 0.14.0 |
| **parity e2e: 0.15.4 vs `keel-v2:s4`** | **PASSED: 0 differences** beyond step 4's additions (table below)                       |

Note on the web suite: 498 of 499 on a quiet machine; the one left (a KeelBot chat test) passes when it runs alone.
While an e2e runs at the same time, more slow UI tests time out after 5 s, as before step 4.

### The marketplace e2e

`e2e/marketplace/e2e.py --image <image>` starts a throw-away keel `keel-mkt` (127.0.0.1:8096, its own volume, the fake
model). A small web server on the computer serves a test catalog; keel reaches it at `host.docker.internal`. The test
keys come from `keel-plugin keygen`. The test plugin `hello` (`e2e/marketplace/fixtures/hello`) has a workflow, an
agent, a page and one engine route. It is packed and signed as 1.0.0, 1.1.0 and 1.2.0 (1.2.0 asks for one more
permission), and `keel-plugin index` builds and signs the catalog.

```
 1 the catalog as a source        6 roll back 1.2.0 → 1.1.0, restart
 2 needs_plugins: 409 + request   7 remove, restart: gone
 3 search (api and agent tool)    8 refusals: index signature, package signature, sha256, expired, revoked
 4 install 1.0.0, restart: route,  9 an agent asks (keel's MCP server) → Inbox → approve → restart → loaded
   web part, workflow loaded      10 rule "agents may ask" off: the agent is told to ask the person
 5 update 1.1.0; 1.2.0 refused → request → deny; again → the web (Plugins, Inbox card) → approve
```

### Parity with 0.15.4

```
parity   A = ghcr.io/miladnalbandi/keel-v2:0.15.4
         B = keel-v2:s4

               same  different  allowed  new in B  skipped
  api [off]     122          0        1        12        0
  mcp [off]       0          0        2         0        0
  web [off]      27          0        2         0        0
  api [on]      122          0        1        12        0
  mcp [on]        0          0        2         0        0
  web [on]       27          0        2         0        0
  flow            1          0        0         0        0
  api [flow]    132          0        1        12        0
  mcp [flow]      0          0        2         0        0
  web [flow]     27          0        2         0        0

PASSED: 0 difference(s) not on the allow list (5.2 min)
```

- **api:** the only allowed difference is step 1's `/api/features.plugins`. "New in B": the six GETs of steps 1–2 and
  the six of step 4 (`/api/plugins/installed`, `/sources`, `/rules`, `/sets`, `/api/marketplace` and a search).
- **mcp:** both lists (read-only and `--write`) are 0.15.4's, in the same order, plus the four marketplace tools at
  the end.
- **web:** every page is the same; the two allowed rows are the menu (only the line `Plugins → #/plugins`) and the
  new page `#/plugins`. The Inbox is the same (it shows a `plugin-install` card only when one waits).

We ran it four times. Runs 2 and 4 passed as above. Run 1 had the flake below (fixed in the tool). In run 3 the codex
CLI did not answer `codex debug models` within 4 s on the new keel, so keel showed its short built-in list of codex
models (its first model is then GPT-5.5, not the CLI's first one) on the api and the Connections page. That is a slow
start of the CLI, with the same code in both images, not step 4.

The allow list (`e2e/parity/allow.yml`) has one entry for each step 4 addition, each with a reason that starts with
"step 4,": the four new GETs under `/api/plugins/…` and `/api/marketplace`, the four MCP tools at the end (exactly
those four), the menu entry Control › Plugins (exactly that line) and the page `#/plugins`. `GET /api/plugins` is the
same as in 0.15.4.

## Found and fixed on the way

- **`needs_plugins` never reached the engine.** The api builds the flow's start from its own `Workflow` type, and that
  type had no `needs_plugins`. So a workflow that needs a plugin started anyway. Now the type carries it (from the
  YAML, and from a plugin's template), only when a workflow has some; every other answer stays the same. A new api
  test checks the start body.
- **A plugin's own workflows were not listed.** The api showed an engine add-on's workflows only when keel Product's
  api part (`KeelAddon`) of that name was on. A marketplace plugin with an engine part and content, but no api part,
  loaded its workflow in the engine, and the project's workflows did not show it. Now a plugin keel-start loaded
  shows its workflows; Product still follows its mode. A new api test checks both.
- **The parity tool** now says `B adds at the end: <tools>` when B keeps the old MCP list and only adds tools after
  it, so the allow list can name exactly the four new tools. It also asks the six new GETs ("new in B").
- **A parity flake.** In the first run the flow round showed "Finished 3" on 0.15.4 and "Finished 4" on step 4 (the
  tabs of Live agents and Jobs): one step ran twice on one keel, which the tool already allows for these two pages,
  but not in a tab's name. Now a tab's count does not count there. The next run had no such difference anyway.

## Open

- **A plugin's agents are not on the Agents page.** The engine loads them and flows can use them, but the api lists
  agents only from `content/agents` and from a plugin's api part (`AgentFiles`, as KeelBot does). Showing every
  plugin's agents would also put keel Product's four agents on the Agents page of the Product image, where they are not
  today. So this needs a choice; left for Milad.
- **A content-only plugin** (no engine part) does not load its workflows and agents yet: the engine reads a plugin's
  content through its engine part (`ADDON["content"]`). 02-plugin-package.md says a content-only plugin is possible.
  The test plugin has an engine part, so the e2e does not hit this.
