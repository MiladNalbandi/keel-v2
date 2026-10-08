// One initiative: where it is (the tracker), what waits for whom (the Next card), its documents by version, the
// questions, the disagreements, the plan and its delivery, the runs, the history and the follow-ups.

import { useState } from "react";
import {
  ClarifyForm,
  answersOf,
  type ClarifyAnswers,
} from "../../web/src/components/ClarifyForm";
import { Markdown } from "../../web/src/components/Markdown";
import { Async, Empty, Panel, Pill, Tabs } from "../../web/src/components/ui";
import { useLoad } from "../../web/src/state";
import type { ClarifyQuestion } from "../../web/src/api";
import {
  productApi,
  STAGE_LABEL,
  type Detail,
  type DocView,
  type Disagreement,
  type FollowUp,
  type PlanView,
  type Question,
} from "./productApi";
import {
  days,
  fmtDate,
  ProductHead,
  StatusPill,
  Tracker,
  useAct,
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
    <div className="pd">
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
            {tab === "plan" && <PlanTab d={d} onChanged={reload} />}
            {tab === "delivery" && (
              <DeliveryTab d={d} reload={reload} />
            )}
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
          <Disagreements d={d} set={set} reload={reload} />
          <FollowUps d={d} onChanged={reload} />
        </aside>
      </div>
    </>
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
            className="btn primary"
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
            className="btn primary"
            type="button"
            disabled={busy}
            onClick={() =>
              void doIt("go", () =>
                productApi.decide(i.id, {
                  choice: "go",
                  option: option || undefined,
                  why: why || undefined,
                }),
              )
            }
          >
            Go
          </button>
          <button
            className="btn"
            type="button"
            disabled={busy}
            onClick={() =>
              void doIt("not_now", () =>
                productApi.decide(i.id, {
                  choice: "not_now",
                  why: why || undefined,
                }),
              )
            }
          >
            Not now
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
            className="btn primary"
            type="button"
            disabled={busy}
            onClick={() =>
              void doIt("approve", () =>
                productApi.approve(i.id, { why: why || undefined }),
              )
            }
          >
            {i.stage === "plan"
              ? "Agree the plan"
              : i.stage === "impact"
                ? "Confirm the impact"
                : i.stage === "outcome"
                  ? "Close the initiative"
                  : "Approve"}
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
      <Handoff id={i.id} onDone={reload} />
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
  return (
    <section className={`pd-next${w ? " is-wait" : ""}`} aria-label="Next">
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
    <span className="pd-inline">
      <input
        type="text"
        aria-label="What to change"
        placeholder="what to change"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <button
        className="btn"
        type="button"
        disabled={busy || !note.trim()}
        onClick={onSend}
      >
        Send back
      </button>
    </span>
  );
}

function Handoff({ id, onDone }: { id: string; onDone: () => void }) {
  const act = useAct();
  const [notes, setNotes] = useState<string[]>([]);
  const send = async (target: "tasks" | "jira" | "both") => {
    const r = await act.run(target, () => productApi.handoff(id, target));
    if (r) {
      setNotes(r.notes);
      onDone();
    }
  };
  return (
    <>
      <p className="sub">
        Each story becomes a task in its repo's project, with its criteria; the
        teams start it as a keel flow. Or as Jira tickets (epic, story,
        sub-tasks).
      </p>
      <div className="pd-actions">
        <button
          className="btn primary"
          type="button"
          disabled={act.busy !== null}
          onClick={() => void send("tasks")}
        >
          Send to keel Tasks
        </button>
        <button
          className="btn"
          type="button"
          disabled={act.busy !== null}
          onClick={() => void send("jira")}
        >
          Send to Jira
        </button>
        <button
          className="btn"
          type="button"
          disabled={act.busy !== null}
          onClick={() => void send("both")}
        >
          Both
        </button>
      </div>
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

function PlanTab({ d, onChanged }: { d: Detail; onChanged: () => void }) {
  const p = d.plan;
  if (!p)
    return (
      <Empty>
        After Go, keel plans epics per team, stories with acceptance criteria,
        estimates and what waits for what.
      </Empty>
    );
  return (
    <div className="pd-doc">
      <div className="pd-doc-bar">
        <span>Plan v{p.version}</span>
        {p.ok ? (
          <Pill tone="ok">checked</Pill>
        ) : (
          <Pill tone="bad">{p.problems.length} problem(s)</Pill>
        )}
        <span className="sub">
          critical path {p.critical_path.join(" → ") || "—"}
          {p.critical_days ? ` · ${p.critical_days} d` : ""}
        </span>
        <Disagree d={d} stage="plan" onDone={onChanged} />
      </div>
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
      <Stories plan={p} />
    </div>
  );
}

function Stories({
  plan,
  delivery = false,
}: {
  plan: PlanView;
  delivery?: boolean;
}) {
  return (
    <>
      {plan.epics.map((e) => (
        <section key={e.id} className="pd-epic" aria-label={`Epic ${e.id}`}>
          <h3>
            <span className="mono">{e.id}</span> {e.title}{" "}
            <span className="chip">{e.team ?? "no team"}</span>
          </h3>
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
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {e.stories.map((s) => (
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
                        <td className="mono">{s.jira_key ?? "—"}</td>
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
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
      <Stories plan={p} delivery />
      {d.initiative.stage === "delivery" && p.epics.some((e) => e.stories.some((s) => !s.task_id && !s.jira_key)) && (
        <Handoff id={d.initiative.id} onDone={reload} />
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
  if (!open)
    return (
      <button className="btn sm" type="button" onClick={() => setOpen(true)}>
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
      setReason("");
      setProposal("");
      onDone();
    }
  };
  return (
    <div className="pd-disagree" role="group" aria-label="Disagree">
      <input
        type="text"
        aria-label="Who disagrees"
        placeholder="your name (optional)"
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
      <button className="btn sm" type="button" onClick={() => setOpen(false)}>
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
