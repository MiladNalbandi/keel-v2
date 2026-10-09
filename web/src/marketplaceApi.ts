// v0.16.0 the marketplace (docs/plugins/13-step4-contract.md §6, §12): search the catalogs, one plugin, what this keel
// has installed, install, update, roll back, remove, restart, sources and rules. The api passes the engine's answers on
// as they are (§12 "Engine routes: the shapes"); GET /api/plugins/installed adds `restart` (GET /api/plugins stays 0.15.4's catalog).

import { del, get, post, put } from "./api";

/** content: no code runs · web: adds pages (JavaScript in the browser) · code: runs code inside keel (03-security.md §3.1) */
export type Trust = "content" | "web" | "code";
export type Category =
  "code" | "review" | "knowledge" | "tickets" | "product" | "other";

/** The permissions a plugin asks for, as in its keel-plugin.yml. */
export type Permissions = {
  secrets?: string[];
  network?: string[];
  workspace?: "read" | "write" | string;
  agent_tools?: "read" | "act" | string;
  flows?: "start" | string;
  tables?: string[];
  pages?: boolean | string[];
  [key: string]: unknown;
};

export type Revoked = { version?: string; why?: string; fixed?: string | null };

/** One version of a plugin in a catalog (newest first). revoked: why, when it was revoked. */
export type CatalogVersion = {
  version: string;
  released?: string;
  requires?: { sdk?: number; keel?: string; plugins?: Record<string, string> };
  permissions?: Permissions;
  size?: number;
  revoked?: string | boolean | null;
  fits?: boolean;
  why_not?: string | null;
};

/** One plugin of a search (GET /api/marketplace): the engine's Hit. */
export type MarketHit = {
  name: string;
  title?: string;
  publisher?: string;
  publisher_title?: string;
  verified?: boolean;
  category?: string;
  summary?: string;
  tags?: string[];
  trust?: Trust | string;
  repo?: string;
  source?: string;
  latest?: string;
  /** the newest version that fits this keel */
  version?: string | null;
  permissions?: Permissions;
  fits?: boolean;
  why_not?: string | null;
  /** the installed version, or nothing */
  installed?: string | boolean | null;
  installed_from?: "image" | "marketplace" | "file" | null;
  /** a newer version that fits, or nothing */
  update?: string | boolean | null;
  revoked?: Revoked | string | null;
  old?: boolean;
  /** a hit says what it needs only on the one-plugin answer ({name: range}) */
  needs?: Record<string, string> | (string | { name: string })[];
};

export type SourceStatus = {
  id: string;
  ok?: boolean;
  problem?: string | null;
  old?: boolean;
};
export type MarketSearch =
  | {
      plugins?: MarketHit[];
      hits?: MarketHit[];
      sources?: (SourceStatus & Partial<Source>)[];
      categories?: string[];
    }
  | MarketHit[];

/** One plugin an install takes (the plan's step). needed_by: the plugin that needs it. */
export type PlanItem = {
  name: string;
  title?: string;
  version?: string;
  trust?: string;
  publisher?: string;
  publisher_title?: string;
  verified?: boolean;
  permissions?: Permissions;
  size?: number;
  needed_by?: string | null;
};

/** What installing it does: the plugins it installs (the needed ones first) and the ones it turns on. */
export type Plan = {
  name?: string;
  version?: string;
  title?: string;
  source?: string;
  install?: PlanItem[];
  turn_on?: string[];
  checks?: string[];
};

/** One plugin (GET /api/marketplace/{name}). plan: null when keel would refuse it (refused says why). */
export type MarketPlugin = MarketHit & {
  versions?: CatalogVersion[];
  checks?: (string | { name?: string; text?: string; ok?: boolean })[];
  plan?: Plan | PlanItem[] | null;
  refused?: { error: string; hint?: string | null } | null;
};

/** One installed plugin (GET /api/plugins/installed). status: loaded (runs now), restart (loads at the next start), off, left out
 *  (the next start leaves it out), removed (gone at the next start). */
export type InstalledPlugin = {
  name: string;
  title?: string;
  version?: string;
  loaded?: string | null;
  parts?: string[];
  from?: "image" | "marketplace" | "file" | string | null;
  source?: string;
  on?: boolean;
  status?: "loaded" | "restart" | "off" | "left out" | "removed" | string;
  problems?: (string | { error?: string })[];
  revoked?: Revoked | string | boolean | null;
  update?: string | boolean | null;
  previous?: string | null;
  image_version?: string | null;
  can_remove?: boolean;
  needed_by?: string[];
  needs?: Record<string, string>;
  publisher?: string;
  trust?: string | null;
  catalog?: string | null;
  permissions?: Permissions;
};

