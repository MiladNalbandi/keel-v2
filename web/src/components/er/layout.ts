// Automatic layout for box-and-line diagrams (the database diagram, the system and module views): a hand-written
// layered (Sugiyama) layout, left to right, with ports on the box sides and orthogonal routes.
//
//   1. connected components are laid out one by one, then packed into rows; boxes without any line go in a grid
//   2. layers: a referenced box sits left of the boxes that reference it (longest path, cycles broken by DFS); a layer
//      taller than the drawing's target height wraps into the next layer, so big schemas stay close to the screen's shape
//   3. order inside a layer: barycentre sweeps on the port positions, keeping the order with the fewest crossings
//   4. vertical position: each box moves towards the ports it links to (so most lines run straight), solved per layer as
//      an isotonic regression that keeps the order and the gaps
//   5. routes: out of the exact row, a vertical in the channel between two layers (one track per line, lines into the
//      same port share one track), through the gap a dummy node kept open in every layer a long line crosses
// Everything is deterministic: the same schema gives the same picture.

export type LNode = { id: string; w: number; h: number };
/** from = the referencing box (a foreign key's table), to = the referenced box; fy / ty = port offsets from the box top. */
export type LEdge = { id: string; from: string; to: string; fy: number; ty: number };
export type Pt = [number, number];
export type XY = { x: number; y: number };
export type LayoutResult = { pos: Map<string, XY>; routes: Map<string, Pt[]>; width: number; height: number };
export type LayoutOpts = { aspect?: number; gapY?: number; packGap?: number; loose?: "grid" | "row" };

const GAP_BOX = 30, GAP_DUMMY_BOX = 16, GAP_DUMMY = 10;
const TRACK = 10, CHANNEL_MIN = 56, CHANNEL_PAD = 22;

type N = {
  id: string; w: number; h: number; dummy: boolean;
  layer: number; y: number;
};
type Seg = { hi: N; hy: number; lo: N; ly: number; edge: string; bundle: string };

// ------------------------------------------------------------------ public

export function layoutGraph(nodes: LNode[], edges: LEdge[], opts: LayoutOpts = {}): LayoutResult {
  const aspect = opts.aspect ?? 1.6;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const real = edges.filter((e) => byId.has(e.from) && byId.has(e.to));
  const linking = real.filter((e) => e.from !== e.to);
  // components (union-find over the lines between different boxes)
  const parent = new Map(nodes.map((n) => [n.id, n.id]));
  const find = (x: string): string => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    let c = x;
    while (parent.get(c) !== r) {
      const nx = parent.get(c)!;
      parent.set(c, r);
      c = nx;
    }
    return r;
  };
  for (const e of linking) {
    const a = find(e.from), b = find(e.to);
    if (a !== b) parent.set(a < b ? b : a, a < b ? a : b);
  }
  const groups = new Map<string, LNode[]>();
  for (const n of nodes) {
    const r = find(n.id);
    groups.set(r, [...(groups.get(r) ?? []), n]);
  }
  const blocks: Block[] = [];
  const loose: LNode[] = [];
  for (const members of groups.values()) {
    if (members.length === 1) {
      loose.push(members[0]);
      continue;
    }
    const ids = new Set(members.map((m) => m.id));
    blocks.push(layoutComponent(members, linking.filter((e) => ids.has(e.from)), aspect));
  }
  blocks.sort((a, b) => b.w * b.h - a.w * a.h || (a.key < b.key ? -1 : 1));
  if (loose.length) blocks.push(looseGrid(loose.sort((a, b) => (a.id < b.id ? -1 : 1)), aspect, blocks));
  const out = pack(blocks, aspect, opts.packGap ?? 90);
  // self references: a loop on the right side, from the referencing row back into the key row
  const loops = new Map<string, number>();
  for (const e of real) {
    if (e.from !== e.to) continue;
    const p = out.pos.get(e.from)!, n = byId.get(e.from)!;
    const k = loops.get(e.from) ?? 0;
    loops.set(e.from, k + 1);
    const x = p.x + n.w, ox = x + 18 + k * 8;
    const y1 = p.y + e.fy, y2 = p.y + e.ty + (Math.abs(e.fy - e.ty) < 1 ? 6 : 0);
    out.routes.set(e.id, [[x, y1], [ox, y1], [ox, y2], [x, y2]]);
    out.width = Math.max(out.width, ox + 10);
  }
  return out;
}

