# 23 · Rule packs in the marketplace (the third kind)

Milad's request: **rules** (keel's guard rules) in the Marketplace, next to plugins and workflows: a **Rules** tab to
browse, preview what a pack blocks and in which phases, which part of keel enforces it, install per project, update,
remove. Branch `feat/workflow-market` (the marketplace branch), not `main`.

**The rules session owns the pack format and the install api** (keel 0.17.2 "Rules": `docs/rules/pack-format.md`,
`GET /api/projects/{pid}/rule-packs`, install / uninstall). This page plans the marketplace side only. **The Rules tab
is built only after that session says the format is final.** Until then only the generic catalog kind (§2) is built.

**Update:** the format is final (keel 0.17.2, `docs/rules/pack-format.md`, schema 1) and the Rules tab is built on
branch `feat/marketplace-rules`: §5 "As built". §1's sample is the format the plan started from; the catalog, the tool
and the tab now read the final one.

## 1 · What a rule pack is (as the rules work had it when this plan started)

```yaml
id: no-new-dependencies
name: No new dependencies
description: Agents never add a package, inside or outside a flow.
rules:
  - id: no-install
    kind: bash                 # bash | edit | read | mcp
    how: regex                 # contains (bash) | glob (edit, read) | name (mcp) | regex
    match: ['\bnpm\s+install\s+[^-\s]', …]
    phases: all                # all | in-flow | none | [green, refactor]
    lanes: [web]               # optional
    except: ["**/*.lock"]      # optional (edit, read)
    reason: Adding a package is a person's decision.
```

A pack is installed per project as `.keel/rules/<id>.yaml`. **A pack rule only refuses**: it can never allow what keel's
own rules forbid. That makes it safe to share. It can still stop work, so the preview must show exactly what it blocks.

## 2 · The catalog: one generic "kind"

