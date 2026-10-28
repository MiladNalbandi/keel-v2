// v0.14.0 the Code Review plugin (api keel.api.review): pull requests (GitHub) and merge requests (GitLab), the branch
// you are on, a review's files, diffs, threads and your pending comments, jumping through the reviewed code, and
// keel's AI runs (overview, checked findings).

import {
  del,
  get,
  post,
  put,
  type Commit,
  type FileDiff,
} from "@keel/web-sdk";

export type HostRef = {
  kind: "github" | "gitlab";
  host: string;
  path: string;
  web: string;
};
export type PrSummary = {
  number: number;
  title: string;
  author: string;
  branch: string;
  base: string;
  draft: boolean;
  updated_at: string;
  url: string;
  review_requested: boolean;
  mine: boolean;
  head_sha?: string | null;
  assigned?: boolean;
  /** 0.18.60 who approved it and who asked for changes: read for your own pull requests only (null: not read) */
  approved?: string[] | null;
  changes_requested?: string[] | null;
};

/** 0.18.60 "approved by ana, bo" / "changes asked by cy" for a pull request of the list, or null when nobody did. */
export function decisionText(p: PrSummary): {
  approved: string | null;
  changes: string | null;
} {
  return {
    approved: p.approved?.length ? `approved by ${p.approved.join(", ")}` : null,
    changes: p.changes_requested?.length
      ? `changes asked by ${p.changes_requested.join(", ")}`
      : null,
  };
}
export type PrList = {
  host: HostRef | null;
  me: string | null;
  prs: PrSummary[];
  counts: Record<string, number>;
  note: string | null;
};
export type BranchSummary = {
  branch: string | null;
  base: string | null;
  ahead: number;
  files: number;
  added: number;
  removed: number;
  pr: PrSummary | null;
  note: string | null;
};
export type CheckRun = { name: string; state: string; url?: string | null };
export type ReviewComment = {
  id: string;
  author: string;
  body: string;
  at: string;
  url?: string | null;
};
export type ReviewThread = {
  id: string;
  path: string | null;
  line: number | null;
  side: "RIGHT" | "LEFT";
  resolved: boolean;
  outdated: boolean;
  comments: ReviewComment[];
  reply_to?: string | null;
  /** a thread on several lines starts here */
  start_line?: number | null;
  start_side?: "RIGHT" | "LEFT" | null;
  /** in your own pending review on the host (started on its site): it goes out with your next review */
  pending?: boolean;
};
export type ChangedFile = {
  path: string;
  status: string;
  from?: string | null;
  added: number;
  removed: number;
  binary: boolean;
};
export type Draft = {
  id: string;
  path: string | null;
  line: number | null;
  side: "RIGHT" | "LEFT";
  body: string;
  finding_id?: string | null;
  created_at: string;
  /** a comment on several lines starts here */
  start_line?: number | null;
  start_side?: "RIGHT" | "LEFT" | null;
};
export type ReviewView = {
  key: string;
  kind: "pr" | "branch";
  number: number | null;
  title: string;
  author: string | null;
  body: string | null;
  base: string;
  branch: string;
  base_sha: string;
  head_sha: string;
  url: string | null;
  state: string | null;
  draft: boolean;
  same_repo: boolean;
  checks: CheckRun[];
  approved: string[];
  changes_requested: string[];
  files: ChangedFile[];
  added: number;
  removed: number;
  commits: Commit[];
  threads: ReviewThread[];
  conversation: ReviewComment[];
  drafts: Draft[];
  viewed: string[];
  can_post: boolean;
  host: HostRef | null;
  me: string | null;
  notes: string[];
  mine?: boolean;
  mergeable?: boolean | null;
  merge_state?: string | null;
  /** a branch review: its open pull request, where its pending comments can go */
  branch_pr?: number | null;
};
export type CodePlace = {
  path: string;
  line: number;
  text: string;
  declaration: boolean;
  test: boolean;
  changed: boolean;
};
export type Places = {
  symbol: string;
  ref: string;
  places: CodePlace[];
  truncated: boolean;
};
export type FileAt = {
  path: string;
  ref: string;
  sha: string;
  text: string;
  truncated: boolean;
};
export type SubmitEvent = "COMMENT" | "APPROVE" | "REQUEST_CHANGES";
export type SubmitResult = {
  posted: number;
  in_body: number;
  event: SubmitEvent;
  url: string | null;
  view: ReviewView;
};

