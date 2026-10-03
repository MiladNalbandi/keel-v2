// Screens and their groups — the same grouped navigation as docs/mockup.html.

export type ScreenId =
  | "projects" | "flow" | "live" | "jobs" | "repo" | "map" | "wiki"
  | "workflows" | "agents" | "skills" | "stacks" | "tools" | "budget" | "settings" | "connections";

export type Group = { id: string; label: string; hint: string; pages: [ScreenId, string][] };

export const GROUPS: Group[] = [
  { id: "run", label: "Run", hint: "what is happening now", pages: [["flow", "Flow"], ["live", "Live agents"], ["jobs", "Jobs"]] },
  { id: "know", label: "Project", hint: "what this project is", pages: [["repo", "Repo"], ["map", "Map"], ["wiki", "Wiki"]] },
  {
    id: "build", label: "Build", hint: "how agents work",
    pages: [["workflows", "Workflows"], ["agents", "Agents"], ["skills", "Skill hub"], ["stacks", "Stacks"], ["tools", "Tools (MCP)"]],
  },
  { id: "control", label: "Control", hint: "cost, limits, accounts", pages: [["budget", "Budget"], ["settings", "Settings"], ["connections", "Connections"]] },
];

export const SCREENS: [ScreenId, string][] = [["projects", "All projects"], ...GROUPS.flatMap((g) => g.pages)];
export const isScreen = (s: string): s is ScreenId => SCREENS.some(([id]) => id === s);
export const groupOf = (id: ScreenId) => GROUPS.find((g) => g.pages.some(([p]) => p === id));

export type Route = { page: ScreenId; arg?: string };

/** "#/flow", "#flow", "#/wiki/kb:architecture" → route. Unknown pages fall back to Flow. */
export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [page, ...rest] = raw.split("/");
  const arg = rest.length ? decodeURIComponent(rest.join("/")) : undefined;
  return { page: isScreen(page) ? page : "flow", arg };
}

export const hashFor = (page: ScreenId, arg?: string) => `#/${page}${arg ? "/" + encodeURIComponent(arg) : ""}`;

/** A notification link ("flow", "#/jobs/j-1", "/jobs") → route. */
export function routeFromLink(link: string | undefined): Route {
  if (!link) return { page: "flow" };
  return parseHash(link.startsWith("#") ? link : "#" + link.replace(/^\/+/, "/"));
}
