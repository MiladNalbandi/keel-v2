# keel keeps its files out of the project (issue #246)

Status: proposal, round 2 (round 1 review by Dash: STU-115). Waits for review and for Milad's OK.
When the build starts, the tasks below are tracked as an OpenSpec change `no-keel-folder`.

## Context

Today a new project gets layout `keel` (`engine/keel_engine/layout.py`, `NEW_PROJECT`): keel writes `.keel/` into the
project folder and adds lines to `.git/info/exclude`. Layout `external` keeps the files in `~/.keel/projects/<id>/` but
puts a `.keel` symlink into the project. Layout `repo` writes into `docs/` and `CLAUDE.md`.

What keel writes into a project today (all must move or stop in the new default):

| What | Where now | Written by |
|---|---|---|
| config, rules | `.keel/config.yml`, `.keel/rules/` | init, Rules page, MCP |
| specs, knowledge, ADRs, runbook, CLAUDE.md block | `.keel/{specs,knowledge,adr}/`, `.keel/RUNNING.md`, `.keel/CLAUDE.md` (layout keel) | flows, Wiki |
| stacks, skills, agents, ladder, flow state, archive, hunts | `.keel/stacks/`, `.keel/skills/`, `.keel/agents/`, `.keel/ladder.json`, `.keel/state.json`, `.keel/archive/`, `.keel/hunts/` | engine |
| flow worktrees | `.keel/worktrees/<name>` | `tools/worktrees.py` |
| exclude lines | `.git/info/exclude` (`/.keel`, `ENGINE_FILES`, worktrees) | `layout.hide`, `git.exclude_engine_files`, `worktrees.add` |
| stack switches | `<git common dir>/keel/stacks.json` | `runtime/stacks.py local_file` |

