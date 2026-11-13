// Rule packs in the marketplace: MSW handlers in the api's shapes (docs/plugins/23-rules-in-marketplace.md §5): search
// (GET /api/marketplace/rule-packs, with what the project has), one pack (GET /api/marketplace/rule-packs/{name}: the
// verified YAML and keel's reading of each rule with who enforces it), install into a project
// (POST /api/projects/{pid}/rule-packs/market/install, the rules api's 409 without replace) and the rules api's list and
// uninstall. An install lands in the rules db, so Build › Rules lists it "from the marketplace". They come before the
// plugin marketplace's handlers: /api/marketplace/:name would take "rule-packs".

import { http, HttpResponse } from "msw";
import type { MarketRulePack, ReadRule, RulePackHit, RulePackReview } from "../rulePackMarketApi";
import type { RulePack } from "../rulesApi";
import type { RulesDb } from "./rulesHandlers";

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));

// #101 one engine: keel's harness. Its ToolBox (edits, reads, shell commands) and MCP guard check before the action;
// the diff guard checks an edit rule again after the step.
const ENGINES = [
  { engine: "harness", title: "keel's harness", protection: "before", words: "every edit, shell command, read and MCP call is checked before it runs" },
] as MarketRulePack["engines"];

const TOOLBOX = { when: "before" as const, who: "toolbox", label: "keel's ToolBox", engines: ["harness"] as const };
const MCP = { when: "before" as const, who: "mcp-guard", label: "keel's MCP guard", engines: ["harness"] as const };
const DIFF = { when: "after" as const, who: "diff-guard", label: "the diff guard", engines: ["harness"] as const };
const BEFORE = { before: ["harness"], engines: { harness: "before" } };

const DEPS_RULES = [
  {
    id: "no-install", kind: "bash", match: ["npm install", "pnpm add"], how: "contains", phases: "all",
    reason: "Adding a package is a person's decision.",
    enforced: { ...BEFORE, after: false, by: [TOOLBOX] },
  },
  {
    id: "no-lockfile", kind: "edit", match: ["**/package-lock.json"], how: "glob", phases: ["green", "refactor"], lanes: ["web"],
    except: ["web/fixtures/**"], reason: "The lock file follows package.json.",
    enforced: { ...BEFORE, after: true, by: [TOOLBOX, DIFF] },
  },
  {
    id: "no-manifest", kind: "edit", match: ["**/package.json"], how: "glob", phases: "all", reason: "A person changes the dependencies.",
    enforced: { ...BEFORE, after: true, by: [TOOLBOX, DIFF] },
  },
] as unknown as ReadRule[];

const depsReview: RulePackReview = {
  rules: 3, kinds: { bash: 1, edit: 2 }, phases: ["all", "green", "refactor"], lanes: ["web"],
  blocks: DEPS_RULES.map((r) => ({ id: r.id, kind: r.kind, match: r.match, how: r.how, phases: r.phases, ...(r.lanes ? { lanes: r.lanes } : {}) })),
};

export const DEPS_YAML = `schema: 1
kind: rules
name: no-new-dependencies
title: No new dependencies
version: 1.1.0
publisher: keel
summary: Agents never add a package, inside or outside a flow.
requires:
  keel: ">=0.17.2"
rules:
  - id: no-install
    kind: bash
    match: [npm install, pnpm add]
    reason: Adding a package is a person's decision.
`;

const GIT_RULES = [
  {
    id: "no-force", kind: "bash", match: ["git push --force"], how: "contains", phases: "in-flow", reason: "History stays as it is.",
    enforced: { ...BEFORE, after: false, by: [TOOLBOX] },
  },
  {
    id: "no-gh-merge", kind: "mcp", match: ["merge_pull_request"], how: "name", phases: "all", reason: "A person merges.",
    enforced: { ...BEFORE, after: false, by: [MCP] },
  },
] as unknown as ReadRule[];

type Entry = MarketRulePack;

