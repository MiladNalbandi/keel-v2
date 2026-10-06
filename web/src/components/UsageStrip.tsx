// The usage dashboard: for each provider that is set up, what its plan says is used and what remains.
// Every number says where it came from and how old it is ("as of last run, 12 min ago"). The full strip sits on
// the All-projects page and the Budget page; a compact one sits in the sidebar header; Connections cards show the line.

import { useEffect, useState, type ReactNode } from "react";
import { api, errorParts, type ProviderUsage, type UsageWindow } from "../api";
import { usd } from "../format";
import { useApp, useLoad } from "../state";

const SHORT: Record<string, string> = { five_hour: "5h", seven_day: "week", month: "month" };
const REFRESH_MS = 60_000;

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

const pct = (w: UsageWindow) => (w.used_pct == null ? null : Math.round(w.used_pct * 100));
const tone = (p: number | null) => (p == null ? "ok" : p >= 95 ? "bad" : p >= 80 ? "warn" : "ok");

/** The card's one line, e.g. "5h 62% · week 31% · resets in 1h 12m" or "212 / 300 premium · resets Nov 1". */
export function usageLine(u: ProviderUsage, now = Date.now()): string {
  if (!u.windows.length) return u.note ?? "No numbers yet.";
  const live = u.windows.filter((w) => !w.resets_at || Date.parse(w.resets_at) > now);
  if (!live.length) return "The windows have reset since keel last saw them.";
  if (u.kind === "api") {
    const w = live[0];
    return `${usd(w.used)}${w.cap ? ` of ${usd(w.cap)}` : ""} this month`;
  }
  const parts = live.map((w) => {
    if (w.window === "month" && w.cap != null && w.used != null) return `${Math.round(w.used)} / ${Math.round(w.cap)} premium`;
    const p = pct(w);
    // Claude's subscription often reports only a status (allowed / rejected), no percentage: say that, not "?".
    const said = w.status === "allowed" ? "ok" : w.status === "rejected" ? "full" : w.status ? w.status.replace(/_/g, " ") : "no % yet";
    return `${SHORT[w.window] ?? w.label} ${p == null ? said : `${p}%`}`;
  });
  const first = live[0];
  const reset = first.window === "month" && first.resets_at
    ? `resets ${new Date(first.resets_at).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" })}`
    : untilText(first.resets_at, now) && `resets in ${untilText(first.resets_at, now)}`;
  return [...parts, reset].filter(Boolean).join(" · ");
}

/** "as of last run, 12 min ago" / "GitHub (unofficial), just now". */
export function sourceText(u: ProviderUsage, now = Date.now()): string {
  const ago = agoText(u.fetched_at, now);
  const src = u.source === "last run" ? "as of last run" : u.source;
  return ago ? `${src}, ${ago}` : src;
}

function fullest(u: ProviderUsage, now = Date.now()): number | null {
  const ps = u.windows.filter((w) => !w.resets_at || Date.parse(w.resets_at) > now).map(pct).filter((p): p is number => p != null);
  return ps.length ? Math.max(...ps) : null;
}

export function UsageCard({ u, compact, onRefreshed }: { u: ProviderUsage; compact?: boolean; onRefreshed: (u: ProviderUsage) => void }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const top = fullest(u);
  const refresh = async () => {
    setBusy(true);
    try {
      const next = await api.refreshUsage(u.id);
      onRefreshed(next);
      if (next.error) toast(next.error);
    } catch (e) {
      toast(`Not refreshed: ${errorParts(e).message}`);
    } finally {
      setBusy(false);
    }
  };
  const label = u.id === "claude" ? "Refresh (one tiny Haiku call)" : `Refresh ${u.name}`;
  return (
    <div className={`ucard${compact ? " compact" : ""}`} data-testid={`usage-${u.id}`}>
      <div className="ucard-h">
        <span className="prov"><i className={`c-${u.id === "api" ? "fake" : u.id}`} /><b>{u.name}</b></span>
        {u.can_refresh && (
          <button className="btn sm ghost" type="button" onClick={refresh} disabled={busy} aria-label={label} title={label}>{busy ? "…" : "↻"}</button>
        )}
      </div>
      <span className="ucard-line">{usageLine(u)}</span>
      {top != null && <div className={`meter m-${tone(top)}`} aria-label={`${u.name} ${top}% used`}><i style={{ width: `${Math.min(100, top)}%` }} /></div>}
      {!compact && <span className="hint">{sourceText(u)}{u.error ? ` · ${u.error}` : ""}</span>}
      {compact && <span className="hint">{sourceText(u)}</span>}
    </div>
  );
}

/** The cards for every provider that is set up. Nothing is shown when none is (no empty box in the sidebar).
 *  `loading` is shown until the first answer (a page can hold the space); nothing by default. */
export function UsageStrip({ compact, empty, loading }: { compact?: boolean; empty?: ReactNode; loading?: ReactNode }) {
  const usage = useLoad("usage:providers", () => api.usageProviders(), { live: false });
  useEffect(() => {
    const t = window.setInterval(() => void usage.reload(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [usage.reload]);
  const cards = usage.data ?? [];
  if (!usage.data && !usage.error && loading) return <>{loading}</>;
  if (!cards.length) return usage.data && empty ? <>{empty}</> : null;
  const put = (n: ProviderUsage) => usage.setData((l) => (l ? l.map((x) => (x.id === n.id ? n : x)) : [n]));
  return (
    <div className={`ustrip${compact ? " compact" : ""}`} aria-label="Provider usage">
      {cards.map((u) => <UsageCard key={u.id} u={u} compact={compact} onRefreshed={put} />)}
    </div>
  );
}

/** One provider's line, for its Connections card. */
export function UsageLine({ provider }: { provider: string }) {
  const usage = useLoad("usage:providers", () => api.usageProviders(), { live: false });
  const u = usage.data?.find((x) => x.id === provider);
  if (!u) return null;
  return <span className="hint" data-testid={`usage-line-${provider}`}>Usage: {usageLine(u)} ({sourceText(u)})</span>;
}
