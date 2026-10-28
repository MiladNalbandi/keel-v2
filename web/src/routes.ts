// Screens and their groups — the same grouped navigation as docs/mockup.html. keel's own screens are named in SCREEN;
// every page (keel's own and the parts') registers in the page registry (sdk/registry.ts) with its group and order, so
// the menu and the router read one list. Aliases (#/code, #/keelbot) come from the pages too.

import { allPages, hasPage, pageFor, pageOf } from "./sdk/registry";

/** A screen's id: one of keel's own (SCREEN) or a registered page's (#/map, #/repo). */
export type ScreenId = string;

/** keel's own screens. The others (Tasks, Code, KeelBot, Graph) are parts' pages (web/src/builtins.ts), and the
 *  plugins' pages (the Map and the Wiki: plugins/map and plugins/wiki, registered at start by addons.ts). */
export const SCREEN = {
  projects: "projects",
  inbox: "inbox",
  flow: "flow",
  live: "live",
  jobs: "jobs",
  workflows: "workflows",
  /** v0.17.2 Build › Rules: keel's rules and the project's rule packs (core: the guard is core) */
  rules: "rules",
  agents: "agents",
  skills: "skills",
  stacks: "stacks",
  tools: "tools",
  quality: "quality",
  budget: "budget",
  settings: "settings",
  connections: "connections",
  /** v0.16.0 Control › Plugins: installed plugins, the marketplace, sources and rules */
  plugins: "plugins",
  /** keel 0.18.66 Help › Start here (#/help) and the short guides (#/help/<guide>) */
  help: "help",
  /** v0.13.0 a page of an add-on (keel Product): the route's `screen` names it. */
  addon: "addon",
} as const;

const OWN = new Set<string>(
  Object.values(SCREEN).filter((s) => s !== SCREEN.addon),
);

/** The menu's groups, in order. Their pages come from the page registry. */
export type GroupHead = { id: string; label: string; hint: string };
export const GROUP_HEADS: GroupHead[] = [
  { id: "run", label: "Run", hint: "what is happening now" },
  { id: "know", label: "Project", hint: "what this project is" },
  { id: "build", label: "Build", hint: "how agents work" },
  { id: "control", label: "Control", hint: "cost, limits, accounts" },
  // keel 0.18.66 Start here and the short guides
  { id: "help", label: "Help", hint: "start here, short guides" },
];

export type Group = GroupHead & { pages: [ScreenId, string][] };

/** The menu: each group with its registered pages, by their order. */
export function menuGroups(): Group[] {
  const pages = allPages();
  return GROUP_HEADS.map((g) => ({
    ...g,
    pages: pages
      .filter((p) => p.group === g.id)
      .sort((a, b) => a.order - b.order)
      .map((p): [ScreenId, string] => [p.id, p.label]),
  }));
}

export const isScreen = (s: string): s is ScreenId =>
  OWN.has(s) || pageOf(s) !== null;
/** A link to this page opens it in this keel: keel's own screens, and a page in the registry (by its id or an alias).
 *  Step 6: a keel with only its core has no Wiki or Tasks page, so a core page does not link to them. */
export const canOpen = (s: string): boolean => OWN.has(s) || hasPage(s);
export const groupOf = (id: ScreenId) =>
  menuGroups().find((g) => g.pages.some(([p]) => p === id));

export type Route = {
  page: ScreenId;
  arg?: string;
  screen?: string;
  /** v0.15.3 the project a link names (/projects/<id>/…) */ project?: string;
  /** v0.16.0 what follows "?" in the link (#/plugins?set=developer), when there is something */
  query?: string;
  /** keel 0.18.57 an old link (#/budget, #/plugins/git) names a page that is a Settings tab now: the link to show
   *  instead (#/settings/budget). The router puts it in the address bar; the route already opens the tab. */
  redirect?: string;
};

/** keel 0.18.57 the Settings page's tabs, in its menu's order: #/settings/<tab>, and #/settings/general/<project> for one
 *  project's own settings. Databases is the Database plugin's tab (slot settings.tab); the others are keel's own. */
export const SETTINGS_TABS = ["general", "models", "review", "connections", "budget", "plugins", "databases", "access"] as const;
export type SettingsTabId = (typeof SETTINGS_TABS)[number];

/** keel's own Settings tabs: id, name in the settings menu, order (a part's tab goes between them by its order). */
export const CORE_SETTINGS_TABS: { id: SettingsTabId; label: string; order: number }[] = [
  { id: "general", label: "General", order: 10 },
  { id: "models", label: "Models & logins", order: 20 },
  // keel 0.18.65 one project's review: the Review page's reviewers and lenses, the checker, the fix, the last review
  { id: "review", label: "Review", order: 25 },
  { id: "connections", label: "Connections", order: 30 },
  { id: "budget", label: "Budget", order: 40 },
  { id: "plugins", label: "Plugins", order: 50 },
  // keel 0.18.66 Access: where keel answers, the devices (Settings › Devices until 0.18.65), AI tools and updates
  { id: "access", label: "Access", order: 70 },
];

