// 0.22.0 api calls of the GitHub Issues plugin (issue #64): a project's connection. The tasks themselves are the Tasks
// plugin's; the GitHub token is core's (Settings › Connections › GitHub).

import { del, get, post, put } from "@keel/web-sdk";

// ---- the keel task statuses: the same list and words as the Tasks plugin (tasksApi.ts) ----
export type TaskStatus = "todo" | "in_progress" | "in_review" | "testing_pp" | "ready_prod" | "done" | "cancelled" | "blocked";
export const TASK_STATUSES: TaskStatus[] = ["todo", "in_progress", "in_review", "testing_pp", "ready_prod", "done", "cancelled", "blocked"];
export const STATUS_LABEL: Record<TaskStatus, string> = {
  todo: "To do",
  in_progress: "In progress",
  in_review: "In review",
  testing_pp: "Testing (PP)",
  ready_prod: "Ready",
  done: "Done",
  cancelled: "Cancelled",
  blocked: "Blocked",
};

export type Follow = "assigned" | "label" | "all";

export type GhIssuesSettings = {
  /** owner/repo; null = the project's origin */
  repo: string | null;
  follow: Follow;
  label: string | null;
  /** keel status → "-" | open | closed | closed:not planned | label:<name> (what the person set) */
  status_map: Partial<Record<TaskStatus, string>>;
  comment: boolean;
  closes_on_merge: boolean;
  github_reviewers: string[];
  poll_minutes: number;
};

export type GhIssuesView = {
  connected: boolean;
  settings: GhIssuesSettings;
  repo: string | null;
  repo_from: "settings" | "origin" | null;
  origin: string | null;
  repo_url: string | null;
  host: string;
  token_set: boolean;
  /** every keel status → what keel does to the issue (the saved map over the defaults) */
  status_map: Record<TaskStatus, string>;
  last_sync_at: string | null;
  last_sync_error: string | null;
  me: string | null;
  /** the other tracker this project follows (a project uses one), e.g. "Jira" */
  other_tracker: string | null;
};

export type GhIssuesSave = Partial<GhIssuesSettings>;

export type GhIssuesTest = {
  ok: boolean;
  user?: string | null;
  repo?: string | null;
  error?: string | null;
  hint?: string | null;
  read_only?: boolean;
};

const e = encodeURIComponent;

export const ghIssuesApi = {
  view: (pid: string) => get<GhIssuesView>(`/projects/${e(pid)}/github-issues`),
  save: (pid: string, s: GhIssuesSave) => put<GhIssuesView>(`/projects/${e(pid)}/github-issues`, s),
  remove: (pid: string) => del<{ ok: boolean }>(`/projects/${e(pid)}/github-issues`),
  test: (pid: string, s?: GhIssuesSave) => post<GhIssuesTest>(`/projects/${e(pid)}/github-issues/test`, s ?? {}),
  labels: (pid: string) => get<string[]>(`/projects/${e(pid)}/github-issues/labels`),
};
