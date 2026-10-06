// The system and module levels of the Map on the same canvas as the database diagram: boxes with a header and rows,
// orthogonal lines, a toolbar (find, zoom, fit, reset, export), drag to move (remembered per project), a minimap.

import { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ApiEndpoint, KeelMap, MapLevel, MapNode } from "../../api";
import { Canvas, type CanvasHandle, type View } from "./Canvas";
import { download, svgToPng, toSvg } from "./exporter";
import { CH, HEAD, ROW, TITLE_CH } from "./geometry";
import { IconDefs, Ic } from "./icons";
import { layoutGraph, type Pt, type XY } from "./layout";
import { elbow, endMark, pathD, routeAround, type Rect } from "./route";
import { attr } from "./model";
import { fileHref } from "./Structure";

const SUB = 18, PAD = 6, MIN_W = 190, MAX_W = 420;

export type GRow = { t: string; method?: string; tip?: string };
export type GBox = {
  id: string; kind: "app" | "api" | "data" | "ext"; icon: string; title: string; sub?: string; rows: GRow[];
  drill?: "modules" | "er"; table?: string; cite?: { rel: string; line: number } | null; band?: string;
  /** another diagram's way in (the Graph page): the header's right text, e.g. "classes ›"; Enter or a double click opens */
  open?: string;
};
/** weight: how many uses the line stands for (drawn thicker); tip: its tooltip */
export type GEdge = { id: string; from: string; to: string; label?: string; weight?: number; tip?: string };
type Band = { id: string; label: string; sub: string };

const cut = (s: string, n: number) => (s.length <= n ? s : s.slice(0, Math.max(1, n - 1)) + "…");

function size(b: GBox): { w: number; h: number } {
  const rowLen = Math.max(0, ...b.rows.map((r) => r.t.length + (r.method ? 7 : 0)));
  const extra = b.drill ? (b.table ? 16 : 70) : b.open ? 14 + b.open.length * 6.6 : 0;
  const w = Math.round(Math.min(MAX_W, Math.max(MIN_W, 48 + b.title.length * TITLE_CH + extra, 24 + rowLen * CH, 24 + (b.sub?.length ?? 0) * 6.6)));
  return { w, h: HEAD + (b.sub ? SUB : 0) + b.rows.length * ROW + PAD + (b.rows.length || b.sub ? 2 : 0) };
}

// ------------------------------------------------------------------ from the engine's levels

const kindOf = (n: MapNode): GBox["kind"] => (n.kind === "data" ? "data" : n.kind === "ext" ? "api" : "app");

/** Endpoints grouped by the first path segment after what every path shares (/api/v1/...). */
export function endpointGroups(eps: ApiEndpoint[]): { prefix: string; groups: [string, ApiEndpoint[]][] } {
  const parts = eps.map((e) => e.path.split("/").filter(Boolean));
  let common = 0;
  if (parts.length > 1) {
    while (parts.every((p) => p.length > common + 1 && p[common] === parts[0][common] && !p[common].startsWith("{"))) common++;
  }
  const prefix = parts[0] ? "/" + parts[0].slice(0, common).join("/") : "";
  const by = new Map<string, number[]>();
  eps.forEach((_, i) => {
    const key = "/" + (parts[i][common] ?? "");
    by.set(key, [...(by.get(key) ?? []), i]);
  });
  // a big group (an /admin with fifty endpoints) splits once more, by its next segment
  const out = new Map<string, ApiEndpoint[]>();
  for (const [key, idx] of by) {
    const split = idx.length > 16 && idx.some((i) => parts[i][common + 1] && !parts[i][common + 1].startsWith("{"));
    for (const i of idx) {
      const next = parts[i][common + 1];
      const k = split && next && !next.startsWith("{") ? `${key}/${next}` : key;
      out.set(k, [...(out.get(k) ?? []), eps[i]]);
    }
  }
  return { prefix: prefix === "/" ? "" : prefix, groups: [...out.entries()].sort((a, b) => a[0].localeCompare(b[0])) };
}

