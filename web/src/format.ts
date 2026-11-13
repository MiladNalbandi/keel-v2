// Small formatting helpers shared by every screen.

import type { Model, Provider } from "./api";

export const PROV: Record<string, string> = { claude: "Claude", codex: "GPT / Codex", copilot: "Copilot", vertex: "Vertex AI", local: "Local model", fake: "Fake model" };
/** How an AC status reads in the UI. */
export const AC_LABEL: Record<string, string> = { "already-met": "already met" };
export const acLabel = (s: string) => AC_LABEL[s] ?? s;
/** #101 how keel reaches a provider: always its API (a model saved as "subscription" or "opencode" runs there too). */
export const MODE_LABEL: Record<string, string> = { api: "API key" };

/** 1234 → "1k", 1_500_000 → "1.50M". */
export function kfmt(n: number | null | undefined): string {
  const v = Number(n) || 0;
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (Math.abs(v) >= 1e3) return Math.round(v / 1e3) + "k";
  return String(Math.round(v));
}

export const usd = (n: number | null | undefined) => "$" + (Number(n) || 0).toFixed(2);

/** Milliseconds → "4m09s"; from an hour on "1h 12m" (a gate that waits since yesterday is not "1452m10s"). */
export function dur(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
}

/** Milliseconds → "1h 12m", "12m", "40s" (rounded to the second). */
export function shortDur(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m` : `${s}s`;
}

/** Bytes → "300 KB" / "1.5 MB"; null when there is no size. */
export const bytes = (n?: number) =>
  !n
    ? null
    : n >= 1024 * 1024
      ? `${(n / 1024 / 1024).toFixed(1)} MB`
      : `${Math.max(1, Math.round(n / 1024))} KB`;

/** 1200 → "1.2k", 800 → "800". */
export const k1 = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

/** "1h 12m", "41 min", "3 days" until an ISO time; "" when unknown or past. */
export function untilText(iso?: string | null, now = Date.now()): string {
  if (!iso) return "";
  const mins = Math.floor((Date.parse(iso) - now) / 60_000);
  if (Number.isNaN(mins) || mins < 0) return "";
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60), m = mins % 60;
  if (h < 48) return m ? `${h}h ${m}m` : `${h}h`;
  return `${Math.floor(h / 24)} days`;
}

/** "just now", "12 min ago", "3h ago". */
export function agoText(iso?: string | null, now = Date.now()): string {
  if (!iso) return "";
  const mins = Math.floor((now - Date.parse(iso)) / 60_000);
  if (Number.isNaN(mins)) return "";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 48 * 60) return `${Math.floor(mins / 60)}h ago`;
  return `${Math.floor(mins / 1440)} days ago`;
}

export function since(iso: string | null | undefined, end?: string | null): string {
  if (!iso) return "—";
  const a = Date.parse(iso);
  const b = end ? Date.parse(end) : Date.now();
  if (Number.isNaN(a)) return "—";
  return dur(b - a);
}

/** ISO time → "12:05:41" (or the date when it is not today). */
export function clock(iso: string | null | undefined, withSeconds = true): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const today = new Date();
  if (d.toDateString() !== today.toDateString()) {
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  const t = d.toTimeString().slice(0, 8);
  return withSeconds ? t : t.slice(0, 5);
}

export const modelLabel = (m: Model | null | undefined) =>
  m ? `${m.model}${m.effort ? " · " + m.effort : ""}` : "default";

export const provLabel = (p: Provider | string | undefined) => (p ? PROV[p] ?? p : "—");

/** "Doc-Sync" → "doc-sync". */
export const slug = (s: string) =>
  s.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "new";

/** "600k" / "1.2M" / "150000" → tokens. */
export function parseTokens(s: string): number {
  const m = String(s).trim().toLowerCase().replace(/[, _]/g, "").match(/^(\d+(?:\.\d+)?)([km]?)/);
  if (!m) return 0;
  const n = parseFloat(m[1]);
  return Math.round(m[2] === "m" ? n * 1e6 : m[2] === "k" ? n * 1e3 : n);
}

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Markdown as one short plain line (notification text): no #, **, backticks or links' brackets; at most `max` chars. */
export function plainText(md: string | null | undefined, max = 220): string {
  const t = String(md ?? "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/(\*\*|__|\*|_)(\S[^*_]*?)\1/g, "$2")
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/-{3,}/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return t.length > max ? t.slice(0, max - 1).trimEnd() + "…" : t;
}

/** 0.18.57 The Code page's link to a file at a line, as one branch, review ref or commit has it: an answer, a finding or
 *  a citation made while looking at a branch the project folder may not have checked out opens THAT file
 *  (`#/repo/<path>:<line>?ref=<ref>`); without a ref, the work tree's. */
export function codeHref(path: string, line?: number | null, ref?: string | null): string {
  const p = path.replace(/^\.\//, "").split("/").map(encodeURIComponent).join("/");
  return `#/repo/${p}${line ? `:${line}` : ""}${ref ? `?ref=${encodeURIComponent(ref)}` : ""}`;
}

/** `src/app/checkout.js:12` or `src/app/checkout.js:12-14` → {path, line}; anything else → null (KeelBot's answers). */
export function fileLink(text: string): { path: string; line: number } | null {
  const m = /^\s*([\w@.~-][\w@./~ -]*\.[\w]+):(\d+)(?:[-–](\d+))?\s*$/.exec(text);
  if (!m || m[1].includes("://") || m[1].startsWith("/")) return null;
  return { path: m[1].replace(/^\.\//, ""), line: Number(m[2]) };
}
