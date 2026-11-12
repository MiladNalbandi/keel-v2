// The org chart (#92): each role is a box, in levels from the top (the roles no one gives work to) down the delegate
// links, and lines join the boxes. A solid line is a work line: work goes down it (the arrow), reports come back up
// it. A dotted line is a side link between roles that do not give each other work: ask, approve or notify. A dot marks
// where a line meets a box. Nothing is drawn from a fixed shape: the lines come from the org's links.
//
// The boxes keep keel's normal layout; the lines are drawn over them after the layout, from the boxes' measured places
// (again when the chart's size changes). On a phone the chart scrolls sideways inside its panel, never the page.

import { useId, useLayoutEffect, useRef, useState } from "react";

export type ChartRole = { id: string; title: string; human: boolean; workflows: string[]; autonomy: "watch" | "propose" | "act";
  team: string[]; boss: string | null };
export type ChartLink = { from: string[]; to: string[]; can: string[] };

export const AUTONOMY: Record<string, string> = { watch: "only proposes", propose: "asks before it acts", act: "acts, calls you at the gates" };
const VERB: Record<string, string> = { delegate: "gives work to", report: "reports to", ask: "asks", approve: "approves for", notify: "tells" };

/** The roles in levels: who takes work from no one first, then each role's team below it. */
export function levels(roles: ChartRole[], links: ChartLink[]): ChartRole[][] {
  const byId = new Map(roles.map((r) => [r.id, r]));
  const placed = new Set<string>();
  let row = roles.filter((r) => !links.some((l) => l.can.includes("delegate") && l.to.includes(r.id) && !l.from.includes(r.id)));
  const out: ChartRole[][] = [];
  while (row.length) {
    row.forEach((r) => placed.add(r.id));
    out.push(row);
    const next: ChartRole[] = [];
    for (const r of row) for (const t of r.team) {
      const x = byId.get(t);
      if (x && !placed.has(t) && !next.includes(x)) next.push(x);
    }
    row = next;
  }
  const rest = roles.filter((r) => !placed.has(r.id));
  if (rest.length) out.push(rest);
  return out;
}

/** Two roles a line joins, and what each may do to the other. */
export type Pair = { a: string; b: string; ab: string[]; ba: string[] };

/** Every pair of roles that a link joins (once per pair, whichever way the links go). */
export function pairs(roles: ChartRole[], links: ChartLink[]): Pair[] {
  const known = new Set(roles.map((r) => r.id));
  const out = new Map<string, Pair>();
  for (const l of links) for (const s of l.from) for (const t of l.to) {
    if (s === t || !known.has(s) || !known.has(t)) continue;
    const [a, b] = s < t ? [s, t] : [t, s];
    const key = `${a} ${b}`;
    const p = out.get(key) ?? { a, b, ab: [], ba: [] };
    const list = s === a ? p.ab : p.ba;
    for (const c of l.can) if (!list.includes(c)) list.push(c);
    out.set(key, p);
  }
  return [...out.values()];
}

/** A work line runs from the boss to its team member; any other pair is a side line. */
export function lineOf(p: Pair): { kind: "work"; boss: string; kid: string } | { kind: "side" } {
  if (p.ab.includes("delegate")) return { kind: "work", boss: p.a, kid: p.b };
  if (p.ba.includes("delegate")) return { kind: "work", boss: p.b, kid: p.a };
  return { kind: "side" };
}

/** What a line means, in words (its tooltip, and the list under the chart). */
export function says(p: Pair, title: (id: string) => string): string {
  const one = (from: string, to: string, kinds: string[]) =>
    kinds.length ? `${title(from)} ${kinds.map((k) => VERB[k] ?? k).join(", ")} ${title(to)}` : "";
  return [one(p.a, p.b, p.ab), one(p.b, p.a, p.ba)].filter(Boolean).join("; ");
}

type Box = { l: number; t: number; r: number; b: number; cx: number };
type Pt = [number, number];
type Wire = { key: string; d: string; side: boolean; a: string; b: string; start: Pt; end: Pt;
  arrowStart: boolean; arrowEnd: boolean; title: string };

// how far an arrow's tip stops before the dot at the box, so the two never sit on each other
const ARROW_GAP = 6;

function place(box: HTMLElement, base: DOMRect): Box {
  const r = box.getBoundingClientRect();
  return { l: r.left - base.left, t: r.top - base.top, r: r.right - base.left, b: r.bottom - base.top, cx: (r.left + r.right) / 2 - base.left };
}

