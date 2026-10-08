// v0.15.2 Jobs and Live agents: a Running and a Finished tab with their counts, and one search box for both.
// Running calls are few and all loaded, so the browser filters them. Finished calls are many and the list has a
// limit, so the api searches them (GET /api/jobs?status=finished&q=…) and counts them (GET /api/jobs/count).

import { useCallback, useEffect, useRef, useState } from "react";
import type { Job } from "../api";
import { PROV } from "../format";
import { useLoad } from "../state";
import { Tabs } from "./ui";

export type RunTab = "running" | "finished";

/**
 * The tab this browser chose last (kept in localStorage under `key`). Before any choice: Running while something
 * runs, else Finished. That first pick is made once, so a call that starts or ends does not switch the tab.
 */
export function useRunTab(key: string, ready: boolean, running: number): [RunTab | null, (t: RunTab) => void] {
  const [tab, setTab] = useState<RunTab | null>(() => {
    try {
      const v = localStorage.getItem(key);
      return v === "running" || v === "finished" ? v : null;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    if (tab === null && ready) setTab(running ? "running" : "finished");
  }, [tab, ready, running]);
  const choose = useCallback((t: RunTab) => {
    setTab(t);
    try {
      localStorage.setItem(key, t);
    } catch {
      /* private window */
    }
  }, [key]);
  return [tab, choose];
}

/** `value` once it stopped changing for `ms`; an empty value comes at once. */
export function useDebounced(value: string, ms = 250): string {
  const [v, setV] = useState(value);
  useEffect(() => {
    if (!value) {
      setV(value);
      return;
    }
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** The search words: lower case, split at spaces. The api (Jobs.kt searchWords) splits the same way. */
export const searchWords = (q: string) => q.trim().toLowerCase().split(/\s+/).filter(Boolean);

/**
 * True when every word is in what the row shows: id, agent, provider (and its name), model, flow step, phase, AC,
 * status, project id or project name. The same fields the api searches.
 */
export function jobMatches(j: Job, words: string[], projectName?: string): boolean {
  if (!words.length) return true;
  const text = [j.id, j.project_id, projectName, j.agent, j.provider, PROV[j.provider], j.model, j.step, j.phase, j.ac, j.status]
    .filter(Boolean).join(" ").toLowerCase();
  return words.every((w) => text.includes(w));
}

/** The newest answer for the same filters (`base`), kept while the next one loads. */
function useKept<T extends { base: string }>(data: T | null, base: string): T | null {
  const kept = useRef<T | null>(null);
  if (data) kept.current = data;
  return kept.current?.base === base ? kept.current : null;
}

/**
 * The finished calls for the filters in `base` and the search `q`, and how many there are without the limit.
 * While a new search loads, the last answer for the same filters stays (the page filters it in the browser), so
 * nothing says "Loading" between two key presses. `rows` is null until the first answer for these filters.
 */
export function useFinished(base: string, q: string, list: (q: string) => Promise<Job[]>, count: (q: string) => Promise<{ count: number }>) {
  const rows = useLoad(`fin:${base}:${q}`, () => list(q).then((r) => ({ base, q, rows: r })));
  const total = useLoad(`fin-n:${base}:${q}`, () => count(q).then((r) => ({ base, q, n: typeof r?.count === "number" ? r.count : null })));
  const keptRows = useKept(rows.data, base);
  const keptTotal = useKept(total.data, base);
  const reloadRows = rows.reload;
  const reloadTotal = total.reload;
  const reload = useCallback(() => {
    void reloadRows();
    void reloadTotal();
  }, [reloadRows, reloadTotal]);
  return {
    rows: keptRows?.rows ?? null,
    /** the search the rows were loaded for */
    q: keptRows?.q ?? null,
    /** all matches, past the limit (null when the api cannot count) */
    total: keptTotal && keptTotal.q === keptRows?.q ? keptTotal.n : null,
    error: rows.error,
    reload,
  };
}

/** Running | Finished, each with its count (no count while it loads). `first` names the first tab. */
export function RunTabs({ value, onChange, running, finished, first = "Running", label }: {
  value: RunTab; onChange: (t: RunTab) => void; running: number | null; finished: number | null; first?: string; label: string;
}) {
  const n = (c: number | null) => (c === null ? null : <>{" "}<span className="tab-n num">{c}</span></>);
  return <Tabs value={value} onChange={onChange} label={label} options={[["running", <>{first}{n(running)}</>], ["finished", <>Finished{n(finished)}</>]]} />;
}

/** The search box of the Running and Finished tabs. */
export function RunSearch({ value, onChange, label, placeholder }: { value: string; onChange: (v: string) => void; label: string; placeholder: string }) {
  return (
    <input type="search" className="inline-input pg-search run-search" aria-label={label} placeholder={placeholder} value={value}
      onChange={(e) => onChange(e.target.value)} />
  );
}