Size: `.keel` is named ~440× in the engine, ~90 in the api, ~80 in web, ~55 in plugins, ~270 in JetBrains, plus
`keel2`, desktop, VS Code, content (agents' instructions) and e2e.

## Goal

1. New default: keel writes nothing into the project's working tree and does not touch `.git/info/exclude` or
   `.git/config`. Allowed inside `.git` (git's own work for the user's changes): objects, refs, branches and
   `.git/worktrees/*` metadata of keel's flow worktrees.
2. keel keeps the project's files in its own storage, one folder per project (the **store**). Rows that already live
   in the database (doc history, flows, inbox …) stay there.
3. Projects that already have `.keel` (folder or link) keep working exactly as today (mode `legacy`). keel offers once
   to move them into the store; a "no" is remembered; deleting the old `.keel` needs a second OK.
4. Opt-in mode `export` writes config and rules into the repo's `.keel/` for teams who want them in git. Off by default.
5. README, docs/GUIDE.md and the layout docs say where keel keeps its files.

## Design

### Storage mode, stored once (not probed)

- Each project gets a stored **storage mode** in the api database: `legacy`, `store` or `export`.
  - Set once, when the project is first registered (or on the first start of this version for known projects):
    a `.keel` folder or link exists → `legacy`; else `store`. Projects known before this version are `legacy` only if
    they have `.keel`.
  - It changes only by an explicit action: the migration (legacy → store) or the export switch (store ↔ export).
  - A later `mkdir .keel`, a branch with a tracked `.keel/config.yml`, or a worktree seed never changes it.
- The engine gets the mode and the store path from the api with the project (the engine already calls the api; no
  disk probing). When the engine runs without the api (tests, tools), the default is `legacy`, i.e. today's behaviour.

### The store and the project id

- Store path: `<KEEL_DATA>/projects/<project-id>/`, where `project-id` is the api's `ProjectRow.id` (the stable slug).
  It does not depend on the folder's path: the api already matches a project that is seen through another mount
  (plain ↔ `--docker`, `HostMount`, `movedHere`) or comes back after being parked, and keeps its id.
- Not-git projects get a store too (the id is not tied to git).
- A root the api does not know as a project gets no store: the engine refuses to write keel's files for it and says
  "add this folder as a project first". keel never creates an empty store silently for an unknown folder.
- Store layout:
  - `docs/`: specs, knowledge, adr, RUNNING.md, CLAUDE.md block, hunts. Agents may read and write here (a spec step
    writes the spec).
  - `private/`: config.yml, rules/, stacks/, skills/, agents/, ladder.json, state.json, archive/, stacks.json. Agents
    never see it.

### One resolver, logical paths unchanged

- Engine `layout.keel_file(root, logical) -> Path` and api `Layout.keelFile(root, logical)`: `logical` is the path as
  keel names it today (`.keel/specs/x.md`, `.keel/config.yml` …). In `legacy`/`export` it is `<root>/<logical>` (as
  today); in `store` it maps `.keel/<docs part>` to `<store>/docs/…` and the rest to `<store>/private/…`.
- So the web app, MCP, citations and the api's routes keep sending the same `.keel/...` strings; only the place they
  are read from changes. The front ends need no path changes where they go through the api.
- The resolver rejects `..`, absolute paths and symlinks that leave the store (real path check), like `layout._sub`
  does for config paths today.
- Agents get absolute paths (they work on the disk). The prompt builders (`runtime/prompts.py`, `session_steps.py`,
  content agents) pass `keel_file(...)` results instead of literal `.keel/...` strings.
- Store files are never committed: spec / knowledge / hunt commits are recorded in doc history, as the hidden layouts
  do today (`Layout.hidden` is true for `store`).
- A new layout name is not needed: `store` mode uses the `keel` layout's tables with the resolver in front.

### Flow worktrees (must be host paths)

- Tests in a flow worktree may ask the host Docker for bind mounts (Testcontainers, compose), so a worktree must be
  a real path on the person's computer. The data volume is not.
- `store` mode puts worktrees in a host folder keel owns, outside the project: native
  `~/.keel2/<KEEL_NAME>/worktrees/<project-id>/<name>`; container: the same host path, mounted by `keel2 start` at the
  same path (like the project folder in `--docker` mode). This folder is not under `KEEL_DATA`, so the sandbox does not
  hide it.
- `runtime/container.py worktree_fit` checks the mounts against this folder; a compose service that mounts only the
  project still gets `ok: False` (same as today).
- `agent_sandbox._project` stops cutting at `/.keel/worktrees/`: for a path in keel's worktrees folder it keeps the
  worktree and the project's git common dir open (`git rev-parse --git-common-dir`), since the worktree's `.git` file
  points there.
- `seed_keel_files` / `keep_keel_files` are not used in `store` mode: every worktree of a project reads the same store.
- All hard-coded `.keel/worktrees` places move to one helper `worktrees.base(root)`: `tools/worktrees.py`,
  `rules/__init__.py` (`is_worktree`, ~l.957), `runtime/knowledge.py` (~l.42), `runtime/bug.py` (~l.347),
  `runtime/container.py` (~l.311), `tools/agent_sandbox.py`, and the api/web places found by grep in step 1.

### Sandbox and rule guard (all three backends)

- bwrap: `--tmpfs <KEEL_DATA>` stays; then `--bind <store>/docs <store>/docs` (read-write). `private/` stays hidden.
- macOS sandbox-exec: the deny on `KEEL_DATA` stays; add an allow for `(subpath "<store>/docs")` after it (later,
  more specific rules win in SBPL).
- container (`agent_user.py`): `/data` stays 0710; `/data/projects` and `/data/projects/<id>` 0710 (traverse only),
  `docs/` group read-write, `private/` 0700 owned by keel's user.
- Rule guard: `RULE_PATH`, `RULE_FILES` and `_IN_KEEL_DIR` also match `<store>/private/` (the real path), so an agent
  that finds the path still cannot write rules or config.
- Criterion 3 is tested per backend (unit tests of the argv / profile / permissions, plus one run each where CI can).

### Migration and export

- `legacy` project, first start of the new version: one Inbox item "Move keel's files out of this project?".
  - No → remembered; the item does not come back; the same action stays in project settings.
  - Yes → copy `.keel/*` into the store (docs/private split), switch mode to `store`, keep the old `.keel` untouched.
    Then a second Inbox item: "Delete the old .keel folder?"; only a yes deletes it. Exclude lines keel added are
    removed only with that same yes.
  - `external` layout (`.keel` is a link to `~/.keel/projects/<id>/`): copy from the link's target; the second OK
    removes only the link, never the target.
  - Flows that run during the copy: the copy waits until no flow of the project is running.
- `export`: project settings switch. On: writes `.keel/config.yml` and `.keel/rules/` into the repo (not hidden from
  git) and reads them from there; off: copies them back into the store and asks before deleting them from the repo.

### Database or files

- The store keeps keel's files as files (in keel's own data folder), not as rows, so agents and every reader keep
  working with files. Milad asked for "its own database"; this needs his OK before the build (open question 2).

