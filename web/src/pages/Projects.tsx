// All projects: every repo keel knows; a ◆ means a flow waits for you.

import { useState } from "react";
import { api, errorParts } from "../api";
import { StartFlowDrawer } from "../components/StartFlow";
import { Drawer, ErrorBox, Loading, PageHead } from "../components/ui";
import { UsageStrip } from "../components/UsageStrip";
import { go, useApp } from "../state";

function AddRepoDrawer({ onClose }: { onClose: () => void }) {
  const { reloadProjects, setProjectId, toast } = useApp();
  const [root, setRoot] = useState("/workspace/");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const add = async () => {
    setBusy(true);
    setErr(null);
    try {
      const p = await api.addProject(root.trim(), name.trim() || undefined);
      await reloadProjects();
      setProjectId(p.id);
      toast(`${p.name} added.`);
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title="Add repo" onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={add} disabled={busy || !root.trim()}>{busy ? "Adding…" : "Add repo"}</button></>}>
      <div className="field">
        <label htmlFor="ar-root">Folder inside the container</label>
        <input type="text" id="ar-root" value={root} onChange={(e) => setRoot(e.target.value)} />
        <span className="hint">A git repo under <span className="mono">/workspace</span> (the folder you mounted with <span className="mono">-v</span>).</span>
      </div>
      <div className="field">
        <label htmlFor="ar-name">Name (optional)</label>
        <input type="text" id="ar-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="the folder name" />
      </div>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

export function ProjectsPage() {
  const { projects, projectsLoaded, projectsError, pid, setProjectId, reloadProjects } = useApp();
  const [drawer, setDrawer] = useState<"add" | "start" | null>(null);
  return (
    <>
      <PageHead
        title="Projects"
        sub="Every repo keel knows on this machine. Each flow is a LangGraph thread; a ◆ means it waits for you."
        actions={<>
          <button className="btn" type="button" onClick={() => setDrawer("add")}>Add repo</button>
          <button className="btn primary" type="button" id="startFlow" onClick={() => setDrawer("start")} disabled={!projects.length}>Start a flow</button>
        </>}
      />
      <div style={{ marginBottom: 16 }}><UsageStrip /></div>
      {projectsError && !projects.length ? (
        <ErrorBox error={{ message: projectsError }} onRetry={() => void reloadProjects()} />
      ) : !projectsLoaded ? <Loading what="Loading projects" /> : !projects.length ? (
        <div className="panel"><div className="panel-body empty grid" style={{ gap: 10, justifyItems: "center" }}>
          <b>No project yet</b>
          <span className="sub">Mount a repo at /workspace when you start the container, or add a folder here.</span>
          <button className="btn primary" type="button" onClick={() => setDrawer("add")}>Add repo</button>
        </div></div>
      ) : (
        <div className="panel">
          <div className="table-wrap">
            <table>
              <thead><tr><th>Project</th><th>Flow</th><th>Now at</th><th>ACs</th><th>Waiting</th><th>Agents</th><th>Branch</th></tr></thead>
              <tbody>
                {projects.map((p) => (
                  <tr key={p.id} className={`click ${p.id === pid ? "rowsel" : ""}`} tabIndex={0}
                    onClick={() => { setProjectId(p.id); go("flow"); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { setProjectId(p.id); go("flow"); } }}>
                    <td><b>{p.name}</b><div className="sub mono">{p.root}</div></td>
                    <td>{p.flow ? <span className="tag keel">{p.flow}</span> : <span className="sub">no flow</span>}</td>
                    <td className="mono">{p.phase || "—"}</td>
                    <td className="num">{p.acs?.[1] ? `${p.acs[0]} / ${p.acs[1]}` : "—"}</td>
                    <td>{p.waiting ? <span className="pill p-warn">◆ {p.waiting} gate</span> : <span className="sub">—</span>}</td>
                    <td>{p.running ? <span className="pill p-run">{p.running} running</span> : <span className="pill p-idle">idle</span>}</td>
                    <td className="mono sub">{p.branch}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <p className="hint" style={{ marginTop: 12 }}>
        Source: the api's project list and each repo's <span className="mono">.keel/state.json</span>. keel v2 keeps writing that format, so keel v1 tools still read these projects.
      </p>
      {drawer === "add" && <AddRepoDrawer onClose={() => setDrawer(null)} />}
      {drawer === "start" && <StartFlowDrawer onClose={() => setDrawer(null)} />}
    </>
  );
}
