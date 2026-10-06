// The Graph page's data as boxes and lines: packages (folded to a depth) and the uses between them, one package's
// units with the packages they touch, and the words for kinds. Pure functions, so tests can check them directly.

import type { GraphGroup, GraphLink, GraphOverview, GraphUnit } from "../../api";
import type { GBox, GEdge } from "../er/BoxDiagram";

export type Depth = number | "all";
type Overview = Extract<GraphOverview, { available: true }>;

const TEST_PATH = /(^|\/)(test|tests|__tests__|e2e|spec|specs|testdata|fixtures)\//i;
const TEST_FILE = /(\.(test|spec)\.[cm]?[jt]sx?$)|((Tests?|IT|Spec)\.(java|kt|scala|groovy)$)|((^|\/)test_[^/]+\.py$)|(_test\.(go|py)$)/;
/** Test code uses everything; it is hidden unless asked for. */
export const isTest = (file: string) => TEST_PATH.test(file) || TEST_FILE.test(file);

/** The folded group a group belongs to at a depth: a package keeps its first `depth` segments, a folder too. */
export function fold(g: GraphGroup, depth: Depth): { key: string; label: string; kind: GraphGroup["kind"] } {
  const path = depth === "all" ? g.path : g.path.slice(0, Math.max(1, depth));
  const label = g.kind === "package" ? path.join(".") : path.join("/");
  return { key: `${g.kind === "package" ? "p" : "f"}:${label}`, label, kind: g.kind };
}

/** The deepest fold that still fits on a screen (at most `max` boxes), and the deepest that changes anything. */
export function autoDepth(groups: GraphGroup[], max = 24): number {
  const deepest = Math.max(1, ...groups.map((g) => g.path.length));
  let best = 1;
  for (let d = 1; d <= deepest; d++) {
    if (new Set(groups.map((g) => fold(g, d).key)).size <= max) best = d;
  }
  return best;
}

export const maxDepth = (groups: GraphGroup[]) => Math.max(1, ...groups.map((g) => g.path.length));

const KIND_WORD: Record<string, [string, string]> = {
  class: ["class", "classes"], interface: ["interface", "interfaces"], enum: ["enum", "enums"], function: ["function", "functions"],
  type_alias: ["type", "types"], constant: ["constant", "constants"], variable: ["variable", "variables"], route: ["route", "routes"],
  file: ["file", "files"], method: ["method", "methods"], field: ["field", "fields"], property: ["property", "properties"],
  struct: ["struct", "structs"], trait: ["trait", "traits"], component: ["component", "components"], module: ["module", "modules"],
};
export const kindWord = (kind: string, n = 1) => (KIND_WORD[kind]?.[n === 1 ? 0 : 1] ?? kind.replace(/_/g, " "));
const USE_WORD: Record<string, string> = { calls: "calls", instantiates: "creates", implements: "implements", extends: "extends", references: "refers to" };
export const useWord = (k: string) => USE_WORD[k] ?? k;
/** "12 calls, 2 creates" */
export const usesText = (k: Record<string, number>) =>
  Object.entries(k).sort((a, b) => b[1] - a[1]).map(([kind, n]) => `${n} ${useWord(kind)}`).join(", ");
/** The strongest kind of use, for the line's style. */
export const mainUse = (k: Record<string, number>) =>
  ["calls", "instantiates", "implements", "extends", "references"].find((x) => k[x]) ?? "calls";

export function iconOf(kind: string): string {
  return ({ class: "class", interface: "iface", trait: "iface", enum: "enum", function: "fn", component: "fn", type_alias: "type",
    constant: "const", variable: "const", route: "route", method: "method", field: "field", property: "field", file: "file" } as Record<string, string>)[kind] ?? "code";
}
const boxKind = (kind: string): GBox["kind"] => (kind === "interface" || kind === "trait" ? "data" : kind === "function" || kind === "route" ? "api" : "app");

/** Units shown at all: tests only when asked for. */
export function visibleUnits(o: Overview, tests: boolean): GraphUnit[] {
  return tests ? o.units : o.units.filter((u) => !isTest(u.file));
}