export type PluginProblem = {
  name: string;
  version?: string | null;
  dir?: string | null;
  error: string;
};
export type RestartState = {
  pending: boolean;
  scheduled: boolean;
  supervised: boolean;
  running: number;
};
/** What loads after a restart: each change, from the version now to the next one (null: none). */
export type Pending = {
  pending: boolean;
  changes?: { name: string; now: string | null; next: string | null }[];
  problem?: string;
};
export type PluginsView = {
  plugins: InstalledPlugin[];
  restart: RestartState;
  pending_restart?: Pending | boolean | null;
  problems: PluginProblem[];
  mode?: string;
  /** false: the engine did not answer; the list is what this run loaded */
  engine?: boolean;
};

export type Source = {
  id: string;
  title?: string;
  url: string;
  key?: string;
  on?: boolean;
  official?: boolean;
  ok?: boolean;
  problem?: string | null;
  old?: boolean;
  fetched_at?: string | null;
  plugins?: number;
  problems?: string[];
};
export type Rules = {
  agents_may_ask: boolean;
  allow_unverified: boolean;
  check_daily: boolean;
  restart_when_idle: boolean;
};
/** A set of plugins: missing = not in this keel, off = in it but off (the engine says both). */
export type PluginSet = {
  id: string;
  title?: string;
  summary?: string;
  plugins: string[];
  missing?: string[];
  off?: string[];
};
export type RestartAnswer = {
  restarting: boolean;
  scheduled: boolean;
  running: number;
};
export type ChangeAnswer = {
  name?: string;
  title?: string;
  version?: string;
  [key: string]: unknown;
};

/** What a plugin-install request carries for its Inbox card (the approval's payload). */
export type InstallRequest = {
  name: string;
  title?: string;
  version?: string | null;
  summary?: string | null;
  trust?: string | null;
  publisher?: string | null;
  verified?: boolean | null;
  permissions?: Permissions | null;
  needs?: string[];
  installs?: { name: string; title?: string | null; version?: string | null }[];
  reason?: string;
  source?: string;
  reasons?: {
    reason: string;
    source?: string;
    project?: string | null;
    at?: string;
  }[];
  /** approving updates it (it is installed in another version) */
  update?: boolean;
  /** an update's new permissions ("+ secrets: gitlab"), and the version it has now */
  more?: string[];
  installed?: string | null;
};

const e = encodeURIComponent;
const query = (p: Record<string, string>) => {
  const s = new URLSearchParams(
    Object.entries(p).filter(([, v]) => v),
  ).toString();
  return s ? `?${s}` : "";
};

/** The official catalog: only on or off can change. */
const forSave = (s: Source) =>
  s.official || s.id === "keel"
    ? { id: s.id, on: s.on !== false }
    : { id: s.id, title: s.title, url: s.url, key: s.key, on: s.on !== false };

export const marketApi = {
  search: (q = "", category = "") =>
    get<MarketSearch>(`/marketplace${query({ q, category })}`),
  plugin: (name: string) => get<MarketPlugin>(`/marketplace/${e(name)}`),
  refresh: (source?: string) =>
    post<{ sources?: Source[] }>(
      `/marketplace/refresh${query({ source: source ?? "" })}`,
    ),
  installed: () => get<PluginsView>("/plugins/installed"),
  /** It answers when the download and the checks are done. */
  install: (name: string, version?: string) =>
    post<ChangeAnswer>(
      "/plugins/install",
      version ? { name, version } : { name },
    ),
  installFile: (path: string) =>
    post<ChangeAnswer>("/plugins/install-file", { path }),
  update: (name: string, version?: string) =>
    post<ChangeAnswer>(
      `/plugins/${e(name)}/update`,
      version ? { version } : {},
    ),
  rollback: (name: string) =>
    post<ChangeAnswer>(`/plugins/${e(name)}/rollback`),
  setOn: (name: string, on: boolean) =>
    put<ChangeAnswer>(`/plugins/${e(name)}`, { on }),
  remove: (name: string, data: "keep" | "delete") =>
    del<ChangeAnswer>(`/plugins/${e(name)}?data=${data}`),
  restart: (now: boolean) =>
    post<RestartAnswer>("/plugins/restart", now ? { now: true } : {}),
  sources: () => get<{ sources?: Source[] } | Source[]>("/plugins/sources"),
  saveSources: (sources: Source[]) =>
    put<{ sources?: Source[] } | Source[]>("/plugins/sources", {
      sources: sources.map(forSave),
    }),
  rules: () => get<Rules>("/plugins/rules"),
  saveRules: (rules: Rules) => put<Rules>("/plugins/rules", rules),
  sets: () => get<{ sets?: PluginSet[] } | PluginSet[]>("/plugins/sets"),
};

