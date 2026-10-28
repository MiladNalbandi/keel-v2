import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { type Job, type Step, type StepExplanation, type ThreadState, type Workflow } from "../../api";
import { FoldPanel } from "../../components/FoldPanel";
import {
  CommitLine, infoRequest, LastRun, PhaseLine, RefusedList, RuleBoxes, RulesTable, StepChoices, stepMeta, stepWhat, Task,
  useStepInfo,
} from "../../components/StepInfo";
import { StepView } from "../../components/StepView";
import { KIND } from "../../components/workflow";
import { useJobSteps } from "../Live";
import { Drawer, ErrorBox, GoButton, Loading, Prov } from "../../components/ui";
import { StepDecisions, useStepDecisions } from "./Decisions";
import { EventsPanel } from "./EventsPanel";
import { RunningCard, WaitsCard, stepIdOf } from "./FlowParts";

// keel 0.18.66 the step panel: docked on the right of the Flow page (next to the steps, never over them), or a drawer on
// a narrow screen. A menu bar picks what it shows: Overview (the step in plain words, its choices, one line of facts,
// what happens in it now, its task and last run), Decided (each answer given at it), Rules (the phase's rules as three
// boxes, the full table one click away) and Commands (the shell commands keel refuses). » folds it to a thin bar at the
// right edge, « opens it again; the browser remembers open or closed, and the last tab.

/** v0.15.2 Is this step the one that runs now (or waits for you)? And the agent of this flow that works in it, if any. */
function nowOf(thread: ThreadState, workflow: Workflow, running: Job[], id: string): { now: boolean; job: Job | null } {
  if (thread.status !== "running" && thread.status !== "waiting") return { now: false, job: null };
  const cur = (thread.waiting?.step ?? thread.current ?? "").replace(/__fix$/, "");
  const job = running.find((j) => j.thread_id === thread.thread_id && stepIdOf(workflow, j.step) === id) ?? null;
  return { now: id === cur || !!job, job };
}

export type StepTab = "overview" | "decided" | "rules" | "commands";
const TABS: [StepTab, string][] = [["overview", "Overview"], ["decided", "Decided"], ["rules", "Rules"], ["commands", "Commands"]];
const OPEN_KEY = "keel2.steppanel.open";
const TAB_KEY = "keel2.steppanel.tab";

const read = (key: string) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};
const write = (key: string, v: string | null) => {
  try {
    if (v === null) localStorage.removeItem(key);
    else localStorage.setItem(key, v);
  } catch { /* private window: not remembered */ }
};

/** keel 0.18.66 the docked panel's state, kept per browser: open or folded to its bar, and the last tab. */
export function useStepDock() {
  const [open, setOpenNow] = useState(() => read(OPEN_KEY) !== "0");
  const [tab, setTabNow] = useState<StepTab>(() => {
    const t = read(TAB_KEY);
    return TABS.some(([k]) => k === t) ? (t as StepTab) : "overview";
  });
  return {
    open, tab,
    setOpen: (v: boolean) => { setOpenNow(v); write(OPEN_KEY, v ? null : "0"); },
    setTab: (t: StepTab) => { setTabNow(t); write(TAB_KEY, t === "overview" ? null : t); },
  };
}

/** » : folds the docked panel to its thin bar. */
export function DockClose({ what, onClose }: { what: string; onClose: () => void }) {
  return (
    <button className="sp-toggle" type="button" onClick={onClose} aria-label={`Close the ${what}`} aria-expanded="true"
      title="Close (it stays as a thin bar)">»</button>
  );
}

/** The folded panel: a thin bar at the right edge with « and what it holds ("Step details · red"). */
export function DockBar({ what, label, onOpen }: { what: string; label: string; onOpen: () => void }) {
  return (
    <aside className="fl-side sp-bar" aria-label={`${what[0].toUpperCase()}${what.slice(1)} (closed)`}>
      <button className="sp-toggle" type="button" onClick={onOpen} aria-label={`Open the ${what}`} aria-expanded="false" title={`Open the ${what}`}>«</button>
      {/* the words open it too (a large target); the button is the one for the keyboard */}
      <span className="sp-bar-t" onClick={onOpen} data-testid="dock-label">{label}</span>
    </aside>
  );
}

/** The clicked step: in the docked panel (`tools`: its » button), or in a drawer (narrow screens). The step that runs
 *  now, or waits for you, shows what happens in it now on Overview (the waiting or running card, the agent's newest
 *  steps, this step's events since it started). */
