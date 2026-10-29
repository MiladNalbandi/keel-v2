import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorParts, type Job, type JobStep, type Step, type ThreadState, type Workflow } from "../../api";
import { ResumeRow } from "../../components/ResumeRow";
import { RunModeSwitch } from "../../components/RunMode";
import {
  AcChips,
  Blocks,
  BlocksLegend,
  StepTable,
  useMapView,
  ViewToggle,
  type BlocksHandle,
} from "../../components/Blocks";
import { phaseTitle } from "../../components/flowmap";
import { sendBacksOf, stepFacts, type AcFacts, type StepFacts } from "../../components/stepFacts";
import { Graph, GraphLegend } from "../../components/Graph";
import { Zoom } from "../../components/Zoom";
import { GoButton, Panel, Pill, Prov, type PillTone } from "../../components/ui";
import { kfmt, shortDur, usd } from "../../format";
import { go, useApp, useLoad } from "../../state";
import { useHasPage } from "../../sdk/registry";
import { askAssistant } from "../../sdk/assistant";
import { fixRequest } from "../../askFix";
import { CopyId, GateCard } from "./Gate";

export function useThreadBits(pid: string, thread: ThreadState, workflow: Workflow) {
  const history = useLoad(`hist:${thread.thread_id}`, () => api.history(thread.thread_id));
  const n = thread.acs.length || 3;
  const est = useLoad(`est:${pid}:${workflow.id}:${n}`, () => api.estimate(pid, workflow.id, n), { live: false });
  const jobs = useLoad(`jobs-run:${pid}`, () => api.jobs({ project: pid, status: "running" }));
  const job = jobs.data?.find((j) => j.thread_id === thread.thread_id) ?? null;
  // keel 0.18.11 what each step of this flow really did (every call of the flow, not the project's newest 50): the
  // tokens each step used, and the facts its block shows (model, effort, time, cost, result, tries).
  const runs = useLoad(`stepruns:${pid}:${thread.thread_id}`, () => api.stepRuns(pid, thread.thread_id));
  const { facts, actual, totals, perAc } = useMemo(() => {
    const r = stepFacts(runs.data ?? [], workflow.steps, thread.ac);
    return { ...r, facts: Object.keys(r.facts).length ? r.facts : undefined, actual: Object.keys(r.actual).length ? r.actual : undefined };
  }, [runs.data, workflow, thread.ac]);
  const visited = useMemo(() => {
    const ids = new Set<string>();
    (history.data ?? []).forEach((c) => {
      const s = workflow.steps.find((x) => x.id === c.step.replace(/__fix$/, "") || x.name === c.step);
      if (s) ids.add(s.id);
    });
    return ids;
  }, [history.data, workflow]);
  const started = useMemo(() => {
    const times = [...(history.data ?? []).map((c) => c.at), ...(runs.data ?? []).map((r) => r.started_at)]
      .map((t) => Date.parse(t)).filter((t) => !Number.isNaN(t));
    return times.length ? Math.min(...times) : null;
  }, [history.data, runs.data]);
  return { history, est, job, jobs, actual, facts, totals, perAc, visited, started };
}

const STATUS_PILL: Record<string, [PillTone, string]> = {
  running: ["run", "running"], waiting: ["warn", "waiting for you"], done: ["ok", "done"], failed: ["bad", "failed"], stopped: ["idle", "stopped"],
};

/** The flow on one line: title, workflow, phase, status; tokens used against the cap, cost, time; Stop. */
/** keel 0.18.11 "4 of 9": the phase the flow is in, among the workflow's phases. */
function phaseAt(workflow: Workflow, thread: ThreadState): string {
  // the workflow's phases in step order (a step without a phase is in the one before it)
  const phases: string[] = [];
  let at = "";
  workflow.steps.forEach((s) => {
    at = s.phase ?? at;
    if (at && !phases.includes(at)) phases.push(at);
  });
  const n = thread.phase ? phases.indexOf(thread.phase) : -1;
  return n >= 0 && phases.length > 1 ? `${n + 1} of ${phases.length}` : "";
}

