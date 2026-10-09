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
| `KEEL_INTERNAL_TOKEN` | random at start, shared by both | header `X-Keel-Token` on engine↔api calls |
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
GET  /templates                               → Workflow[]        built-in keel flows: feature, change, fix, init
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
# web (pages/Repo.tsx + pages/repo/; since step 3 plugins/code/web): an activity bar (Explorer, Search, Source control, keel), editor tabs (single click = preview
# tab replaced by the next one, double click = pinned), a status bar. Deep links: #/repo/<path> opens a file, #/repo/<path>:<line>
# opens it at that line and highlights it (the Map links a table to its migration this way); the URL follows the active tab.
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
type Skill = { id, kind, source: "keel"|"keel pack"|"claude"|"yours", stack, version, tokens, agents: string[], when: string, enabled: bool }
GET    /api/projects/{pid}/stacks                     → Stack[]   { name, lane, source, detected, detect, layers, commands: {name, cmd}[], tools: {name, on, fail}[], skills }
GET    /api/mcp-servers                               → McpServer[]
POST   /api/mcp-servers  McpServerSpec   PUT /api/mcp-servers/{name}   DELETE /api/mcp-servers/{name}
POST   /api/mcp-servers/{name}/test                   → { ok, tools, error? }
GET    /api/projects/{pid}/mcp-allow                  → { [agent]: string[] }     PUT same shape
type McpServer = McpServerSpec & { enabled, builtin, status: "ok"|"off"|"error", tools: string[] }

# control
GET    /api/projects/{pid}/budget                     → { month: { tokens, cost_usd, premium_requests, flows }, days: { day, claude, codex, copilot, fake }[], caps: Cap[], top: { agent, provider, tokens, cost_usd }[], recent: { title, estimate, real, status }[] }
GET    /api/projects/{pid}/budget/now                 → BudgetNow (v0.5.2, the budget bar; see "v0.5.2: the budget bar")
GET    /api/projects/{pid}/graph                      → GraphOverview (v0.5.3, the code graph; see "v0.5.3: the Graph page")
GET    /api/limits                                    → Limit[]      PUT /api/limits  Limit[]
type Limit = { id, name, unit, used, cap, note, source?, window?, used_pct?, remaining?, resets_at?, fetched_at? }   // manual cap + the provider's numbers when known
GET    /api/usage/providers                           → ProviderUsage[]   // only providers that are set up; codex/copilot cached 60 s
POST   /api/usage/providers/{id}/refresh              → ProviderUsage     // codex/copilot: read now; claude: one tiny Haiku call
type ProviderUsage = { id, name, kind: "subscription"|"api", source, fetched_at?, windows: { window, label, used_pct?, used?, cap?, remaining?, resets_at?, status? }[], live, can_refresh, error?, note? }
GET    /api/settings/general                          → Settings
PUT    /api/settings/general        Partial<Settings>
GET    /api/projects/{pid}/settings                   → { general: Settings, overrides: Partial<Settings>, effective: Settings }
PUT    /api/projects/{pid}/settings  { [key]: value | null }    (null = use general)
type Settings = { gates_mode, keel_rules, fix_attempts, coverage_min, default_model: Model, implementer_model: Model, reviewer_model: Model, cheaper_model: Model,
                  cap_tokens, on_cap, branch_pattern, web_lane_worktree, push_pr, notify: "all"|"needs_you"|"none", env_names: string[], mcp: string[] }
GET    /api/connections                               → { providers: { id, label, modes: { id, label, ready: bool, detail }[], selected, key_set: bool, key_hint?: string }[], machine: { name, ok, version? }[] }
                                                        // v0.4.2: the 8 program checks (node git java docker claude codex copilot opencode) run in parallel, 5 s each, cached 1 min
PUT    /api/connections/{provider}  { mode }
PUT    /api/secrets/{name}   { value }   → { hint }   (stored AES-GCM in /data; never returned)
DELETE /api/secrets/{name}
POST   /api/connections/{provider}/test               → { ok, text?, ms, error? }