function countText(units: GraphUnit[]): string {
  const by = new Map<string, number>();
  for (const u of units) by.set(u.kind, (by.get(u.kind) ?? 0) + 1);
  const order = ["class", "interface", "enum", "function", "type_alias", "route", "constant", "file"];
  const rank = (k: string) => (order.includes(k) ? order.indexOf(k) : order.length);
  return [...by.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || b[1] - a[1])
    .slice(0, 3).map(([k, n]) => `${n} ${kindWord(k, n)}`).join(" · ");
}

function linkTip(from: string, to: string, n: number, k: Record<string, number>) {
  return `${from} uses ${to}: ${n} use${n === 1 ? "" : "s"} (${usesText(k)})`;
}

function addK(into: Record<string, number>, k: Record<string, number>) {
  for (const [kind, n] of Object.entries(k)) into[kind] = (into[kind] ?? 0) + n;
}

/**
 * Lines run from the used box to the user (the layout puts a user left of what it uses, and the arrow sits at the
 * line's start): entry points end up on the left, the core on the right, and every arrow points at what is used.
 */
function drawn(id: string, user: string, used: string, n: number, k: Record<string, number>, userName: string, usedName: string): GEdge {
  return { id, from: used, to: user, label: n > 1 ? String(n) : undefined, weight: n, tip: linkTip(userName, usedName, n, k) };
}

/** The overview: one box per folded group, a line per pair of groups that use each other. */
export function overviewBoxes(o: Overview, depth: Depth, tests: boolean): { boxes: GBox[]; edges: GEdge[]; keyOf: Map<string, string> } {
  const groups = new Map(o.groups.map((g) => [g.id, g]));
  const units = visibleUnits(o, tests);
  const unitById = new Map(units.map((u) => [u.id, u]));
  const keyOf = new Map<string, string>();            // unit -> folded group key
  const info = new Map<string, { label: string; kind: GraphGroup["kind"]; units: GraphUnit[]; files: Set<string> }>();
  for (const u of units) {
    const g = groups.get(u.group);
    if (!g) continue;
    const f = fold(g, depth);
    keyOf.set(u.id, f.key);
    const i = info.get(f.key) ?? { label: f.label, kind: f.kind, units: [], files: new Set<string>() };
    i.units.push(u);
    i.files.add(u.file);
    info.set(f.key, i);
  }
  const inbound = new Map<string, number>();          // uses of a unit from outside its folded group
  const pairs = new Map<string, { a: string; b: string; n: number; k: Record<string, number> }>();
  for (const l of o.links) {
    const a = keyOf.get(l.from), b = keyOf.get(l.to);
    if (!a || !b || !unitById.has(l.from) || !unitById.has(l.to) || a === b) continue;
    inbound.set(l.to, (inbound.get(l.to) ?? 0) + l.n);
    const p = pairs.get(`${a}>${b}`) ?? { a, b, n: 0, k: {} };
    p.n += l.n;
    addK(p.k, l.k);
    pairs.set(`${a}>${b}`, p);
  }
  const boxes: GBox[] = [...info.entries()].sort((x, y) => x[1].label.localeCompare(y[1].label)).map(([key, i]) => {
    const top = [...i.units].sort((x, y) => (inbound.get(y.id) ?? 0) - (inbound.get(x.id) ?? 0) || x.name.localeCompare(y.name)).slice(0, 5);
    return {
      id: key, kind: "app", icon: "folder", title: i.label,
      sub: `${countText(i.units)}${i.files.size > 1 ? ` · ${i.files.size} files` : ""}`,
      rows: [
        ...top.map((u) => ({ t: `${u.name}${inbound.get(u.id) ? `  ← ${inbound.get(u.id)}` : ""}`, tip: `${kindWord(u.kind)} ${u.name}, ${u.file}:${u.line}` })),
        ...(i.units.length > top.length ? [{ t: `+${i.units.length - top.length} more` }] : []),
      ],
      open: "open ›",
    };
  });
  const label = (key: string) => info.get(key)?.label ?? key;
  const edges = [...pairs.values()].map((p, idx) => drawn(`g${idx}`, p.a, p.b, p.n, p.k, label(p.a), label(p.b)));
  return { boxes, edges, keyOf };
}

