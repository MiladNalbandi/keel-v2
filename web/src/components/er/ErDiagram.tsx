// The database diagram, drawn like an IDE's: tables as boxes (header, key icons, typed columns), orthogonal lines
// from the exact foreign-key row to the key it references with crow's feet, an automatic layered layout, boxes you
// can drag (remembered per project), a toolbar, a minimap and a Structure panel for the selected table.

import { memo, useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import type { DbRelation, DbSchema, DbTable } from "../../api";
import { Canvas, type CanvasHandle, type View } from "./Canvas";
import { download, svgToPng, toSvg } from "./exporter";
import { boxShape, DEFAULT_PREFS, FOOT, HEAD, portOffset, ROW, type BoxShape, type Mode, type Prefs } from "./geometry";
import { IconDefs, Ic } from "./icons";
import { layoutGraph, type LEdge, type Pt, type XY } from "./layout";
import { attr, indexedColumns, relationIndex } from "./model";
import { elbow, endMark, pathD, routeAround, type EndKind, type Rect } from "./route";
import { fileHref, Structure } from "./Structure";

// ------------------------------------------------------------------ remembered per project (per browser)

const store = {
  get<T>(key: string, fallback: T): T {
    try {
      const v = localStorage.getItem(key);
      return v ? { ...fallback, ...JSON.parse(v) } : fallback;
    } catch {
      return fallback;
    }
  },
  set(key: string, v: unknown) {
    try {
      if (v === null) localStorage.removeItem(key);
      else localStorage.setItem(key, JSON.stringify(v));
    } catch {
      /* a private window: nothing is remembered */
    }
  },
};
export const prefsKey = (pid: string) => `keel2.er.${pid}.prefs`;
export const posKey = (pid: string, mode: Mode) => `keel2.er.${pid}.pos.${mode}`;

// ------------------------------------------------------------------ helpers

const hit = (pts: Pt[], r: Rect) => {
  for (let i = 0; i + 1 < pts.length; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[i + 1];
    const lx = Math.min(x1, x2), hx = Math.max(x1, x2), ly = Math.min(y1, y2), hy = Math.max(y1, y2);
    if (lx < r.x + r.w - 1 && hx > r.x + 1 && ly < r.y + r.h - 1 && hy > r.y + 1) return true;
  }
  return false;
};

function portFor(t: DbTable, s: BoxShape, cols: string[]): number {
  if (!cols.length) return HEAD / 2;
  return Math.min(...cols.map((c) => portOffset(t, s, c)));
}

function ends(r: DbRelation, notation: Prefs["notation"]): [EndKind, EndKind] {
  if (r.kind === "uses") return ["none", "arrow"];
  if (notation === "arrow") return ["none", "arrow"];
  return [r.one_to_one ? "one" : "many", r.nullable ? "zero-one" : "only-one"];
}

const relTitle = (r: DbRelation, names: Map<string, DbTable>) => {
  const n = (id: string) => names.get(id)?.name ?? id;
  if (r.kind === "uses") return `${n(r.from)} reads ${n(r.to)}`;
  const act = [r.on_delete && `on delete ${r.on_delete.toLowerCase()}`, r.on_update && `on update ${r.on_update.toLowerCase()}`].filter(Boolean).join(", ");
  return `${r.name ? r.name + ": " : ""}${n(r.from)}(${r.from_columns.join(", ")}) → ${n(r.to)}(${r.to_columns.join(", ")})${act ? ", " + act : ""}`;
};

// ------------------------------------------------------------------ the drawing

type BoxState = "" | "hi" | "dim";

const TableBox = memo(function TableBox({ t, s, x, y, sel, state, match, hiCols, lod, types, active, indexed }: {
  t: DbTable; s: BoxShape; x: number; y: number; sel: boolean; state: BoxState; match: boolean; hiCols: string; lod: boolean;
  types: boolean; active: boolean; indexed: string;
}) {
  const view = t.kind !== "table";
  const hiSet = new Set(hiCols ? hiCols.split("\u0000") : []);
  const idx = new Set(indexed ? indexed.split("\u0000") : []);
  const w = s.w, h = s.h;
  const label = `${view ? "View" : "Table"} ${t.schema ? t.schema + "." : ""}${t.name}, ${t.columns.length} column${t.columns.length === 1 ? "" : "s"}`;
  return (
    <g className={`erd-box ${view ? "view" : ""} ${sel ? "sel" : ""} ${state} ${match ? "match" : ""}`} transform={`translate(${x} ${y})`}
      data-tid={t.id} role="button" tabIndex={active ? 0 : -1} aria-label={label} aria-pressed={sel}>
      <rect className="erd-shadow" x={1.5} y={2.5} width={w} height={h} rx={6} />
      <rect className="erd-frame" width={w} height={h} rx={6} />
      <path className="erd-head" d={`M0 6a6 6 0 0 1 6-6H${w - 6}a6 6 0 0 1 6 6V${HEAD}H0Z`} />
      {s.rows.length > 0 || s.more ? <path className="erd-sep" d={`M0 ${HEAD}H${w}`} /> : null}
      <use href={view ? "#erd-i-view" : "#erd-i-table"} className="erd-tic" x={9} y={7} width={16} height={16} />
      <text className="erd-title" x={30} y={19.5}>{s.title}{s.schema && <tspan className="erd-schema" dx={7}>{s.schema}</tspan>}</text>
      {lod ? (
        s.rows.length ? <path className="erd-lod" d={s.rows.map((_, k) => `M12 ${HEAD + k * ROW + ROW / 2}H${Math.max(30, w * 0.62)}`).join("")} /> : null
      ) : s.rows.map((i, k) => {
        const c = t.columns[i];
        const cell = s.cells[k];
        const kind = c.pk && c.fk ? "pkfk" : c.pk ? "key" : c.fk ? "fkey" : c.unique ? "unique" : idx.has(c.name.toLowerCase()) ? "index" : "col";
        const tip = [`${c.name} ${c.type}`.trim(), c.nullable ? "null" : "not null", c.default ? `default ${c.default}` : "",
          c.fk ? `→ ${c.fk.table}${c.fk.column ? "." + c.fk.column : ""}` : "", c.comment ?? ""].filter(Boolean).join("  ·  ");
        return (
          <g key={c.name} className={`erd-row ${hiSet.has(c.name.toLowerCase()) ? "hi" : ""}`} transform={`translate(0 ${HEAD + k * ROW})`}>
            <title>{tip}</title>
            <rect className="erd-rowbg" x={1} y={0} width={w - 2} height={ROW} />
            <use href={`#erd-i-${kind}`} className={`erd-ic ${kind}`} x={10} y={4} width={12} height={12} />
            <text className={`erd-col ${c.nullable ? "" : "nn"} ${c.pk ? "pk" : ""}`} x={28} y={14}>{cell.name}</text>
            {types && cell.type && (
              <text className="erd-type" x={w - 10} y={14} textAnchor="end">{cell.type}{cell.nullable && <tspan className="erd-q">?</tspan>}</text>
            )}
          </g>
        );
      })}
      {s.more > 0 && (
        <text className="erd-more" x={w / 2} y={HEAD + s.rows.length * ROW + FOOT / 2 + 4} textAnchor="middle" data-more={t.id}>+{s.more} more</text>
      )}
    </g>
  );
});

const Edge = memo(function Edge({ r, pts, state, notation, title }: { r: DbRelation; pts: Pt[]; state: BoxState; notation: Prefs["notation"]; title: string }) {
  if (pts.length < 2) return null;
  const [a, b] = ends(r, notation);
  const m1 = endMark(pts[0], pts[1], a), m2 = endMark(pts[pts.length - 1], pts[pts.length - 2], b);
  const d = pathD(pts);
  return (
    <g className={`erd-edge ${r.kind} ${state} ${r.self ? "self" : ""}`} data-rid={r.id}>
      <title>{title}</title>
      <path className="erd-hit" d={d} />
      <path className="erd-line" d={d} />
      {m1.d && <path className={`erd-mark ${a}`} d={m1.d} />}
      {m2.d && <path className={`erd-mark ${b}`} d={m2.d} />}
      {m1.circle && <circle className="erd-o" cx={m1.circle[0]} cy={m1.circle[1]} r={m1.circle[2]} />}
      {m2.circle && <circle className="erd-o" cx={m2.circle[0]} cy={m2.circle[1]} r={m2.circle[2]} />}
    </g>
  );
});

// ------------------------------------------------------------------ the component

export type ErProps = {
  schema: DbSchema;
  pid: string;
  name: string;
  /** Select this table first (from another level's box). */
  initial?: string | null;
  toolbarExtra?: ReactNode;
};

export function ErDiagram({ schema, pid, name, initial, toolbarExtra }: ErProps) {
  const uid = useId().replace(/:/g, "");
  const [prefs, setPrefsState] = useState<Prefs>(() => store.get(prefsKey(pid), DEFAULT_PREFS));
  const setPrefs = (p: Partial<Prefs>) => {
    const next = { ...prefs, ...p };
    setPrefsState(next);
    store.set(prefsKey(pid), next);
  };
  const [moved, setMoved] = useState<Record<string, XY>>(() => store.get(posKey(pid, prefs.mode), {}));
  useEffect(() => setMoved(store.get(posKey(pid, prefs.mode), {})), [pid, prefs.mode]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [sel, setSel] = useState<string | null>(initial ?? null);
  const [hover, setHover] = useState<string | null>(null);
  const [hoverRel, setHoverRel] = useState<string | null>(null);
  const [panel, setPanel] = useState<boolean>(!!initial);
  const [query, setQuery] = useState("");
  const [lod, setLod] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [drag, setDrag] = useState<{ id: string; x: number; y: number } | null>(null);
  const [exporting, setExporting] = useState<"svg" | "png" | null>(null);
  const [menu, setMenu] = useState(false);
  const [viewMenu, setViewMenu] = useState(false);
  const [announce, setAnnounce] = useState("");
  const canvas = useRef<CanvasHandle>(null);
  const scene = useRef<SVGGElement>(null);
  const defs = useRef<SVGSVGElement>(null);
  const search = useRef<HTMLInputElement>(null);

  // ---- data
  const byId = useMemo(() => new Map(schema.tables.map((t) => [t.id, t])), [schema]);
  const rel = useMemo(() => relationIndex(schema), [schema]);
  const linked = useCallback((id: string) => {
    const n = rel.get(id);
    return !!n && (n.out.some((r) => byId.has(r.to)) || n.in.length > 0);
  }, [rel, byId]);
  const tables = useMemo(() => schema.tables.filter((t) => prefs.unrelated || linked(t.id)), [schema, prefs.unrelated, linked]);
  const hiddenCount = schema.tables.length - tables.length;
  const shapes = useMemo(() => new Map(tables.map((t) => [t.id, boxShape(t, prefs.mode, prefs.types, expanded.has(t.id))])),
    [tables, prefs.mode, prefs.types, expanded]);
  const relations = useMemo(() => schema.relations.filter((r) => shapes.has(r.from) && shapes.has(r.to)), [schema, shapes]);
  const ledges = useMemo<LEdge[]>(() => relations.map((r) => ({
    id: r.id, from: r.from, to: r.to,
    fy: r.kind === "uses" ? HEAD / 2 : portFor(byId.get(r.from)!, shapes.get(r.from)!, r.from_columns),
    ty: r.kind === "uses" ? HEAD / 2 : portFor(byId.get(r.to)!, shapes.get(r.to)!, r.to_columns),
  })), [relations, shapes, byId]);
  const auto = useMemo(() => layoutGraph(tables.map((t) => ({ id: t.id, w: shapes.get(t.id)!.w, h: shapes.get(t.id)!.h })), ledges),
    [tables, shapes, ledges]);

  // ---- positions: the layout, then what was moved by hand, then the box being dragged
  const pos = useMemo(() => {
    const out = new Map(auto.pos);
    for (const [id, p] of Object.entries(moved)) if (out.has(id)) out.set(id, p);
    if (drag) out.set(drag.id, { x: drag.x, y: drag.y });
    return out;
  }, [auto, moved, drag]);
  const rect = useCallback((id: string, p = pos): Rect => {
    const q = p.get(id)!, s = shapes.get(id)!;
    return { x: q.x, y: q.y, w: s.w, h: s.h };
  }, [pos, shapes]);

  // routes: the layout's, except lines touching a moved box (or crossing one): those go around the boxes
  const cache = useRef(new Map<string, Pt[]>());
  const settled = useMemo(() => {
    const movedIds = Object.keys(moved).filter((id) => shapes.has(id));
    if (!movedIds.length) return auto.routes;
    const base = new Map(auto.pos);
    for (const id of movedIds) base.set(id, moved[id]);
    const rects = new Map(tables.map((t) => [t.id, rect(t.id, base)]));
    const movedRects = movedIds.map((id) => rects.get(id)!);
    const out = new Map(auto.routes);
    for (const e of ledges) {
      const a = rects.get(e.from)!, b = rects.get(e.to)!;
      const touches = moved[e.from] || moved[e.to];
      const r = auto.routes.get(e.id);
      if (!touches && r && !movedRects.some((m) => m !== a && m !== b && hit(r, m))) continue;
      if (e.from === e.to) {
        const x = a.x + a.w, ox = x + 18;
        out.set(e.id, [[x, a.y + e.fy], [ox, a.y + e.fy], [ox, a.y + e.ty + 6], [x, a.y + e.ty + 6]]);
        continue;
      }
      const key = `${e.id}|${a.x},${a.y},${a.w},${a.h}|${b.x},${b.y},${b.w},${b.h}|${e.fy},${e.ty}`;
      const others = [...rects.values()].filter((o) => o !== a && o !== b);
      const old = cache.current.get(key);
      if (old && !others.some((o) => hit(old.slice(1, -1), o))) {
        out.set(e.id, old);
        continue;
      }
      const route = routeAround(a, a.y + e.fy, b, b.y + e.ty, others);
      cache.current.set(key, route);
      out.set(e.id, route);
    }
    return out;
  }, [auto, moved, ledges, tables, shapes, rect]);
  const routes = useMemo(() => {
    if (!drag) return settled;
    const out = new Map(settled);
    for (const e of ledges) {
      if (e.from !== drag.id && e.to !== drag.id) continue;
      const a = rect(e.from), b = rect(e.to);
      out.set(e.id, e.from === e.to
        ? [[a.x + a.w, a.y + e.fy], [a.x + a.w + 18, a.y + e.fy], [a.x + a.w + 18, a.y + e.ty + 6], [a.x + a.w, a.y + e.ty + 6]]
        : elbow(a, a.y + e.fy, b, b.y + e.ty));
    }
    return out;
  }, [settled, drag, ledges, rect]);

  const world = useMemo(() => {
    let x0 = 0, y0 = 0, x1 = auto.width, y1 = auto.height;
    for (const t of tables) {
      const r = rect(t.id);
      x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y); x1 = Math.max(x1, r.x + r.w); y1 = Math.max(y1, r.y + r.h);
    }
    for (const pts of routes.values()) for (const [x, y] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }, [auto, tables, rect, routes]);

  // ---- focus: the hovered (or selected) table, its lines and neighbours; the rest dims
  const relById = useMemo(() => new Map(relations.map((r) => [r.id, r])), [relations]);
  const focusRels = useMemo(() => {
    if (exporting) return null;
    if (hoverRel && relById.has(hoverRel)) return [relById.get(hoverRel)!];
    const f = hover ?? sel;
    if (!f || !shapes.has(f)) return null;
    return relations.filter((r) => r.from === f || r.to === f);
  }, [hover, hoverRel, sel, relations, relById, shapes, exporting]);
  const focusTables = useMemo(() => {
    if (!focusRels) return null;
    const s = new Set<string>();
    const f = hoverRel ? null : hover ?? sel;
    if (f) s.add(f);
    for (const r of focusRels) { s.add(r.from); s.add(r.to); }
    return s;
  }, [focusRels, hover, hoverRel, sel]);
  const hiCols = useMemo(() => {
    const m = new Map<string, Set<string>>();
    const add = (t: string, cols: string[]) => {
      const s = m.get(t) ?? new Set<string>();
      cols.forEach((c) => s.add(c.toLowerCase()));
      m.set(t, s);
    };
    for (const r of focusRels ?? []) { add(r.from, r.from_columns); add(r.to, r.to_columns); }
    return m;
  }, [focusRels]);

  // ---- search
  const q = query.trim().toLowerCase();
  const results = useMemo(() => {
    if (!q) return [];
    const byName = schema.tables.filter((t) => t.name.toLowerCase().includes(q) || t.id.toLowerCase().includes(q))
      .sort((a, b) => Number(!a.name.toLowerCase().startsWith(q)) - Number(!b.name.toLowerCase().startsWith(q)) || a.name.localeCompare(b.name))
      .map((t) => ({ t, col: null as string | null }));
    const seen = new Set(byName.map((r) => r.t.id));
    const byCol = schema.tables.filter((t) => !seen.has(t.id)).flatMap((t) => {
      const c = t.columns.find((x) => x.name.toLowerCase().includes(q));
      return c ? [{ t, col: c.name }] : [];
    });
    return [...byName, ...byCol].slice(0, 12);
  }, [q, schema]);
  const matchIds = useMemo(() => new Set(results.map((r) => r.t.id)), [results]);
  const [pick, setPick] = useState(0);
  const [listOpen, setListOpen] = useState(false);
  // the list closes a moment after the field loses focus (a click on a result lands first); coming back cancels that
  const closeList = useRef<number | undefined>(undefined);
  const openList = () => { window.clearTimeout(closeList.current); setListOpen(true); };
  useEffect(() => setPick(0), [q]);

  // ---- selection
  const reveal = useCallback((id: string, force = false) => {
    if (!shapes.has(id)) return;
    canvas.current?.reveal(rect(id), force);
  }, [shapes, rect]);
  const select = useCallback((id: string | null, opts: { reveal?: boolean; focus?: boolean; open?: boolean } = {}) => {
    if (id && !shapes.has(id)) {
      // hidden by "tables without relations": show them again so the pick lands somewhere
      if (byId.has(id)) setPrefs({ unrelated: true });
      else return;
    }
    setSel(id);
    if (opts.open) setPanel(true);
    if (id) {
      const t = byId.get(id)!;
      setAnnounce(`${t.name} selected. ${rel.get(id)?.out.length ?? 0} references, ${rel.get(id)?.in.length ?? 0} referenced by.`);
      if (opts.reveal) window.setTimeout(() => reveal(id, true), 0);
      if (opts.focus !== false) {
        window.setTimeout(() => {
          const el = scene.current?.querySelector<SVGGElement>(`[data-tid="${attr(id)}"]`);
          try { el?.focus({ preventScroll: true }); } catch { /* old browsers */ }
        }, 0);
      }
    } else setAnnounce("");
  }, [shapes, byId, rel, reveal]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (initial && byId.has(initial)) select(initial, { reveal: true, open: true, focus: false });
  }, [initial]); // eslint-disable-line react-hooks/exhaustive-deps

  const jump = (id: string) => {
    setListOpen(false);
    select(id, { reveal: true });
  };

  // ---- pointer on the boxes: click selects, drag moves, the "+N more" footer expands
  const dragRef = useRef<{ id: string; sx: number; sy: number; ox: number; oy: number; moved: boolean; pointer: number } | null>(null);
  // a press captures the pointer (for dragging), so the browser sends the double click to the scene, not the box:
  // the table pressed last is the one meant
  const pressed = useRef<string | null>(null);
  const onScenePointerDown = (e: React.PointerEvent<SVGGElement>) => {
    const t = e.target as Element;
    const g = t.closest("[data-tid]") as SVGGElement | null;
    pressed.current = g?.dataset.tid ?? null;
    if (!g || e.button !== 0 || canvasSpace()) return;
    e.stopPropagation();
    const id = g.dataset.tid!;
    const p = pos.get(id)!;
    dragRef.current = { id, sx: e.clientX, sy: e.clientY, ox: p.x, oy: p.y, moved: false, pointer: e.pointerId };
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  };
  const onScenePointerMove = (e: React.PointerEvent<SVGGElement>) => {
    const d = dragRef.current;
    if (!d || d.pointer !== e.pointerId) return;
    const k = canvas.current?.view().k ?? 1;
    const dx = (e.clientX - d.sx) / k, dy = (e.clientY - d.sy) / k;
    if (!d.moved && Math.abs(dx) + Math.abs(dy) < 4 / k) return;
    d.moved = true;
    setDrag({ id: d.id, x: Math.round(d.ox + dx), y: Math.round(d.oy + dy) });
  };
  const onScenePointerUp = (e: React.PointerEvent<SVGGElement>) => {
    const d = dragRef.current;
    if (!d || d.pointer !== e.pointerId) return;
    dragRef.current = null;
    if (d.moved && drag) {
      const next = { ...moved, [d.id]: { x: drag.x, y: drag.y } };
      setMoved(next);
      store.set(posKey(pid, prefs.mode), next);
      setDrag(null);
      return;
    }
    setDrag(null);
    const more = (e.target as Element).closest("[data-more]");
    if (more) {
      setExpanded((s) => new Set(s).add(d.id));
      return;
    }
    select(d.id, { focus: true });
  };
  const canvasSpace = () => !!canvas.current?.el()?.classList.contains("space");
  const onSceneOver = (e: React.PointerEvent<SVGGElement>) => {
    if (dragRef.current) return;
    const t = e.target as Element;
    const g = t.closest("[data-tid]") as SVGGElement | null;
    const r = t.closest("[data-rid]") as SVGGElement | null;
    setHover(g ? g.dataset.tid! : null);
    setHoverRel(!g && r ? r.dataset.rid! : null);
  };
  const onSceneLeave = () => { setHover(null); setHoverRel(null); };
  const onSceneDouble = (e: React.MouseEvent<SVGGElement>) => {
    const g = (e.target as Element).closest("[data-tid]") as SVGGElement | null;
    const id = g?.dataset.tid ?? pressed.current;
    if (id) select(id, { open: true });
  };

  // ---- keyboard: arrows walk between tables, Enter opens the structure, Esc clears
  const move = (dx: number, dy: number) => {
    const from = sel && shapes.has(sel) ? rect(sel) : null;
    if (!from) {
      const first = [...tables].sort((a, b) => rect(a.id).y - rect(b.id).y || rect(a.id).x - rect(b.id).x)[0];
      if (first) select(first.id, { reveal: true });
      return;
    }
    const cx = from.x + from.w / 2, cy = from.y + Math.min(from.h, 120) / 2;
    let best: string | null = null, score = Infinity;
    for (const t of tables) {
      if (t.id === sel) continue;
      const r = rect(t.id);
      const tx = r.x + r.w / 2 - cx, ty = r.y + Math.min(r.h, 120) / 2 - cy;
      const along = tx * dx + ty * dy, across = Math.abs(tx * dy - ty * dx);
      if (along <= 4 || across > along * 2.2) continue;
      const s = along + across * 2.5;
      if (s < score) { score = s; best = t.id; }
    }
    if (best) select(best, { reveal: true });
  };
  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest?.("input, textarea, select, .st")) return;
    const k = e.key;
    if (k === "ArrowLeft" || k === "ArrowRight" || k === "ArrowUp" || k === "ArrowDown") {
      e.preventDefault();
      move(k === "ArrowLeft" ? -1 : k === "ArrowRight" ? 1 : 0, k === "ArrowUp" ? -1 : k === "ArrowDown" ? 1 : 0);
    } else if (k === "Enter" || k === " ") {
      if (sel) {
        e.preventDefault();
        setPanel(true);
      }
    } else if (k === "Escape") {
      e.preventDefault();
      setSel(null);
      setPanel(false);
      setQuery("");
      setAnnounce("Selection cleared.");
    } else if (k === "+" || k === "=") canvas.current?.zoomBy(1.25);
    else if (k === "-") canvas.current?.zoomBy(0.8);
    else if (k === "0") canvas.current?.zoomTo(1);
    else if (k === "f") canvas.current?.fit();
    else if (k === "/") {
      e.preventDefault();
      search.current?.focus();
    }
  };

  // ---- view: a new layout (another mode, unlinked tables shown or hidden) fits itself to the window
  const firstLayout = useRef(true);
  useEffect(() => {
    if (firstLayout.current) {
      firstLayout.current = false;
      return;
    }
    canvas.current?.home(false);
  }, [auto]);
  const onView = useCallback((v: View) => {
    setZoom(v.k);
    setLod(v.k < 0.42);
  }, []);
  const resetLayout = () => {
    setMoved({});
    cache.current.clear();
    store.set(posKey(pid, prefs.mode), null);
    setExpanded(new Set());
    window.setTimeout(() => canvas.current?.fit(), 0);
  };

  // ---- export: draw once in full detail without the focus, then serialise
  useEffect(() => {
    if (!exporting || !scene.current) return;
    const kind = exporting;
    const bg = getComputedStyle(canvas.current?.el() ?? document.body).getPropertyValue("--dg-bg").trim()
      || getComputedStyle(document.body).backgroundColor;
    const shifted = toSvg(scene.current, defs.current?.querySelector("defs") ?? null, world, bg || "#ffffff");
    const file = `${name || "schema"}-database`.replace(/[^\w.-]+/g, "-");
    setExporting(null);
    if (kind === "svg") download(`${file}.svg`, new Blob([shifted], { type: "image/svg+xml" }));
    else {
      svgToPng(shifted, world.w + 48, world.h + 48).then((b) => download(`${file}.png`, b)).catch((err: Error) => setAnnounce(err.message));
    }
  }, [exporting]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- the scene (re-rendered only when what it shows changes, never on pan or zoom)
  const active = sel && shapes.has(sel) ? sel : tables[0]?.id;
  const names = byId;
  const sceneEl = useMemo(() => (
    <g ref={scene} className={`erd-scene ${focusTables ? "focusing" : ""} ${q ? "searching" : ""}`}
      onPointerDown={onScenePointerDown} onPointerMove={onScenePointerMove} onPointerUp={onScenePointerUp}
      onPointerOver={onSceneOver} onPointerLeave={onSceneLeave} onDoubleClick={onSceneDouble}>
      <g className="erd-edges">
        {[...relations].sort((a, b) => Number(!!focusRels?.includes(a)) - Number(!!focusRels?.includes(b))).map((r) => (
          <Edge key={r.id} r={r} pts={routes.get(r.id) ?? []} notation={prefs.notation} title={relTitle(r, names)}
            state={focusRels ? (focusRels.includes(r) ? "hi" : "dim") : ""} />
        ))}
      </g>
      <g className="erd-boxes">
        {tables.map((t) => {
          const p = pos.get(t.id)!;
          return (
            <TableBox key={t.id} t={t} s={shapes.get(t.id)!} x={p.x} y={p.y} sel={sel === t.id && !exporting}
              state={focusTables ? (focusTables.has(t.id) ? "hi" : "dim") : ""} match={!exporting && matchIds.has(t.id)}
              hiCols={[...(hiCols.get(t.id) ?? [])].join("\u0000")} lod={lod && !exporting} types={prefs.types}
              active={t.id === active} indexed={[...indexedColumns(t)].join("\u0000")} />
          );
        })}
      </g>
    </g>
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ), [relations, routes, tables, pos, shapes, sel, focusTables, focusRels, matchIds, hiCols, lod, prefs, exporting, active, q, names]);

  const minimap = useMemo(() => (
    <g className="dg-mini-scene">
      {tables.map((t) => {
        const r = rect(t.id);
        return <rect key={t.id} className={`${t.kind !== "table" ? "view" : ""} ${sel === t.id ? "sel" : ""} ${matchIds.has(t.id) ? "match" : ""}`}
          x={r.x} y={r.y} width={r.w} height={r.h} rx={4} />;
      })}
    </g>
  ), [tables, rect, sel, matchIds]);

  // the most connected table: where the diagram opens when all of it would be too small to read
  const start = useMemo(() => {
    let best: string | null = null, deg = -1;
    for (const t of tables) {
      const n = relations.filter((r) => r.from === t.id || r.to === t.id).length;
      if (n > deg) { deg = n; best = t.id; }
    }
    return best ? rect(best) : null;
  }, [tables, relations]); // eslint-disable-line react-hooks/exhaustive-deps
  const selected = sel ? byId.get(sel) : undefined;
  const fkCount = relations.filter((r) => r.kind === "fk").length;
  const tableCount = schema.tables.filter((t) => t.kind === "table").length, viewCount = schema.tables.length - tableCount;
  const modes: [Mode, string, string][] = [["all", "All columns", "Columns"], ["keys", "Keys only", "Keys"], ["names", "Names only", "Names"]];

  return (
    <div className={`dg erd ${panel && selected ? "with-panel" : ""}`}>
      <svg ref={defs} width="0" height="0" style={{ position: "absolute" }} aria-hidden="true"><IconDefs /></svg>
      <div className="dg-bar" role="toolbar" aria-label="Diagram tools">
        <div className="dg-search" role="search">
          <Ic name="search" />
          <input ref={search} type="search" placeholder="Find a table or column" aria-label="Find a table" value={query}
            role="combobox" aria-expanded={listOpen && !!q} aria-controls={`${uid}-res`} aria-autocomplete="list"
            aria-activedescendant={listOpen && results.length ? `${uid}-r${pick}` : undefined}
            onChange={(e) => { setQuery(e.target.value); openList(); }} onFocus={openList}
            onBlur={() => { closeList.current = window.setTimeout(() => setListOpen(false), 120); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setPick((p) => Math.min(results.length - 1, p + 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setPick((p) => Math.max(0, p - 1)); }
              else if (e.key === "Enter" && results[pick]) { e.preventDefault(); jump(results[pick].t.id); }
              else if (e.key === "Escape") { setQuery(""); canvas.current?.el()?.focus(); }
            }} />
          {q && listOpen && (
            <ul className="dg-results" id={`${uid}-res`} role="listbox" aria-label="Matching tables">
              {results.length ? results.map((r, i) => (
                <li key={r.t.id + (r.col ?? "")} id={`${uid}-r${i}`} role="option" aria-selected={i === pick}
                  className={i === pick ? "on" : ""} onMouseDown={(e) => { e.preventDefault(); jump(r.t.id); }}>
                  <svg width="14" height="14" aria-hidden="true" className={`dg-ric ${r.t.kind !== "table" ? "view" : ""}`}><use href={r.t.kind !== "table" ? "#erd-i-view" : "#erd-i-table"} /></svg>
                  <span className="mono">{r.t.name}</span>
                  {r.col && <span className="dg-rsub mono">.{r.col}</span>}
                  {r.t.schema && <span className="dg-rsub">{r.t.schema}</span>}
                </li>
              )) : <li className="dg-none" role="option" aria-selected="false">No table or column matches “{query.trim()}”</li>}
            </ul>
          )}
        </div>
        <div className="dg-seg" role="radiogroup" aria-label="Show">
          {modes.map(([m, label, short]) => (
            <button key={m} type="button" role="radio" aria-checked={prefs.mode === m} title={label} aria-label={label}
              className={prefs.mode === m ? "on" : ""} onClick={() => setPrefs({ mode: m })}>{short}</button>
          ))}
        </div>
        <div className="dg-menuwrap">
          <button type="button" className="dg-btn" aria-haspopup="menu" aria-expanded={viewMenu} onClick={() => setViewMenu((m) => !m)}
            onBlur={(e) => { if (!e.currentTarget.parentElement?.contains(e.relatedTarget as Node)) window.setTimeout(() => setViewMenu(false), 150); }}>
            <span className="dg-lbl-always">View</span><Ic name="chev" />
          </button>
          {viewMenu && (
            <div className="dg-menu left" role="menu" aria-label="View">
              {([["types", "Column types", prefs.types, () => setPrefs({ types: !prefs.types })],
                ["unrelated", "Tables without relations", prefs.unrelated, () => setPrefs({ unrelated: !prefs.unrelated })],
                ["notation", "Crow's foot line ends", prefs.notation === "crow", () => setPrefs({ notation: prefs.notation === "crow" ? "arrow" : "crow" })],
              ] as [string, string, boolean, () => void][]).map(([k, label, on, flip]) => (
                <button key={k} type="button" role="menuitemcheckbox" aria-checked={on} onClick={flip}>
                  <span className="dg-tick" aria-hidden="true">{on ? "✓" : ""}</span>{label}
                </button>
              ))}
            </div>
          )}
        </div>
        <span className="dg-gap" />
        <div className="dg-zoom" role="group" aria-label="Zoom">
          <button type="button" className="dg-btn icon" aria-label="Zoom out" title="Zoom out (−)" onClick={() => canvas.current?.zoomBy(0.8)}><Ic name="minus" /></button>
          <span className="dg-pct mono" aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button type="button" className="dg-btn icon" aria-label="Zoom in" title="Zoom in (+)" onClick={() => canvas.current?.zoomBy(1.25)}><Ic name="plus" /></button>
          <button type="button" className="dg-btn" aria-label="Fit to the window" title="Fit (F)" onClick={() => canvas.current?.fit()}><Ic name="fit" /><span>Fit</span></button>
          <button type="button" className="dg-btn" aria-label="Actual size" title="100% (0)" onClick={() => canvas.current?.zoomTo(1)}>1:1</button>
        </div>
        <button type="button" className="dg-btn" onClick={resetLayout} disabled={!Object.keys(moved).length && !expanded.size}
          title="Forget moved boxes and lay the diagram out again" aria-label="Reset layout"><Ic name="reset" /><span>Reset</span></button>
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
        {selected?.cite ? (
          <a className="dg-btn" href={fileHref(selected.cite)} title={`Open ${selected.cite.rel}:${selected.cite.line}`}><Ic name="file" /><span>Open migration</span></a>
        ) : (
          <button type="button" className="dg-btn" disabled title="Select a table to open the migration that creates it"><Ic name="file" /><span>Open migration</span></button>
        )}
        <button type="button" className={`dg-btn ${panel ? "on" : ""}`} aria-pressed={panel} onClick={() => setPanel((p) => !p)}
          title="The selected table's columns, keys, relations and indexes"><Ic name="panel" /><span>Structure</span></button>
        {toolbarExtra}
      </div>
      <div className="dg-body">
        <Canvas ref={canvas} world={world} label={`Database diagram of ${name}: ${tables.length} tables shown, ${fkCount} foreign keys`}
          describedBy={`${uid}-help`} onView={onView} onBackground={() => { setSel(null); setAnnounce(""); }}
          onKeyDown={onKey} grabbing={!!drag} minimap={minimap} start={start}>
          {sceneEl}
        </Canvas>
        {panel && (
          selected ? (
            <Structure t={selected} rel={rel.get(selected.id)} names={byId} onPick={(id) => select(id, { reveal: true, focus: false })}
              onClose={() => setPanel(false)} />
          ) : (
            <aside className="st st-empty" aria-label="Structure">
              <header className="st-head"><div className="st-title"><h3>Structure</h3></div>
                <button type="button" className="dg-btn icon" aria-label="Close the structure" onClick={() => setPanel(false)}><Ic name="close" /></button></header>
              <p className="st-sub st-body">Select a table to see its columns, keys, relations, indexes and the migration that defines it.</p>
            </aside>
          )
        )}
      </div>
      <div className="dg-status">
        <span>{tableCount} table{tableCount === 1 ? "" : "s"}{viewCount ? `, ${viewCount} view${viewCount === 1 ? "" : "s"}` : ""}, {fkCount} foreign key{fkCount === 1 ? "" : "s"}
          {hiddenCount ? <> · <button type="button" className="dg-link" onClick={() => setPrefs({ unrelated: true })}>{hiddenCount} unlinked hidden</button></> : null}</span>
        {selected && <span className="dg-selinfo mono">{selected.name}</span>}
        <span id={`${uid}-help`} className="dg-help">Drag the background to pan, Ctrl/⌘ + wheel to zoom, arrow keys to move between tables, Enter for the structure, Esc to clear.</span>
        <span className="sr-only" role="status" aria-live="polite">{announce}</span>
      </div>
    </div>
  );
}
