# keel as a small core + plugins: the plan

**Status:** plan only (October 2026). Nothing in keel's code changes with this folder.
**Mockup:** [the clickable mockup](https://claude.ai/artifact/8e2GmT6UvJ3zNVHpMPiY6Z) (private link; source:
[mockup.html](mockup.html)).

Milad's goal: keel should be as **light** as possible. keel is a small **core**. Every other part is a **plugin**
with its own git repo and version. People find plugins in a **marketplace** and install them into a running keel.
Anyone can publish a plugin. Agents (KeelBot, flows) can **ask** for a plugin, and a person approves. Nothing may
break on the way.

## The best way, in one page

**Recommendation: "the keel Product way, made general".** keel Product already works as an add-on. We make that the
one way for every part, and we add three things it lacks: loading from a folder at start, signed packages, and slots
in the UI.

```
   1 ONE PACKAGE                 2 ONE HOST, LOADED AT START          3 ONE CATALOG
   ───────────────               ────────────────────────────         ──────────────────────
   db-1.4.0.kplug                keel-start → resolver                index.json (signed)
    keel-plugin.yml (manifest)     ├ engine: plugin folders on        on GitHub Pages,
    engine/  api/  web/            │   sys.path, ADDON hooks          pointing to each repo's
    content/  migrations/          ├ api: plugin jars via             GitHub release
                                   │   PropertiesLauncher loader.path
   from its own repo,              │   + own Flyway history          person: Install
   signed by its publisher         └ web: import(url) + import map    agent:  can only ASK
                                       with one shared React          → Inbox approval
```

In plain words:

1. **A plugin is one signed file** with up to five parts: engine (Python), api (Kotlin jar), web (JavaScript),
   content (workflows, agents, skills) and its own database migrations. One manifest, `keel-plugin.yml`, describes it.
2. **keel loads plugins when it starts**, from `/data/plugins` (installed by you) and `/opt/keel-v2/plugins` (came in the
   image). Installing = download, check, unpack, restart (about 30 s). The image is never rebuilt.
3. **Core never imports a plugin.** Plugins use core only through a small, versioned **SDK** and named **slots**
   (places in a page, like "a tab in the Code page" or "a kind in Connections"). A plugin can be a host too: Code
   has slots for Git, Code Review, Database and CI/CD.
4. **Trust comes from signatures**, because a plugin that runs code is trusted like keel itself. keel shows the
   permissions before install, checks sha256 and the Ed25519 signature, and keeps every plugin in its own name box.
5. **The marketplace is a git repo + one signed JSON file.** No server of our own.
6. **Agents get search tools and a "request" tool, never an "install" tool.** A request is an Inbox card.

### Why this way, and not another

| other way                                                             | why not (now)                                                                                                                                                                 |
| --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| build an image per plugin set (like `EDITION` today)                  | people would need to build images; agents could not add a plugin; this is what we want to leave                                                                               |
| each plugin as its own process or container (sidecar)                 | strong walls, but many processes in one container, more memory, and Code / KeelBot need deep UI and event links. Agent tools already work this way (MCP servers), and stay so |
| load plugins **without** restart (separate class loaders, PF4J, OSGi) | hard with Spring, leaks memory, many bugs; a 30-second restart is a fair price                                                                                                |
| Module Federation for the web                                         | ties plugins to one bundler; an **import map** is a web standard and simpler                                                                                                  |
| one big repo forever, with feature flags                              | not "light"; others cannot publish                                                                                                                                            |

Content-only and web-only plugins load **without** a restart. Only engine and api code needs one.

## Core and plugins: what goes where

The split follows Milad's list. The mapping of today's code ([01-today.md](01-today.md)) showed four places where a
part is **used by flows**. In those places the engine piece stays in core, and only the page becomes a plugin:

| part                | becomes a plugin                                             | stays in core (flows need it)                                                                                                |
| ------------------- | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| **Code**            | the Code page (IDE), a host for its own plugins              | the git helper flows use (`RepoService.git/base` → **Workspace**), the GitHub connection and "open the pull request" in ship |
| ↳ Git               | Git panel, `git:*` actions, `keel-git` agent tools, commands | —                                                                                                                            |
| ↳ Code Review       | all of it                                                    | —                                                                                                                            |
| ↳ Database          | all of it (and its ER tab in Map through a slot)             | —                                                                                                                            |
| ↳ CI/CD             | all of it (needs Git)                                        | —                                                                                                                            |
| **KeelBot**         | the chat, its runner and commands                            | the "ask a person and wait" broker → a core **Approvals** service                                                            |
| **Map**             | all of it                                                    | —                                                                                                                            |
| **Graph**           | the page and the code-graph context for agents               | a **ContextProvider** hook (flows run without the graph, with less context)                                                  |
| **Wiki**            | the page and the refresh button                              | the knowledge base: librarian, `knowledge_check`, the push blocker, the guard rule                                           |
| **Tasks**, **Jira** | all of it                                                    | an **InboxSource** hook so Tasks can add Inbox cards                                                                         |
| **keel Product**    | all of it (already an add-on; needs Tasks and Jira)          | —                                                                                                                            |

Core keeps what Milad listed: the flow engine and flows, workflows, agents, stacks, guard and gates, settings,
connections and secrets, budget, Inbox and notifications, projects, the plugin host and the marketplace client. The
mapping also found shared UI (the code renderer, the diagram kit); it goes into the web SDK. Quality, Skill hub, Jobs,
Live agents, Tools (MCP), Hunt and Doctor were not in the list; they stay core for now (question 3).

## The first step, and why

**Step 1 (0.15.0): one plugin host, proven with keel Product.** Build the resolver, the start loop, `PropertiesLauncher`,
the general per-plugin Flyway, the runtime web loader with the import map, and the first SDK. Then ship Product as a
real `.kplug` file instead of a special image. Add the **fence**: tests that fail when core imports a plugin.

Why first: it tests every risky idea (a jar from `/data`, a web part at run time with one React, a plugin's own
migrations, a safe self-restart) on the one part that is **already separate** and only in a beta image. So today's
users carry no risk, and every later step only repeats it. Details: [05-migration.md](05-migration.md).

## The files in this folder

