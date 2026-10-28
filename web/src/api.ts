// Typed client for the keel v2 api (docs/CONTRACT.md, "The api"). Every path is relative, so the same
// build works behind the Spring Boot api (which serves web/dist) and behind the Vite dev proxy.

import { signInNeeded } from "./signIn";

// ---------- shared types (copied from CONTRACT.md) ----------

/** 0.18.46 vertex (Google Vertex AI) and local (a model server: Ollama, LM Studio, vLLM, llama.cpp) run in mode "api". */
export type Provider = "fake" | "claude" | "codex" | "copilot" | "vertex" | "local";
export type Mode = "subscription" | "api" | "opencode";
export type Model = { provider: Provider; mode: Mode; model: string; effort?: string };

/** "include" only shows in a stored workflow the engine has not expanded yet (the engine replaces it with the steps). */
export type StepKind = "agent" | "code" | "gate" | "branch" | "parallel" | "include";
export type Lane = { name: string; sub?: string; kind: "agent" | "code" };
export type Step = {
  id: string;
  kind: StepKind;
  name: string;
  agent?: string;
  model?: string;
  phase?: string;
  action?: string;
  per_ac?: boolean;
  parallel?: number;
  lanes?: Lane[];
  back?: string;
  no?: string;
  lock?: boolean;
  max_tokens?: number;
  on_limit?: "pause" | "cheaper" | "stop";
  tools?: string[];
  /** v0.10.0 a plugin step's settings (db:check's sql and expect, git:pr's title...): `with:` in the YAML. */
  with?: Record<string, unknown>;
  // Engine workflow keys (engine/keel_engine/workflows/model.py) the flow map reads; the builder keeps them as they are.
  /** A code step's next step: "end", "continue" or a step id. */
  then?: string;
  /** A gate's named exits {name: step id | "end"}, or a list (one per loop item / a choice a branch reads). */
  choices?: string[] | Record<string, string>;
  /** When the step runs (agent, code, parallel), when a gate asks, or what a branch tests. */
  when?: Record<string, unknown>;
  /** start_flow: the workflow it starts. */
  flow?: string;
  for_each?: string;
  per_item?: boolean;
  /** A parallel step: one agent per item of this list. */
  from?: string;
  cap?: number;
  rounds?: number;
  after_rounds?: string;
  redo?: string;
  retry_only?: boolean;
  soft?: boolean;
  skippable?: "deferred" | "optional";
  group?: string;
  skip_menu?: boolean;
  /** v0.17.5 this gate also asks the skip menus after it (the spec gate) */
  menus?: boolean;
  report?: string;
  markers?: string[];
  instructions?: string;
  /** Set by the engine's include: where the step came from, outermost include first ("ship", "ship/cover"). */
  included_from?: string;
};
export type Workflow = {
  id: string;
  name: string;
  /** keel (a template) | yours | library. */
  source?: string;
  based_on?: string;
  keel_rules: boolean;
  version: number;
  steps: Step[];
  yaml: string;
  /** v0.9.0, in a project's list: its folder on the Workflows page, how often it ran here, and its newest run. */
  folder?: string | null;
  runs?: number;
  last_run?: { thread_id: string; title: string; status: string; at: string } | null;
  /** 0.16.1 plugin track: a workflow installed from the marketplace (docs/plugins/21-workflow-marketplace.md §3): its
   *  catalog, its name there, the version it has, and a newer version that fits (null: none). Absent for the others. */
  market?: { catalog: string; name: string; version: string; update: string | null } | null;
};

/** v0.9.0: one flow of a project's history (GET /api/projects/{pid}/runs). */
export type RunRow = {
  thread_id: string; title: string; workflow_id: string | null; status: string; phase: string | null; current: string | null;
  waiting: string | null; acs_done: number; acs_total: number; tokens: number; where: "folder" | "worktree"; branch: string | null;
  error: string | null; created_at: string; updated_at: string;
  /** v0.15.4 what it cost, and whether it is the project's newest flow (only that one, when stopped, can be resumed) */
  cost_usd?: number; latest?: boolean;
  /** 0.18.57 what it works on (review-pr: pr:20), and the newer run of the same source that replaced this failed one */
  source?: string | null; replaced_by?: string | null;
};
/** v0.9.0: keel's check of a workflow YAML that is not saved yet (KeelBot's new workflow). */
export type WorkflowCheck = {
  name: string; steps: number; gates: number; keel_rules: boolean; agents: string[]; mcp: string[]; tools: string[];
  commands: string[]; edits_files: boolean; valid: boolean; errors: string[]; warnings: string[];
  /** 0.17.4 each error at its line of the YAML (the editor marks it) */
  lines?: { line: number; message: string }[];
};

/** 0.17.4 the lines a refused workflow save or import names (its 400/422 body's `lines`), or none. */
export function errorLines(err: unknown): { line: number; message: string }[] {
  const lines = err instanceof ApiError ? err.body?.lines : undefined;
  return Array.isArray(lines) ? lines.filter((l): l is { line: number; message: string } => typeof l?.line === "number") : [];
}

/** v0.3: "already-met" = an earlier criterion's code already covers it (shown like done, own label). */
export type AcStatus = "todo" | "red" | "green" | "done" | "already-met";
export type ThreadStatus = "running" | "waiting" | "done" | "failed" | "stopped";
/** One question of the explorer's clarify loop: 2-4 options, the recommended one first. */
export type ClarifyQuestion = {
  id: string;
  question: string;
  why?: string;
  /** keel 0.18.22 `why`: the option takes a reason (a findings gate's "accept"); its answer is "<label>: <reason>" */
  options: { label: string; description?: string; recommended?: boolean; why?: "required" | "optional";
    /** keel 0.18.63 KeelBot's "Switch to Edit" option: picking it switches the chat to that mode, then sends the answer */
    mode?: "edit" | "ask" }[];
  /** keel 0.18.9 several options may be picked (the answer is a list) */
  multiple?: boolean;
  /** B9 false: no "own words" box (a skip menu: words there were read as a step's name) */
  own_words?: boolean;
};
/** keel 0.18.13 what one named choice of a waiting question means (same order as `choices`): its words, whether it
 *  needs a reason (`why`), and which one a plain approve takes (`default`). Answer: approve with payload {choice: name}. */
export type ChoiceInfo = { name: string; label: string; description: string; why: "required" | "optional" | "none"; default: boolean };
/** keel 0.18.28 one highlight.js language of a project's stacks, with its file endings and the stacks that bring it */
export type StackLanguage = { name: string; ext: string[]; stacks?: string[] };
/** 0.18.28 a plain approve's or reject's real effect at a gate; `reason`: "required", "used", or "not used…" */
export type PlainEffect = { does: string; reason: string; choice?: string };

/** v0.17.4 where the criteria's build order came from */
export type AcOrder = { ids: string[]; from: "spec" | "plan" | "keel" | "person" | "id" };
export type ThreadState = {
  /** v0.17.4 the criteria (acs) are in build order; where it came from */
  ac_order?: AcOrder;
  /** 0.18.57 the branch or ref the flow's citations are about: a worktree flow's branch, the reviewed PR's head ref
   *  (refs/keel/review-pr/7) or branch; null: the project folder's checkout */
  source_ref?: string | null;
  thread_id: string;
  project_id: string;
  workflow_id: string;
  title: string;
  status: ThreadStatus;
  current: string | null;
  phase: string;
  ac: string | null;
  acs: { id: string; layer: string; title: string; status: AcStatus }[];
  waiting?: {
    step: string; kind: "gate" | "budget" | "fix" | "clarify" | "usage"; title: string; detail: string; options: ("approve" | "reject")[];
    /** v0.3.1: the question's id (the inbox sends it back so a stale page cannot answer the next question) */
    id?: string;
    labels?: { approve?: string; reject?: string }; questions?: ClarifyQuestion[];
    /** kind "usage": continue | wait | cheaper | stop; kind "gate": the gate's named exits. Resume with payload.choice */
    choices?: string[];
    /** keel 0.18.13 the choices' labels, descriptions and reasons; missing from an older engine (raw `choices` then) */
    choice_info?: ChoiceInfo[];
    /** 0.18.28 the waiting step's own name ("verify the suite + commit"); a suggestion names the gate by it */
    name?: string;
    /** 0.18.64 an AC gate's criterion: the card shows its code (api.acCode) */
    ac?: string;
    /** 0.18.28 what a plain approve and a plain reject do here, and whether the reason is used */
    plain?: { approve?: PlainEffect; reject?: PlainEffect };
  };
  usage: { tokens_in: number; tokens_out: number; tokens_cached?: number; cost_usd: number; premium_requests: number; cap_tokens: number; cap_usd?: number };
  checkpoints: number;
  /** v0.4.2, only in the answer to POST /flows: what the project's caps changed for this flow */
  cap_note?: string;
  error?: string;
  updated_at: string;
  /** v0.2: what still blocks shipping (computed by push_check, refreshed after every commit). */
  blockers?: Blocker[];
  /** v0.2: the init flow's setup ladder. */
  ladder?: LadderRung[];
  /** v0.4.1: how much keel decides by itself in this flow (changeable during a run). */
  run_mode?: RunMode;
  /** v0.3.1: the last 50 gate decisions as text; "auto-approved (mode …)" marks the run mode's own. */
  gate_log?: string[];
};
/** v0.4.1 run modes (engine runtime/run_mode.py). */
export type RunMode = "manual" | "important" | "auto" | "readonly";
/** v0.4.1: what one step really does (engine POST /steps/explain via POST /api/projects/{pid}/workflows/explain-step). */
export type StepRoute = { label: string; to: string | null; to_name: string; text?: string };
export type BucketRule = { bucket: string; what: string; may: "edit" | "new-only" | "delete-only" | "read-only" | "no-access"; label: string; note?: string };
export type CommitInfo = { type: string; about: string; prefix: string; message: string; author: string; may_contain: string[]; may_not_contain: string[]; extra: string[] };
export type ActionDoc = { name: string; known: boolean; summary: string; steps: string[]; for_this_step?: string; after?: string; command?: string };
export type StepRun = {
  checkpoint: string; at: string; note: string; ok: boolean; ac?: string; item?: string; went_to?: string; output?: string;
  commit?: { sha: string; subject: string }; answer?: string; tokens?: { in: number; out: number; step: number };
  markers?: Record<string, string>; decided?: string[];
};
export type StepExplanation = {
  id: string; name: string; kind: StepKind; phase: string; phase_meaning: string; phase_inherited?: boolean; lock?: boolean; thread: boolean;
  workflow?: { id: string; name: string };
  included_from?: { flow: string; step: string; text: string };
  runs_only_when?: string;
  skippable?: { band: string; unit: string; text: string };
  rules: { phase: string; buckets: BucketRule[]; shell_refused: string[]; read_only?: boolean; lane_scoped?: boolean; lane_note?: string; reads_blocked?: string; commit?: CommitInfo };
  loop?: { kind?: string; text?: string; fan_out?: { text: string }; lanes?: Lane[] } | null;
  next: StepRoute[];
  agent?: {
    id: string; about: string; custom?: boolean; tools?: string | null; max_turns?: number | null;
    model: { step: string; agent_file?: string | null; effort?: string | null; rule: string; now?: Partial<Model> };
    knowledge?: { sections: string[]; code_graph: boolean; memory: boolean; strict: boolean; hints?: boolean };
    instructions?: string | null; markers: { name: string; values: string[]; registered: boolean; text: string }[];
    collect?: { key: string; text: string } | null; mcp_tools?: string[]; role?: string;
    prompt: string; placeholders: boolean; prompt_notes?: string[]; system?: string;
  };
  code?: { actions: ActionDoc[]; chain: string | null; text?: string; soft?: string; rounds?: string };
  gate?: { answers: StepRoute[]; notes: string[]; costs?: string };
  branch?: { condition: string; routes: StepRoute[]; rounds?: string };
  last_runs?: { count: number; runs: StepRun[]; now?: string | null; calls?: { agent: string; provider?: string; model?: string; status: string; tokens_in: number; tokens_out: number; result: string }[] };
};
export type ExplainRequest = { workflow_id?: string; workflow?: Workflow; step_id: string; thread_id?: string };

