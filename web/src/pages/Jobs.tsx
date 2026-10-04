// Jobs (Run): every agent call — what runs now, and everything before. One row per call, with its steps.

import { useEffect, useRef, useState } from "react";
import { api, errorParts, type Job } from "../api";
import { mergeSteps } from "../components/StepFeed";
import { FilesTouched, Outcome, StepView, useJumpToStep } from "../components/StepView";
import { Async, ErrorBox, GoButton, PageHead, Panel, Prov, Since, StatusPill, Tabs } from "../components/ui";
import { clock, kfmt, since, usd } from "../format";
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
    <div ref={box} style={{ marginTop: 16 }}>
      <Panel title={<h2>{job.data ? `${job.data.agent} · ${job.data.phase || job.data.step}` : id} · step feed</h2>}
        extra={<button className="btn sm ghost" type="button" id="closeJob" onClick={onClose}>Close</button>} body="feed">
        {job.error ? <ErrorBox error={job.error} onRetry={() => void job.reload()} /> : !job.data ? <div className="empty loading">Loading…</div>
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
    <div className="panel"><div className="panel-body grid" style={{ gap: 6 }}>
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

export function JobsPage({ pid }: { pid: string }) {
  const { projects } = useApp();
  const { arg } = useRoute();
  const [tab, setTab] = useState<"now" | "hist">("now");
  const [fProject, setFProject] = useState(pid);
  const [fProvider, setFProvider] = useState("");
  const [fStatus, setFStatus] = useState("");
  const running = useLoad(`jobs-now:${pid}`, () => api.jobs({ project: pid, status: "running" }));
  const histKey = `jobs-hist:${fProject}:${fProvider}:${fStatus}`;
  const hist = useLoad(tab === "hist" ? histKey : null, () => api.jobs({ project: fProject || undefined, provider: fProvider || undefined, status: fStatus || undefined }));
  useEffect(() => {
    const t = window.setInterval(() => void running.reload(), 5000);
    return () => window.clearInterval(t);
  }, [running.reload]);
  const pname = (id: string) => projects.find((p) => p.id === id)?.name ?? id;
  const n = running.data?.length ?? 0;
  return (
    <>
      <PageHead title="Jobs" sub="Every agent call: what runs now, and everything before. One row per call, with its steps."
        actions={<Tabs value={tab} onChange={setTab} label="Jobs" options={[["now", `Running now (${n})`], ["hist", "History"]]} />} />
      {tab === "now" ? (
        <Async r={running} what="Loading jobs">
          {(list) => list.length ? (
            <div className="grid g2" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))" }}>
              {list.map((j) => <RunningCard key={j.id} j={j} />)}
            </div>
          ) : <div className="empty">Nothing is running.</div>}
        </Async>
      ) : (
        <>
          <div className="row" style={{ marginBottom: 10 }}>
            <select aria-label="Project" value={fProject} onChange={(e) => setFProject(e.target.value)}>
              <option value="">All projects</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <select aria-label="Provider" value={fProvider} onChange={(e) => setFProvider(e.target.value)}>
              <option value="">All providers</option>
              <option value="claude">Claude</option><option value="codex">GPT / Codex</option><option value="copilot">Copilot</option><option value="fake">Fake model</option>
            </select>
            <select aria-label="Status" value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
              <option value="">Any status</option><option value="running">running</option><option value="done">done</option><option value="failed">failed</option>
            </select>
          </div>
          <Async r={hist} what="Loading history">
            {(list) => (
              <div className="panel"><div className="table-wrap"><table>
                <thead><tr><th>When</th><th>Project</th><th>Agent</th><th>Model</th><th>Node</th><th>Time</th><th>Tokens in / out</th><th>Cost</th><th>Status</th></tr></thead>
                <tbody>
                  {list.map((j) => (
                    <tr key={j.id} className={`click ${arg === j.id ? "rowsel" : ""}`} tabIndex={0} onClick={() => go("jobs", j.id)} onKeyDown={(e) => e.key === "Enter" && go("jobs", j.id)}>
                      <td className="mono sub">{clock(j.started_at, false)}</td>
                      <td>{pname(j.project_id)}</td>
                      <td><b>{j.agent}</b></td>
                      <td><Prov p={j.provider} m={j.model} /></td>
                      <td className="mono">{j.phase || j.step}</td>
                      <td className="num">{since(j.started_at, j.ended_at)}</td>
                      <td className="num mono" title="new input / output (cached context re-sent)">{kfmt(j.tokens_in)} / {kfmt(j.tokens_out)}{j.tokens_cached ? <span className="sub"> (+{kfmt(j.tokens_cached)} cached)</span> : null}</td>
                      <td className="num">{j.cost_usd ? usd(j.cost_usd) : "—"}</td>
                      <td><StatusPill status={j.status} /></td>
                    </tr>
                  ))}
                  {!list.length && <tr><td colSpan={9} className="empty">No job matches.</td></tr>}
                </tbody>
              </table></div></div>
            )}
          </Async>
          <p className="hint">Cost shows only for API-key and Claude CLI calls; subscription CLIs report tokens without a price.</p>
        </>
      )}
      {arg && <JobSteps key={arg} id={arg} onClose={() => go("jobs")} />}
    </>
  );
}