// ------------------------------------------------------------------ one component

type Block = { key: string; w: number; h: number; pos: Map<string, XY>; routes: Map<string, Pt[]> };

function layoutComponent(members: LNode[], edges: LEdge[], aspect: number): Block {
  const ids = members.map((m) => m.id).sort();
  const byId = new Map(members.map((m) => [m.id, m]));
  // 1. break cycles: DFS from each box in id order; an edge back into the stack is turned around
  const outs = new Map<string, LEdge[]>(ids.map((i) => [i, []]));
  for (const e of edges) outs.get(e.from)!.push(e);
  const state = new Map<string, number>();
  const reversed = new Set<string>();
  const visit = (u: string) => {
    state.set(u, 1);
    for (const e of outs.get(u)!) {
      const s = state.get(e.to) ?? 0;
      if (s === 1) reversed.add(e.id);
      else if (s === 0) visit(e.to);
    }
    state.set(u, 2);
  };
  for (const i of ids) if (!state.get(i)) visit(i);
  // hi = the box drawn right (it references), lo = the box drawn left (it is referenced)
  const dag = edges.map((e) => (reversed.has(e.id)
    ? { e, hi: e.to, lo: e.from, hy: e.ty, ly: e.fy, rev: true }
    : { e, hi: e.from, lo: e.to, hy: e.fy, ly: e.ty, rev: false }));
  const parentsOf = new Map<string, string[]>(ids.map((i) => [i, []]));
  const childrenOf = new Map<string, string[]>(ids.map((i) => [i, []]));
  for (const d of dag) {
    parentsOf.get(d.hi)!.push(d.lo);
    childrenOf.get(d.lo)!.push(d.hi);
  }
  // 2. layers: longest path from the referenced boxes, then roots move right next to their nearest child
  const layer = new Map<string, number>();
  const depth = (u: string): number => {
    const known = layer.get(u);
    if (known !== undefined) return known;
    layer.set(u, 0);
    const ps = parentsOf.get(u)!;
    const v = ps.length ? 1 + Math.max(...ps.map(depth)) : 0;
    layer.set(u, v);
    return v;
  };
  ids.forEach(depth);
  for (let pass = 0; pass < 4; pass++) {
    for (const u of ids) {
      if (parentsOf.get(u)!.length) continue;
      const ch = childrenOf.get(u)!;
      if (ch.length) layer.set(u, Math.max(0, Math.min(...ch.map((c) => layer.get(c)!)) - 1));
    }
  }
  let layers = compact(ids, layer);
  // wrap a layer taller than the target height: leaves stay, boxes with children move on (nearer to their children)
  const area = members.reduce((s, m) => s + (m.w + 90) * (m.h + GAP_BOX), 0);
  const cap = Math.max(Math.max(...members.map((m) => m.h)) + GAP_BOX, Math.sqrt(area / aspect) * 1.08);
  for (let l = 0; l < layers.length; l++) {
    const prev = new Set(l ? layers[l - 1] : []);
    const ranked = [...layers[l]].sort((a, b) =>
      childrenOf.get(a)!.length - childrenOf.get(b)!.length
      || parentsOf.get(b)!.filter((p) => prev.has(p)).length - parentsOf.get(a)!.filter((p) => prev.has(p)).length
      || (a < b ? -1 : 1));
    let used = 0, keep = 0;
    for (const id of ranked) {
      const h = byId.get(id)!.h + GAP_BOX;
      if (keep && used + h > cap) break;
      used += h;
      keep++;
    }
    if (keep < ranked.length) {
      const moved = ranked.slice(keep);
      layers = [...layers.slice(0, l), ranked.slice(0, keep), moved, ...layers.slice(l + 1)];
      // everything to the right moves one layer on, so references still point left
      layers.forEach((ls, k) => ls.forEach((id) => layer.set(id, k)));
    }
  }
  // dummies: one per layer a long line crosses
  const nodes = new Map<string, N>(members.map((m) => [m.id, { id: m.id, w: m.w, h: m.h, dummy: false, layer: layer.get(m.id)!, y: 0 }]));
  const rows: N[][] = layers.map((ls) => ls.map((id) => nodes.get(id)!));
  // Long lines into the same port share their dummies: a key many tables reference is reached by one trunk that
  // branches near the tables (edge grouping), not by a fan of parallel lines across the drawing.
  const chains = new Map<string, { nodes: N[]; ys: number[]; rev: boolean }>();
  const segs: Seg[][] = layers.map(() => []);                    // segs[l]: between layer l (hi side) and l - 1
  const shared = new Map<string, N>();
  const segKeys = new Set<string>();
  for (const d of dag) {
    const hi = nodes.get(d.hi)!, lo = nodes.get(d.lo)!;
    const chain: N[] = [hi];
    for (let l = hi.layer - 1; l > lo.layer; l--) {
      const key = `~${lo.id}~${d.ly}~${l}`;
      let dn = shared.get(key);
      if (!dn) {
        dn = { id: key, w: 0, h: 0, dummy: true, layer: l, y: 0 };
        shared.set(key, dn);
        rows[l].push(dn);
      }
      chain.push(dn);
    }
    chain.push(lo);
    const ys = chain.map((_, k) => (k === 0 ? d.hy : k === chain.length - 1 ? d.ly : 0));
    chains.set(d.e.id, { nodes: chain, ys, rev: d.rev });
    for (let k = 0; k + 1 < chain.length; k++) {
      const sk = `${chain[k].id}:${ys[k]}>${chain[k + 1].id}:${ys[k + 1]}`;
      if (segKeys.has(sk)) continue;
      segKeys.add(sk);
      segs[chain[k].layer].push({ hi: chain[k], hy: ys[k], lo: chain[k + 1], ly: ys[k + 1], edge: d.e.id,
        bundle: `${chain[k + 1].id}:${ys[k + 1]}` });
    }
  }
  // 3. order: start from a breadth-first walk, then barycentre sweeps; keep the best
  initialOrder(rows, segs);
  restack(rows);
  let best = rows.map((r) => [...r]);
  let bestX = crossings(rows, segs);
  for (let it = 0; it < 14 && bestX > 0; it++) {
    const down = it % 2 === 0;
    for (let k = 0; k < rows.length; k++) {
      const l = down ? k : rows.length - 1 - k;
      sweep(rows, segs, l, down ? "left" : "right");
    }
    const x = crossings(rows, segs);
    if (x < bestX) {
      bestX = x;
      best = rows.map((r) => [...r]);
    }
  }
  for (let l = 0; l < rows.length; l++) rows[l] = best[l];
  restack(rows);
  // 4. vertical positions
  for (let it = 0; it < 12; it++) {
    const sides: ("left" | "right" | "both")[] = it < 10 ? [it % 2 ? "right" : "left"] : ["both"];
    for (const side of sides) {
      const order = side === "right" ? rows.map((_, i) => rows.length - 1 - i) : rows.map((_, i) => i);
      for (const l of order) placeLayer(rows[l], segs, l, side);
    }
  }
  const minY = Math.min(...rows.flat().map((n) => n.y));
  rows.flat().forEach((n) => (n.y = Math.round(n.y - minY)));
  // 5. channels: tracks for the verticals between two layers, then x
  const widths = rows.map((r) => Math.max(0, ...r.map((n) => n.w)));
  const tracks: Map<string, number>[] = rows.map(() => new Map());
  const channelW: number[] = rows.map(() => CHANNEL_MIN);
  for (let l = 1; l < rows.length; l++) {
    const t = assignTracks(segs[l]);
    tracks[l] = t.track;
    channelW[l] = Math.max(CHANNEL_MIN, CHANNEL_PAD * 2 + (t.count - 1) * TRACK);
  }
  const xs: number[] = [];
  let x = 0;
  for (let l = 0; l < rows.length; l++) {
    if (l) x += channelW[l];
    xs.push(x);
    x += widths[l];
  }
  const boxX = (n: N) => xs[n.layer] + (widths[n.layer] - n.w) / 2;
  const trackX = (l: number, key: string) => {
    const count = Math.max(1, ...[...tracks[l].values()].map((v) => v + 1));
    const left = xs[l - 1] + widths[l - 1];
    return left + (channelW[l] - (count - 1) * TRACK) / 2 + (tracks[l].get(key) ?? 0) * TRACK;
  };
  const pos = new Map<string, XY>();
  for (const n of nodes.values()) pos.set(n.id, { x: boxX(n), y: n.y });
  const routes = new Map<string, Pt[]>();
  for (const [id, c] of chains) {
    const hi = c.nodes[0], lo = c.nodes[c.nodes.length - 1];
    const pts: Pt[] = [[boxX(hi), hi.y + c.ys[0]]];
    for (let k = 0; k + 1 < c.nodes.length; k++) {
      const a = c.nodes[k], b = c.nodes[k + 1];
      const ya = a.y + c.ys[k], yb = b.y + c.ys[k + 1];
      if (ya !== yb) {                                                 // a sub-pixel jog still keeps the line orthogonal
        const tx = trackX(a.layer, `${b.id}:${c.ys[k + 1]}`);
        pts.push([tx, ya], [tx, yb]);
      }
    }
    pts.push([boxX(lo) + lo.w, lo.y + c.ys[c.ys.length - 1]]);
    const simple = simplify(pts);
    routes.set(id, c.rev ? simple.reverse() : simple);
  }
  const w = x, h = Math.max(...[...nodes.values()].map((n) => n.y + n.h));
  return { key: ids[0], w, h, pos, routes };
}

