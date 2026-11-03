// The keel api's JSON, only the fields the extension reads. keel writes snake_case (Jackson SNAKE_CASE) and the names
// here are kept as they come over the wire: map keys (GitHub thread ids, project ids) must never be renamed, so there
// is no camelCase pass. Unknown fields are ignored, so a newer keel never breaks the extension.

// ---------------------------------------------------------------- pairing (POST /api/devices/pair, GET /api/devices/me)

export interface Device {
  id: string;
  name?: string;
  platform?: string;
  created_at?: string;
}

export interface KeelInfo {
  name?: string;
  version?: string;
}

export interface PairResponse {
  token: string;
  device: Device;
  keel?: KeelInfo;
}

export interface DeviceMe {
  device?: Device;
  keel?: KeelInfo;
  id?: string;
  name?: string;
}

export interface Health {
  ok?: boolean;
  engine?: boolean;
  version?: string;
  fake?: boolean;
}

// ---------------------------------------------------------------- projects

export interface Project {
  id: string;
  name: string;
  root?: string;
  branch?: string;
  flow?: string | null;
  phase?: string;
  waiting?: number;
  running?: number;
}

// ---------------------------------------------------------------- inbox (GET /api/inbox, POST /api/inbox/{tid}/act)

export interface ChoiceInfo {
  name: string;
  label?: string;
  description?: string;
  /** required | optional | none */
  why?: string;
  default?: boolean;
}

export interface QuestionOption {
  label: string;
  description?: string | null;
  recommended?: boolean;
  /** required | optional: picking it takes a reason ("<label>: <reason>") */
  why?: string | null;
}

export interface ClarifyQuestion {
  id: string;
  question: string;
  why?: string | null;
  options?: QuestionOption[];
  multiple?: boolean;
}

export interface Labels {
  approve?: string | null;
  reject?: string | null;
}

export interface InboxPermission {
  id: string;
  session?: string;
  command?: string;
  path?: string | null;
}

export interface InboxItem {
  project_id: string;
  project_name?: string;
  thread_id?: string;
  flow?: string;
  workflow_id?: string | null;
  step?: string | null;
  /** gate | clarify | fix | budget | usage | dependency | permission | task | ... */
  kind: string;
  title?: string;
  detail?: string;
  options?: string[];
  choices?: string[] | null;
  choice_info?: ChoiceInfo[] | null;
  questions?: ClarifyQuestion[] | null;
  labels?: Labels | null;
  id?: string | null;
  phase?: string | null;
  since?: string | null;
  permission?: InboxPermission | null;
}

export interface InboxView {
  items?: InboxItem[];
  count?: number;
}

/** The answer to a waiting question; `id` binds it to the question it answers (a stale screen gets a 409). */
export interface InboxAnswer {
  decision: string;
  id?: string | null;
  why?: string | null;
  payload?: Record<string, unknown> | null;
}

// ---------------------------------------------------------------- flows

export interface Ac {
  id: string;
  layer?: string;
  title?: string;
  status?: string;
}

export interface Waiting {
  step?: string;
  kind?: string;
  title?: string;
  detail?: string;
  options?: string[];
  id?: string | null;
  labels?: Labels | null;
  questions?: ClarifyQuestion[] | null;
  choices?: string[] | null;
  choice_info?: ChoiceInfo[] | null;
  name?: string | null;
}

export interface Usage {
  tokens_in?: number;
  tokens_out?: number;
  tokens_cached?: number;
  cost_usd?: number;
  cap_tokens?: number;
}

export interface ThreadState {
  thread_id: string;
  project_id?: string;
  workflow_id?: string;
  title?: string;
  /** running | waiting | done | failed | stopped */
  status?: string;
  current?: string | null;
  phase?: string;
  ac?: string | null;
  acs?: Ac[];
  waiting?: Waiting | null;
  usage?: Usage;
  error?: string | null;
  updated_at?: string;
  run_mode?: string | null;
}

export interface WorkflowStep {
  id: string;
  kind?: string;
  name?: string;
  agent?: string | null;
  phase?: string | null;
}

export interface Workflow {
  id: string;
  name?: string;
  source?: string | null;
  steps?: WorkflowStep[];
}

export interface FlowView {
  thread?: ThreadState | null;
  workflow?: Workflow | null;
}

/** One flow of a project's history (GET /api/projects/{pid}/runs). */
export interface RunRow {
  thread_id: string;
  title?: string;
  workflow_id?: string | null;
  status?: string;
  phase?: string | null;
  current?: string | null;
  waiting?: string | null;
  acs_done?: number;
  acs_total?: number;
  tokens?: number;
  where?: string;
  branch?: string | null;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
  cost_usd?: number;
}

export interface Checkpoint {
  id: string;
  n?: number;
  step?: string;
  at?: string;
}

export interface StartFlowBody {
  workflow_id: string;
  title: string;
  request?: string | null;
  where?: string;
  run_mode?: string;
  cap_tokens?: number | null;
  /** Flow inputs the workflow may read from its state data (model / effort when picked). */
  options?: Record<string, string> | null;
}

// ---------------------------------------------------------------- the review plugin (/api/projects/{pid}/review/...)

export interface HostRef {
  kind?: string;
  host?: string;
  path?: string;
  web?: string;
}