# notifications
GET    /api/notifications?limit=50                    → Notification[]   { id, type: "review"|"failed"|"budget"|"finished"|"started", project_id, title, body, link, at, read }
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
github-copilot`), else a built-in list. Effort is passed to the CLIs: claude `--effort`, codex `-c model_reasoning_effort=…`.

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
- **Review findings.** After a review step (agent `code-reviewer`, `security-auditor` or `reviewer`, not per AC), the
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
- **init:** the `questions` pause shows keel v1's three questions with the detected defaults (labels "Use the
  defaults" / "Use my answers"; both go on). The answers are written to `.keel/config.yml` under `init:`
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
  the server's working folder (an agent runs in its project), else the first project.
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
  phase does not allow, `tools/guard.py`), so a forbidden edit exists on disk until the step ends.
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
- **Review findings** (reviewer, code-reviewer, security-auditor steps): with `back`, findings go to that step as
  feedback without a question for `rounds` (default 2) rounds. Without it the question is as before, plus: payload
  `route` = review-fix (implementer, verify_green + fix commit; default) | coverage-fix (test-author, verify_green +
  coverage commit) | red (test-author, red commit); after the fix the flow goes on from `redo`; past `rounds` the
  question says the limit is reached. "Go on anyway" keeps them in `data.dismissed_findings` (shown at the final review).
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

Workflows in `content/workflows/` (ORDER: feature, change, fix, diagnose, review, init, knowledge-refresh, cover, ship, hunt, hunt-next).

- **review** (read-only, phase `review`): `review_scope` reads `data.lens` (code | all | correctness | security | performance |
  architecture | assertions | ac <ID>) and `data.base` (default base_branch, main, master) and builds `data.review_lenses`
  (one item per reviewer, each naming its `agent`: code-reviewer, reviewer, ac-reviewer); an empty diff, an unknown lens, a
  missing base or a criterion without its test/feat commits **stops** the flow (`ActionResult.stop`). One reviewer per item
  in parallel → `report` → gate showing every answer verbatim + tally (`2 of 4 blocking`). Reviews in a read-only phase never
  start the findings fix loop. Send back = review again.
- **diagnose** (phase `bug-investigate`): explorer frames the symptom and collects `hypotheses` → one investigator each
  (ROOT-CAUSE) → `report` → investigator ranks + RECIPE/KIND → `report` → gate `choices: [fix, feature, unresolved]`:
  start_flow fix (seed recipe + symptoms, never the cause), start_flow feature (seed evidence), or `investigation_note`
  (`docs/investigations/<date>-<slug>.md`, every hypothesis kept, committed `docs: investigation note …`).
- **fix**: `bug_intake` (data.no_gates or `gates.bug_gates: false` → `gates.skipped["gate-r"|"gate-f"]`: those gates approve
  themselves and the PR body lists them; data.needs_e2e → marker E2E) → reproducer (REPRO; not-reproducible → start_flow
  diagnose) → verify red + `test(BUG)` → Gate R `choices: [investigate, stop]` → hypotheses → investigators in parallel → no
  confirmed cause → `escalate_model` once (settings.stronger_model, else Opus / high effort) and again → still none →
  start_flow diagnose → plan (E2E marker) → Gate F `choices: [fix, feature]` → implementer → verify + `fix(BUG)` (`rounds: 1`,
  then `after_rounds: reset`: keel's uncommitted edits go back, reproduce again) → regression e2e when E2E yes (seed or
  plan) → `ship` include.
- **change**: triage (SIZE marker, inline ACs) → `change_size` (ids `CHG-<n>.<m>`, > change.max_inline_acs → feature) →
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
- Code actions get `settings` (the thread's) and `thread_id`. StartThread.settings gains `fast`, `fix_attempts_per_rung`,
  `hunt_mode` (auto|semi), `hunt_scope` (all|diff|paths), `hunt_lenses`, `hunt_run`; a parent's `start_flow` seed may carry
  `mode`, `scope`, `lenses`, `fast`, `run` instead.

### hunt (content/workflows/hunt.yaml, runtime/hunt.py + hunt_actions.py)
`start` (run in the backlog) → `confirm_lenses` gate (confirm|stop; `payload.lenses` or "drop x" in the note) → `confirm`
(one sweep item per lens×lane, brief copied in) → `deps` (security lens: the dependency audit first, always) → `sweep`
(one `hunter` per item; JSON list + `FINDINGS: n`) → `ingest` (lane enforced by classifying every cited path, cap
`max_candidates_per_lens` (halved when fast), severity dropped, same file ±`dedup_line_window` merged as `also`;
`docs/hunts/<run>/candidates.md`, UNVERIFIED) → `sweep_gate` (semi only: prove|stop) → `prove` (one `prover` per candidate,
symptom and where only, never the claim; `batch` = `hunt.prove_concurrency`) → `verdicts` (proven needs a recipe that ran
twice, `.sh .http .sql .md .probe.ts`, a severity; a 5xx is never below `severity_floor_5xx`; refused verdicts go back to
the provers, at most `prove_rounds` (3) rounds, then unproven / raised to the floor) → `verdicts_gate` (semi only) →
`group` (investigator, groups that share a cause) → `report` (`docs/hunts/<run>/report.md` + `repro/`; refuses while any
candidate has no verdict; marks `needs_e2e`) → `commit` (`docs(HUNT-<run>): bug hunt: N proven, M suspected`, only that
folder) → `triage` gate (take → `start_flow hunt-next` | close {id, as} + note | stop).

### hunt-next
`take` (refuses while any candidate has no verdict or the report folder is uncommitted; the top open group by worst severity,
marked dispatched) → defect: `start_flow fix`, unspecified: `start_flow feature`, seed {title, request = the lead's symptom,
recipe = `docs/hunts/<run>/repro/<file>` + its body, symptoms (every member), needs_e2e, hunt: {run, group, lead, findings}}
→ `close_gate` (fixed|accepted|wontfix|later; a note is required, else it asks again) → `close`.

### init
`discover → arch_detect (runtime/arch.py; low confidence and not fast → one arch-surveyor, ARCH marker) → questions →
plan_gate → write_config → arch_set (architecture: {style, confidence, source} in .keel/config.yml) → lanes (ladder_soft ‖
librarians) → doctor (one setup-doctor per failing rung) → ladder_retry → ... → rung_gate (only when a rung failed
fix_attempts_per_rung times: fix | exclude → setup.ladder_exclude | accept → setup.not_checked) → rung_apply →
knowledge_check → commit → audit_now (skip | hunt: start_flow hunt, semi, whole project) → hand_over`. `fast`: no knowledge
sections by default, no surveyor, a rung that passed before with the same command is re-used.

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
waives the integration/e2e/smoke gates) → `spec` (explorer: the interview; clarify questions on `spec_gate`) → only once there are
criteria (`when: {state: acs}`): `explore_areas` → `explore` (one explorer per area: api, web, data) → `maps` (report) → `plan`
(appends `## Plan`, marker ORDER) → `spec_sync` (criteria re-read from the spec, ORDER applied, the gate text) → `spec_gate`
(locked; exits approve → `freeze` | edit, rewrite → `spec_edit` | review → `spec_review` (reviewer, phase review, shown on the
gate) | order → `plan` | reject → `spec_restart` → `spec`) → `freeze` (frontmatter status frozen + approved/frozen dates; only
the spec is committed, `docs: spec and plan: <title>`) → `options` (skip menu: integration_gate, security, e2e, smoke) →
`contract` → `contract_diff` → `contract_gate` (approve → commit | change → contract | amend → the contract draft is put back,
then the amendment) → AC loop (red/green may end with `AMEND: <why>` → `red_to_amend`/`green_to_amend`) → `integration` →
`integration_commit` (`fix(integration)`) → `integration_gate` → `security_deps` (verify_deps, soft) → `security_scope` →
`security` (fan-out: security-auditor, plus dependency-triager when verify deps failed; findings → the findings question) →
`review_fix` (retry_only) → `review_fix_commit` → `code_review` (code-reviewer, back review_fix, 2 rounds) → `e2e_scope` ([E2E]
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
| `ac` | a per-criterion AC gate | ask | approve when `last_failure` is empty and the AC review before it (ac-reviewer/reviewer) has no `AC-REVIEW: findings` and no Blocking items | approve | ask |
| `spec` | spec / triage gate, amendments | ask | ask | approve (never when only a send-back fits: no criteria) | ask |
| `clarify` | the explorer's questions | ask | ask | approve with each question's recommended option | ask |
| `contract`, `final-review`, `pr` | contract gate, `report: verdicts`, the gate after a `pr` step | ask | ask | approve | ask |
| `choice`, `skip-menu`, `gate` | named choices (default = first / `recommend`), skip menus (run everything), any other gate | ask | ask | approve | ask |
| `findings`, `already-met`, `escalate` | review findings within `rounds or 2`, a test that passes before code, a change-flow escalation | ask | ask | approve | ask |
| **safety**: `failure`, `rounds`, `rung`, `budget`, `dependency`, `secrets`, `readonly`, `note` | step failed / keeps failing, loop past its rounds (branch, findings past their limit), init rung_gate, token cap and plan windows, new dependency, a secret staged for commit, a read-only commit, hunt-next close_gate | ask | ask | ask | ask |

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
InboxItem = { project_id, project_name, thread_id, flow (the flow's title), workflow_id, step,
              kind: gate | clarify | fix | budget | usage | dependency, title, detail (≤ 700 chars), more, options, choices?,
              questions?, labels?, id?, phase?, ac?, run_mode?, auto_approved, last_auto?, since }
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
  `KEY: title`; its request is title + description + "Jira ticket: KEY (url)". A refused start (a used-up cap, uncommitted
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
  caps: CapLeft[]               // its day and month caps that keel checks (as in /caps/left)
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
  writes, server functions like pg_terminate_backend) are refused. Columns named like a secret show as `•••` to models.
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
GET|PUT|DELETE /api/gitlab {url, token?}                   → { set, url, host, hint }   (the token never leaves keel)
```

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
