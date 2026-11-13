// The budget bar on top of every page: this project today and this month (with its day and month caps), and each
// running flow against its own cap. Amber from 80% used, red from 95%. It links to Budget. (#101 no provider plan
// windows: every model runs on its API, and keel's own count and caps are the budget.)

import { useEffect, useRef } from "react";
import { api, type BudgetNow, type CapLeft, type FlowSpend } from "../api";
import { kfmt, usd } from "../format";
import { useApp, useLoad } from "../state";

/** At most one reload per this many ms while live events arrive; and one per minute anyway. */
const LIVE_MS = 5_000;
const IDLE_MS = 60_000;

type Tone = "ok" | "warn" | "bad";
export const toneOf = (pct: number | null): Tone => (pct == null ? "ok" : pct >= 95 ? "bad" : pct >= 80 ? "warn" : "ok");
const pctOf = (used: number, cap: number | null | undefined) => (cap && cap > 0 ? Math.round((used / cap) * 100) : null);
const money = (c: Pick<CapLeft, "unit">) => c.unit === "usd";
const amount = (c: Pick<CapLeft, "unit">, n: number) => (money(c) ? usd(n) : kfmt(n));

/** A flow against its tightest cap: tokens when it has a token cap, else dollars, else no cap. */
export function flowUse(f: FlowSpend): { used: string; cap: string | null; pct: number | null } {
  const byTokens = pctOf(f.tokens, f.cap_tokens);
  const byUsd = pctOf(f.cost_usd, f.cap_usd);
  if (byUsd != null && (byTokens == null || byUsd > byTokens)) return { used: usd(f.cost_usd), cap: usd(f.cap_usd), pct: byUsd };
  return { used: kfmt(f.tokens), cap: f.cap_tokens ? kfmt(f.cap_tokens) : null, pct: byTokens };
}

function Meter({ pct }: { pct: number | null }) {
  if (pct == null) return null;
  return <span className={`bb-meter m-${toneOf(pct)}`} aria-hidden="true"><i style={{ width: `${Math.min(100, pct)}%` }} /></span>;
}

/** Today or this month, with the fullest cap of that window (all of them in the tooltip). */
function SpendSeg({ label, spend, all, caps, hint }: { label: string; spend: BudgetNow["today"]; all?: BudgetNow["today"]; caps: CapLeft[]; hint: string }) {
  const top = caps.map((c) => ({ c, pct: pctOf(c.used, c.limit) })).sort((a, b) => (b.pct ?? 0) - (a.pct ?? 0))[0];
  const capText = (c: CapLeft) => `${amount(c, c.used)} of ${amount(c, c.limit)}${c.window === "month" ? " API" : ""} (${c.action} when used up)`;
  // 0.18.55 other projects spent too (keel Product's own runs have a project of their own): the total for all of them
  const others = !!all && (all.tokens > spend.tokens || all.cost_usd > spend.cost_usd + 0.005);
  const title = [`${label}: ${kfmt(spend.tokens)} tokens, ${usd(spend.cost_usd)} at API prices (${hint}).`,
    ...(others && all ? [`All projects (keel Product too): ${kfmt(all.tokens)} tokens, ${usd(all.cost_usd)}.`] : []),
    ...caps.map((c) => `Cap: ${capText(c)}.`)].join("\n");
  return (
    <a className={`bb-seg bb-${toneOf(top?.pct ?? null)}`} href="#/settings/budget" title={title} data-testid={`bb-${label.toLowerCase().replace(/\W+/g, "-")}`}>
      <span className="bb-l">{label}</span>
      <b className="num">{kfmt(spend.tokens)}</b>
      <span className="bb-sub bb-cost num">{usd(spend.cost_usd)}</span>
      {others && all && <span className="bb-sub bb-all num" data-testid={`bb-all-${label.toLowerCase().replace(/\W+/g, "-")}`}>· all {usd(all.cost_usd)}</span>}
      {top && <>
        <span className="bb-sub num">· {money(top.c) ? "" : "cap "}{amount(top.c, top.c.limit)}{top.c.window === "month" ? " API" : ""}</span>
        <Meter pct={top.pct} />
        <span className={`bb-pct num t-${toneOf(top.pct)}`}>{top.pct}%</span>
      </>}
    </a>
  );
}

