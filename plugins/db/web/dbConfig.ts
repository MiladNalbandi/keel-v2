// Database connections global or per project (Control › Databases, a project's Databases): their JSON, the api calls
// (keel.api.plugins DatabasesController in the plugin's api part), and what the list and the editor work out from them.
// The password goes to the api only; it never comes back.

import { del, get, post, put } from "@keel/web-sdk";
import type { Driver } from "./logos";

export type Env = "local" | "test" | "staging" | "prod";
export type ConnectMode = "host" | "socket" | "url" | "env";
/** none < private (#96: agents run reads, the rows come only to you) < read < write_approve */
export type Access = "none" | "private" | "read" | "write_approve";
/** #96 the most keel's MCP server (Claude Code, OpenCode, Codex … through keel2 mcp) may do with databases */
export type McpAccess = "connection" | "private" | "none";
export type Keep = "keel" | "session" | "never";

export const ENVS: Env[] = ["local", "test", "staging", "prod"];
/** a change of data may run there (by the person; by an agent only after their OK) */
export const CHANGE_ENVS: Env[] = ["local", "test"];

/** One connection as the api lists it: everything but the password. */
export type DbEntry = {
  id: string;
  name: string;
  source: "project" | "global";
  overridden: boolean;
  hidden: boolean;
  project: string | null;
  driver: Driver;
  kind: string;
  env: Env;
  connect_mode: ConnectMode;
  host: string | null;
  port: number | null;
  username: string | null;
  default_db: string | null;
  url_override: string | null;
  socket_path: string | null;
  env_var: string | null;
  note: string | null;
  agent_access: Access;
  agent_access_effective: Access;
  ask_first: boolean;
  keep_password: Keep;
  has_password: boolean;
  url: string | null;
  shown: string;
  found_in: string | null;
  projects: string[];
  ok: boolean | null;
  server: string | null;
  tables: number | null;
  error: string | null;
  checked_at: string | null;
  can_change: boolean;
};

export type DbTest = {
  ok: boolean;
  server?: string | null;
  tables?: number | null;
  ms?: number | null;
  error?: string | null;
  hint?: string | null;
};

/** What the editor holds while the person types. */
export type DbDraft = {
  id?: string;
  name: string;
  env: Env;
  scope: "global" | "project";
  /** a global one only for these projects (empty: every project) */
  projects: string[];
  driver: Driver;
  connect_mode: ConnectMode;
  host: string;
  port: string;
  sign_in: "password" | "none";
  username: string;
  /** typed now; empty keeps the saved one */
  password: string;
  keep_password: Keep;
  default_db: string;
  url_override: string;
  socket_path: string;
  env_var: string;
  note: string;
  agent_access: Access;
  ask_first: boolean;
  found_in?: string;
  /** a whole address from "Find in project" (masked; the api reads the real one again) */
  import_url?: string;
};

export type DbSuggestion = {
  name: string;
  kind: string;
  url: string;
  shown: string;
  source: string;
  password: boolean;
};
type Project = { id: string; name: string };

const e = encodeURIComponent;
const q = (pid?: string) => (pid ? `?project=${e(pid)}` : "");

