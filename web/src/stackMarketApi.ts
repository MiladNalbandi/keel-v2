// Stacks in the marketplace (keel 0.18.9), the fifth kind of Settings › Plugins › Marketplace. Search the catalogs'
// stacks (what this project has, and updates) and keel's own (used where they are detected; a keel pack installs), one
// stack (its verified YAML, keel's review and keel's check against the project), install into a project as
// .keel/stacks/<name>.yml, update (replace), remove. No restart: keel reads a project's stacks when it needs them.

import { del, get, post, type Stack, type StackCheck } from "./api";
import { e, query, type Refusal } from "./marketHttp";
import type { SourceStatus } from "./marketplaceApi";

/** What keel (and the catalog) says a stack does: its lane, what detects it, its layers, every command and tool, its skills. */
export type StackReview = {
  lane: string;
  detect: Record<string, string[]>;
  layers: string[];
  commands: Record<string, string>;
  tools: { name: string; run: string; on: string; fail: string }[];
  skills: string[];
};

export type { Refusal } from "./marketHttp";

/** One stack of a catalog (GET /api/marketplace/stacks → stacks). */
export type StackHit = {
  name: string;
  kind: "stacks";
  title: string;
  publisher: string;
  publisher_title?: string;
  verified: boolean;
  version: string | null;
  summary: string;
  category: string;
  tags?: string[];
  review: StackReview;
  requires?: { keel?: string };
  fits: boolean;
  refused: Refusal | null;
  source: string;
  repo?: string;
  latest?: string;
  old?: boolean;
  why_not?: string | null;
  trust: "content";
  /** a marketplace install in this project: its version, whether it was changed here */
  installed: { version: string; edited: boolean; origin: string } | null;
  update: string | null;
  /** the project has its own stack (or an installed pack) with this name: an install replaces it */
  conflict: string | null;
};

/** One of keel's own stacks (content/stacks, content/packs). */
export type BuiltinStack = {
  name: string;
  lane: string;
  source: "keel" | "keel pack";
  detect: Record<string, string[]>;
  installable: boolean;
  builtin: true;
  publisher: "keel";
  trust: "content";
  /** this project uses it (detected, not turned off) */
  used: boolean;
  off: boolean;
  /** a keel pack installed in this project */
  installed: boolean;
};

export type StackSearch = {
  stacks: StackHit[];
  builtin: BuiltinStack[];
  sources: (SourceStatus & { title?: string })[];
  categories: string[];
  catalog_error: Refusal | null;
};

export type StackVersion = { version: string; released?: string; review?: StackReview; revoked?: string | null; fits?: boolean; why_not?: string | null };

/** One stack (GET /api/marketplace/stacks/{name}): a catalog's, or keel's own (builtin). */
export type MarketStack = Omit<StackHit, "installed"> & {
  installed: StackHit["installed"] | boolean;
  builtin: boolean;
  versions?: StackVersion[];
  checks?: string[];
  /** the verified YAML, exactly as published (null when keel would not fetch it) */
  yaml: string | null;
  catalog_review?: StackReview;
  fetch_error: Refusal | null;
  /** keel's check against the project (with a project) */
  check: StackCheck | null;
  /** keel's own only */
  lane?: string;
  installable?: boolean;
  used?: boolean;
  off?: boolean;
};


export const stackMarketApi = {
  search: (q = "", category = "", project: string | null = null) => get<StackSearch>(`/marketplace/stacks${query({ q, category, project })}`),
  stack: (name: string, project: string | null = null) => get<MarketStack>(`/marketplace/stacks/${e(name)}${query({ project })}`),
  /** replace: over another version, or over the project's own stack with that name (409 without it) */
  install: (pid: string, body: { name: string; version?: string; replace?: boolean }) =>
    post<{ stack: Stack; path: string; market: { catalog: string; name: string; version: string }; review: StackReview; warnings: string[] }>(
      `/projects/${e(pid)}/stacks/market/install`, body),
  /** a keel pack into the project (its stack file and its skills) */
  installPack: (pid: string, name: string) => post<Stack>(`/projects/${e(pid)}/stacks/${e(name)}/install`),
  remove: (pid: string, name: string) => del<{ name: string; removed: string }>(`/projects/${e(pid)}/stacks/${e(name)}`),
};

/** "api · 3 layers · 6 commands · 2 tools". */
export function stackShort(r: StackReview | null | undefined): string {
  if (!r) return "";
  const n = (k: number, w: string) => `${k} ${w}${k === 1 ? "" : "s"}`;
  return [r.lane, n(r.layers?.length ?? 0, "layer"), n(Object.keys(r.commands ?? {}).length, "command"), n(r.tools?.length ?? 0, "tool")]
    .filter(Boolean).join(" · ");
}

/** A stack's page in Settings › Plugins. */
export const stackHref = (name: string) => `#/settings/plugins/stacks/${encodeURIComponent(name)}`;
/** The Marketplace tab with the Stacks view. */
export const MARKET_STACKS = "#/settings/plugins?tab=market&kind=stacks";
