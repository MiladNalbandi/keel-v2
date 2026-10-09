// Typed client for the keel v2 api (docs/CONTRACT.md, "The api"). Every path is relative, so the same
// build works behind the Spring Boot api (which serves web/dist) and behind the Vite dev proxy.

// ---------- shared types (copied from CONTRACT.md) ----------

export type Provider = "fake" | "claude" | "codex" | "copilot";
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
};

/** v0.9.0: one flow of a project's history (GET /api/projects/{pid}/runs). */
export type RunRow = {
  thread_id: string; title: string; workflow_id: string | null; status: string; phase: string | null; current: string | null;
  waiting: string | null; acs_done: number; acs_total: number; tokens: number; where: "folder" | "worktree"; branch: string | null;
  error: string | null; created_at: string; updated_at: string;
};
/** v0.9.0: keel's check of a workflow YAML that is not saved yet (KeelBot's new workflow). */
export type WorkflowCheck = {
  name: string; steps: number; gates: number; keel_rules: boolean; agents: string[]; mcp: string[]; tools: string[];
  commands: string[]; edits_files: boolean; valid: boolean; errors: string[]; warnings: string[];
};

/** v0.3: "already-met" = an earlier criterion's code already covers it (shown like done, own label). */
export type AcStatus = "todo" | "red" | "green" | "done" | "already-met";
export type ThreadStatus = "running" | "waiting" | "done" | "failed" | "stopped";
/** One question of the explorer's clarify loop: 2-4 options, the recommended one first. */
export type ClarifyQuestion = {
  id: string;
  question: string;
  why?: string;
  options: { label: string; description?: string; recommended?: boolean }[];
};

export type ThreadState = {
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
  | "gate.waiting" | "gate.decided" | "budget.warn" | "budget.stop" | "guard.refused" | "thread.done" | "thread.failed"
  | "helper.started" | "helper.step" | "helper.finished" | "helper.permission" | "helper.permission.answered" | "helper.commit";
export const ENGINE_EVENT_TYPES: EngineEventType[] = [
  "thread.started", "step.started", "step.finished", "agent.started", "agent.step", "agent.finished",
  "gate.waiting", "gate.decided", "budget.warn", "budget.stop", "guard.refused", "thread.done", "thread.failed",
  "helper.started", "helper.step", "helper.finished", "helper.permission", "helper.permission.answered", "helper.commit",
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
export type Health = { ok: boolean; engine: boolean; version: string; fake: boolean };

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
};

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

/** The engine's code graph index of a project (engine runtime/scan.py). */
export type IndexStatus = {
  project: string;
  status: "idle" | "indexing" | "ready" | "failed";
  files: number;
  symbols: number;
  indexed_at?: string | null;
  error?: string | null;
  available?: boolean;
};

export type RepoInfo = {
  branch: string;
  base: string;
  ahead: number;
  behind: number;
  remote: string;
  worktrees: { branch: string; path: string }[];
  branches: { name: string; note: string }[];
};
export type TreeNode = {
  path: string;
  name: string;
  depth: number;
  kind: "dir" | "file";
  mark?: "A" | "M" | "D";
  keel: boolean;
  frozen: boolean;
  ac?: string;
};
export type RepoFile = {
  path: string;
  size: number;
  mark?: "A" | "M" | "D";
  frozen: boolean;
  keel: boolean;
  ac?: string | null;
  head: string;
  last_commit?: Commit | null;
  /** v0.5.1: a NUL byte in the first 8 KB; mtime; the keel rule that applies in the active phase. */
  binary?: boolean;
  modified?: number;
  phase?: string;
  bucket?: string;
  verdict?: string;
};
export type Commit = { sha: string; message: string; author: string; at: string; keel?: boolean };
/** v0.5.1 Repo IDE: git status, diffs, a commit's files, search (git grep), the quick-open file list. */
export type Change = { path: string; from?: string; staged?: string; unstaged?: string; untracked?: boolean; conflict?: boolean };
export type FileDiff = { path: string; against: string; ref: string; diff: string; binary: boolean; truncated: boolean };
export type CommitView = Commit & { body: string; keel: boolean; files: { path: string; status: string; from?: string }[] };
/** v0.12.0 one local branch against the base (Git plugin): its own commits and the files it changed since it left the base. */
export type BranchView = {
  name: string; base: string | null; current: boolean; ahead: number; behind: number;
  commits: Commit[]; files: { path: string; status: string; from?: string }[]; truncated?: boolean;
};
export type SearchMatch = { line: number; column: number; length: number; text: string; ranges: [number, number][] };
export type SearchResult = { results: { path: string; matches: SearchMatch[] }[]; matches: number; files: number; truncated: boolean; timed_out: boolean; took_ms: number };
export type SearchQuery = { q: string; regex?: boolean; case?: boolean; word?: boolean; include?: string; exclude?: string; max?: number };
export type UpdateFromBase = { ok: boolean; merged: boolean; conflicts: string[]; output: string };
export type Unlock = { path: string; phase: string };
export type KeelDoc = { path: string; what: string; by: string; updated: string; status: "ok" | "live" | "check" };
export type FactKind = "fact" | "rule" | "flaky" | "unlock";
export type Fact = { id: string; title: string; text: string; kind: FactKind; source: string; at: string };
export type Memory = {
  facts: Fact[];
  knowledge: { id: string; status: "written" | "stale" | "missing"; words: number; cites: number }[];
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

export type WikiTree = { sections: { id: string; title: string; items: { id: string; title: string; status?: string }[] }[] };
export type WikiPage = { id: string; title: string; markdown: string; meta: Record<string, unknown> };

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

export type SkillSource = "keel" | "keel pack" | "claude" | "yours";
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
};
export type SkillDetail = Skill & { body: string; refs: { path: string; tokens: number }[] };

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
  };
}