export const dbxApi = {
  globals: () => get<DbEntry[]>("/databases"),
  effective: (pid: string) => get<DbEntry[]>(`/projects/${e(pid)}/databases`),
  projects: () => get<Project[]>("/projects"),
  suggest: (pid: string) =>
    get<DbSuggestion[]>(`/projects/${e(pid)}/databases/suggest`),
  createGlobal: (body: object) => post<DbEntry>("/databases", body),
  createIn: (pid: string, body: object) =>
    post<DbEntry>(`/projects/${e(pid)}/databases`, body),
  updateGlobal: (id: string, body: object, pid?: string) =>
    put<DbEntry>(`/databases/${e(id)}${q(pid)}`, body),
  updateIn: (pid: string, id: string, body: object) =>
    put<DbEntry>(`/projects/${e(pid)}/databases/${e(id)}`, body),
  removeGlobal: (id: string) => del(`/databases/${e(id)}`),
  removeIn: (pid: string, id: string) =>
    del(`/projects/${e(pid)}/databases/${e(id)}`),
  test: (body: object, pid?: string) =>
    post<DbTest>(
      pid ? `/projects/${e(pid)}/databases/test` : "/databases/test",
      body,
    ),
  serverDatabases: (body: object, pid?: string) =>
    post<{ databases: { name: string }[] }>(
      pid
        ? `/projects/${e(pid)}/databases/server-databases`
        : "/databases/server-databases",
      body,
    ),
  hide: (pid: string, id: string, hidden: boolean) =>
    put<DbEntry[]>(`/projects/${e(pid)}/databases/${e(id)}/hidden`, { hidden }),
  mcp: () => get<{ access: McpAccess }>("/databases/mcp"),
  setMcp: (access: McpAccess) =>
    put<{ access: McpAccess }>("/databases/mcp", { access }),
};

export const DEFAULT_PORT: Partial<Record<Driver, number>> = {
  postgres: 5432,
  mysql: 3306,
  mariadb: 3306,
};

export function newDraft(
  scope: "global" | "project",
  over: Partial<DbDraft> = {},
): DbDraft {
  return {
    name: "",
    env: "local",
    scope,
    projects: [],
    driver: "postgres",
    connect_mode: "host",
    host: "localhost",
    port: "5432",
    sign_in: "password",
    username: "",
    password: "",
    keep_password: "keel",
    default_db: "",
    url_override: "",
    socket_path: "",
    env_var: "DATABASE_URL",
    note: "",
    agent_access: "read",
    ask_first: false,
    ...over,
  };
}

export function draftOf(c: DbEntry): DbDraft {
  return {
    id: c.id,
    name: c.name,
    env: c.env,
    scope: c.source,
    projects: c.projects,
    driver: c.driver,
    connect_mode: c.connect_mode,
    host: c.host ?? "",
    port: c.port ? String(c.port) : "",
    sign_in: c.has_password || c.keep_password !== "keel" ? "password" : "none",
    username: c.username ?? "",
    password: "",
    keep_password: c.keep_password,
    default_db: c.default_db ?? "",
    url_override: c.url_override ?? c.url ?? "",
    socket_path: c.socket_path ?? "",
    env_var: c.env_var ?? "",
    note: c.note ?? "",
    agent_access: c.agent_access,
    ask_first: c.ask_first,
    found_in: c.found_in ?? undefined,
  };
}

const enc = (s: string) => encodeURIComponent(s);

/** The address the fields make, without the password (the api builds the same one). */
export function builtUrl(d: DbDraft): string {
  if (d.driver === "sqlite") return `sqlite:${d.default_db}`;
  if (d.connect_mode === "url") return d.url_override;
  if (d.connect_mode === "env") return "";
  const user = d.username ? `${enc(d.username)}@` : "";
  const db = enc(d.default_db);
  if (d.connect_mode === "socket") {
    return d.driver === "postgres"
      ? `postgres://${user}/${db}?host=${enc(d.socket_path)}`
      : `${d.driver}://${user}localhost/${db}?unix_socket=${enc(d.socket_path)}`;
  }
  const host = d.host.includes(":") ? `[${d.host}]` : d.host;
  return `${d.driver}://${user}${host}${d.port ? `:${d.port}` : ""}/${db}`;
}