function compact(ids: string[], layer: Map<string, number>): string[][] {
  const used = [...new Set(ids.map((i) => layer.get(i)!))].sort((a, b) => a - b);
  const remap = new Map(used.map((l, k) => [l, k]));
  const out: string[][] = used.map(() => []);
  for (const i of ids) {
    const k = remap.get(layer.get(i)!)!;
    layer.set(i, k);
    out[k].push(i);
  }
  return out;
}

const gapBetween = (a: N, b: N) => (a.dummy && b.dummy ? GAP_DUMMY : a.dummy || b.dummy ? GAP_DUMMY_BOX : GAP_BOX);

function restack(rows: N[][]) {
  for (const r of rows) {
    let y = 0;
    r.forEach((n, i) => {
      if (i) y += gapBetween(r[i - 1], n);
      n.y = y;
      y += n.h;
    });
  }
}

function initialOrder(rows: N[][], segs: Seg[][]) {
  // layer 0: the most linked first; then each layer by the mean index of what it links to on its left
  const deg = new Map<string, number>();
  segs.flat().forEach((s) => {
    deg.set(s.hi.id, (deg.get(s.hi.id) ?? 0) + 1);
    deg.set(s.lo.id, (deg.get(s.lo.id) ?? 0) + 1);
  });
  rows[0].sort((a, b) => (deg.get(b.id) ?? 0) - (deg.get(a.id) ?? 0) || (a.id < b.id ? -1 : 1));
  for (let l = 1; l < rows.length; l++) {
    const idx = new Map(rows[l - 1].map((n, i) => [n.id, i]));
    const key = new Map<string, number>();
    for (const n of rows[l]) {
      const ls = segs[l].filter((s) => s.hi === n).map((s) => idx.get(s.lo.id) ?? 0);
      key.set(n.id, ls.length ? ls.reduce((a, b) => a + b, 0) / ls.length : 1e9);
    }
    rows[l].sort((a, b) => key.get(a.id)! - key.get(b.id)! || (a.id < b.id ? -1 : 1));
  }
}