export type McpServer = McpServerSpec & { enabled: boolean; builtin: boolean; status: "ok" | "off" | "error"; tools: string[]; label?: string };
export type McpAllow = Record<string, string[]>;

/** v0.10.0 a keel plugin (Tools › Plugins): what it adds, and whether it is on for this project (scope: its own choice
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
export type CatalogModel = { id: string; label: string; efforts?: string[] };
export type CatalogSource = "cli" | "cache" | "builtin";
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
};
/** Tokens (cached input counts a tenth) and dollars at API prices. */
export type Spend = { tokens: number; cost_usd: number };
/** A running or waiting flow and what it used against its own caps (null = no cap). */
export type FlowSpend = {
  thread_id: string; title: string; status: ThreadStatus; tokens: number; cost_usd: number; cap_tokens: number | null; cap_usd: number | null;
};
/** v0.6.0 KeelBot (`/api/projects/{pid}/helper/...`): chat sessions in the Code page, run by keel's own harness. */
export type HelperMode = "ask" | "fix" | "side";
/** A side session's hand-over: its branch, the commits kept on it, what is not kept yet, and its last answer. */
export type HelperHandover = { session: string; title: string; branch: string; base: string; worktree: string;
  commits: { sha: string; subject: string }[]; uncommitted: string[]; asked: string[]; answer: string };
/** Fix mode: a file KeelBot changed in this chat, against what it was before its first change. */
export type HelperChange = { path: string; status: "added" | "modified" | "deleted"; added: number; removed: number; diff: string };
/** A Fix chat's command that waits for the person's OK (a card in the panel and the Inbox). */
export type HelperQuestion = { id: string; session: string; project: string; thread_id?: string | null; kind: string; command: string;
  path?: string; title: string; at: string };
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
  data: { status?: string; provider?: string; model?: string; tokens_in?: number; tokens_out?: number; tokens_cached?: number;
    cost_usd?: number; ms?: number; command?: string; mentions?: HelperMention[]; selection?: HelperSelection; sha?: string; files?: string[] };
  at: string;
};
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
};
export type HelperMention = { kind: "file" | "symbol" | "ac" | string; value: string; file?: string; line?: number };
export type HelperSelection = { path: string; from?: number; to?: number; text: string };
export type HelperCommand = { name: string; description: string; plugin: string; source: "keel" | "project" | string };
export type HelperTurnStarted = { session: string; call_id: string; n: number; command?: string | null };

