// Budget (Control), in three parts that never mix: what the providers report (their plan windows), what keel counted
// (this project this month, and each account across all projects against the limit you set), and the limits that
// stop a flow (the cap and plan-window rules from Settings, plus this project's caps). Each part loads on its own.

import { useEffect, useState } from "react";
import { api, errorParts, type Budget, type Cap, type CapScope, type Limit } from "../api";
import { EmptyState, Section, Skeleton, useWidth } from "../components/page";
import { Drawer, ErrorBox, PageHead, Panel, Prov } from "../components/ui";
import { UsageStrip } from "../components/UsageStrip";
import { kfmt, parseTokens, PROV, usd } from "../format";
import { useApp, useLoad, type Loaded } from "../state";

const PROVS = ["claude", "codex", "copilot", "fake"] as const;
const FILL: Record<string, string> = { claude: "fill-claude", codex: "fill-codex", copilot: "fill-copilot", fake: "" };

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

/** Tokens per day, stacked by provider. A phone gets a narrower drawing (bigger text) instead of a sideways scroll. */
export function UsageChart({ days }: { days: Budget["days"] }) {
  const [box, width] = useWidth<HTMLDivElement>();
  const narrow = width > 0 && width < 520;
  const W = narrow ? 340 : 640, H = narrow ? 190 : 200, padL = narrow ? 38 : 46, padT = 26, padB = 24;
  const every = narrow ? 4 : days.length <= 8 ? 1 : 2;
  const totals = days.map((d) => PROVS.reduce((a, p) => a + (d[p] || 0), 0));
  const max = niceMax(Math.max(...totals, 0));
  const bw = (W - padL - 6) / Math.max(1, days.length);
  const gap = Math.min(8, bw * 0.25);
  const y = (v: number) => H - padB - (v / max) * (H - padB - padT);
  const ticks = [0, max / 2, max];
  return (
    <div ref={box}><svg viewBox={`0 0 ${W} ${H}`} className="chart bd-chart" role="img"
      aria-label={`Tokens per day, last ${days.length} days, by provider. Most in one day: ${kfmt(Math.max(...totals, 0))} tokens.`}>
      <text x={0} y={12} className="ax bd-ax-t">tokens per day</text>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={padL} x2={W - 2} y1={y(t)} y2={y(t)} stroke="var(--border)" />
          <text x={padL - 6} y={y(t) + 4} textAnchor="end" className="ax">{kfmt(t)}</text>
        </g>
      ))}
      {days.map((d, i) => {
        let base = 0;
        const x = padL + gap / 2 + i * bw;
        return (
          <g key={d.day}>
            {PROVS.map((p) => {
              const v = d[p] || 0;
              if (!v) return null;
              const r = <rect key={p} x={x} y={y(base + v)} width={Math.max(1, bw - gap)} height={Math.max(1, y(base) - y(base + v))} rx={2} className={FILL[p]} style={p === "fake" ? { fill: "var(--faint)" } : undefined}><title>{PROV[p]}: {kfmt(v)} tokens on {d.day}</title></rect>;
              base += v;
              return r;
            })}
            {i % every === 0 && <text x={x + (bw - gap) / 2} y={H - 6} textAnchor="middle" className="ax">{dayLabel(d.day, i === 0)}</text>}
          </g>
        );
      })}
    </svg></div>
  );
}

