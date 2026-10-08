// Screens and their groups — the same grouped navigation as docs/mockup.html.

export type ScreenId =
  | "projects" | "inbox" | "flow" | "tasks" | "live" | "jobs" | "repo" | "helper" | "map" | "graph" | "wiki"
  | "workflows" | "agents" | "skills" | "stacks" | "tools" | "quality" | "budget" | "settings" | "connections"
  /** v0.13.0 a page of an add-on (keel Product): the route's `screen` names it. */
  | "addon";

export type Group = { id: string; label: string; hint: string; pages: [ScreenId, string][] };

export const GROUPS: Group[] = [
  { id: "run", label: "Run", hint: "what is happening now", pages: [["flow", "Flow"], ["tasks", "Tasks"], ["inbox", "Inbox"], ["live", "Live agents"], ["jobs", "Jobs"]] },
  { id: "know", label: "Project", hint: "what this project is", pages: [["repo", "Code"], ["helper", "KeelBot"], ["map", "Map"], ["graph", "Graph"], ["wiki", "Wiki"]] },
  {
    id: "build", label: "Build", hint: "how agents work",
    pages: [["workflows", "Workflows"], ["agents", "Agents"], ["skills", "Skill hub"], ["stacks", "Stacks"], ["tools", "Tools (MCP)"], ["quality", "Quality"]],
  },
  { id: "control", label: "Control", hint: "cost, limits, accounts", pages: [["budget", "Budget"], ["settings", "Settings"], ["connections", "Connections"]] },
];

export const SCREENS: [ScreenId, string][] = [["projects", "All projects"], ...GROUPS.flatMap((g) => g.pages)];
export const isScreen = (s: string): s is ScreenId => SCREENS.some(([id]) => id === s);
export const groupOf = (id: ScreenId) => GROUPS.find((g) => g.pages.some(([p]) => p === id));

export type Route = { page: ScreenId; arg?: string; screen?: string };

/** Pages by the names they show: #/code is the Code page (id repo), #/keelbot is KeelBot's own page (id helper). */
const ALIASES: Record<string, ScreenId> = { code: "repo", keelbot: "helper" };

/** "#/flow", "#flow", "#/wiki/kb:architecture" → route. Unknown pages fall back to Flow. */
export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [first, ...rest] = raw.split("/");
  const page = ALIASES[first] ?? first;
  const arg = rest.length ? decodeURIComponent(rest.join("/")) : undefined;
  if (isScreen(page)) return { page, arg };
  // maybe an add-on's page (#/initiatives); the router shows Flow when no add-on has it
  if (ADDON_SCREEN.test(page)) return { page: "addon", screen: page, arg };
  return { page: "flow", arg };
}

const ADDON_SCREEN = /^[a-z][a-z0-9-]{1,31}$/;

/** An add-on page's link: #/initiatives, #/initiatives/INI-12. */
export const hashForScreen = (screen: string, arg?: string) => `#/${screen}${arg ? "/" + encodeURIComponent(arg) : ""}`;

export const hashFor = (page: ScreenId, arg?: string) => `#/${page}${arg ? "/" + encodeURIComponent(arg) : ""}`;

/** A notification link ("flow", "#/jobs/j-1", "/jobs") → route. */
export function routeFromLink(link: string | undefined): Route {
  if (!link) return { page: "flow" };
  return parseHash(link.startsWith("#") ? link : "#" + link.replace(/^\/+/, "/"));
}