/** keel 0.18.11 what the running step does now, in one line: what its agent writes, else its newest step. */
function nowWords(job: Job | null, text?: string, steps?: JobStep[]): string {
  const line = (s?: string | null) => (s ?? "").split("\n").map((x) => x.trim()).filter(Boolean).at(-1) ?? "";
  if (text?.trim()) return `✎ ${line(text)}`;
  const last = steps?.at(-1);
  if (last) {
    // 0.18.15 a code step's job: its line already says what runs ("rung 4/12 · tests: $ npm test")
    if ((last as { job?: string }).job === "code") return line(last.text) || "working…";
    if (last.kind === "tool") return `> ${last.tool ?? "tool"} ${line(last.text)}`.trim();
    if (last.kind === "edit") return `✎ edits ${last.path ?? ""}`.trim();
    if (last.kind === "thinking") return "thinking…";
    if (line(last.text)) return line(last.text);
  }
  return job && job.steps_count ? "working…" : "starting…";
}

/** keel 0.18.11 the flow in one line, on top once the flow bar has scrolled away: state, phase, step, time, cost and
 *  the one thing to do. */
export function NowLine({ thread, workflow, started, onJump }: { thread: ThreadState; workflow: Workflow; started: number | null; onJump: () => void }) {
  const [tone, label] = STATUS_PILL[thread.status] ?? ["idle", thread.status];
  const u = thread.usage;
  const used = u.tokens_in + u.tokens_out + Math.floor((u.tokens_cached ?? 0) / 10);
  const cur = (thread.waiting?.step ?? thread.current ?? "").replace(/__fix$/, "");
  const step = workflow.steps.find((s) => s.id === cur);
  const live = thread.status === "running" || thread.status === "waiting";
  const at = phaseAt(workflow, thread);
  // 0.18.68 the bar sticks at the top: what sticks under it (the side column, the live line) starts below it, at its
  // real height (it wraps to two lines on a narrow page); before, the side column's top hid behind it
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    const root = document.documentElement;
    if (!el || typeof ResizeObserver === "undefined") return;
    const set = () => root.style.setProperty("--now-h", `${Math.ceil(el.getBoundingClientRect().height) + 12}px`);
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => { ro.disconnect(); root.style.removeProperty("--now-h"); };
  }, []);
  return (
    <div ref={ref} className="fl-now" role="region" aria-label="The flow now">
      <Pill tone={tone}>{label}</Pill>
      <span className="fl-now-t" title={thread.title}>{thread.title || workflow.name}</span>
      <span className="fl-now-m">
        <span className="fl-now-p">{at ? `phase ${at} · ${phaseTitle(thread.phase)}` : phaseTitle(thread.phase)}</span>
        {step && <span className="fl-now-s">{step.name}{thread.ac ? ` (${thread.ac})` : ""}</span>}
        <span className="fl-now-n">{[started ? shortDur((live ? Date.now() : Date.parse(thread.updated_at)) - started) : null, `${kfmt(used)} tokens · ${usd(u.cost_usd)}`]
          .filter(Boolean).join(" · ")}</span>
      </span>
      {live && <button type="button" className={thread.status === "waiting" ? "btn sm warn" : "btn sm"} onClick={onJump}>{thread.status === "waiting" ? "Go to it" : "Jump to current"}</button>}
    </div>
  );
}

export function FlowBar({ thread, workflow, estimate, started, onStart, onJump, extra, readOnly, branch }: {
  thread: ThreadState; workflow: Workflow; estimate: number | null; started: number | null; onStart: () => void; onJump?: () => void; extra?: React.ReactNode;
  /** v0.15.4 an earlier run: no Stop, Start or run mode here, and its own branch */
  readOnly?: boolean; branch?: string | null;
}) {
  const { project, toast } = useApp();
  const [busy, setBusy] = useState(false);
  // v0.15.3 Stop flow asks first
  const [confirmStop, setConfirmStop] = useState(false);
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
          <span>phase <b>{phaseTitle(thread.phase)}</b>{phaseAt(workflow, thread) ? <span className="sub"> ({phaseAt(workflow, thread)})</span> : null}</span>
          <Pill tone={tone}>{label}</Pill>
          {(() => {
            const b = (readOnly ? branch : project?.branch) ?? "—";
            return <span>branch <span className="mono fl-branch" title={b}>{b}</span></span>;
          })()}
          <CopyId id={thread.thread_id} />
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
        <div className="fl-stat"><span className="fl-stat-l">time</span><span className="fl-stat-v"><b>{started ? shortDur(end - started) : "—"}</b></span></div>
      </div>
      <div className="actions">
        {extra}
        {!readOnly && <>
        {onJump && live && <button className="btn" type="button" onClick={onJump} aria-label="Jump to current">
          <span className="fl-long">Jump to current</span><span className="fl-short" aria-hidden="true">Jump</span></button>}
        {live && <RunModeSwitch compact pid={thread.project_id} threadId={thread.thread_id} mode={thread.run_mode} />}
        {live ? (
          <button className="btn" type="button" disabled={busy} onClick={() => setConfirmStop(true)}>Stop flow</button>
        ) : <button className="btn primary" type="button" onClick={onStart}>Start a flow</button>}
        {live && <button className="btn ghost" type="button" onClick={onStart}
          title="It runs next to this one, in a worktree of its own">Start another flow</button>}
        </>}
      </div>
      {confirmStop && (
        <ConfirmStop title={thread.title} busy={busy} onCancel={() => setConfirmStop(false)} onStop={async () => {
          setBusy(true);
          try {
            await api.stopThread(thread.thread_id);
            toast("Stopped. Every finished step is saved: Resume goes on from there.");
            setConfirmStop(false);
          } catch (e) {
            toast(errorParts(e).message);
          } finally {
            setBusy(false);
          }
        }} />
      )}
    </header>
  );
}