/** A limit keel can read from the provider is not typed by hand; its manual cap stays as the fallback. */
const hasLiveSource = (l: Limit) => !!l.source && l.id !== "claude";
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
    <Drawer title="Account limits" onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button><button className="btn primary" type="button" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button></>}>
      <p className="sub" style={{ margin: 0 }}>keel counts what its own agent runs use on each account, across all projects. Write the limit your plan allows to see how close you are. Leave it empty for no limit.</p>
      {rows.map((l, i) => hasLiveSource(l) ? (
        <div key={l.id} className="field"><span className="lab">{l.name}</span><span className="hint">Read from {l.source}. No limit to type.</span></div>
      ) : (
        <div key={l.id} className="field">
          <label htmlFor={`lim-${l.id}`}>{l.name} <span className="sub" style={{ textTransform: "none", letterSpacing: 0 }}>({l.unit})</span></label>
          <div className="row">
            <input type="text" id={`lim-${l.id}`} inputMode="decimal" value={l.text} placeholder="no limit" className="bd-lim-in"
              onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, text: e.target.value } : r)))} />
            <input type="text" aria-label={`${l.name} note`} value={l.note} placeholder="note, e.g. resets Nov 1" className="bd-lim-note"
              onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, note: e.target.value } : r)))} />
          </div>
          {isTokens(l) && <span className="hint">For example 600k or 1.2M.</span>}
        </div>
      ))}
      {!rows.length && <span className="sub">No accounts known.</span>}
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
      toast(cap ? "Cap saved." : "Cap added. It is checked before every agent step.");
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title={cap ? "Edit cap" : "Add cap"} onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={save} disabled={busy}>{busy ? "Saving…" : cap ? "Save cap" : "Add cap"}</button></>}>
      <div className="field"><label htmlFor="cap-scope">What it limits</label>
        <select id="cap-scope" value={scope} onChange={(e) => {
          const v = e.target.value as CapScope;
          setScope(v);
          if (v === "api_month") setUnit("usd");
        }}>
          {(Object.keys(CAP_SCOPE) as CapScope[]).map((k) => <option key={k} value={k}>{CAP_SCOPE[k]}</option>)}
        </select></div>
      <div className="field"><label htmlFor="cap-limit">Limit</label>
        <div className="row">
          <input type="text" id="cap-limit" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder={unit === "usd" ? "100" : "500k"} style={{ width: 120 }} />
          <select aria-label="Unit" value={unit} onChange={(e) => setUnit(e.target.value as Cap["unit"])}>
            <option value="tokens">tokens</option><option value="usd">US dollars (API cost)</option>
          </select>
        </div></div>
      <div className="field"><label htmlFor="cap-action">When it is hit</label>
        <select id="cap-action" value={action} onChange={(e) => setAction(e.target.value as Cap["action"])}>
          {(Object.keys(CAP_ACTION) as Cap["action"][]).map((k) => <option key={k} value={k}>{CAP_ACTION[k]}</option>)}
        </select></div>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

