# keel TUI: a Settings screen (`/settings`)

keel TUI keeps a few choices of its own (the sidebar, the colour overrides, and since 0.18.63 whether this window
follows the other keel windows), and has more that the person sets with a flag or an environment variable at the
start. They are spread over `/sidebar`, `/mouse`, `/theme`, `/follow`, `keel-tui --help` and `~/.config/keel2/tui.json`.
A Settings screen puts them in one place, like the dashboard's Settings › General › This browser.

Branch `feat/own-focus-per-window`. What is built is the first step below; the full screen is a mockup for a later
version.

## Why now: one window, its own project (0.18.63)

The shared focus (keel 0.18.6, `api/.../ui/UiFocus.kt`) keeps every keel front end on the same project and KeelBot
chat. That is right for one person with one dashboard tab and one keel TUI, and a mess when they work on two
projects at once: a pick in one window moves every window. **Follow other keel windows** is one switch per window:

```
                       Follow other keel windows
                       on (the default)                   off
  ─────────────────────────────────────────────────────────────────────────────────────────────────
  another window       this window goes there too          this window stays where it is
  picks a project
  or a chat
  this window picks    the other windows go there too       no other window moves
  a project or a chat  (PUT /api/ui/focus)                  (nothing is sent)
  at the start         the focus's project and chat         web: this browser's last project
                       (unless --project / the folder)      keel TUI: --project, the folder, else the first
  kept in              web: localStorage keel2.followFocus  keel TUI: tui.json { "follow": false }
```

One switch, not two ("don't follow" and "don't move the others"): a window that follows but never shares would be
moved by the others and never move them back, and a window that shares but never follows would pull the others to
where it is without going where they are. Neither is what "stay on my own project" means, and the code has one gate
for both ways (`followsFocus()` on the web, `KeelCore.follows` in keel TUI).

## Built: `/settings` and `/follow` (0.18.63)

`/settings` opens keel TUI's picker with each setting and its value now; Enter changes the marked one (Follow,
the sidebar and the mouse switch; Theme opens a second picker). `-c "/settings"` lists Follow and the sidebar (the
mouse and the theme belong to a running app).

```
│  /settings
│
╭──────────────────────────────────────────────────────────────────────────────────────────────────╮
│ keel TUI settings (Enter changes the marked one, Esc leaves)                                     │
│                                                                                                  │
│ ❯ 1. Follow other keel windows  on                                                               │
│      the project and KeelBot chat move with the dashboard and other keel TUIs, both ways         │
│      (/follow); kept                                                                             │
│                                                                                                  │
│   2. Sidebar                    on                                                               │
│      on a terminal 120 columns wide or more, full screen (Ctrl+B, /sidebar); kept                │
│                                                                                                  │
│   3. Mouse                      off                                                              │
│      the wheel scrolls, a drag copies (/mouse); this run (--no-mouse at the start)               │
│                                                                                                  │
│   4. Theme                      keel                                                             │
│      keel, dark, light or auto (/theme); this run (KEEL_TUI_THEME at the start)                  │
│                                                                                                  │
│ 1–4 picks · ↑↓ · Enter · type to filter · Esc                                                    │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯

  keel TUI settings (Enter changes the marked one, Esc leaves) → Follow other keel windows  on
✓ follow off: this window keeps its own project and KeelBot chat: it follows no other keel window, and
  what you open here moves none of them
```

`/follow` says which it is; `/follow off` and `/follow on` switch it. Nothing moves when it switches: the next
project or chat opened here or elsewhere is shared again.

## Mockup: the full Settings screen (later)

A screen of its own, like a plugin's mode (`core/modes.ts`): it takes the space between keel's header and the status
line until Esc. Sections in the order the person needs them; each row says where it is kept. Only what keel TUI really
has today is on it.

```
╭─ keel TUI settings ─────────────────────────────────────────────────────────────── Esc closes ─╮
│                                                                                                 │
│  This window                                                                                    │
│  ❯ Follow other keel windows                              [● on ]   kept                        │
│      on: the project and KeelBot chat move with the dashboard and the other keel TUIs,          │
│      both ways. off: this window keeps its own and moves none of them.                          │
│    Project                                                demo  ›   /project                    │
│                                                                                                 │
│  Screen                                                                                         │
│    Sidebar (120 columns or more, full screen)             [● on ]   kept · Ctrl+B               │
│    Mouse: the wheel scrolls, a drag copies                [○ off]   this run · --no-mouse       │
│    Theme                              keel · dark · light · auto    this run · KEEL_TUI_THEME   │
│    Colour overrides                   ok=#3cc463  gate=ansi:yellow  tui.json colors ·           │
│                                                                     KEEL_TUI_COLORS wins        │
│    Panels at the start                flow · agents · inbox  (none) this run · Ctrl+F/A/N       │
│    Glyphs                             unicode                       KEEL_TUI_ASCII=1, TERM=dumb │
│    Clipboard                          OSC 52 through the terminal   KEEL_TUI_CLIPBOARD          │
│    Full screen or inline              full screen                   --inline · KEEL_TUI_INLINE  │
│                                                                                                 │
│  KeelBot (this chat; kept by keel, the same in the dashboard)                                   │
│    Model                              claude · sonnet           ›   /model                      │
│    Thinking effort                    default                   ›   /effort                     │
│    Mode                               ask · edit · fix · side       Shift+Tab · /mode           │
│                                                                                                 │
│  Connection (read only: set at the start)                                                       │
│    keel                               http://localhost:8080         --url · KEEL_URL            │
│    Dashboard links                    http://localhost:8080         --public-url                │
│    Settings file                      ~/.config/keel2/tui.json      KEEL_TUI_SETTINGS           │
│    Profiles (--profile)               ~/.local/state/keel2/tui-profiles                         │
│                                                                                                 │
│  ↑↓ move · Space or Enter changes · ← → picks a value · / filters · Esc closes                  │
╰─────────────────────────────────────────────────────────────────────────────────────────────────╯
```

- **kept** rows write `tui.json` (settings.ts). A write reads the file again first (0.18.63), so two keel TUIs do not
  drop each other's settings; a running keel TUI keeps what it read at its start for the rest of its run.
- **this run** rows change the running app; the flag or variable next to them sets them at the start. Keeping them in
  `tui.json` too (theme, mouse, panels) is the obvious next step and needs only a key each.
- **KeelBot** rows open the pickers that exist (`/model`, `/effort`); they are the chat's, kept by keel.
- **Connection** rows are read only: changing keel's address means a new keel TUI.
- keel TUI has no sounds and no own key map today: keys are fixed (content/agent-clients/commands.json `keys`), so the
  screen has no row for them.

## Files

| What | Where |
|------|-------|
| The switch, web | `web/src/state.tsx` (`followsFocus`, `shareFocus`, `followFocus` / `setFollowFocus`), `web/src/pages/settings/General.tsx` (This browser), `plugins/keelbot/web/HelperPanel.tsx` |
| The switch, keel TUI | `cli/src/core/keel.ts` (`follows`, `setFollow`, `CoreOptions.settings`), `cli/src/core/settings.ts` (`follow`), `cli/src/main.tsx` (one store) |
| `/follow`, `/settings` | `cli/src/core/commands/session.ts`, `content/agent-clients/commands.json`, `cli/src/ui/App.tsx` (`Ctx.view`) |
| Tests | `web/src/test/uifocus.test.tsx`, `plugins/keelbot/web/test/keelbot-chats.test.tsx`, `cli/test/follow.test.tsx` |
| Contract | `docs/CONTRACT.md` § keel 0.18.63 |
