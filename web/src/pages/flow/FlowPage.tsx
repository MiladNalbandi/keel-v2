import { AppliedPanel } from "../../components/DocHistory";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  errorParts,
  type Checkpoint,
  type FlowView,
  type Job,
  type RunRow,
  type ThreadState,
  type Workflow,
} from "../../api";
import { EmptyState } from "../../components/EmptyState";
import { CoreWelcome, useCoreOnly } from "../../components/CoreOnly";
import { type AcOrder } from "../../api";
import { RulesNowPanel } from "../../components/RulesNow";
import { FlowBoardView, showBoard } from "../../components/FlowBoard";
import { FlowRuns } from "../../components/FlowRuns";
import { ResumeRow } from "../../components/ResumeRow";
import { RunHistory } from "../../components/RunHistory";
import { CiteLinks } from "../../components/Markdown";
import { hasPage, useSlot } from "../../sdk/registry";
import { SLOTS, type FlowPanelItem } from "../../sdk/slots";
import { StartFlowDrawer } from "../../components/StartFlow";
import { initOffered, isInitFlow, markInitOffered, needsInit } from "../../components/InitAsk";
import { dropOtherDrafts } from "../../drafts";
import { InitBanner, SetupCard } from "../../components/InitSetup";
import { type BlocksHandle } from "../../components/Blocks";
import { useWide } from "../../components/useWide";
import { Async, ErrorBox, PageHead, Panel, Pill, Tabs, Ticks, type PillTone } from "../../components/ui";
import { acLabel, clock } from "../../format";
import { go, useApp, useLoad, useRoute } from "../../state";
import { DecisionsPanel } from "./Decisions";
import { EventsPanel } from "./EventsPanel";
import {
  BudgetMeter,
  FlowBar,
  type FlowSide,
  LiveCanvas,
  NowLine,
  StatusCard,
  WaitsCard,
  perStep,
  useThreadBits,
} from "./FlowParts";
import { BeforeShipPanel, GateCard, type GateSaid } from "./Gate";
import { FlowFeed } from "./FlowFeed";
import { SessionLine, SessionNow, SessionWaitCard, waitsForSession } from "./SessionParts";
import { isTyping, modalOpen } from "../../keys";
import { InitFlow } from "./InitFlow";
import { DockBar, DockClose, JobFeed, StepPanel, useStepDock } from "./StepPanel";

const AC_TONE: Record<string, PillTone> = { done: "ok", green: "ok", red: "bad", todo: "idle", "already-met": "met" };

