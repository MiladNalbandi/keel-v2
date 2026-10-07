// Jobs (Run): every agent call. What runs now sits on top (only while something runs); the history is always there
// below it — agent, flow step, model, tokens, time and status, one row per call — and a row opens its steps in place.

import { useEffect, useRef, useState } from "react";
import { api, errorParts, type Job } from "../api";
import { EmptyState } from "../components/EmptyState";
import { StartFlowDrawer } from "../components/StartFlow";
import { mergeSteps } from "../components/StepFeed";
import { FilesTouched, Outcome, StepView, useJumpToStep } from "../components/StepView";
import { PipelinesView } from "../components/plugins/Pipelines";
import { ErrorBox, GoButton, Loading, PageHead, Panel, Pill, Prov, Since, StatusPill, Tabs } from "../components/ui";
import { clock, kfmt, plural, since, usd } from "../format";
import { go, useApp, useLoad, useRoute } from "../state";
import { useJobSteps } from "./Live";

function JobSteps({ id, onClose }: { id: string; onClose: () => void }) {
  const job = useLoad(`job:${id}`, () => api.job(id), { live: false });
  const live = useJobSteps(job.data?.status === "running" ? id : null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.scrollIntoView?.({ block: "nearest" }), []);
  const steps = mergeSteps(job.data?.steps ?? [], live.steps);
  const anchor = `job-${id}`;
  const jump = useJumpToStep(anchor);
  const running = job.data?.status === "running" || live.running;
  return (
    <div ref={box} className="job-steps">
      <Panel title={<h2>{job.data ? `${job.data.agent} · ${job.data.phase || job.data.step}` : id} · step feed</h2>}
        extra={<div className="row">
          {job.data && <GoButton to="live" arg={id} className="btn sm">{running ? "Watch live" : "Open in Live agents"}</GoButton>}
          <button className="btn sm ghost" type="button" id="closeJob" onClick={onClose}>Close</button>
        </div>} body="feed">
        {job.error ? <ErrorBox error={job.error} onRetry={() => void job.reload()} /> : !job.data ? <Loading what="Loading the steps" />
          : !steps.length ? <div className="empty">No steps recorded.</div> : (
            <>
              <FilesTouched steps={steps} onJump={jump} />
              {steps.map((s) => <StepView key={s.n} s={s} idPrefix={anchor} />)}
              {!running && <Outcome job={job.data} steps={steps} onJump={jump} />}
            </>
          )}
      </Panel>
    </div>
  );
}

function RunningCard({ j }: { j: Job }) {
  const { projects, toast } = useApp();
  return (
    <div className="panel run-card"><div className="panel-body grid" style={{ gap: 6 }}>
      <div className="row" style={{ justifyContent: "space-between" }}><b>{j.agent}</b><span className="pill p-run num"><Since from={j.started_at} /></span></div>
      <span className="sub">{projects.find((p) => p.id === j.project_id)?.name ?? j.project_id} · node <span className="mono">{j.phase || j.step}</span>{j.ac ? ` · ${j.ac}` : ""}</span>
      <span><Prov p={j.provider} m={j.model} /></span>
      <span className="sub num">{j.steps_count} steps · keel calls {j.mcp_calls}</span>
      <div className="row">
        <GoButton to="live" arg={j.id}>Watch live</GoButton>
        <button className="btn sm ghost" type="button" onClick={() => go("jobs", j.id)}>Steps</button>
        <button className="btn sm ghost" type="button" onClick={async () => {
          try {
            await api.stopJob(j.id);
            toast("Stop sent.");
          } catch (e) {
            toast(errorParts(e).message);
          }
        }}>Stop</button>
      </div>
    </div></div>
  );
}

const same = (a: string, b: string) => a.replace(/[-_]/g, "") === b.replace(/[-_]/g, "");
/** "green · AC-002", "explore", "verify · verify_green". */
const whereText = (j: Job) =>
  [j.phase || j.step, j.phase && j.step && !same(j.step, j.phase) ? j.step : null, j.ac].filter(Boolean).join(" · ");

