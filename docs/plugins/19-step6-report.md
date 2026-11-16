# 19 · Step 6 report: a core image, and a clean keel with only its core

**Branch:** on top of `feat/plugin-step4` (`fca8ec9`: keel 0.15.6 + steps 1–4), on the plugin track. Local only, not
pushed, never merged into `main`, never tagged. The spec is "Step 6" in
[16-steps5-7-contract.md](16-steps5-7-contract.md).

Step 6 only **adds**. A full keel (every plugin loaded) looks and works as before: every new check says "yes" there.

## What a keel with only its core looks like

Every part is a plugin since step 3. A keel with **only its core** has no plugin loaded. There are two ways to get one:

- the **core image**: `keel2 start --core` (the image `ghcr.io/miladnalbandi/keel-v2:core`, built with
  `EDITION=core`: no plugin inside);
- the **full image** with `KEEL_PLUGINS=off` (every plugin inside, none loaded).

```
 keel (core only)                      the main area
 ──────────────────                    ─────────────────────────────────────────────
 All projects                          Flow, Inbox, Live agents, Jobs, Workflows, …
 Run      Flow · Inbox ·                 · no link to Tasks, Code, KeelBot, Map, Graph, Wiki
          Live agents · Jobs             · no call to a plugin's api
 Project  ┌ Start with a set ────┐     ⌘K (the launcher)
          │ Developer  Review    │       · scopes: All, Flows, Actions
          │ Knowledge  Tickets   │       · no "Ask KeelBot", no "Add a task", no "Go to file"
          └──────────────────────┘       · "?" is a plain character, ⌘↩ asks nobody
 Build    Workflows · Agents · …       ⌘/ (the keys)
 Control  Budget · Settings ·            · no KeelBot keys, no Map and Graph keys
          Connections · Plugins
```

A set button opens Settings › Plugins with that set (step 4). After the plugins are installed and keel restarts, their
pages and links come back.

## The rule: link only to a page that is there

The web SDK has two new helpers:

```ts
hasPage(id); // true when the page registry has this page (by id or alias): hasPage("wiki")
useHasPage(id); // the same in a component; it renders again when a page comes or goes
```

Core uses them before every link to a plugin's page, and before every call to a plugin's api:

```
 a core page wants a link to the Wiki
        │
        ▼
 hasPage("wiki") ── no ──▶ no link (and no request to /wiki/…)
        │ yes
        ▼
 the link, as before
```

A plugin's page registers a moment after keel starts (its web part loads at start). `useHasPage` follows it, so the
link appears as soon as the page is there.

Plugins use `usePages()` for the same check (`usePages().some((p) => p.id === "wiki")`). Every keel with SDK 1 has
`usePages`, so a plugin built this way also runs on an older keel.

## What was fixed

**In core** (`web/src`):

