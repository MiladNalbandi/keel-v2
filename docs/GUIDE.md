# keel v2 guide

The [README](../README.md) is the short version. This page has the details.

- [Flows in detail](#flows-in-detail)
- [KeelBot](#keelbot)
- [Quality](#quality)
- [Inbox and run modes](#inbox-and-run-modes)
- [Tasks and Jira](#tasks-and-jira)
- [Logins](#logins)
- [Use keel from Claude Code or Claude Desktop (MCP)](#use-keel-from-claude-code-or-claude-desktop-mcp)
- [Docker mode](#docker-mode)
- [Problems](#problems)
- [Folders in the container](#folders-in-the-container)
- [Without the install script](#without-the-install-script)
- [Develop keel v2](#develop-keel-v2)

## How a flow works

```
 you: "show each player's rank"
   │
   ▼
 spec ─◆─ red ─ green ─◆─ … (one loop per criterion) … ─ review ─◆─ done
        │     │       │                                          │
        │     │       └ you approve each criterion               └ you approve the end
        │     └ an agent writes the smallest code that passes; keel commits it
        └ you approve the criteria; an agent writes a failing test, keel checks it fails, then commits it
```

- The flow is a [LangGraph](https://github.com/langchain-ai/langgraph) graph; the agents are LangChain or CLI agents.
- keel runs the tests and makes the commits itself, on its own branch (`feat/…`). It never pushes, and it never
  commits files you changed yourself.
- keel's commits are by you, and end with `Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>`, so GitHub shows both.
  Settings › Git: **KeelBot as co-author** (on by default) and **Commit author** (`Name <email>`; empty: the
  project's git name — in Docker there is none, so set it there, or the commits are by KeelBot alone).
- Every step, file change, command and token is visible: **Live agents**, **Jobs**, **Budget**.
- **Several flows at once**: while a flow runs in the project folder, the next one runs in a worktree of its own
  (a copy of the project on its own branch, under `.keel/worktrees/`), so neither touches the other's files. Press
  **Start** on three tasks, or **Start another flow** on the Flow page. The Flow page then shows a board: each flow,
  the files two flows both change, the branches that will not merge cleanly, and an order to merge them in. The
  Inbox groups their gates by flow. On top of the Flow page, a tab for each flow that runs or waits switches between
  them, and **History** lists every flow of the project (all, or one workflow's); **Open** shows any of them with all
  its details.
- When something needs you, keel plays a sound, shows a pop-up and its mascot jumps.
- keel v2 has its own guard, rules, agents, skills and MCP server. [keel v1](https://github.com/MiladNalbandi/keel) is a
  separate project and is not in the image.

## Flows in detail

| Flow | What it does |
|---|---|
| **feature** | spec (with questions) → plan → contract → per criterion: failing test, code, review ◆ → security, full review, e2e, smoke → ship |
| **change** | small change without a spec: trivial (one commit) or 1–3 criteria; too big → hands over to feature |
| **fix** | reproduce the bug as a failing test ◆ → find the cause (parallel investigators) ◆ → fix → regression test → ship |
| **diagnose** | a bug you cannot reproduce yet: 3–4 guesses, one investigator each → hand to fix or feature, or write a note |
| **review** | read-only review of the branch, one reviewer per lens; the report word for word |
| **lint** | run the project's formatters and linters (from its stacks and `.keel/config.yml`) → fix the findings without changing behaviour (2 rounds) → `chore(lint)` commit → report ◆ |
| **cover** | measure coverage of the changed lines → per gap: test / delete / accept with a reason |
| **ship** | verify, static checks, release tests, coverage, deps, audit, trace, lens reviewers, final review ◆, memory, PR body ◆ (never pushes) |
| **hunt** · **hunt-next** | read-only bug hunt: hunters per lens, provers reproduce each candidate twice, a report; the top bug goes to fix |
| **init** · **knowledge-refresh** | set a project up (architecture, setup checks, knowledge) · refresh `docs/knowledge/` |

◆ = keel stops and waits for you. The flows are YAML files in `content/workflows/`; the **Workflows** page edits them.
A workflow can sit in a **Folder** (type a name above the blocks; the list groups by folder), and the page shows how
often it ran and opens its last run. A workflow does not need agents: a few code steps (`run: npm test`) and a gate
automate a task without any model. KeelBot writes one for you when you ask.

## KeelBot

Open the Code page and press **⌘I** (or the KeelBot button at the bottom of the left bar), or open **KeelBot** in the
menu. Ask about the project in plain words. KeelBot reads the code, the knowledge pages, the map and the code graph,
and every answer links to the lines (`file:line` opens the editor there).

KeelBot also knows keel itself: the workflows this project can run and its flows. It gives you buttons; nothing
starts or is saved until you press one.

- **Which workflow?** Ask "how do I add a weekly report page?": KeelBot names the workflow that fits and why, and
  gives a **Start the flow** button with a title (you can change it) and the request every agent gets.
- **About a running flow**: "why does Euro prices wait?", "how far is the fix?", "what did it cost?".
- **A workflow of your own**: "make a workflow that runs the linters and the tests, then stops for a look". KeelBot
  writes it with only the steps the task needs (code steps only when no model is needed); keel checks it on the spot
  and shows what it runs. **Save the workflow** (into a folder if you like), or **Ask KeelBot to fix it** when the
  check finds a problem.

- **More room**: hide keel's menu with ‹ next to the bell (or ⌘\; ☰ brings it back); drag KeelBot's left edge to make it wider; **Focus** (Code page header, or **Hide the header** in
  the status bar) hides the Code header; **KeelBot only** (or ⤢ in the panel, or **KeelBot** in the menu) opens the
  KeelBot alone on its own page, where a `file:line` link opens the Code page at that line.
- **Point at things**: select lines and press **Ask** (or ⌘I); type `@` for a file, a symbol or a criterion of the
  running flow; the open file always goes along.
- **Commands**: type `/` — `/explain`, `/where`, `/review`, `/plan`, `/gate` (what the waiting gate asks), `/test`. A
  project can add its own in `.keel/plugins/<name>/plugin.yml` (see `content/plugins/core/plugin.yml`).
- **Ask mode changes nothing**: no edit, no new file, no command that changes files or git, whatever the model.
- **Fix mode** (while a flow waits at a gate): switch to **Fix** and tell KeelBot what to change. It works inside the
  rules of the flow's phase. A command that changes something waits for your OK in the panel or the Inbox (**Allow
  once**, **Always**, **Deny**). The panel lists every changed file with its diff and **Undo**. **Done** runs the tests
  and lets keel commit only those files; if the tests fail, one click hands the failure back to KeelBot.
- **Side sessions** (any time): switch to **Side** to try an idea in KeelBot's own copy of the project (a git
  worktree on its own branch). Nothing touches your project folder or a running flow. **Keep** runs the tests and
  commits on that branch. Then **Make a task**, **Start a flow on the branch** (a change flow writes the tests for it),
  or **Throw away**.
- **Costs**: each answer shows its tokens and time; the budget bar counts them. Claude and Codex continue their own
  session, so a follow-up question is cheap (the context stays cached).
- The model is the `helper` agent's (Agents page); change it for one chat in the panel.

## Plugins: Database, Git and CI/CD

Turn them on in **Tools › Plugins** (for this project, or for every project). Nothing new to learn: they add to the
pages you use.

- **Database**: add the project's database in **Connections › Databases** (keel suggests what docker compose,
  `.env.example` or Spring's config name; the password is saved encrypted). Then:
  - ask KeelBot data questions ("which players have no score?") — it reads the schema, runs one read-only query and
    shows the rows; `/sql` does it directly;
  - **Map › Database (ER) › Query**: click a table for its first rows, or write SQL;
  - workflows get `db:query`, `db:check` (a data check: `expect: none`), `db:change` and `db:migrate` blocks — a
    workflow of code steps only checks your data with no model and no tokens.
  A change of data (INSERT, UPDATE, DELETE) runs only on a **local** or **test** database: keel counts the rows first,
  then you press **Run it**. Staging and prod are read only for everyone. Schema changes belong in your migrations.
- **Git**: add a token in **Connections › GitHub**. Then **Code › Source control** shows the branch against its
  remote and main, **Push**, **Update from main**, **Switch branch**, **New branch**, a commit box, and the pull
  request with its CI checks and review comments (**Ask KeelBot to address the comments**). KeelBot reads git and the
  PR (`/commit`, `/pr`, `/sync`, `/branch` draft the next step) and gives buttons; workflows get `git:sync`,
  `git:push`, `git:pr`, `git:pr-checks` (wait for CI), `git:branch` and `git:cleanup`. keel never force-pushes and never
  pushes to main.
  Under **Branches**, click a branch (for example `feat/test`) to open it as a tab: the files it changed since it left
  main (click one for its diff), its commits that main does not have, **Switch to feat/test**, and **Ask KeelBot to
  review it**. Switching keeps your uncommitted work; git stops when a change would be lost.
- **Database tool (Code page)**: with the Database plugin on, the Code page gets a **Database** activity, like the
  database window of an IntelliJ IDE: every connection as a tree of tables and columns (keys, types, foreign keys).
  Click a table for its data (and its structure); press the console icon on a connection for a query console
  (⌘↵ runs the statement under the cursor). Several connections side by side: a local, a test and a read-only prod.
- **CI/CD**: keel watches the pipelines on GitHub Actions (with the GitHub token) and tells you when one fails.
  **Run › Jobs › Pipelines** shows the runs; open a failed one for its failed jobs and the end of the log, then
  **Fix it** (the ci-fix flow: read the failure, fix, commit, push, wait for CI), **Run the failed jobs again**, or
  **Ask KeelBot why** (`/ci`). **Settings › When CI fails**: tell me, start the fix flow by itself, or nothing.
- **Claude Code**: `keel2 mcp` adds the read tools (`keel_db_query`, `keel_git_status`, `keel_ci_failure` …);
  `keel2 mcp --write` also the acting ones, and each waits for your **Allow** in keel's Inbox.

## Quality

**Build › Quality** shows how well keel's flows work with each model. keel runs small eval projects (in
`content/evals/`) through the flows you pick, in run mode **auto**, one case at a time, and scores each run from 0 to
100: did it reach the end, how often was something sent back, and did it stay within the token estimate. Pick one or
two models and press **Run now**, or turn on **Every night at …**. When a score falls by 15 points or more since the
run before, the page shows it in red: check the last change to a prompt, an agent, a skill or a model. The runs' tokens
count in the budget.

## Inbox and run modes

**Inbox** lists everything that waits for you in every project: gates, the explorer's questions, a check that keeps
failing, a budget pause, a new dependency. Answer it there or open its flow.

Each flow has a run mode. Pick it in *Start a flow* (the default is in Settings), and change it on the Flow page while
the flow runs.

| Run mode | keel decides by itself |
|---|---|
| **manual** (default) | nothing: it stops at every gate |
| **important** | a criterion's gate, when its checks pass and its review is clean |
| **auto** | every gate it can decide (plain approve, the default choice, the recommended answers); each is logged and listed in the final review and the PR body; it never opens a PR |
| **readonly** | nothing; agents cannot edit, write or commit anything |

In every mode keel still stops for the token cap, a new dependency, a secret in a commit, a check that keeps failing,
and anything only you can answer. It never pushes.

## Tasks and Jira

**Tasks** is a board of your work: your own tasks, or the tickets of a Jira board or query (Connections › Jira, per
project: Jira Cloud with email + API token, or Server / Data Center with a personal access token).

Press **Start** on a task and keel runs a flow for it (bug → fix, story → feature, task → change). It moves the Jira
ticket to In progress, to In review when the PR opens (and asks the reviewers on GitHub and in Jira), and to Testing
(PP) when the PR is approved. Then the Inbox asks you to confirm PP testing and the release. When keel cannot reach
Jira, the Inbox asks you to move the real ticket by hand. Without Jira, tasks stay in keel with their history.
Details: [CONTRACT.md](CONTRACT.md), "v0.5.0: tasks and Jira".

## Logins

The image has claude, codex, copilot and opencode installed. Nothing from your computer is copied in. Each login is
saved encrypted in keel's own database (the `keel-data` volume), so it survives restarts and updates.

| Tool | Log in from the dashboard | Or paste |
|---|---|---|
| Claude (subscription) | sign in on claude.com, paste the code it shows | `claude setup-token` on your computer |
| Codex (ChatGPT) | open the device link, type the code | `~/.codex/auth.json` |
| GitHub Copilot | open github.com/login/device, type the code | a token with "Copilot Requests" |

From the terminal: `keel2 token <claude|codex|copilot>`, and `keel2 tokens` to list them.

## Use keel from Claude Code or Claude Desktop (MCP)

keel has an MCP server: your Claude can read the flow (status, timeline, next step, phase rules) while keel runs.

```bash
claude mcp add keel-v2 -- "$(command -v keel2)" mcp      # Claude Code, read-only
keel2 mcp --print-config                                 # the same line + the Claude Desktop JSON
```

Read-only by default. With `keel2 mcp --write` (and `--write --print-config`) your Claude can also approve or send
back a waiting gate. keel's own agents always get it read-only. keel v1 is a separate project; its MCP server can be
added like any other: `keel2 start --with-keel-v1 <keel checkout>`, then turn on "keel v1 (optional)" in Tools.

## Docker mode

The image has JDK 21, Node 20, Python 3.12 and the Docker CLI (compose, buildx). `keel2 start --docker` mounts your
Docker socket and the project at the **same path** as on your computer, so Testcontainers and compose bind mounts
work. Docker access is as strong as root on your computer: use it only for projects you trust.

## Problems

Run `keel2 doctor` first. The most common ones:

| You see | Do this |
|---|---|
| "Docker is not running" | start Docker Desktop (keel2 starts it on a Mac) |
| "not a git repository" | say yes when keel2 offers `git init` |
| port 8080 is busy | nothing: keel2 uses the next free port and tells you |
| an agent "is not logged in" | Connections › Set up login |
| your tests start containers | `keel2 start --docker <folder>` |

All commands: `keel2 help` (logs, tokens, backup, restore, uninstall …).

## Folders in the container

| Folder | What |
|---|---|
| `/workspace` | your project (one git repo, or a folder of repos); its real path with `--docker` |
| `/data` | keel's database, flow checkpoints, encrypted logins |
| `/opt/keel-v2/content` | keel v2's agents, skills, stacks, packs, templates and workflows |
| `/opt/keel-v1-optional` | only with `--with-keel-v1`: that keel v1 checkout, read-only, for its MCP server |

## Without the install script

```bash
docker run -d --name keel-v2 -p 127.0.0.1:8080:8080 \
  -v /path/to/your/project:/workspace -v keel-data:/data \
  ghcr.io/miladnalbandi/keel-v2
```

## Develop keel v2

| Part | Tech | Run | Tests |
|---|---|---|---|
| `engine/` | Python 3.12, FastAPI, LangGraph, LangChain, MCP | `uv run keel-engine` (:8090) | `uv run pytest` |
| `api/` | Kotlin, Spring Boot 3, SQLite, Flyway | `./gradlew bootRun` (:8080) | `./gradlew test` |
| `web/` | React, TypeScript, Vite | `npm run dev` (:5173) | `npm test` |

How the parts talk: [CONTRACT.md](CONTRACT.md).

```bash
docker build -t keel-v2 .                                 # everything is installed in the image
docker build --build-arg INSTALL_CLIS=0 -t keel-v2:slim . # without the agent CLIs
npx -y -p playwright@1 node docs/gif/record.js            # record the README's GIF from a running keel
scripts/no-v1.sh                                          # lists anything that still depends on keel v1 (CI: 0)
```

A version tag (`v*`) builds amd64 + arm64 images and pushes them to `ghcr.io/miladnalbandi/keel-v2` (and to Docker
Hub when the `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets are set). Dependabot (`.github/dependabot.yml`) opens a
pull request every week for each part's dependencies, the Docker base images and the GitHub Actions.