export type BlockerGate = "release" | "coverage" | "deps" | "knowledge" | "secrets";
export type Blocker = { gate: BlockerGate | string; why: string; fix: string };
export type LadderRung = { n: number; name: string; cmd: string; status: "pass" | "fail" | "fixing" | "waiting" | "skipped"; detail?: string };
export type Checkpoint = { id: string; n: number; step: string; at: string; note: string };
/** keel 0.18.11 what one step of a flow really did (GET .../flows/{tid}/step-runs): its agent calls added up per step and
 *  criterion. `tries` counts rounds (parallel lanes are one); `ms` is working time, not a gate's wait; `result` is the
 *  first line of the last answer. */
export type StepRunFacts = {
  step: string; ac: string | null; tries: number; status: JobStatus; agent: string | null; provider: Provider | null; model: string | null;
  effort?: string | null; started_at: string; ended_at: string | null; ms: number; tokens_in: number; tokens_out: number; tokens_cached: number;
  cost_usd: number; result: string | null; call_id: string;
};
/** 0.18.64 one file of an AC gate's commit (GET .../flows/{tid}/ac-code): its diff from the first hunk, cut at 400 lines
 *  or 60 KB (`truncated`); lock, generated and binary files come `folded`, without text (ask for it with sha + path). */
export type AcCodeFile = {
  path: string; old_path?: string; status: "added" | "modified" | "deleted" | "renamed"; added: number; removed: number;
  bucket: "test" | "prod" | "generated" | "lock" | "secret"; binary?: boolean; diff: string | null; lines?: number; truncated?: boolean; folded?: boolean;
  /** 0.18.64 a secret file (a .env, a key): never with its diff */
  note?: string;
};
export type AcCodeCommit = { sha: string; short: string; subject: string; files: AcCodeFile[]; added: number; removed: number; more_files?: number };
/** 0.18.64 the criterion's code at its AC gate: its RED and GREEN commits (null: none found), and the totals */
export type AcCode = {
  ac: string; red: AcCodeCommit | null; green: AcCodeCommit | null; commits: number; files: number; added: number; removed: number;
  truncated: boolean; branch: string | null;
};
export type Estimate = {
  tokens: number;
  low: number;
  high: number;
  cost_usd: number;
  premium_requests: number;
  by_provider: Partial<Record<Provider, number>>;
  per_step: { step: string; tokens: number }[];
};
export type McpServerSpec = { name: string; command: string; args: string[]; env?: Record<string, string>; cwd?: string };

export type EngineEventType =
  | "thread.started" | "step.started" | "step.finished" | "agent.started" | "agent.step" | "agent.finished"
  | "code.started" | "code.step" | "code.finished"
  | "gate.waiting" | "gate.decided" | "budget.warn" | "budget.stop" | "guard.refused" | "thread.done" | "thread.failed"
  | "helper.started" | "helper.step" | "helper.finished" | "helper.permission" | "helper.permission.answered" | "helper.commit"
  /** v0.16.0 a write keel's MCP server made (api McpAudit) */
  | "mcp.call"
  /** a write keel TUI made (api McpAudit, X-Keel-Via: tui), or the xal client (X-Keel-Via: xal) */
  | "tui.call" | "xal.call"
  /** keel 0.18.2 the text an agent or KeelBot is writing now (never stored) */
  | "helper.delta" | "agent.delta";
export const ENGINE_EVENT_TYPES: EngineEventType[] = [
  "thread.started", "step.started", "step.finished", "agent.started", "agent.step", "agent.finished",
  "code.started", "code.step", "code.finished",
  "gate.waiting", "gate.decided", "budget.warn", "budget.stop", "guard.refused", "thread.done", "thread.failed",
  "helper.started", "helper.step", "helper.finished", "helper.permission", "helper.permission.answered", "helper.commit",
  "mcp.call", "tui.call", "xal.call", "helper.delta", "agent.delta",
];
/** v0.16.0: the one generic channel. The api sends every engine event the web does not list above (approval.*, a plugin's
 *  own events) as `event: engine`, its type inside; it lands in `recent` like the others. */
export const ENGINE_CHANNEL = "engine";
export type EngineEvent = {
  /** one of ENGINE_EVENT_TYPES, or any other engine event from the generic channel */
  type: EngineEventType | (string & {});
  thread_id: string;
  project_id: string;
  step?: string;
  at: string;
  call_id?: string;
  data: Record<string, unknown>;
};

// ---------- api types ----------

/** `version` is keel v2's own version. */
export type Health = { ok: boolean; engine: boolean; version: string; fake: boolean; native?: boolean };
/** keel 0.18.22 the stack setup (engine runtime/stack_setup.py): `line` says what it does now ("installing php8.3-cli for
 *  billing-router…") or what is missing (native mode never installs). */
export type StackSetup = {
  state: "idle" | "running" | "ready" | "failed" | "missing" | "needs approval";
  mode: "container" | "native" | "host" | null;
  line: string;
  busy: boolean;
  missing: string[];
  failed: string[];
};

export type Project = {
  id: string;
  name: string;
  root: string;
  branch: string;
  flow: string | null;
  phase: string;
  acs: [number, number];
  waiting: number;
  running: number;
  /** v0.7.x: flows that run or wait, in the project folder and in worktrees */
  flows?: number;
  /** keel 0.18.55 keel knows this project (it has a .keel/config.yml; the init flow writes it). Missing from an older keel. */
  configured?: boolean;
};

// v0.15.7 Add projects from your folders (GET /api/folders, GET /api/folders/browse, POST /api/projects/bulk)
export type FolderRoot = { path: string; exists: boolean };
export type FoundRepo = { path: string; name: string; root: string; project_id: string | null };
export type Folders = { roots: FolderRoot[]; repos: FoundRepo[]; truncated?: boolean };
export type BrowseDir = { path: string; name: string; repo: boolean; project_id: string | null };
export type FolderWalk = { path: string; root: string; parent: string | null; dirs: BrowseDir[]; truncated?: boolean };
export type BulkAdded = { added: Project[]; skipped: { root: string; why: string }[] };

export type FlowView = { thread: ThreadState | null; workflow: Workflow | null };
/** v0.7.x: one flow on the project's board: in the project folder, or in a worktree of its own next to the others. */
export type BoardFlow = {
  thread_id: string; title: string; workflow_id?: string | null; status: ThreadStatus; phase?: string | null; current?: string | null;
  waiting?: { step?: string; kind?: string; title?: string } | null; where: "folder" | "worktree"; worktree?: string | null; branch?: string | null;
  /** the files it changed against the base branch */
  files: string[]; updated_at?: string | null;
  /** finished, but its worktree is still there (Remove the worktree; the branch stays) */
  worktree_left?: boolean;
};
/** The project's flows side by side: files two of them change, branches that conflict, and an order to merge them in. */
export type FlowBoard = { flows: BoardFlow[]; overlaps: { file: string; flows: string[] }[]; conflicts: { a: string; b: string; files: string[] }[];
  order: string[] };
export type FlowWhere = "folder" | "worktree" | "auto";
/** 0.18.57 what a start should know first (GET /projects/{pid}/start-check): can a worktree flow run the tests (no when
 *  they run in a container that sees only the project folder), and how the local base branch differs from its remote. */
export type StartCheck = { worktree?: { ok: boolean; reason?: string; service?: string | null }; base?: string | null };
/** 0.18.57 who already works on a task (a refused start's `owners`) */
export type TaskOwner = { kind: "flow" | "side"; id: string; who: string; why: string };
/** 0.18.57 who else reads the project folder (GET /projects/{pid}/folder-activity): KeelBot's Ask and Edit chats that
 *  answer now or did lately, the flow there, and whether a worktree flow can run the tests (null: not known). */
export type FolderActivity = { chats: { id: string; title: string; mode: string; status: string; updated_at?: string | null }[];
  flow?: string | null; worktree_ok?: boolean | null };

/** v0.8.0 quality runs: an eval set (content/evals) and its cases. */
export type EvalSet = { name: string; description: string; project: string; problems: string[];
  cases: { id: string; title: string; workflow: string; request: string; cap_tokens?: number | null }[] };
/** outcome: end (the PR gate or done) | stuck (at another gate) | cap (its token cap stopped it) | failed | timeout | refused | stopped */
export type QualityCase = { id: string; n: number; eval_set: string; case_id: string; title: string; workflow_id: string; model: Model;
  status: "queued" | "running" | "done" | "stopped"; outcome?: string | null; reached?: string | null; project_id?: string | null;
  thread_id?: string | null; score?: number | null; tokens?: number | null; cost_usd?: number | null; ms?: number | null;
  sendbacks?: number | null; estimate?: number | null; progress?: number | null; started_at?: string | null; ended_at?: string | null };