| place                             | before, on a core-only keel                              | now                                                                 |
| --------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------- |
| Flow, the init flow               | "Open wiki" and "Read what is written so far" → `#/wiki` | no link (`GoButton` shows nothing for a page that is not there)     |
| ⌘K, actions                       | "Ask KeelBot…", "Go to file…", "Add a task" → `#/tasks`  | only keel's own actions                                             |
| ⌘K, scopes and field              | Files, Code, Pull requests, Tasks: always empty          | only the scopes something can fill; the field says what it searches |
| ⌘K, "?" and ⌘↩                    | a KeelBot chat → `POST /helper/sessions` fails           | "?" is a plain character; ⌘↩ does nothing; no "ask KeelBot" hint    |
| ⌘K, code search and preview       | `/graph/search` and `/repo/raw` (404)                    | not asked                                                           |
| ⌘K, results of other kinds        | "Ask KeelBot about it" on every flow and gate            | not there without KeelBot                                           |
| the key sheet (⌘/)                | KeelBot's rows, "Map and Graph diagrams"                 | not there                                                           |
| Inbox, a KeelBot command          | "Open KeelBot ▸" → `#/repo`                              | the Code page, else KeelBot's page, else no button                  |
| diagrams (Map, Graph): file:line  | a link to `#/repo/…`                                     | text when there is no Code page; "Open migration" is off            |
| "Refresh stale"                   | `POST /wiki/refresh` (the Wiki plugin's api)             | only with the Wiki                                                  |
| a notification for a missing page | went to `#/addon`                                        | keeps its own link; the router shows Flow (or an add-on's page)     |

The launcher's source slot (`launcher.source`) has a new optional field `kinds` (what a source finds: `file`,
`symbol`, `pr`, `task`). Code, Code Review and Tasks set it. A source without `kinds` may find anything, as before.

**Between plugins.** A set can leave plugins out: Developer has no Wiki, Review has no Graph, Knowledge has no Code
page, Tickets has no KeelBot. So each plugin checks too:

| plugin  | what it shows only when the other plugin is there                                                                   |
| ------- | ------------------------------------------------------------------------------------------------------------------- |
| Code    | the index row (Graph's `/index`), "KeelBot only", the keel view's Wiki links, a branch's "Ask KeelBot to review it" |
| Git     | its four "Ask KeelBot …" buttons (Git does not need KeelBot)                                                        |
| CI/CD   | "Ask KeelBot why"                                                                                                   |
| Map     | "Open the wiki"                                                                                                     |
| Graph   | a symbol's file and its uses as links (else text)                                                                   |
| KeelBot | @ lists files only with the Code page, symbols only with the Graph (no request otherwise)                           |

## keel2 and the image

- `keel2 start --core [dir]` starts the `:core` image. `KEEL_IMAGE` still wins. The container remembers it (label
  `keel2.core`), so `keel2 restart` and `keel2 update` keep a core keel core.
- `keel2 status` says the edition: `edition    core (keel's core only, no plugin inside: add them in Control ›
Plugins)`, or `full (every plugin inside)`, or `product (…)`. It reads the image's `KEEL_EDITION`.
- `docker/tests/keel2.test.sh` checks this with a stub docker (no Docker, no network); CI runs it.
- `docker.yml` has a new job, **core**: it builds `EDITION=core` and pushes `:<version>-core` on every tag, and `:core`
  when `:latest` moves. It runs when the workflow runs today (release tags `v*`).
- `Dockerfile`: the edition (`ARG EDITION`, `ENV KEEL_EDITION`) now comes after the big layers. So `:latest`, `:core`
  and the product image share every big layer (14 of their 19 layers; checked on the two images below). A person who
  changes edition downloads about 45 MB (mostly the api jar, built for each image), and CI builds the big layers once.

## The e2e

`python3 e2e/core/e2e.py --image keel-v2:s6-core --full-image keel-v2:s6` starts a throw-away keel `keel-core`
(127.0.0.1:8097, volume `keel-core-data`, a small git project at `/workspace`, the fake model) two times:

1. **core**: the core image;
2. **off**: the full image with `KEEL_PLUGINS=off`.

Each time it checks: health and the image's edition; `/api/plugin-host` and `/api/features` list no plugin; it runs
two flows on the fake model (init, and change, which waits at a gate). Then `e2e/core/walk.mjs` opens headless
Chromium and visits every menu page, `#/projects` and both flows, and clicks every tab. On every page: **no console
error, no failed request, no request to a plugin's route, no link to a page that is not there**. It also checks the
menu ("Start with a set" in the Project group), the launcher and the key sheet. keel-core is removed after each run.

| run                                    | result                |
| -------------------------------------- | --------------------- |
| core (`keel-v2:s6-core`)               | **PASSED: 37 checks** |
| off (`keel-v2:s6`, `KEEL_PLUGINS=off`) | **PASSED: 37 checks** |

Both runs together: 74 checks in 153 s (on the final images, built with the Dockerfile change above).

Before the fixes (a first version of the same walk, on a core image of `fca8ec9`): 9 checks failed. Each flow page
had two links to `#/wiki`, the launcher had 7 scopes, "Ask KeelBot…", "Go to file…" and "Add a task", its footer
offered KeelBot, and the key sheet listed KeelBot's keys. Every other page was already clean.

## Other checks

| what                                        | result                                                    |
| ------------------------------------------- | --------------------------------------------------------- |
| web: tsc, vitest                            | 527 of 527 passed (27 new: 17 in core, 10 in the plugins) |
| web: build, build:plugins, build:product    | ok                                                        |
| the fence (web)                             | empty, as before                                          |
| `scripts/no-v1.sh --strict`                 | clean                                                     |
| `docker/tests/keel2.test.sh`                | 14 checks passed                                          |
| `shellcheck -S error` (keel2, the tests, …) | clean                                                     |

The engine and the api did not change, so their suites were not run again. The parity e2e was not run in this step
(its containers belong to the integrator). Every change is behind a check that is true when the plugin is there.

## Image sizes

| image                    | on disk (unpacked) | to download (compressed) |
| ------------------------ | ------------------ | ------------------------ |
| `keel-v2:s6` (full)      | 3.69 GB            | 977.1 MB                 |
| `keel-v2:s6-core` (core) | 3.69 GB            | 976.2 MB                 |

The core image is only about **2 MB smaller** (unpacked; 0.9 MB to download). The plugins are small: all 11 together
are 1.9 MB. The big parts are the same in both images:

```
 the agent CLIs + CodeGraph   1.32 GB   ███████████████████████████
 system tools, Node, Docker    470 MB   ██████████
 the engine's Python libraries 342 MB   ███████
 the JDK                       309 MB   ██████
 the api                        44 MB   █
 the plugins (full only)         2 MB   ·
```

So a core image is about **choice**, not size: it starts with no plugin, and a person adds the ones they need. A much
smaller image would need `INSTALL_CLIS=0` (no agent CLIs, about 1.3 GB less).

## Milad's outward step

Publish `:core` **with keel's next release from `main`**, when the plugin track becomes that release. Nothing else is
needed: the `core` job in `docker.yml` builds and pushes `:<version>-core` and `:core` on the release tag. Then
`keel2 start --core` works for everyone (the `keel2` script comes with that release too).

## Notes

- **The opencode CLI** failed to install once while building (`npm i -g opencode-ai` on arm64: "EBADPLATFORM … musl").
  The next build installed it. It looks like a short network problem, but if it happens on a release build, the arm64
  image fails. Pinning `opencode-ai` would stop it; that is Milad's choice.
- **Jobs › Pipelines:** a CI/CD notification links to `#/jobs/pipelines`. Without CI/CD (core only) that link opens the
  Jobs page and asks for a job named "pipelines" (a 404). Only an old notification from before can have this link.
- **The new SDK names** (`hasPage`, `useHasPage`) need this keel or newer. The plugins in this repo use `usePages`
  instead, so they also run on an older keel.
