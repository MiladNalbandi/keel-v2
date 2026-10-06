// Box geometry of the database diagram: which rows a table shows, how big its box is, where each column's port sits.
// Text is measured by character count: rows use the monospace font (an exact 0.6em advance), titles the UI font.

import type { DbColumn, DbTable } from "../../api";
import { isKeyColumn, schemaLabel } from "./model";

export type Mode = "all" | "keys" | "names";
export type Notation = "crow" | "arrow";
export type Prefs = { mode: Mode; types: boolean; unrelated: boolean; notation: Notation };
export const DEFAULT_PREFS: Prefs = { mode: "all", types: true, unrelated: true, notation: "crow" };

export const HEAD = 30;          // header height
export const ROW = 20;           // one column
export const FOOT = 20;          // "+N more"
export const PAD_B = 5;          // under the last row
export const ROW_FONT = 12;      // px, monospace
export const CH = ROW_FONT * 0.6;
export const TITLE_CH = 7.6;     // 12.5px semi-bold UI font, a generous average
export const MIN_W = 168, MAX_W = 380;
export const COLLAPSE_AT = 26;   // a table with more columns shows this many and "+N more" until expanded
export const COLLAPSED_ROWS = 20;

export type BoxShape = {
  id: string;
  w: number;
  h: number;
  /** Column indexes shown, in order. */
  rows: number[];
  /** Columns not shown (the "+N more" footer). */
  more: number;
  title: string;
  schema: string;
  /** Per shown row: the name and type as drawn (truncated to fit). */
  cells: { name: string; type: string; nullable: boolean }[];
};

export function shownRows(t: DbTable, mode: Mode, expanded: boolean): { rows: number[]; more: number } {
  if (mode === "names") return { rows: [], more: 0 };
  const all = t.columns.map((_, i) => i);
  const picked = mode === "keys" ? all.filter((i) => isKeyColumn(t.columns[i])) : all;
  if (mode === "all" && !expanded && picked.length > COLLAPSE_AT) {
    return { rows: picked.slice(0, COLLAPSED_ROWS), more: picked.length - COLLAPSED_ROWS };
  }
  return { rows: picked, more: 0 };
}

const cut = (s: string, max: number) => (s.length <= max ? s : max <= 1 ? "…" : s.slice(0, max - 1) + "…");

/** The type as a box shows it: the usual long spellings shortened (the Structure panel keeps them whole). */
export function shortType(t: string): string {
  return t.replace(/\s+/g, " ")
    .replace(/\btimestamp\s*(\(\d+\))?\s+with time zone\b/i, (_, p) => `timestamptz${p ?? ""}`)
    .replace(/\btimestamp\s*(\(\d+\))?\s+without time zone\b/i, (_, p) => `timestamp${p ?? ""}`)
    .replace(/\btime\s*(\(\d+\))?\s+with time zone\b/i, (_, p) => `timetz${p ?? ""}`)
    .replace(/\bcharacter varying\b/i, "varchar")
    .replace(/\bdouble precision\b/i, "float8");
}

export const typeLabel = (c: DbColumn) => shortType(c.type || (c.generated ? "generated" : ""));

export function boxShape(t: DbTable, mode: Mode, types: boolean, expanded: boolean): BoxShape {
  const { rows, more } = shownRows(t, mode, expanded);
  const schema = schemaLabel(t);
  // icon 16 + gap; name; gap 18; type; "?" 8; padding 10 + 10
  const nameLen = Math.max(0, ...rows.map((i) => t.columns[i].name.length));
  const typeLen = types ? Math.max(0, ...rows.map((i) => typeLabel(t.columns[i]).length + (t.columns[i].nullable ? 1 : 0))) : 0;
  const rowW = 10 + 18 + nameLen * CH + (types && typeLen ? 18 + typeLen * CH : 0) + 12;
  const headW = 10 + 22 + t.name.length * TITLE_CH + (schema ? 8 + schema.length * 6.6 : 0) + 14;
  const w = Math.ceil(Math.min(MAX_W, Math.max(MIN_W, rowW + 2, headW)));
  const h = HEAD + rows.length * ROW + (more ? FOOT : 0) + (rows.length || more ? PAD_B : 0);
  // fit text: names keep at least 10 characters, types get the rest
  const inner = w - 10 - 18 - 12;
  const cells = rows.map((i) => {
    const c = t.columns[i];
    const ty = types ? typeLabel(c) : "";
    const tyMax = ty ? Math.max(4, Math.floor((inner - 18 - Math.min(c.name.length, 14) * CH) / CH + 0.05) - (c.nullable ? 1 : 0)) : 0;
    const tyShown = ty ? cut(ty, tyMax) : "";
    const nameMax = Math.max(6, Math.floor((inner - (tyShown ? 18 + (tyShown.length + (c.nullable ? 1 : 0)) * CH : 0)) / CH + 0.05));
    return { name: cut(c.name, nameMax), type: tyShown, nullable: c.nullable };
  });
  const titleMax = Math.max(6, Math.floor((w - 10 - 22 - 14 - (schema ? 8 + schema.length * 6.6 : 0)) / TITLE_CH));
  return { id: t.id, w, h, rows, more, title: cut(t.name, titleMax), schema, cells };
}

/** The y of a column's row centre inside its box; a hidden column attaches to the footer, or to the header. */
export function portOffset(t: DbTable, shape: BoxShape, column: string | undefined): number {
  if (column !== undefined) {
    const low = column.toLowerCase();
    const k = shape.rows.findIndex((i) => t.columns[i].name.toLowerCase() === low);
    if (k >= 0) return HEAD + k * ROW + ROW / 2;
    if (shape.more) return HEAD + shape.rows.length * ROW + FOOT / 2;
  }
  return HEAD / 2;
}
