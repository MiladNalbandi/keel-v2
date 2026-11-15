// All projects, the home: every repo keel knows, each on one row with what it does now — its flow and phase, what
// waits for you (Answer opens its Inbox items), the agents that work, its branch and last activity — and the quick
// actions (Answer, Watch live, Open flow, Start a flow). v0.15.7 "Add projects" ticks repos from your folders
// (components/AddProjects); #/projects/add opens it (the menu's switcher and ⌘K link there).

import { useEffect, useMemo, useState } from "react";
import { api, type Job, type Project } from "../api";
import { AddProjectsDrawer } from "../components/AddProjects";
import { EmptyState } from "../components/EmptyState";
import { needsInit } from "../components/InitAsk";
import { isTyping, modalOpen } from "../keys";
import { StartFlowDrawer } from "../components/StartFlow";
import { ErrorBox, Loading, PageHead, Pill } from "../components/ui";
import { agoText, plural } from "../format";
import { hashFor } from "../routes";
import { go, useApp, useLoad, useRoute } from "../state";

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
          {shown && <span className="tag rdw-cur" title="The project the other screens show now">current</span>}
          {needsInit(p) && <span className="tag" data-testid="not-set-up" title="keel does not know this project yet: open it to set it up (init)">not set up</span>}
        </h2>
        <span className="sub mono proj-root" title={p.root}>{p.root}</span>
      </div>
      <div className="proj-flow">
        {p.flow ? (
          <span className="proj-now">
            <span className="tag keel rdw-wf">{p.flow}</span>
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
  // v0.15.7 #/projects/add opens Add projects; closing it goes back to #/projects
  const { arg } = useRoute();
  const adding = drawer?.kind === "add" || arg === "add";
  const closeDrawer = () => {
    setDrawer(null);
    if (arg === "add") go("projects");
  };
  // the last agent call of every project, for "last activity" (it refetches on every live event)
  const jobs = useLoad("projects:activity", () => api.jobs({ limit: 100 }));
  const acts = useMemo(() => lastActivity(jobs.data), [jobs.data]);
  const waitingAll = projects.reduce((a, p) => a + (p.waiting || 0), 0);
  const waitingIn = projects.filter((p) => p.waiting > 0).length;
  const runningAll = projects.reduce((a, p) => a + (p.running || 0), 0);
  // keel 0.28 the keys on the head's buttons: A adds projects, I opens the inbox while something waits (N is the shell's)
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.defaultPrevented || isTyping(e.target) || modalOpen()) return;
      if (drawer || arg === "add") return;
      if (e.key === "a") {
        e.preventDefault();
        setDrawer({ kind: "add" });
      } else if (e.key === "i" && waitingAll > 0) {
        e.preventDefault();
        go("inbox");
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [drawer, arg, waitingAll]);
  return (
    <div className="rdw rdw-proj">
      <PageHead
        title="Projects"
        sub="Every repo keel knows on this machine. Answer what waits for you, watch what runs, or start a flow."
        actions={<>
          <button className="btn" type="button" aria-keyshortcuts="A" onClick={() => setDrawer({ kind: "add" })}>
            <FolderPlus /> Add projects <kbd aria-hidden="true">A</kbd>
          </button>
          <button className="btn primary" type="button" id="startFlow" aria-keyshortcuts="N" onClick={() => setDrawer({ kind: "start" })} disabled={!projects.length}>
            <span aria-hidden="true" className="rdw-plus">+</span> Start a flow <kbd aria-hidden="true">N</kbd>
          </button>
        </>}
      />
      {projectsError && !projects.length ? (
        <ErrorBox error={{ message: projectsError }} onRetry={() => void reloadProjects()} />
      ) : !projectsLoaded ? <Loading what="Loading projects" /> : !projects.length ? (
        <div className="panel"><EmptyState title="No project yet"
          action={<button className="btn primary" type="button" onClick={() => setDrawer({ kind: "add" })}>Add projects</button>}>
          Add the repos from your folders, or mount a repo at /workspace when you start the container. New to keel?{" "}
          <a href="#/help">Help › Start here</a> walks you through the first steps.
        </EmptyState></div>
      ) : (
        <>
          <div className="home-sum rdw-sum rise r1" aria-label="Across all projects">
            {waitingAll > 0 ? (
              <span className="home-wait"><b className="amber">◆ {waitingAll} waiting for you</b> <span className="sub">in {plural(waitingIn, "project")}</span></span>
            ) : <span className="sub">Nothing waits for you.</span>}
            <span className={`sub rdw-dotted${runningAll ? " on" : ""}`}>{runningAll ? `${plural(runningAll, "agent")} working` : "No agent is working"}</span>
            {waitingAll > 0 && <a className="btn sm" href={hashFor("inbox")} aria-keyshortcuts="I">Open the inbox <kbd aria-hidden="true">I</kbd></a>}
          </div>
          {projectsError && <div className="errbox" role="alert" style={{ marginBottom: 12 }}><b>{projectsError}</b></div>}
          <ul className="proj-list rdw-list rise r2" aria-label="Projects">
            {projects.map((p) => (
              <ProjectRow key={p.id} p={p} shown={p.id === pid} act={acts[p.id]} onStart={() => setDrawer({ kind: "start", project: p.id })} />
            ))}
          </ul>
          <p className="rdw-foot rise r3">
            New to keel? <a href="#/help">Help › Start here</a> walks you through the first steps. A repo you mount at{" "}
            <span className="mono">/workspace</span> shows up in Add projects.
          </p>
        </>
      )}
      {adding && <AddProjectsDrawer onClose={closeDrawer} />}
      {drawer?.kind === "start" && <StartFlowDrawer projectId={drawer.project} onClose={closeDrawer} />}
    </div>
  );
}

/** A folder with a plus (Add projects). */
const FolderPlus = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><path d="M12 11v5M9.5 13.5h5" />
  </svg>
);
