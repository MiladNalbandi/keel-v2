// All projects, the home: every repo keel knows, each on one row with what it does now — its flow and phase, what
// waits for you (Answer opens its Inbox items), the agents that work, its branch and last activity — and the quick
// actions (Answer, Watch live, Open flow, Start a flow).

import { useMemo, useState } from "react";
import { api, errorParts, type Job, type Project } from "../api";
import { EmptyState } from "../components/EmptyState";
import { StartFlowDrawer } from "../components/StartFlow";
import { agoText } from "../components/UsageStrip";
import { Drawer, ErrorBox, Loading, PageHead, Pill } from "../components/ui";
import { plural } from "../format";
import { hashFor } from "../routes";
import { go, useApp, useLoad } from "../state";

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

type Activity = { at: string; running: boolean };

/** The newest agent call per project (the job list is newest first; a running call counts as now). */
export function lastActivity(jobs: Job[] | null): Record<string, Activity> {
  const m: Record<string, Activity> = {};
  (jobs ?? []).forEach((j) => {
    const running = j.status === "running";
    const at = running ? new Date().toISOString() : j.ended_at ?? j.started_at;
    const cur = m[j.project_id];
    if (!cur || (!cur.running && (running || Date.parse(at) > Date.parse(cur.at)))) m[j.project_id] = { at, running };
  });
  return m;
}

function ProjectRow({ p, shown, act, onStart }: { p: Project; shown: boolean; act?: Activity; onStart: () => void }) {
  const { setProjectId } = useApp();
  const open = (page: "flow" | "live") => {
    setProjectId(p.id);
    go(page);
  };
  const state = p.waiting ? "wait" : p.running ? "run" : p.flow ? "idle" : "none";
  const idle = !p.waiting && !p.running;
  return (
    <li className={`proj s-${state}${shown ? " shown" : ""}`} data-testid="project-row" aria-labelledby={`proj-${p.id}`} onClick={() => open("flow")}>
      <div className="proj-id">
        <h2 className="proj-name" id={`proj-${p.id}`}>
          <button type="button" className="linkbtn" onClick={(e) => { e.stopPropagation(); open("flow"); }} title={`Open ${p.name}`}>{p.name}</button>
          {shown && <span className="tag" title="The project the other screens show now">current</span>}
        </h2>
        <span className="sub mono proj-root" title={p.root}>{p.root}</span>
      </div>
      <div className="proj-flow">
        {p.flow ? (
          <span className="proj-now">
            <span className="tag keel">{p.flow}</span>
            <span><span className="sub">at</span> <b className="mono">{p.phase || "—"}</b></span>
            {p.acs?.[1] ? <span className="sub num">ACs {p.acs[0]} / {p.acs[1]}</span> : null}
          </span>
        ) : <span className="sub">No flow yet</span>}
        <span className="proj-facts sub">
          <span className="mono proj-branch" title={`branch ${p.branch}`}>⎇ {p.branch || "—"}</span>
          {act && <span>{act.running ? "working now" : `last activity ${agoText(act.at)}`}</span>}
        </span>
      </div>
      <div className="proj-status">
        {p.waiting > 0 && <Pill tone="warn">{p.waiting} waiting for you</Pill>}
        {p.running > 0 && <Pill tone="run">{plural(p.running, "agent")} working</Pill>}
        {idle && <Pill tone="idle">{p.flow ? "idle" : "no flow"}</Pill>}
      </div>
      <div className="proj-acts" onClick={(e) => e.stopPropagation()}>
        {p.waiting > 0 && (
          <a className="btn sm warn" href={hashFor("inbox", p.id)} aria-label={`Answer what waits in ${p.name}`}>Answer</a>
        )}
        {p.running > 0 && (
          <button className="btn sm" type="button" onClick={() => open("live")} aria-label={`Watch the agents of ${p.name}`}>Watch live</button>
        )}
        {p.flow && (
          <button className={`btn sm${idle ? "" : " ghost"}`} type="button" onClick={() => open("flow")} aria-label={`Open the flow of ${p.name}`}>Open flow</button>
        )}
        {idle && (
          <button className={`btn sm${p.flow ? " ghost" : " primary"}`} type="button" onClick={onStart} aria-label={`Start a flow in ${p.name}`}>Start a flow</button>
        )}
      </div>
    </li>
  );
}

export function ProjectsPage() {
  const { projects, projectsLoaded, projectsError, pid, reloadProjects } = useApp();
  const [drawer, setDrawer] = useState<{ kind: "add" } | { kind: "start"; project?: string } | null>(null);
  // the last agent call of every project, for "last activity" (it refetches on every live event)
  const jobs = useLoad("projects:activity", () => api.jobs({ limit: 100 }));
  const acts = useMemo(() => lastActivity(jobs.data), [jobs.data]);
  const waitingAll = projects.reduce((a, p) => a + (p.waiting || 0), 0);
  const waitingIn = projects.filter((p) => p.waiting > 0).length;
  const runningAll = projects.reduce((a, p) => a + (p.running || 0), 0);
  return (
    <>
      <PageHead
        title="Projects"
        sub="Every repo keel knows on this machine. Answer what waits for you, watch what runs, or start a flow."
        actions={<>
          <button className="btn" type="button" onClick={() => setDrawer({ kind: "add" })}>Add repo</button>
          <button className="btn primary" type="button" id="startFlow" onClick={() => setDrawer({ kind: "start" })} disabled={!projects.length}>Start a flow</button>
        </>}
      />
      {projectsError && !projects.length ? (
        <ErrorBox error={{ message: projectsError }} onRetry={() => void reloadProjects()} />
      ) : !projectsLoaded ? <Loading what="Loading projects" /> : !projects.length ? (
        <div className="panel"><EmptyState title="No project yet"
          action={<button className="btn primary" type="button" onClick={() => setDrawer({ kind: "add" })}>Add repo</button>}>
          Mount a repo at /workspace when you start the container, or add a folder here.
        </EmptyState></div>
      ) : (
        <>
          <div className="home-sum" aria-label="Across all projects">
            {waitingAll > 0 ? (
              <span className="home-wait"><b className="amber">◆ {waitingAll} waiting for you</b> <span className="sub">in {plural(waitingIn, "project")}</span></span>
            ) : <span className="sub">Nothing waits for you.</span>}
            <span className="sub">{runningAll ? `${plural(runningAll, "agent")} working` : "No agent is working"}</span>
            {waitingAll > 0 && <a className="btn sm" href={hashFor("inbox")}>Open the inbox</a>}
          </div>
          {projectsError && <div className="errbox" role="alert" style={{ marginBottom: 12 }}><b>{projectsError}</b></div>}
          <ul className="proj-list" aria-label="Projects">
            {projects.map((p) => (
              <ProjectRow key={p.id} p={p} shown={p.id === pid} act={acts[p.id]} onStart={() => setDrawer({ kind: "start", project: p.id })} />
            ))}
          </ul>
        </>
      )}
      {drawer?.kind === "add" && <AddRepoDrawer onClose={() => setDrawer(null)} />}
      {drawer?.kind === "start" && <StartFlowDrawer projectId={drawer.project} onClose={() => setDrawer(null)} />}
    </>
  );
}
