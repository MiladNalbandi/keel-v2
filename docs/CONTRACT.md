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
 │  keel v1 at /opt/keel  (node; `keel` on PATH; MCP server /opt/keel/mcp/server.js)          │
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
| `KEEL_HOME` | `/opt/keel` (dev: `../keel`) | api, engine — keel v1 (mcp/, bin/keel, dashboard; going away in v0.4.0) |
| `KEEL_ENGINE_URL` | `http://127.0.0.1:8090` | api |
| `KEEL_API_URL` | `http://127.0.0.1:8080` | engine — where it POSTs events |
| `KEEL_INTERNAL_TOKEN` | random at start, shared by both | header `X-Keel-Token` on engine↔api calls |
| `KEEL_FAKE` | `0` | engine — `1` forces the fake model everywhere (tests, demo) |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GITHUB_TOKEN` | — | optional; Connections can store keys instead |

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
  phase?: string;              // keel v1 phase this step belongs to (for guards + .keel/state.json mirror): spec, red, green, gate, ...
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
  settings: { gates_mode: "every-ac"|"end-of-lane"|"end"; cap_tokens: number; on_cap: "pause"|"cheaper"|"stop"; cheaper_model?: Model };
  mcp: McpServerSpec[];                        // servers this flow may use; per-agent allowlist inside Step.tools
  skills: Record<string, string>;              // agent id → concatenated SKILL.md text to add to its prompt
  agents?: Record<string, { knowledge: Knowledge }>;   // v0.4: what each agent uses (missing → its front matter default)
};
type ThreadState = {
  thread_id: string; project_id: string; workflow_id: string; title: string;
  status: "running" | "waiting" | "done" | "failed" | "stopped";
  current: string | null;                      // step id
  phase: string;                               // keel v1 phase name (mirrored to .keel/state.json)
  ac: string | null;
  acs: { id: string; layer: string; title: string; status: "todo"|"red"|"green"|"done" }[];
  waiting?: { step: string; kind: "gate"|"budget"|"fix"; title: string; detail: string; options: ("approve"|"reject")[] };
  usage: { tokens_in: number; tokens_out: number; cost_usd: number; premium_requests: number; cap_tokens: number };
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
GET    /api/health                                   → { ok, engine: bool, keel: { version, home }, fake }
GET    /api/events?project={pid}                     SSE: event: <EngineEvent.type | "notification" | "project.changed">, data: JSON

# projects
GET    /api/projects                                 → Project[]
POST   /api/projects            { root, name? }      → Project            (registers a path inside the container)
GET    /api/projects/{pid}                           → Project
type Project = { id, name, root, branch, flow: string|null, phase: string, acs: [done,total], waiting: number, running: number }

# flow (Run)
GET    /api/projects/{pid}/flow                      → { thread: ThreadState|null, workflow: Workflow|null, keel_state: object|null }
POST   /api/projects/{pid}/flows  { workflow_id, title, acs? }  → ThreadState
POST   /api/threads/{tid}/resume  { decision, why? }  → ThreadState
POST   /api/threads/{tid}/stop                        → ThreadState
GET    /api/threads/{tid}/history                     → Checkpoint[]
POST   /api/threads/{tid}/rewind  { checkpoint_id }   → ThreadState
GET    /api/projects/{pid}/estimate?workflow_id=&acs=3 → Estimate     (uses this project's job history)

# jobs / live (Run)
GET    /api/jobs?project=&status=running|done|failed&agent=&provider=&limit=50 → Job[]
GET    /api/jobs/{id}                                 → Job & { steps: JobStep[] }
GET    /api/jobs/{id}/steps?after=n                   → { steps: JobStep[], running: bool }
POST   /api/jobs/{id}/stop
type Job = { id, project_id, thread_id, agent, provider, model, step, phase, ac, status, started_at, ended_at, tokens_in, tokens_out, cost_usd, premium_requests, steps_count, mcp_calls }
type JobStep = { n, at, kind, text, tool?, server?, path?, diff?, ms?, ok? }

# repo (Project)
GET    /api/projects/{pid}/repo                       → { branch, base, ahead, behind, remote, worktrees: {branch,path}[], branches: {name, note}[] }
GET    /api/projects/{pid}/repo/tree?depth=4          → TreeNode[]   { path, name, depth, kind: "dir"|"file", mark?: "A"|"M"|"D", keel: bool, frozen: bool, ac?: string }
GET    /api/projects/{pid}/repo/file?path=            → { path, size, mark?, frozen, keel, ac?, head: string (first 120 lines), last_commit }
GET    /api/projects/{pid}/repo/commits?limit=30      → { sha, message, author, at }[]
GET    /api/projects/{pid}/keel-docs                  → { path, what, by, updated, status: "ok"|"live"|"check" }[]
GET    /api/projects/{pid}/memory                     → { facts: Fact[], knowledge: { id, status: "written"|"stale"|"missing", words, cites }[] }
POST   /api/projects/{pid}/memory  { title, text, kind }      PUT /api/projects/{pid}/memory/{fid}   DELETE /api/projects/{pid}/memory/{fid}
type Fact = { id, title, text, kind: "fact"|"rule"|"flaky"|"unlock", source, at }

# map + wiki (Project)
GET    /api/projects/{pid}/map                        → keel v1 map JSON (.keel/map.json) or { missing: string }
POST   /api/projects/{pid}/map/rebuild                → runs `keel map` in the repo, returns the new map
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
  (init flow). Both are also mirrored into `.keel/state.json` (`blockers`, `setup.rungs`).
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
GET  /api/projects/{pid}/repo/history?path=                → { sha, message, author, at }[]      (git log --follow -n 30)
POST /api/projects/{pid}/unlock      { path, phase? }       → { unlocks }   (writes to the active thread via resume payload, else to .keel/state.json)
GET  /api/projects/{pid}/stacks                             (exists) + Stack.installable: bool
POST /api/projects/{pid}/stacks      { name, from }         → Stack          (copies the closest keel stack YAML into <root>/.keel/stacks/<name>.yml)
POST /api/projects/{pid}/stacks/{name}/install              → Stack          (runs `keel packs add <packs dir> --project` in the repo)
POST /api/projects/{pid}/wiki/refresh  { sections?: string[] } → ThreadState (starts the knowledge-refresh workflow for stale sections)
GET  /api/projects/{pid}/caps        → Cap[]     POST /api/projects/{pid}/caps  Cap    PUT /api/projects/{pid}/caps/{id}  Cap    DELETE /api/projects/{pid}/caps/{id}
type Cap = { id, scope: "day"|"flow"|"step"|"api_month", limit: number, unit: "tokens"|"usd", action: "pause"|"cheaper"|"stop" }
POST /api/projects/{pid}/skills/import { url } | { body }   → Skill   (a SKILL.md from a URL or pasted text; http/https, 256 KB)
GET  /api/providers/models                                  → engine /providers/models
POST /api/projects/{pid}/flows       + { cap_tokens?, on_cap? }          (per-flow cap; overrides settings for that thread)
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
- "Open in editor" stays out (no editor inside a container). "Open in keel v1": `GET /api/keel-dashboard` starts
  `keel dashboard` inside the container on 7391 and returns `{ url: "/keel-v1/" }`; the api reverse-proxies `/keel-v1/**`
  to 127.0.0.1:7391 with the Host header keel expects.

### v0.2 shapes as built
- `POST /api/projects/{pid}/unlock` → `{ unlocks, via: "thread"|"state", thread_id }`; unlocks go to a thread only while it waits with `kind:"fix"`, else into `.keel/state.json` `unlocks` (`{path, phase, reason, at}`); the engine merges on-disk unlocks into a running thread before every step.
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
- **keel v1 sees v2 flows that wait.** While a step waits, `.keel/state.json` shows that step and its phase, with
  `engine.status: "waiting"`. Every step writes the state when it starts.
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
- Map = keel v1's `.keel/map.json` shape (`sha, at, limits, counts, levels`) with `levels.system`, `levels.modules`
  and `levels.er` (from SQL migrations); `flow` and `classes` are not built (the Map page shows its empty state).
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

