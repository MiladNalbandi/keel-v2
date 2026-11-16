# 21 · A marketplace for workflows (and, next, rule packs)

Milad's request: "make the marketplace also for workflows". The plugin marketplace (step 4) gets a second kind of item:
a **workflow** you can find, read, install into a project, update and remove, and publish. The design leaves room for a
third kind, **rule packs** (keel 0.16.2 "Rules" builds them on `main`'s line; not built here). Plugin track only:
`plugin-base`, never `main`, no tag, no release.

## 1 · The choice: a new kind of catalog entry, not a `.kplug`

```
 plugin  (step 4)                                  workflow  (this plan)
 ──────────────────                                ──────────────────────
 .kplug: code + content, keel-wide                 one YAML file: the workflow, per project
 install → /data/plugins/store, restart            install → the workflows table, no restart
 a plugin's own repo, release per tag              a publisher's repo, release per tag (or the catalog repo)
 signed (minisign), sha256, trust, revoked,        THE SAME: signed (minisign), sha256, trust, revoked,
 expiry, publisher keys                            expiry, publisher keys
```

**Recommended:** a catalog entry of kind `workflow` whose file is the signed workflow YAML. It reuses everything that
matters for safety (signatures, publisher keys, verified, revoked, expiry, the Sources and the rules), and the install
path keel already has for its built-in library (`WorkflowService.install`, the **Install review**: steps, gates, agents,
MCP servers, shell commands, files it changes, warnings). A content-only `.kplug` would also work, but it installs
keel-wide, needs a restart, cannot be per project and is not an editable copy — so it fits a workflow badly.

## 2 · The catalog format (format 1 stays readable by today's keel)

The index gets new top-level lists next to `plugins`. A keel that does not know them ignores them.

```json
{
  "format": 1, "built": "…", "expires": "…", "publishers": { … }, "plugins": [ … ], "revoked": [ … ],
  "workflows": [
    { "name": "release-notes", "kind": "workflow", "title": "Release notes", "publisher": "ana-k",
      "summary": "…", "category": "docs", "tags": ["release"], "repo": "https://github.com/ana-k/keel-workflows",
      "versions": [
        { "version": "1.2.0", "released": "2026-10-15",
          "requires": { "keel": ">=0.16.1", "plugins": { "git": ">=1.0.0" }, "agents": ["release-writer"] },
          "url": "https://github.com/ana-k/keel-workflows/releases/download/release-notes-v1.2.0/release-notes-1.2.0.yaml",
          "sha256": "…", "size": 4210,
          "review": { "steps": 6, "gates": 2, "agents": ["release-writer"], "mcp": [], "commands": ["git log …"],
                      "edits_files": true, "keel_rules": true } } ] } ],
  "rules": []
}
```

- `kind` is in every entry (`plugin` for the old list when absent), so one search and one Sources list serve all kinds.
- `revoked` entries get an optional `kind` (default `plugin`).
- `review` is the Install review the catalog's sync computed (keel computes it again at install; a difference stops the
  install).
- **Rule packs next:** a `rules` list with `kind: "rules"` and a YAML file (`content/rules/*.yaml` on `main`'s line),
  installed per project like a workflow. Nothing here builds it; the format, the search, the Sources and the publishing
  tool are written so it is one more kind, not a new system.

## 3 · Install, update, remove (per project)

| action | how |
| --- | --- |
| **install** `{name, version?, project, scope: project\|all}` | engine (marketplace): choose the newest fitting version (keel version, needed plugins), refuse revoked / expired / unverified-and-not-allowed, download (max 256 KB), check sha256 and the publisher's signature → api `WorkflowService.installFromMarketplace` validates it (the engine validator), computes the Install review, inserts the row with origin `marketplace`, the catalog, name and version |
| **update** | the same for a newer version: a new version of the same row (the old one stays in `workflow_versions`, so Roll back works); if the person changed their copy, keel asks: keep my changes (a new copy) or replace |
| **remove** | deletes the row (as Delete does today); runs and their history stay |
| **needs** | a needed plugin that is not on → the dialog offers to install / turn it on first (the plugin path of step 4); a needed agent that keel does not have → listed, the install is refused with the agent's name (agents come with plugins, or as custom agents) |

No restart: a workflow is data, not code. Trust level of every workflow: **content only** (it runs only through keel's
flow engine, its agents and gates), but the review shows the shell commands and the files it changes, in red when it
runs commands.

**Data:** a migration adds to `workflows`: `market_catalog`, `market_name`, `market_version` (null for others). It
takes the number after the track's last one (V17 today); a port from `main` renames the track's unreleased migrations
when `main` releases one with the same number, as before.

## 4 · The web

- **Settings › Plugins › Marketplace** gets a kind switch: **Plugins · Workflows** (later **Rules**). The Workflows
  view: search, categories, cards (title, publisher ✓, version, summary, the review in short: "6 steps · 2 gates ·
  runs 1 command", needs), Install / Installed in this project / Update.
- **A workflow's page** (`#/plugins/workflow/<name>`): the steps as a small graph (the Workflows page's own graph
  component, read-only) and the YAML (folded), the Install review (agents, MCP, commands, files it changes, warnings),
  needs, versions, and the checks keel runs. Install asks: this project or all projects.
- **Build › Workflows:** an installed one shows the badge **"from marketplace · 1.2.0"** (and "update 1.3.0 ready"),
  with Update and Remove in its menu.
- **Publish:** a workflow's menu gets **Publish to marketplace**: it shows what the package will hold (the YAML, the
  name, version, summary, needs, the Install review), lets the person fix them, and downloads a folder (zip) with
  `<name>-<version>.yaml` and the marketplace entry `workflows/<name>.yml`, plus the exact `keel-plugin` commands to
  sign it and open the pull request. Signing stays with the person (their key never touches keel).

