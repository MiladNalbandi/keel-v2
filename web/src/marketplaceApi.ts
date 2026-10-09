// v0.16.0 the marketplace (docs/plugins/13-step4-contract.md §6): search the catalogs, one plugin, what this keel has
// installed, install, update, roll back, remove, restart, sources and rules. The api passes the engine's answers on as
// they are, so the readers below accept the shapes the engine may send (a list, or an object that holds it).

import { del, get, post, put } from "./api";

/** content: no code runs · web: adds pages (JavaScript in the browser) · code: runs code inside keel (03-security.md §3.1) */
export type Trust = "content" | "web" | "code";
export type Category =
  "code" | "knowledge" | "tickets" | "review" | "product" | "other";

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

export type CatalogVersion = {
  version: string;
  released?: string;
  requires?: { sdk?: number; keel?: string; plugins?: Record<string, string> };
  permissions?: Permissions;
  size?: number;
  revoked?: boolean | string | null;
};

/** One hit of a search (GET /api/marketplace). */
export type MarketHit = {
  name: string;
  title?: string;
  publisher?: string | { name?: string; title?: string; verified?: boolean };
  verified?: boolean;
  category?: string;
  summary?: string;
  tags?: string[];
  trust?: Trust | string;
  version?: string;
  latest?: string;
  /** false, true or the installed version */
  installed?: boolean | string | null;
  /** a newer version that fits, or nothing */
  update?: string | boolean | null;
  fits?: boolean;
  needs?: (string | { name: string })[];
  revoked?: boolean | string | { why?: string } | null;
};

export type SourceStatus = {
  id: string;
  ok?: boolean;
  problem?: string | null;
  old?: boolean;
};
export type MarketSearch =
  | {
      hits?: MarketHit[];
      results?: MarketHit[];
      plugins?: MarketHit[];
      sources?: SourceStatus[];
    }
  | MarketHit[];

export type PlanItem = {
  name: string;
  title?: string;
  version?: string;
  trust?: string;
  permissions?: Permissions;
};

/** One plugin (GET /api/marketplace/{name}). */
export type MarketPlugin = MarketHit & {
  repo?: string;
  versions?: CatalogVersion[];
  permissions?: Permissions;
  checks?: (string | { name?: string; text?: string; ok?: boolean })[];
  plan?:
    | PlanItem[]
    | { version?: string; plugins?: PlanItem[]; install?: PlanItem[] };
};

