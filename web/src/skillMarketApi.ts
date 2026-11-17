// Skills in the marketplace (keel 0.18.6), the fourth kind of Settings › Plugins › Marketplace. Search the catalogs'
// skills (what this project has, and updates) and keel's own (in every project already), one skill (its verified
// SKILL.md and keel's review), install into a project for the agents the person picks (source "marketplace"), update
// (replace), remove. A skill is one of the project's skills: no restart, an agent reads it at its next run.

import { del, get, post, put, type Skill } from "./api";
import { e, query, type Refusal } from "./marketHttp";
import { k1 } from "./format";
import type { SourceStatus } from "./marketplaceApi";

/** What keel (and the catalog) says a skill is: its size in tokens, its ## sections, what it is for. */
export type SkillReview = { tokens: number; sections: string[]; description: string };

export type { Refusal } from "./marketHttp";

/** One skill of a catalog (GET /api/marketplace/skills → skills). */
export type SkillHit = {
  name: string;
  kind: "skills";
  title: string;
  publisher: string;
  publisher_title?: string;
  verified: boolean;
  /** the newest version this keel can use, or null */
  version: string | null;
  summary: string;
  category: string;
  tags?: string[];
  review: SkillReview;
  requires?: { keel?: string };
  fits: boolean;
  refused: Refusal | null;
  source: string;
  repo?: string;
  latest?: string;
  old?: boolean;
  why_not?: string | null;
  trust: "content";
  /** in this project: its version, whether it was changed here, its skill id, the agents that load it */
  installed: { version: string; edited: boolean; sid: string; agents: string[] } | null;
  update: string | null;
};

/** One of keel's own skills (content/skills, the keel packs'): in every project; the tab puts it on agents. */
export type BuiltinSkill = {
  name: string;
  title: string;
  summary: string;
  source: string;
  kind: string;
  category: string;
  stack: string;
  version: string;
  tokens: number;
  agents: string[];
  when: string;
  enabled: boolean;
  refs: number;
  builtin: true;
  publisher: "keel";
  trust: "content";
};

export type SkillSearch = {
  skills: SkillHit[];
  builtin: BuiltinSkill[];
  sources: (SourceStatus & { title?: string })[];
  categories: string[];
  /** no catalog answered: keel's own are still listed */
  catalog_error: Refusal | null;
};

export type SkillVersion = {
  version: string;
  released?: string;
  requires?: { keel?: string };
  review?: SkillReview;
  size?: number;
  revoked?: string | null;
  fits?: boolean;
  why_not?: string | null;
};

/** One skill (GET /api/marketplace/skills/{name}): a catalog's, or keel's own (builtin). */
export type MarketSkill = Omit<SkillHit, "installed" | "update"> & {
  installed: SkillHit["installed"];
  update: string | null;
  builtin: boolean;
  versions?: SkillVersion[];
  checks?: string[];
  /** the verified SKILL.md, exactly as published (null when keel would not fetch it) */
  text: string | null;
  catalog_review?: SkillReview;
  fetch_error: Refusal | null;
  /** keel's own only: the agents that load it here, when, on or off, and its references */
  agents?: string[];
  when?: string;
  enabled?: boolean;
  refs?: { path: string; tokens: number }[];
};


export const skillMarketApi = {
  search: (q = "", category = "", project: string | null = null) =>
    get<SkillSearch>(`/marketplace/skills${query({ q, category, project })}`),
  skill: (name: string, project: string | null = null) => get<MarketSkill>(`/marketplace/skills/${e(name)}${query({ project })}`),
  /** agents: put it on them; replace: an update over another installed version (409 without it) */
  install: (pid: string, body: { name: string; version?: string; agents?: string[]; replace?: boolean }) =>
    post<{ skill: Skill; market: { catalog: string; name: string; version: string }; review: SkillReview }>(
      `/projects/${e(pid)}/skills/market/install`,
      body,
    ),
  /** the agents a skill is on (keel's own too) */
  agents: (pid: string, sid: string, agents: string[]) => put<Skill>(`/projects/${e(pid)}/skills/${e(sid)}`, { agents }),
  remove: (pid: string, sid: string) => del<{ removed: string }>(`/projects/${e(pid)}/skills/${e(sid)}`),
};

/** "1.2k tokens · 3 sections". */
export function skillShort(r: SkillReview | null | undefined): string {
  if (!r) return "";
  const t = k1(r.tokens);
  const n = r.sections?.length ?? 0;
  return `${t} tokens · ${n} ${n === 1 ? "section" : "sections"}`;
}

/** A skill's page in Settings › Plugins. */
export const skillHref = (name: string) => `#/settings/plugins/skills/${encodeURIComponent(name)}`;
/** The Marketplace tab with the Skills view. */
export const MARKET_SKILLS = "#/settings/plugins?tab=market&kind=skills";
