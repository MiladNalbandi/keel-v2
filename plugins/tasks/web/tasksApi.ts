// v0.5.0 api calls: tasks (local or from Jira) and their lifecycle (docs/CONTRACT.md, "v0.5.0: tasks and Jira"). The
// Jira connection and the MCP catalog are the Jira plugin's (plugins/jira/web/jiraApi.ts).

import { del, get, post, put, type ProjectSettings, type RunMode, type Workflow } from "@keel/web-sdk";

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
export type TaskEvent = { id: number; task_id: string; at: string; kind: string; from_status: string | null; to_status: string | null; note: string | null; actor: "user" | "keel" | "jira" | "github" };
export type TaskItem = {
  id: number; task_id: string; project_id: string; kind: "task" | "jira-manual" | "task-proposal" | "task-review"; stage: string | null;
  title: string; detail: string; created_at: string; done_at: string | null;
  /** 0.18.52 a proposal (workflow_id, why, title, request, questions, kind flow | review, no_pr) or a review comment
   *  (thread_id, pr_url, author, where, said, verdict, why, reply) */
  data?: Record<string, unknown> | null;
};
export type TaskFlow = { thread_id: string; workflow_id: string | null; status: string; phase: string | null; current: string | null; title: string | null };

export type Task = {
  id: string;
  project_id: string;
  title: string;
  description: string;
  type: TaskType;
  status: TaskStatus;
  source: "local" | "jira" | "github";
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
/** 0.22.0 tracker: the project's tracker (jira, github; null without one); where: what it follows (GitHub: owner/repo). */
export type SyncInfo = {
  connected: boolean; kind: "cloud" | "server" | "github" | null; last_sync_at: string | null; last_sync_error: string | null; me: string | null; poll_minutes: number | null;
  tracker?: "jira" | "github" | null; where?: string | null;
};
export type TaskList = { tasks: Task[]; sync: SyncInfo };
export type SyncResult = {
  ok: boolean; jira: boolean; total: number; created: number; updated: number; moved: number; at: string | null;
  error?: string | null; hint?: string | null; reviews_checked: number; reviews_moved: number; comments_checked?: number;
};
/** 0.18.52 task automations (docs/plans/task-automations.md) */
export type AutomationMode = "off" | "propose" | "start";
export type AutomationRule = { mode: AutomationMode; run_mode?: RunMode | null; cap_tokens?: number | null };
export type AutomationSettings = {
  rules: Record<string, AutomationRule>;
  keelbot: { account?: string | null; label?: string | null };
  allow_reply: boolean;
  daily_auto_starts: number;
  pick_cap_tokens: number;
};
export type AutomationView = {
  settings: AutomationSettings; statuses: TaskStatus[]; modes: AutomationMode[]; review_watch: boolean; tracker: boolean; auto_starts_today: number;
};
export type ItemActBody = { note?: string; workflow_id?: string; answers?: Record<string, string | string[]>; allow_dirty?: boolean };
export type JiraUser = { name: string; account_id?: string | null; username?: string | null; email?: string | null };

/** 0.18.57 the Jira plugin's connection, as the Automations drawer's "Jira statuses" tab reads it (a 404 when the plugin
 *  is off): whether it is connected, and the keel status → Jira status map ("-" = do not move the ticket). */
export type JiraLink = { connected: boolean; settings: { kind: "cloud" | "server"; base_url: string; status_map: Partial<Record<TaskStatus, string>> }; me: { name: string } | null };
export type JiraFound = { statuses: { name: string; category: string | null }[]; transitions: { name: string; to: string }[]; suggested: Partial<Record<TaskStatus, string>> };

export type NewTask = { title: string; description?: string; type?: TaskType; external_key?: string; external_url?: string; assignee?: string; priority?: string; reviewers?: string[] };

const e = encodeURIComponent;

export const tasksApi = {
  list: (pid: string) => get<TaskList>(`/projects/${e(pid)}/tasks`),
  create: (pid: string, t: NewTask) => post<Task>(`/projects/${e(pid)}/tasks`, t),
  sync: (pid: string) => post<SyncResult>(`/projects/${e(pid)}/tasks/sync`),
  get: (id: string) => get<Task>(`/tasks/${e(id)}`),
  update: (id: string, t: Partial<NewTask>) => put<Task>(`/tasks/${e(id)}`, t),
  remove: (id: string) => del<{ ok: boolean }>(`/tasks/${e(id)}`),
  start: (id: string, b: { workflow_id?: string; run_mode?: RunMode; allow_dirty?: boolean; allow_fake?: boolean; force?: boolean }) => post<Task>(`/tasks/${e(id)}/start`, b),
  confirm: (id: string, stage: "pp" | "prod", note?: string) => post<Task>(`/tasks/${e(id)}/confirm`, { stage, ...(note ? { note } : {}) }),
  move: (id: string, to: TaskStatus, note?: string) => post<Task>(`/tasks/${e(id)}/status`, { to, ...(note ? { note } : {}) }),
  setPr: (id: string, url: string) => post<Task>(`/tasks/${e(id)}/pr`, { url }),
  /** An Inbox task item's button: confirm | send_back | done. */
  act: (itemId: number, action: string, note?: string) => post<Task>(`/inbox/tasks/${itemId}/act`, { action, ...(note ? { note } : {}) }),
  /** 0.18.52 a proposal's or review comment's button, with the picked flow, the answers or a note. */
  actWith: (itemId: number, action: string, b: ItemActBody) => post<Task>(`/inbox/tasks/${itemId}/act`, { action, ...b }),
  /** "Pick a flow for me" and "Review it": keel opens a proposal (it starts nothing from here). */
  pick: (id: string) => post<Task>(`/tasks/${e(id)}/pick`),
  review: (id: string) => post<Task>(`/tasks/${e(id)}/review`),
  automations: (pid: string) => get<AutomationView>(`/projects/${e(pid)}/tasks/automations`),
  saveAutomations: (pid: string, s: Partial<AutomationSettings>) => put<AutomationView>(`/projects/${e(pid)}/tasks/automations`, s),
  /** the Jira plugin's user search (a 404 when it is off) */
  jiraUsers: (pid: string, q: string) => get<JiraUser[]>(`/projects/${e(pid)}/jira/users?q=${e(q)}`),

  /** the Jira plugin's connection and its status map (Automations › Jira statuses; the setup stays in Connections) */
  jira: (pid: string) => get<JiraLink>(`/projects/${e(pid)}/jira`),
  jiraStatuses: (pid: string) => get<JiraFound>(`/projects/${e(pid)}/jira/discover`),
  saveStatusMap: (pid: string, status_map: Partial<Record<TaskStatus, string>>) => put<JiraLink>(`/projects/${e(pid)}/jira`, { status_map }),

  // keel's own reads the task drawer needs: the workflows a task can start, and the project's run mode
  workflows: (pid: string) => get<Workflow[]>(`/projects/${e(pid)}/workflows`),
  projectSettings: (pid: string) => get<ProjectSettings>(`/projects/${e(pid)}/settings`),
};
