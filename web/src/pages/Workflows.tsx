// Workflows (Build): the diagram-first builder, the steps table, an inspector for the selected step,
// the estimate, a YAML tab, and the Library (install, import, export). Save → PUT /api/workflows/{id}.

import { useEffect, useMemo, useState } from "react";
import { api, errorParts, type Agent, type Estimate, type OnCap, type Step, type Workflow } from "../api";
import { Builder, useBuilder } from "../components/Builder";
import { clone, moveStep, same, updateStep } from "../components/builderOps";
import { Async, ErrorBox, PageHead, Panel, Tabs } from "../components/ui";
import { KIND, toYaml, tokensByStep } from "../components/workflow";
import { ImportDrawer, LibraryTab, NewWorkflowDrawer } from "../components/WorkflowDrawers";
import { kfmt, PROV, usd } from "../format";
import { go, useApp, useLoad, useRoute } from "../state";

type Tab = "builder" | "yaml" | "library";

export function WorkflowsPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const { arg } = useRoute();
  const list = useLoad(`wfs:${pid}`, () => api.workflows(pid), { live: false });
  const agents = useLoad(`agents:${pid}`, () => api.agents(pid), { live: false });
  const [tab, setTab] = useState<Tab>("builder");
  const [drawer, setDrawer] = useState<"new" | "import" | null>(null);
  const wid = arg ?? list.data?.find((w) => w.id === "feature")?.id ?? list.data?.[0]?.id ?? null;
  const templates = (list.data ?? []).filter((w) => !w.based_on);

  const opened = (w: Workflow) => {
    list.setData((l) => (l ? [...l.filter((x) => x.id !== w.id), w] : [w]));
    setTab("builder");
    go("workflows", w.id);
  };

  const tabs = <Tabs value={tab} onChange={setTab} label="Workflow view" options={[["builder", "Builder"], ["yaml", "YAML"], ["library", "Library"]]} />;

  return (
    <>
      {tab === "library" ? (
        <>
          <PageHead title="Workflows" sub={`Install ready-made workflows into ${project?.name ?? pid}, import your own, or export one to share.`}
            actions={<button className="btn" type="button" id="wfnew" onClick={() => setDrawer("new")}>New workflow</button>} />
          <div className="row" style={{ marginBottom: 12 }}>{tabs}</div>
          <LibraryTab pid={pid} onInstalled={opened} onImported={opened} />
        </>
      ) : (
        <Async r={list} what="Loading workflows">
          {(wfs) => !wfs.length || !wid ? (
            <>
              <PageHead title="Workflows" sub="No workflow yet." actions={<button className="btn primary" type="button" onClick={() => setDrawer("new")}>New workflow</button>} />
              <div className="row" style={{ marginBottom: 12 }}>{tabs}</div>
              <div className="empty">Make one, or install one from the Library.</div>
            </>
          ) : (
            <Editor key={wid} pid={pid} wid={wid} wfs={wfs} agents={agents.data ?? []} tab={tab} tabs={tabs}
              onNew={() => setDrawer("new")} onImport={() => setDrawer("import")}
              onSaved={(w) => list.setData((l) => (l ? l.map((x) => (x.id === w.id ? w : x)) : l))} />
          )}
        </Async>
      )}
      {drawer === "new" && <NewWorkflowDrawer pid={pid} templates={templates.length ? templates : list.data ?? []} onClose={() => setDrawer(null)}
        onCreated={opened} onLibrary={() => setTab("library")} />}
      {drawer === "import" && <ImportDrawer pid={pid} onClose={() => setDrawer(null)} onImported={opened} />}
    </>
  );
}

