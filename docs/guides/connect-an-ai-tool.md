# Connect an AI tool (MCP)

Let Claude Code or another AI tool read keel and, if you allow it, answer gates and start flows for you.

## What MCP is

**MCP** (Model Context Protocol) is a standard way for an AI tool to use another program's tools. keel has an MCP
server. Your AI tool starts it with `keel2 mcp` and then can call keel's tools, like `keel_status` or
`keel_start_flow`.

```text
 your AI tool ──starts──► keel2 mcp ──► keel (running on my-computer)
      │                      │
      └─ asks you before ────┘  pushes, deletes, stops, saves …
```

## Three levels

Each level has everything of the level below it.

| Level | Command | What it adds |
|---|---|---|
| read (default) | `keel2 mcp` | status, flows, Inbox, live agents, budget, specs, search, read files, rules, CI report … |
| write | `keel2 mcp --write` | decide or send back a gate, answer the Inbox, start, stop and resume flows, set a run mode, ask KeelBot, run CI jobs, git / PR / database writes, Code Review comments |
| admin | `keel2 mcp --admin` | delete flows, pause, add and remove projects, switch the project, connect GitHub, change workflows and rules |

Start with the lowest level you need. keel's own agents always get the read level.

## Connect Claude Code

In a terminal, run:

```bash
claude mcp add keel -- keel2 mcp --write
```

To see the ready lines for each level (the Claude Code line and the Claude Desktop JSON):

```bash
keel2 mcp --print-config
```

## Other tools: Help › Start here

Open **Help › Start here** in the dashboard. It is a 5-step checklist. **Step 3** shows the command to connect
Claude Code, opencode, Codex, and Cursor / VS Code. Copy the line for your tool from there.

## Check that it works

Ask your AI tool to call `keel_health`. It says whether keel is up and how often the connection reconnected.

If it does not work, run `keel2 doctor` in a terminal. It checks Docker, the port, the image and the project, and
says how to fix problems. More in [Troubleshooting](#/help/troubleshooting).

## Which project

A tool you give no `project` uses the only project. Else it uses the project that holds the folder the AI tool runs
in. Else it names the projects and asks you to pick one.

## When keel restarts

After `keel2 add` or `keel2 update`, `keel2 mcp` waits for keel and reconnects by itself. A call in between answers
"keel is restarting, try again in a few seconds". `keel_reconnect` starts the connection again now (for example
after an update, to get the newest tools).

## keel asks you first

Pushes, database changes, deletes, stops and saves ask you first, in your AI tool's own form.

**A tool without forms**: then you confirm in keel's Inbox.

1. The tool does nothing yet. It answers "This needs the person's OK: <what>" with a one-time token.
2. keel puts the same question in its Inbox (the dashboard, keel TUI, the phone app).
3. For the dangerous kinds (deletes, pushes, stops, a model change, a data change, a pull request) you approve the
   question in keel's Inbox. Then the tool calls again with `confirm: "<token>"`.
4. For the other kinds, the token works when you say yes in the chat (within 5 minutes, once, for that call only).

Everything the tool changes shows on the flow's timeline as "via MCP · <client>: <tool>".

## Danger: --yes

```bash
keel2 mcp --yes      # DANGER: skips every question
```

With `--yes` the model's acting tools run with **no question to you**. Do not use it unless you are sure.

To keep only some tools, use `--allow`:

```bash
keel2 mcp --write --allow keel_status,keel_approve_gate
```

## Useful tools

- `keel_status`, `keel_inbox`: what waits, with options and the recommended one.
- `keel_approve_gate`, `keel_answer`: decide a gate (see [Answer a gate](#/help/answer-a-gate)).
- `keel_start_flow`: start a flow. Without a type it lists every flow and its inputs.
- `keel_models`, `keel_agent_model`: models and efforts (see [Choose models per step](#/help/models-per-step)).
- `keel_ci_run`, `keel_ci_report`: your CI jobs (see [Run your CI jobs before ship](#/help/ci-before-ship)).

In keel 0.18.66 the **Settings › Access** tab also has a part for AI tools (MCP).
