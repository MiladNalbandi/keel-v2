// The Database plugin's api calls and types (plugins/db): the same routes and JSON as keel 0.15.1 (keel.api.plugins
// DatabaseController in the plugin's api part), through keel's own transport (@keel/web-sdk get/post/put/del).

import { del, get, post, put } from "@keel/web-sdk";

export type DbConnection = {
  name: string;
  kind: "postgres" | "mysql" | "sqlite";
  env: "local" | "test" | "staging" | "prod";
  shown: string;
  source?: string | null;
  ok?: boolean | null;
  server?: string | null;
  tables?: number | null;
  error?: string | null;
  checked_at?: string | null;
  /** a change of data may run here: local and test only */
  can_change: boolean;
};
export type DbSuggestion = {
  name: string;
  kind: DbConnection["kind"];
  url: string;
  shown: string;
  env: "local";
  source: string;
  password: boolean;
};
export type LiveTable = {
  name: string;
  columns: { name: string; type: string; nullable: boolean; pk: boolean }[];
  fks: { column: string; table: string; ref: string }[];
};
export type LiveSchema = {
  connection: string;
  kind: string;
  tables: LiveTable[];
};
/** A query's result: a read (columns, rows) or a change (changed; done: run for real, else counted and rolled back). */
export type DbResult = {
  connection: string;
  env?: string;
  kind: "read" | "change";
  sql: string;
  ms?: number;
  columns?: string[];
  rows?: unknown[][];
  count?: number;
  truncated?: boolean;
  masked?: string[];
  changed?: number;
  done?: boolean;
};
type Tested = {
  connection: DbConnection;
  test: { ok: boolean; error?: string; hint?: string };
};
/** A plugin of Tools › Plugins as the api lists it for a project: is it on here. */
type PluginOn = { name: string; enabled?: boolean };

const e = encodeURIComponent;
const base = (pid: string) => `/projects/${e(pid)}/db`;

export const dbApi = {
  /** the project's plugins (keel's own list, GET /projects/{pid}/plugins): the Database plugin's pieces ask if it is on */
  plugins: (pid: string) => get<PluginOn[]>(`/projects/${e(pid)}/plugins`),
  connections: (pid: string) => get<DbConnection[]>(`${base(pid)}/connections`),
  suggest: (pid: string) => get<DbSuggestion[]>(`${base(pid)}/suggest`),
  add: (
    pid: string,
    body: { name: string; url: string; env: string; source?: string },
  ) => post<Tested>(`${base(pid)}/connections`, body),
  update: (pid: string, name: string, body: { url?: string; env?: string }) =>
    put<DbConnection>(`${base(pid)}/connections/${e(name)}`, body),
  remove: (pid: string, name: string) =>
    del(`${base(pid)}/connections/${e(name)}`),
  test: (pid: string, name: string) =>
    post<Tested>(`${base(pid)}/connections/${e(name)}/test`),
  schema: (pid: string, connection?: string) =>
    get<LiveSchema>(
      `${base(pid)}/schema${connection ? `?connection=${e(connection)}` : ""}`,
    ),
  query: (
    pid: string,
    body: {
      connection?: string;
      sql: string;
      change?: boolean;
      confirm?: boolean;
    },
  ) => post<DbResult>(`${base(pid)}/query`, body),
};