// ---------- reading the answers ----------

export const hitsOf = (s: MarketSearch | null | undefined): MarketHit[] =>
  !s ? [] : Array.isArray(s) ? s : (s.plugins ?? s.hits ?? []);
export const sourceStatusOf = (
  s: MarketSearch | null | undefined,
): SourceStatus[] => (!s || Array.isArray(s) ? [] : (s.sources ?? []));
export const sourcesOf = (
  s: { sources?: Source[] } | Source[] | null | undefined,
): Source[] => (!s ? [] : Array.isArray(s) ? s : (s.sources ?? []));
export const setsOf = (
  s: PluginSet[] | { sets?: PluginSet[] } | null | undefined,
): PluginSet[] => (!s ? [] : Array.isArray(s) ? s : (s.sets ?? []));

const planItemsOf = (plan: MarketPlugin["plan"]): PlanItem[] =>
  !plan ? [] : Array.isArray(plan) ? plan : (plan.install ?? []);

/** What installing this plugin installs (its plan): the needed plugins first, then itself. */
export function planOf(p: MarketPlugin): PlanItem[] {
  const items = planItemsOf(p.plan).filter(
    (i) => i && typeof i.name === "string",
  );
  if (items.length) return items;
  return [
    {
      name: p.name,
      title: p.title,
      version: newestVersion(p),
      trust: typeof p.trust === "string" ? p.trust : undefined,
      permissions: permissionsOf(p),
      publisher: p.publisher,
      publisher_title: p.publisher_title,
      verified: p.verified,
    },
  ];
}

/** The plugins its plan turns on (in keel already, but off). */
export const turnOnOf = (p: MarketPlugin): string[] =>
  p.plan && !Array.isArray(p.plan) ? (p.plan.turn_on ?? []) : [];

/** The version an install takes: the plan's, else the newest that fits, else the newest listed. */
export function newestVersion(p: MarketPlugin): string | undefined {
  const plan = p.plan;
  if (plan && !Array.isArray(plan) && plan.version) return plan.version;
  const own = planItemsOf(plan).find((i) => i.name === p.name)?.version;
  return own ?? p.version ?? p.latest ?? p.versions?.[0]?.version;
}

/** The permissions of the version an install takes. */
export function permissionsOf(p: MarketPlugin): Permissions | undefined {
  const v = newestVersion(p);
  return (
    p.versions?.find((x) => x.version === v)?.permissions ??
    planItemsOf(p.plan).find((i) => i.name === p.name)?.permissions ??
    p.permissions
  );
}

/** The plugins it needs: the answer's {name: range}, else the version's requires.plugins (or a list of names). */
export function needsOf(p: MarketPlugin | MarketHit): string[] {
  const n = p.needs;
  if (n && !Array.isArray(n)) return Object.keys(n);
  if (Array.isArray(n))
    return n.map((x) => (typeof x === "string" ? x : x.name)).filter(Boolean);
  const v = (p as MarketPlugin).versions?.find(
    (x) => x.version === newestVersion(p as MarketPlugin),
  );
  return v?.requires?.plugins ? Object.keys(v.requires.plugins) : [];
}

export const installedVersion = (h: MarketHit): string | null =>
  h.installed === true
    ? ""
    : typeof h.installed === "string" && h.installed
      ? h.installed
      : null;

export const updateOf = (x: {
  update?: string | boolean | null;
}): string | null =>
  typeof x.update === "string" && x.update
    ? x.update
    : x.update === true
      ? "a newer version"
      : null;

export const publisherOf = (
  h: MarketHit | PlanItem,
): { name: string; verified: boolean } => ({
  name: h.publisher_title || h.publisher || "",
  verified: Boolean(h.verified),
});

/** Is a restart due: the engine's {pending, changes} (or true). */
export const pendingOf = (v: PluginsView): Pending | null => {
  const p = v.pending_restart;
  if (p && typeof p === "object") return p;
  return v.restart?.pending || p === true
    ? { pending: true, changes: [] }
    : null;
};

