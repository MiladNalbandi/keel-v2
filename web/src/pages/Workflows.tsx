// Workflows (Build): a workspace in three columns. Left: the workflow list (keel's templates and the project's own,
// search, New / Import / Library). Centre: the workflow as Scratch-style blocks with a palette (drag a block in, or
// click +), or as a table, the graph or YAML. Right: the selected block's editor, "What it does" and the token
// estimate. On top: the name, version, keel rules, the save state and the save errors (with links to the steps).
// Under 1100 px the columns stack and the block editor opens in a drawer. Save → PUT /api/workflows/{id}.

import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, errorParts, type Agent, type Estimate, type OnCap, type Step, type StepKind, type Workflow } from "../api";
import { Blocks, BlocksLegend, Palette, StepTable, type BlocksHandle } from "../components/Blocks";
import { useBuilder, type BuilderApi } from "../components/Builder";
import { clone, same, updateStep } from "../components/builderOps";
import { buildFlowMap, exitsOf, PHASES, phaseMeaning, phaseTitle, sumOf } from "../components/flowmap";
import { Graph, GraphLegend } from "../components/Graph";
import { infoRequest, StepInfoBody, useStepInfo } from "../components/StepInfo";
import { Async, Drawer, ErrorBox, Loading, PageHead, Tabs } from "../components/ui";
import { useWide } from "../components/useWide";
import { KIND, toYaml, tokensByStep } from "../components/workflow";
import { ImportDrawer, LibraryTab, NewWorkflowDrawer } from "../components/WorkflowDrawers";
import { Zoom } from "../components/Zoom";
import { kfmt, PROV, usd } from "../format";
import { go, useApp, useLoad, useRoute } from "../state";

type View = "blocks" | "table" | "graph" | "yaml";
type Side = "block" | "explain" | "estimate";

const isTemplate = (w: Workflow) => w.source === "keel" || (!w.source && !w.based_on);

/** The three columns; on wide screens they fill the window and scroll by themselves. */
function Wf({ children, cls = "" }: { children: ReactNode; cls?: string }) {
  const ref = useFitHeight<HTMLDivElement>();
  return <div ref={ref} className={`wf ${cls}`}>{children}</div>;
}

export function WorkflowsPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const { arg } = useRoute();
  const list = useLoad(`wfs:${pid}`, () => api.workflows(pid), { live: false });
  const agents = useLoad(`agents:${pid}`, () => api.agents(pid), { live: false });
  const [library, setLibrary] = useState(false);
  const [drawer, setDrawer] = useState<"new" | "import" | null>(null);
  const wid = arg ?? list.data?.find((w) => w.id === "feature")?.id ?? list.data?.[0]?.id ?? null;
  const templates = (list.data ?? []).filter((w) => !w.based_on);

  const opened = (w: Workflow) => {
    list.setData((l) => (l ? [...l.filter((x) => x.id !== w.id), w] : [w]));
    setLibrary(false);
    go("workflows", w.id);
  };
  const rail = (
    <Rail wfs={list.data ?? []} current={library ? null : wid} library={library} pid={pid}
      onPick={(id) => { setLibrary(false); go("workflows", id); }} onNew={() => setDrawer("new")} onImport={() => setDrawer("import")}
      onLibrary={() => setLibrary(true)} />
  );

  return (
    <>
      <PageHead title="Workflows" sub={`What ${project?.name ?? pid} can run, as blocks. keel rules keep the gates and test checks locked.`} />
      {library ? (
        <Wf cls="wf-lib">
          {rail}
          <div className="wf-main wf-scroll"><LibraryTab pid={pid} onInstalled={opened} onImported={opened} /></div>
        </Wf>
      ) : (
        <Async r={list} what="Loading workflows">
          {(wfs) => !wfs.length || !wid ? (
            <Wf cls="wf-lib">
              {rail}
              <div className="wf-main"><div className="panel"><div className="panel-body sx-empty">
                <b>No workflow yet.</b>
                <span className="sub">Make one from a template, or install one from the Library.</span>
                <span className="row"><button className="btn primary" type="button" onClick={() => setDrawer("new")}>New workflow</button>
                  <button className="btn" type="button" onClick={() => setLibrary(true)}>Open the Library</button></span>
              </div></div></div>
            </Wf>
          ) : (
            <Editor key={wid} pid={pid} wid={wid} agents={agents.data ?? []} rail={rail}
              onSaved={(w) => list.setData((l) => (l ? l.map((x) => (x.id === w.id ? w : x)) : l))} />
          )}
        </Async>
      )}
      {drawer === "new" && <NewWorkflowDrawer pid={pid} templates={templates.length ? templates : list.data ?? []} onClose={() => setDrawer(null)}
        onCreated={opened} onLibrary={() => { setDrawer(null); setLibrary(true); }} />}
      {drawer === "import" && <ImportDrawer pid={pid} onClose={() => setDrawer(null)} onImported={opened} />}
    </>
  );
}