function Editor({ pid, wid, wfs, agents, tab, tabs, onNew, onImport, onSaved }: {
  pid: string; wid: string; wfs: Workflow[]; agents: Agent[]; tab: Tab; tabs: React.ReactNode;
  onNew: () => void; onImport: () => void; onSaved: (w: Workflow) => void;
}) {
  const { project, toast } = useApp();
  const loaded = useLoad(`wf:${wid}`, () => api.workflow(wid), { live: false });
  const [draft, setDraft] = useState<Workflow | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [acs, setAcs] = useState(3);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const saved = loaded.data;
  useEffect(() => {
    if (saved) {
      setDraft(clone(saved));
      setSel((s) => s ?? saved.steps[0]?.id ?? null);
    }
  }, [saved]);
  const w = draft ?? saved;
  const dirty = !!draft && !!saved && !same(draft, saved);
  const b = useBuilder(w ?? { id: wid, name: "", keel_rules: true, version: 0, steps: [], yaml: "" }, (n) => setDraft(n), setSel);
  const est = useLoad(`wfest:${pid}:${wid}:${acs}:${saved?.version ?? 0}`, () => api.estimate(pid, wid, acs), { live: false });
  const tokens = useMemo(() => (w ? tokensByStep(w, est.data) : undefined), [w, est.data]);
  const customAgents = useMemo(() => new Set(agents.filter((a) => a.custom).map((a) => a.id)), [agents]);

  if (loaded.error) return <ErrorBox error={loaded.error} onRetry={() => void loaded.reload()} />;
  if (!w) return <div className="empty loading">Loading…</div>;
  const step = w.steps.find((s) => s.id === sel) ?? null;

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const out = await api.saveWorkflow({ ...w, yaml: toYaml(w) });
      loaded.setData(out);
      onSaved(out);
      b.clear();
      toast(`Saved as version ${out.version}. New flows use it; running flows keep their version.`);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead title="Workflows" sub={`Workflows ${project?.name ?? pid} can run. keel's flows are templates; keel rules keep the gates and test checks you cannot remove.`}
        actions={<>
          <select id="wfpick" aria-label="Workflow" value={wid} onChange={(e) => go("workflows", e.target.value)}>
            {wfs.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
          <button className="btn" type="button" id="wfnew" onClick={onNew}>New workflow</button>
          <button className="btn" type="button" onClick={onImport}>Import</button>
          <a className="btn" href={api.exportUrl(wid)} download={`${wid}.workflow.yaml`} style={{ textDecoration: "none" }}>Export</a>
          <button className="btn primary" type="button" onClick={save} disabled={busy || !dirty}>{busy ? "Saving…" : dirty ? "Save" : "Saved"}</button>
        </>} />
      <div className="row" style={{ marginBottom: 12, justifyContent: "space-between" }}>
        {tabs}
        <div className="row">
          {dirty && <span className="hint amber">unsaved changes</span>}
          {dirty && <button className="btn sm ghost" type="button" onClick={() => { setDraft(saved ? clone(saved) : null); b.clear(); }}>Discard</button>}
          <label className="chk"><input type="checkbox" id="wrules" checked={w.keel_rules}
            onChange={(e) => {
              setDraft({ ...w, keel_rules: e.target.checked });
              b.clear();
              toast(e.target.checked ? "keel rules on: gates and test checks are locked again." : "keel rules off: every step can be removed. Flows of this workflow are no longer keel-checked.");
            }} /> keel rules {w.keel_rules ? "on" : "off"}</label>
        </div>
      </div>
      {err && <div style={{ marginBottom: 12 }}><ErrorBox error={err} /></div>}
      {tab === "builder" && <Builder w={w} sel={sel} onSelect={setSel} b={b} tokens={tokens} customAgents={customAgents} />}
      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="grid" style={{ alignContent: "start" }}>
          {tab === "builder" ? <StepsTable w={w} sel={sel} onSelect={setSel} tokens={tokens} onMove={(id, d) => setDraft(moveStep(w, id, d))} onRemove={(id) => b.remove(id)} />
            : (
              <Panel title="YAML" extra={<span className="hint">{dirty ? "with your unsaved changes" : `version ${w.version}`}</span>}>
                <pre className="yaml">{dirty || !w.yaml ? toYaml(w) : w.yaml}</pre>
              </Panel>
            )}
        </div>
        <div className="grid" style={{ alignContent: "start" }}>
          {step && <Inspector w={w} s={step} agents={agents} onChange={(patch) => setDraft(updateStep(w, step.id, patch))}
            onMove={(d) => setDraft(moveStep(w, step.id, d))} onRemove={() => b.remove(step.id)} />}
          <EstimatePanel est={est.data} error={est.error} acs={acs} setAcs={setAcs} dirty={dirty} />
        </div>
      </div>
    </>
  );
}

