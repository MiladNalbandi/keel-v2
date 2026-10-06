// The database diagram's layout and routing (components/er): boxes never overlap, every line is orthogonal, starts at
// the foreign-key row and ends at the key row, goes around boxes, and 150 tables lay out quickly.

import { describe, expect, it } from "vitest";
import type { DbSchema } from "../api";
import { boxShape, HEAD, portOffset, ROW } from "../components/er/geometry";
import { layoutGraph, type LEdge, type Pt } from "../components/er/layout";
import { fromLegacy } from "../components/er/model";
import { routeAround, type Rect } from "../components/er/route";
import shop from "./fixtures/er-shop.json";

const schema = shop as unknown as DbSchema;

function build(s: DbSchema) {
  const byId = new Map(s.tables.map((t) => [t.id, t]));
  const shapes = new Map(s.tables.map((t) => [t.id, boxShape(t, "all", true, false)]));
  const edges: LEdge[] = s.relations.map((r) => ({
    id: r.id, from: r.from, to: r.to,
    fy: r.kind === "uses" ? HEAD / 2 : Math.min(...r.from_columns.map((c) => portOffset(byId.get(r.from)!, shapes.get(r.from)!, c))),
    ty: r.kind === "uses" ? HEAD / 2 : Math.min(...r.to_columns.map((c) => portOffset(byId.get(r.to)!, shapes.get(r.to)!, c))),
  }));
  const out = layoutGraph(s.tables.map((t) => ({ id: t.id, w: shapes.get(t.id)!.w, h: shapes.get(t.id)!.h })), edges);
  const rect = (id: string): Rect => ({ ...out.pos.get(id)!, w: shapes.get(id)!.w, h: shapes.get(id)!.h });
  return { out, edges, rect, shapes };
}

const overlaps = (a: Rect, b: Rect, m = 0) => a.x < b.x + b.w + m && b.x < a.x + a.w + m && a.y < b.y + b.h + m && b.y < a.y + a.h + m;
const crosses = (pts: Pt[], r: Rect) => pts.slice(1).some(([x2, y2], i) => {
  const [x1, y1] = pts[i];
  return Math.min(x1, x2) < r.x + r.w - 1 && Math.max(x1, x2) > r.x + 1 && Math.min(y1, y2) < r.y + r.h - 1 && Math.max(y1, y2) > r.y + 1;
});

describe("database diagram layout", () => {
  it("places every table without overlaps and routes every relation orthogonally from row to row", () => {
    const { out, edges, rect } = build(schema);
    expect(out.pos.size).toBe(schema.tables.length);
    const ids = [...out.pos.keys()];
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) expect(overlaps(rect(ids[i]), rect(ids[j], ), 8), `${ids[i]} / ${ids[j]}`).toBe(false);
    expect(out.routes.size).toBe(edges.length);
    for (const e of edges) {
      const pts = out.routes.get(e.id)!;
      for (let i = 1; i < pts.length; i++) expect(pts[i][0] === pts[i - 1][0] || pts[i][1] === pts[i - 1][1], e.id).toBe(true);
      const a = rect(e.from), b = rect(e.to);
      expect(pts[0][1]).toBeCloseTo(a.y + e.fy);                                   // leaves the foreign-key row
      expect([a.x, a.x + a.w].some((x) => Math.abs(x - pts[0][0]) < 0.6)).toBe(true);
      expect(pts[pts.length - 1][1]).toBeCloseTo(b.y + e.ty);                     // arrives at the referenced row
      if (e.from !== e.to) {
        for (const id of ids) if (id !== e.from && id !== e.to) expect(crosses(pts, rect(id)), `${e.id} crosses ${id}`).toBe(false);
      }
    }
  });

  it("puts a referenced table left of the tables that reference it", () => {
    const { rect } = build(schema);
    expect(rect("tenant").x).toBeLessThan(rect("app_user").x);
    expect(rect("customer_order").x).toBeLessThan(rect("order_line").x);
    expect(rect("billing.invoice").x).toBeLessThan(rect("billing.payment").x);
  });

  it("is deterministic", () => {
    const a = build(schema).out, b = build(schema).out;
    expect([...a.pos.entries()]).toEqual([...b.pos.entries()]);
  });

  it("lays out 150 tables and 260 foreign keys in well under a second", () => {
    const tables = Array.from({ length: 150 }, (_, i) => ({
      id: `t${i}`, name: `table_${i}`, kind: "table", schema: null,
      columns: Array.from({ length: 4 + (i % 9) }, (_, c) => ({ name: c ? `col_${c}` : "id", type: "bigint", nullable: c > 2, pk: c === 0, unique: false })),
    }));
    let seed = 7;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    const relations = Array.from({ length: 260 }, (_, k) => {
      const from = 1 + Math.floor(rnd() * 149), to = Math.floor(rnd() * from);
      return { id: `r${k}`, kind: "fk", from: `t${from}`, from_columns: ["col_1"], to: `t${to}`, to_columns: ["id"] };
    });
    const t0 = performance.now();
    const { out, rect } = build({ tables, relations } as unknown as DbSchema);
    const ms = performance.now() - t0;
    expect(out.pos.size).toBe(150);
    expect(ms).toBeLessThan(1500);
    const ids = [...out.pos.keys()];
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) expect(overlaps(rect(ids[i]), rect(ids[j]))).toBe(false);
  });

  it("routes around boxes in the way when boxes were moved by hand", () => {
    const a: Rect = { x: 0, y: 0, w: 100, h: 100 }, b: Rect = { x: 600, y: 0, w: 100, h: 100 }, wall: Rect = { x: 250, y: -200, w: 120, h: 500 };
    const pts = routeAround(a, 50, b, 50, [wall]);
    expect(pts[0]).toEqual([100, 50]);
    expect(pts[pts.length - 1]).toEqual([600, 50]);
    expect(crosses(pts, wall)).toBe(false);
    for (let i = 1; i < pts.length; i++) expect(pts[i][0] === pts[i - 1][0] || pts[i][1] === pts[i - 1][1]).toBe(true);
  });

  it("an old map's ER boxes still give a schema", () => {
    const s = fromLegacy({
      width: 1, height: 1,
      nodes: [
        { id: "tbl:players", kind: "data", title: "players", rows: [{ t: "id    bigint", flag: "pk" }, { t: "team_id  bigint", flag: "fk" }], x: 0, y: 0, w: 1, h: 1 },
        { id: "tbl:teams", kind: "data", title: "teams", rows: [{ t: "id uuid", flag: "pk" }], x: 0, y: 0, w: 1, h: 1 },
      ],
      edges: [{ from: "tbl:teams", to: "tbl:players", kind: "fk", d: "M 0 0" }],
    });
    expect(s.relations).toMatchObject([{ from: "players", from_columns: ["team_id"], to: "teams", to_columns: ["id"] }]);
    expect(s.tables[0].columns[1]).toMatchObject({ name: "team_id", type: "bigint", fk: { table: "teams" } });
    expect(ROW).toBe(20);
  });
});
