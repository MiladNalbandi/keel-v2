// v0.5.0 api calls of the Jira plugin: the Jira connection per project and the MCP catalog (docs/CONTRACT.md, "v0.5.0:
// tasks and Jira"). Moved from keel's tasksApi.ts; the tasks themselves are the Tasks plugin's.

import { del, get, post, put, type McpServer } from "@keel/web-sdk";

// ---- the keel task statuses a Jira status maps to: the same list and words as the Tasks plugin (tasksApi.ts) ----
export type TaskStatus =
  | "todo"
  | "in_progress"
  | "in_review"
  | "testing_pp"
  | "ready_prod"
  | "done"
  | "cancelled"
  | "blocked";
export const TASK_STATUSES: TaskStatus[] = [
  "todo",
  "in_progress",
  "in_review",
  "testing_pp",
  "ready_prod",
  "done",
  "cancelled",
  "blocked",
];
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

// ---- Jira connection ----
export type JiraSettings = {
  kind: "cloud" | "server";
  base_url: string;
  email: string | null;
  project_key: string | null;
  board_id: string | null;
  jql: string | null;
  /** keel status → Jira status or transition name; "-" = do not move */
  status_map: Partial<Record<TaskStatus, string>>;
  reviewer_field: string | null;
  jira_reviewers: string[];
  github_reviewers: string[];
  poll_minutes: number;
};
export type JiraUser = {
  name: string;
  account_id?: string | null;
  username?: string | null;
  email?: string | null;
};
export type JiraView = {
  connected: boolean;
  settings: JiraSettings;
  token_set: boolean;
  token_hint: string | null;
  default_jql: string;
  jql: string;
  last_sync_at: string | null;
  last_sync_error: string | null;
  me: JiraUser | null;
  mcp_server: string | null;
};
export type JiraSave = Partial<Omit<JiraSettings, "status_map">> & {
  token?: string;
  status_map?: Partial<Record<TaskStatus, string>>;
};
export type JiraTest = {
  ok: boolean;
  user?: JiraUser | null;
  error?: string | null;
  hint?: string | null;
  kind?: string | null;
};
export type JiraStatus = { name: string; category: string | null };
export type JiraDiscovery = {
  statuses: JiraStatus[];
  transitions: {
    id: string;
    name: string;
    to: string;
    category: string | null;
  }[];
  fields: {
    id: string;
    name: string;
    type: string | null;
    items: string | null;
    custom: boolean;
  }[];
  suggested: Partial<Record<TaskStatus, string>>;
  keel_statuses: TaskStatus[];
};
export type CatalogEntry = {
  id: string;
  name: string;
  about: string;
  url: string;
  license: string;
  command: string;
  ready: boolean;
  why: string | null;
  server: string | null;
  added: boolean;
};

const e = encodeURIComponent;

export const jiraApi = {
  jira: (pid: string) => get<JiraView>(`/projects/${e(pid)}/jira`),
  saveJira: (pid: string, s: JiraSave) =>
    put<JiraView>(`/projects/${e(pid)}/jira`, s),
  deleteJira: (pid: string) => del<{ ok: boolean }>(`/projects/${e(pid)}/jira`),
  testJira: (pid: string, s?: JiraSave) =>
    post<JiraTest>(`/projects/${e(pid)}/jira/test`, s ?? {}),
  discoverJira: (pid: string, key?: string) =>
    get<JiraDiscovery>(
      `/projects/${e(pid)}/jira/discover${key ? `?key=${e(key)}` : ""}`,
    ),

  catalog: (pid: string) =>
    get<CatalogEntry[]>(`/projects/${e(pid)}/mcp-catalog`),
  addFromCatalog: (pid: string, id: string) =>
    post<McpServer>(`/projects/${e(pid)}/mcp-catalog/${e(id)}`),
};