export function FlowPage({ pid }: { pid: string }) {
  // v0.7.x: #/flow/<thread> opens one flow (a flow in a worktree); #/flow is the project folder's flow
  const { arg: tid } = useRoute();
  const flow = useLoad(tid ? `flow:${pid}:${tid}` : `flow:${pid}`, () => (tid ? api.flowOf(pid, tid) : api.flow(pid)));
  const board = useLoad(`flows:${pid}`, () => api.flowBoard(pid));
  const { project } = useApp();
  const [start, setStart] = useState(false);
  // v0.16.0 no plugin added a page: a project with no flow opens on "Welcome to keel core"
  const { bare } = useCoreOnly();
  // v0.15.4 an earlier run, open read only: it changes nothing of the current flow (no rewind or resume when it opens)
  const [viewing, setViewing] = useState<RunRow | null>(null);
  useEffect(() => setViewing(null), [pid, tid]);
  const cur = flow.data?.thread ?? null;
  const live = !!cur && (cur.status === "running" || cur.status === "waiting");
  // keel 0.18.64 the flow waits for nothing now (answered here, in the Inbox or in keel2): its unsent drafts go
  const curTid = cur?.thread_id ?? null;
  const curPid = cur?.project_id ?? pid;
  const curWaits = cur?.status === "waiting" && !!cur.waiting;
  useEffect(() => {
    if (curTid && !curWaits) dropOtherDrafts(curPid, curTid, null);
  }, [curPid, curTid, curWaits]);
  // the Runs history shows when the current flow does not run or wait (or there is none)
  const history = !!flow.data && !live;
  const shown = history ? viewing : null;
  const top = useRef<HTMLDivElement>(null);
  const load = (r: RunRow | null) => {
    setViewing(r);
    if (r) window.setTimeout(() => top.current?.scrollIntoView?.({ block: "start" }), 0);
  };
  // keel 0.18.55 a project keel does not know yet: the setup card the first time it opens here (a project found by
  // itself), the banner after that; neither while its init flow runs or waits
  const [offered, setOffered] = useState(() => initOffered(pid));
  useEffect(() => setOffered(initOffered(pid)), [pid]);
  const unknown = !!flow.data && project?.id === pid && needsInit(project) && !(live && isInitFlow(flow.data.workflow));
  // after Resume the flow runs: show it live (the project's flow, or that flow by its id)
  const resumed = async (r: RunRow) => {
    setViewing(null);
    if (r.thread_id !== cur?.thread_id && r.thread_id !== tid) go("flow", r.thread_id);
    else await flow.reload();
    void board.reload();
  };
  return (
    <>
      {unknown && project && (offered ? <InitBanner project={project} />
        : <SetupCard project={project} onLater={() => setOffered(true)} onStarted={() => { markInitOffered(pid); setOffered(true); void flow.reload(); }} />)}
      <FlowRuns pid={pid} selected={flow.data?.thread?.thread_id ?? null} loading={!flow.data} history={!history} />
      {showBoard(board.data) && (
        <FlowBoardView board={board.data!} selected={flow.data?.thread?.thread_id ?? null} onChanged={() => void board.reload()} />
      )}
      {history && (
        <RunHistory pid={pid} current={cur?.thread_id ?? null} viewing={shown?.thread_id ?? null} onLoad={load} onChanged={flow.reload}
          resume={(r, close) => <ResumeRow thread={r} autoAsk onCancel={close} onDone={() => resumed(r)} />} />
      )}
      <div ref={top} className="rh-anchor" />
      {shown ? (
        <EarlierRun key={shown.thread_id} pid={pid} run={shown} onBack={() => setViewing(null)} onResumed={() => resumed(shown)} />
      ) : (
      <Async r={flow} what="Loading the flow">
        {(f) => !f.thread || !f.workflow ? (
          bare ? (
            // v0.16.0 a keel with only its core opens on its welcome; the project's own empty flow follows it
            <CoreWelcome>
              <div className="ch-sect">
                <h2>{project?.name ?? pid}</h2>
                <p className="sub">{showBoard(board.data) ? "No flow is running in the project folder." : "No flow is running in this project."}</p>
              </div>
              <div className="panel">
                <EmptyState title="Nothing running here yet"
                  action={<button className="btn primary" type="button" id="startFlow2" onClick={() => setStart(true)}>Start a flow</button>}>
                  Start a feature, change, fix or hunt. You will see the estimate before it starts. New to keel?{" "}
                  <a href="#/help">Help › Start here</a>.
                </EmptyState>
              </div>
            </CoreWelcome>
          ) : (
          <>
            <PageHead title={project?.name ?? pid} sub={showBoard(board.data) ? "No flow is running in the project folder." : "No flow is running in this project."} />
            <div className="panel">
              <EmptyState title="Nothing running here yet"
                action={<button className="btn primary" type="button" id="startFlow2" onClick={() => setStart(true)}>Start a flow</button>}>
                Start a feature, change, fix or hunt. You will see the estimate before it starts. New to keel?{" "}
                <a href="#/help">Help › Start here</a>.
              </EmptyState>
            </div>
          </>
          )
        ) : isInit(f.workflow) ? (
          <InitFlow pid={pid} f={f as Required<FlowView> & { thread: ThreadState; workflow: Workflow }} onStart={() => setStart(true)} reload={flow.reload} />
        ) : (
          <>
            {f.thread.base_behind && (
              <div className="rh-banner" role="alert" aria-label="The base is not up to date">
                <div className="rh-banner-t">
                  <b>This flow started from an old base</b>
                  <span className="sub">{f.thread.base_behind}</span>
                </div>
              </div>
            )}
            <ThreadView pid={pid} thread={f.thread} workflow={f.workflow} reload={flow.reload} onStart={() => setStart(true)} />
            <AppliedPanel key={f.thread.thread_id} tid={f.thread.thread_id} />
          </>
        )}
      </Async>
      )}
      {start && <StartFlowDrawer onClose={() => setStart(false)} />}
    </>
  );
}

/** v0.15.4 How an earlier run opens: read only, and Resume only for the project's newest flow when it was stopped. */
type ReadOnly = { resumable: boolean; branch?: string | null; onResumed: () => Promise<void> };

