# The keel TUI

keel TUI is keel in your terminal: chat with KeelBot, start flows, answer gates and watch agents with `/` commands.

## Start it

keel must run first (`keel2 start`). Then, in a terminal:

```bash
keel2          # or: keel2 tui
```

A **TUI** is a "text user interface": an app that runs inside the terminal. keel TUI starts on the project of the
folder you are in. What you do there shows in the dashboard as "via keel TUI".

## The screen

```text
 keel v2 0.18.66 · ✓ claude · today 120k $0.84 · month 2.1M $14.20   ← version, logins, spend
 ─────────────────────────────────────────────────────────────────
 KeelBot's answers, flow events and command output scroll here
 ─────────────────────────────────────────────────────────────────
 › Ask KeelBot, or / for commands
 [?] ask · my-app · main · ◆ 1 waiting here · ctx 41%                 ← where you are
```

- Type a question and press **Enter**. KeelBot answers.
- Type `/` for commands. **Tab** completes them.
- `ctx 41%` is how full the chat's context is. It is yellow from 70% and red from 85%. `/compact` makes room.

## Typing

- **Enter** sends. **Alt+Enter** or **Ctrl+J** adds a new line.
- **↑ / ↓** in an empty prompt: your earlier prompts of this project.
- `@` opens a picker for files, symbols and criteria (`@AC-2`).
- `!git status` runs a shell command in the project folder, through keel's guard. Its output goes with your next
  question.
- `/editor` or **Ctrl+X E** opens the prompt in your editor.
- **Esc** stops KeelBot's answer.

## KeelBot's modes

The first word of the bottom line is KeelBot's mode. **Shift+Tab** (or **Alt+Tab**) goes to the next one, or type
`/mode <ask|edit|fix|side>`.

| Mode | What KeelBot may do |
|---|---|
| `[?] ask` | read only |
| `[=/] edit` | change files in the project folder, no flow needed |
| `[-{] fix` | only while a flow waits at a gate |
| `[-<] side` | work on its own branch, in its own copy of the project |

## Main commands

`/help` lists them all, in groups.

| Group | Commands |
|---|---|
| Flows | `/project`, `/flow [workflow] [idea…]`, `/flows`, `/resume`, `/stop`, `/run`, `/status`, `/next` |
| Gates | `/inbox`, `/approve [n]`, `/reject [n] <why>`, `/answer` |
| Look | `/jobs`, `/agent`, `/live`, `/steps`, `/timeline`, `/diagram`, `/rules`, `/budget`, `/dashboard` |
| KeelBot | `/model`, `/effort`, `/chats`, `/new`, `/mode`, `/changes`, `/keep`, `/undo`, `/redo`, `/compact`, `/fix` |
| Screen | `/panel`, `/clear`, `/copy`, `/mouse`, `/theme`, `/follow`, `/settings`, `/quit` |

Some examples:

```
/flow quick fix the typo on the login page
/status
/approve
/reject The test does not cover an empty list.
/undo
```

Plugins add more. For example the Code Review plugin adds `/review`, and the Supervisor adds `/supervisor`.

## Panels and keys

- **Ctrl+F** the flow rail, **Ctrl+A** the agents, **Ctrl+N** the Inbox, **Ctrl+B** the sidebar (on a terminal
  120 columns wide or more).
- **Ctrl+O** opens the last tool call's output, or follows the agent at work.
- **PgUp / PgDn** (also the mouse wheel, Home, End) scroll.
- Drag with the mouse to copy text. `/copy` copies the last answer. In macOS Terminal copying does not work this
  way: type `/mouse off`, then select.

## Leave

`/quit` or **Ctrl+C** twice. keel keeps running.

## Several windows

keel TUI and the dashboard follow each other: switch the project or the chat in one, and the other follows. To keep
a window on its own project, type `/follow off`.

## Options

```bash
keel2 tui -c /status -c /next   # print answers without opening the app
keel2 tui --inline              # keep the transcript in the terminal's scrollback
keel2 tui --no-mouse            # give the mouse back to the terminal
```

## Colours

keel TUI has its own colours for a dark background. Change them:

- `/theme keel|dark|light|auto` while it runs, or `KEEL_TUI_THEME=light` before you start it.
- `NO_COLOR=1` gives plain text.
- Change single roles with `KEEL_TUI_COLORS`, for example:

```bash
KEEL_TUI_COLORS="ok=#3cc463,gate=#e0a13a" keel2 tui
```

Green means ok, amber means a gate or a warning, red means an error. `/theme` with no word lists your changes.