function sweep(rows: N[][], segs: Seg[][], l: number, side: "left" | "right") {
  const r = rows[l];
  const key = new Map<string, number>();
  for (const n of r) {
    const ys: number[] = [];
    if (side === "left") for (const s of segs[l]) { if (s.hi === n) ys.push(s.lo.y + s.ly - s.hy); }
    else if (l + 1 < rows.length) for (const s of segs[l + 1]) { if (s.lo === n) ys.push(s.hi.y + s.hy - s.ly); }
    key.set(n.id, ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length + n.h / 2 : n.y + n.h / 2);
  }
  r.sort((a, b) => key.get(a.id)! - key.get(b.id)!);
  restack([r]);
}

function crossings(rows: N[][], segs: Seg[][]): number {
  let total = 0;
  for (let l = 1; l < rows.length; l++) {
    const s = segs[l].map((g) => [g.hi.y + g.hy, g.lo.y + g.ly]);
    for (let i = 0; i < s.length; i++) {
      for (let j = i + 1; j < s.length; j++) {
        if ((s[i][0] - s[j][0]) * (s[i][1] - s[j][1]) < 0) total++;
      }
    }
  }
  return total;
}

/** Pool adjacent violators: tops as close as possible to the wished ones, in order, with the gaps kept. */
function placeLayer(r: N[], segs: Seg[][], l: number, side: "left" | "right" | "both") {
  if (!r.length) return;
  const wish: number[] = [], weight: number[] = [];
  for (const n of r) {
    const ys: number[] = [];
    if (side !== "right") for (const s of segs[l] ?? []) { if (s.hi === n) ys.push(s.lo.y + s.ly - s.hy); }
    if (side !== "left") for (const s of segs[l + 1] ?? []) { if (s.lo === n) ys.push(s.hi.y + s.hy - s.ly); }
    if (ys.length) {
      ys.sort((a, b) => a - b);
      const mid = ys.length % 2 ? ys[(ys.length - 1) / 2] : (ys[ys.length / 2 - 1] + ys[ys.length / 2]) / 2;
      wish.push(mid);
      weight.push(n.dummy ? ys.length * 2 : ys.length);
    } else {
      wish.push(n.y);
      weight.push(0.05);
    }
  }
  const off: number[] = [];
  let acc = 0;
  r.forEach((n, i) => {
    if (i) acc += r[i - 1].h + gapBetween(r[i - 1], n);
    off.push(acc);
  });
  type Pool = { from: number; to: number; w: number; sum: number };
  const pools: Pool[] = [];
  r.forEach((_, i) => {
    pools.push({ from: i, to: i, w: weight[i], sum: weight[i] * (wish[i] - off[i]) });
    while (pools.length > 1) {
      const b = pools[pools.length - 1], a = pools[pools.length - 2];
      if (a.sum / a.w <= b.sum / b.w) break;
      pools.splice(pools.length - 2, 2, { from: a.from, to: b.to, w: a.w + b.w, sum: a.sum + b.sum });
    }
  });
  for (const p of pools) {
    const z = p.sum / p.w;
    for (let i = p.from; i <= p.to; i++) r[i].y = z + off[i];
  }
}