function StepsTable({ w, sel, onSelect, tokens, onMove, onRemove }: {
  w: Workflow; sel: string | null; onSelect: (id: string) => void; tokens?: Record<string, number>;
  onMove: (id: string, d: -1 | 1) => void; onRemove: (id: string) => void;
}) {
  return (
    <Panel title="Steps in order" extra={<span className="hint">same as the diagram</span>} body={false}>
      <div className="table-wrap"><table>
        <thead><tr><th>#</th><th>Step</th><th>Type</th><th>Model</th><th>Tokens</th><th></th></tr></thead>
        <tbody>
          {w.steps.map((s, i) => (
            <tr key={s.id} className={s.id === sel ? "rowsel" : ""}>
              <td className="num sub">{i + 1}</td>
              <td><button type="button" className="linkbtn" onClick={() => onSelect(s.id)}>{s.name}</button>{s.per_ac && <> <span className="tag">each AC</span></>}</td>
              <td className="sub">{KIND[s.kind]}</td>
              <td>{s.model ? <span className="mono sub">{s.model}</span> : <span className="sub">—</span>}</td>
              <td className="num mono">{tokens?.[s.id] ? kfmt(tokens[s.id]) : "0"}</td>
              <td><div className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
                <button className="btn sm ghost" type="button" aria-label={`Move ${s.name} earlier`} disabled={i === 0} onClick={() => onMove(s.id, -1)}>↑</button>
                <button className="btn sm ghost" type="button" aria-label={`Move ${s.name} later`} disabled={i === w.steps.length - 1} onClick={() => onMove(s.id, 1)}>↓</button>
                <button className="btn sm ghost" type="button" aria-label={`Remove ${s.name}`} onClick={() => onRemove(s.id)}>{s.lock && w.keel_rules ? "locked" : "×"}</button>
              </div></td>
            </tr>
          ))}
          {!w.steps.length && <tr><td colSpan={6} className="empty">No steps. Use the + in the diagram.</td></tr>}
        </tbody>
      </table></div>
    </Panel>
  );
}

const ON_LIMIT: [OnCap, string][] = [["pause", "pause and ask me"], ["cheaper", "switch to a cheaper model"], ["stop", "stop the step"]];

