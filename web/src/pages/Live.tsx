// Live agents (Run): watch each agent while it works — what it says, which tools it calls, what code it writes,
// and what it returns. Steps arrive over SSE (agent.step) and by polling /api/jobs/{id}/steps. With nobody working,
// the feed shows the last agent that ran and the Finished tab opens any other.
// v0.15.2 Working now and Finished are two tabs with their counts (the chosen tab is kept in this browser), and one
// search box finds agents by name, model, provider, flow step, AC and status.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorParts, type Job, type JobStep } from "../api";
import { kindClass, mergeSteps } from "../components/StepFeed";
import { FilesTouched, kindLabel, Outcome, StepView, useJumpToStep } from "../components/StepView";
import { EmptyState } from "../components/EmptyState";
import { jobMatches, RunSearch, RunTabs, searchWords, useDebounced, useFinished, useRunTab } from "../components/RunSearch";
import { StartFlowDrawer } from "../components/StartFlow";
import { agoText } from "../components/UsageStrip";
import { ErrorBox, GoButton, Loading, PageHead, Panel, Pill, Prov, Since, Tabs } from "../components/ui";
import { kfmt, plainText, PROV, since } from "../format";
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
        <div className="grid agent-mini" style={{ gap: 3 }}>
          {steps.slice(-4).map((s) => (
            <div key={s.n} className="mini"><span className={`kind ${kindClass(s.kind)}`}>{kindLabel(s.kind)}</span><span className="sub">{s.path || plainText(s.text || s.tool || "", 60)}</span></div>
          ))}
        </div>
      )}
    </button>
  );
}

