// Typed client for the keel v2 api (docs/CONTRACT.md, "The api"). Every path is relative, so the same
// build works behind the Spring Boot api (which serves web/dist) and behind the Vite dev proxy.

// ---------- shared types (copied from CONTRACT.md) ----------

export type Provider = "fake" | "claude" | "codex" | "copilot";
export type Mode = "subscription" | "api" | "opencode";
export type Model = { provider: Provider; mode: Mode; model: string; effort?: string };

export type StepKind = "agent" | "code" | "gate" | "branch" | "parallel";
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
};
export type Workflow = {
  id: string;
  name: string;
  based_on?: string;
  keel_rules: boolean;
  version: number;
  steps: Step[];
  yaml: string;
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
  waiting?: { step: string; kind: "gate" | "budget" | "fix" | "clarify"; title: string; detail: string; options: ("approve" | "reject")[]; labels?: { approve?: string; reject?: string }; questions?: ClarifyQuestion[] };
  usage: { tokens_in: number; tokens_out: number; tokens_cached?: number; cost_usd: number; premium_requests: number; cap_tokens: number };
  checkpoints: number;
  error?: string;
  updated_at: string;
  /** v0.2: what still blocks shipping (computed by push_check, refreshed after every commit). */
  blockers?: Blocker[];
  /** v0.2: the init flow's setup ladder. */
  ladder?: LadderRung[];
};
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
  | "gate.waiting" | "gate.decided" | "budget.warn" | "budget.stop" | "guard.refused" | "thread.done" | "thread.failed";
export const ENGINE_EVENT_TYPES: EngineEventType[] = [
  "thread.started", "step.started", "step.finished", "agent.started", "agent.step", "agent.finished",
  "gate.waiting", "gate.decided", "budget.warn", "budget.stop", "guard.refused", "thread.done", "thread.failed",
];
export type EngineEvent = {
  type: EngineEventType;
  thread_id: string;
  project_id: string;
  step?: string;
  at: string;
  call_id?: string;
  data: Record<string, unknown>;
};

// ---------- api types ----------

export type Health = { ok: boolean; engine: boolean; keel: { version: string; home: string }; fake: boolean };

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
};

export type FlowView = { thread: ThreadState | null; workflow: Workflow | null; keel_state: Record<string, unknown> | null };

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
  ac?: string;
  head: string;
  last_commit: string;
};
export type Commit = { sha: string; message: string; author: string; at: string };
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
export type KeelMap = {
  sha: string;
  at: string;
  demo?: boolean;
  limits?: string[];
  counts?: Record<string, number>;
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
export type AgentKnowledge = { sections: KnowledgeSection[]; code_graph: boolean; memory: boolean; strict: boolean };
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
  tools: { name: string; on: string; fail: string }[];
  skills: string[];
  /** v0.2: a keel pack that `keel packs add` can install into this project. */
  installable?: boolean;
};
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
    tools: list(r.tools).map((t) => ({ name: text((t as Record<string, unknown>)?.name), on: text((t as Record<string, unknown>)?.on),
      fail: text((t as Record<string, unknown>)?.fail) })),
    skills, installable: !!r.installable,
  };
}

export type McpServer = McpServerSpec & { enabled: boolean; builtin: boolean; status: "ok" | "off" | "error"; tools: string[]; label?: string };
export type McpAllow = Record<string, string[]>;

export type CapScope = "day" | "flow" | "step" | "api_month";
export type Cap = { id: string; scope: CapScope; limit: number; unit: "tokens" | "usd"; action: "pause" | "cheaper" | "stop" };
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
export type Limit = { id: string; name: string; unit: string; used: number; cap: number; note: string };