function Inspector({ w, s, agents, onChange, onMove, onRemove }: {
  w: Workflow; s: Step; agents: Agent[]; onChange: (p: Partial<Step>) => void; onMove: (d: -1 | 1) => void; onRemove: () => void;
}) {
  const i = w.steps.indexOf(s);
  const prev = w.steps[i - 1], next = w.steps[i + 1];
  const locked = !!s.lock && w.keel_rules;
  const llm = s.kind === "agent" || s.kind === "parallel";
  const models = [...new Set(["default", ...w.steps.map((x) => x.model).filter(Boolean) as string[], ...agents.map((a) => a.model?.model).filter(Boolean)])];
  return (
    <Panel title="Step" extra={<button className="btn sm ghost" type="button" onClick={onRemove}>{locked ? "keel rule · why?" : "Remove step"}</button>} body="grid">
      <div className="grid" style={{ gap: 12 }}>
        <div className="field"><label htmlFor="wn">Name</label><input type="text" id="wn" value={s.name} onChange={(e) => onChange({ name: e.target.value })} /></div>
        <div className="field"><span className="lab">Type</span><span>{KIND[s.kind]}{s.phase ? <> · phase <span className="mono">{s.phase}</span></> : null}</span></div>
        <div className="field">
          <span className="lab">Connections</span>
          <div className="conn-mini"><span className="sub">comes after</span><b>{prev ? prev.name : "start"}</b><span className="sub">goes to</span><b>{next ? next.name : "done"}</b></div>
          {s.kind === "gate" && <>
            <label className="sub" htmlFor="wback">If you send it back, go to</label>
            <select id="wback" value={s.back ?? ""} onChange={(e) => onChange({ back: e.target.value || undefined })}>
              <option value="">— none —</option>
              {w.steps.filter((_x, j) => j < i).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
          </>}
          {s.kind === "branch" && <>
            <label className="sub" htmlFor="wno">If the answer is no, go to</label>
            <select id="wno" value={s.no ?? ""} onChange={(e) => onChange({ no: e.target.value || undefined })}>
              <option value="">— none —</option>
              {w.steps.filter((x) => x.id !== s.id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
            <span className="hint">If yes, it goes on to “{next ? next.name : "done"}”.</span>
          </>}
          <div className="row">
            <button className="btn sm" type="button" disabled={i === 0} onClick={() => onMove(-1)}>Move earlier</button>
            <button className="btn sm" type="button" disabled={i === w.steps.length - 1} onClick={() => onMove(1)}>Move later</button>
          </div>
        </div>
        {llm ? (
          <>
            <div className="field"><label htmlFor="wa">Agent</label>
              <select id="wa" value={s.agent ?? ""} onChange={(e) => onChange({ agent: e.target.value || undefined })}>
                {!agents.some((a) => a.id === s.agent) && s.agent && <option value={s.agent}>{s.agent}</option>}
                {agents.map((a) => <option key={a.id} value={a.id}>{a.custom ? "★ " : ""}{a.label || a.id}</option>)}
              </select>
            </div>
            <div className="field"><label htmlFor="wm">Model</label>
              <input type="text" id="wm" list="wm-list" value={s.model ?? ""} onChange={(e) => onChange({ model: e.target.value || undefined })} placeholder="default" />
              <datalist id="wm-list">{models.map((m) => <option key={m} value={m} />)}</datalist>
              <span className="hint">"default" uses the agent's model from Agents / Settings.</span>
            </div>
            {s.kind === "parallel" && (
              <div className="field"><label htmlFor="wpar">Copies at the same time</label>
                <input type="text" id="wpar" inputMode="numeric" value={s.parallel ?? 2} onChange={(e) => onChange({ parallel: Math.max(2, Number(e.target.value) || 2) })} style={{ width: 80 }} /></div>
            )}
            <div className="field"><label htmlFor="wcap">Max tokens for this step (per run)</label>
              <div className="row">
                <input type="text" id="wcap" inputMode="numeric" value={s.max_tokens ?? ""} placeholder="no limit" style={{ width: 110 }}
                  onChange={(e) => onChange({ max_tokens: Number(e.target.value.replace(/\D/g, "")) || undefined })} />
                <select aria-label="When the limit is hit" value={s.on_limit ?? "pause"} onChange={(e) => onChange({ on_limit: e.target.value as OnCap })}>
                  {ON_LIMIT.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </div>
            </div>
          </>
        ) : s.kind === "code" ? (
          <div className="field"><label htmlFor="wact">What it runs</label>
            <input type="text" id="wact" value={s.action ?? ""} onChange={(e) => onChange({ action: e.target.value || undefined })} placeholder="verify_red | verify_green | commit | run:<cmd>" />
            <span className="hint">Runs plain code. Costs no tokens.</span>
          </div>
        ) : (
          <p className="hint" style={{ margin: 0 }}>{s.kind === "gate" ? "Waits for you (LangGraph interrupt). Costs no tokens." : "Decides yes or no with plain code. Costs no tokens."}</p>
        )}
        <label className="chk"><input type="checkbox" id="wpa" checked={!!s.per_ac} disabled={locked} onChange={(e) => onChange({ per_ac: e.target.checked || undefined })} /> run once per acceptance criterion</label>
      </div>
    </Panel>
  );
}

const PCOL: Record<string, string> = { claude: "#e2725b", codex: "#3fa37a", copilot: "#6e7bff", fake: "var(--faint)" };

function EstimatePanel({ est, error, acs, setAcs, dirty }: { est: Estimate | null; error: { message: string; hint?: string } | null; acs: number; setAcs: (n: number) => void; dirty: boolean }) {
  const by = Object.entries(est?.by_provider ?? {}).filter(([, v]) => v) as [string, number][];
  const tot = by.reduce((a, [, v]) => a + v, 0) || 1;
  return (
    <Panel title="Estimate for this workflow" extra={<span className="hint">from this project's past runs</span>} body="grid">
      <div className="grid" style={{ gap: 12 }}>
        <div className="field"><label htmlFor="acn">Acceptance criteria in the spec</label>
          <div className="row"><input type="range" id="acn" min={1} max={10} value={acs} onChange={(e) => setAcs(Number(e.target.value))} style={{ flex: "1 1 140px" }} /><b className="num">{acs}</b></div></div>
        {error ? <ErrorBox error={error} /> : !est ? <span className="sub loading">Estimating…</span> : (
          <>
            <div className="est-big"><span className="num">{kfmt(est.tokens)}</span><span className="sub">tokens expected · range {kfmt(est.low)} – {kfmt(est.high)}</span></div>
            <div className="stack" aria-label="tokens by provider">{by.map(([p, v]) => <i key={p} style={{ width: `${((v / tot) * 100).toFixed(1)}%`, background: PCOL[p] }} />)}</div>
            <div className="legend">{by.map(([p, v]) => <span key={p}><i style={{ border: 0, background: PCOL[p] }} />{PROV[p] ?? p} {kfmt(v)}</span>)}</div>
            <div className="kv">
              <span>If all on API keys</span><b className="num">{usd(est.cost_usd)}</b>
              <span>Copilot premium requests</span><b className="num">≈ {est.premium_requests}</b>
            </div>
          </>
        )}
        <p className="hint" style={{ margin: 0 }}>
          Per agent step: (input + output tokens, median of past runs) × runs per AC × (1 + retry rate). The range uses the 10th and 90th percentile.
          {dirty ? " Save to estimate your changes." : ""}
        </p>
      </div>
    </Panel>
  );
}