function JobRow({ j, sel, pname, showProject, showCost }: { j: Job; sel: boolean; pname: string; showProject: boolean; showCost: boolean }) {
  const toggle = () => go("jobs", sel ? undefined : j.id);
  const cached = j.tokens_cached ? ` (+${kfmt(j.tokens_cached)} cached)` : "";
  return (
    <tr className={`click jrow ${sel ? "rowsel" : ""}`} tabIndex={0} aria-expanded={sel} onClick={toggle}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } }}>
      <td className="j-when mono sub" title={j.started_at}>{clock(j.started_at, false)}</td>
      {showProject && <td className="j-proj">{pname}</td>}
      <td className="j-agent"><b>{j.agent}</b></td>
      <td className="j-where mono">{whereText(j)}</td>
      <td className="j-model"><Prov p={j.provider} m={j.model} /></td>
      <td className="j-tok num mono" title={`new input ${j.tokens_in} / output ${j.tokens_out}${cached}`}>
        {kfmt(j.tokens_in)} / {kfmt(j.tokens_out)}{j.tokens_cached ? <span className="sub"> +{kfmt(j.tokens_cached)}</span> : null}
      </td>
      <td className="j-time num">{j.status === "running" ? <Since from={j.started_at} /> : since(j.started_at, j.ended_at)}</td>
      {showCost && <td className="j-cost num">{j.cost_usd ? usd(j.cost_usd) : "—"}</td>}
      <td className="j-status"><StatusPill status={j.status} /></td>
    </tr>
  );
}