/** v0.15.4 An earlier run, read only: its steps (Blocks / Table / Graph), events, checkpoints and how it ended. Opening it
 * only reads (GET); the current flow keeps working as it is. */
function EarlierRun({ pid, run, onBack, onResumed }: { pid: string; run: RunRow; onBack: () => void; onResumed: () => Promise<void> }) {
  const r = useLoad(`flow-ro:${pid}:${run.thread_id}`, () => api.flowOf(pid, run.thread_id));
  const resumable = !!run.latest && run.status === "stopped";
  return (
    <>
      <div className="rh-banner" role="region" aria-label="An earlier run (read only)">
        <div className="rh-banner-t">
          <b>An earlier run (read only)</b>
          <span className="sub">{resumable ? "It is the last flow and it was stopped: Resume goes on from its last saved step."
            : "Looking at it changes nothing in the current flow."}</span>
        </div>
        <button className="btn" type="button" onClick={onBack}>Back to the current flow</button>
      </div>
      <Async r={r} what="Loading the run">
        {(f) => !f.thread || !f.workflow ? (
          <div className="panel"><EmptyState title="Nothing saved for this run">Its state or its workflow is gone.</EmptyState></div>
        ) : (
          <ThreadView pid={pid} thread={f.thread} workflow={f.workflow} reload={r.reload} onStart={onBack}
            readOnly={{ resumable, branch: run.branch, onResumed }} />
        )}
      </Async>
    </>
  );
}

const isInit = (w: Workflow) => isInitFlow(w);


const SIDE_WORDS: Record<FlowSide, string> = {
  now: "Now", step: "Step", decisions: "Decisions", checkpoints: "Checkpoints", budget: "Budget", criteria: "Criteria", ship: "Before ship", more: "More",
};

/** keel 0.18.66 what the folded side panel's bar says: the step it shows, else its tab. */
const barLabel = (side: FlowSide, name: string | null) => (side === "step" && name ? `Step details · ${name}` : `Flow details · ${SIDE_WORDS[side]}`);

/** The Details drawer (keel 0.28; the side panel before): what happens now (and the running agent's last steps, the
 *  rules now, the events), the selected step, the decisions, the checkpoints, the budget, the criteria, what blocks
 *  shipping and the add-ons' panels, one tab each. » or D folds it to a thin bar at the right edge (wide screens). */