function FlowSeg({ flows }: { flows: FlowSpend[] }) {
  const ranked = flows.map((f) => ({ f, u: flowUse(f) })).sort((a, b) => (b.u.pct ?? -1) - (a.u.pct ?? -1));
  const { f, u } = ranked[0];
  const more = ranked.length - 1;
  const title = ranked.map(({ f: x, u: y }) => `${x.title || "flow"} (${x.status}): ${y.used}${y.cap ? ` of ${y.cap} cap` : ", no cap"} · ${usd(x.cost_usd)}`).join("\n");
  return (
    <a className={`bb-seg bb-flow bb-${toneOf(u.pct)}`} href="#/flow" title={title} data-testid="bb-flow">
      <span className={`bb-dot ${f.status === "waiting" ? "is-wait" : "is-run"}`} aria-hidden="true" />
      <span className="bb-l">{f.status === "waiting" ? "waiting" : "running"}</span>
      <span className="bb-title">{f.title || "flow"}</span>
      <b className="num">{u.used}</b>
      <span className="bb-sub num">{u.cap ? `/ ${u.cap}` : "· no cap"}</span>
      <Meter pct={u.pct} />
      {u.pct != null && <span className={`bb-pct num t-${toneOf(u.pct)}`}>{u.pct}%</span>}
      {more > 0 && <span className="bb-more">+{more}</span>}
    </a>
  );
}

export function BudgetBar() {
  const { pid, projectsLoaded, tick } = useApp();
  const now = useLoad(pid ? `budget-now:${pid}` : null, () => api.budgetNow(pid!), { live: false });

  // Live events come in bursts while an agent works: reload at most every LIVE_MS, and every IDLE_MS anyway.
  const last = useRef(Date.now());
  const reload = now.reload;
  useEffect(() => {
    if (!tick) return;
    const t = window.setTimeout(() => {
      last.current = Date.now();
      void reload();
    }, Math.max(0, last.current + LIVE_MS - Date.now()));
    return () => window.clearTimeout(t);
  }, [tick, reload]);
  useEffect(() => {
    const t = window.setInterval(() => {
      if (document.hidden) return; // a background tab asks nothing; the live events catch it up when it is shown
      last.current = Date.now();
      void reload();
    }, IDLE_MS);
    return () => window.clearInterval(t);
  }, [reload]);

  const b = now.data;

  const today = b?.caps.filter((c) => c.window === "day") ?? [];
  const month = b?.caps.filter((c) => c.window === "month") ?? [];
  const pcts = [
    ...(b?.caps ?? []).map((c) => pctOf(c.used, c.limit)),
    ...(b?.flows ?? []).map((f) => flowUse(f).pct),
  ].filter((p): p is number => p != null);
  const tone = toneOf(pcts.length ? Math.max(...pcts) : null);

  return (
    // drawn from the first moment (a page that measures its own top must not be pushed down later)
    <div className={`bbar bb-${tone}`} role="region" aria-label="Budget at a glance">
      {((pid && !b && !now.error) || (!pid && !projectsLoaded)) && <span className="bb-seg bb-wait">Budget…</span>}
      {!pid && projectsLoaded && <a className="bb-seg bb-sub" href="#/projects">No project yet: the budget starts with the first one</a>}
      {pid && now.error && <a className="bb-seg bb-sub" href="#/settings/budget" title={now.error.message}>Budget not loaded</a>}
      {b && <SpendSeg label="Today" spend={b.today} all={b.all_today} caps={today} hint="since 00:00 UTC" />}
      {b && <SpendSeg label="This month" spend={b.month} all={b.all_month} caps={month} hint="since the 1st, UTC" />}
      {b?.flows.length ? <FlowSeg flows={b.flows} /> : null}
    </div>
  );
}