/** One folded group: its units, the lines between them, and a box for each other group they use or that uses them. */
export function groupBoxes(o: Overview, key: string, depth: Depth, tests: boolean): { boxes: GBox[]; edges: GEdge[]; label: string; units: number } {
  const { keyOf } = overviewBoxes(o, depth, tests);
  const groups = new Map(o.groups.map((g) => [g.id, g]));
  const units = visibleUnits(o, tests);
  const mine = units.filter((u) => keyOf.get(u.id) === key);
  const mineIds = new Set(mine.map((u) => u.id));
  const unitById = new Map(units.map((u) => [u.id, u]));
  const labelOf = (k: string) => {
    const any = units.find((u) => keyOf.get(u.id) === k);
    const g = any && groups.get(any.group);
    return g ? fold(g, depth).label : k.slice(2);
  };
  const usesIn = new Map<string, number>(), usesOut = new Map<string, number>();
  const inner: GraphLink[] = [];
  const outer = new Map<string, { user: string; used: string; n: number; k: Record<string, number> }>();
  for (const l of o.links) {
    if (!unitById.has(l.from) || !unitById.has(l.to)) continue;
    const fromMine = mineIds.has(l.from), toMine = mineIds.has(l.to);
    if (fromMine && toMine) {
      inner.push(l);
    } else if (fromMine || toMine) {
      const other = keyOf.get(fromMine ? l.to : l.from);
      if (!other) continue;
      const user = fromMine ? l.from : `x:${other}`, used = fromMine ? `x:${other}` : l.to;
      const p = outer.get(`${user}>${used}`) ?? { user, used, n: 0, k: {} };
      p.n += l.n;
      addK(p.k, l.k);
      outer.set(`${user}>${used}`, p);
    } else continue;
    if (toMine) usesIn.set(l.to, (usesIn.get(l.to) ?? 0) + l.n);
    if (fromMine) usesOut.set(l.from, (usesOut.get(l.from) ?? 0) + l.n);
  }
  const ext = new Map<string, { n: number; uses: number; usedBy: number }>();
  for (const p of outer.values()) {
    const x = p.user.startsWith("x:") ? p.user : p.used;
    const e = ext.get(x) ?? { n: 0, uses: 0, usedBy: 0 };
    e.n += p.n;
    if (x === p.user) e.uses += p.n; else e.usedBy += p.n;
    ext.set(x, e);
  }
  const base = (f: string) => f.split("/").pop() ?? f;
  const boxes: GBox[] = [
    ...mine.sort((a, b) => a.name.localeCompare(b.name)).map<GBox>((u) => ({
      id: u.id, kind: boxKind(u.kind), icon: iconOf(u.kind), title: u.name, sub: `${kindWord(u.kind)} · ${base(u.file)}`,
      rows: [{ t: [u.members ? `${u.members} member${u.members === 1 ? "" : "s"}` : "", usesIn.get(u.id) ? `used ${usesIn.get(u.id)}×` : "",
        usesOut.get(u.id) ? `uses ${usesOut.get(u.id)}×` : ""].filter(Boolean).join(" · ") || "no uses" }],
      cite: { rel: u.file, line: u.line }, open: "open ›",
    })),
    ...[...ext.entries()].sort((a, b) => b[1].n - a[1].n).map<GBox>(([id, e]) => ({
      id, kind: "ext", icon: "folder", title: labelOf(id.slice(2)),
      sub: [e.usedBy ? `used here ${e.usedBy}×` : "", e.uses ? `uses this ${e.uses}×` : ""].filter(Boolean).join(" · "), rows: [], open: "open ›",
    })),
  ];
  const nameOf = (id: string) => (id.startsWith("x:") ? labelOf(id.slice(2)) : unitById.get(id)?.name ?? id);
  const edges = [
    ...inner.map((l, i) => drawn(`i${i}`, l.from, l.to, l.n, l.k, nameOf(l.from), nameOf(l.to))),
    ...[...outer.values()].map((p, i) => drawn(`o${i}`, p.user, p.used, p.n, p.k, nameOf(p.user), nameOf(p.used))),
  ];
  return { boxes, edges, label: labelOf(key), units: mine.length };
}

/** The label of a group id as the page shows it now (folded to the depth). */
export function groupLabel(o: Overview, groupId: string, depth: Depth): string {
  const g = o.groups.find((x) => x.id === groupId);
  return g ? fold(g, depth).label : groupId.replace(/^\w+:/, "");
}
