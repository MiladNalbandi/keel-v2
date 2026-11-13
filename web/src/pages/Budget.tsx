// Settings › Budget (keel 0.18.57; Control › Budget before), in two parts that never mix: what keel counted (this
// project this month, the tokens per day, and each account across all projects against the limit you set), and the
// limits that stop a flow (the rules for every flow, saved in General, plus this project's caps). Each part loads on its
// own. (#101 every model runs on its provider's API: there are no plan windows to read any more.)

import { useEffect, useState } from "react";
import { api, errorParts, type Budget, type Cap, type CapLeft, type CapScope, type CapsLeft, type Limit, type Model } from "../api";
import { EmptyState, Skeleton } from "../components/page";
import { Note, RadioCards, Seg } from "../components/settingsUi";
import { Drawer, ErrorBox, NoProject, Prov } from "../components/ui";
import { kfmt, parseTokens, PROV, usd } from "../format";
import { useApp, useLoad, type Loaded } from "../state";
import { SettingsFrame } from "./settings/frame";
import { GeneralRow, useScopedSettings, type Row } from "./settings/rows";

const PROVS = ["claude", "codex", "copilot", "fake"] as const;

function niceMax(v: number) {
  if (v <= 0) return 1000;
  const p = 10 ** Math.floor(Math.log10(v));
  const m = [1, 2, 2.5, 5, 10].find((x) => x * p >= v) ?? 10;
  return m * p;
}