export type GatesMode = "every-ac" | "end-of-lane" | "end";
export type OnCap = "pause" | "cheaper" | "stop";
export type Settings = {
  gates_mode: GatesMode;
  keel_rules: boolean;
  fix_attempts: number;
  coverage_min: number;
  default_model: Model;
  implementer_model: Model;
  reviewer_model: Model;
  cheaper_model: Model;
  cap_tokens: number;
  on_cap: OnCap;
  branch_pattern: string;
  web_lane_worktree: boolean;
  push_pr: string;
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
export const del = <T = void>(path: string) => request<T>("DELETE", path);
export const getText = (path: string) => request<string>("GET", path, undefined, { text: true });

// ---- login helper ----
export type LoginView = { id: string; provider: string; status: "starting" | "waiting" | "code_needed" | "done" | "failed" | "cancelled";
  url?: string | null; code?: string | null; message: string; hint?: string | null };

// ---- workspace Doctor ----
export type DoctorAction = "commit" | "stash" | "ignore" | "keep";
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

export const api = {
  health: () => get<Health>("/health"),
  /** `pid` "*" = every project (notifications + project.changed only). */
  eventsUrl: (pid: string | null, notifyAll = false) => "/api/events" + q({ project: pid ?? (notifyAll ? "*" : null), notify: notifyAll && pid ? "all" : null }),
  keelDashboard: () => get<{ url: string }>("/keel-dashboard"),
  /** The raw answer; `normalizeCatalog` (ModelPicker) also accepts the pre-v0.3 `{provider: [{id,label}]}` shape. */
  providerModels: () => get<unknown>("/providers/models"),

  // projects
  projects: () => get<Project[]>("/projects"),
  addProject: (root: string, name?: string) => post<Project>("/projects", { root, name }),
  project: (pid: string) => get<Project>(`/projects/${e(pid)}`),

  // flow
  flow: (pid: string) => get<FlowView>(`/projects/${e(pid)}/flow`),
  startLogin: (provider: string) => post<LoginView>("/logins", { provider }),
  login: (id: string) => get<LoginView>(`/logins/${e(id)}`),
  loginCode: (id: string, code: string) => post<LoginView>(`/logins/${e(id)}/code`, { code }),
  cancelLogin: (id: string) => del<LoginView>(`/logins/${e(id)}`),
  doctorWorkspace: (pid: string) => post<Diagnosis>(`/projects/${e(pid)}/doctor/workspace`),
  applyDoctor: (pid: string, plan: { action: DoctorAction; files: string[]; message?: string | null; patterns?: string[] | null; title?: string }[]) =>
    post<DoctorApplied>(`/projects/${e(pid)}/doctor/workspace/apply`, { plan }),
  startFlow: (pid: string, body: {
    workflow_id: string; title: string; acs?: { id: string; layer: "API" | "WEB"; title: string }[]; cap_tokens?: number; on_cap?: OnCap;
    allow_fake?: boolean; allow_dirty?: boolean; request?: string;
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
  tree: (pid: string, depth = 4) => get<TreeNode[]>(`/projects/${e(pid)}/repo/tree${q({ depth })}`),
  file: (pid: string, path: string) => get<RepoFile>(`/projects/${e(pid)}/repo/file${q({ path })}`),
  commits: (pid: string, limit = 30) => get<Commit[]>(`/projects/${e(pid)}/repo/commits${q({ limit })}`),
  updateFromBase: (pid: string) => post<UpdateFromBase>(`/projects/${e(pid)}/repo/update-from-base`),
  fileHistory: (pid: string, path: string) => get<Commit[]>(`/projects/${e(pid)}/repo/history${q({ path })}`),
  unlock: (pid: string, path: string, phase?: string) =>
    post<{ unlocks: Unlock[] }>(`/projects/${e(pid)}/unlock`, phase ? { path, phase } : { path }),
  keelDocs: (pid: string) => get<KeelDoc[]>(`/projects/${e(pid)}/keel-docs`),
  memory: (pid: string) => get<Memory>(`/projects/${e(pid)}/memory`),
  addFact: (pid: string, f: { title: string; text: string; kind: FactKind }) => post<Fact>(`/projects/${e(pid)}/memory`, f),
  editFact: (pid: string, fid: string, f: { title: string; text: string; kind: FactKind }) =>
    put<Fact>(`/projects/${e(pid)}/memory/${e(fid)}`, f),
  forgetFact: (pid: string, fid: string) => del(`/projects/${e(pid)}/memory/${e(fid)}`),

  // map + wiki
  map: (pid: string) => get<MapResponse>(`/projects/${e(pid)}/map`),
  rebuildMap: (pid: string) => post<MapResponse>(`/projects/${e(pid)}/map/rebuild`),
  wiki: (pid: string) => get<WikiTree>(`/projects/${e(pid)}/wiki`),
  wikiPage: (pid: string, id: string) => get<WikiPage>(`/projects/${e(pid)}/wiki/page${q({ id })}`),
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
  importWorkflow: (pid: string, body: { yaml: string } | { url: string }) =>
    post<{ workflow: Workflow; review: InstallReview }>(`/projects/${e(pid)}/workflows/import`, body),
  library: () => get<LibraryItem[]>("/library"),
  install: (pid: string, id: string, scope: "project" | "all") =>
    post<Workflow>(`/projects/${e(pid)}/library/${e(id)}/install`, { scope }),

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
  caps: (pid: string) => get<Cap[]>(`/projects/${e(pid)}/caps`),
  addCap: (pid: string, c: Omit<Cap, "id"> & { id?: string }) => post<Cap>(`/projects/${e(pid)}/caps`, c),
  saveCap: (pid: string, c: Cap) => put<Cap>(`/projects/${e(pid)}/caps/${e(c.id)}`, c),
  deleteCap: (pid: string, id: string) => del(`/projects/${e(pid)}/caps/${e(id)}`),
  limits: () => get<Limit[]>("/limits"),
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
