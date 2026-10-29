// keel 0.18.66 Help › Start here, the first-run welcome, Settings › Access and the update notice: their api calls.
//   GET /api/help/start      the five steps as keel sees them (projects, models, MCP tools, init, the first flow)
//   GET|PUT /api/help/welcome  the welcome was finished or skipped (once per keel, not per browser)
//   GET /api/mcp/clients     the AI tools (MCP) and terminal apps that called keel
//   GET /api/access          where keel answers (this computer, Wi-Fi, Tailscale)
//   GET /api/updates, PUT /api/updates/settings, POST /api/updates/check   the daily release check

import { get, post, put } from "./api";

/** One AI tool or terminal app keel saw. via: mcp (an AI tool), tui (keel TUI) or xal; level: read, write, admin or
 *  "you" (a terminal app acts as the person). today: calls since midnight. */
export type McpClientSeen = {
  client: string;
  via: "mcp" | "tui" | "xal" | string;
  level: string;
  first_at: string;
  last_at: string;
  last_tool?: string | null;
  today: number;
  total: number;
};

export type WelcomeState = { seen: boolean; at?: string | null; how?: string | null };

export type StartView = {
  projects: number;
  project: { id: string; name: string; configured: boolean; init_running: boolean } | null;
  models: { provider: string; label: string; mode: string }[];
  mcp: McpClientSeen[];
  flow: { thread_id: string; project_id: string; workflow: string | null; title: string; status: string } | null;
  flows: number;
  welcome: WelcomeState;
};

export type AccessView = {
  mode: "local" | "wifi" | "tailscale";
  bind: string | null;
  wifi: string[];
  names: string[];
  tailscale: string | null;
  tunnel: string | null;
  native: boolean;
  folder: string | null;
  /** 0.18.68 keel2's absolute path on this computer: the MCP snippets name it (null: an older keel2) */
  keel2_path?: string | null;
};

export type UpdatesView = {
  current: string;
  latest: string | null;
  newer: boolean;
  name: string | null;
  notes: string;
  url: string;
  published_at: string | null;
  checked_at: string | null;
  error: string | null;
  check_daily: boolean;
  channel: "stable" | "product" | string;
  edition: string;
  image: string | null;
  native: boolean;
  command: string;
  command_why: string;
};

const q = (pid?: string | null) => (pid ? `?project=${encodeURIComponent(pid)}` : "");

export const helpApi = {
  start: (pid?: string | null) => get<StartView>(`/help/start${q(pid)}`),
  welcome: () => get<WelcomeState>("/help/welcome"),
  saveWelcome: (seen: boolean, how?: "finished" | "skipped") => put<WelcomeState>("/help/welcome", { seen, how }),
  mcpClients: () => get<{ clients: McpClientSeen[] }>("/mcp/clients"),
  access: () => get<AccessView>("/access"),
  updates: () => get<UpdatesView>("/updates"),
  saveUpdates: (body: { check_daily: boolean; channel?: string | null }) => put<UpdatesView>("/updates/settings", body),
  checkUpdates: () => post<UpdatesView>("/updates/check"),
};
