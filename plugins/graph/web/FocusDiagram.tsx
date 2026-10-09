// One symbol on the Graph page, like an IDE's call hierarchy drawn as a diagram: who uses it on the left (one or two
// steps away), the symbol in the middle with its members, what it uses on the right. A click selects a box and lights
// its lines; Enter or a double click puts that box in the middle. Zoom, pan and the minimap come from the Map's canvas.

import { memo, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  attr, Canvas, elbow, endMark, Ic, IconDefs, pathD, type CanvasHandle, type Pt, type Rect,
} from "@keel/web-sdk";
import type { GraphFocus, GraphNodeRef } from "./graphApi";
import { iconOf, kindWord, mainUse, usesText } from "./model";

type Focus = Extract<GraphFocus, { focus: unknown }>;

const W = 236, FW = 290, GAP = 120, VGAP = 12;
const NH = 50;                     // a neighbour: header + one line
const HEAD = 34, SUB = 18, ROW = 20, MAX_ROWS = 16;
const cut = (s: string, n: number) => (s.length <= n ? s : s.slice(0, Math.max(1, n - 1)) + "…");

export type Placed = { pos: Map<string, Rect>; width: number; height: number; focusRect: Rect; rows: number };

/** Columns left to right (-2, -1, focus, 1, 2), each centred on the focus; a far column follows its near one's order. */
export function placeFocus(data: Focus): Placed {
  const cols = [...new Set([0, ...data.nodes.map((n) => n.col)])].sort((a, b) => a - b);
  const xOf = (c: number) => {
    let x = 0;
    for (const k of cols) {
      if (k === c) return x;
      x += (k === 0 ? FW : W) + GAP;
    }
    return x;
  };
  const members = data.level === "unit" ? data.focus.members : [];
  const rows = Math.min(members.length, MAX_ROWS) + (members.length > MAX_ROWS ? 1 : 0);
  const fh = HEAD + SUB + rows * ROW + (rows ? 8 : 4);
  const near = new Map<string, number>();
  const pos = new Map<string, Rect>();
  const heights: number[] = [];
  const stacks = cols.filter((c) => c !== 0).map((c) => {
    let list = data.nodes.filter((n) => n.col === c);
    if (Math.abs(c) === 2) {
      // under the near box it hangs from, so the lines do not cross
      const parentOf = (n: GraphNodeRef) => Math.min(...data.edges.filter((e) => e.from === n.id || e.to === n.id)
        .map((e) => near.get(e.from === n.id ? e.to : e.from) ?? 999));
      list = [...list].sort((a, b) => parentOf(a) - parentOf(b));
    }
    list.forEach((n, i) => { if (Math.abs(c) === 1) near.set(n.id, i); });
    heights.push(list.length * (NH + VGAP) - VGAP);
    return { c, list };
  });
  const tallest = Math.max(fh, ...heights);
  const mid = tallest / 2;
  const focusRect = { x: xOf(0), y: Math.round(mid - fh / 2), w: FW, h: fh };
  for (const { c, list } of stacks) {
    const h = list.length * (NH + VGAP) - VGAP;
    let y = Math.round(mid - h / 2);
    for (const n of list) {
      pos.set(n.id, { x: xOf(c), y, w: W, h: NH });
      y += NH + VGAP;
    }
  }
  pos.set(data.focus.id, focusRect);
  const width = xOf(cols[cols.length - 1]) + (cols[cols.length - 1] === 0 ? FW : W);
  return { pos, width, height: tallest, focusRect, rows };
}

