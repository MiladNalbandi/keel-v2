import { useState } from "react";
import { api, errorParts, type Checkpoint, type EngineEvent, type ThreadState, type Workflow } from "../../api";
import { EVENT_FILTERS, eventRow, humanize, inFilter, isUsage, WHO_LABEL, type EventFilter, type EventRow, type QA } from "../../components/eventRows";
import { Markdown } from "../../components/Markdown";
import { FoldPanel } from "../../components/FoldPanel";
import { Confirm, ErrorBox } from "../../components/ui";
import { clock } from "../../format";
import { useApp } from "../../state";

/** How many saved steps the Events panel shows under the live events. */
const EARLIER = 8;
const FILTER_KEY = "keel2.events.filter";

function storedFilter(): EventFilter {
  try {
    const v = localStorage.getItem(FILTER_KEY);
    return EVENT_FILTERS.some(([k]) => k === v) ? (v as EventFilter) : "all";
  } catch {
    return "all";
  }
}

/** What happened in this flow, newest first: the events of this visit, then the saved steps (checkpoints) before them,
 * so a flow that waits still says how it got here. v0.15.2 `step`: only that step's events since it started (the
 * step's Now view). Hide folds it (this browser remembers it); ⤢ shows every event and saved step in a large view.
 * Each event reads as a row (eventRows.ts): an agent's answer as folded Markdown, a gate's decision as a badge with its
 * answers, a step that starts or ends as a divider; usage events show only under Usage. */
export function EventsPanel({ pid, thread, history, onAll, step, title = "Events", readOnly, workflow }: {
  pid: string; thread: ThreadState; history?: Checkpoint[] | null; onAll?: () => void; step?: string; title?: string;
  /** v0.15.4 an earlier run: no "Allow this file" */
  readOnly?: boolean;
  /** the flow's workflow: a step's block name beside its id */
  workflow?: Workflow;
}) {
  const [filter, setFilterState] = useState<EventFilter>(storedFilter);
  const setFilter = (f: EventFilter) => {
    setFilterState(f);
    try {
      if (f === "all") localStorage.removeItem(FILTER_KEY);
      else localStorage.setItem(FILTER_KEY, f);
    } catch { /* private window: not remembered */ }
  };
  const list = (all?: boolean) => <EventList pid={pid} thread={thread} history={history} step={step} all={all} readOnly={readOnly} workflow={workflow}
    filter={filter} setFilter={setFilter} />;
  return (
    <FoldPanel id="flow.events" title={title}
      extra={onAll && (history?.length ?? 0) > 0 ? <button className="btn sm ghost" type="button" onClick={onAll}>All checkpoints</button> : undefined}
      big={() => list(true)}>
      {list()}
    </FoldPanel>
  );
}

/** v0.15.2 One step's events since it last started (a step that runs again, for the next criterion, starts over). */
function sinceStart(events: EngineEvent[], step: string): EngineEvent[] {
  const mine = events.filter((e) => (e.step ?? "").replace(/__fix$/, "") === step);
  const at = mine.map((e) => e.type).lastIndexOf("step.started");
  return at > 0 ? mine.slice(at) : mine;
}

