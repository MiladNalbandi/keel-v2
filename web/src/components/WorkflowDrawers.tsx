// Drawers and the Library tab of the Workflows screen: new workflow, install review, import.

import { useState } from "react";
import { api, errorParts, type InstallReview, type LibraryItem, type Workflow } from "../api";
import { kfmt } from "../format";
import { useApp, useLoad } from "../state";
import { Async, Drawer, ErrorBox } from "./ui";

type Err = { message: string; hint?: string } | null;

export function NewWorkflowDrawer({ pid, templates, onClose, onCreated, onLibrary }: {
  pid: string; templates: Workflow[]; onClose: () => void; onCreated: (w: Workflow) => void; onLibrary: () => void;
}) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const [from, setFrom] = useState<"template" | "blank" | "library">("template");
  const [tpl, setTpl] = useState(templates.find((t) => t.id === "fix")?.id ?? templates[0]?.id ?? "");
  const [rules, setRules] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const create = async () => {
    if (from === "library") { onClose(); onLibrary(); return; }
    setBusy(true);
    setErr(null);
    try {
      const w = await api.newWorkflow(pid, { name: name.trim() || "My workflow", from: from === "blank" ? "blank" : `template:${tpl}`, keel_rules: rules });
      toast(`${w.name} made. It gets a wiki page when you save.`);
      onCreated(w);
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title="New workflow" onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" id="nwCreate" onClick={create} disabled={busy}>{from === "library" ? "Open the library" : busy ? "Creating…" : "Create and open builder"}</button></>}>
      <div className="field"><label htmlFor="nw-name">Name</label><input type="text" id="nw-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Hotfix" /></div>
      <div className="field">
        <span className="lab">Start from</span>
        <label className="radio"><input type="radio" name="nwfrom" checked={from === "template"} onChange={() => setFrom("template")} />
          <span><b>A keel flow</b> — copy{" "}
            <select aria-label="keel flow to copy" value={tpl} onChange={(e) => setTpl(e.target.value)} onClick={() => setFrom("template")}>
              {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>{" "}and change it</span></label>
        <label className="radio"><input type="radio" name="nwfrom" checked={from === "blank"} onChange={() => setFrom("blank")} /><span><b>Empty</b> — add every step yourself</span></label>
        <label className="radio"><input type="radio" name="nwfrom" checked={from === "library"} onChange={() => setFrom("library")} /><span><b>The library</b> — install one, then edit it</span></label>
      </div>
      <label className="chk"><input type="checkbox" id="nw-rules" checked={rules} onChange={(e) => setRules(e.target.checked)} /> keel rules on (spec approval, test checks and final review stay)</label>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

function ReviewKv({ r }: { r: InstallReview }) {
  return (
    <div className="kv">
      {r.from && <><span>From</span><b>{r.from}{r.version ? ` · v${r.version}` : ""}</b></>}
      <span>Agents it runs</span><b>{r.agents.join(", ") || "none"}</b>
      <span>MCP servers it needs</span><b>{r.mcp.join(", ") || "none"}</b>
      <span>Gates that wait for you</span><b>{r.gates}</b>
      <span>Estimated tokens per run</span><b>≈ {kfmt(r.est_tokens)}</b>
      <span>Edits files?</span><b>{r.edits_files ? "yes — keel guards apply" : "no"}</b>
    </div>
  );
}

export function InstallDrawer({ pid, item, onClose, onInstalled }: { pid: string; item: LibraryItem; onClose: () => void; onInstalled: (w: Workflow) => void }) {
  const { project, toast } = useApp();
  const [scope, setScope] = useState<"project" | "all">("project");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const install = async () => {
    setBusy(true);
    setErr(null);
    try {
      const w = await api.install(pid, item.id, scope);
      toast(`${item.name} installed. It is in the workflow list and has a wiki page.`);
      onInstalled(w);
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title={`Install ${item.name}`} onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button><button className="btn primary" type="button" id="doInstall" onClick={install} disabled={busy}>{busy ? "Installing…" : "Install"}</button></>}>
      <p className="sub" style={{ margin: 0 }}>{item.about}</p>
      <div className="field"><span className="lab">Check before you install</span>
        <ReviewKv r={{ from: item.source, version: item.version, agents: item.agents, mcp: item.mcp, gates: item.gates, est_tokens: item.est_tokens, edits_files: item.edits_files }} />
      </div>
      <div className="field"><span className="lab">Install for</span>
        <label className="radio"><input type="radio" name="iscope" checked={scope === "project"} onChange={() => setScope("project")} /><span>{project?.name ?? pid} only</span></label>
        <label className="radio"><input type="radio" name="iscope" checked={scope === "all"} onChange={() => setScope("all")} /><span>All projects on this machine</span></label>
      </div>
      <p className="hint" style={{ margin: 0 }}>With keel rules on, an installed workflow cannot remove spec approval, the test checks or the final review.</p>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

export function ImportDrawer({ pid, onClose, onImported }: { pid: string; onClose: () => void; onImported: (w: Workflow) => void }) {
  const { toast } = useApp();
  const [url, setUrl] = useState("");
  const [yaml, setYaml] = useState("");
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const [done, setDone] = useState<{ workflow: Workflow; review: InstallReview } | null>(null);
  const doImport = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.importWorkflow(pid, yaml ? { yaml } : { url: url.trim() });
      setDone(r);
      toast(`Read ${r.workflow.name} — ${r.workflow.steps.length} steps, ${r.review.gates} gates. Review it before it runs.`);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  if (done) {
    return (
      <Drawer title={`Imported ${done.workflow.name}`} onClose={onClose}
        footer={<button className="btn primary" type="button" onClick={() => { onImported(done.workflow); onClose(); }}>Open in builder</button>}>
        <div className="field"><span className="lab">Check before it runs</span><ReviewKv r={done.review} /></div>
        {done.review.warnings?.length ? <div className="errbox">{done.review.warnings.map((w, i) => <span key={i}>{w}</span>)}</div> : null}
      </Drawer>
    );
  }
  return (
    <Drawer title="Import a workflow" onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={doImport} disabled={busy || (!url.trim() && !yaml)}>{busy ? "Reading…" : "Import"}</button></>}>
      <div className="field"><label htmlFor="imp-url">From a link or git repo</label>
        <input type="text" id="imp-url" value={url} onChange={(e) => { setUrl(e.target.value); setYaml(""); setFileName(""); }} placeholder="https://github.com/your-team/workflows/blob/main/hotfix.yaml" /></div>
      <div className="field"><label htmlFor="imp-file">Or a file</label>
        <input type="file" id="imp-file" accept=".yaml,.yml" onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          setYaml(await f.text());
          setFileName(f.name);
          setUrl("");
        }} />
        {fileName && <span className="hint">{fileName} · {yaml.split("\n").length} lines</span>}
      </div>
      <p className="hint" style={{ margin: 0 }}>After import you see the same check as Install: agents, MCP servers, gates and the token estimate.</p>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

export function LibraryTab({ pid, onInstalled, onImported }: { pid: string; onInstalled: (w: Workflow) => void; onImported: (w: Workflow) => void }) {
  const lib = useLoad("library", () => api.library());
  const [q, setQ] = useState("");
  const [install, setInstall] = useState<LibraryItem | null>(null);
  const [imp, setImp] = useState(false);
  const needle = q.trim().toLowerCase();
  return (
    <>
      <div className="row" style={{ marginBottom: 12, justifyContent: "space-between" }}>
        <div className="row"><button className="btn sm" type="button" id="importWf" onClick={() => setImp(true)}>Import file or URL</button><span className="hint">.yaml from a file, a git repo or a link</span></div>
        <input type="text" className="inline-input" placeholder="Search workflows" aria-label="Search workflows" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 200 }} />
      </div>
      <Async r={lib} what="Loading the library">
        {(items) => {
          const list = items.filter((l) => !needle || (l.name + l.about + l.source).toLowerCase().includes(needle));
          return list.length ? (
            <div className="libgrid">
              {list.map((l) => (
                <div key={l.id} className="panel"><div className="panel-body grid" style={{ gap: 8 }}>
                  <div className="row" style={{ justifyContent: "space-between" }}><b>{l.name}</b><span className={`tag ${l.source === "keel" ? "keel" : ""}`}>{l.source}</span></div>
                  <span className="sub">{l.about}</span>
                  <div className="row"><span className="tag">{l.steps} steps</span><span className="tag">◆ {l.gates}</span><span className="tag">≈ {kfmt(l.est_tokens)} tokens</span><span className="tag">v{l.version}</span></div>
                  <div className="row">
                    {l.installed ? <><span className="pill p-ok">installed</span><a className="btn sm ghost" href={api.exportUrl(l.id)} download style={{ textDecoration: "none" }}>Export</a></>
                      : <button className="btn sm primary" type="button" onClick={() => setInstall(l)}>Install</button>}
                  </div>
                </div></div>
              ))}
            </div>
          ) : <div className="empty">{items.length ? "No workflow matches." : "The library is empty."}</div>;
        }}
      </Async>
      {install && <InstallDrawer pid={pid} item={install} onClose={() => setInstall(null)} onInstalled={(w) => { void lib.reload(); onInstalled(w); }} />}
      {imp && <ImportDrawer pid={pid} onClose={() => setImp(false)} onImported={onImported} />}
    </>
  );
}
