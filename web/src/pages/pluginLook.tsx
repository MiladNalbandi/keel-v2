// v0.16.1 how Settings › Plugins draws a plugin (docs/plugins/mockup.html): its small code-style icon, its publisher with
// the verified tick, the marketplace's category chips, and which plugin lives inside another one's page (Git, Code
// Review, CI/CD and Database in Code; Jira in Tasks).
//
// "Inside" comes from the slots first: a plugin whose piece sits in a host page's slot (code.activity, code.tab) lives
// in that page. keel's own set adds what its slots do not say (CI/CD shows in Code and Jobs, Jira fills Tasks).

import type { ReactNode } from "react";
import { slotItems, useSlot } from "../sdk/registry";
import { SLOTS } from "../sdk/slots";
import type { InstalledPlugin, MarketHit } from "../marketplaceApi";

/** keel's own plugins, in the order the page lists them: icon text, icon colour, the chip they show under, the host
 *  page they live in. */
const KEEL_SET: Record<
  string,
  { ic: string; tone: string; cat: Chip; inside?: string }
> = {
  code: { ic: "{ }", tone: "", cat: "code" },
  git: { ic: "git", tone: "c3", cat: "code", inside: "code" },
  review: { ic: "CR", tone: "c2", cat: "code", inside: "code" },
  db: { ic: "DB", tone: "c4", cat: "code", inside: "code" },
  ci: { ic: "CI", tone: "c3", cat: "code", inside: "code" },
  keelbot: { ic: "KB", tone: "", cat: "assist" },
  map: { ic: "MAP", tone: "c2", cat: "know" },
  graph: { ic: "GR", tone: "c2", cat: "know" },
  wiki: { ic: "WK", tone: "c3", cat: "know" },
  tasks: { ic: "TK", tone: "c4", cat: "tickets" },
  jira: { ic: "JR", tone: "c2", cat: "tickets", inside: "tasks" },
  product: { ic: "PRD", tone: "", cat: "product" },
};

/** The slots that are a host page's: a piece in one of them lives in that page. */
const HOST_SLOTS: [string, string][] = [
  [SLOTS.codeActivity, "code"],
  [SLOTS.codeTab, "code"],
];

const TONES = ["", "c2", "c3", "c4"];

/** Is this one of keel's own plugins (the keel set)? */
export const isKeelSet = (name: string) => name in KEEL_SET;

/** The page's order: keel's set as the mockup lists it, then the others by name. */
export function byKeelOrder(a: string, b: string): number {
  const keys = Object.keys(KEEL_SET);
  const ia = keys.indexOf(a);
  const ib = keys.indexOf(b);
  if (ia >= 0 || ib >= 0)
    return (ia < 0 ? keys.length : ia) - (ib < 0 ? keys.length : ib);
  return a.localeCompare(b);
}

/** The icon's text: the catalog's own (a few letters), keel's set, else the title's first letters. */
function iconText(name: string, title?: string, icon?: unknown): string {
  if (typeof icon === "string" && icon.trim() && icon.trim().length <= 4)
    return icon.trim();
  const own = KEEL_SET[name]?.ic;
  if (own) return own;
  const words = (title || name).split(/[^A-Za-z0-9]+/).filter(Boolean);
  const text =
    words.length > 1
      ? words
          .slice(0, 3)
          .map((w) => w[0])
          .join("")
      : (words[0] ?? name).slice(0, 3);
  return text.toUpperCase();
}

function toneOf(name: string): string {
  const own = KEEL_SET[name];
  if (own) return own.tone;
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return TONES[h % TONES.length];
}

/** A plugin's small code-style icon: "{ }", "git", "DB"… */
export function PluginIcon({
  name,
  title,
  icon,
  size,
}: {
  name: string;
  title?: string;
  icon?: unknown;
  size?: "sm" | "lg";
}) {
  return (
    <span
      className={`mk-ico ${toneOf(name)}${size ? ` ${size}` : ""}`}
      aria-hidden="true"
    >
      {iconText(name, title, icon)}
    </span>
  );
}

/** keel's own mark, for the keel core row. */
export const KeelMark = () => (
  <span className="mk-ico sm" aria-hidden="true">
    <svg viewBox="0 0 120 68" width="18" aria-hidden="true">
      <path
        fill="var(--accent)"
        fillRule="evenodd"
        d="M4 0H116Q120 0 119.4 4C118 22 92 38 66.6 50Q64 51.5 64 54L62.2 64.5Q61.8 67 60 67Q58.2 67 57.8 64.5L56 54Q56 51.5 53.4 50C28 38 2 22 .6 4Q0 0 4 0ZM58.7 0H61.3V57.7A1.3 1.3 0 0 1 58.7 57.7Z"
      />
    </svg>
  </span>
);

/** "keel ✓" for a verified publisher, "ana-k community" for the others. */
export function Publisher({
  name,
  verified,
}: {
  name: string;
  verified: boolean;
}): ReactNode {
  if (!name) return null;
  return (
    <>
      <span>{name}</span>
      {verified ? (
        <span className="mk-tick" title="Verified publisher">
          ✓
        </span>
      ) : (
        <span className="tag">community</span>
      )}
    </>
  );
}