export type Severity = "blocking" | "should_fix" | "nit";
export type Finding = {
  id: string;
  title: string;
  severity: Severity;
  category: string;
  path: string | null;
  line: number | null;
  side: "RIGHT" | "LEFT";
  why: string;
  fix: string;
  suggestion?: string | null;
  pre_existing: boolean;
  reviewer: string;
  check?: "confirmed" | "rejected" | "not checked";
  check_why?: string;
};
export type Overview = {
  stage: string;
  summary: string;
  files: { path: string; what: string }[];
  order: string[];
  diagram: string;
  effort: number;
  risk: "low" | "medium" | "high" | null;
  risk_why: string;
  split: string;
  questions: string[];
};
export type FindingsResult = {
  stage: string;
  findings?: Finding[];
  rejected?: Finding[];
  /** keel 0.18.57: claims nobody could prove or disprove (unproven, unanswered, over the cap): never findings */
  not_checked?: Finding[];
  nits?: Finding[];
  more_nits?: number;
  pre_existing?: Finding[];
  security?: string;
  tests?: string;
  counts?: Record<string, number>;
  reviewers?: number;
};
export type Verdict = "reviewer_right" | "author_right" | "partly" | "unclear";
export type JudgeItem = {
  claim: string;
  verdict: Verdict;
  why: string;
  fix?: string | null;
};
/** keel's check of one thread ("Is the reviewer right?"), an extra button's answer, or the flow one started. */
export type JudgeResult = {
  stage: "check" | "done" | "capped" | "flow";
  thread_id?: string;
  items?: JudgeItem[];
  reply?: string;
  /** an extra ask button's answer */
  text?: string;
  label?: string;
  agent?: string;
  model?: string;
  effort?: string | null;
  harder?: boolean;
  estimate?: number;
  cap?: number;
  tokens?: number;
  tokens_in?: number;
  tokens_out?: number;
  over_cap?: boolean;
  checked_at?: string;
  batch_size?: number;
  context?: { code_lines: number; hunk_lines: number; files: number };
  /** a flow it started (workflow mode) */
  flow_id?: string;
  workflow?: string;
  title?: string;
  branch?: string | null;
};
export type ThreadButtonInfo = {
  id: string;
  label: string;
  mode: "ask" | "workflow";
  agent?: string | null;
  workflow?: string | null;
};
export type ThreadConfig = {
  judge?: {
    mode: "quick" | "workflow";
    workflow: string;
    allow_harder: boolean;
    token_cap: number;
    agent: string;
  };
  address_workflow?: string;
  /** keel 0.18.56 the flows the Address it confirmation offers, the configured one first */
  address_flows?: { id: string; name: string }[];
  buttons?: ThreadButtonInfo[];
  /** keel 0.18.65 what Find problems runs (Settings › Review): its text, the reviewers, how a finding is fixed */
  find?: {
    text: string;
    reviewers: { letter: string; name: string }[];
    fix: { flow: "quick" | "small" | "change" | "ask"; one_flow_per_file: boolean };
  };
};
export type AiRun<R> = {
  id: string;
  kind: string;
  status: "running" | "done" | "failed";
  stage: string;
  head_sha: string;
  stale: boolean;
  sessions: { sid: string; role: string; status: string }[];
  result: R | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};
