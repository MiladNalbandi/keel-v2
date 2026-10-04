// Live agents (Run): watch each agent while it works — what it says, which tools it calls, what code it writes,
// and what it returns. Steps arrive over SSE (agent.step) and by polling /api/jobs/{id}/steps.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorParts, type Job, type JobStep } from "../api";
import { kindClass, mergeSteps } from "../components/StepFeed";
import { FilesTouched, kindLabel, Outcome, StepView, useJumpToStep } from "../components/StepView";
import { Async, ErrorBox, PageHead, Panel, Pill, Prov, Since, Tabs } from "../components/ui";
import { kfmt, PROV, since } from "../format";
import { go, useApp, useLoad, useRoute } from "../state";

type Filter = "all" | "code" | "msg";
const FILTERS: Record<Filter, (s: JobStep) => boolean> = {
  all: () => true,
  code: (s) => ["tool", "read", "write", "edit", "guard", "error"].includes(s.kind),
  msg: (s) => ["text", "thinking", "answer"].includes(s.kind),
};

/** Steps of one job: the first load, then new ones every 2 s while it runs, merged with SSE steps. */
export function useJobSteps(id: string | null) {
  const { liveSteps } = useApp();
  const [polled, setPolled] = useState<JobStep[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const lastN = useRef(0);
  useEffect(() => {
    setPolled([]);
    setError(null);
    lastN.current = 0;
    if (!id) return;
    let gone = false;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const r = await api.jobSteps(id, lastN.current);
        if (gone) return;
        if (r.steps.length) {
          lastN.current = Math.max(lastN.current, ...r.steps.map((s) => s.n));
          setPolled((p) => mergeSteps(p, r.steps));
        }
        setRunning(r.running);
        setError(null);
        if (r.running) timer = window.setTimeout(poll, 2000);
      } catch (e) {
        if (gone) return;
        setError(errorParts(e));
        timer = window.setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => {
      gone = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [id]);
  const sse = id ? liveSteps[id] : undefined;
  const steps = useMemo(() => mergeSteps(polled, sse ?? []), [polled, sse]);
  return { steps, running, error };
}

function AgentCard({ j, sel, steps }: { j: Job; sel: boolean; steps?: JobStep[] }) {
  const { projects } = useApp();
  const pname = projects.find((p) => p.id === j.project_id)?.name ?? j.project_id;
  return (
    <button type="button" className={`agent-card ${sel ? "sel" : ""}`} onClick={() => go("live", j.id)} aria-pressed={sel}>
      <div className="row" style={{ justifyContent: "space-between" }}><b>{j.agent}</b><span className="sub num"><Since from={j.started_at} /></span></div>
      <span className="sub">{pname} · node <span className="mono">{j.phase || j.step}</span>{j.ac ? ` · ${j.ac}` : ""} · <Prov p={j.provider} m={j.model} /></span>
      {sel && steps && (
        <div className="grid" style={{ gap: 3 }}>
          {steps.slice(-4).map((s) => (
            <div key={s.n} className="mini"><span className={`kind ${kindClass(s.kind)}`}>{kindLabel(s.kind)}</span><span className="sub">{(s.path || s.text || s.tool || "").split("\n")[0].slice(0, 46)}</span></div>
          ))}
        </div>
      )}
    </button>
  );
}

function Feed({ job }: { job: Job }) {
  const { toast } = useApp();
  const { steps, running, error } = useJobSteps(job.id);
  const [filter, setFilter] = useState<Filter>("all");
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLDivElement>(null);
  const shown = steps.filter(FILTERS[filter]);
  const showAll = useCallback(() => {
    setFilter("all");
    setFollow(false);
  }, []);
  const anchor = `live-${job.id}`;
  const jump = useJumpToStep(anchor, showAll);
  const isRunning = running || job.status === "running";
  useEffect(() => {
    if (follow) box.current?.lastElementChild?.scrollIntoView?.({ block: "nearest" });
  }, [shown.length, follow]);
  const tokens = job.tokens_in + job.tokens_out + Math.floor((job.tokens_cached ?? 0) / 10);
  return (
    <Panel
      title={<h2>{job.agent}{job.ac ? ` · ${job.ac}` : ""}</h2>}
      extra={<div className="row">
        {isRunning ? <Pill tone="run">working · step {steps.length}</Pill> : <Pill tone={job.status === "done" ? "ok" : "bad"}>{job.status === "done" ? "finished" : job.status}</Pill>}
        <span className="tag num">tokens {kfmt(tokens)}</span>
      </div>}
      body="grid"
    >
      <div className="grid" style={{ gap: 10 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <Tabs value={filter} onChange={setFilter} label="Show" options={[["all", "Everything"], ["code", "Code & commands"], ["msg", "Messages"]]} />
          <label className="chk"><input type="checkbox" id="follow" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow the newest step</label>
        </div>
        <FilesTouched steps={steps} onJump={jump} />
        {error && <ErrorBox error={error} />}
        <div ref={box} className="feed live-feed" aria-live="polite">
          {shown.length ? shown.map((s) => <StepView key={s.n} s={s} idPrefix={anchor} />) : <div className="empty">{isRunning ? "Waiting for the first step…" : "No steps."}</div>}
        </div>
        {!isRunning && <Outcome job={job} steps={steps} onJump={jump} />}
        {isRunning && (
          <div className="row">
            <button className="btn sm" type="button" onClick={async () => {
              try {
                await api.stopJob(job.id);
                toast("Stop sent; the thread keeps its last checkpoint.");
              } catch (e) {
                toast(errorParts(e).message);
              }
            }}>Stop</button>
          </div>
        )}
      </div>
    </Panel>
  );
}

export function LivePage({ pid }: { pid: string }) {
  const { project } = useApp();
  const { arg } = useRoute();
  const running = useLoad(`live-run:${pid}`, () => api.jobs({ project: pid, status: "running" }));
  const recent = useLoad(`live-fin:${pid}`, () => api.jobs({ project: pid, limit: 12 }));
  const [finOpen, setFinOpen] = useState(false);
  const runningList = running.data ?? [];
  const finished = (recent.data ?? []).filter((j) => j.status !== "running");
  const selId = arg ?? runningList[0]?.id ?? null;
  const selJob = runningList.find((j) => j.id === selId) ?? finished.find((j) => j.id === selId) ?? null;
  const detail = useLoad(selId && !selJob ? `job:${selId}` : null, () => api.job(selId!), { live: false });
  const job = selJob ?? detail.data;
  const { steps } = useJobSteps(selJob?.status === "running" ? selJob.id : null);

  // keep the running list fresh even when no event arrives
  useEffect(() => {
    const t = window.setInterval(() => void running.reload(), 5000);
    return () => window.clearInterval(t);
  }, [running.reload]);

  return (
    <>
      <PageHead
        title="Live agents"
        sub={`Watch each agent while it works in ${project?.name ?? pid}: what it says, which tools it calls, what code it writes, and what it returns.`}
        actions={runningList.length ? <Pill tone="run">{runningList.length} agent{runningList.length === 1 ? "" : "s"} working now</Pill> : undefined}
      />
      <Async r={running} what="Loading agents">
        {() => (
          <div className="live-grid">
            <div className="grid" style={{ alignContent: "start", gap: 10 }}>
              <span className="lab-s">Working now</span>
              {!runningList.length && <span className="sub">No agent is working in this project.</span>}
              {runningList.map((j) => <AgentCard key={j.id} j={j} sel={j.id === selId} steps={j.id === selId ? steps : undefined} />)}
              <button type="button" className="btn ghost" aria-expanded={finOpen} onClick={() => setFinOpen((o) => !o)}>
                {finOpen ? "▾" : "▸"} Finished ({finished.length})
              </button>
              {finOpen && (
                <div className="grid" style={{ gap: 4 }}>
                  {finished.map((f) => (
                    <button key={f.id} type="button" className="fin linkbtn" style={{ fontWeight: 400 }} onClick={() => go("live", f.id)}>
                      <b>{f.agent}</b>
                      <span className="sub">{f.phase || f.step}{f.ac ? ` ${f.ac}` : ""} · {PROV[f.provider] ?? f.provider} · {since(f.started_at, f.ended_at)} · {kfmt(f.tokens_in + f.tokens_out)}</span>
                      <span className={f.status === "done" ? "k-answer" : "k-guard"} style={{ fontSize: 12 }}>{f.status}</span>
                    </button>
                  ))}
                  {!finished.length && <span className="sub">Nothing finished yet.</span>}
                </div>
              )}
            </div>
            {job ? <Feed key={job.id} job={job} /> : detail.error ? <ErrorBox error={detail.error} /> : (
              <div className="panel"><div className="panel-body empty">{selId ? "Loading…" : "Pick an agent to watch. When an agent starts, its feed opens here."}</div></div>
            )}
          </div>
        )}
      </Async>
    </>
  );
}