function FlowSidePanel({ pid, thread, workflow, job, running, side, setSide, selected, onClose, onDone, history, onJump, readOnly, dock, wide, estimate, panels }: {
  pid: string; thread: ThreadState; workflow: Workflow; job: Job | null; running: Job[]; side: FlowSide; setSide: (s: FlowSide) => void;
  selected: string | null; onClose: () => void; onDone: () => Promise<void>; history: ReturnType<typeof useThreadBits>["history"]; onJump: () => void;
  readOnly?: ReadOnly; dock: ReturnType<typeof useStepDock>; wide: boolean; estimate: number | null; panels: FlowPanelItem[];
}) {
  const name = selected ? workflow.steps.find((s) => s.id === selected)?.name ?? selected : null;
  if (wide && !dock.open) return <DockBar what="side panel" label={barLabel(side, name)} onOpen={() => dock.setOpen(true)} />;
  const tabs: [FlowSide, string][] = [["now", "Now"], ["step", name ? `Step: ${name}` : "Step"], ["decisions", "Decisions"], ["checkpoints", "Checkpoints"],
    ["budget", "Budget"], ["criteria", "Criteria"], ["ship", "Before ship"], ...(panels.length ? [["more", "More"] as [FlowSide, string]] : [])];
  const at = side === "more" && !panels.length ? "now" : side;
  return (
    <aside className="fl-side rdfl-details" aria-label="Now, the selected step and checkpoints">
      <div className="fl-side-top">
        <Tabs value={at} onChange={setSide} label="Flow details" options={tabs} />
        {wide && <DockClose what="side panel" onClose={() => dock.setOpen(false)} />}
      </div>
      <div className="rdfl-details-b" key={at}>
      {at === "now" ? (
        <div className="grid" style={{ gap: 12 }}>
          {waitsForSession(thread) ? <SessionNow thread={thread} onJump={onJump} />
            : thread.status === "waiting" && thread.waiting ? <WaitsCard thread={thread} onJump={onJump} />
            : <StatusCard pid={pid} thread={thread} workflow={workflow} job={job} onDone={onDone} onCheckpoints={readOnly ? undefined : () => setSide("checkpoints")}
              noResume={readOnly && !readOnly.resumable ? "Only the last stopped flow can be resumed." : undefined} />}
          {thread.runner === "session" && !waitsForSession(thread) && <SessionLine thread={thread} compact />}
          {job && <JobFeed job={job} />}
          <RulesNowPanel thread={thread} />
          <EventsPanel pid={pid} thread={thread} workflow={workflow} history={history.error ? [] : history.data} onAll={() => setSide("checkpoints")} readOnly={!!readOnly} />
        </div>
      ) : at === "step" ? (
        selected ? <StepPanel key={selected} pid={pid} thread={thread} workflow={workflow} stepId={selected} running={running} onClose={onClose} onJump={onJump}
          tab={dock.tab} setTab={dock.setTab} />
          : <div className="panel"><div className="panel-body wb-hint"><b>Select a block to see what it does.</b><span className="sub">Its task, the rules of its phase, where it goes next and what it did in this flow.</span></div></div>
      ) : at === "decisions" ? (
        <DecisionsPanel pid={pid} thread={thread} workflow={workflow} />
      ) : at === "checkpoints" ? (
        <CheckpointsPanel thread={thread} history={history.data} error={history.error} onRewound={onDone} readOnly={!!readOnly} />
      ) : at === "budget" ? (
        <BudgetMeter thread={thread} estimate={estimate} />
      ) : at === "criteria" ? (
        <AcsPanel thread={thread} />
      ) : at === "ship" ? (
        <BeforeShipPanel blockers={thread.blockers} off={thread.gates_off} pid={pid} />
      ) : (
        <div className="grid" style={{ gap: 12 }}>
          {panels.map((p) => <p.component key={p.id} pid={pid} thread={thread} readOnly={!!readOnly} />)}
        </div>
      )}
      </div>
      {wide && (
        <div className="rdfl-keys" aria-label="Keys on this page">
          <span><kbd>D</kbd> panel</span><span><kbd>G</kbd> current</span><span><kbd>J</kbd><kbd>K</kbd> steps</span><span><kbd>A</kbd><kbd>R</kbd><kbd>O</kbd> gate</span><span><kbd>?</kbd> keys</span>
        </div>
      )}
    </aside>
  );
}

const RAIL_KEY = "keel2.flow.rail";
const railOpen = () => {
  try {
    return localStorage.getItem(RAIL_KEY) !== "0";
  } catch {
    return true;
  }
};

/** keel 0.28 the steps rail: open, or folded to a thin bar at the left edge (the browser remembers it). */
function useRail(): [boolean, (v: boolean) => void] {
  const [open, setOpenNow] = useState(railOpen);
  const setOpen = (v: boolean) => {
    setOpenNow(v);
    try {
      if (v) localStorage.removeItem(RAIL_KEY);
      else localStorage.setItem(RAIL_KEY, "0");
    } catch { /* private window: not remembered */ }
  };
  return [open, setOpen];
}