export function StepPanel({ pid, thread, workflow, stepId, running, onClose, onJump, drawer, tab, setTab, tools }: {
  pid: string; thread: ThreadState; workflow: Workflow; stepId: string; running: Job[]; onClose: () => void; onJump: () => void;
  drawer?: boolean; tab: StepTab; setTab: (t: StepTab) => void; tools?: ReactNode;
}) {
  // the workflow the page draws, plus the thread (its state and what the step did there)
  const { x, error } = useStepInfo(pid, infoRequest(stepId, workflow, thread.thread_id));
  const decided = useStepDecisions(pid, thread, workflow, stepId, x);
  const step = workflow.steps.find((s) => s.id === stepId);
  const { now, job } = nowOf(thread, workflow, running, stepId);
  // the name the page draws (a thread started on an older template may explain a step by another name)
  const name = step?.name ?? x?.name ?? stepId;
  const title = `What ${name} does`;
  const kind = KIND[step?.kind ?? x?.kind ?? "agent"] ?? step?.kind ?? x?.kind;
  const counts: Partial<Record<StepTab, number>> = { decided: decided.shown.length, commands: x?.rules.shell_refused.length ?? 0 };
  const uid = useId();
  const live = now && step ? <StepNow pid={pid} thread={thread} workflow={workflow} step={step} job={job} onJump={onJump} /> : null;
  const body = (
    <TabBody id={stepId} uid={uid} tab={tab} render={(t) => t === "decided" ? <StepDecisions workflow={workflow} stepId={stepId} d={decided} />
      : error ? <ErrorBox error={error} />
        : t === "overview" ? <Overview x={x} live={live} />
          : !x ? <Loading what="Reading the step" />
            : t === "rules" ? <RulesTab x={x} /> : <CommandsTab x={x} />} />
  );
  const bar = <MenuBar uid={uid} tab={tab} setTab={setTab} counts={counts} />;
  if (drawer) {
    return (
      <Drawer title={title} onClose={onClose} className="step-drawer">
        <div className="sp sp-in-drawer">
          <span className="sp-kind">Step · {kind}</span>
          {bar}
          {body}
        </div>
      </Drawer>
    );
  }
  return (
    <section className="panel step-panel sp" aria-label={title}>
      <div className="sp-head">
        <div className="sp-title">
          <span className="sp-kind">Step · {kind}</span>
          <h2 title={title}>{name}</h2>
        </div>
        {tools}
      </div>
      {bar}
      {body}
    </section>
  );
}

/** The menu bar (a tablist): Overview · Decided (count) · Rules · Commands (count). ← → Home End move along it. */
function MenuBar({ uid, tab, setTab, counts }: { uid: string; tab: StepTab; setTab: (t: StepTab) => void; counts: Partial<Record<StepTab, number>> }) {
  const ref = useRef<HTMLDivElement>(null);
  const key = (e: KeyboardEvent) => {
    const at = TABS.findIndex(([k]) => k === tab);
    const to = e.key === "ArrowRight" ? at + 1 : e.key === "ArrowLeft" ? at - 1 : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : null;
    if (to === null) return;
    e.preventDefault();
    const next = TABS[(to + TABS.length) % TABS.length][0];
    setTab(next);
    ref.current?.querySelector<HTMLElement>(`[data-tab="${next}"]`)?.focus();
  };
  return (
    <div className="sp-tabs" role="tablist" aria-label="Step details" ref={ref} onKeyDown={key}>
      {TABS.map(([k, label]) => (
        <button key={k} type="button" role="tab" id={`${uid}-tab-${k}`} aria-controls={`${uid}-panel`} data-tab={k} aria-selected={tab === k}
          tabIndex={tab === k ? 0 : -1} onClick={() => setTab(k)}>
          {label}{counts[k] ? <> <span className="sp-count">{counts[k]}</span></> : null}
        </button>
      ))}
    </div>
  );
}

/** The picked tab's content. A new step or tab slides in (not with reduced motion: styles.css). */
function TabBody({ id, uid, tab, render }: { id: string; uid: string; tab: StepTab; render: (t: StepTab) => ReactNode }) {
  return (
    <div key={`${id}:${tab}`} className="sp-body sp-slide" role="tabpanel" id={`${uid}-panel`} aria-labelledby={`${uid}-tab-${tab}`}>
      {render(tab)}
    </div>
  );
}

/** Overview: what the step does in plain words, its choices in one box, one line of facts; then what happens in it now,
 *  its task in full and its last run. */