## 5 · Tools and the marketplace repo

- `keel-plugin workflow lint <file>` (schema checks without keel), `keel-plugin sign` (as for plugins), `keel-plugin
  index` reads `workflows/*.yml` and the releases' `*.yaml` + `.minisig` (and later `rules/`).
- The marketplace repo gets `workflows/` (one entry file per workflow) next to `plugins/`; `sync.yml` and
  `check-pr.yml` handle both.
- A workflow's file can come from a publisher's repo release (like a plugin), or, for small community workflows, from
  the marketplace repo itself (`workflows/<name>/<version>.yaml` + `.minisig`). Recommended: both, the same checks.

## 6 · Work, files and tests

| part | files | tests |
| --- | --- | --- |
| engine: catalog kinds, workflow install path, search across kinds, routes `/marketplace/workflows*` | `keel_engine/marketplace/{catalog,workflows,routes}.py` | index with workflows (good, bad signature, revoked by kind, expired, broken entry left out), search, the download checks, older index without `workflows` |
| api: `/api/marketplace/workflows`, `/api/projects/{pid}/workflows/market/{install,update,remove}`, the migration, the badge fields on `GET /projects/{pid}/workflows`, publish bundle `GET /workflows/{wid}/publish` | `keel.api.marketplace`, `keel.api.workflows` | install into a project and all projects, update (unchanged, changed copy), remove, needs (plugin off, agent missing), review mismatch refused |
| web: the kind switch, cards, the workflow page, the badge, Publish | `pages/Plugins.tsx`, new `pages/MarketWorkflow.tsx`, `pages/Workflows.tsx` | vitest for each |
| tools: `keel-plugin workflow lint`, `index` with workflows | `tools/keel-plugin` | the format, sign → index → verify |
| e2e: `e2e/marketplace` gets a workflow round | `e2e/marketplace/e2e.py` | search, install into a project, it runs on the fake model, update, remove, a bad signature |
| parity | — | a full keel with no workflow catalog: 0 differences beyond the allowed additions |

## 7 · Risks

- **A workflow can run shell commands** (`run:` steps) and edit files: the review must show it before install; keel's
  guard and gates still apply. Signed by a known publisher, but not "safe" by itself.
- **Agents a workflow names may not exist** in this keel: refused with a clear message (no silent missing step).
- **Migration numbers** can clash with `main` again (the Rules work may add one): the port renames the track's.
- **Rules on `main`** may shape rule packs differently: this plan only reserves the `rules` list and the kind; the
  details follow `main`'s 0.16.2.

## 8 · Open questions (recommended answers)

1. Install a marketplace workflow for **one project or all**? → Ask each time; default **this project**.
2. When the person has **changed** an installed workflow and an update comes? → Ask: **keep my copy** (default) or
   replace with the new version.
3. **Community workflows that run shell commands:** allowed? → Yes, with the red warning in the review; the rule
   "allow unverified publishers" does not block content, but the review always shows commands.
4. Where do workflow files live? → A publisher's repo release **or** the marketplace repo; both checked the same way.
5. keel's built-in **library** (`library/*.yaml`)? → Keep it (offline, in the image). Later it can be a catalog of its
   own.

## 9 · The api shapes (as built)

What the engine and the api answer, as built on the plugin track (tests: `engine/tests/test_marketplace_workflows.py`,
`api/src/test/kotlin/keel/api/MarketWorkflowsApiTest.kt`, `tools/keel-plugin/tests/test_workflow.py`). A refusal is
`{error, hint?, ...}` in plain words, as for plugins (13-step4-contract.md §12).

### The catalog (format 1)

