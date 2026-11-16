// 0.16.1 plugin track: the marketplace for workflows (docs/plugins/21-workflow-marketplace.md §3, §4). Search the
// catalogs' workflows, one workflow (its steps, YAML, versions and checks), install it into a project or all projects,
// update it (keep my copy or replace), remove it (DELETE /api/workflows/{wid}, as Delete does), and the publish package.

import { ApiError, api, get, post, type Step, type Workflow } from "./api";
import { e, query } from "./marketHttp";
import type { SourceStatus } from "./marketplaceApi";

/** What a workflow does, as the catalog's sync (and keel, again at install) computed it: the Install review. */
export type WorkflowReview = {
  steps: number;
  gates: number;
  agents: string[];
  mcp: string[];
  /** the shell commands its `run:` steps run */
  commands: string[];
  edits_files: boolean;
  keel_rules: boolean;
  /** keel's own review (the page's) may add these */
  warnings?: string[];
  est_tokens?: number;
};

/** One workflow of a search (GET /api/marketplace/workflows). */
export type WorkflowHit = {
  name: string;
  kind: "workflow";
  title: string;
  publisher: string;
  publisher_title?: string;
  verified: boolean;
  version: string;
  summary: string;
  category: string;
  tags?: string[];
  review: WorkflowReview;
  /** {plugin: version range} and the agents it names */
  needs: { plugins: Record<string, string>; agents: string[] };
  fits: boolean;
  /** why keel would not install it (revoked, expired, unverified and not allowed…) */
  refused: string | null;
  source: string;
  /** in this project (or all projects): the version, where, and the workflow's id here */
  installed: { version: string; scope: "project" | "all"; wid: string } | null;
  /** a newer version that fits */
  update: string | null;
};

export type WorkflowVersion = {
  version: string;
  released?: string;
  requires?: { keel?: string; plugins?: Record<string, string>; agents?: string[] };
  review?: WorkflowReview;
  url?: string;
  size?: number;
  sha256?: string;
};

/** One workflow (GET /api/marketplace/workflows/{name}): the hit, its versions, the checks keel runs, its YAML and steps. */
export type MarketWorkflow = WorkflowHit & {
  versions: WorkflowVersion[];
  checks: string[];
  yaml: string;
  steps: Step[];
};

export type WorkflowSearch = {
  workflows: WorkflowHit[];
  sources: (SourceStatus & { title?: string })[];
  categories: string[];
};

/** A 409 of install: what is missing (the plugin path installs plugins; keel never does it from here). */
export type InstallRefusal = {
  error: string;
  hint?: string;
  missing_plugins?: string[];
  missing_agents?: string[];
};

export type PublishInfo = {
  name: string;
  version: string;
  summary: string;
  category: string;
  publisher: string;
};


export const workflowMarketApi = {
  search: (q = "", category = "", project: string | null = null) =>
    get<WorkflowSearch>(
      `/marketplace/workflows${query({ q, category, project })}`,
    ),
  workflow: (name: string, project: string | null = null) =>
    get<MarketWorkflow>(`/marketplace/workflows/${e(name)}${query({ project })}`),
  install: (
    pid: string,
    body: { name: string; version?: string; scope: "project" | "all" },
  ) =>
    post<{ workflow: Workflow; review: WorkflowReview }>(
      `/projects/${e(pid)}/workflows/market/install`,
      body,
    ),
  update: (
    pid: string,
    wid: string,
    body: { version?: string; mode: "keep" | "replace" },
  ) =>
    post<{ workflow: Workflow }>(
      `/projects/${e(pid)}/workflows/market/${e(wid)}/update`,
      body,
    ),
  remove: (wid: string) => api.deleteWorkflow(wid),
  /** The package to publish: a zip with <name>-<version>.yaml and the marketplace entry workflows/<name>.yml. */
  publishUrl: (wid: string, p: PublishInfo) =>
    `/api/workflows/${e(wid)}/publish${query({ ...p })}`,
};

// ---------- reading the answers ----------

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;

/** "6 steps · 2 gates · runs 1 command": the review in short. */
export function reviewShort(r: WorkflowReview | null | undefined): string {
  if (!r) return "";
  const parts = [plural(r.steps ?? 0, "step"), plural(r.gates ?? 0, "gate")];
  const n = r.commands?.length ?? 0;
  if (n) parts.push(`runs ${plural(n, "command")}`);
  return parts.join(" · ");
}

export const runsCommands = (r: WorkflowReview | null | undefined) =>
  (r?.commands?.length ?? 0) > 0;

/** The plugins it needs, by name. */
export const neededPlugins = (h: Pick<WorkflowHit, "needs">): string[] =>
  Object.keys(h.needs?.plugins ?? {});

/** A refused install's fields (missing plugins and agents), or null when the error is another one. */
export function refusalOf(err: unknown): InstallRefusal | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const b = (err.body ?? {}) as Partial<InstallRefusal>;
  const list = (v: unknown) =>
    Array.isArray(v) ? v.map(String).filter(Boolean) : [];
  return {
    error: err.message,
    hint: err.hint,
    missing_plugins: list(b.missing_plugins),
    missing_agents: list(b.missing_agents),
  };
}

/** A workflow's page in Settings › Plugins. */
export const workflowHref = (name: string) =>
  `#/settings/plugins/workflow/${encodeURIComponent(name)}`;
/** The Marketplace tab with the Workflows view. */
export const MARKET_WORKFLOWS = "#/settings/plugins?tab=market&kind=workflows";

/** The categories a workflow can have, for the Publish dialog (the catalog may list more). */
export const WORKFLOW_CATEGORIES = [
  "code",
  "review",
  "testing",
  "docs",
  "release",
  "ops",
  "other",
];

/** A name the catalog accepts: lower case, digits and dashes. */
export const slugName = (s: string) =>
  s
    .toLowerCase()
    .replace(/\(keel\)/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