| file                                                         | what it answers                                                                                                                                                                                                                                                         |
| ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [01-today.md](01-today.md)                                   | where each part lives today, who calls whom, the ranked blockers                                                                                                                                                                                                        |
| [02-plugin-package.md](02-plugin-package.md)                 | the package, the manifest, loading without a rebuild, install / update / remove, dependencies, data                                                                                                                                                                     |
| [03-security.md](03-security.md)                             | trust levels, signatures, permissions, what a plugin may never do                                                                                                                                                                                                       |
| [04-marketplace.md](04-marketplace.md)                       | the catalog, search, the Plugins page, the api, agents asking, publishing                                                                                                                                                                                               |
| [05-migration.md](05-migration.md)                           | seven releasable steps, their tests, and why step 1 is first                                                                                                                                                                                                            |
| [mockup.html](mockup.html)                                   | the mockup's source (published as a claude.ai artifact)                                                                                                                                                                                                                 |
| [06-spikes.md](06-spikes.md)                                 | the two risky ideas of step 1 tried for real: a plugin jar from a folder, a plugin web part with one React                                                                                                                                                              |
| [07-step1-contract.md](07-step1-contract.md)                 | the shared spec the five parts of step 1 were built against                                                                                                                                                                                                             |
| [08-step1-report.md](08-step1-report.md)                     | step 1 is built: what keel can do now, the commands, the test results                                                                                                                                                                                                   |
| [09-step2-contract.md](09-step2-contract.md)                 | the shared spec for step 2: approvals, Inbox sources, the engine registry, api services, web slots                                                                                                                                                                      |
| [10-step2-report.md](10-step2-report.md)                     | step 2 is built: what changed, the fence numbers, the test results                                                                                                                                                                                                      |
| [11-step3-contract.md](11-step3-contract.md)                 | step 3: plugin folders, the build machinery, the waves, the parity e2e                                                                                                                                                                                                  |
| [12-step3-progress.md](12-step3-progress.md)                 | step 3 so far: which parts are plugins, the checks, parity with 0.15.1; the port of 0.15.2–0.15.4 and parity with 0.15.4                                                                                                                                                |
| [13-step4-contract.md](13-step4-contract.md)                 | step 4: the marketplace, minisign signatures, install / update / roll back / remove, agents can ask, publishing                                                                                                                                                         |
| [14-publishing.md](14-publishing.md)                         | how keel's plugins are released and the catalog is built: `keel-plugin`, the two keys, the release and sync workflows, what Milad does once ([setup-signing-keys.sh](tools/setup-signing-keys.sh), [marketplace-repo/](marketplace-repo/))                              |
| [15-step4-report.md](15-step4-report.md)                     | step 4 is built: what keel can do now (find, install, update, roll back, remove, agents ask, the Inbox card, restart), the defaults, what Milad does before the real marketplace works, the checks and parity with 0.15.4                                               |
| [16-steps5-7-contract.md](16-steps5-7-contract.md)           | steps 5–7: each plugin in its own repo (built against keel at a fixed commit, `plugins.lock`), a core image, other publishers (`keel-plugin dev`, the template repo, the guide)                                                                                         |
| [tools/create-plugin-repos.sh](tools/create-plugin-repos.sh) | creates the 14 repos in `keel-studio` (`keel-marketplace`, `keel-plugin-template`, `keel-plugin-<name>` × 12) with a short README, a draft manifest and the license; [move-plugin-repos.sh](tools/move-plugin-repos.sh) moves the first ones from `MiladNalbandi` there |

## Open questions for Milad

1. **Knowledge and code graph:** is it OK that the knowledge base stays in core and only the Wiki page is a plugin?
   And that Graph is a plugin that **adds** context to agents, so flows run without it but with less context?
2. **Code:** is it OK that core keeps the git helper, the GitHub connection and "open the pull request" (ship needs
   them), and the Code plugin is the page plus its four sub-plugins?
3. **Not in your list:** Quality, Skill hub, Jobs, Live agents, Tools (MCP), Hunt, Doctor. Core, or plugins later?
4. **Images:** keep `:latest` = core + the keel set (no change for anyone) and add `:core`? Or, at 1.0, make
   `:latest` core-only with a "Start with a set" page on first start?
5. **Signing:** Ed25519 keys in GitHub Actions secrets (simple), or Sigstore keyless signing (no keys to keep, more
   work to verify)?
6. ~~**Where:**~~ **Answered:** the GitHub organisation [`keel-studio`](https://github.com/keel-studio), like
   muxy-app: `keel-studio/keel-marketplace`, `keel-studio/keel-plugin-template` and `keel-studio/keel-plugin-<name>`.
   keel itself stays at `MiladNalbandi/keel-v2` (it was moved to keel-studio/keel-app for a few hours on 2026-10-08
   and moved back); its image stays `ghcr.io/miladnalbandi/keel-v2`.
7. **Other publishers:** at first only content-only plugins (workflows, agents, skills), or also code plugins with an
   "unverified" warning (off by default)?
8. **Restart:** after an approved install, restart by itself when no agent step runs, or always wait for a button?
9. **CodeGraph CLI:** move it out of the image into the Graph plugin (installed from npm at plugin install time,
   so it needs the network once)?
10. **Python libraries of plugins:** install from PyPI at install time (pinned with hashes, needs the network), or
    bundle wheels for each CPU (bigger files, works offline)?