function catalog(): Entry[] {
  return [
    {
      name: "no-new-dependencies", kind: "rules", title: "No new dependencies", publisher: "keel", publisher_title: "keel",
      verified: true, version: "1.1.0", summary: "Agents never add a package, inside or outside a flow.", category: "safety",
      tags: ["dependencies"], review: depsReview, requires: { keel: ">=0.17.2" }, fits: true, refused: null, source: "keel",
      latest: "1.1.0", old: false, why_not: null, trust: "content", installed: null, update: null,
      versions: [
        { version: "1.1.0", released: "2026-10-16", requires: { keel: ">=0.17.2" }, review: depsReview, size: 1500, fits: true },
        { version: "1.0.0", released: "2026-10-01", requires: { keel: ">=0.17.2" },
          review: { rules: 1, kinds: { bash: 1 }, phases: ["all"], lanes: [] }, size: 900, fits: true },
      ],
      checks: ["the file's signature matches a key of its publisher", "keel computes the review again: it must be the catalog's"],
      yaml: DEPS_YAML, rules: DEPS_RULES, engines: ENGINES,
      pack: { id: "no-new-dependencies", name: "no-new-dependencies", title: "No new dependencies", version: "1.1.0", publisher: "keel",
        summary: "Agents never add a package, inside or outside a flow.", requires: { keel: ">=0.17.2", plugins: {} } },
      fetch_error: null,
    },
    {
      name: "strict-git", kind: "rules", title: "Strict git", publisher: "bo", publisher_title: "bo", verified: false,
      version: "1.2.0", summary: "No force push in a flow; a person merges.", category: "git",
      review: { rules: 2, kinds: { bash: 1, mcp: 1 }, phases: ["in-flow", "all"], lanes: [] }, fits: true, refused: null,
      source: "keel", trust: "content", installed: null, update: null,
      versions: [{ version: "1.2.0", requires: { keel: ">=0.17.2" } }, { version: "1.0.0" }],
      checks: [], yaml: "schema: 1\nname: strict-git\n", rules: GIT_RULES, engines: ENGINES, fetch_error: null,
    },
    {
      name: "future-pack", kind: "rules", title: "Future pack", publisher: "keel", publisher_title: "keel", verified: true,
      version: null, latest: "2.0.0", summary: "Needs a newer keel.", category: "other",
      review: { rules: 1, kinds: { read: 1 }, phases: ["all"], lanes: [] }, fits: false,
      refused: { error: "No version of Future pack can be installed: needs keel >=9.0.0.", hint: "Wait for a new version, or update keel." },
      source: "keel", trust: "content", installed: null, update: null, versions: [{ version: "2.0.0", fits: false }], checks: [],
      yaml: null, rules: [], fetch_error: null,
    },
  ];
}

export function createMarketRulePacks() {
  return {
    catalog: catalog(),
    sources: [{ id: "keel", title: "keel official", ok: true }] as { id: string; title?: string; ok?: boolean; problem?: string | null }[],
    /** the api answers 404 (an older api: no rule pack catalog) */
    down: false,
    /** a pack whose fetch fails (a bad signature): its page says why */
    badSignature: null as string | null,
    searches: [] as { q: string; category: string; project: string }[],
  };
}
export type MarketRulePacks = ReturnType<typeof createMarketRulePacks>;

const newer = (a: string, b: string) => {
  const n = (v: string) => v.split("-")[0].split(".").map(Number);
  const [x, y] = [n(a), n(b)];
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i];
  return false;
};

/** A pack the rules db has: installed {version, edited, source} and update. */
function decorate<T extends RulePackHit>(e: T, rules: RulesDb): T {
  const mine = rules.installed.find((p) => p.id === e.name);
  const installed = mine ? { version: mine.version, edited: !!mine.edited, source: mine.source ?? "file" } : null;
  const update = installed && e.version && newer(e.version, installed.version) ? e.version : null;
  return { ...clone(e), installed, update };
}

const hitOf = (e: Entry): RulePackHit => {
  const { versions: _v, checks: _c, yaml: _y, rules: _r, engines: _e, pack: _p, fetch_error: _f, ...hit } = e;
  return hit;
};