export type FlowSide = "now" | "step" | "decisions" | "checkpoints";

/** The live canvas: the workflow's blocks with each one's state and tokens; the waiting gate's card sits under its
 * block, so you act where the flow stopped. */
export function LiveCanvas({ thread, workflow, tokens, actual, facts, totals, perAc, visited, waiting, canvas, onOpen, selected, still, onJump, job }: {
  thread: ThreadState; workflow: Workflow; tokens?: Record<string, number>; actual?: Record<string, number>; visited: Set<string>;
  /** keel 0.18.11 what each step really did (its block's facts line, result and tries), each step over every round,
   *  and each criterion over the loop */
  facts?: Record<string, StepFacts>;
  totals?: Record<string, { ms: number; cost: number }>;
  perAc?: Record<string, AcFacts>;
  /** "Go to it": the same as the side panel's (the answer card, its first button focused) */
  onJump?: () => void;
  /** the agent call at work in this flow (its block shows what it does now) */
  job?: Job | null;
  waiting: React.ReactNode; canvas: React.RefObject<BlocksHandle | null>; onOpen: (id: string) => void; selected: string | null;
  /** v0.15.4 an earlier run: the page does not scroll to its last block (its banner stays in view) */
  still?: boolean;
}) {
  const [view, setView] = useMapView("flow");
  const waitAt = thread.status === "waiting" ? (thread.waiting?.step ?? thread.current ?? "").replace(/__fix$/, "") : null;
  const onCanvas = !!waitAt && workflow.steps.some((s) => s.id === waitAt);
  const sendBacks = useMemo(() => sendBacksOf(thread.gate_log), [thread.gate_log]);
  const { liveText, liveSteps } = useApp();
  const liveLine = useMemo(() => {
    if (thread.status !== "running") return null;
    const id = job ? stepIdOf(workflow, job.step) : thread.current?.replace(/__fix$/, "") ?? null;
    if (!id) return null;
    return { id, text: nowWords(job ?? null, job ? liveText[job.id] : undefined, job ? liveSteps[job.id] : undefined) };
  }, [thread.status, thread.current, job, liveText, liveSteps, workflow.steps]);
  // keel 0.18.11 follow the live step until the person scrolls or moves around the steps; then "Jump to live"
  const cur = thread.current?.replace(/__fix$/, "") ?? null;
  const live = thread.status === "running" && !still;
  const [follow, setFollow] = useState(true);
  useEffect(() => {
    if (follow && live && cur && view === "blocks") canvas.current?.reveal(cur);
  }, [cur]);   // eslint-disable-line react-hooks/exhaustive-deps
  const away = () => { if (live) setFollow(false); };
  const failedAt = thread.status === "failed" && cur && workflow.steps.some((s) => s.id === cur) ? cur : null;
  const needs = waitAt && onCanvas ? waitAt : failedAt;
  const nameOf = (id: string) => workflow.steps.find((s) => s.id === id)?.name ?? id;
  return (
    <section className="panel fl-canvas" aria-label={`${workflow.name}: the steps`} onWheel={away} onTouchMove={away}
      onKeyDown={(e) => { if (["PageUp", "PageDown", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) away(); }}>
      <div className="panel-head">
        <h2>Steps</h2>
        <ViewToggle value={view} onChange={setView} />
      </div>
      {waiting && !onCanvas && <div className="panel-body" style={{ paddingBottom: 0 }}>{waiting}</div>}
      <div className={`panel-body sx-scroll fl-scroll${view === "blocks" ? " is-blocks" : ""}`}>
        {needs && view === "blocks" && (
          <div className={`fl-needs ${failedAt ? "is-bad" : ""}`} role="status">
            <span>{failedAt ? <><b>{nameOf(needs)}</b> failed. See why and what to do.</>
              : <>Needs you: <b>{nameOf(needs)}</b>{thread.ac ? ` for ${thread.ac}` : ""} waits for your answer.</>}</span>
            <button type="button" className="btn sm" onClick={() => (onJump ? onJump() : canvas.current?.reveal(needs, true))}>Go to it</button>
          </div>
        )}
        {live && !follow && view === "blocks" && (
          <div className="fl-live">
            <button type="button" onClick={() => { setFollow(true); if (cur) canvas.current?.reveal(cur); }}>Jump to live</button>
          </div>
        )}
        {view === "blocks" ? (
          <>
            <Blocks ref={canvas} steps={workflow.steps} tokens={thread.status === "done" ? undefined : tokens} actual={actual} facts={facts} totals={totals} acFacts={perAc} sendBacks={sendBacks}
              foldKey={thread.thread_id} liveLine={liveLine} acCount={thread.acs.length || 3}
              current={thread.current} status={thread.status} acs={thread.acs} currentAc={thread.ac} visited={visited} selected={selected}
              onOpenStep={onOpen} autoScroll={!still} inline={(id) => (onCanvas && id === waitAt ? <div className="sx-inline">{waiting}</div> : null)}
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
                acs={thread.acs} currentAc={thread.ac} onSelect={onOpen} selected={selected} />
            </Zoom>
            <AcChips acs={thread.acs} current={thread.ac} />
            <GraphLegend />
          </>
        )}
      </div>
    </section>
  );
}


/** The flow waits for you: for what, and a button to the answer card under its block. */
export function WaitsCard({ thread, onJump }: { thread: ThreadState; onJump: () => void }) {
  return (
    <div className="interrupt fl-waits">
      <b><Pill tone="warn">waiting for you</Pill> {thread.waiting?.title}</b>
      <span className="sub">The flow stopped here. Your answer goes in the card under its block.</span>
      <div className="row"><button className="btn warn" type="button" onClick={onJump}>Go to it</button></div>
    </div>
  );
}

/** v0.15.2 The workflow step a job names: its id, a review's `<id>__fix`, or its name. */
export function stepIdOf(workflow: Workflow, step: string): string | null {
  return workflow.steps.find((x) => x.id === step.replace(/__fix$/, "") || x.name === step)?.id ?? null;
}


export function perStep(w: Workflow, per?: { step: string; tokens: number }[]) {
  if (!per) return undefined;
  const m: Record<string, number> = {};
  per.forEach((p) => {
    const s = w.steps.find((x) => x.id === p.step || x.name === p.step);
    if (s) m[s.id] = (m[s.id] ?? 0) + p.tokens;
  });
  return m;
}

/** The ◆ card when the thread waits, the running card while an agent works, or how it ended. */
export function StatusCard({ pid, thread, workflow, job, onDone, onCheckpoints, noResume }: {
  pid: string; thread: ThreadState; workflow: Workflow; job: Job | null; onDone: () => Promise<void>;
  /** v0.15.3 show the Checkpoints tab (pick an earlier step to go back to) */
  onCheckpoints?: () => void;
  /** v0.15.4 an earlier run that cannot be resumed: this line instead of Resume */
  noResume?: string;
}) {
  const step = workflow.steps.find((s) => s.id === (thread.current ?? "").replace(/__fix$/, ""));
  const keelbot = useHasPage("helper");
  if (thread.status === "waiting" && thread.waiting) return <GateCard thread={thread} workflow={workflow} onDone={onDone} />;
  if (thread.status === "running") return <RunningCard step={step} name={step?.name ?? thread.current ?? ""} ac={thread.ac} job={job} />;
  void pid;
  const tone: PillTone = thread.status === "done" ? "ok" : thread.status === "failed" ? "bad" : "idle";
  return (
    <div className={thread.status === "failed" ? "errbox" : "running-card"} style={thread.status === "done" ? { borderColor: "var(--ok)", background: "var(--ok-soft)" } : undefined}>
      <b><Pill tone={tone}>{thread.status}</Pill> {thread.status === "done" ? "The flow is done." : thread.status === "failed" ? "The flow failed." : "The flow was stopped."}</b>
      {thread.error && <span className="sub">{thread.error}</span>}
      {thread.status === "failed" && keelbot && (
        <div className="row">
          <button type="button" className="btn sm" title="KeelBot reads the error and the project, says why it failed and proposes a fix you press"
            onClick={() => { askAssistant(fixRequest(`the flow “${thread.title}” at ${step?.name ?? thread.current ?? "its step"}`, { message: thread.error || "The flow failed." })); go("helper"); }}>
            Ask KeelBot why it failed
          </button>
        </div>
      )}
      {thread.status !== "done" && (noResume ? <span className="hint">{noResume}</span> : <ResumeRow thread={thread} onDone={onDone} onCheckpoints={onCheckpoints} />)}
      {(thread.status === "stopped" || thread.status === "failed") && !noResume && <FinishByHand thread={thread} onDone={onDone} />}
    </div>
  );
}

/** 0.18.74 a stopped flow you finished yourself: keel commits the folder's changes (its secret scan first), marks the
 *  criteria you tick done, and ends the flow as done. A field report: a stopped flow could not be finished by hand. */
function FinishByHand({ thread, onDone }: { thread: ThreadState; onDone: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const open_ = thread.acs.filter((a) => a.status !== "done");
  const [done, setDone] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!open)
    return (
      <div className="row">
        <button type="button" className="btn sm ghost" onClick={() => setOpen(true)}>Finish by hand…</button>
      </div>
    );
  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.finishByHand(thread.thread_id, done, note.trim());
      await onDone();
    } catch (e) {
      setError(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="fl-byhand" aria-label="Finish by hand" onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <span className="sub">keel commits what is changed in the folder now (after its secret check), marks the criteria
        you tick done, and ends this flow as done.</span>
      {open_.length > 0 && (
        <fieldset>
          <legend>Done by you</legend>
          {open_.map((a) => (
            <label key={a.id} className="row">
              <input type="checkbox" checked={done.includes(a.id)}
                onChange={(e) => setDone(e.target.checked ? [...done, a.id] : done.filter((x) => x !== a.id))} />
              <span><b>{a.id}</b> {a.title}</span>
            </label>
          ))}
        </fieldset>
      )}
      <label className="ss-label">What you did (goes in the commit)
        <input className="ss-input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="optional" />
      </label>
      {error && <span className="errline" role="alert">{error}</span>}
      <div className="row">
        <button type="submit" className="btn sm primary" disabled={busy}>{busy ? "Finishing…" : "Commit and finish"}</button>
        <button type="button" className="btn sm ghost" disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  );
}

/** v0.15.3 Stop flow asks first, in a small dialog over the page (Esc or Keep it running closes it). */
export function ConfirmStop({ title, busy, onCancel, onStop }: { title: string; busy: boolean; onCancel: () => void; onStop: () => void }) {
  const keep = useRef<HTMLButtonElement>(null);
  // The parent passes a new arrow on every render (every live event): keep the latest in a ref and
  // set up focus and the Esc key once, so a keyboard user's Tab is not pulled back to "Keep it running".
  const cancel = useRef(onCancel);
  cancel.current = onCancel;
  useEffect(() => {
    keep.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); cancel.current(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="fl-confirm-back" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="fl-confirm panel" role="alertdialog" aria-modal="true" aria-labelledby="fl-stop-h" aria-describedby="fl-stop-p">
        <h2 id="fl-stop-h">Stop this flow?</h2>
        <p id="fl-stop-p">“{title}” stops now, and the step that runs stops with it. Every finished step stays saved: you can
          Resume later from the last one, or go back to an earlier step.</p>
        <div className="row">
          <button className="btn warn" type="button" disabled={busy} onClick={onStop}>{busy ? "Stopping…" : "Stop the flow"}</button>
          <button ref={keep} className="btn" type="button" disabled={busy} onClick={onCancel}>Keep it running</button>
        </div>
      </div>
    </div>
  );
}

/** An agent (or plain code) works in this step: who, with which model, and where to watch it. */
export function RunningCard({ step, name, ac, job }: { step?: Step; name: string; ac?: string | null; job: Job | null }) {
  return (
    <div className="running-card">
      <b><Pill tone="run">running</Pill> {step ? step.name : name ? name : "starting…"}{ac ? ` — ${ac}` : ""}</b>
      <span className="sub">
        {job ? <>{job.agent} · <Prov p={job.provider} m={job.model} e={job.effort} /> · {job.steps_count} {job.steps_count === 1 ? "step" : "steps"}
          {job.mcp_calls ? ` · ${job.mcp_calls} keel ${job.mcp_calls === 1 ? "tool" : "tools"} used` : ""}</>
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