export type AiState = {
  overview: AiRun<Overview> | null;
  findings: AiRun<FindingsResult> | null;
  decisions: Record<string, { decision: string; why: string | null }>;
  /** per thread: keel's latest check */
  judges?: Record<string, AiRun<JudgeResult>>;
  /** per thread: the flow Address it started */
  addressed?: Record<string, { flow_id: string; title: string; branch: string | null; workflow?: string; at: string }>;
  /** per "<button id>|<thread>": an extra button's latest run */
  buttons?: Record<string, AiRun<JudgeResult>>;
  config?: ThreadConfig;
  /** keel 0.18.65 per finding: the flow "Fix it" started */
  fixes?: Record<string, { flow_id: string; title: string; branch: string | null; workflow?: string; with?: number; at: string }>;
};
export type AddressResult = {
  flow_id: string;
  title: string;
  branch: string | null;
  workflow: string;
  note: string;
  state: AiState;
};
export type ThreadButton = {
  label: string;
  mode: "ask" | "workflow";
  agent?: string | null;
  workflow?: string | null;
  prompt: string;
};
export type ThreadSettingsValues = {
  judge_mode?: "quick" | "workflow";
  judge_agent?: string;
  judge_model?: { provider: string; mode: string; model: string; effort?: string | null } | null;
  judge_effort?: "low" | "medium" | "high";
  judge_token_cap?: number;
  judge_allow_harder?: boolean;
  judge_workflow?: string;
  address_workflow?: string;
  thread_buttons?: ThreadButton[];
};
export type ThreadSettingsView = {
  defaults: ThreadSettingsValues;
  general: ThreadSettingsValues;
  overrides: ThreadSettingsValues | null;
  effective: ThreadSettingsValues;
  agents: string[];
  workflows: { id: string; name: string }[];
  models: { role: string; model: { provider: string; mode: string; model: string } }[];
  limits: { buttons: number; label: number; min_cap: number; max_cap: number; placeholders: string[] };
};
/** keel 0.18.57 the overview's diagrams (GET /review/diagrams): read from git, no model; kept per head commit. */
export type Cite = { path: string; line: number } | null;
export type DbColumn = {
  name: string;
  type: string;
  pk: boolean;
  fk: string | null;
  unique: boolean;
  nullable: boolean | null;
  default: string | null;
  status: "new" | "changed" | "removed" | "same";
  was: string | null;
  cite?: Cite;
};
export type DbTable = {
  name: string;
  status: "new" | "changed" | "removed" | "related";
  renamed_from?: string;
  cite?: Cite;
  columns: DbColumn[];
  indexes: { name: string; columns: string[]; unique: boolean; status: string }[];
  fks: { name: string | null; columns: string[]; ref_table: string | null; ref_columns: string[]; on_delete: string | null; status: string }[];
};
export type DbRisk = {
  kind: string;
  severity: "high" | "medium" | "low";
  text: string;
  path: string;
  line: number;
  uses?: { path: string; line: number }[];
};
export type DbMigration = {
  path: string;
  framework: string;
  status: string;
  role: string;
  parsed: boolean;
  partly?: boolean;
  steps: { line: number; text: string; kind: string; table: string | null }[];
};
export type DatabaseDiagram = {
  shown: boolean;
  count: number;
  before?: string;
  after?: string;
  notes?: string[];
  tables?: DbTable[];
  changes?: { new: number; changed: number; removed: number };
  migrations?: DbMigration[];
  risks?: DbRisk[];
  not_parsed?: { path: string; line: number; why: string }[];
  mermaid?: string;
  ascii?: string;
};
export type SysNode = {
  id: string;
  label: string;
  layer: string;
  area: string;
  files: string[];
  status: "added" | "changed" | "removed";
};
export type SystemDiagram = {
  source: "map" | "paths";
  nodes: SysNode[];
  edges: { from: string; to: string; label: string; kind: "code" | "guess" | "data" }[];
  groups: { id: string; title: string; nodes: string[] }[];
  externals: { id: string; label: string; kind: string; detail: string; files: string[] }[];
  collapsed: { id: string; label: string; names: string[] } | null;
  tests: number;
  skipped: number;
  mermaid: string;
  ascii: string;
};
export type Diagrams = {
  key: string;
  base: string;
  head: string;
  text: string;
  database: DatabaseDiagram;
  system: SystemDiagram;
};
export type GitLabView = {
  set: boolean;
  url: string | null;
  host: string | null;
  hint: string | null;
};

const e = encodeURIComponent;
const R = (pid: string) => `/projects/${e(pid)}/review`;
const qk = (key: string) => `key=${e(key)}`;