export function marketRulePackHandlers(
  m: MarketRulePacks,
  rules: RulesDb,
  log: (req: Request) => Promise<Record<string, unknown>>,
) {
  return [
    http.get("/api/marketplace/rule-packs", ({ request }) => {
      if (m.down) return HttpResponse.json({ error: "Not found" }, { status: 404 });
      const u = new URL(request.url);
      const q = (u.searchParams.get("q") ?? "").toLowerCase();
      const category = u.searchParams.get("category") ?? "";
      m.searches.push({ q, category, project: u.searchParams.get("project") ?? "" });
      const hits = m.catalog
        .filter((e) => (!category || e.category === category) &&
          (!q || [e.name, e.title, e.summary, ...(e.tags ?? [])].some((s) => s.toLowerCase().includes(q))))
        .map((e) => decorate(hitOf(e), rules));
      return HttpResponse.json({ rules: hits, sources: m.sources, categories: ["safety", "code", "git", "files", "secrets", "other"] });
    }),
    http.get("/api/marketplace/rule-packs/:name", ({ params }) => {
      const e = m.catalog.find((x) => x.name === params.name);
      if (!e) return HttpResponse.json({ error: `There is no rule pack ${String(params.name)} in the catalogs keel reads.` }, { status: 404 });
      const out = decorate(e, rules);
      if (m.badSignature === e.name) {
        return HttpResponse.json({ ...out, yaml: null, rules: [], fetch_error: {
          error: `The signature of ${e.name} ${e.version} does not match its publisher's key: the signature is not the publisher's.`,
          hint: "keel installed nothing." } });
      }
      return HttpResponse.json(out);
    }),
    http.post("/api/projects/:pid/rule-packs/market/install", async ({ request }) => {
      const b = await log(request);
      const e = m.catalog.find((x) => x.name === b.name);
      if (!e) return HttpResponse.json({ error: `There is no rule pack ${String(b.name)}.` }, { status: 404 });
      if (m.badSignature === e.name) {
        return HttpResponse.json({ error: `The signature of ${e.name} ${e.version} does not match its publisher's key.`, hint: "keel installed nothing." }, { status: 502 });
      }
      const version = (b.version as string | undefined) ?? e.version ?? "";
      const have = rules.installed.find((p) => p.id === e.name);
      if (have && have.version === version && !b.replace) return HttpResponse.json({ error: `${e.name} ${version} is installed already.`, hint: null }, { status: 409 });
      if (have && !b.replace) {
        return HttpResponse.json({ error: `${e.name} ${have.version} is installed; installing ${version} replaces it (replace).`, hint: null }, { status: 409 });
      }
      const pack: RulePack = {
        id: e.name, name: e.name, title: e.title, version, publisher: e.publisher, summary: e.summary, source: "marketplace",
        requires: { keel: e.requires?.keel ?? "", plugins: {} },
        rules: e.rules.map((r) => ({ id: r.id, kind: r.kind, match: r.match, how: r.how, phases: r.phases, reason: r.reason, enforced: r.enforced.engines })),
      };
      rules.installed = [...rules.installed.filter((p) => p.id !== e.name), pack];
      return HttpResponse.json({ pack, installed: rules.installed, market: { catalog: e.source, name: e.name, version } });
    }),
    http.get("/api/projects/:pid/rule-packs", () =>
      HttpResponse.json(rules.installed.map((p) => ({ name: p.id, title: p.title, version: p.version, publisher: p.publisher,
        summary: p.summary, requires: p.requires, source: p.source ?? "file", file: `.keel/rules/${p.id}.yaml`, off: false,
        ...(p.edited ? { edited: true } : {}) })))),
    http.post("/api/projects/:pid/rule-packs/uninstall", async ({ request }) => {
      const b = await log(request);
      if (!rules.installed.some((p) => p.id === b.name)) return HttpResponse.json({ error: `This project has no rule pack ${String(b.name)}.` }, { status: 404 });
      rules.installed = rules.installed.filter((p) => p.id !== b.name);
      return HttpResponse.json({ installed: [] });
    }),
  ];
}
