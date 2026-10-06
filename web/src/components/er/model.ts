// The database diagram's data: the engine's `schema` (v0.5.1), or one rebuilt from an older map's `levels.er` boxes,
// so a map built before the upgrade still draws (with fewer details) until it is rebuilt.

import type { DbColumn, DbRelation, DbSchema, DbTable, KeelMap, MapLevel } from "../../api";

export function schemaOf(m: KeelMap): DbSchema | null {
  if (m.schema && Array.isArray(m.schema.tables)) return m.schema.tables.length ? m.schema : null;
  return m.levels.er?.nodes?.length ? fromLegacy(m.levels.er) : null;
}

/** keel v1 / v0.4 rows read "name<spaces>type" with a pk/fk flag; edges run from the referenced table to the referencing one. */
export function fromLegacy(er: MapLevel): DbSchema {
  const tables: DbTable[] = er.nodes.map((n) => {
    const id = n.id.replace(/^tbl:/, "");
    const columns: DbColumn[] = (n.rows ?? []).map((r) => {
      const [name, ...type] = r.t.trim().split(/\s+/);
      return { name, type: type.join(" "), nullable: false, pk: r.flag === "pk", unique: false, fk: null, cite: null };
    });
    const pk = columns.filter((c) => c.pk).map((c) => c.name);
    return {
      id, name: n.title, schema: null, kind: "table", cite: n.cite ?? null, columns,
      primary_key: pk.length ? { columns: pk } : null, uniques: [], indexes: [], foreign_keys: [],
    };
  });
  const byId = new Map(tables.map((t) => [t.id, t]));
  const legacyFlags = new Map(er.nodes.map((n) => [n.id.replace(/^tbl:/, ""), (n.rows ?? []).map((r) => r.flag)]));
  const used = new Map<string, Set<string>>();
  const relations: DbRelation[] = [];
  for (const e of er.edges ?? []) {
    const parent = byId.get(e.from.replace(/^tbl:/, "")), child = byId.get(e.to.replace(/^tbl:/, ""));
    if (!parent || !child) continue;
    const flags = legacyFlags.get(child.id) ?? [];
    const taken = used.get(child.id) ?? new Set<string>();
    const fkCols = child.columns.filter((_, i) => flags[i] === "fk" || flags[i] === "pk");
    const guess = fkCols.find((c) => !taken.has(c.name) && c.name.toLowerCase().startsWith(parent.name.toLowerCase().replace(/s$/, "")))
      ?? child.columns.find((c, i) => flags[i] === "fk" && !taken.has(c.name));
    const pkCol = parent.columns.find((c) => c.pk);
    if (guess) {
      taken.add(guess.name);
      used.set(child.id, taken);
      guess.fk = { table: parent.id, column: pkCol?.name ?? null };
    }
    const from_columns = guess ? [guess.name] : [];
    const to_columns = pkCol ? [pkCol.name] : [];
    child.foreign_keys!.push({ name: null, columns: from_columns, ref_table: parent.id, ref_name: parent.name, ref_columns: to_columns });
    relations.push({ id: `fk:${child.id}:${relations.length}`, kind: "fk", from: child.id, from_columns, to: parent.id, to_columns,
      nullable: true, one_to_one: false, self: parent.id === child.id });
  }
  return { tables, relations };
}

export const tableKind = (t: DbTable) => (t.kind === "table" ? "table" : "view");

/** "public.users" -> the schema part only when it says something. */
export const schemaLabel = (t: DbTable) => (t.schema && !["public", "dbo", "main"].includes(t.schema.toLowerCase()) ? t.schema : "");

export type Neighbours = {
  out: DbRelation[];
  in: DbRelation[];
};

/** Relations by table id, both directions; a self reference shows in both lists. */
export function relationIndex(s: DbSchema): Map<string, Neighbours> {
  const idx = new Map<string, Neighbours>(s.tables.map((t) => [t.id, { out: [], in: [] }]));
  for (const r of s.relations) {
    idx.get(r.from)?.out.push(r);
    idx.get(r.to)?.in.push(r);
  }
  return idx;
}

export const isKeyColumn = (c: DbColumn) => c.pk || !!c.fk || c.unique;

export function indexedColumns(t: DbTable): Set<string> {
  const out = new Set<string>();
  for (const i of t.indexes ?? []) i.columns.forEach((c) => out.add(c.toLowerCase()));
  return out;
}

/** A value for a quoted attribute selector ([data-tid="…"]); CSS.escape is not everywhere (jsdom). */
export const attr = (v: string) => v.replace(/["\\]/g, "\\$&");