The signed index already has `plugins`, `workflows` and a reserved `rules: []` list (21-workflow-marketplace.md §2).
Step one, built now: the catalog code (keel's engine and `keel-plugin index`) handles kinds through **one table**, not
three copies:

| kind | list | file | max size | review | install goes to |
| --- | --- | --- | --- | --- | --- |
| plugin | `plugins` | `.kplug` | 200 MB | permissions, parts | the plugin store (restart) |
| workflow | `workflows` | `.yaml` | 256 KB | steps, gates, agents, commands, files | the workflows table (per project) |
| rules | `rules` | `.yaml` | 64 KB | rules by kind, phases, lanes, enforcement | the rules session's install api (per project) |

Each kind gives: its list name, file suffix, size limit, a `review` builder, and its checks. Search, Sources, revoked
(`kind`), expiry, signatures, publisher keys and trust stay shared. A new kind is one more table row and its install
call.

**Built** (branch `wk_generic`, from `feat/workflow-market`). The plugin and workflow answers are the same as before
(the engine's and the tools' tests ran unchanged, except the one that said `rules` reads as nothing; one more test
compares every plugin and workflow answer with and without rule packs in the index). The **Rules tab and the rules
install call are not built**: they wait for the rules session's final pack format.

| part | module | what it does |
| --- | --- | --- |
| engine: the table | `keel_engine/marketplace/kinds.py` | `TABLE` = `PLUGIN`, `WORKFLOW`, `RULES` (`Kind`: name, list, noun, suffix, max size, categories, the entry and version types, the hooks with each kind's own checks, `summarise`); `rules_review` checks a pack version's catalog review; `pack_review(doc)` builds it from a pack's YAML (read-only, tolerant of unknown fields) |
| engine: the catalog | `keel_engine/marketplace/catalog.py` | `parse_index` reads every list through the table (the shared checks: name rule, publisher and keys, version, sha256, https, size up to the kind's limit, `requires.keel` / `requires.plugins`; then the kind's own); `Index.rules`, `Index.entries(kind)`; revoked by `kind`; `matching` / `lookup_kind` for any kind |
| engine: rule packs | `keel_engine/marketplace/rulepacks.py` | `search`, `detail` (no fetch, no install) |
| engine: routes | `keel_engine/marketplace/routes.py` | `GET /marketplace/rule-packs`, `GET /marketplace/rule-packs/{name}`, `?kind=rules` on search |
| tools: the table | `tools/keel-plugin/keel_plugin/catalog.py` | `FILE_KINDS` = `WORKFLOWS`, `RULES` (`FileKind`): `rules/<name>.yml`, the releases' `<name>-<version>.yaml` packs (a mapping with a list of `rules` and no `steps`; anything else stays a workflow), the repo's own `rules/<name>/<version>.yaml` (`--files-url`); `revoked.yml` takes `kind: rules` |
| tools: lint | `tools/keel-plugin/keel_plugin/rules.py` | `keel-plugin rules lint <file> … [--name N --version V]` |

- **Rules entries** (the index's `rules` list): `{name, kind: "rules", title, publisher, summary, category, tags,
  repo?, versions: [{version, released, requires: {keel?}, url, sha256, size (≤ 64 KB), review}]}`, review
  `{rules: int, kinds: {kind: count} (adds up to rules), phases: [str], lanes: [str]}` (other review keys are dropped).
  Categories: `safety`, `code`, `git`, `files`, `secrets`, `other` (unknown reads as `other`). A broken entry or
  version is left out and listed with the words `rule pack <name> …`. A plugin may still not be called `workflows`,
  `rules` or `refresh`.
- **The pack review** (the engine's `pack_review` and the tool's `rules.review` give the same): every rule that is a
  mapping counts; its `kind` counts as itself (`unknown` when missing); a rule without `phases` reads as `all`; phases
  and lanes in the order they first appear; kinds in the order bash, edit, read, mcp, then others by name.
- **`keel-plugin rules lint`**: `id` (a name, the same as the marketplace name), `name`, `description` (text, optional),
  `rules` (a non-empty list) with a unique `id`, `kind` (bash, edit, read, mcp), `how` (contains, glob, name, regex;
  a warning when it is not the kind's: contains → bash, glob → edit/read, name → mcp), `match` (a pattern or a list;
  a regex must compile), `phases` (all, in-flow, none or a list of phase names), `lanes` / `except` (lists; `except` on
  bash or mcp is a warning), `reason`; at most 64 KB; a field it does not know is a warning. It prints the review.
- The marketplace repo's `sync.yml` also puts `rules/<name>/<version>.yaml` (+ `.minisig`) on Pages. Not yet:
  `fetch-releases.sh` and `check-pr.yml` for rule packs (with the Rules tab).

### Engine routes (as built)

`/marketplace/rules` was already the four marketplace rules (13-step4-contract.md §12; the api's `/api/plugins/rules`
calls it), so the packs are under **`/marketplace/rule-packs`**. A refusal is `{error, hint?}` as for plugins.

```
RulesHit {name, kind: "rules", title, publisher, publisher_title, verified, version: str|null (the newest that fits),
          summary, category, tags: [str], review: {rules, kinds: {kind: count}, phases: [str], lanes: [str]},
          requires: {keel?}, fits: bool, refused: null|{error, hint}, source, repo, latest, old: bool,
          why_not: str|null, trust: "content"}

GET /marketplace/rule-packs?q=&category=  → {rules: [RulesHit], sources: [Source], categories: [str]}
GET /marketplace/rule-packs/{name}        → RulesHit + {versions: [{version, released, requires, review, url, size,
                                              sha256, revoked: str|null, fits, why_not}] (newest first),
                                              checks: [str]}                  404 unknown, 400 a bad name
GET /marketplace/search?q=&category=&kind=plugin|workflow|rules|all
    kind=rules → the rule-packs answer; kind=all adds rules and rules_categories (next to workflows and
    workflow_categories); kind=plugin (the default) and kind=workflow answer as before.
```

- **The newest that fits**: not revoked (`kind: rules`) and keel's version in `requires.keel`. `refused`: the catalog
  is old, or no version is for this keel. A pack only refuses, so its trust is `content`: the rule "allow unverified
  publishers" never blocks it.
- A Source (`sources`) is unchanged: it has no rule pack count yet (the plugin and workflow answers stay the same);
  the Rules tab can add `rules: int` there.
- No `POST …/fetch` or install route: the Rules tab will fetch the verified YAML and call the rules session's
  `POST /api/projects/{pid}/rule-packs`. (Built since: `POST /marketplace/rule-packs/{name}/fetch`, §5.)

## 3 · The Rules tab (after the format is final)

- **Marketplace › Rules:** cards (title, publisher ✓, version, description, "3 rules · bash 1 · edit 2 · in every
  phase", Install / Installed here / Update).
- **A pack's page:** every rule in a table: kind, what it matches (the patterns), how, **phases**, lanes, except, the
  reason; and **who enforces it**:
  - **before the action** — the PreToolUse hook (Claude Code), the ToolBox (other engines) and keel's MCP guard refuse
    a matching `bash`, `read`, `edit` or `mcp` call before it runs;
  - **after the step** — the diff guard checks a step's changed files against the `edit` rules and fails the step.

  The exact list per engine comes from the rules session's api (each engine's real support), not from this page.
- **Install** into this project (or all projects): the rules session's `POST …/rule-packs` with the verified YAML and
  the marketplace's name, version and catalog (so Build › Safety & History can show "from marketplace · 1.0.0"). **Update** and
  **Remove** through the same api.
- Trust level: **content**. The rule "allow unverified publishers" does not block a pack (it only refuses), but the
  preview always shows everything it blocks.

## 4 · Open questions (recommended answers)

1. Per project or for all? → Like workflows: ask, default this project (if the rules api supports "all").
2. A pack that blocks something keel's own flow needs (for example `git commit`)? → The preview warns ("this blocks a
   step keel runs: commit") using the rules api's check; install still allowed.
3. Can a project's own changes to an installed pack be kept on update? → Follow the rules session (packs are files in
   `.keel/rules/`, so "keep my copy" = keep the file and skip the update).
4. Publishing a pack → the same Publish flow as workflows (a zip with the YAML and the entry, signing by the person).

## 5 · As built (branch `feat/marketplace-rules`)

`feat/workflow-market` (keel 0.17.0 + the workflow marketplace + the kinds table) merged with the rules work (0.17.2,
the final pack format and its install api). Not on `main`, not released.

### The format

The marketplace ships the pack file **unchanged**: the file a publisher signs is the file keel installs as
`.keel/rules/<name>.yaml`. It is keel's final format (`docs/rules/pack-format.md`, schema 1):

```yaml
schema: 1
kind: rules
name: no-new-dependencies       # = the pack's id = its file name = the marketplace name ([a-z0-9-], at most 64)
title: No new dependencies
version: 1.0.0                  # = the marketplace version
publisher: keel                 # = the entry's publisher (keel itself defaults it to local; a marketplace pack names it)
summary: Agents never add a package.
requires: {keel: ">=0.17.2", plugins: {db: ">=1.0.0"}}      # plugins: a map or a list of names
rules:
  - {id: no-install, kind: bash, match: [npm install, pnpm add], reason: Adding a package is a person's decision.}
```

- **Names**: a rules entry's name follows the pack rule (`kinds.PACK_NAME`, `^[a-z0-9][a-z0-9-]{0,63}$`), not the
  plugin rule (a digit first and 64 characters are fine). The api checks it the same (`Marketplace.PACK_NAME`).
  `rule-packs` joins the names a plugin may not take (`/api/marketplace/rule-packs` is keel's route).
- **The review** (engine `kinds.pack_review`, tool `rules.review`, the same answer): `{rules, kinds: {kind: count},
  phases, lanes, blocks}`. New: `blocks`, what each rule blocks, one per rule in the pack's order:
  `{id, kind, match: [text], how, phases, lanes?, except?}` (`how` is the kind's default when the rule has none; one
  phase name reads as a list of it). The catalog's `blocks` is optional (an index built before reads without it); when
  it is there it must be one item per rule.
- **`keel-plugin rules lint`** checks the final format: `schema: 1`, `kind: rules`, `name` (the marketplace name),
  `title`, `version` (the marketplace version), `publisher` (required here), `summary`/`stack` (text), `requires`
  (`keel` a range; `plugins` a map or a list of plugin names), rules (`id` [a-z0-9-] and unique; `kind`; `match` a
  text or a list; `how` optional; `phases` all / in-flow / none / a phase / a list; `lanes` api and/or web; `except`;
  `reason`). It prints the review and one `blocks` line per rule.
- **`keel-plugin index`**: a rule pack version's `requires` is the pack's own (`keel` from the pack, else the entry's
  `keel:`; `plugins` as a map); a file whose `publisher` is not the entry's is left out ("its publisher is bob, the
  entry rules/x.yml says keel"); release files may use pack names (`FILE` in rules.py).

### Engine (keel_engine)

```
POST /marketplace/rule-packs/{name}/fetch {version?}
  → {name, version, title, publisher, catalog, yaml, review, requires, publisher_title, verified, sha256, size}
```

`rulepacks.fetch`: the newest version that fits (or `version`), refused 409 for an old catalog, a revoked version or a
version for another keel; the download (≤ the catalog's size, ≤ 64 KB) and its sha256 (502), the `.minisig` with one of
the publisher's keys (502), UTF-8, then **keel's own parser** (`rules.packs.parse_pack`): not a pack → 502 with
`errors`; its name, version and publisher must be the catalog's (502); its own `requires.keel` must fit this keel (409);
keel's review of the file must equal the catalog's (`rules`, `kinds`, `phases`, `lanes`, and `blocks` when the catalog
lists it) → else 409 `{differences, review, catalog_review}`. `yaml` is the file as published; `requires` is the pack's.

```
POST /rules/read {yaml}
  → {pack: {name, title, version, publisher, summary, requires, ...} (no rules), fits, why_not,
     rules: [{id, kind, match, how, phases, lanes?, except?, reason,
              enforced: {before: [engine], after: bool, engines: {engine: before|wrapped|after|none},
                         by: [{when: before|after, who, label, engines: [engine]}]}}],
     engines: [{engine, title, protection, words}], enforcers: [{who, label}]}          422 not a pack
```

`rules_view.reading` / `enforcement` read **who enforces a rule** from the real enforcement code, per engine
(`enforced()`, which asks `cli_runners.protection` and codex's hook support):

| who | label | when | engines |
| --- | --- | --- | --- |
| `hook` | keel's PreToolUse hook | before | claude, opencode, codex 0.150+ (protection `before`) for bash, edit, read |
| `mcp-guard` | keel's MCP guard | before | the same engines, for `mcp` rules (`hook.check_mcp`) |
| `toolbox` | keel's ToolBox | before | api-key models (`tools/agent_tools.py`; no MCP) |
| `wrappers` | keel's command wrappers | before | copilot and an older codex, for a bash rule whose patterns all name a program keel wraps (`shim.NAMES`) |
| `diff-guard` | the diff guard | after | every engine, for `edit` rules (`tools/guard.py guard_diff` → `rules.check_edit`) |

`before` lists the engines that check the rule before the action (`before` or `wrapped`); `after` is true for edit
rules.

### api

```
GET  /api/marketplace/rule-packs?q=&category=&project=
     → the engine's {rules: [RulesHit], sources, categories}, each hit + installed: {version, edited, source} | null
       (the rules api's GET /api/projects/{pid}/rule-packs) and update: str | null (a newer version that fits)
GET  /api/marketplace/rule-packs/{name}?project=
     → the engine's detail + installed, update, catalog_review, and (fetched and verified) yaml, review (keel's),
       pack, rules (keel's reading, each with enforced), engines, enforcers; fetch_error: {error, hint} | null when the
       fetch fails (the page still opens); a refused pack (old catalog, no version for this keel) is not fetched
POST /api/projects/{pid}/rule-packs/market/install {name, version?, replace?}
     → fetch, then the rules api's install {source: "marketplace", yaml, name, version, replace}:
       {pack, installed, market: {catalog, name, version, sha256}, review}
       the rules api's 409 (the same version; another version without replace) and 422 (not valid; requires.keel not
       met) come back as it gives them; a fetch refusal keeps the engine's status (409, 502)
remove: POST /api/projects/{pid}/rule-packs/uninstall {name} (the rules api)
```

`MarketRulePacks.kt` (service + controller, package `keel.api.rules`). Install needs the MCP **admin** level
(`McpLevels`: `rule-packs/(install|uninstall|market/install)`). keel records `source: marketplace` and the version in
`.keel/rules/.installed.json`; Build › Safety & History shows the pack "from the marketplace" (and "edited" after a change there).

### web

- Control › Plugins › Marketplace: the kind switch **Plugins · Workflows · Rules** (`#/plugins?tab=market&kind=rules`).
- `RulesMarket` (`pages/MarketRulePack.tsx`): search, category chips, cards (icon, title, publisher ✓ / community,
  version, summary, "3 rules · bash 1 · edit 2 · every phase", lanes; Install / Installed here / Update to x; an
  "edited" note; a refused pill), From keel / From the community, and a note when no catalog answers.
- A pack's page `#/plugins/rules/<name>`: every rule in a table (rule, kind, what it matches, how, phases, lanes,
  except, the reason, **who enforces it**: "before the action keel's PreToolUse hook (Claude Code, OpenCode, Codex),
  keel's ToolBox (API-key models), …" / "after the step the diff guard (…)", built from the api's `by`), a "Who
  enforces it" panel (per mechanism, the rules it covers, each engine's protection in words), the YAML (folded),
  versions, requires, checks; Install (this project, a version), Update (a copy changed here: "Keep my copy" (the
  default, nothing is sent) or "Replace it with x" → `replace: true`; an unchanged copy updates with replace at once),
  Remove (confirm; the rules api's uninstall) and **Open in Rules** (`#/rules`). A fetch error shows as an alert and
  Install is off.
- `rulePackMarketApi.ts`: the types and calls; `packShort`, `phasesShort`, `whoEnforces`.

### Tests and the e2e

- engine `tests/test_marketplace_rules.py` (17: the final format, names, blocks, fetch and its refusals, keel's reading),
  tools `tests/test_rules.py`, api `MarketRulePacksApiTest` (4), web `marketRulePacks.test.tsx` (11, MSW:
  `test/marketRulePackHandlers.ts`; installs land in the rules db).
- `e2e/marketplace/e2e.py` round `rule_packs` (also alone: `--only rules`): no-npm-install 1.0.0 / 1.1.0 linted, signed
  and indexed with keel-plugin; search; the page (keel's reading, who enforces it); install into the project; the rules
  api lists it with source marketplace and Build › Safety & History shows it; **keel's PreToolUse hook in the container refuses
  `npm install left-pad` with "(rule no-npm-install/no-install)"** and lets `ls` through; installed here + update;
  409 again and without replace; update with replace; the web (`rules-web.mjs`: the kind switch, the card, the page's
  table and who enforces it; it saves `rt_rules_view.png` and `rt_rule_pack_page.png`); uninstall (the guard no
  longer applies it); a bad signature → the page's `fetch_error` and install 502, nothing installed.
  `KEEL_NAME` / `KEEL_VOLUME` / `KEEL_PORT` give the e2e's keel other names; it refuses the person's keels' names.

### Not built

- The marketplace repo's `fetch-releases.sh` and `check-pr.yml` for rule packs (a pull request's pack lint and its
  review for the maintainer), and a Publish flow for packs (question 4).
- "All projects": the rules api installs into one project.
- The warning for a pack that blocks a step keel runs (question 2).
