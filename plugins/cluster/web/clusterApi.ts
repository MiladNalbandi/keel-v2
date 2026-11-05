// The Cluster plugin's calls to keel's api (/api/plugins/cluster, the plugin's Kotlin part), its shapes, and the small pure
// helpers the page uses: ages, the search, the kubectl commands a person copies. It imports only @keel/web-sdk.

import { get, post, put } from "@keel/web-sdk";

export type Access = "read" | "ask" | "free";

export type Context = {
  name: string; cluster: string; server: string; namespace: string; current: boolean; local: boolean; on: boolean; access: Access;
};
export type Contexts = { kubectl: boolean; kubectl_version?: string | null; kubeconfig?: string | null; in_docker: boolean; contexts: Context[] };

export type Pod = {
  name: string; ns: string; ready: string; status: string; restarts: number; ip: string | null; node: string | null; age: number | null;
  app: string | null; containers: string[]; images: string[]; commit: string | null; owner: string | null;
};
export type Deploy = { name: string; ns: string; ready: string; desired: number; available: number; updated: number; images: string[]; commit: string | null; age: number | null };
export type Svc = { name: string; ns: string; type: string; cluster_ip: string | null; external: string[]; ports: string[]; selector?: Record<string, string> | null };
export type NodeRow = {
  name: string; ready: boolean; roles: string[]; internal_ip: string | null; external_ip: string | null; version: string | null;
  cpu: string | null; memory: string | null; cpu_pct?: number | null; mem_pct?: number | null; pods: number;
};
export type KEvent = { type: string; reason: string; object: string; ns: string; message: string; age: number | null; count: number | null };
export type Ing = { name: string; ns: string; hosts: { host: string; path: string; service: string }[]; address: string[] };
export type Problem = { kind: string; object: string; ns: string; name: string; title: string; line: string };
export type Snapshot = {
  context: string; at: string; namespaces: string[]; pods: Pod[]; deployments: Deploy[]; services: Svc[]; nodes: NodeRow[];
  events: KEvent[]; ingresses: Ing[]; problems: Problem[]; metrics: boolean; warnings: string[];
};
export type Logs = { lines: string[]; container: string; containers: string[] };
export type TestResult = { ok: boolean; ms: number; version?: string | null; nodes?: number | null; error?: string | null; hint?: string | null };
export type RunCell = { commit: string | null; state: "same" | "behind" | "ahead" | "diverged" | "unknown"; behind?: number | null; subject?: string | null; pr?: string | null };
export type Runs = {
  repo: { remote: string; web: string | null; base: string | null; head: string | null } | null; contexts: string[];
  apps: { name: string; ns: string; by: Record<string, RunCell> }[]; errors: Record<string, string>;
};
export type HistoryItem = { at: string; who: string; context: string; action: string; target: string; result: string };
export type ChangeBody = { context: string; action: "delete" | "restart" | "scale"; kind: "pod" | "deployment"; ns: string; name: string; replicas?: number };

const P = "/plugins/cluster";
const q = (o: Record<string, string | number | boolean | undefined>) =>
  Object.entries(o).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

export const clusterApi = {
  contexts: () => get<Contexts>(`${P}/contexts`),
  save: (clusters: Record<string, { on: boolean; access: Access }>) => put<Contexts>(`${P}/settings`, { clusters }),
  test: (context: string) => post<TestResult>(`${P}/test`, { context }),
  snapshot: (context: string, ns = "") => get<Snapshot>(`${P}/snapshot?${q({ context, ns })}`),
  logs: (b: { context: string; ns: string; name: string; container?: string; previous?: boolean; tail?: number }) =>
    get<Logs>(`${P}/logs?${q({ ...b, previous: b.previous ? true : undefined })}`),
  describe: (b: { context: string; kind: string; ns: string; name: string }) => get<{ text: string }>(`${P}/describe?${q(b)}`),
  exec: (b: { context: string; ns: string; name: string; container?: string; command: string }) => post<{ output: string; code: number }>(`${P}/exec`, b),
  change: (b: ChangeBody) => post<{ done: boolean; message: string }>(`${P}/change`, b),
  runs: (pid: string) => get<Runs>(`${P}/runs?${q({ pid })}`),
  history: () => get<{ items: HistoryItem[] }>(`${P}/history`),
};

