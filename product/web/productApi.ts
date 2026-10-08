// keel Product's api (product/api ProductController): initiatives, their stages, questions, disagreements, follow-ups,
// the plan and its hand-off, and the teams with their knowledge.

import { del, get, getText, patch, post, put } from "@keel/web-sdk";

export const STAGES = [
  "idea",
  "brief",
  "impact",
  "decision",
  "plan",
  "delivery",
  "outcome",
  "done",
] as const;
export type Stage = (typeof STAGES)[number];
export const STAGE_LABEL: Record<string, string> = {
  idea: "Idea",
  brief: "Brief",
  impact: "Impact",
  decision: "Decision",
  plan: "Plan",
  delivery: "Delivery",
  outcome: "Outcome",
  done: "Done",
};

export type Initiative = {
  id: string;
  n: number;
  title: string;
  idea: string;
  why_now?: string | null;
  outcome_hope?: string | null;
  owner?: string | null;
  stage: string;
  status: string;
  option?: string | null;
  repos: string[];
  metric?: string | null;
  revisit_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type Progress = { done: number; total: number };

export type BoardItem = {
  id: string;
  title: string;
  stage: string;
  status: string;
  waiting: boolean;
  teams: string[];
  option?: string | null;
  progress?: Progress | null;
  next: string;
  updated_at: string;
  revisit_at?: string | null;
};

export type GateQuestion = {
  id: string;
  question: string;
  why?: string;
  options: { label: string; description?: string; recommended?: boolean }[];
};
export type Waiting = {
  step?: string;
  kind?: string;
  title?: string;
  detail?: string;
  questions?: GateQuestion[];
  choices?: string[];
};

export type StageView = {
  stage: string;
  status: string;
  thread_id?: string | null;
  waiting?: Waiting | null;
  next: string;
};

export type DocView = {
  kind: string;
  version: number;
  versions: number[];
  path: string;
  text: string;
  data?: unknown;
  approved_at?: string | null;
  created_at: string;
};
export type ProductDoc = {
  initiative_id: string;
  kind: string;
  version: number;
  path: string;
  sha?: string | null;
  text: string;
  data?: unknown;
  thread_id?: string | null;
  created_at: string;
  approved_at?: string | null;
};

export type Question = {
  id: string;
  initiative_id: string;
  stage: string;
  role: string;
  asked_to?: string | null;
  about?: string | null;
  text: string;
  answer?: string | null;
  answered_by?: string | null;
  status: string;
  created_at: string;
  answered_at?: string | null;
};

export type Disagreement = {
  id: string;
  initiative_id: string;
  stage: string;
  author: string;
  reason: string;
  proposal?: string | null;
  decider: string;
  status: string;
  outcome?: string | null;
  decided_by?: string | null;
  created_at: string;
  decided_at?: string | null;
};

export type ProductEvent = {
  id: number;
  initiative_id: string;
  at: string;
  actor: string;
  kind: string;
  text: string;
  data?: unknown;
};

export type FollowUp = {
  id: string;
  initiative_id: string;
  kind: string;
  text: string;
  owner?: string | null;
  due_at: string;
  repeat_days?: number | null;
  ref?: string | null;
  done_at?: string | null;
  last_reminded_at?: string | null;
  created_at: string;
};

export type Run = {
  thread_id: string;
  initiative_id: string;
  stage: string;
  workflow_id: string;
  status: string;
  reason?: string | null;
  started_at: string;
  ended_at?: string | null;
};

export type Story = {
  id: string;
  epic: string;
  team?: string | null;
  repo?: string | null;
  project_id?: string | null;
  title: string;
  criteria: string[];
  tasks: string[];
  depends_on: string[];
  estimate_days: number[];
  task_id?: string | null;
  task_status?: string | null;
  jira_key?: string | null;
};
export type Epic = {
  id: string;
  team?: string | null;
  title: string;
  stories: Story[];
};
export type PlanView = {
  version: number;
  ok: boolean;
  problems: string[];
  critical_path: string[];
  critical_days?: number | null;
  teams?: Record<string, number[]> | null;
  total_days?: number[] | null;
  epics: Epic[];
  progress?: Progress | null;
};

export type RepoOwner = {
  project_id: string;
  name: string;
  root: string;
  teams: string[];
};

export type Detail = {
  initiative: Initiative;
  stage: StageView;
  docs: Record<string, DocView>;
  questions: Question[];
  disagreements: Disagreement[];
  runs: Run[];
  history: ProductEvent[];
  follow_ups: FollowUp[];
  plan?: PlanView | null;
  repos: RepoOwner[];
  teams: string[];
};

export type TeamPath = {
  team_id: string;
  project_id: string;
  glob: string;
  source: string;
};
export type Team = {
  id: string;
  name: string;
  lead?: string | null;
  jira_project?: string | null;
  capacity_days?: number | null;
  members: string[];
  source: string;
  owns: TeamPath[];
  created_at: string;
  updated_at: string;
};
export type TeamPage = { name: string; title: string; text: string };
export type Suggestion = {
  id: string;
  team_id: string;
  page: string;
  text: string;
  source?: string | null;
  status: string;
  created_at: string;
};
export type TeamKnowledge = {
  team: Team;
  pages: TeamPage[];
  suggestions: Suggestion[];
};
export type TeamBody = {
  name?: string;
  lead?: string | null;
  jira_project?: string | null;
  capacity_days?: number | null;
  members?: string[];
  owns?: { project_id: string; glob: string }[];
};
export type ImportResult = {
  teams: string[];
  paths: number;
  read: string[];
  skipped: string[];
};

export type NewInitiative = {
  title: string;
  idea: string;
  why_now?: string;
  outcome_hope?: string;
  owner?: string;
  repos: string[];
  start?: boolean;
};
export type Handoff = {
  tasks: string[];
  jira: string[];
  skipped: string[];
  notes: string[];
};

const e = encodeURIComponent;
const I = (id: string) => `/initiatives/${e(id)}`;

export const productApi = {
  info: () =>
    get<{ version: string; project_id: string; root: string }>("/product"),
  board: () => get<BoardItem[]>("/initiatives"),
  repos: () => get<RepoOwner[]>("/initiatives/repos"),
  create: (b: NewInitiative) => post<Detail>("/initiatives", b),
  get: (id: string) => get<Detail>(I(id)),
  patch: (id: string, b: Partial<NewInitiative>) => patch<Detail>(I(id), b),
  start: (id: string, stage?: string) =>
    post<Detail>(`${I(id)}/start`, stage ? { stage } : {}),
  approve: (
    id: string,
    b: { why?: string; answers?: Record<string, string> } = {},
  ) => post<Detail>(`${I(id)}/approve`, b),
  sendBack: (id: string, note: string) =>
    post<Detail>(`${I(id)}/send-back`, { note }),
  decide: (
    id: string,
    b: {
      choice: "go" | "not_now";
      option?: string;
      why?: string;
      revisit?: string;
    },
  ) => post<Detail>(`${I(id)}/decide`, b),
  rerun: (id: string, b: { stage?: string; note?: string } = {}) =>
    post<Detail>(`${I(id)}/rerun`, b),
  stop: (id: string) => post<Detail>(`${I(id)}/stop`),
  ask: (id: string, b: { text: string; to: string; about?: string }) =>
    post<Question>(`${I(id)}/questions`, b),
  answer: (id: string, qid: string, answer: string) =>
    post<Question>(`${I(id)}/questions/${e(qid)}/answer`, { answer }),
  disagree: (
    id: string,
    b: { stage?: string; reason: string; proposal?: string; author?: string },
  ) => post<Disagreement>(`${I(id)}/disagreements`, b),
  settle: (id: string, did: string, b: { outcome: string; note?: string }) =>
    post<Disagreement>(`${I(id)}/disagreements/${e(did)}/settle`, b),
  rerunWithObjection: (id: string, did: string) =>
    post<Detail>(`${I(id)}/disagreements/${e(did)}/rerun`),
  addFollowUp: (
    id: string,
    b: { text: string; due_at: string; owner?: string },
  ) => post<FollowUp>(`${I(id)}/follow-ups`, b),
  doneFollowUp: (id: string, fid: string) =>
    post<FollowUp>(`${I(id)}/follow-ups/${e(fid)}/done`),
  park: (id: string, b: { revisit?: string; why?: string } = {}) =>
    post<Detail>(`${I(id)}/park`, b),
  unpark: (id: string) => post<Detail>(`${I(id)}/unpark`),
  released: (id: string) => post<Detail>(`${I(id)}/released`),
  outcome: (id: string, metric: string) =>
    post<Detail>(`${I(id)}/outcome`, { metric }),
  handoff: (id: string, target: "tasks" | "jira" | "both") =>
    post<Handoff>(`${I(id)}/handoff`, { target }),
  doc: (id: string, kind: string, version: number) =>
    get<ProductDoc>(`${I(id)}/docs/${e(kind)}/${version}`),
  deckUrl: (id: string, version?: number) =>
    `/api${I(id)}/deck${version ? `?version=${version}` : ""}`,
  deck: (id: string) => getText(`${I(id)}/deck`),
  rebuildDeck: (id: string) => post<ProductDoc>(`${I(id)}/deck`),
  teams: () => get<Team[]>("/teams"),
  team: (id: string) => get<TeamKnowledge>(`/teams/${e(id)}`),
  createTeam: (b: TeamBody) => post<Team>("/teams", b),
  updateTeam: (id: string, b: TeamBody) => patch<Team>(`/teams/${e(id)}`, b),
  deleteTeam: (id: string) => del<{ ok: boolean }>(`/teams/${e(id)}`),
  importCodeowners: () => post<ImportResult>("/teams/import-codeowners"),
  savePage: (id: string, page: string, text: string) =>
    put<TeamPage>(`/teams/${e(id)}/pages/${e(page)}`, { text }),
  decideSuggestion: (id: string, sid: string, accept: boolean) =>
    post<TeamKnowledge>(`/teams/${e(id)}/suggestions/${e(sid)}`, { accept }),
};