export function systemBoxes(level: MapLevel): { boxes: GBox[]; edges: GEdge[] } {
  const boxes = level.nodes.map<GBox>((n) => ({
    id: n.id, kind: kindOf(n), icon: n.kind === "data" ? "db" : n.kind === "ext" ? "api" : "code", title: n.title, sub: n.sub,
    rows: (n.rows ?? []).map((r) => ({ t: r.t })), drill: n.drill === "modules" || n.drill === "er" ? n.drill : undefined, cite: n.cite,
  }));
  // the engine's lines run API -> code -> database; drawn left to right in that order
  const edges = (level.edges ?? []).map<GEdge>((e, i) => ({ id: `e${i}`, from: e.to, to: e.from, label: e.label }));
  return { boxes, edges };
}

export function moduleBoxes(m: KeelMap, level: MapLevel): { boxes: GBox[]; bands: Band[] } {
  const boxes: GBox[] = [];
  const mods = level.nodes.filter((n) => n.id.startsWith("mod:"));
  const tables = level.nodes.filter((n) => n.id.startsWith("tbl:"));
  for (const n of mods) boxes.push({ id: n.id, kind: "app", icon: "folder", title: n.title, sub: n.sub, rows: (n.rows ?? []).map((r) => ({ t: r.t })), band: "code" });
  const eps = m.api?.endpoints ?? [];
  if (eps.length) {
    const { prefix, groups } = endpointGroups(eps);
    for (const [key, list] of groups) {
      boxes.push({
        id: `api:${key}`, kind: "api", icon: "api", title: `${prefix}${key}`, sub: `${list.length} endpoint${list.length === 1 ? "" : "s"}`, band: "api",
        cite: list[0].cite ?? null,
        rows: list.map((e) => ({ method: e.method, t: e.path.slice(prefix.length) || "/", tip: [e.summary, e.operation].filter(Boolean).join(" · ") })),
      });
    }
  } else {
    const api = level.nodes.find((n) => n.id === "api:contract");
    if (api) boxes.push({ id: api.id, kind: "api", icon: "api", title: api.title, sub: api.sub, band: "api", cite: api.cite,
      rows: (api.rows ?? []).map((r) => {
        const [method, ...rest] = r.t.split(" ");
        return /^[A-Z]+$/.test(method) && rest.length ? { method, t: rest.join(" ") } : { t: r.t };
      }) });
  }
  const schemaTables = new Map((m.schema?.tables ?? []).map((t) => [t.id, t]));
  for (const n of tables) {
    const id = n.id.replace(/^tbl:/, "");
    const t = schemaTables.get(id);
    const fks = t?.foreign_keys?.filter((f) => f.ref_table).length ?? 0;
    boxes.push({ id: n.id, kind: "data", icon: "table", title: n.title, band: "db", drill: "er", table: id, cite: n.cite,
      rows: [{ t: t ? `${t.columns.length} columns${fks ? `, ${fks} FK` : ""}` : n.rows?.[0]?.t ?? "" }] });
  }
  const count = (b: string) => boxes.filter((x) => x.band === b).length;
  const bands: Band[] = [
    { id: "code", label: "Code", sub: `${m.counts?.modules ?? count("code")} top folders` },
    { id: "api", label: "API", sub: `${eps.length || count("api")} endpoints${m.api?.contract ? " in " + m.api.contract : ""}` },
    { id: "db", label: "Database", sub: `${count("db")} tables` },
  ].filter((b) => count(b.id));
  return { boxes, bands };
}

// ------------------------------------------------------------------ the module level's layout: bands of columns

function bandLayout(boxes: GBox[], bands: Band[], sizes: Map<string, { w: number; h: number }>) {
  const pos = new Map<string, XY>();
  const frames: (Band & Rect)[] = [];
  const total = boxes.reduce((s, b) => s + sizes.get(b.id)!.h + 16, 0);
  const colH = Math.max(560, Math.min(1300, Math.sqrt(total * 900) * 0.9));
  let x = 0;
  for (const band of bands) {
    const inBand = boxes.filter((b) => b.band === band.id);
    const top = 52;
    let cx = x + 18, cy = top, colW = 0, maxY = top;
    for (const b of inBand) {
      const s = sizes.get(b.id)!;
      if (cy > top && cy + s.h > top + colH) {
        cx += colW + 18;
        cy = top;
        colW = 0;
      }
      pos.set(b.id, { x: cx, y: cy });
      cy += s.h + 16;
      colW = Math.max(colW, s.w);
      maxY = Math.max(maxY, cy);
    }
    const w = cx + colW + 18 - x;
    frames.push({ ...band, x, y: 0, w, h: maxY + 4 });
    x += w + 40;
  }
  return { pos, frames, width: Math.max(0, x - 40), height: Math.max(0, ...frames.map((f) => f.h)) };
}