export type QualityScore = { workflow_id: string; model: Model; score: number; cases: number; reached_end: number; tokens: number; ms: number };
export type QualityRun = { id: string; status: "queued" | "running" | "done" | "stopped" | "failed"; trigger: "manual" | "nightly" | string;
  flows: string[]; models: Model[]; sets: string[]; created_at: string; started_at?: string | null; ended_at?: string | null;
  error?: string | null; scores: QualityScore[]; cases: QualityCase[] };
/** A flow × model over the runs, oldest first; drop: its last score fell 15 points or more since the one before. */
export type QualityLine = { workflow_id: string; model: Model; points: { run_id: string; at: string; score: number }[];
  last?: number | null; previous?: number | null; drop: boolean };
export type QualitySchedule = { enabled: boolean; at: string; flows: string[]; models: Model[]; last_run_date?: string | null };
export type QualityView = { sets: EvalSet[]; active: QualityRun | null; runs: QualityRun[]; lines: QualityLine[]; schedule: QualitySchedule };

export type JobStatus = "running" | "done" | "failed" | "stopped" | "guard" | string;
export type Job = {
  id: string;
  project_id: string;
  thread_id: string;
  agent: string;
  provider: Provider;
  model: string;
  /** Thinking effort the agent ran with; null = the engine's default or a model without effort. */
  effort?: string | null;
  step: string;
  phase: string;
  ac: string | null;
  status: JobStatus;
  started_at: string;
  ended_at: string | null;
  tokens_in: number;
  tokens_cached?: number;   // cache reads: re-sent context, about a tenth of the price
  tokens_out: number;
  cost_usd: number;
  premium_requests: number;
  steps_count: number;
  mcp_calls: number;
};
export type JobStepKind = "text" | "thinking" | "tool" | "read" | "write" | "edit" | "answer" | "guard" | "error" | string;
/**
 * One agent step (CONTRACT.md, "Agent step quality"). Newlines are kept in every text field.
 * - read: `path`, `text` = file content.
 * - write / edit: `path`, `diff` = unified diff, `text` = one-line summary.
 * - tool: `tool`, `text` = the command (or arguments), `output`, `ok`, `ms`; `server` for an MCP tool.
 * - text / thinking / answer: `text` is Markdown.
 */
export type JobStep = {
  n: number;
  at: string;
  kind: JobStepKind;
  text: string;
  tool?: string;
  server?: string;
  path?: string;
  diff?: string;
  output?: string;
  ms?: number;
  ok?: boolean;
};
export type JobDetail = Job & { steps: JobStep[] };

/** The engine's code graph index of a project (engine runtime/scan.py), read over http from the Graph plugin's
 *  /index route (plugins/graph): the Code page's status bar and index row show it. */
export type IndexStatus = {
  project: string;
  status: "idle" | "indexing" | "ready" | "failed";
  files: number;
  symbols: number;
  indexed_at?: string | null;
  error?: string | null;
  available?: boolean;
};

// (the Code page's repo types and calls are its own since step 3: plugins/code/web/codeApi.ts)
export type Commit = { sha: string; message: string; author: string; at: string; keel?: boolean };
/** A file's diff (the Code plugin's GET /repo/diff; the Code Review plugin's diffs have the same shape). */
export type FileDiff = { path: string; against: string; ref: string; diff: string; binary: boolean; truncated: boolean };
export type Unlock = { path: string; phase: string };
export type KeelDoc = { path: string; what: string; by: string; updated: string; status: "ok" | "live" | "check" };
export type FactKind = "fact" | "rule" | "flaky" | "unlock";
export type Fact = { id: string; title: string; text: string; kind: FactKind; source: string; at: string };
/** 0.18.57 the project notes' switch (knowledge.build in .keel/config.yml): on = the agents get them and the flows keep
 *  them up to date; setting = what the config says (null: nothing, then on only for a project that has notes). */
export type Notes = { on: boolean; setting: "on" | "off" | null; written: number };
export type Memory = {
  facts: Fact[];
  knowledge: { id: string; status: "written" | "stale" | "missing"; words: number; cites: number }[];
  notes?: Notes | null;
};

// keel v1 .keel/map.json (lib/map.js). Only the parts the Map page draws are typed.
export type MapRow = { t: string; flag?: string | null };
export type MapNode = {
  id: string;
  kind: string;
  title: string;
  sub?: string;
  rows?: MapRow[];
  x: number;
  y: number;
  w: number;
  h: number;
  cite?: { rel: string; line: number } | null;
  drill?: string;
  unsourced?: boolean;
};
export type MapEdge = { from: string; to: string; kind: string; label?: string; d: string; lx?: number; ly?: number };
export type MapBand = { id: string; label: string; x: number; y: number; w: number; h: number };
export type MapLevel = { nodes: MapNode[]; edges: MapEdge[]; bands?: MapBand[]; width: number; height: number; overflow?: number };
export type Cite = { rel: string; line: number };
/** The database the SQL migrations leave behind (engine runtime/sqlschema.py). v0.5.1; older maps have only `levels.er`. */
export type DbColumn = {
  name: string; type: string; nullable: boolean; default?: string | null; pk: boolean; unique: boolean;
  identity?: boolean; generated?: string | null; comment?: string | null; cite?: Cite | null;
  fk?: { table: string; column?: string | null; missing?: boolean } | null;
};
export type DbKey = { name?: string | null; columns: string[]; cite?: Cite | null };
export type DbIndex = DbKey & { unique: boolean; method?: string | null; where?: string | null };
export type DbForeignKey = DbKey & {
  ref_table: string | null; ref_name: string; ref_columns: string[]; on_delete?: string | null; on_update?: string | null; missing?: boolean;
};
export type DbTable = {
  id: string; name: string; schema?: string | null; kind: "table" | "view" | "materialized view" | string;
  cite?: Cite | null; comment?: string | null; columns: DbColumn[];
  primary_key?: DbKey | null; uniques?: DbKey[]; indexes?: DbIndex[]; foreign_keys?: DbForeignKey[]; checks?: number;
  changes?: (Cite & { what: string })[]; definition?: string | null; uses?: string[];
};
export type DbRelation = {
  id: string; kind: "fk" | "uses" | string; name?: string | null; from: string; from_columns: string[]; to: string; to_columns: string[];
  on_delete?: string | null; on_update?: string | null; nullable?: boolean; one_to_one?: boolean; self?: boolean; cite?: Cite | null;
};
export type DbSchema = { tables: DbTable[]; relations: DbRelation[] };
export type ApiEndpoint = { method: string; path: string; cite?: Cite | null; summary?: string; tags?: string[]; operation?: string | null };

export type KeelMap = {
  sha: string;
  at: string;
  demo?: boolean;
  limits?: string[];
  counts?: Record<string, number>;
  sources?: { contract?: string | null; migrations?: string[]; scanned?: number; looked_in?: string[]; configured?: boolean; config?: string };
  schema?: DbSchema;
  api?: { contract?: string | null; endpoints: ApiEndpoint[] };
  levels: {
    system?: MapLevel;
    flow?: MapLevel & { byJourney?: Record<string, MapLevel>; source?: string };
    modules?: MapLevel;
    classes?: { byModule: Record<string, MapLevel> };
    er?: MapLevel;
  };
};
export type MapResponse = KeelMap | { missing: string };

/** One wiki page in the menu. id, title and status are in every keel; the rest came with the wiki's order (a keel
 *  before it leaves them out). kind: overview | knowledge | spec | decision | runbook | workflow; number: the number in
 *  the file name ("101"); label: the title without it; updated: the file's time; also: the same file in other folders. */
export type WikiItem = {
  id: string; title: string; status?: string | null;
  kind?: string | null; path?: string | null; number?: string | null; label?: string | null; updated?: string | null; also?: string[] | null;
  /** 0.18.55 a workflow page's workflow: where it comes from (keel | yours | marketplace), keel Product's ("product")
   *  and what it is based on. The Wiki groups the workflows by purpose with them. */
  source?: string | null; addon?: string | null; based_on?: string | null;
};
export type WikiTree = {
  sections: { id: string; title: string; items: WikiItem[] }[];
  /** v0.15.8 an external layout whose folder keel cannot see: what to do */
  notice?: string | null;
  /** v0.15.8 where keel keeps the project's files: repo | keel | external */
  layout?: string | null;
  /** 0.18.57 the project notes' switch: off, the menu says so with Build them */
  notes?: Notes | null;
};
export type WikiPage = { id: string; title: string; markdown: string; meta: Record<string, unknown> };
/** 0.17.3 a spec's build status (the Wiki plugin's GET /projects/{pid}/specs/status). */
export type SpecState = "built" | "in_progress" | "stopped" | "not_built";
export type SpecFlow = {
  thread_id: string; workflow: string | null; title: string; status: string; state: Exclude<SpecState, "not_built">;
  phase: string | null; waiting: string | null; created_at: string | null; updated_at: string | null; resumable: boolean;
};
export type SpecStatus = {
  spec: string; path: string; title: string; acs: number; state: SpecState;
  thread_id?: string | null; workflow?: string | null; flow_title?: string | null; status?: string | null; phase?: string | null;
  waiting?: string | null; updated_at?: string | null; resumable?: boolean; why?: string | null; flows: SpecFlow[];
};

export type InstallReview = {
  from?: string;
  version?: string;
  agents: string[];
  mcp: string[];
  gates: number;
  est_tokens: number;
  edits_files: boolean;
  steps?: number;
  warnings?: string[];
};
export type LibraryItem = {
  id: string;
  name: string;
  source: string;
  version: string;
  about: string;
  steps: number;
  gates: number;
  est_tokens: number;
  agents: string[];
  mcp: string[];
  edits_files: boolean;
  installed: boolean;
};