```
index    {format: 1, built, expires, publishers, plugins: [...], revoked: [...], workflows: [Workflow], rules: []}
Workflow {name, kind: "workflow", title, publisher, summary, category, tags: [str], repo?,
          versions: [{version, released, url, sha256, size (≤ 256 KB),
                      requires: {keel?: range, plugins?: {name: range}, agents?: [name]},
                      review: {steps, gates, agents: [str], mcp: [str], commands: [str], edits_files, keel_rules}}]}
revoked  [{name, version, why, kind?: "plugin" (default) | "workflow"}]
```

- An index without `workflows` / `rules` reads as before. `rules` holds rule packs (23-rules-in-marketplace.md §2).
  A plugin entry may say `kind: "plugin"`; another kind in the plugins list (or `plugin` in the workflows list) is left
  out with a problem.
- Per entry, as for plugins: the name rule, a version `x.y.z` or a pre-release, `https` (http only under the test rule),
  64-hex sha256, a listed publisher with a key, a size up to 256 KB, and a valid `review`; a broken version or entry is
  left out and listed. The plugin names `workflows`, `rules` and `refresh` are refused (they are keel's
  `/api/marketplace/<name>` routes).
- Workflow categories: `code`, `review`, `docs`, `release`, `quality`, `ops`, `other` (unknown reads as `other`).
- A source's status (`Source`) gains `workflows: int`.

### Engine routes

```
Hit  {name, kind: "workflow", title, publisher, publisher_title, verified, version: str|null (the newest that fits),
      summary, category, tags, review: {steps, gates, agents, mcp, commands, edits_files, keel_rules},
      needs: {plugins: {name: range}, agents: [str]}, fits: bool, refused: null|{error, hint}, source,
      + repo, latest, old: bool, why_not: str|null, missing_plugins: [str], trust: "content"}

GET  /marketplace/workflows?q=&category=   → {workflows: [Hit], sources: [Source], categories: [str]}
GET  /marketplace/workflows/{name}         → Hit + {versions: [{version, released, requires, review, url, size, sha256,
                                                   revoked: str|null, fits, why_not, missing_plugins}] (newest first),
                                                   checks: [str]}                       404 unknown, 400 a bad name
POST /marketplace/workflows/{name}/fetch {version?}
     → {name, version, title, publisher, catalog, yaml, review, requires,
        + publisher_title, verified, sha256, size, missing_plugins}
     409 old catalog / revoked / another keel; 404 no such version; 502 download, size, sha256, signature, not a
     workflow YAML (it must be UTF-8 and a mapping with a name and a list of steps)
GET  /marketplace/search?q=&category=&kind=plugin|workflow|all   (plugin: 0.16's answer as it was; all adds
     workflows and workflow_categories)
```

- **The newest that fits**: not revoked (by kind), keel's version in `requires.keel`, and every needed plugin running in
  this keel (run/resolved.json or an engine part) in its range. When every such version misses a plugin, the newest one
  for this keel is still chosen (`fits: false`, `why_not`, `missing_plugins`): its page shows it, an install refuses it.
- `refused` (Hit): the catalog is old, or no version is for this keel. A workflow's trust is `content`: the rule
  "allow unverified publishers" never blocks it.

### Api routes

```
GET  /api/marketplace/workflows?q=&category=&project=
     → the engine's answer; each hit + installed: {version, scope: "project"|"all", wid}|null, update: str|null
GET  /api/marketplace/workflows/{name}?project=
     → the engine's detail + installed + update + catalog_review (the catalog's review) +
       yaml (the newest fitting version, fetched and verified) + steps (a Workflow's steps) +
       review (keel's InstallReview {name, source, steps, gates, keel_rules, locked, agents, mcp, tools, commands,
               edits_files, valid, errors, warnings} + est_tokens)
     refused or no version: yaml null, steps [], review = the catalog's.
     409 {error, hint, differences: [str], review, catalog_review} when keel's review is not the catalog's;
     the engine's 4xx/5xx pass on as they are (a bad signature: 502).
POST /api/projects/{pid}/workflows/market/install {name, version?, scope: "project"|"all"}
     → {workflow: Workflow, review: InstallReview}
     409 {error, hint, missing_plugins}   a needed plugin this keel does not run
     409 {error, hint, missing_agents}    an agent keel does not have (requires.agents and the steps' agents)
     409 {error, hint, differences, review, catalog_review}   keel's review is not the catalog's
     409 {error, hint, installed: {version, scope, wid}}       the same name in the same scope ("installed already")
     422 the engine's validation; 400 a bad name or scope; 404 an unknown project or workflow
POST /api/projects/{pid}/workflows/market/{wid}/update {version?, mode: "keep"|"replace"}
     → {workflow: Workflow, review: InstallReview, kept: wid|null}
     unchanged copy (any mode), or replace: a new version of the same row (the old one stays in workflow_versions);
     changed copy + keep: a new row "<name> <version>" (its YAML's name changed), and the person's copy becomes
     their own (source "yours", no market) - kept is its id.
     409 the same version, or not a marketplace workflow; 400 a bad mode; the install's 409s as above.
Remove: DELETE /api/workflows/{wid} (as today).
GET  /api/projects/{pid}/workflows   each Workflow + market: {catalog, name, version, update: str|null} only when it
     came from the marketplace (every other row has no market key; a list without one never asks the engine).
GET  /api/workflows/{wid}/publish?name=&version=&summary=&category=&publisher=
     → application/zip, Content-Disposition: attachment; filename=<name>-<version>.zip:
       <name>-<version>.yaml   the export (without a top-level `version:` that is not a whole number)
       workflows/<name>.yml    name, title, publisher (default "you"), repo placeholder, category, summary, tags: []
       PUBLISH.md              keel-plugin workflow lint, keygen, sign, verify, gh release create or the marketplace
                               repo's own folder, and the pull request
     defaults: name = the marketplace name or a slug of the title; version = the next patch of the marketplace version,
     else 1.0.0; category = other. 400 a bad name, version or category. keel never signs.
```

