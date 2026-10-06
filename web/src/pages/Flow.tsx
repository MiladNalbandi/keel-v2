// Flow (Run): a summary bar (status, tokens against the cap, cost, time, Stop), the running thread as live blocks
// (each with its state and real tokens; the gate that waits for you sits under its block with its buttons), a side
// panel (what happens now and the running agent's steps, what the clicked step does, checkpoints with Rewind), then
// the budget, the acceptance criteria, what blocks shipping and keel's state. The init workflow adds its ladder and
// knowledge build.

import { useMemo, useRef, useState } from "react";
import {
  api, errorParts, type Blocker, type Checkpoint, type EngineEvent, type FlowView, type Job, type LadderRung, type Memory, type ThreadState, type Workflow,
} from "../api";
import { CodeBlock, FoldedText } from "../components/Code";
import { answersOf, ClarifyForm, type ClarifyAnswers } from "../components/ClarifyForm";
import { Markdown } from "../components/Markdown";
import { eventLine } from "../components/events";
import { EmptyState } from "../components/EmptyState";
import { FlowBoardView, showBoard } from "../components/FlowBoard";
import { StartFlowDrawer } from "../components/StartFlow";
import { autoLines, RunModeNote, RunModeSwitch } from "../components/RunMode";
import { AcChips, Blocks, BlocksLegend, StepTable, useMapView, ViewToggle, type BlocksHandle } from "../components/Blocks";
import { phaseTitle } from "../components/flowmap";
import { Graph, GraphLegend } from "../components/Graph";
import { infoRequest, StepInfoBody, StepInfoDrawer, useStepInfo } from "../components/StepInfo";
import { StepView } from "../components/StepView";
import { Zoom } from "../components/Zoom";
import { useJobSteps } from "./Live";
import { useWide } from "../components/useWide";
import { Async, Confirm, ErrorBox, GoButton, Loading, PageHead, Panel, Pill, Prov, Tabs, type PillTone } from "../components/ui";
import { acLabel, clock, kfmt, usd } from "../format";
import { useApp, useLoad, useRoute } from "../state";

const AC_TONE: Record<string, PillTone> = { done: "ok", green: "ok", red: "bad", todo: "idle", "already-met": "met" };

export function FlowPage({ pid }: { pid: string }) {
  // v0.7.x: #/flow/<thread> opens one flow (a flow in a worktree); #/flow is the project folder's flow
  const { arg: tid } = useRoute();
  const flow = useLoad(tid ? `flow:${pid}:${tid}` : `flow:${pid}`, () => (tid ? api.flowOf(pid, tid) : api.flow(pid)));
  const board = useLoad(`flows:${pid}`, () => api.flowBoard(pid));
  const { project } = useApp();
  const [start, setStart] = useState(false);
  return (
    <>
      {showBoard(board.data) && (
        <FlowBoardView board={board.data!} selected={flow.data?.thread?.thread_id ?? null} onChanged={() => void board.reload()} />
      )}
      <Async r={flow} what="Loading the flow">
        {(f) => !f.thread || !f.workflow ? (
          <>
            <PageHead title={project?.name ?? pid} sub={showBoard(board.data) ? "No flow is running in the project folder." : "No flow is running in this project."} />
            <div className="panel">
              <EmptyState title="Nothing running here yet"
                action={<button className="btn primary" type="button" id="startFlow2" onClick={() => setStart(true)}>Start a flow</button>}>
                Start a feature, change, fix or hunt. You will see the estimate before it starts.
              </EmptyState>
            </div>
          </>
        ) : isInit(f.workflow) ? (
          <InitFlow pid={pid} f={f as Required<FlowView> & { thread: ThreadState; workflow: Workflow }} onStart={() => setStart(true)} reload={flow.reload} />
        ) : (
          <ThreadView pid={pid} thread={f.thread} workflow={f.workflow} reload={flow.reload} onStart={() => setStart(true)} />
        )}
      </Async>
      {start && <StartFlowDrawer onClose={() => setStart(false)} />}
    </>
  );
}

const isInit = (w: Workflow) => w.id === "init" || w.based_on === "keel/init" || w.based_on === "init";

function useThreadBits(pid: string, thread: ThreadState, workflow: Workflow) {
  const history = useLoad(`hist:${thread.thread_id}`, () => api.history(thread.thread_id));
  const n = thread.acs.length || 3;
  const est = useLoad(`est:${pid}:${workflow.id}:${n}`, () => api.estimate(pid, workflow.id, n), { live: false });
  const jobs = useLoad(`jobs-run:${pid}`, () => api.jobs({ project: pid, status: "running" }));
  const job = jobs.data?.find((j) => j.thread_id === thread.thread_id) ?? null;
  // Every job of this project (done ones too): the tokens each step really used.
  const all = useLoad(`jobs-all:${pid}`, () => api.jobs({ project: pid }));
  const actual = useMemo(() => {
    const m: Record<string, number> = {};
    (all.data ?? []).filter((j) => j.thread_id === thread.thread_id).forEach((j) => {
      const s = workflow.steps.find((x) => x.id === j.step.replace(/__fix$/, "") || x.name === j.step);
      if (s) m[s.id] = (m[s.id] ?? 0) + j.tokens_in + j.tokens_out + Math.floor((j.tokens_cached ?? 0) / 10);
    });
    return Object.keys(m).length ? m : undefined;
  }, [all.data, thread.thread_id, workflow]);
  const visited = useMemo(() => {
    const ids = new Set<string>();
    (history.data ?? []).forEach((c) => {
      const s = workflow.steps.find((x) => x.id === c.step.replace(/__fix$/, "") || x.name === c.step);
      if (s) ids.add(s.id);
    });
    return ids;
  }, [history.data, workflow]);
  const started = useMemo(() => {
    const times = [...(history.data ?? []).map((c) => c.at), ...(all.data ?? []).filter((j) => j.thread_id === thread.thread_id).map((j) => j.started_at)]
      .map((t) => Date.parse(t)).filter((t) => !Number.isNaN(t));
    return times.length ? Math.min(...times) : null;
  }, [history.data, all.data, thread.thread_id]);
  return { history, est, job, jobs, actual, visited, started };
}