export type Agent = {
  id: string;
  label: string;
  about: string;
  custom: boolean;
  phases: string[];
  model: Model;
  tools: string[];
  skills: string[];
  prompt: string;
  enabled: boolean;
  overridden: string[];
  /** v0.2: which lane the agent works in. "follow" = the AC's layer decides. */
  lane?: AgentLane;
  /** v0.4: the project knowledge it uses (default from its file, then this project's change). */
  knowledge?: AgentKnowledge;
  /** Rough tokens of its ticked sections that exist in the project (file size / 4). */
  knowledge_tokens?: number;
  /** The project's docs/knowledge sections that exist → rough tokens. */
  knowledge_files?: Record<string, number>;
};
export type AgentLane = "follow" | "api" | "web";
export const KNOWLEDGE_SECTIONS = ["architecture", "domain", "conventions", "data", "integrations", "journeys"] as const;
export type KnowledgeSection = (typeof KNOWLEDGE_SECTIONS)[number];
export type AgentKnowledge = { sections: KnowledgeSection[]; code_graph: boolean; memory: boolean; strict: boolean;
  /** keel's own "where to look" lookups in the code graph, in the prompt (no tool call); off by default */
  hints?: boolean };
export type CustomAgent = {
  id: string;
  label: string;
  about: string;
  phases: string[];
  model: Model;
  tools: string[];
  skills: string[];
  prompt: string;
};
export type TestResult = { ok: boolean; text?: string; ms: number; error?: string };

export type SkillSource = "keel" | "keel pack" | "claude" | "yours" | "marketplace" | "repo";
/** 0.18.6 where a skill from the marketplace came from; edited: its text was changed here since. */
export type SkillMarket = { catalog: string; name: string; version: string; edited: boolean };
export type Skill = {
  id: string;
  kind: string;
  source: SkillSource;
  stack: string;
  version: string;
  tokens: number;
  agents: string[];
  when: string;
  enabled: boolean;
  about?: string;
  /** 0.18.6 a skill installed from the marketplace */
  market?: SkillMarket | null;
};
export type SkillDetail = Skill & { body: string; refs: { path: string; tokens: number }[] };
/** 0.18.57 Skills › Found in the repo: the repo's own docs and skills for coding agents (init, knowledge refresh). */
export type RepoSkill = { id: string; name: string; path: string; about: string; use: "tests" | "implementation" | null; why: string; agents: string[] };
export type RepoSkills = { on: boolean; found_at: string | null; docs: { path: string; title: string }[]; skills: RepoSkill[]; attached: string[] };

export type Stack = {
  name: string;
  lane: string;
  source: string;
  detected: boolean;
  detect: string;
  layers: string[];
  commands: { name: string; cmd: string }[];
  /** v0.4.1: what keel runs for this stack (formatters, linters); `off` = turned off with `<name>: false`. */
  tools: StackTool[];
  skills: string[];
  /** v0.2: a keel pack that `keel packs add` can install into this project. */
  installable?: boolean;
  /** keel 0.18.9 where it comes from: keel, keel pack (not installed), pack (installed here), marketplace, project */
  origin?: string;
  /** turned off for this project (.keel/config.yml stacks.ignore): not used, whatever detection says */
  off?: boolean;
  /** the project added it, so it can be removed; else `why` says why not (keel's own: turn it off instead) */
  removable?: boolean;
  why?: string | null;
  market?: { catalog?: string; version?: string; edited?: boolean } | null;
  /** the project's file, relative to its root */
  file?: string | null;
};
/** keel 0.18.9 what keel reads in the project's files for a new stack (GET .../stacks/suggest). */
export type StackSuggestion = {
  lane: "api" | "web";
  lanes: Record<string, string[]>;
  name: string;
  detect: { files: string[]; extensions: string[]; package_deps: string[] };
  layers: { id: string; what?: string; where?: string }[];
  skills: Record<string, string>;
  closest: string | null;
  commands: Record<string, { cmd: string; from: string }[]>;
  keys: { key: string; what: string; placeholder: string }[];
  tools: { name: string; run: string; on: string; fail: string; match?: string; description?: string; from?: string }[];
  read: string[];
};
/** keel 0.18.9 the New stack form's answers (the engine writes the YAML: runtime/stack_edit.render). */
export type StackForm = {
  name: string;
  lane: string;
  detect: { files: string[]; package_deps: string[]; extensions: string[]; exclude_files?: string[] };
  layers: { id: string; what?: string; where?: string }[];
  commands: Record<string, string>;
  tools: Record<string, { run: string; on: string; fail: string; match?: string; description?: string } | false>;
  skills?: Record<string, string>;
};
/** keel's check of a stack (POST .../stacks/check): it saves nothing. */
export type StackCheck = {
  ok: boolean;
  errors: string[];
  warnings: string[];
  name: string | null;
  lane: string | null;
  review: { lane: string; detect: Record<string, string[]>; layers: string[]; commands: Record<string, string>;
    tools: { name: string; run: string; on: string; fail: string }[]; skills: string[] } | null;
  detects_here: boolean | null;
  replaces: "keel" | "project" | null;
  yaml: string;
  skills_missing?: string[];
};
export type StackTool = { name: string; on: string; fail: string; description?: string; kind?: string; match?: string; off?: boolean };
/** keel v1's stack files describe `detect` and `layers` as objects and `skills` as a map; older data is plain text/lists.
 *  Both become what the Stacks page shows, so a new shape can never crash the page. */
