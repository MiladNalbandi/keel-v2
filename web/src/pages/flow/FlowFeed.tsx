// keel 0.28 the redesign: the Flow page's middle column, "What happens". The flow as a calm feed, newest at the
// bottom: one turn per agent call of the flow (who, which model, the step, time and cost, and its answer's first line),
// its work folded to one line per tool call (a click opens the full step), the running agent's newest steps as they
// come, the line that says what you just answered, and the gate that waits for you with its keys. A click on a block
// (or J / K) follows one step: the feed shows only that step's turns.

import { Fragment, useMemo, useState, type ReactNode } from "react";
import type { Job, JobStep, StepRunFacts, ThreadState, Workflow } from "../../api";
import { Markdown } from "../../components/Markdown";
import { StepView, isShell, mcpOf, ms } from "../../components/StepView";
import { parseDiff } from "../../components/Code";
import { GoButton, Pill } from "../../components/ui";
import { shortDur, usd } from "../../format";
import { useJobSteps } from "../Live";
import { stepIdOf } from "./FlowParts";
import type { GateSaid } from "./Gate";

/** How many of the running agent's newest steps the feed shows before "Show all". */
const LIVE_LAST = 6;

const initials = (s: string) => (s.replace(/[^A-Za-z0-9]+/g, " ").trim().slice(0, 2) || "··").toLowerCase();

export function FlowFeed({ thread, workflow, runs, job, selected, onClear, gate, said, onBack, onDetails, readOnly }: {
  thread: ThreadState; workflow: Workflow;
  /** every agent call of this flow, added up per step (GET .../step-runs); null while it loads */
  runs: StepRunFacts[] | null;
  /** the agent call at work in this flow now */
  job: Job | null;
  /** the step the feed follows (a click on its block), or the whole flow */
  selected: string | null; onClear: () => void;
  /** the gate card, when the flow waits for you */
  gate: ReactNode;
  /** what you answered last (this page, this flow) */
  said: GateSaid | null;
  /** the way back: the Checkpoints in Details */
  onBack: () => void;
  /** Details › Now (Resume, Finish by hand, Ask KeelBot) */
  onDetails: () => void;
  readOnly?: boolean;
}) {
  const nameOf = (id: string | null, raw: string) => (id ? workflow.steps.find((s) => s.id === id)?.name : null) ?? raw;
  const turns = useMemo(() => (runs ?? [])
    .map((r) => ({ r, id: stepIdOf(workflow, r.step) }))
    .sort((a, b) => Date.parse(a.r.started_at) - Date.parse(b.r.started_at)), [runs, workflow]);
  const jobStep = job ? stepIdOf(workflow, job.step) : null;
  const liveId = job && job.status === "running" ? job.id : null;
  const shown = selected ? turns.filter((t) => t.id === selected) : turns;
  // the running call has no row yet: it shows as a turn of its own
  const extra = liveId && !turns.some((t) => t.r.call_id === liveId) && (!selected || selected === jobStep) ? job : null;
  const over = thread.status === "done" || thread.status === "failed" || thread.status === "stopped";
  const selName = selected ? nameOf(selected, selected) : "";
  // what you answered last sits where it happened: before the first turn that started after it (else at the end)
  const saidAt = said ? shown.findIndex((t) => Date.parse(t.r.started_at) > said.at) : -1;
  const saidLine = said && !gate ? (
    <li className="rdfl-said-li">
      <div className={`rdfl-said ${said.decision === "approve" ? "is-ok" : ""}`} role="status">
        <span aria-hidden="true">{said.decision === "approve" ? "✓" : "↩"}</span>
        <span className="rdfl-said-t"><b>You answered “{said.label}”</b> · {said.title}</span>
        {!readOnly && (
          <button type="button" className="btn sm ghost" onClick={onBack}
            title="keel cannot take an answer back by itself: rewind to the checkpoint before it, and the gate asks again">
            Go back to a checkpoint…
          </button>
        )}
      </div>
    </li>
  ) : null;
  return (
    <section className="rdfl-feed" aria-label="What happens">
      <div className="rdfl-feed-h">
        {selected ? (
          <>
            <span className="rdfl-tag">Showing: {selName}</span>
            <button type="button" className="btn sm ghost" onClick={onClear}>Show the whole flow <span aria-hidden="true">×</span></button>
          </>
        ) : <span className="sub">The whole flow · newest at the bottom · click a block to follow one step</span>}
      </div>
      <ol className="rdfl-turns" aria-label={selected ? `What ${selName} did` : "What the steps did"}>
        {shown.map(({ r, id }, n) => (
          <Fragment key={`${r.call_id}:${r.ac ?? ""}`}>
            {n === saidAt && saidLine}
            <Turn run={r} name={nameOf(id, r.step)} kind={workflow.steps.find((s) => s.id === id)?.kind ?? "agent"} live={r.call_id === liveId} />
          </Fragment>
        ))}
        {saidAt < 0 && saidLine}
        {extra && <LiveJobTurn job={extra} name={nameOf(jobStep, extra.step)} kind={workflow.steps.find((s) => s.id === jobStep)?.kind ?? "agent"} />}
        {!shown.length && !extra && (
          <li className="rdfl-none">
            {runs === null ? <span className="sub">Loading what the steps did…</span>
              : selected ? <><b>Not run in this flow yet</b><span className="sub">{selName} shows here once it runs.</span></>
                : <><b>Nothing has run yet</b><span className="sub">Each agent shows here as it works: what it says, and its tool calls, one line each.</span></>}
          </li>
        )}
      </ol>
      {gate && <div className="rdfl-gate">{gate}</div>}
      {over && (
        <div className={`rdfl-end is-${thread.status}`}>
          <span><b>{thread.status === "done" ? "Flow done" : thread.status === "failed" ? "Flow failed" : "Flow stopped"}</b>
            {thread.error ? <span className="sub"> · {thread.error}</span> : null}</span>
          {thread.status !== "done" && !readOnly && <button type="button" className="btn sm" onClick={onDetails}>What now: Details</button>}
        </div>
      )}
    </section>
  );
}