/** A version that is a pre-release (0.3.0-beta.1) is a beta. */
export const isBeta = (v?: string | null) => !!v && /-/.test(v);

// ---------- the marketplace's chips ----------

export type Chip =
  | "all"
  | "code"
  | "assist"
  | "know"
  | "tickets"
  | "product"
  | "community";

export const CHIPS: [Chip, string][] = [
  ["all", "All"],
  ["code", "Code"],
  ["assist", "Assistant"],
  ["know", "Knowledge"],
  ["tickets", "Tickets"],
  ["product", "Product"],
  ["community", "Community"],
];

/** Made by keel (its own set, or the publisher keel), or by the community. */
export const fromKeel = (h: { name: string; publisher?: string | null }) =>
  (h.publisher ?? "") === "keel" || (!h.publisher && isKeelSet(h.name));

/** The catalog's category (code, review, knowledge, tickets, product, other) as one of the page's chips. */
function chipOf(h: MarketHit): Chip | null {
  if (fromKeel(h) && KEEL_SET[h.name]) return KEEL_SET[h.name].cat;
  const tags = (h.tags ?? []).map((t) => t.toLowerCase());
  switch (h.category) {
    case "code":
    case "review":
      return "code";
    case "knowledge":
      return "know";
    case "tickets":
      return "tickets";
    case "product":
      return "product";
  }
  if (tags.some((t) => t === "assistant" || t === "chat")) return "assist";
  return null;
}

/** Does this plugin show under this chip? */
export function inChip(h: MarketHit, chip: Chip): boolean {
  if (chip === "all") return true;
  if (chip === "community") return !fromKeel(h);
  return chipOf(h) === chip;
}

// ---------- which plugin lives inside another one's page ----------

/** name → the plugin whose page it lives in, for the plugins of this list (live: a piece that registers later counts). */
export function useInside(plugins: { name: string }[]): Map<string, string> {
  // the hooks make the page draw again when a part puts a piece in a host slot
  useSlot(SLOTS.codeActivity);
  useSlot(SLOTS.codeTab);
  return insideOf(plugins);
}

export function insideOf(plugins: { name: string }[]): Map<string, string> {
  const here = new Set(plugins.map((p) => p.name));
  const out = new Map<string, string>();
  for (const [slot, host] of HOST_SLOTS) {
    for (const item of slotItems(slot)) {
      const owner = (item as { plugin?: string }).plugin;
      if (owner && owner !== host && here.has(owner) && !out.has(owner))
        out.set(owner, host);
    }
  }
  for (const p of plugins) {
    const host = KEEL_SET[p.name]?.inside;
    if (!out.has(p.name) && host && here.has(host)) out.set(p.name, host);
  }
  for (const [name, host] of out) if (!here.has(host)) out.delete(name);
  return out;
}

/** The installed plugins in the table's order: each host followed by the plugins that live inside it. */
export function nested(
  plugins: InstalledPlugin[],
  inside: Map<string, string>,
): { p: InstalledPlugin; parent?: string; kids: number }[] {
  const sorted = [...plugins].sort((a, b) => byKeelOrder(a.name, b.name));
  const kids = (name: string) =>
    sorted.filter((x) => inside.get(x.name) === name);
  const out: { p: InstalledPlugin; parent?: string; kids: number }[] = [];
  for (const p of sorted) {
    if (inside.has(p.name)) continue;
    const mine = kids(p.name);
    out.push({ p, kids: mine.length });
    for (const k of mine) out.push({ p: k, parent: p.name, kids: 0 });
  }
  return out;
}

// ---------- what a part brings, in words ----------

export const PART_WORDS: Record<string, string> = {
  engine: "Engine code: tools for agents and actions for flows",
  api: "API code: its own endpoints",
  web: "Pages and panels in keel's web",
  content: "Workflows, agents, skills or commands",
  migrations: "Its own tables in keel's database, with their own history",
};

/** For a plugin keel does not have yet: what its trust level lets it bring. */
export const TRUST_PARTS: Record<string, string[]> = {
  content: ["content"],
  web: ["web", "content"],
  code: ["engine", "api", "web", "content"],
};

/** The pieces a plugin's web part put in keel's slots, in words ("Code › Database"). */
export function piecesOf(name: string): [string, string][] {
  const where: [string, string][] = [
    [SLOTS.codeActivity, "Code"],
    [SLOTS.jobsTab, "Jobs"],
    [SLOTS.connectionsKind, "Connections"],
    [SLOTS.settingsSection, "Settings"],
    [SLOTS.toolsCard, "Tools"],
  ];
  const out: [string, string][] = [];
  for (const [slot, page] of where)
    for (const item of slotItems(slot)) {
      const owner = (item as { plugin?: string }).plugin;
      if (owner === name && item.title)
        out.push(["web", `${page} › ${item.title}`]);
    }
  return out;
}
