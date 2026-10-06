// v0.5.0 api calls: tasks (local or from Jira), their lifecycle, the Jira connection per project and the MCP catalog
// (docs/CONTRACT.md, "v0.5.0: tasks and Jira").

import { del, get, post, put, type McpServer, type RunMode } from "./api";

export type TaskStatus = "todo" | "in_progress" | "in_review" | "testing_pp" | "ready_prod" | "done" | "cancelled" | "blocked";
export type TaskType = "bug" | "story" | "task";

export const TASK_STATUSES: TaskStatus[] = ["todo", "in_progress", "in_review", "testing_pp", "ready_prod", "done", "cancelled", "blocked"];
export const STATUS_LABEL: Record<TaskStatus, string> = {
  todo: "To do", in_progress: "In progress", in_review: "In review", testing_pp: "Testing (PP)", ready_prod: "Ready",
  done: "Done", cancelled: "Cancelled", blocked: "Blocked",
};
/** The flow a task starts when no workflow is picked (the api's TaskTypes.defaultWorkflow). */
export const DEFAULT_FLOW: Record<TaskType, string> = { bug: "fix", story: "feature", task: "change" };

export type Reviewer = { login: string; on: "github" | "jira"; state: "wanted" | "requested" | "approved" | "changes_requested" | "commented" | "set" };
export type TaskEvent = { id: number; task_id: string; at: string; kind: string; from_status: string | null; to_status: string | null; note: string | null; actor: "user" | "keel" | "jira" };
export type TaskItem = { id: number; task_id: string; project_id: string; kind: "task" | "jira-manual"; stage: string | null; title: string; detail: string; created_at: string; done_at: string | null };
export type TaskFlow = { thread_id: string; workflow_id: string | null; status: string; phase: string | null; current: string | null; title: string | null };

export type Task = {
  id: string;
  project_id: string;
  title: string;
  description: string;
  type: TaskType;
  status: TaskStatus;
  source: "local" | "jira";
  external_key: string | null;
  external_url: string | null;
  external_status: string | null;
  assignee: string | null;
  priority: string | null;
  thread_id: string | null;
  workflow_id: string | null;
  pr_url: string | null;
  reviewers: Reviewer[];
  blocked_reason: string | null;
  created_at: string;
  updated_at: string;
  flow: TaskFlow | null;
  waiting: TaskItem[];
  /** GET /tasks/{id} only */
  events?: TaskEvent[] | null;
};
export type SyncInfo = { connected: boolean; kind: "cloud" | "server" | null; last_sync_at: string | null; last_sync_error: string | null; me: string | null; poll_minutes: number | null };
export type TaskList = { tasks: Task[]; sync: SyncInfo };
export type SyncResult = {
  ok: boolean; jira: boolean; total: number; created: number; updated: number; moved: number; at: string | null;
  error?: string | null; hint?: string | null; reviews_checked: number; reviews_moved: number;
};
export type NewTask = { title: string; description?: string; type?: TaskType; external_key?: string; external_url?: string; assignee?: string; priority?: string; reviewers?: string[] };

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
export type JiraUser = { name: string; account_id?: string | null; username?: string | null; email?: string | null };
export type JiraView = {
  connected: boolean; settings: JiraSettings; token_set: boolean; token_hint: string | null; default_jql: string; jql: string;
  last_sync_at: string | null; last_sync_error: string | null; me: JiraUser | null; mcp_server: string | null;
};
export type JiraSave = Partial<Omit<JiraSettings, "status_map">> & { token?: string; status_map?: Partial<Record<TaskStatus, string>> };
export type JiraTest = { ok: boolean; user?: JiraUser | null; error?: string | null; hint?: string | null; kind?: string | null };
export type JiraStatus = { name: string; category: string | null };
export type JiraDiscovery = {
  statuses: JiraStatus[]; transitions: { id: string; name: string; to: string; category: string | null }[];
  fields: { id: string; name: string; type: string | null; items: string | null; custom: boolean }[];
  suggested: Partial<Record<TaskStatus, string>>; keel_statuses: TaskStatus[];
};
export type CatalogEntry = { id: string; name: string; about: string; url: string; license: string; command: string; ready: boolean; why: string | null; server: string | null; added: boolean };

const e = encodeURIComponent;

export const tasksApi = {
  list: (pid: string) => get<TaskList>(`/projects/${e(pid)}/tasks`),
  create: (pid: string, t: NewTask) => post<Task>(`/projects/${e(pid)}/tasks`, t),
  sync: (pid: string) => post<SyncResult>(`/projects/${e(pid)}/tasks/sync`),
  get: (id: string) => get<Task>(`/tasks/${e(id)}`),
  update: (id: string, t: Partial<NewTask>) => put<Task>(`/tasks/${e(id)}`, t),
  remove: (id: string) => del<{ ok: boolean }>(`/tasks/${e(id)}`),
  start: (id: string, b: { workflow_id?: string; run_mode?: RunMode; allow_dirty?: boolean; allow_fake?: boolean }) => post<Task>(`/tasks/${e(id)}/start`, b),
  confirm: (id: string, stage: "pp" | "prod", note?: string) => post<Task>(`/tasks/${e(id)}/confirm`, { stage, ...(note ? { note } : {}) }),
  move: (id: string, to: TaskStatus, note?: string) => post<Task>(`/tasks/${e(id)}/status`, { to, ...(note ? { note } : {}) }),
  setPr: (id: string, url: string) => post<Task>(`/tasks/${e(id)}/pr`, { url }),
  /** An Inbox task item's button: confirm | send_back | done. */
  act: (itemId: number, action: string, note?: string) => post<Task>(`/inbox/tasks/${itemId}/act`, { action, ...(note ? { note } : {}) }),

  jira: (pid: string) => get<JiraView>(`/projects/${e(pid)}/jira`),
  saveJira: (pid: string, s: JiraSave) => put<JiraView>(`/projects/${e(pid)}/jira`, s),
  deleteJira: (pid: string) => del<{ ok: boolean }>(`/projects/${e(pid)}/jira`),
  testJira: (pid: string, s?: JiraSave) => post<JiraTest>(`/projects/${e(pid)}/jira/test`, s ?? {}),
  discoverJira: (pid: string, key?: string) => get<JiraDiscovery>(`/projects/${e(pid)}/jira/discover${key ? `?key=${e(key)}` : ""}`),

  catalog: (pid: string) => get<CatalogEntry[]>(`/projects/${e(pid)}/mcp-catalog`),
  addFromCatalog: (pid: string, id: string) => post<McpServer>(`/projects/${e(pid)}/mcp-catalog/${e(id)}`),
};