/** The head line of a turn: who, with which model, the step, how long and what it cost. */
function TurnHead({ who, model, effort, name, ac, time, cost, tries, status }: {
  who: string; model?: string | null; effort?: string | null; name: string; ac?: string | null; time?: string | null; cost?: number | null;
  tries?: number; status: string;
}) {
  return (
    <div className="rdfl-turn-h">
      <b>{who}</b>
      {model && <span className="mono">{model}{effort ? ` · ${effort}` : ""}</span>}
      <span>{name}{ac ? ` (${ac})` : ""}</span>
      {time && <span className="num">{time}</span>}
      {cost ? <span className="num">{usd(cost)}</span> : null}
      {tries && tries > 1 ? <span title={`${tries} tries: it was sent back or ran again`}>×{tries}</span> : null}
      {status === "running" ? <span className="rdfl-working">working</span>
        : status === "failed" ? <Pill tone="bad">failed</Pill> : status === "stopped" ? <Pill tone="idle">stopped</Pill> : null}
    </div>
  );
}

/** One agent call of the flow (its rounds added up): the head line, its answer's first line, and its steps on a click. */
function Turn({ run, name, kind, live }: { run: StepRunFacts; name: string; kind: string; live: boolean }) {
  const [open, setOpen] = useState(false);
  const who = run.agent ?? "plain code";
  const text = run.result?.trim();
  return (
    <li className={`rdfl-turn${live ? " is-live" : ""}`} data-step={run.step}>
      <span className={`rdfl-av k-${kind}`} aria-hidden="true">{run.agent ? initials(run.agent) : "{}"}</span>
      <div className="rdfl-turn-b">
        <TurnHead who={who} model={run.provider ? run.model : null} effort={run.effort} name={name} ac={run.ac} time={run.ms ? shortDur(run.ms) : null}
          cost={run.cost_usd} tries={run.tries} status={live ? "running" : run.status} />
        {text ? <p className="rdfl-say">{text}</p> : !live ? <p className="rdfl-say sub">No answer written.</p> : null}
        {live ? <CallSteps id={run.call_id} live /> : (
          <>
            <button type="button" className="rdfl-more" aria-expanded={open} onClick={() => setOpen(!open)}>
              {open ? "Hide what it did" : "Show what it did"}
            </button>
            {open && <CallSteps id={run.call_id} />}
          </>
        )}
      </div>
    </li>
  );
}

/** The running call before its first row: the job's own facts. */
function LiveJobTurn({ job, name, kind }: { job: Job; name: string; kind: string }) {
  return (
    <li className="rdfl-turn is-live" data-step={job.step}>
      <span className={`rdfl-av k-${kind}`} aria-hidden="true">{initials(job.agent)}</span>
      <div className="rdfl-turn-b">
        <TurnHead who={job.agent} model={job.model} effort={job.effort} name={name} ac={job.ac} status="running" />
        <CallSteps id={job.id} live />
      </div>
    </li>
  );
}