/** v0.5.3 the code graph (`GET /api/projects/{pid}/graph`): the CodeGraph index rolled up into units and groups. */
export type GraphGroup = { id: string; kind: "package" | "folder"; name: string; label: string; path: string[] };
export type GraphUnit = { id: string; name: string; kind: string; group: string; file: string; line: number; members: number };
/** from uses to: n uses in all, k by kind (calls, instantiates, implements, extends, references). */
export type GraphLink = { from: string; to: string; n: number; k: Record<string, number> };
export type GraphUnavailable = { available: false; status: string; reason: string };
export type GraphOverview = {
  available: true; status?: string; indexed_at?: string | null;
  counts: { files: number; symbols: number; units: number; links: number; uses: number };
  groups: GraphGroup[]; units: GraphUnit[]; links: GraphLink[];
} | GraphUnavailable;
export type GraphHit = { id: string; name: string; kind: string; file: string; line: number; unit: string; group: string };
export type GraphNodeRef = { id: string; name: string; kind: string; unit: string; group: string; file: string; line: number; col: number };
export type GraphEdge = GraphLink & { sites: { file: string; line: number }[] };
export type GraphMember = { id: string; name: string; kind: string; line: number; in: number; out: number };
export type GraphFocus = {
  available: true; level: "unit" | "member"; depth: number; missing?: undefined;
  focus: {
    id: string; name: string; kind: string; qualified: string; signature?: string | null; docstring?: string | null;
    file: string; line: number; end_line?: number | null; group: string; unit: { id: string; name: string; kind: string } | null; members: GraphMember[];
  };
  /** col -2 / -1: who uses it (two steps, one step); 1 / 2: what it uses */
  nodes: GraphNodeRef[]; edges: GraphEdge[]; more: Record<string, number>; impact: number; impact_capped: boolean;
} | { available: true; missing: string } | GraphUnavailable;

/** v0.5.2 `GET /api/projects/{pid}/budget/now`, for the budget bar on every page: caps = the day and month caps. */
export type BudgetNow = { today: Spend; month: Spend; flows: FlowSpend[]; caps: CapLeft[] };
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
  keel_rules: boolean;
  fix_attempts: number;
  coverage_min: number;
  default_model: Model;
  implementer_model: Model;
  reviewer_model: Model;
  cheaper_model: Model;
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
};
export type ProjectSettings = { general: Settings; overrides: Partial<Settings>; effective: Settings };

export type Connections = {
  providers: {
    id: Provider;
    label: string;
    modes: { id: Mode; label: string; ready: boolean; detail: string }[];
    selected: Mode;
    key_set: boolean;
    key_hint?: string;
    login_secret?: string | null;  // CLI login secret name (subscription), e.g. CLAUDE_CODE_OAUTH_TOKEN
    login_set?: boolean;
    login_hint?: string | null;
  }[];
  machine: { name: string; ok: boolean; version?: string }[];
};

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
  constructor(message: string, status: number, hint?: string, details?: string[]) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.hint = hint;
    this.details = details;
  }
}

export const OLD_SERVER_HINT = "The server sent a web page instead of data. Is the keel api running on port 8080?";