export function JobsPage({ pid }: { pid: string }) {
  const { projects } = useApp();
  const { arg } = useRoute();
  const [fProject, setFProject] = useState(pid);
  const [fProvider, setFProvider] = useState("");
  const [fStatus, setFStatus] = useState("");
  const [start, setStart] = useState(false);
  const running = useLoad(`jobs-now:${pid}`, () => api.jobs({ project: pid, status: "running" }));
  const histKey = `jobs-hist:${fProject}:${fProvider}:${fStatus}`;
  const hist = useLoad(histKey, () => api.jobs({ project: fProject || undefined, provider: fProvider || undefined, status: fStatus || undefined }));
  useEffect(() => {
    const t = window.setInterval(() => void running.reload(), 5000);
    return () => window.clearInterval(t);
  }, [running.reload]);
  // a call that ends leaves "Running now": show it in the history too
  const n = running.data?.length ?? 0;
  const prevN = useRef(n);
  useEffect(() => {
    if (n < prevN.current) void hist.reload();
    prevN.current = n;
  }, [n, hist.reload]);
  const pname = (id: string) => projects.find((p) => p.id === id)?.name ?? id;
  const filtered = fProject !== pid || !!fProvider || !!fStatus;
  const clear = () => {
    setFProject(pid);
    setFProvider("");
    setFStatus("");
  };
  const list = hist.data ?? [];
  const showProject = !fProject;
  const showCost = list.some((j) => j.cost_usd > 0);
  const cols = 7 + (showProject ? 1 : 0) + (showCost ? 1 : 0);
  const open = arg && list.some((j) => j.id === arg) ? arg : null;
  const plugins = useLoad(`plugins:${pid}`, () => api.plugins(pid), { live: false });
  const ciOn = !!plugins.data?.find((p) => p.name === "ci")?.enabled;
  const tabs = ciOn ? (
    <Tabs value={arg === "pipelines" ? "pipelines" : "calls"} label="Jobs view" onChange={(t) => go("jobs", t === "pipelines" ? "pipelines" : undefined)}
      options={[["calls", "Agent calls"], ["pipelines", "Pipelines"]]} />
  ) : null;
  if (ciOn && arg === "pipelines") {
    return (
      <>
        <PageHead title="Jobs" sub="Agent calls, and the project's CI pipelines (CI/CD plugin)." />
        <div style={{ marginBottom: 12 }}>{tabs}</div>
        <PipelinesView pid={pid} />
      </>
    );
  }
  return (
    <>
      <PageHead title="Jobs" sub="Every agent call: what runs now, and everything before. Open a row to see its steps."
        actions={running.data ? (n ? <Pill tone="run">{n} running now</Pill> : <Pill tone="idle">nothing running</Pill>) : undefined} />
      {tabs && <div style={{ marginBottom: 12 }}>{tabs}</div>}
      {running.error && <div style={{ marginBottom: 16 }}><ErrorBox error={running.error} onRetry={() => void running.reload()} /></div>}
      {n > 0 && (
        <section className="jobs-sec" aria-labelledby="jobs-now-h">
          <h2 className="sec-h" id="jobs-now-h">Running now <span className="sub num">{n}</span></h2>
          <div className="run-cards">
            {running.data!.map((j) => <RunningCard key={j.id} j={j} />)}
          </div>
        </section>
      )}
      {arg && !open && hist.data && <JobSteps key={arg} id={arg} onClose={() => go("jobs")} />}
      <section className="jobs-sec" aria-labelledby="jobs-hist-h">
        <div className="sec-bar">
          <h2 className="sec-h" id="jobs-hist-h">History {hist.data && <span className="sub num">{plural(list.length, "call")}</span>}</h2>
          <div className="row jobs-filters" role="group" aria-label="Filters">
            <select aria-label="Filter by project" value={fProject} onChange={(e) => setFProject(e.target.value)}>
              <option value="">All projects</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <select aria-label="Filter by provider" value={fProvider} onChange={(e) => setFProvider(e.target.value)}>
              <option value="">All providers</option>
              <option value="claude">Claude</option><option value="codex">GPT / Codex</option><option value="copilot">Copilot</option><option value="fake">Fake model</option>
            </select>
            <select aria-label="Filter by status" value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
              <option value="">Any status</option><option value="running">running</option><option value="done">done</option><option value="failed">failed</option>
            </select>
            {filtered && <button className="btn sm ghost" type="button" onClick={clear}>Clear filters</button>}
          </div>
        </div>
        {hist.error && !hist.data ? <ErrorBox error={hist.error} onRetry={() => void hist.reload()} />
          : !hist.data ? <div className="panel"><Loading what="Loading the history" /></div>
            : !list.length ? (
              <div className="panel">
                {filtered ? (
                  <EmptyState title="No call matches these filters" action={<button className="btn sm" type="button" onClick={clear}>Clear filters</button>}>
                    Try another project, provider or status.
                  </EmptyState>
                ) : (
                  <EmptyState title={fProject ? "No agent has run in this project yet" : "No agent has run yet"}
                    action={<button className="btn primary" type="button" onClick={() => setStart(true)}>Start a flow</button>}>
                    Start a flow. Every agent call then shows up here with its steps, tokens and time.
                  </EmptyState>
                )}
              </div>
            ) : (
              <div className="panel"><div className="table-wrap"><table className="jobs-t" aria-label="Agent calls">
                <thead><tr>
                  <th>When</th>{showProject && <th>Project</th>}<th>Agent</th><th>Flow step</th><th>Model</th>
                  <th title="new input / output (+ cached context re-sent)">Tokens in / out</th><th>Time</th>{showCost && <th>Cost</th>}<th>Status</th>
                </tr></thead>
                <tbody>
                  {list.map((j) => (
                    <JobRowWithSteps key={j.id} j={j} open={open === j.id} cols={cols} pname={pname(j.project_id)} showProject={showProject} showCost={showCost} />
                  ))}
                </tbody>
              </table></div></div>
            )}
        {showCost && <p className="hint">Cost shows only for API-key and Claude CLI calls; subscription CLIs report tokens without a price.</p>}
      </section>
      {start && <StartFlowDrawer onClose={() => setStart(false)} />}
    </>
  );
}

function JobRowWithSteps({ j, open, cols, pname, showProject, showCost }: {
  j: Job; open: boolean; cols: number; pname: string; showProject: boolean; showCost: boolean;
}) {
  return (
    <>
      <JobRow j={j} sel={open} pname={pname} showProject={showProject} showCost={showCost} />
      {open && (
        <tr className="job-open"><td colSpan={cols}><JobSteps key={j.id} id={j.id} onClose={() => go("jobs")} /></td></tr>
      )}
    </>
  );
}