export function normalizeStack(raw: unknown): Stack {
  const r = (raw ?? {}) as Record<string, unknown>;
  const text = (v: unknown): string => {
    if (v == null) return "";
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
    if (Array.isArray(v)) return v.map(text).filter(Boolean).join(", ");
    if (typeof v === "object") {
      return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${text(x)}`).filter((t) => !t.endsWith(": ")).join(" · ");
    }
    return "";
  };
  const detect = r.detect && typeof r.detect === "object" && !Array.isArray(r.detect)
    ? Object.values(r.detect as Record<string, unknown>).map(text).filter(Boolean).join(" · ")
    : text(r.detect);
  const layers = (Array.isArray(r.layers) ? r.layers : []).map((l) =>
    l && typeof l === "object" ? String((l as Record<string, unknown>).id ?? text(l)) : text(l)).filter(Boolean);
  const skills = Array.isArray(r.skills) ? r.skills.map(text).filter(Boolean)
    : r.skills && typeof r.skills === "object" ? Object.values(r.skills as Record<string, unknown>).map(text).filter(Boolean) : [];
  const list = (v: unknown) => (Array.isArray(v) ? v : []);
  return {
    name: String(r.name ?? ""), lane: text(r.lane), source: text(r.source), detected: !!r.detected, detect, layers,
    commands: list(r.commands).map((c) => ({ name: text((c as Record<string, unknown>)?.name), cmd: text((c as Record<string, unknown>)?.cmd) })),
    tools: list(r.tools).map((t) => {
      const o = (t ?? {}) as Record<string, unknown>;
      const tool: StackTool = { name: text(o.name), on: text(o.on), fail: text(o.fail) };
      if (o.description) tool.description = text(o.description);
      if (o.kind) tool.kind = text(o.kind);
      if (o.match) tool.match = text(o.match);
      if (o.off) tool.off = true;
      return tool;
    }),
    skills, installable: !!r.installable,
    origin: r.origin == null ? text(r.source) : text(r.origin), off: !!r.off, removable: !!r.removable,
    why: r.why == null ? null : text(r.why), market: (r.market && typeof r.market === "object" ? r.market : null) as Stack["market"],
    file: r.file == null ? null : text(r.file),
  };
}

export type McpServer = McpServerSpec & { enabled: boolean; builtin: boolean; status: "ok" | "off" | "error"; tools: string[]; label?: string };
export type McpAllow = Record<string, string[]>;

/** v0.10.0 a keel plugin (Control › Plugins): what it adds, and whether it is on for this project (scope: its own choice
 *  or every project's). */
export type PluginAction = { name: string; with: Record<string, "required" | "optional">; summary: string };
export type Plugin = {
  name: string; title: string; description: string; needs: string[]; tools: { server?: string; read?: string[] };
  actions: PluginAction[]; shows_in: string[]; commands: { name: string; description: string }[];
  enabled?: boolean; scope?: "project" | "all" | null;
};
// (the Database and Git plugins' types and calls are their own: plugins/db/web/dbApi.ts, plugins/git/web/gitApi.ts)
/** v0.11.0 CI/CD plugin: a pipeline run on GitHub Actions; a run's detail adds its jobs and the failed steps' log. */
export type CiRun = {
  id: number; workflow: string; title: string; branch: string; sha: string; event: string; status: string;
  conclusion: string | null; url: string; attempt?: number; created_at: string; updated_at: string; failed: boolean;
  jobs?: { id: number; name: string; status: string; conclusion: string | null; url: string; failed_steps: string[] }[];
  log?: string;
};
export type CapScope = "day" | "flow" | "step" | "api_month";
export type Cap = { id: string; scope: CapScope; limit: number; unit: "tokens" | "usd"; action: "pause" | "cheaper" | "stop" };
/** v0.4.2 `GET /api/projects/{pid}/caps/left`: what each cap leaves for a flow that starts now. */
export type CapLeft = {
  id: string; scope: CapScope; unit: Cap["unit"]; action: Cap["action"]; limit: number;
  /** this project's use today (day) or this month's API-key use (api_month); 0 for flow and step */
  used: number;
  left: number;
  window: "day" | "month" | "flow" | "step";
  /** ISO, UTC: the next midnight (day) or the 1st of next month (api_month) */
  resets_at?: string | null;
  /** false: keel cannot check it (dollars per step), it limits nothing */
  checked: boolean;
  note: string;
};
/** The limits a flow that starts now gets: the smallest cap left binds. */
export type FlowLimits = {
  cap_tokens: number; on_cap: OnCap; cap_usd?: number | null; on_cap_usd?: OnCap | null;
  step_cap_tokens?: number | null; step_on_cap?: OnCap | null;
  /** a used-up cap says cheaper: every agent starts on the cheaper model */
  cheaper: boolean;
  /** "settings" | "flow" | a cap id */
  tokens_from?: string | null; usd_from?: string | null; step_from?: string | null;
  notes: string[];
  refused?: { cap_id: string; error: string; hint: string } | null;
};
export type CapsLeft = { caps: CapLeft[]; next_flow: FlowLimits };
/** v0.3 model catalog (`GET /api/providers/models`): per provider, the models of each mode and the effort choices. */
/** 0.18.46 badge: "local" for a model on a local server; tools: false = it cannot call tools (ask-type uses only);
 *  context: its window in tokens, when the server says. */
export type CatalogModel = { id: string; label: string; efforts?: string[]; badge?: string; tools?: boolean; context?: number };
export type CatalogSource = "cli" | "cache" | "builtin" | "server";
export type CatalogProvider = {
  label: string;
  /** Only the modes this provider has. */
  modes: Partial<Record<Mode, CatalogModel[]>>;
  /** Default effort choices for this provider ([] = no effort setting). */
  efforts: string[];
  default: { mode: Mode; model: string; effort?: string };
  /** Where the list came from: cli = asked the installed CLI. */
  source: CatalogSource;
};
export type Catalog = Record<string, CatalogProvider>;
export type Budget = {
  month: { tokens: number; cost_usd: number; premium_requests: number; flows: number };
  days: { day: string; claude: number; codex: number; copilot: number; fake: number }[];
  caps: Cap[];
  top: { agent: string; provider: Provider; tokens: number; cost_usd: number }[];
  recent: { title: string; estimate: number; real: number; status: string }[];
  /** 0.18.55 every project with agent runs this month (keel Product's own runs too); `all` is their sum. */
  projects?: ProjectSpend[];
  all?: Spend;
};
export type ProjectSpend = { project_id: string; name: string; tokens: number; cost_usd: number; api_cost_usd: number; calls: number };
/** Tokens (cached input counts a tenth) and dollars at API prices. */
export type Spend = { tokens: number; cost_usd: number };
/** A running or waiting flow and what it used against its own caps (null = no cap). */
export type FlowSpend = {
  thread_id: string; title: string; status: ThreadStatus; tokens: number; cost_usd: number; cap_tokens: number | null; cap_usd: number | null;
};
/** v0.6.0 KeelBot (`/api/projects/{pid}/helper/...`): chat sessions in the Code page, run by keel's own harness. */
export type HelperMode = "ask" | "edit" | "fix" | "side";
/** A side session's hand-over: its branch, the commits kept on it, what is not kept yet, and its last answer. */
export type HelperHandover = { session: string; title: string; branch: string; base: string; worktree: string;
  commits: { sha: string; subject: string }[]; uncommitted: string[]; asked: string[]; answer: string };
/** Fix mode: a file KeelBot changed in this chat, against what it was before its first change. */
export type HelperChange = { path: string; status: "added" | "modified" | "deleted"; added: number; removed: number; diff: string };
/** A Fix chat's command that waits for the person's OK (a card in the panel and the Inbox). */
export type HelperQuestion = { id: string; session: string; project: string; thread_id?: string | null; kind: string; command: string;
  path?: string; title: string; at: string;
  /** keel 0.18.18 what to know before saying yes (the command throws work away, a flow works in this folder) */
  warning?: string;
  /** keel 0.18.18 what Always keeps (`git checkout *`); "" when the command is asked every time */
  always?: string };
/** Done: keel's commit of KeelBot's files after the checks, or why not (no change, the checks failed, the commit refused). */
export type HelperDone = { ok: true; sha: string; message: string; files: string[]; checks?: string | null }
  | { ok: false; step: "changes" | "checks" | "commit"; error: string; command?: string; output?: string };
export type HelperMessage = {
  n: number;
  /** user: the person; helper: an answer; note: a turn that stopped or failed (its text says why) */
  role: "user" | "helper" | "note";
  text: string;
  /** the turn's agent call: its steps come from /jobs/{call_id}/steps and live agent steps */
  call_id?: string | null;
  data: { status?: string; provider?: string; model?: string; effort?: string; tokens_in?: number; tokens_out?: number; tokens_cached?: number;
    cost_usd?: number; ms?: number; command?: string; mentions?: HelperMention[]; selection?: HelperSelection; sha?: string; files?: string[];
    /** 0.18.20 an answer: its turn (the user message's n); `files` is then what this answer changed ([] for none) */
    turn?: number;
    /** 0.18.19 part of an answer /undo took back (kept, shown dim); final: a newer message came, /redo cannot bring it back */
    reverted?: boolean; final?: boolean;
    /** 0.18.23 an answer: the mode it ran in, and the model's thinking (kept for a reload, never sent to the model) */
    mode?: string; thinking?: string; thinking_ms?: number;
    /** 0.18.57 an answer: what it was made from (its file:line links open the file as that ref has it) */
    source?: AnswerSource;
    /** 0.18.57 Ask and Edit: the branch of the project folder this answer read, and a switch since the last answer
     *  (during: while it ran) — the chat's banner "a flow switched this folder to <branch> at <time>" */
    folder_branch?: string; branch_switch?: FolderSwitch };
  at: string;
};
/** 0.18.57 The branch (or commit) an answer was made from, the side session's worktree, and the refs it read files of
 *  with `git show <ref>:<path>` (a PR head, another branch): `paths` maps such a path to its ref. */
export type AnswerSource = { ref?: string | null; sha?: string | null; worktree?: string | null; paths?: Record<string, string> };
/** The ref a `path` cited in an answer belongs to: the ref it was read from, else the answer's own. */
export const sourceRef = (s: AnswerSource | null | undefined, path: string): string | null =>
  s?.paths?.[path] ?? s?.paths?.[path.replace(/^\.\//, "")] ?? s?.ref ?? null;
/** 0.18.57 the project folder moved to another branch (a flow on its own branch checked it out) */
export type FolderSwitch = { from: string; to: string; at?: string | null; by?: string; during?: boolean };
export type HelperSession = {
  id: string; project: string; root: string; mode: HelperMode; title: string; model: Model;
  status: "idle" | "running" | "failed"; error?: string | null; thread_id?: string | null; grants?: string[];
  /** Fix: the phase whose rules KeelBot works in (at a gate, the phase of the work under review, e.g. green) */
  phase?: string | null;
  /** Side: its own worktree (null once handed over) on branch keel/helper/<id>, from base_sha */
  worktree?: string | null; branch?: string | null; base_sha?: string | null;
  tokens_in: number; tokens_out: number; tokens_cached: number; cost_usd: number; turns: number;
  created_at: string; updated_at: string;
  messages?: HelperMessage[]; busy?: boolean;
  /** v0.15.2 the folder it is in (one of KeelBot's chat folders, plugins/keelbot), or none */
  folder?: string | null;
  /** P1-1 how full the model's context window is after the last turn: the last turn's tokens_in + tokens_out +
   *  tokens_cached, and the model's window size (engine/keel_engine/models/catalog.py's context_window). */
  ctx_tokens?: number; ctx_window?: number;
  /** P1-3 in the session list only: its last user or KeelBot message as one short line ("" when none), and how many
   *  messages it stores (plugins/keelbot helper.py list_sessions) */
  last_message?: string; message_count?: number;
  /** 0.18.57 Ask and Edit chats: the branch the project folder is on now (the panel compares it with the last answer's) */
  folder_branch?: string | null;
};
export type HelperMention = { kind: "file" | "symbol" | "ac" | string; value: string; file?: string; line?: number };
export type HelperSelection = { path: string; from?: number; to?: number; text: string };
export type HelperCommand = { name: string; description: string; plugin: string; source: "keel" | "project" | string };
export type HelperTurnStarted = { session: string; call_id: string; n: number; command?: string | null };

/** A symbol the code graph finds by name (`GET /api/projects/{pid}/graph/search`, the Graph plugin's route): the
 *  launcher, KeelBot's @ and the Code page search with it. The Graph page's own types are the plugin's
 *  (plugins/graph/web/graphApi.ts). */
export type GraphHit = { id: string; name: string; kind: string; file: string; line: number; unit: string; group: string };

/** v0.5.2 `GET /api/projects/{pid}/budget/now`, for the budget bar on every page: caps = the day and month caps. */
export type BudgetNow = { today: Spend; month: Spend; flows: FlowSpend[]; caps: CapLeft[];
  /** 0.18.55 every project together (keel Product's own runs too) */
  all_today?: Spend; all_month?: Spend };
export type Limit = {
  id: string; name: string; unit: string; used: number; cap: number; note: string;
  /** The provider's own numbers (usage dashboard); absent = only the manual cap and keel's own count. */
  source?: string | null; window?: string | null; used_pct?: number | null; remaining?: number | null;
  resets_at?: string | null; fetched_at?: string | null;
};

/** One window of a provider's plan (`GET /api/usage/providers`): used_pct is 0..1, null when the source does not say. */
export type UsageWindow = {
  window: string; label: string; used_pct?: number | null; used?: number | null; cap?: number | null;
  remaining?: number | null; resets_at?: string | null; status?: string | null;
};
/** A usage card: only providers that are set up get one. */
export type ProviderUsage = {
  id: "claude" | "codex" | "copilot" | "api" | string; name: string; kind: "subscription" | "api";
  source: string; fetched_at?: string | null; windows: UsageWindow[]; live: boolean; can_refresh: boolean;
  error?: string | null; note?: string | null;
};

export type GatesMode = "every-ac" | "end-of-lane" | "end";
export type OnCap = "pause" | "cheaper" | "stop";
export type Settings = {
  gates_mode: GatesMode;
  /** v0.4.1: the run mode a new flow starts with */
  run_mode?: RunMode;
  fix_attempts: number;
  coverage_min: number;
  default_model: Model;
  implementer_model: Model;
  reviewer_model: Model;
  cheaper_model: Model;
  /** 0.18.46 this project's agents run on local models only: keel refuses a cloud model for them */
  local_only?: boolean;
  cap_tokens: number;
  on_cap: OnCap;
  /** warn / pause before a subscription agent when its plan window is this used (0..1) */
  usage_warn?: number;
  usage_pause?: number;
  branch_pattern: string;
  web_lane_worktree: boolean;
  push_pr: string;
  /** who keel's commits are by ("Name <email>"; empty: the project's git name, else KeelBot) */
  commit_author?: string;
  /** keel's commits end with Co-Authored-By: KeelBot <keel.dev.bot@gmail.com> (on by default) */
  commit_coauthor?: boolean;
  /** v0.11.0 CI/CD plugin: when a pipeline fails: notify | fix (start the ci-fix flow) | quiet */
  ci_on_failure?: "notify" | "fix" | "quiet";
  /** v0.13.0 what this keel does (General only): auto, dev, product or both */
  keel_mode?: string;
  notify: "all" | "needs_you" | "none";
  env_names: string[];
  mcp: string[];
  /** 0.18.57 the repo's own skills are loaded for their agents (Skills › Found in the repo) */
  repo_skills?: boolean;
};
export type ProjectSettings = { general: Settings; overrides: Partial<Settings>; effective: Settings;
  /** B15 the General keys someone saved (the others are keel's defaults). */
  general_saved?: string[] };

export type Connections = {
  providers: {
    id: Provider;
    label: string;
    // need: what a mode that is not ready misses (keel 0.18.12+): the CLI, or keel's login for it
    modes: { id: Mode; label: string; ready: boolean; detail: string; need?: "cli" | "login" | null }[];
    selected: Mode;
    key_set: boolean;
    key_hint?: string;
    login_secret?: string | null;  // CLI login secret name (subscription), e.g. CLAUDE_CODE_OAUTH_TOKEN
    login_set?: boolean;
    login_hint?: string | null;
  }[];
  machine: { name: string; ok: boolean; version?: string }[];
};

/** 0.18.46 Connections › Local models: the model servers (never their keys) and the URL each kind usually has. */
export type LocalServerKind = "ollama" | "lmstudio" | "vllm" | "llamacpp" | "openai-compatible";
export type LocalEndpoint = {
  name: string; kind: LocalServerKind; url: string; context?: number | null; price_in?: number | null; price_out?: number | null;
  key_set: boolean; key_hint?: string | null;
};
export type LocalModels = { enabled: boolean; endpoints: LocalEndpoint[]; kinds: LocalServerKind[]; defaults: Record<string, string> };
export type LocalEndpointBody = {
  kind: LocalServerKind; url: string; key?: string; clear_key?: boolean; context?: number | null; price_in?: number | null; price_out?: number | null;
};
export type LocalTest = { ok: boolean; ms: number; models?: { id: string; label?: string; context?: number }[]; error?: string; hint?: string };
/** 0.18.46 Connections › Vertex AI: never the service-account key, only its account's e-mail. */
export type VertexView = { project: string; region: string; credentials: "service-account" | "adc"; key_set: boolean; client_email?: string | null; ready: boolean };
export type VertexBody = { project: string; region: string; credentials_json?: string; clear_credentials?: boolean };

export type NotificationType = "review" | "failed" | "budget" | "finished" | "started";
export type Notification = {
  id: string;
  type: NotificationType;
  project_id: string;
  title: string;
  body: string;
  link: string;
  at: string;
  read: boolean;
  /** v0.4.1: a gate's notification knows its thread and step; done = that gate was decided (anywhere). */
  thread_id?: string | null;
  step?: string | null;
  done?: boolean;
  /** 0.18.55 at most three short lines (each one fact with a mark): what the toast and the bell list show. */
  summary?: string[];
};
export type NotificationSettings = {
  sound: boolean;
  volume: number;
  tone: "chime" | "soft" | "off";
  popup: boolean;
  desktop: boolean;
  scope: "all" | "project";
  kinds: Record<NotificationType, boolean>;
  quiet: boolean;
};

// ---------- transport ----------

/** An api error: `error` is what went wrong, `hint` (when the api sends one) says what to do. */
export class ApiError extends Error {
  status: number;
  hint?: string;
  /** Extra lines some errors carry, e.g. workflow validation `errors: string[]`. */
  details?: string[];
  /** The whole JSON answer of an error, for the fields some errors add (a 409's missing_plugins, missing_agents). */
  body?: Record<string, unknown>;
  constructor(message: string, status: number, hint?: string, details?: string[], body?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.hint = hint;
    this.details = details;
    this.body = body;
  }
}

export const OLD_SERVER_HINT = "The server sent a web page instead of data. Is the keel api running on port 8080?";

/**
 * Who sends the requests. The web app keeps the default: the same origin ("") and the browser's fetch. keel's terminal
 * app (cli/, keel TUI) sets keel's address, Node's fetch and the headers that say who acts (X-Keel-Via: tui), so it
 * uses this very client instead of a copy.
 */
export type Transport = {
  /** keel's address before "/api", e.g. http://localhost:8080; "" = this page's own origin */
  base: string;
  /** extra headers for every request: a map, or a function asked at each request */
  headers: Record<string, string> | (() => Record<string, string>);
  /** the fetch to use; none = the global fetch at the time of the request */
  fetch?: typeof fetch;
};
let transport: Transport = { base: "", headers: {} };
/** Change the transport (keel TUI); the web app never calls it. */
export function setTransport(t: Partial<Transport>) {
  transport = { ...transport, ...t };
}
/** The full URL of an api path ("/projects" → base + "/api/projects"). */
export const apiUrl = (path: string) => transport.base + "/api" + path;

async function request<T>(method: string, path: string, body?: unknown, opts: { text?: boolean } = {}): Promise<T> {
  let res: Response;
  const extra = typeof transport.headers === "function" ? transport.headers() : transport.headers;
  const headers = { ...extra, ...(body === undefined ? {} : { "content-type": "application/json" }) };
  try {
    // v0.18.1 X-Keel-Web says the call comes from keel's own page: another site's page cannot send it (keel answers
    // no CORS preflight), so a change needs it next to the sign-in cookie (api auth/LocalAuth). keel TUI's transport
    // adds its own credential (Authorization: Bearer, the web key).
    res = await (transport.fetch ?? fetch)(apiUrl(path), {
      method,
      credentials: "same-origin",
      headers: { "X-Keel-Web": "1", ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError("Cannot reach the keel api.", 0, "Start it (docker run … or ./gradlew bootRun) and reload.");
  }
  const type = res.headers.get("content-type") ?? "";
  if (res.status === 401 && res.headers.get("x-keel-auth") === "sign-in") signInNeeded();
  if (!res.ok) {
    let err: { error?: string; hint?: string; errors?: unknown } = {};
    if (type.includes("json")) err = await res.json().catch(() => ({}));
    const details = Array.isArray(err.errors) ? err.errors.map(String) : undefined;
    throw new ApiError(err.error || `${res.status} ${res.statusText}`.trim(), res.status, err.hint, details, err as Record<string, unknown>);
  }
  if (opts.text) return (await res.text()) as T;
  if (res.status === 204 || res.headers.get("content-length") === "0") return undefined as T;
  if (!type.includes("json")) {
    const txt = await res.text();
    if (!txt) return undefined as T;
    throw new ApiError("Unexpected answer from the api.", res.status, OLD_SERVER_HINT);
  }
  return (await res.json()) as T;
}

export const get = <T>(path: string) => request<T>("GET", path);
export const post = <T>(path: string, body: unknown = {}) => request<T>("POST", path, body);
export const put = <T>(path: string, body: unknown) => request<T>("PUT", path, body);
export const patch = <T>(path: string, body: unknown) => request<T>("PATCH", path, body);
export const del = <T = void>(path: string) => request<T>("DELETE", path);
export const getText = (path: string) => request<string>("GET", path, undefined, { text: true });

// ---- login helper ----
/** Connections › GitHub: is a token set, and when it came from "Sign in with GitHub", who it is (never the token). */
export type GitHubState = { set: boolean; hint: string | null; from: "keel" | "env" | null; login?: string | null; scopes?: string[]; host?: string; via?: "gh" | "oauth" | null };
export type GitHubLoginView = { id: string; status: "starting" | "waiting" | "done" | "failed" | "cancelled"; host: string; url: string | null; code: string | null;
  message: string; login: string | null; scopes: string[]; via: string | null };
export type LoginView = { id: string; provider: string; status: "starting" | "waiting" | "code_needed" | "done" | "failed" | "cancelled";
  url?: string | null; code?: string | null; message: string; hint?: string | null; error?: string | null };

// ---- workspace Doctor ----
export type DoctorAction = "commit" | "stash" | "exclude" | "ignore" | "keep";
export type DirtyFile = { path: string; status: string; size: number; kind: "tooling" | "docs" | "code" | "local" | "secret"; secret: boolean; tracked: boolean };
export type PlanItem = { id: string; title: string; why: string; action: DoctorAction; files: string[]; message?: string | null; patterns?: string[] | null };
export type Diagnosis = { by: string; summary: string; files: DirtyFile[]; plan: PlanItem[]; note?: string | null; tokens_in: number; tokens_out: number };
export type DoctorApplied = { results: { action: string; files: string[]; ok: boolean; detail: string }[]; remaining: string[]; clean: boolean };

const q = (params: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  });
  const str = s.toString();
  return str ? "?" + str : "";
};
const e = encodeURIComponent;

// ---------- routes ----------

/** v0.13.0 what this keel does: its mode, the parts that are on, the installed add-ons and the menu items they add. */
export type AddonScreen = { id: string; label: string; group: string; needs_project: boolean; addon: string };
export type Features = {
  mode: "dev" | "product" | "both" | string;
  modes: string[];
  parts: Record<string, boolean>;
  addons: { name: string; version: string; title: string; part: string; on: boolean }[];
  screens: AddonScreen[];
  /** The plugins this keel loaded, with their web part's urls (absolute) when they have one. */
  plugins: PluginInfo[];
};
/** A plugin's web part: its ES module and its stylesheets, served by the api under /plugins/<name>/<version>/web/. */
export type PluginWeb = { entry: string; css: string[] };
export type PluginInfo = { name: string; title: string; version: string; web: PluginWeb | null };

export const api = {
  health: () => get<Health>("/health"),
  /** keel 0.18.64 sign this browser out (its session ends), or every browser (each one's session ends) */
  webSignOut: (everywhere = false) => post<{ ok: boolean; ended?: number }>(everywhere ? "/web/sign-out-everywhere" : "/web/sign-out"),
  /** keel 0.18.22 what keel installs for the projects' stacks (setup.apt), and how far it is */
  stackSetup: () => get<StackSetup>("/setup"),
  features: () => get<Features>("/features"),
  /** `pid` "*" = every project (notifications + project.changed only). */
  /** keel 0.18.6 the project and KeelBot chat every keel front end shows (api ui/UiFocus.kt) */
  focus: () => get<{ project: string | null; chats: Record<string, string>; by?: string | null; client?: string | null }>("/ui/focus"),
  setFocus: (body: { project?: string; chat?: string; chat_project?: string; close?: boolean; by: string; client: string }) =>
    put<{ project: string | null; chats: Record<string, string> }>("/ui/focus", body),
  eventsUrl: (pid: string | null, notifyAll = false) => apiUrl("/events") + q({ project: pid ?? (notifyAll ? "*" : null), notify: notifyAll && pid ? "all" : null }),
  /** The raw answer; `normalizeCatalog` (ModelPicker) also accepts the pre-v0.3 `{provider: [{id,label}]}` shape. */
  providerModels: () => get<unknown>("/providers/models"),

  // projects
  projects: () => get<Project[]>("/projects"),
  addProject: (root: string, name?: string) => post<Project>("/projects", { root, name }),
  /** v0.15.7 the folders keel may look in, and the git repos inside them */
  folders: () => get<Folders>("/folders"),
  /** v0.15.7 one folder's sub folders (403 outside the folders keel may look in) */
  browseFolder: (path?: string) => get<FolderWalk>("/folders/browse" + q({ path })),
  /** v0.15.7 adds many repos at once; one that is a project already comes back in `skipped` with why */
  addProjects: (roots: string[], names?: Record<string, string>) => post<BulkAdded>("/projects/bulk", { roots, ...(names ? { names } : {}) }),
  project: (pid: string) => get<Project>(`/projects/${e(pid)}`),

  // flow
  flow: (pid: string) => get<FlowView>(`/projects/${e(pid)}/flow`),
  flowOf: (pid: string, tid: string) => get<FlowView>(`/projects/${e(pid)}/flows/${e(tid)}`),
  flowBoard: (pid: string) => get<FlowBoard>(`/projects/${e(pid)}/flows`),
  /** v0.15.4 delete a flow from the run history (only a mark: its checkpoints, branch and calls stay) */
  deleteRun: (pid: string, tid: string) => del<{ ok: boolean; thread_id: string }>(`/projects/${e(pid)}/flows/${e(tid)}`),
  quality: () => get<QualityView>("/quality"),
  qualityStart: (body: { flows: string[]; models: Model[]; sets?: string[] }) => post<QualityRun>("/quality/runs", body),
  qualityStop: (id: string) => post<QualityRun>(`/quality/runs/${e(id)}/stop`),
  qualitySchedule: (body: QualitySchedule) => put<QualitySchedule>("/quality/schedule", body),
  removeWorktree: (tid: string) => post<{ ok: boolean; worktree: string }>(`/threads/${e(tid)}/worktree/remove`),
  startLogin: (provider: string) => post<LoginView>("/logins", { provider }),
  login: (id: string) => get<LoginView>(`/logins/${e(id)}`),
  loginCode: (id: string, code: string) => post<LoginView>(`/logins/${e(id)}/code`, { code }),
  cancelLogin: (id: string) => del<LoginView>(`/logins/${e(id)}`),
  doctorWorkspace: (pid: string) => post<Diagnosis>(`/projects/${e(pid)}/doctor/workspace`),
  applyDoctor: (pid: string, plan: { action: DoctorAction; files: string[]; message?: string | null; patterns?: string[] | null; title?: string }[]) =>
    post<DoctorApplied>(`/projects/${e(pid)}/doctor/workspace/apply`, { plan }),
  folderActivity: (pid: string) => get<FolderActivity>(`/projects/${e(pid)}/folder-activity`),
  startFlow: (pid: string, body: {
    workflow_id: string; title: string; acs?: { id: string; layer: "API" | "WEB"; title: string }[]; cap_tokens?: number; on_cap?: OnCap;
    allow_fake?: boolean; allow_dirty?: boolean; request?: string; options?: Record<string, unknown>; run_mode?: RunMode; where?: FlowWhere;
    /** B8 continue: stay on the branch the project folder is on (default: a new branch from the base branch) */
    branch?: "new" | "continue";
    /** 0.18.57 start although another flow or side agent works on the same ticket, branch or files */
    force?: boolean;
  }) =>
    post<ThreadState>(`/projects/${e(pid)}/flows`, body),
  startCheck: (pid: string) => get<StartCheck>(`/projects/${e(pid)}/start-check`),
  /** 0.18.57 "Ignore this path": an untracked path into .git/info/exclude (this computer only) */
  excludePath: (pid: string, path: string) => post<DoctorApplied>(`/projects/${e(pid)}/doctor/workspace/exclude`, { path }),
  resume: (tid: string, decision: "approve" | "reject", why?: string, payload?: Record<string, unknown>) =>
    post<ThreadState>(`/threads/${e(tid)}/resume`, { decision, ...(why ? { why } : {}), ...(payload ? { payload } : {}) }),
  stopThread: (tid: string) => post<ThreadState>(`/threads/${e(tid)}/stop`),
  history: (tid: string) => get<Checkpoint[]>(`/threads/${e(tid)}/history`),
  stepRuns: (pid: string, tid: string) => get<StepRunFacts[]>(`/projects/${e(pid)}/flows/${e(tid)}/step-runs`),
  /** 0.18.64 the AC gate's code: the criterion's RED and GREEN commits with each file's diff */
  acCode: (pid: string, tid: string, ac: string) => get<AcCode>(`/projects/${e(pid)}/flows/${e(tid)}/ac-code?ac=${e(ac)}`),
  /** 0.18.64 one file of those commits with its diff, also a folded one (the gate's Show) */
  acCodeFile: (pid: string, tid: string, ac: string, sha: string, path: string) =>
    get<{ ac: string; sha: string; file: AcCodeFile | null }>(`/projects/${e(pid)}/flows/${e(tid)}/ac-code?ac=${e(ac)}&sha=${e(sha)}&path=${e(path)}`),
  /** keel 0.18.54 the flow's gate decisions (who answered, the answers) and what the Supervisor said, oldest first: the
   *  stored gate.decided / supervisor.decided / .escalated / .paused events */
  flowDecisions: (pid: string, tid: string) => get<EngineEvent[]>(`/projects/${e(pid)}/flows/${e(tid)}/decisions`),
  rewind: (tid: string, checkpoint_id: string) => post<ThreadState>(`/threads/${e(tid)}/rewind`, { checkpoint_id }),
  estimate: (pid: string, workflow_id: string, acs: number) =>
    get<Estimate>(`/projects/${e(pid)}/estimate${q({ workflow_id, acs })}`),
  /** Estimate workflow YAML that is not saved yet. */
  estimateYaml: (pid: string, yaml: string, acs: number) => post<Estimate>(`/projects/${e(pid)}/estimate`, { yaml, acs }),

  // jobs
  // v0.15.2 status "finished" = every call that does not run any more; q = search words (agent, model, provider, project, step, AC, status)
  jobs: (f: { project?: string; status?: string; agent?: string; provider?: string; q?: string; limit?: number } = {}) =>
    get<Job[]>(`/jobs${q({ limit: 50, ...f })}`),
  /** v0.15.2 how many calls match the same filters as jobs(), without its limit (the Finished tab's count). */
  jobCount: (f: { project?: string; status?: string; agent?: string; provider?: string; q?: string } = {}) =>
    get<{ count: number }>(`/jobs/count${q(f)}`),
  job: (id: string) => get<JobDetail>(`/jobs/${e(id)}`),
  jobSteps: (id: string, after = 0) => get<{ steps: JobStep[]; running: boolean }>(`/jobs/${e(id)}/steps${q({ after })}`),
  stopJob: (id: string) => post<void>(`/jobs/${e(id)}/stop`),

  // repo: the Code plugin's endpoints (its own calls are in plugins/code/web/codeApi.ts); keel's core reads a file
  // for the launcher's preview and the file list for KeelBot's @ mentions
  raw: (pid: string, path: string) => getText(`/projects/${e(pid)}/repo/raw${q({ path })}`),
  repoFiles: (pid: string) => get<{ files: string[]; truncated: boolean }>(`/projects/${e(pid)}/repo/files`),
  index: (pid: string) => get<IndexStatus>(`/projects/${e(pid)}/index`),
  rebuildIndex: (pid: string) => post<IndexStatus>(`/projects/${e(pid)}/index/rebuild`),
  unlock: (pid: string, path: string, phase?: string) =>
    post<{ unlocks: Unlock[] }>(`/projects/${e(pid)}/unlock`, phase ? { path, phase } : { path }),
  keelDocs: (pid: string) => get<KeelDoc[]>(`/projects/${e(pid)}/keel-docs`),
  memory: (pid: string) => get<Memory>(`/projects/${e(pid)}/memory`),
  addFact: (pid: string, f: { title: string; text: string; kind: FactKind }) => post<Fact>(`/projects/${e(pid)}/memory`, f),
  editFact: (pid: string, fid: string, f: { title: string; text: string; kind: FactKind }) =>
    put<Fact>(`/projects/${e(pid)}/memory/${e(fid)}`, f),
  forgetFact: (pid: string, fid: string) => del(`/projects/${e(pid)}/memory/${e(fid)}`),

  // "Refresh stale" (the Wiki and the Code page): the Wiki plugin's api starts the knowledge refresh (plugins/wiki)
  refreshWiki: (pid: string, sections?: string[]) =>
    post<ThreadState>(`/projects/${e(pid)}/wiki/refresh`, sections?.length ? { sections } : {}),

  // workflows
  workflows: (pid: string) => get<Workflow[]>(`/projects/${e(pid)}/workflows`),
  /** 0.17.4 what New workflow's "a small example" makes: the engine's starter with the id it would get */
  workflowStarter: (pid: string, name: string, keelRules: boolean) =>
    get<{ id: string; yaml: string }>(`/projects/${e(pid)}/workflows/starter?name=${e(name)}&keel_rules=${keelRules}`),
  newWorkflow: (pid: string, body: { name: string; from: string; keel_rules: boolean }) =>
    post<Workflow>(`/projects/${e(pid)}/workflows`, body),
  workflow: (wid: string) => get<Workflow>(`/workflows/${e(wid)}`),
  saveWorkflow: (w: Workflow) => put<Workflow>(`/workflows/${e(w.id)}`, w),
  deleteWorkflow: (wid: string) => del(`/workflows/${e(wid)}`),
  exportUrl: (wid: string) => `/api/workflows/${e(wid)}/export`,
  exportWorkflow: (wid: string) => getText(`/workflows/${e(wid)}/export`),
  importWorkflow: (pid: string, body: { yaml: string; folder?: string } | { url: string }) =>
    post<{ workflow: Workflow; review: InstallReview }>(`/projects/${e(pid)}/workflows/import`, body),
  checkWorkflow: (pid: string, yaml: string) => post<WorkflowCheck>(`/projects/${e(pid)}/workflows/check`, { yaml }),
  setWorkflowFolder: (pid: string, wid: string, folder: string) =>
    put<{ folder: string | null }>(`/projects/${e(pid)}/workflows/${e(wid)}/folder`, { folder }),
  runs: (pid: string, workflow?: string, limit = 20) => get<RunRow[]>(`/projects/${e(pid)}/runs${q({ workflow, limit })}`),
  library: () => get<LibraryItem[]>("/library"),
  install: (pid: string, id: string, scope: "project" | "all") =>
    post<Workflow>(`/projects/${e(pid)}/library/${e(id)}/install`, { scope }),
  /** What one step really does: rules, routes, the real prompt or its actions in words, and (with a thread) its last runs. */
  explainStep: (pid: string, body: ExplainRequest) => post<StepExplanation>(`/projects/${e(pid)}/workflows/explain-step`, body),

  // agents, skills, stacks, tools
  agents: (pid: string) => get<Agent[]>(`/projects/${e(pid)}/agents`),
  saveAgent: (pid: string, aid: string,
    body: Partial<Pick<Agent, "model" | "tools" | "skills" | "prompt" | "enabled" | "lane">> & { knowledge?: AgentKnowledge | null }) =>
    put<Agent>(`/projects/${e(pid)}/agents/${e(aid)}`, body),
  newAgent: (pid: string, a: CustomAgent) => post<Agent>(`/projects/${e(pid)}/agents`, a),
  deleteAgent: (pid: string, aid: string) => del(`/projects/${e(pid)}/agents/${e(aid)}`),
  testAgent: (aid: string, pid: string) => post<TestResult>(`/agents/${e(aid)}/test`, { pid }),
  skills: (pid: string) => get<Skill[]>(`/projects/${e(pid)}/skills`),
  skill: (sid: string, pid?: string) => get<SkillDetail>(`/skills/${e(sid)}${pid ? `?project=${e(pid)}` : ""}`),
  repoSkills: (pid: string) => get<RepoSkills>(`/projects/${e(pid)}/skills/repo`),
  repoSkillsOn: (pid: string, on: boolean) => put<RepoSkills>(`/projects/${e(pid)}/skills/repo`, { on }),
  newSkill: (pid: string, s: { name: string; kind: string; stack: string; body: string }) => post<Skill>(`/projects/${e(pid)}/skills`, s),
  saveSkill: (pid: string, sid: string, s: { agents?: string[]; when?: string; body?: string }) =>
    put<Skill>(`/projects/${e(pid)}/skills/${e(sid)}`, s),
  importSkill: (pid: string, body: { url: string } | { body: string }) => post<Skill>(`/projects/${e(pid)}/skills/import`, body),
  stacks: (pid: string) => get<unknown[]>(`/projects/${e(pid)}/stacks`).then((l) => l.map(normalizeStack)),
  /** keel 0.18.28 the highlight.js languages of the project's stacks (their `languages:`, else their detect.extensions) */
  languages: (pid: string) => get<{ languages: StackLanguage[] }>(`/projects/${e(pid)}/languages`),
  newStack: (pid: string, name: string, from: string) => post<unknown>(`/projects/${e(pid)}/stacks`, { name, from }).then(normalizeStack),
  installStack: (pid: string, name: string) => post<unknown>(`/projects/${e(pid)}/stacks/${e(name)}/install`).then(normalizeStack),
  // keel 0.18.9 a stack written with keel's help, checked, saved, turned off for the project, removed
  stackYaml: (pid: string, name: string) => get<{ yaml: string }>(`/projects/${e(pid)}/stacks/${e(name)}`),
  stackSuggest: (pid: string, lane?: string) => get<StackSuggestion>(`/projects/${e(pid)}/stacks/suggest${lane ? `?lane=${e(lane)}` : ""}`),
  checkStack: (pid: string, body: { yaml?: string; form?: StackForm; replace?: boolean }) => post<StackCheck>(`/projects/${e(pid)}/stacks/check`, body),
  saveStack: (pid: string, yaml: string, replace = false) =>
    post<{ stack: unknown; path: string; replaced: boolean; warnings: string[] }>(`/projects/${e(pid)}/stacks/save`, { yaml, replace })
      .then((r) => ({ ...r, stack: normalizeStack(r.stack) })),
  removeStack: (pid: string, name: string) => del<{ name: string; removed: string; back: string | null }>(`/projects/${e(pid)}/stacks/${e(name)}`),
  stackOff: (pid: string, name: string, off: boolean) => put<{ name: string; off: boolean; all: string[] }>(`/projects/${e(pid)}/stacks/${e(name)}/off`, { off }),
  mcpServers: () => get<McpServer[]>("/mcp-servers"),
  addMcpServer: (s: McpServerSpec) => post<McpServer>("/mcp-servers", s),
  saveMcpServer: (name: string, s: Partial<McpServer>) => put<McpServer>(`/mcp-servers/${e(name)}`, s),
  deleteMcpServer: (name: string) => del(`/mcp-servers/${e(name)}`),
  testMcpServer: (name: string) => post<{ ok: boolean; tools: unknown[]; error?: string }>(`/mcp-servers/${e(name)}/test`),
  mcpAllow: (pid: string) => get<McpAllow>(`/projects/${e(pid)}/mcp-allow`),
  saveMcpAllow: (pid: string, a: McpAllow) => put<McpAllow>(`/projects/${e(pid)}/mcp-allow`, a),

  // control
  budget: (pid: string) => get<Budget>(`/projects/${e(pid)}/budget`),
  // KeelBot (plugins/keelbot, its own calls: keelbotApi.ts): the launcher's read-only question (⌘K, Ask)
  helperCreate: (pid: string, body: { mode?: HelperMode; model?: Model; title?: string } = {}) =>
    post<HelperSession>(`/projects/${e(pid)}/helper/sessions`, body),
  helperSession: (pid: string, sid: string) => get<HelperSession>(`/projects/${e(pid)}/helper/sessions/${e(sid)}`),
  helperTurn: (pid: string, sid: string, body: { text: string; model?: Model; mentions?: HelperMention[]; selection?: HelperSelection; open_file?: string }) =>
    post<HelperTurnStarted>(`/projects/${e(pid)}/helper/sessions/${e(sid)}/turn`, body),
  // the Graph plugin's search (plugins/graph), over http: the launcher, KeelBot's @ and the Code page use it
  graphSearch: (pid: string, q: string) => get<{ available: boolean; reason?: string; results: GraphHit[] }>(`/projects/${e(pid)}/graph/search?q=${e(q)}`),
  budgetNow: (pid: string) => get<BudgetNow>(`/projects/${e(pid)}/budget/now`),
  caps: (pid: string) => get<Cap[]>(`/projects/${e(pid)}/caps`),
  capsLeft: (pid: string) => get<CapsLeft>(`/projects/${e(pid)}/caps/left`),
  addCap: (pid: string, c: Omit<Cap, "id"> & { id?: string }) => post<Cap>(`/projects/${e(pid)}/caps`, c),
  saveCap: (pid: string, c: Cap) => put<Cap>(`/projects/${e(pid)}/caps/${e(c.id)}`, c),
  deleteCap: (pid: string, id: string) => del(`/projects/${e(pid)}/caps/${e(id)}`),
  limits: () => get<Limit[]>("/limits"),
  usageProviders: () => get<ProviderUsage[]>("/usage/providers"),
  refreshUsage: (id: string) => post<ProviderUsage>(`/usage/providers/${e(id)}/refresh`),
  saveLimits: (l: Limit[]) => put<Limit[]>("/limits", l),
  generalSettings: () => get<Settings>("/settings/general"),
  saveGeneralSettings: (s: Partial<Settings>) => put<Settings>("/settings/general", s),
  projectSettings: (pid: string) => get<ProjectSettings>(`/projects/${e(pid)}/settings`),
  saveProjectSettings: (pid: string, s: Partial<Record<keyof Settings, unknown>>) =>
    put<ProjectSettings>(`/projects/${e(pid)}/settings`, s),
  connections: () => get<Connections>("/connections"),
  setMode: (provider: string, mode: Mode) => put<void>(`/connections/${e(provider)}`, { mode }),
  setSecret: (name: string, value: string) => put<{ hint: string }>(`/secrets/${e(name)}`, { value }),
  // 0.18.46 local model servers and Vertex AI
  localModels: () => get<LocalModels>("/providers/local"),
  setLocalModels: (enabled: boolean) => put<LocalModels>("/providers/local", { enabled }),
  saveLocalEndpoint: (name: string, body: LocalEndpointBody) => put<LocalModels>(`/providers/local/endpoints/${e(name)}`, body),
  deleteLocalEndpoint: (name: string) => del<LocalModels>(`/providers/local/endpoints/${e(name)}`),
  testLocalEndpoint: (name: string) => post<LocalTest>(`/providers/local/endpoints/${e(name)}/test`),
  vertex: () => get<VertexView>("/providers/vertex"),
  saveVertex: (body: VertexBody) => put<VertexView>("/providers/vertex", body),
  deleteSecret: (name: string) => del(`/secrets/${e(name)}`),
  // v0.10.0 plugins: Database and Git
  plugins: (pid: string) => get<Plugin[]>(`/projects/${e(pid)}/plugins`),
  setPlugin: (pid: string, name: string, enabled: boolean, scope: "project" | "all" = "project") =>
    put<Plugin[]>(`/projects/${e(pid)}/plugins/${e(name)}`, { enabled, scope }),
  github: () => get<GitHubState>("/github"),
  // v0.18.30 Sign in with GitHub: the device code and link, polled; sign out removes keel's token
  githubLogin: (host: string) => post<GitHubLoginView>("/github/login", { host }),
  githubLoginState: (id: string) => get<GitHubLoginView>(`/github/login/${e(id)}`),
  githubLoginCancel: (id: string) => del<GitHubLoginView>(`/github/login/${e(id)}`),
  githubSignOut: () => del<{ ok: boolean }>("/github"),
  testConnection: (provider: string) => post<TestResult>(`/connections/${e(provider)}/test`),

  // notifications
  notifications: (limit = 50) => get<Notification[]>(`/notifications${q({ limit })}`),
  readAll: () => post<void>("/notifications/read-all"),
  readOne: (id: string) => post<void>(`/notifications/${e(id)}/read`),
  notificationSettings: () => get<NotificationSettings>("/notification-settings"),
  saveNotificationSettings: (s: NotificationSettings) => put<NotificationSettings>("/notification-settings", s),
};

/** The message and hint of any thrown value, for error states. */
export function errorParts(err: unknown): { message: string; hint?: string; details?: string[] } {
  if (err instanceof ApiError) return { message: err.message, hint: err.hint, details: err.details };
  if (err instanceof Error) return { message: err.message };
  return { message: String(err) };
}
