// Orthogonal routes around boxes, for lines whose boxes were moved by hand (the layered layout routes the rest),
// and the line ends: crow's foot / bars / circle, or a plain arrow.
//
// routeAround: an A* search on the sparse grid of the boxes' (padded) edges and the two ports, with a cost per bend,
// so a line takes few turns and never crosses a box. Lines leave and enter a box horizontally, at the row's y.

import { simplify, type Pt } from "./layout";

export type Rect = { x: number; y: number; w: number; h: number };
export type Side = "l" | "r";

const M = 14;          // clearance around a box
const STUB = 16;       // a line leaves a box straight for at least this long
const BEND = 36;       // what a turn costs, in pixels of length

/** Which sides a line between two boxes uses: facing sides when they are apart, else both on the shorter outside. */
export function sides(a: Rect, b: Rect): [Side, Side] {
  if (b.x >= a.x + a.w + 2 * STUB) return ["r", "l"];
  if (b.x + b.w <= a.x - 2 * STUB) return ["l", "r"];
  const right = Math.max(a.x + a.w, b.x + b.w) - Math.min(a.x + a.w, b.x + b.w);
  const left = Math.max(a.x, b.x) - Math.min(a.x, b.x);
  return right <= left ? ["r", "r"] : ["l", "l"];
}

/** A quick three-part line (while dragging): out, across, in. */
export function elbow(a: Rect, ay: number, b: Rect, by: number): Pt[] {
  const [sa, sb] = sides(a, b);
  const x1 = sa === "r" ? a.x + a.w : a.x, x2 = sb === "r" ? b.x + b.w : b.x;
  if (sa !== sb) {
    const mx = (x1 + x2) / 2;
    return simplify([[x1, ay], [mx, ay], [mx, by], [x2, by]]);
  }
  const ox = sa === "r" ? Math.max(x1, x2) + 24 : Math.min(x1, x2) - 24;
  return simplify([[x1, ay], [ox, ay], [ox, by], [x2, by]]);
}

type Grid = { xs: number[]; ys: number[]; hBlock: Uint8Array; vBlock: Uint8Array };

function uniqSorted(v: number[]): number[] {
  const s = [...new Set(v.map((x) => Math.round(x * 2) / 2))].sort((a, b) => a - b);
  return s;
}

function grid(obstacles: Rect[], extraX: number[], extraY: number[], bounds: Rect): Grid {
  const inside = (v: number, lo: number, hi: number) => v >= lo && v <= hi;
  let xs: number[] = [...extraX], ys: number[] = [...extraY];
  for (const o of obstacles) {
    xs.push(o.x - M, o.x + o.w + M);
    ys.push(o.y - M, o.y + o.h + M);
  }
  xs = uniqSorted(xs.filter((x) => inside(x, bounds.x, bounds.x + bounds.w)));
  ys = uniqSorted(ys.filter((y) => inside(y, bounds.y, bounds.y + bounds.h)));
  // the middle of every corridor, so lines run centred between boxes rather than hugging one
  const mids = (v: number[]) => v.flatMap((x, i) => (i && x - v[i - 1] > 3 * M ? [(x + v[i - 1]) / 2] : []));
  xs = uniqSorted([...xs, ...mids(xs)]);
  ys = uniqSorted([...ys, ...mids(ys)]);
  const nx = xs.length, ny = ys.length;
  const hBlock = new Uint8Array(nx * ny), vBlock = new Uint8Array(nx * ny);
  const pad = obstacles.map((o) => ({ l: o.x - M, r: o.x + o.w + M, t: o.y - M, b: o.y + o.h + M }));
  for (let yi = 0; yi < ny; yi++) {
    const y = ys[yi];
    const hit = pad.filter((p) => y > p.t + 0.01 && y < p.b - 0.01);
    if (!hit.length) continue;
    for (let xi = 0; xi + 1 < nx; xi++) {
      const x1 = xs[xi], x2 = xs[xi + 1];
      if (hit.some((p) => x1 < p.r - 0.01 && x2 > p.l + 0.01)) hBlock[yi * nx + xi] = 1;
    }
  }
  for (let xi = 0; xi < nx; xi++) {
    const x = xs[xi];
    const hit = pad.filter((p) => x > p.l + 0.01 && x < p.r - 0.01);
    if (!hit.length) continue;
    for (let yi = 0; yi + 1 < ny; yi++) {
      const y1 = ys[yi], y2 = ys[yi + 1];
      if (hit.some((p) => y1 < p.b - 0.01 && y2 > p.t + 0.01)) vBlock[yi * nx + xi] = 1;
    }
  }
  return { xs, ys, hBlock, vBlock };
}