export function revokedText(
  r: InstalledPlugin["revoked"] | MarketHit["revoked"],
): string | null {
  if (!r) return null;
  if (typeof r === "string") return r;
  if (typeof r === "object")
    return (
      [r.why, r.fixed ? `${r.fixed} fixes it` : null]
        .filter(Boolean)
        .join(" · ") || "revoked"
    );
  return "revoked";
}

// ---------- words ----------

export const TRUST: Record<
  string,
  { label: string; tone: string; about: string }
> = {
  content: {
    label: "Content only",
    tone: "t-content",
    about:
      "Workflows, agents and skills. No code runs; agents still obey the guard and gates.",
  },
  web: {
    label: "Adds pages",
    tone: "t-web",
    about:
      "A web part runs as you in your browser: it can call keel's api as you.",
  },
  code: {
    label: "Runs code in keel",
    tone: "t-code",
    about:
      "Its code runs inside keel with keel's rights: files, secrets and network. Install it only from a publisher you trust.",
  },
};
export const trustOf = (t: string | null | undefined) =>
  t ? (TRUST[t] ?? { label: t, tone: "", about: "" }) : null;

export const CATEGORIES: [string, string][] = [
  ["", "All"],
  ["code", "Code"],
  ["knowledge", "Knowledge"],
  ["tickets", "Tickets"],
  ["review", "Review"],
  ["product", "Product"],
  ["other", "Other"],
];

export const FROM: Record<string, string> = {
  image: "in the image",
  marketplace: "marketplace",
  file: "a file (unsigned)",
};

export type Level = "high" | "medium" | "low";
export type PermissionLine = { level: Level; text: string; detail?: string };

const list = (v: unknown): string[] =>
  Array.isArray(v) ? v.map(String) : typeof v === "string" && v ? [v] : [];

/** A plugin's permissions in plain words, each with its level (03-security.md §3.3), the highest first. */
export function permissionLines(
  perms: Permissions | null | undefined,
): PermissionLine[] {
  if (!perms || typeof perms !== "object") return [];
  const out: PermissionLine[] = [];
  const secrets = list(perms.secrets);
  if (secrets.length)
    out.push({
      level: "high",
      text: `Read your ${secrets.join(", ")} connections`,
      detail: "keel gives it only the secrets of these kinds.",
    });
  const net = list(perms.network);
  if (net.length) {
    const hosts = net.map((h) =>
      h === "from-connections" ? "the hosts of your connections" : h,
    );
    out.push({
      level: "medium",
      text: `Talk to ${hosts.join(", ")}`,
      detail: "Its HTTP client may reach only these hosts.",
    });
  }
  if (perms.workspace) {
    out.push({
      level: "medium",
      text:
        perms.workspace === "write"
          ? "Read and change files in /workspace"
          : "Read files in /workspace",
    });
  }
  if (perms.agent_tools) {
    out.push({
      level: "medium",
      text:
        perms.agent_tools === "act"
          ? "Give agents tools that change things"
          : "Give agents tools that read",
      detail:
        perms.agent_tools === "act"
          ? "Each change still asks a person first."
          : undefined,
    });
  }
  if (perms.flows) out.push({ level: "medium", text: "Start flows by itself" });
  const tables = list(perms.tables);
  if (tables.length)
    out.push({
      level: "low",
      text: `Keep its own tables: ${tables.join(", ")}`,
    });
  if (perms.pages) out.push({ level: "low", text: "Add pages to the menu" });
  const known = new Set([
    "secrets",
    "network",
    "workspace",
    "agent_tools",
    "flows",
    "tables",
    "pages",
  ]);
  for (const [k, v] of Object.entries(perms)) {
    if (known.has(k) || v === null || v === undefined || v === false) continue;
    out.push({
      level: "medium",
      text: `${k}: ${list(v).join(", ") || String(v)}`,
    });
  }
  const rank: Record<Level, number> = { high: 0, medium: 1, low: 2 };
  return out.sort((a, b) => rank[a.level] - rank[b.level]);
}

/** Who asked, in words. */
export function whoAsked(source: string | null | undefined): string {
  switch ((source ?? "").toLowerCase()) {
    case "keelbot":
      return "KeelBot";
    case "workflow":
      return "A workflow";
    case "mcp":
      return "Claude Code";
    case "keel":
      return "keel";
    case "":
    case "agent":
      return "An agent";
    default:
      return source!;
  }
}
