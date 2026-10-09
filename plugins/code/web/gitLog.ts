// v0.15.2 The Git log's logic, kept free of React so it is easy to test: the Log tab (one per Code page, its path is
// the branch it shows), the graph column (lanes from each commit's parents), ref badges and dates.

import type { LogRef, RefItem } from "./gitLogApi";
import { openTab, type Tabs } from "./model";

/** The Log tab's id. Its path: "" = the current branch, "*" = all branches, else a branch, remote branch or tag. */
export const LOG_TAB = "keel:log";
export const ALL = "*";

/** Open the Log tab (pinned) on a branch; an open Log tab moves to that branch. */
export function openLog(t: Tabs, branch = ""): Tabs {
  return setLogBranch(openTab(t, { kind: "log", path: branch }, true), branch);
}

/** The Log tab shows another branch (the person picked it in the tab). */
export const setLogBranch = (t: Tabs, branch: string): Tabs => ({
  ...t,
  tabs: t.tabs.map((x) => (x.id === LOG_TAB ? { ...x, path: branch } : x)),
});

export const logTitle = (branch: string) =>
  branch === ALL ? "Log: all branches" : branch ? `Log: ${branch}` : "Git log";

// ---------- the graph ----------

/** A line in one row, in lanes (x) and thirds of the row (y: 0 top, 1 the dot's middle, 2 bottom). */
export type Seg = { x1: number; y1: 0 | 1; x2: number; y2: 1 | 2; color: number };
/** One row of the graph: the commit's lane and colour, the lines through the row, and how many lanes it uses. */
export type GraphRow = { lane: number; color: number; width: number; segs: Seg[] };

export const GRAPH_COLORS = 6;

/**
 * Lanes like `git log --graph`, for commits in date order (children before parents). Each lane waits for one commit;
 * a commit takes the lane that waits for it (else a free one: a branch tip), its first parent keeps the lane, other
 * parents (a merge) get a lane of their own, and the lanes that waited for the same commit end in it.
 */
export function graphRows(commits: { sha: string; parents: string[] }[]): GraphRow[] {
  const lanes: (string | null)[] = [];
  const colors: number[] = [];
  let next = 0;
  const free = () => {
    const i = lanes.indexOf(null);
    return i < 0 ? lanes.length : i;
  };
  return commits.map((c) => {
    const above = lanes.slice();
    const aboveColors = colors.slice();
    let lane = above.indexOf(c.sha);
    if (lane < 0) {
      lane = free();
      lanes[lane] = c.sha;
      colors[lane] = next++ % GRAPH_COLORS;
    }
    const color = colors[lane];
    const segs: Seg[] = [];
    above.forEach((s, j) => {
      if (s === null) return;
      if (s === c.sha) segs.push({ x1: j, y1: 0, x2: lane, y2: 1, color: aboveColors[j] });
      else segs.push({ x1: j, y1: 0, x2: j, y2: 2, color: aboveColors[j] });
    });
    // the lanes that waited for this commit end here
    lanes.forEach((s, j) => {
      if (s === c.sha && j !== lane) lanes[j] = null;
    });
    const [first, ...rest] = c.parents;
    lanes[lane] = first ?? null;
    if (first) segs.push({ x1: lane, y1: 1, x2: lane, y2: 2, color });
    for (const p of rest) {
      let k = lanes.indexOf(p);
      if (k < 0) {
        k = free();
        lanes[k] = p;
        colors[k] = next++ % GRAPH_COLORS;
      }
      segs.push({ x1: lane, y1: 1, x2: k, y2: 2, color: colors[k] });
    }
    while (lanes.length && lanes[lanes.length - 1] === null) {
      lanes.pop();
      colors.pop();
    }
    const width = Math.max(lane, ...segs.map((s) => Math.max(s.x1, s.x2))) + 1;
    return { lane, color, width, segs };
  });
}

// ---------- refs and dates ----------

const KIND_ORDER: Record<LogRef["kind"], number> = { head: 0, local: 1, remote: 2, tag: 3 };

/** A commit's badges: the current branch first, then local branches, remote branches and tags. */
export const sortRefs = (refs: LogRef[]) =>
  [...refs].sort((a, b) => Number(b.current) - Number(a.current) || KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.name.localeCompare(b.name));

/** Remote branches by their remote: origin/main → ["origin", [{…, short: "main"}]]. */
export function byRemote(refs: RefItem[]): [string, (RefItem & { short: string })[]][] {
  const out = new Map<string, (RefItem & { short: string })[]>();
  for (const r of refs) {
    const i = r.name.indexOf("/");
    const remote = i < 0 ? r.name : r.name.slice(0, i);
    const list = out.get(remote) ?? [];
    list.push({ ...r, short: i < 0 ? r.name : r.name.slice(i + 1) });
    out.set(remote, list);
  }
  return [...out.entries()];
}

/** "14:05" today, "Oct 3" this year, "Oct 3, 2023" before. */
export function when(iso: string, now = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (d.toDateString() === now.toDateString()) return d.toTimeString().slice(0, 5);
  const opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString(undefined, opts);
}

/** The authors of the loaded commits, most commits first (the author filter's suggestions). */
export function authorsOf(commits: { author: string }[]): string[] {
  const n = new Map<string, number>();
  for (const c of commits) n.set(c.author, (n.get(c.author) ?? 0) + 1);
  return [...n.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([a]) => a);
}