/** Lines from user to used; into and out of the middle box they fan out along its side, so they can be told apart. */
export function routeFocus(data: Focus, p: Placed): Map<number, Pt[]> {
  const out = new Map<number, Pt[]>();
  const f = data.focus.id;
  const sideOf = (id: string, other: string): "l" | "r" => ((p.pos.get(other)?.x ?? 0) < (p.pos.get(id)?.x ?? 0) ? "l" : "r");
  const fan = { l: [] as number[], r: [] as number[] };
  data.edges.forEach((e, i) => {
    if (e.from === f || e.to === f) fan[sideOf(f, e.from === f ? e.to : e.from)].push(i);
  });
  const portY = new Map<number, number>();
  for (const s of ["l", "r"] as const) {
    const list = fan[s].sort((a, b) => {
      const oa = data.edges[a].from === f ? data.edges[a].to : data.edges[a].from;
      const ob = data.edges[b].from === f ? data.edges[b].to : data.edges[b].from;
      return (p.pos.get(oa)?.y ?? 0) - (p.pos.get(ob)?.y ?? 0);
    });
    const top = p.focusRect.y + 10, span = Math.max(1, p.focusRect.h - 20);
    list.forEach((i, k) => portY.set(i, Math.round(top + ((k + 1) * span) / (list.length + 1))));
  }
  data.edges.forEach((e, i) => {
    const a = p.pos.get(e.from), b = p.pos.get(e.to);
    if (!a || !b) return;
    const ay = e.from === f ? portY.get(i)! : a.y + NH / 2;
    const by = e.to === f ? portY.get(i)! : b.y + NH / 2;
    out.set(i, elbow(a, ay, b, by));
  });
  return out;
}

const NodeBox = memo(function NodeBox({ n, r, sel, lit, active }: { n: GraphNodeRef; r: Rect; sel: boolean; lit: boolean; active: boolean }) {
  return (
    <g className={`gfn ${sel ? "sel" : ""} ${lit ? "lit" : ""} k-${n.kind}`} transform={`translate(${r.x} ${r.y})`} data-tid={n.id}
      role="button" tabIndex={active ? 0 : -1} aria-pressed={sel} aria-label={`${kindWord(n.kind)} ${n.name}, ${n.col < 0 ? "uses it" : "is used by it"}`}>
      <rect className="erd-shadow" x={1.5} y={2.5} width={r.w} height={r.h} rx={6} />
      <rect className="erd-frame" width={r.w} height={r.h} rx={6} />
      <use href={`#erd-i-${iconOf(n.kind)}`} className="g-ic" x={9} y={8} width={15} height={15} />
      <text className="erd-title" x={30} y={20}>{cut(n.name, 27)}</text>
      <text className="g-sub" x={10} y={40}>{cut(`${kindWord(n.kind)} · ${n.file.split("/").pop()}`, 34)}</text>
    </g>
  );
});