export interface PrSummary {
  number: number;
  title?: string;
  author?: string;
  branch?: string;
  base?: string;
  draft?: boolean;
  updated_at?: string;
  url?: string;
  review_requested?: boolean;
  mine?: boolean;
  assigned?: boolean;
}

export interface PrList {
  host?: HostRef | null;
  me?: string | null;
  prs?: PrSummary[];
  counts?: Record<string, number>;
  note?: string | null;
}

export interface CheckRun {
  name: string;
  state?: string;
  url?: string | null;
}

export interface ReviewComment {
  id: string;
  author?: string;
  body?: string;
  at?: string;
  url?: string | null;
}

export interface ReviewThread {
  id: string;
  path?: string | null;
  line?: number | null;
  side?: string;
  resolved?: boolean;
  outdated?: boolean;
  comments?: ReviewComment[];
  start_line?: number | null;
}

export interface ChangedFile {
  path: string;
  status?: string;
  /** a renamed file: its old path */
  from?: string | null;
  added?: number;
  removed?: number;
  binary?: boolean;
}

export interface ReviewView {
  key: string;
  kind?: string;
  number?: number | null;
  title?: string;
  author?: string | null;
  body?: string | null;
  base?: string;
  branch?: string;
  url?: string | null;
  state?: string | null;
  draft?: boolean;
  checks?: CheckRun[];
  files?: ChangedFile[];
  added?: number;
  removed?: number;
  threads?: ReviewThread[];
  can_post?: boolean;
  me?: string | null;
  notes?: string[];
}

export interface FileDiff {
  path?: string;
  diff?: string;
  binary?: boolean;
  truncated?: boolean;
}

// ---------------------------------------------------------------- KeelBot (/api/projects/{pid}/helper/...)

export interface Model {
  provider?: string;
  mode?: string;
  model?: string;
  effort?: string | null;
}

export interface HelperMessageData {
  status?: string | null;
  model?: string | null;
  files?: string[] | null;
  /** 0.18.57 what the answer was made from: its path:line links open the file as that ref has it */
  source?: AnswerSource | null;
}

/** 0.18.57 the branch (or commit) an answer was made from, and the files it read at another ref (path -> ref) */
export interface AnswerSource {
  ref?: string | null;
  sha?: string | null;
  worktree?: string | null;
  paths?: Record<string, string> | null;
}

/** 0.18.57 a file as a branch, review ref or commit has it (GET /api/projects/{pid}/repo/file-at) */
/** 0.21.1 a whole file as the review's head (or base) commit has it (GET /review/file) */
export interface ReviewFile {
  path: string;
  ref: string;
  sha: string;
  text: string;
  truncated: boolean;
}

export interface RefFile {
  path: string;
  ref: string;
  sha: string;
  kind: string;
  branch?: string | null;
  /** the ref is the branch the project folder has checked out */
  current: boolean;
  checked_out?: string | null;
  binary: boolean;
  text?: string | null;
}

export interface HelperMessage {
  n: number;
  /** user | helper | assistant | note */
  role: string;
  text?: string;
  call_id?: string | null;
  data?: HelperMessageData | null;
  at?: string;
}

export interface HelperSession {
  id: string;
  project?: string;
  /** ask | edit | fix | side */
  mode?: string;
  title?: string;
  model?: Model | null;
  /** idle | running | failed */
  status?: string;
  error?: string | null;
  tokens_in?: number;
  tokens_out?: number;
  cost_usd?: number;
  updated_at?: string;
  messages?: HelperMessage[] | null;
  busy?: boolean | null;
}

export interface TurnStarted {
  session?: string;
  call_id?: string;
  n?: number;
}

export interface HelperChange {
  path: string;
  status?: string;
  added?: number;
  removed?: number;
}

export interface HelperDone {
  ok?: boolean;
  sha?: string | null;
  message?: string | null;
  error?: string | null;
}

export interface JobStep {
  n?: number;
  at?: string;
  kind?: string;
  text?: string;
  tool?: string | null;
  path?: string | null;
  ok?: boolean | null;
  diff?: string | null;
  output?: string | null;
  ms?: number | null;
  server?: string | null;
}

/** One agent call of the jobs list (GET /api/jobs?project=), for a flow's activity when it is opened mid-run. */
export interface JobRow {
  id: string;
  project_id?: string | null;
  thread_id?: string | null;
  agent?: string | null;
  provider?: string | null;
  model?: string | null;
  step?: string | null;
  phase?: string | null;
  status?: string;
  started_at?: string;
  ended_at?: string | null;
  tokens_in?: number;
  tokens_out?: number;
  cost_usd?: number;
  effort?: string | null;
}

export interface JobSteps {
  steps?: JobStep[];
  running?: boolean;
}

/** One model of the catalog (GET /api/providers/models). */
export interface CatalogModel {
  id: string;
  label: string;
  efforts?: string[];
}

export interface CatalogProvider {
  id: string;
  label: string;
  modes: Record<string, CatalogModel[]>;
  efforts: string[];
}

/** One engine event from the live stream (GET /api/events, server-sent events). */
export interface EngineEvent {
  type: string;
  thread_id?: string;
  project_id?: string;
  step?: string | null;
  at?: string;
  call_id?: string | null;
  data?: Record<string, unknown> | null;
}

/** keel's error body: `{error, hint}`. */
export interface ErrorBody {
  error?: string | null;
  hint?: string | null;
}
