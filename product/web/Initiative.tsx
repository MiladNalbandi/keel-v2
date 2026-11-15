// One initiative: where it is (the tracker), what waits for whom (the Next card), its documents by version, the
// questions, the disagreements, the plan and its delivery, the runs, the history and the follow-ups.

import { useEffect, useState } from "react";
import {
  Async,
  ClarifyForm,
  Confirm,
  Empty,
  Markdown,
  Panel,
  Pill,
  Tabs,
  answersOf,
  useLoad,
  type ClarifyAnswers,
  type ClarifyQuestion,
} from "@keel/web-sdk";
import {
  productApi,
  STAGE_LABEL,
  type Detail,
  type DocView,
  type Disagreement,
  type FollowUp,
  type LinkedTicket,
  type PlanView,
  type Question,
  type RepoOwner,
  type SendResult,
  type Story,
  type StoryEdit,
  type Team,
  type TicketPreview,
} from "./productApi";
import {
  days,
  fmtDate,
  Key,
  ProductHead,
  StatusPill,
  Tracker,
  useAct,
  usePageKeys,
} from "./shared";

const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

type Tab =
  | "brief"
  | "questions"
  | "impact"
  | "decision"
  | "plan"
  | "delivery"
  | "outcome"
  | "runs"
  | "history";

const TAB_OF: Record<string, Tab> = {
  idea: "brief",
  brief: "brief",
  impact: "impact",
  decision: "decision",
  plan: "plan",
  delivery: "delivery",
  outcome: "outcome",
  done: "outcome",
};

export function InitiativePage({ id }: { id: string }) {
  const r = useLoad(`initiative:${id}`, () => productApi.get(id));
  return (
    <div className="pd pdx">
      <Async r={r} what={`Loading ${id}`}>
        {(d) => (
          <Loaded
            d={d}
            set={(n) => r.setData(n)}
            reload={() => void r.reload()}
          />
        )}
      </Async>
    </div>
  );
}