// ------------------------------------------------------------------ drawing

const Box = memo(function Box({ b, x, y, w, h, sel, match, active }: { b: GBox; x: number; y: number; w: number; h: number; sel: boolean; match: boolean; active: boolean }) {
  let y0 = HEAD + (b.sub ? SUB : 0);
  const maxRow = Math.floor((w - 24) / CH);
  return (
    <g className={`gbox k-${b.kind} ${sel ? "sel" : ""} ${match ? "match" : ""}`} transform={`translate(${x} ${y})`} data-tid={b.id}
      role="button" tabIndex={active ? 0 : -1} aria-pressed={sel}
      aria-label={`${b.title}${b.sub ? ", " + b.sub : ""}${b.drill ? `, opens the ${b.drill === "er" ? "database diagram" : "modules"}` : b.open ? ", opens it" : ""}`}>
      <rect className="erd-shadow" x={1.5} y={2.5} width={w} height={h} rx={6} />
      <rect className="erd-frame" width={w} height={h} rx={6} />
      <path className="erd-head" d={`M0 6a6 6 0 0 1 6-6H${w - 6}a6 6 0 0 1 6 6V${HEAD}H0Z`} />
      <path className="erd-sep" d={`M0 ${HEAD}H${w}`} />
      <use href={`#erd-i-${b.icon}`} className="g-ic" x={9} y={7} width={16} height={16} />
      <text className="erd-title" x={30} y={19.5}>{cut(b.title, Math.floor((w - 44 - (b.drill ? (b.table ? 14 : 64) : b.open ? 10 + b.open.length * 6.6 : 0)) / TITLE_CH))}</text>
      {b.drill && <text className="g-drill" x={w - 10} y={19.5} textAnchor="end">{b.table ? "›" : b.drill === "er" ? "diagram ›" : "modules ›"}</text>}
      {!b.drill && b.open && <text className="g-drill" x={w - 10} y={19.5} textAnchor="end">{b.open}</text>}
      {b.sub && <text className="g-sub" x={10} y={HEAD + 13}>{cut(b.sub, Math.floor((w - 20) / 6.6))}</text>}
      {b.rows.map((r, i) => {
        y0 += ROW;
        return (
          <text key={i} className="g-row" x={10} y={y0 - 5}>
            {r.tip && <title>{r.tip}</title>}
            {r.method && <tspan className={`g-meth m-${r.method.toLowerCase()}`}>{r.method.padEnd(7, " ")}</tspan>}
            {cut(r.t, maxRow - (r.method ? 7 : 0))}
          </text>
        );
      })}
    </g>
  );
});

// ------------------------------------------------------------------ the component