/** What the api gets: the fields, the password only when typed, the scope and its projects. */
export function bodyOf(d: DbDraft): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: d.name.trim(),
    env: d.env,
    driver: d.driver,
    connect_mode: d.driver === "sqlite" ? "host" : d.connect_mode,
    host: d.host,
    port: d.port.trim() ? Number(d.port) : null,
    username: d.username,
    default_db: d.default_db,
    socket_path: d.socket_path,
    env_var: d.env_var,
    note: d.note,
    agent_access: d.agent_access,
    ask_first: d.ask_first,
    keep_password: d.sign_in === "none" ? "keel" : d.keep_password,
  };
  if (d.connect_mode === "url" && d.driver !== "sqlite")
    body.url_override = d.url_override;
  if (d.sign_in === "password" && d.password) body.password = d.password;
  if (d.sign_in === "none") body.clear_password = true;
  if (d.scope === "global") body.projects = d.projects;
  if (d.import_url) {
    body.url = d.import_url;
    body.source = d.found_in;
  }
  if (d.id) body.id = d.id;
  return body;
}

/** The badge of an environment: local green, test blue, staging amber, prod red ("read only" for agents, always). */
export const envTone = (env: Env) =>
  (({ local: "ok", test: "run", staging: "warn", prod: "bad" }) as const)[env];
export const envBadge = (env: Env) =>
  env === "prod" ? "prod · read only" : env;

export type Group = {
  id: string;
  title: string;
  icon: "globe" | "folder";
  items: DbEntry[];
};

/** The list's groups: "All projects" (global ones) first, then "<project> only" (its own), each by name. */
export function groupEntries(
  all: DbEntry[],
  projectName?: string,
  filter = "",
): Group[] {
  const f = filter.trim().toLowerCase();
  const hit = (c: DbEntry) =>
    !f ||
    [
      c.name,
      c.driver,
      c.env,
      c.host ?? "",
      c.default_db ?? "",
      c.note ?? "",
    ].some((s) => s.toLowerCase().includes(f));
  const by = (a: DbEntry, b: DbEntry) => a.name.localeCompare(b.name);
  const globals = all.filter((c) => c.source === "global" && hit(c)).sort(by);
  const own = all.filter((c) => c.source === "project" && hit(c)).sort(by);
  const out: Group[] = [
    { id: "global", title: "All projects", icon: "globe", items: globals },
  ];
  if (projectName !== undefined)
    out.push({
      id: "project",
      title: `${projectName} only`,
      icon: "folder",
      items: own,
    });
  return out;
}

/** The connections whose last test failed. */
export const problems = (all: DbEntry[]) =>
  all.filter((c) => c.ok === false && !c.hidden);

export const testLine = (t: DbTest) =>
  t.ok
    ? `✓ ${[t.server, t.tables != null ? `${t.tables} tables` : null, t.ms != null ? `${t.ms} ms` : null].filter(Boolean).join(" · ")}`
    : `✗ ${t.error ?? "keel could not connect"}`;

const SCHEME: Record<string, Driver> = { postgres: "postgres", postgresql: "postgres", mysql: "mysql", mariadb: "mariadb", sqlite: "sqlite", sqlite3: "sqlite" };

/** The fields of an address keel found in the project (its password masked as •••): only to show them. */
export function fieldsOf(url: string): Partial<DbDraft> {
  const raw = url.trim().replace(/^jdbc:/, "");
  const driver = SCHEME[raw.split(":")[0].split("+")[0].toLowerCase()];
  if (!driver) return {};
  if (driver === "sqlite") return { driver, default_db: decodeURIComponent(raw.replace(/^sqlite3?:(\/\/)?/i, "")) };
  const m = raw.match(/^[^:]+:\/\/(?:([^:@/]*)(?::[^@]*)?@)?(\[[^\]]+\]|[^:/?]*)(?::(\d+))?(?:\/([^?#]*))?/);
  if (!m) return { driver };
  return {
    driver, connect_mode: "host", username: decodeURIComponent(m[1] ?? ""), host: (m[2] ?? "").replace(/^\[|\]$/g, ""),
    port: m[3] ?? String(DEFAULT_PORT[driver] ?? ""), default_db: decodeURIComponent(m[4] ?? ""),
  };
}