/** The workflow list: keel's templates and this project's own, with a search, New, Import and the Library. */
function Rail({ wfs, current, library, pid, onPick, onNew, onImport, onLibrary }: {
  wfs: Workflow[]; current: string | null; library: boolean; pid: string;
  onPick: (id: string) => void; onNew: () => void; onImport: () => void; onLibrary: () => void;
}) {
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const hit = (w: Workflow) => !needle || w.name.toLowerCase().includes(needle) || w.id.toLowerCase().includes(needle);
  const groups: [string, Workflow[]][] = [
    ["This project", wfs.filter((w) => !isTemplate(w) && hit(w))],
    ["keel templates", wfs.filter((w) => isTemplate(w) && hit(w))],
  ];
  return (
    <nav className="wf-rail" aria-label="Workflows">
      <div className="wf-rail-actions">
        <button className="btn sm primary" type="button" id="wfnew" onClick={onNew}>New workflow</button>
        <button className="btn sm" type="button" onClick={onImport}>Import</button>
        <button className={`btn sm ${library ? "is-on" : ""}`} type="button" aria-pressed={library} onClick={onLibrary}>Library</button>
      </div>
      {/* Under 1360 px the list is a menu. */}
      <select className="wf-pick" id="wfpick" aria-label="Workflow" value={current ?? ""} onChange={(e) => onPick(e.target.value)}>
        {current === null && <option value="">Library</option>}
        {wfs.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
      </select>
      <input type="search" className="inline-input wf-search" placeholder="Find a workflow" aria-label="Find a workflow" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="wf-groups">
        {groups.map(([title, items]) => (
          <div key={title} className="wf-group">
            <div className="wf-group-h">{title}</div>
            {items.length ? items.map((w) => (
              <button key={w.id} type="button" className="wf-item" aria-current={w.id === current ? "page" : undefined} onClick={() => onPick(w.id)}>
                <span className="wf-item-n">{w.name}</span>
                <span className="wf-item-m">{w.steps.length} steps · v{w.version}{w.keel_rules ? "" : " · rules off"}</span>
              </button>
            )) : <span className="sub wf-none">{needle ? "no match" : title === "This project" ? `none yet in ${pid}: start from a template` : "none"}</span>}
          </div>
        ))}
      </div>
    </nav>
  );
}

function Editor({ pid, wid, agents, rail, onSaved }: {
  pid: string; wid: string; agents: Agent[]; rail: ReactNode; onSaved: (w: Workflow) => void;
}) {
  const { toast } = useApp();
  const loaded = useLoad(`wf:${wid}`, () => api.workflow(wid), { live: false });
  const [draft, setDraft] = useState<Workflow | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [acs, setAcs] = useState(3);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string; details?: string[] } | null>(null);
  const [view, setView] = useState<View>(() => {
    try { const v = localStorage.getItem("keel2.view.builder"); return v === "table" || v === "graph" || v === "yaml" ? v : "blocks"; } catch { return "blocks"; }
  });
  const [side, setSide] = useState<Side>("estimate");
  // YAML typed by hand in the YAML tab. null = not edited (the tab shows the YAML of the blocks).
  const [yamlText, setYamlText] = useState<string | null>(null);
  const wide = useWide();
  const canvas = useRef<BlocksHandle>(null);
  const saved = loaded.data;
  useEffect(() => {
    if (saved) {
      setDraft(clone(saved));
      setYamlText(null);
    }
  }, [saved]);
  const w = draft ?? saved;
  const stepsDirty = !!draft && !!saved && !same(draft, saved);
  const dirty = stepsDirty || yamlText !== null;
  const currentYaml = w ? (yamlText ?? (stepsDirty || !w.yaml ? toYaml(w) : w.yaml)) : "";
  const select = (id: string | null) => { setSel(id); if (id) setSide((s) => (s === "estimate" ? "block" : s)); };
  const b = useBuilder(w ?? { id: wid, name: "", keel_rules: true, version: 0, steps: [], yaml: "" }, (n) => setDraft(n), select);
  const est = useLoad(`wfest:${pid}:${wid}:${acs}:${saved?.version ?? 0}`, () => api.estimate(pid, wid, acs), { live: false });
  const draftEst = useDraftEstimate(pid, dirty ? currentYaml : null, acs);
  const shownEst = dirty ? draftEst.est : est.data;
  const tokens = useMemo(() => (w ? tokensByStep(w, shownEst) : undefined), [w, shownEst]);
  const customAgents = useMemo(() => new Set(agents.filter((a) => a.custom).map((a) => a.id)), [agents]);
  const setViewKept = (v: View) => { setView(v); try { localStorage.setItem("keel2.view.builder", v); } catch { /* not kept */ } };

  if (loaded.error) return <Wf>{rail}<div className="wf-main"><ErrorBox error={loaded.error} onRetry={() => void loaded.reload()} /></div></Wf>;
  if (!w) return <Wf>{rail}<div className="wf-main"><Loading what="Loading the workflow" /></div></Wf>;
  const step = w.steps.find((s) => s.id === sel) ?? null;

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      // A hand-edited YAML is the source of truth: the api validates it with the engine and sends back the parsed steps.
      const out = await api.saveWorkflow({ ...w, yaml: yamlText ?? toYaml(w) });
      setYamlText(null);
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
  const discard = () => { setDraft(saved ? clone(saved) : null); setYamlText(null); setErr(null); b.clear(); };
  const pick = (id: string) => {
    select(id);
    if (view !== "blocks" && view !== "table") setViewKept("blocks");
    window.setTimeout(() => canvas.current?.reveal(id, false), 0);
  };
  const addFromPalette = (kind: StepKind | "loop") => {
    const at = sel ? w.steps.findIndex((s) => s.id === sel) : w.steps.length - 1;
    if (kind === "loop") b.addAt(at, "agent", "ac");
    else b.addAt(at, kind);
  };
  const inspector = step && (
    <Inspector w={w} s={step} agents={agents} onChange={(patch) => setDraft(updateStep(w, step.id, patch))}
      onMove={(d) => b.move(step.id, d)} onRemove={() => b.remove(step.id)} onPick={pick} onClose={wide ? () => setSel(null) : undefined} />
  );
  const estimate = (
    <EstimatePanel est={shownEst} error={dirty ? draftEst.error : est.error} acs={acs} setAcs={setAcs} dirty={dirty} w={w} tokens={tokens} />
  );

  return (
    <Wf>
      {rail}
      <div className="wf-main">
        <header className={`wf-top ${dirty ? "is-dirty" : ""}`} role="region" aria-label="This workflow">
          <div className="wf-top-row">
            <div className="wf-title">
              <h2>{w.name}</h2>
              <span className="wf-sub">
                {isTemplate(w) ? <span className="tag keel">keel template</span> : w.based_on ? <span className="tag">from {w.based_on}</span> : null}
                <span>v{w.version}</span><span>{w.steps.length} steps</span>
                {shownEst ? <span title={`estimated tokens for the whole workflow, ${acs} ${acs === 1 ? "criterion" : "criteria"}`}>≈ {kfmt(shownEst.tokens)} tokens</span> : null}
              </span>
            </div>
            <div className="wf-save">
              <label className="chk wf-rules"><input type="checkbox" id="wrules" checked={w.keel_rules}
                onChange={(e) => {
                  setDraft({ ...w, keel_rules: e.target.checked });
                  b.clear();
                  toast(e.target.checked ? "keel rules on: gates and test checks are locked again." : "keel rules off: every step can be removed. Flows of this workflow are no longer keel-checked.");
                }} /> keel rules {w.keel_rules ? "on" : "off"}</label>
              {dirty ? <span className="hint amber wf-dirty">unsaved changes</span> : <span className="hint">saved</span>}
              {dirty && <button className="btn sm ghost" type="button" onClick={discard}>Discard</button>}
              <button className="btn primary" type="button" onClick={save} disabled={busy || !dirty}>{busy ? "Saving…" : dirty ? "Save" : "Saved"}</button>
            </div>
          </div>
          <div className="wf-top-row">
            <Tabs value={view} onChange={setViewKept} label="Workflow view" options={[["blocks", "Blocks"], ["table", "Table"], ["graph", "Graph"], ["yaml", "YAML"]]} />
            <a className="btn sm" href={api.exportUrl(wid)} download={`${wid}.workflow.yaml`} style={{ textDecoration: "none" }}>Export</a>
          </div>
          {err && view !== "yaml" && <StepErrorBox error={err} steps={w.steps} onPick={pick} />}
          {view !== "yaml" && yamlText !== null && <p className="hint amber" style={{ margin: 0 }}>You changed the YAML by hand. Save to see those changes in the blocks.</p>}
          <RemovedNotice b={b} />
        </header>

        <div className="wf-scroll sx-scroll">
          {view === "yaml" ? (
            <section className="panel wf-yaml" aria-label="YAML">
              <div className="panel-body grid" style={{ gap: 8 }}>
                <label htmlFor="wyaml" className="sub">Edit the workflow as text. Save checks it with the engine; problems show below. {dirty ? "(with your unsaved changes)" : `(version ${w.version})`}</label>
                <textarea id="wyaml" className="yaml-edit" spellCheck={false} value={currentYaml}
                  aria-invalid={err?.details?.length ? true : undefined} aria-describedby={err ? "wyaml-err" : undefined}
                  onChange={(e) => setYamlText(e.target.value)} />
                {err && <div id="wyaml-err"><ErrorBox error={err} /></div>}
                {yamlText !== null && <span className="hint">The blocks show your YAML after you save.</span>}
              </div>
            </section>
          ) : view === "graph" ? (
            <section className="panel" aria-label="Graph">
              <div className="panel-body grid" style={{ gap: 10 }}>
                {b.st.insertAt !== null && (
                  <div className="wbar" role="group" aria-label="Insert a step">
                    <span>Insert after <b>{w.steps[b.st.insertAt]?.name ?? "start"}</b>:</span>
                    {(["agent", "code", "gate", "branch", "parallel"] as StepKind[]).map((k) => (
                      <button key={k} className="btn sm" type="button" onClick={() => b.add(k)}>{KIND[k].replace("◆ ", "")}</button>
                    ))}
                    <button className="btn sm ghost" type="button" onClick={b.cancel}>Cancel</button>
                  </div>
                )}
                <LockQuestion w={w} b={b} />
                <Zoom id="builder">
                  <Graph steps={w.steps} edit per={6} selected={sel} insertAt={b.st.insertAt} keel={w.keel_rules} tokens={tokens} customAgents={customAgents}
                    onSelect={(id) => { select(id); b.closeInsert(); }} onInsert={b.insertAt} onRemove={(id) => b.remove(id)} />
                </Zoom>
                <GraphLegend />
              </div>
            </section>
          ) : view === "table" ? (
            <section className="panel" aria-label="Table">
              <StepTable steps={w.steps} tokens={tokens} selected={sel} onOpenStep={select} acCount={acs} />
            </section>
          ) : (
            <section className="wf-canvas" aria-label="Blocks">
              <Palette onAdd={addFromPalette} where={step ? `after ${step.name}` : "at the end"} />
              <Blocks ref={canvas} steps={w.steps} tokens={tokens} acCount={acs} customAgents={customAgents} keel={w.keel_rules} selected={sel}
                onOpenStep={(id) => { select(id); b.closeInsert(); }}
                edit={{
                  insertAt: b.st.insertAt, insertLoop: b.st.insertLoop, onInsert: b.insertAt, onAdd: b.add, onCancelInsert: b.closeInsert,
                  onAddAt: b.addAt, onRemove: (id) => b.remove(id), onMove: b.move, onMoveTo: b.moveTo,
                  notice: b.st.lockAsk ? { id: b.st.lockAsk, node: <LockQuestion w={w} b={b} /> } : null,
                }}
                label={`Blocks of ${w.name}: ${w.steps.length} steps`} />
              <BlocksLegend />
            </section>
          )}
          {!wide && <div className="wf-below">{estimate}</div>}
        </div>
      </div>

      {wide ? (
        <aside className="wf-panel" aria-label="Block, what it does and estimate">
          <Tabs value={side} onChange={setSide} label="Side panel" options={[["block", "Block"], ["explain", "What it does"], ["estimate", "Estimate"]]} />
          <div className="wf-panel-body">
            {side === "estimate" ? estimate
              : !step ? (
                <div className="panel"><div className="panel-body wb-hint">
                  <b>Select a block to {side === "block" ? "edit it" : "see what it does"}.</b>
                  <span className="sub">Click a block, or use ↑ and ↓ to move between blocks and Enter to open one. Alt + ↑ / ↓ moves a block; Delete removes it.</span>
                </div></div>
              ) : side === "block" ? inspector : <ExplainPanel pid={pid} w={w} id={step.id} />}
          </div>
        </aside>
      ) : step ? (
        <Drawer title={`Block: ${step.name}`} onClose={() => setSel(null)}>
          <Tabs value={side === "explain" ? "explain" : "block"} onChange={(v) => setSide(v)} label="Block" options={[["block", "Edit"], ["explain", "What it does"]]} />
          {side === "explain" ? <ExplainPanel pid={pid} w={w} id={step.id} /> : inspector}
        </Drawer>
      ) : null}
    </Wf>
  );
}