export function BoxDiagram({ pid, level, name, boxes, edges = [], bands, onDrill, label }: {
  pid: string; level: string; name: string; boxes: GBox[]; edges?: GEdge[]; bands?: Band[];
  onDrill: (b: GBox) => void;
  /** the canvas's name for screen readers; "System map of …" / "Modules map of …" by default */
  label?: string;
}) {
  const uid = useId().replace(/:/g, "");
  const key = `keel2.map.${pid}.${level}.pos`;
  const [moved, setMoved] = useState<Record<string, XY>>(() => {
    try { return JSON.parse(localStorage.getItem(key) ?? "{}"); } catch { return {}; }
  });
  const [drag, setDrag] = useState<{ id: string; x: number; y: number } | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [zoom, setZoom] = useState(1);
  const [exporting, setExporting] = useState<"svg" | "png" | null>(null);
  const [menu, setMenu] = useState(false);
  const canvas = useRef<CanvasHandle>(null);
  const scene = useRef<SVGGElement>(null);
  const defs = useRef<SVGSVGElement>(null);

  const sizes = useMemo(() => new Map(boxes.map((b) => [b.id, size(b)])), [boxes]);
  const auto = useMemo(() => {
    if (bands?.length) {
      const l = bandLayout(boxes, bands, sizes);
      return { pos: l.pos, routes: new Map<string, Pt[]>(), width: l.width, height: l.height, frames: l.frames };
    }
    const l = layoutGraph(boxes.map((b) => ({ id: b.id, ...sizes.get(b.id)! })), edges.map((e) => ({ id: e.id, from: e.from, to: e.to, fy: HEAD / 2, ty: HEAD / 2 })), { packGap: 120 });
    return { ...l, frames: [] as (Band & Rect)[] };
  }, [boxes, edges, bands, sizes]);
  const pos = useMemo(() => {
    const out = new Map(auto.pos);
    for (const [id, p] of Object.entries(moved)) if (out.has(id)) out.set(id, p);
    if (drag) out.set(drag.id, { x: drag.x, y: drag.y });
    return out;
  }, [auto, moved, drag]);
  const rect = useCallback((id: string): Rect => ({ ...pos.get(id)!, ...sizes.get(id)! }), [pos, sizes]);
  const routes = useMemo(() => {
    const out = new Map(auto.routes);
    const touched = new Set([...Object.keys(moved), ...(drag ? [drag.id] : [])]);
    for (const e of edges) {
      if (!touched.has(e.from) && !touched.has(e.to)) continue;
      const a = rect(e.from), b = rect(e.to);
      out.set(e.id, drag ? elbow(a, a.y + HEAD / 2, b, b.y + HEAD / 2)
        : routeAround(a, a.y + HEAD / 2, b, b.y + HEAD / 2, boxes.filter((x) => x.id !== e.from && x.id !== e.to).map((x) => rect(x.id))));
    }
    return out;
  }, [auto, moved, drag, edges, rect, boxes]);
  const world = useMemo(() => {
    let x0 = 0, y0 = 0, x1 = auto.width, y1 = auto.height;
    for (const b of boxes) {
      const r = rect(b.id);
      x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y); x1 = Math.max(x1, r.x + r.w); y1 = Math.max(y1, r.y + r.h);
    }
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }, [auto, boxes, rect]);

  const q = query.trim().toLowerCase();
  const matches = useMemo(() => new Set(q ? boxes.filter((b) => b.title.toLowerCase().includes(q) || b.rows.some((r) => r.t.toLowerCase().includes(q))).map((b) => b.id) : []), [q, boxes]);
  const byId = useMemo(() => new Map(boxes.map((b) => [b.id, b])), [boxes]);

  const select = (id: string | null, reveal = false) => {
    setSel(id);
    if (id && reveal) window.setTimeout(() => canvas.current?.reveal(rect(id), true), 0);
    if (id) window.setTimeout(() => {
      try { scene.current?.querySelector<SVGGElement>(`[data-tid="${attr(id)}"]`)?.focus({ preventScroll: true }); } catch { /* old browsers */ }
    }, 0);
  };

  const dragRef = useRef<{ id: string; sx: number; sy: number; ox: number; oy: number; moved: boolean; pointer: number } | null>(null);
  const onDown = (e: React.PointerEvent<SVGGElement>) => {
    const g = (e.target as Element).closest("[data-tid]") as SVGGElement | null;
    if (!g || e.button !== 0 || canvas.current?.el()?.classList.contains("space")) return;
    e.stopPropagation();
    const p = pos.get(g.dataset.tid!)!;
    dragRef.current = { id: g.dataset.tid!, sx: e.clientX, sy: e.clientY, ox: p.x, oy: p.y, moved: false, pointer: e.pointerId };
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  };
  const onMove = (e: React.PointerEvent<SVGGElement>) => {
    const d = dragRef.current;
    if (!d || d.pointer !== e.pointerId) return;
    const k = canvas.current?.view().k ?? 1;
    const dx = (e.clientX - d.sx) / k, dy = (e.clientY - d.sy) / k;
    if (!d.moved && Math.abs(dx) + Math.abs(dy) < 4 / k) return;
    d.moved = true;
    setDrag({ id: d.id, x: Math.round(d.ox + dx), y: Math.round(d.oy + dy) });
  };
  const onUp = (e: React.PointerEvent<SVGGElement>) => {
    const d = dragRef.current;
    if (!d || d.pointer !== e.pointerId) return;
    dragRef.current = null;
    if (d.moved && drag) {
      const next = { ...moved, [d.id]: { x: drag.x, y: drag.y } };
      setMoved(next);
      try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* private window */ }
    } else select(d.id);
    setDrag(null);
  };
  const onDouble = (e: React.MouseEvent<SVGGElement>) => {
    const g = (e.target as Element).closest("[data-tid]") as SVGGElement | null;
    const b = g ? byId.get(g.dataset.tid!) : undefined;
    if (b?.drill || b?.open) onDrill(b);
  };
  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest?.("input")) return;
    const k = e.key;
    const dirs: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (dirs[k]) {
      e.preventDefault();
      const [dx, dy] = dirs[k];
      if (!sel) return select(boxes[0]?.id ?? null, true);
      const f = rect(sel), cx = f.x + f.w / 2, cy = f.y + Math.min(f.h, 120) / 2;
      let best: string | null = null, score = Infinity;
      for (const b of boxes) {
        if (b.id === sel) continue;
        const r = rect(b.id), tx = r.x + r.w / 2 - cx, ty = r.y + Math.min(r.h, 120) / 2 - cy;
        const along = tx * dx + ty * dy, across = Math.abs(tx * dy - ty * dx);
        if (along <= 4 || across > along * 2.2) continue;
        if (along + across * 2.5 < score) { score = along + across * 2.5; best = b.id; }
      }
      if (best) select(best, true);
    } else if (k === "Enter" && sel) {
      const b = byId.get(sel);
      if (b?.drill || b?.open) onDrill(b);
    } else if (k === "Escape") { setSel(null); setQuery(""); }
    else if (k === "+" || k === "=") canvas.current?.zoomBy(1.25);
    else if (k === "-") canvas.current?.zoomBy(0.8);
    else if (k === "0") canvas.current?.zoomTo(1);
    else if (k === "f") canvas.current?.fit();
  };
  const onView = useCallback((v: View) => setZoom(v.k), []);
  const reset = () => {
    setMoved({});
    try { localStorage.removeItem(key); } catch { /* private window */ }
    window.setTimeout(() => canvas.current?.fit(), 0);
  };
  useEffect(() => {
    if (!exporting || !scene.current) return;
    const kind = exporting;
    const bg = getComputedStyle(canvas.current?.el() ?? document.body).getPropertyValue("--dg-bg").trim() || "#ffffff";
    const svg = toSvg(scene.current, defs.current?.querySelector("defs") ?? null, world, bg);
    const file = `${name || "project"}-${level}`.replace(/[^\w.-]+/g, "-");
    setExporting(null);
    if (kind === "svg") download(`${file}.svg`, new Blob([svg], { type: "image/svg+xml" }));
    else svgToPng(svg, world.w + 48, world.h + 48).then((b) => download(`${file}.png`, b)).catch(() => undefined);
  }, [exporting]); // eslint-disable-line react-hooks/exhaustive-deps

  const active = sel ?? boxes[0]?.id;
  const sceneEl = useMemo(() => (
    <g ref={scene} className="gscene" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onDoubleClick={onDouble}>
      {auto.frames.map((f) => (
        <g key={f.id} className="gband">
          <rect x={f.x} y={f.y} width={f.w} height={f.h} rx={12} />
          <text x={f.x + 18} y={f.y + 24}>{f.label}</text>
          <text className="g-bandsub" x={f.x + 18} y={f.y + 40}>{f.sub}</text>
        </g>
      ))}
      {edges.map((e) => {
        const pts = routes.get(e.id) ?? [];
        if (pts.length < 2) return null;
        const mark = endMark(pts[0], pts[1], "arrow");
        const mid = pts[Math.floor((pts.length - 1) / 2)], mid2 = pts[Math.floor((pts.length - 1) / 2) + 1] ?? mid;
        return (
          <g key={e.id} className="erd-edge gedge">
            {e.tip && <title>{e.tip}</title>}
            <path className="erd-line" d={pathD(pts)} style={e.weight ? { strokeWidth: Math.min(4.5, 1.1 + Math.log2(e.weight) * 0.7) } : undefined} />
            <path className="erd-mark arrow" d={mark.d} />
            {e.label && <text className="gedge-lbl" x={(mid[0] + mid2[0]) / 2} y={(mid[1] + mid2[1]) / 2 - 6} textAnchor="middle">{e.label}</text>}
          </g>
        );
      })}
      {boxes.map((b) => {
        const r = rect(b.id);
        return <Box key={b.id} b={b} x={r.x} y={r.y} w={r.w} h={r.h} sel={!exporting && sel === b.id} match={!exporting && matches.has(b.id)} active={b.id === active} />;
      })}
    </g>
  ), [auto, edges, routes, boxes, rect, sel, matches, exporting, active]); // eslint-disable-line react-hooks/exhaustive-deps

  const minimap = useMemo(() => (
    <g className="dg-mini-scene">
      {boxes.map((b) => {
        const r = rect(b.id);
        return <rect key={b.id} className={`${sel === b.id ? "sel" : ""} ${matches.has(b.id) ? "match" : ""}`} x={r.x} y={r.y} width={r.w} height={r.h} rx={4} />;
      })}
    </g>
  ), [boxes, rect, sel, matches]);

  const selected = sel ? byId.get(sel) : undefined;
  return (
    <div className="dg gd">
      <svg ref={defs} width="0" height="0" style={{ position: "absolute" }} aria-hidden="true"><IconDefs /></svg>
      <div className="dg-bar" role="toolbar" aria-label="Diagram tools">
        <div className="dg-search" role="search">
          <Ic name="search" />
          <input type="search" placeholder="Find a box" aria-label="Find a box" value={query} onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                const first = boxes.find((b) => matches.has(b.id));
                if (first) select(first.id, true);
              } else if (e.key === "Escape") setQuery("");
            }} />
        </div>
        {q && <span className="dg-check">{matches.size} match{matches.size === 1 ? "" : "es"}</span>}
        <span className="dg-gap" />
        <div className="dg-zoom" role="group" aria-label="Zoom">
          <button type="button" className="dg-btn icon" aria-label="Zoom out" onClick={() => canvas.current?.zoomBy(0.8)}><Ic name="minus" /></button>
          <span className="dg-pct mono" aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button type="button" className="dg-btn icon" aria-label="Zoom in" onClick={() => canvas.current?.zoomBy(1.25)}><Ic name="plus" /></button>
          <button type="button" className="dg-btn" aria-label="Fit to the window" onClick={() => canvas.current?.fit()}><Ic name="fit" /><span>Fit</span></button>
          <button type="button" className="dg-btn" aria-label="Actual size" onClick={() => canvas.current?.zoomTo(1)}>1:1</button>
        </div>
        <button type="button" className="dg-btn" onClick={reset} disabled={!Object.keys(moved).length}><Ic name="reset" /><span>Reset layout</span></button>
        <div className="dg-menuwrap">
          <button type="button" className="dg-btn" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)}
            onBlur={() => window.setTimeout(() => setMenu(false), 150)}><Ic name="export" /><span>Export</span></button>
          {menu && (
            <div className="dg-menu" role="menu">
              <button type="button" role="menuitem" onClick={() => { setMenu(false); setExporting("svg"); }}>SVG image</button>
              <button type="button" role="menuitem" onClick={() => { setMenu(false); setExporting("png"); }}>PNG image</button>
            </div>
          )}
        </div>
      </div>
      <div className="dg-body">
        <Canvas ref={canvas} world={world} label={label ?? `${level === "system" ? "System" : "Modules"} map of ${name}: ${boxes.length} boxes`}
          describedBy={`${uid}-help`} onView={onView} onBackground={() => setSel(null)} onKeyDown={onKey} grabbing={!!drag} minimap={minimap}
          start={boxes[0] ? rect(boxes[0].id) : null}>
          {sceneEl}
        </Canvas>
      </div>
      <div className="dg-status">
        {selected ? (
          <>
            <span className="dg-selinfo">{selected.title}</span>
            {selected.sub && <span>{selected.sub}</span>}
            {selected.drill && <button type="button" className="dg-link" onClick={() => onDrill(selected)}>{selected.drill === "er" ? "Open in the database diagram" : "Open the modules"}</button>}
            {!selected.drill && selected.open && <button type="button" className="dg-link" onClick={() => onDrill(selected)}>Open {selected.title}</button>}
            {selected.cite && <a className="dg-link" href={fileHref(selected.cite)}>{selected.cite.rel}:{selected.cite.line}</a>}
          </>
        ) : <span>{boxes.length} boxes{edges.length ? `, ${edges.length} lines` : ""}</span>}
        <span id={`${uid}-help`} className="dg-help">Drag the background to pan, Ctrl/⌘ + wheel to zoom, arrow keys to move, Enter or a double click opens.</span>
      </div>
    </div>
  );
}
