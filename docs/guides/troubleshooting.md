# Troubleshooting

When keel does not work, start with `keel2 doctor`; this page lists the most common problems and their fixes.

## First: three commands

```bash
keel2 doctor      # check Docker, the port, the image and the project, and say how to fix problems
keel2 status      # is keel running, for which project, on which port, which edition
keel2 logs        # follow keel's log (Ctrl+C to leave)
```

```text
 something is wrong
      │
      ▼
 keel2 doctor ──► it says the fix ──► do it
      │
      └─ not clear ──► keel2 status (is it running?) ──► keel2 logs (what happened?)
```

## Common problems

| You see | Do this |
|---|---|
| "Docker is not running" | start Docker Desktop (keel2 starts it on a Mac) |
| "not a git repository" | say yes when keel2 offers `git init`; for a git worktree, see below |
| port 8080 is busy | nothing: keel2 uses the next free port and tells you |
| an agent "is not logged in" | Settings › Models & logins › Set up login |
| your tests start containers | `keel2 start --docker <folder>` |
| "Sign in to keel" in the browser | `keel2 open` |
| copying in keel TUI does nothing (macOS Terminal) | `/mouse off`, then select |

All commands: `keel2 help`.

## A login does not work

Paste the token in one piece from the clipboard. A long token is often shown over two lines.

```bash
pbpaste | keel2 token claude       # macOS
wl-paste | keel2 token claude      # Linux
```

keel checks the token before it saves it and says what is wrong. `keel2 tokens` lists the saved logins.

## A git worktree is "not a git repository"

A git worktree has a `.git` **file** that points to its repository somewhere else. Since 0.18.17 `keel2 start` and
`keel2 add` also mount that repository. Get the newest `keel2` script, then run `keel2 restart`. When keel still
cannot see it, the message says which folder keel checked, what git said, and the fix.

## Commits are refused: a keel v1 state file

A project that ran keel v1 may still have `.keel/state.json`. It blocks commits. Archive it (nothing is deleted):

```bash
keel2 state          # what keel v1 left in this folder
keel2 state close    # archive it to .keel/archive/
```

## A configured command no longer runs

```bash
keel2 doctor my-app
```

It checks that the project's commands can still run (a compose service, a composer or npm script, a make target).
Each stale one is listed with its line in `.keel/config.yml`.

## Your AI tool (MCP) does not connect

- Ask the tool to call `keel_health`: is keel up, and how often did the connection reconnect?
- "keel is restarting, try again in a few seconds": wait. After `keel2 add` or `keel2 update`, `keel2 mcp`
  reconnects by itself.
- After an update, `keel_reconnect` starts the connection again, with the newest tools.
- Check the command with `keel2 mcp --print-config`.
- Run `keel2 doctor`.

More in [Connect an AI tool (MCP)](#/help/connect-an-ai-tool).

## The phone cannot reach keel

See [Use keel from your phone](#/help/phone). When keel2 shows the wrong Wi-Fi address, set `KEEL_LAN_IP`.

## A plugin breaks keel

Start keel with only the plugins that came with it:

```bash
keel2 start --safe
```

## Update keel

From keel 0.18.66, keel checks once a day for a newer release. When there is one, it shows a small notice with the
update command. To update:

```bash
keel2 update
```

It gets the newest image and restarts keel if it is running. In native mode it gets the newest release, and
`keel2 update --rollback` goes back one version.

To use one exact image (a **pinned** image: a fixed version that does not change), set `KEEL_IMAGE`:

```bash
KEEL_IMAGE=<image> keel2 update
```

After an update or a restart the container is new. keel installs a stack's `setup:` packages again at start.

## Save your data first

```bash
keel2 backup         # keel's data (database, logins, history) to a .tgz file
keel2 restore <file> # put a backup back
```

## Still stuck?

Read the long guide (Help › All of the guide), or ask KeelBot. After a failed button keel asks "It did not work.
Find a fix?". In keel TUI, `/fix` sends the last error to KeelBot.