/** "What it does" for the selected block, of the draft (unsaved edits show too). */
function ExplainPanel({ pid, w, id }: { pid: string; w: Workflow; id: string }) {
  const { x, error } = useStepInfo(pid, infoRequest(id, w));
  return (
    <section className="panel" aria-label="What it does">
      <div className="panel-body">{error ? <ErrorBox error={error} /> : !x ? <Loading what="Reading the step" /> : <StepInfoBody x={x} />}</div>
    </section>
  );
}

function RemovedNotice({ b }: { b: BuilderApi }) {
  if (!b.st.removed) return null;
  return (
    <div className="wbar ok" role="status">
      <span>Removed <b>{b.st.removed.step.name}</b>. Arrows were reconnected.</span>
      <button className="btn sm" type="button" onClick={b.undo}>Undo</button>
    </div>
  );
}

function LockQuestion({ w, b }: { w: Workflow; b: BuilderApi }) {
  const lockStep = b.st.lockAsk ? w.steps.find((x) => x.id === b.st.lockAsk) : null;
  if (!lockStep) return null;
  return (
    <div className="wbar warn" role="alert">
      <span><b>{lockStep.name}</b> is a keel rule: {lockStep.kind === "gate" ? "a person must approve here" : "it proves the tests are real"}. To remove it, turn keel rules off for this workflow.</span>
      <button className="btn sm warn" type="button" onClick={b.rulesOffAndRemove}>Turn rules off and remove</button>
      <button className="btn sm ghost" type="button" onClick={b.cancel}>Keep it</button>
    </div>
  );
}