### Data (V17)

`workflows` gains `market_catalog`, `market_name`, `market_version` and, beyond the plan, **`market_sha256`**: the
sha256 of the YAML keel wrote. A copy whose YAML no longer has it was changed by the person, so update knows to ask
keep or replace (the catalog's file of the installed version may be revoked or gone, so keel cannot fetch it again to
compare). The new rows have `source = "marketplace"`.

### Choices made while building

- **The review check** compares agents, MCP servers and commands as sets; for **edits** it refuses only when keel finds
  the workflow changes files and the catalog says it does not. The other way round (the catalog says more) is allowed:
  `keel-plugin` cannot know every custom agent, so it counts an agent keel does not have as one that changes files.
- **Workflow files** may not carry a semver `version:` (keel's engine reads `version` as a number): the marketplace
  version is in the file name (`<name>-<version>.yaml`) and the catalog. `keel-plugin workflow lint` says so, and the
  publish zip drops such a line.
- `requires.keel` comes from the entry's optional `keel:` range, `requires.plugins` from the YAML's `needs_plugins`
  (any version), `requires.agents` from the agents it names that keel does not have by itself.
- A workflow kept in the marketplace repo is served from Pages: `keel-plugin index --files-url <pages>` gives its URL
  `<pages>/workflows/<name>/<version>.yaml`, and the sync copies those files into the site.

### Tools and the marketplace repo

- `keel-plugin workflow lint <file> … [--name N --version V]`: name, `version` (a whole number or none), steps with
  unique ids, known kinds and names, agent steps with an agent, includes with a flow, `run:` with a command,
  `back`/`no`/`then` to a step, `needs_plugins` names, at most 256 KB; prints the review (steps, gates, agents, MCP,
  every `run:` command).
- `keel-plugin index` also reads `workflows/*.yml`, every `<name>-<version>.yaml` (+ `.minisig`, + `.json`) in the
  releases folder and `workflows/<name>/<version>.yaml` here; each is linted, signature-checked with its publisher's
  keys and gets its review; `revoked.yml` takes `kind: workflow`; `rules`: rule packs (23-rules-in-marketplace.md §2).
- `docs/plugins/marketplace-repo/workflows/` (README, `release-notes.yml`); `fetch-releases.sh` fetches a workflow's
  releases (tags `<name>-v<version>`); `sync.yml` passes `--files-url` and copies the repo's own workflow files to
  Pages; `check-pr.yml` lints and signature-checks each new or changed workflow and prints its review.

## 10 · Built (branch `feat/workflow-market`, separate from the 0.17.0 release)

- Web: the Plugins · Workflows switch, workflow cards, a workflow's page (steps graph, YAML, Install review, needs,
  versions, checks), the install dialog, the "from marketplace · version" badge with Update / Remove in Build ›
  Workflows, Publish to marketplace. Engine + api + tools as in §9. Checks on the merged branch: web 585/585, engine
  marketplace 109, api 325, tools 193, the marketplace e2e's workflow round 78 checks.
- On Milad's native try-out keel (:8188, built from this branch) the local signed catalog
  (`~/.keel2/keel-plugins/local-catalog`, see 20-track-report.md) lists two example workflows from keel's library,
  signed with the local publisher key: **Release notes 1.0.0** and **Flaky test fixer 0.9.2** (their semver
  `version:` line dropped, as lint asks). Not installed: Milad installs them himself.