/** "Sep 23" for the first day and the 1st of a month, else the day number. */
function dayLabel(day: string, first: boolean) {
  const d = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return day.slice(-2);
  return first || d.getUTCDate() === 1
    ? d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" })
    : String(d.getUTCDate());
}
const longDay = (day: string) => {
  const d = new Date(`${day}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? day : d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
};

/** Tokens per day, stacked by provider: one bar per day, a click (or Tab and Enter) on a bar says that day's numbers. */
export function UsageChart({ days }: { days: Budget["days"] }) {
  const totals = days.map((d) => PROVS.reduce((a, p) => a + (d[p] || 0), 0));
  const max = niceMax(Math.max(...totals, 0));
  const [picked, setPicked] = useState(days.length - 1);
  const i = Math.min(Math.max(0, picked), days.length - 1);
  const d = days[i];
  const shown = PROVS.filter((p) => days.some((x) => x[p]));
  const every = days.length <= 8 ? 1 : 2;
  return (
    <>
      <div className="ss-chart">
        <div className="ss-chart-y" aria-hidden="true"><span>{kfmt(max)}</span><span>{kfmt(max / 2)}</span><span>0</span></div>
        <div className="ss-chart-p" role="group"
          aria-label={`Tokens per day, last ${days.length} days, by provider. Most in one day: ${kfmt(Math.max(...totals, 0))} tokens.`}>
          <span className="ss-chart-grid" style={{ top: 0 }} />
          <span className="ss-chart-grid" style={{ top: "calc((100% - 22px) / 2)" }} />
          <span className="ss-chart-grid" style={{ bottom: 22 }} />
          {days.map((x, j) => (
            <button key={x.day} type="button" aria-pressed={j === i} onClick={() => setPicked(j)}
              aria-label={`${longDay(x.day)}: ${kfmt(totals[j])} tokens`} title={`${longDay(x.day)}: ${kfmt(totals[j])} tokens`}>
              {[...shown].reverse().map((p) => x[p] ? (
                <span key={p} className={`seg sw-${p}`} style={{ height: `${(x[p] / max) * 100}%` }} />
              ) : null)}
              {(j % every === 0 || j === days.length - 1) && <span className="lab" aria-hidden="true">{dayLabel(x.day, j === 0)}</span>}
            </button>
          ))}
        </div>
      </div>
      {d && (
        <span role="status" style={{ fontSize: 13 }} data-testid="chart-picked">
          <b style={{ fontWeight: 600 }}>{longDay(d.day)}: {kfmt(totals[i])} tokens.</b>{" "}
          <span className="ss-meta">{shown.map((p) => `${PROV[p] ?? p} ${kfmt(d[p] || 0)}`).join(" · ")}</span>
        </span>
      )}
    </>
  );
}

const isMoney = (l: Pick<Limit, "unit">) => /\$|usd|spent/i.test(l.unit);
const isPct = (l: Pick<Limit, "unit">) => /%|window/i.test(l.unit) && !/tokens|requests/i.test(l.unit);
const isTokens = (l: Pick<Limit, "unit">) => /token/i.test(l.unit);

/** ["123k", "tokens in the last 5 hours"], ["$0.00", "spent this month"], ["212", "requests this month"]. */
export function limitUsed(l: Limit): [string, string] {
  if (isMoney(l)) return [usd(l.used), l.unit.replace(/^\s*(usd|\$)\s*/i, "").trim()];
  if (isPct(l)) return [`${Math.round(l.used)}%`, l.unit.replace(/^%\s*/, "")];
  return [isTokens(l) ? kfmt(l.used) : Math.round(l.used).toLocaleString(), l.unit];
}
/** The cap in the limit's unit: "$100.00", "300", "600k". */
export const limitCap = (l: Pick<Limit, "unit" | "cap">) => (isMoney(l) ? usd(l.cap) : isTokens(l) ? kfmt(l.cap) : Math.round(l.cap).toLocaleString());

function LimitsDrawer({ limits, focus, onClose, onSaved }: { limits: Limit[]; focus?: string; onClose: () => void; onSaved: (l: Limit[]) => void }) {
  const { toast } = useApp();
  const [rows, setRows] = useState(limits.map((l) => ({ ...l, text: l.cap ? (isTokens(l) ? kfmt(l.cap) : String(l.cap)) : "" })));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  useEffect(() => {
    if (focus) document.getElementById(`lim-${focus}`)?.focus();
  }, [focus]);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const out: Limit[] = rows.map(({ text, ...l }) => ({ ...l, cap: !text.trim() ? 0 : isTokens(l) ? parseTokens(text) : Number(text.replace(/[$,\s]/g, "")) || 0 }));
      const saved = await api.saveLimits(out);
      onSaved(saved ?? out);
      toast("Account limits saved.");
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title="Account limits" onClose={onClose} className="ss-drawer"
      footer={<><button className="ss-btn" type="button" onClick={onClose}>Cancel</button><button className="ss-btn p" type="button" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button></>}>
      <p className="ss-sub">keel counts what its own agent runs use on each account, across all projects. Write a limit to see how close you are. Leave it empty for no limit.</p>
      {rows.map((l, i) => (
        <div key={l.id} className="ss-label">
          <label htmlFor={`lim-${l.id}`}>{l.name} <span className="ss-meta">({l.unit})</span></label>
          <span className="ss-actions">
            <input type="text" id={`lim-${l.id}`} inputMode="decimal" value={l.text} placeholder="no limit" className="ss-input mono" style={{ width: 120 }}
              onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, text: e.target.value } : r)))} />
            <input type="text" aria-label={`${l.name} note`} value={l.note} placeholder="note, e.g. resets Nov 1" className="ss-input" style={{ flex: "1 1 180px" }}
              onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, note: e.target.value } : r)))} />
          </span>
          {isTokens(l) && <span className="ss-help">For example 600k or 1.2M.</span>}
        </div>
      ))}
      {!rows.length && <span className="ss-meta">No accounts known.</span>}
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

export const CAP_SCOPE: Record<CapScope, string> = {
  day: "All flows, per day",
  flow: "Each flow",
  step: "Any single agent step",
  api_month: "API keys, per month",
};
export const CAP_ACTION: Record<Cap["action"], string> = { pause: "pause and ask me", cheaper: "switch to cheaper models", stop: "stop" };
export const capLimit = (c: Pick<Cap, "limit" | "unit">) => (c.unit === "usd" ? usd(c.limit) : `${kfmt(c.limit)} tokens`);

/** How each scope counts, for the cap drawer. */
const CAP_HOW: Record<CapScope, string> = {
  day: "Counts every run of this project since 00:00 UTC. A flow that starts gets what is left today.",
  flow: "Each flow gets this much.",
  step: "Each agent step gets this much. A workflow step's own smaller limit still wins.",
  api_month: "Counts this project's runs on API keys since the 1st (UTC). A flow that starts gets what is left this month.",
};
export const HOW_CAPS = "A flow starts with the smallest cap left; keel checks it before every agent step.";

/** What a cap leaves now, in words: "450k left today", "used up · resets tomorrow", "all of it, every flow". */
export function capLeftText(l: CapLeft | undefined): { text: string; tone?: "bad" | "warn"; used?: string } {
  if (!l) return { text: "—" };
  if (!l.checked) return { text: "not checked: keel counts tokens per step, not dollars", tone: "warn" };
  if (l.window === "flow" || l.window === "step") return { text: `all of it, every ${l.window}` };
  const amount = (v: number) => (l.unit === "usd" ? usd(v) : kfmt(v));
  const when = l.window === "day" ? "today" : "this month";
  const used = `${amount(l.used)} used ${when}`;
  if (l.left <= 0) return { text: `used up · resets ${l.window === "day" ? "tomorrow, 00:00 UTC" : "on the 1st"}`, tone: "bad", used };
  return { text: `${amount(l.left)} left ${when}`, tone: l.limit > 0 && l.left / l.limit < 0.2 ? "warn" : undefined, used };
}

function CapDrawer({ pid, cap, onClose, onSaved }: { pid: string; cap: Cap | null; onClose: () => void; onSaved: (c: Cap) => void }) {
  const { toast } = useApp();
  const [scope, setScope] = useState<CapScope>(cap?.scope ?? "flow");
  const [unit, setUnit] = useState<Cap["unit"]>(cap?.unit ?? "tokens");
  const [limit, setLimit] = useState(cap ? (cap.unit === "usd" ? String(cap.limit) : kfmt(cap.limit)) : "");
  const [action, setAction] = useState<Cap["action"]>(cap?.action ?? "pause");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const value = unit === "usd" ? Number(limit.replace(/[$,\s]/g, "")) : parseTokens(limit);
  const valid = Number.isFinite(value) && value > 0;
  const save = async () => {
    if (!valid) {
      setErr({ message: "Write a limit above 0.", hint: unit === "usd" ? "For example 100." : "For example 500k or 1.2M." });
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const body = { scope, unit, limit: value, action };
      const out = cap ? await api.saveCap(pid, { ...body, id: cap.id }) : await api.addCap(pid, body);
      onSaved(out ?? { ...body, id: cap?.id ?? `cap-${Date.now()}` });
      toast(cap ? "Cap saved." : `Cap added. ${HOW_CAPS}`);
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title={cap ? "Edit cap" : "Add cap"} onClose={onClose} className="ss-drawer"
      footer={<><button className="ss-btn" type="button" onClick={onClose}>Cancel</button>
        <button className="ss-btn p" type="button" onClick={save} disabled={busy}>{busy ? "Saving…" : cap ? "Save cap" : "Add cap"}</button></>}>
      <div className="ss-label">
        <b id="cap-scope-h" style={{ fontWeight: 600, fontSize: 14 }}>What it limits</b>
        <RadioCards labelledBy="cap-scope-h" value={scope} onChange={(v) => {
          setScope(v);
          if (v === "api_month") setUnit("usd");
        }} options={(Object.keys(CAP_SCOPE) as CapScope[]).map((k) => ({ id: k, title: CAP_SCOPE[k], sub: CAP_HOW[k] }))} />
      </div>
      <div className="ss-label">
        <label htmlFor="cap-limit" style={{ fontWeight: 600, fontSize: 14 }}>Limit</label>
        <span className="ss-actions">
          <input type="text" id="cap-limit" className="ss-input mono" value={limit} onChange={(e) => { setLimit(e.target.value); setErr(null); }}
            placeholder={unit === "usd" ? "100" : "500k"} style={{ width: 120 }} aria-invalid={!!err} />
          <Seg label="Unit" value={unit} onChange={setUnit} options={[["tokens", "tokens"], ["usd", "US dollars (API cost)"]]} />
        </span>
        {unit === "usd" && (scope === "step"
          ? <Note tone="warn" role="note">keel cannot count dollars per step: this cap would limit nothing. Pick tokens.</Note>
          : <span className="ss-help">Dollars are the cost runs report, at the API's prices.{scope === "api_month" ? " This month counts API-key runs only." : ""}</span>)}
      </div>
      <div className="ss-label">
        <b style={{ fontWeight: 600, fontSize: 14 }}>When it is hit</b>
        <Seg label="When it is hit" value={action} onChange={setAction} options={(Object.keys(CAP_ACTION) as Cap["action"][]).map((k) => [k, CAP_ACTION[k]] as [Cap["action"], string])} />
      </div>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

/** What a flow started now gets from Settings and every cap (the smallest one left binds), or why it cannot start. */
function NextFlow({ left, caps }: { left: CapsLeft; caps: Cap[] }) {
  const n = left.next_flow;
  const style = { margin: 0, padding: "12px 20px", borderTop: "1px solid var(--border)", fontSize: 13 };
  if (n.refused) return <p className="ss-meta" style={style} data-testid="next-flow"><span className="ss-warn-text">A flow cannot start now: {n.refused.error}</span> {n.refused.hint}</p>;
  const from = (id?: string | null) => {
    const c = caps.find((x) => x.id === id);
    return c ? ` (${CAP_SCOPE[c.scope] ?? c.scope})` : id === "settings" ? " (Settings)" : "";
  };
  const parts: string[] = [];
  if (n.cap_tokens > 0) parts.push(`${kfmt(n.cap_tokens)} tokens${from(n.tokens_from)}, then ${CAP_ACTION[n.on_cap] ?? n.on_cap}`);
  if (n.cap_usd) parts.push(`${usd(n.cap_usd)} of reported cost${from(n.usd_from)}, then ${CAP_ACTION[n.on_cap_usd ?? n.on_cap] ?? n.on_cap_usd}`);
  if (n.step_cap_tokens) parts.push(`at most ${kfmt(n.step_cap_tokens)} tokens per agent step, then ${CAP_ACTION[n.step_on_cap ?? n.on_cap] ?? n.step_on_cap}`);
  return (
    <p className="ss-meta" style={style} data-testid="next-flow">
      {parts.length ? <>A flow started now gets {parts.join("; ")}.</> : "A flow started now has no token or dollar cap."}
      {n.cheaper && <> <span className="ss-warn-text">Every agent starts on the cheaper model: a cap that says so is used up.</span></>}
    </p>
  );
}

function CapsPanel({ pid, name, onAdd, onEdit, caps, left, onChanged }: {
  pid: string; name: string; caps: Loaded<Cap[]>; left: Loaded<CapsLeft>; onAdd: () => void; onEdit: (c: Cap) => void; onChanged: () => void;
}) {
  const { toast } = useApp();
  const [asking, setAsking] = useState<string | null>(null);
  const remove = async (c: Cap) => {
    try {
      await api.deleteCap(pid, c.id);
      caps.setData((l) => (l ? l.filter((x) => x.id !== c.id) : l));
      onChanged();
      toast("Cap deleted.");
    } catch (e) {
      toast(`Not deleted: ${errorParts(e).message}`);
    } finally {
      setAsking(null);
    }
  };
  const empty = !!caps.data && !caps.data.length;
  return (
    <section className="ss-list" aria-labelledby="b-caps">
      <div className="ss-row0">
        <b id="b-caps" style={{ flex: 1, fontWeight: 600 }}>Caps for {name}</b>
        {!empty && <button className="ss-btn sm" type="button" onClick={onAdd}>Add cap</button>}
      </div>
      {caps.error ? <div className="ss-row"><ErrorBox error={caps.error} onRetry={() => void caps.reload()} /></div>
        : !caps.data ? <div className="ss-row"><Skeleton lines={2} label="Loading caps" /></div>
          : empty ? (
            <div className="ss-row">
              <span className="ss-grow"><b style={{ fontWeight: 500 }}>No extra cap</b><span className="ss-meta">Only the cap per flow from Settings applies. Add one for a day, a single step or API spend.</span></span>
              <button className="ss-btn sm" type="button" onClick={onAdd}>Add cap</button>
            </div>
          ) : (
            <div className="ss-tablewrap" style={{ borderTop: "1px solid var(--border)" }}><table aria-label="Caps" className="ss-table">
              <thead><tr><th>Scope</th><th>Limit</th><th>Left now</th><th>When hit</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {caps.data.map((c) => {
                  const l = capLeftText(left.data?.caps.find((x) => x.id === c.id));
                  return (
                    <tr key={c.id}>
                      <td style={{ fontWeight: 500 }}>{CAP_SCOPE[c.scope] ?? c.scope}</td><td className="ss-mono">{capLimit(c)}</td>
                      <td data-label="left now" data-testid={`cap-left-${c.id}`}>
                        {!left.data && !left.error ? <span className="ss-meta">…</span>
                          : <><span style={l.tone ? { color: `var(--${l.tone})` } : undefined}>{l.text}</span>{l.used && <span className="ss-meta"> · {l.used}</span>}</>}
                      </td>
                      <td className="ss-meta">{CAP_ACTION[c.action] ?? c.action}</td>
                      <td style={{ textAlign: "right" }}>
                        {asking === c.id ? (
                          <span className="ss-actions" style={{ justifyContent: "flex-end", flexWrap: "nowrap" }}>
                            <button className="ss-btn sm dp" type="button" onClick={() => remove(c)}>Yes, delete</button>
                            <button className="ss-btn sm" type="button" onClick={() => setAsking(null)}>Keep</button>
                          </span>
                        ) : (
                          <span className="ss-actions" style={{ justifyContent: "flex-end", flexWrap: "nowrap" }}>
                            <button className="ss-btn sm g" type="button" aria-label={`Edit cap ${CAP_SCOPE[c.scope] ?? c.scope}`} onClick={() => onEdit(c)}>Edit</button>
                            <button className="ss-btn sm q" type="button" aria-label={`Delete cap ${CAP_SCOPE[c.scope] ?? c.scope}`} onClick={() => setAsking(c.id)}>Delete</button>
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table></div>
          )}
      {left.data && caps.data && <NextFlow left={left.data} caps={caps.data} />}
    </section>
  );
}

const limitProv = (l: Limit) => (l.id === "api" ? "api" : /claude|anthropic/i.test(l.name) ? "claude" : /codex|openai|gpt/i.test(l.name) ? "codex" : /copilot|github/i.test(l.name) ? "copilot" : "claude");

/** keel's own count for each account (all projects), next to the limit you set — or "No cap set" and a way to set one. */
function AccountsPanel({ limits, onEdit }: { limits: Loaded<Limit[]>; onEdit: (focus?: string) => void }) {
  return (
    <section className="ss-list narrow" aria-labelledby="b-acc">
      <div className="ss-row0">
        <b id="b-acc" style={{ flex: 1, fontWeight: 600 }}>Each account, all projects</b>
        {!!limits.data?.length && <button className="ss-btn sm" type="button" onClick={() => onEdit()}>Edit limits</button>}
      </div>
      {limits.error ? <div className="ss-row"><ErrorBox error={limits.error} onRetry={() => void limits.reload()} /></div>
        : !limits.data ? <div className="ss-row"><Skeleton lines={3} label="Counting" /></div>
          : !limits.data.length ? <div className="ss-row"><EmptyState compact title="No account to count">keel counts an account once a provider is set up in Models &amp; keys.</EmptyState></div> : (
            <ul aria-label="Accounts" style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {limits.data.map((l) => {
                const pc = l.cap > 0 ? (l.used / l.cap) * 100 : null;
                const tone = pc == null ? "ok" : pc > 80 ? "bad" : pc > 60 ? "warn" : "ok";
                return (
                  <li key={l.id} className="ss-acct" data-testid={`limit-${l.id}`}>
                    <span className="ss-inline"><span className={`ss-swatch sw-${limitProv(l)}`} aria-hidden="true" /><b style={{ fontWeight: 500 }}>{l.name}</b></span>
                    <span style={{ fontSize: 13 }}><b style={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{limitUsed(l)[0]}</b> {limitUsed(l)[1]}
                      {l.note && !/0 = not set/i.test(l.note) && <span className="ss-meta"> · {l.note}</span>}</span>
                    {pc == null ? (
                        <span className="ss-inline"><span className="ss-meta">No cap set</span>
                          <button className="ss-btn sm g" type="button" onClick={() => onEdit(l.id)} aria-label={`Set a cap for ${l.name}`}>Set a cap</button></span>
                      ) : (
                        <span className="ss-acct-cap">
                          <span className={`ss-bar t-${tone}`} role="img" aria-label={`${Math.round(pc)}% of the cap`}><i style={{ width: `${Math.min(100, pc)}%` }} /></span>
                          <span className="ss-meta ss-small">{Math.round(pc)}% of {limitCap(l)}</span>
                        </span>
                      )}
                  </li>
                );
              })}
            </ul>
          )}
    </section>
  );
}

/** 0.18.55 every project's runs this month, keel Product's own too (no project picker shows it), adding up to the total
 *  the accounts count; the API-keys column sums to the "API keys (all)" account row. */
function ProjectsPanel({ b }: { b: Loaded<Budget> }) {
  const rows = b.data?.projects ?? [];
  const all = b.data?.all;
  if (!b.data || !rows.length) return null;
  return (
    <section className="ss-list" aria-labelledby="b-proj">
      <div className="ss-row0"><b id="b-proj" style={{ fontWeight: 600 }}>Each project, this month</b></div>
      <div className="ss-tablewrap" style={{ borderTop: "1px solid var(--border)" }}><table className="ss-table" aria-label="Each project, this month">
        <thead><tr><th>Project</th><th>Tokens</th><th>At API prices</th><th>On API keys</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.project_id} data-testid={`project-spend-${r.project_id}`}>
              <td style={{ fontWeight: 600 }}>{r.name}</td>
              <td className="ss-mono" data-label="tokens">{kfmt(r.tokens)}</td>
              <td data-label="at API prices">{usd(r.cost_usd)}</td>
              <td data-label="on API keys">{r.api_cost_usd ? usd(r.api_cost_usd) : <span className="ss-meta">none</span>}</td>
            </tr>
          ))}
          {all && rows.length > 1 && (
            <tr data-testid="project-spend-all"><td style={{ fontWeight: 600 }}>All projects</td>
              <td className="ss-mono" data-label="tokens"><b>{kfmt(all.tokens)}</b></td>
              <td data-label="at API prices"><b>{usd(all.cost_usd)}</b></td>
              <td data-label="on API keys"><b>{usd(rows.reduce((n, r) => n + (r.api_cost_usd || 0), 0))}</b></td></tr>
          )}
        </tbody>
      </table></div>
    </section>
  );
}

function MonthKpis({ b, name }: { b: Loaded<Budget>; name: string }) {
  if (b.error) return <ErrorBox error={b.error} onRetry={() => void b.reload()} />;
  const m = b.data?.month;
  const v = (x: string | number) => (m ? x : <span className="pg-skel bd-kpi-skel" aria-hidden="true" />);
  return (
    <section className="ss-cardp" style={{ gap: 12 }} aria-labelledby="b-month">
      <b id="b-month" style={{ fontWeight: 600 }}>{name}, this month</b>
      {!m && <span className="sr-only" role="status">Loading this month's numbers…</span>}
      <div className="ss-kpis">
        <div className="ss-kpi"><span className="ss-meta">Tokens</span><b>{v(kfmt(m?.tokens))}</b></div>
        <div className="ss-kpi"><span className="ss-meta">Cost at API prices</span><b>{v(usd(m?.cost_usd))}</b></div>
        <div className="ss-kpi"><span className="ss-meta">Flows</span><b>{v(m?.flows ?? 0)}</b></div>
      </div>
    </section>
  );
}

/** The rules for every flow: the cap per flow and what a flow does when it is hit (General's values, saved here). */
export const BUDGET_ROWS: Row[] = [
  { key: "cap_tokens", label: "Cap per flow (tokens)", kind: { t: "tokens" }, help: "A flow stops at this many tokens and does what you pick below. Start a flow can set its own cap." },
  { key: "on_cap", label: "When a cap is hit", kind: { t: "select", opts: [["pause", "pause and ask me"], ["cheaper", "switch to cheaper model"], ["stop", "stop"]] },
    help: "Pause and ask you, switch to the cheaper model, or stop the flow." },
];

/** For every flow: the rules, editable, and the cheaper model they switch to (it is chosen in Models & keys). */
function ForEveryFlow({ pid, name }: { pid: string; name: string }) {
  const s = useScopedSettings(pid, name);
  const cheap = (s.proj.data?.effective ?? s.general.data)?.cheaper_model as Model | undefined;
  return (
    <section className="ss-cardp" style={{ gap: 0 }} aria-labelledby="b-every" data-testid="for-every-flow">
      <b id="b-every" style={{ fontWeight: 600 }}>For every flow</b>
      <span className="ss-meta ss-small">Saved for every project; a project that changes one says so here.</span>
      {s.general.error ? <ErrorBox error={s.general.error} onRetry={() => void s.general.reload()} /> : (
        BUDGET_ROWS.map((row) => <GeneralRow key={row.key} row={row} s={s} projectName={name} />)
      )}
      <div className="ss-set">
        <div className="ss-set-l"><b>Cheaper model</b><span className="ss-help">Used when a cap says to switch.</span></div>
        <div className="ss-set-c" style={{ fontSize: 13 }}>
          <span>
            {!cheap ? <span className="ss-meta">…</span> : cheap.provider === "fake"
              ? <span className="ss-warn-text">not set: a real flow pauses and asks instead</span>
              : <b style={{ fontWeight: 600 }}>{PROV[cheap.provider] ?? cheap.provider} {cheap.model}{cheap.effort ? ` · ${cheap.effort}` : ""}</b>}
            {" · "}<a href="#/settings/models">change in Models &amp; keys</a>
          </span>
        </div>
      </div>
    </section>
  );
}

export function BudgetPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const name = project?.name ?? pid;
  const [capEdit, setCapEdit] = useState<Cap | "new" | null>(null);
  const line = `What ${pid ? name : "a project"} used, and the limits that stop a flow.`;
  if (!pid)
    return (
      <SettingsFrame tab="budget" title="Budget" line={line}>
        <NoProject />
      </SettingsFrame>
    );
  return (
    <SettingsFrame tab="budget" title="Budget" line={line}
      actions={<button className="ss-btn p" type="button" onClick={() => setCapEdit("new")}>Add cap</button>}>
      <BudgetBody pid={pid} name={name} capEdit={capEdit} setCapEdit={setCapEdit} />
    </SettingsFrame>
  );
}

function BudgetBody({ pid, name, capEdit, setCapEdit }: { pid: string; name: string; capEdit: Cap | "new" | null; setCapEdit: (c: Cap | "new" | null) => void }) {
  const budget = useLoad(`budget:${pid}`, () => api.budget(pid));
  const limits = useLoad("limits", () => api.limits());
  const [editing, setEditing] = useState<{ focus?: string } | null>(null);
  const caps = useLoad(`caps:${pid}`, () => api.caps(pid), { live: false });
  const left = useLoad(`capsLeft:${pid}`, () => api.capsLeft(pid));
  const b = budget.data;
  const shown = b ? PROVS.filter((p) => b.days.some((d) => d[p])) : [];
  const empty = (title: string, text: string) => <div className="ss-row"><EmptyState compact title={title}>{text}</EmptyState></div>;
  return (
    <>
      <section className="ss-sec" aria-labelledby="b-count">
        <div>
          <h2 id="b-count" className="ss-h2">What keel counted</h2>
          <p className="ss-sub">Tokens and cost of the agent runs keel made: {name} this month, and each account across all projects.</p>
        </div>
        <MonthKpis b={budget} name={name} />
        <div className="ss-split">
          <section className="ss-cardp wide" style={{ gap: 12 }} aria-labelledby="b-days">
            <div className="ss-inline" style={{ gap: "8px 16px" }}>
              <b id="b-days" style={{ flex: 1, fontWeight: 600 }}>Tokens per day</b>
              {shown.map((p) => <span key={p} className="ss-legend"><span className={`ss-swatch sw-${p}`} />{PROV[p]}</span>)}
            </div>
            {!b ? (budget.error ? <span className="ss-meta">No chart: the budget did not load.</span> : <Skeleton lines={4} label="Drawing" />)
              : shown.length ? <UsageChart days={b.days} />
                : <EmptyState compact title="No usage yet">The chart fills in once an agent of {name} has run.</EmptyState>}
            <span className="ss-meta ss-small">Last {b?.days.length || 14} days. Input and output tokens, plus a tenth of cached input (it costs about a tenth).</span>
          </section>
          <AccountsPanel limits={limits} onEdit={(focus) => setEditing({ focus })} />
        </div>
        <ProjectsPanel b={budget} />
        <div className="ss-split">
          <section className="ss-list half" aria-labelledby="b-top">
            <div className="ss-row0"><b id="b-top" style={{ fontWeight: 600 }}>Biggest users this month</b></div>
            {!b ? (!budget.error && <div className="ss-row"><Skeleton lines={3} /></div>) : !b.top.length ? empty("No agent ran this month", "The agents that used the most tokens show here.") : (
              <div className="ss-tablewrap" style={{ borderTop: "1px solid var(--border)" }}><table className="ss-table" aria-label="Biggest users this month">
                <thead><tr><th>Agent</th><th>Model</th><th>Tokens</th><th>At API prices</th></tr></thead>
                <tbody>
                  {b.top.map((t, i) => (
                    <tr key={i}><td style={{ fontWeight: 600 }}>{t.agent}</td><td><Prov p={t.provider} /></td><td className="ss-mono" data-label="tokens">{kfmt(t.tokens)}</td>
                      <td data-label="at API prices">{usd(t.cost_usd)}</td></tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </section>
          <section className="ss-list half" aria-labelledby="b-est">
            <div className="ss-row0"><b id="b-est" style={{ fontWeight: 600 }}>Estimate vs real, last flows</b></div>
            {!b ? (!budget.error && <div className="ss-row"><Skeleton lines={3} /></div>) : !b.recent.length ? empty("No flow yet", "Each flow's estimate and what it really used show here.") : (
              <div className="ss-tablewrap" style={{ borderTop: "1px solid var(--border)" }}><table className="ss-table" aria-label="Estimate vs real">
                <thead><tr><th>Flow</th><th>Estimate</th><th>Real</th><th>Diff</th><th>Status</th></tr></thead>
                <tbody>
                  {b.recent.map((r, i) => {
                    const d = r.estimate ? Math.round(((r.real - r.estimate) / r.estimate) * 100) : null;
                    return (
                      <tr key={i}><td>{r.title}</td>
                        <td className="ss-mono" data-label="estimate">{r.estimate ? kfmt(r.estimate) : "—"}</td>
                        <td className="ss-mono" data-label="real">{kfmt(r.real)}</td>
                        <td data-label="diff" style={d == null ? undefined : { color: `var(--${Math.abs(d) > 30 ? "bad" : Math.abs(d) > 10 ? "warn" : "ok"})` }}>{d == null ? "—" : `${d > 0 ? "+" : ""}${d}%`}</td>
                        <td className="ss-meta">{r.status}</td></tr>
                    );
                  })}
                </tbody>
              </table></div>
            )}
          </section>
        </div>
      </section>

      <section className="ss-sec" aria-labelledby="b-limits">
        <div>
          <h2 id="b-limits" className="ss-h2">Limits that stop a flow</h2>
          <p className="ss-sub">{HOW_CAPS} When one is reached, the flow pauses and asks you, switches to the cheaper model, or stops. A cap that is used up today or this month stops a new flow from starting (or starts it on the cheaper model).</p>
        </div>
        <ForEveryFlow pid={pid} name={name} />
        <CapsPanel pid={pid} name={name} caps={caps} left={left} onAdd={() => setCapEdit("new")} onEdit={(c) => setCapEdit(c)} onChanged={() => void left.reload()} />
      </section>

      <p className="ss-sub ss-small">Where the numbers come from: keel counts the tokens each API call reports, and prices them with its price table.</p>
      {capEdit && <CapDrawer pid={pid} cap={capEdit === "new" ? null : capEdit} onClose={() => setCapEdit(null)}
        onSaved={(c) => {
          caps.setData((l) => (l ? (l.some((x) => x.id === c.id) ? l.map((x) => (x.id === c.id ? c : x)) : [...l, c]) : [c]));
          void left.reload();
        }} />}
      {editing && limits.data && <LimitsDrawer limits={limits.data} focus={editing.focus} onClose={() => setEditing(null)} onSaved={(l) => limits.setData(l)} />}
    </>
  );
}
