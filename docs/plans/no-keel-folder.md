# keel keeps its files out of the project (issue #246)

Status: proposal, waits for review and for Milad's OK on the phases.

## Context

Today a new project gets layout `keel` (`engine/keel_engine/layout.py`, `NEW_PROJECT`): keel writes `.keel/` into the
project folder (config.yml, rules, specs, knowledge, ADRs, RUNNING.md, CLAUDE.md, flow state, hunts, worktrees) and
adds `/.keel` to `.git/info/exclude`. Layout `external` moves the files to `~/.keel/projects/<id>/`, but still puts a
`.keel` symlink into the project. Layout `repo` writes into `docs/` and `CLAUDE.md`.

The project folder is the user's. keel should not change it unless the user asks: a user who opens keel on a project
and runs a flow should find only the commits of the work itself, nothing else.

Size: `.keel` is named about 440 times in the engine, 90 in the api, 80 in the web app, 55 in plugins, 270 in the
JetBrains plugin, plus the launcher, the desktop app, content (agents' instructions name `.keel/...` paths) and e2e.
Most engine places build the path by hand (`Path(root) / ".keel" / ...`), and many pass `.keel/...` as a
project-relative path to agents, to the git tools and to the web app.

## Goal

1. By default keel writes nothing into the project folder: no `.keel`, no symlink, no `.git/info/exclude` line, no
   `CLAUDE.md` block, no `docs/` files. The commits of the user's code changes are still made.
2. keel keeps the project's config, rules, specs, knowledge, ADRs, runbook, flow state, hunts and worktrees in its own
   storage, one folder per project: `<KEEL_DATA>/projects/<project-id>/` (the data volume the container already has).
   Structured state that has a database table already (doc history, flows, inbox…) stays in the database.
3. A project that has a `.keel` folder keeps working: keel reads it as today. On the first start keel offers (Inbox
   confirmation) to copy it into its storage; after the copy it asks again before it deletes the old folder, and never
   deletes without that OK.
4. An opt-in setting `export: repo` (per project, off by default) writes the config and rules into the repo for teams
   who want them in git (today's layouts `keel` and `repo` stay available behind it).
5. README, docs/GUIDE.md and the layout docs say where keel keeps its files.

## Acceptance criteria

1. Start keel on a clean git project (no `.keel`), init it, run a flow to the end: `git status --porcelain --ignored`
   and a file listing of the project show only the files the flow's commits changed; no `.keel`, nothing in
   `.git/info/exclude`.
2. The config, rules, specs and knowledge of that project are under `<KEEL_DATA>/projects/<id>/` and the web app,
   MCP and the agents read and change them there.
3. Agents in a flow can read the spec and the knowledge files (absolute paths into keel's storage, allowed by the
   agent sandbox), and cannot write keel's rules there (the rule guard covers the new place).
4. A project with an existing `.keel/` folder starts and runs a flow as before; the migration offer shows in the Inbox;
   saying no leaves everything as it is; saying yes copies, and the old folder stays until a second OK.
5. With `export: repo` the config and rules are written to the repo as today.
6. Two keel worktrees of one project share the project's storage folder (the id is the project's, not the folder's).
7. All CI suites stay green; a new e2e journey checks criterion 1.

## Design

- **One resolver.** `layout.keel_home(root) -> Path` (engine) and `Layout.keelHome(root)` (api) return where keel's
  files are: `<root>/.keel` when that exists (legacy) or when `export: repo` is on, else the project's storage folder.
  Every `Path(root) / ".keel"` goes through it. A new layout name `store` is the default for new projects
  (`NEW_PROJECT = "store"`); `hidden` is true for it; `hide()` does nothing for it.
- **Project id.** The api's project id; for a git project the id is stored in keel's database keyed by the main
  worktree's path (common git dir), so linked worktrees map to the same folder.
- **Paths given to agents and the web app.** Today they are project-relative (`.keel/specs/x.md`). They become a
  logical path (`keel:specs/x.md`) that the resolver turns into the real file; agents get the absolute path. The agent
  sandbox (`tools/agent_sandbox.py`) gets read access to the project's storage folder; the rule guard
  (`rules/__init__.py` `RULE_PATH`) matches the new folder too.
- **Commits.** Spec / knowledge / hunt commits are recorded (doc history) instead of committed, as the hidden layouts
  do today.
- **Worktrees** that keel makes go under `<KEEL_DATA>/projects/<id>/worktrees/` instead of `<root>/.keel/worktrees`.
- **Launcher.** `keel2` already mounts the data volume; nothing new is mounted into the project.
- **Front ends.** The web app, JetBrains and VS Code read keel's files through the api, not from the disk; places that
  read `.keel/` from the disk move to the api.

## Phases (each one PR, each one released; pause after each for review)

1. **Resolver + store layout, engine and api.** `keel_home`, layout `store` as default for new projects, all engine and
   api places through the resolver, sandbox + rule guard, worktrees, commits recorded. Existing `.keel` projects
   unchanged. Tests: unit tests for the resolver, the clean-project e2e journey (criterion 1).
2. **Front ends.** Web app, plugins, JetBrains, VS Code, desktop, content (agents' instructions) use the api / logical
   paths.
3. **Migration + export.** Inbox offer to copy a `.keel` folder into the storage and, with a second OK, delete it;
   `export: repo` setting in the web app's project settings.
4. **Docs.** README, GUIDE, layout docs, CHANGELOG.

## Test plan

- Engine: unit tests for `keel_home` (clean, legacy `.keel`, symlink, export on, linked worktree); the existing layout
  tests keep passing with the old layouts.
- e2e journey "clean project": fake model, init, one flow to done, assert the project folder has no keel files and
  `.git/info/exclude` is unchanged.
- e2e journey "legacy project": a `.keel` project runs a flow; migration offer: no → unchanged, yes → copied, folder
  kept until the second OK.
- Full CI suites per phase.

## Open questions

- Feature freeze (2026-11-18): this is a new default, asked for by Milad in #246; confirm it goes ahead now.
- "Database": the plan keeps keel's files as files in keel's own data folder (one per project), not as rows in the
  database, so the agents and every reader keep working with files. OK, or must they be database rows?
- One PR per phase (4 PRs and releases) instead of one big PR: OK?