/** An end of a line moved back along its last segment, by `by` (the room for an arrow before the dot). */
function back(to: Pt, from: Pt, by: number): Pt {
  const dx = to[0] - from[0], dy = to[1] - from[1];
  const len = Math.hypot(dx, dy) || 1;
  const k = Math.min(by, len / 2) / len;
  return [to[0] - dx * k, to[1] - dy * k];
}

/** A wire through these points; an end with an arrow stops short of its dot. */
function wire(key: string, pts: Pt[], side: boolean, a: string, b: string, arrowStart: boolean, arrowEnd: boolean, title: string): Wire {
  const drawn = [...pts];
  if (arrowEnd) drawn[drawn.length - 1] = back(pts[pts.length - 1], pts[pts.length - 2], ARROW_GAP);
  if (arrowStart) drawn[0] = back(pts[0], pts[1], ARROW_GAP);
  const d = drawn.map(([x, y], i) => `${i ? "L" : "M"} ${x} ${y}`).join(" ");
  return { key, d, side, a, b, start: pts[0], end: pts[pts.length - 1], arrowStart, arrowEnd, title };
}

/** The lines, from where the boxes are now. */
function measure(el: HTMLElement, roles: ChartRole[], links: ChartLink[], title: (id: string) => string): Wire[] {
  const base = el.getBoundingClientRect();
  const boxes = new Map<string, Box>();
  el.querySelectorAll<HTMLElement>("[data-role]").forEach((n) => boxes.set(n.dataset.role ?? "", place(n, base)));
  const out: Wire[] = [];
  for (const p of pairs(roles, links)) {
    const a = boxes.get(p.a), b = boxes.get(p.b);
    if (!a || !b) continue;
    const line = lineOf(p);
    const words = says(p, title);
    const key = `${p.a} ${p.b}`;
    if (line.kind === "work") {
      const up = boxes.get(line.boss) as Box, down = boxes.get(line.kid) as Box;
      const x1 = up.cx, y1 = up.b, x2 = down.cx, y2 = down.t;
      // an elbow through the middle of the gap: a boss's lines share their trunk, like an org chart
      const mid = y1 + (y2 - y1) / 2;
      const pts: Pt[] = y2 > y1 + 4 ? [[x1, y1], [x1, mid], [x2, mid], [x2, y2]] : [[x1, y1], [x2, y2]];
      out.push(wire(key, pts, false, line.boss, line.kid, false, true, words));
      continue;
    }
    // a side line: across the gap between two boxes of one level, else from the upper box's bottom to the lower's top,
    // beside the work lines (shifted right) so it never hides one
    let pts: Pt[];
    let fromA: boolean;
    if (Math.abs(a.t - b.t) < 6) {
      const [left, right] = a.cx <= b.cx ? [a, b] : [b, a];
      fromA = left === a;
      const between = [...boxes.values()].some((x) => x !== a && x !== b && Math.abs(x.t - a.t) < 6 && x.cx > left.cx && x.cx < right.cx);
      const y = Math.max(left.t, right.t) + 22;
      // next to each other: straight across; else over the boxes between them, just above the level
      pts = !between ? [[left.r, y], [right.l, y]]
        : [[left.cx + 24, left.t], [left.cx + 24, Math.min(left.t, right.t) - 12], [right.cx - 24, Math.min(left.t, right.t) - 12], [right.cx - 24, right.t]];
    } else {
      const [up, down] = a.t < b.t ? [a, b] : [b, a];
      fromA = up === a;
      const x1 = up.cx + 26, x2 = down.cx + 26, y1 = up.b, y2 = down.t;
      const mid = y1 + (y2 - y1) / 2 + 7;
      pts = [[x1, y1], [x1, mid], [x2, mid], [x2, y2]];
    }
    // the arrows point where each side's links go
    const toEnd = fromA ? p.ab.length > 0 : p.ba.length > 0;
    const toStart = fromA ? p.ba.length > 0 : p.ab.length > 0;
    out.push(wire(key, pts, true, p.a, p.b, toStart, toEnd, words));
  }
  return out;
}

