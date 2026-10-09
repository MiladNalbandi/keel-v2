// Screens and their groups — the same grouped navigation as docs/mockup.html. keel's own screens are named in SCREEN;
// every page (keel's own and the parts') registers in the page registry (sdk/registry.ts) with its group and order, so
// the menu and the router read one list. Aliases (#/code, #/keelbot) come from the pages too.

import { allPages, pageFor, pageOf } from "./sdk/registry";

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
export const groupOf = (id: ScreenId) =>
  menuGroups().find((g) => g.pages.some(([p]) => p === id));

export type Route = {
  page: ScreenId;
  arg?: string;
  screen?: string;
  /** v0.15.3 the project a link names (/projects/<id>/…) */ project?: string;
  /** v0.16.0 what follows "?" in the link (#/plugins?set=developer), when there is something */
  query?: string;
};

/** "#/flow", "#flow", "#/wiki/kb:architecture", "#/plugins?set=developer" → route. Unknown pages fall back to Flow. */
export function parseHash(hash: string): Route {
  const full = hash.replace(/^#\/?/, "");
  const q = full.indexOf("?");
  const raw = q < 0 ? full : full.slice(0, q);
  const query = q < 0 ? "" : full.slice(q + 1);
  const withQuery = (r: Route): Route => (query ? { ...r, query } : r);
  const [first, ...rest] = raw.split("/");
  // a page by the name it shows: #/code is the Code page (id repo), #/keelbot is KeelBot's own page (id helper)
  const page = pageFor(first)?.id ?? first;
  const arg = rest.length ? decodeURIComponent(rest.join("/")) : undefined;
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
  if (pm) return { ...parseHash("#/" + pm[2]), project: decodeURIComponent(pm[1]) };
  return parseHash(
    link.startsWith("#") ? link : "#" + link.replace(/^\/+/, "/"),
  );
}
