// Rule packs in the marketplace (docs/plugins/23-rules-in-marketplace.md §3, §5): the third kind of Control › Plugins ›
// Marketplace. Search the catalogs' packs (what this project has, and updates), one pack (its verified file, keel's own
// reading of each rule and who enforces it), install into a project through the rules api (source "marketplace"),
// update (replace), remove (the rules api's uninstall). A pack is a file in the project's .keel/rules/: no restart.

import { get, post } from "./api";
import { e, query, type Refusal } from "./marketHttp";
import type { SourceStatus } from "./marketplaceApi";
import { ENGINE_TITLE, rulesApi, type Engine, type InstalledPack, type PackRule, type RulePack, type When } from "./rulesApi";

/** What each rule blocks, as the catalog lists it (and keel computes it again at fetch). */
export type PackBlock = {
  id: string;
  kind: string;
  match: string[];
  how: string;
  phases: string | string[];
  lanes?: string[];
  except?: string[];
};

/** A pack's review: the rules by kind, the phases and lanes they apply in, and what each blocks. */
export type RulePackReview = {
  rules: number;
  kinds: Record<string, number>;
  phases: string[];
  lanes: string[];
  /** a catalog built before 0.17.2's final format has no blocks */
  blocks?: PackBlock[];
};

export type { Refusal } from "./marketHttp";

/** One pack of a search (GET /api/marketplace/rule-packs). */
export type RulePackHit = {
  name: string;
  kind: "rules";
  title: string;
  publisher: string;
  publisher_title?: string;
  verified: boolean;
  /** the newest version this keel can use, or null */
  version: string | null;
  summary: string;
  category: string;
  tags?: string[];
  review: RulePackReview;
  requires?: { keel?: string; plugins?: Record<string, string> };
  fits: boolean;
  refused: Refusal | null;
  source: string;
  repo?: string;
  latest?: string;
  old?: boolean;
  why_not?: string | null;
  trust: "content";
  /** in this project (the rules api's list): its version, whether it was changed here, where it came from */
  installed: { version: string; edited: boolean; source: string } | null;
  /** a newer version that fits */
  update: string | null;
};

export type RulePackVersion = {
  version: string;
  released?: string;
  requires?: { keel?: string; plugins?: Record<string, string> };
  review?: RulePackReview;
  url?: string;
  size?: number;
  sha256?: string;
  revoked?: string | null;
  fits?: boolean;
  why_not?: string | null;
};

/** One mechanism that checks a rule: before the action (keel's ToolBox, keel's MCP guard) or after the step (the diff
 *  guard), and for which engines (#101 one: keel's harness). */
export type EnforcedBy = { when: "before" | "after"; who: string; label: string; engines: Engine[] };
export type RuleEnforced = {
  before: Engine[];
  after: boolean;
  engines: Record<Engine, When>;
  by: EnforcedBy[];
};
/** One rule as keel reads it (the rules work's parser), with who enforces it. */
export type ReadRule = Omit<PackRule, "enforced"> & { enforced: RuleEnforced };
export type EngineInfo = { engine: Engine; title: string; protection: string; words: string };

/** One pack (GET /api/marketplace/rule-packs/{name}). */
export type MarketRulePack = RulePackHit & {
  versions: RulePackVersion[];
  checks: string[];
  /** the verified file, exactly as published (null when keel would not fetch it) */
  yaml: string | null;
  /** keel's reading: the pack's header and each rule */
  pack?: Omit<RulePack, "rules">;
  rules: ReadRule[];
  engines?: EngineInfo[];
  enforcers?: { who: string; label: string }[];
  catalog_review?: RulePackReview;
  /** the fetch failed: a bad signature, a changed file, a review that is not the catalog's */
  fetch_error: Refusal | null;
};

export type RulePackSearch = {
  rules: RulePackHit[];
  sources: (SourceStatus & { title?: string })[];
  categories: string[];
};


export const rulePackMarketApi = {
  search: (q = "", category = "", project: string | null = null) =>
    get<RulePackSearch>(`/marketplace/rule-packs${query({ q, category, project })}`),
  pack: (name: string, project: string | null = null) =>
    get<MarketRulePack>(`/marketplace/rule-packs/${e(name)}${query({ project })}`),
  /** replace: an update over another installed version (the rules api answers 409 without it) */
  install: (pid: string, body: { name: string; version?: string; replace?: boolean }) =>
    post<{ pack: RulePack; installed: InstalledPack[]; market: { catalog: string; name: string; version: string } }>(
      `/projects/${e(pid)}/rule-packs/market/install`,
      body,
    ),
  remove: (pid: string, name: string) => rulesApi.uninstall(pid, name),
};

// ---------- reading the answers ----------

const KIND_ORDER = ["bash", "edit", "read", "mcp"];

/** "every phase", "in a flow", "green, refactor": the phases a pack (or a rule) applies in. */
export function phasesShort(phases: string | string[] | undefined): string {
  const list = Array.isArray(phases) ? phases : [phases ?? "all"];
  if (!list.length || list.includes("all")) return "every phase";
  const words = list.map((p) => ({ "in-flow": "in a flow", none: "outside a flow" })[p] ?? p);
  return words.join(", ");
}

/** "3 rules · bash 1 · edit 2 · every phase": a pack's review in short. */
export function packShort(r: RulePackReview | null | undefined): string {
  if (!r) return "";
  const rank = (k: string) => (KIND_ORDER.includes(k) ? KIND_ORDER.indexOf(k) : 99);
  const kinds = Object.entries(r.kinds ?? {})
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([k, n]) => `${k} ${n}`);
  return [`${r.rules} ${r.rules === 1 ? "rule" : "rules"}`, ...kinds, phasesShort(r.phases)].join(" · ");
}

/** "keel's ToolBox": one mechanism, from the api. keel's harness is the one engine, so it is not named after each
 *  mechanism; any other engine an api names still is ("the diff guard (X)"). */
export function byWords(b: EnforcedBy, engines?: EngineInfo[]): string {
  const title = (n: string) => engines?.find((x) => x.engine === n)?.title ?? ENGINE_TITLE[n] ?? n;
  const others = b.engines.filter((n) => n !== "harness");
  return others.length ? `${b.label} (${others.map(title).join(", ")})` : b.label;
}

/** Who enforces a rule, as two lines: before the action, after the step (empty when nothing checks it then). */
export function whoEnforces(r: ReadRule, engines?: EngineInfo[]): { before: string[]; after: string[] } {
  const by = r.enforced?.by ?? [];
  return {
    before: by.filter((b) => b.when === "before").map((b) => byWords(b, engines)),
    after: by.filter((b) => b.when === "after").map((b) => byWords(b, engines)),
  };
}

/** A pack's page in Control › Plugins. */
export const packHref = (name: string) => `#/settings/plugins/rules/${encodeURIComponent(name)}`;
/** The Marketplace tab with the Rules view. */
export const MARKET_RULES = "#/settings/plugins?tab=market&kind=rules";