export const reviewApi = {
  prs: (pid: string, filter: "review" | "assigned" | "mine" | "all") =>
    get<PrList>(`${R(pid)}/prs?filter=${filter}`),
  branch: (pid: string) => get<BranchSummary>(`${R(pid)}/branch`),
  view: (pid: string, key: string, refresh = false) =>
    get<ReviewView>(
      `${R(pid)}/view?${qk(key)}${refresh ? "&refresh=true" : ""}`,
    ),
  diff: (pid: string, key: string, path: string) =>
    get<FileDiff>(`${R(pid)}/diff?${qk(key)}&path=${e(path)}`),
  file: (
    pid: string,
    key: string,
    path: string,
    side: "head" | "base" = "head",
  ) => get<FileAt>(`${R(pid)}/file?${qk(key)}&path=${e(path)}&side=${side}`),
  definition: (pid: string, key: string, symbol: string) =>
    get<Places>(`${R(pid)}/definition?${qk(key)}&symbol=${e(symbol)}`),
  usages: (pid: string, key: string, symbol: string) =>
    get<Places>(`${R(pid)}/usages?${qk(key)}&symbol=${e(symbol)}`),
  addDraft: (
    pid: string,
    b: {
      key: string;
      path?: string | null;
      line?: number | null;
      side?: "RIGHT" | "LEFT";
      body: string;
      finding_id?: string | null;
      start_line?: number | null;
      start_side?: "RIGHT" | "LEFT" | null;
    },
  ) => post<Draft>(`${R(pid)}/drafts`, b),
  moveDrafts: (pid: string, from: string, to: string) =>
    post<ReviewView>(`${R(pid)}/drafts/move`, { from, to }),
  editDraft: (pid: string, id: string, body: string) =>
    put<Draft>(`${R(pid)}/drafts/${e(id)}`, { body }),
  deleteDraft: (pid: string, id: string) =>
    del<{ ok: boolean }>(`${R(pid)}/drafts/${e(id)}`),
  viewed: (pid: string, key: string, path: string, viewed: boolean) =>
    post<{ viewed: string[] }>(`${R(pid)}/viewed`, { key, path, viewed }),
  reply: (pid: string, key: string, threadId: string, body: string) =>
    post<ReviewView>(`${R(pid)}/threads/${e(threadId)}/reply`, { key, body }),
  resolve: (pid: string, key: string, threadId: string, resolved: boolean) =>
    post<ReviewView>(`${R(pid)}/threads/${e(threadId)}/resolve`, {
      key,
      resolved,
    }),
  submit: (pid: string, key: string, event: SubmitEvent, body: string) =>
    post<SubmitResult>(`${R(pid)}/submit`, { key, event, body }),
  merge: (pid: string, key: string, method: string, deleteBranch: boolean) =>
    post<{ merged: boolean; message: string; view: ReviewView }>(`${R(pid)}/merge`, { key, method, delete_branch: deleteBranch }),
  checkout: (pid: string, key: string) =>
    post<{ branch: string; note: string }>(`${R(pid)}/checkout`, { key }),
  ai: (pid: string, key: string) => get<AiState>(`${R(pid)}/ai?${qk(key)}`),
  diagrams: (pid: string, key: string, refresh = false) =>
    get<Diagrams>(`${R(pid)}/diagrams?${qk(key)}${refresh ? "&refresh=true" : ""}`),
  aiStart: (pid: string, key: string, kind: "overview" | "findings") =>
    post<AiState>(`${R(pid)}/ai/${kind}`, { key }),
  decide: (
    pid: string,
    key: string,
    findingId: string,
    decision: "dismissed" | "commented" | "open",
    why?: string,
  ) =>
    post<AiState>(`${R(pid)}/ai/findings/${e(findingId)}`, {
      key,
      decision,
      why,
    }),
  judge: (pid: string, key: string, threadId: string, opts: { again?: boolean; harder?: boolean } = {}) =>
    post<AiState>(`${R(pid)}/threads/${e(threadId)}/judge`, { key, ...opts }),
  judgeAll: (pid: string, key: string, opts: { again?: boolean; harder?: boolean } = {}) =>
    post<AiState>(`${R(pid)}/threads/judge`, { key, ...opts }),
  /** [fix]: the person's answer to "What should the fix be?" (0.18.57, asked when the thread has no reply of theirs). */
  address: (pid: string, key: string, threadId: string, note?: string, workflow?: string, fix?: string) =>
    post<AddressResult>(`${R(pid)}/threads/${e(threadId)}/address`, { key, note, workflow, fix }),
  /** keel 0.18.65 "Fix it" on a confirmed finding: the flow Settings › Review names, or [workflow] when it asks each time. */
  fixFinding: (pid: string, key: string, findingId: string, workflow?: string) =>
    post<AddressResult>(`${R(pid)}/ai/findings/${e(findingId)}/fix`, { key, workflow }),
  button: (pid: string, key: string, threadId: string, buttonId: string, opts: { again?: boolean; harder?: boolean } = {}) =>
    post<AiState>(`${R(pid)}/threads/${e(threadId)}/buttons/${e(buttonId)}`, { key, ...opts }),
  threadSettings: (pid: string | null) =>
    get<ThreadSettingsView>(pid ? `${R(pid)}/settings` : "/review/settings"),
  saveThreadSettings: (pid: string | null, patch: Record<string, unknown>) =>
    put<ThreadSettingsView>(pid ? `${R(pid)}/settings` : "/review/settings", patch),
  gitlab: () => get<GitLabView>("/gitlab"),
  saveGitlab: (url: string, token?: string) =>
    put<GitLabView>("/gitlab", { url, token }),
  clearGitlab: () => del<GitLabView>("/gitlab"),
};

/** Where a comment sits: "12", "10–12", or "10 (old)–12" when it starts on the old side. */
export function linesLabel(c: {
  line: number | null;
  side: "RIGHT" | "LEFT";
  start_line?: number | null;
  start_side?: "RIGHT" | "LEFT" | null;
}): string {
  if (c.line == null) return "";
  const old = (side?: string | null) => (side === "LEFT" ? " (old)" : "");
  if (c.start_line == null) return `${c.line}${old(c.side)}`;
  const s = c.start_side ?? c.side;
  return s === c.side
    ? `${c.start_line}–${c.line}${old(c.side)}`
    : `${c.start_line}${old(s)}–${c.line}${old(c.side)}`;
}

/** "#7" on GitHub, "!7" on GitLab (merge requests). */
export const prLabel = (
  host: HostRef | null | undefined,
  n: number | null | undefined,
) => (n == null ? "" : `${host?.kind === "gitlab" ? "!" : "#"}${n}`);
export const prWord = (host: HostRef | null | undefined) =>
  host?.kind === "gitlab" ? "merge request" : "pull request";
