# keel TUI: keel v2 in the terminal

> **Parked (0.37.1):** keel TUI keeps working and gets its fixes, but no new features: they land in the web app (and keel desktop) and the IDE plugins (#194). To decide in early January 2027: keep it as it is, cut it down to KeelBot chat, or remove it.

keel's own terminal app, the Claude Code way: open it and see keel's logo, the version and which keel you talk to;
KeelBot is the main view; slash commands do what the dashboard does; panels show the flow, the agents and the inbox;
what happens in the dashboard shows in the terminal, and what you do in the terminal shows in the dashboard.

```
  keel2            (no arguments, in a terminal, keel running)  ─┐
  keel2 tui                                                      ├─▶ keel-tui (Ink app, node)
  keel-tui --url http://localhost:8080   (npm bin)              ─┘        │
                                                                          │  web/src/api.ts  (the web's typed client,
                                                                          │   one transport: base URL + headers)
                                                                          ▼
                     ┌──────────────── keel api (Kotlin) ─────────────────┐
   REST  /api/...  ──┤  X-Keel-Via: tui  → McpAudit → event tui.call       │──▶ timeline: "via keel TUI: /approve"
   SSE   /api/events ◀┤  gate.waiting, thread.done, agent.started, ...     │
   POST /api/diagrams/ascii ──▶ engine POST /diagrams/ascii ──▶ keel_engine/diagrams.py (shared with keel2 mcp)
                     └─────────────────────────────────────────────────────┘
```

## Stack

TypeScript + **Ink 5** (React for the terminal, React 18 like the web; Claude Code itself is an Ink app) in a new
package `cli/`. esbuild bundles it into one file, `cli/dist/keel-tui.mjs`, that needs only Node 20+.

*OpenTUI* (sst/opentui) was the alternative: a Zig core with TypeScript bindings, fast and with a richer layout
model. Not chosen: it needs Bun or a native Zig build per platform (the Docker image and the native release would ship
native binaries), it is pre-1.0 with a changing API, it has no `ink-testing-library` equivalent, and Ink lets the TUI
use React and the web's own typed client with nothing new to learn.

**No copy-paste of the client.** `web/src/api.ts` gets a small transport (`setTransport({ base, headers, fetch })`;
the web leaves it at "" and the browser's fetch). The TUI imports `web/src/api.ts`, `web/src/inboxApi.ts` and
`plugins/keelbot/web/keelbotApi.ts` as they are (`@keel/web-sdk` points to `cli/src/sdk.ts`, which re-exports
`web/src/api.ts`). SSE in Node: `cli/src/sse.ts`, a small reader over fetch's body stream (named events, reconnect
with backoff), the twin of the web's EventSource use in `web/src/state.tsx`.

## Tasks and files

| # | Task | Files |
|---|------|-------|
| 1 | Transport in the web client | `web/src/api.ts` (setTransport), web test |
| 2 | Shared ASCII diagram renderer | `engine/keel_engine/diagrams.py`, engine route `POST /diagrams/ascii`, api `POST /api/diagrams/ascii` (`api/.../diagrams/DiagramController.kt`), `McpLevels.kt` READ_POSTS, golden tests `engine/tests/golden/diagrams/`, shared workflow fixtures `engine/tests/fixtures/flowmap/*.json` read by pytest and vitest |
| 3 | "via keel TUI" | `api/.../mcp/McpAudit.kt` (X-Keel-Via: tui → `tui.call` event; xal → `xal.call`), engine `runtime/service.py` (`started_via: tui` → `thread.started.via`), web `components/events.ts` |
| 4 | The app | `cli/src/`: `main.tsx` (args), `transport.ts`, `sse.ts`, `keel.ts` (status, next, timeline… as text), `commands.ts` (registry, parse, autocomplete), `markdown.ts`, `logo.ts`, `ui/*` (App, StartScreen, Prompt, Picker, Panels, StatusLine), `script.ts` (non-interactive driver) |
| 5 | Ship it | `keel2` (`tui`, the no-argument default), `Dockerfile` (cli stage → `/opt/keel-v2/cli/keel-tui.mjs`), `scripts/build-native.sh` (`cli/keel-tui.mjs` in the tarball), `cli/package.json` bin `keel-tui`, CI job |
| 6 | Tests and the real check | `cli/test/*` (ink-testing-library), `cli/test/contract.test.ts` (stub api), `scripts/test-keel-tui.sh` (throw-away keel), captures in `docs/pictures/tui/` |

### What the app has

- **Start screen**: keel's ASCII logo, `keel v2 <version>`, the URL, docker or native, the fake model when it is on,
  the project and its branch, one line "waiting for you" (inbox count, the gate of this project) and one line "next"
  (the same words as `keel_next`), tips.
- **KeelBot** is the main view: plain text is a turn. The answer streams (`helper.step` events of the turn's call),
  the final answer is terminal markdown, `path/file.ts:12` is marked. `/model` picks a model (the catalog of
  `/api/providers/models`), `/chats` lists and opens earlier chats, `/new` starts a new one, `/mode ask|fix`.
- **Slash commands** with autocomplete (Tab, ↑/↓): /help /project /flow /flows /resume /stop /status /next /inbox
  /approve /reject <why> /answer /jobs /live /steps /timeline /workflows /diagram <flow|workflow|map-er|graph> /rules
  /plugins /budget /dashboard /model /chats /new /mode /panel /clear /quit.
- **Panels** (keys): Ctrl+F flow rail (steps, current, gates), Ctrl+A live agents, Ctrl+N inbox (Ctrl+B: the sidebar on a wide terminal). **Status line**:
  project · flow · phase · AC n/m · gates waiting · tokens · live/reconnecting.
- **Vice versa**: the SSE stream's gate.waiting, thread.done / failed, agent.started, notifications show as notices
  in the transcript. Every request carries `X-Keel-Via: tui`, `X-Keel-Client: keel TUI <version>` and
  `X-Keel-Tool: <the command>`; writes become a `tui.call` event, so the dashboard's timeline says "via keel TUI".
  A flow started with /flow sends `options.started_via: "tui"`: "flow started via keel TUI". Flows, chats and
  decisions are keel's own records, so the dashboard and the TUI always see the same.

## Shared ASCII renderer (agreed with the "keel in Claude Code/OpenCode/Codex" session)

One renderer, `engine/keel_engine/diagrams.py`: `render(kind, data, width=100, max_lines=60) -> str`. Pure,
deterministic, plain ASCII (every char < 128), every line <= width, at most `max_lines` lines and then
`... N more (zoom with id=...)`.

| kind | data | picture |
|------|------|---------|
| `workflow` | `{workflow}` | the web's flow map (`web/src/components/flowmap.ts` buildFlowMap, ported): main path, side paths, if/else arms with their `when`, per-AC / for_each loops, included flows folded, orphans |
| `flow` | `{workflow, thread, visited?}` | the same, with each step's state (done, running, waiting, failed), what waits (the gate and how to answer) and the criteria strip |
| `map-system`, `map-modules` | a map level `{nodes, edges}` | boxes and their links |
| `map-er` | `{schema, focus?}` | tables with columns and FK arrows; a focus table and its neighbours; a short list for more than 12 tables |
| `graph` | `/graph/node` focus `{focus, columns}` | callers <- focus -> callees, columns -2..2, at most 24 per column |

Routes: engine `POST /diagrams/ascii {kind, data, width, max_lines}` → `{kind, text, lines}`; api `POST
/api/diagrams/ascii` (proxied; read level in `McpLevelGuard`, so `keel2 mcp` read-only may call it). The MCP tools
import `keel_engine.diagrams` directly; the TUI uses the api route. Drift guard: `engine/tests/fixtures/flowmap/`
holds one JSON per `content/workflows/*.yaml` (steps as the engine expands them, and the main path, sections and
orphans `buildFlowMap` gives); a pytest checks the Python port, a vitest checks the web's `buildFlowMap`.

## Tests

- web: transport (base URL, headers, a custom fetch); flowmap fixtures.
- engine: diagrams golden files (every kind, ASCII only, width, max lines, deterministic), the route.
- api: `tui.call` recorded for a write with `X-Keel-Via: tui`, not for a GET; `/api/diagrams/ascii` is read level.
- cli (vitest + ink-testing-library): start screen, command parsing and autocomplete, chat streaming (SSE stub),
  panels and status line, markdown, the SSE parser; an API-contract test against a stub http server (paths, methods,
  bodies and the via headers).
- Real check, `scripts/test-keel-tui.sh`: a throw-away keel (`KEEL_NAME=keel-tui-lab KEEL_VOLUME=keel-tui-lab-data
  PORT=8099 KEEL_OPEN=0`, own `XDG_CONFIG_HOME`, a scratch git repo, an image built from this branch) driven with
  `keel-tui --script`: start screen, /workflows, /flow on the fake model to its first gate, /approve, /live, one
  KeelBot turn. Text captures in `docs/pictures/tui/`. Then the container, its volume and the built image are removed.

## Risks

- Ink and raw mode: no TTY (CI, pipes) → `--script` / `-c` modes that never ask for raw mode.
- `docker exec -it` inside the container: the terminal must pass TERM and the size; Docker does. The project the
  terminal is in maps from the host path (the container's label) to `/workspace`.
- Two renderers drifting (web SVG flow map vs ASCII): the shared fixtures.
- The 0.17.4 engine work touches `runtime/service.py`: this branch changes one line there (started_via).
- `McpLevels.kt` READ_POSTS: the other session adds a line beside ours; whoever merges second rebases.

## Shared with other clients

- **Command table**: `content/agent-clients/commands.json` (names, arguments, help text, `writes`, what an argument
  completes from, `local`, the keys). keel TUI builds its commands from it; the xal client (clients/xal) and any other
  front end read the same file.
- **UI-free core**: `cli/src/core/` (KeelCore: project, flow, inbox, agents, the live stream, KeelBot; commands,
  formatting, markdown, the SSE reader `sse.ts`, the transport). `cli/src/core/index.ts` is its one entry; an xal
  plugin or an OpenTUI front end imports it unchanged. Only `cli/src/ui/` is Ink.
- **Who acts**: `X-Keel-Via: tui | xal | mcp` (+ `X-Keel-Client`, `X-Keel-Tool`); the api records a write as a
  `<via>.call` event, and `options.started_via` of a flow (`mcp`, `tui`, `xal`) shows in `thread.started`.

## Status (built on feat/keel-tui)

Everything above is built. The real check (`scripts/test-keel-tui.sh`) ran against a throw-away keel (an image with
this branch's api, engine, plugins and keel TUI on top of 0.17.3's): captures in `docs/pictures/tui/`.

## Colour, streaming and "working" (0.18.2, feat/tui-polish)

```
  runner (claude --include-partial-messages · API astream · fake)  emit("delta", chunk)
     │
     ▼  models/stream.py DeltaThrottle: the text so far, at most every 80 ms
  engine  helper.delta (KeelBot) · agent.delta (a flow's agents)      ── never stored ──┐
     │                                                                                   │
     ▼  api EventService: published by name, not written to the events table             │
  SSE  ──▶ dashboard KeelBot (live text + caret)   ──▶ keel TUI (the answer as it is written,
                                                        the agents panel's "✎ …" line)
  the whole message (helper.step text / answer) stays the record and replaces the live text
```

- **Colours**: semantic roles (`cli/src/core/theme.ts`: person, keelbot, heading, gate, question, option, recommended,
  chosen, suggest, hint, detail, meta, ok / warn / bad, code, path, link, add / del) on the terminal's own named ANSI
  colours, so its theme picks the shade; fixed shades only where a named colour is too pale (green and amber on light,
  the meta grey on a known background). Palettes KEEL (the default, 0.18.29: the light palette's role choices with fixed shades that read on a dark warm
  background: green #2fa84f, amber #f2b84b, grey #9a958c, violet #a48bff; bold ok/gate/recommended/warn), DARK, LIGHT and
  AUTO (background unknown: meta is the text colour dimmed, nothing fixed). `KEEL_TUI_THEME=keel|dark|light|auto`; unset
  (or the old `terminal`) is keel, or light when COLORFGBG or the background the terminal reports (OSC 11) is light; auto
  follows the background; `/theme keel|dark|light|auto` while it runs; NO_COLOR and FORCE_COLOR. Colour is never the only sign:
  the person's " › " is bold, gates and notices have "◆", suggestions "✦", questions "?" and their number.
- **Code**: highlight.js (the web's highlighter) → the palette's code roles (`highlight.ts`); diffs with line numbers.
- **Working**: a turning compass over a wave, a keel verb per turn, seconds, `~tokens`, `esc to interrupt` (Esc stops
  the turn: `POST …/helper/sessions/{sid}/stop`); one line per agent at work.

### What we took from xal

xal (https://github.com/xal-sh/xal, MIT) is an open-source terminal coding harness. keel's palette stays the base; from
xal we took how to use colour richly (code that was adapted keeps xal's notice; `cli/THIRD_PARTY_NOTICES.md`):

| xal | keel TUI |
|-----|----------|
| colours are the terminal's own 16 (`theme/colors.ts`: faint 8, red 9, green 10, yellow 11, blue 12, magenta 13, cyan 14), so any terminal theme fits | every palette is on the terminal's named colours (`KEEL_TUI_THEME=terminal` is auto now) |
| a person's message on a background 8 % toward the text colour (`userMessageBackground`) | a bold " › " in the person's colour and bold words, with a line of air above (no tint: it needs the background's exact shade) |
| tool calls settle as one line: `>` reads, `*` changes, the title faint, the outcome right-aligned (✓ green, x red, the seconds) | the live tool lines and the settled list above each answer |
| the end of a turn: `✓ Finished in 4.2s`, `x Failed after …`, `! Interrupted after …` | the line under each answer, with the model and tokens |
| a question is amber with `?` and `↑↓ · Enter · Esc` (`permission-popover.ts`) | pickers that ask (title ends in "?") in amber with "?"; lists stay in the accent |
| diffs with line numbers from the hunk headers, hunks in yellow (`output/diff.ts`) | `cli/src/core/diff.ts` (adapted), in code blocks |
| markdown: bullets by depth `• ◦ ▪ ·`, headings 1–2 in the accent, links with the address faint, tables in a box with the header in the accent (`markdown/render.ts`) | the same in `cli/src/core/markdown.ts` (the table layout adapted) |
| a braille spinner at 80 ms | keel's own compass glyph, at xal's 80 ms |

Captures: `docs/pictures/tui/before/` (0.18.1) and `docs/pictures/tui/after/` (this branch), each as text (`*.txt`), with
colours (`*.ansi`, after only: 0.18.1's --script has no --ansi) and as a real terminal session (`interactive.ansi`:
`cat` it in a terminal). The main run's captures (`docs/pictures/tui/*.txt|ansi`) include KeelBot while it writes
(`11a-…`) and its answer (`11b-…`).

## Round 3 (feat/tui-round3): mentions and paste, Fix mode, TUI plugins, --profile, the full screen

| # | What | How | Files |
|---|------|-----|-------|
| 1 | `@` mentions, paste | `@` opens a fuzzy picker over the project's files (`GET …/repo/files`, as the dashboard's KeelBot), symbols from the code graph (`…/graph/search`) and the flow's criteria (`@AC-2`); the turn sends them as `mentions` (the api's `HelperMention`). A paste of many lines becomes `[pasted 120 lines #1]` in the prompt and goes whole to KeelBot; an image path becomes `[image name.png]` with a note: KeelBot takes no images yet, it gets the path | `cli/src/core/mentions.ts`, `ui/App.tsx` |
| 2 | Fix mode: live diffs, Keep / Undo, questions | each write/edit step's diff (line numbers) shows while KeelBot works; after a Fix or Side turn the changed files are listed with their diffs; `/changes`, `/keep [path\|all]`, `/undo [path\|all]`. Undo is the dashboard's (`POST …/undo {path?}`); Keep is its Done, plus a new `paths` so one file can be kept (engine `helper.done(paths)`, api `HelperDoneBody.paths`). KeelBot's permission questions (`helper.permission`) are amber yes/no pickers: once, always, deny (`POST /api/approvals/{id}/decide`) | keelbot engine `helper.py`, api `Helper.kt`, `cli` |
| 3 | TUI plugins | a plugin adds commands and a panel in its manifest, `contributes.tui`: declarative calls to its own api routes (`/projects/{pid}/<plugin>/…`) and how to show the answer (title, items, line templates, diff); nothing runs in the TUI process. A call that changes something (POST) asks first. `GET /api/tui/plugins?project=` lists what the loaded, turned-on plugins add (checked again there); `keel-plugin lint` checks it. Example: the Git plugin's `/git status\|log\|diff` (a new `GET …/git/diff`) and a branch panel (Ctrl+G) | api `tui/TuiPlugins.kt`, `tools/keel-plugin`, `plugins/git`, `cli/src/core/plugins.ts`, `docs/plugins/25-tui-plugins.md` |
| 5 | Full screen | keel's name (version, project, branch, flow) stays on the top line, the prompt and the status line at the bottom; the transcript between them scrolls with PgUp / PgDn, Shift+↑↓, the mouse wheel (SGR reports, `/mouse on\|off`, `--no-mouse`), Home and End, and stays put while new lines come. The app uses the terminal's own screen (DECSET 1049), so the shell's scrollback is untouched and comes back on exit. `/clear` empties the transcript but keeps the start screen. `--inline` (`KEEL_TUI_INLINE=1`) keeps the earlier way: the transcript in the terminal's scrollback. `--script --rows <n>` drives the full screen | `cli/src/core/lines.ts`, `ui/App.tsx`, `main.tsx` |
| 4 | `--profile` | startup time, each api call's latency (by route), SSE lag (receive time − the event's `at`), React commit time per frame, memory every second; a summary on exit and a JSON file (`keel2 tui --profile` copies it out of the container); nothing leaves the machine | `cli/src/core/profile.ts`, `keel2` |

Trust (3): the TUI never loads a plugin's code; `contributes.tui` is data. Code from a plugin would need keel's signed,
trusted-publisher rules (03-security.md) and is not part of this round.

## Open questions (with my recommendation)

1. Where does the TUI run with Docker? **Inside the container (`docker exec -it`)**: Node is there, nothing to
   install on the computer. Native: the release's `cli/keel-tui.mjs` with the computer's Node. `npm i -g` of `cli/`
   gives `keel-tui` for anyone who wants it on the host.
2. Plain `keel2` opened help until now. **Open the TUI when stdin and stdout are a terminal and keel runs; otherwise
   help as before** (`KEEL_TUI=0` keeps help). Every subcommand stays as it is.
3. Write level: the TUI is a person at the keyboard, like the dashboard: **full access, no MCP level token**; its
   writes are recorded as "via keel TUI", not "via MCP".
4. Which KeelBot mode by default? **Ask** (read-only), like the web panel; `/mode fix` when a flow waits.
5. Mermaid output of the renderer: **later** (`format: "mermaid"` is reserved).

## 0.18.4: KeelBot's flow buttons, /agent, clicks

| What | How | Files |
|------|-----|-------|
| KeelBot's flow buttons | ```` ```keel-flow {"do": "approve" \| "reject" \| "stop" \| "mode", …} ```` : decide a gate, stop flows, a run mode; a card in the dashboard (`FlowCard`), a yes / no in keel TUI (which shows `keel-start` too); nothing happens until the person presses | `plugins/keelbot/engine/keel_plugin_keelbot/keelbot.py`, `plugins/keelbot/web/Actions.tsx`, `cli/src/core/buttons.ts` |
| `/run`, `/stop all`, `/stop <n>` | the dashboard's run-mode switch (`POST /threads/{id}/mode`); stop many flows after one yes; `/flows` is numbered | `cli/src/core/commands.ts` |
| `/agent` | the agents at work (flow jobs) and the person's own (KeelBot side chats, own worktree); `/agent <n>` follows one live (its steps every 2 s and the text it writes), `/agent new <task>` starts one; a notice when it is finished | `cli/src/core/keel.ts`, `ui/parts.tsx` `JobPanel` |
| Clicks | the full screen with the mouse: an agent line follows it (Ctrl+O too); the flow panel's waiting line and the status line's waiting ask what to do (approve, send back, answer, stop, read it first); an inbox item answers it. Boxes register with `Clickable`; a click finds the smallest box under it from Ink's layout | `cli/src/ui/click.tsx` |
| Reading | `/status` puts what waits first, one line per criterion; an empty line above each answer; wrapped marked lines hang under their text; the spinner is one braille character |  `cli/src/core/format.ts`, `core/lines.ts`, `ui/thinking.tsx` |

