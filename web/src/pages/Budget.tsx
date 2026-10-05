// Budget (Control): what this project uses, the caps that stop its flows, and the account limits all
// projects share (editable), tokens per day by provider, estimate vs real, and the biggest users.

import { useState } from "react";
import { api, errorParts, type Budget, type Cap, type CapScope, type Limit } from "../api";
import { Async, Drawer, ErrorBox, PageHead, Panel, Prov } from "../components/ui";
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

export function UsageChart({ days }: { days: Budget["days"] }) {
  const W = 640, H = 190, padL = 44, padB = 24;
  const totals = days.map((d) => PROVS.reduce((a, p) => a + (d[p] || 0), 0));
  const max = niceMax(Math.max(...totals, 0));
  const bw = (W - padL - 10) / Math.max(1, days.length);
  const y = (v: number) => H - padB - (v / max) * (H - padB - 12);
  const ticks = [0, max / 3, (2 * max) / 3, max];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label={`Tokens per day, last ${days.length} days, by provider`}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={padL} x2={W - 6} y1={y(t)} y2={y(t)} stroke="var(--border)" />
          <text x={padL - 6} y={y(t) + 4} textAnchor="end" className="ax">{kfmt(t)}</text>
        </g>
      ))}
      {days.map((d, i) => {
        let base = 0;
        const x = padL + 4 + i * bw;
        return (
          <g key={d.day}>
            {PROVS.map((p) => {
              const v = d[p] || 0;
              if (!v) return null;
              const r = <rect key={p} x={x} y={y(base + v)} width={Math.max(1, bw - 8)} height={y(base) - y(base + v)} className={FILL[p]} style={p === "fake" ? { fill: "var(--faint)" } : undefined}><title>{PROV[p]} {kfmt(v)} · {d.day}</title></rect>;
              base += v;
              return r;
            })}
            {(i % 2 === 0 || days.length <= 8) && <text x={x + (bw - 8) / 2} y={H - 6} textAnchor="middle" className="ax">{d.day.slice(-2)}</text>}
          </g>
        );
      })}
    </svg>
  );
}

/** A limit keel can read from the provider is not typed by hand; its manual cap stays as the fallback. */
const hasLiveSource = (l: Limit) => !!l.source && l.id !== "claude";

function LimitsDrawer({ limits, onClose, onSaved }: { limits: Limit[]; onClose: () => void; onSaved: (l: Limit[]) => void }) {
  const { toast } = useApp();
  const [rows, setRows] = useState(limits.map((l) => ({ ...l })));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const out = await api.saveLimits(rows);
      onSaved(out ?? rows);
      toast("Limits saved.");
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
      <p className="sub" style={{ margin: 0 }}>Codex and Copilot are read from the provider, and Claude from its runs (see Accounts). Write a cap here only where keel has no number from the provider; "used" then comes from the jobs keel ran.</p>
      {rows.map((l, i) => hasLiveSource(l) ? (
        <div key={l.id} className="field"><span className="lab">{l.name}</span><span className="hint">Read from {l.source}. No cap to type.</span></div>
      ) : (
        <div key={l.id} className="field">
          <label htmlFor={`lim-${l.id}`}>{l.name} <span className="sub" style={{ textTransform: "none", letterSpacing: 0 }}>({l.unit})</span></label>
          <div className="row">
            <input type="text" id={`lim-${l.id}`} inputMode="decimal" value={String(l.cap)} style={{ width: 110, border: "1px solid var(--border)", background: "var(--bg)", borderRadius: 7, padding: "6px 8px" }}
              onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, cap: Number(e.target.value) || 0 } : r)))} />
            <input type="text" aria-label={`${l.name} note`} value={l.note} placeholder="note, e.g. resets Nov 1" style={{ flex: "1 1 160px", border: "1px solid var(--border)", background: "var(--bg)", borderRadius: 7, padding: "6px 8px" }}
              onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, note: e.target.value } : r)))} />
          </div>
        </div>
      ))}
      {!rows.length && <span className="sub">No limits defined.</span>}
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