async function request<T>(method: string, path: string, body?: unknown, opts: { text?: boolean } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch("/api" + path, {
      method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError("Cannot reach the keel api.", 0, "Start it (docker run … or ./gradlew bootRun) and reload.");
  }
  const type = res.headers.get("content-type") ?? "";
  if (!res.ok) {
    let err: { error?: string; hint?: string; errors?: unknown } = {};
    if (type.includes("json")) err = await res.json().catch(() => ({}));
    const details = Array.isArray(err.errors) ? err.errors.map(String) : undefined;
    throw new ApiError(err.error || `${res.status} ${res.statusText}`.trim(), res.status, err.hint, details);
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
export type LoginView = { id: string; provider: string; status: "starting" | "waiting" | "code_needed" | "done" | "failed" | "cancelled";
  url?: string | null; code?: string | null; message: string; hint?: string | null };

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
  features: () => get<Features>("/features"),
  /** `pid` "*" = every project (notifications + project.changed only). */
  eventsUrl: (pid: string | null, notifyAll = false) => "/api/events" + q({ project: pid ?? (notifyAll ? "*" : null), notify: notifyAll && pid ? "all" : null }),
  /** The raw answer; `normalizeCatalog` (ModelPicker) also accepts the pre-v0.3 `{provider: [{id,label}]}` shape. */
  providerModels: () => get<unknown>("/providers/models"),

  // projects
  projects: () => get<Project[]>("/projects"),
  addProject: (root: string, name?: string) => post<Project>("/projects", { root, name }),
  project: (pid: string) => get<Project>(`/projects/${e(pid)}`),

  // flow
  flow: (pid: string) => get<FlowView>(`/projects/${e(pid)}/flow`),
  flowOf: (pid: string, tid: string) => get<FlowView>(`/projects/${e(pid)}/flows/${e(tid)}`),
  flowBoard: (pid: string) => get<FlowBoard>(`/projects/${e(pid)}/flows`),
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
  startFlow: (pid: string, body: {
    workflow_id: string; title: string; acs?: { id: string; layer: "API" | "WEB"; title: string }[]; cap_tokens?: number; on_cap?: OnCap;
    allow_fake?: boolean; allow_dirty?: boolean; request?: string; options?: Record<string, unknown>; run_mode?: RunMode; where?: FlowWhere;
  }) =>
    post<ThreadState>(`/projects/${e(pid)}/flows`, body),
  resume: (tid: string, decision: "approve" | "reject", why?: string, payload?: Record<string, unknown>) =>
    post<ThreadState>(`/threads/${e(tid)}/resume`, { decision, ...(why ? { why } : {}), ...(payload ? { payload } : {}) }),
  stopThread: (tid: string) => post<ThreadState>(`/threads/${e(tid)}/stop`),
  history: (tid: string) => get<Checkpoint[]>(`/threads/${e(tid)}/history`),
  rewind: (tid: string, checkpoint_id: string) => post<ThreadState>(`/threads/${e(tid)}/rewind`, { checkpoint_id }),
  estimate: (pid: string, workflow_id: string, acs: number) =>
    get<Estimate>(`/projects/${e(pid)}/estimate${q({ workflow_id, acs })}`),
  /** Estimate workflow YAML that is not saved yet. */
  estimateYaml: (pid: string, yaml: string, acs: number) => post<Estimate>(`/projects/${e(pid)}/estimate`, { yaml, acs }),

  // jobs
  jobs: (f: { project?: string; status?: string; agent?: string; provider?: string; limit?: number } = {}) =>
    get<Job[]>(`/jobs${q({ limit: 50, ...f })}`),
  job: (id: string) => get<JobDetail>(`/jobs/${e(id)}`),
  jobSteps: (id: string, after = 0) => get<{ steps: JobStep[]; running: boolean }>(`/jobs/${e(id)}/steps${q({ after })}`),
  stopJob: (id: string) => post<void>(`/jobs/${e(id)}/stop`),

  // repo
  repo: (pid: string) => get<RepoInfo>(`/projects/${e(pid)}/repo`),
  tree: (pid: string, depth = 4, dir?: string) => get<TreeNode[]>(`/projects/${e(pid)}/repo/tree${q({ depth, dir })}`),
  file: (pid: string, path: string) => get<RepoFile>(`/projects/${e(pid)}/repo/file${q({ path })}`),
  commits: (pid: string, limit = 30, range?: "branch") => get<Commit[]>(`/projects/${e(pid)}/repo/commits${q({ limit, range })}`),
  rawUrl: (pid: string, path: string) => `/api/projects/${e(pid)}/repo/raw${q({ path })}`,
  raw: (pid: string, path: string) => getText(`/projects/${e(pid)}/repo/raw${q({ path })}`),
  repoFiles: (pid: string) => get<{ files: string[]; truncated: boolean }>(`/projects/${e(pid)}/repo/files`),
  search: (pid: string, s: SearchQuery) =>
    get<SearchResult>(`/projects/${e(pid)}/repo/search${q({ q: s.q, regex: s.regex ? "true" : null, case: s.case ? "true" : null, word: s.word ? "true" : null, include: s.include, exclude: s.exclude, max: s.max })}`),
  changes: (pid: string) => get<Change[]>(`/projects/${e(pid)}/repo/changes`),
  diff: (pid: string, path: string, against: "head" | "base", sha?: string, branch?: string) =>
    get<FileDiff>(`/projects/${e(pid)}/repo/diff${q({ path, against: sha || branch ? null : against, sha, branch })}`),
  commit: (pid: string, sha: string) => get<CommitView>(`/projects/${e(pid)}/repo/commit${q({ sha })}`),
  updateFromBase: (pid: string) => post<UpdateFromBase>(`/projects/${e(pid)}/repo/update-from-base`),
  index: (pid: string) => get<IndexStatus>(`/projects/${e(pid)}/index`),
  rebuildIndex: (pid: string) => post<IndexStatus>(`/projects/${e(pid)}/index/rebuild`),
  fileHistory: (pid: string, path: string) => get<Commit[]>(`/projects/${e(pid)}/repo/history${q({ path })}`),
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
  skill: (sid: string) => get<SkillDetail>(`/skills/${e(sid)}`),
  newSkill: (pid: string, s: { name: string; kind: string; stack: string; body: string }) => post<Skill>(`/projects/${e(pid)}/skills`, s),
  saveSkill: (pid: string, sid: string, s: { agents?: string[]; when?: string; body?: string }) =>
    put<Skill>(`/projects/${e(pid)}/skills/${e(sid)}`, s),
  importSkill: (pid: string, body: { url: string } | { body: string }) => post<Skill>(`/projects/${e(pid)}/skills/import`, body),
  stacks: (pid: string) => get<unknown[]>(`/projects/${e(pid)}/stacks`).then((l) => l.map(normalizeStack)),
  newStack: (pid: string, name: string, from: string) => post<unknown>(`/projects/${e(pid)}/stacks`, { name, from }).then(normalizeStack),
  installStack: (pid: string, name: string) => post<unknown>(`/projects/${e(pid)}/stacks/${e(name)}/install`).then(normalizeStack),
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
  graph: (pid: string) => get<GraphOverview>(`/projects/${e(pid)}/graph`),
  graphSearch: (pid: string, q: string) => get<{ available: boolean; reason?: string; results: GraphHit[] }>(`/projects/${e(pid)}/graph/search?q=${e(q)}`),
  graphNode: (pid: string, id: string, depth = 1) => get<GraphFocus>(`/projects/${e(pid)}/graph/node?id=${e(id)}&depth=${depth}`),
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
  deleteSecret: (name: string) => del(`/secrets/${e(name)}`),
  // v0.10.0 plugins: Database and Git
  plugins: (pid: string) => get<Plugin[]>(`/projects/${e(pid)}/plugins`),
  setPlugin: (pid: string, name: string, enabled: boolean, scope: "project" | "all" = "project") =>
    put<Plugin[]>(`/projects/${e(pid)}/plugins/${e(name)}`, { enabled, scope }),
  github: () => get<{ set: boolean; hint: string | null; from: "keel" | "env" | null }>("/github"),
  // the Code page's branch tab (pages/repo/Branch.tsx) reads a branch and switches with the Git plugin's api; the Git
  // plugin's panel and KeelBot buttons have their own calls (plugins/git/web/gitApi.ts)
  gitSwitch: (pid: string, branch: string, create = false) => post<{ branch: string }>(`/projects/${e(pid)}/git/switch`, { branch, create }),
  gitBranch: (pid: string, name: string) => get<BranchView>(`/projects/${e(pid)}/git/branch${q({ name })}`),
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