function Overview({ x, live }: { x: StepExplanation | null; live: ReactNode }) {
  if (!x) return <>{live}<Loading what="Reading the step" /></>;
  const hints = [x.loop?.text, x.loop?.fan_out?.text, x.included_from?.text, x.runs_only_when ? `Runs only when ${x.runs_only_when}.` : "",
    x.skippable ? `Skippable (${x.skippable.band}): ${x.skippable.text}.` : "", x.lock ? "A keel rule: it cannot be removed while keel rules are on." : ""]
    .filter(Boolean);
  // a gate's other notes (its choices and when it asks are said above)
  const notes = (x.gate?.notes ?? []).filter((n) => !n.startsWith("A plain approve picks ") && !n.startsWith("Pauses only when") && !n.startsWith("A keel rule"));
  return (
    <>
      <div className="fl-about sp-about" data-testid="step-about">
        <p>{stepWhat(x)}</p>
        {hints.map((h, i) => <p key={i} className="hint">{h}</p>)}
      </div>
      <StepChoices x={x} />
      <div className="sp-meta" data-testid="step-meta">{stepMeta(x).map((m) => <span key={m}>{m}</span>)}</div>
      {live && <section className="grid sp-now" style={{ gap: 12 }} aria-label="Now"><h3 className="sp-h">Now</h3>{live}</section>}
      <div className="grid xbody" style={{ gap: 16 }}>
        {(x.agent || x.code) && <Task x={x} />}
        {x.branch?.rounds && <p className="hint">{x.branch.rounds}</p>}
        {notes.length > 0 && <ul className="xlist sp-notes">{notes.map((n, i) => <li key={i} className="hint">{n}</li>)}</ul>}
        <LastRun x={x} />
      </div>
    </>
  );
}

/** Rules: the phase in words, its rules as three boxes, and the full table, file by file, one click away. */
function RulesTab({ x }: { x: StepExplanation }) {
  const [full, setFull] = useState(false);
  const id = useId();
  return (
    <div className="grid xbody" style={{ gap: 12 }}>
      <PhaseLine x={x} />
      <RuleBoxes x={x} />
      {x.rules.lane_note && <p className="hint">{x.rules.lane_note}</p>}
      <CommitLine x={x} />
      <button type="button" className="linkbtn sp-more" aria-expanded={full} aria-controls={id} onClick={() => setFull(!full)}>
        {full ? "Hide the full table" : "The full table, file by file ›"}
      </button>
      {full && <section id={id} className="xsec" aria-label="The full table, file by file"><RulesTable x={x} /></section>}
    </div>
  );
}

/** Commands: the shell commands keel refuses in this step, one line each with why. */
function CommandsTab({ x }: { x: StepExplanation }) {
  return (
    <div className="grid xbody" style={{ gap: 10 }}>
      <p className="sub">Shell commands keel refuses in this step:</p>
      <RefusedList x={x} />
      {x.rules.reads_blocked && <p className="hint">An agent never reads {x.rules.reads_blocked}.</p>}
    </div>
  );
}

/** v0.15.2 What happens in the step now: the waiting card (Go to it) or the running card, the agent's newest steps, and
 * this step's events since it started. */
function StepNow({ pid, thread, workflow, step, job, onJump }: { pid: string; thread: ThreadState; workflow: Workflow; step: Step; job: Job | null; onJump: () => void }) {
  const waits = thread.status === "waiting" && !!thread.waiting && thread.waiting.step.replace(/__fix$/, "") === step.id;
  const runs = !waits && (!!job || (thread.status === "running" && (thread.current ?? "").replace(/__fix$/, "") === step.id));
  return (
    <>
      {waits ? <WaitsCard thread={thread} onJump={onJump} /> : runs ? <RunningCard step={step} name={step.name} ac={job?.ac ?? thread.ac} job={job} /> : null}
      {job && <JobFeed job={job} />}
      <EventsPanel pid={pid} thread={thread} workflow={workflow} step={step.id} title={`Events of ${step.name}`} />
    </>
  );
}

/** The running agent's newest steps, with a link to watch it live. v0.15.2 Hide folds it (this browser remembers it);
 * ⤢ shows every step so far in a large view. */
export function JobFeed({ job }: { job: Job }) {
  const { steps } = useJobSteps(job.id);
  const last = steps.slice(-6);
  const watch = <GoButton to="live" arg={job.id} className="btn sm">Watch live</GoButton>;
  const none = <span className="sub">Waiting for the first step…</span>;
  return (
    <FoldPanel id="flow.works" title={`${job.agent} works`} extra={watch} bodyClass="feed fl-feed" live
      big={() => (
        <>
          <div className="row fl-big-head">
            <span className="sub">{job.agent} · <Prov p={job.provider} m={job.model} e={job.effort} />{job.ac ? ` · ${job.ac}` : ""} · {steps.length} steps so far</span>
            {watch}
            <GoButton to="jobs" arg={job.id} className="btn sm ghost">Open job</GoButton>
          </div>
          <div className="feed" aria-label={`Every step of ${job.agent}`} aria-live="polite">
            {steps.length ? steps.map((st) => <StepView key={st.n} s={st} idPrefix={`flbig-${job.id}`} />) : none}
          </div>
        </>
      )}>
      {last.length ? last.map((st) => <StepView key={st.n} s={st} idPrefix={`fl-${job.id}`} />) : none}
    </FoldPanel>
  );
}