function Loaded({
  d,
  set,
  reload,
}: {
  d: Detail;
  set: (d: Detail) => void;
  reload: () => void;
}) {
  const i = d.initiative;
  const [tab, setTab] = useState<Tab>(TAB_OF[i.stage] ?? "brief");
  // the stage moved on (a run ended): show its tab
  const [shownStage, setShownStage] = useState(i.stage);
  if (i.stage !== shownStage) {
    setShownStage(i.stage);
    setTab(TAB_OF[i.stage] ?? tab);
  }
  const act = useAct();
  const waiting = !!d.stage.waiting;
  const deck = d.docs.deck;
  const doIt = async (name: string, fn: () => Promise<Detail>) => {
    const n = await act.run(name, fn);
    if (n) set(n);
  };
  const open = d.questions.filter((q) => q.status === "open").length;
  const tabs: [Tab, string][] = [
    ["brief", "Brief"],
    ["questions", `Questions${open ? ` (${open})` : ""}`],
    ["impact", "Impact"],
    ["decision", "Decision"],
    ["plan", "Plan"],
    ["delivery", "Delivery"],
    ["outcome", "Outcome"],
    ["runs", `Runs (${d.runs.length})`],
    ["history", "History"],
  ];
  return (
    <>
      <ProductHead
        crumbs={[["Initiatives", "#/initiatives"], [i.id]]}
        title={
          <>
            {i.title} <StatusPill status={i.status} waiting={waiting} />
          </>
        }
        sub={i.idea}
        actions={
          <>
            {deck && (
              <a
                className="btn"
                href={productApi.deckUrl(i.id)}
                target="_blank"
                rel="noreferrer"
              >
                Presentation v{deck.version}
              </a>
            )}
            {i.status === "running" && (
              <button
                className="btn"
                type="button"
                disabled={!!act.busy}
                onClick={() => void doIt("stop", () => productApi.stop(i.id))}
              >
                Stop the run
              </button>
            )}
            {i.status === "parked" ? (
              <button
                className="btn"
                type="button"
                disabled={!!act.busy}
                onClick={() =>
                  void doIt("unpark", () => productApi.unpark(i.id))
                }
              >
                Back from Not now
              </button>
            ) : (
              i.stage !== "done" && (
                <ParkButton
                  onPark={(revisit, why) =>
                    doIt("park", () => productApi.park(i.id, { revisit, why }))
                  }
                />
              )
            )}
          </>
        }
      />
      <Tracker stage={i.stage} status={i.status} />
      <div className="pd-main">
        <div className="pd-left">
          <NextCard d={d} set={set} reload={reload} />
          {act.box}
          <Tabs
            label="Initiative"
            value={tab}
            onChange={setTab}
            options={tabs}
          />
          <div className="pd-tab" role="tabpanel">
            {tab === "brief" && (
              <DocTab d={d} kind="brief" onChanged={reload} />
            )}
            {tab === "questions" && <Questions d={d} onChanged={reload} />}
            {tab === "impact" && <ImpactTab d={d} onChanged={reload} />}
            {tab === "decision" && <DecisionTab d={d} onChanged={reload} />}
            {tab === "plan" && (
              <PlanTab d={d} onChanged={reload} set={set} />
            )}
            {tab === "delivery" && <DeliveryTab d={d} reload={reload} />}
            {tab === "outcome" && (
              <DocTab
                d={d}
                kind="outcome"
                onChanged={reload}
                empty="The outcome comes after the release: keel compares the metric with the brief's promise."
              />
            )}
            {tab === "runs" && <Runs d={d} />}
            {tab === "history" && <History d={d} />}
          </div>
        </div>
        <aside className="pd-side">
          <Panel title="Facts">
            <dl className="pd-facts">
              <dt>Owner</dt>
              <dd>{i.owner ?? "—"}</dd>
              <dt>Stage</dt>
              <dd>{STAGE_LABEL[i.stage] ?? i.stage}</dd>
              {i.option && (
                <>
                  <dt>Option</dt>
                  <dd>{i.option}</dd>
                </>
              )}
              {i.why_now && (
                <>
                  <dt>Why now</dt>
                  <dd>{i.why_now}</dd>
                </>
              )}
              {i.outcome_hope && (
                <>
                  <dt>Hoped outcome</dt>
                  <dd>{i.outcome_hope}</dd>
                </>
              )}
              {i.metric && (
                <>
                  <dt>Metric</dt>
                  <dd>{i.metric}</dd>
                </>
              )}
              <dt>Teams</dt>
              <dd>{d.teams.join(", ") || "—"}</dd>
              <dt>Repos</dt>
              <dd>{d.repos.map((r) => r.name).join(", ") || "—"}</dd>
            </dl>
          </Panel>
          <LinkedTickets d={d} reload={reload} />
          <Disagreements d={d} set={set} reload={reload} />
          <FollowUps d={d} onChanged={reload} />
        </aside>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- B6: tickets already on the Tasks boards

/** keel's task statuses in words (the Tasks plugin's). */
const TASK_STATUS: Record<string, string> = {
  todo: "To do", in_progress: "In progress", in_review: "In review", testing_pp: "Testing (PP)", ready_prod: "Ready",
  done: "Done", cancelled: "Cancelled", blocked: "Blocked",
};

/** One ticket's line: key (a link to Jira), title, keel's status, Jira's status, project, assignee, git work. */
function TicketLine({ t }: { t: LinkedTicket }) {
  const work = [t.branches ? `${t.branches} branch${t.branches === 1 ? "" : "es"}` : "", t.commits ? `${t.commits} commit${t.commits === 1 ? "" : "s"}` : ""]
    .filter(Boolean).join(", ");
  return (
    <>
      <span className="pd-card-top">
        {t.url ? <a className="mono" href={t.url} target="_blank" rel="noreferrer">{t.key}</a> : <b className="mono">{t.key}</b>}
        {t.status ? <Pill tone={t.status === "done" ? "ok" : t.status === "blocked" ? "bad" : t.status === "todo" ? "idle" : "run"}>{TASK_STATUS[t.status] ?? t.status}</Pill>
          : <Pill tone="warn">not on a Tasks board now</Pill>}
      </span>
      {t.title && <span>{t.title}</span>}
      <span className="sub">
        {[t.external_status ? `Jira: ${t.external_status}` : t.source === "local" ? "local task" : "", t.project_id ?? "", t.assignee ?? "", work]
          .filter(Boolean).join(" · ")}
      </span>
    </>
  );
}

/** The tickets on the teams' Tasks boards that belong to this initiative, and Add tickets to link more. */
function LinkedTickets({ d, reload }: { d: Detail; reload: () => void }) {
  const id = d.initiative.id;
  const [list, setList] = useState<LinkedTicket[]>(d.linked ?? []);
  useEffect(() => setList(d.linked ?? []), [d.linked]);
  const [adding, setAdding] = useState(false);
  const act = useAct();
  const unlink = async (key: string) => {
    const next = await act.run(`unlink:${key}`, () => productApi.unlinkTicket(id, key));
    if (next) {
      setList(next);
      reload();
    }
  };
  return (
    <Panel title="Tickets">
      <p className="sub">Tickets on the teams' Tasks boards (Jira or local) that belong to this initiative. Nothing changes in Jira.</p>
      {list.length === 0 ? (
        <p className="sub" data-testid="linked-empty">No ticket linked yet.</p>
      ) : (
        <ul className="pd-list" aria-label="Linked tickets">
          {list.map((t) => (
            <li key={t.key} className="pd-card pd-ticket">
              <TicketLine t={t} />
              <span className="pd-actions">
                <button className="btn sm ghost" type="button" disabled={act.busy !== null} aria-label={`Unlink ${t.key}`}
                  onClick={() => void unlink(t.key)}>Unlink</button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {act.box}
      {adding ? (
        <AddTickets id={id} onLinked={(next) => { setList(next); reload(); }} onClose={() => setAdding(false)} />
      ) : (
        <div className="pd-actions"><button className="btn sm" type="button" onClick={() => setAdding(true)}>Add tickets</button></div>
      )}
    </Panel>
  );
}

/** Search the Tasks boards (the initiative's repos, or every project) and link the chosen tickets. */
function AddTickets({ id, onLinked, onClose }: { id: string; onLinked: (l: LinkedTicket[]) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [mine, setMine] = useState(true);
  const [found, setFound] = useState<LinkedTicket[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const act = useAct();
  useEffect(() => {
    let live = true;
    const t = window.setTimeout(() => {
      void act.run("search", () => productApi.ticketCandidates(id, q.trim(), !mine)).then((r) => { if (live && r) setFound(r); });
    }, 250);
    return () => { live = false; window.clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, q, mine]);
  const toggle = (t: LinkedTicket) => setPicked((p) => {
    const n = new Set(p);
    if (n.has(t.key)) n.delete(t.key); else n.add(t.key);
    return n;
  });
  const link = async () => {
    const chosen = (found ?? []).filter((t) => picked.has(t.key));
    const next = await act.run("link", () => productApi.linkTickets(id, { tasks: chosen.map((t) => t.task_id!).filter(Boolean) }));
    if (next) {
      onLinked(next);
      onClose();
    }
  };
  return (
    <div className="pd-form" role="group" aria-label="Add tickets">
      <div className="pd-row">
        <input type="search" aria-label="Search tickets" placeholder="key or title" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="chk"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> only this initiative's repos</label>
      </div>
      {found === null ? <p className="sub">Searching the Tasks boards…</p> : found.length === 0 ? (
        <p className="sub">No ticket found{q.trim() ? ` for “${q.trim()}”` : ""}. Sync Jira on the Tasks board, or search every project.</p>
      ) : (
        <ul className="pd-list" aria-label="Tickets found">
          {found.map((t) => (
            <li key={t.key} className="pd-card pd-ticket">
              <label className="chk">
                <input type="checkbox" checked={picked.has(t.key)} onChange={() => toggle(t)} aria-label={`Pick ${t.key}`} />
                <span><TicketLine t={t} /></span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {act.box}
      <div className="pd-actions">
        <button className="btn sm primary" type="button" disabled={!picked.size || act.busy !== null} onClick={() => void link()}>
          {picked.size ? `Link ${picked.size} ticket${picked.size === 1 ? "" : "s"}` : "Link tickets"}
        </button>
        <button className="btn sm" type="button" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

function ParkButton({
  onPark,
}: {
  onPark: (revisit?: string, why?: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [revisit, setRevisit] = useState("");
  const [why, setWhy] = useState("");
  if (!open)
    return (
      <button className="btn" type="button" onClick={() => setOpen(true)}>
        Not now…
      </button>
    );
  return (
    <span className="pd-inline">
      <label className="sr-only" htmlFor="park-date">
        Look again on
      </label>
      <input
        id="park-date"
        type="date"
        value={revisit}
        onChange={(e) => setRevisit(e.target.value)}
      />
      <input
        type="text"
        aria-label="Why not now"
        placeholder="why (optional)"
        value={why}
        onChange={(e) => setWhy(e.target.value)}
      />
      <button
        className="btn"
        type="button"
        onClick={() =>
          void onPark(revisit || undefined, why || undefined).then(() =>
            setOpen(false),
          )
        }
      >
        Park it
      </button>
    </span>
  );
}

// ---------------------------------------------------------------- what waits now

function NextCard({
  d,
  set,
  reload,
}: {
  d: Detail;
  set: (d: Detail) => void;
  reload: () => void;
}) {
  const i = d.initiative;
  const w = d.stage.waiting;
  const act = useAct();
  const [why, setWhy] = useState("");
  const [note, setNote] = useState("");
  const [picked, setPicked] = useState<ClarifyAnswers>({});
  const [typed, setTyped] = useState<ClarifyAnswers>({});
  const [option, setOption] = useState("");
  const [metric, setMetric] = useState("");
  const doIt = async (name: string, fn: () => Promise<Detail>) => {
    const n = await act.run(name, fn);
    if (n) {
      set(n);
      setWhy("");
      setNote("");
    }
  };
  const busy = act.busy !== null;
  // the keys the buttons show: A approves (or Go), L is Not now, R goes to Send back, ↩ in a field sends it
  const keys: Record<string, () => void> = {};
  const focusBack = () => document.getElementById("pd-sendback")?.focus();
  let body: React.ReactNode = null;
  if (w?.kind === "clarify" && w.questions?.length) {
    const qs = w.questions as ClarifyQuestion[];
    body = (
      <>
        <ClarifyForm
          questions={qs}
          picked={picked}
          typed={typed}
          onPick={(q, l) => setPicked((p) => ({ ...p, [q]: l }))}
          onType={(q, t) => setTyped((p) => ({ ...p, [q]: t }))}
        />
        <div className="pd-actions">
          <button
            className="btn gate"
            type="button"
            disabled={busy}
            onClick={() =>
              void doIt("answers", () =>
                productApi.approve(i.id, {
                  answers: answersOf(qs, picked, typed),
                }),
              )
            }
          >
            Send my answers
          </button>
          <span className="sub">keel then writes the brief.</span>
        </div>
      </>
    );
  } else if (w && i.stage === "decision") {
    body = (
      <>
        <p className="sub">
          Read the memo and the presentation. Go starts the plan with the option
          you pick; Not now parks the idea until a date.
        </p>
        <div className="pd-row">
          <div className="field">
            <label htmlFor="dec-option">Option</label>
            <input
              id="dec-option"
              type="text"
              value={option}
              onChange={(e) => setOption(e.target.value)}
              placeholder="keel's recommendation"
            />
          </div>
          <div className="field pd-grow">
            <label htmlFor="dec-why">Why</label>
            <input
              id="dec-why"
              type="text"
              value={why}
              onChange={(e) => setWhy(e.target.value)}
            />
          </div>
        </div>
        <div className="pd-actions">
          <button
            className="btn gate"
            type="button"
            disabled={busy}
            onClick={(keys.a = () =>
              void doIt("go", () =>
                productApi.decide(i.id, {
                  choice: "go",
                  option: option || undefined,
                  why: why || undefined,
                }),
              ))}
          >
            Go <Key k="A" />
          </button>
          <button
            className="btn"
            type="button"
            disabled={busy}
            onClick={(keys.l = () =>
              void doIt("not_now", () =>
                productApi.decide(i.id, {
                  choice: "not_now",
                  why: why || undefined,
                }),
              ))}
          >
            Not now <Key k="L" />
          </button>
          <SendBack
            note={note}
            setNote={setNote}
            busy={busy}
            onSend={() =>
              void doIt("back", () => productApi.sendBack(i.id, note))
            }
          />
        </div>
      </>
    );
  } else if (w) {
    body = (
      <>
        {w.detail && (
          <div className="pd-detail">
            <Markdown text={w.detail} fold={12} />
          </div>
        )}
        <div className="field">
          <label htmlFor="gate-why">A note (optional)</label>
          <input
            id="gate-why"
            type="text"
            value={why}
            onChange={(e) => setWhy(e.target.value)}
          />
        </div>
        <div className="pd-actions">
          <button
            className="btn gate"
            type="button"
            disabled={busy}
            onClick={(keys.a = () =>
              void doIt("approve", () =>
                productApi.approve(i.id, { why: why || undefined }),
              ))}
          >
            {i.stage === "plan"
              ? "Agree the plan"
              : i.stage === "impact"
                ? "Confirm the impact"
                : i.stage === "outcome"
                  ? "Close the initiative"
                  : "Approve"}{" "}
            <Key k="A" />
          </button>
          <SendBack
            note={note}
            setNote={setNote}
            busy={busy}
            onSend={() =>
              void doIt("back", () => productApi.sendBack(i.id, note))
            }
          />
        </div>
      </>
    );
  } else if (i.stage === "idea" && i.status !== "running") {
    body = (
      <div className="pd-actions">
        <button
          className="btn primary"
          type="button"
          disabled={busy}
          onClick={() => void doIt("start", () => productApi.start(i.id))}
        >
          Start discovery
        </button>
      </div>
    );
  } else if (
    i.status === "failed" ||
    (i.status === "ready" && !["delivery", "outcome", "done"].includes(i.stage))
  ) {
    body = (
      <div className="pd-row">
        <input
          type="text"
          aria-label="A note for keel"
          placeholder="a note for keel (optional)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <button
          className="btn primary"
          type="button"
          disabled={busy}
          onClick={() =>
            void doIt("rerun", () =>
              productApi.rerun(i.id, { note: note || undefined }),
            )
          }
        >
          Run the {STAGE_LABEL[i.stage]?.toLowerCase() ?? i.stage} again
        </button>
      </div>
    );
  } else if (i.stage === "delivery") {
    const sent = d.plan?.epics.some((e) =>
      e.stories.some((s) => s.task_id || s.jira_key),
    );
    body = sent ? (
      <div className="pd-actions">
        <button
          className="btn primary"
          type="button"
          disabled={busy}
          onClick={() => void doIt("released", () => productApi.released(i.id))}
        >
          It is released
        </button>
        <span className="sub">
          keel then plans the outcome checks in 4 and 8 weeks.
        </span>
      </div>
    ) : (
      <Handoff id={i.id} tasksLeft onDone={reload} />
    );
  } else if (i.stage === "outcome" && i.status !== "running") {
    body = (
      <div className="pd-row">
        <input
          type="text"
          aria-label="The metric now"
          placeholder="the metric now, e.g. conversion 2.4%"
          value={metric}
          onChange={(e) => setMetric(e.target.value)}
        />
        <button
          className="btn primary"
          type="button"
          disabled={busy || !metric.trim()}
          onClick={() =>
            void doIt("outcome", () => productApi.outcome(i.id, metric))
          }
        >
          Check the outcome
        </button>
      </div>
    );
  }
  if (w) keys.r = focusBack;
  if (busy) for (const k of Object.keys(keys)) delete keys[k];
  usePageKeys(keys);
  const running = i.status === "running";
  if (!body && running)
    body = (
      <>
        <div className="pd-shims" aria-hidden="true">
          <div className="pd-shim" style={{ width: "86%" }} />
          <div className="pd-shim" style={{ width: "64%" }} />
          <div className="pd-shim" style={{ width: "72%" }} />
        </div>
        <span className="sub">
          You can leave: the Inbox tells you when it waits.
        </span>
      </>
    );
  return (
    <section
      className={`pd-next${w ? " is-wait" : running ? " is-run" : ""}`}
      aria-label="Next"
    >
      <div className="pd-next-head">
        <span className="pd-next-label">Next</span>
        <b>{cap(w?.title && w.kind !== "clarify" ? w.title : d.stage.next)}</b>
        {i.status === "running" && (
          <Pill tone="run">
            keel works on the {STAGE_LABEL[i.stage]?.toLowerCase()}
          </Pill>
        )}
      </div>
      {body}
      {act.box}
    </section>
  );
}

function SendBack({
  note,
  setNote,
  busy,
  onSend,
}: {
  note: string;
  setNote: (s: string) => void;
  busy: boolean;
  onSend: () => void;
}) {
  return (
    <span className="pd-inline pd-sendback">
      {/* several lines: what to change is often more than a few words */}
      <textarea
        id="pd-sendback"
        className="pd-text short"
        rows={3}
        aria-label="What to change"
        placeholder="what to change (one or more lines)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <button
        className="btn"
        type="button"
        disabled={busy || !note.trim()}
        onClick={onSend}
      >
        Send back <Key k="R" />
      </button>
    </span>
  );
}

/** "Send to keel Tasks" while a story has no keel task. */
function Handoff({
  id,
  tasksLeft,
  onDone,
}: {
  id: string;
  tasksLeft: boolean;
  onDone: () => void;
}) {
  const act = useAct();
  const [notes, setNotes] = useState<string[]>([]);
  const send = async () => {
    const r = await act.run("tasks", () => productApi.handoff(id, "tasks"));
    if (r) {
      setNotes(r.notes);
      onDone();
    }
  };
  if (!tasksLeft && notes.length === 0) return null;
  return (
    <>
      <p className="sub">
        Each story becomes a task in its repo's project, with its criteria; the
        teams start it as a keel flow. Or preview its Jira tickets in Delivery.
      </p>
      {tasksLeft && (
        <div className="pd-actions">
          <button
            className="btn primary"
            type="button"
            disabled={act.busy !== null}
            onClick={() => void send()}
          >
            Send to keel Tasks
          </button>
        </div>
      )}
      {notes.length > 0 && (
        <ul className="pd-notes">
          {notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {act.box}
    </>
  );
}

const ACTION_TONE = { create: "run", exists: "ok", blocked: "bad" } as const;

/** Jira: first a preview of every ticket (and every problem), then a confirmed send of what is new. */
function JiraTickets({
  id,
  left,
  onDone,
}: {
  id: string;
  left: boolean;
  onDone: () => void;
}) {
  const act = useAct();
  const [preview, setPreview] = useState<TicketPreview | null>(null);
  const [asking, setAsking] = useState(false);
  const [result, setResult] = useState<SendResult | null>(null);
  const creates = preview?.rows.filter((r) => r.action === "create").length ?? 0;
  const load = async () => {
    setResult(null);
    setAsking(false);
    const p = await act.run("preview", () => productApi.ticketPreview(id));
    if (p) setPreview(p);
  };
  const send = async () => {
    const r = await act.run("send", () => productApi.ticketSend(id, []));
    setAsking(false);
    if (r) {
      setResult(r);
      setPreview(null);
      onDone();
    }
  };
  if (!left && !result) return null;
  return (
    <section className="pd-jira" aria-label="Jira tickets">
      {left && (
        <div className="pd-actions">
          <button
            className={preview ? "btn" : "btn primary"}
            type="button"
            disabled={act.busy !== null}
            onClick={() => void load()}
          >
            {preview ? "Preview again" : "Preview Jira tickets"}
          </button>
          {preview && creates > 0 && (
            <button
              className="btn primary"
              type="button"
              disabled={act.busy !== null}
              onClick={() => setAsking(true)}
            >
              Send {creates} tickets to Jira
            </button>
          )}
          {preview && creates === 0 && (
            <span className="sub">Nothing new to make in Jira.</span>
          )}
        </div>
      )}
      {asking && (
        <Confirm
          text={`Send ${creates} tickets to Jira? keel keeps each key, so a second send makes no twins.`}
          yes="Send them"
          busy={act.busy !== null}
          onYes={() => void send()}
          onNo={() => setAsking(false)}
        />
      )}
      {preview && (
        <>
          {preview.problems.length > 0 && (
            <ul className="pd-notes bad" aria-label="Problems">
              {preview.problems.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          )}
          <div className="pd-table-wrap">
            <table className="pd-table" aria-label="Ticket preview">
              <thead>
                <tr>
                  <th>Ticket</th>
                  <th>Project</th>
                  <th>Type</th>
                  <th>Action</th>
                  <th>Problem</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((r) => (
                  <tr key={`${r.level}:${r.ref}`}>
                    <td className={`pd-lv pd-lv-${r.level}`}>{r.title}</td>
                    <td className="mono">{r.project ?? "—"}</td>
                    <td>{r.type ?? "—"}</td>
                    <td>
                      <Pill
                        tone={
                          ACTION_TONE[r.action as keyof typeof ACTION_TONE] ??
                          "idle"
                        }
                      >
                        {r.action === "exists" && r.key ? r.key : r.action}
                      </Pill>
                    </td>
                    <td>{r.problem ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {preview.notes.length > 0 && (
            <ul className="pd-notes">
              {preview.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </>
      )}
      {result && (
        <div className="pd-sent">
          <p>
            {result.jira.length
              ? `Made ${result.jira.length} tickets in Jira: ${result.jira.join(", ")}`
              : "Jira made no ticket."}
          </p>
          {result.notes.length > 0 && (
            <ul className="pd-notes">
              {result.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {act.box}
    </section>
  );
}

// ---------------------------------------------------------------- documents

function DocTab({
  d,
  kind,
  onChanged,
  empty,
  extra,
}: {
  d: Detail;
  kind: string;
  onChanged: () => void;
  empty?: string;
  extra?: React.ReactNode;
}) {
  const doc = d.docs[kind];
  const [version, setVersion] = useState<number | null>(null);
  const old = useLoad(
    doc && version && version !== doc.version
      ? `doc:${d.initiative.id}:${kind}:${version}`
      : null,
    () => productApi.doc(d.initiative.id, kind, version!),
    { live: false },
  );
  if (!doc)
    return (
      <Empty>
        {empty ?? `No ${kind} yet: keel writes it at the ${kind} stage.`}
      </Empty>
    );
  const shown: DocView | null =
    version && version !== doc.version
      ? old.data
        ? { ...doc, ...old.data, versions: doc.versions }
        : null
      : doc;
  return (
    <div className="pd-doc">
      <div className="pd-doc-bar">
        <label>
          Version{" "}
          <select
            value={version ?? doc.version}
            onChange={(e) => setVersion(Number(e.target.value))}
          >
            {[...doc.versions].reverse().map((v) => (
              <option key={v} value={v}>
                v{v}
                {v === doc.version ? " (latest)" : ""}
              </option>
            ))}
          </select>
        </label>
        {shown?.approved_at ? (
          <Pill tone="ok">approved {fmtDate(shown.approved_at)}</Pill>
        ) : (
          <Pill tone="idle">not approved yet</Pill>
        )}
        <span className="sub mono">{shown?.path}</span>
        <Disagree d={d} stage={kind} onDone={onChanged} />
      </div>
      {extra}
      {shown ? (
        <Markdown text={shown.text} />
      ) : (
        <Async r={old} what="Loading the version">
          {() => null}
        </Async>
      )}
    </div>
  );
}

function ImpactTab({ d, onChanged }: { d: Detail; onChanged: () => void }) {
  const repos =
    (
      d.docs.impact?.data as
        | {
            repos?: {
              repo: string;
              title?: string;
              team?: string;
              risk?: string;
            }[];
          }
        | undefined
    )?.repos ?? [];
  const extra = repos.length > 0 && (
    <div className="pd-risks">
      {repos.map((r) => (
        <span key={r.repo} className={`pd-risk r-${r.risk ?? "unknown"}`}>
          {r.title ?? r.repo} · {r.team ?? "no team"} · risk {r.risk ?? "?"}
        </span>
      ))}
    </div>
  );
  return (
    <DocTab
      d={d}
      kind="impact"
      onChanged={onChanged}
      extra={extra}
      empty="keel reads each repo in parallel (read only) and writes what changes, the risk and the open questions per team."
    />
  );
}

function DecisionTab({ d, onChanged }: { d: Detail; onChanged: () => void }) {
  const act = useAct();
  const deck = d.docs.deck;
  const extra = (
    <div className="pd-actions">
      {deck && (
        <a
          className="btn sm"
          href={productApi.deckUrl(d.initiative.id)}
          target="_blank"
          rel="noreferrer"
        >
          Open the presentation v{deck.version}
        </a>
      )}
      {d.docs.decision && (
        <button
          className="btn sm"
          type="button"
          disabled={act.busy !== null}
          onClick={() =>
            void act
              .run("deck", () => productApi.rebuildDeck(d.initiative.id))
              .then((r) => r && onChanged())
          }
        >
          Build the presentation again
        </button>
      )}
      {act.box}
    </div>
  );
  return (
    <DocTab
      d={d}
      kind="decision"
      onChanged={onChanged}
      extra={extra}
      empty="keel writes a decision memo with options, cost and risk, and a presentation for the people who decide."
    />
  );
}

/** The plan's drafts can change while the leads look at it and after they agreed it, until a story is sent. */
const editable = (d: Detail) =>
  d.initiative.stage === "delivery" ||
  (d.initiative.stage === "plan" && d.initiative.status === "waiting");

type Drafts = {
  id: string;
  teams: Team[];
  repos: RepoOwner[];
  set: (d: Detail) => void;
};

function PlanTab({
  d,
  onChanged,
  set,
}: {
  d: Detail;
  onChanged: () => void;
  set: (d: Detail) => void;
}) {
  const p = d.plan;
  const canEdit = !!p && editable(d);
  const teams = useLoad(canEdit ? "plan-teams" : null, productApi.teams, {
    live: false,
  });
  const [adding, setAdding] = useState(false);
  // P5 every save is a plan version: an older one is shown as it was, read only
  const [version, setVersion] = useState<number | null>(null);
  const versions = d.docs.plan?.versions ?? (p ? [p.version] : []);
  const older = !!p && version != null && version !== p.version;
  const old = useLoad(
    older ? `doc:${d.initiative.id}:plan:${version}` : null,
    () => productApi.doc(d.initiative.id, "plan", version!),
    { live: false },
  );
  if (!p)
    return (
      <Empty>
        After Go, keel plans epics per team, stories with acceptance criteria,
        estimates and what waits for what.
      </Empty>
    );
  const drafts: Drafts | undefined = canEdit
    ? { id: d.initiative.id, teams: teams.data ?? [], repos: d.repos, set }
    : undefined;
  const picker = (
    <label>
      Version{" "}
      <select
        aria-label="Plan version"
        value={version ?? p.version}
        onChange={(e) => setVersion(Number(e.target.value))}
      >
        {[...versions].reverse().map((v) => (
          <option key={v} value={v}>
            v{v}
            {v === p.version ? " (latest)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
  if (older)
    return (
      <div className="pd-doc">
        <div className="pd-doc-bar">
          {picker}
          <Pill tone="idle">an older version, read only</Pill>
        </div>
        <Async r={old} what="Loading the version">
          {(doc) => <Markdown text={planMarkdown(doc.data)} />}
        </Async>
      </div>
    );
  return (
    <div className="pd-doc">
      <div className="pd-doc-bar">
        {versions.length > 1 ? picker : <span>Plan v{p.version}</span>}
        {p.ok ? (
          <Pill tone="ok">checked</Pill>
        ) : (
          <Pill tone="bad">{p.problems.length} problem(s)</Pill>
        )}
        {p.edited && <Pill tone="warn">edited by hand</Pill>}
        {p.initiative_key && (
          <span className="chip mono" title="The initiative's Jira ticket">
            {p.initiative_key}
          </span>
        )}
        <span className="sub">
          critical path {p.critical_path.join(" → ") || "—"}
          {p.critical_days ? ` · ${p.critical_days} d` : ""}
        </span>
        <Disagree d={d} stage="plan" onDone={onChanged} />
      </div>
      {p.edited && (
        <p className="sub">
          Plan v{p.version} was changed by hand. keel checked it again: the
          critical path and the totals are up to date.
        </p>
      )}
      {p.problems.length > 0 && (
        <ul className="pd-notes bad">
          {p.problems.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      )}
      {p.teams && (
        <div className="pd-risks">
          {Object.entries(p.teams).map(([t, dd]) => (
            <span key={t} className="pd-risk">
              {t}: {days(dd)}
            </span>
          ))}
          {p.total_days && (
            <span className="pd-risk">total {days(p.total_days)}</span>
          )}
        </div>
      )}
      <Stories plan={p} drafts={drafts} />
      {drafts &&
        (adding ? (
          <EpicForm
            drafts={drafts}
            onDone={() => setAdding(false)}
          />
        ) : (
          <div className="pd-actions">
            <button
              className="btn sm"
              type="button"
              onClick={() => setAdding(true)}
            >
              Add an epic
            </button>
          </div>
        ))}
    </div>
  );
}

/** An older plan version as readable Markdown, from its stored check (as the engine writes plan-vN.md). */
export function planMarkdown(data: unknown): string {
  type S = { id?: string; title?: string; repo?: string; depends_on?: string[]; estimate_days?: number[] | number; criteria?: string[] };
  type E = { id?: string; title?: string; team?: string; stories?: S[] };
  const r = (data ?? {}) as {
    plan?: { epics?: E[] };
    counts?: { epics?: number; stories?: number; tasks?: number };
    total_days?: number[];
    critical_path?: string[];
    critical_days?: number;
    problems?: string[];
  };
  const c = r.counts ?? {};
  const lines = [
    "# Plan",
    "",
    `${c.epics ?? 0} epics · ${c.stories ?? 0} stories · ${c.tasks ?? 0} tasks · ${days(r.total_days)} in total`,
  ];
  if (r.critical_path?.length)
    lines.push(`Critical path: ${r.critical_path.join(" → ")} (${r.critical_days ?? 0} days)`);
  for (const e of r.plan?.epics ?? []) {
    lines.push("", `## ${e.id} · ${e.title ?? ""} (${e.team ?? "?"})`);
    for (const st of e.stories ?? []) {
      const est = typeof st.estimate_days === "number" ? [st.estimate_days, st.estimate_days] : st.estimate_days;
      const after = st.depends_on?.length ? ` · after ${st.depends_on.join(", ")}` : "";
      lines.push(`- **${st.id}** ${st.title ?? ""} · ${st.repo ?? "?"} · ${days(est)}${after}`);
      for (const cr of st.criteria ?? []) lines.push(`  - ${cr}`);
    }
  }
  if (r.problems?.length) lines.push("", "## Problems", ...r.problems.map((x) => `- ${x}`));
  return lines.join("\n");
}

const sameList = (a: (string | number)[], b: (string | number)[]) =>
  a.length === b.length && a.every((x, n) => x === b[n]);
const splitLines = (v: string, sep: RegExp = /\n/) =>
  v
    .split(sep)
    .map((x) => x.trim())
    .filter(Boolean);

function TeamSelect({
  id,
  value,
  teams,
  onChange,
}: {
  id: string;
  value: string;
  teams: Team[];
  onChange: (v: string) => void;
}) {
  const known = teams.some((t) => t.id === value);
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">no team</option>
      {value && !known && <option value={value}>{value}</option>}
      {teams.map((t) => (
        <option key={t.id} value={t.id}>
          {t.name}
        </option>
      ))}
    </select>
  );
}

/** Edits a story draft (or writes a new one in [epic]); only what changed is sent. */
function StoryForm({
  drafts,
  story,
  epic,
  onDone,
}: {
  drafts: Drafts;
  story?: Story;
  epic: string;
  onDone: () => void;
}) {
  const s = story;
  const est = s?.estimate_days ?? [];
  const [title, setTitle] = useState(s?.title ?? "");
  const [criteria, setCriteria] = useState((s?.criteria ?? []).join("\n"));
  const [tasks, setTasks] = useState((s?.tasks ?? []).join("\n"));
  const [deps, setDeps] = useState((s?.depends_on ?? []).join(", "));
  const [low, setLow] = useState(est.length ? String(est[0]) : "");
  const [high, setHigh] = useState(
    est.length ? String(est[est.length - 1]) : "",
  );
  const [team, setTeam] = useState(s?.team ?? "");
  const repo0 = s?.project_id ?? s?.repo ?? "";
  const [repo, setRepo] = useState(repo0);
  const act = useAct();
  const id = (f: string) => `sf-${s?.id ?? `new-${epic}`}-${f}`;
  const body = (): StoryEdit => {
    const b: StoryEdit = {};
    const c = splitLines(criteria);
    const t = splitLines(tasks);
    const dd = splitLines(deps, /[,\s]+/);
    const e = [low, high]
      .map((x) => x.trim())
      .filter(Boolean)
      .map(Number);
    if (!s || title.trim() !== s.title) b.title = title.trim();
    if (!sameList(c, s?.criteria ?? [])) b.criteria = c;
    if (!sameList(t, s?.tasks ?? [])) b.tasks = t;
    if (!sameList(dd, s?.depends_on ?? [])) b.depends_on = dd;
    if (e.length && !sameList(e, est)) b.estimate_days = e;
    if (team !== (s?.team ?? "") && team) b.team = team;
    if (repo !== repo0) b.repo = repo;
    return b;
  };
  const save = async () => {
    const b = body();
    if (s && Object.keys(b).length === 0) return onDone();
    const n = await act.run("save", () =>
      s
        ? productApi.editStory(drafts.id, s.id, b)
        : productApi.addStory(drafts.id, { epic, ...b }),
    );
    if (n) {
      drafts.set(n);
      onDone();
    }
  };
  const repos = drafts.repos;
  return (
    <div
      className="pd-form pd-draft"
      role="group"
      aria-label={s ? `Edit ${s.id}` : `New story in ${epic}`}
    >
      <div className="field">
        <label htmlFor={id("title")}>Title</label>
        <input
          id={id("title")}
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor={id("criteria")}>Criteria (one per line)</label>
        <textarea
          id={id("criteria")}
          className="pd-text short"
          value={criteria}
          onChange={(e) => setCriteria(e.target.value)}
          placeholder="AC-1 [API] GET /prices returns the currency"
        />
      </div>
      <div className="field">
        <label htmlFor={id("tasks")}>Tasks (one per line)</label>
        <textarea
          id={id("tasks")}
          className="pd-text short"
          value={tasks}
          onChange={(e) => setTasks(e.target.value)}
        />
      </div>
      <div className="pd-row">
        <div className="field">
          <label htmlFor={id("deps")}>Waits for</label>
          <input
            id={id("deps")}
            type="text"
            value={deps}
            onChange={(e) => setDeps(e.target.value)}
            placeholder="PAY-S1, PAY-S2"
          />
        </div>
        <div className="field">
          <label htmlFor={id("low")}>Days, at least</label>
          <input
            id={id("low")}
            type="text"
            inputMode="decimal"
            value={low}
            onChange={(e) => setLow(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={id("high")}>Days, at most</label>
          <input
            id={id("high")}
            type="text"
            inputMode="decimal"
            value={high}
            onChange={(e) => setHigh(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={id("team")}>Team</label>
          <TeamSelect
            id={id("team")}
            value={team}
            teams={drafts.teams}
            onChange={setTeam}
          />
        </div>
        <div className="field">
          <label htmlFor={id("repo")}>Repo</label>
          <select
            id={id("repo")}
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
          >
            <option value="">no repo</option>
            {repo0 && !repos.some((r) => r.project_id === repo0) && (
              <option value={repo0}>{s?.repo ?? repo0}</option>
            )}
            {repos.map((r) => (
              <option key={r.project_id} value={r.project_id}>
                {r.name}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="pd-actions">
        <button
          className="btn sm primary"
          type="button"
          disabled={!title.trim() || act.busy !== null}
          onClick={() => void save()}
        >
          {s ? "Save the story" : "Add the story"}
        </button>
        <button className="btn sm" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
      {act.box}
    </div>
  );
}

/** Renames an epic or gives it another team (its unsent stories follow), or writes a new one. */
function EpicForm({
  drafts,
  epic,
  onDone,
}: {
  drafts: Drafts;
  epic?: { id: string; title: string; team?: string | null };
  onDone: () => void;
}) {
  const [title, setTitle] = useState(epic?.title ?? "");
  const [team, setTeam] = useState(epic?.team ?? "");
  const act = useAct();
  const id = (f: string) => `ef-${epic?.id ?? "new"}-${f}`;
  const save = async () => {
    const n = await act.run("save", () => {
      if (!epic)
        return productApi.addEpic(drafts.id, {
          title: title.trim(),
          ...(team ? { team } : {}),
        });
      const b: { title?: string; team?: string } = {};
      if (title.trim() !== epic.title) b.title = title.trim();
      if (team && team !== (epic.team ?? "")) b.team = team;
      return productApi.editEpic(drafts.id, epic.id, b);
    });
    if (n) {
      drafts.set(n);
      onDone();
    }
  };
  return (
    <div
      className="pd-row pd-draft"
      role="group"
      aria-label={epic ? `Edit epic ${epic.id}` : "New epic"}
    >
      <div className="field pd-grow">
        <label htmlFor={id("title")}>Epic title</label>
        <input
          id={id("title")}
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor={id("team")}>Team</label>
        <TeamSelect
          id={id("team")}
          value={team}
          teams={drafts.teams}
          onChange={setTeam}
        />
      </div>
      <button
        className="btn sm primary"
        type="button"
        disabled={!title.trim() || act.busy !== null}
        onClick={() => void save()}
      >
        {epic ? "Save the epic" : "Add the epic"}
      </button>
      <button className="btn sm" type="button" onClick={onDone}>
        Cancel
      </button>
      {act.box}
    </div>
  );
}

/** The work keel found in git for a sent story's Jira key. */
const workText = (s: Story) =>
  s.work
    ? `${s.work.branches.length} branches · ${s.work.commits.length} commits`
    : null;

function Stories({
  plan,
  delivery = false,
  drafts,
}: {
  plan: PlanView;
  delivery?: boolean;
  drafts?: Drafts;
}) {
  // what is open: "story:PAY-S1", "new:PAY" (a new story in PAY), "epic:PAY", "delete:PAY-S1"
  const [open, setOpen] = useState<string | null>(null);
  const act = useAct();
  const remove = async (sid: string) => {
    if (!drafts) return;
    const n = await act.run(sid, () => productApi.deleteStory(drafts.id, sid));
    setOpen(null);
    if (n) drafts.set(n);
  };
  const removeEpic = async (eid: string) => {
    if (!drafts) return;
    const n = await act.run(eid, () => productApi.deleteEpic(drafts.id, eid));
    if (n) drafts.set(n);
  };
  const cols = delivery ? 4 : drafts ? 6 : 5;
  return (
    <>
      {act.box}
      {plan.epics.map((e) => (
        <section key={e.id} className="pd-epic" aria-label={`Epic ${e.id}`}>
          <h3>
            <span className="mono">{e.id}</span> {e.title}{" "}
            <span className="chip">{e.team ?? "no team"}</span>
            {e.jira_key && <span className="chip mono">{e.jira_key}</span>}
            {drafts && open !== `epic:${e.id}` && (
              <span className="pd-inline">
                <button
                  className="btn sm"
                  type="button"
                  aria-label={`Edit epic ${e.id}`}
                  onClick={() => setOpen(`epic:${e.id}`)}
                >
                  Edit
                </button>
                <button
                  className="btn sm"
                  type="button"
                  aria-label={`Add a story to ${e.id}`}
                  onClick={() => setOpen(`new:${e.id}`)}
                >
                  Add a story
                </button>
                {e.stories.length === 0 && !e.jira_key && (
                  <button
                    className="btn sm"
                    type="button"
                    aria-label={`Delete epic ${e.id}`}
                    disabled={act.busy !== null}
                    onClick={() => void removeEpic(e.id)}
                  >
                    Delete
                  </button>
                )}
              </span>
            )}
          </h3>
          {drafts && open === `epic:${e.id}` && (
            <EpicForm drafts={drafts} epic={e} onDone={() => setOpen(null)} />
          )}
          {drafts && open === `new:${e.id}` && (
            <StoryForm
              drafts={drafts}
              epic={e.id}
              onDone={() => setOpen(null)}
            />
          )}
          {e.stories.length > 0 && (
            <div className="pd-table-wrap">
              <table className="pd-table">
                <thead>
                  <tr>
                    <th>Story</th>
                    <th>Repo</th>
                    {delivery ? (
                      <>
                        <th>Task</th>
                        <th>Jira</th>
                      </>
                    ) : (
                      <>
                        <th>Estimate</th>
                        <th>Waits for</th>
                        <th>Criteria</th>
                        {drafts && (
                          <th className="pd-act">
                            <span className="sr-only">Actions</span>
                          </th>
                        )}
                      </>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {e.stories.map((s) => {
                    const sent = s.jira_key ?? s.task_id;
                    return [
                      <tr key={s.id}>
                        <td>
                          <span className="mono">{s.id}</span> {s.title}
                          {!delivery && s.tasks.length > 0 && (
                            <ul className="pd-sub">
                              {s.tasks.map((t) => (
                                <li key={t}>{t}</li>
                              ))}
                            </ul>
                          )}
                        </td>
                        <td>{s.repo ?? "—"}</td>
                        {delivery ? (
                          <>
                            <td>
                              {s.task_id ? (
                                <a href={`#/tasks/${s.task_id}`}>
                                  {s.task_status ?? "task"}
                                </a>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td>
                              <span className="mono">{s.jira_key ?? "—"}</span>
                              {workText(s) && (
                                <div className="sub">{workText(s)}</div>
                              )}
                            </td>
                          </>
                        ) : (
                          <>
                            <td>{days(s.estimate_days)}</td>
                            <td className="mono">
                              {s.depends_on.join(", ") || "—"}
                            </td>
                            <td>
                              <ul className="pd-sub">
                                {s.criteria.map((c) => (
                                  <li key={c}>{c}</li>
                                ))}
                              </ul>
                            </td>
                            {drafts && (
                              <td className="pd-act">
                                {sent ? (
                                  <span
                                    className="chip mono"
                                    title="Sent: change it there"
                                  >
                                    {sent}
                                  </span>
                                ) : (
                                  <span className="pd-inline">
                                    <button
                                      className="btn sm"
                                      type="button"
                                      aria-label={`Edit ${s.id}`}
                                      onClick={() => setOpen(`story:${s.id}`)}
                                    >
                                      Edit
                                    </button>
                                    <button
                                      className="btn sm"
                                      type="button"
                                      aria-label={`Delete ${s.id}`}
                                      onClick={() => setOpen(`delete:${s.id}`)}
                                    >
                                      Delete
                                    </button>
                                  </span>
                                )}
                              </td>
                            )}
                          </>
                        )}
                      </tr>,
                      drafts && !sent && open === `story:${s.id}` && (
                        <tr key={`${s.id}:edit`}>
                          <td colSpan={cols}>
                            <StoryForm
                              drafts={drafts}
                              story={s}
                              epic={e.id}
                              onDone={() => setOpen(null)}
                            />
                          </td>
                        </tr>
                      ),
                      drafts && !sent && open === `delete:${s.id}` && (
                        <tr key={`${s.id}:delete`}>
                          <td colSpan={cols}>
                            <Confirm
                              text={`Take ${s.id} out of the plan?`}
                              yes="Take it out"
                              busy={act.busy !== null}
                              onYes={() => void remove(s.id)}
                              onNo={() => setOpen(null)}
                            />
                          </td>
                        </tr>
                      ),
                    ];
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ))}
    </>
  );
}

function DeliveryTab({ d, reload }: { d: Detail; reload: () => void }) {
  const p = d.plan;
  if (!p || !["delivery", "outcome", "done"].includes(d.initiative.stage))
    return (
      <Empty>The stories go to the teams when the leads agreed the plan.</Empty>
    );
  const prog = p.progress;
  const stories = p.epics.flatMap((e) => e.stories);
  const tasksLeft = stories.some((s) => !s.task_id);
  const jiraLeft = stories.some((s) => !s.jira_key);
  return (
    <div className="pd-doc">
      {prog && (
        <div className="pd-doc-bar">
          <span>
            {prog.done} of {prog.total} stories done
          </span>
          <span className="pd-progress wide">
            <i
              style={{
                width: `${prog.total ? Math.round((100 * prog.done) / prog.total) : 0}%`,
              }}
            />
          </span>
        </div>
      )}
      {p.initiative_key && (
        <p className="sub">
          The initiative's Jira ticket:{" "}
          <span className="mono">{p.initiative_key}</span>
        </p>
      )}
      <Stories plan={p} delivery />
      {d.initiative.stage === "delivery" && (
        <>
          <Handoff
            id={d.initiative.id}
            tasksLeft={tasksLeft}
            onDone={reload}
          />
          <JiraTickets id={d.initiative.id} left={jiraLeft} onDone={reload} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- questions, disagreements, follow-ups

const ASK_TO: [string, string][] = [
  ["keel", "keel (from the documents)"],
  ["po", "the PO"],
  ["pm", "the PM"],
  ["lead", "a team lead"],
  ["dev", "a developer"],
  ["legal", "Legal"],
  ["architect", "the architect"],
];

function Questions({ d, onChanged }: { d: Detail; onChanged: () => void }) {
  const act = useAct();
  const [text, setText] = useState("");
  const [to, setTo] = useState("keel");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const ask = async () => {
    const q = await act.run("ask", () =>
      productApi.ask(d.initiative.id, { text, to }),
    );
    if (q) {
      setText("");
      onChanged();
    }
  };
  const answer = async (q: Question) => {
    const r = await act.run(q.id, () =>
      productApi.answer(d.initiative.id, q.id, answers[q.id] ?? ""),
    );
    if (r) onChanged();
  };
  return (
    <div className="pd-doc">
      <div className="pd-row">
        <select
          aria-label="Ask"
          value={to}
          onChange={(e) => setTo(e.target.value)}
        >
          {ASK_TO.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
        <input
          className="pd-grow"
          type="text"
          aria-label="Your question"
          placeholder="Ask a question about this initiative"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <button
          className="btn primary"
          type="button"
          disabled={!text.trim() || act.busy !== null}
          onClick={() => void ask()}
        >
          Ask
        </button>
      </div>
      {act.box}
      {d.questions.length === 0 ? (
        <Empty>
          No question yet. keel asks its own at the brief; you can ask keel or a
          person any time.
        </Empty>
      ) : (
        <ul className="pd-qs">
          {d.questions.map((q) => (
            <li key={q.id} className={`pd-q s-${q.status}`}>
              <div>
                <b>{q.text}</b>{" "}
                <span className="sub">
                  {q.role === "keel-asked"
                    ? "keel asked"
                    : `to ${q.asked_to ?? q.role}`}{" "}
                  · {STAGE_LABEL[q.stage] ?? q.stage}
                </span>
              </div>
              {q.answer ? (
                <div className="pd-answer">
                  {q.answer} <span className="sub">— {q.answered_by}</span>
                </div>
              ) : (
                <div className="pd-row">
                  <input
                    className="pd-grow"
                    type="text"
                    aria-label={`Answer: ${q.text}`}
                    value={answers[q.id] ?? ""}
                    onChange={(e) =>
                      setAnswers((a) => ({ ...a, [q.id]: e.target.value }))
                    }
                  />
                  <button
                    className="btn sm"
                    type="button"
                    disabled={!(answers[q.id] ?? "").trim()}
                    onClick={() => void answer(q)}
                  >
                    Answer
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Disagree({
  d,
  stage,
  onDone,
}: {
  d: Detail;
  stage: string;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [proposal, setProposal] = useState("");
  const [author, setAuthor] = useState("");
  const act = useAct();
  // each form starts empty: the name of whoever wrote the last one must not stick (empty = you, the signed-in person)
  const reset = () => {
    setReason("");
    setProposal("");
    setAuthor("");
  };
  if (!open)
    return (
      <button
        className="btn sm"
        type="button"
        onClick={() => {
          reset();
          setOpen(true);
        }}
      >
        I disagree…
      </button>
    );
  const save = async () => {
    const r = await act.run("disagree", () =>
      productApi.disagree(d.initiative.id, {
        stage,
        reason,
        proposal: proposal || undefined,
        author: author || undefined,
      }),
    );
    if (r) {
      setOpen(false);
      reset();
      onDone();
    }
  };
  return (
    <div className="pd-disagree" role="group" aria-label="Disagree">
      <input
        type="text"
        aria-label="Who disagrees"
        placeholder="your name (empty: you)"
        value={author}
        onChange={(e) => setAuthor(e.target.value)}
      />
      <input
        type="text"
        aria-label="Why you disagree"
        placeholder="why you disagree"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <input
        type="text"
        aria-label="Your proposal"
        placeholder="what you propose (optional)"
        value={proposal}
        onChange={(e) => setProposal(e.target.value)}
      />
      <button
        className="btn sm primary"
        type="button"
        disabled={!reason.trim() || act.busy !== null}
        onClick={() => void save()}
      >
        Write it down
      </button>
      <button
        className="btn sm"
        type="button"
        onClick={() => {
          setOpen(false);
          reset();
        }}
      >
        Cancel
      </button>
      {act.box}
    </div>
  );
}

function Disagreements({
  d,
  set,
  reload,
}: {
  d: Detail;
  set: (d: Detail) => void;
  reload: () => void;
}) {
  const act = useAct();
  const [outcome, setOutcome] = useState<Record<string, string>>({});
  if (d.disagreements.length === 0) return null;
  const settle = async (x: Disagreement) => {
    if (
      await act.run(x.id, () =>
        productApi.settle(d.initiative.id, x.id, {
          outcome: outcome[x.id] ?? "",
        }),
      )
    )
      reload();
  };
  const rerun = async (x: Disagreement) => {
    const n = await act.run(x.id, () =>
      productApi.rerunWithObjection(d.initiative.id, x.id),
    );
    if (n) set(n);
  };
  return (
    <Panel title="Disagreements">
      <ul className="pd-dis">
        {d.disagreements.map((x) => (
          <li key={x.id} className={`s-${x.status}`}>
            <div>
              <b>{x.author}</b> on the {x.stage}: {x.reason}
              {x.proposal && <> · proposes {x.proposal}</>}
            </div>
            <div className="sub">
              decides:{" "}
              {x.decider === "po"
                ? "the PO"
                : x.decider === "lead"
                  ? "the lead"
                  : x.decider}
            </div>
            {x.status === "open" ? (
              <div className="pd-row">
                <input
                  className="pd-grow"
                  type="text"
                  aria-label={`Decision on: ${x.reason}`}
                  placeholder="what was decided"
                  value={outcome[x.id] ?? ""}
                  onChange={(e) =>
                    setOutcome((o) => ({ ...o, [x.id]: e.target.value }))
                  }
                />
                <button
                  className="btn sm"
                  type="button"
                  disabled={!(outcome[x.id] ?? "").trim()}
                  onClick={() => void settle(x)}
                >
                  Decide
                </button>
                <button
                  className="btn sm"
                  type="button"
                  onClick={() => void rerun(x)}
                >
                  Run again with it
                </button>
              </div>
            ) : (
              <div className="pd-answer">{x.outcome}</div>
            )}
          </li>
        ))}
      </ul>
      {act.box}
    </Panel>
  );
}

function FollowUps({ d, onChanged }: { d: Detail; onChanged: () => void }) {
  const act = useAct();
  const [text, setText] = useState("");
  const [due, setDue] = useState("");
  const open = d.follow_ups.filter((f) => !f.done_at);
  const add = async () => {
    if (
      await act.run("add", () =>
        productApi.addFollowUp(d.initiative.id, { text, due_at: due }),
      )
    ) {
      setText("");
      setDue("");
      onChanged();
    }
  };
  const done = async (f: FollowUp) => {
    if (
      await act.run(f.id, () => productApi.doneFollowUp(d.initiative.id, f.id))
    )
      onChanged();
  };
  return (
    <Panel title="Follow-ups">
      {open.length === 0 ? (
        <p className="sub">Nothing to follow up.</p>
      ) : (
        <ul className="pd-fus">
          {open.map((f) => (
            <li key={f.id}>
              <span>{f.text}</span>
              <span className="sub">
                {fmtDate(f.due_at)}
                {f.owner ? ` · ${f.owner}` : ""}
              </span>
              <button
                className="btn sm ghost"
                type="button"
                onClick={() => void done(f)}
                aria-label={`Done: ${f.text}`}
              >
                Done
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="pd-row">
        <input
          className="pd-grow"
          type="text"
          aria-label="Follow up on"
          placeholder="follow up on…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <input
          type="date"
          aria-label="When"
          value={due}
          onChange={(e) => setDue(e.target.value)}
        />
        <button
          className="btn sm"
          type="button"
          disabled={!text.trim() || !due}
          onClick={() => void add()}
        >
          Add
        </button>
      </div>
      {act.box}
    </Panel>
  );
}

// ---------------------------------------------------------------- runs and history

function Runs({ d }: { d: Detail }) {
  if (d.runs.length === 0) return <Empty>No run yet.</Empty>;
  return (
    <div className="pd-table-wrap">
      <table className="pd-table">
        <thead>
          <tr>
            <th>Stage</th>
            <th>Why</th>
            <th>Status</th>
            <th>Started</th>
            <th>Flow</th>
          </tr>
        </thead>
        <tbody>
          {d.runs.map((r) => (
            <tr key={r.thread_id}>
              <td>{STAGE_LABEL[r.stage] ?? r.stage}</td>
              <td>{r.reason ?? "—"}</td>
              <td>
                <StatusPill status={r.status} />
              </td>
              <td className="sub">
                {r.started_at.replace("T", " ").slice(0, 16)}
              </td>
              <td className="mono">
                {r.workflow_id} · {r.thread_id}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function History({ d }: { d: Detail }) {
  if (d.history.length === 0) return <Empty>Nothing happened yet.</Empty>;
  return (
    <ol className="pd-history">
      {d.history.map((e) => (
        <li key={e.id} className={`k-${e.kind}`}>
          <span className="sub">{e.at.replace("T", " ").slice(0, 16)}</span>
          <b>{e.actor}</b>
          <span>{e.text}</span>
        </li>
      ))}
    </ol>
  );
}