/** The event rows: the newest 12 events (of the filter) and 8 saved steps before them; `all` (the big view): every one. */
function EventList({ pid, thread, history, step, all, readOnly, workflow, filter, setFilter }: {
  pid: string; thread: ThreadState; history?: Checkpoint[] | null; step?: string; all?: boolean; readOnly?: boolean; workflow?: Workflow;
  filter: EventFilter; setFilter: (f: EventFilter) => void;
}) {
  const { recent } = useApp();
  const nameOf = (id: string) => workflow?.steps.find((s) => s.id === id)?.name;
  const ofThread = recent.filter((e: EngineEvent) => e.thread_id === thread.thread_id && e.type !== "agent.step");
  const shown = (step ? sinceStart(ofThread, step) : ofThread).map((e) => ({ e, row: eventRow(e, nameOf) })).filter((x) => inFilter(x.row, filter));
  const mine = (all ? shown : shown.slice(-12)).slice().reverse();
  const hidden = filter === "all" ? ofThread.filter((e) => isUsage(e.type)).length : 0;
  const [asking, setAsking] = useState<EngineEvent | null>(null);
  // the saved steps from before the oldest live event (the history is newest first); none in a step's Now view or
  // under a filter (they are steps, not events of that kind)
  const oldest = mine.length ? Date.parse(mine[mine.length - 1].e.at) : Infinity;
  const before = step || filter !== "all" ? [] : (history ?? []).filter((c) => !(Date.parse(c.at) >= oldest));
  const earlier = all ? before : before.slice(0, EARLIER);
  return (
    <>
      <div className="ev-bar">
        <div className="tabs ev-filter" role="group" aria-label="Which events">
          {EVENT_FILTERS.map(([k, label]) => (
            <button key={k} type="button" aria-pressed={filter === k} onClick={() => setFilter(k)}
              title={k === "all" ? "Every event but token usage" : k === "usage" ? "Token usage: an agent's tokens so far" : undefined}>{label}</button>
          ))}
        </div>
        <span className="ev-order sub" data-testid="events-order">↓ Newest first{hidden ? ` · ${hidden} usage hidden` : ""}</span>
      </div>
      <div className="events" aria-label="What happened, newest first">
        {mine.map(({ e, row }, i) => {
          const path = e.type === "guard.refused" && typeof e.data?.path === "string" ? e.data.path : null;
          return (
            <EventRowView key={i} at={e.at} row={row} extra={path && !readOnly && asking !== e ? (
              <button className="btn sm ghost" type="button" onClick={() => setAsking(e)} aria-label={`Allow ${path} in this phase`}>Allow this file in this phase</button>
            ) : null} />
          );
        })}
        {mine.length > 0 && earlier.length > 0 && <span className="ev-sep sub">Earlier</span>}
        {earlier.map((c) => (
          <div key={c.id} className="ev-cp" data-testid="event-checkpoint">
            <span className="t mono sub">{clock(c.at, false)}</span>
            <span title={c.note}><b>{humanize(c.step)}</b> <span className="sub">{c.note}</span></span>
          </div>
        ))}
        {before.length > earlier.length && <span className="hint">{before.length - earlier.length} older steps are under Checkpoints.</span>}
        {!mine.length && !earlier.length && (
          filter !== "all" ? <span className="sub">No {EVENT_FILTERS.find(([k]) => k === filter)?.[1].toLowerCase()} events in this visit yet.</span>
            : step ? <span className="sub">Nothing happened in this step yet. Its events show here as they happen.</span>
              : history === undefined || (history !== null && !history.length)
                ? <span className="sub">Nothing happened yet. Events show here as they happen.</span>
                : history === null ? <span className="sub loading">Loading what happened…</span> : null
        )}
      </div>
      {asking && <UnlockConfirm pid={pid} path={String(asking.data.path)} phase={typeof asking.data.phase === "string" ? asking.data.phase : thread.phase} onClose={() => setAsking(null)} />}
    </>
  );
}

/** One event row: its time, its kind (a coloured label), then what happened. A step that starts or ends is a divider. */
export function EventRowView({ at, row, extra }: { at: string; row: EventRow; extra?: React.ReactNode }) {
  if (row.divider) {
    return (
      <div className={`ev-div ev-${row.divider}`} data-kind="phase" role="separator" aria-label={row.text}>
        <span className="t mono sub">{clock(at, false)}</span><span className="ev-div-t">{row.text}</span>
      </div>
    );
  }
  return (
    <div className="ev-row" data-kind={row.kind}>
      <span className="t mono sub">{clock(at, false)}</span>
      <span className={`k k-${row.kind}`}>{row.label}</span>
      <div className="ev-body">
        {row.decision ? (
          <div className="ev-head">
            <span className={`pill p-${row.decision.tone}`} data-testid="decision-badge">{row.decision.label}</span>
            <b>{row.gate ?? row.text}</b>
            {row.by && row.by !== "you" && <span className="ev-by">by {WHO_LABEL[row.by]}</span>}
          </div>
        ) : <span className="ev-text">{row.text}</span>}
        {row.answers && <QaList answers={row.answers} />}
        {row.note && (row.decision ? <FoldMd text={row.note} /> : <span className="sub">{row.note}</span>)}
        {row.md && <FoldMd text={row.md} />}
        {extra && <div>{extra}</div>}
      </div>
    </div>
  );
}

/** "Answers to your questions" as a numbered question → answer list. */
export function QaList({ answers }: { answers: QA[] }) {
  return (
    <ol className="ev-qa" aria-label="Your answers">
      {answers.map((a) => (
        <li key={a.n} value={a.n}>
          <span className="ev-q">{a.q}</span>
          <span className="ev-a"><span aria-hidden="true">→ </span>{a.a || <span className="sub">not answered (the recommended option)</span>}</span>
        </li>
      ))}
    </ol>
  );
}

/** Markdown folded to about three lines, with Show more / Show less when it is longer. */
export function FoldMd({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 220 || text.split("\n").filter((l) => l.trim()).length > 3;
  return (
    <div className="ev-md-wrap">
      <div className={`md ev-md${long && !open ? " is-folded" : ""}`}><Markdown text={text} breaks fold={8} /></div>
      {long && <button type="button" className="linkbtn ev-more" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Show less" : "Show more"}</button>}
    </div>
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