/** One track per vertical (lines into the same port share theirs), ordered so fewer of them cross. */
function assignTracks(segs: Seg[]): { track: Map<string, number>; count: number } {
  type V = { key: string; lo: number; top: number; bot: number; up: boolean };
  const byKey = new Map<string, { ly: number; hys: number[] }>();
  for (const s of segs) {
    const hy = s.hi.y + s.hy, ly = s.lo.y + s.ly;
    if (Math.abs(hy - ly) <= 0.5) continue;
    const v = byKey.get(s.bundle) ?? { ly, hys: [] };
    v.hys.push(hy);
    byKey.set(s.bundle, v);
  }
  const vs: V[] = [...byKey.entries()].map(([key, v]) => {
    const all = [v.ly, ...v.hys];
    const mean = v.hys.reduce((a, b) => a + b, 0) / v.hys.length;
    return { key, lo: v.ly, top: Math.min(...all), bot: Math.max(...all), up: v.ly > mean };
  });
  // a line coming down from the left wants to be right of one that comes down later; going up, the other way round
  vs.sort((a, b) => (a.up === b.up ? (a.up ? a.lo - b.lo : b.lo - a.lo) : a.up ? -1 : 1) || (a.key < b.key ? -1 : 1));
  const track = new Map<string, number>();
  const placed: { v: V; t: number }[] = [];
  let count = 0;
  for (const v of vs) {
    let t = 0;
    for (const p of placed) if (p.v.top < v.bot + 6 && v.top < p.v.bot + 6) t = Math.max(t, p.t + 1);
    track.set(v.key, t);
    placed.push({ v, t });
    count = Math.max(count, t + 1);
  }
  return { track, count: Math.max(1, count) };
}