/** Keeps the columns to the window's height (they scroll by themselves on wide screens). */
export function useFitHeight<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = () => el.style.setProperty("--wf-top", `${Math.max(0, Math.round(el.getBoundingClientRect().top + window.scrollY))}px`);
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);
  return ref;
}

/** Split a message into text and the steps it names ('id', "name", or a list after a colon), as buttons. */
export function linkSteps(text: string, steps: Step[]): (string | { id: string; text: string })[] {
  const byKey = new Map<string, Step>();
  steps.forEach((s) => byKey.set(s.id, s));
  steps.forEach((s) => { if (!byKey.has(s.name)) byKey.set(s.name, s); });
  const out: (string | { id: string; text: string })[] = [];
  let last = 0;
  for (const m of text.matchAll(/(['"`“‘])([^'"`”’\n]{1,120})(['"`”’])/g)) {
    const hit = byKey.get(m[2]);
    if (!hit) continue;
    const at = (m.index ?? 0) + m[1].length;
    if (at > last) out.push(text.slice(last, at));
    out.push({ id: hit.id, text: m[2] });
    last = at + m[2].length;
  }
  if (!out.length) {
    // "Locked steps cannot be removed while keel rules are on: spec approval, AC gate"
    const colon = text.lastIndexOf(": ");
    const items = colon > 0 ? text.slice(colon + 2).replace(/\.$/, "").split(/,\s*/) : [];
    if (items.length && items.every((x) => byKey.has(x.trim()))) {
      out.push(text.slice(0, colon + 2));
      items.forEach((x, k) => { if (k) out.push(", "); out.push({ id: byKey.get(x.trim())!.id, text: x.trim() }); });
      return out;
    }
    return [text];
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** The message with each step it names as a link (by its name, its id in the tooltip) that selects the block. */
function Linked({ text, steps, onPick }: { text: string; steps: Step[]; onPick: (id: string) => void }) {
  return <>{linkSteps(text, steps).map((p, k) => {
    if (typeof p === "string") return p;
    const name = steps.find((s) => s.id === p.id)?.name ?? p.text;
    return <button key={k} type="button" className="linkbtn steplink" onClick={() => onPick(p.id)} title={p.id} aria-label={`Show step ${name}`}>{name}</button>;
  })}</>;
}

/** A save error on top of the builder; every step it names is a link that selects that block. */
function StepErrorBox({ error, steps, onPick }: { error: { message: string; hint?: string; details?: string[] }; steps: Step[]; onPick: (id: string) => void }) {
  return (
    <div className="errbox" role="alert">
      <b><Linked text={error.message} steps={steps} onPick={onPick} /></b>
      {error.hint && <span className="sub"><Linked text={error.hint} steps={steps} onPick={onPick} /></span>}
      {error.details?.length ? <ul className="errlist">{error.details.map((d, i) => <li key={i}><Linked text={d} steps={steps} onPick={onPick} /></li>)}</ul> : null}
    </div>
  );
}

/** Estimate for YAML that is not saved yet: POST /api/projects/{pid}/estimate, 600 ms after the last change. */
function useDraftEstimate(pid: string, yaml: string | null, acs: number) {
  const [est, setEst] = useState<Estimate | null>(null);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  useEffect(() => {
    if (yaml === null) {
      setEst(null);
      setError(null);
      return;
    }
    let gone = false;
    const t = window.setTimeout(() => {
      api.estimateYaml(pid, yaml, acs).then(
        (e) => { if (!gone) { setEst(e); setError(null); } },
        (e) => { if (!gone) { setEst(null); setError(errorParts(e)); } },
      );
    }, 600);
    return () => {
      gone = true;
      window.clearTimeout(t);
    };
  }, [pid, yaml, acs]);
  return { est, error };
}

const ON_LIMIT: [OnCap, string][] = [["pause", "pause and ask me"], ["cheaper", "switch to a cheaper model"], ["stop", "stop the step"]];

/** The selected block's editor: every field the blocks show, its connections by name, and where to move it. */
function Inspector({ w, s, agents, onChange, onMove, onRemove, onPick, onClose }: {
  w: Workflow; s: Step; agents: Agent[]; onChange: (p: Partial<Step>) => void; onMove: (d: -1 | 1) => void; onRemove: () => void;
  onPick?: (id: string) => void; onClose?: () => void;
}) {
  const i = w.steps.indexOf(s);
  const prev = w.steps[i - 1], next = w.steps[i + 1];
  const locked = !!s.lock && w.keel_rules;
  const llm = s.kind === "agent" || s.kind === "parallel";
  const models = [...new Set(["default", ...w.steps.map((x) => x.model).filter(Boolean) as string[], ...agents.map((a) => a.model?.model).filter(Boolean)])];
  const exits = exitsOf(w.steps, i, new Map(w.steps.map((x, j) => [x.id, j])));
  const phase = s.phase ?? [...w.steps.slice(0, i)].reverse().find((x) => x.phase)?.phase;
  const step = (id: string | null | undefined, name: string) => id && id !== "end" && onPick
    ? <button type="button" className="linkbtn steplink" onClick={() => onPick(id)}>{name}</button> : <b>{name}</b>;
  return (
    <section className="panel insp" aria-label="Step">
      <div className="panel-head">
        <h2>Step</h2>
        <span className="row" style={{ gap: 4 }}>
          <button className="btn sm ghost" type="button" onClick={onRemove}>{locked ? "keel rule · why?" : "Remove"}</button>
          {onClose && <button className="btn sm ghost" type="button" onClick={onClose} aria-label="Close the step">Close</button>}
        </span>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
        <div className="field"><label htmlFor="wn">Name</label><input type="text" id="wn" value={s.name} onChange={(e) => onChange({ name: e.target.value })} /></div>
        <div className="field"><span className="lab">Kind</span>
          <span className={`sx-kind k-${s.kind} insp-kind`}>{KIND[s.kind].replace("◆ ", "")}{s.included_from ? <span className="sub"> · from the {s.included_from.split("/")[0]} flow</span> : null}{locked ? <span className="sub"> · 🔒 keel rule</span> : null}</span>
        </div>
        <div className="field"><label htmlFor="wph">Phase</label>
          <select id="wph" value={s.phase ?? ""} onChange={(e) => onChange({ phase: e.target.value || undefined })}>
            <option value="">— as the step before{phase && !s.phase ? ` (${phaseTitle(phase)})` : ""} —</option>
            {[...new Set([...Object.keys(PHASES), ...(s.phase ? [s.phase] : [])])].filter((p) => p !== "none").map((p) => <option key={p} value={p}>{phaseTitle(p)} · {p}</option>)}
          </select>
          {phase && <span className="hint">{phaseMeaning(phase) || phase}</span>}
        </div>
        <div className="field">
          <span className="lab">Connections</span>
          <div className="conn-mini">
            <span className="sub">comes after</span>{prev ? step(prev.id, prev.name) : <b>start</b>}
            {!(s.kind === "gate" && s.choices && !Array.isArray(s.choices)) && <>
              <span className="sub">{s.kind === "branch" ? "if yes" : "goes to"}</span>{s.then === "end" ? <b>end of the flow</b> : s.then && s.then !== "continue" ? step(s.then, w.steps.find((x) => x.id === s.then)?.name ?? s.then) : next ? step(next.id, next.name) : <b>done</b>}
            </>}
            {exits.filter((e) => e.kind !== "then" && e.kind !== "yes").map((e, k) => (
              <Fragment key={k}><span className="sub">{e.label}</span>{e.to ? step(e.to, `${e.back ? "↩ " : ""}${e.toName}`) : <b>{e.toName}</b>}</Fragment>
            ))}
          </div>
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
          {s.kind === "code" && (
            <>
              <label className="sub" htmlFor="wthen">Then</label>
              <select id="wthen" value={s.then ?? ""} onChange={(e) => onChange({ then: e.target.value || undefined })}>
                <option value="">go on to the next block</option>
                <option value="end">end the flow</option>
                {w.steps.filter((x) => x.id !== s.id).map((x) => <option key={x.id} value={x.id}>jump to {x.name}</option>)}
              </select>
            </>
          )}
          <div className="row">
            <button className="btn sm" type="button" disabled={i === 0} onClick={() => onMove(-1)}>Move earlier</button>
            <button className="btn sm" type="button" disabled={i === w.steps.length - 1} onClick={() => onMove(1)}>Move later</button>
            <span className="hint">or drag it, or Alt + ↑ / ↓</span>
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
            {s.lanes?.length ? (
              <div className="field"><span className="lab">Lanes</span>
                <ul className="errlist">{s.lanes.map((l, k) => <li key={k}><b>{l.name}</b> <span className="sub">{l.kind}{l.sub ? ` · ${l.sub}` : ""}</span></li>)}</ul>
                <span className="hint">Change lanes in the YAML view.</span>
              </div>
            ) : null}
            <div className="field"><label htmlFor="wtools">Tools</label>
              <input type="text" id="wtools" value={(s.tools ?? []).join(", ")} placeholder="none (the agent's own)"
                onChange={(e) => onChange({ tools: e.target.value.split(",").map((t) => t.trim()).filter(Boolean) })} />
              <span className="hint">Comma-separated, like <span className="mono">mcp:keel:*</span>.</span>
            </div>
            <div className="field"><label htmlFor="wcap">Max tokens for this step (per run)</label>
              <div className="row">
                <input type="text" id="wcap" inputMode="numeric" value={s.max_tokens ?? ""} placeholder="no limit" style={{ width: 110 }}
                  onChange={(e) => onChange({ max_tokens: Number(e.target.value.replace(/\D/g, "")) || undefined })} />
                <select aria-label="When the limit is hit" value={s.on_limit ?? "pause"} onChange={(e) => onChange({ on_limit: e.target.value as OnCap })}>
                  {ON_LIMIT.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </div>
            </div>
            <div className="field"><label htmlFor="winstr">Instructions for this step</label>
              <textarea id="winstr" value={s.instructions ?? ""} placeholder="What this step wants that the agent's role does not say" rows={4}
                onChange={(e) => onChange({ instructions: e.target.value || undefined })} />
            </div>
          </>
        ) : s.kind === "code" ? (
          <div className="field"><label htmlFor="wact">What it runs</label>
            <input type="text" id="wact" value={s.action ?? ""} onChange={(e) => onChange({ action: e.target.value || undefined })} placeholder="verify_red | verify_green | commit | run:<cmd>" />
            <span className="hint">Runs plain code. Costs no tokens.</span>
          </div>
        ) : (
          <p className="hint" style={{ margin: 0 }}>{s.kind === "gate" ? "Waits for you (LangGraph interrupt). Costs no tokens." : s.kind === "include" ? `Runs the steps of the ${s.flow ?? "?"} flow here.` : "Decides yes or no with plain code. Costs no tokens."}</p>
        )}
        <label className="chk"><input type="checkbox" id="wpa" checked={!!s.per_ac} disabled={locked} onChange={(e) => onChange({ per_ac: e.target.checked || undefined })} /> run once per acceptance criterion (inside the loop)</label>
        <p className="hint" style={{ margin: 0 }}>Other keys (when, choices, markers…) stay as they are; change them in the YAML view.</p>
      </div>
    </section>
  );
}

const PCOL: Record<string, string> = { claude: "#e2725b", codex: "#3fa37a", copilot: "#6e7bff", fake: "var(--faint)" };

/** The estimate: total and range, the criteria slider, the provider split, and the cost of each section (bars). */
function EstimatePanel({ est, error, acs, setAcs, dirty, w, tokens }: {
  est: Estimate | null; error: { message: string; hint?: string } | null; acs: number; setAcs: (n: number) => void; dirty: boolean;
  w?: Workflow; tokens?: Record<string, number>;
}) {
  const by = Object.entries(est?.by_provider ?? {}).filter(([, v]) => v) as [string, number][];
  const tot = by.reduce((a, [, v]) => a + v, 0) || 1;
  const map = useMemo(() => (w ? buildFlowMap(w.steps) : null), [w]);
  const rows = map && tokens ? map.sections.map((x) => ({
    key: x.key, label: x.kind === "phase" ? phaseTitle(x.phase) : x.kind === "loop" ? "For each criterion" : `${x.phase} (included)`,
    v: sumOf(x.ids, tokens),
  })).filter((r) => r.v > 0) : [];
  const max = Math.max(1, ...rows.map((r) => r.v));
  const all = rows.reduce((a, r) => a + r.v, 0) || 1;
  return (
    <section className="panel" aria-label={dirty ? "Estimate with your changes" : "Estimate for this workflow"}>
      <div className="panel-head">
        <h2>{dirty ? "Estimate with your changes" : "Estimate for this workflow"}</h2>
        <span className="hint">{dirty ? "not saved yet" : "from this project's past runs"}</span>
      </div>
      <div className="panel-body grid" style={{ gap: 12 }}>
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
            {rows.length > 0 && (
              <div className="est-secs" aria-label="Tokens per section">
                <span className="lab">Where the tokens go</span>
                {rows.map((r) => (
                  <div key={r.key} className="est-sec">
                    <span className="est-sec-l">{r.label}</span>
                    <span className="sx-bar" aria-hidden="true"><i style={{ width: `${Math.max(2, (r.v / max) * 100)}%` }} /></span>
                    <span className="est-sec-v num">{kfmt(r.v)} <span className="sub">{Math.round((r.v / all) * 100)}%</span></span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
        <p className="hint" style={{ margin: 0 }}>
          Per agent step: (input + output tokens, median of past runs) × runs per AC × (1 + retry rate). The range uses the 10th and 90th percentile.
          {dirty ? " This estimate includes your unsaved changes." : ""}
        </p>
      </div>
    </section>
  );
}
