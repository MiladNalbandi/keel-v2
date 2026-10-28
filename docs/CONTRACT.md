# keel v2 — build contract

The single source of truth for how the three parts fit. `docs/mockup.html` is the UI reference
(look, screens, wording). If code and this file disagree, fix one of them in the same change.

## Shape

```
 one Docker image  ─  docker run -p 127.0.0.1:8080:8080 -v /path/to/project:/workspace -v keel-data:/data keel-v2
 ┌──────────────────────────────────────────────────────────────────────────────────────────┐
 │ tini → /usr/local/bin/keel-start (bash): starts engine, then api; exits if either dies     │
 │                                                                                            │
 │  api   (Kotlin Spring Boot 3, JDK 21)  0.0.0.0:8080   /api/** + serves web/dist at /        │
 │        SQLite  /data/keel.db  (Flyway)                                                     │
 │  engine (Python 3.12, FastAPI, LangGraph) 127.0.0.1:8090  (only the api talks to it)        │
 │        SQLite  /data/checkpoints.db  (langgraph-checkpoint-sqlite)                          │
 │  keel v2's content at /opt/keel-v2/content (agents, skills, stacks, packs, templates)       │
 │  CLIs (optional, build arg INSTALL_CLIS=1): claude, codex, copilot, opencode                │
 └──────────────────────────────────────────────────────────────────────────────────────────┘
 /workspace  = a mounted project (a git repo) or a folder of repos   → registered at start
 /data       = all state (db, checkpoints, master.key for secrets, notification inbox)
```

Local development without Docker: engine `cd engine && uv run keel-engine` (port 8090),
api `cd api && ./gradlew bootRun` (port 8080, `KEEL_ENGINE_URL=http://127.0.0.1:8090`),
web `cd web && npm run dev` (port 5173, proxies `/api` to 8080).

## Environment variables

| Var | Default | Used by |
|---|---|---|
| `KEEL_DATA` | `/data` (dev: `./.data`) | api, engine |
| `KEEL_WORKSPACE` | `/workspace` (dev: unset) | api — scan for projects at start |
| `KEEL_CONTENT` | `/opt/keel-v2/content` (dev: `<repo>/content`) | api, engine — keel v2's agents/, skills/, stacks/, packs/, templates/ |
| `KEEL_V1_OPTIONAL` | `/opt/keel-v1-optional` | api — where `keel2 start --with-keel-v1` mounts a keel v1 checkout (its MCP server only) |
| `KEEL_ENGINE_URL` | `http://127.0.0.1:8090` | api |
| `KEEL_API_URL` | `http://127.0.0.1:8080` | engine — where it POSTs events |
| `KEEL_INTERNAL_TOKEN` | random at start, shared by both | header `X-Keel-Token` on engine↔api calls; v0.18.1 also keel2's and the e2e suites' api calls (the api refuses `/internal/events` when it is empty) |
| `KEEL_PUBLIC_URL` | — | api (v0.18.1) — the dashboard as people open it: its host and origin count as keel's own (sign-in) |
| `KEEL_ALLOWED_HOSTS` | — | api (v0.18.1) — more host names keel answers on (comma-separated), besides localhost |
| `KEEL_DEV_ORIGINS` | — | api (v0.18.1) — more origins keel's own page may come from (comma-separated) |
| `KEEL_FAKE` | `0` | engine — `1` forces the fake model everywhere (tests, demo) |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GITHUB_TOKEN` | — | optional; Connections can store keys instead |

Removed in 0.4.1 (keel v2 does not contain, run or proxy keel v1): `KEEL_HOME`, `KEEL_DASHBOARD_PORT`,
`KEEL_DASHBOARD_AUTOSTART` and the image's build args `KEEL_REPO` / `KEEL_REF`. They are ignored when set;
`keel2 update` warns about a `KEEL_HOME` left in the environment. `scripts/no-v1.sh --strict` (CI) fails on any
line that depends on keel v1 again.

## Engine API (FastAPI, 127.0.0.1:8090) — called only by the api

All JSON. Errors: `{ "error": string, "hint"?: string }` with 4xx/5xx.

```
GET  /health                                  → { ok: true, version, fake: bool }
GET  /templates                               → Workflow[]        built-in keel flows: feature, change, quick, small, implement, fix, …
POST /workflows/validate   { yaml }           → { ok, errors: string[], workflow?: Workflow }
POST /workflows/estimate   { yaml, acs, history?: StepHistory[] } → Estimate
POST /threads              StartThread        → { thread_id }
GET  /threads/{id}                            → ThreadState
POST /threads/{id}/resume  { decision: "approve"|"reject", why?: string, payload?: object } → ThreadState
POST /threads/{id}/stop                       → ThreadState
GET  /threads/{id}/history                    → Checkpoint[]      newest first
POST /threads/{id}/rewind  { checkpoint_id }  → ThreadState       continues from that checkpoint (new branch)
GET  /threads/{id}/unlocks                    → Unlock[]          { path, phase, by, reason?, at? }
POST /threads/{id}/unlocks { path, phase?, reason? } → Unlock[]   phase defaults to the thread's phase now
POST /mcp/tools            McpServerSpec      → { ok, tools: {name, description}[], error? }   (tools/list)
POST /providers/test       { provider, mode, model, key? } → { ok, text?, ms, error? }        ("Reply with exactly: OK")
POST /providers/usage      { provider: "claude"|"codex"|"copilot", key? } → { ok, provider, source, at, windows: UsageWindow[], plan?, error? }
                           // codex: `codex app-server` account/rateLimits/read; copilot: GitHub copilot_internal/user (unofficial);
                           // claude: the windows of its last run (rate_limit_event). UsageWindow = { window, label, used_pct 0..1, resets_at unix s, status?, used?, cap?, remaining? }
GET  /providers/models                        → { [provider]: {id, label}[] }
```

Engine → api events: `POST {KEEL_API_URL}/internal/events` header `X-Keel-Token`, body `EngineEvent[]`.
The engine buffers and retries; the api stores them and fans out over SSE.

## Shared types

```ts
type Provider = "fake" | "claude" | "codex" | "copilot";
type Mode = "subscription" | "api" | "opencode";        // copilot: "subscription" = Copilot CLI, "opencode" = OpenCode, "api" = GitHub Models key
type Model = { provider: Provider; mode: Mode; model: string; effort?: string };

type StepKind = "agent" | "code" | "gate" | "branch" | "parallel";
type Step = {
  id: string; kind: StepKind; name: string;
  agent?: string;              // keel agent id (agents/*.md) or custom agent id
  model?: string;              // key into the model table, or "default"
  phase?: string;              // the phase this step belongs to (for the guard): spec, red, green, gate, ...
  action?: string;             // code steps: "verify_red" | "verify_green" | "commit" | "push_check" | "write_config" | "ladder" | "memory_check" | "run:<cmd>"
  per_ac?: boolean;            // inside the "for each AC" loop
  parallel?: number;           // parallel copies (kind "parallel")
  lanes?: { name: string; sub?: string; kind: "agent"|"code" }[];   // two different things at the same time
  back?: string;               // gate: where "send back" goes (step id)
  no?: string;                 // branch: where "no" goes; "yes" = next step
  lock?: boolean;              // keel rule: cannot be removed while keel_rules is on
  max_tokens?: number; on_limit?: "pause" | "cheaper" | "stop";
  tools?: string[];            // MCP tools allowed, e.g. "mcp:keel:keel_next"
  // also actions: verify_fast | verify_module | verify_release | verify_coverage | verify_deps | audit | trace | trace_strict
  //   | arch | pr | open_pr (verdict actions write the engine `verdicts` table) | start_flow (hand-off, below)
  from?: string;               // parallel: one agent per item of the state list state.data[from] (or acs); results in data["<id>_results"]
  cap?: number;                // at most this many items
  batch?: number;              // at most this many agents of the step at a time
  for_each?: string;           // first step of a loop over state.data[for_each] (dicts with an id); item status todo|done|skipped|failed
  per_item?: boolean;          // inside that loop (follows the for_each step directly)
  markers?: string[];          // NAME: value lines read from the agent's answer into state.markers[<id>] (REPRO, ROOT-CAUSE, ...)
  collect?: string;            // the JSON list in the agent's answer (```json) becomes state.data[collect]
  when?: { marker: string; equals?: string; in?: string[]; step?: string | string[]; any?: boolean };   // branch on a marker
                               // (also data: <path> or state: <path>; on agent/parallel/code steps: run only when it holds)
  flow?: string; seed?: Record<string, unknown>;   // start_flow: workflow id, seed ("$state.path" reads state)
  then?: "end" | "continue" | string;              // code: after the actions end the flow, go on, or jump to that step id
  // v0.4.0 cover + ship (section "v0.4.0 additions: cover, ship and include"):
  soft?: boolean;              // code: a failing check goes on; markers[<id>].RESULT = pass | fail
  retry_only?: boolean;        // agent/code: runs only when work was sent back to it (a failed check, a gate's send-back)
  rounds?: number;             // code: fix attempts before asking; branch: the loop it closes runs at most N times; review: fix rounds
  redo?: string;               // review: where the flow goes on after a fix (default the review)
  skippable?: "deferred" | "optional"; group?: string;   // the skip menu may turn it off; a group is skipped as one
  skip_menu?: boolean; report?: "verdicts";             // gates: the opening skip menu; the final review's verdict table
  choices?: string[] | Record<string, string>; on_skip?: { choice?: string; record?: string };   // list in a for_each loop: one choice per item; list outside a loop: named exits as markers CHOICE/WHY; map: named exits {name: step id | "end"}
  // v0.4.0 review, diagnose, fix, change (section "v0.4.0 additions: review, diagnose, fix and change"):
  instructions?: string;       // agent: extra task text; "{{data.report}}" is replaced by that state value
  after_rounds?: string;       // code: where failures go once `rounds` are used up, instead of asking
  recommend?: string;          // gate with list choices: plain approve takes this marker's value when it names a choice (change: SIZE)
};
// kind "include" (+ flow): replaced by the steps of that workflow when the engine loads it (ids prefixed "<id>_").
type Workflow = { id: string; name: string; based_on?: string; keel_rules: boolean; version: number; steps: Step[]; yaml: string };

// Workflow YAML (what users edit, export, import)
// name: Hotfix
// based_on: keel/fix
// keel_rules: true
// budget: { max_tokens: 150000, on_limit: pause }
// steps:
//   - { id: repro, kind: agent, name: bug-repro, agent: reproducer, model: default, phase: bug-repro }
//   - { id: gr, kind: gate, name: gate R, back: repro, lock: true, phase: gate-r }

type StartThread = {
  project_id: string; root: string;            // absolute path of the repo inside the container
  workflow: Workflow; title: string;
  acs?: { id: string; layer: "API"|"WEB"; title: string; status?: string }[];   // optional; otherwise the spec step writes them (done/already-met stay finished)
  models: Record<string, Model>;               // agent id → model ("default" key = fallback)
  settings: { gates_mode: "every-ac"|"end-of-lane"|"end"; cap_tokens: number; on_cap: "pause"|"cheaper"|"stop"; cheaper_model?: Model;
              cap_usd?: number; on_cap_usd?: "pause"|"cheaper"|"stop"; step_cap_tokens?: number; step_on_cap?: "pause"|"cheaper"|"stop" };   // v0.4.2 project caps (below)
  mcp: McpServerSpec[];                        // servers this flow may use; per-agent allowlist inside Step.tools
  skills: Record<string, string>;              // agent id → concatenated SKILL.md text to add to its prompt
  agents?: Record<string, { knowledge: Knowledge }>;   // v0.4: what each agent uses (missing → its front matter default)
  other_projects?: { id: string; name: string; root: string }[];   // 0.18.64 every other registered project: read only for the agents
};
type ThreadState = {
  thread_id: string; project_id: string; workflow_id: string; title: string;
  status: "running" | "waiting" | "done" | "failed" | "stopped";
  current: string | null;                      // step id
  phase: string;                               // phase name (keel v1's names; nothing is mirrored into the project)
  ac: string | null;
  acs: { id: string; layer: string; title: string; status: "todo"|"red"|"green"|"done" }[];
  waiting?: { step: string; kind: "gate"|"budget"|"fix"; title: string; detail: string; options: ("approve"|"reject")[] };
  usage: { tokens_in: number; tokens_out: number; cost_usd: number; premium_requests: number; cap_tokens: number; cap_usd: number };  // cap_usd 0 = none (v0.4.2)
  checkpoints: number; error?: string; updated_at: string;
};
type Checkpoint = { id: string; n: number; step: string; at: string; note: string };
type Estimate = { tokens: number; low: number; high: number; cost_usd: number; premium_requests: number;
                  by_provider: Record<Provider, number>; per_step: { step: string; tokens: number }[] };
type StepHistory = { agent: string; tokens_in: number; tokens_out: number; retries: number };
type McpServerSpec = { name: string; command: string; args: string[]; env?: Record<string,string>; cwd?: string };