export function Chart({ roles, links, compact }: { roles: ChartRole[]; links: ChartLink[]; compact?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const centred = useRef(false);
  const uid = useId().replace(/:/g, "");
  const [drawn, setDrawn] = useState<{ wires: Wire[]; w: number; h: number }>({ wires: [], w: 0, h: 0 });
  const [hot, setHot] = useState<string | null>(null);
  const title = (id: string) => roles.find((r) => r.id === id)?.title ?? id;
  const shape = JSON.stringify([roles.map((r) => [r.id, r.title, r.team]), links]);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const draw = () => setDrawn({ wires: measure(el, roles, links, title), w: el.scrollWidth, h: el.scrollHeight });
    draw();
    // wider than its panel (a phone): it opens with the top of the org in the middle, not at its left edge
    const sc = scroller.current;
    if (sc && !centred.current && sc.scrollWidth > sc.clientWidth) {
      centred.current = true;
      sc.scrollLeft = (sc.scrollWidth - sc.clientWidth) / 2;
    }
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(draw);
    ro.observe(el);
    return () => ro.disconnect();
    // the shape is what the lines follow (a new array with the same roles draws nothing new)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape, compact]);
  if (!roles.length) return <p className="sub">No roles yet.</p>;
  const side = pairs(roles, links).filter((p) => lineOf(p).kind === "side");
  const touches = (w: Wire) => hot !== null && (w.a === hot || w.b === hot);
  return (
    <div className="org-chart-wrap">
      <div className="org-chart-scroll" ref={scroller}>
        <div className={`org-chart${compact ? " compact" : ""}${hot ? " has-hot" : ""}`} ref={box}>
          <svg className="org-wires" width={drawn.w} height={drawn.h} aria-hidden="true" data-testid="org-wires">
            <defs>
              <marker id={`${uid}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 1 1 L 9 5 L 1 9" className="org-arrow" />
              </marker>
            </defs>
            {drawn.wires.map((w) => (
              <g key={w.key} className={`org-wire${w.side ? " side" : " work"}${touches(w) ? " on" : ""}`} data-wire={w.side ? "side" : "work"}>
                <title>{w.title}</title>
                <path d={w.d} markerStart={w.arrowStart ? `url(#${uid}-arrow)` : undefined} markerEnd={w.arrowEnd ? `url(#${uid}-arrow)` : undefined} />
                <circle cx={w.start[0]} cy={w.start[1]} r="3.2" className="org-dot" />
                <circle cx={w.end[0]} cy={w.end[1]} r="3.2" className="org-dot" />
              </g>
            ))}
          </svg>
          {levels(roles, links).map((row, i) => (
            <div key={i} className="org-level">
              {row.map((r) => (
                <div key={r.id} data-role={r.id} className={`org-role${r.human ? " org-human" : ""}${hot === r.id ? " hot" : ""}`}
                  tabIndex={0} onMouseEnter={() => setHot(r.id)} onMouseLeave={() => setHot(null)} onFocus={() => setHot(r.id)}
                  onBlur={() => setHot(null)} aria-label={`${r.title}${r.human ? ", a person" : ""}`}>
                  <div className="org-role-head">
                    <b>{r.human ? "👤 " : ""}{r.title}</b><span className="sub mono">{r.id}</span>
                  </div>
                  {r.human ? <span className="sub">a person: gets reports and calls</span> : <span className="sub">{AUTONOMY[r.autonomy]}</span>}
                  {!compact && !r.human && <div className="chips">{r.workflows.map((w) => <span key={w} className="chip">{w}</span>)}</div>}
                  {!compact && r.team.length > 0 && <span className="sub">gives work to {r.team.map(title).join(", ")}</span>}
                  {!compact && r.boss && <span className="sub">reports to {title(r.boss)}</span>}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="org-legend" aria-hidden="true">
        <span><svg width="36" height="10"><path d="M 3 5 H 26" className="org-legend-work" /><path d="M 21 1.5 L 26 5 L 21 8.5" className="org-arrow" /><circle cx="3" cy="5" r="2.6" className="org-dot" /><circle cx="32" cy="5" r="2.6" className="org-dot" /></svg>work goes down, reports come up</span>
        <span><svg width="36" height="10"><path d="M 3 5 H 32" className="org-legend-side" /><circle cx="3" cy="5" r="2.6" className="org-dot" /><circle cx="32" cy="5" r="2.6" className="org-dot" /></svg>a side link: asks, approves or tells</span>
      </div>
      {side.length > 0 && !compact && (
        <ul className="org-side-list">
          {side.map((p) => <li key={`${p.a} ${p.b}`}>{says(p, title)}</li>)}
        </ul>
      )}
    </div>
  );
}