## Acceptance criteria

1. Clean project test (e2e journey `no-keel-files`, fake model): a new git project with one commit; snapshot the
   file list of the working tree (without `.git`), and copies of `.git/info/exclude` and `.git/config`. Register the
   project, run `init`, then the `quick` flow to done. After it:
   - working tree: the only new or changed paths are the files in the flow's commits
     (`git diff --name-only <start>..<flow head>`) plus paths ignored by the project's own `.gitignore` (test/build
     output);
   - `.git/info/exclude` and `.git/config` unchanged (only `worktree`/branch sections git itself adds are allowed);
   - no `.keel` file or link anywhere in the project.
2. The project's config, rules, specs and knowledge are in its store; the web app, MCP and the agents read and change
   them there.
3. In each sandbox backend an agent can read and write `<store>/docs/` and cannot read `<store>/private/` or the rest
   of `KEEL_DATA`; the rule guard blocks writes to the store's rules and config.
4. A flow in a worktree runs its tests from keel's host worktrees folder in native, plain container and `--docker`
   modes; `worktree_fit` gives the same answers as today for the same compose files.
5. A `legacy` project starts and runs a flow exactly as before. Migration: no → nothing changes and no new Inbox item
   on the next start; yes → copied, mode `store`, old `.keel` kept until the second yes.
6. Moving the project between plain and `--docker` mode keeps its store (same id).
7. With `export` on, config and rules are in the repo's `.keel/` and are read from there.
8. All CI suites stay green.

## Phases (each a PR; the default flips only in the last one)

1. **Resolver + storage mode (no behaviour change).** api: storage mode column, store path, sent to the engine.
   engine + api: `keel_file` / `keelFile`, `worktrees.base`, tests. Default stays `legacy` for everyone.
2. **Engine callers.** Every engine `.keel` place through the resolver (split into two PRs if it is too big to
   review: runtime/, then the rest). Still no change for users.
3. **api, sandbox, worktrees, front ends, content.** api callers, sandbox backends, rule guard, host worktrees folder
   + `keel2` mount, VS Code / JetBrains places that read `.keel` from disk go through the api, agents' instructions.
   `store` mode works end to end but is opt-in (hidden setting) for testing.
4. **Flip + migration + export + docs.** `store` becomes the default for new projects, Inbox migration, export
   switch, README/GUIDE, e2e journeys for criteria 1, 5 and 7. Release.

Phases 1–3 are released as normal versions (no user-visible change); phase 4 is the release that changes the default.

## Test plan

- Unit: resolver (each mode, `..`/absolute/symlink escapes), storage mode set once (a later `mkdir .keel` does not
  change it), `worktrees.base`, `_project` for the new worktree folder, sandbox argv/profile per backend, rule guard
  on store paths, migration (no remembered, yes copies, delete only after second yes, external link target kept).
- api: storage mode on register / moved / parked projects; id kept across plain ↔ `--docker`.
- e2e: `no-keel-files` (criterion 1), `legacy-keel` (criterion 5), `export` (criterion 7); worktree flow in native
  and container modes (criterion 4).
- Full CI suites per phase.

## Open questions for Milad

1. Feature freeze (2026-11-18): this changes the default. #246 asks for it; please confirm it goes ahead now.
2. "Own database": the store keeps files in keel's own data folder, not database rows (agents need files). OK?
3. Four phases (1–3 without user-visible change, 4 flips the default) instead of one big PR. OK?
