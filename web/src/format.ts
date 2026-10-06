// Small formatting helpers shared by every screen.

import type { Model, Provider } from "./api";

export const PROV: Record<string, string> = { claude: "Claude", codex: "GPT / Codex", copilot: "Copilot", fake: "Fake model" };
/** How an AC status reads in the UI. */
export const AC_LABEL: Record<string, string> = { "already-met": "already met" };
export const acLabel = (s: string) => AC_LABEL[s] ?? s;
export const MODE_LABEL: Record<string, string> = { subscription: "Subscription", api: "API key", opencode: "OpenCode" };

/** 1234 → "1k", 1_500_000 → "1.50M". */
export function kfmt(n: number | null | undefined): string {
  const v = Number(n) || 0;
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (Math.abs(v) >= 1e3) return Math.round(v / 1e3) + "k";
  return String(Math.round(v));
}

export const usd = (n: number | null | undefined) => "$" + (Number(n) || 0).toFixed(2);

/** Milliseconds → "4m09s". */
export function dur(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
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