export function simplify(pts: Pt[]): Pt[] {
  const out: Pt[] = [];
  for (const p of pts) {
    const last = out[out.length - 1];
    if (last && Math.abs(last[0] - p[0]) < 0.01 && Math.abs(last[1] - p[1]) < 0.01) continue;
    if (out.length >= 2) {
      const a = out[out.length - 2], b = out[out.length - 1];
      const colinear = (Math.abs(a[0] - b[0]) < 0.01 && Math.abs(b[0] - p[0]) < 0.01) || (Math.abs(a[1] - b[1]) < 0.01 && Math.abs(b[1] - p[1]) < 0.01);
      if (colinear) out[out.length - 1] = p;
      else out.push(p);
    } else out.push(p);
  }
  return out;
}

// ------------------------------------------------------------------ loose boxes and packing

function looseGrid(boxes: LNode[], aspect: number, others: Block[]): Block {
  const gapX = 36, gapY = 24;
  const area = boxes.reduce((s, b) => s + (b.w + gapX) * (b.h + gapY), 0);
  const tallest = Math.max(...boxes.map((b) => b.h));
  const otherH = Math.max(0, ...others.map((o) => o.h));
  const colH = Math.max(tallest, Math.min(Math.max(otherH, 400), Math.sqrt(area / aspect) * 1.2));
  const pos = new Map<string, XY>();
  let x = 0, y = 0, colW = 0, h = 0;
  for (const b of boxes) {
    if (y && y + b.h > colH) {
      x += colW + gapX;
      y = 0;
      colW = 0;
    }
    pos.set(b.id, { x, y });
    colW = Math.max(colW, b.w);
    y += b.h + gapY;
    h = Math.max(h, y - gapY);
  }
  return { key: "~loose", w: x + colW, h, pos, routes: new Map() };
}

function pack(blocks: Block[], aspect: number, gap: number): LayoutResult {
  const area = blocks.reduce((s, b) => s + (b.w + gap) * (b.h + gap), 0);
  const maxW = Math.max(Math.max(0, ...blocks.map((b) => b.w)), Math.sqrt(area * aspect));
  const pos = new Map<string, XY>();
  const routes = new Map<string, Pt[]>();
  let x = 0, y = 0, rowH = 0, width = 0;
  for (const b of blocks) {
    if (x && x + b.w > maxW) {
      x = 0;
      y += rowH + gap;
      rowH = 0;
    }
    for (const [id, p] of b.pos) pos.set(id, { x: p.x + x, y: p.y + y });
    for (const [id, r] of b.routes) routes.set(id, r.map(([px, py]) => [px + x, py + y] as Pt));
    x += b.w + gap;
    rowH = Math.max(rowH, b.h);
    width = Math.max(width, x - gap);
  }
  return { pos, routes, width, height: y + rowH };
}