/** 45s, 12m, 3h, 6d: as kubectl shows an age. */
export function age(s: number | null | undefined): string {
  if (s === null || s === undefined || s < 0) return "—";
  if (s < 120) return `${Math.round(s)}s`;
  if (s < 7200) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export type Kind = "pods" | "deploy" | "svc" | "nodes" | "events";
export const KINDS: [Kind, string][] = [["pods", "Pods"], ["deploy", "Deployments"], ["svc", "Services"], ["nodes", "Nodes"], ["events", "Events"]];
const ALIASES: Record<string, Kind> = {
  po: "pods", pod: "pods", pods: "pods", deploy: "deploy", dp: "deploy", deployment: "deploy", deployments: "deploy",
  svc: "svc", service: "svc", services: "svc", no: "nodes", node: "nodes", nodes: "nodes", ev: "events", event: "events", events: "events",
};

/** A search line that starts with ":" is a command (k9s style): `:svc` shows another kind, `:ns shop` a namespace. */
export function command(text: string): { kind?: Kind; ns?: string } | null {
  if (!text.startsWith(":")) return null;
  const [word, arg] = text.slice(1).trim().split(/\s+/);
  if ((word === "ns" || word === "namespace") && arg) return { ns: arg === "all" ? "" : arg };
  const kind = ALIASES[word ?? ""];
  return kind ? { kind } : null;
}

/** True when the row's words hold the search (a command line filters nothing). */
export function matches(query: string, ...words: (string | number | null | undefined)[]): boolean {
  const t = query.trim().toLowerCase();
  if (!t || t.startsWith(":")) return true;
  return words.filter((w) => w !== null && w !== undefined).join(" ").toLowerCase().includes(t);
}

export const podTone = (status: string): "ok" | "bad" | "warn" | "idle" =>
  status === "Running" ? "ok" : status === "Pending" || status === "ContainerCreating" || status === "Terminating" ? "warn"
    : status === "Completed" || status === "Succeeded" ? "idle" : "bad";

/** A pod's Deployment, from its owner ReplicaSet/<deploy>-<hash> and the deployments keel sees. */
/** A Kubernetes memory size ("16255400Ki", "8Gi") in GiB for people; as it is when it is not one. */
export function memSize(v: string | null | undefined): string {
  if (!v) return "—";
  const m = /^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?$/.exec(v);
  if (!m) return v;
  const f: Record<string, number> = { Ki: 2 ** 10, Mi: 2 ** 20, Gi: 2 ** 30, Ti: 2 ** 40, K: 1e3, M: 1e6, G: 1e9, T: 1e12 };
  const gib = (Number(m[1]) * (m[2] ? f[m[2]] : 1)) / 2 ** 30;
  return gib >= 10 ? `${Math.round(gib)} GiB` : `${gib.toFixed(1)} GiB`;
}

export function deployOf(p: Pod, deployments: Deploy[]): string | null {
  const own = p.owner ?? "";
  const m = /^(?:ReplicaSet|replicaset)\/(.+)-[a-z0-9]+$/.exec(own) ?? /^(?:Deployment|deployment)\/(.+)$/.exec(own);
  const name = m?.[1];
  return name && deployments.some((d) => d.ns === p.ns && d.name === name) ? name : null;
}

const quote = (s: string) => (/^[A-Za-z0-9@%_+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`);
export const execCommand = (ctx: string, ns: string, pod: string, container: string) =>
  `kubectl --context ${quote(ctx)} -n ${quote(ns)} exec -it ${quote(pod)}${container ? ` -c ${quote(container)}` : ""} -- sh`;
export const forwardCommand = (ctx: string, ns: string, pod: string, local: string, port: string) =>
  `kubectl --context ${quote(ctx)} -n ${quote(ns)} port-forward pod/${quote(pod)} ${quote(local)}:${quote(port)}`;

export const ACCESS_WORDS: Record<Access, string> = { read: "only look", ask: "change after you approve", free: "change without asking" };
export const ACCESS_PILL: Record<Access, string> = { read: "read only", ask: "changes ask first", free: "changes allowed" };

/** keel in Docker cannot reach a cluster that listens on the person's own 127.0.0.1 (kind, minikube, k3d). */
export const loopback = (server: string) => /^https?:\/\/(127\.\d+\.\d+\.\d+|localhost|\[::1\])(:|\/|$)/.test(server);
