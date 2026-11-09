// Safety & History: each project's record of every tool call an agent made and what keel's guard decided
// (engine/keel_engine/safety, served by the Python api: api-py/keel_api/safety/routes.py).

import { get } from "./api";

export type Tier = "T0" | "T1" | "T2" | "T3";
export type HistoryLine = {
  seq: number; at?: string; thread?: string; step?: string; agent?: string; source?: string;
  kind: "read" | "write" | "edit" | "tool" | "approval"; tool?: string; server?: string | null; text?: string; path?: string | null;
  action: "allow" | "deny"; ok?: boolean; tier?: Tier | null; rule?: string | null; reason?: string | null; undo?: string | null;
  /** approval lines: the person's answer (once | always | deny), or none when nobody answered */
  answer?: string | null; status?: string; by?: string | null;
  hash?: string; prev?: string;
};
export type HistoryCounts = { total: number; action: Record<string, number>; tier: Record<string, number>; rule: Record<string, number> };
export type HistoryPage = { items: HistoryLine[]; more: boolean; next: number | null; counts: HistoryCounts };
export type HistoryFilter = { thread?: string; action?: string; tier?: string; kind?: string; q?: string; before?: number };

const e = encodeURIComponent;

export const historyApi = {
  list: (pid: string, f: HistoryFilter = {}) => {
    const qs = Object.entries(f).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${e(String(v))}`).join("&");
    return get<HistoryPage>(`/projects/${e(pid)}/history${qs ? `?${qs}` : ""}`);
  },
  verify: (pid: string) => get<{ ok: boolean; text: string }>(`/projects/${e(pid)}/history/verify`),
};

export const TIER_WORDS: Record<Tier, string> = {
  T0: "read only", T1: "can be undone", T2: "hard to undo", T3: "cannot be undone",
};
