// v0.4.1 api calls: the inbox (everything waiting, across projects), notification clean-up and run modes
// (docs/CONTRACT.md, "v0.4.1: inbox, notifications, run modes").

import { del, get, post, type ClarifyQuestion, type RunMode, type ThreadState } from "./api";

/** One thing that waits for a person (GET /api/inbox). */
export type InboxItem = {
  project_id: string;
  project_name: string;
  thread_id: string;
  /** the flow's title */
  flow: string;
  workflow_id: string | null;
  step: string | null;
  /** gate | clarify | fix | budget | usage | dependency */
  kind: string;
  title: string;
  /** cut to ~700 characters; `more` = the Flow page shows the rest */
  detail: string;
  more: boolean;
  options: ("approve" | "reject")[];
  choices?: string[] | null;
  questions?: ClarifyQuestion[] | null;
  labels?: { approve?: string; reject?: string } | null;
  /** the question's id: sent back with the answer */
  id?: string | null;
  phase?: string | null;
  ac?: string | null;
  run_mode?: RunMode | null;
  /** gates the run mode already approved in this flow, and the last such gate-log line */
  auto_approved?: number;
  last_auto?: string | null;
  since?: string | null;
  /** v0.5.0: a task's item (kind task | jira-manual) instead of a flow's pause; answered with tasksApi.act. */
  task?: InboxTask | null;
};
export type InboxTask = {
  id: string; item_id: number; key: string | null; url: string | null; title: string; status: string; stage: string | null; pr_url: string | null;
  actions: { id: "confirm" | "send_back" | "done"; label: string; needs_note: boolean }[];
};
export type InboxView = { items: InboxItem[]; count: number; kinds: string[]; projects: { id: string; name: string; count: number }[] };
export type InboxAnswer = { decision: "approve" | "reject"; why?: string; payload?: Record<string, unknown>; id?: string | null };

const e = encodeURIComponent;
const q = (params: Record<string, string | undefined>) => {
  const s = Object.entries(params).filter(([, v]) => v).map(([k, v]) => `${k}=${e(v!)}`).join("&");
  return s ? `?${s}` : "";
};

export const inboxApi = {
  list: (f: { project?: string; kind?: string } = {}) => get<InboxView>(`/inbox${q(f)}`),
  count: () => get<{ count: number; projects: Record<string, number> }>("/inbox/count"),
  /** The same resume as the Flow page; 409 when the thread now asks something else. */
  act: (tid: string, a: InboxAnswer) => post<ThreadState>(`/inbox/${e(tid)}/act`, a),
  /** From the next gate on. */
  setMode: (tid: string, mode: RunMode) => post<ThreadState>(`/threads/${e(tid)}/mode`, { mode }),
  deleteNote: (id: string) => del<{ ok: boolean }>(`/notifications/${e(id)}`),
  clearNotes: () => del<{ ok: boolean; count: number }>("/notifications"),
};