export function FocusDiagram({ data, name, onOpen, selected, onSelect }: {
  data: Focus; name: string; onOpen: (id: string) => void; selected: string | null; onSelect: (id: string | null) => void;
}) {
  const uid = useId().replace(/:/g, "");
  const canvas = useRef<CanvasHandle>(null);
  const scene = useRef<SVGGElement>(null);
  const [zoom, setZoom] = useState(1);
  const placed = useMemo(() => placeFocus(data), [data]);
  const routes = useMemo(() => routeFocus(data, placed), [data, placed]);
  const f = data.focus;
  const members = data.level === "unit" ? f.members : [];
  const byId = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data]);
  useEffect(() => { window.setTimeout(() => canvas.current?.home(false), 0); }, [data]);

  const lit = (i: number) => !!selected && (data.edges[i].from === selected || data.edges[i].to === selected);
  const focusBox = (
    <g className={`gfn gfocus ${selected === f.id ? "sel" : ""}`} transform={`translate(${placed.focusRect.x} ${placed.focusRect.y})`} data-tid={f.id}
      role="button" tabIndex={selected ? -1 : 0} aria-label={`${kindWord(f.kind)} ${f.name}, the symbol in the middle`}>
      <rect className="erd-shadow" x={1.5} y={2.5} width={FW} height={placed.focusRect.h} rx={7} />
      <rect className="erd-frame" width={FW} height={placed.focusRect.h} rx={7} />
      <path className="erd-head" d={`M0 7a7 7 0 0 1 7-7H${FW - 7}a7 7 0 0 1 7 7V${HEAD}H0Z`} />
      <use href={`#erd-i-${iconOf(f.kind)}`} className="g-ic" x={10} y={9} width={16} height={16} />
      <text className="erd-title gfocus-t" x={32} y={22}>{cut(f.name, 30)}</text>
      <text className="g-sub" x={10} y={HEAD + 13}>{cut(`${kindWord(f.kind)} · ${f.file.split("/").pop()}:${f.line}`, 42)}</text>
      {members.slice(0, MAX_ROWS).map((m, i) => (
        <g key={m.id} className="gmem" data-mid={m.id} transform={`translate(0 ${HEAD + SUB + i * ROW + 4})`}>
          <title>{`${kindWord(m.kind)} ${m.name}, line ${m.line}: used ${m.in}× from outside, uses ${m.out}× outside. Double-click to put it in the middle.`}</title>
          <rect className="gmem-hit" x={2} y={0} width={FW - 4} height={ROW} rx={3} />
          <use href={`#erd-i-${iconOf(m.kind)}`} className="g-ic sm" x={10} y={3} width={13} height={13} />
          <text className="g-row" x={29} y={14}>{cut(m.name, 26)}</text>
          {(m.in > 0 || m.out > 0) && <text className="g-cnt" x={FW - 10} y={14} textAnchor="end">{m.in ? `←${m.in}` : ""}{m.in && m.out ? " " : ""}{m.out ? `${m.out}→` : ""}</text>}
        </g>
      ))}
      {members.length > MAX_ROWS && <text className="erd-more" x={FW / 2} y={HEAD + SUB + MAX_ROWS * ROW + 18} textAnchor="middle">+{members.length - MAX_ROWS} more (in the panel)</text>}
    </g>
  );

  const order = useMemo(() => [f.id, ...data.nodes.map((n) => n.id)], [f.id, data.nodes]);
  const select = (id: string | null) => {
    onSelect(id);
    if (id) window.setTimeout(() => {
      try { scene.current?.querySelector<SVGGElement>(`[data-tid="${attr(id)}"]`)?.focus({ preventScroll: true }); } catch { /* old browsers */ }
    }, 0);
  };
  const onClick = (e: React.MouseEvent<SVGGElement>) => {
    const t = e.target as Element;
    const g = t.closest("[data-tid]") as SVGGElement | null;
    if (!g) return;
    e.stopPropagation();
    select(g.dataset.tid!);
  };
  const onDouble = (e: React.MouseEvent<SVGGElement>) => {
    const t = e.target as Element;
    const m = t.closest("[data-mid]") as SVGGElement | null;
    if (m) return onOpen(m.dataset.mid!);
    const g = t.closest("[data-tid]") as SVGGElement | null;
    if (g && g.dataset.tid !== f.id) onOpen(g.dataset.tid!);
  };
  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const k = e.key;
    if (k === "ArrowDown" || k === "ArrowUp" || k === "ArrowLeft" || k === "ArrowRight") {
      e.preventDefault();
      const cur = selected ?? f.id;
      const r = placed.pos.get(cur);
      if (!r) return select(f.id);
      const dx = k === "ArrowLeft" ? -1 : k === "ArrowRight" ? 1 : 0, dy = k === "ArrowUp" ? -1 : k === "ArrowDown" ? 1 : 0;
      let best: string | null = null, score = Infinity;
      for (const id of order) {
        if (id === cur) continue;
        const o = placed.pos.get(id)!;
        const tx = o.x + o.w / 2 - (r.x + r.w / 2), ty = o.y + o.h / 2 - (r.y + r.h / 2);
        const along = tx * dx + ty * dy, across = Math.abs(tx * dy - ty * dx);
        if (along <= 4 || across > along * 2.5) continue;
        if (along + across * 2 < score) { score = along + across * 2; best = id; }
      }
      if (best) select(best);
    } else if (k === "Enter" && selected && selected !== f.id) onOpen(selected);
    else if (k === "Escape") onSelect(null);
    else if (k === "+" || k === "=") canvas.current?.zoomBy(1.25);
    else if (k === "-") canvas.current?.zoomBy(0.8);
    else if (k === "f") canvas.current?.fit();
  };

  const sceneEl = (
    <g ref={scene} className="gscene" onClick={onClick} onDoubleClick={onDouble}>
      {data.edges.map((e, i) => {
        const pts = routes.get(i);
        if (!pts || pts.length < 2) return null;
        const end = endMark(pts[pts.length - 1], pts[pts.length - 2], "arrow");
        const midA = pts[Math.floor((pts.length - 1) / 2)], midB = pts[Math.floor((pts.length - 1) / 2) + 1] ?? midA;
        const userName = e.from === f.id ? f.name : byId.get(e.from)?.name ?? e.from;
        const usedName = e.to === f.id ? f.name : byId.get(e.to)?.name ?? e.to;
        return (
          <g key={i} className={`erd-edge gedge u-${mainUse(e.k)} ${lit(i) ? "lit" : ""} ${selected && !lit(i) ? "dim" : ""}`}>
            <title>{`${userName} uses ${usedName}: ${usesText(e.k)}${e.sites[0] ? ` (first at ${e.sites[0].file.split("/").pop()}:${e.sites[0].line})` : ""}`}</title>
            <path className="erd-line" d={pathD(pts)} style={{ strokeWidth: Math.min(4, 1.1 + Math.log2(e.n) * 0.6) }} />
            <path className="erd-mark arrow" d={end.d} />
            {e.n > 1 && <text className="gedge-lbl" x={(midA[0] + midB[0]) / 2} y={(midA[1] + midB[1]) / 2 - 6} textAnchor="middle">{e.n}</text>}
          </g>
        );
      })}
      {focusBox}
      {data.nodes.map((n) => (
        <NodeBox key={n.id} n={n} r={placed.pos.get(n.id)!} sel={selected === n.id} active={selected === n.id}
          lit={!!selected && data.edges.some((e) => (e.from === selected && e.to === n.id) || (e.to === selected && e.from === n.id))} />
      ))}
    </g>
  );
  const minimap = (
    <g className="dg-mini-scene">
      {[...placed.pos.entries()].map(([id, r]) => <rect key={id} className={selected === id || id === f.id ? "sel" : ""} x={r.x} y={r.y} width={r.w} height={r.h} rx={4} />)}
    </g>
  );
  const users = data.nodes.filter((n) => n.col < 0).length, used = data.nodes.filter((n) => n.col > 0).length;
  return (
    <div className="dg gd gfd">
      <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true"><IconDefs /></svg>
      <div className="dg-bar" role="toolbar" aria-label="Diagram tools">
        <span className="gfd-cols"><span>← uses it</span><b>{f.name}</b><span>it uses →</span></span>
        <span className="dg-gap" />
        <div className="dg-zoom" role="group" aria-label="Zoom">
          <button type="button" className="dg-btn icon" aria-label="Zoom out" onClick={() => canvas.current?.zoomBy(0.8)}><Ic name="minus" /></button>
          <span className="dg-pct mono" aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button type="button" className="dg-btn icon" aria-label="Zoom in" onClick={() => canvas.current?.zoomBy(1.25)}><Ic name="plus" /></button>
          <button type="button" className="dg-btn" aria-label="Fit to the window" onClick={() => canvas.current?.fit()}><Ic name="fit" /><span>Fit</span></button>
        </div>
      </div>
      <div className="dg-body">
        <Canvas ref={canvas} world={{ w: placed.width, h: placed.height }} label={`${f.name} in ${name}: ${users} use it, it uses ${used}`}
          describedBy={`${uid}-help`} onView={(v) => setZoom(v.k)} onBackground={() => onSelect(null)} onKeyDown={onKey} minimap={minimap}
          start={placed.focusRect}>
          {sceneEl}
        </Canvas>
      </div>
      <div className="dg-status">
        <span>{users} use{users === 1 ? "s" : ""} it · it uses {used}{data.depth === 2 ? " (two steps)" : ""}</span>
        <span className="gfd-legend" aria-label="Line styles">
          <span><i className="gl u-calls" />calls</span><span><i className="gl u-instantiates" />creates</span>
          <span><i className="gl u-implements" />implements / extends</span><span><i className="gl u-references" />refers to</span>
        </span>
        <span id={`${uid}-help`} className="dg-help">A click selects and lights a box's lines; Enter or a double click puts it in the middle (a member too).</span>
      </div>
    </div>
  );
}
