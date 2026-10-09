// The Graph plugin's api calls and types (plugins/graph/api: keel.api.graph.GraphController), with keel's transport from
// @keel/web-sdk. Moved from keel's api.ts with the page.

import { get, post } from "@keel/web-sdk";

/** v0.5.3 the code graph (`GET /api/projects/{pid}/graph`): the CodeGraph index rolled up into units and groups. */
export type GraphGroup = {
  id: string;
  kind: "package" | "folder";
  name: string;
  label: string;
  path: string[];
};
export type GraphUnit = {
  id: string;
  name: string;
  kind: string;
  group: string;
  file: string;
  line: number;
  members: number;
};
/** from uses to: n uses in all, k by kind (calls, instantiates, implements, extends, references). */
export type GraphLink = {
  from: string;
  to: string;
  n: number;
  k: Record<string, number>;
};
export type GraphUnavailable = {
  available: false;
  status: string;
  reason: string;
};
export type GraphOverview =
  | {
      available: true;
      status?: string;
      indexed_at?: string | null;
      counts: {
        files: number;
        symbols: number;
        units: number;
        links: number;
        uses: number;
      };
      groups: GraphGroup[];
      units: GraphUnit[];
      links: GraphLink[];
    }
  | GraphUnavailable;
export type GraphHit = {
  id: string;
  name: string;
  kind: string;
  file: string;
  line: number;
  unit: string;
  group: string;
};
export type GraphNodeRef = {
  id: string;
  name: string;
  kind: string;
  unit: string;
  group: string;
  file: string;
  line: number;
  col: number;
};
export type GraphEdge = GraphLink & { sites: { file: string; line: number }[] };
export type GraphMember = {
  id: string;
  name: string;
  kind: string;
  line: number;
  in: number;
  out: number;
};
export type GraphFocus =
  | {
      available: true;
      level: "unit" | "member";
      depth: number;
      missing?: undefined;
      focus: {
        id: string;
        name: string;
        kind: string;
        qualified: string;
        signature?: string | null;
        docstring?: string | null;
        file: string;
        line: number;
        end_line?: number | null;
        group: string;
        unit: { id: string; name: string; kind: string } | null;
        members: GraphMember[];
      };
      /** col -2 / -1: who uses it (two steps, one step); 1 / 2: what it uses */
      nodes: GraphNodeRef[];
      edges: GraphEdge[];
      more: Record<string, number>;
      impact: number;
      impact_capped: boolean;
    }
  | { available: true; missing: string }
  | GraphUnavailable;
/** The engine's code graph index of a project (engine runtime/scan.py): what a rebuild answers. */
export type IndexStatus = {
  project: string;
  status: "idle" | "indexing" | "ready" | "failed";
  files: number;
  symbols: number;
  indexed_at?: string | null;
  error?: string | null;
  available?: boolean;
};

const e = encodeURIComponent;

export const graphApi = {
  graph: (pid: string) => get<GraphOverview>(`/projects/${e(pid)}/graph`),
  graphSearch: (pid: string, q: string) =>
    get<{ available: boolean; reason?: string; results: GraphHit[] }>(
      `/projects/${e(pid)}/graph/search?q=${e(q)}`,
    ),
  graphNode: (pid: string, id: string, depth = 1) =>
    get<GraphFocus>(
      `/projects/${e(pid)}/graph/node?id=${e(id)}&depth=${depth}`,
    ),
  rebuildIndex: (pid: string) =>
    post<IndexStatus>(`/projects/${e(pid)}/index/rebuild`),
};
