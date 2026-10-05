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
| `KEEL_HOME` | `/opt/keel` (dev: `../keel`) | api, engine — keel v1 (agents/, skills/, stacks/, packs/, mcp/, bin/keel) |
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
POST /mcp/tools            McpServerSpec      → { ok, tools: {name, description}[], error? }   (tools/list)
POST /providers/test       { provider, mode, model, key? } → { ok, text?, ms, error? }        ("Reply with exactly: OK")
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
};
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
  acs?: { id: string; layer: "API"|"WEB"; title: string }[];   // optional; otherwise the spec step writes them
  models: Record<string, Model>;               // agent id → model ("default" key = fallback)
  settings: { gates_mode: "every-ac"|"end-of-lane"|"end"; cap_tokens: number; on_cap: "pause"|"cheaper"|"stop"; cheaper_model?: Model };
  mcp: McpServerSpec[];                        // servers this flow may use; per-agent allowlist inside Step.tools
  skills: Record<string, string>;              // agent id → concatenated SKILL.md text to add to its prompt
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
      | "gate.waiting" | "gate.decided" | "budget.warn" | "budget.stop" | "guard.refused" | "thread.done" | "thread.failed";
  thread_id: string; project_id: string; step?: string; at: string;
  call_id?: string;                            // agent.* events: one id per agent call
  data: Record<string, unknown>;
  // agent.started  data: { agent, provider, model, phase, ac }
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
type Limit = { id, name, unit, used, cap, note }       // account windows: editable; "used" from jobs
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

- **keel rules** (`engine/keel_engine/rules/`): ported from keel v1 and tested against `engine/tests/fixtures/keel_v1_rules.json`
  (PHASES, TRANSITIONS, RAILS, MATRIX, COMMIT_RULES, FLOW_START, red_accept/red_reject, classify cases).
- **Guards**: agent tools `write_file` / `run_command` refuse what `MATRIX[phase]` denies; after every agent step the
  engine diffs the repo (`git status --porcelain`) and reverts files the phase does not allow, emitting `guard.refused`.
- **Gates** are LangGraph `interrupt()`; only `POST /threads/{id}/resume` continues them. Locked steps can only be removed
  from a workflow when `keel_rules` is false (the api refuses otherwise with `{error, hint}`).
- **Mirror**: the engine writes `<root>/.keel/state.json` (keel v1 schema: flow, phase, acs, gates, ...) and appends
  `<root>/.keel/logs/events.jsonl` (`{at, kind: tool|agent|phase|gate|guard, ...}`) so keel v1's CLI and dashboard read v2 projects.
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
- **Live guard for the claude CLI**: subscription claude runs with `--plugin-dir $KEEL_HOME` so keel v1's hooks enforce the
  phase rules on every tool call (engine keeps `.keel/state.json` current before each agent step). opencode runs with
  keel's opencode adapter (`$KEEL_HOME/opencode`). codex/copilot keep the after-step diff guard.
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
GET  /api/projects/{pid}/estimate    + POST variant { yaml, acs }         (estimate unsaved workflow YAML)
Agent                                + lane: "follow"|"api"|"web"          (PUT accepts it; passed to the engine as Step metadata)
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
- **Skills** (`skills/`, in the image at `/opt/keel-v2/skills`, shown in Build › Skill hub with source "keel v2"):
  `spec-clarify` (the question format, identity and work-placement probes) and `spec-writing` (spec layout, criteria
  form, drawings); both assigned to the `explorer` for phase `spec`. keel v1's `spec-authoring` (a live chat) is no
  longer assigned. Example result: `docs/examples/spec-leaderboard.md`.

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