/** "1h 12m", "12m", "40s". */
function elapsed(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m` : `${s}s`;
}

const STATUS_PILL: Record<string, [PillTone, string]> = {
  running: ["run", "running"], waiting: ["warn", "waiting for you"], done: ["ok", "done"], failed: ["bad", "failed"], stopped: ["idle", "stopped"],
};

/** The flow on one line: title, workflow, phase, status; tokens used against the cap, cost, time; Stop. */
function FlowBar({ thread, workflow, estimate, started, onStart, onJump, extra }: {
  thread: ThreadState; workflow: Workflow; estimate: number | null; started: number | null; onStart: () => void; onJump?: () => void; extra?: React.ReactNode;
}) {
  const { project, toast } = useApp();
  const [busy, setBusy] = useState(false);
  const live = thread.status === "running" || thread.status === "waiting";
  const u = thread.usage;
  const used = u.tokens_in + u.tokens_out + Math.floor((u.tokens_cached ?? 0) / 10);
  const cap = u.cap_tokens || Math.max(used, estimate ?? 0, 1);
  const [tone, label] = STATUS_PILL[thread.status] ?? ["idle", thread.status];
  const end = live ? Date.now() : Date.parse(thread.updated_at);
  return (
    <header className="fl-bar" role="region" aria-label="This flow">
      <div className="fl-bar-main">
        <div className="crumb">{project?.name ?? thread.project_id} › Run</div>
        <h1>{thread.title || workflow.name}</h1>
        <div className="fl-facts">
          <span><b>{workflow.name}</b> v{workflow.version}</span>
          <span>phase <b>{phaseTitle(thread.phase)}</b></span>
          <Pill tone={tone}>{label}</Pill>
          <span>branch <span className="mono">{project?.branch ?? "—"}</span></span>
          <span className="sub fl-tid">thread <span className="mono">{thread.thread_id}</span></span>
        </div>
      </div>
      <div className="fl-stats">
        <div className="fl-stat fl-stat-tok">
          <span className="fl-stat-l">tokens</span>
          <span className="fl-stat-v"><b>{kfmt(used)}</b> <span className="sub">of {kfmt(u.cap_tokens || cap)} cap<span className="fl-est">{estimate !== null ? ` · ≈ ${kfmt(estimate)} expected` : ""}</span></span></span>
          <span className="sx-bar fl-meter" aria-hidden="true"><i style={{ width: `${Math.min(100, (used / cap) * 100)}%`, background: used > cap * 0.8 ? "var(--warn)" : undefined }} />
            {estimate !== null && <em style={{ left: `${Math.min(100, (estimate / cap) * 100)}%` }} />}</span>
        </div>
        <div className="fl-stat"><span className="fl-stat-l">cost</span><span className="fl-stat-v"><b>{usd(u.cost_usd)}</b></span></div>
        <div className="fl-stat"><span className="fl-stat-l">time</span><span className="fl-stat-v"><b>{started ? elapsed(end - started) : "—"}</b></span></div>
      </div>
      <div className="actions">
        {extra}
        {onJump && live && <button className="btn" type="button" onClick={onJump} aria-label="Jump to current">
          <span className="fl-long">Jump to current</span><span className="fl-short" aria-hidden="true">Jump</span></button>}
        {live && <RunModeSwitch compact pid={thread.project_id} threadId={thread.thread_id} mode={thread.run_mode} />}
        {live ? (
          <button className="btn" type="button" disabled={busy} onClick={async () => {
            setBusy(true);
            try {
              await api.stopThread(thread.thread_id);
              toast("Stop sent; the thread keeps its last checkpoint.");
            } catch (e) {
              toast(errorParts(e).message);
            } finally {
              setBusy(false);
            }
          }}>Stop flow</button>
        ) : <button className="btn primary" type="button" onClick={onStart}>Start a flow</button>}
        {live && <button className="btn ghost" type="button" onClick={onStart}
          title="It runs next to this one, in a worktree of its own">Start another flow</button>}
      </div>
    </header>
  );
}

type FlowSide = "now" | "step" | "checkpoints";

/** The live canvas: the workflow's blocks with each one's state and tokens; the waiting gate's card sits under its
 * block, so you act where the flow stopped. */
function LiveCanvas({ thread, workflow, tokens, actual, visited, waiting, canvas, onOpen, selected }: {
  thread: ThreadState; workflow: Workflow; tokens?: Record<string, number>; actual?: Record<string, number>; visited: Set<string>;
  waiting: React.ReactNode; canvas: React.RefObject<BlocksHandle>; onOpen: (id: string) => void; selected: string | null;
}) {
  const [view, setView] = useMapView("flow");
  const waitAt = thread.status === "waiting" ? (thread.waiting?.step ?? thread.current ?? "").replace(/__fix$/, "") : null;
  const onCanvas = !!waitAt && workflow.steps.some((s) => s.id === waitAt);
  return (
    <section className="panel fl-canvas" aria-label={`${workflow.name}: the steps`}>
      <div className="panel-head">
        <h2>Steps</h2>
        <ViewToggle value={view} onChange={setView} />
      </div>
      {waiting && !onCanvas && <div className="panel-body" style={{ paddingBottom: 0 }}>{waiting}</div>}
      <div className="panel-body sx-scroll fl-scroll">
        {view === "blocks" ? (
          <>
            <Blocks ref={canvas} steps={workflow.steps} tokens={thread.status === "done" ? undefined : tokens} actual={actual} acCount={thread.acs.length || 3}
              current={thread.current} status={thread.status} acs={thread.acs} currentAc={thread.ac} visited={visited} selected={selected}
              onOpenStep={onOpen} autoScroll inline={(id) => (onCanvas && id === waitAt ? <div className="sx-inline">{waiting}</div> : null)}
              label={`Steps of ${workflow.name}: ${workflow.steps.length} steps`} />
            {thread.acs.length > 0 && !workflow.steps.some((s) => s.per_ac) ? <AcChips acs={thread.acs} current={thread.ac} /> : null}
            <BlocksLegend live />
          </>
        ) : view === "table" ? (
          <>
            {waiting && onCanvas && <div style={{ marginBottom: 12 }}>{waiting}</div>}
            <StepTable steps={workflow.steps} tokens={tokens} actual={actual} onOpenStep={onOpen} selected={selected} acCount={thread.acs.length || 3} />
          </>
        ) : (
          <>
            {waiting && onCanvas && <div style={{ marginBottom: 12 }}>{waiting}</div>}
            <Zoom id="flow">
              <Graph steps={workflow.steps} current={thread.current} status={thread.status} tokens={thread.status === "done" ? undefined : tokens}
                acs={thread.acs} currentAc={thread.ac} onSelect={onOpen} />
            </Zoom>
            <AcChips acs={thread.acs} current={thread.ac} />
            <GraphLegend />
          </>
        )}
      </div>
    </section>
  );
}

/** The right column: what is happening now (and the running agent's last steps), the selected step, checkpoints. */
function FlowSidePanel({ pid, thread, workflow, job, side, setSide, selected, onClose, onDone, history, onJump }: {
  pid: string; thread: ThreadState; workflow: Workflow; job: Job | null; side: FlowSide; setSide: (s: FlowSide) => void;
  selected: string | null; onClose: () => void; onDone: () => Promise<void>; history: ReturnType<typeof useThreadBits>["history"]; onJump: () => void;
}) {
  const name = selected ? workflow.steps.find((s) => s.id === selected)?.name ?? selected : null;
  return (
    <aside className="fl-side" aria-label="Now, the selected step and checkpoints">
      <Tabs value={side} onChange={setSide} label="Flow details" options={[["now", "Now"], ["step", name ? `Step: ${name}` : "Step"], ["checkpoints", "Checkpoints"]]} />
      {side === "now" ? (
        <div className="grid" style={{ gap: 12 }}>
          {thread.status === "waiting" && thread.waiting ? (
            <div className="interrupt fl-waits">
              <b><Pill tone="warn">waiting for you</Pill> {thread.waiting.title}</b>
              <span className="sub">The flow stopped here. Your answer goes in the card under its block.</span>
              <div className="row"><button className="btn warn" type="button" onClick={onJump}>Go to it</button></div>
            </div>
          ) : <StatusCard pid={pid} thread={thread} workflow={workflow} job={job} onDone={onDone} />}
          {job && <JobFeed job={job} />}
          <EventsPanel pid={pid} thread={thread} history={history.error ? [] : history.data} onAll={() => setSide("checkpoints")} />
        </div>
      ) : side === "step" ? (
        selected ? <StepInfoPanel pid={pid} workflow={workflow} stepId={selected} threadId={thread.thread_id} onClose={onClose} />
          : <div className="panel"><div className="panel-body wb-hint"><b>Select a block to see what it does.</b><span className="sub">Its task, the rules of its phase, where it goes next and what it did in this flow.</span></div></div>
      ) : (
        <CheckpointsPanel thread={thread} history={history.data} error={history.error} onRewound={onDone} />
      )}
    </aside>
  );
}

/** "What this step does" in the side panel (the drawer's content), for the workflow the page draws and this thread. */
function StepInfoPanel({ pid, workflow, stepId, threadId, onClose }: { pid: string; workflow: Workflow; stepId: string; threadId: string; onClose: () => void }) {
  const { x, error } = useStepInfo(pid, infoRequest(stepId, workflow, threadId));
  const name = x?.name ?? workflow.steps.find((s) => s.id === stepId)?.name ?? stepId;
  return (
    <section className="panel" aria-label={`What ${name} does`}>
      <div className="panel-head"><h2>What {name} does</h2><button className="btn sm ghost" type="button" onClick={onClose}>Close</button></div>
      <div className="panel-body">{error ? <ErrorBox error={error} /> : !x ? <Loading what="Reading the step" /> : <StepInfoBody x={x} />}</div>
    </section>
  );
}

/** The running agent's newest steps, with a link to watch it live. */
function JobFeed({ job }: { job: Job }) {
  const { steps } = useJobSteps(job.id);
  const last = steps.slice(-6);
  return (
    <section className="panel" aria-label={`${job.agent} works`}>
      <div className="panel-head"><h2>{job.agent} works</h2><GoButton to="live" arg={job.id} className="btn sm">Watch live</GoButton></div>
      <div className="panel-body feed fl-feed" aria-live="polite">
        {last.length ? last.map((st) => <StepView key={st.n} s={st} idPrefix={`fl-${job.id}`} />) : <span className="sub">Waiting for the first step…</span>}
      </div>
    </section>
  );
}

function ThreadView({ pid, thread, workflow, reload, onStart }: {
  pid: string; thread: ThreadState; workflow: Workflow; reload: () => Promise<void>; onStart: () => void;
}) {
  const { history, est, job, actual, visited, started } = useThreadBits(pid, thread, workflow);
  const tokens = useMemo(() => perStep(workflow, est.data?.per_step), [workflow, est.data]);
  const [sel, setSel] = useState<string | null>(null);
  const [side, setSide] = useState<FlowSide>("now");
  const wide = useWide();
  const canvas = useRef<BlocksHandle>(null);
  const cur = (thread.waiting?.step ?? thread.current ?? "").replace(/__fix$/, "");
  // "Go to it" / "Jump to current": open the folds around the block, then show the answer card under it (when the flow
  // waits) and put the focus on its first button, so you can answer right away.
  const jump = () => {
    canvas.current?.reveal(cur, true);
    if (thread.status !== "waiting") return;
    window.setTimeout(() => {
      const card = document.querySelector<HTMLElement>('.fl [aria-label="Gate waits for you"]');
      if (!card) return;
      card.scrollIntoView?.({ block: "center", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      (card.querySelector<HTMLElement>("button") ?? card.querySelector<HTMLElement>("textarea, input"))?.focus({ preventScroll: true });
    }, 120);
  };
  const open = (id: string) => { setSel(id); setSide("step"); };
  const waiting = thread.status === "waiting" && thread.waiting ? <GateCard thread={thread} workflow={workflow} onDone={reload} /> : null;
  return (
    <>
      <FlowBar thread={thread} workflow={workflow} estimate={est.data?.tokens ?? null} started={started} onStart={onStart} onJump={jump} />
      {!wide && sel && <StepInfoDrawer pid={pid} workflow={workflow} stepId={sel} threadId={thread.thread_id} onClose={() => setSel(null)} />}
      <div className="fl">
        <LiveCanvas thread={thread} workflow={workflow} tokens={tokens} actual={actual} visited={visited} waiting={waiting} canvas={canvas} onOpen={open} selected={sel} />
        <FlowSidePanel pid={pid} thread={thread} workflow={workflow} job={job} side={wide ? side : side === "step" ? "now" : side} setSide={setSide}
          selected={sel} onClose={() => { setSel(null); setSide("now"); }} onDone={reload} history={history} onJump={jump} />
      </div>
      <div className="grid fl-more">
        <BudgetMeter thread={thread} estimate={est.data?.tokens ?? null} />
        <AcsPanel thread={thread} />
        <BeforeShipPanel blockers={thread.blockers} />
      </div>
    </>
  );
}

function perStep(w: Workflow, per?: { step: string; tokens: number }[]) {
  if (!per) return undefined;
  const m: Record<string, number> = {};
  per.forEach((p) => {
    const s = w.steps.find((x) => x.id === p.step || x.name === p.step);
    if (s) m[s.id] = (m[s.id] ?? 0) + p.tokens;
  });
  return m;
}

/** The ◆ card when the thread waits, the running card while an agent works, or how it ended. */
export function StatusCard({ pid, thread, workflow, job, onDone }: {
  pid: string; thread: ThreadState; workflow: Workflow; job: Job | null; onDone: () => Promise<void>;
}) {
  const step = workflow.steps.find((s) => s.id === (thread.current ?? "").replace(/__fix$/, ""));
  if (thread.status === "waiting" && thread.waiting) return <GateCard thread={thread} workflow={workflow} onDone={onDone} />;
  if (thread.status === "running") {
    return (
      <div className="running-card">
        <b><Pill tone="run">running</Pill> node <span className="mono">{step?.name ?? thread.current ?? thread.phase}</span>{thread.ac ? ` — ${thread.ac}` : ""}</b>
        <span className="sub">
          {job ? <>{job.agent} · <Prov p={job.provider} m={job.model} /> · {job.steps_count} steps · keel calls {job.mcp_calls}</>
            : step?.agent ? <>{step.agent}{step.tools?.length ? ` · tools: ${step.tools.join(", ")}` : ""}</>
              : step?.kind === "code" ? "plain code, no LLM" : "working"}
        </span>
        <div className="row">
          <GoButton to="live" arg={job?.id}>Watch live</GoButton>
          <GoButton to="jobs" arg={job?.id} className="btn sm ghost">Open job</GoButton>
        </div>
      </div>
    );
  }
  void pid;
  const tone: PillTone = thread.status === "done" ? "ok" : thread.status === "failed" ? "bad" : "idle";
  return (
    <div className={thread.status === "failed" ? "errbox" : "running-card"} style={thread.status === "done" ? { borderColor: "var(--ok)", background: "var(--ok-soft)" } : undefined}>
      <b><Pill tone={tone}>{thread.status}</Pill> {thread.status === "done" ? "The flow is done." : thread.status === "failed" ? "The flow failed." : "The flow was stopped."}</b>
      {thread.error && <span className="sub">{thread.error}</span>}
      {thread.status !== "done" && <span className="hint">Rewind to a checkpoint below to try again from there.</span>}
    </div>
  );
}

type GateLabels = {
  approve: string; reject: string; needWhy: boolean; whyLabel?: string; explain?: string;
  approved: string; rejected: string; special?: boolean;
};

/** Button labels per waiting kind, so the choice is clear without reading the code. */
export function gateLabels(w: NonNullable<ThreadState["waiting"]>, acId?: string, backName?: string): GateLabels {
  const t = w.title.toLowerCase();
  if (w.kind === "usage") {
    return {
      approve: "Continue anyway", reject: "Stop here", needWhy: false,
      explain: "A plan window of this provider is nearly used up. Continue uses it anyway (the provider may refuse when it is full), wait sleeps until it resets, the cheaper model is the one in Settings.",
      approved: "The flow goes on.", rejected: "Stopped.", special: true,
    };
  }
  if (w.kind === "budget") {
    return { approve: "Continue over the cap", reject: "Stop here", needWhy: false, approved: "The flow continues over its cap.", rejected: "Stopped.", special: true };
  }
  if (w.kind === "fix" && /dependenc/.test(t)) {
    return {
      approve: "Approve the new dependency", reject: "Refuse it", needWhy: true, whyLabel: "Why (needed to refuse)",
      explain: "The commit adds a dependency to a manifest. keel asks before new code from outside comes in.",
      approved: "Dependency approved. The commit goes on.", rejected: "Refused. The agent gets your reason and tries without it.", special: true,
    };
  }
  if (w.kind === "fix") {
    // The engine names the buttons of its newer questions (a staged secret, a readonly commit): use its words.
    return {
      approve: w.labels?.approve ?? "Approve fix", reject: w.labels?.reject ?? "Reject fix", needWhy: true,
      whyLabel: `Why (needed for “${w.labels?.reject ?? "Reject fix"}”)`,
      approved: w.labels?.approve ? "Done. The flow moves on." : "Fix approved.",
      rejected: w.labels?.reject ? "Done, with your reason." : "Fix rejected. The agent gets your reason.", special: true,
    };
  }
  if (w.kind === "clarify") {
    return {
      approve: w.labels?.approve ?? "Send my answers", reject: "Send back", needWhy: false,
      whyLabel: "Anything else the explorer should know (optional)",
      explain: "The explorer could not decide these from the code. One click each: the recommended option comes first, and your own words win over a click.",
      approved: "Answers sent. The explorer continues with them.", rejected: "Sent back.", special: true,
    };
  }
  if (w.kind === "gate" && /blocking finding/.test(t)) {
    return {
      approve: w.labels?.approve ?? "Fix them", reject: w.labels?.reject ?? "Go on anyway", needWhy: true, whyLabel: "Why (needed to go on anyway)",
      explain: "A reviewer marked these findings as blocking. The implementer can fix them, then the review runs again.",
      approved: "The implementer fixes the findings; the tests run and the review runs again.",
      rejected: "Accepted with your reason. The flow goes on.",
    };
  }
  if (w.labels?.approve || w.labels?.reject) {
    return {
      approve: w.labels.approve ?? "Approve", reject: w.labels.reject ?? "Send back", needWhy: true,
      whyLabel: /question/.test(t) ? `Your answers (needed for “${w.labels.reject ?? "Send back"}”)` : `Why (needed for “${w.labels.reject ?? "Send back"}”)`,
      approved: "Done. The flow moves on.", rejected: "Sent back with your reason.",
    };
  }
  if (w.kind === "gate" && /already passes/.test(t)) {
    return {
      approve: "Mark as already met", reject: "Send back for a stricter test", needWhy: true, whyLabel: "Why (needed to send back)",
      explain: "The new test passes without new code: an earlier criterion's code already covers this one.",
      approved: "Marked as already met. The flow goes on with the next AC.",
      rejected: "Sent back. The test author writes a stricter test with your reason.",
    };
  }
  if (/escalat/.test(t)) {
    return {
      approve: "Yes, switch to a feature flow", reject: "No, keep the change flow", needWhy: false, whyLabel: "Note (optional)",
      explain: "This change touches a contract, a migration, auth code, or is large. A feature flow adds a spec and an AC gate.",
      approved: "Escalated. The work continues as a feature flow.", rejected: "Kept as a change flow.", special: true,
    };
  }
  return {
    approve: `Approve${acId ? " " + acId : ""}`, reject: `Send back${backName ? " to " + backName : ""}`, needWhy: true, whyLabel: "Why (needed to send back)",
    approved: "Approved. The flow moves on.", rejected: "Sent back. The agent gets your reason in its prompt.",
  };
}

const GATE_LABEL: Record<string, string> = { release: "release", coverage: "coverage", deps: "dependencies", knowledge: "knowledge", secrets: "secrets" };

export function BeforeShipPanel({ blockers }: { blockers?: Blocker[] }) {
  return (
    <Panel title="Before ship" extra={<span className="hint">checked at push_check and after every commit</span>}>
      {!blockers ? <span className="sub">Not checked yet. It runs at push_check.</span> : !blockers.length ? (
        <div className="row"><Pill tone="ok">ready</Pill><span className="sub">Nothing blocks shipping.</span></div>
      ) : (
        <div className="blockers" aria-label="What blocks shipping">
          {blockers.map((b, i) => (
            <div key={i} className="blocker">
              <div className="row"><Pill tone={b.gate === "secrets" ? "bad" : "warn"}>{GATE_LABEL[b.gate] ?? b.gate}</Pill><span>{b.why}</span></div>
              {b.fix && <span className="sub">Fix: {b.fix}</span>}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function GateCard({ thread, workflow, onDone }: { thread: ThreadState; workflow: Workflow; onDone: () => Promise<void> }) {
  const { toast, reloadProjects } = useApp();
  const w = thread.waiting!;
  const [why, setWhy] = useState("");
  const [picked, setPicked] = useState<ClarifyAnswers>({});
  const [typed, setTyped] = useState<ClarifyAnswers>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const step = workflow.steps.find((s) => s.id === w.step.replace(/__fix$/, "")) ?? workflow.steps.find((s) => s.id === thread.current);
  const back = step?.back ? workflow.steps.find((s) => s.id === step.back) : undefined;
  const ac = thread.acs.find((a) => a.id === thread.ac);
  const labels = gateLabels(w, ac?.id, back?.name);

  const showAc = w.kind === "gate" && !labels.special;

  const decide = async (decision: "approve" | "reject", extra?: Record<string, unknown>) => {
    if (decision === "reject" && labels.needWhy && !why.trim()) {
      document.getElementById("why")?.focus();
      toast("Write why first, so the agent knows what to change.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const answers = w.kind === "clarify" && w.questions ? { answers: answersOf(w.questions, picked, typed) } : extra;
      await api.resume(thread.thread_id, decision, why.trim() || undefined, answers);
      setPicked({});
      setTyped({});
      toast(decision === "approve" ? labels.approved : labels.rejected);
      setWhy("");
      await Promise.all([onDone(), reloadProjects()]);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="interrupt" role="region" aria-label="Gate waits for you">
      <h3><Pill tone="warn">◆ waits for you</Pill> {w.title}{showAc && ac ? ` — ${ac.id} [${ac.layer}] ${ac.title}` : ""}</h3>
      <RunModeNote mode={thread.run_mode} kind={w.kind === "fix" && /dependenc/i.test(w.title) ? "dependency" : w.kind} title={w.title}
        auto={{ count: autoLines(thread.gate_log).length, last: autoLines(thread.gate_log).at(-1) }} />
      {labels.explain && <p className="sub" style={{ margin: 0 }}>{labels.explain}</p>}
      {w.kind === "clarify" && w.questions
        ? <ClarifyForm questions={w.questions} picked={picked} typed={typed}
            onPick={(id, label) => setPicked((p) => ({ ...p, [id]: label }))} onType={(id, text) => setTyped((p) => ({ ...p, [id]: text }))} />
        : w.detail && <GateDetail text={w.detail} />}
      {labels.whyLabel && (
        <div className="field">
          <label htmlFor="why">{labels.whyLabel}</label>
          <textarea id="why" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. also check the error message text in the test" />
        </div>
      )}
      {w.kind === "usage" && w.choices ? (
        <div className="row">
          {w.choices.includes("continue") && <button className="btn warn" type="button" id="approve" disabled={busy} onClick={() => decide("approve", { choice: "continue" })}>Continue anyway</button>}
          {w.choices.includes("wait") && <button className="btn" type="button" disabled={busy} onClick={() => decide("approve", { choice: "wait" })}>Wait for the reset</button>}
          {w.choices.includes("cheaper") && <button className="btn" type="button" disabled={busy} onClick={() => decide("approve", { choice: "cheaper" })}>Use the cheaper model</button>}
          {w.choices.includes("stop") && <button className="btn" type="button" id="sendback" disabled={busy} onClick={() => decide("reject")}>Stop here</button>}
        </div>
      ) : w.kind === "gate" && w.choices?.length ? (
        <div className="row" aria-label="Choose one">
          {w.choices.map((c, n) => (
            <button key={c} className={n === 0 ? "btn warn" : "btn"} type="button" id={n === 0 ? "approve" : undefined} disabled={busy}
              onClick={() => decide("approve", { choice: c })}>{c}</button>))}
          {w.options.includes("reject") && <button className="btn" type="button" id="sendback" disabled={busy} onClick={() => decide("reject")}>{labels.reject}</button>}
        </div>
      ) : <div className="row">
        {w.options.includes("approve") && <button className="btn warn" type="button" id="approve" disabled={busy} onClick={() => decide("approve")}>{labels.approve}</button>}
        {w.options.includes("reject") && <button className="btn" type="button" id="sendback" disabled={busy} onClick={() => decide("reject")}>{labels.reject}</button>}
        <span className="hint">Resumes the thread with <span className="mono">Command(resume={"{decision, why}"})</span>.</span>
      </div>}
      {err && <ErrorBox error={err} />}
    </div>
  );
}

export function BudgetMeter({ thread, estimate, title = "Budget for this flow" }: { thread: ThreadState; estimate: number | null; title?: string }) {
  const u = thread.usage;
  // cache reads count a tenth, like their price (same rule as the engine's caps)
  const used = u.tokens_in + u.tokens_out + Math.floor((u.tokens_cached ?? 0) / 10);
  const cap = u.cap_tokens || Math.max(used, estimate ?? 0, 1);
  const pct = (n: number) => `${Math.min(100, (n / cap) * 100)}%`;
  return (
    <Panel title={title} extra={<span className="hint">cap {kfmt(u.cap_tokens)}</span>} body="grid">
      <div className="grid" style={{ gap: 8 }}>
        <div className="meter2" role="meter" aria-label="Tokens used of the cap" aria-valuemin={0} aria-valuemax={cap} aria-valuenow={used}>
          <i className="used" style={{ width: pct(used), background: used > cap * 0.8 ? "var(--warn)" : undefined }} />
          {estimate !== null && <i className="est" style={{ left: pct(estimate) }} title="estimate" />}
        </div>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="num"><b>{kfmt(used)}</b> used</span>
          <span className="num sub">estimate {estimate === null ? "…" : kfmt(estimate)}</span>
          <span className="num sub">cap {kfmt(u.cap_tokens)}</span>
        </div>
        <span className="hint">Spent so far: {usd(u.cost_usd)} on API, {u.premium_requests} Copilot premium requests.</span>
      </div>
    </Panel>
  );
}

function CheckpointsPanel({ thread, history, error, onRewound }: {
  thread: ThreadState; history: Checkpoint[] | null; error: { message: string; hint?: string } | null; onRewound: () => Promise<void>;
}) {
  const { toast } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const rewind = async (cp: Checkpoint) => {
    setBusy(cp.id);
    try {
      await api.rewind(thread.thread_id, cp.id);
      toast(`Rewound to checkpoint #${cp.n}. A new branch starts here; the old one stays in history.`);
      await onRewound();
    } catch (e) {
      const p = errorParts(e);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <Panel title="Checkpoints" extra={<span className="hint">Every step is saved. Rewind starts a new branch from that point.</span>}>
      {error ? <ErrorBox error={error} /> : !history ? <div className="empty loading">Loading…</div> : !history.length ? <div className="empty">No checkpoint yet.</div> : (
        <ul className="timeline">
          {history.map((c, i) => (
            <li key={c.id} className={i === 0 ? "head" : ""}>
              <span className="cp">#{c.n}</span>
              <span className="t">{clock(c.at)}</span>
              <span><span className="mono">{c.step}</span> <span className="sub">{c.note}</span></span>
              {i === 0 ? <span className="tag">now</span> : (
                <button className="btn sm ghost" type="button" disabled={busy !== null} onClick={() => rewind(c)}>{busy === c.id ? "Rewinding…" : "Rewind here"}</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function AcsPanel({ thread }: { thread: ThreadState }) {
  return (
    <Panel title="Acceptance criteria">
      {!thread.acs.length ? <span className="sub">The spec step writes them. They show here after it.</span> : (
        <div className="acs">
          {thread.acs.map((a) => (
            <div key={a.id} className="ac">
              <div className="row" style={{ justifyContent: "space-between" }}><b>{a.id}</b><Pill tone={AC_TONE[a.status] ?? "idle"}>{acLabel(a.status)}</Pill></div>
              <span className="sub">[{a.layer}] {a.title}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

/** How many saved steps the Events panel shows under the live events. */
const EARLIER = 8;

/** What happened in this flow, newest first: the events of this visit, then the saved steps (checkpoints) before them,
 * so a flow that waits still says how it got here. */
export function EventsPanel({ pid, thread, history, onAll }: {
  pid: string; thread: ThreadState; history?: Checkpoint[] | null; onAll?: () => void;
}) {
  const { recent } = useApp();
  const mine = recent.filter((e: EngineEvent) => e.thread_id === thread.thread_id && e.type !== "agent.step").slice(-12).reverse();
  const [asking, setAsking] = useState<EngineEvent | null>(null);
  // the saved steps from before the oldest live event (the history is newest first)
  const oldest = mine.length ? Date.parse(mine[mine.length - 1].at) : Infinity;
  const before = (history ?? []).filter((c) => !(Date.parse(c.at) >= oldest));
  const earlier = before.slice(0, EARLIER);
  return (
    <Panel title="Events" extra={onAll && (history?.length ?? 0) > 0 ? <button className="btn sm ghost" type="button" onClick={onAll}>All checkpoints</button> : undefined}>
      <div className="events" aria-label="What happened, newest first">
        {mine.map((e, i) => {
          const l = eventLine(e);
          const path = e.type === "guard.refused" && typeof e.data?.path === "string" ? e.data.path : null;
          return (
            <div key={i}>
              <span className="t mono sub">{clock(e.at, false)}</span><span className="k">{l.k}</span>
              <span>{l.text}{path && asking !== e && (
                <> <button className="btn sm ghost" type="button" onClick={() => setAsking(e)} aria-label={`Allow ${path} in this phase`}>Allow this file in this phase</button></>
              )}</span>
            </div>
          );
        })}
        {mine.length > 0 && earlier.length > 0 && <span className="ev-sep sub">Earlier</span>}
        {earlier.map((c) => (
          <div key={c.id} className="ev-cp" data-testid="event-checkpoint">
            <span className="t mono sub">{clock(c.at, false)}</span>
            <span title={c.note}><b className="mono">{c.step}</b> <span className="sub">{c.note}</span></span>
          </div>
        ))}
        {before.length > EARLIER && <span className="hint">{before.length - EARLIER} older steps are under Checkpoints.</span>}
        {!mine.length && !earlier.length && (
          history === undefined || (history !== null && !history.length)
            ? <span className="sub">Nothing happened yet. Events show here as they happen.</span>
            : history === null ? <span className="sub loading">Loading what happened…</span> : null
        )}
      </div>
      {asking && <UnlockConfirm pid={pid} path={String(asking.data.path)} phase={typeof asking.data.phase === "string" ? asking.data.phase : thread.phase} onClose={() => setAsking(null)} />}
    </Panel>
  );
}

function UnlockConfirm({ pid, path, phase, onClose }: { pid: string; path: string; phase: string; onClose: () => void }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const yes = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.unlock(pid, path, phase || undefined);
      toast(`${path} is allowed in ${phase || "this phase"}. The unlock is logged.`);
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid" style={{ gap: 8, marginTop: 10 }}>
      <Confirm text={<>The guard stopped a write to <b className="mono">{path}</b>{phase ? <> in <b>{phase}</b></> : null}. Allow agents to edit this file in this phase, for this flow only?
        The unlock is logged in the flow's events.</>}
        yes="Yes, allow it" busy={busy} onYes={yes} onNo={onClose} />
      {err && <ErrorBox error={err} />}
    </div>
  );
}

// ---------- init flow: ladder ‖ knowledge build ----------

type Rung = { label: string; cmd: string; status: "ok" | "fail" | "fix" | "todo" | "skip"; note: string };

const LADDER_STATUS: Record<LadderRung["status"], Rung["status"]> = { pass: "ok", fail: "fail", fixing: "fix", waiting: "todo", skipped: "skip" };

/** The setup ladder, from the thread (the engine keeps it in the flow's state). */
export function rungsFrom(thread: ThreadState | null): Rung[] {
  return [...(thread?.ladder ?? [])].sort((a, b) => a.n - b.n).map((r) => ({
    label: r.name, cmd: r.cmd, status: LADDER_STATUS[r.status] ?? "todo", note: r.detail ?? "",
  }));
}

const RUNG_PILL: Record<Rung["status"], [PillTone, string]> = { ok: ["ok", "pass"], fail: ["bad", "fail"], fix: ["warn", "fixing"], todo: ["idle", "waiting"], skip: ["idle", "skipped"] };
const KB_PILL: Record<string, [PillTone, string]> = { written: ["ok", "written"], stale: ["warn", "stale"], missing: ["idle", "not written"], writing: ["run", "writing"] };

function InitFlow({ pid, f, onStart, reload }: { pid: string; f: { thread: ThreadState; workflow: Workflow }; onStart: () => void; reload: () => Promise<void> }) {
  const { thread, workflow } = f;
  const { history, est, job, jobs, actual, visited, started } = useThreadBits(pid, thread, workflow);
  const memory = useLoad<Memory>(`mem:${pid}`, () => api.memory(pid));
  const rungs = rungsFrom(thread);
  const passed = rungs.filter((r) => r.status === "ok").length;
  const kb = memory.data?.knowledge ?? [];
  const writing = (jobs.data ?? []).filter((j) => j.thread_id === thread.thread_id && /librarian/.test(j.agent));
  const written = kb.filter((k) => k.status === "written").length;
  const [explain, setExplain] = useState<string | null>(null);
  const canvas = useRef<BlocksHandle>(null);
  const waiting = thread.status === "waiting" && thread.waiting ? <GateCard thread={thread} workflow={workflow} onDone={reload} /> : null;
  return (
    <>
      {explain && <StepInfoDrawer pid={pid} workflow={workflow} stepId={explain} threadId={thread.thread_id} onClose={() => setExplain(null)} />}
      <FlowBar thread={thread} workflow={workflow} estimate={est.data?.tokens ?? null} started={started} onStart={onStart}
        onJump={() => canvas.current?.reveal((thread.current ?? "").replace(/__fix$/, ""), true)} extra={<GoButton to="wiki" className="btn">Open wiki</GoButton>} />
      <LiveCanvas thread={thread} workflow={workflow} actual={actual} visited={visited} waiting={waiting} canvas={canvas} onOpen={setExplain} selected={explain} />
      <div className="lanes" style={{ marginTop: 12 }}>
            <div className="lane">
              <div className="lane-h"><b>Ladder</b><span className="sub num">{rungs.length ? `${passed} / ${rungs.length} rungs` : "not started"}</span></div>
              <div className="meter m-ok"><i style={{ width: `${rungs.length ? (passed / rungs.length) * 100 : 0}%` }} /></div>
              <span className="sub">plain code · each rung must pass before the next</span>
            </div>
            <div className="lane">
              <div className="lane-h"><b>Knowledge build</b><span className="sub num">{written} / {kb.length || "?"} sections</span></div>
              <div className="meter m-ok"><i style={{ width: `${kb.length ? (written / kb.length) * 100 : 0}%`, background: "var(--run)" }} /></div>
              <span className="sub">{writing.length ? `librarian × ${writing.length} in parallel` : "librarians"} · writes the wiki</span>
            </div>
      </div>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <Panel title="Ladder" extra={<span className="hint">the exact command that worked is saved for each rung</span>} body={false}>
          {!rungs.length ? <div className="panel-body empty">Rungs show here when the ladder runs.</div> : (
            <div className="table-wrap"><table>
              <thead><tr><th>#</th><th>Rung</th><th>Command</th><th>Status</th></tr></thead>
              <tbody>{rungs.map((r, i) => (
                <tr key={i}>
                  <td className="num sub">{i + 1}</td>
                  <td><b>{r.label}</b>{(r.status === "fix" || r.status === "fail") && r.note && <div className="sub">{r.note}</div>}</td>
                  <td className="mono sub">{r.cmd}</td>
                  <td><Pill tone={RUNG_PILL[r.status][0]}>{RUNG_PILL[r.status][1]}</Pill></td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
        </Panel>
        <div className="grid" style={{ alignContent: "start" }}>
          {thread.status !== "waiting" && <StatusCard pid={pid} thread={thread} workflow={workflow} job={job} onDone={reload} />}
          <Panel title="Knowledge base → wiki" body="grid">
            <div className="grid" style={{ gap: 8 }}>
              {memory.error ? <ErrorBox error={memory.error} /> : !kb.length ? <span className="sub">No section yet.</span> : kb.map((k) => {
                const live = writing.some((j) => j.agent.includes(k.id) || j.step.includes(k.id));
                const [tone, lab] = live ? KB_PILL.writing : KB_PILL[k.status] ?? ["idle", k.status];
                return <div key={k.id} className="row" style={{ justifyContent: "space-between" }}><span><b>{k.id}</b> <span className="sub">{k.words} words · {k.cites} citations</span></span><Pill tone={tone}>{lab}</Pill></div>;
              })}
              <GoButton to="wiki">Read what is written so far</GoButton>
            </div>
          </Panel>
          <BudgetMeter thread={thread} estimate={est.data?.tokens ?? null} title="Budget for init" />
          <EventsPanel pid={pid} thread={thread} history={history.error ? [] : history.data} />
        </div>
      </div>
    </>
  );
}

/** A pause's detail: the explanation as text, then each `$ command` with its output folded (like agent steps). */
export function GateDetail({ text }: { text: string }) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const at = lines.findIndex((l) => l.startsWith("$ "));
  const prose = (at < 0 ? lines : lines.slice(0, at)).join("\n").trim();
  const cmd = at < 0 ? null : lines[at].slice(2);
  const out = at < 0 ? "" : lines.slice(at + 1).join("\n").replace(/\s+$/, "");
  return (
    <div className="gate-detail" data-testid="gate-detail">
      {prose && <div className="facts"><Markdown text={prose} breaks /></div>}
      {cmd !== null && (
        <div className="cmd" data-testid="gate-cmd">
          <span className="prompt" aria-hidden="true">$</span>
          <CodeBlock text={cmd} lang="bash" gutter={false} className="cmd-code" />
        </div>
      )}
      {out && <FoldedText text={out} className="cmd-out" />}
    </div>
  );
}
