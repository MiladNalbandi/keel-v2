# 0.17.1: one place for plugins

**Why:** keel 0.17.0 had two places to switch a plugin: **Tools › Plugins** (cards: Install for this project, For
every project) and **Settings › Plugins** (installed, marketplace). Milad: the Tools one "should not exist". From
0.17.1 **Settings › Plugins** is the one place.

```
  0.17.0                                    0.17.1
  ──────                                    ──────
  Tools page                                Tools page
   ├ Plugins (cards, on/off)   ── goes ──▶   (gone)
   ├ Servers (+ plugin servers)              ├ Servers (+ plugin servers)    stays
   ├ Recent tool calls                       ├ Recent tool calls             stays
   └ Who may use what                        └ Who may use what              stays

  Settings › Plugins › Installed             Settings › Plugins › Installed
   └ keel-wide switch                         ├ db, git, ci, review: [ Off | This project | All projects ]
                                              └ any other plugin: keel-wide switch + "On"/"Off"
  #/plugins/<name>                          #/plugins/<name>
   └ About, Permissions, Versions, Needs      + About: KeelBot tools, workflow blocks, commands, shows in
                                              + Projects: every project with the same control
```

## What moves where

1. **Tools page.** The Plugins section and `PluginCards.tsx` go (nothing else used it). The page keeps Servers, the
   rows of the plugins' own MCP servers (`withPlugins`, for "Who may use what"), Recent tool calls and the catalog
   cards. Its sub text no longer says "Plugins keel adds".
