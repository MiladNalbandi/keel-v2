// v0.14.0 the Code Review plugin (api keel.api.review): pull requests (GitHub) and merge requests (GitLab), the branch
// you are on, a review's files, diffs, threads and your pending comments, jumping through the reviewed code, and
// keel's AI runs (overview, checked findings).

import { del, get, post, put, type Commit, type FileDiff } from "./api";

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
};
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
  nits?: Finding[];
  more_nits?: number;
  pre_existing?: Finding[];
  security?: string;
  tests?: string;
  counts?: Record<string, number>;
  reviewers?: number;
};
export type AiRun<R> = {
  id: string;
  kind: "overview" | "findings";
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
    },
  ) => post<Draft>(`${R(pid)}/drafts`, b),
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
  gitlab: () => get<GitLabView>("/gitlab"),
  saveGitlab: (url: string, token?: string) =>
    put<GitLabView>("/gitlab", { url, token }),
  clearGitlab: () => del<GitLabView>("/gitlab"),
};

/** "#7" on GitHub, "!7" on GitLab (merge requests). */
export const prLabel = (
  host: HostRef | null | undefined,
  n: number | null | undefined,
) => (n == null ? "" : `${host?.kind === "gitlab" ? "!" : "#"}${n}`);
export const prWord = (host: HostRef | null | undefined) =>
  host?.kind === "gitlab" ? "merge request" : "pull request";