/** One installed plugin (GET /api/plugins). from: image (in keel's image), marketplace, or file. */
export type InstalledPlugin = {
  name: string;
  title?: string;
  version?: string;
  parts?: string[];
  from?: "image" | "marketplace" | "file" | string;
  source?: string;
  on?: boolean;
  status?: string;
  problems?: (string | { error?: string })[];
  revoked?: boolean | string | { why?: string; fixed?: string } | null;
  update?: string | boolean | null;
  previous?: string | null;
  publisher?: string;
  trust?: string;
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
export type PluginsView = {
  plugins: InstalledPlugin[];
  restart: RestartState;
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
  fetched_at?: string | null;
};
export type Rules = {
  agents_may_ask: boolean;
  allow_unverified: boolean;
  check_daily: boolean;
  restart_when_idle: boolean;
};
export type PluginSet = {
  id: string;
  title?: string;
  summary?: string;
  plugins: string[];
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
  status?: string;
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
  update?: boolean;
};

const e = encodeURIComponent;
const query = (p: Record<string, string>) => {
  const s = new URLSearchParams(
    Object.entries(p).filter(([, v]) => v),
  ).toString();
  return s ? `?${s}` : "";
};

export const marketApi = {
  search: (q = "", category = "") =>
    get<MarketSearch>(`/marketplace${query({ q, category })}`),
  plugin: (name: string) => get<MarketPlugin>(`/marketplace/${e(name)}`),
  refresh: () => post<unknown>("/marketplace/refresh"),
  installed: () => get<PluginsView>("/plugins"),
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
    put<{ sources?: Source[] } | Source[]>("/plugins/sources", { sources }),
  rules: () => get<Rules>("/plugins/rules"),
  saveRules: (rules: Rules) => put<Rules>("/plugins/rules", rules),
  sets: () => get<PluginSet[] | { sets?: PluginSet[] }>("/plugins/sets"),
};

// ---------- reading the answers ----------

export const hitsOf = (s: MarketSearch | null | undefined): MarketHit[] =>
  !s ? [] : Array.isArray(s) ? s : (s.hits ?? s.results ?? s.plugins ?? []);
export const sourceStatusOf = (
  s: MarketSearch | null | undefined,
): SourceStatus[] => (!s || Array.isArray(s) ? [] : (s.sources ?? []));
export const sourcesOf = (
  s: { sources?: Source[] } | Source[] | null | undefined,
): Source[] => (!s ? [] : Array.isArray(s) ? s : (s.sources ?? []));
export const setsOf = (
  s: PluginSet[] | { sets?: PluginSet[] } | null | undefined,
): PluginSet[] => (!s ? [] : Array.isArray(s) ? s : (s.sets ?? []));

/** What installing this plugin installs (its plan): the needed plugins first, then itself. */
export function planOf(p: MarketPlugin): PlanItem[] {
  const plan = p.plan;
  const items = Array.isArray(plan)
    ? plan
    : (plan?.plugins ?? plan?.install ?? []);
  if (items.length) return items.filter((i) => i && typeof i.name === "string");
  return [
    {
      name: p.name,
      title: p.title,
      version: newestVersion(p),
      trust: typeof p.trust === "string" ? p.trust : undefined,
    },
  ];
}

/** The version an install takes: the plan's, else the newest listed. */
export function newestVersion(p: MarketPlugin): string | undefined {
  const plan = p.plan;
  if (plan && !Array.isArray(plan) && plan.version) return plan.version;
  const own = planItemsOf(plan).find((i) => i.name === p.name)?.version;
  return own ?? p.version ?? p.latest ?? p.versions?.[0]?.version;
}

const planItemsOf = (plan: MarketPlugin["plan"]): PlanItem[] =>
  Array.isArray(plan) ? plan : (plan?.plugins ?? plan?.install ?? []);

/** The permissions of the version an install takes. */
export function permissionsOf(p: MarketPlugin): Permissions | undefined {
  const v = newestVersion(p);
  return (
    p.versions?.find((x) => x.version === v)?.permissions ??
    p.permissions ??
    planItemsOf(p.plan).find((i) => i.name === p.name)?.permissions
  );
}

/** The plugins it needs: from the version's requires.plugins, else its `needs`. */
export function needsOf(p: MarketPlugin | MarketHit): string[] {
  const v =
    "versions" in p
      ? (p as MarketPlugin).versions?.find(
          (x) => x.version === newestVersion(p as MarketPlugin),
        )
      : undefined;
  const fromVersion = v?.requires?.plugins
    ? Object.keys(v.requires.plugins)
    : null;
  return (
    fromVersion ??
    (p.needs ?? [])
      .map((n) => (typeof n === "string" ? n : n.name))
      .filter(Boolean)
  );
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

export function publisherOf(h: MarketHit): { name: string; verified: boolean } {
  const p = h.publisher;
  if (p && typeof p === "object")
    return {
      name: p.title ?? p.name ?? "",
      verified: Boolean(p.verified ?? h.verified),
    };
  return { name: p ?? "", verified: Boolean(h.verified) };
}

export function revokedText(
  r: InstalledPlugin["revoked"] | MarketHit["revoked"],
): string | null {
  if (!r) return null;
  if (typeof r === "string") return r;
  if (typeof r === "object")
    return (
      [r.why, "fixed" in r && r.fixed ? `${r.fixed} fixes it` : null]
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
    case "":
    case "agent":
      return "An agent";
    default:
      return source!;
  }
}