2. **Installed tab: the control** (fixed in 0.17.1 after the first try). A plugin a project turns on
   (`per_project: true`; today db, git, ci, review: the ones `GET /api/projects/{pid}/plugins` lists) has **one
   segmented control** in the On column, for the project chosen now, in place of the switch:

   ```
     Git            [ Off | This project | All projects ]
     Database       [ Off | This project | All projects ]
                      on in 2 other projects              ← a link to #/plugins/db?at=projects
     Wiki           (●) On                                ← no per-project choice: the keel-wide switch
   ```

   The first try put a second small switch under the keel-wide one, with "This project: On" and a "For every
   project" link. The column was narrow, the two switches stacked and the labels broke one word per line ("This /
   project: / On / For / every / project"): nobody could tell which switch was which.

   **What each choice means and sends.** A plugin loads for a project when it is on keel-wide
   (`PUT /api/plugins/{name}` `{on}`, after a restart) **and** on for that project: the project's own row, else the
   every-project row `"*"` (the api's `scope` says which one: `project` or `all`).

   | shown | when | a click sends (`PUT /api/projects/{pid}/plugins/{name}`) |
   |---|---|---|
   | **Off** | off keel-wide, or not enabled for this project | `{enabled: false, scope: "project"}` |
   | **This project** | on keel-wide, enabled by the project's own row | `{enabled: true, scope: "project"}`; when the `"*"` row is on, `{enabled: false, scope: "all"}` first (else it would stay on everywhere) |
   | **All projects** | on keel-wide, enabled by the `"*"` row | `{enabled: true, scope: "all"}` (the api drops the project's own row) |

   **This project** and **All projects** also send `PUT /api/plugins/{name}` `{on: true}` when the plugin is off
   keel-wide (it loads after the restart). **Off** never turns it off keel-wide: that is the plugin page's **Turn
   off**. The keel-wide state counts: a plugin that is off in keel shows **Off** even when a project's row says on,
   because nothing loads.

   Under the control: **on in 2 other projects** (Off) or **also on in 2 other projects** (This project), when other
   projects have it on; it links to the plugin page's Projects list (`?at=projects` scrolls there). No project yet:
   the keel-wide switch.

   **Look.** The On column has `min-width: max-content` and `white-space: nowrap`, the control too: it never wraps.
   On a phone (the table's box under 560 px) the table is a list of cards (`.rt`, pages.css) and the control sits on
   its own line right under the plugin's name. A plugin with no per-project choice has its switch with **On** or
   **Off** beside it.

   **Keyboard and screen readers.** `role="radiogroup"` named "Git: where it is on"; each choice `role="radio"` with
   `aria-checked`; only the chosen one is in the tab order (roving tabindex). ←/→ and ↑/↓ move the choice and apply
   it (they wrap), Home and End go to Off and All projects.
3. **The plugin's page** (`#/plugins/<name>`). About also lists what the cards said: KeelBot tools, workflow blocks,
   commands, where it shows. A new **Projects** section lists every project with **the same control** and the same
   words ("Database in platform: where it is on"); **This project** there means that row's project. The first try's
   "On for every project" button is gone: **All projects** on any row does it.
4. **Links and hints.** Every "Tools › Plugins" a person reads now says **Settings › Plugins**: the Workflows page's
   note on an off block, the api's and engine's 409 hints (Database, Git, CI/CD, Code Review off for this project),
   Claude Code's MCP error, docs/GUIDE.md and the README's "What you see" row. No ⌘K action or notification linked to
   `#/tools` for plugins, so none changes.

No api change: the per-project api is the same as in 0.10.0.

## Code page toolbar

The person's screenshot of the Code page: the top row had the index status, **Rebuild**, **KeelBot only** and
**Focus**.

```
  0.17.0  head:  Index: ready · … · now  [Rebuild]  [Update from main]  [KeelBot only]  [Focus]
          status bar:                                           ludus-engine   Focus ⇧⌘\

  0.17.1  head:  [Update from main]
          status bar:          Index: ready · … · now  ↻ Rebuild   ludus-engine   Focus ⇧⌘\
```

- **Focus** leaves the head. It stays in the purple status bar (and ⇧⌘\, Esc Esc).
- **Rebuild** and the index status move to the status bar, next to the project name, in its style ("↻ Rebuild",
  "Starting…" while busy). Only when the Graph plugin is loaded, as before. They now show in Focus mode too.
- **KeelBot only** goes: the panel has its own KeelBot toggle and "Open KeelBot full screen". It had no key, so the
  ⌘/ cheat sheet and `keys.ts` do not change.

## The menu's view switch (a bug)

The All / Product / Dev switch (Shell.tsx `ViewSwitch`, shown when keel has both parts) spilled out of the menu. The
menu head (`.side-head`) is a grid with two columns: the brand and the side tools. The project picker spans both
columns; the switch did not, so it sat in the narrow left column (about 95 px) and its three buttons spilled over.

```
  before                         after
  ┌──────────┬────────┐          ┌───────────────────┐
  │ keel     │ tools  │          │ keel        tools │
  │ [project picker  ]│          │ [project picker  ]│
  │ [All|Prod│uct|Dev]  ← out    │ [All |Product|Dev]│  three equal parts
  └──────────┴────────┘          └───────────────────┘
```

Fix in `web/src/styles.css`: `.view-switch` gets `grid-column: 1 / -1` and `repeat(3, minmax(0, 1fr))`, its buttons
`min-width: 0`, and the focus ring `outline-offset: -2px` (it stays inside the frame).

## Tests

- web: the Tools page has no Plugins section (and still shows the plugin's server in "Who may use what" once it is
  on); Installed's control: the three choices and their api calls (This project from All projects sends two), a
  plugin off keel-wide shows Off and This project turns it on, the "on in 2 other projects" hint and its link, the
  arrow keys, Home, End and the one tab stop, the no-per-project plugin's switch with On/Off beside it, and the
  min-width / nowrap rules (jsdom draws no CSS); the plugin page's Projects section lists each project with the same
  control; the Workflows note says Settings › Plugins.
- Code plugin (plugins/code/web/test): no Focus or KeelBot only button in the head; Rebuild and the index status in
  the status bar; Focus from the status bar.
- The view switch (web/src/test/viewswitch.test.tsx): its three buttons, and the CSS rule (jsdom draws no CSS).
- api and engine: the hints' new words (no test pinned the old ones).
- e2e/parity: `web #/tools *` and `web #/repo *` differ from 0.17.0 only by what is gone (allow.yml, reason
  "0.17.1, …").

## 4 · A plugin's web files are never stale (found on the try-out keel)

keel serves a plugin's web files `immutable` for a year, under `/plugins/<name>/<version>/web/…`. 0.17.1 changes the
Code plugin's page without a new plugin version (still 1.0.0), so a browser that had loaded it kept the old toolbar.
Since 0.17.1 the urls in `/api/features` end with `?v=<the file's sha256, 12 hex>`: a changed file gets a new url,
an unchanged one keeps its cache. (`PluginHost.webUrl`; `PluginHostApiTest`.)

## 5 · What a change does now, and what waits for a restart

The person: "everything is on by default and I cannot turn it on or off". The switches worked, but nothing visible
happened until a restart, and only the banner at the top said so. Since 0.17.1:

```
  Installed                                                            last cell of the row
  ─────────                                                            ────────────────────
  keel core   …  On   (● always on)                                    no switch: core cannot go off
  Wiki        …  Off  ( ○ ) Off          applies after restart  [Restart now]   engine/api plugin: waits
  Notes       …  On   ( ● ) On                                    applied       web/content plugin: now
  Notes       …  Off  ( ○ ) Off       applied — reload the page               its web part left
  Database    …  On   [Off|This project|All projects]             applied       loaded keel-wide: now
```

- **The intro** has one more line: "Everything is on by default, like 0.16.1. Start with fewer plugins:
  `keel2 start --core`."
- **A change that waits.** Its row (the plugin's name is in the engine's `pending_restart.changes`) says **applies
  after restart** with **Restart now** (`POST /api/plugins/restart {now: true}`; it asks first while agent steps run;
  a keel that keel-start does not run says `keel2 restart`). The banner stays.
- **A web or content-only plugin** (its parts are only `web` and/or `content`; none of keel's 12 today) applies at
  once. After each change the api calls the engine's new `POST /marketplace/apply-live {names}`:

  ```
   web ── PUT /api/plugins/notes {on} ──▶ api ── PUT /marketplace/installed/notes ──▶ engine (installed.json)
                                          api ── POST /marketplace/apply-live ──────▶ engine: live.apply()
                                                   resolve; keep only the web/content-only changes whose
                                                   needs stay loaded; write run/resolved.json + run/env
                                                   (everything else as loaded); addons.reload_content()
                                          api ◀── {applied, names: {notes: applied|restart|same}, pending_restart}
                                          api: PluginHost.reload() (/api/features, /plugins/<name>/… files)
   web ◀── {…, live} ── "applied"; a new web part: /api/features again → its page joins the menu
                        a web part that left or changed: "applied — reload the page"
  ```

  A plugin with an engine, api or migrations part, or a web plugin that needs one that waits, keeps **applies after
  restart**. keel never restarts by itself here. Only a change that waits marks the restart as due; a web/content-only
  install's notification says "… is installed: it works now".
- **The per-project control.** For a plugin keel has loaded, a project's choice applies at once: the api reads
  `project_plugins` on every call (the plugin's pages and routes, each KeelBot turn, each flow it starts; a flow that
  already runs keeps the list it started with). The row says **applied**. When the choice also turns the plugin on
  keel-wide, that part waits: **applies after restart**. (Checked by `PluginsApiTest`: a flow started right after the
  choice carries it, the next one after Off does not.)
- **keel core's row** has no switch: an **always on** pill.

Tests: engine `tests/test_marketplace_live.py` (on/off, update, content-only, code waits, api and migrations wait, needs
that wait, a loaded plugin that needs it, no resolved.json, the route); api `MarketplaceApiTest` (a web plugin applies
and the plugin host reads it, a plugin with code waits) and `PluginsApiTest` (a project's choice at once); web
`marketplace.test.tsx` (core's pill and the intro, the row's Restart now and its question, a web plugin's page appears
and its off asks for a reload) and `plugins.test.tsx` (the per-project control says applied, or applies after restart).