function ThreadView({ pid, thread, workflow, reload, onStart, readOnly }: {
  pid: string; thread: ThreadState; workflow: Workflow; reload: () => Promise<void>; onStart: () => void;
  /** v0.15.4 an earlier run, read only */
  readOnly?: ReadOnly;
}) {
  const { history, est, job, jobs, runs, actual, facts, totals, perAc, visited, started } = useThreadBits(pid, thread, workflow);
  const panels = useSlot<FlowPanelItem>(SLOTS.flowPanel);
  const tokens = useMemo(() => perStep(workflow, est.data?.per_step), [workflow, est.data]);
  // v0.15.2 the agents of this flow that work now: a click on the step one of them works in shows that step "now"
  const running = useMemo(() => (jobs.data ?? []).filter((j) => j.thread_id === thread.thread_id && j.status === "running"), [jobs.data, thread.thread_id]);
  const [sel, setSel] = useState<string | null>(null);
  const [side, setSide] = useState<FlowSide>("now");
  const wide = useWide();
  const canvas = useRef<BlocksHandle>(null);
  const [rail, setRail] = useRail();
  // keel 0.28 what you answered last: the feed says it (and the way back) until the flow waits again
  const [said, setSaid] = useState<GateSaid | null>(null);
  useEffect(() => setSaid(null), [thread.thread_id]);
  // keel 0.18.11 once the flow bar has scrolled out of view, the flow's one line stays on top
  const barRef = useRef<HTMLDivElement>(null);
  const [barGone, setBarGone] = useState(false);
  useEffect(() => {
    const el = barRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([e]) => setBarGone(!e.isIntersecting), { rootMargin: "-48px 0px 0px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  const cur = (thread.waiting?.step ?? thread.current ?? "").replace(/__fix$/, "");
  // "Go to it" / "Jump to current": open the folds around the block, then show the answer card (when the flow waits) and
  // put the focus on its first button, so you can answer right away. A folded steps rail opens first.
  const jump = () => {
    if (!rail) setRail(true);
    if (rail) canvas.current?.reveal(cur, true);
    else window.setTimeout(() => canvas.current?.reveal(cur, true), 0);
    if (thread.status !== "waiting") return;
    window.setTimeout(() => {
      const card = document.querySelector<HTMLElement>('.fl [aria-label="Gate waits for you"]');
      if (!card) return;
      card.scrollIntoView?.({ block: "center", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      (card.querySelector<HTMLElement>("button") ?? card.querySelector<HTMLElement>("textarea, input"))?.focus({ preventScroll: true });
    }, 120);
  };
  // keel 0.18.66 a click on a step shows it in the docked panel, and opens the panel when it was folded to its bar
  const dock = useStepDock();
  const open = (id: string) => { setSel(id); setSide("step"); if (wide && !dock.open) dock.setOpen(true); };
  const details = (s: FlowSide) => {
    setSide(s);
    if (wide && !dock.open) dock.setOpen(true);
    if (!wide) window.setTimeout(() => document.querySelector<HTMLElement>(".rdfl-details")?.scrollIntoView?.({ block: "start" }), 0);
  };
  const live = thread.status === "running" || thread.status === "waiting";
  // keel 0.28 the page's keys (not while typing or in a dialog): D folds Details, G jumps to the current step, J / K
  // follow the next or the previous step
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    if (e.defaultPrevented || e.repeat || e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target) || modalOpen()) return;
    const k = e.key.toLowerCase();
    if (k === "d" && wide) { e.preventDefault(); dock.setOpen(!dock.open); }
    else if (k === "g" && live && !readOnly) { e.preventDefault(); jump(); }
    else if (k === "j" || k === "k") {
      const ids = workflow.steps.map((s) => s.id);
      if (!ids.length) return;
      e.preventDefault();
      const at = sel ? ids.indexOf(sel) : -1;
      const to = at < 0 ? (k === "j" ? 0 : ids.length - 1) : Math.min(ids.length - 1, Math.max(0, at + (k === "j" ? 1 : -1)));
      open(ids[to]);
      if (rail) canvas.current?.reveal(ids[to]);
    }
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  const gate = !readOnly && thread.status === "waiting" && thread.waiting
    ? (waitsForSession(thread) ? <SessionWaitCard thread={thread} workflow={workflow} onDone={reload} onDecided={setSaid} />
      : <GateCard thread={thread} workflow={workflow} onDone={reload} onDecided={setSaid} />) : null;
  // v0.15.4 an earlier run changes nothing, except Resume of the last stopped one: then the page shows it live
  const done = readOnly ? readOnly.onResumed : reload;
  // 0.18.57 the flow's file:line citations open the Code page at the branch or ref the flow looked at (its worktree's
  // branch, the reviewed PR's head), not the project folder's checkout
  const cites = useMemo(() => (hasPage("repo") ? { ref: thread.source_ref ?? null } : null), [thread.source_ref]);
  const cls = ["fl", "rdfl", wide && !dock.open ? "is-folded" : "", rail ? "" : "rail-folded"].filter(Boolean).join(" ");
  const stepsAt = workflow.steps.findIndex((s) => s.id === cur);
  return (
    <CiteLinks.Provider value={cites}>
      <div ref={barRef}>
        <FlowBar thread={thread} workflow={workflow} estimate={est.data?.tokens ?? null} started={started} onStart={onStart} onJump={jump}
          readOnly={!!readOnly} branch={readOnly?.branch} details={wide ? { open: dock.open, onToggle: () => dock.setOpen(!dock.open) } : undefined} />
      </div>
      {barGone && !readOnly && <NowLine thread={thread} workflow={workflow} started={started} onJump={jump} />}
      {!wide && sel && <StepPanel drawer key={sel} pid={pid} thread={thread} workflow={workflow} stepId={sel} running={running} onClose={() => setSel(null)}
        onJump={() => { setSel(null); jump(); }} tab={dock.tab} setTab={dock.setTab} />}
      <div className={cls}>
        {rail ? (
          <div className="rdfl-rail">
            <LiveCanvas thread={thread} workflow={workflow} tokens={tokens} actual={actual} facts={facts} totals={totals} perAc={perAc} visited={visited} waiting={null} canvas={canvas} onOpen={open} selected={sel}
              still={!!readOnly} onJump={jump} job={job}
              fold={<button type="button" className="sp-toggle rdfl-fold" onClick={() => setRail(false)} aria-label="Fold the steps" aria-expanded="true" title="Fold the steps to a thin bar">«</button>} />
          </div>
        ) : (
          <aside className="rdfl-rail-bar" aria-label="Steps (folded)">
            <button type="button" className="sp-toggle" onClick={() => setRail(true)} aria-label="Open the steps" aria-expanded="false" title="Open the steps">»</button>
            <span className="sp-bar-t" onClick={() => setRail(true)}>Steps{stepsAt >= 0 ? ` · ${stepsAt + 1} of ${workflow.steps.length}` : ` · ${workflow.steps.length}`}</span>
          </aside>
        )}
        <FlowFeed thread={thread} workflow={workflow} runs={runs.error ? [] : runs.data} job={job} selected={sel} onClear={() => { setSel(null); setSide("now"); }}
          gate={gate} said={said} onBack={() => details("checkpoints")} onDetails={() => details("now")} readOnly={!!readOnly} />
        <FlowSidePanel pid={pid} thread={thread} workflow={workflow} job={job} running={running} side={wide ? side : side === "step" ? "now" : side} setSide={setSide}
          selected={sel} onClose={() => { setSel(null); setSide("now"); }} onDone={done} history={history} onJump={jump} readOnly={readOnly} dock={dock} wide={wide}
          estimate={est.data?.tokens ?? null} panels={panels} />
      </div>
    </CiteLinks.Provider>
  );
}

function CheckpointsPanel({ thread, history, error, onRewound, readOnly }: {
  thread: ThreadState; history: Checkpoint[] | null; error: { message: string; hint?: string } | null; onRewound: () => Promise<void>;
  /** v0.15.4 an earlier run: the checkpoints without Rewind */
  readOnly?: boolean;
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
    <Panel title="Checkpoints" extra={<span className="hint">{readOnly ? "Every saved step of this run." : "Every step is saved. Rewind starts a new branch from that point."}</span>}>
      {error ? <ErrorBox error={error} /> : !history ? <div className="empty loading">Loading…</div> : !history.length ? <div className="empty">No checkpoint yet.</div> : (
        <ul className="timeline">
          {history.map((c, i) => (
            <li key={c.id} className={i === 0 ? "head" : ""}>
              <span className="cp">#{c.n}</span>
              <span className="t">{clock(c.at)}</span>
              <span><span className="mono">{c.step}</span> <span className="sub">{c.note}</span></span>
              {i === 0 ? <span className="tag">now</span> : readOnly ? null : (
                <button className="btn sm ghost" type="button" disabled={busy !== null} onClick={() => rewind(c)}>{busy === c.id ? "Rewinding…" : "Rewind here"}</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

const ORDER_FROM: Record<AcOrder["from"], string> = {
  spec: "the spec's ORDER line", plan: "the plan's ORDER", keel: "keel moved it (an order question)", person: "you moved it",
  id: "criteria ids",
};

function AcsPanel({ thread }: { thread: ThreadState }) {
  const from = thread.ac_order?.from;
  return (
    <Panel title="Acceptance criteria">
      {!thread.acs.length ? <span className="sub">The spec step writes them. They show here after it.</span> : (
        <div className="acs">
          {/* v0.17.4 the list is the build order: keel builds them top to bottom */}
          <span className="sub" data-testid="ac-order">Built in this order{from ? `, from ${ORDER_FROM[from] ?? from}` : ""}.</span>
          {thread.acs.map((a, n) => (
            <div key={a.id} className="ac">
              <div className="row" style={{ justifyContent: "space-between" }}><b>{n + 1}. {a.id}</b><Pill tone={AC_TONE[a.status] ?? "idle"}>{acLabel(a.status)}</Pill></div>
              <span className="sub">[{a.layer}] <Ticks text={a.title} /></span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