function Feed({ job, note }: { job: Job; note?: string }) {
  const { toast } = useApp();
  const { steps, running, error } = useJobSteps(job.id);
  const [filter, setFilter] = useState<Filter>("all");
  // a finished feed opens at its start (no jump down the page); a running one follows the newest step
  const [follow, setFollow] = useState(job.status === "running");
  const box = useRef<HTMLDivElement>(null);
  const shown = steps.filter(FILTERS[filter]);
  const showAll = useCallback(() => {
    setFilter("all");
    setFollow(false);
  }, []);
  const anchor = `live-${job.id}`;
  const jump = useJumpToStep(anchor, showAll);
  const isRunning = running || job.status === "running";
  // follow inside the feed's own scroll box: the page itself never jumps
  useEffect(() => {
    const b = box.current;
    if (follow && b) b.scrollTop = b.scrollHeight;
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
        {note && <p className="hint live-note">{note}</p>}
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

/** One finished call in the Recent list. */
function RecentRow({ j, sel }: { j: Job; sel: boolean }) {
  const ok = j.status === "done";
  return (
    <button type="button" className={`recent-row ${sel ? "sel" : ""}`} onClick={() => go("live", j.id)} aria-pressed={sel}>
      <span className="rr-top"><b>{j.agent}</b><span className={`rr-st ${ok ? "k-answer" : "k-guard"}`}>{ok ? "done" : j.status}</span></span>
      <span className="sub rr-where"><span className="mono">{j.phase || j.step}</span>{j.ac ? ` · ${j.ac}` : ""} · {PROV[j.provider] ?? j.provider}</span>
      <span className="hint num">{agoText(j.ended_at ?? j.started_at) || "—"} · took {since(j.started_at, j.ended_at)} · {kfmt(j.tokens_in + j.tokens_out)} tokens</span>
    </button>
  );
}

export function LivePage({ pid }: { pid: string }) {
  const { project } = useApp();
  const { arg } = useRoute();
  const [search, setSearch] = useState("");
  const running = useLoad(`live-run:${pid}`, () => api.jobs({ project: pid, status: "running" }));
  // v0.15.2 finished agents: the api searches them (the list has a limit) and counts them for the tab
  const dq = useDebounced(search.trim());
  const fin = useFinished(`live:${pid}`, dq,
    (q) => api.jobs({ project: pid, status: "finished", q: q || undefined, limit: 30 }),
    (q) => api.jobCount({ project: pid, status: "finished", q: q || undefined }));
  const [start, setStart] = useState(false);
  const runningList = running.data ?? [];
  const finished = (fin.rows ?? []).filter((j) => j.status !== "running");
  const words = searchWords(search);
  const pname = project?.name ?? pid;
  const runShown = runningList.filter((j) => jobMatches(j, words, pname));
  const finShown = finished.filter((j) => jobMatches(j, words, pname));
  // the feed opens on the last agent that ran, not on the first match of a search
  const lastRan = useRef<Job | null>(null);
  if (fin.q === "" && fin.rows) lastRan.current = finished[0] ?? null;
  // what the feed shows: the agent in the link, else the one that works now, else the last one that ran
  const selId = arg ?? runningList[0]?.id ?? (running.data ? lastRan.current?.id : undefined) ?? null;
  const selJob = runningList.find((j) => j.id === selId) ?? finished.find((j) => j.id === selId) ?? (lastRan.current?.id === selId ? lastRan.current : null);
  const detail = useLoad(selId && !selJob ? `job:${selId}` : null, () => api.job(selId!), { live: false });
  const job = selJob ?? detail.data;
  const { steps } = useJobSteps(selJob?.status === "running" ? selJob.id : null);
  const loaded = !!running.data && !!fin.rows;
  const never = loaded && fin.q === "" && !runningList.length && !finished.length;
  const [tab, setTab] = useRunTab("keel2.live.tab", !!running.data || !!running.error, runningList.length);
  const fresh = fin.q === search.trim();
  const finCount = fin.rows === null ? null : fresh && fin.total !== null ? fin.total : finShown.length;

  // keep both lists fresh even when no event arrives
  useEffect(() => {
    const t = window.setInterval(() => {
      void running.reload();
      if (runningList.length) fin.reload();
    }, 5000);
    return () => window.clearInterval(t);
  }, [running.reload, fin.reload, runningList.length]);

  const note = !arg && job && job.status !== "running" && running.data && !runningList.length
    ? "No agent is working right now. This is the last one that ran." : undefined;

  return (
    <>
      <PageHead
        title="Live agents"
        sub={`Watch each agent while it works in ${project?.name ?? pid}: what it says, which tools it calls, what code it writes, and what it returns.`}
        actions={running.data ? (runningList.length
          ? <Pill tone="run">{runningList.length} agent{runningList.length === 1 ? "" : "s"} working now</Pill>
          : <Pill tone="idle">no agent working</Pill>) : undefined}
      />
      {never ? (
        <div className="panel">
          <EmptyState title="No agent has run in this project yet"
            action={<><button className="btn primary" type="button" onClick={() => setStart(true)}>Start a flow</button><GoButton to="flow" className="btn">Open the flow</GoButton></>}>
            Start a flow. While an agent works, you see here what it says, the tools it calls and the code it writes.
          </EmptyState>
        </div>
      ) : (
        <div className="live-grid">
          <div className="live-list live-tabbed">
            <div className="live-bar">
              <RunTabs value={tab ?? "running"} onChange={setTab} label="Working or finished" first="Working now" running={running.data ? runShown.length : null} finished={finCount} />
              <RunSearch value={search} onChange={setSearch} label="Search agents" placeholder="Search agent, model, step, AC, status" />
            </div>
            {tab === "finished" ? (
              <section aria-label="Finished" className="live-sec">
                {fin.error && !fin.rows ? <ErrorBox error={fin.error} onRetry={fin.reload} />
                  : !fin.rows ? <Loading what="Loading finished agents" />
                    : !finShown.length ? <p className="sub live-idle">{words.length ? "No finished agent matches this search." : "Nothing finished yet."}</p>
                      : <div className="recent-list">{finShown.map((f) => <RecentRow key={f.id} j={f} sel={f.id === selId} />)}</div>}
                {fresh && finCount !== null && finCount > finShown.length && finShown.length > 0 && (
                  <p className="hint">The newest {finShown.length} of {finCount}. Search to find an older one.</p>
                )}
                <GoButton to="jobs" className="btn sm ghost">All calls in Jobs</GoButton>
              </section>
            ) : (
              <section aria-label="Working now" className="live-sec">
                {running.error ? <ErrorBox error={running.error} onRetry={() => void running.reload()} />
                  : !running.data || !tab ? <Loading what="Loading agents" />
                    : !runShown.length ? (
                      <p className="sub live-idle">
                        {words.length ? "No working agent matches this search." : "No agent is working in this project. When a flow reaches an agent step, it shows up here."}
                      </p>
                    ) : runShown.map((j) => <AgentCard key={j.id} j={j} sel={j.id === selId} steps={j.id === selId ? steps : undefined} />)}
              </section>
            )}
          </div>
          {job ? <Feed key={job.id} job={job} note={note} /> : detail.error ? <ErrorBox error={detail.error} /> : (
            <div className="panel live-wait"><Loading what={selId ? "Loading the feed" : "Loading agents"} /></div>
          )}
        </div>
      )}
      {start && <StartFlowDrawer onClose={() => setStart(false)} />}
    </>
  );
}