/** A Settings tab's link: settingsHash("budget") is #/settings/budget; `rest` follows it (#/settings/plugins/git). */
export const settingsHash = (tab: string, rest?: string) => `#/settings/${tab}${rest ? `/${rest}` : ""}`;

/** The pages that became a Settings tab (keel 0.18.57). Their old links keep working: #/budget opens Settings › Budget,
 *  #/plugins/git?at=projects opens Settings › Plugins › Git there, #/connections the Connections tab. */
const MOVED: Record<string, SettingsTabId> = {
  budget: "budget",
  connections: "connections",
  plugins: "plugins",
  databases: "databases",
  // keel 0.18.66 Devices is part of Access now: #/devices and #/settings/devices open it
  devices: "access",
  models: "models",
};

/** keel 0.18.66 Settings tabs that became part of another tab: their links open that one. */
const TAB_MOVED: Record<string, SettingsTabId> = { devices: "access" };

/** The tab and the rest of a Settings link: #/settings/plugins/rules/x → ["plugins", "rules/x"]; #/settings → general. */
export function settingsTabOf(r: Pick<Route, "page" | "arg">): [string, string | undefined] {
  if (r.page !== "settings" || !r.arg) return ["general", undefined];
  const i = r.arg.indexOf("/");
  return i < 0 ? [r.arg, undefined] : [r.arg.slice(0, i), r.arg.slice(i + 1) || undefined];
}

/** "#/flow", "#flow", "#/wiki/kb:architecture", "#/plugins?set=developer" → route. Unknown pages fall back to Flow. */
/** decodeURIComponent that never throws: a link with a broken escape (#/jobs/100%) keeps its raw text. */
export function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

export function parseHash(hash: string): Route {
  const full = hash.replace(/^#\/?/, "");
  const q = full.indexOf("?");
  const raw = q < 0 ? full : full.slice(0, q);
  const query = q < 0 ? "" : full.slice(q + 1);
  const withQuery = (r: Route): Route => (query ? { ...r, query } : r);
  // v0.15.8 a link that names its project (keel_dashboard): #/projects/<id>/<page>[/<arg>]
  const pm = raw.match(/^projects\/([^/]+)\/(.+)$/);
  if (pm) {
    const inner = parseHash("#/" + pm[2] + (query ? `?${query}` : ""));
    return {
      ...inner,
      project: safeDecode(pm[1]),
      ...(inner.redirect ? { redirect: `#/projects/${pm[1]}/${inner.redirect.slice(2)}` } : {}),
    };
  }
  const [first, ...rest] = raw.split("/");
  // keel 0.18.66 a Settings tab that is part of another one now (#/settings/devices is Settings › Access)
  if (first === "settings" && rest[0] && TAB_MOVED[rest[0]]) {
    const to = TAB_MOVED[rest[0]];
    return withQuery({ page: SCREEN.settings, arg: to, redirect: `${settingsHash(to)}${query ? `?${query}` : ""}` });
  }
  // keel 0.18.57 a page that is a Settings tab now: the tab, and the link to show instead
  const moved = MOVED[first];
  if (moved && !pageFor(first)) {
    const tail = rest.join("/");
    return withQuery({
      page: SCREEN.settings,
      arg: safeDecode(tail ? `${moved}/${tail}` : moved),
      redirect: `${settingsHash(moved, tail)}${query ? `?${query}` : ""}`,
    });
  }
  // a page by the name it shows: #/code is the Code page (id repo), #/keelbot is KeelBot's own page (id helper)
  const page = pageFor(first)?.id ?? first;
  const arg = rest.length ? safeDecode(rest.join("/")) : undefined;
  if (isScreen(page)) return withQuery({ page, arg });
  // maybe an add-on's page (#/initiatives); the router shows Flow when no add-on has it
  if (ADDON_SCREEN.test(page))
    return withQuery({ page: SCREEN.addon, screen: page, arg });
  return withQuery({ page: SCREEN.flow, arg });
}

/** One value of the link's query (#/plugins?set=developer → "developer"), or "". */
export const queryValue = (r: Route, key: string): string =>
  new URLSearchParams(r.query ?? "").get(key) ?? "";

const ADDON_SCREEN = /^[a-z][a-z0-9-]{1,31}$/;

/** An add-on page's link: #/initiatives, #/initiatives/INI-12. */
export const hashForScreen = (screen: string, arg?: string) =>
  `#/${screen}${arg ? "/" + encodeURIComponent(arg) : ""}`;

export const hashFor = (page: ScreenId, arg?: string) =>
  `#/${page}${arg ? "/" + encodeURIComponent(arg) : ""}`;

/** A notification link ("flow", "#/jobs/j-1", "/jobs") → route. v0.15.3 also the api's own form,
 *  "/projects/<id>/flow": that project's page, not the All projects page. */
export function routeFromLink(link: string | undefined): Route {
  if (!link) return { page: SCREEN.flow };
  const pm = link.replace(/^#?\/*/, "").match(/^projects\/([^/]+)\/(.+)$/);
  if (pm) return { ...parseHash("#/" + pm[2]), project: safeDecode(pm[1]) };
  return parseHash(
    link.startsWith("#") ? link : "#" + link.replace(/^\/+/, "/"),
  );
}