/** The steps of one call, calm: what the agent says as text, every tool call, read and edit as one line. `live`: the
 *  newest ones (Show all for the rest) and a link to watch it live. */
function CallSteps({ id, live }: { id: string; live?: boolean }) {
  const { steps, error } = useJobSteps(id);
  const [all, setAll] = useState(!live);
  const list = all ? steps : steps.slice(-LIVE_LAST);
  return (
    <div className="rdfl-steps" aria-live={live ? "polite" : undefined}>
      {error ? <span className="sub">Its steps could not be loaded: {error.message}</span>
        : !steps.length ? (live ? <><span className="rdfl-shim" /><span className="rdfl-shim short" /></> : <span className="sub">Loading its steps…</span>)
          : null}
      {!all && steps.length > LIVE_LAST && (
        <button type="button" className="rdfl-more" onClick={() => setAll(true)}>Show all {steps.length} steps</button>
      )}
      {list.map((s) => <FeedStep key={s.n} s={s} idPrefix={`rdfl-${id}`} />)}
      {live && (
        <div className="row rdfl-watch">
          <GoButton to="live" arg={id} className="btn sm ghost">Watch live</GoButton>
          <GoButton to="jobs" arg={id} className="btn sm ghost">Open job</GoButton>
        </div>
      )}
    </div>
  );
}

/** One line for a tool call, read or edit: what it is, and its outcome. */
function lineOf(s: JobStep): { mark: string; label: string; plus?: number; minus?: number } {
  const first = (t?: string) => (t ?? "").split("\n").map((x) => x.trim()).find(Boolean) ?? "";
  if (s.kind === "write" || s.kind === "edit") {
    const d = s.diff ? parseDiff(s.diff) : null;
    return { mark: "✎", label: `${s.kind === "write" ? "Wrote" : "Edited"} ${s.path ?? d?.files[0] ?? "a file"}`, plus: d?.added, minus: d?.removed };
  }
  if (s.kind === "read") return { mark: "≡", label: `Read ${s.path ?? "a file"}` };
  if (s.kind === "tool") {
    const m = mcpOf(s);
    if (m) return { mark: "◇", label: `${m.server} · ${m.tool || "tool"}${s.text?.trim() ? ` ${first(s.text)}` : ""}` };
    if (isShell(s.tool)) return { mark: "$", label: first(s.text) || "a command" };
    return { mark: "›", label: `${s.tool ?? "tool"} ${first(s.text)}`.trim() };
  }
  return { mark: s.kind === "error" ? "✕" : "!", label: first(s.text || s.output) || s.kind };
}

/** A step of a call in the feed: text as it is said, thinking as one quiet line, everything else as one line that opens
 *  the full step (diff, output, file). */
function FeedStep({ s, idPrefix }: { s: JobStep; idPrefix: string }) {
  const [open, setOpen] = useState(false);
  if (s.kind === "text" || s.kind === "answer") {
    return <div className="rdfl-text md" id={`${idPrefix}-${s.n}`}><Markdown text={s.text} breaks /></div>;
  }
  if (s.kind === "thinking") return <p className="rdfl-think" id={`${idPrefix}-${s.n}`}>thinking…</p>;
  const l = lineOf(s);
  return (
    <div className={`rdfl-tool${open ? " is-open" : ""}${s.kind === "error" || s.kind === "guard" || s.ok === false ? " is-bad" : ""}`} id={`${idPrefix}-${s.n}`}>
      <button type="button" className="rdfl-tool-l" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="rdfl-tool-m mono" aria-hidden="true">{l.mark}</span>
        <span className="rdfl-tool-t mono">{l.label}</span>
        {l.plus ? <span className="rdfl-plus num">+{l.plus}</span> : null}
        {l.minus ? <span className="rdfl-minus num">−{l.minus}</span> : null}
        {s.ok !== undefined && <span className={`rdfl-ok ${s.ok ? "" : "bad"}`}>{s.ok ? "ok" : "failed"}</span>}
        {s.ms !== undefined && <span className="sub num">{ms(s.ms)}</span>}
        <span className="rdfl-chev" aria-hidden="true">›</span>
      </button>
      {open && <div className="rdfl-tool-b"><StepView s={s} idPrefix={`${idPrefix}-x`} /></div>}
    </div>
  );
}