class Heap {
  private a: number[] = [];
  private k: number[] = [];
  get size() { return this.a.length; }
  push(v: number, key: number) {
    const a = this.a, k = this.k;
    a.push(v); k.push(key);
    let i = a.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (k[p] <= k[i]) break;
      [a[p], a[i]] = [a[i], a[p]]; [k[p], k[i]] = [k[i], k[p]];
      i = p;
    }
  }
  pop(): number {
    const a = this.a, k = this.k;
    const top = a[0];
    const lv = a.pop()!, lk = k.pop()!;
    if (a.length) {
      a[0] = lv; k[0] = lk;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < a.length && k[l] < k[m]) m = l;
        if (r < a.length && k[r] < k[m]) m = r;
        if (m === i) break;
        [a[m], a[i]] = [a[i], a[m]]; [k[m], k[i]] = [k[i], k[m]];
        i = m;
      }
    }
    return top;
  }
}

const DX = [1, -1, 0, 0], DY = [0, 0, 1, -1];

function search(g: Grid, s: [number, number], sDir: number, t: [number, number], tDir: number): Pt[] | null {
  const { xs, ys } = g;
  const nx = xs.length, ny = ys.length;
  const si = xs.indexOf(s[0]), sj = ys.indexOf(s[1]), ti = xs.indexOf(t[0]), tj = ys.indexOf(t[1]);
  if (si < 0 || sj < 0 || ti < 0 || tj < 0) return null;
  const n = nx * ny * 4;
  const cost = new Float64Array(n).fill(Infinity);
  const prev = new Int32Array(n).fill(-1);
  const heap = new Heap();
  const st = (sj * nx + si) * 4 + sDir;
  cost[st] = 0;
  const hx = (i: number, j: number) => Math.abs(xs[i] - xs[ti]) + Math.abs(ys[j] - ys[tj]);
  heap.push(st, hx(si, sj));
  let budget = 400000;
  while (heap.size && budget-- > 0) {
    const cur = heap.pop();
    const d = cur & 3, cell = cur >> 2, i = cell % nx, j = (cell - i) / nx;
    if (i === ti && j === tj && d !== (tDir ^ 1)) {                     // any way in but backwards
      const pts: Pt[] = [];
      for (let c = cur; c >= 0; c = prev[c]) {
        const cc = c >> 2, ci = cc % nx, cj = (cc - ci) / nx;
        pts.push([xs[ci], ys[cj]]);
      }
      return simplify(pts.reverse());
    }
    const base = cost[cur];
    for (let nd = 0; nd < 4; nd++) {
      if (nd === (d ^ 1)) continue;                                      // no U-turns
      const ni = i + DX[nd], nj = j + DY[nd];
      if (ni < 0 || nj < 0 || ni >= nx || nj >= ny) continue;
      if (DY[nd] === 0 && g.hBlock[j * nx + Math.min(i, ni)]) continue;
      if (DX[nd] === 0 && g.vBlock[Math.min(j, nj) * nx + i]) continue;
      const len = Math.abs(xs[ni] - xs[i]) + Math.abs(ys[nj] - ys[j]);
      const c = base + len + (nd === d ? 0 : BEND);
      const nxt = (nj * nx + ni) * 4 + nd;
      if (c < cost[nxt]) {
        cost[nxt] = c;
        prev[nxt] = cur;
        heap.push(nxt, c + hx(ni, nj));
      }
    }
  }
  return null;
}