function CapsPanel({ pid, onAdd, onEdit, caps }: { pid: string; caps: Loaded<Cap[]>; onAdd: () => void; onEdit: (c: Cap) => void }) {
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
  return (
    <Panel title="Caps" extra={<span className="row"><span className="hint">checked before every agent step</span><button className="btn sm" type="button" onClick={onAdd}>Add cap</button></span>} body={false}>
      {caps.error ? <div className="panel-body"><ErrorBox error={caps.error} onRetry={() => void caps.reload()} /></div> : !caps.data ? <div className="empty loading">Loading…</div> : (
        <div className="table-wrap"><table aria-label="Caps">
          <thead><tr><th>Scope</th><th>Limit</th><th>When hit</th><th></th></tr></thead>
          <tbody>
            {caps.data.map((c) => (
              <tr key={c.id}>
                <td>{CAP_SCOPE[c.scope] ?? c.scope}</td><td className="num mono">{capLimit(c)}</td><td className="sub">{CAP_ACTION[c.action] ?? c.action}</td>
                <td><div className="row" style={{ gap: 4, flexWrap: "nowrap", justifyContent: "flex-end" }}>
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
            {!caps.data.length && <tr><td colSpan={4} className="empty">No cap. Flows run until they finish.</td></tr>}
          </tbody>
        </table></div>
      )}
    </Panel>
  );
}

const limitProv = (l: Limit) => (/claude|anthropic/i.test(l.name) ? "claude" : /codex|openai|gpt/i.test(l.name) ? "codex" : /copilot|github/i.test(l.name) ? "copilot" : "claude");

export function BudgetPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const budget = useLoad(`budget:${pid}`, () => api.budget(pid));
  const limits = useLoad("limits", () => api.limits());
  const [editing, setEditing] = useState(false);
  const caps = useLoad(`caps:${pid}`, () => api.caps(pid), { live: false });
  const [capEdit, setCapEdit] = useState<Cap | "new" | null>(null);
  const name = project?.name ?? pid;
  return (
    <>
      <PageHead title="Budget" sub={`What ${name} uses, the caps that stop its flows, and the account limits all projects share.`}
        actions={<>
          <button className="btn" type="button" onClick={() => setEditing(true)} disabled={!limits.data}>Edit limits</button>
          <button className="btn primary" type="button" onClick={() => setCapEdit("new")}>Add cap</button>
        </>} />
      <Async r={budget} what="Loading the budget">
        {(b) => (
          <>
            <Panel title={`${name} this month`} body="kvrow" style={{ marginBottom: 16 }}>
              <div><span className="sub">Tokens</span><b className="big num">{kfmt(b.month.tokens)}</b></div>
              <div><span className="sub">API cost</span><b className="big num">{usd(b.month.cost_usd)}</b></div>
              <div><span className="sub">Flows</span><b className="big num">{b.month.flows}</b></div>
              <div><span className="sub">Copilot premium requests</span><b className="big num">{b.month.premium_requests}</b></div>
            </Panel>
            <span className="lab-s" style={{ display: "block", marginBottom: 8 }}>Accounts · what each plan says is used and what remains</span>
            <div style={{ marginBottom: 12 }}>
              <UsageStrip empty={<div className="empty">No provider is set up yet. Save a login or a key in Connections.</div>} />
            </div>
            <span className="lab-s" style={{ display: "block", marginBottom: 8 }}>Manual caps · where keel cannot read the provider</span>
            {limits.error ? <ErrorBox error={limits.error} /> : !limits.data ? <div className="empty loading">Loading…</div> : !limits.data.length ? <div className="empty">No account limits set.</div> : (
              <div className="grid g4">
                {limits.data.filter((l) => !hasLiveSource(l)).map((l) => {
                  const pc = l.cap ? (l.used / l.cap) * 100 : 0;
                  const cls = pc > 80 ? "bad" : pc > 60 ? "warn" : "ok";
                  const money = /\$|usd|spent/i.test(l.unit);
                  const pct = /%|window/i.test(l.unit);
                  return (
                    <div key={l.id} className="panel"><div className="panel-body grid" style={{ gap: 6 }}>
                      <span className="sub"><span className="prov"><i className={`c-${limitProv(l)}`} />{l.name}</span></span>
                      <span className="big num">{money ? usd(l.used) : pct ? `${Math.round(pc)}%` : l.used}<span className="sub"> {pct ? l.unit : money ? `of ${usd(l.cap)}` : `of ${l.cap} ${l.unit}`}</span></span>
                      <div className={`meter m-${cls}`}><i style={{ width: `${Math.min(100, pc)}%` }} /></div>
                      {l.note && <span className="hint">{l.note}</span>}
                    </div></div>
                  );
                })}
              </div>
            )}
            <div className="grid g2" style={{ marginTop: 16 }}>
              <Panel title="Tokens per day" extra={<div className="legend">{PROVS.filter((p) => b.days.some((d) => d[p])).map((p) => <span key={p}><i className={`c-${p}`} style={{ border: 0, background: p === "fake" ? "var(--faint)" : undefined }} />{PROV[p]}</span>)}</div>}>
                {b.days.length ? <div className="table-wrap"><UsageChart days={b.days} /></div> : <div className="empty">No usage yet.</div>}
                <p className="hint" style={{ margin: "6px 0 0" }}>This month: {kfmt(b.month.tokens)} tokens · API cost {usd(b.month.cost_usd)} · Copilot {b.month.premium_requests} premium requests.</p>
              </Panel>
              <CapsPanel pid={pid} caps={caps} onAdd={() => setCapEdit("new")} onEdit={(c) => setCapEdit(c)} />
            </div>
            <div className="grid g2" style={{ marginTop: 16 }}>
              <Panel title="Estimate vs real, last flows" body={false}>
                <div className="table-wrap"><table>
                  <thead><tr><th>Flow</th><th>Estimate</th><th>Real</th><th>Diff</th><th>Status</th></tr></thead>
                  <tbody>
                    {b.recent.map((r, i) => {
                      const d = r.estimate ? Math.round(((r.real - r.estimate) / r.estimate) * 100) : 0;
                      return (
                        <tr key={i}><td>{r.title}</td><td className="num mono">{kfmt(r.estimate)}</td><td className="num mono">{kfmt(r.real)}</td>
                          <td className="num" style={{ color: `var(--${Math.abs(d) > 30 ? "bad" : Math.abs(d) > 10 ? "warn" : "ok"})` }}>{d > 0 ? "+" : ""}{d}%</td>
                          <td className="sub">{r.status}</td></tr>
                      );
                    })}
                    {!b.recent.length && <tr><td colSpan={5} className="empty">No finished flow yet.</td></tr>}
                  </tbody>
                </table></div>
              </Panel>
              <Panel title="Biggest users this month" body={false}>
                <div className="table-wrap"><table>
                  <thead><tr><th>Agent</th><th>Model</th><th>Tokens</th><th>API cost</th></tr></thead>
                  <tbody>
                    {b.top.map((t, i) => (
                      <tr key={i}><td><b>{t.agent}</b></td><td><Prov p={t.provider} /></td><td className="num mono">{kfmt(t.tokens)}</td>
                        <td className="num">{t.cost_usd ? usd(t.cost_usd) : <span className="sub">subscription</span>}</td></tr>
                    ))}
                    {!b.top.length && <tr><td colSpan={4} className="empty">No agent ran this month.</td></tr>}
                  </tbody>
                </table></div>
              </Panel>
            </div>
          </>
        )}
      </Async>
      <p className="hint" style={{ marginTop: 12 }}>Where the numbers come from: LangChain usage metadata for API calls, the CLI's own usage line for claude / codex / copilot / opencode. Plan windows come from the providers (Claude: its last run; Codex: codex app-server; Copilot: GitHub, unofficial). Manual caps are values you can edit.</p>
      {capEdit && <CapDrawer pid={pid} cap={capEdit === "new" ? null : capEdit} onClose={() => setCapEdit(null)}
        onSaved={(c) => caps.setData((l) => (l ? (l.some((x) => x.id === c.id) ? l.map((x) => (x.id === c.id ? c : x)) : [...l, c]) : [c]))} />}
      {editing && limits.data && <LimitsDrawer limits={limits.data} onClose={() => setEditing(false)} onSaved={(l) => limits.setData(l)} />}
    </>
  );
}