function CapsPanel({ pid, name, onAdd, onEdit, caps }: { pid: string; name: string; caps: Loaded<Cap[]>; onAdd: () => void; onEdit: (c: Cap) => void }) {
  const { toast } = useApp();
  const [asking, setAsking] = useState<string | null>(null);
  const remove = async (c: Cap) => {
    try {
      await api.deleteCap(pid, c.id);
      caps.setData((l) => (l ? l.filter((x) => x.id !== c.id) : l));
      toast("Cap deleted.");
    } catch (e) {
      toast(`Not deleted: ${errorParts(e).message}`);
    } finally {
      setAsking(null);
    }
  };
  const empty = !!caps.data && !caps.data.length;
  return (
    <Panel title={<h3>Caps for {name}</h3>} extra={empty ? undefined : <button className="btn sm" type="button" onClick={onAdd}>Add cap</button>} body={false}>
      {caps.error ? <div className="panel-body"><ErrorBox error={caps.error} onRetry={() => void caps.reload()} /></div>
        : !caps.data ? <Skeleton lines={2} label="Loading caps" />
          : empty ? (
            <EmptyState compact title="No extra cap" action={<button className="btn sm" type="button" onClick={onAdd}>Add cap</button>}>
              Only the cap per flow from Settings applies. Add one for a day, a single step or API spend.
            </EmptyState>
          ) : (
            <div className="table-wrap rt-wrap"><table aria-label="Caps" className="rt">
              <thead><tr><th>Scope</th><th>Limit</th><th>When hit</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {caps.data.map((c) => (
                  <tr key={c.id}>
                    <td className="rt-main">{CAP_SCOPE[c.scope] ?? c.scope}</td><td className="num mono">{capLimit(c)}</td><td className="sub">{CAP_ACTION[c.action] ?? c.action}</td>
                    <td className="rt-end"><div className="row" style={{ gap: 4, flexWrap: "nowrap", justifyContent: "flex-end" }}>
                      {asking === c.id ? (
                        <>
                          <button className="btn sm warn" type="button" onClick={() => remove(c)}>Yes, delete</button>
                          <button className="btn sm" type="button" onClick={() => setAsking(null)}>Keep</button>
                        </>
                      ) : (
                        <>
                          <button className="btn sm ghost" type="button" aria-label={`Edit cap ${CAP_SCOPE[c.scope] ?? c.scope}`} onClick={() => onEdit(c)}>Edit</button>
                          <button className="btn sm ghost" type="button" aria-label={`Delete cap ${CAP_SCOPE[c.scope] ?? c.scope}`} onClick={() => setAsking(c.id)}>Delete</button>
                        </>
                      )}
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
    </Panel>
  );
}

const limitProv = (l: Limit) => (l.id === "api" ? "fake" : /claude|anthropic/i.test(l.name) ? "claude" : /codex|openai|gpt/i.test(l.name) ? "codex" : /copilot|github/i.test(l.name) ? "copilot" : "claude");

/** keel's own count for each account (all projects), next to the limit you set — or "No cap set" and a way to set one. */
function AccountsPanel({ limits, onEdit }: { limits: Loaded<Limit[]>; onEdit: (focus?: string) => void }) {
  return (
    <Panel title={<h3>Each account, all projects</h3>}
      extra={limits.data?.length ? <button className="btn sm" type="button" onClick={() => onEdit()}>Edit limits</button> : undefined} body={false}>
      {limits.error ? <div className="panel-body"><ErrorBox error={limits.error} onRetry={() => void limits.reload()} /></div>
        : !limits.data ? <Skeleton lines={3} label="Counting" />
          : !limits.data.length ? <EmptyState compact title="No account to count">keel counts an account once a provider is set up in Connections.</EmptyState> : (
            <ul className="bd-accts" aria-label="Accounts">
              {limits.data.map((l) => {
                const live = hasLiveSource(l);
                const pc = l.cap > 0 ? (l.used / l.cap) * 100 : null;
                const tone = pc == null ? "ok" : pc > 80 ? "bad" : pc > 60 ? "warn" : "ok";
                return (
                  <li key={l.id} className="bd-acct" data-testid={`limit-${l.id}`}>
                    <span className="prov bd-acct-n"><i className={`c-${limitProv(l)}`} style={l.id === "api" ? { background: "var(--faint)" } : undefined} />{l.name}</span>
                    <span className="bd-acct-used"><b className="pg-num">{limitUsed(l)[0]}</b> {limitUsed(l)[1]}
                      {l.note && !/0 = not set/i.test(l.note) && <span className="hint"> · {l.note}</span>}</span>
                    <span className="bd-acct-cap">
                      {live ? <span className="sub">its own window is above (read from {l.source})</span>
                        : pc == null ? (
                          <>
                            <span className="sub">No cap set</span>
                            <button className="btn sm" type="button" onClick={() => onEdit(l.id)} aria-label={`Set a cap for ${l.name}`}>Set a cap</button>
                          </>
                        ) : (
                          <>
                            <span className={`meter m-${tone}`} aria-label={`${Math.round(pc)}% of the cap`}><i style={{ width: `${Math.min(100, pc)}%` }} /></span>
                            <span className="sub pg-num">{Math.round(pc)}% of {limitCap(l)}</span>
                          </>
                        )}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
    </Panel>
  );
}

function MonthKpis({ b, name }: { b: Loaded<Budget>; name: string }) {
  if (b.error) return <ErrorBox error={b.error} onRetry={() => void b.reload()} />;
  const m = b.data?.month;
  const v = (x: string | number) => (m ? x : <span className="pg-skel bd-kpi-skel" aria-hidden="true" />);
  return (
    <Panel title={<h3>{name}, this month</h3>} body="bd-kpis" className="bd-month">
      {!m && <span className="sr-only" role="status">Loading this month's numbers…</span>}
      <div><span className="sub">Tokens</span><b className="big num">{v(kfmt(m?.tokens))}</b></div>
      <div><span className="sub">Cost at API prices</span><b className="big num">{v(usd(m?.cost_usd))}</b><span className="hint">a plan pays for subscription runs</span></div>
      <div><span className="sub">Flows</span><b className="big num">{v(m?.flows ?? 0)}</b></div>
      <div><span className="sub">Copilot premium requests</span><b className="big num">{v((m?.premium_requests ?? 0).toLocaleString())}</b></div>
    </Panel>
  );
}

/** The flow-stopping rules that live in Settings: the cap per flow and the plan-window thresholds. */
function FromSettings({ pid }: { pid: string }) {
  const s = useLoad(`settings:${pid}`, () => api.projectSettings(pid), { live: false });
  const e = s.data?.effective;
  const pct = (x: number | undefined, d: number) => `${Math.round((x ?? d) * 100)}%`;
  const cheap = e?.cheaper_model;
  return (
    <Panel title={<h3>From Settings</h3>} extra={<a className="btn sm" href="#/settings">Change in Settings</a>} body={false}>
      {s.error ? <div className="panel-body"><ErrorBox error={s.error} onRetry={() => void s.reload()} /></div> : !e ? <Skeleton lines={3} label="Reading settings" /> : (
        <dl className="bd-rules">
          <div><dt>Cap per flow</dt><dd><b className="pg-num">{kfmt(e.cap_tokens)} tokens</b>, then {CAP_ACTION[e.on_cap] ?? e.on_cap}</dd></div>
          <div><dt>Plan window</dt><dd>warn at <b>{pct(e.usage_warn, 0.8)}</b> used, pause before the next agent at <b>{pct(e.usage_pause, 0.95)}</b></dd></div>
          <div><dt>Cheaper model</dt><dd>{!cheap || cheap.provider === "fake" ? <span className="amber">not set: a real flow pauses and asks instead</span> : <><b>{PROV[cheap.provider] ?? cheap.provider} {cheap.model}</b>, when a cap says to switch</>}</dd></div>
        </dl>
      )}
    </Panel>
  );
}

export function BudgetPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const budget = useLoad(`budget:${pid}`, () => api.budget(pid));
  const limits = useLoad("limits", () => api.limits());
  const [editing, setEditing] = useState<{ focus?: string } | null>(null);
  const caps = useLoad(`caps:${pid}`, () => api.caps(pid), { live: false });
  const [capEdit, setCapEdit] = useState<Cap | "new" | null>(null);
  const name = project?.name ?? pid;
  const b = budget.data;
  return (
    <>
      <PageHead title="Budget" sub={`What ${name} used, what each provider says is left, and the limits that stop a flow.`}
        actions={<button className="btn primary" type="button" onClick={() => setCapEdit("new")}>Add cap</button>} />

      <Section title="What the providers report" sub="Plan windows read from Claude, Codex and Copilot themselves. They reset on the provider's clock; keel only reads them.">
        <UsageStrip loading={<div className="panel"><Skeleton lines={2} label="Asking the providers" /></div>}
          empty={<div className="panel"><EmptyState title="No provider is set up yet" action={<a className="btn" href="#/connections">Open Connections</a>}>Save a login or a key in Connections to see its plan usage here.</EmptyState></div>} />
      </Section>

      <Section title="What keel counted" sub={`Tokens and cost of the agent runs keel made: ${name} this month, and each account across all projects.`}>
        <MonthKpis b={budget} name={name} />
        <div className="grid bd-g">
          <Panel title={<h3>Tokens per day</h3>} extra={b && <div className="legend">{PROVS.filter((p) => b.days.some((d) => d[p])).map((p) => <span key={p}><i className={`c-${p}`} style={{ border: 0, background: p === "fake" ? "var(--faint)" : undefined }} />{PROV[p]}</span>)}</div>}>
            {!b ? (budget.error ? <span className="sub">No chart: the budget did not load.</span> : <Skeleton lines={4} label="Drawing" />)
              : b.days.some((d) => PROVS.some((p) => d[p])) ? <UsageChart days={b.days} />
                : <EmptyState compact title="No usage yet">The chart fills in once an agent of {name} has run.</EmptyState>}
            <p className="hint" style={{ margin: "6px 0 0" }}>Last {b?.days.length || 14} days. Input and output tokens, plus a tenth of cached input (it costs about a tenth).</p>
          </Panel>
          <AccountsPanel limits={limits} onEdit={(focus) => setEditing({ focus })} />
        </div>
        <div className="grid bd-g">
          <Panel title={<h3>Biggest users this month</h3>} body={false}>
            {!b ? (!budget.error && <Skeleton lines={3} />) : !b.top.length ? <EmptyState compact title="No agent ran this month">The agents that used the most tokens show here.</EmptyState> : (
              <div className="table-wrap rt-wrap"><table className="rt" aria-label="Biggest users this month">
                <thead><tr><th>Agent</th><th>Model</th><th>Tokens</th><th>At API prices</th></tr></thead>
                <tbody>
                  {b.top.map((t, i) => (
                    <tr key={i}><td className="rt-main"><b>{t.agent}</b></td><td><Prov p={t.provider} /></td><td className="num mono" data-label="tokens">{kfmt(t.tokens)}</td>
                      <td className="num" data-label="at API prices">{t.cost_usd ? usd(t.cost_usd) : <span className="sub">subscription</span>}</td></tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </Panel>
          <Panel title={<h3>Estimate vs real, last flows</h3>} body={false}>
            {!b ? (!budget.error && <Skeleton lines={3} />) : !b.recent.length ? <EmptyState compact title="No flow yet">Each flow's estimate and what it really used show here.</EmptyState> : (
              <div className="table-wrap rt-wrap"><table className="rt" aria-label="Estimate vs real">
                <thead><tr><th>Flow</th><th>Estimate</th><th>Real</th><th>Diff</th><th>Status</th></tr></thead>
                <tbody>
                  {b.recent.map((r, i) => {
                    const d = r.estimate ? Math.round(((r.real - r.estimate) / r.estimate) * 100) : null;
                    return (
                      <tr key={i}><td className="rt-main">{r.title}</td>
                        <td className="num mono" data-label="estimate">{r.estimate ? kfmt(r.estimate) : "—"}</td>
                        <td className="num mono" data-label="real">{kfmt(r.real)}</td>
                        <td className="num" data-label="diff" style={d == null ? undefined : { color: `var(--${Math.abs(d) > 30 ? "bad" : Math.abs(d) > 10 ? "warn" : "ok"})` }}>{d == null ? "—" : `${d > 0 ? "+" : ""}${d}%`}</td>
                        <td className="sub rt-end">{r.status}</td></tr>
                    );
                  })}
                </tbody>
              </table></div>
            )}
          </Panel>
        </div>
      </Section>

      <Section title="Limits that stop a flow" sub="Checked before each agent step. When one is reached, the flow pauses and asks you, switches to the cheaper model, or stops.">
        <div className="grid bd-g">
          <FromSettings pid={pid} />
          <CapsPanel pid={pid} name={name} caps={caps} onAdd={() => setCapEdit("new")} onEdit={(c) => setCapEdit(c)} />
        </div>
      </Section>

      <p className="hint" style={{ marginTop: 18 }}>Where the numbers come from: the providers' own plan windows (Claude: its last run; Codex: codex app-server; Copilot: GitHub, unofficial); keel's count from LangChain usage metadata for API calls and each CLI's usage line.</p>
      {capEdit && <CapDrawer pid={pid} cap={capEdit === "new" ? null : capEdit} onClose={() => setCapEdit(null)}
        onSaved={(c) => caps.setData((l) => (l ? (l.some((x) => x.id === c.id) ? l.map((x) => (x.id === c.id ? c : x)) : [...l, c]) : [c]))} />}
      {editing && limits.data && <LimitsDrawer limits={limits.data} focus={editing.focus} onClose={() => setEditing(null)} onSaved={(l) => limits.setData(l)} />}
    </>
  );
}