/** A route from box a's row ay to box b's row by that goes around every other box in `obstacles`. */
export function routeAround(a: Rect, ay: number, b: Rect, by: number, obstacles: Rect[]): Pt[] {
  const [sa, sb] = sides(a, b);
  const p1: Pt = [sa === "r" ? a.x + a.w : a.x, ay];
  const p2: Pt = [sb === "r" ? b.x + b.w : b.x, by];
  const q1: Pt = [p1[0] + (sa === "r" ? STUB + M : -STUB - M), ay];
  const q2: Pt = [p2[0] + (sb === "r" ? STUB + M : -STUB - M), by];
  // the stubs leave the box outwards: right = direction 0, left = 1; arriving at q2 we move towards the box
  const sDir = sa === "r" ? 0 : 1, tDir = sb === "r" ? 1 : 0;
  const all = [...obstacles, a, b];
  const span = (pad: number): Rect => {
    const x0 = Math.min(a.x, b.x) - pad, y0 = Math.min(a.y, b.y) - pad;
    return { x: x0, y: y0, w: Math.max(a.x + a.w, b.x + b.w) + pad - x0, h: Math.max(a.y + a.h, b.y + b.h) + pad - y0 };
  };
  for (const pad of [260, 900, 1e6]) {
    const box = span(pad);
    const near = all.filter((o) => o.x < box.x + box.w && o.x + o.w > box.x && o.y < box.y + box.h && o.y + o.h > box.y);
    const g = grid(near, [q1[0], q2[0]], [ay, by], { x: box.x - M * 2, y: box.y - M * 2, w: box.w + M * 4, h: box.h + M * 4 });
    const mid = search(g, [round(q1[0]), round(ay)], sDir, [round(q2[0]), round(by)], tDir);
    if (mid) return simplify([p1, ...mid, p2]);
  }
  return elbow(a, ay, b, by);
}

const round = (v: number) => Math.round(v * 2) / 2;

// ------------------------------------------------------------------ drawing

export function pathD(pts: Pt[], r = 5): string {
  if (pts.length < 2) return "";
  let d = `M${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [px, py] = pts[i - 1], [x, y] = pts[i], [nx, ny] = pts[i + 1];
    const l1 = Math.hypot(x - px, y - py), l2 = Math.hypot(nx - x, ny - y);
    const k = Math.min(r, l1 / 2, l2 / 2);
    if (k < 0.5) {
      d += ` L${x} ${y}`;
      continue;
    }
    const ax = x - ((x - px) / l1) * k, ay = y - ((y - py) / l1) * k;
    const bx = x + ((nx - x) / l2) * k, by = y + ((ny - y) / l2) * k;
    d += ` L${r1(ax)} ${r1(ay)} Q${x} ${y} ${r1(bx)} ${r1(by)}`;
  }
  const [lx, ly] = pts[pts.length - 1];
  return d + ` L${lx} ${ly}`;
}

const r1 = (v: number) => Math.round(v * 10) / 10;

export type EndKind = "many" | "one" | "only-one" | "zero-one" | "arrow" | "none";

/** The mark at one end of a line; `at` is the end on the box, `from` the next point of the line. */
export function endMark(at: Pt, from: Pt, kind: EndKind): { d: string; circle?: [number, number, number] } {
  const ux = Math.sign(from[0] - at[0]) || 0, uy = Math.sign(from[1] - at[1]) || 0;   // pointing away from the box
  const px = -uy, py = ux;                                                            // across the line
  const P = (along: number, across: number) => `${r1(at[0] + ux * along + px * across)} ${r1(at[1] + uy * along + py * across)}`;
  switch (kind) {
    case "many":
      return { d: `M${P(0, -6)} L${P(12, 0)} L${P(0, 6)} M${P(0, 0)} L${P(12, 0)}` };
    case "one":
      return { d: `M${P(9, -5)} L${P(9, 5)}` };
    case "only-one":
      return { d: `M${P(7, -5)} L${P(7, 5)} M${P(12, -5)} L${P(12, 5)}` };
    case "zero-one":
      return { d: `M${P(7, -5)} L${P(7, 5)}`, circle: [r1(at[0] + ux * 15), r1(at[1] + uy * 15), 3.5] };
    case "arrow":
      return { d: `M${P(10, -4.5)} L${P(0.5, 0)} L${P(10, 4.5)} Z` };
    default:
      return { d: "" };
  }
}