type EngineEvent = {
  type: "thread.started" | "step.started" | "step.finished" | "agent.started" | "agent.step" | "agent.finished"
      | "gate.waiting" | "gate.decided" | "budget.warn" | "budget.stop" | "guard.refused" | "thread.done" | "thread.failed"
      | "provider.usage";                      // data: { provider, windows: UsageWindow[], source, at } after a run that reported its plan windows
  thread_id: string; project_id: string; step?: string; at: string;
  call_id?: string;                            // agent.* events: one id per agent call
  data: Record<string, unknown>;
  // agent.started  data: { agent, provider, model, mode, phase, ac }
  // agent.step     data: { n, kind: "text"|"thinking"|"tool"|"write"|"edit"|"result"|"answer"|"guard"|"error", text, tool?, server?, path?, diff?, ms?, ok? }
  // agent.finished data: { status: "done"|"failed"|"stopped", tokens_in, tokens_out, cost_usd, premium_requests, result? }
  // gate.waiting   data: { kind, title, detail }
};
```

## The api (Spring Boot, /api) — called by the web

JSON, errors `{ error, hint? }`. All project routes take `{pid}` (project id = slug of the folder name).

```
GET    /api/health                                   → { ok, engine: bool, version, fake }   (version: keel v2's own, from the api's build-info; 0.4.1 dropped `keel: { version, home }`, which was keel v1's)
GET    /api/events?project={pid}                     SSE: event: <EngineEvent.type | "notification" | "project.changed">, data: JSON

# projects
GET    /api/projects                                 → Project[]
POST   /api/projects            { root, name? }      → Project            (registers a path inside the container)
GET    /api/projects/{pid}                           → Project
type Project = { id, name, root, branch, flow: string|null, phase: string, acs: [done,total], waiting: number, running: number }
                                                       (flow, phase, acs: the running or waiting flow's; none → null, "none", [0,0])

# flow (Run)
GET    /api/projects/{pid}/flow                      → { thread: ThreadState|null, workflow: Workflow|null }   (0.4.1: no keel_state)
POST   /api/projects/{pid}/flows  { workflow_id, title, acs? }  → ThreadState
POST   /api/threads/{tid}/resume  { decision, why? }  → ThreadState
POST   /api/threads/{tid}/stop                        → ThreadState
GET    /api/threads/{tid}/history                     → Checkpoint[]
POST   /api/threads/{tid}/rewind  { checkpoint_id }   → ThreadState
GET    /api/projects/{pid}/estimate?workflow_id=&acs=3 → Estimate     (uses this project's job history)

# jobs / live (Run)
GET    /api/jobs?project=&status=running|done|failed|finished&agent=&provider=&q=&limit=50 → Job[]
GET    /api/jobs/count?project=&status=&agent=&provider=&q=  → { count }   (v0.15.2: same filters, no limit)
       status finished = every call that does not run any more (done also means stopped). q = search words, each
       one must be in the id, agent, provider (or its web name, e.g. "GPT / Codex"), model, step, phase, AC, status,
       project id or project name; case does not matter.
GET    /api/jobs/{id}                                 → Job & { steps: JobStep[] }
GET    /api/jobs/{id}/steps?after=n                   → { steps: JobStep[], running: bool }
POST   /api/jobs/{id}/stop
type Job = { id, project_id, thread_id, agent, provider, model, step, phase, ac, status, started_at, ended_at, tokens_in, tokens_out, cost_usd, premium_requests, steps_count, mcp_calls }
type JobStep = { n, at, kind, text, tool?, server?, path?, diff?, ms?, ok? }

# repo (Project)
# since step 3 the Code plugin's (plugins/code: api keel.api.repo.RepoController and RepoSearch in keel-plugin-code.jar,
# web plugins/code/web); RepoService (the repo's info, tree, files and diffs) stays core: Git and Code Review read the repo with it
GET    /api/projects/{pid}/repo                       → { branch, base, ahead, behind, remote, worktrees: {branch,path}[], branches: {name, note}[] }
GET    /api/projects/{pid}/repo/tree?depth=4&dir=     → TreeNode[]   { path, name, depth, kind: "dir"|"file", mark?: "A"|"M"|"D", keel: bool, frozen: bool, ac?: string }
                                                        (dir: lazy loading — that folder's subtree, depth levels down; depth 1 = the root's children)
GET    /api/projects/{pid}/repo/file?path=            → { path, size, mark?, frozen, keel, ac?, head: string (first 120 lines), last_commit: Commit,
                                                          binary, modified (epoch ms), phase, bucket, verdict }   (ac: newest AC-nnn in this branch's commits of the file;
                                                          bucket/verdict: the keel rule for the active phase, verdict "deny" = frozen)
GET    /api/projects/{pid}/repo/commits?limit=30&range= → Commit[]  { sha, message, author, at, keel: bool }   (range=branch: base..HEAD; keel: by KeelBot / the configured keel author, or Co-Authored-By KeelBot)
# v0.5.1 the Repo page as a small read-only IDE (nothing here writes to the project)
GET    /api/projects/{pid}/repo/raw?path=             → the file's bytes (text/plain;charset=UTF-8, image/*, or octet-stream); 413 above 10 MB;
                                                        CSP sandbox + nosniff, so an SVG/HTML file never runs as keel's page; .git/, secrets, outside paths refused
GET    /api/projects/{pid}/repo/files                 → { files: string[], truncated }   (quick open: git ls-files tracked + untracked-not-ignored, no deleted/secret files; ≤ 50,000; cached 5 s)
GET    /api/projects/{pid}/repo/search?q=&regex=&case=&word=&include=&exclude=&max=500
                                                      → { results: { path, matches: { line, column, length, text, ranges: [start,end][] }[] }[], matches, files, truncated, timed_out, took_ms }
                                                        (git grep --untracked -I: .gitignore respected, binary and secret files skipped; regex = PCRE when git has it, else ERE;
                                                         include/exclude: comma-separated globs, `*.kt` anywhere, `src/` a folder; `..`/absolute → 403; bad regex → 400; max ≤ 5000; 10 s timeout)
GET    /api/projects/{pid}/repo/changes               → { path, from?, staged?: "A"|"M"|"D"|"R"|…, unstaged?: "M"|"D", untracked?: bool, conflict?: bool }[]   (git status -z)
GET    /api/projects/{pid}/repo/diff?path=&against=head|base&sha=
                                                      → { path, against, ref, diff (unified), binary, truncated }   (head: work tree vs HEAD; base: work tree vs the merge-base
                                                         with main/master; sha: that commit's change; an untracked file diffs against nothing)
GET    /api/projects/{pid}/repo/commit?sha=           → { sha, message, body, author, at, keel, files: { path, status: "A"|"M"|"D"|"R", from? }[] }
GET    /api/projects/{pid}/repo/file-at?path=&ref=&from= → RefFile { path, ref, sha, kind: "branch"|"remote"|"review"|"commit"|"ref",
                                                         branch? (what Check out switches to), current (ref = the checked-out branch),
                                                         checked_out?, worktree?: { branch, path }, source: "ref"|"worktree", size, binary,
                                                         text?, dirty (uncommitted changes in the project folder) }
                                                         (0.18.57, plugins/code RepoRefs: `git cat-file` of <commit>:<path>, nothing checked
                                                         out; ref: letters, digits, `._/+-` only, then `git rev-parse --verify`; 400 for an
                                                         option, a range, `@{…}`, `~`, `^` or `:`; the path: no `..`, no .git, no secret file
                                                         (403); from=worktree reads the worktree that has the branch out, uncommitted too)
GET    /api/projects/{pid}/repo/find?path=            → { path, in_work_tree, checked_out?, refs: { ref, label, kind, sha, at }[] }
                                                         (0.18.57: the local branches, refs/keel/… review refs and remote branches that have
                                                         the file, newest first, one per commit, at most 8; never the checked-out branch)
# web (pages/Repo.tsx + pages/repo/; since step 3 plugins/code/web): an activity bar (Explorer, Search, Source control, keel), editor tabs (single click = preview
# tab replaced by the next one, double click = pinned), a status bar. Deep links: #/repo/<path> opens a file, #/repo/<path>:<line>
# opens it at that line and highlights it (the Map links a table to its migration this way); the URL follows the active tab.
# 0.18.57 #/repo/<path>:<line>?ref=<ref> opens the file as that branch, review ref (refs/keel/review-pr/7) or commit has it,
# read-only, under "Showing <ref> — not checked out" with Check out this branch (asks; refused while the folder has
# uncommitted changes) and Open in the (side session's) worktree; the checked-out branch opens the work tree's file.
# A file the work tree does not have offers "Found on <branch> — open it" (GET …/repo/find). The SDK's codeHref(path,
# line, ref) writes the link. Who sets the ref: KeelBot answers (message data.source { ref, sha, worktree?, paths?: {path:
# ref} } — the folder's branch, a side session's worktree, files read with `git show <ref>:<path>`), flows (thread
# state source_ref: a reviewed PR's head ref, a worktree flow's branch; the Flow page's file:line chips link there),
# the VS Code extension (a KeelBot link with a ref opens a read-only keel-ref: document from file-at). The JetBrains
# plugin still opens links by path in the local folder (it says "not in this project folder" for another branch's file).
# Keys: Ctrl/⌘+P quick open (":12" = line), +Shift+F search, +Shift+E explorer, +Shift+G source control, +F find in file,
# +G go to line, Alt+Z word wrap. Text files up to 2 MB are shown; long files render only the rows on screen.
GET    /api/projects/{pid}/keel-docs                  → { path, what, by, updated, status: "ok"|"live"|"check" }[]
GET    /api/projects/{pid}/memory                     → { facts: Fact[], knowledge: { id, status: "written"|"stale"|"missing", words, cites }[] }
POST   /api/projects/{pid}/memory  { title, text, kind }      PUT /api/projects/{pid}/memory/{fid}   DELETE /api/projects/{pid}/memory/{fid}
type Fact = { id, title, text, kind: "fact"|"rule"|"flaky"|"unlock", source, at }

# map + wiki (Project)
GET    /api/projects/{pid}/map                        → the map the engine built (keel v1's map shape) or { missing: string }
POST   /api/projects/{pid}/map/rebuild                → the engine builds the map for HEAD, returns it
GET    /api/projects/{pid}/wiki                       → { sections: {id, title, items: {id, title, status?}[]}[] }   (knowledge, workflows, runbook, decisions)
GET    /api/projects/{pid}/wiki/page?id=kb:architecture|wf:<id>|runbook|adr:<file> → { id, title, markdown, meta }

# workflows (Build)
GET    /api/projects/{pid}/workflows                  → Workflow[]   (templates + this project's + installed)
POST   /api/projects/{pid}/workflows  { name, from: "template:<id>"|"blank"|"library:<id>", keel_rules } → Workflow
GET    /api/workflows/{wid}                           → Workflow
PUT    /api/workflows/{wid}           Workflow        → Workflow (version+1; validated by the engine)
DELETE /api/workflows/{wid}
GET    /api/workflows/{wid}/export                    → text/yaml, Content-Disposition: attachment; filename=<id>.workflow.yaml
POST   /api/projects/{pid}/workflows/import  { yaml } | { url }  → { workflow, review: InstallReview }
GET    /api/library                                   → LibraryItem[]
POST   /api/projects/{pid}/library/{id}/install  { scope: "project"|"all" } → Workflow
type LibraryItem = { id, name, source, version, about, steps, gates, est_tokens, agents: string[], mcp: string[], edits_files: bool, installed: bool }

# agents, skills, stacks, tools (Build)
GET    /api/projects/{pid}/agents                     → Agent[]
PUT    /api/projects/{pid}/agents/{aid}   { model?, tools?, skills?, prompt?, enabled? }   (project override)
POST   /api/projects/{pid}/agents         CustomAgent  → Agent
DELETE /api/projects/{pid}/agents/{aid}               (custom only)
POST   /api/agents/{aid}/test             { pid }      → { ok, text, ms, error? }
type Agent = { id, label, about, custom: bool, phases: string[], model: Model, tools: string[], skills: string[], prompt: string, enabled: bool, overridden: string[] }
GET    /api/projects/{pid}/skills                     → Skill[]
GET    /api/skills/{sid}                              → Skill & { body: string, refs: {path, tokens}[] }
POST   /api/projects/{pid}/skills         { name, kind, stack, body } → Skill
PUT    /api/projects/{pid}/skills/{sid}   { agents?, when?, body? }
GET    /api/projects/{pid}/skills/repo                → { on, found_at, docs: {path, title}[], skills: {id, name, path, about, use: "tests"|"implementation"|null, why, agents}[], attached: string[] }
PUT    /api/projects/{pid}/skills/repo    { on }      → the same   (0.18.57 the repo's own skills, from .keel/project-rules.json
                                                        written by init / knowledge-refresh; source "repo", read live from the
                                                        repo; on = the project's setting repo_skills; a repo skill's body is
                                                        changed and the skill removed in the repo: 409 here)
type Skill = { id, kind, source: "keel"|"keel pack"|"claude"|"yours", stack, version, tokens, agents: string[], when: string, enabled: bool }
GET    /api/projects/{pid}/stacks                     → Stack[]   { name, lane, source, detected, detect, layers, commands: {name, cmd}[], tools: {name, on, fail}[], skills }
GET    /api/mcp-servers                               → McpServer[]
POST   /api/mcp-servers  McpServerSpec   PUT /api/mcp-servers/{name}   DELETE /api/mcp-servers/{name}
POST   /api/mcp-servers/{name}/test                   → { ok, tools, error? }
GET    /api/projects/{pid}/mcp-allow                  → { [agent]: string[] }     PUT same shape
type McpServer = McpServerSpec & { enabled, builtin, status: "ok"|"off"|"error", tools: string[] }

# control
GET    /api/projects/{pid}/budget                     → { month: { tokens, cost_usd, premium_requests, flows }, days: { day, claude, codex, copilot, fake }[], caps: Cap[], top: { agent, provider, tokens, cost_usd }[], recent: { title, estimate, real, status }[],
                                                    projects: { project_id, name, tokens, cost_usd, api_cost_usd, calls }[], all: Spend }   // 0.18.55 every project this month (keel Product's own runs too), all = their sum
GET    /api/projects/{pid}/budget/now                 → BudgetNow (v0.5.2, the budget bar; see "v0.5.2: the budget bar")
GET    /api/projects/{pid}/graph                      → GraphOverview (v0.5.3, the code graph; see "v0.5.3: the Graph page")
GET    /api/limits                                    → Limit[]      PUT /api/limits  Limit[]
type Limit = { id, name, unit, used, cap, note, source?, window?, used_pct?, remaining?, resets_at?, fetched_at? }   // manual cap + the provider's numbers when known
GET    /api/usage/providers                           → ProviderUsage[]   // only providers that are set up; codex/copilot cached 60 s
POST   /api/usage/providers/{id}/refresh              → ProviderUsage     // codex/copilot: read now; claude: one tiny Haiku call
type ProviderUsage = { id, name, kind: "subscription"|"api", source, fetched_at?, windows: { window, label, used_pct?, used?, cap?, remaining?, resets_at?, status? }[], live, can_refresh, error?, note? }
GET    /api/settings/general                          → Settings
PUT    /api/settings/general        Partial<Settings>
GET    /api/projects/{pid}/settings                   → { general: Settings, overrides: Partial<Settings>, effective: Settings, general_saved: string[] }   (general_saved: the General keys someone saved, B15)
PUT    /api/projects/{pid}/settings  { [key]: value | null }    (null = use general)
type Settings = { gates_mode, keel_rules, fix_attempts, coverage_min, default_model: Model, implementer_model: Model, reviewer_model: Model, cheaper_model: Model,
                  cap_tokens, on_cap, branch_pattern, web_lane_worktree, push_pr, notify: "all"|"needs_you"|"none", env_names: string[], mcp: string[],
                  repo_skills: bool }
GET    /api/connections                               → { providers: { id, label, modes: { id, label, ready: bool, detail, need?: "cli"|"login" }[], selected, key_set: bool, key_hint?: string }[], machine: { name, ok, version? }[] }
                                                        // v0.4.2: the 8 program checks (node git java docker claude codex copilot opencode) run in parallel, 5 s each, cached 1 min
PUT    /api/connections/{provider}  { mode }
PUT    /api/secrets/{name}   { value }   → { hint }   (stored AES-GCM in /data; never returned. 400 when a known login or key cannot be right: bad characters, a Claude token without sk-ant-, a Codex login that is not JSON)
       (B8a a model login or key — Claude or Codex login, ANTHROPIC_API_KEY, OPENAI_API_KEY — and a finished POST /api/logins make that provider's default model the default, implementer and reviewer model when General has none saved for them; never a GitHub token, never with local_only on)
DELETE /api/secrets/{name}
POST   /api/connections/{provider}/test               → { ok, text?, ms, error? }

# notifications
GET    /api/notifications?limit=50                    → Notification[]   { id, type: "review"|"failed"|"budget"|"finished"|"started", project_id, title, body, link, at, read, summary }
       summary (0.18.55): at most 3 short lines, one fact each with a mark (✓ ⤼ ✗ ◆), made from structured data (an older
       row: []); the toast and the bell list show it, body keeps the full text. Titles say what to do ("◆ shop: approve
       the spec?", "◆ shop is set up — check it", "✓ Add CSV export is done", "✗ Fix the totals stopped: <reason>"),
       never a step id. The engine's gate.waiting adds { flow, step_name, phase, ac, summary } for them.
POST   /api/notifications/read-all      POST /api/notifications/{id}/read
GET    /api/notification-settings     PUT same       { sound, volume, tone, popup, desktop, scope, kinds: {review, failed, budget, finished, started}, quiet }

# internal (engine only, X-Keel-Token)
POST   /internal/events   EngineEvent[]
```

## Rules the code must keep

- **keel rules** (`engine/keel_engine/rules/`, tables in `rules/data/keel_rules.json`; the api reads a byte-identical copy in
  `api/src/main/resources/keel/keel_rules.json`): tested against keel v2's golden file `engine/tests/fixtures/guard_golden.json`
  (PHASES, TRANSITIONS, RAILS, MATRIX, COMMIT_RULES, FLOW_START, red_accept/red_reject, classify cases).
- **Guards**: agent tools `write_file` / `run_command` refuse what `MATRIX[phase]` denies; after every agent step the
  engine diffs the repo (`git status --porcelain`) and reverts files the phase does not allow, emitting `guard.refused`.
- **Gates** are LangGraph `interrupt()`; only `POST /threads/{id}/resume` continues them. Locked steps can only be removed
  from a workflow when `keel_rules` is false (the api refuses otherwise with `{error, hint}`).
- **No mirror** (since v0.4.0): the flow's state lives only in the engine (`/data`); the engine writes no v1-format state
  file or event log into the project. `.keel/config.yml` (project settings) stays.
- **Secrets**: never logged, never returned; subscription mode removes `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GITHUB_TOKEN`… from CLI child env.
- **Fake model** (provider `fake`): deterministic, no network; a full feature flow on the bundled demo repo must reach `done`
  with gates approved through the API. It is the default model until the user picks another in Connections/Settings.
- Writing for people: short plain sentences in UI text (see mockup).

## v0.2 additions (all routes below are new; existing routes keep working)

### Engine
```
POST /workflows/estimate   + optional { models }          (already accepted) — the api always sends models
GET  /providers/models                                    (exists) — the api now proxies it
POST /threads              StartThread + { keys?, settings.fix_attempts?, settings.simulate_checks? }  (exists)
```
- **ThreadState** gains `blockers: { gate: "release"|"coverage"|"deps"|"knowledge"|"secrets", why: string, fix: string }[]`
  (computed by the `push_check` step and refreshed after every commit) and `ladder?: { n, name, cmd, status: "pass"|"fail"|"fixing"|"waiting"|"skipped", detail? }[]`
  (init flow). Neither is written into the project (no mirror since v0.4.0).
- **Unlocks**: `StartThread.settings.unlocks?: { path, phase }[]` and resume `payload.unlock: { path, phase }` add to
  `state.unlocks` (keel v1 semantics: that path bypasses the guard matrix in that phase). Logged as a `gate` event.
- **Commit checks** (keel v1 `keel commit`): staged-diff secret scan (allow line marker `keel:allow-secret`), new
  manifest dependencies need approval (refused with `waiting.kind="fix"` titled "Approve new dependency"), coverage commits
  may only delete production lines, trivial commits may not edit existing tests, change-flow escalation triggers
  (contract / migration / auth paths / size) → `waiting.kind="gate"` "Escalate to a feature flow?".
- **Live guard for the claude CLI** (v0.4.0: keel v2's own hook, see "v0.4.0 additions"). codex/copilot keep the after-step diff guard.
- New built-in template **`knowledge-refresh`**: one librarian per selected stale section (parallel) → `memory_check` → commit.
  Started by the api's wiki refresh.

### Api
```
POST /api/projects/{pid}/repo/update-from-base             → { ok, merged: bool, conflicts: string[], output }   (git merge <base>; aborts on conflict)
GET  /api/projects/{pid}/repo/history?path=                → { sha, message, author, at, keel }[]      (git log --follow -n 30)
POST /api/projects/{pid}/unlock      { path, phase? }       → { unlocks }   (to the active thread: a resume payload when it waits on a fix, else the engine's /threads/{id}/unlocks)
GET  /api/projects/{pid}/stacks                             (exists) + Stack.installable: bool
POST /api/projects/{pid}/stacks      { name, from }         → Stack          (copies the closest keel stack YAML into <root>/.keel/stacks/<name>.yml)
POST /api/projects/{pid}/stacks/{name}/install              → Stack          (copies content/packs/<name> to <root>/.keel/stacks/<name>; a folder already there counts as installed. 0.4.1: no keel v1 binary)
POST /api/projects/{pid}/wiki/refresh  { sections?: string[] } → ThreadState (starts the knowledge-refresh workflow for stale sections)
GET  /api/projects/{pid}/caps        → Cap[]     POST /api/projects/{pid}/caps  Cap    PUT /api/projects/{pid}/caps/{id}  Cap    DELETE /api/projects/{pid}/caps/{id}
type Cap = { id, scope: "day"|"flow"|"step"|"api_month", limit: number, unit: "tokens"|"usd", action: "pause"|"cheaper"|"stop" }
GET  /api/projects/{pid}/caps/left   → { caps: CapLeft[], next_flow: FlowLimits }      (v0.4.2: what each cap leaves now; see "v0.4.2: project caps")
POST /api/projects/{pid}/skills/import { url } | { body }   → Skill   (a SKILL.md from a URL or pasted text; http/https, 256 KB)
GET  /api/providers/models                                  → engine /providers/models
POST /api/projects/{pid}/flows       + { cap_tokens?, on_cap? }          (per-flow cap instead of Settings' for that thread; v0.4.2: the project's caps still apply, the smallest left binds)
POST /api/projects/{pid}/flows       + { options? }                       (v0.4: flow inputs → StartThread.data, e.g. review {lens, base}, fix {no_gates})
POST /api/projects/{pid}/flows       + { force? }                         (0.18.57: start although another flow or side agent works on the same ticket, branch or files; else 409 { error, hint, owners: [{kind: flow|side, id, who, why}] })
POST /api/projects/{pid}/flows       409 for uncommitted files            (0.18.57: + { dirty: [paths], ignorable: [untracked paths] }; tool folders never block)
GET  /api/projects/{pid}/start-check                                      (0.18.57: { worktree: {ok, reason, service}, base: drift line | null }; engine GET /start-check?root=)
POST /api/projects/{pid}/doctor/workspace/exclude { path }               (0.18.57: one untracked path → .git/info/exclude → Applied)
GET  /api/projects/{pid}/doctor/config                                    (0.18.57: { stale: [{key, command, problem, file, line, missing_in}], lines }; engine GET /config-check?root=)
GET  /api/projects/{pid}/keel-state                                       (0.18.64: { project, flows: [StateFlow], v1: V1State | null }; engine GET /keel-state?root=)
POST /api/projects/{pid}/keel-state/close                                 (0.18.64: archive keel v1's .keel/state.json → { archived, flow, was_active }; 404 when none; engine POST /keel-state/close { root })
GET  /api/projects/{pid}/start-check                                      + v1_state: line | null (0.18.64)
POST /api/projects/{pid}/helper/sessions + { force? }                     (0.18.57: a side session on a task someone works on is 409 with owners unless force)
POST /api/tasks/{id}/start           + { force? }                         (0.18.57: as above)
GET  /api/projects/{pid}/estimate    + POST variant { yaml, acs }         (estimate unsaved workflow YAML)
Agent                                + lane: "follow"|"api"|"web"          (PUT accepts it; passed to the engine as Step metadata)
Agent (v0.4)                         + knowledge: Knowledge, knowledge_tokens: int, knowledge_files: {section: tokens}
                                       Knowledge = { sections: ("architecture"|"domain"|"conventions"|"data"|"integrations"|"journeys")[],
                                       code_graph, memory, strict: bool }; default = the `knowledge:` front matter block of
                                       content/agents/<id>.md; PUT { knowledge: {...} | null } overrides / clears it; an
                                       unknown section is 400. Sent to the engine in StartThread.agents; the estimate gets
                                       knowledge_tokens per agent (counted for agents with no history). strict → the guard
                                       refuses reading other docs/knowledge sections ("knowledge section <x> is not given to <agent> (strict)").
GET  /api/events?project=*                                   SSE for all projects (notifications + project.changed); web uses this for the bell
GET  /api/projects/{pid}/flow        + thread.blockers, thread.ladder (from ThreadState)
```
- "Open in editor" stays out (no editor inside a container). "Open in keel v1" (keel v1's dashboard behind a proxy in
  the image) was **removed in 0.4.1**: no `GET /api/keel-dashboard`, and the old proxy path answers 404.

### v0.2 shapes as built
- `POST /api/projects/{pid}/unlock` → `{ unlocks, via: "thread"|"engine", thread_id }`; a thread that waits with `kind:"fix"` gets it as a resume payload, any other running or waiting thread through the engine (`POST /threads/{id}/unlocks`); no flow → 409. Nothing is written into the project.
- `/budget.caps` rows are `BudgetCap = Cap & { name, source: "settings"|"yours" }` (the settings row has `id:"settings"`); the web reads `/caps` for editing.
- `ThreadState` always has `blockers` and `unlocks`; `ladder` for init flows. `Estimate` has `cost_by_provider`.
- Validation failures: 400/422 with `{ error, hint?, errors: string[] }`.
- knowledge-refresh reads sections from `settings.sections`, else from `acs[].id`.
- CLI logins: claude subscription gets `CLAUDE_CODE_OAUTH_TOKEN` (env or `keys.claude_oauth`); the copilot CLI gets `GH_TOKEN`/`COPILOT_GITHUB_TOKEN` (env or `keys.copilot`); every other child has them stripped.

## v0.3 additions

### Model catalog (engine `GET /providers/models`, proxied by the api at `GET /api/providers/models`)
```ts
type Catalog = Record<Provider, {
  label: string;                                     // "Claude", "GPT / Codex", "GitHub Copilot", "Fake"
  modes: Record<Mode, { id: string; label: string; efforts?: string[] }[]>;   // only the modes this provider has
  efforts: string[];                                 // default effort choices for this provider ([] = no effort setting)
  default: { mode: Mode; model: string; effort?: string };
  source: "cli" | "cache" | "builtin";               // where the list came from (cli = asked the installed CLI)
}>;
```
Lists come from the CLIs where possible (codex models cache / `codex`, `copilot` help or config, `opencode models
github-copilot`), else a built-in list. Effort is passed to the CLIs: claude `--effort`, codex `-c model_reasoning_effort=…`
(0.18.9: also opencode `--variant` and the API keys; see "v0.18.9: thinking effort").

### Agent step quality (engine → `agent.step` data, shown in Live agents / Jobs)
- `kind: "read"` — `path`, `text` = file content (first 400 lines, newlines kept).
- `kind: "write" | "edit"` — `path`, `diff` = unified diff (`---/+++/@@` lines), `text` = one-line summary.
- `kind: "tool"` — `tool` (e.g. Bash), `text` = the command, `output` = its output (first 300 lines, newlines kept), `ok`, `ms`.
- `kind: "text" | "thinking" | "answer"` — Markdown, newlines kept.

## v0.3.1 additions

- **Pauses have an id.** `waiting.id` names the question (from step, kind and title). The engine sends it back as
  `asked` on resume; an answer only counts for the question with that id. When a resumed node reaches a different
  question first, it pauses again on that one instead of using the answer.
- **`waiting.labels`** `{approve?, reject?}`: the button texts the engine wants, when they are not the usual ones.
- **`gate_log`** on the thread state: the last 50 gate decisions as text lines.
- **Review findings.** After a review step (agent `branch-reviewer`, `security-auditor` or `lens-reviewer`, not per AC), the
  engine reads the "Blocking" section of each answer (and `E2E-RESULT: fail`). If there are blocking findings, the
  flow goes to the node `<step id>__fix` and pauses: `kind: gate`, title `"<step name>: N blocking finding(s)"`,
  labels `Fix them` / `Go on anyway`.
  - approve: the implementer runs in phase `review-fix`, the whole test suite runs, and the fix is committed as
    `fix(review): address <step name> findings`. Then the review step runs again.
  - reject (`why` needed): the findings are written to the gate log and the flow goes on.
  - While this runs, `current` is `<step id>__fix`; the web shows it on the review step.
- **Agent steps keep `output`.** `agent_steps.output` (migration V4) stores a tool step's output; `GET /api/jobs/{id}`
  and `/steps` return it.
- **AC status `already-met`.** When an AC's new test passes before any code is written, the engine pauses with
  `"<AC> already passes"`. Approve commits the test and sets this status.
- **Commit subjects** are at most 72 characters, cut at a whole word with `…`; the full criterion goes in the body.

## v0.3.2 additions

- **A flow follows its project folder.** `POST /threads/{id}/resume` and `/rewind` take `root` (the api sends the
  project's folder now). When it differs, the engine moves the thread there. It refuses (409) to run in a folder that
  is missing or holds only `.keel/`. Threads that were running when the engine restarted are moved by the same rule
  (/workspace ⇄ the real path that `keel2 start --docker` uses), or marked failed with the reason.
- ~~**keel v1 sees v2 flows that wait.**~~ Removed in v0.4.0 (no mirror): the engine writes no state file into the
  project. Since 0.4.1 the api does not read one either (a project's flow, phase and AC counts come from its threads).
- **Failures read well.** A Claude turn limit says "The agent used all its turns (N) before it finished." The
  usage-limit and login checks ignore JSON lines. A failed run's tokens are counted in its job (`agent.finished`
  with `status: failed` carries the tokens).
- **Parallel steps retry only what failed.** After "try again", agents of that step that already finished are not run
  again (memory only; cleared when the step finishes or on rewind).
- **Turns.** The explorer gets 40 turns in the spec and triage steps (its keel v1 file says 20, for mapping only).
- **fix workflow:** `repro → verify_repro (verify red + commit test(BUG)) → gate R → …`.
- **init:** the `questions` pause shows keel v1's three questions with the detected defaults (since v0.19.0 a
  `clarify` form, see "v0.19.0: one way to ask"; both answers go on). The answers are written to `.keel/config.yml` under `init:`
  (`runs_on`, `services`, `knowledge_sections`). The `setup plan` pause shows the config, the commands and the steps.
  Init writes the same test command keel detects (node:test: `--test-name-pattern`).
- **knowledge-refresh** without a section list: the sections from `init.knowledge_sections`, else the existing
  ones, else all five. Each librarian gets one.
- **Projects whose folder is not mounted** are not listed (their history is kept).

## v0.3.3 additions — agents keep their work

- **Agent sessions are kept.** Each agent run is known by its attempt key (step, criterion, agent, copy, section).
  claude runs with `--session-id <uuid>` (its sessions are kept in `/data/agent-home/claude`), codex sessions are in
  `CODEX_HOME` on /data. The same step again — after a restart, a "try again" or a send-back — continues that session
  (`claude --resume`, `codex exec resume`); the agent is told why. A lost session starts a new one. Providers that
  cannot resume get a short trail of what their last try read and did. A rewind clears the thread's agent memory.
- **Restart:** threads that were running wait for the api (`POST /threads/{id}/continue {keys, root}`), because logins
  live only in memory; the api sends them when it is ready and marks the cut-off agent runs `stopped`. Without that
  call a thread continues by itself after `KEEL_CONTINUE_GRACE` seconds (60).
- Agents are told to read keel's memory (`docs/knowledge/`) before exploring the code.

## v0.3.4 additions — the clarify loop

- **Questions before the spec.** The explorer (spec and triage steps) may end its answer with one fenced
  ` ```keel-questions ` block (JSON, max 4 questions, 2–4 options each, the recommended one first) and no criteria.
  The engine (`runtime/clarify.py`) reads it; `spec_gate` then pauses with `waiting.kind: "clarify"`,
  `waiting.questions: [{id, question, why?, options: [{label, description?, recommended?}]}]`,
  `options: ["approve"]`, `labels.approve: "Send my answers"`.
- **Answering:** `POST /threads/{id}/resume {decision: "approve", why?, payload: {answers: {<question id>: <label or typed text>}}}`.
  An unanswered question gets its recommended option, and the explorer is told so. The answers go back to the same
  explorer session (agent memory), which writes the spec. After `MAX_ROUNDS` (2) rounds the explorer must decide.
- **Spec check** (`runtime/spec_check.py`, keel v1's `keel spec check`): `[WEB]` criteria need a Mockup with four
  states, `[API]` criteria a Request path; empty sections, vague or "then"-less criteria, an AC id inside a drawing,
  more than 8 criteria are noted. Gaps that are clearly missing send the spec back to the explorer once (same session;
  real models by default, `settings.spec_check` overrides). The spec gate shows the rest as "Spec check" lines.
  0.18.25 every spec starts with six sections (content/templates/spec.md, `spec_check.REQUIRED`, `SECTION_ORDER`): In
  plain words, For engineers, What will change, Payload changes, Database changes, Architecture; a missing one is a
  "missing" item on the one send-back. Payload changes and Database changes are always there: a bare "none" is
  missing too, "none: <the reason>" passes.
- **Skills** (now `content/skills/`, see v0.4.0 below; shown in Build › Skill hub with source "keel"):
  `spec-clarify` (the question format, identity and work-placement probes) and `spec-writing` (spec layout, criteria
  form, drawings); both assigned to the `explorer` for phase `spec`. keel v1's `spec-authoring` (a live chat) is no
  longer assigned. Example result: `docs/examples/spec-leaderboard.md`.

## v0.4.0: content

- **One content folder.** keel v2 reads every agent, skill, stack, pack and template from its own `content/`
  (`content/README.md` says what each folder is). In the image it is `/opt/keel-v2/content` (`ENV KEEL_CONTENT`);
  `KEEL_CONTENT` overrides it anywhere; a dev checkout falls back to `<repo>/content`. Nothing reads agents, skills,
  stacks or templates from `KEEL_HOME` any more. Most files started as copies of keel v1 (MIT, commit `a9ed9e3`,
  `content/NOTICE.md`), edited so no agent is told to run a keel command, a slash command or read `.keel/state.json`.
- **Engine:** `config.content_dir()`; the role text and `maxTurns` come from `content/agents/<agent>.md`, skill
  `references/x.md` paths resolve under `content/skills/`, the librarian's templates are `content/templates/knowledge/`.
- **Api:** `KeelProperties.contentDir` (`keel.content`, env `KEEL_CONTENT`). Agents from `content/agents`; skills from
  `content/skills` (source `keel`) and `content/packs/<name>/skills` (source `keel pack`), version `v2`, plus the
  user's; stacks from `content/stacks/*.yml` and `content/packs/<name>/stack.yml` (+ the project's `.keel/stacks`).
  Installing a pack copies `content/packs/<name>` into `<root>/.keel/stacks/<name>`.
- `engine/tests/test_content.py` checks the front matter, the YAML, the packs' paths and the forbidden v1 words.

## v0.4.0: MCP

keel v2 has its own MCP server (`engine/keel_engine/mcp_server.py`, FastMCP from the `mcp` SDK), started as
`python -m keel_engine.mcp [--read-only | --write]` on stdio. It reads the public `/api` routes over HTTP
(`KEEL_API_URL`, default `http://127.0.0.1:8080`; the api listens only on 127.0.0.1). keel v1's MCP server is no
longer built in.

| Tool | Mode | What it returns (plain text) | api routes |
|---|---|---|---|
| `keel_status` | read | project, branch, flow, current step + phase, every AC with status, what is waiting (title, kind, options, clarify questions), usage (tokens vs cap, $), blockers | `GET /projects`, `GET /projects/{pid}/flow` |
| `keel_projects` | read | one line per project: id, branch, flow, phase, criteria done, waiting yes/no | `GET /projects` |
| `keel_timeline` | read | checkpoints and agent runs, newest first; `limit` 1-200 (20), `filter` all \| steps \| agents \| failed | `GET /threads/{id}/history`, `GET /jobs?project=` |
| `keel_next` | read | the single next action (gate to decide, step running, flow done/failed, "no flow: start one") + push blockers | flow |
| `keel_explain` | read | what a phase permits and refuses (edit buckets from `rules.MATRIX`, always-refused files, commit rules, shell rules), its place on each rail, next phases; `phase` defaults to the current one | flow (+ `keel_engine.rules`) |
| `keel_approve_gate` | write | decides the gate the project's flow waits at: `decision` approve \| reject, `why?`, `answers?` (clarify: question id → label or own words) | `POST /threads/{id}/resume {decision, why?, payload?: {answers}}` |
| `keel_resume` | write | the raw resume: `decision`, `thread_id?` (default the project's flow), `why?`, `payload?` | same |

- Every tool takes an optional `project` (id or name). Without it: `KEEL_PROJECT`, else the project whose root holds
  the server's working folder (an agent runs in its project), else the first project. 0.18.64: else the only project,
  else the client's folder (`KEEL_CLIENT_CWD`), else none: the answer names the projects to pick from (see "keel
  0.18.64" below).
- **Read-only vs write:** the write tools are registered only with `--write` (or `KEEL_MCP_WRITE=1` without
  `--read-only`); `--read-only` always wins. In read-only mode `tools/list` does not show them at all.
- **Agents** get the builtin `keel` server read-only (an agent never approves its own gate): the api seeds
  `mcp_servers.keel` = `<keel.mcp-python> -m keel_engine.mcp --read-only` (`KEEL_MCP_PYTHON`, default
  `/opt/engine/.venv/bin/python`, dev fallback `python3`), builtin and locked (command and args cannot change; it can be
  turned off or given env values). The engine adds `KEEL_API_URL` to its env; without an api entry it uses
  `[sys.executable, "-m", "keel_engine.mcp", "--read-only"]`. Settings keep `mcp: ["keel"]` as the default.
- `McpServer` has an optional `label` ("keel v2 (read-only)", "keel v1 (optional)"). The Tools page shows it and can
  turn any server on or off (`PUT /api/mcp-servers/{name} {enabled}`); "Add server" takes any `name + command + args + env`.
- **keel v1, optional and external:** `keel2 start --with-keel-v1 <keel checkout> [dir]` mounts that checkout read-only
  at `/opt/keel-v1-optional` (`KEEL_V1_OPTIONAL`), remembered in the container label `keel2.keelv1` for restarts. The
  api then offers `keel-v1` = `node /opt/keel-v1-optional/mcp/server.js`, **off**, not builtin; turn it on in Tools
  and allow it per agent like any other server. Without the mount the untouched entry is not offered; a deleted entry is
  not offered again. Nothing else in keel v2 reads it.
- **From Claude Code / Claude Desktop:** `keel2 mcp [--write]` runs
  `docker exec -i keel-v2 /opt/engine/.venv/bin/python -m keel_engine.mcp --read-only|--write` (stdio passthrough).
  `keel2 mcp [--write] --print-config` prints `claude mcp add keel-v2 -- <abs path>/keel2 mcp [--write]` and the
  Claude Desktop snippet `{"mcpServers": {"keel-v2": {"command": "<abs path>/keel2", "args": ["mcp"]}}}`.
- No `keel_dashboard` tool: the dashboard is the web UI.

## v0.4.0 additions — keel v2's own guard

- **Guard hook** `python -m keel_engine.hook pre-tool` (`engine/keel_engine/hook.py`): reads the Claude hook JSON
  (`{tool_name, tool_input, cwd}`) on stdin and the guard context from the file named by `KEEL_GUARD_CTX`
  (`{root, phase, ac: {id, layer}, lane, unlocks, agent, thread, knowledge_allowed, knowledge_strict}`, written by the engine per agent run into the run's scratch
  folder, mode 0600). Decides with keel's rules: `check_edit` (Edit, Write, MultiEdit, NotebookEdit; serena edit tools),
  `check_read` (Read), `check_bash` (Bash), MCP write-ish tools need `mcp.allow` during a flow. Allow = exit 0; deny = exit 2
  with `[keel guard] <reason>` on stderr, which the claude runner turns into a `guard` step and a `guard.refused` event
  (`source: "keel-hook"`). **Fails closed**: no readable context or any error → only Read/Glob/Grep/LS pass.
- **claude**: `--settings <scratch>/keel-guard.json` with one PreToolUse hook
  (`"<engine python>" -I -m keel_engine.hook pre-tool || exit 2`, matcher `Edit|Write|MultiEdit|NotebookEdit|Bash|Read|mcp__.*`). Always on.
- **opencode**: the engine writes a small plugin (`<scratch>/opencode/plugins/keel-guard.js`, loaded through
  `OPENCODE_CONFIG_DIR`) that hands each guarded call to the same hook. keel v1's adapter left in `<root>/.opencode/` by
  v0.3 is removed when found (only that file, only when git does not track it).
- **codex and copilot** have no pre-tool hook: they are guarded after each step only (the diff guard puts back the files the
  phase does not allow, `tools/guard.py`), so a forbidden edit exists on disk until the step ends. (v0.17.2: codex 0.150+
  runs the hook, and both get keel's command wrappers; see "v0.17.2: rules".)
- **Unlocks** live in the thread (engine DB + graph state). `POST /threads/{id}/unlocks` stores one, rewrites the guard
  context files of the thread's running agents at once, and the next step merges it into `state.unlocks`. The api's
  `POST /api/projects/{pid}/unlock` → `{ unlocks, via: "thread"|"engine", thread_id }`: a thread waiting with `kind:"fix"` gets it
  as a resume payload (as before), otherwise the project's running or waiting thread gets it through the engine; with no
  such thread the api answers 409.
- **Upgrade**: on the first start or resume of a thread, unlocks of an active flow in a project's old v0.3 state file are
  imported once into that thread (recorded in `legacy_unlock_imports`); that file is never written again.
- **Ladder rung 12** is the guard self-test: a context in phase red, a production-file edit must exit 2 and a read exit 0.

## v0.4.0 additions — verdicts, knowledge check, map, scan and code graph

### Engine
```
POST /projects/{p}/scan  { root, rebuild?: false }  → IndexStatus (status "indexing"; the scan runs in the background)
GET  /projects/{p}/index                           → IndexStatus
POST /projects/{p}/map   { root }                  → Map (built for HEAD and stored)
GET  /projects/{p}/map                             → Map | { missing }
IndexStatus = { project, root?, status: idle|indexing|ready|failed, files, symbols, indexed_at?, error?,
                stack?: string[], knowledge?: { present, missing, configured }, map?: { counts, sha } | { error },
                index_dir?, available: bool }
```
- Scan = stack (marker files) → `codegraph init -y` (first time) / `sync -q` / `index -q` (rebuild) → map → which
  `docs/knowledge/` sections exist. Events `index.progress` `{status, step: stack|graph|map}` and `index.done`
  `{status, files, symbols, error, ...}` with `thread_id: ""`. `.codegraph/` is added to `.git/info/exclude`. When SQLite
  cannot live in the project, the index moves to `$KEEL_DATA/index/<project>` and `.codegraph` links to it.
- `codegraph sync` runs in the background at every flow start and after every keel commit (best effort).
- Agents get the MCP server `codegraph` (`codegraph serve --mcp --path <root> --no-watch`, `CODEGRAPH_MCP_TOOLS=
  explore,callers,callees,impact,search`) only while the project's index is `ready`, and only agents with
  `knowledge.code_graph` on.
- Map = keel v1's map shape (`sha, at, limits, counts, levels`; the api no longer reads a `.keel/map.json` keel v1 left, 0.4.1) with `levels.system`, `levels.modules`
  and `levels.er` (from SQL migrations); `flow` and `classes` are not built (the Map page shows its empty state).
- v0.5.1 adds (old fields unchanged, `levels.er` no longer caps columns):
  ```
  schema   = { tables: Table[], relations: Relation[] }            # everything the migrations leave, no cap
  Table    = { id ("name" | "schema.name"), name, schema?, kind: table|view|materialized view, cite: {rel, line},
               comment?, columns: Column[], primary_key?: {name?, columns, cite}, uniques: [{name?, columns, cite}],
               indexes: [{name?, columns, unique, method?, where?, cite}], checks: int, changes: [{rel, line, what}],
               foreign_keys: [{name?, columns, ref_table (id) | null, ref_name, ref_columns, on_delete?, on_update?, cite,
                               missing (the target is not in the migrations)}],
               definition?, uses?: [table id] }                     # views
  Column   = { name, type, nullable, default?, pk, unique, identity, generated?, comment?, cite,
               fk?: {table, column?, missing} }
  Relation = { id, kind: fk|uses, name?, from (table id), from_columns, to, to_columns, on_delete?, on_update?,
               nullable, one_to_one, self, cite }                   # fk: child -> parent; uses: view -> table
  api      = { contract?, endpoints: [{method, path, cite, summary, tags, operation?}] }
  sources += { looked_in: string[], configured: bool, config: string, skipped_statements: int }
  counts  += { views, relations }
  ```
  Migrations: `map.migrations` in `.keel/config.yml` (folders or globs) when set, else `backend.dir`/`backend.migrations`
  (+ Flyway `db/vendor/*` beside it), else any `.sql` under db/migration(s), db/changelog, a folder named migration(s)
  (prisma, supabase, golang-migrate), db/schema.sql, db/structure.sql. Flyway versions sort as versions (V2 < V4.1 <
  V10), R__ last; `*.down.sql`, Flyway undo `U*__` and dbmate/goose down sections are skipped; test folders count only
  when nothing else is found. The SQL reader: runtime/sqlschema.py.
- Web (v0.5.1): the Map draws every level on one canvas (web/src/components/er): pan, zoom, minimap, find, drag
  (positions per project and level in `keel2.er.<pid>.pos.<mode>` / `keel2.map.<pid>.<level>.pos`), Reset layout,
  SVG/PNG export. The database level is laid out in the browser (a layered layout with orthogonal routes from the
  foreign-key row to the key row) and has a Structure panel; a map without `schema` (built before 0.5.1) is drawn from
  `levels.er`. `#/repo/<path>:<line>` opens that file in the Repo page with the line marked.
- Engine DB tables: `verdicts(project, kind, ok, detail_json, "commit", at)`, `project_map`, `project_index`.
- Code actions: `knowledge_check` (old name `memory_check`) writes the `memory` verdict; `verify_release` and a
  whole-suite `verify_green` write `release`; `verify_coverage` runs `commands.coverage` and writes `coverage`.
  Simulated runs write no verdict. Push blockers read these; `deps` and `security` are "not run" warnings unless
  `.keel/config.yml` sets `security.required: true` (or a list of gates).

### Api
```
GET  /api/projects/{pid}/index           → IndexStatus (engine proxy)
POST /api/projects/{pid}/index/rebuild   → IndexStatus (a scan with rebuild: true)
```
- Registering a project (POST /api/projects, the workspace, the startup scan) starts an engine scan (fire and forget).
- `index.done` becomes a notification: "Index ready: N files, N symbols" or "Index failed: <reason>".
- `GET /api/projects/{pid}/map` reads the engine's map (an old `.keel/map.json` only while the engine has none);
  `POST …/map/rebuild` asks the engine to build it.

## v0.4.0 additions: cover, ship and include

Workflows `cover` and `ship` (content/workflows; ORDER = feature, change, fix, init, knowledge-refresh, cover, ship).

- **cover** (phase coverage-fix): `measure` (verify_coverage, soft) → branch on RESULT → `decide` (for_each
  coverage_groups; choices test | delete | accept; accept needs a reason, lands in `data.coverage_accepted`, which the PR
  body and the final review print) → `write` (test-author; the item carries `decision`) → `review` (reviewer, assertions
  lens; BLOCKING: yes goes back to `write` by itself, 2 rounds, then keel asks: send back once more or go on, dismissed) →
  `commit` (coverage commit: refuses added production lines and `.keel/config.yml`) → `remeasure` (soft) → `covered`
  (branch, rounds 2: one more round, then go on with a reason or stop) → `report` (coverage_report: verdict + accepted).
  Every measure writes the sha-stamped coverage verdict; accepted groups stay skipped in later rounds
  (`data.coverage_accepted`), and a round that leaves the same groups open says so (`data.coverage_fingerprint`).
- **ship**: `plan` (skip_menu) → `verify_fix` (implementer, review-fix, retry_only) → `verify_fix_commit` → `verify`
  (verify_fast+verify_module, rounds 2) → `release` (deferred) → `cover_*` (the cover steps, one deferred unit `cover`) →
  `deps` (deferred; stands down when no manifest changed) → `audit` → `trace` (trace_strict) → `lenses` + `review`
  (optional unit `reviewers`: one reviewer per lens from review_lenses, rounds 2, redo verify) → `spec_walk` (optional:
  behaviour no criterion asks for) → `final_review` (report: verdicts; reject → verify_fix with the user's words) →
  `memory` → `memory_check` → `memory_commit` → `pr` → `pr_gate` → `open_pr` (never pushes).
- **Skip menu** (`skip_menu: true` gate): lists the steps after it as always runs / deferred / optional. Answer:
  approve, payload `{skip: {<unit>: "<reason>"}, lenses?: ["correctness", ...]}`; a skip without a reason or of an
  unknown unit asks again. Skips go to `data.ship_skipped` `[{step, band, reason}]` (PR body "Ship steps skipped",
  final review "Exceptions"), lenses to `data.lenses_chosen`. A unit is a step id or a `group`.
- **Review findings** (lens-reviewer, branch-reviewer, security-auditor steps): with `back`, findings go to that step as
  feedback without a question for `rounds` (default 2) rounds. Without it the question is as before, plus: payload
  `route` = review-fix (implementer, verify_green + fix commit; default) | coverage-fix (test-author, verify_green +
  coverage commit) | red (test-author, red commit); after the fix the flow goes on from `redo`; past `rounds` the
  question says the limit is reached. "Go on anyway" keeps them in `data.dismissed_findings` (shown at the final review).
  0.18.22 per finding: the question carries `questions` (runtime/clarify.py's shape), one per finding (`f1`, `f2`, ...,
  at most 10) with the options `fix` (recommended), `accept` and `not a finding` (both `why: "required"`) and, in a
  flow with the amendment loop, `amend spec`. Answer `payload.answers` `{f1: "fix", f2: "accept: <reason>"}`: the label,
  or `<label>: <reason>`; own words naming no option count as accept with the words as the reason. A reason-needing
  option without one (and without `why`) is refused (400). An unanswered finding follows the decision: approve = fix,
  reject = accept with `why` (old clients work as before). Fix ones go to the fixer (the others named as "leave as
  they are"); amend spec runs `amend_start` with the finding as the reason and goes to `amend_entry`; accepted and
  not-a-finding ones go to `data.dismissed_findings`, one entry each: `{step, name, item, lens, findings: [text],
  decision, why}`, and are not asked again when the review reports them again. Each review's "Non-blocking" items go
  to `data.nonblocking_findings` `[{step, name, lens, text}]` (once per step and file:line).
- **Security verdicts** (0.18.22): the security step writes verdict `security` (ok = no blocking finding; the findings
  gate writes ok with `detail.accepted` when every finding was accepted); a dependency triage with nothing reachable
  turns a failed `deps` verdict into ok with `detail.cleared` `[{advisory, reason}]` (also `data.deps_triage`). The
  push gate's deps/security entries read these verdicts (missing: "not run on this branch"; stale; failing summary).
  `verify_deps` blocks only when an audit's worst severity is at or above `security.deps.fail_on` (default high;
  an output with no readable severity blocks).
- **AC gate detail** (0.18.22): the criterion line, `RED: <sha> test(<AC>) ...` with its test files, `GREEN: <sha>
  feat(<AC>) ...` with its diffstat (commits since the flow started), `AC-REVIEW: pass|findings` and the criterion-reviewer's
  answer word for word, `Board: done ... · now <AC> · todo ...`, and `Last step:`.
- **Choice gate** (`choices` on a per-item gate): options `["approve"]`, the question carries `choices`; payload
  `{choice}` (plain approve = the first); stored on the item (`decision`, `reason`) and as `markers[<id>].CHOICE`.
- **Final report** (`report: verdicts`): exceptions first (skipped steps, accepted coverage, dismissed findings,
  skipped gates, unlocks, flaky tests, push blockers), then fast/module/release/coverage/deps/audit/trace/arch/memory
  for HEAD (stale verdicts marked), review rounds, the trace table, the spec, the diffstat.
- **Include**: `{ id: ship, kind: include, name: ship, flow: ship }` is replaced by the steps of `ship` wherever a
  workflow is validated (YAML, StartThread JSON, templates). Ids become `ship_<id>`; back/no/redo/when.step follow. With
  `skippable` on the include, all its steps are one unit (group = the include id). Nested includes work, cycles are
  errors. feature/fix/change end with the ship steps by adding that one line (the api accepts `kind: include`).
- Code actions `review_lenses` (data.review_lenses from data.lenses_chosen, review.lenses or correctness/security/
  performance + architecture when architecture.style or rules are set) and `coverage_report`; state `rounds`
  ({branch id: send-backs}); a code step's `back` names where its failures go.

## v0.4.0 additions: review, diagnose, fix and change

Workflows in `content/workflows/` (ORDER: feature, change, from 0.18.12 quick, small, implement, then fix, diagnose, review, review-pr, init, knowledge-refresh, cover, ship, hunt, hunt-next; then lint and, from 0.18.6, plugin).

- **review** (read-only, phase `review`): `review_scope` reads `data.lens` (code | all | correctness | security | performance |
  architecture | assertions | ac <ID>) and `data.base` (default base_branch, main, master) and builds `data.review_lenses`
  (one item per reviewer, each naming its `agent`: branch-reviewer, lens-reviewer, criterion-reviewer); an empty diff, an unknown lens, a
  missing base or a criterion without its test/feat commits **stops** the flow (`ActionResult.stop`). One reviewer per item
  in parallel → `report` → gate showing every answer verbatim + tally (`2 of 4 blocking`). Reviews in a read-only phase never
  start the findings fix loop. Send back = review again.
- **review-pr** (read-only, phase `review`; ORDER after review): someone else's change against a ticket and the team's
  definition of done (keel v1's review-pr). Input: the request's first line names the PR (a number, `#12`, `PR 12`, a pull
  URL) or `branch: <name>`, the rest is the ticket; or `data` (the api's options) `pr`, `branch`, `base`, `ticket`, `force`.
  `ticket_prep` (no model): a PR is read with `gh pr view` and its head and base fetched into `refs/keel/review-pr/<n>`
  (a branch: local, else `origin/<branch>`); a closed or draft PR, an empty diff, only lockfiles / generated files, or only
  docs **stops** the flow (`force` reviews a draft, closed or docs-only one). The checklist: the ticket's "Acceptance
  criteria" / "Definition of done" bullets (else its checkboxes) as T1.., `.keel/dod.md` as D1.. (else
  `content/templates/dod.md`); script checks on the added lines decide AUTO verdicts (secrets, disabled tests, debug / TODO
  prove not-met; no database change = met). Files ranked by risk with a `like:` neighbour at the base; `data.ticket_jobs` =
  one `all` job (400 changed lines or less) or ticket / team / bugs (up to two groups) / quality. → `ticket_build`: compile
  (`commands.api_compile`, `web_typecheck`) and the check tools (no fixers, no sonar) in a throwaway `git worktree` outside
  the project; only messages naming a changed file count (fake model: only `.keel/config.yml` tools, no compile) →
  `pr-reviewer` per job (result lines `T1 | met | file:line | why | to do`, `F | blocking|minor | ...`, `Q | ...`,
  `S | ...`, `TICKET-REVIEW: done`) → `ticket_claims`: one `check` job per not-met item and blocking finding →
  `pr-reviewer` per claim (`CHECK | confirmed|rejected | why`) → `ticket_record`: a rejected finding is dropped, a
  rejected not-met becomes unclear, an item no reviewer judged becomes unclear (named in the note); verdict not done (a
  not-met item, a blocking bug, a compile error in a changed file) / needs a check / done; the report (next steps last) is
  the gate's text → gate `share` `choices: {done: end, share: share_text}` with the multi-select question `sections`
  (summary, notmet, blocking, unclear, minor, build, quality; only non-empty ones offered; summary, notmet and blocking
  recommended) → `ticket_share`: the author's text from the ticked sections → gate `post` `choices: {keep: end, post:
  post_comment}` → `ticket_comment`: `gh pr comment <n> --body-file`, never in run mode auto, on the fake model or for a
  branch. A plain approve at either gate shares and posts nothing.
- **diagnose** (phase `bug-investigate`): explorer frames the symptom and collects `hypotheses` → one investigator each
  (ROOT-CAUSE) → `report` → investigator ranks + RECIPE/KIND → `report` → gate `choices: [fix, feature, unresolved]`:
  start_flow fix (seed recipe + symptoms, never the cause), start_flow feature (seed evidence), or `investigation_note`
  (`docs/investigations/<date>-<slug>.md`, every hypothesis kept, committed `docs: investigation note …`).
- **spike** (phase `spec`): explorer frames the question, what it needs from outside the repo and `angles` (`keep`:
  `data.frame_text`) → 0.18.20 `web_docs`: the web-researcher reads the frame and researches the outside docs (search
  first, at least five sources; `keep`: `data.web_docs_text`, CONFIDENCE) → one investigator per angle (VERDICT; the web
  findings in its prompt, it may read the web with `curl -sL`) → `report` → investigator writes the findings (confirmed
  vs not, sources with URLs, RECOMMEND) → `report` → gate `choices: [spec, drop]`.
- **clarify** (phase `spec`; runtime/clarify_flow.py, the clarify-loop skill of the person's ai-coding-toolkit): explorer
  `read` (`keep`, markers TASK-TYPE and SECTIONS) → `clarify_start` sets `state.clarify` = {questions: [task_type],
  step: type_set, title} (a code step may ask: the clarify gate's answers jump to `clarify.step`, and `title` names the
  form) → gate `task_type` (`when: {state: clarify.questions}`) → `clarify_type` (data.clarify.type, the template; the
  `sections` question, `multiple: true`) → gate `sections` → `clarify_sections` (sections 2 and 3 always; data.clarify
  .brief, `ask` yes when 1 or 4 is ticked; `clarify_rounds` back to 0) → explorer `rounds` (`asks: true`, `rounds: 4`:
  an asking agent step's own number of question rounds, 6 questions a round; NO_QUESTIONS ends them) ⇄ gate
  `round_gate` → explorer `assemble` (`keep`) → `clarify_doc` writes `<specs>/clarify/<date>-<slug>.md` as a keel spec
  (the six first sections, In plain words to Architecture, then the template's sections, the skill's Definition of
  done, the prompt as the last appendix) and runs `spec_check.check`: a "fix" finding sends it back to `assemble` once (branch
  `checked` on marker SPEC-CHECK) → gate `result` `choices: {feature, fix, change, small, spike, prompt, reject: assemble,
  stop: end}`; `data.result_without` drops the flows that do not fit the type and `data.result_order` puts the one that
  fits first (the default: a gate's `data.<gate id>_order`) → start_flow with seed {title, request (the request, the
  prompt's report part and the criteria as AC lines), evidence (the document), acs (feature only)}, or
  `clarify_prompt` (ends, no child).
- **fix**: `bug_intake` (data.no_gates or `gates.bug_gates: false` → `gates.skipped["gate-r"|"gate-f"]`: those gates approve
  themselves and the PR body lists them; data.needs_e2e → marker E2E) → reproducer (REPRO; not-reproducible → start_flow
  diagnose) → verify red + `test(BUG)` → Gate R `choices: [investigate, stop]` → hypotheses → investigators in parallel → no
  confirmed cause → `escalate_model` once (settings.stronger_model, else Opus / high effort) and again → still none →
  start_flow diagnose → plan (E2E marker) → Gate F `choices: [fix, feature]` → implementer → verify + `fix(BUG)` (`rounds: 1`,
  then `after_rounds: reset`: keel's uncommitted edits go back, reproduce again) → regression e2e when E2E yes (seed or
  plan) → `ship` include.
- **change**: triage (SIZE marker, inline ACs; 0.18.21: a change that rests on outside docs the repo does not hold is a
  feature, whose flow researches them on the web) → `change_size` (ids `CHG-<n>.<m>`, > change.max_inline_acs → feature) →
  scope gate `choices: [small, trivial, feature]` → feature: start_flow feature with the inline ACs; trivial: implementer +
  verify + `refactor:` commit (refused → reset, triage again); small: `change_start` (gate mode end; small against a feature
  recommendation logs `escalation-override: <why>`) → AC loop → `ship` include. A commit's escalation trigger, approved, now
  starts the feature flow with the inline ACs (their done status kept) and ends the change flow.
- **Engine pieces**: gate `choices` outside a loop → markers[gate] `{CHOICE, WHY}` (plain approve = the first choice;
  reject = send back); `when.step` may be a list and `when.any` reads each fan-out item's marker; a code step's `then: <id>`
  jumps; a fan-out item's own `agent` is used when no agent's answer made the list; `report` action → `state.show`, the next
  gate's detail; `last_answer` keeps 8000 chars, fan-out results 16000; markers CODE-REVIEW and AC-REVIEW (pass | findings)
  are registered; start_flow seeds may carry `evidence` (added to the child's request) and `no_gates`.


## v0.4.0 additions: hunt, hunt-next and init

### Workflow keys (engine, generic)
- **Gate exits:** `choices: {take: take, close: close, stop: end}` (a map) on any gate: the pause has `options: ["approve"]`
  and `choices: [names]`; `payload.choice` picks one (default the first), `"end"` finishes the flow, and the answer is
  kept in `data["<gate id>_answer"] = {choice, why, payload}` for the step it leads to. (A list stays the per-item choice
  gate of cover.)
- **`when` on a gate:** the gate pauses only when it holds; otherwise it is approved by the engine and logged
  `gate <id> approve: not asked, ...`. `when` (gates and branches) also reads `data: <path>` (`{data: hunt.mode, equals: semi}`;
  no equals/in = "is set"). A branch's `no` may be `end`.
- **Gate detail from the state:** `data["<gate id>_detail"]`, when a step before the gate wrote it, is the pause's detail.
- **`batch: "$data.path"`** reads the concurrency when the step runs. A fan-out with `collect` tags each collected entry with
  `from_item` (the id of the item whose agent gave it).
- A code step's `back` may also point forward (hunt: a refused close goes back to the triage gate).
- Code actions get `settings` (the thread's) and `thread_id`. StartThread.settings gains `fast`,
  `hunt_mode` (auto|semi), `hunt_scope` (all|diff|paths), `hunt_lenses`, `hunt_run`; a parent's `start_flow` seed may carry
  `mode`, `scope`, `lenses`, `fast`, `run` instead.

### hunt (content/workflows/hunt.yaml, runtime/hunt.py + hunt_actions.py)
`own_branch: false` (the api cuts no branch for it; hunt-next too). `start` (run in the backlog; the proposed lenses are
`hunt.lenses`, else the ones the project's code calls for: `hunt.fit_lenses`) → `confirm_lenses` gate (confirm|stop; `payload.lenses` or "drop x" in the note) → `confirm`
(one sweep item per lens×lane, brief copied in) → `deps` (security lens: the dependency audit first, always) → `sweep`
(one `hunter` per item; JSON list + `FINDINGS: n`) → `ingest` (lane enforced by classifying every cited path, cap
`max_candidates_per_lens` (halved when fast), severity dropped, same file ±`dedup_line_window` merged as `also`;
`docs/hunts/<run>/candidates.md`, UNVERIFIED) → `sweep_gate` (semi only: prove|stop) → `prove` (one `prover` per candidate,
symptom and where only, never the claim; `batch` = `hunt.prove_concurrency`) → `verdicts` (proven needs a recipe that ran
twice, `.sh .http .sql .md .probe.ts`, a severity; a 5xx is never below `severity_floor_5xx`; refused verdicts go back to
the provers, at most `prove_rounds` (3) rounds, then unproven / raised to the floor) → `verdicts_gate` (semi only) →
`group` (investigator, groups that share a cause) → `report` (`docs/hunts/<run>/report.md` + `repro/`; refuses while any
candidate has no verdict; marks `needs_e2e`, never in a project with no UI or HTTP layer) → `commit`
(`docs(HUNT-<run>): bug hunt: N proven, M suspected`, only that folder; with layout keel / external the report folder is
`.keel/hunts/<run>/`, outside git, and nothing is committed) → `triage` gate (take → `start_flow hunt-next` | close {id, as} + note | stop).

### hunt-next
`take` (refuses while any candidate has no verdict or the report folder is uncommitted; the top open group by worst severity,
marked dispatched) → defect: `start_flow fix`, unspecified: `start_flow feature`, seed {title, request = the lead's symptom,
recipe = `docs/hunts/<run>/repro/<file>` + its body, symptoms (every member), needs_e2e, hunt: {run, group, lead, findings}}
→ `close_gate` (fixed|accepted|wontfix|later; a note is required, else it asks again) → `close`.

### init
`git_check → git_repo gate (only when the folder is not a git repository or has no commit: init = git init and the first
commit, refused for files that look like secrets or dependency folders | retry | go_on | stop) → git_init → discover
(explorer, keep: true: its ≤60-line map is data.discover_text) → arch_detect (runtime/arch.py; low confidence and not fast →
one arch-surveyor, ARCH marker) → questions (run, services, knowledge, and layout while the config names none) →
plan_gate (the config block, every ladder rung with its command, compose services with their ports, environment variable
names, the layout) → write_config (an existing config gets only its init: block) → arch_set (architecture: {style,
confidence, source} in .keel/config.yml) → lanes (ladder_soft ‖ one librarian per section written this run, each given the
explorer's map) → doctor (one read-only setup-doctor for all failing rungs, resuming its session each round; it proposes,
a keel-fix block holds the commands) → rung_report → rung_gate (when a rung fails: the DIAGNOSIS and the fix; apply_fix
runs the shown commands and writes a corrected rung command to .keel/config.yml | retry | skip → setup.not_checked | stop) → rung_apply → ... → knowledge_check → commit →
summary (data.hand_over_detail) → audit_now (skip | hunt: start_flow hunt, semi, whole project; only a person answers it)
→ hand_over (rungs, sections written and kept, what was not checked, what next)`. `fast`: no knowledge sections by
default, no surveyor, a rung that passed before with the same command is re-used. A re-run keeps the sections that exist
(`init.write_sections` is what this run writes; `init.knowledge_sections` in the config is the whole selection).

### Engine DB + API
Tables `hunt_runs`, `hunt_candidates`, `hunt_groups`, `hunt_recipes` (runtime/migrate.py).
```
GET  /projects/{p}/hunts                → [{run, at, sha, branch, mode, fast, scope, lenses, thread_id, counts, open, report, candidates_report}]
GET  /projects/{p}/hunts/{run}          → that + {swept, gates, stack, candidates: [... , recipe], groups, report_markdown, candidates_markdown}
POST /projects/{p}/hunts/{run}/close  {id, as: fixed|accepted|wontfix, note}   → the run (400 without a note)
api: GET /api/projects/{pid}/hunts, GET /api/projects/{pid}/hunts/{run}, POST /api/projects/{pid}/hunts/{run}/close (pass-through)
```

## v0.4.0 additions: feature

`content/workflows/feature.yaml` (version 2; keel v1 skills/feature). Ship's review, final review, memory and PR come from the
`ship` include; feature keeps no copies.

`preflight` (test command, own branch `feat/<slug>` when on the base branch, else stays: handed-over commits stay; data.no_gates
waives the integration/e2e/smoke gates) → 0.18.21 `web_check` (explorer, `keep`: `data.web_check_text`, marker WEB: yes | no: does
the request or the code it touches depend on a third-party API, webhook, library or standard whose details the repo does not
hold?) → `web_docs` only when WEB is yes (`when: {marker: WEB, equals: "yes", step: web_check}`; web-researcher, `keep`:
`data.web_docs_text`, CONFIDENCE) → `spec` (explorer: the interview; `{{data.web_docs_text}}` in its prompt: criteria about
outside data cite what is confirmed, an unconfirmed detail becomes an open question; clarify questions on `spec_gate`) → only once there are
criteria (`when: {state: acs}`): `explore_areas` → `explore` (one explorer per area: api, web, data) → `maps` (report) → `plan`
(appends `## Plan`, marker ORDER) → `spec_sync` (criteria re-read from the spec, ORDER applied, the gate text) → `spec_gate`
(locked; exits approve → `freeze` | edit, rewrite → `spec_edit` | review → `spec_review` (reviewer, phase review, shown on the
gate) | order → `plan` | reject → `spec_restart` → `spec`) → `freeze` (frontmatter status frozen + approved/frozen dates; only
the spec is committed, `docs: spec and plan: <title>`) → `options` (skip menu: integration_gate, security, e2e, smoke) →
`contract` → `contract_diff` → `contract_gate` (approve → commit | change → contract | amend → the contract draft is put back,
then the amendment) → AC loop (red/green may end with `AMEND: <why>` → `red_to_amend`/`green_to_amend`) → `integration` →
`integration_commit` (`fix(integration)`) → `integration_gate` → `security_deps` (verify_deps, soft) → `security_scope` →
`security` (fan-out: security-auditor, plus dependency-triager when verify deps failed; findings → the findings question) →
`review_fix` (retry_only) → `review_fix_commit` → `code_review` (branch-reviewer, back review_fix, 2 rounds) → `e2e_scope` ([E2E]
criteria or needs_e2e; marker TOOL) → `e2e_tool` (only when commands.e2e is missing: write → specs written unrun, recorded in
data.ship_skipped `e2e-run` | check → look again) → `e2e` → `e2e_commit` (verify_e2e) → `e2e_gate` → `smoke_scope` ([SMOKE] or a
Smoke checks section) → `smoke` → `smoke_commit` (verify_smoke: smoke/*.sh + commands.smoke_e2e) → `smoke_gate` → ship_* → `adr`
→ `adr_commit` → `close`.

Amendment: `amend_start` (reason from the AMEND line or the contract gate note) → `amend` (explorer writes a dated block under
`## Amendments`, markers REOPEN, CONTRACT) → `amend_show` (no block → back to amend) → `amend_gate` (locked; approve | context |
change → amend again | rebuild → start_flow feature with the reason, commits stay) → `amend_commit` (only the spec,
`docs: amend: ...`; new criteria join, REOPEN ones are todo) → the contract again when CONTRACT yes (or it came from the
contract gate), else back into the AC loop. Seeds: inline acs (change) skip the questions and keep their status; evidence (fix,
diagnose) goes into the request. Deferred: lanes in worktrees, spike mode.

Engine (generic): `when` on agent/parallel/code steps (skipped when it does not hold) and `when.state: <path>`; a skip menu
covers the steps up to the next skip menu; a plain reject on a gate with named exits takes the `reject` exit when there is
one; a soft code step keeps what it said in `data["<id>_output"]`; markers SECURITY, DEPS (clean | findings) and AMEND (free
text, never asked for) are registered; a reviewer answer ending `CODE-REVIEW|SECURITY|DEPS: findings` without a Blocking
section counts its list items as findings; commit takes `paths` (only those are staged) and names integration commits
`fix(integration)`; StartThread acs may carry `status`. Actions in `runtime/feature_actions.py`.


## v0.4.1: lint and static checks

Stack packs (`content/stacks/*.yml`, `content/packs/<name>/stack.yml`, `<root>/.keel/stacks`) and `.keel/config.yml` declare
`tools:`; the engine runs them (`runtime/stacks.py` detects the stacks like the api's StackService, `runtime/tools.py` runs
the tools, `runtime/lint_actions.py` holds the actions).

```yaml
tools:
  <name>:
    run: 'npx --no-install eslint --fix {FILES}'   # {FILES} {FILE}: quoted paths keel produced, relative to the tool's folder
                                                   # {BUILD}: backend.build (a ./wrapper at the root is found from a subfolder); {DIR}
    on: manual | edit | batch | pre-commit | pre-push   # default manual
    fail: fix | block | warn                       # default warn. fix = it changes files (keep, re-stage); block = refuse; warn = note
    match: '\.(ts|tsx)$'                          # regex on the repo-relative path; no match among the files = the tool does not run
    timeout: 120                                   # seconds
    kind: check | status | task                    # default check; only checks are lint (status = CI probe, task = writes a file)
    lane: api | web                                # folder: backend.dir / frontend.dir (default: the stack's lane); "." or missing = root
    dir: ''                                        # an explicit folder instead
    description: '...'
  <name>: false                                    # turns a stack's tool off
```
- **Merge**: matched keel stacks → matched keel packs → the project's `.keel/stacks` → `.keel/config.yml`; a later block overrides
  only the keys it names. A malformed tool (bad `on`/`fail`/`kind`/`lane`/`timeout`/`match`, no `run`, `{FILES}` with
  `on: manual`) is listed as a problem and never run. PyYAML's `on: → true` key is read as `on`.
- **Running** never raises. Exit 127, or "command not found" / npx "could not determine executable" / "No module named" /
  gradle "Task … not found" in a short output = **not available** (never a failure). A file-scoped tool gets at most 200 paths
  per command line. Every run emits `tool.ran` `{tool, on, fail, ok, available, ms, files, cmd, dir, code, source, output}`
  (full output, 24 KB) for the dashboard; agents get one line per tool.
- **Simulated checks** (every model fake, or settings.simulate_checks): only the tools of `.keel/config.yml` run.
- **Edit hook** (every workflow): after an agent step in a phase that may edit, the files it changed (not the ones the guard put
  back) get the `edit` and `batch` tools. A fixer's changes stay; failures become `data.lint_notes` (one line per tool), shown
  in the step note, in the next agent's prompt and in the next gate's detail.
- **Commit hook** (`commit` action): the `pre-commit` tools on the staged files, fixers first: fix → `git add` the staged files
  again; block → the commit is refused ("Static checks refused the commit: <tools>", trimmed output), and the code step's
  retry hands it to the agent before it; warn (and a fixer's nonzero exit) → a line in the commit note. Skipped when
  `data.lint_tree` (written by `lint_run`) equals the working tree.
- **Verdict** `lint` (verdicts.KINDS): `{scope, files, tools: {name: {ok, available, fail, on, ms, files, head}}, summary,
  warnings, problems}`, sha-stamped; passes unless a block or fix tool failed; ok false + available false when nothing could run.
- **Phase `lint-fix`** (keel_rules.json, the api copy and the golden fixture): MATRIX allows api-main, web-src, api-test, web-test,
  e2e, smoke, other; denies migration, contract, specs. Commit type `lint` → `chore(lint): <title>` (no id). FLOW_START
  `lint: lint-fix`, RAILS `lint: [lint-fix, close]`, TRANSITIONS `none → lint-fix`, `lint-fix → ship | close | none`.
- **lint workflow** (`content/workflows/lint.yaml`, ORDER ends with `lint`; data `scope: diff | all`): `scope` (lint_scope: the
  branch diff and uncommitted files, or `git ls-files` with `scope: all`; the user's own uncommitted files are left out) → `run`
  (lint_run, soft: every check tool on manual/edit/batch/pre-commit, fixers first; verdict; `data.lint_findings`) → `findings`
  (branch on RESULT; no → commit) → `fix` (implementer, lint-fix, instructions carry the findings: no behaviour change, no
  suppressions) → `rerun` → `clean` (branch, `rounds: 2`; still failing → "Go on anyway" / "Stop") → `commit` (chore(lint)) →
  `report` (lint_report: verdict, per-tool table, still failing, went on) → `read_report` gate (send back = run again).
- **ship**: `lint` after `verify` (`verify_lint`, soft, `skippable: optional`): the check tools on the branch diff **without**
  fixers. The final review's verdict table has a `lint` row and an exception "static checks fail: …" when the fresh verdict
  fails; the PR body has a "Static checks" section (one line per tool).
- **review**: `lens: lint` runs the same check-only set on the diff (no agent) and the report starts with "Static checks";
  `lint: true` adds that section to any other lens.
- **Stacks**: kotlin-spring (ktlint-format pre-commit fix, ktlint pre-commit block, detekt manual block, sonar status),
  ts-react (eslint-fix + prettier on edit fix, tsc pre-commit block, eslint manual block), react-js (the same without tsc),
  django and the new python stack (ruff-format on edit fix, ruff-fix pre-commit fix, ruff manual block, mypy manual warn),
  symfony (cs-fix pre-commit fix, phpstan + cs-check manual block), new go stack (gofmt on edit fix, go-vet pre-commit block,
  golangci-lint manual block). `schema-dump` tools are `kind: task`. `static_checks` names only programs a check tool runs
  (test_content).
- **Api**: `Stack.tools[]` = `{name, on, fail, description?, kind?, match?, off}`; the Stacks page shows them. Start a flow lists
  the lint flow (engine templates) and sends `options: {scope}`.

## v0.4.1: explain a step

"What does this step really do": the graph's nodes (Flow, both layouts; Wiki; the Wiki step table) open a drawer, and the
builder's step panel has a "What it does" button (it explains the unsaved draft).

```
engine  POST /steps/explain  { workflow?, step_id, root?, thread_id?, project_id?, agents? }  → StepExplanation
        (workflow missing = the thread's own; 400 without both, 404 for an unknown step or thread)
api     POST /api/projects/{pid}/workflows/explain-step  { workflow_id? | workflow?, step_id, thread_id? }
        → the engine's answer; the api adds root (the project folder), project_id and agents (each agent's knowledge)
StepExplanation = { id, name, kind, phase, phase_meaning, phase_inherited, lock, thread: bool, included_from?, runs_only_when?,
  skippable?, rules: { buckets: [{bucket, what, may: edit|new-only|delete-only|read-only|no-access, label, note?}],
  shell_refused: string[], lane_scoped, commit?: {type, prefix, message, author, may_contain, may_not_contain, extra} },
  loop?: {kind, text, fan_out?}, next: [{label, to, to_name, text}],
  agent?: {id, about, model: {step, agent_file, effort, rule, now?}, knowledge, instructions, markers, collect, prompt,
           placeholders, prompt_notes, system},          // prompt = prompts.task_prompt + step_asks, as Compiler._run_agent builds it
  code?: {chain, actions: [{name, summary, steps[], for_this_step?, after?}]}, gate?: {answers, notes}, branch?: {condition, routes},
  last_runs?: {count, now?, runs: [{at, note, ok, ac?, item?, went_to?, output?, commit?: {sha, subject}, answer?, tokens?,
               markers?, decided?}], calls?} }   // with thread_id, from the thread's checkpoints (+ the engine's recent events)
```
- Without a thread the prompt carries «placeholders» (`«the current criterion»`, `«data.report: filled in ...»`).
- Action words live in `engine/keel_engine/runtime/action_docs.py` (`DOCS`: summary + steps, else the function's
  docstring); `tests/test_explain.py` fails when an action the engine dispatches has no entry, or an entry has no action.
- A code step now keeps the head of what its actions printed in the state (`output`), for the last run.


## v0.4.1: inbox, notifications, run modes

### Run modes (engine `runtime/run_mode.py`)
`StartThread.settings.run_mode`: `manual` (default) | `important` | `auto` | `readonly`. `Compiler._ask` asks the policy
before every `interrupt()`: `run_mode.classify(question, step, …)` names the pause, `run_mode.decide(mode, kind, …)` returns
the answer keel gives itself (`{decision: "approve", why: "auto-approved (mode <m>)", payload, auto: true}`) or `None` (ask).
The caller logs an automatic answer like any other, so the gate log reads `gate spec_gate approve: auto-approved (mode auto)`,
`ac AC-1 approve: auto-approved (mode important)`.

| kind (`classify`) | what it is | manual | important | auto | readonly |
|---|---|---|---|---|---|
| `ac` | a per-criterion AC gate | ask | approve when `last_failure` is empty and the AC review before it (criterion-reviewer/lens-reviewer) has no `AC-REVIEW: findings` and no Blocking items | approve | ask |
| `spec` | spec / triage gate, amendments | ask | ask | approve (never when only a send-back fits: no criteria) | ask |
| `clarify` | the explorer's questions | ask | ask | approve with each question's recommended option | ask |
| `contract`, `final-review`, `pr` | contract gate, `report: verdicts`, the gate after a `pr` step | ask | ask | approve | ask |
| `choice`, `skip-menu`, `gate` | named choices (default = first / `recommend`), skip menus (run everything), any other gate | ask | ask | approve | ask |
| `findings`, `already-met`, `escalate` | review findings within `rounds or 2`, a test that passes before code, a change-flow escalation | ask | ask | approve | ask |
| **safety**: `failure`, `rounds`, `rung`, `budget`, `dependency`, `secrets`, `readonly`, `note` | step failed / keeps failing, loop past its rounds (branch, findings past their limit), init rung_gate, token cap and plan windows, new dependency, a secret staged for commit, a read-only commit, hunt-next close_gate, init audit_now and git_repo | ask | ask | ask | ask |

- keel approves the very same question (same `waiting.id`) by itself at most `MAX_REPEATS` (3) times in a run, then asks.
- **Secrets stop every mode**: the commit's secret scan now asks (`kind: fix`, "A secret is staged for commit", labels
  "Send back to remove it" / "Stop the flow"): approve sends the refusal to the agent before the commit, reject stops the flow.
- New-dependency questions carry labels `Allow` / `Refuse`.
- **readonly**: the guard context gains `readonly: true` (hook: every Edit/Write/MultiEdit/NotebookEdit, serena edit, write-ish
  MCP tool, and shell command that writes — redirects, rm/mv/cp/mkdir/touch, git add/commit/push/reset/checkout…, package
  installs, sed -i — is refused; an unlock or `mcp.allow` does not open it); the ToolBox refuses `write_file` and such
  commands; the diff guard puts back every changed file in any phase; the edit-hook tools do not run; the `commit` action
  asks before staging (`kind: fix`, "Read-only run: the commit is refused"; approve tries again, reject stops).
- **auto**: `open_pr` returns the body and opens no PR (also when the PR gate's own approval was automatic). The final review's
  Exceptions list `- gate auto-approved: <line>`; the PR body has `## Auto-approved gates` (run mode + lines) in important/auto.
- keel never pushes in any mode; stop and rewind always work (a rewind clears the repeat counter).

```
engine  POST /threads/{id}/mode  { mode }   → ThreadState     (stored with the thread's settings; counts from the next pause;
                                                              the question that waits now stays the user's: a resume carries
                                                              its id and the policy never answers that one; event thread.mode {mode, from})
        ThreadState + run_mode, waiting.id (the question id; already sent back as `asked`)
api     POST /api/threads/{tid}/mode { mode }    → the engine's state (400 for an unknown mode)
        POST /api/projects/{pid}/flows + { run_mode? }   (default: the project's setting)
        Settings + run_mode: "manual" | "important" | "auto" | "readonly"  (general + project override)
```

### Inbox (api)
```
GET  /api/inbox?project=&kind=   → { items: InboxItem[], count, kinds: string[], projects: {id, name, count}[] }
GET  /api/inbox/count            → { count, projects: {pid: n} }      (the database only)
POST /api/inbox/{tid}/act  { decision, why?, payload?, id? }  → ThreadState
     (FlowService.resume — the Flow page's path; 409 when the thread no longer waits or now asks another question than `id`)
     (P4 an approval keel or a part asked itself — step "approval", e.g. product-jira-send — is no flow: approve
      approves it, reject denies it, → { id, status: "decided" }. Its payload.open {page, arg, label} is where the
      Inbox's Open goes; the web answers it at POST /api/approvals/{id}/decide)
InboxItem = { project_id, project_name, thread_id, flow (the flow's title), workflow_id, step,
              kind: gate | clarify | fix | budget | usage | dependency, title, detail (≤ 700 chars), more, options, choices?,
              choice_info? (v0.19.0), questions?, labels?, id?, phase?, ac?, run_mode?, auto_approved, last_auto?, since }
```
Built from `threads` rows with status `waiting` in listed projects, each asked of the engine (`GET /threads/{id}`, cached 3 s;
the saved `state_json` when the engine is down); a thread the engine says moved on is saved and left out. `since` = the
thread's last `gate.waiting` event. Gate events (`gate.waiting`, `gate.decided`, `thread.done`, `thread.failed`) publish
`project.changed`, so every tab's project waiting counts (the Inbox badge) follow.

### Notifications
```
DELETE /api/notifications/{id}     → { ok }            (404 when missing)
DELETE /api/notifications          → { ok, count }     (clear all)
POST   /api/notifications/read-all → { ok, count }
Notification + thread_id?, step?, done: bool          (migration V6)
SSE    notification.done  { thread_id, ids, read }    (light: every tab gets it)
```
A gate's `review` notification keeps its thread and step. It becomes **done** (and read) when that gate is decided from anywhere:
`POST /api/threads/{tid}/resume` (Flow page, Inbox, MCP `keel_approve_gate`; only notifications older than the resume) or an
engine `gate.decided` event (the run mode, a waived gate); `thread.done` / `thread.failed` mark the rest done (read state kept).

### Web
- **Run › Inbox** (`#/inbox`, no project needed; `pages/Inbox.tsx`): filters (project, kind), one card per item with inline
  actions (approve, send back with a reason, choices, clarify answers, allow/refuse, budget and plan-window choices),
  `Open flow ▸` (switches project, `#/flow`), "Nothing is waiting for you". Nav badge = the sum of `Project.waiting`.
- `components/RunMode.tsx`: `RunModePicker` (Start a flow; default from Settings), `RunModeSwitch` (`<RunModeSwitch pid threadId
  mode onChange? compact?/>`, mounted in the Flow page's summary bar next to Stop), `RunModeNote` (gate card and inbox item:
  the mode, gates keel approved by itself, why this one waits). Settings › Flow and gates › Run mode (new flows).
- Notifications drawer: mark one read, delete one, mark all read, clear all (confirm); decided gates show "✓ decided".


## v0.4.2: project caps

Every cap of a project (Budget › Limits that stop a flow) is real now: **a flow starts with the smallest cap left, and
keel checks it before every agent step** (the engine's `_budget`, as for the Settings cap). Code: api
`budget/CapPlanner.kt` (what is left, the start limits), `flow/FlowService.buildStart`; engine `runtime/compiler.py`
`_budget` + `_step_cap`, `app.py` `Settings`.

### At flow start (api)
For each cap the api computes what is **left now**, then the binding (smallest left) one of each kind becomes the flow's limit:

| scope | unit | left now | sent to the engine as |
|---|---|---|---|
| `flow` | tokens / usd | the limit (each flow gets all of it) | `cap_tokens` / `cap_usd` |
| `day` | tokens / usd | limit − this project's use since 00:00 **UTC** today (`agent_calls.started_at`) | `cap_tokens` / `cap_usd` |
| `api_month` | tokens / usd | limit − this project's **API-key** use since the 1st (UTC) (`agent_calls.mode = 'api'`) | `cap_tokens` / `cap_usd` |
| `step` | tokens | the limit | `step_cap_tokens` (every step; a step's own smaller `max_tokens` still wins) |
| `step` | usd | — | **not checked** (keel counts tokens per step, not dollars); the Budget row says so |

- Tokens everywhere = `tokens_in + tokens_out + tokens_cached / 10` (as `budget_tokens` and the Budget page). Dollars =
  the cost runs report (`cost_usd`).
- `cap_tokens = min(Settings cap_tokens or the request's cap_tokens, every tokens cap left)`; `on_cap` = the action of
  the one that binds (ties: stop before pause before cheaper). `cap_usd` / `on_cap_usd` likewise among the dollar caps
  (none → no dollar cap). `step_cap_tokens` / `step_on_cap` = the smallest step tokens cap.
- **A used-up cap** (day or api_month, left ≤ 0):
  - action `pause` or `stop` → `409 { error, hint }`, nothing starts:
    `error`: `The cap "All flows, per day: 200k tokens" is used up: 212k tokens used today.`
    (month: `… is used up: $25.40 spent on API keys this month.`)
    `hint`: `It resets at 00:00 UTC tomorrow (2026-10-07). Raise or delete the cap in Budget › Limits that stop a flow, or start the flow after the reset.`
    (month: `It resets on the 1st (2026-11-01, 00:00 UTC). …`)
  - action `cheaper` → the flow starts with **every agent on the cheaper model** (`StartThread.models`, `default` included),
    and that cap does not limit it further. Never the fake model for a flow with real models: then it is a 409 that
    says `no real cheaper model is set` (hint: Settings › Cheaper model).
- `POST /api/projects/{pid}/flows` answers the ThreadState **+ `cap_note?: string`** when a cap changed the flow
  ("This flow gets 150k tokens, then pause and ask: what is left today of …", "… is used up …: every agent starts on the
  cheaper model."). The cheaper model's login/key is sent with the start, resume and rewind keys.
- A start **without `cap_tokens`** (the API, MCP `keel_start_flow`, keel2 `/flow`, a KeelBot button) gets what the
  Start-a-flow drawer gives: max(Settings cap, the estimate's `high` × 1.3 rounded up to 10k), the estimate counting the
  `acs` given (none: 3). A raise says so first in `cap_note` ("keel raised this flow's cap from 500k (Settings) to 1.8M:
  the estimate's high end plus 30%."). An explicit `cap_tokens`, a Settings cap of 0 (no cap) or a failed estimate stay
  as they are, and the project caps still bind (the smallest left wins; then the raise is not mentioned). B15 only keel's
  default cap is raised: a `cap_tokens` someone saved in Settings (General or the project) stays, and `cap_note` says it
  is below the estimate ("This flow keeps the cap of 400k saved in Settings, below …").
- A flow gets what is left when it **starts**; two flows started together can each use it.

```
GET /api/projects/{pid}/caps/left → {
  caps: CapLeft[],          // CapLeft = Cap + { used, left, window: "day"|"month"|"flow"|"step", resets_at?: ISO (UTC), checked: bool, note }
  next_flow: FlowLimits     // what a flow started now gets (Settings' cap, no request cap)
}
FlowLimits = { cap_tokens, on_cap, cap_usd?, on_cap_usd?, step_cap_tokens?, step_on_cap?, cheaper: bool,
               tokens_from?, usd_from?, step_from?: "settings"|"flow"|<cap id>, notes: string[],
               refused?: { cap_id, error, hint } }
```
`/budget.caps` names a step dollar cap "… (not checked: keel cannot count dollars per step)".

### Engine
- `StartThread.settings` accepts `cap_usd ≥ 0`, `on_cap_usd`, `step_cap_tokens ≥ 0`, `step_on_cap` (before 0.4.2 an
  unknown field was dropped silently; a bad value is now a 422). `usage.cap_usd` starts at `settings.cap_usd` (0 = none).
- Before each agent step `_budget` checks, in this order: the step's limit (`_step_cap`: its own `max_tokens`, or
  `step_cap_tokens` when that is smaller or the step has none; action = the step's `on_limit` when its own limit binds,
  else `step_on_cap`, else `on_cap`), the token cap (`on_cap`), then the dollar cap: **over when `usage.cost_usd ≥
  usage.cap_usd`** (action `on_cap_usd`, else `on_cap`). A warning (`budget.warn`) at 80 % of the token or dollar cap.
- Actions as before: `cheaper` switches to `_real_cheaper` (never the fake model for a real flow; none → pause and ask),
  `stop` stops, `pause` asks (`waiting.kind: "budget"`, title "Token cap reached" or "Cost cap reached"); approving
  raises the cap that was hit (`payload.cap_tokens` / `payload.cap_usd`, default: used + the old cap).
- `budget.warn` / `budget.stop` data: `{ used, cap, step_used, step_cap, cost_usd, cap_usd, limit: "step"|"token"|"cost", action?, model? }`.
- **Which runs count toward a dollar cap**: every run's reported `cost_usd` — API-key runs (billed; from the provider's
  usage or the model catalog), **and subscription CLI runs that report a cost** (the Claude Code CLI reports
  `total_cost_usd` at API prices although the plan pays it; Codex and Copilot subscription runs report none). Fake runs
  cost 0. So a dollar cap can pause a subscription flow early; use a tokens cap for subscription work. Only `api_month`
  counts API-key runs alone, and only for what was used **before** the flow started.

### Web
- Budget › Limits that stop a flow: "A flow starts with the smallest cap left; keel checks it before every agent step."
  The caps table has **Left now** ("450k left today · 550k used today", "$96.50 left this month", "all of it, every
  flow", "used up · resets tomorrow, 00:00 UTC", "not checked: …") and a line under it with what a flow started now
  gets, or why it cannot start (`next_flow`). The cap drawer explains each scope, and warns that dollars per step are
  not checked.
- Start a flow: a hint "This project's caps apply too (Budget): the smallest one left wins, now at most …"; a refused
  start shows the api's `error` and `hint` in the drawer (it stays open); after a start the toast adds `cap_note`.

### Connections (api)
`GET /api/connections` runs its `--version` checks (and `docker version`) **in parallel** on a pool of 8
(`connections/MachineTools.kt`), each still at most 5 s and cached for a minute, so the answer takes about the slowest
single check instead of their sum (8 checks of 1 s: about 1.4 s instead of 8 s).


## v0.5.0: tasks and Jira

A task is a piece of work for one project: **local** (made in keel) or **jira** (synced from a Jira ticket, or made in
keel with a Jira key). A task starts a flow only when the user presses **Start** (no automatic pick-up); keel then walks
it To do → In progress → In review → Testing (PP) → Ready for production → Done and waits for the user at PP and
release (keel never deploys and never pushes). The work can still start as a plain flow on the Flow page.

### Data (Flyway V7)
```
tasks(id, project_id, title, description, type bug|story|task, status todo|in_progress|in_review|testing_pp|ready_prod|done|cancelled|blocked,
      source local|jira, external_key, external_url, external_status, assignee, priority, thread_id, workflow_id, pr_url,
      reviewers json [{login, on: github|jira, state: wanted|requested|approved|changes_requested|commented|set}], blocked_reason,
      created_at, updated_at)                                   unique (project_id, external_key)
task_events(id, task_id, at, kind, from_status, to_status, note, actor user|keel|jira)
task_inbox(id, task_id, project_id, kind task|jira-manual, stage pp|prod|<Jira status>|reviewers, title, detail, created_at, done_at)
jira_connections(project_id, json JiraSettings, me_json, last_sync_at, last_sync_error, updated_at)
```
The Jira token is a secret (`SecretService`, AES-GCM) named `jira.<project id>`; it is never returned, logged or put in
an error. Event kinds: created, updated, start, start_refused, flow, pr, reviewers, review, approved, confirm, send_back,
status, blocked, note, jira (keel moved the ticket), jira_status (changed in Jira), jira_update, jira_error, jira_manual,
github, github_error.

### Api
```
GET    /api/projects/{pid}/tasks?source=local|jira    → { tasks: Task[], sync: { connected, kind, last_sync_at, last_sync_error, me, poll_minutes } }
POST   /api/projects/{pid}/tasks  { title, description?, type?, external_key?, external_url?, assignee?, priority?, reviewers?: string[] } → Task
POST   /api/projects/{pid}/tasks/sync               → SyncResult { ok, jira, total, created, updated, moved, at, error?, hint?, reviews_checked, reviews_moved }
GET    /api/tasks/{id}                              → Task & { events: TaskEvent[] }      (oldest first)
PUT    /api/tasks/{id}   { title?, description?, type?, external_key?, external_url?, assignee?, priority?, reviewers? } → Task
DELETE /api/tasks/{id}                              → { ok }   (the Jira ticket stays)
POST   /api/tasks/{id}/start   { workflow_id?, run_mode?, allow_dirty?, allow_fake? } → Task
POST   /api/tasks/{id}/confirm { stage: pp|prod, note? }       → Task
POST   /api/tasks/{id}/status  { to, note? }                   → Task   (back to in_progress from review/PP/ready = send back: note needed)
POST   /api/tasks/{id}/pr      { url }                          → Task   (the user pastes the PR link)
POST   /api/inbox/tasks/{item_id}/act { action: confirm|send_back|done, note? } → Task
GET    /api/projects/{pid}/jira                     → JiraView { connected, settings, token_set, token_hint, default_jql, jql, last_sync_at, last_sync_error, me, mcp_server }
PUT    /api/projects/{pid}/jira  JiraSettings + { token? }   → JiraView   (null keeps a value, "" clears it; a blank token keeps the saved one)
DELETE /api/projects/{pid}/jira                     → { ok }   (also the token and the jira-<pid> MCP server)
POST   /api/projects/{pid}/jira/test  [unsaved JiraSettings + token]  → { ok, user?, error?, hint?, kind? }   (GET /rest/api/2/myself)
GET    /api/projects/{pid}/jira/discover?key=       → { statuses, transitions (of that ticket), fields (reviewer candidates), suggested, keel_statuses }
GET    /api/projects/{pid}/mcp-catalog              → CatalogEntry[]   { id, name, about, url, license, command, ready, why, server, added }
POST   /api/projects/{pid}/mcp-catalog/jira         → McpServer       (added turned off)
type Task = { id, project_id, title, description, type, status, source, external_key, external_url, external_status, assignee, priority,
              thread_id, workflow_id, pr_url, reviewers, blocked_reason, created_at, updated_at,
              flow: { thread_id, workflow_id, status, phase, current, title } | null, waiting: TaskItem[] }
type JiraSettings = { kind: cloud|server, base_url, email (cloud), project_key, board_id, jql, status_map: {keel status: Jira status|transition|"-"},
                      reviewer_field, jira_reviewers: string[], github_reviewers: string[], poll_minutes (0 = only Sync now) }
```
- **Start**: the workflow is `workflow_id`, else by type: bug → `fix`, story → `feature`, task → `change`. The flow's title is
  `KEY: title`; its request is title + description + "Jira ticket: KEY (url)"; its data has `ticket: KEY` (0.18.55: every
  keel commit of the flow ends with ` [KEY]`, `feat(AC-2): … [KEY]`, so the ticket scan links them). A refused start (a used-up cap, uncommitted
  files, the fake model, an unknown workflow) keeps the status and records `start_refused` with the api's error and hint;
  the error is returned as it was (409 / 404). A task whose flow runs or waits cannot start another (409).
- **Inbox**: `InboxItem` gains `task: { id, item_id, key, url, title, status, stage, pr_url, actions: [{id, label, needs_note}] }`
  for the kinds `task` (Confirm PP testing, Ship to production: confirm | send_back) and `jira-manual` (done). Their `id` is
  `task-item-<n>`, `thread_id` the task's flow (or ""). `GET /api/inbox/count` and `Project.waiting` count open task items. Opening
  one also creates a `review` notification linked to `/tasks/<id>`; every task change publishes `project.changed`.

### The lifecycle (`api/.../tasks/TaskMachine.kt`, no IO; `TaskService.fire` applies it)

| Trigger | From | To | Jira (when the task has a key) | Inbox |
|---|---|---|---|---|
| Start (user) | todo, in_progress, blocked | in_progress | move to the in_progress status; comment "keel started the <flow> flow… <link>" | — |
| PR opened (engine `step.finished` note "PR opened: <url>", or `pr_url` in the thread state at `thread.done`) or pasted (user) | todo, in_progress, blocked | in_review | move; comment "Pull request: <url>"; set the reviewer field | — (GitHub reviewers asked with a token) |
| Flow done without a PR | in_progress | in_progress | — | — (history: "paste its link") |
| Flow failed / stopped (`thread.failed`, `thread.done {status: stopped}`) | in_progress | blocked (reason) | move only when `blocked` is mapped; comment | — |
| Hand-off (`thread.started` with `data.parent` = the task's thread) | any | same; the task follows the new thread | — | — |
| PR approved (review poll: an APPROVED, no CHANGES_REQUESTED as latest per reviewer) | in_review | testing_pp | move; comment "approved by …" | task/pp "Confirm PP testing for KEY" |
| Changes requested | in_review | in_review | — | — (history) |
| Confirm pp (user) | testing_pp | ready_prod | move; comment "Testing in PP passed." | task/prod "Ship KEY to production" |
| Confirm prod (user) | ready_prod | done | move; comment "Shipped to production." | — |
| Send back (user, note) | in_review, testing_pp, ready_prod | in_progress | move; comment "Sent back from …" | open task items close |
| Move by hand (user) | any (≠ to) | to | move (cancelled/blocked only when mapped); comment = the note; cancelled stops the flow | the item of the new status |
| Changed in Jira (sync, actor jira) | any | the mapped status, else unchanged | nothing (never moved back) | the item of the new status |

- Leaving a status closes the task's open `task` items. A Jira target is the mapping's value, else the usual name (To Do,
  In Progress, In Review, Testing in PP, Ready for Production, Done); `-` = do not move. `transitionTo` reads the ticket's
  status (already there → nothing), then `GET /transitions` and picks the one whose target status, else whose name, matches.
- **No connection, or Jira refused / unreachable** for a task with a key: an Inbox item `jira-manual` "Move KEY to <status>
  in Jira (keel could not: <why>)" (an older one is replaced; a later successful move closes it); **Done** records
  `jira_manual` (actor user) and sets `external_status`. The reviewer field the same way ("Set the reviewers of KEY…").
  A failed comment is only a `jira_error` event. Local tasks without a key go through the same states with no Jira step.
- **GitHub reviewers**: the task's own logins, else the connection's `github_reviewers` (`org/team` → `team_reviewers`);
  `POST /repos/{o}/{r}/pulls/{n}/requested_reviewers` with the stored token (`GITHUB_TOKEN`, else `GH_TOKEN`, else the
  environment). No token: a `github` event says so and nobody is asked. The review poll reads `GET …/pulls/{n}/reviews`.
  GitHub Enterprise PR URLs use `https://<host>/api/v3`; `keel.tasks.github-api` (`KEEL_GITHUB_API`) overrides.
- **Sync** (Sync now, and every `poll_minutes` per connection; the PR reviews every second scheduler tick): issues from the
  board (`/rest/agile/1.0/board/{id}/issue?jql=`) when `board_id` is set, else the search; JQL = `jql`, else
  `assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC` (prefixed `project = KEY AND` when a project
  key is set and no board). Upsert by key (type: Bug → bug, Story/Epic → story, else task; description as text); open tasks
  whose key left the query are read again with `key in (…)`. A status that differs from `external_status` is a `jira_status`
  event; the task moves when the mapping (or a usual name, or the done category) says where. Errors go to `last_sync_error`.
  A sync never writes to Jira.
- Engine events reach the lifecycle through `EventService` → `EngineEventStored` (Spring event) → `TaskEngineEvents`, which
  checks with one indexed query that a task follows the thread and runs the rest on one background worker
  (`keel.tasks.inline-effects` runs it on the event thread in tests). Links in comments use `KEEL_PUBLIC_URL`
  (`keel.tasks.public-url`, default `http://127.0.0.1:8080`) + `/#/tasks/<id>`.

### Jira Cloud vs Server / Data Center (`api/.../jira/JiraClient.kt`, Spring `RestClient`, no SDK)

| | Cloud (`https://<site>.atlassian.net`) | Server / Data Center |
|---|---|---|
| Auth | Basic base64(email:API token) | Bearer personal access token |
| Test | `GET /rest/api/2/myself` (accountId) | the same (name) |
| Search | `GET /rest/api/3/search/jql` (nextPageToken; description in ADF → plain text); 404 → falls back to `/rest/api/2/search` | `GET /rest/api/2/search` (startAt/total) |
| Board | `GET /rest/agile/1.0/board/{id}/issue` | the same |
| Transitions | `GET`/`POST /rest/api/2/issue/{key}/transitions` | the same |
| Comment | `POST /rest/api/2/issue/{key}/comment {body: text}` — **v2 plain text on both** (Cloud's v3 would need ADF) | the same |
| Reviewer field | `PUT /rest/api/2/issue/{key}`; users as `{accountId}` (an email is looked up with `/rest/api/2/user/search?query=`) | users as `{name}` |
| Discovery | `GET /rest/api/2/project/{key}/statuses` (or `/rest/api/2/status`), `GET /rest/api/2/field` | the same |

Errors: 401 "Jira refused the login (401)." with a hint per kind; 403 (and the CAPTCHA case from `X-Seraph-LoginReason`),
404 (what was not found), 400/409/422 with Jira's own `errorMessages`/`errors`, 429, 5xx, and network failures in words
(connection refused, unknown host, timeout, TLS). Every message is cleaned of the token and the Basic credentials. Redirects
are not followed.

### The optional Jira MCP server
Tools › Catalog offers **Jira (mcp-atlassian)** (github.com/sooperset/mcp-atlassian, MIT) for a project with a Jira
connection: `POST /api/projects/{pid}/mcp-catalog/jira` adds `jira-<pid>` = `uvx mcp-atlassian`, **turned off**, env
`JIRA_URL`, `JIRA_USERNAME` + `JIRA_API_TOKEN` (Cloud) or `JIRA_PERSONAL_TOKEN` (Server), `READ_ONLY_MODE=true`,
`JIRA_PROJECTS_FILTER=<key>`. MCP env values `secret:<name>` are resolved by `McpService` only when a flow or a test starts
the server, so `GET /api/mcp-servers` never shows the token. To use it: turn it on, add it to Settings › MCP servers,
allow it per agent. Saving the connection refreshes its env; deleting the connection deletes it. keel itself never needs it.

### Engine
`ThreadState` gains `pr_url` (from `data.pr_url`, which `open_pr` sets when it opened the PR).

### 0.18.52 task automations (docs/plans/task-automations.md)
keel proposes, the person approves. Additive:
- `GET|PUT /api/projects/{pid}/tasks/automations`: `{rules: {in_progress|in_review|blocked: {mode: off|propose|start, run_mode,
  cap_tokens}}, keelbot: {account, label}, allow_reply, daily_auto_starts, pick_cap_tokens}`; the view adds `statuses`,
  `modes`, `review_watch` (Code Review is on), `tracker` (Jira is connected), `auto_starts_today`.
- `POST /api/tasks/{id}/pick` ("Pick a flow for me") and `POST /api/tasks/{id}/review` ("Review it"): open an Inbox item of
  kind `task-proposal` (data: workflow_id, why, title, request, questions, kind flow | review); never start anything.
- `POST /api/inbox/tasks/{item}/act`: kind `task-proposal` takes `start | other (workflow_id) | later` with `answers`
  and `allow_dirty`; kind `task-review` (a KeelBot-owned ticket's new PR comment) takes `reply (note = edited text) |
  address | handle`. Inbox items of these kinds carry `payload` (the data) and `questions` (clarify's shape).
- `POST /api/tasks/{id}/start` gains `title`, `request`, `cap_tokens`; `POST /api/projects/{pid}/tasks/sync` gains
  `comments_checked`; `GET /api/projects/{pid}/jira/users?q=` (the KeelBot account picker).
- A Jira move seen by the sync (never keel's own: it sets `external_status` when it moves the ticket) runs the status's
  rule; a review flow runs read-only (`own_branch` off) and never changes the task's status (`task_runs`).
- engine `POST /tasks/pick`: one no-tools model call (`runtime/task_pick.py`), the default flow without a model.
- api SDK `keel.api.flow.PrCommentJudge` (the Code Review plugin's bean): threads, the cheap check, reply, address.
- core migration `V24__task_automations.sql`: `task_automations`, `task_watch`, `task_runs`, `task_inbox.data`.

### Web
- **Run › Tasks** (the Tasks plugin: `plugins/tasks/web/Tasks.tsx`, `tasksApi.ts`): the board (To do, In progress, In review, Testing (PP), Ready, Done; Blocked
  when a task is blocked; cancelled under Done), cards (key or "local", type, assignee, flow status, PR, reviewers, "needs
  you"), Mine / All, source and text filters, Sync now and the last sync (or "Connect Jira"), New task, and the task drawer
  (status, Jira link and status, flow with Open flow, PR, reviewers, its Inbox items with their buttons, Start flow with the
  workflow by type and the run mode, PR link, Mark approved, Confirm PP, Ship, Send back with a reason, Cancel, Reopen,
  Delete, the history). `#/tasks/<id>` opens a task (and switches to its project).
- **Inbox**: task items with their buttons and "Open task ▸". **Connections › Jira** (the Jira plugin: `plugins/jira/web/JiraCard.tsx`): a card per
  project (Cloud / Server, URL, email, token, project key, board, poll, JQL, Test, Connect/Save/Remove; when connected: the
  status mapping found from Jira with a select per keel status, the reviewer field, Jira and GitHub reviewers).
  **Tools › Catalog**: the optional Jira MCP server.

## v0.5.2: the budget bar

A bar on top of every page (`web/src/components/BudgetBar.tsx`) shows the picked project's budget at a glance. It reads
only the api database, so the web may ask for it often.

```
GET /api/projects/{pid}/budget/now → {
  today: Spend,                 // this project since 00:00 UTC
  month: Spend,                 // this project since the 1st (UTC)
  flows: FlowSpend[],           // its running and waiting flows, newest first, at most 5
  caps: CapLeft[],              // its day and month caps that keel checks (as in /caps/left)
  all_today: Spend,             // 0.18.55 every project together since 00:00 UTC (keel Product's own runs too)
  all_month: Spend              // 0.18.55 every project together since the 1st (UTC)
}
Spend     = { tokens, cost_usd }   // tokens = in + out + a tenth of cached input; dollars at API prices
FlowSpend = { thread_id, title, status, tokens, cost_usd, cap_tokens: number|null, cap_usd: number|null }
```
- A flow's `tokens` and `cost_usd` are the larger of its finished agent calls and the engine's own count in the stored
  thread state (the engine's count is what the flow's cap is checked against). A cap of 0 is `null` (no cap).
- 404 for an unknown project.

### Web
- The bar is drawn on every page from the first moment (a page that measures its own top is never pushed down): Today and
  This month (tokens, dollars, and the fullest day or month cap with a meter), the busiest running flow against its cap
  (`+N` when more run) and each set-up provider's fullest plan window. Each part links to Budget (the flow part to Flow);
  the tooltips hold the details (every cap, every flow, each provider's source and age).
- It turns amber when anything in it reaches 80% and red at 95%, the same thresholds as the provider cards.
- It reloads on live events (at most once every 5 s) and once a minute. On a desktop it sticks to the top (41px, the CSS
  variable `--bar-h`, which sticky page parts add to their `top`); on a phone it sits under the header and scrolls away.
- The sidebar no longer has the compact provider cards: the bar shows the same windows.

## v0.5.3: the Graph page

**The code graph since step 3** is the plugin folder `plugins/graph` (id `graph`): engine `keel_plugin_graph` (its
`codegraph.py`, `codegraph_view.py`, `graph_hints.py` and the hooks on_scan, on_commit, on_thread_start, mcp_specs,
prompt_context, index_available), api `keel-plugin-graph.jar` (`keel.api.graph`: `/graph`, `/graph/search`,
`/graph/node`, `/index`, `/index/rebuild` and the `index.done` notification), web `plugins/graph/web` (the page and its
stylesheet). The urls and the JSON are the same. The project scan and its `project_index` row, each agent's
`code_graph` setting and the CodeGraph CLI in the image stay keel's.

**Project › Graph** (`web/src/pages/Graph.tsx`, `components/graph/*`) draws the code graph keel keeps for agents (the
CodeGraph index, `engine/keel_engine/tools/codegraph.py`). The engine reads it read only and rolls it up
(`engine/keel_engine/runtime/codegraph_view.py`):

- a **unit** is a top-level class, interface, enum, function, type or constant; methods, fields and nested classes
  count for the unit that holds them, code at the top of a file for the file
- a **group** is the unit's Java/Kotlin package (the file's package line, without what every package shares), else
  its folder; `path` is the segments the web folds by depth
- a **use** is a call, a creation (instantiates), implements, extends or a reference; imports are left out

```
GET /api/projects/{pid}/graph → GraphOverview
  { available: true, status, indexed_at, counts: { files, symbols, units, links, uses },
    groups: { id, kind: "package"|"folder", name, label, path: string[] }[],
    units:  { id, name, kind, group, file, line, members }[],
    links:  { from, to, n, k: { calls?, instantiates?, implements?, extends?, references? } }[] }   // from uses to
  | { available: false, status: "indexing"|"missing"|"failed"|"engine", reason }

GET /api/projects/{pid}/graph/search?q=… → { available, results: { id, name, kind, file, line, unit, group }[] }
    up to 30; exact names first, units before members; a member's name is Class.member

GET /api/projects/{pid}/graph/node?id=…&depth=1|2 → GraphFocus
  { available: true, level: "unit"|"member", depth,
    focus: { id, name, kind, qualified, signature, docstring, file, line, end_line, group,
             unit: { id, name, kind } | null, members: { id, name, kind, line, in, out }[] },
    nodes: { id, name, kind, unit, group, file, line, col }[],     // col -2/-1 use it, 1/2 it uses
    edges: { from, to, n, k, sites: { file, line }[] }[],          // from uses to; a cycle keeps its box on one side
    more: { "-1": n, "1": n, ... },                                // boxes left out (24 per column at most)
    impact, impact_capped }                                        // what reaches it through uses, any number of steps
  | { available: true, missing } | { available: false, ... }
```
- The API sends `q` and `id` on to the engine as JSON (`POST /projects/{pid}/graph/search|node`); `depth` is 1 or 2;
  no `id` is a 400; the engine down answers `available: false, status: "engine"`.
- The engine caches the rolled-up graph per index file and reads it again when the file (or its WAL) changes.
- A unit's neighbours are units (its members' uses count for it); a member's are symbols. Doc comments come as plain
  text (no comment marks, HTML tags or `{@link x}` braces).

### Web
- `#/graph` packages (folded to a depth that fits, at most 24 boxes, unless chosen; tests hidden unless asked; both
  remembered per project), `#/graph/in:<p:|f:><label>` one package's units with a grey box per package they touch,
  `#/graph/<symbol id>` one symbol in the middle (1 or 2 steps) with a panel: kind, file (into the Repo page), impact,
  doc, Used by / Uses with the first place of each use, members.
- Lines run from what is used to the user in the layout, so users sit left and what they use right, and every arrow
  points at what is used; a line is thicker for more uses. Line style by kind: calls solid, creates dashed,
  implements / extends dotted, refers to faint.
- The box diagram (`components/er/BoxDiagram.tsx`) takes `open` (a box's way in), `weight` and `tip` on a line, and a
  canvas `label`; the Map draws as before.

## v0.6.0: the Helper

A chat in the Repo page (⌘I, or the Helper button in the activity bar) with an agent that reads the project and answers
with `file:line` links. keel runs it with its own harness (the KeelBot plugin's engine,
`plugins/keelbot/engine/keel_plugin_keelbot/helper.py`): one answer is one agent run on the model's runner (claude, codex,
copilot / opencode, an API key, or the fake model), with keel's guarded tools, the MCP servers the `helper` agent may use
plus the code graph, the guard context the hook reads on every tool call, and the diff guard as the backstop for engines
without a hook.

- **Modes**: `ask` (read only: the guard's readonly; codex runs in its read-only sandbox, copilot without its write and
  shell tools; anything that still changes is put back). More modes come in later releases.
- **Sessions continue**: claude and codex continue their own CLI session (the first turn of a claude session pins
  `--session-id`, the next ones `--resume`); the API-key runner gets the earlier messages; other CLIs get the conversation
  so far in the prompt (`TRANSCRIPT_CHARS`).
- **The prompt** carries the mode, the knowledge sections the `helper` agent uses
  (`plugins/keelbot/content/agents/helper.md`), the plugins' context files, the project's running or waiting flow (title, phase, spec, criteria, the waiting gate), what
  the person points at (`@` mentions, selected lines, the open file), and the question.
- **Plugins** (`engine/keel_engine/runtime/plugins.py`): `content/plugins/<name>/plugin.yml` (keel's, and since
  step 3 the same folder in a loaded plugin package's content, like `plugins/ci/content/plugins/ci`; listed together in
  name order) and
  `<project>/.keel/plugins/<name>/plugin.yml` (the project's; a command with the same name replaces keel's):
  `commands` (`/name` sends the template; `{{args}}` is the rest of the line) and `context` (project files every prompt
  names). A broken plugin is listed with its problems and never used.

```
GET    /api/projects/{pid}/helper/sessions                → HelperSession[] (newest first)
POST   /api/projects/{pid}/helper/sessions                {mode?: "ask", model?: Model, title?} → HelperSession
GET    /api/projects/{pid}/helper/sessions/{sid}          → HelperSession & {messages, busy}   (404 for another project's)
PATCH  /api/projects/{pid}/helper/sessions/{sid}          {title?, model?, folder?} → HelperSession   (another provider starts its CLI session fresh;
                                                          folder: a folder id of this project, "" = no folder; moving keeps updated_at)
DELETE /api/projects/{pid}/helper/sessions/{sid}          → {ok}
GET    /api/projects/{pid}/helper/folders                 → HelperFolder[] (by name)                         v0.15.2
POST   /api/projects/{pid}/helper/folders                 {name} → HelperFolder   (400 no name or > 60 characters, 409 the name exists in any case)
PATCH  /api/projects/{pid}/helper/folders/{fid}           {name} → HelperFolder   (404 for another project's)
DELETE /api/projects/{pid}/helper/folders/{fid}           → {ok, moved}   (its chats are kept, with no folder)
POST   /api/projects/{pid}/helper/sessions/{sid}/turn     {text, model?, mentions?, selection?, open_file?} → {session, call_id, n, command}
POST   /api/projects/{pid}/helper/sessions/{sid}/stop     → HelperSession
GET    /api/projects/{pid}/helper/commands                → {name, description, plugin, source: keel|project}[]

HelperSession = {id, project, root, mode, title, model, status: idle|running|failed, error, thread_id, tokens_in, tokens_out,
                 tokens_cached, cost_usd, turns, created_at, updated_at, folder}
HelperFolder  = {id, project, name, chats, created_at, updated_at}      (engine table helper_folders; helper_sessions.folder)
HelperMessage = {n, role: user|helper|note, text, call_id, data: {status, provider, model, tokens_in, tokens_out, tokens_cached,
                 cost_usd, ms, command?, mentions?, selection?}, at}
HelperMention = {kind: file|symbol|ac, value, file?, line?}      HelperSelection = {path, from?, to?, text}
```
- A turn answers at once; the answer comes as events with `thread_id` = the session id and `step` "helper":
  `helper.started` (agent, provider, model, mode, phase `helper-<mode>`), `helper.step` (an agent step), `helper.finished`
  (status done|stopped|failed, tokens, cost, result). The api stores them as an agent call (agent `helper`, so the budget,
  Live agents and Jobs count it) and its steps, never as a flow, and sends no "failed" notification for them.
- The api adds to every turn: the logins of the session's model, the MCP servers and the `helper` agent's MCP allow list,
  the `helper` agent's knowledge and skills (Agents page), and the project's running or waiting flow.
- One answer at a time per session (409 while one runs); `stop` cancels it at any moment and leaves a `note` message.

### Web
- `components/helper/HelperPanel.tsx` in `pages/repo/Ide.tsx` (since step 3 the Code plugin's `plugins/code/web/Ide.tsx`,
  through the slot `assistant`; a column on the right; a phone shows it over the page):
  the chat list, the model, what the chat used, the answers (Markdown; `file:line` chips open the editor there), each
  answer's steps (live while it runs from `helper.step`, stored ones on demand from `/jobs/{call_id}/steps`), the open
  file and the selected lines that go along, `/` commands and `@` mentions (criteria of the flow, code-graph symbols,
  files), Stop. While an answer runs the panel reads the session again on every live tick and every 5 s, so a missed
  `helper.finished` (a hidden tab pauses the event stream) never leaves it "working".
- The editor's toolbar has **Ask** (the selected lines, or the file); ⌘I opens the Helper with the selection, and closes it
  when nothing is selected.
- v0.15.2 chats: leaving the page (or closing the panel) and coming back keeps the open chat (`keel2.helper.<pid>.session`),
  each chat's draft with its mentions (`keel2.helper.<pid>.drafts`, localStorage) and where it was scrolled to, the new
  chat's mode and whether the list is open (`keel2.helper.<pid>.tab`, sessionStorage); a chat is forgotten only on a 404.
  **Chats** (`components/helper/ChatList.tsx`, on the plugin track `plugins/keelbot/web/ChatList.tsx`; the folder
  routes are the KeelBot plugin's, engine `keel_plugin_keelbot/routes.py` and api `keel.api.helper`, while the table
  `helper_folders` stays in core's `runtime/migrate.py`; a column on `#/keelbot` of a wide screen, over the conversation in the
  panel) lists every chat by folder with search, new / rename / delete folder, and per chat rename, move to a folder and
  delete; every delete asks in the page, never `window.confirm`. A new Ask chat shows how to use KeelBot.
- v0.15.2 new answers (`components/helper/unread.tsx`; on the plugin track `plugins/keelbot/web/unread.tsx`, through the
  slots `nav.badge`, `shell.watch`, `notes.setting`, `settings.browser` and the assistant's `count`, so core never imports
  KeelBot): a `helper.finished` (not `stopped`) for a chat nobody looks at (no
  panel shows it, or the tab is hidden) counts as new (`keel2.keelbot.unread`: project → chat → call ids) on the menu's
  KeelBot entry, its folded-menu icon, the Code page's KeelBot button and the chat's row; showing the chat clears it.
  It also plays KeelBot's own sound (`notify.playKeelBot`: three short rising sine notes), unless Do not disturb is on or
  the switch in Settings › This browser (or the notification settings) is off (`keel2.keelbot.sound`, on at first).
- More room (v0.8.x): the Helper's column has a drag edge (300 px up to all but 360 px of the IDE, remembered as
  `keel2.repo.helper.w`); **Focus** hides the Repo page head (`keel2.repo.focus`, the status bar brings it back);
  `#/helper` (menu: Project › Helper, **Helper only**, ⤢ in the panel) shows the Helper alone in one wide column
  (`HelperPanel layout="page"`), and its `file:line` links open `#/repo/<path>:<line>`.
- Markdown (web `components/Markdown.tsx`): a fence closes only on a plain fence of the same kind at least as long,
  and `<details><summary>` is a fold-out with Markdown inside. Gate texts render as Markdown on the Flow page and in
  the Inbox. The engine wraps text it did not write in a fence longer than any inside it (`runtime/tools.fenced`), so
  a spec extract with its own code block no longer turns the rest of a PR body into code.


## v0.6.x: the Helper's Fix mode (fix at a gate)

While a flow waits at a gate, a **Fix** chat lets the Helper change files, inside the same rules as the flow:

- **Guard**: the ToolBox and the hook use the waiting flow's criterion (lane and layer), unlocks and a phase, so a file
  that phase freezes stays frozen. The diff guard checks the files after every turn, as for a flow step.
- **Which phase**: a gate's own phase often lets only notes change (the AC gate's `gate`), yet a fix there is the work
  under review. So the chat works in the nearest phase, from the flow's own back through the steps before the waiting
  one (the api sends `phases_before`, nearest first), whose rules let code change: at the AC gate `green`, at the spec
  gate `spec` (`helper.fix_phase`, `rules.edits_code`). The session stores it as `phase`; Done commits with that
  phase's commit type. A file outside it can still be unlocked for the flow (Repo page).
- **Permission cards**: a shell command that changes something (`run_mode.readonly_bash` says it is not read-only)
  waits for the person. The PreToolUse hook (claude) and keel's ToolBox (API-key models) call
  `POST /helper/permissions/ask` on the engine with the turn's own ask key (it can only ask; it cannot answer), and wait
  up to 10 minutes (`permissions.ASK_TIMEOUT`; the claude hook timeout is longer). The person answers in the panel or
  the Inbox: **Allow once**, **Always** (this command, for the rest of the chat; stored in the session's `grants`, a
  `prefix *` grant matches the prefix) or **Deny** (with an optional reason the agent reads). No answer is a deny.
  Codex and Copilot have no hook: their commands run in their own sandbox, and the diff guard still checks the files.
- **Changes and Undo**: before its first change to a file the Helper's session keeps the file's earlier content
  (`helper_files`), so the panel lists every changed file against what it was (+/- lines, diff), and Undo puts one
  file (or all) back.
- **Done**: runs the module's test command (`testcmd.command_for`), then keel's commit of only those files
  (`actions.commit`: the phase's commit rules, secrets, new dependencies, pre-commit tools), leaves a `note` message and
  emits `helper.commit` on the flow's thread. The commit reads `<type>(<the gate's criterion, else helper>): <message>`,
  the type from the chat's phase (at the AC gate `feat(AC-2): …`). The flow's PR body lists these commits under "Helper changes"
  (`helper.commits_for`). A failure says which step failed (`changes`, `checks`, `commit`) with the
  output; the panel can hand it back to the Helper.
- Fix needs a waiting flow that does not run read-only (409 otherwise), and Done refuses once that flow moved on.

```
POST   /api/projects/{pid}/helper/sessions                {mode: "fix", model?, title?} → HelperSession (thread_id = the waiting flow)
GET    /api/projects/{pid}/helper/sessions/{sid}/changes  → HelperChange[]
POST   /api/projects/{pid}/helper/sessions/{sid}/undo     {path?} → HelperChange[]   (no path: every file)
POST   /api/projects/{pid}/helper/sessions/{sid}/done     {message?} → HelperDone   (message: the commit's subject; else the chat's title)
GET    /api/projects/{pid}/helper/permissions             → HelperQuestion[]   (the commands that wait now)
POST   /api/projects/{pid}/helper/permissions/{qid}       {decision: once|always|deny, why?} → {id, decision}

HelperSession  += {phase (fix: the phase it works in), grants (fix: the "Always" commands)}
HelperChange   = {path, status: added|modified|deleted, added, removed, diff}
HelperDone     = {ok: true, sha, message, files, checks} | {ok: false, step: changes|checks|commit, error, command?, output?}
HelperQuestion = {id, session, project, thread_id, kind, command, path, title, at}
engine only:   POST /helper/permissions/ask {session, key, kind, command, path} → {decision: allow|deny, why}   (no token; the key)
```
- Events: `helper.permission` (a command waits; the api adds a notification and the Inbox lists it with kind
  `permission` and `permission: {id, session, command, path}`), `helper.permission.answered` (the notification is
  done), `helper.commit` (on the flow's thread: session, sha, message, files, checks).
- Web: the panel's **Ask | Fix** switch (Fix is on while a flow waits; switching starts a new chat), a bar with the gate
  and phase (and a warning once the flow moved on), the permission cards, **Changed files** (Diff, Undo, Undo all, the
  file name opens the editor's Changes view) and **Done: run the checks and commit**. The Inbox shows the same
  permission card (Allow once, Always for this command, Deny with a reason).


## v0.7.0: Helper side sessions (their own worktree)

A **Side** chat works in its own copy of the project: a git worktree at `<project>/.keel/worktrees/helper-<id>` on a new
branch `keel/helper/<id>` from the project folder's HEAD (`engine/keel_engine/tools/worktrees.py`). It runs at any time,
also while a flow runs in the project folder, and neither touches the other's files:

- The worktree is excluded from the project's git status (`.git/info/exclude`), and the Repo page does not walk into it.
- Edits: only keel's always-on rules (secrets, `.git`, existing migrations); nothing outside the worktree (the ToolBox
  and, with `confine` in the guard context, the hook refuse it); the diff guard checks every turn. Commands that change
  something wait for the person, as in Fix mode.
- **Changes** are what is not kept yet (the worktree against the last commit on its branch); Undo puts a file back.
- **Keep** (`done` without a flow): the checks run in the worktree, then keel commits on the side branch
  (`fix(helper): <message>`); the project folder's branch does not move.
- Hand over:
  - **Make a task**: a task whose description says what was asked, the commits on the branch, and the last answer.
  - **Start a flow on the branch**: only with everything kept, no flow running or waiting in the project folder, and a
    clean folder. The worktree goes (`release`; the branch stays), the folder checks the branch out, and a `change`
    flow starts there (its preflight stays on a non-base branch): it writes the tests for what the branch does, and a
    test that passes at once is "already met".
  - **Throw away** (delete the chat): the worktree and its branch go.
- Dependencies are not installed in a new worktree (node_modules, a virtualenv): the Helper can run the install
  command (it asks first).

```
POST   /api/projects/{pid}/helper/sessions                {mode: "side"} → HelperSession (worktree, branch, base_sha)
POST   /api/projects/{pid}/helper/sessions/{sid}/done     {message?} → HelperDone   (Keep: commits on the side branch)
GET    /api/projects/{pid}/helper/sessions/{sid}/handover → HelperHandover
POST   /api/projects/{pid}/helper/sessions/{sid}/task     {title?, type?} → TaskView
POST   /api/projects/{pid}/helper/sessions/{sid}/flow     {title?, workflow_id? = "change"} → ThreadState   (409 when it cannot)
DELETE /api/projects/{pid}/helper/sessions/{sid}          → {ok}   (a side session: its worktree and branch go)

HelperSession  += {worktree (null once handed over), branch, base_sha}
HelperHandover = {session, title, branch, base, worktree, commits: [{sha, subject}], uncommitted: [path], asked: [text], answer}
engine only:   POST /helper/sessions/{sid}/release → HelperHandover   (the worktree goes, the branch stays; 409 with unkept files)
```
- Web: the panel's **Ask | Fix | Side** switch; a Side chat shows its branch, what is kept on it, **Make a task**,
  **Start a flow on the branch** (after a confirm) and **Throw away**; its Changed files box has **Keep** instead of
  Done, and a file name shows its diff (the file is not in the project folder).


## v0.7.x: several flows at once (each in its own worktree)

A project can run several flows at the same time. One works in the project folder (as before); every other one works
in a git worktree of its own at `<project>/.keel/worktrees/flow-<slug>-<id>` on its own branch (the branch pattern,
`feat/<slug>`), made from the **base branch** (main or master), not from what the folder has checked out.

- **Where a flow starts** (`where` on `POST /api/projects/{pid}/flows`): `auto` (default) = the project folder when no
  flow runs or waits there, else a worktree; `folder` (409 while the folder is busy); `worktree`. A worktree flow skips
  the folder's dirty check and branch switch. Tasks' Start uses `auto`, so three tasks start three flows side by side.
- **The folder flow's branch** (`branch` on the same call, 0.18.55): `new` = its own branch, made from the
  base branch also when the folder is on another branch (what init or another flow left checked out); `continue` =
  stay on the branch the folder is on (Start a flow's "Continue <branch>"; on the base branch it still gets its own).
  No `branch` (0.18.57, B7): `continue` for the flows that finish the folder's branch (ship, cover, review, lint,
  address, and a flow `based_on` one of them), `new` for every other flow. A new branch (and a worktree's) starts from
  `origin/<base>` when the local base is only behind it; a local base with commits of its own on both sides is a 409
  (pull or rebase it first). Nothing is fetched.
  Parts that put the branch there themselves (CI fix, review address) keep it. An init that ends (done, failed,
  stopped) with no commit on its branch puts the folder back on the base branch and drops that branch.
- The api keeps `threads.worktree` (its folder name) and `threads.branch` (V8 migration); every resume, rewind and
  restart sends the engine that thread's folder (`rootNow`). A flow a flow hands over to (change → feature) inherits
  its parent's worktree (`thread.started` with `data.parent`).
- "The project's flow" (`GET /flow`, the project's phase, the Repo page's frozen marks, unlocks, the Helper's Fix
  mode) is the project folder's flow. Flows in worktrees are reached by id.
- A finished flow keeps its worktree until the person removes it (its branch, commits and PR stay).
- The engine makes and removes the worktrees (`tools/worktrees.py`): `POST /worktrees {root, name, branch, start}`,
  `POST /worktrees/remove {root, name, branch?}` (409 for an existing branch, 400 for a name keel does not make).

```
GET    /api/projects/{pid}/flows                → FlowBoard
GET    /api/projects/{pid}/flows/{tid}          → FlowView   (one flow of the project, wherever it runs)
POST   /api/projects/{pid}/flows                {…, where?: auto|folder|worktree} → ThreadState
POST   /api/threads/{tid}/worktree/remove       → {ok, worktree}   (409 while it runs or another flow works there)

FlowBoard = {flows: BoardFlow[], overlaps: [{file, flows: [tid]}], conflicts: [{a, b, files}], order: [tid]}
BoardFlow = {thread_id, title, workflow_id, status, phase, current, waiting, where: folder|worktree, worktree, branch,
             files (changed against the base branch: committed or not), updated_at, worktree_left}
Project  += {flows}   (flows that run or wait, folder and worktrees)
```
- **overlaps**: files two or more running or waiting flows change. **conflicts**: pairs of branches that do not merge
  cleanly (`git merge-tree --write-tree`, git 2.38+), with the files. **order**: fewest conflicts first, then the
  smaller change.
- Web: the Flow page shows the board once a flow runs in a worktree or two flows exist (cards open `#/flow/<tid>`;
  **Remove the worktree** for a finished one); **Start another flow** next to Stop; the Start drawer's **Where it
  runs** (the folder option is off while a flow runs there); the Inbox puts each flow's items under its own heading
  when several flows of one project wait.


## v0.8.0: quality runs

keel runs its flows on small eval projects with the models you pick and scores every run, so a change to a prompt,
an agent, a skill or a model shows up as a drop before it ships.

- **Eval sets**: `content/evals/<name>/eval.yml` (name, description, `project`: a folder there or `demo`, cases:
  `id`, `title`, `workflow` (default change), `request`, optional `acs`, `cap_tokens` (default: twice the flow's estimate)). keel ships
  `shop-js` (JavaScript, `node --test`) and `scores-py` (the demo, pytest). A broken set is listed with its problems
  and never run. Engine: `GET /evals`, `POST /evals/prepare {name, dest}` (a fresh git repo with the project on main;
  only under `$KEEL_DATA/evals`).
- **A run** = every case of the chosen sets × the chosen flows × the chosen models (1-4), one case at a time. Each
  case: a fresh copy of its project as a **hidden project** (`projects.hidden`, V9: no list, Inbox or notification
  shows it), its flow in run mode **auto** (it approves what it can and never opens a PR) with the chosen model for
  every agent (`FlowService.start(model = …)`) and the case's cap (on_cap stop). It ends at the PR gate or done (the
  end), at another gate (stuck: something only a person answers), at its token cap (cap), failed, refused (it did not
  start), or after 30 minutes (timeout); keel stops what still runs, then deletes the copy (the results stay).
- **Score** 0-100 = the result (end 60; stuck, cap, timeout or stopped 40 × how far it got; failed 20 × how far it got)
  + send-backs (20, 5 less for each "reject" in its gate log) + tokens (20 within the estimate, 0 at twice the
  estimate or more, a line in between; 10 without an estimate). A flow × model's score in a run is its cases' mean.
- **Drop**: a flow × model whose score fell 15 points or more since the run before.
- **Nightly**: once a day at or after the time (UTC) when no run is on, with the schedule's flows and models.
- A run that keel's restart cut off is marked stopped ("keel restarted").

```
GET  /api/quality                     → {sets: EvalSet[], active: QualityRun|null, runs: QualityRun[] (last 20), lines: QualityLine[], schedule}
POST /api/quality/runs                {flows: [workflow], models: [Model], sets?: [name]} → QualityRun   (409 while one is on)
GET  /api/quality/runs/{id}           → QualityRun
POST /api/quality/runs/{id}/stop      → QualityRun   (it stops after the case that runs now)
PUT  /api/quality/schedule            {enabled, at: "HH:MM" UTC, flows, models} → QualitySchedule

QualityRun   = {id, status: queued|running|done|stopped|failed, trigger: manual|nightly, flows, models, sets, created_at,
                started_at, ended_at, error, scores: QualityScore[], cases: QualityCase[]}
QualityCase  = {id, n, eval_set, case_id, title, workflow_id, model, status, outcome, reached, project_id, thread_id, score,
                tokens, cost_usd, ms, sendbacks, estimate, progress, started_at, ended_at}
QualityScore = {workflow_id, model, score, cases, reached_end, tokens, ms}
QualityLine  = {workflow_id, model, points: [{run_id, at, score}] (oldest first, last 12), last, previous, drop}
```
- Web: **Build › Quality**: the scores by flow and model (score, change, trend line, a red "dropped"), a banner for a
  drop, the run now with its cases (Stop the run), **Run the eval cases** (flows, eval sets, one or two models, Run
  now, Every night at … UTC), and the last runs with their cases.

## v0.8.x: fewer tokens per agent turn

Measured on a ludus clone (Helper Ask, Claude Haiku, 3 questions × 2 runs each way, tokens as the budget counts them:
in + out + cached ÷ 10):

- Every turn of a claude agent re-sends its whole prompt. The built-in tool descriptions of the Claude CLI were about
  17,700 tokens of it, also for tools keel never lets agents use. The runner now passes `--tools` with only keel's
  built-in tools (`Read, Edit, Write, Bash, Glob, Grep`; a read-only run such as the Helper's Ask gets no Edit or
  Write), so a turn's fixed part fell from about 25k to 7k tokens. With the prompts' "read the places you need
  together, in one turn" (several tool calls at once), the 12 answers took 303k tokens instead of 630k (−52%), with
  the same answers.
- The same for the other engines: Copilot runs with `--disable-builtin-mcps` (GitHub's own MCP servers are not loaded;
  keel opens PRs itself), and each opencode run gets an `opencode.json` in its config folder that turns off the
  built-in tools a step never needs (web fetch and search, to-do lists, sub-tasks, skills; a read-only run also write,
  edit and patch).
- 0.18.20 web research: an agent whose file names `WebSearch` / `WebFetch` in `tools:` (the built-in
  **web-researcher**, `runtime/prompts.py` `web_tools`) gets them in any phase, because they write nothing: Claude
  Code's `--tools` and `--allowedTools` list them and `--disallowedTools` leaves them out; opencode keeps `webfetch` and
  `websearch` on. keel's hook never sees them (its matcher). Codex, Copilot and API-key mode have no web tools from
  keel: the agent reads the web with a read-only `curl -sL` through its shell (DuckDuckGo's HTML search, raw GitHub
  files), which the guard allows in every phase; in a read-only run `curl -o` / `wget` into a file is refused.
- Where to look (`runtime/graph_hints.py`, an agent's `knowledge.hints`): keel looks up the names in a question or a
  step in the code graph itself (no model, no tool call) and lists the line ranges to read first, with who uses them
  and what they call. It saved no tokens in the same measurement (153k with, 150k without) and the answers named the
  same code, so it is **off by default**; turn it on per agent in the Agents drawer (**Where to look**).
- The menu (web `components/Shell.tsx`): on a big screen **Hide the menu** (‹ next to the bell) folds it into a thin
  strip with ☰ and the bell; ⌘\ toggles it; remembered as `keel2.nav.hidden`. A phone keeps its own bar.

## v0.8.x: keel's commits are by you, with KeelBot as co-author

- Settings › Git (general and per project): `commit_coauthor` (bool, default `true`) and `commit_author` (string,
  `Name <email>`, default empty; anything else is a 400 with an example). Flows get both in `StartThread.settings`
  (`commit_author` only when set); the Helper's Done/Keep sends them as `commit: { commit_author?, commit_coauthor }`
  to `POST /helper/sessions/{sid}/done`.
- Who a keel commit is by (engine `runtime/actions.py` `commit_ident`, used by the commit action, the Helper's commits
  and the hunt report): `commit_author`, else `.keel/config.yml` `commit.author_name` / `author_email` (KeelBot's own
  address there counts as unset: older templates wrote it), else the project's git name (`git config user.name` /
  `user.email`; the Docker image's placeholder `keel <keel@localhost>` counts as unset), else
  `KeelBot <keel.dev.bot@gmail.com>`.
- With `commit_coauthor` (missing = on) the message ends with `Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>`,
  except when KeelBot is the author already.
- The Repo page counts a commit as keel's (`keel: true`) when KeelBot or the configured keel author made it, or when
  it names KeelBot in a `Co-Authored-By` line.

## v0.9.0: KeelBot, workflow folders, the flows' history

- **Names**: keel's Helper is now **KeelBot** and the Repo page is the **Code** page, in everything people see. Ids and
  paths stay (`helper` agent, screen ids `helper` / `repo`, `/api/projects/{pid}/helper/...`); `#/keelbot` and
  `#/code` open the same pages as `#/helper` and `#/repo`.
- **KeelBot's view of keel**: every turn (`POST /helper/sessions/{sid}/turn`) carries
  `keel: { workflows: [{id, name, source, based_on, folder, last_run?: {title, status}, steps: [{id, kind, name, agent}]}],
  flows: [{thread_id, title, workflow, status, phase, step, waiting?: {title}, acs_done, acs_total, tokens, where, branch,
  error, updated_at}] }` (the 8 newest flows). The engine (the KeelBot plugin's `keel_plugin_keelbot/keelbot.py`) puts
  them in the prompt with what each keel template is for, and how to give buttons; a question about writing a workflow also gets the format, the phases,
  the code-step actions and the agents.
- **Buttons**: KeelBot never starts or saves anything. Its answer ends with fenced blocks the web turns into cards
  (`components/helper/Actions.tsx`): ` ```keel-start ` with JSON `{workflow, title, request}` (Start the flow →
  `POST /api/projects/{pid}/flows`), and ` ```keel-workflow ` with a whole workflow YAML (checked at once, then Save →
  `POST /api/projects/{pid}/workflows/import`, optionally into a folder).

```
POST /api/projects/{pid}/workflows/check   { yaml }  → InstallReview { name, steps, gates, keel_rules, agents, mcp, tools,
                                                       commands, edits_files, valid, errors, warnings }   (nothing saved)
POST /api/projects/{pid}/workflows/import  { yaml | url, folder? }   (folder: v0.9.0)
PUT  /api/projects/{pid}/workflows/{wid}/folder  { folder }  → { folder }   ("" takes it out; at most 40 characters)
GET  /api/projects/{pid}/workflows   → Workflow[] + folder, runs, last_run { thread_id, title, status, at }
GET  /api/projects/{pid}/runs?workflow=&limit=20  → RunRow[] { thread_id, title, workflow_id, status, phase, current,
     waiting, acs_done, acs_total, tokens, where, branch, error, created_at, updated_at }   (newest first)
```

- Folders are per project and hold keel's templates too (V10 `workflow_folders`).
- The Flow page (Run): a tab for each flow that runs or waits (when more than one, or when another one is open), and
  **History** (the newest 20, all or one workflow's; **Open** shows any flow with all its details).

## v0.10.0: plugins: Database and Git

A plugin is keel's own add-on (`content/plugins/<name>/plugin.yml`, code in `engine/keel_engine/plugins/<name>`). A
project turns one on in **Tools › Plugins** (or every project does); keel then changes where people already work. The
plugin file v2 adds, for keel's own plugins only (a project's `.keel/plugins` stay text only):

```
installable: true          off until a project turns it on (keel's core plugin is always on)
needs: [database]          what the person sets up (Connections)
tools: {server: keel-db, read: [db_connections, db_schema, db_query]}   the MCP server keel runs; read tools only
actions: [db:query, db:check, db:change, db:migrate]                   workflow code steps; settings in `with:`
shows_in: [connections, map, workflows, keelbot, inbox]
```

- **Calls carry the plugins.** `StartThread.settings` gets `plugins: [db, git]`, `push_pr`, `branch_pattern`; the keys
  (memory only, never in the saved settings) get `db:<connection>` → `{name, kind, url, env}` as JSON and `github` → the
  token. A KeelBot turn gets `plugins` and the same keys; `/helper/commands` gets `plugins` (their `/commands`).
- **Read tools for models.** For one agent call or KeelBot turn the engine opens a key (`plugins.open_call`, memory only)
  and starts `python -m keel_engine.plugins.server db|git` (stdio MCP; since step 3 the Database and Git
  plugins' own `python -m keel_plugin_db.server` and `python -m keel_plugin_git.server`, with the plugin's folder on
  `PYTHONPATH`) with `KEEL_PLUGIN_URL` and `KEEL_PLUGIN_KEY`
  only; each tool call comes back as `POST /plugins/call {key, tool, args}` → `{text}`. KeelBot gets the tools of every
  plugin that is on; a flow agent only when it is ticked on Tools › "Who may use what" (`keel-db`, `keel-git`). The
  allow list now also takes a bare server name (the Tools page saves those; before, the engine dropped them).
- **The Database plugin since step 3** is the plugin folder `plugins/db`: engine `keel_plugin_db` (its MCP server
  `python -m keel_plugin_db.server`), content `plugins/db/content/plugins/db`, api `keel-plugin-db.jar` (the keys
  `db:<connection>` come from its FlowContributor), web `plugins/db/web` (KeelBot's `keel-query` card is its item in
  the slot `keelbot.card`). Its table `db_connections` and the secrets `db.<id>` stay in core.
- **Database rules** (`plugins/db/core.py`): sqlglot reads the SQL: `read` (SELECT, EXPLAIN without ANALYZE, SHOW,
  DESCRIBE, PRAGMA) runs in a read-only transaction, 200 rows, 15 s; `change` (INSERT, UPDATE, DELETE, MERGE) only on
  a `local` or `test` connection, first run and rolled back to count the rows, then for real with `confirm`; `schema`
  (CREATE, ALTER, DROP TABLE) and `never` (DROP DATABASE, TRUNCATE, GRANT, more than one statement, a read that also
  writes, server functions like pg_terminate_backend, also named inside a string another function runs as SQL such as
  `query_to_xml`, `dblink`, the `pg_ls_*` and `lo_*` families, MySQL's `/*! … */` and `/*M! … */`, `INTO OUTFILE`,
  `INTO DUMPFILE`, `LOAD DATA`) are refused. Columns that hold a secret (by name, or computed from one under an alias)
  show as `•••` to models; the api masks every agent's query whatever it asks. A SQLite file must resolve inside the
  project folder, never in keel's data folder. A least-privilege database role is still recommended: these rules are a
  second line. A schema keeps at most 300 tables, the search_path's first (`truncated`, `total`).
  In Docker, `localhost` in an address means the person's computer (host.docker.internal).
- **Git rules** (`plugins/git/core.py`; since step 3 the plugin folder `plugins/git`: engine `keel_plugin_git`, content
  `content/plugins/git`, api `keel-plugin-git.jar`, web `plugins/git/web`): never force, never push to the base branch, main or master; commit = every
  change but keel's files, the secret check, the author and KeelBot line from Settings; a merge of the base that
  conflicts is undone at once; cleanup deletes only merged local branches.
- **Buttons, not actions.** KeelBot never changes data or git: `keel-query {sql, connection?}` and
  `keel-git {op: commit|push|pr|switch|sync, ...}` blocks become cards the person presses (web
  `components/helper/Actions.tsx`; since step 3 a block no core card handles is a plugin's card from the slot
  `keelbot.card`, `{id, kind: "keel-git", component}` with the props `{block: {kind, body}, pid}`: the Git plugin's
  `plugins/git/web/GitCard.tsx`).
- **Workflow steps** (`with:` on a code step; `workflows/validate.py` checks required and unknown keys):
  `db:query` (rows to data.<step>), `db:check` (`expect: none | some | <n>`, soft + a branch on RESULT), `db:change`
  (asks: "Change data in local?", once per fingerprint, unless run mode auto on a local database), `db:migrate`
  (`commands.migrate`); `git:branch`, `git:sync`, `git:push`, `git:pr`, `git:pr-checks`, `git:cleanup` (push and PR
  follow `push_pr`: never skips, ask needs the gate before approved or asks, auto goes on; run mode auto never pushes).

```
GET  /api/plugins                                   → the catalog (engine GET /plugins: tools, actions with their `with`)
GET  /api/projects/{pid}/plugins                    → catalog + enabled, scope (project | all | null)
PUT  /api/projects/{pid}/plugins/{name}  { enabled, scope: project | all }
GET  /api/github                                    → { set, hint, from }   (token: PUT /api/secrets/GITHUB_REPO_TOKEN)
GET|POST /api/projects/{pid}/db/connections         { name, url, env: local|test|staging|prod, source? } → { connection, test }
PUT|DELETE /api/projects/{pid}/db/connections/{name} · POST …/{name}/test
GET  /api/projects/{pid}/db/suggest                 → what compose, .env.example, Spring's config and SQLite files name
GET  /api/projects/{pid}/db/schema?connection=
POST /api/projects/{pid}/db/query  { connection?, sql, change?, confirm?, mask? } → DbResult (read or change)
GET  /api/projects/{pid}/git/status | branches | log | pr
POST /api/projects/{pid}/git/switch {branch, create} | commit {message} | sync | push | pr {title, body, draft} | cleanup
POST /api/projects/{pid}/plugins/ask { title, command } → { id } · GET /api/plugins/asks/{id} → waiting | decision
```

- V11: `project_plugins (project_id | '*', plugin, enabled)` and `db_connections` (the address with its password is the
  secret `db.<id>`; the row keeps it with the password as •••, and the last test).
- **keel2 mcp** (Claude Code, Claude Desktop): `keel_db_schema`, `keel_db_query`, `keel_git_status`, `keel_pr_status`;
  with `--write` also `keel_db_change`, `keel_git_commit`, `keel_git_push`, `keel_pr_create`, which ask the person in
  keel's Inbox first (engine `POST /plugins/ask`, session `mcp`) and wait up to 10 minutes.
- Pages (no new ones): Tools › Plugins, Connections › Databases and GitHub, Map › Database › Query, Code › Source
  control › Git (branch, push, switch, commit, the PR's checks and comments, "Ask KeelBot to address the comments"),
  Workflows (a code step lists the plugins' blocks and edits `with:`), Inbox (Claude Code's questions).

## v0.11.0: the CI/CD plugin, and the Database tool on the Code page

**CI/CD plugin** (since step 3 the plugin folder `plugins/ci`: engine `keel_plugin_ci`, content `content/plugins/ci`, api
`keel-plugin-ci.jar`, web `plugins/ci/web`): the project's pipelines on GitHub Actions through `gh` and the token of
Connections › GitHub.

- A plugin may bring its own workflows (`workflows: [ci-fix]` → `content/plugins/ci/workflows/ci-fix.yaml`). The
  engine's `GET /templates` lists them after keel's with `"plugin": "ci"`; the api lists one only for the projects that
  turned that plugin on (`Workflow.plugin`).
- **ci-fix**: `ci:logs` (the failure into `data.ci_failure`) → the implementer fixes it (phase review-fix: code, tests
  and config) → commit (`fix(ci): …`) → `git:push` (needs the Git plugin; follows `push_pr`) → `ci:wait` (soft) → a
  gate when CI still fails (back to the failure), else the result gate.
- Steps: `ci:status {branch?}`, `ci:wait {minutes?}`, `ci:logs {run?}`, `ci:rerun {run?}`. Read tools for models
  (MCP `keel-ci`): `ci_runs`, `ci_failure`. KeelBot gives `keel-ci {op: fix | rerun, run?}` buttons; `/ci` asks it why
  CI failed.
- **The watcher** (api, every 2 minutes, `keel.ci.tick-ms`): for each project with the plugin on and a GitHub token, a
  failed run that finished in the last two hours and is not in V12 `ci_seen` is told once (notification `failed`,
  link `/jobs/pipelines`). Settings `ci_on_failure`: `notify` (default) | `fix` (start ci-fix; only in the project
  folder, free and on the run's branch; otherwise the notification says why not) | `quiet`.

```
GET  /api/projects/{pid}/ci/runs?branch=          → CiRun[] { id, workflow, title, branch, sha, event, status, conclusion,
                                                     url, attempt, created_at, updated_at, failed }   (newest first)
GET  /api/projects/{pid}/ci/runs/{id}             → CiRun + jobs [{id, name, status, conclusion, url, failed_steps}], log
POST /api/projects/{pid}/ci/runs/{id}/rerun       → { id, rerun }          (gh run rerun --failed)
POST /api/projects/{pid}/ci/fix  { run? }         → the ci-fix thread      (409: no Git plugin, another branch, folder busy)
POST /api/projects/{pid}/ci/check                 → { told: [run ids] }    (the watcher's look, now)
engine POST /plugins/ci/runs | run | rerun  { root, keys, branch?, limit?, run? }
```

- Pages: Run › Jobs › **Pipelines** (when the plugin is on; runs, a failed run's jobs, steps and log, Fix it, Run the
  failed jobs again, Ask KeelBot why), Code › Source control › Git (**Why did CI fail?** on a PR with a failed check),
  Settings › Git › **When CI fails**. keel2 mcp: `keel_ci_runs`, `keel_ci_failure`; `--write` `keel_ci_rerun` (asks).

**Database tool on the Code page** (IntelliJ style; the Database plugin): a **Database** activity next to Source
control. The side bar is a tree of every connection (kind, local / test / staging / prod, connected or not) ▸ its tables
▸ their columns (primary keys, types, foreign keys `→ table.column`), with a filter. A connection opens a **console**
tab (`kind: db`, path `<connection>`): the connection can be switched (the text goes with it, the tab follows); ⌘↵ runs the statement under the cursor or the
selection; the text and a history of 20 statements are kept per connection in the browser. A table opens a **table**
tab (path `<connection>::<table>`): its first 100 rows with a WHERE filter, and its structure. A change of data in a
console is counted first and runs after Run it, on a local or test database only (the same rules as everywhere).

## v0.11.1: plugin tools fixes

- **Claude's MCP servers are ready at the first call.** Claude Code 2.1 connects MCP servers in the background in
  `-p` mode, so a tool called right away failed with "No such tool available". keel runs `claude` with
  `MCP_CONNECTION_NONBLOCKING=0`: the servers are connected before the first model call.
- **The guard and keel's plugin servers.** The read tools of `keel-db`, `keel-git` and `keel-ci` (`hook.py`
  `PLUGIN_READ_TOOLS`: exactly the tools `plugins/server.py` registers) pass in every phase and in read-only runs.
  Before, `ci_runs` was refused because its name has "run". Any other MCP tool is judged as before.
- **Code › Database console**: switching a console to another connection keeps its SQL text, clears the old result,
  and its tab follows (`console · prod`).

## v0.12.0: a branch in the Code page (Git plugin)

Code › Source control › **Branches**: with the Git plugin on, a click opens a branch as an editor tab (`kind:
branch`, path = the branch name; a single click a preview tab, a double click a pinned one). The tab shows the files the
branch changed since it left the base (`base...branch`, so the base's own new commits do not show; a click shows the
file's diff), its commits that the base does not have (each opens its files, like the commits list), how far it is ahead
and behind, **Switch to <branch>** (the Git plugin's switch) and **Ask KeelBot to review it**. Without the plugin a branch
stays a line.

```
GET /api/projects/{pid}/git/branch?name=feat/test   → BranchView { name, base, current, ahead, behind,
                                                       commits: Commit[] (base..branch, ≤100), files: [{path, status, from?}]
                                                       (≤1000), truncated }       (409 without the Git plugin;
                                                       a name is a local branch: 400 for an option or a range, 404 unknown)
GET /api/projects/{pid}/repo/diff?path=…&branch=feat/test   → FileDiff { against: "branch", ref: "main…feat/test" }
```

- The Git plugin's switch, commit, sync and cleanup send `project.changed`: the Code page reads the branch, the tree and
  the changes again.


## v0.14.0: Code Review

An installable plugin (`review`; since step 3 the plugin folder `plugins/review`: engine `keel_plugin_review` (its
content `content/plugins/review` and the fake model's answers for its runs), api `keel-plugin-review.jar`, web
`plugins/review/web` with its own `style.css`). With it on, Code gets a **Review** tool window (⇧⌘9): the
branch you are on, and the pull requests (GitHub) or merge requests (GitLab) to review, assigned to you, yours or all.
One opens in the tool window (Approve, Submit review, Merge your own, Check out; tabs Changes as a folder tree with
viewed marks, Commits, Overview, Findings, Threads, Checklist) and each file as an editor tab (`kind: review`, path
`<key>|<file>`, view `diff` or `code`). A review key is `pr:<n>` or `branch:<name>`. The host is origin's: github.com or
an Enterprise host (token: Connections › GitHub), or GitLab (Connections › GitLab: server URL + token). keel fetches a
pull request's head and base into `refs/keel/review/pr-<n>` and `-base`; it never builds or runs that code.

```
GET  /api/projects/{pid}/review/prs?filter=review|assigned|mine|all  → PrList { host, me, prs: PrSummary[], counts, note }
GET  /api/projects/{pid}/review/branch                     → BranchSummary { branch, base, ahead, files, added, removed, pr?, note }
GET  /api/projects/{pid}/review/view?key=pr:7[&refresh=true] → ReviewView { files, commits, threads, conversation, checks,
                                                              approved, changes_requested, drafts, viewed, can_post, mine,
                                                              mergeable, merge_state, notes, … }
GET  /api/projects/{pid}/review/diff?key&path               → FileDiff (base merge base … head)
GET  /api/projects/{pid}/review/file?key&path&side=head|base → { path, ref, sha, text, truncated }   the whole file
GET  /api/projects/{pid}/review/definition?key&symbol       → Places { symbol, ref, places: [{path, line, text,
GET  /api/projects/{pid}/review/usages?key&symbol             declaration, test, changed}], truncated }   (git grep at head)
POST /api/projects/{pid}/review/drafts {key, path?, line?, side, body, finding_id?}   a pending comment (PUT/DELETE /drafts/{id})
POST /api/projects/{pid}/review/viewed {key, path, viewed}  → { viewed }               per head commit
POST /api/projects/{pid}/review/threads/{id}/reply {key, body} · /resolve {key, resolved} → ReviewView
POST /api/projects/{pid}/review/submit {key, event: COMMENT|APPROVE|REQUEST_CHANGES, body} → { posted, in_body, view }
POST /api/projects/{pid}/review/merge {key, method: merge|squash|rebase, delete_branch}   only your own (409 otherwise)
POST /api/projects/{pid}/review/checkout {key}             → { branch, note }   its own branch, or review/pr-<n> for a fork
GET  /api/projects/{pid}/review/ai?key                      → { overview, findings, decisions }
POST /api/projects/{pid}/review/ai/overview|findings {key}  → starts read-only KeelBot runs (overview; reviewers A ∥ B, then checks)
POST /api/projects/{pid}/review/ai/findings/{id} {key, decision: dismissed|commented|open, why}
GET  /api/projects/{pid}/review/diagrams?key[&refresh=true] → { key, base, head, text, database, system }   the overview's
                                                              Database and System tabs, read from git (no model), kept
                                                              per head commit
GET|PUT|DELETE /api/gitlab {url, token?}                   → { set, url, host, hint }   (the token never leaves keel)
```

- The overview's diagrams (0.18.57): `database` = `{ shown, count, before, after, notes, tables: [{name, status:
  new|changed|removed|related, renamed_from?, columns: [{name, type, pk, fk, unique, nullable, default, status, was,
  cite}], indexes, fks}], changes, migrations: [{path, framework, status, role, parsed, steps: [{line, text}]}],
  risks: [{kind, severity, text, path, line, uses?}], not_parsed: [{path, line, why}], mermaid, ascii }` (`shown` false
  when no changed file holds the database); `system` = `{ source: map|paths, nodes, edges, groups, externals,
  collapsed, tests, mermaid, ascii }`; `text` = both in ASCII (keel TUI's `d` key in the review mode). Migrations are
  read for SQL (Flyway, Liquibase SQL, plain), Doctrine (`addSql`), Laravel (Schema builder), Alembic (`op.*`), Django,
  Rails, TypeORM / Knex SQL strings, Liquibase XML / YAML and schema.prisma; ORM entity classes and statements keel
  cannot read are listed in `not_parsed`, never skipped. "Before" is the Database plugin's live schema (read only)
  when the project has a connection and it does not already hold the change, else the base commit's migrations.

- Submit sends one review: comments on lines of the diff as line comments, any other as part of the review's text.
  GitHub: one review; GitLab: one discussion per line comment, a note, then approve (Request changes is a note and takes
  back your approval). A review on a head that moved is refused until it is opened again.
- keel's AI never posts: a finding becomes a pending comment only when the person presses Add as comment.

## v0.15.0: the launcher

Web only: no new api. ⌘K (Ctrl+K off a Mac) on any page, ⇧⇧ in Code, or the search button next to the bell opens one
search box (`web/src/components/launcher`). The empty box shows what waits for you (the inbox, this project first, and
pull requests you are asked to review), the results you used last (per browser, `keel2.launcher.recent`) and a few
actions. Typing searches, in groups with a best match first: the inbox, pull requests (when Code Review is on), files
(`GET …/repo/files`), code (`GET …/graph/search`), tasks (by title or Jira key), flows, pages, keel's actions and the
other projects. The selected result shows a preview (a file around its line, a pull request, a task, a flow).

- Keys: ↑↓ (⌃N ⌃P), ⌥↑↓ next group, ⇥ next scope (All, Files, Code, Pull requests, Tasks, Flows, Actions), ⌘1–9 pick,
  ↩ the first action, ⌘K all actions of the result, ⌘↩ ask KeelBot about it, ⌘⇧C copy, Esc back one step, ⌫ on an
  empty field drops the prefix. While it is open the page's own keys are quiet.
- Prefixes: `>` actions, `@` code, `#` pull requests and tasks, `!` flows and gates, `?` ask; `Prefs.kt:42` opens a file
  at a line.
- An action that changes something asks first in the panel: approve a gate (`POST /api/inbox/{tid}/act`, plain gates
  only), check out a pull request's branch.
- `?` asks KeelBot in a new Ask chat (read only: `POST …/helper/sessions {mode: ask}` and a turn); the answer shows in
  the launcher with its file:line links; ⌘↩ continues the chat on KeelBot's page.
- `#/repo/@review/<key>` (e.g. `#/repo/@review/pr%3A7`) opens that review in Code's Review tool window.
- keel (the pet by the bell) is off at first; Settings › This browser (or the notification settings) turns it on, kept
  in this browser (`keel2.mascot` = `1`). The logo next to "keel" no longer shrinks away when the row is full.

## v0.15.1: Focus mode in Code

Code's Focus is now the whole window, like an IDE's Zen mode: keel's menu, the usage bar and the Code header go; the IDE
keeps its activity bar, tabs, editor, KeelBot and status bar. ⇧⌘\ (Ctrl+Shift+\ off a Mac) turns it on and off, Esc
twice (an Esc nothing else used) leaves it, and so do the status bar's Exit focus and the launcher's "Focus mode in
Code". While it is on, `<html data-focus="code">` is set (the shell's parts hide by it); leaving the Code page clears it,
and `keel2.repo.focus` = `1` opens Code in Focus mode next time.

## v0.15.2: the Git log

Code › Source control › **Log** (like JetBrains' Git › Log; read only, no plugin needed) opens one editor tab (`kind:
log`, id `keel:log`, path = the branch it shows: `""` the current one, `*` all branches, else a branch, remote branch or
tag). On the left the branches: HEAD, local (the current one marked, ↑↓ against the base), remote (by remote), tags. In
the middle the commits with a graph, ref badges, author, date and id; filters for branch, author, text (or a commit id)
and path; "Uncommitted changes (N files)" on top for the current branch; commits the base does not have are marked, the
ones it has are dimmed. A commit shows its message, its files and one file's diff; a double click opens the file's
change as a commit tab. A branch in Source control (without the Git plugin) and a branch tab ("Show in the log") open
the log on that branch. The filters and the chosen commit stay per project in this browser tab (`keel2.repo.log.<pid>`).
On the plugin track it is the Code plugin's: web `plugins/code/web/Log.tsx`, `gitLog.ts`, `gitLogApi.ts`, api
`keel.api.repo.RepoLog` / `RepoLogController` in `keel-plugin-code.jar` (on core's `RepoService`).

```
GET /api/projects/{pid}/repo/refs   → RefsView { head (null when detached), base, local: RefItem[], remote: RefItem[], tags: RefItem[] }
                                      RefItem { name, sha, date, subject, current, upstream?, ahead?, behind? }  (ahead/behind: a
                                      local branch against the base; local ≤200, remote ≤300, tags ≤100)
GET /api/projects/{pid}/repo/log?branch=&all=&author=&q=&path=&limit=100&skip=0
                                    → GitLog { branch (null = all), head, base, ahead, behind, has_more,
                                      commits: [{ sha, parents, subject, author, email, at, refs: [{name, kind: head|local|remote|tag,
                                      current}], in_base, keel }] }
```

- `branch` blank = HEAD; a local branch, a remote branch or a tag by name, never an option, a range or a revision (400;
  404 unknown). `all=true`: every branch, remote branch and tag (`--branches --remotes --tags HEAD`, not stashes or
  keel's review refs).
- Date order, `limit` ≤ 1000 (+ `skip` for paging). `author` and `q` are fixed strings, any case (both must match);
  `q` that is a commit id (7–40 hex) the shown branch has returns that commit only. `path` is a file or folder inside
  the repo (deleted ones too; `.git` and secret files 403); the parents are then rewritten to the shown commits so the
  graph stays connected.
- `in_base`: the base (main or master) has the commit; without a base every commit is `false`.
- `GET …/repo/commit` and `…/repo/diff?sha=` of a merge commit now show what it brought into its first parent (before:
  no files).

## v0.15.3: Resume a stopped flow, Stop asks first, Markdown, keyboard first

Web only. A stopped or failed flow's card says why and offers **Resume the flow** (after an in-page "Yes, resume": a
rewind to its newest checkpoint, `POST /api/threads/{tid}/rewind`, so the next step runs again) and **Go back to an
earlier step…** (the Checkpoints tab). **Stop flow** asks first in a small dialog (Stop the flow / Keep it running, Esc).
keel's version shows at the menu's foot. In the Flow page the graph scrolls with its panel (no second scroll bar).
- Markdown in an agent's tool output (a `cat` of a `.md` file, or text that reads like Markdown) and a read `.md` file
  show rendered; Raw shows the text, and copying whole blocks of the rendered view gives their Markdown (Copy Markdown
  copies all). A `git diff` / `git show` in tool output shows as a highlighted diff. In Code a `.md` file opens rendered
  (Preview), and Preview works in every view (code, changes).
- In Code a panel keeps the wheel at its end (like an IDE); elsewhere nested panels hand it to the parent.
- Keyboard first: ⌘/ (or ?) opens the key cheat sheet for the page (searchable, IntelliJ or VS Code keymap). Code:
  ⌘1 / ⌘9 / ⇧⌘9 (also ⌥1 / ⌥9 / ⌥⇧9) show and hide their panel, ⇧Esc hides the active tool window, ⌥W closes the tab,
  ⌥⇧[ / ⌥⇧] previous / next tab, ⌘E recent files. The menu: F6 or ⌃⌘M jumps in (↑↓, ↩, Esc back), ⌃1–⌃9 open the
  first pages (Ctrl+Alt+1–9 off a Mac). On the plugin track the sheet's areas for Code, Code Review and KeelBot come from
  those plugins through the slot `keys.area` (core keeps Everywhere, the menu, the launcher, Flow, Workflows and the
  diagrams in `src/keys.ts`); Focus mode's key and event are core's (`FOCUS_KEYS`, `FOCUS_EVENT`, through @keel/web-sdk).
- A notification opens where it came from: the api's `/projects/<id>/<page>` link goes to that project's page (a flow's notification to its own thread), not to All projects.

## v0.15.4: run history

When the project's flow does not run or wait (done, failed, stopped, or none), the Flow page shows **Runs**: every flow
of the project, newest start first (search, status and workflow filters), each with its status, start, how long it
took, tokens, cost, result (ACs done x of y, or the error) and branch. The History fold-out above it hides meanwhile
(it comes back while a flow runs). **Load** opens a run read only under a banner "An earlier run (read only)" with
"Back to the current flow": its steps (Blocks / Table / Graph), events, checkpoints (no Rewind) and outcome. Opening it
only reads (`GET /api/projects/{pid}/flows/{tid}`); the current flow and its state do not change. A flow that runs or
waits elsewhere opens live (Open, `#/flow/<tid>`), as before.
- **Resume** only for the project's newest flow (`latest`), and only when it was stopped: the v0.15.3 Resume (asked
  first, then `POST /api/threads/{tid}/rewind` to its newest checkpoint); the page then shows it live. Other stopped or
  failed runs say "Only the last stopped flow can be resumed". The current flow's own card keeps its Resume (a failed
  one too).
- `GET /api/projects/{pid}/runs` rows add `cost_usd` (the flow's usage, else the sum of its agent calls) and `latest`
  (the project's newest flow by start). Deleted flows are left out, unless they run or wait again.
- `DELETE /api/projects/{pid}/flows/{tid}` → `{ ok: true, thread_id }` deletes a flow from the history, after an
  in-page confirm. It is only a mark (`threads.hidden_at`, migration V14): the engine's checkpoints, the branch and
  commits, the agent calls (budgets) and the events stay, and `GET …/flows/{tid}` still opens it. 409 while it runs
  or waits, and for a stopped or failed flow that is the project's current flow or its newest one (it can still be
  resumed); 404 for an unknown or already deleted flow. `GET …/flow` and a workflow's run count skip deleted flows.
- Saving a finished flow's state again (opening it) no longer moves its `updated_at`, so looking at an old run does
  not make it the project's current flow.

## v0.15.5: Copilot login, every model easy to find

- A login (Copilot, Codex) is noticed while its CLI still runs: the Copilot CLI can stay open after it saved its token, and the page waited for the 15-minute limit.
- The model dropdown: a host with several companies' models (Copilot) shows one sub-group per company with its mark; typing filters the list, a count says how many match; the list is taller.

## v0.15.6: the Copilot login finishes

- After the person authorizes, the Copilot CLI finds no keychain in keel's container and asks "Store token in plaintext config file? (y/N)". keel now answers yes (the token goes to the login's own temporary folder; keel saves it encrypted as GH_TOKEN and deletes the folder). If the CLI still says the token was not saved, the login ends as failed with a clear message.

## v0.15.7: several project folders

- `keel2 add <folder>…` keeps a per-keel list of extra host folders (`${XDG_CONFIG_HOME:-~/.config}/keel2/<KEEL_NAME>.folders`,
  one absolute path per line). `keel2 start` mounts each one that exists at the same path (`-v <path>:<path>`), in
  both modes, and skips missing ones with a warning. `keel2 remove` and `keel2 folders` edit and show the list.
- The container gets `KEEL_FOLDERS=<p1>:<p2>:…`: every project folder it has, as paths inside the container, the main
  project first (`/workspace`, or its host path with `--docker`, the same as `KEEL_WORKSPACE`), then the added
  folders in list order. It is always set (only the main folder when nothing was added). A path with `:` cannot be
  added.
- After `/api/health` answers, keel2 registers each added folder that is a git repo, and each git repo directly
  inside an added folder of repos, with `POST /api/projects {"root": "<path>"}`; roots that `GET /api/projects`
  already lists are skipped.

## v0.15.7: add projects from your folders

- `KEEL_FOLDERS` (`keel.folders`): the folders keel may look in for repos, absolute paths inside the container with `:` between them, like PATH. The main project folder comes first (today's `KEEL_WORKSPACE`, or `/workspace`), then the folders `keel2 add` mounted. Not set: `KEEL_WORKSPACE` alone.
- `GET /api/folders` → `{ roots: [{ path, exists }], repos: [{ path, name, root, project_id }], truncated }`. The git repos in each root: the root itself when it is a repo, else its children, grandchildren and great-grandchildren (depth 3 at most). A repo is not looked into. Skipped: hidden folders (`.git`, `.venv`, …), `node_modules`, `build`, `dist`, `target`, `out`, `vendor`, `venv`, `__pycache__`, symlink loops and symlinks that leave the roots. At most 500 repos (`truncated: true` when it stopped there). `project_id` is the project with that folder, else null.
- `GET /api/folders/browse?path=` → `{ path, root, parent, dirs: [{ path, name, repo, project_id }], truncated }`: one folder's sub folders (the same skips), for the web app's folder walk. No `path` = the first root that is there. Only inside a root or keel's data folder: a path with `..`, a path outside them, or one whose symlinks lead outside them gets 403; a missing folder 404. `parent` is null at the root.
- `POST /api/projects/bulk { roots: [path…], names?: { path: name } }` → `{ added: [Project], skipped: [{ root, why }] }`. Each root goes through the same register as `POST /api/projects` (it must be a folder; an id is made from the name or the folder name). A root that is a project already is skipped with why `already a project (<id>)`; a refused one with the error's text. Each added project is scanned and sent as `project.changed`. An empty list, or more than 500, is 400.
- Web: All projects → **Add projects** (also `#/projects/add`) lists the repos grouped by folder with a checkbox each ("added" and disabled when it is a project already); a search box, "Select all shown", **Add N projects** with the result (added, and skipped with why); **Browse…** walks a folder (breadcrumbs, folders, a repo marker) to tick repos there; **Add by path** keeps the manual field. No folder mounted: one line explains `keel2 add <folder> [<folder>…]`.
- The menu's project picker is a searchable switcher (the project, its phase and branch line, ◆ waiting): a list with a search box, ↑↓ Enter Esc and type-to-filter, each project's waiting count and branch, and "Add projects…" at the bottom. It also sits in the folded rail (the project's two letters) and on the phone bar. ⌘K keeps "Switch to <project>" and adds "Add projects…".

## v0.15.8: keel v1 layouts, clearer folder errors, two MCP tools

- **Layouts** (keel v1's `layout:` in `.keel/config.yml`; engine `keel_engine/layout.py`, api `knowledge/Layout.kt`, the same rules): `repo` — specs in `specs.dir`, else `docs/specs` or `specs`; `docs/knowledge`, `docs/adr`, `docs/RUNNING.md`. `keel` — `.keel/specs`, `.keel/knowledge`, `.keel/adr`, `.keel/RUNNING.md`, with `/.keel` and `/.keel/` in `.git/info/exclude`. `external` — the same paths, `.keel` a symlink to a folder outside the clone. No key: a `.keel` link is `external`, a project with keel files in `docs/` stays `repo`, a new project gets `repo` (0.15.9 switches new projects to `keel`). keel writes where the layout says and reads every place that exists, the layout's own first (a project with `.keel/specs` and `docs/specs` shows both). Init writes `layout:` into the config it creates. In `keel` / `external`, a docs, memory or setup commit of files under `.keel/` is recorded, not committed (`Recorded, not committed: layout keel keeps keel's files out of git.`). The guard's `specs` bucket covers every spec folder.
- `GET /api/projects/{pid}/layout` → `{ name, specs, knowledge, adr, runbook, claude, spec_dirs, knowledge_dirs, adr_dirs, runbooks, link, missing, hidden }`.
- `GET /api/projects/{pid}/wiki` adds a **Specs** section (`spec:<path>` items, first), `notice` (an external layout whose folder keel cannot see: "this project keeps keel's files in <target>; add that folder too: keel2 add <target>", else null) and `layout`. `GET …/wiki/page?id=spec:<path>` → the spec (`meta: { path, acs, updated }`); a path outside the layout's spec folders is 404. `kb:`, `adr:` and `runbook` pages read through the layout; `meta.path` / `meta.from` are the real paths. `GET …/keel-docs` lists the files where they are.
- Folders keel cannot see: `POST /api/projects`, `POST /api/projects/bulk` (`skipped[].why`) and `GET /api/folders/browse` say `keel can't see <path>. Run \`keel2 add <path>\` in a terminal first, then add it here.` `keel2 add` warns when an added project's `.keel` links to a folder keel will not see.
- MCP: `keel_dashboard(project?, view: flow | map | er)` (read) → `<KEEL_PUBLIC_URL or the api url>/#/projects/<id>/flow | map/system | map/er`; `keel2 mcp` sets `KEEL_PUBLIC_URL` to `http://localhost:<port>`. `keel_start_flow(type, idea, project?)` (only with `--write`) → `POST /api/projects/{pid}/flows { workflow_id: type, title: first line of idea, request: idea, options: { started_via: "mcp" } }`; waits up to 20 s for the first gate. The engine's `thread.started` event then carries `via: "mcp"`; the web timeline shows "flow started via MCP". Agents keep `--read-only`.
- Web: `#/projects/<id>/<page>[/<arg>]` picks that project and shows `#/<page>[/<arg>]`; `#/map/<level>` opens that map level.

## v0.15.9: keel's file history, and `.keel/` by default

- **Default layout**: a project with no `layout:` key and no keel files in `docs/` gets `keel` (`.keel/specs`, `.keel/knowledge`, …, hidden from git in `.git/info/exclude`). Projects with keel files in `docs/` stay `repo`; init writes `layout:` into the config it creates.
- **Migration `V15__doc_history.sql`**: `doc_versions` (one row per version of a spec, knowledge section, ADR or runbook: `path, kind, version, sha256, content (≤ 512 KB, secret-looking lines masked), size, deleted, by_kind (agent | keelbot | user | keel | outside), by_name, via, thread_id, step, phase, commit_sha, at`) and `flow_applied` (`thread_id, kind (branch | worktree | commit | recorded), ref, subject, files_json, at`).
- **Engine events**: `doc.version { path, kind, sha256, content?, size, deleted, masked?, by_kind, by_name, phase, via? }` (the web ignores `doc.version` and `keel.applied` on SSE; it reads the history routes) — sent before each step (changes since the last one: `outside`), after it (by the step's agent, `keel` for a code step, `user` for a gate) and around a KeelBot turn (`keelbot`). The api stores it in `doc_versions` (the same text as the newest version is skipped) and keeps the event without `content` in `events` and on SSE. `keel.applied { kind: commit | recorded, sha?, ref, subject, files }` — a keel commit (its files' newest versions get `commit_sha`) or a hidden-layout step recorded instead of committed. The api adds `branch` / `worktree` rows when it starts a flow.
- `GET /api/projects/{pid}/docs/history` → `[{ path, kind, versions, at, state: ok | missing | changed | deleted | unreadable }]` (the file on disk is the current text; `changed` = it differs from the newest version).
- `GET /api/projects/{pid}/docs/history/versions?path=` → versions, newest first, without content; `GET …/versions/{id}` → one with content.
- `GET /api/projects/{pid}/docs/history/diff?path=&from=<id|file>&to=<id|file>` → `{ path, from, to, diff }` (unified).
- `POST /api/projects/{pid}/docs/history/restore { version_id }` → writes that version to its file and records it as a new version `by_kind: user`. A version stored with masked lines (`masked`) is refused (409): restoring it would write the masks. Only a path the layout knows (its spec, knowledge and ADR folders, the runbook); 400 otherwise. 409 while the folder's flow is in red, green or a gate, or when the version has no stored text.
- `GET /api/threads/{tid}/applied` → `{ thread_id, items: [{ kind, ref, subject, files, at }] }`.
- Web: Wiki spec / knowledge / ADR pages show **History** (versions, who, phase, commit, a diff to the file now, Restore with a confirm); Code › keel files lists files that are missing (Restore) or changed outside keel; Flow shows **What keel applied**; the Wiki says when a project keeps keel's files in `docs/`.

## 0.18.5: what keel TUI can do, as MCP tools (`keel_engine/mcp_tui.py`)

Everything the person can do in keel's terminal app an MCP client can do for them, at the level it would need:

- **read**: `keel_workflows` (was admin), `keel_agents` (`GET /jobs?project=&status=running` and the person's KeelBot
  side chats), `keel_agent {agent_id}` (a job's steps `GET /jobs/{id}/steps`, else a chat's last messages),
  `keel_keelbot_chats`, `keel_keelbot_changes {chat}` (`GET …/helper/sessions/{sid}/changes`), `keel_diagram {kind, arg}`
  (`POST /diagrams/ascii` with the data keel TUI's `/diagram` sends: flow, workflow, map-er, map-system, map-modules, graph).
- **write**: `keel_stop_flow` (was admin) and `keel_stop_flows {flows | all}` (`POST /threads/{tid}/stop` is a write route
  for MCP tokens now; both ask the person first), `keel_run_mode {mode, thread_id}` (`POST /threads/{tid}/mode`),
  `keel_resume_flow` (`POST /threads/{tid}/rewind` to the last checkpoint), `keel_agent_start {task, mode: side|ask}`
  (`POST …/helper/sessions`, then `…/turn`), `keel_keelbot_ask {text, chat, mode, wait}` (waits for the answer; KeelBot's
  buttons come back as the keel tools that do them), `keel_keelbot_keep {chat, paths}` and `keel_keelbot_undo {chat, path}`
  (`…/done`, `…/undo`; both ask first).
- keel's own agents still get read only.

## 0.18.6: a plugin's mode in keel TUI (`contributes.tui.modes`), and Code Review in keel TUI and MCP

- **Manifest**: `contributes.tui.modes: [{ name, title, args?, summary, state: { <name>: call }, status?, list: { title?,
  items, line, mark?: { list, field, text }, empty? }, detail?: { title?, call, diff | text, notes?: [{ items, path?, line?,
  side?, text, tone? }] }, keys: [{ key, label, view?: list | detail | both, pick?: { title, call?, items?, line?, match?,
  choices? }, ask?, confirm?, call?, show?, reload?, open?, done? }] }]` (docs/plugins/25-tui-plugins.md). Data only.
  Rules (api `TuiPluginService.mode`, `keel-plugin lint` `_tui_mode`, the same): the name is the plugin's or
  `<plugin>-<word>`; at most 4 modes, 20 keys; state, detail and pick calls are GET of the plugin's own routes; a key's
  POST needs `confirm` or `ask`; a key is one letter or digit, not `j`, `k`, `q`; a command with the mode's name has `sub`.
- `GET /api/tui/plugins?project=` → each plugin also has `modes` (the checked list). `TuiShow` gets `text` (a template).
- **keel TUI** (`cli/src/core/modes.ts` `ModeSession`, `cli/src/ui/ModeView.tsx`): `/<mode> [arg]` reads the state calls
  in order (a later one may use an earlier answer; only the first must answer), then shows the mode between keel's header
  and status line until Esc. Template values in a path are URL-encoded; `.` / `..` values and paths that leave the
  plugin's routes are refused. A body text that is exactly `{name}` keeps the value's JSON type.
- **Code Review api**: `GET /api/projects/{pid}/review/open?ref=` → the `ReviewView` of `""` (the branch the folder is on;
  400 when it is on no branch), `12` / `#12` / `!12` (`pr:12`), `pr:…` / `branch:…`, or another word (`branch:<word>`).
- **keel2 mcp** (the Code Review plugin's engine part, `PART.keel_mcp`, `keel_plugin_review/mcp_tools.py`):
  read `keel_review_prs {filter}`, `keel_review_files {pr | branch}`, `keel_review_diff {path?}` (≤ 600 lines),
  `keel_review_notes` (`GET …/review/ai`); write `keel_review_comment {path, line, body, side}` (`POST …/review/drafts`),
  `keel_review_explain` / `keel_review_findings` (`POST …/review/ai/overview | findings`), and, asking the person first
  (MCP elicitation, or `--yes`), `keel_review_reply {thread_id, body}`, `keel_review_resolve {thread_id, resolved}`,
  `keel_review_submit {event: APPROVE | REQUEST_CHANGES | COMMENT, body}`. Each first opens the review with
  `GET …/review/open` and refuses when the plugin is off for the project. Levels: GET is read; every POST is write
  (`McpLevelGuard`'s default).

## 0.18.6: skills from everywhere, the skills marketplace, the plugin workflow

**Skills** (`api/.../skills/`): a skill is a SKILL.md an agent reads before it works (it never gives the agent more
power: keel's rules and the phase decide that). keel's own (`content/skills`, the keel packs') are in every project; a
project's own are written here, imported from a link, or installed from the marketplace (`source: marketplace`,
`market: {catalog, name, version, edited}`; V18__skill_market.sql adds `market_catalog`, `market_name`, `market_version`,
`market_sha256` to `custom_skills`). `DELETE /api/projects/{pid}/skills/{sid}` removes one of the project's own (keel's:
409). Attaching a skill to agents is the skill's `agents` (`PUT …/skills/{sid}`); an agent whose own `skills` list is
set in the project (an override, which wins) gets it there too.

**MCP** (`keel_engine/mcp_skills.py`): read `keel_skills`, `keel_skill {skill}`; write, each asks the person first:
`keel_skill_create {text, agents?}` (keel checks the front matter first: name, description, text),
`keel_skill_import {url, agents?}`, `keel_skill_attach {skill, agents, detach?}`, `keel_skill_market_install {name,
version?, agents?, replace?}`; `keel_format kind=skill`; `keel_marketplace_search` lists the skills that match too (the
catalogs' and keel's own). McpLevels: the skill writes are write; `DELETE …/skills/{sid}` is admin; a read token reads only.

**keel TUI**: `/skills` (the list, the agents of each), `/skills market [words]`, `/skill <id>`, `/skill attach|detach
<id> <agent…>`, `/skill install <name> [agent…]` (the review, then one yes), `/create skill <what it should teach>
[--for <agent…>] | --file <SKILL.md>`. KeelBot's `keel-skill` block (a whole SKILL.md; `agents: [...]` in its front
matter): a card in the dashboard (agents to tick, Save), a yes/no in keel TUI (checked first; refused ones are never
offered).

**The marketplace's fourth kind, `skills`** (engine `marketplace/kinds.py` SKILLS, `marketplace/skills.py`; tools
`keel_plugin/skill.py`): an entry `{name, kind: skills, title, publisher, summary, category (testing, implementation,
review, debugging, knowledge, authoring, other), tags, versions: [{version, released, requires: {keel?}, url, sha256,
size ≤ 256 KB, review: {tokens, sections, description}}]}`; the file is the SKILL.md, shipped unchanged (its front
matter's name is the catalog name; its `version`, when it has one, the catalog version). `skills` joins the names a
plugin may not take.

```
engine  GET  /marketplace/skills?q=&category=   {skills: [Hit], sources, categories}; /marketplace/search?kind=skills|all
        GET  /marketplace/skills/{name}         Hit + {versions, checks}
        POST /marketplace/skills/{name}/fetch {version?}   {name, version, text, review, catalog, …}: sha256, the publisher's
             signature, the front matter, keel's review = the catalog's (409 with differences), requires.keel (409)
api     GET  /api/marketplace/skills?q=&category=&project=   + installed {version, edited, sid, agents} | null, update;
             builtin: keel's own [{name, title, summary, source, kind, category, tokens, agents, when, enabled, refs}];
             catalog_error when no catalog answers (keel's own still listed)
        GET  /api/marketplace/skills/{name}?project=   + text (verified), review, catalog_review, fetch_error; a name only
             keel has: builtin: true with its text and review
        POST /api/projects/{pid}/skills/market/install {name, version?, agents?, replace?}   {skill, market, review};
             409 the same version, or another without replace
```

The dashboard: **Settings › Plugins › Marketplace › Skills** (`#/settings/plugins?tab=market&kind=skills`; a skill's page
`#/settings/plugins/skills/<name>`): From keel (keel's own, Agents…), the catalogs' cards (Install with the agents to tick,
Installed here, Update: keep an edited copy or replace it, Remove), the text and keel's review. **Publishing**:
`keel-plugin skills lint <name>-<version>.md`, `keel-plugin skills add <skill dir…> --market <dir>` (keel's own are in
`docs/plugins/marketplace-repo/skills/`, unsigned until keel's key signs them), `keel-plugin index` builds the `skills`
list (files kept in the marketplace repo: `skills/<name>/<version>.md`, served by the sync).

**The `plugin` workflow** (`content/workflows/plugin.yaml`, last in ORDER): preflight → the plugin's spec and its gate →
the scaffold with `keel-plugin new` (one commit) → per criterion red, green, AC review, AC gate → `keel-plugin lint` on the
built package (`scripts/plugin-check.sh lint`: `scripts/build-plugin.sh`, then lint) → the plugin's tests
(`scripts/plugin-check.sh test`: `scripts/test-plugins.sh <name>`), each with 2 fix rounds before keel asks → the review
and its gate → close. `scripts/plugin-check.sh` finds the plugin from the branch's changes under `plugins/`.

## 0.18.9: stacks: remove, turn off, a guided New stack, the stacks marketplace

A **stack** is how one project builds and tests: one YAML file (`content/stacks/<name>.yml`'s format: `name`, `lane`,
`detect`, `layers`, `commands`, `tools`, `skills`). The api's `StackService` lists them; the engine owns the files
(`runtime/stack_edit.py`, routes in `runtime/stack_routes.py`) and gets the project's folder from the api.

**Where each comes from** (`GET /api/projects/{pid}/stacks`, each item adds `origin`, `off`, `removable`, `why`,
`market`, `file`): `keel` (content/stacks: not removable, `why` says to turn it off), `keel pack` (content/packs, not
installed: `installable`), `pack` (a keel pack installed here: `.keel/stacks/<pack>/stack.yml`), `marketplace`
(`.keel/stacks/.installed.json` `{name: {source, version, catalog, sha256, at}}`; `market: {catalog, version, edited}`,
edited = the file's sha256 is not the one installed), `project` (any other file under `.keel/stacks`).

**Turned off**: `.keel/config.yml` `stacks: {ignore: [name…]}` (not `off`: YAML 1.1 reads that key as false). A stack
turned off is never matched (`runtime/stacks.matched`), so its tools never run; the list still shows it with
`off: true`. Needs `.keel/config.yml` (409 without one).

```
api   GET    /api/projects/{pid}/stacks/{name}            the stack + yaml (its file's text)
      GET    /api/projects/{pid}/stacks/suggest?lane=     what keel reads in the project's files: {lane, lanes: {api, web:
             [why]}, name, detect: {files, extensions, package_deps}, layers, skills, closest (keel's stack it is most like),
             commands: {key: [{cmd, from}]}, keys: [{key, what, placeholder}], tools: [{name, run, on, fail, match?, from}],
             read: [files]} from package.json scripts, Gradle, Maven, pyproject, composer.json, go.mod, Cargo.toml, a
             Makefile (last), .pre-commit-config.yaml, and keel's closest stack
      POST   /api/projects/{pid}/stacks/check {yaml? | form?, replace?}   saves nothing: {ok, errors, warnings, name,
             lane, review, detects_here, replaces (keel | project | null), yaml (the form rendered), skills_missing}
      POST   /api/projects/{pid}/stacks/save {yaml, replace?}   .keel/stacks/<name>.yml after the check: {stack, path,
             replaced, warnings}; 422 {errors} (a name the project has already, without replace, is an error)
      DELETE /api/projects/{pid}/stacks/{name}            the project's own file, or an installed pack's folder (with its
             skills): {name, removed, back: "keel" when keel's stack of that name is used again}; keel's own 409
      PUT    /api/projects/{pid}/stacks/{name}/off {off}  {name, off, all}
      POST   /api/projects/{pid}/stacks {name, from} and …/stacks/{name}/install   as before (copy a stack, install a pack)
```

The form (`check {form}`): `{name, lane, detect: {files, package_deps, extensions, exclude_files}, layers: [{id, what,
where}], commands: {key: cmd}, tools: {name: {run, on, fail, match?, description?} | false}, skills: {kind: id}}`;
empty answers are left out. Commands per lane — api: `api_test_ac` ({AC}, {AC_ID} or {AC_KEY}), `api_test_module`,
`api_test_pkg` ({PKG}), `coverage_api`, `static_checks`, `migrate`, `api_compile`; web: `web_test_ac`, `web_test_module`,
`web_test_paths` ({PATHS}), `coverage_web`, `static_checks`, `web_typecheck`. The check's errors: not YAML, no name/id,
lane not api|web, detect names nothing, no layers or a layer without id or twice, an empty command, a tool keel would
not run (`runtime/tools.problem`), a name the project has already; its warnings: a command keel does not read or of the
other lane, no `*_test_ac` / `*_test_module`, a test command without its placeholder, a detect block that does not match.

**MCP** (`keel_engine/mcp_stacks.py`): read `keel_stacks`, `keel_stack {stack}`, `keel_stack_suggest {lane?}`,
`keel_stack_check {yaml, replace?}`; write `keel_stack_create {yaml, replace?}`, `keel_stack_remove {stack}`,
`keel_stack_market_install {name, version?, replace?}` (each asks the person first) and `keel_stack_off {stack, off}`
(easy to undo: no question); `keel_format kind=stack`; `keel_marketplace_search` lists stacks too. McpLevels:
`POST …/stacks/check` is a read; save, remove, off and installs are write.

**keel TUI**: `/stacks`, `/stacks market [words]`, `/stack <name>` (with its YAML), `/stack remove <name>` (a yes
first; keel's own: why not, and `/stack off`), `/stack off|on <name>`, `/stack install <name>` (every command first,
then a yes), `/stack install-pack <name>`, `/create stack` (keel's guided questions: lane, name, keel's check, a yes),
`/create stack <what it is>` (KeelBot writes it) and `--file <path>`. KeelBot's `keel-stack` block (the whole YAML): a
**Save the stack** card in the dashboard (checked first), a yes/no in keel TUI.

**The marketplace's fifth kind, `stacks`** (engine `marketplace/kinds.py` STACKS, `marketplace/stacks.py`; tools
`keel_plugin/stack.py`): an entry `{name, kind: stacks, title, publisher, summary, category (jvm, javascript, python,
php, go, rust, dotnet, other), tags, versions: [{version, released, requires: {keel?}, url, sha256, size ≤ 64 KB,
review: {lane, detect, layers, commands, tools: [{name, run, on, fail}], skills}}]}`; the file is the stack YAML, shipped
unchanged (its name is the catalog name; its `version`, when it has one, the catalog version; keel's check finds no
error). Trust `content` (its commands run through keel's flow and shell guard, as a workflow's do; the review lists
every one). `stacks` joins the names a plugin may not take.

```
engine  GET  /marketplace/stacks?q=&category=   {stacks: [Hit], sources, categories}; /marketplace/search?kind=stacks|all
        GET  /marketplace/stacks/{name}         Hit + {versions, checks}
        POST /marketplace/stacks/{name}/fetch {version?}   {name, version, yaml, review, catalog, sha256, warnings, …}:
             sha256, signature, name/version, keel's check (502), keel's review = the catalog's (409), requires.keel (409)
api     GET  /api/marketplace/stacks?q=&category=&project=   + installed {version, edited, origin} | null, update,
             conflict (the project's own stack has that name); builtin: keel's own [{name, lane, source, detect,
             installable, used, off, installed}]; catalog_error when no catalog answers
        GET  /api/marketplace/stacks/{name}?project=   + yaml (verified), review, catalog_review, check (keel's check
             against the project), fetch_error; keel's own: builtin: true with its yaml and review
        POST /api/projects/{pid}/stacks/market/install {name, version?, replace?}   {stack, path, market, review,
             warnings}; 409 the same version, or a stack of that name here without replace
```

The dashboard: **Build › Stacks** (From, turned off, Turn off / on, Show YAML, Remove after a yes, **Find stacks**,
**New stack**: Lane → Detect → Test layers → Commands → Tools → Check, each step filled from keel's suggestions, the YAML
editable and checked again before Save) and **Settings › Plugins › Marketplace › Stacks** (`#/settings/plugins?tab=market&kind=stacks`;
a stack's page `#/settings/plugins/stacks/<name>`: From keel (Install pack), the catalogs' cards, the YAML, keel's review and
check, Install, Update, Remove). **Publishing**: `keel-plugin stacks lint <name>-<version>.yaml`, `keel-plugin stacks
add <stack.yml | pack dir…> --market <dir>` (keel's own are in `docs/plugins/marketplace-repo/stacks/`, unsigned until
keel's key signs them); `keel-plugin index` builds the `stacks` list.

## v0.16.0: full MCP (read, --write, --admin)

- `keel2 mcp [--write | --admin] [--allow t1,t2] [--yes] [--print-config]` → `python -m keel_engine.mcp` with the same flags. Each level includes the one below; a tool above the level, or not in `--allow`, is not registered. `--read-only` (what agents get: engine `tools/mcp.py`, api `McpService`) always wins over `--write`, `--admin`, `KEEL_MCP_WRITE` and `KEEL_MCP_LEVEL`. `--allow` (or `KEEL_MCP_ALLOW`) with an unknown tool name stops the server with the list of tools.
- **read**: `keel_status`, `keel_projects`, `keel_timeline`, `keel_next`, `keel_explain`, `keel_dashboard`, the plugins' `keel_db_schema`, `keel_db_query`, `keel_git_status`, `keel_pr_status`, `keel_ci_runs`, `keel_ci_failure`, and new `keel_flows` (`GET /projects/{pid}/runs`), `keel_inbox` (`GET /inbox`), `keel_live` (`GET /jobs?status=running`, each job's last step), `keel_budget` (`GET /projects/{pid}/budget/now`), `keel_specs` (keel docs, `spec:` wiki pages, `history: true` → `/docs/history/versions`), `keel_knowledge` (`/memory`, `kb:` pages), `keel_search` (`kind: text` → `/repo/search`, `symbol` → `/graph/search`), `keel_read_file` (`/repo/raw`, numbered lines, at most 400, secret files refused before the api is asked), `keel_read_project_file` (0.18.64: any registered project, read from its folder under an agent's read rules), `keel_actions` (every engine action with its level), `keel_run_action`.
- **write**: `keel_approve_gate`, `keel_resume`, `keel_start_flow`, the plugins' `keel_git_commit`, `keel_git_push`, `keel_pr_create`, `keel_ci_rerun`, `keel_db_change`, and new `keel_answer` (`POST /inbox/{tid}/act { decision, why, payload: { answers, choice } }`, or `POST /inbox/tasks/{id}/act`), `keel_edit_gate` (send back with notes), `keel_restore_doc` (0.15.9 restore).
- **admin**: `keel_stop_flow` (`POST /threads/{tid}/stop`), `keel_delete_run` (`DELETE /projects/{pid}/flows/{tid}`), `keel_add_project` (only a folder `GET /folders` lists), `keel_remove_project` (`DELETE /api/projects/{pid}`, new: the project is hidden, its history kept; 409 while a flow runs or waits there; `POST /api/projects` with its folder shows it again), `keel_workflows` (list, or one workflow's YAML), `keel_edit_workflow` (`POST /projects/{pid}/workflows/check`, then `PUT /workflows/{wid} { yaml }`; nothing saved when the check has errors).
- **Engine actions**: `action_docs.ACTION_LEVELS` gives an action its level (read: audit, trace, arch, reports …; write: verify_release, verify_coverage, verify_fast, verify_deps, verify_lint, lint_run, knowledge_check, memory_check, push_check, pr; admin: arch_set, open_pr); every other action is `flow-only`. `POST /api/projects/{pid}/actions/{name} { args }` → engine `POST /projects/{pid}/actions/{name} { root, args, settings }` → `{ ok, note, detail }` (404 unknown, 409 flow-only).
- **Confirm**: `keel_git_push`, `keel_db_change`, `keel_delete_run`, `keel_stop_flow`, `keel_remove_project`, `keel_edit_workflow` and `keel_restore_doc` ask the person through the client (MCP elicitation, `{ confirm: bool }`); a client without elicitation gets a one-time token and, 0.18.64, a question in keel's Inbox (below: "A client without
forms: you confirm in keel's Inbox"); `--yes` skips the question (danger: the model's acting tools then run with no
question to the person). The plugins' "ask in keel's Inbox" stays as a second check.
- **Audit**: every non-GET request from the MCP server carries `X-Keel-Via: mcp`, `X-Keel-Client: <the client's name>` and `X-Keel-Tool: <tool>`. The api (`mcp/McpAudit.kt`) records each as an event `mcp.call { via, client, tool, method, path, ok, status }` on the project (and thread); the web timeline shows "via MCP · <client>: <tool>". Reads are not recorded.
- No tool calls keel's login, key or connection-secret routes; `repo/file` and `repo/raw` already refuse secret files (`.env`, keys, certificates; `RepoService.safePath`).

## v0.16.1: native mode (keel2 start --native)

- `keel2 start --native [folder]` runs keel on this computer without Docker: needs Java 21, uv, git and curl (Node 20+ for the agent CLIs). `--from-tarball <keel-v2-<version>-native.tar.gz>` or `--from-source <checkout>` instead of the download from `https://github.com/MiladNalbandi/keel-v2/releases/download/v<version>/` (`KEEL_RELEASE_URL` overrides; the `.sha256` next to it must match). `--docker` is not needed natively (the project's tests use this computer's Docker directly).
- Files: `${KEEL2_HOME:-~/.keel2}/<KEEL_NAME>/` with `app/<version>/` (api.jar, engine/ + its `.venv` from `uv sync --frozen`, content/, keel-native-start, VERSION), `current` and `previous` links, `data/` (`KEEL_DATA`: keel.db, master.key, agent homes), `npm/` (keel's own agent CLIs and codegraph, `npm i -g --prefix`, pinned like the image; `KEEL_NATIVE_CLIS=0` skips them), `run/` (keel.pid, ports `api=` / `engine=`, the internal token, mode 600), `logs/keel.log` (the previous run is `keel.log.1`), `mode` (`native`), `project`.
- `docker/keel-native-start` (in the tarball as `keel-native-start`) starts the engine on `KEEL_ENGINE_PORT` (the first free from 8090) and `java -jar api.jar` on `KEEL_PORT` (the first free from `PORT`, default 8080), both on 127.0.0.1, with `KEEL_NATIVE=1`, `KEEL_CONTENT`, `KEEL_MCP_PYTHON`, `KEEL_FOLDERS` (the project and the added folders, nothing mounted) and keel's `npm/bin` first on its `PATH`. It never writes into `$HOME`.
- `KEEL_NATIVE=1`: every agent CLI gets `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_CACHE_HOME` under `$KEEL_DATA/agent-home/home` (engine `models/cli.py safe_env`), so it never writes into the person's own home.
- The other commands work the same natively: `stop` (TERM, then KILL after 20 s), `restart`, `status` (says agents run as you), `logs` (`tail -F`), `doctor`, `update` (the newest release into `app/<new>`, `--rollback` back to `previous`), `mcp` (the engine's Python, `KEEL_API_URL` and the token from `run/`), `add` / `remove` / `folders`, `token` / `tokens`, `sql` (`data/keel.db`), `backup` / `restore` (`data/`, the same `.tgz` format), `open`, `uninstall [--all]`. A Docker keel and a native keel with the same name refuse each other.
- The first native start warns that agents run as the person and see the whole computer, and asks `[y/N]` (`KEEL_YES=1` answers yes).
- `scripts/build-native.sh [out dir]` builds the tarball and its `.sha256`; the `docker` workflow attaches both to the tag's GitHub release (or keeps them as the run's artifact); the `ci` workflow's `native-smoke` job runs a flow to its first gate natively on Ubuntu and macOS.

## v0.16.1 addendum: `native` in health, the `conflict` file state

- `GET /api/health` also returns `"native": true|false`: true when the api runs with `KEEL_NATIVE=1` (`keel2 start --native`). The web header shows a "native" pill then ("Native mode: keel's agents run as you and can read what you can.").
- `GET /api/projects/{pid}/docs/history`: a file's `state` is `"conflict"` when its two newest versions have different, non-null `thread_id`s (two flows wrote it, one in a worktree and one in the project folder), their texts differ, and the file on disk equals the newest one (so the other text was overwritten). Such a file also has `conflict: [newestVersionId, otherVersionId]` (absent otherwise). The web lists it under "Two versions — pick one": a diff `from=<other>&to=<newest>`, "Keep this one" on the newest (nothing is written) or on the other (`POST .../docs/history/restore { version_id: <other> }`, as a new version by the person).

## v0.16.1 addendum: MCP level tokens

- The api makes three random tokens at start (read, write, admin; 64 hex characters each), kept only in memory (`mcp/McpLevels.kt` `McpTokens`); a restart makes new ones.
- `POST /api/mcp/token { level: read | write | admin }` → `{ level, token }`, only with `X-Keel-Token` equal to the internal token (`KEEL_INTERNAL_TOKEN`, compared in constant time); 403 otherwise (also when keel has no internal token), 400 for an unknown level. keel2 and the engine have the internal token; agents never do (engine `models/cli.py` `SECRET_VARS`).
- keel's MCP server (`python -m keel_engine.mcp`) asks for its level's token on its first request when it has the internal token (`KEEL_INTERNAL_TOKEN`, else the file `KEEL_INTERNAL_TOKEN_FILE` names) and sends it as `X-Keel-Mcp-Token` on every request; it no longer sends `X-Keel-Token` on ordinary requests. `keel2 mcp` natively passes `KEEL_INTERNAL_TOKEN` from `run/token`; in Docker it passes `KEEL_INTERNAL_TOKEN_FILE=/data/run/internal-token`, which `docker/keel-start` writes (mode 600). Agents' builtin `keel` server gets neither, so it sends no token.
- `McpLevelGuard` checks requests under `/api/` that carry `X-Keel-Via: mcp` or `X-Keel-Mcp-Token`, and every `POST /api/projects/{pid}/actions/{name}` (only MCP calls it). Without a valid token such a request is at the **read** level. GET is fine at read; `POST /api/projects/{pid}/db/query` is fine at read, but `change: true` needs write; other methods need **write**; **admin**: `POST /api/threads/{tid}/stop`, `DELETE /api/projects/{pid}/flows/{tid}`, `POST /api/projects`, `POST /api/projects/bulk`, `DELETE /api/projects/{pid}`, `PUT` / `DELETE /api/workflows/{wid}`. An action needs its engine level: `McpLevelGuard.READ_ACTIONS` and `ADMIN_ACTIONS` mirror `action_docs.ACTION_LEVELS` (a Kotlin and a Python test pin both lists); any other action needs write. A refused call → 403 `{ error: "This needs the MCP <level> level: run keel2 mcp --write | --admin", hint }`, still recorded by `McpAudit` (`ok: false, status: 403`).
- The web app sends neither header and never calls `/actions`, so it works as before. The guard binds only callers that say they are MCP: it is not a login for the api.

## v0.17.2: rules

Plan and decisions: `docs/plans/0.17.2.md`.

- **Where rules live** (all files in the project, read by the guard on every tool call): `.keel/config.yml` blocks `guards` (`protected`, `generated`, `read_block`, `bash_deny_always`, `bash_deny_in_flow`, `read_guard_max_lines`, default 2000, 0 = off), `mcp.allow`, `rules` (`off: [<built-in id> | pack/<id>]`, `push_gate: true`, `push_gate_commands: ["git push", "gh pr create"]`), `boundaries.rules`; and rule packs `.keel/rules/<id>.yaml`. `rules.load_config(root)` adds `configured` (the config file exists), `packs` and `pack_errors` (files that could not be used, with why).
- **Rule pack** (`engine/keel_engine/rules/packs.py`, final format: `docs/rules/pack-format.md`): one YAML file `{schema: 1, kind?: rules, name, title, version, publisher? (local), summary?, stack?, requires?: {keel: range, plugins: {name: range}}, rules: [{id, kind: bash|edit|read|mcp, match: [text], how?: contains|glob|name|regex (default per kind: contains, glob, glob, name), phases?: all|in-flow|none|[phase], lanes?: [api|web], except?: [glob], reason}]}`; `name` is the id and the file name; at most 64 KB; a pack whose `requires.keel` this keel does not meet is not used. A pack rule only refuses; it runs inside `check_edit` (after the always-deny buckets, after unlocks), `check_read`, `check_bash` and `hook.check_mcp`, so the hook, the ToolBox and the diff guard all apply it. The refusal reason ends with `(rule <pack>/<rule>)`. The library is `content/rules/*.yaml` (strict-secrets, no-new-dependencies, migrations-only-in-flow, frontend-not-api, stack-spring, stack-node, stack-python), each `version: 1.0.0`, `publisher: keel`. `.keel/rules/.installed.json` records `{name: {version, source: library|file|marketplace, at, edited?}}`; the pack file stays as published.
- **Built-in rules** (`rules.BUILTINS`, `can_off`): never off — phase-matrix, env-files, keel-rules-files, migrations-immutable, force-push, no-verify, commit-in-flow, history-rewrite, secrets-at-write, disabled-tests; may be off (`rules.off`) — generated, read-build-output, skip-test-flags, new-dependency, read-twice, big-file-read, push-gate, mcp-writes, other-projects (0.18.64: off, the project keeps to itself both ways). The force-push commands and `--no-verify` (always) and `git reset --hard`, `git rebase -i` (in a flow) are refused even when the config's lists drop them (`rules.NEVER_DENY_ALWAYS`, `NEVER_DENY_IN_FLOW`).
- **New bucket `keel-rules`**: `.keel/config.yml` and `.keel/rules/**`; always refused to agents (phase `setup` may write `.keel/config.yml`). The api's `KeelRules.classify` has it too.
- **keel v1 hook gaps closed**: the push gate (`push_blockers` in the guard context, from `runtime/blockers.py for_agents` when the agent starts; only in a project with `.keel/config.yml`); secrets and disabled-test markers in the text an edit adds (Write `content`, Edit `new_string`, MultiEdit, NotebookEdit, a patch's `+` lines; `rules.check_content`) refused before the write, and the diff guard puts back a changed file whose new lines hold one (`bucket: "content"`); big files read with offset/limit; an agent's last failed shell command in `data.last_command_failure {command, output, step, agent}` (not `state.last_failure`); an agent whose answer has none of the step's registered result markers is asked once more (event `agent.reask {agent, missing}`).
- **Guard context** gains `push_blockers: [{gate, why, fix}]`.
- **codex** (0.150+): `-c hooks.PreToolUse=[{matcher=".*",hooks=[{type="command",command=<hook>,timeout=10}]}] --dangerously-bypass-hook-trust`; the hook reads codex's `shell` / `exec_command` (command string or argv) as Bash and `apply_patch` as one Edit per file with its added lines. **codex and copilot** also get keel's command wrappers (`keel_engine/shim.py`, folder `<scratch>/keel-bin` first on `PATH`, `KEEL_SHIM_DIR`): git, gh, npm, pnpm, yarn, bun, pip, pip3, uv, poetry, cargo, go, gem, cat, head, tail, less, more ask the hook (Bash) and then run the real program; a refusal exits 126 with `[keel guard] <reason>`. copilot in a flow also gets `--deny-tool 'shell(git commit)'`. Protection per engine (`cli_runners.protection`): claude, opencode, API-key models, codex 0.150+ `before`; copilot (and an older codex) `partial` (shell before, file edits after the step).
- **Fail-closed** (unchanged, now in the Guide): with no readable guard context, or any error in the hook, only Read/Glob/Grep/LS pass (`.env` and build output stay refused by keel's defaults); everything else is refused. Reads are kept because they change nothing, and refusing them too would only fail the agent with no useful signal.
- **Engine**: `GET /rules?root=` → `{configured, config_file, rules_folder, builtins: [{id, kind, phases, words, can_off, off, enforced, saved_in}], lists: [{key, kind, words, form, value, default, enforced, saved_in}], packs: [{...pack, file, off, rules: [{..., enforced}]}], pack_errors, engines: [{engine, protection, words}], buckets, phases: [{phase, meaning, matrix}]}`; `enforced` is `{claude|opencode|codex|copilot|api: before|wrapped|after|none}`. `PUT /rules/config {root, patch}` (only `guards`, `mcp`, `rules`, `boundaries`; those top-level blocks are rewritten, every other line stays; never-off ids and a `guards.protected` without the .env files → 400; no `.keel/config.yml` → 409) → the view. `GET /rules/library` → `[{...pack, yaml}]`; `GET /rules/library/{id}` → `{yaml}`. `POST /rules/check {yaml, calls?}` → `{ok, errors, pack?, dry_run?: {checked, blocked: [{at?, thread?, agent?, phase?, tool?, kind, target, reason}]}}`. `POST /rules/packs {root, yaml, replace?}` → `{pack, view}` (422 invalid, 409 the id exists and is not `replace`). `GET /rules/packs/{id}?root=` → `{yaml}`. `DELETE /rules/packs/{id}?root=` → the view. `GET /threads/{tid}/rules-now` → `{thread_id, status, current, phase, meaning, lane, may: [{bucket, what, rule, words}], may_not, always, packs: [{pack, name, rule, kind, reason}], unlocks, shell, engine: {provider, protection, words}, last_command_failure}`.
- **api** (`rules/RulesController.kt`): `GET /api/projects/{pid}/rules`, `PUT /api/projects/{pid}/rules/config`, `GET /api/rules/library`, `POST /api/projects/{pid}/rules/library/{id}/install`, `POST /api/projects/{pid}/rules/packs {yaml}`, `PUT` / `DELETE /api/projects/{pid}/rules/packs/{id}`, `GET /api/projects/{pid}/rules/packs/{id}/export` (text/yaml, `<id>.rules.yaml`), `POST /api/projects/{pid}/rules/check {yaml}` (the api adds the project's last 500 `agent.step` and `guard.refused` events as `calls`, with the phase from `agent.started`), `GET /api/threads/{tid}/rules-now` (the engine's answer plus `fired: [{reason, rule, count, phase, examples, source: "before the action" | "after the step", last_at, step}]` from the thread's `guard.refused` events, grouped by rule). MCP levels: the rule writes are **admin**; `rules/check` is read.
- **MCP**: `keel_rules` (read); `keel_rule_install` and `keel_rule_edit` (admin; `keel_rule_edit` asks the person first).
- **Settings**: `keel_rules` is gone (nothing read it; a workflow's own `keel_rules` stays). A saved value of a removed setting is ignored.
- **Fake model**: `KEEL_FAKE_COMMAND` = a shell command every writing agent runs through its tools first (tests and real checks of rule packs).
- **Installable packs** (a marketplace install calls these; MCP admin level for the writes): `GET /api/projects/{pid}/rule-packs` → `[{name, title, version, publisher, summary, requires, source, file, off, edited?, update?}]` (`update`: keel's library has a newer version of a library pack); `POST /api/projects/{pid}/rule-packs/install {source: library|file|marketplace, name?, version?, yaml?, replace?}` → `{pack, installed}` (404 no such library pack or version, 409 the same version is installed or another version is and `replace` is not set, 422 invalid or `requires.keel` not met); `POST /api/projects/{pid}/rule-packs/uninstall {name}` → `{installed}`. Engine: `GET /rules/packs?root=`, `POST /rules/packs/install {root, source, name?, version?, yaml?, replace?}`, `POST /rules/packs/uninstall {root, name}`, `GET /rules/library/{name}?version=`. Export is named `<name>-<version>.rules.yaml`.
- **keel2 data volume** (bug fix): `KEEL_VOLUME` defaults to `keel-data` for `KEEL_NAME=keel-v2` and to `<KEEL_NAME>-data` for any other name; an existing container keeps the volume it mounts at `/data` unless `KEEL_VOLUME` is set. `start`, `restore` and `uninstall --all` refuse a data volume another running container uses (`docker ps --filter volume=`). Test: `scripts/test-keel2-volume.sh` (CI `scripts` job).
- **Rule pack editor** (v0.17.2 follow-up): `POST /rules/check` (engine) and `POST /api/projects/{pid}/rules/check` also answer `lines: [{line, message}]` (each error at the line of the field it names, a YAML syntax error at its mark) and `formatted` (the pack as keel saves it: `rules/packs.py normalize`). `POST /rules/packs` (the page's New rule, Edit, Import) writes the normalized text; the `rule-packs/install` routes keep the file as published. The web's `components/CodeEditor.tsx` (highlight.ts under a textarea, line numbers, errors at their line) edits the YAML.

## v0.17.4: criteria order, the amendment loop guard, Java-safe test names

Plan: `docs/plans/0.17.4.md`.

- **Build order**: the spec's `ORDER:` line (plain, `**ORDER**:`, in a list; the last one that names criteria) decides the order of `state.acs`, else the plan agent's `ORDER` marker, else the ids. Listed ids first, the rest after. The per-AC loop takes the first unfinished criterion in that list, so a resume keeps the order; an amendment keeps it (new criteria after the rest); `rules.gate_due` (end-of-lane, end) uses it. `state.data.ac_order = {ids, from: spec | plan | keel | person | id}`; the engine's thread state has `ac_order`; the spec gate says "in build order (from …)"; a flow started from a spec takes its ORDER at once.
- **Amendment loop guard** (feature workflow): `amend_entry` (branch) → `amend_loop` (gate, choices `first`, `skip`, `edit`, `stop`; kind `rounds`, never decided by a run mode) → `amend_first` / `amend_skip` (action `amend_reorder`, then `red`), `amend_edit` (`amend_with_note`, then `amend`), `amend_stop` (`amend_stop`: the flow stops with the person's note); after `amend_show`: `amend_changed` (branch on `data.amend.route == gate`) and `amend_none` (branch on `route == reorder`, else `amend_loop`) → `amend_reordered` (then `red`).
  `spec_amendment`: a round that changed nothing in the spec (`git status` of the spec file clean) while the explorer says it is not a spec defect, or the spec only holds an older block, no longer reaches `amend_gate`. If the explorer talks about order and the spec's ORDER (or the AMEND / the explorer) names unfinished criteria to build first, keel moves them before the current one (once per criterion; gate log "keel moved …") and goes back to red for the first; otherwise `amend_loop`. An explorer that wrote nothing and says nothing about it is still sent back to write the block. `amend_start`: the same criterion back with an AMEND after a round that changed nothing goes straight to `amend_loop` (no explorer). `data.amend_history` records each round `{ac, changed, said, reordered?, choice?, at}`; `data.amend.route` is `amend | ask | gate | reorder | moved`.
- **Gates** show the text a code step prepared even when branch steps sit between them (`compiler._code_before`).
- **Test commands**: placeholder `{AC_ID}` (AC-006 → AC006) next to `{AC}` and `{AC_KEY}`. A Maven `-Dtest=` or Gradle `--tests` filter on `{AC}` also matches the dash-free id (`-Dtest=*AC-006*,*AC006*`; a second `--tests '*AC006*'`). Maven is detected (`./mvnw` or `mvn`, `-Dtest='*{AC}*,*{AC_ID}*'`). `ran_no_tests` knows Surefire's "No tests were executed", "No tests matching pattern", "No tests to run" and Gradle's "No tests found for given includes"; `ac_test_passed` accepts the dash-free id. The red prompt says how to name the test (a Maven/Gradle project: `AC006` in the class or method name). Stack `kotlin-spring`: `api_test_ac` filters both forms.
- **MCP**: `keel_status` shows `order AC-6 → AC-2 → … (from …)`; text is cut at a word boundary with "…" (gate text up to 1500 characters); a gate with named choices lists them with what each does (only the ones its text describes), and `keel_next` says to choose one with `payload {"choice": "<name>"}`.
- **Fake model** `fake-lab` (real checks): the plan writes the order only into the spec's Plan (`**ORDER**: AC-2, AC-1`), the test author answers AMEND on AC-1, the amendment explorer says it is not a spec defect and writes nothing.

## v0.18.0: deleting a workflow, the workflow YAML editor

- **Delete**: `DELETE /api/workflows/{wid}` answers 409 `"<name> has a flow running (<title>): stop it or let it finish first."` while a thread of it is `running` or `waiting` (finished, stopped and failed flows keep their history and do not block it). keel's templates stay 409 `keel templates cannot be deleted`. A marketplace workflow is deleted the same way (its row holds the catalog record, so the Marketplace's "Installed here" follows). Build › Workflows: Delete on the open workflow's header, in its ⋯ menu (Remove for a marketplace one) and in the list row's ⋯ menu, with an in-page confirm (role alertdialog); a template shows "keel's own workflow: it can't be deleted (make a copy)" (New workflow with that template picked; keel has no way to hide or turn off a template).
- **Another workflow's YAML**: `PUT /api/workflows/{wid}` with YAML whose top-level `id:` is neither `wid` nor the id the stored YAML carries → 409 `"This YAML is a different workflow (id <id>). Use Import to add it as a new workflow."` (no `id:` = a rename at most). The web tells it before saving and offers Import (`POST /api/projects/{pid}/workflows/import`).
- **Errors at their line**: engine `POST /workflows/validate` → `{ok, errors, lines: [{line, message}], workflow?, formatted?}` (`workflows/editor.py error_lines`: the step and key a message names, a syntax error's mark, one line per field of a schema message). `POST /api/projects/{pid}/workflows/check` → InstallReview + `lines`; a refused save or import (422, and 400 when keel cannot read the YAML) → `{error, hint, lines}`. `errors` and `hint` keep the same texts.
- **Saved as keel writes it**: a save (`PUT /api/workflows/{wid}`) stores the engine's `formatted` (`workflows/editor.py normalize`): head comments kept, the head in the order `id, name, source, version, about, est_tokens, mcp, needs_plugins, based_on, keel_rules, budget, steps`, each step `id, kind, name, phase`, then the Step model's order with `instructions` last, unknown keys after; 2-space indent, a blank line between steps, short lists on one line. A file with comments below its head is kept as it is.
- **The starter**: engine `GET /workflows/starter?name=&id=&keel_rules=` → `{yaml}`: a small valid workflow (plan agent, plan gate, red / verify_red / green / verify_green per AC, a final gate). `POST /api/projects/{pid}/workflows {from: "blank"}` makes it (the old empty workflow only when the engine cannot answer); `GET /api/projects/{pid}/workflows/starter?name=&keel_rules=` → `{id, yaml}` shows it first.
- **Web**: `components/CodeEditor.tsx` edits the YAML tab, Import's "Or paste it" and shows New workflow's example and the marketplace workflow page's YAML (read only); errors are marked in the gutter and listed (a click jumps to the line); Save waits while a line is marked. `components/WorkflowHelp.tsx`: "The fields of a workflow".

## v0.18.1: keel's local api proves every caller

Plan: `docs/plans/agent-clients.md`, section 0. Until 0.18.0 a request without the MCP headers passed with no check:
any local program, or a web page through the browser, could approve a gate, start a flow or delete a secret.

- **Filter** `api/.../auth/LocalAuth.kt` (before McpAudit and McpLevelGuard), for every request: the Host must be
  `localhost`, `127.0.0.1`, `[::1]`, `*.localhost`, the host of `KEEL_PUBLIC_URL` or one of `KEEL_ALLOWED_HOSTS`, else
  403 (DNS rebinding). Outside `/api/` and `/internal/` (the web app's files, plugins' web files, `/web/login`) and for
  `GET /api/health` nothing more is needed. Otherwise, in this order: `X-Keel-Mcp-Token` (a level token: McpLevelGuard
  keeps the call within its level, whatever else it carries), `X-Keel-Token` (the internal token: the engine, keel2,
  keel's scripts), `Authorization: Bearer <web key>` (a person's terminal app: the keel TUI), the sign-in cookie (a GET
  passes with it; any other method also needs `X-Keel-Web: 1` and, when the browser sends one, an `Origin` that is
  keel's own, `KEEL_PUBLIC_URL`'s or one of `KEEL_DEV_ORIGINS`). A wrong token → 403; nothing → 401 with
  `X-Keel-Auth: sign-in` and `{error: "keel needs you to sign in", hint: "Run keel2 open …"}`. The request attribute
  `keel.auth.who` is `mcp`, `internal`, `person` or `browser`; McpLevelGuard only judges MCP calls (`who = mcp`, or
  `X-Keel-Via: mcp`). keel never answers CORS (no `Access-Control-Allow-*`, a preflight gets 401).
- **Web key** (`auth/WebSignIn.kt WebKey`): 32 random bytes, hex, in `$KEEL_DATA/run/web-key` (0600), made once, kept
  across restarts. The cookie is `keel_<first 8 hex of sha256(key)>` (two keels on localhost keep theirs apart),
  value the key, `HttpOnly; SameSite=Strict; Path=/; Max-Age=400 days`.
- **Sign-in**: `POST /api/web/code` (internal token only) → `{code, path: "/web/login?code=…", expires_in: 60}`; a code
  works once. `GET /web/login?code=&next=` sets the cookie and redirects to `next` (a path on this keel only) or `/`; a
  used or old code redirects to `/?signin=expired`. `POST /api/web/sign-out` clears this browser's cookie.
  `GET /api/web/me` → `{signed_in: true, via}`.
- **MCP level tokens** (`McpTokens`) are `HMAC-SHA256(internal token, "keel-mcp:<level>")`: the same while the
  container runs, also across an api restart (exit 75). The engine computes the read one (`config.mcp_token`) and hands
  it to keel's agents' own MCP server as `KEEL_MCP_TOKEN` (`tools/mcp.py keel_server_env`); `mcp_server.KeelApi` sends
  it when it has no internal token.
- **Web**: `web/src/api.ts` sends `X-Keel-Web: 1` and `credentials: "same-origin"` on every call (plugins' pages too,
  through `@keel/web-sdk`); a 401 with `X-Keel-Auth: sign-in` shows `components/SignIn.tsx` ("Run keel2 open", Try
  again). The Vite dev server forwards `/web/login` and sends the api's own origin.
- **keel2**: `api()` calls send `X-Keel-Token` read from the container (`docker exec … cat /data/run/internal-token`)
  or `~/.keel2/<name>/run/token`, through a pipe (`curl -H @<(…)`), never on a command line. `keel2 open [--print]
  [--dev]` opens (or prints) a one-time sign-in link; `start` and native start open one. `keel2 auth reset` deletes the
  web key and restarts keel. Test: `scripts/test-keel2-auth.sh` (CI `scripts` job).
- **Built-in rule `keel-keys`** (never off): no agent reads, searches, lists, changes or sends keel's own keys
  (`/data/run/…`, `~/.keel2/<name>/run/…`, `~/.keel2/<name>/data/run/…`, the engine's `$KEEL_DATA/run/`), and no shell
  command names them, `KEEL_INTERNAL_TOKEN`, `X-Keel-Token` or `X-Keel-Mcp-Token` (`rules.keel_key_file`,
  `KEEL_KEY_WORDS`; the hook checks every tool's path, also without a context).
- **Callers**: the e2e suites read the container's token (`e2e/keelauth.py`) and sign their browser walks in
  (`KEEL_SIGNIN`, parity `KEEL_SIGNIN_A` / `_B`); `keel-plugin dev` sends the token (`$KEEL_INTERNAL_TOKEN`, else read
  from the container); the api tests send it by default (`support/SignedIn.kt`, `ApiTest.bare` for none).
- **Limit**: a program of the same user that reads keel's files can still act as keel; the sign-in closes plain HTTP
  (other programs, other users, web pages) and the rule keeps agents away from the files.

### Phone access: paired devices (keel's Android app)

- **Pairing QR**: `keel://pair?u=<urlencoded base URL, no trailing slash>&c=<one-time code>&n=<urlencoded keel name>`
  (`%20`, never `+`). The keel name is `KEEL_NAME` (keel2 passes it), else `keel`.
- `POST /api/devices/pairing` (a person only: the cookie or the web key; never a device, an MCP token or the internal
  token) → `{code, expiresAt, urls: [{kind: tailscale|wifi|tunnel|local, url, qr}], qr, name, reachable}`. A code is 10
  characters (`A-Z` and `2-9` without `0 O 1 I`), valid 10 minutes, used once; `local` is the page's own address.
- `POST /api/devices/pair` (open: the code is the credential) `{code, name, platform}` →
  `{token, device: {id, name, platform, createdAt}, keel: {name, version}}`; a wrong, used or expired code → 403
  `{error, hint}`; 5 wrong codes a minute from one client (30 from all) → 429 for the rest of the minute, even for a
  right code. The device token is `kd_` + 64 hex; the table `devices` (V23) keeps only its SHA-256.
- Every app call: `Authorization: Bearer <device token>` (no `X-Keel-Web`): the person's access (`keel.auth.who` =
  `device`), except the device routes above and below and `/internal/**`; `last_seen_at`/`last_ip` are written at most
  once a minute. An unknown or removed token → 403 `{error: "This device was removed", hint: "Pair it again in keel:
  Settings › Devices"}`. `GET /api/devices/me` (a device) → `{device, keel}`.
- `GET /api/devices` (a person) → `[{id, name, platform, createdAt, lastSeenAt, lastIp}]`; `DELETE /api/devices/{id}`
  removes the row (its token stops at once). These bodies are camelCase (the app's contract), unlike the rest of the api.
- `PUT /api/devices/addresses {urls: [{kind, url}]}` (the internal token: keel2) replaces where keel is reachable,
  kept in `$KEEL_DATA/run/addresses.json`; `GET` for keel2 or a person. keel2 sends it after every start and share
  change (`keel2 start --wifi`, `keel2 share tailscale|tunnel|off`).
- **Host and origin**: besides the names above, `LocalAuth` accepts an IP-address Host (a phone on the Wi-Fi; DNS
  rebinding always comes with a name) and the hosts and origins of the registered addresses (Tailscale, tunnel).
- **Wrong tries** (`auth/AuthLimiter`): wrong web keys, cookies, device tokens and sign-in codes count per client
  (Cf-Connecting-Ip or the last X-Forwarded-For when a local proxy calls, else the address): 20 a minute from one
  client, 200 from all → 429 with `Retry-After`. A direct call from this computer (loopback, no proxy header) is never
  counted; the internal and MCP tokens never are.
- **keel2**: `--wifi` / `KEEL_BIND` publish `-p <bind>:<port>:8080` (label `keel2.bind`, kept by restart) or native
  `SERVER_ADDRESS`; `share tailscale` runs `tailscale serve --bg --https=443` (keel's port when another serve has 443)
  and `share off` runs `tailscale serve --https=<that port> off` only; `share tunnel` runs `cloudflared tunnel --url
  http://127.0.0.1:<port>` in the background, else `cloudflare/cloudflared:2026.9.1` (`--network host` on Linux,
  `host.docker.internal` on Docker Desktop) as `<KEEL_NAME>-tunnel`. State: `~/.config/keel2/<KEEL_NAME>.share/`.
  Test: `scripts/test-keel2-remote.sh` (fakes only).

## v0.18.1 (orchestration): orchestration fixes (stall ladder, cost, fewer stops)

Plan: `docs/plans/0.17.5.md`.

- **depends_on**: plan lines `- AC-n depends on: AC-x, AC-y` (also `(depends on …)`, `after …`, `needs …`) give each criterion `depends_on` in `state.acs`; the per-AC loop takes the first unfinished criterion whose dependencies are finished (list order when none is).
- **A test command that cannot start** (`testcmd.cannot_run`: a missing `./mvnw`, a wrong folder) in `verify_red` / `verify_green` is no longer "red confirmed" or a retry of the agent: the check asks `{type: "cannot-run", kind: "fix", choices: ["recheck", "skip", "stop"]}` (no `skip` for the whole suite). Run modes never decide it.
- **Choice asks of a check** (`ActionResult.ask` with `choices`; `compiler._choice_answer`): `recheck` runs the check again, `retry` sends the agent back with the person's note, `skip` moves the criterion to the end of the list, `edit` starts an amendment for it with the note, `stop` stops the flow. Skip and edit put the criterion's uncommitted work back (keel's agents' files only). Gate log `"<type> <choice>: <note>"`. The Flow page shows one button per choice for a `fix` pause too.
- **Stall ladder** (a per-AC check in a per-AC loop that has a retry target and no `after_rounds`): round 1 the same agent with the failure; round 2 an `investigator` (phase review, read only) reads it and proposes, its text joins the feedback (`data.ladder_proposal`); round 3 the agent's model is escalated once (`data.ladder_escalated = {agent, ac, before}`, restored when the loop moves to another criterion); after `fix_attempts` (default 3) rounds: `{type: "stuck", choices: ["retry", "skip", "edit", "stop"]}`; `retry` is one more round, then the question again. Gate log `stall <AC> round <n>: …`.
- **BLOCKED** marker (registered, free text) on `red` and `green`: `BLOCKED: <tooling | needs AC-x>: <what>`; the implementer's `GREEN-RESULT: stalled` counts as BLOCKED (`markers.blocked`). Branches `red_blocked` / `green_blocked` → `red_stuck` / `green_stuck` (action `stuck`): unfinished criteria it names are built first (gate log "keel moved …", `data.ac_order.from = keel`); otherwise `{type: "blocked", choices: ["retry", "skip", "edit", "stop"]}`.
- **Failed agent calls in the flow's usage**: what a failed call used (`ModelError.usage`: an error, a turn limit) is kept in `ThreadContext.unbooked` and booked into `state.usage` by the next agent step, the finish, or a failed end. The Flow page, `keel_status`, `keel_flows` and the cap now count it like the Budget page (`agent_calls`).
- **Start a flow** (web): while the cap is keel's default (nobody saved `cap_tokens`, B15), it follows the estimate's high end plus 30% (rounded up to 10k) and says so; a cap below the estimate's high end shows a warning.
- **Out of turns**: a `ModelError` with `max_turns` (claude `error_max_turns`) continues the agent's own session once with a "continue" prompt (`compiler.run_or_continue`: claude `--resume`, codex `exec resume`; the session from the step's memory, else the request's). The cut-off run's tokens are added to the result; a second limit is raised with both counted.
- **Fewer approve-only stops**: the plan ends with `CONTRACT-DELTA: yes | none` (or says "Contract delta: none"); `spec_sync` sets `data.contract = yes | no`; `contract`, `contract_diff`, `contract_gate` and `contract_commit` have `when: {data: contract, equals: "yes"}`. `show_diff` in phase contract with nothing written sets `data.contract = "no"`. An amendment that moves the API shape sets it to `yes` again. Step field `menus: true` (gates; feature's `spec_gate`): the gate's text lists the skip menus after it and its approval's `payload.skip` (and `lenses`) decides them; the menus then log `gate <id> approve: chosen at spec_gate` and do not stop. An unknown name or a skip without a reason still asks at the menu. Feature flow, run mode important, 3 criteria on the fake model: 7 stops → 5 (4 without a contract delta).
- **A gate's exits for this time**: `data["<gate id>_without"]` lists dict-choice exits a step before the gate ruled out; the question leaves them out and an answer that names one is asked again (gate log `unknown choice`). `amend_loop` has no `first` when no other criterion is unfinished (`data.amend_loop_without = ["first"]`).
- **MCP `keel_start_flow(allow_fake)`**: a write-level argument, sent as the start request's `allow_fake` (the web's "Run with the fake model anyway"). Without it, the fake-model refusal (409) says to call again with `allow_fake=true`; keel does not start a fake run on its own.

## v0.18.9: thinking effort

- **Model.effort** (`low | medium | high | xhigh | max`, or a provider's own word such as Codex's `minimal` / `ultra`):
  optional on every model (agents, KeelBot, settings). None = the engine's own default. The api keeps it lower case and
  treats `"default"` or `""` as none (`Model.checked()`: agent override and custom agent, KeelBot's create / patch /
  turn); anything that is not one short word is a 400 (`effort cannot be "…"`).
- **Which models take one** (engine `models/catalog.py efforts_for(provider, mode, model)`; `[]` = no effort setting,
  `None` = keel cannot tell and passes it on): claude every model but Haiku 4.x (`CLAUDE_EFFORTS`); codex `None` (the
  CLI / API checks it; the catalog lists each model's own from Codex's cache); copilot `opencode` mode
  `opencode_efforts(model)` (Gemini none, Claude low..high, GPT low..high plus xhigh from GPT-5.1 on); copilot
  `subscription` and `api` none; fake `low, medium, high` (recorded only). The catalog shows the same: a model's
  `efforts` ([] for Haiku), else the provider's.
- **`effort_for(model)`**: the effort a runner passes on, or None when the model does not take it (dropped, not an
  error). Runners: Claude Code `--effort <e>`; Codex `-c model_reasoning_effort=<e>`; OpenCode `--variant <e>` (before
  the prompt); API key Claude `ChatAnthropic(output_config={"effort": e})`, Codex `ChatOpenAI(reasoning_effort=e)`;
  Copilot CLI and GitHub Models none; the fake model emits a `thinking` step `Thinking effort: <e>.`.
  `POST /providers/test` takes `effort` too.
- **Events**: `agent.started` and KeelBot's `helper.started` carry `data.effort` (what was passed on, else null);
  KeelBot's answer message `data.effort` (only when set).
- **History**: Flyway `V19__agent_call_effort.sql` adds `agent_calls.effort` (from the started event); `Job.effort`
  (`GET /api/jobs`, `/api/jobs/{id}`) is null for the engine's default.
- **MCP**: `keel_agents` and `keel_agent` show `provider/model · effort e`; `keel_agent_definitions` shows each agent's
  `model provider/model (mode) · effort e|default`. `keel_agent_create` takes optional `provider`, `mode`, `model`,
  `effort` (from the project's default model). New write tool `keel_agent_model(agent, provider?, mode?, model?, effort?,
  chat?)` (asks first, `CONFIRM`): agent id or `keelbot` (the `helper` agent; with `chat`, PATCHes that KeelBot chat's
  model). Another provider starts on its catalog default; a new model keeps the effort when it has it; `default`
  clears it; an effort the model lacks is refused with its choices.
- **keel TUI**: `/model` picks the effort after the model when the model has choices; `/effort [level]` (commands.json,
  group KeelBot) sets or clears it. Job lines, notices and KeelBot's turn footer show `provider/model · effort`.
- **Web**: `Prov` takes `e` (Jobs, Live, Flow, run search); `ModelPicker` says "no effort setting" for a model without
  choices; Agents rows, StartFlow, Budget's cheaper model, the step drawer and KeelBot's answer footer show the effort.

## v0.19.0: one way to ask (questions and choice_info)

- **A question** keeps runtime/clarify.py's shape: `{id, question, why, options: [{label, description, recommended}],
  multiple?: true}`. Answers go back as `payload.answers = {<id>: <label> | [<labels>] | <own words>}`. A "multiple"
  question takes a list; a form ticks every `recommended` option of it by default.
- **`waiting.choice_info`** (engine `runtime/choices.py`) on every pause that has named `choices`, in the same order:
  `[{name, label, description, why: "required" | "optional" | "none", default}]`. `name` is what the resume sends as
  `payload.choice`; `label` the button text; `description` the one line of meaning (the gate's own "- name: …" prose
  when it has some); `why` whether a note is required (the resume is refused without one: 400 `Say why when you choose
  "<label>".`; `why`, `payload.reason` or `payload.note` count), read by the handler (`optional`) or not read (`none`);
  `default` marks the one choice a plain approve takes (`_default_choice` / the first / the `recommend` marker).
  `choices` and the prose stay for old clients. `GET /api/inbox` items carry `choice_info` and `questions` as they are.
- **Labels, reasons, defaults** (`runtime/choices.py CATALOGUE`, keyed by `<flow>:<step>` or the kind of a check):
  required = a send-back exit `reject` (spec gate: "Start the interview again"), cover `decide`'s `accept` ("Accept as
  uncovered"), the hunt triage's `close` ("Close a finding"), a for_each item's `skip` ("Skip this item"). Stuck,
  blocked and cannot-run checks: retry "Try again" (optional), skip "Skip this criterion" (none), edit "Amend the spec
  first" (optional), recheck "Check again" (none), stop "Stop the flow" (optional); a plain approve takes the first.
  Plan windows (`usage`): continue "Continue anyway" (default), wait "Wait for the reset", cheaper "Use the cheaper
  model", stop "Stop the flow".
- **init's `questions` pause** is `kind: "clarify"` with three questions (ids fixed): `run` "Where does the app run while
  you develop?" (this machine / dev container / decide later, and `compose service <name>` first when the docs say the
  tools run in a compose service's container or one compose service builds the project and mounts its folder; that
  answer writes the backend commands as `docker compose exec -T <name> ...`), `services` (a compose file: `use <file>`,
  written as `commands.services: docker compose up -d --wait` / `I run them myself`; none: `none` / `I run them myself` / `generate a compose file`), `knowledge` (`multiple: true`: architecture,
  domain, conventions, data, integrations). The recommended options are keel's defaults (a fast init recommends no
  section). `init_gates.from_answers(root, answers)` maps the answers to `init:`; approving without answers takes the
  defaults; options `approve` ("Use these answers") and `reject` ("Use my answers": an old client's typed words, read
  by the keyword parser `init_gates.answers`). Run mode auto answers it with the recommended options (every
  recommended label of a multiple question).
- **Gates with questions beside their choices** (`waiting.questions` on a `kind: "gate"`): the hunt's `confirm_lenses`
  (`lenses`, multiple, the proposed set recommended; `payload.answers.lenses` = `payload.lenses`), the hunt's `triage`
  (`finding` = an open finding or group, `as` = fixed | accepted | wontfix; with choice `close` and a note, same as
  `payload {id, as}`), and the skip menus (`skip`, multiple, nothing recommended; options are the skippable steps with
  their band as description) on the menu gate and on a gate with `menus: true`. Skips: `payload.answers.skip` plus
  `payload.reasons {<step>: <why>}`, or `payload.skip {<step>: <why>}` as before.
- **for_each item gate** (no `choices` of its own): `choices: ["approve", "skip"]`; skip needs a reason (`why` or
  `payload.reason`); `payload.skip: true` still works.
- **MCP**: `keel_status`, `keel_next` and `keel_inbox` show questions (why, options with descriptions, recommended,
  pick one / pick any) and choices from `choice_info` (label, description, plain approve, needs a reason).
  `keel_approve_gate` and `keel_answer` take `answers: {id: label | [labels] | words}`, `choice` and `why` (a choice
  that needs a reason is refused before it is sent). `keel_answer(ask_person: true)` asks the person through the
  client's elicitation form (an enum per single question, a list of an enum per multiple one, an own-words field each,
  the choices as an enum of their labels, a why field) and sends the answer; a client without forms gets the question
  as text. KeelBot's ```keel-questions``` come back from `keel_keelbot_ask` as a hint to send the answers with
  `keel_keelbot_ask(chat, text="Answers to your questions: …")` (`clarify.answers_message`, the web card's format).

## MCP catches up with the dashboard and keel TUI (feat/mcp-up)

- **Any question gate, from its data.** The read tools show every waiting question from `questions` and `choice_info`
  (a question with no options says "your words"); `keel_inbox` adds how to answer each item (its `thread_id`). Nothing
  in the MCP server knows one gate: the feature interview's lens questions, per-finding security decisions, "ready to
  ship?", the init doctor's rung choices and review-pr's share-sections pick all work from these shapes.
- **Asking in a form.** `keel_approve_gate` and `keel_answer` with `decision: approve`, no `answers` and no `choice` on a
  `kind: clarify` gate ask the person through elicitation when the client supports it (an enum per single question,
  a list of an enum per "multiple" one, an own-words field each — the only field for a question without options —
  the choices as an enum of their labels, a why field), then send `payload {answers, choice}` and `why`. A client
  without forms gets the questions as text and nothing is sent. `ask_person: true` (now on both tools) asks for any
  waiting gate.
- **Flows.** `keel_start_flow` with no `type` lists every flow `GET /projects/{pid}/workflows` returns, one line each
  (keel's own from a fixed table, others from the YAML's `about:` or first comment line) with the inputs its YAML
  comments list (`Inputs` / `Options`, one `#   key  what` line each). It takes `title`, `request` (or `idea`), `ticket`,
  `pr` and `inputs`, sent as the start body's `title`, `request` and `options` (`ticket`, `pr`, the inputs and
  `started_via: "mcp"`); a flow with only a ticket or PR needs no request. `keel_flows` adds the same list after the runs.
- **Projects (admin).** `keel_pause` (asks first) stops a running or waiting flow (`POST /threads/{tid}/stop`, every
  finished step saved); `keel_resume` without `decision` goes on with a stopped or failed flow from its last checkpoint
  (`POST /threads/{tid}/rewind`, as `keel_resume_flow`). keel has no separate cancel route: a paused flow never resumed
  is cancelled, `keel_delete_run` takes it off the history. `keel_focus` reads `GET /api/ui/focus`, and with `project`
  (and `chat`) switches the dashboard and keel TUI to it (`PUT /api/ui/focus {project, chat, by: "mcp", client}`; the
  api needs the write token for it; keel 0.18.63: only the windows that follow the others). `keel_add_project` is unchanged.
- **KeelBot.** The server's instructions say how to answer questions and keel-questions cards and the failure rule;
  `keel_keelbot_ask`'s hint for a keel-questions card adds the failure rule (never run the failed command again
  unchanged, the person picks one of the ways forward).

## Stack setup: a stack's `setup:` block (feat/stack-setup, keel 0.18.22)

A stack pack may have a `setup:` block. Every reader of stack packs checks it (`runtime/stacks.validate_setup`): a
stack with a broken block keeps everything else; its setup is left out and `_setup_errors` says why. The stack check
(`POST /stacks/check`) reports the same errors, and says that `setup.run` runs as keel's user (not root).

```yaml
setup:
  apt: [package, ...]            # Debian package names: ^[a-z0-9][a-z0-9+.-]*$
  run: [command, ...]            # after apt, as keel's own user (never root), once per container
  each_worktree: [command, ...]  # in the lane's folder (backend.dir / frontend.dir) of a new flow worktree
```

**System part** (`runtime/stack_setup.py`, `SystemSetup`). The union of `setup.apt` and `setup.run` over every
registered project (the engine's `project_index` rows, the folders it was told about in `$KEEL_DATA/setup/roots.json`,
and `KEEL_FOLDERS`). It runs in a background thread at the engine's start, on `POST /projects/{p}/scan`, after a stack
is saved, removed or turned off, and before a flow that needs it. It never blocks the start.

- Container: the engine runs `sudo -n /usr/local/bin/keel-stack-setup apt <packages>`. The helper
  (`docker/keel-stack-setup`) is root-owned and does one thing: `apt` with Debian package names
  (`^[a-z0-9][a-z0-9+.-]*$`, checked before apt runs). Any other first argument is refused (exit 2).
  `/etc/sudoers.d/keel-stack-setup` is `keel ALL=(root) NOPASSWD: /usr/local/bin/keel-stack-setup apt *`: nothing else
  runs as root. `setup.run` commands run as keel's own user (`bash -c`, in keel's home), never through sudo.
  A project's own stack (`_source` project) runs its setup.apt, setup.run and
  setup.each_worktree only after a person approved them: an Inbox card of kind `stack-setup`, approval stored in
  `<data>/setup/approved.json` keyed by sha256[:24] of (project, stack, run, each_worktree, apt); a marketplace install
  (`/stacks/save` source marketplace) records it. `GET /setup/status` has `state: "needs approval"` and `approval: [...]`
  while some wait. Packages already installed (`dpkg-query`) and run commands already run in this container (`~/.cache/keel-stack-setup/
  <sha256[:16]>`) are left out. The apt cache is `/var/cache/keel-apt` (root-owned, outside the
  data volume, lists fetched fresh each time, so keel's user cannot plant packages); a new container downloads again.
- Native mode (`KEEL_NATIVE=1`) and any engine without the helper never install. The status says which programs are
  missing (a package's program: `php8.3-cli` → php, `golang-go` → go, `composer` → composer).
- Status: `$KEEL_DATA/setup/status.json`, and `$KEEL_DATA/setup/packages.json` (what was installed, for whom).

```
engine GET  /setup/status   → {state: idle|running|ready|failed|missing, mode: container|native|host|null, line,
                               busy, missing: string[], failed: string[], log: string[], at}
engine POST /setup/run      → the same (looks again in the background)
api    GET  /api/setup      → the engine's /setup/status (needs a login; /api/health stays without the line)
```

The dashboard shows `line` while `state` is running, failed or missing ("setup: installing php8.3-cli for
billing-router…").

**Flow part.** Every flow starts at the node `__setup`. It is silent when there is nothing to do. Otherwise it is a
step named "set up the stack", with a code job (`code.started`, `code.step` per command with its output,
`code.finished`):

1. The stacks' packages: in the container it asks the system part and waits (a line every 5 seconds, at most 30
   minutes). Elsewhere a missing program is only a note, unless a command needs it.
2. `each_worktree` for each stack with work here: a linked worktree not set up yet with these commands, or a folder
   with a `dependency_files` entry and without what it makes (`composer.json` → `vendor/`, `package.json` →
   `node_modules/`). The same commands in the same folder run once. Each job is recorded in the worktree's git dir,
   `keel-setup.json` (`{"<stack>:<dir>": {ok, sha, at}}`), so it goes with the worktree.

A failure pauses at `__setup_gate`: `waiting = {step: "__setup", kind: "gate", choices: [retry, skip, stop],
choice_info}`. The run mode never answers it (kind `failure`). retry runs `__setup` again; skip goes on and records
`{skipped: sha}` so it is not asked again for the same commands; stop ends the flow (`status: stopped`). A KeelBot side
session's new worktree runs `each_worktree` in the background (no gate).

**Init.** The plan lists a `setup:` block for each project stack of a language the image lacks (PHP, Go, Ruby, Rust)
that has none. `write_config` (after the plan gate) appends it to the stack's file. **Setup check**: rung 1
(Toolchain) also looks for each stack's programs, and says "php not found; stack php-symfony lists setup.apt; keel
installs it at start" (native: "install it yourself", with the packages).

**Guard.** A shell command that names `keel-stack-setup` is refused in every phase (bucket `keel-setup`).

## 0.18.28: the suite outside each criterion, the integration way out, run modes, KeelBot and MCP

### Flows (engine)

- **`verify_after_green`** (a code action; feature, change and small run it as `ac_suite` after each green, `back:
  green`). `.keel/config.yml`: `checks.after_green: none | changed | suite` (default `changed`), `checks.suite_every: N`
  (default 3). `changed`: the branch's test files through `commands.api_test_paths` / `web_test_paths` (`{PATHS}`),
  else the module command plus the paths for pytest, vitest, jest, mocha, else the module suite; the whole suite every
  N finished criteria and for the last open criterion of its lane. Failure: note `"<AC>'s change broke tests outside
  it: <tests>"`, the failure detail, `update.unlocks` (the criterion's own test files, phase green, by keel) and
  `data.after_green[<AC>] = {ok, ran: none|changed|suite, why, failing?}`; the AC gate shows it under "Outside this
  criterion". A failing code step now keeps `suite_failure`, `data` and `unlocks` of its result.
- **Already met**: approving "already passes" sets `data.already_met[<AC>] = {auto, why}` and goes to the criterion's
  `verify_after_green` step (else, as before, the loop's end). Approved by a person (`auto: false`): the criterion-reviewer
  step is skipped and the AC gate passes through (log `ac <AC> approve: already met, approved at "already passes"`).
  Approved by the run mode: the criterion-reviewer reads the test (isolation, cleanup, assertions) and the gate is the run
  mode's. The status stays `already-met`. The branch-reviewer's prompt lists every already-met criterion under "Not
  reviewed per AC".
- **A failing whole suite** (`verify_green` without a criterion): the detail is `$ cmd`, `exit N`, the runner's own
  failure report (`runtime/suite_failure.excerpt`), and when the output names no failure (or is empty) a second run with
  a JUnit report (`--log-junit` for phpunit and `composer test`, `--junitxml` for pytest) and its failures. In a flow
  with criteria (not the bug flow, not simulated) `update.suite_failure = {failing, alone: [{name, command, passed, ran}],
  pollution, suspects: [{file, acs, writes, commits, cleanup}], likely_ac, evidence, layer, command}`; a failing test is
  run alone with `commands.api_test_one` (`{TEST}`) or the criterion command with the test's short name.
- **The suite question** (a code step outside the loop whose check left `suite_failure`): with `pollution` or a
  `likely_ac` it asks at once, else after the agent's rounds. `kind: fix`, `type: suite`, choices in this order (those
  that apply): `send_back_ac`, `keelbot_fix` (only when a part has the `flow_fix` hook), `retry`, `stop`, each in
  `choice_info`; `reject_choice` = the first. Answers: `send_back_ac` sets the criterion to `todo`,
  `data.reopen = {ac, to: "green", from}`, the feedback (the failure, the analysis, the person's note) and unlocks of
  its suspect test files, and enters the loop, whose begin node takes `data.reopen` (that criterion, at that step);
  `keelbot_fix` awaits the hook and runs the step again (event `keelbot.fix {session, status}`); `retry` runs it
  again; `stop` stops with the reason. Past `settings.sendback_limit` only `retry` and `stop`. Gate log `suite <step>
  <choice>[: why]`.
- **Part hook `flow_fix`** (`keel_engine/extensions.py`): `async flow_fix(project, root, model, keys, text, thread,
  title) -> {session, status, answer}`. KeelBot's opens an Edit chat in `root` titled `Fix: <step name>` and waits for
  its turn.
- **The interview** (`_clarify_gate`): an answer that asks back (`clarify.is_confused`: "what do you mean", "?",
  "explain", "I don't understand", a sentence ending in "?"; never an option's label) is no answer. Those questions are
  asked again once at the same gate as `clarify.explained(q, said)` (`explained: true`, `asked_back`, the question with
  "What it decides" and each option as an example; title `<title>: in plain words`); the other answers wait in
  `clarify.kept_questions` / `kept_answers` and join the next round. Asked back again: the words go on with "do not
  pick an option for them". Event `gate.decided {decision: "explain", clarify: true}`.
- **`when`**: `gt`, `gte`, `lt`, `lte` compare the value's first number; a marker's `"0"` no longer counts as set; a
  key outside `marker, data, state, step, equals, in, absent, any, gt, gte, lt, lte` is a validation error.
- **A critic in a loop**: a step whose declared marker a later branch with `no:` reads (`when.step` names it or no
  step) is not a review step (`Compiler._branch_decides`): its findings never open the `<id>__fix` gate.

### Waiting questions (engine `GET /threads/{id}`)

- `waiting.name`: the waiting step's name (a review's fix node: its review's).
- `waiting.plain = {approve: {does, reason, choice?}, reject: {...}}` (`runtime/choices.plain_effects`): what a plain
  approve and a plain reject do and whether the reason is `required`, `used` or `not used: …`. A question's producer
  may set its own `plain` and `reject_choice`.
- The api's Inbox items carry `plain` and `step_name`; KeelBot's turn body carries `waiting.name`, each choice's
  `description` and `plain`.

### Run modes

- `POST /api/projects/{pid}/flows` from the start form always sends `run_mode`. `keel_start_flow` takes `run_mode`;
  keel TUI `/flow <wf> --mode <m> <idea>`; a `keel-start` block takes `"mode"`. A child flow keeps its parent's current
  `settings.run_mode` (start_child). `keel_run_mode` names the thread it changed.

### KeelBot (plugins/keelbot)

- `POST /helper/sessions/{sid}/turn` while a turn runs: no 409. `{session, queued: true, position, text}`, event
  `helper.queued {session, text, position}`; the queued turns go in order when the running one ends (also after a
  stop). `interrupt: true` stops the running turn first and sends this one now. `GET /helper/sessions/{sid}` has
  `queued: [text]`. The api's turn body takes `interrupt`.
- The prompt's flow block lists the waiting gate's choices with what each does, whether a reason is used, and the
  plain effects, with the rule never to suggest a choice whose effect does not match.

### MCP

- New read tool `keel_read_gate(project?, flow?, page=1)`: the gate's name, title, choices (with what each does),
  options, plain effects, questions and its whole text (at a spec gate also the spec), `GATE_PAGE` = 12,000 characters
  a page: `text page 1 of 3 (N characters)` and `page 1 of 3: the next is keel_read_gate(...)`.
- `keel_status` shows up to 4,000 characters of the gate's text, then `(the gate's text goes on: N characters, P
  page(s); keel_read_gate(...) reads it whole)`; `keel_status`, `keel_next` and `keel_inbox` list the plain effects.
- `keel_edit_gate(notes)`: approve with the gate's first edit choice (`edit`, `change`, `rewrite`, `context`, `reject`)
  and the notes as why; a plain reject where the gate takes one; else a message naming the choices.
- The server runs sync tools in worker threads, answers a tool's exception as `keel: <tool> failed (...)`, logs to
  stderr, and `main` serves stdio on the real stdout with `sys.stdout` pointed at stderr.

### Stacks and languages

- A stack's optional `languages: [{name: <highlight.js name>, ext: [<file ending>]}]` (`runtime/stacks.validate_languages`,
  `stack_edit.check`, keel-plugin lint). Without it: one per `detect.extensions` ending known in
  `content/highlight-languages.json` (`names`, `extensions`). keel's packs and their marketplace copies carry it.
- Engine `GET /stacks/languages?root=` and api `GET /api/projects/{pid}/languages` → `{languages: [{name, ext, stacks}]}`
  for the stacks the project uses (detected or its own, not turned off). Saving, removing, turning off, creating or
  installing a stack publishes `project.changed {id, stacks: true}`.
- Web: `highlight.ts` builds in its base languages and loads the others with `import()` on first use
  (`loadLanguage`, `useLanguage`); `setStackLanguages` maps the project's stack endings first, the static map after.

## Review agents' names (feat/review-agent-names)

The four review agents have names that say when each one runs. Their prompts did not change.

| old name          | new name             | runs                                                                 |
|-------------------|----------------------|----------------------------------------------------------------------|
| `ac-reviewer`     | `criterion-reviewer` | one criterion's RED and GREEN commits, at its AC gate, during a flow |
| `code-reviewer`   | `branch-reviewer`    | the whole branch diff, once, at the end of a flow                    |
| `reviewer`        | `lens-reviewer`      | a diff against the spec through one lens (ship, cover, review)       |
| `ticket-reviewer` | `pr-reviewer`        | someone else's PR: checklist items, a bug hunt, or one claim         |

- One alias table on each side: engine `keel_engine/agent_names.py` (`RENAMED`, `canonical`, `canonical_keys`, `lookup`)
  and api `keel.api.agents.AgentNames`; the web and keel TUI share `web/src/agentNames.ts` for names a person types.
- An old name is never listed (Agents page, `GET /api/projects/{pid}/agents`, `keel_agent_definitions`), and a plugin
  cannot bring an agent file with one. An old name passed in still works: a workflow step's `agent`, an agent lane's
  `sub` and a step `model` that names an agent (engine `Step`/`Lane`, api `WorkflowDoc.parse`), `PUT
  /api/projects/{pid}/agents/{old}` (saves under the new name), skill assignments, `keel_agent_model`,
  `keel_skill_attach`, a marketplace workflow's review and `requires.agents`, and the engine's agent file lookup
  (`addons.agent_path`, role, maxTurns, knowledge).
- Engine `POST /workflows/validate` stays `ok` for an old name and adds `notes: ["Step '<id>': agent '<old>' is now called
  '<new>'; the old name still works."]` (only when there is one).
- Stored settings: api migration `V21__review_agent_names.sql` moves `agent_overrides` rows and the names in
  `skill_assign` once; a row already under the new name wins. Reading still maps an old key to the new name (new wins),
  and so does the engine for a thread's `models`, `skills`, `agents` and `state.agent_models` (a flow started before).

## v0.18.46: local models and Vertex AI (feat/local-models)

Two providers whose connection is more than one key. Both run in `mode: "api"` through the API runner (LangChain).

- **`local`**: model servers on this computer or the network: Ollama, LM Studio, vLLM, llama.cpp server, any
  OpenAI-compatible URL. A model id is `<server>/<model>` (`ollama/qwen3-coder:30b`). No effort. Cost $0 unless the
  server sets a price. Built in core (not a plugin: a plugin cannot add a provider to the catalog, the runners and
  the api's key routing yet), off until a server is added; the switch is `PUT /api/providers/local {enabled}`.
- **`vertex`**: Claude (Anthropic on Vertex, efforts as the Anthropic API) and Gemini (Vertex's OpenAI-compatible
  endpoint, no effort) in a Google Cloud project. Built-in model lists.

```
GET    /api/providers/local                         → { enabled, endpoints: [{ name, kind, url, context?, price_in?, price_out?, key_set, key_hint? }],
                                                        kinds: string[], defaults: { [kind]: url } }
PUT    /api/providers/local                         { enabled } → same
PUT    /api/providers/local/endpoints/{name}        { kind: ollama|lmstudio|vllm|llamacpp|openai-compatible, url, key?, clear_key?, context?, price_in?, price_out? } → same
DELETE /api/providers/local/endpoints/{name}        → same
POST   /api/providers/local/endpoints/{name}/test   → engine POST /providers/local/test → { ok, ms, models?: [{id, label, context?}], error?, hint? }
GET    /api/providers/vertex                        → { project, region, credentials: "service-account"|"adc", key_set, client_email?, ready }
PUT    /api/providers/vertex                        { project, region, credentials_json?, clear_credentials? } → same
```

- Secrets: a server's key is `local.<name>`, the service-account key `VERTEX_CREDENTIALS`; neither is ever sent back.
- The engine gets each connection as the provider's key (a JSON string): `keys.local = {"enabled": true, "endpoints":
  [{name, kind, url, key?, context?, price_in?, price_out?}]}`, `keys.vertex = {"project", "region", "credentials"?}`.
  The api's `SecretService.keyForProvider` gives it, so flows, KeelBot, the review check and `POST /providers/test` carry it.
- Engine `POST /providers/models {keys}`: the catalog with `local` and `vertex` when they are set up (the api calls it
  instead of `GET` when one is). A local model entry may carry `badge: "local"`, `tools: false` (it cannot call tools)
  and `context` (tokens); the provider entry `source: "server"` and `problems?: string[]` (a server that did not answer).
- Tool calls: Ollama's `/api/show` capabilities, else one probe per model, remembered in `$KEEL_DATA/local-models.json`.
  A model without tool calls runs only ask-type calls (a read-only call outside a flow phase: KeelBot Ask, summaries;
  `POST /agents/ask` sends no tools); a code step answers `{error, hint}` ("cannot call tools").
- Context: the server's setting, else Ollama's `num_ctx` (4096, its default, when the model sets none; never above the
  model's trained length), else the `/v1/models` length, else 8192; the prompt is packed
  to fit (old turns first, then the middle of the prompt).
- Setting `local_only` (bool, Models section, per project): a flow start, a KeelBot turn or a review check on a model
  whose provider is not `local` (or `fake`) is refused with 400 `{error, hint}`.
- `GET /api/connections` lists `vertex` and `local` (one mode, `api`; `key_set` = set up).
- keel2: every container gets `--add-host host.docker.internal:host-gateway` (Linux), so the default server URLs
  `http://host.docker.internal:11434` (Ollama) and `…:1234/v1` (LM Studio) reach the host; a native keel uses 127.0.0.1.

## keel 0.18.55: init when a project is added (feat/init-on-add)

```
Project += {configured: boolean}   (the project has a .keel/config.yml: keel knows it; the init flow writes it)
```
- Additive: `GET /api/projects`, `GET /api/projects/{pid}`, `POST /api/projects` and `POST /api/projects/bulk`'s
  `added` carry it. Same test as the engine's `rules` config (`configured`). A client that does not get it (an older
  keel) treats the project as known and shows no init hint.
- Web: after **Add projects** (and the first time a project found by itself is opened on the Flow page) a card
  **Set up keel for <project>** offers **Set up now (recommended)** (starts `init` and opens it) or **Later**. An
  unconfigured project shows a banner on the Flow page; **Start a flow** and the Tasks **Start** ask once
  ("keel does not know this project yet": **Run init first (recommended)** / **Start anyway**).
- keel TUI: the start box and `/project` say `/flow init`; `/flow <other>` asks the same once; `/flow init` needs no idea.
- MCP: `keel_start_flow` (with or without a type) puts one line about `init` first for such a project; it starts
  what it was asked to start, as before.

## keel 0.18.57: project notes opt-in, KeelBot's graph default, Where to look, the folder's branch (feat/knowledge-opt-in)

```
.keel/config.yml   knowledge: { build: on | off }      (YAML 1.1 bare on/off are booleans: both read the same)
Notes            = { on: boolean, setting: "on" | "off" | null, written: int }
Memory          += { notes?: Notes }                    (GET /api/projects/{pid}/memory)
Wiki            += { notes?: Notes }                    (GET /api/projects/{pid}/wiki; the Wiki plugin)
FolderActivity   = { chats: {id, title, mode, status, updated_at}[], flow: string | null, worktree_ok: boolean | null }
HelperMessage.data += { folder_branch?: string, branch_switch?: {from, to, at: string | null, by: string, during?: true} }
HelperSession   += { folder_branch?: string }           (GET one session, Ask and Edit chats only: live)
```
- **The switch** (engine `runtime/knowledge.py` `enabled`, api `KnowledgeService.notes`, the same rule): the config's
  `knowledge.build` when it says; else on only when a section exists or `init.knowledge_sections` is not empty. A new
  project starts off. Off: no knowledge paths in any agent prompt (`agent_knowledge.prompt_block` gives no
  "Knowledge you can use" lines, and no "none of your sections exist" line either; `task_prompt` names no knowledge
  folder), ship's `memory_plan` answers `{state: "off", ask: "no", run: "no", check: "no"}` with `memory_choice
  {choice: "off"}` (PR body: "off: the project notes are off"). `project_rules` (project-rules.md / .json) runs at
  init and knowledge-refresh either way.
- **Writing it**: `PUT /api/projects/{pid}/rules/config { knowledge: { build: "on" | "off" } }` (the engine's
  `rules_view.OWNED` gains `knowledge`; any other key or value → 400). init's `write_config` writes `build: on` when
  the person ticked sections (`write_sections`), else `off` in a new config (a re-run leaves a value that is there).
  knowledge-refresh's `knowledge_scan` turns it on (the memory commit takes the config along; `.keel/` is a memory
  path).
- **init**: the knowledge question is `{id: "knowledge", question: "Build project notes (…)?", multiple: true,
  options: ["Build project notes (<cost>, recommended for large repos)", architecture, domain, conventions, data,
  integrations]}`, none recommended unless the notes are on; the first option means all five. `<cost>` is
  `knowledge.build_estimate(root, ctx.models).label` ("≈$3–7", or "≈900k–2.1M tokens" for plan-paid models), kept as
  `init.notes_cost` for the plan and the hand-over (never in the config). `audit_now` gains the choice `notes`
  ("Build project notes") → step `start_notes` (`start_flow` knowledge-refresh, then end).
- **Estimate**: engine `POST /workflows/estimate` takes `root?`; for the `knowledge-refresh` workflow a librarian with
  no history then also reads `librarian_read_tokens(root)` = clamp(100k + the repo's tracked text / 4, 100k, 600k)
  input tokens per call (lock files, binaries and files over 512 KB left out). The api's estimate sends the project
  folder. Web: `notesCostLabel(estimate)` writes it as the engine does.
- **Web**: `ProjectNotesOff` (`@keel/web-sdk`, with `notesCostLabel`, `setNotes`, `NOTE_SECTIONS`): the Wiki shows
  it instead of Refresh stale and the "not written" row with Write them; **Build them (<cost>)** posts
  `/wiki/refresh {sections: the five}`; notes that exist but are off get **Turn on**; on, the Wiki head has **Turn
  notes off**. A knowledge page while off: `meta.status: "off"`. The Map's Journeys tab and the init page say the
  notes are off.
- **KeelBot**: `plugins/keelbot/content/agents/helper.md` `knowledge.code_graph: false` (was true). The index, the
  Graph plugin and its page stay.
- **Where to look**: the engine's `AgentKnowledge` model (StartThread.agents, KeelBot's turn) now has `hints`; before,
  pydantic dropped it, so the Graph plugin's `prompt_context` hook always saw hints off. Default stays off.
- **The folder's branch**: an Ask or Edit chat (no worktree) records `folder_branch` with each answer; the next turn
  compares it with the folder's branch now and, when it moved, its prompt says so ("moved from branch X to Y (the flow
  "T" checked it out at <time>)"; `by` is the project's flow on that branch from the api's keel view, `at` from git's
  reflog) and the answer's data carries `branch_switch`. A move while the answer runs: `branch_switch.during`. Event
  `helper.branch {from, to, at, by, during?, session, n}`. The panel's banner reads `branch_switch`, or compares the
  session's live `folder_branch` with the last answer's.
- **`GET /api/projects/{pid}/folder-activity` → FolderActivity**: KeelBot's Ask and Edit chats of the project with no
  worktree that run now or were updated in the last 30 minutes (from the engine's `/helper/sessions`), the folder's
  flow, and `worktree_ok` from the engine's `GET /start-check` when the engine has it (else null). Start a flow: a
  chat there and a flow that takes its own branch → `where: "worktree"` (recommended), unless `worktree_ok` is false;
  the project folder picked anyway shows a warning with **Use a worktree**.

## keel 0.18.63: a window can keep its own project and chat (feat/own-focus-per-window)

The shared focus (keel 0.18.6, api `ui/UiFocus.kt`: `GET` / `PUT /api/ui/focus`, live event `ui.focus`) is unchanged.
What changes is that each front end can opt out of it, per window.

```
web (localStorage)   keel2.followFocus = "0"        (missing or anything else: following, as before)
keel TUI (tui.json)  { "follow": false }             (KEEL_TUI_SETTINGS, else ~/.config/keel2/tui.json; missing: following)
```
- **Follow other keel windows**: one switch per window, on at first. On is 0.18.6 as it was. Off: that window
  sends no `PUT /api/ui/focus` (its project picks, its KeelBot chat opening, closing or starting), ignores `ui.focus`
  events, and does not read `GET /api/ui/focus` at the start (the web opens on the browser's own `keel2.project`; keel
  TUI on `--project`, the folder it starts in, else the first project; neither opens the focus's chat). Switching it
  moves nothing at once: the next pick here or elsewhere is shared again (keel TUI reads the focus again so its copy is
  not stale).
- **Web**: Settings › General › This browser › **Follow other keel windows** (`useApp().followFocus` /
  `setFollowFocus`). `followsFocus()` (`@keel/web-sdk`, beside `shareFocus`) reads the key at each use, so every tab of
  that browser goes along at once; `shareFocus` does nothing while it is false; KeelBot's panel reads the focus at the
  start only when it is true.
- **keel TUI**: `/follow [on|off]` (no word says which it is) and `/settings` (a picker: Follow other keel windows,
  Sidebar, and in the app Mouse and Theme, each with its value now; Enter changes the marked one; follow and the
  sidebar are kept in tui.json, the mouse and the theme are for this run). `KeelCore.follows` / `setFollow(on)`;
  `CoreOptions.settings` is the store (main.tsx gives the core and the app the same one). A write of tui.json reads the
  file again first, so two keel TUIs do not drop each other's settings.
- **What reads the focus**: `keel_focus` (MCP) reads and writes the same focus: with no arguments it says the project
  and chat the following windows share (a window that keeps its own is not in it); with `project` it moves only the
  windows that follow. `McpAudit` leaves `/api/ui/focus` out of the audit as before. Notifications do not read the focus:
  opening one picks its project in that window, shared only when the window follows.

## keel 0.18.64: the spec check checks citations; keel_spec_check and keel_citations_check (fix/spec-citations)

A field report (docs/plans/field-report-clarify.md, bug 3): a spec cited lines 235-245 of a 38-line file and passed
keel's spec check.

```
 spec text ──► citations.find ──► each path:line that names a project file ──► the file? its line count?
                                                                                │
                      spec_check.check(..., root=) ◄── "fix" findings ◄─────────┘
                      knowledge.check            ◄── the same parser, backticked citations only
```
- **One parser** (`engine/keel_engine/runtime/citations.py`), shared by the knowledge check (`keel memory check`:
  backticked citations only, as before) and the spec check (every form): `path:88`, `path:88-95`, `path#L88`,
  `path#L88-L95`, bare or in backticks; `path:88:5` reads line 88. Not citations: URLs (masked first, so
  `http://host:8080/a.py:3` is none), host:port and times (no extension), versions like `1.2:3` (an extension starts
  with a letter). In the spec check a citation names a project file when that file exists, or when the project's files
  (git ls-files, else the folder) have its extension; an absolute path outside the project and a `../` path are not
  checked. A bare name or a shorthand (`.../billing/Invoice.java:7`) is the one file that ends with it (the knowledge
  check's rule).
- **Findings** (level `fix`, so a flow sends the spec back once): `src/orders.py:88-95 does not exist: the file has 40
  lines.`, `src/gone.py:3 does not exist: no such file.`; a bare name that matches two files is a `note`. At most 10
  are named, then one line counts the rest. A file's line count is now exact in both checks (a last newline ends a line;
  the knowledge check counted one more before).
- **Where**: `spec_check.check(text, acs, outside, root)` checks citations when it gets the project's root: the
  clarify flow's write_doc, the feature flow's spec step (its one send-back), the spec gate's text and spec_sync's
  report all pass it.
- **Engine** (`runtime/spec_routes.py`): `POST /specs/check {root, path}` → `{path, criteria: [ids], findings: [{level,
  text}], fix, summary}`; `POST /citations/check {root, path? | text?}` → `{path, checked, bad: [{cite, path, problem,
  level}]}`. A path is relative to the project (400 for `../` or an absolute path, 404 for a missing file); a file or
  a text over 2 MB is refused.
- **api** (`knowledge/SpecCheckController.kt`): `POST /api/projects/{pid}/specs/check {path}` and
  `POST /api/projects/{pid}/citations/check {path | text}` add the project's folder and ask the engine (400 for no path,
  or for both or neither of path and text). Both are reads for MCP (`McpLevels` READ_POSTS).
- **MCP** (read, after `keel_read_file`): `keel_spec_check {path}` — the criteria, how many findings a flow would send
  back, each finding; `keel_citations_check {path | text}` — how many citations of project files were checked and each
  one that does not resolve. A secret file's path is refused before the api is asked.

## keel 0.18.64: a flow's agents read the other registered projects (feat/read-other-projects)

A flow's agent may read every other project registered in the same keel, and never change it. Field report bug 5
(docs/plans/field-report-clarify.md): an agent's `cat` of an upstream project was refused by claude -p with "no
interactive user response was available".

```
 api FlowService.buildStart ── StartThread.other_projects [{id, name, root}] (every project but the flow's own)
 engine thread body ── ThreadContext.other_projects (a flow it starts with start_flow copies it)
 each agent run ── other_projects.usable(...) ── ToolBox.others, guard context `others`, claude --add-dir <root>
```
- **Read**: read_file (ToolBox) and Read (hook) of an absolute path in another project, judged with that project's own
  `.keel/config.yml` (`rules.check_read`: protected-env, keel's keys, build output, a big file in parts). Shell: a
  command that names that project's path, changes into it (`cd`, `git -C`) or runs where the shell already is there
  (the hook call's `cwd`) may use only read-only commands in every part (cat, head, grep, rg, ls, find without
  -delete/-exec, sed without -i, git log/show/diff/status/blame/grep/ls-files ...), no redirect into it, and its
  protected-env files are not printed (`runtime/other_projects.py check_command`).
- **Never write**: Edit/Write (hook) and write_file (ToolBox) there are refused: "<path> is in <name>, another project
  registered in keel: agents may read it but never change it."
- **Prompt**: one line per other project after the task ("Other projects registered in keel (read only ...):" then
  "- name: root"), only when there are any.
- **Outside**: read_file of a path outside the project and every other project says "<path> is outside this project and
  is not another project registered in keel; ask the person to add it (keel2 add) or to paste what you need". For a
  claude agent (guard context `outside_refused`) the hook says it first, for Read and for read commands (cat, ls, grep,
  ... naming an existing path), so claude's own permission text never reaches the model. The temp folder, /dev and the
  run's scratch folder stay readable. codex, copilot and opencode read outside the project as before.
- **Guard context**: `others`, `others_off`, `outside_refused`; absent for KeelBot (it is not told, nothing changes).
- **Off**: the built-in rule `other-projects` (can_off, `rules.off`): off, the project's agents get no other projects,
  and other projects' agents (and `keel_read_project_file`) do not get it.
- **MCP** (read): `keel_read_project_file {project, path, offset?, limit?}` → the file's text, read from the folder keel
  sees (no plugin route), under the same rules (and `mcp_more.is_secret`), at most 60,000 characters, with a note on how
  to read on.

## keel 0.18.64: the clarify document is the source of truth (fix/clarify-doc)

From the field report (docs/plans/field-report-clarify.md, bugs 1 and 2) and its missed MCP tools.

- **A send-back patches the last draft**: the assemble step's instructions have `{{data.clarify.draft}}` ("Your last
  draft", `(nothing yet)` on the first pass). The spec check's send-back sets it to the document keel just wrote; a
  reject at the result gate (choice `reject` → new code step `redraft`, action `clarify_redraft`, `then: checked`)
  sets it to the file as it is then (the person's edits in it). The feedback asks to patch it: keep what is right,
  change only what the note or the findings ask, give every `## ` section again. Every reject note still applies
  (`data.clarify.notes`). A draft over 11,000 characters is cut with a pointer to its file.
- **The result gate reads the file**: `clarify_doc` stores `data.result_doc = "clarify"` (no `result_detail`); a gate
  whose `data["<gate>_doc"]` is set builds its text with `flow_doc.gate_text` when it runs, and the question carries
  `doc`, so engine `GET /threads/{id}` builds `waiting.detail` again from the file each time. Its second line says
  which copy a hand-over uses: the file as it is when you choose; `You edited <path> at <local time with zone>; the
  hand-over uses this file …` when the file differs from keel's copy (`data.clarify.evidence`); `This is your own draft
  (sent with the reject at <time>)`; or, the file gone, keel's copy.
- **The hand-over reads the file**: `to_feature` … `to_spike` run `clarify_hand+start_flow`, `prompt_only`
  `clarify_hand+clarify_prompt`. `clarify_hand` reads the document at the choice: the criteria parsed again from its
  Acceptance criteria, the prompt from its appendix (````text), the request (the flow's own while the file still holds
  it word for word, else the file's Request section), the evidence = the file. The link both ways was already there:
  the clarify flow's `children [{thread_id, workflow, step, title}]`, the new flow's `parent {thread_id, workflow,
  step}` (engine `GET /threads/{id}`; a test pins it).
- **A replacement draft with the reject**: resume payload `{choice: "reject", document: "<whole text>"}`. `redraft`
  writes it to the file, keeps `"(written to <path>)"` in `data.result_answer.payload.document`, and runs the spec
  check: pass → marker `SPEC-CHECK: pass` on `write_doc` → the result gate again, no assemble pass; fail →
  `SPEC-CHECK: fix`, the assemble step patches the draft (the check's one send-back is used).
- **Engine** `GET /threads/{id}/document` → `{thread_id, path, kind: clarify|spec, exists, text, edited, edited_at?,
  waiting: <gate step> | null}` (the clarify document, else the flow's spec; 404 when it has none).
  `PUT /threads/{id}/document {text, root?}`: only while the flow waits at a gate (409 running or not waiting, 400
  empty); writes the file (a `.md` inside the project or the folder `.keel` links to, never through a symlink) and
  emits `doc.version` with `by_kind: user`, `by_name: "edited while the flow waited"`, `via: api`.
- **api** `GET` / `PUT /api/threads/{tid}/document {text}` pass through to the engine (`FlowService.document`,
  `writeDocument`: the project folder as `root`, the saved state refreshed). `PUT` is the MCP write level.
- **History**: engine `doc_history.files` also lists `<specs>/clarify/*.md` (kind spec); api
  `DocHistoryService.known` lets them be restored.
- **MCP**: `keel_flow_document(project?, thread_id?, text?)` (read; `text` needs `--write`), `keel_reject_with_document
  (text, why?, project?, thread_id?)` (write; only at a clarify result gate; no `why` sends a note that says to keep
  the draft), `keel_handover(flow, text?, why?, project?, thread_id?)` (write; only a flow the gate offers; with `text`
  it `PUT`s the document first; answers with the new flow's thread id and the link). They come after `keel_diagram`.

## keel 0.18.64: the base against the remotes, and keel's branch sync (feat/base-sync)

Field report bugs 4 and 6 (docs/plans/field-report-clarify.md): a flow branched from a local base behind a second
remote while a timed-out fetch went unsaid, and nothing could bring a flow's branch up to its base.

```
POST /api/projects/{pid}/flows        + { base? }   a local branch or <remote>/<branch>: the flow's new branch (folder or
                                                     worktree) starts from it; 400 when it does not resolve, with
                                                     branch: continue, for a read-only flow or one with own_branch: false
                                      → ThreadState + base_note?   (the engine's start note, below)
POST /api/projects/{pid}/remotes/fetch → { base, remotes: RemoteRow[], lines: string[], note, fetched: [{remote, ok, error?}] }
                                                     (MCP read level: a fetch moves remote-tracking refs only)
POST /api/threads/{tid}/sync  { onto?, mode?: merge|rebase, action?: start|continue|abort }
                                      → SyncResult + thread   (write level; 404 unknown flow, 400 bad mode or action)
type RemoteRow  = { remote, ok, error?, branch?: "<remote>/<base>", ahead?, behind?, default?: "<remote>/<default>",
                    default_ahead?, default_behind? }
type SyncResult = { ok, synced, mode, onto, branch, before, head, base_head?, commit?, behind?, conflicts: string[],
                    state?: merge|rebase (a conflict waits), note, next? }

engine POST /remotes/fetch  { root, keys?, start? }   → as the api's (runtime/branch_sync.py fetch_all + report)
engine POST /threads/{tid}/sync { onto?, mode, action, root? } → SyncResult + thread (Engine.sync_branch)
engine POST /threads            + data.remotes_fetched?  → { thread_id, source?, base_note? }
```
- **Fetch**: every remote at once (`git fetch -q --no-tags <remote> +refs/heads/*:refs/remotes/<remote>/*`), at most
  `git.FETCH_EACH` = 20 s each, no password or ssh host-key prompt (BatchMode), keel's GitHub login only for a remote on
  its host (tools/git_auth.py). A failure or a timeout is a row with `ok: false` and git's first line (the token masked).
- **Report** (`branch_sync.report`): the base is base_branch (origin/main counts as main), else main, else master. Per
  remote: ahead / behind of the base against `<remote>/<base>`, and against the remote's default branch
  (refs/remotes/<remote>/HEAD, else its main or master) when that is another branch. Lines: "fetch from <remote> failed:
  <why>; the base may be stale", "<base> is N commits behind <remote>/<base>" (+ "and M ahead" when they diverged),
  "<base> is N commits behind <remote>/<default> (<remote>'s default branch)", else "<base> is up to date with …"; with
  the flow's start commit: "the flow's branch starts from <ref>", or "the flow starts from the local <base>: to start
  from the newer code, start it with base <ref>".
- **Start**: the api fetches (engine `/remotes/fetch`, keys = the contributors' keys) before it makes the branch, so
  B7's freshBase sees the fetched `origin/<base>`; it sends the rows as `data.remotes_fetched` (`[]` for a read-only
  flow, which fetches what it reads itself; nothing for a project without a remote). The engine's start_thread fetches
  itself only when `remotes_fetched` is missing and the flow has no parent. The report is `state.data.base_check`,
  `thread.started` carries `note` and `remotes`, and `POST /threads` answers `base_note`. Nothing here fails a start.
- **Sync** (`branch_sync.sync`, run by keel, not an agent: the guards that refuse an agent's merge, reset or commit do
  not apply). Refused (409) while a step runs, when the checkout is not on the flow's branch (state.branch), on a
  detached HEAD, with changed tracked files, or while a merge or rebase waits (start). `onto` default:
  `verdict_actions.base_ref` (the base's remote copy, else the local base); 400 when it does not resolve or is the
  branch itself. merge: `git merge --no-edit -m "Merge <onto> into <branch>" -m <co-author>` with keel's commit
  identity (`actions.commit_args`); rebase: `git rebase <onto>` with that committer. A conflict: `ok: false`,
  `conflicts`, `state`, `next`; the merge or rebase waits. continue: the conflicted files must be free of markers
  (else `ok: false` with them); keel stages exactly them and commits (MERGE_MSG without its # lines) or runs
  `rebase --continue`. abort: `git merge|rebase --abort`. When the branch moved: the thread body's `synced_base` and
  `ThreadContext.synced_base` = the merge base with `onto`; the compiler's next step writes it into `state.base_head`.
  Events `branch.synced {ok, synced, mode, onto, branch, head, note, conflicts}` and, for a merge commit,
  `keel.applied {kind: commit, …}`.
- **MCP**: `keel_fetch_remotes(project?)` (read) — one line per remote, then the note and the way to use the newer
  branch; `keel_branch_sync(thread_id?, onto?, mode, action, project?)` (write) — the project folder's flow by
  default; a conflict lists the files and the continue call. `keel_start_flow(base=…)` sends `base` (refused with
  `branch=continue`) and its answer ends with "Base: <note>." Both new tools come after keel's earlier tools and
  before the marketplace's four. The older `POST /api/projects/{pid}/repo/update-from-base` (project folder, merge
  only, aborts on a conflict) stays as it was.

## keel 0.18.64: MCP after a restart, the project a tool means, keel's own files, what really happened

From a field report (docs/plans/field-report-clarify.md: bug 7 and the smaller ones).

- **keel2 add** restarts keel only for a folder it must mount, and says so first (`keel restarts once now …`, `Open MCP
  sessions (keel2 mcp) reconnect by themselves …`). A folder keel sees already (inside the main project, or inside a
  mounted folder; `in_container` maps it: the main project is `/workspace/…` in the plain mode) is registered at once
  (`POST /api/projects` with the container path), no restart. A listed folder keel has not mounted yet restarts it.
- **keel_add_project** maps a path on the person's computer inside the main project to keel's (`KEEL_HOST_WORKSPACE` →
  `KEEL_WORKSPACE`, `mcp_more.seen_as`), so a repo keel sees needs no terminal; only a folder outside every folder keel
  has still needs `keel2 add` (the answer says keel restarts once and keel2 mcp reconnects).
- **keel2 mcp** waits up to `KEEL_MCP_START_WAIT` (20 s) for a container that is restarting, with a growing pause. Its
  relay (0.18.51) now: answers a `tools/call` that waits while keel is down after `KEEL_MCP_RELAY_HOLD` (10 s) as a tool
  error `keel: keel is restarting: try again in a few seconds …` (a result with `isError`, which clients show and go on
  from, not a JSON-RPC error after minutes); answers `ping` at once; looks for keel with a pause from 0.5 s up to 5 s;
  sends `notifications/tools/list_changed` after a reconnect; answers `keel_reconnect` itself (closes the server, waits
  for the new one); gives the server `KEEL_MCP_RELAY_N` (its start count, `-e KEEL_MCP_RELAY_N` on docker exec).
  The api client's "does not answer" error says keel is restarting or stopped and to try again in a few seconds.
- **Which project**: `docker exec -e KEEL_CLIENT_CWD=<the client's folder as keel sees it, empty when keel cannot see
  it>`. `mcp_api._project`: the named one (or `KEEL_PROJECT`), else the only project, else the project whose root holds
  `KEEL_CLIENT_CWD` (without the variable: the server's own folder, as agents and native keel have it), else an error
  naming the projects (`Pass project (one of: …)`). Before, a read answered for the first project: the server's folder
  in the container is `/workspace`, the main project, whatever folder the client was in.
- **Time zones**: keel2 passes the computer's zone (`TZ`, else `/etc/localtime`'s link, else `/etc/timezone`) to
  keel2 mcp's server and to keel TUI in the container. `_clock` shows local time with the zone's short name
  (`10-05 12:04:00 CEST`; an offset `UTC+05:30` when the zone has no name); `keel_timeline` uses it. The web shows the
  browser's local time as before.
- **Dirty tree**: `FlowService.refuseDirty` leaves out keel's own output under `.keel/` in every layout
  (`WorkspaceDoctor.isKeelOutput`): `.keel/specs/`, `knowledge/`, `adr/`, `docs/`, `hunts/`, `hunt/`, `chats/`,
  `worktrees/`, `RUNNING.md`, `CLAUDE.md`, `memory.json`, and the layout's folders under `.keel/` (a `memory.dir` such as
  `.keel/docs/knowledge`). The person's settings there (`config.yml`, `rules/`, `stacks/`, `dod.md`, `lenses/`,
  `plugins/`, `project-rules.json`) still refuse a start. They stay out of keel's commits like any file dirty at the
  start (`preexisting`).
- **Knowledge on a branch**: the knowledge check reads the sections from disk whatever git says. A worktree
  (`tools/worktrees.seed_keel_files`) gets, in layout repo too, the layout's files the branch's `.gitignore` ignores
  (`git check-ignore`); `keep_keel_files` brings back only those. In one of keel's worktrees a section that is not there
  is read from the project folder (`knowledge.source`), and a cited file too (`on_disk`). A shorthand citation that no
  tracked file matches looks at the files on disk (`knowledge.Files.disk`, walked once, `node_modules` and the like
  left out).
- **New read tools** (after `keel_diagram`, `mcp_diag.py`):

| Tool | What it returns | api |
|---|---|---|
| `keel_agent_prompt(agent_id, page)` | the system and task prompt one agent call of a flow got, each cut at 60,000 characters (`system_cut` / `prompt_cut`), 20,000 characters a page | `GET /api/jobs/{id}/prompt` |
| `keel_flow_diff(step?, thread_id?, ac?, project?)` | what a step's last run changed against the run before: its document (the two newest versions keel kept in this flow), else its agent's answer (the newest call and the same agent's call before); no step: the steps that ran more than once | `GET /api/projects/{pid}/flows/{tid}/step-diff?step=&ac=` → `{kind: document \| answer \| none \| list, runs, path, before, after, diff, why, steps}` |
| `keel_health()` | keel's api and engine, keel's version against the server's, when this connection's server started, whether keel2 mcp's relay reconnects it and how often it did, the time zone | `GET /api/health` |
| `keel_reconnect()` | the relay starts the server again and answers when the new one is ready; without a relay: how to reconnect | — |

- **The prompt is kept**: the engine emits `agent.prompt` (`{agent, ac, system, prompt, system_cut, prompt_cut}`,
  `compiler.PROMPT_RECORD_MAX` = 60,000 characters each) once per agent call, before it runs. The api stores it in
  `events` like every event and sends it to no screen (`RECORD_ONLY`). `agent.finished`'s `result` keeps up to
  `compiler.RESULT_MAX` (20,000) characters of the answer (2,000 before), so keel_flow_diff compares whole answers.

## keel 0.18.64: MCP answers, starts, models and confirmations (fix/mcp-ux)

The field report's bugs 12 and 13 (`docs/plans/field-report-clarify.md`).

- **What an answer ran**: engine `POST /threads/{id}/resume` adds `answered { decision, choice?, does }` to the state it
  returns (`runtime/choices.answered`: a named choice runs whatever the decision says, as `Compiler._choice` does; no
  choice: the plain approve's or reject's effect). The api passes it through (`/threads/{id}/resume`, `/inbox/{id}/act`).
  `keel_answer` says `answered thread <id>: ran the choice "edit" (…) (the choice decides: "edit" ran, not a plain
  reject)`, no longer `(reject)`; `keel_approve_gate` and `keel_edit_gate` say `<gate> on thread <id>: ran the choice …`
  when a choice ran. An older engine (no `answered`): the choice that was sent, else the default choice.
- **`keel_start_flow` answers at once**: no wait for the first gate by default (it polled for 20 s and outlasted a
  client's timeout). The answer has the thread id, `Status: starting|running|waiting` and how to follow it
  (`keel_status`, `keel_flows`; `keel_read_gate(flow=…)` and `keel_answer(thread_id=…)` when it waits). New `wait`
  (seconds, at most 60) waits for the first gate, polling `GET /projects/{pid}/flows/{tid}`. The start POST may take 30 s
  (branch, worktree, estimate); a timeout says the flow may still start and to look with `keel_flows` before a retry.
- **Models**: new read tool `keel_models`: `GET /providers/models` and `GET /connections`: per provider its default, each
  mode with `[set up]` / `[not set up: <detail>]` (a mode's `ready`; no key or login is printed), its models grouped by
  the efforts they take (`effort none` for a model without one), and the role words. `keel_agent_model` (and
  `keel_agent_create`) refuse with the choices: `<model> is not a model of <provider> in the <mode> mode; pick one of:
  …`, `<provider> is not a provider keel knows; pick one of: …`, `<mode> is not a mode of <provider>; pick one of: …`,
  `<provider>/<model> has no effort setting "<e>"; it takes: …, or default.`, `<provider>/<model> has no effort
  setting; it has none.` A model is checked only when provider, mode or model changes (an id set under the dashboard's
  Other model keeps working for an effort change).
- **Role words** (`agent_names.ROLES`, MCP only; workflows and settings keep agent ids): planning, planner, spec →
  explorer in the spec phase; triage → explorer in the triage phase; coding, coder, implementation → implementer;
  testing, tests → test-author; contract → contract-author; debugging → investigator; research → web-researcher; docs →
  librarian; security → security-auditor; final-review → branch-reviewer. A project agent of that id wins over a role.
  Project-wide, a role with a phase changes the whole agent and the answer says so.
- **A model for one flow or one step**: `keel_agent_model(..., thread_id?, step?)`. api `GET /api/threads/{tid}/models`
  and `POST /api/threads/{tid}/models { agent?, step?, phase?, model? }` (`FlowService.setThreadModels`: the model is
  `Model.checked()`, `LocalOnly` applies, its login or key goes along as `keys`, memory only) → engine
  `GET|POST /threads/{tid}/models` (`Engine.models_of`, `Engine.set_models`, `runtime/thread_models.py`). The flow's start
  body keeps `models` (`default`, one per agent, now `step:<step id>`) and `model_picks { key: what was there before }`;
  `model: null` takes a pick back. `agent` replaces that agent's entry in this flow; `step` adds `step:<id>`; `agent` +
  `phase` adds `step:<id>` for each of the agent's steps in that phase. 400 for a step that runs no agent, an agent the
  flow does not run, a step of another agent; 404 for an unknown thread. Event `thread.models { changed, model }`. The
  answer: `{ thread_id, changed, models, picks, steps: [{ id, name, agent, phase, model, picked }] }`.
  `Compiler._model` order: the cap's cheaper model, the step's pick, escalate_model's stronger model, the step's own
  `model:`, the agent's entry, the default (`explain.model_info` the same). Other flows and the Agents page never change.
- **Confirmations without forms** (`mcp_confirm.py`): a tool that asks first (`CONFIRM`, a part's `confirm=True`, a
  publishing action) and a client without elicitation (or whose form fails): nothing is done and the answer is `keel:
  this MCP client cannot ask you to confirm <what> in a form, so nothing was done yet. This needs the person's OK:
  <what>. Ask the person; never confirm by yourself. …` with a one-time token `ok-…`. The token is bound to the tool and
  a hash of its arguments, ends after 300 s (600 s when it has an Inbox question), and a bad or late one answers `that
  confirm token is not valid for this call …` with a new one. Those tools (and keel_run_action and the parts' write
  tools) take an optional `confirm: string`. Elicitation where it works and `--yes` are unchanged; 0.18.64 the answer no
  longer names `--yes`.
- **A client without forms: you confirm in keel's Inbox** (0.18.64, security audit F13). The model reads the token, so
  it could confirm by itself. With the token keel also asks in its Inbox: `POST /api/projects/{pid}/plugins/ask
  {title: "<client>: <what>?", command: "<tool>(<arguments>)"}` (engine approvals, kind `plugin`, session `mcp`: the
  Inbox's "Claude Code asks" card; the project is the tool's `project`, else the only one or the client's folder's).
  The call with the token reads `GET /api/plugins/asks/{id}`: approved → done; denied → `keel: not done — the person said
  no to <what> in keel's Inbox`; still waiting → `keel: the person has not approved <what> in keel's Inbox yet …` (the
  token stays open). The dangerous kinds (`mcp_confirm.OUT_OF_BAND`: keel_delete_run, keel_remove_project,
  keel_stack_remove, keel_keelbot_undo, keel_git_push, keel_stop_flow, keel_stop_flows, keel_pause, keel_agent_model,
  keel_db_change, and keel_run_action's open_pr / pr) need the Inbox's approval: the token alone does nothing, and
  when keel cannot ask in its Inbox (no project, no write level) the answer is `… keel could not put the question in
  its Inbox (…), so nothing was done … they confirm it in keel's dashboard` with no token. The other kinds act on the
  token (the Inbox's approval counts too; a denial there wins). `KEEL_MCP_CONFIRM=token`: the token alone confirms
  every kind (as before).

## keel 0.18.64: a spec handed over is kept, spec numbers, keel v1's leftover state (fix/spec-seed-v1-state)

From the field report (docs/plans/field-report-clarify.md, bugs 8, 9 and 11).

- **A spec handed over is kept** (`runtime/spec_check.py` `seeded`, `kept_items`, `lost`). What a flow was handed: the
  spec file it started from (its text at the start, `data.spec_at_start`), a parent flow's document that is a spec
  (`data.seed.evidence` with front matter or an Acceptance criteria section: the clarify hand-over), and the request
  when it names risks, out-of-scope items or criteria itself (a spec pasted into it). The spec writer is told to keep
  all of it and only fill the gaps (`prompts.seeded_block`). After it writes, every item under a Risks or Out of scope
  heading (or a `Risks:` label) and every criterion line must still be in the spec, in its words or close (at least
  `KEEP_MIN` of its words in one line); a lost one is a `fix` finding ("Lost from the request: the risk “…” is not in
  the spec"), sent back once like the other spec check findings, also when the criteria were handed over, and shown on
  the spec gate. The check is additive: `check()` is unchanged, the callers add `lost()`.
- **A send-back patches the last draft** (`prompts.draft_block`): the spec writer gets the draft whole (up to
  `DRAFT_MAX`) and is told to change only what the note names; it is not told to write a new numbered file.
- **Spec numbers** (`prompts.spec_folders`, `next_spec_number`, `renumber_spec`): the next free number counts every spec
  folder keel knows (the layout's, keel v1's `docs/specs`, `specs`, `.keel/specs`, the config's `specs.dir`), files and
  folders named `NNN-…`. The spec writer gets the number also when the criteria were handed over. A spec this flow
  wrote first (not committed, not the one it started from) whose number another spec has moves to the next free one,
  its front matter's `id` with it.
- **keel v1's leftover state** (`runtime/v1_state.py`). `.keel/state.json` naming a flow in an active phase is said
  where a commit fails (keel's commit step, the Git plugin's commit, the workspace Doctor's commit), in the preflight
  warnings and in `GET /start-check` (`v1_state`: the line or null): "a keel v1 flow (feature 012-prices, phase
  full-review) left .keel/state.json: it blocks commits; archive it with keel_state close or `keel2 state close`".
  A commit keel's guard refuses during a flow names that flow ("The flow that blocks this commit: the keel v2 flow
  "Prices in euros" (thread t-1), phase green."; the guard context's `flow`, `ToolBox.flow`).
  - engine `GET /keel-state?root=` → `{v1: {path, flow, phase, id, title, ac, updated, active, thread, line, name,
    error?} | null}`; `POST /keel-state/close {root}` → `{archived, flow, was_active}` (404 when there is no file). The
    file moves to `.keel/archive/state-<date>.json` (`-2`, `-3` … when taken); nothing is deleted.
  - api `GET /api/projects/{pid}/keel-state` → `{project, flows: [{id, title, workflow, status, step, phase,
    worktree}], v1}` (the flows that run or wait there); `POST /api/projects/{pid}/keel-state/close` (an MCP write).
  - MCP `keel_state {action: show | close, project?}`: a read tool; `close` needs `--write` (read says so and does
    nothing). keel2: `keel2 state [close] [folder]` does the same on the folder, without keel running.
