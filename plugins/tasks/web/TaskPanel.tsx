// 0.18.57 one task in the new Tasks page (TaskBoard.tsx): the ticket, what waits for you (PP, the release, a Jira move by
// hand, the PR link, the flow at a gate, a review comment and keel's check of it), keel's suggestion (the flow, why, its
// steps; Start / another flow / how it runs / Not now) or keel's own proposal, the running flow with how far it is, the
// pull request, the description, the history, and More actions. The same api calls as the classic drawer (Tasks.tsx).

import { useEffect, useMemo, useState } from "react";
import {
  answersOf,
  clock,
  ClarifyForm,
  clearDraft,
  DraftKept,
  noKeys,
  useAnswerDraft,
  Confirm,
  Drawer,
  ErrorBox,
  errorParts,
  go,
  hashForScreen as hashFor,
  InitAsk,
  isInitFlow,
  Loading,
  needsInit,
  Pill,
  Ticks,
  useApp,
  useHasPage,
  useLoad,
  useStartInit,
  WorkspaceDoctor,
  type ClarifyAnswers,
  type RunMode,
  type Workflow,
} from "@keel/web-sdk";
import {
  tasksApi,
  type Task,
  type TaskEvent,
  type TaskItem,
  type TaskStatus,
} from "./tasksApi";
import { AutomationItem, proposalDraft, questionsOf } from "./Automations";
import {
  answerItems,
  flowTone,
  flowWords,
  keyOf,
  nameOf,
  prLabel,
  prNumber,
  progressOf,
  proposals,
  reviewComments,
  RUN_WORDS,
  runWords,
  stepsOf,
  STATUS_WORDS,
  suggestFor,
} from "./taskModel";

type Err = { message: string; hint?: string };
const ACTOR: Record<TaskEvent["actor"], string> = {
  user: "you",
  keel: "keel",
  jira: "Jira",
};
const PICKING = "keel is picking a flow… the proposal will be in your Inbox.";
/** A workflow's name without keel's "(keel)" mark: "fix (keel)" → "fix". */
const short = (w: Workflow | undefined, id: string) =>
  (w?.name ?? id).replace(/\s*\(keel\)$/, "");
/** The Inbox, on this item's project, with the item in view (the Inbox's ?item=, its id there: task-item-<id>). */
const inboxItemHash = (it: TaskItem) =>
  `${hashFor("inbox", it.project_id)}?item=task-item-${it.id}`;

export function StatusPill({ s }: { s: TaskStatus }) {
  return <span className={`pill tkn-pill tkn-s-${s}`}>{STATUS_WORDS[s]}</span>;
}

function Spark() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      aria-hidden="true"
    >
      <path d="M12 3l1.8 4.8L18.6 9.6 13.8 11.4 12 16.2 10.2 11.4 5.4 9.6 10.2 7.8z" />
    </svg>
  );
}

// ---------- what waits for you ----------

/** A PP test, the release, the PR link after a flow without one (B11), or a Jira move keel could not make: its buttons. */
function WaitCard({
  it,
  busy,
  onAct,
}: {
  it: TaskItem;
  busy: boolean;
  onAct: (action: string, note?: string) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [askNote, setAskNote] = useState(false);
  const id = `tkn-item-${it.id}`;
  const pr = it.kind === "task" && it.stage === "pr";
  const ask = (
    <>
      <span className="tkn-wait-tag">waits for you</span>
      <b className="tkn-wait-q">{it.title}</b>
      {it.detail && (
        <span className="sub">
          <Ticks text={it.detail} />
        </span>
      )}
    </>
  );
  if (it.kind === "jira-manual") {
    return (
      <div className="tkn-wait" role="group" aria-label={it.title}>
        {ask}
        <div className="row">
          <button
            className="btn primary"
            type="button"
            disabled={busy}
            onClick={() => void onAct("done")}
          >
            Done, I moved it
          </button>
        </div>
      </div>
    );
  }
  if (pr) {
    return (
      <div className="tkn-wait" role="group" aria-label={it.title}>
        {ask}
        <div className="field">
          <label htmlFor={id}>Pull request link</label>
          <input
            id={id}
            type="url"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="https://github.com/owner/repo/pull/12"
          />
        </div>
        <div className="row">
          <button
            className="btn primary"
            type="button"
            disabled={busy || !note.trim()}
            onClick={() => void onAct("pr", note.trim())}
          >
            Save the PR link
          </button>
          <button
            className="btn ghost"
            type="button"
            disabled={busy}
            onClick={() => void onAct("later")}
          >
            Not now
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="tkn-wait" role="group" aria-label={it.title}>
      {ask}
      {askNote && (
        <div className="field">
          <label htmlFor={id}>Why does it go back?</label>
          <textarea
            id={id}
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
      )}
      <div className="row">
        <button
          className="btn primary"
          type="button"
          disabled={busy}
          onClick={() => void onAct("confirm", note.trim() || undefined)}
        >
          {it.stage === "prod" ? "Shipped, confirm" : "PP works, confirm"}
        </button>
        <button
          className="btn"
          type="button"
          disabled={busy || (askNote && !note.trim())}
          onClick={() => {
            if (!note.trim()) {
              setAskNote(true);
              window.setTimeout(() => document.getElementById(id)?.focus(), 0);
              return;
            }
            void onAct("send_back", note.trim());
          }}
        >
          Send back
        </button>
      </div>
    </div>
  );
}

/** keel's proposal ("I want to run the fix flow on SHOPS-1234"): why, its questions, Start / Use another flow / Not now. */
function ProposalCard({
  it,
  wfs,
  onAct,
}: {
  it: TaskItem;
  wfs: Workflow[] | null;
  onAct: (action: string, b: Record<string, unknown>) => Promise<void>;
}) {
  const d = it.data ?? {};
  const questions = questionsOf(d.questions);
  // 0.18.64 the unsent answers are kept (the same draft as the Inbox card's), until the flow starts
  const place = useMemo(() => proposalDraft(it.project_id, it.id), [it.project_id, it.id]);
  const [picked, setPicked, keptPicked] = useAnswerDraft<ClarifyAnswers>(place, "picked", {}, noKeys);
  const [typed, setTyped, keptTyped] = useAnswerDraft<ClarifyAnswers>(place, "typed", {});
  const [other, setOther] = useState(false);
  const proposed = String(d.workflow_id ?? "");
  const [wid, setWid] = useState("");
  const [allowDirty, setAllowDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err | null>(null);
  const noPr = !!d.no_pr;
  const review = d.kind === "review";
  const wf = wfs?.find((w) => w.id === proposed);
  const send = async (action: string) => {
    setBusy(true);
    setErr(null);
    try {
      const answers = answersOf(questions, picked, typed);
      await onAct(action, {
        ...(action === "other" ? { workflow_id: wid } : {}),
        ...((action === "start" || action === "other") &&
        Object.keys(answers).length
          ? { answers }
          : {}),
        ...(allowDirty ? { allow_dirty: true } : {}),
      });
      if (action === "start" || action === "other") clearDraft(place);
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };
  const dirty = !!err && /uncommitted changes/i.test(err.message);
  const others = (wfs ?? []).filter((w) => w.id !== proposed);
  return (
    <section
      className="tkn-suggest"
      aria-label={it.title}
      data-testid="proposal"
    >
      <span className="tkn-kicker">
        <Spark />
        KEEL PROPOSES
      </span>
      <h3 className="tkn-suggest-h">
        {review ? (
          "A review of the pull request"
        ) : (
          <>{short(wf, proposed)} flow</>
        )}
      </h3>
      {!noPr && !review && d.fallback ? (
        <span className="hint">
          keel's default flow: its pick did not work this time.
        </span>
      ) : null}
      {d.why ? (
        <p className="tkn-why">
          <span className="sub">Why: </span>
          <Ticks text={String(d.why)} />
        </p>
      ) : it.detail ? (
        <p className="tkn-why">
          <Ticks text={it.detail} />
        </p>
      ) : null}
      {d.trigger ? (
        <p className="sub tkn-because">Because {String(d.trigger)}</p>
      ) : null}
      {!noPr && <p className="tkn-steps mono">{stepsOf(proposed, wf)}</p>}
      {d.title && !noPr ? (
        <p className="sub">The flow's title: {String(d.title)}</p>
      ) : null}
      {!noPr && questions.length > 0 && (
        <ClarifyForm
          questions={questions}
          picked={picked}
          typed={typed}
          onPick={(id, v) => setPicked((p) => ({ ...p, [id]: v }))}
          onType={(id, v) => setTyped((p) => ({ ...p, [id]: v }))}
        />
      )}
      {!noPr && (
        <p className="sub tkn-mode">
          How it runs: {runWords(d.run_mode ? String(d.run_mode) : null)}
          {d.cap_tokens
            ? `, at most ${Number(d.cap_tokens).toLocaleString()} tokens`
            : ""}
        </p>
      )}
      {other && (
        <div className="tkn-chips" role="group" aria-label="Pick another flow">
          {others.map((w) => (
            <button
              key={w.id}
              type="button"
              className="tkn-chip"
              aria-pressed={wid === w.id}
              onClick={() => setWid(w.id)}
            >
              {short(w, w.id)}
            </button>
          ))}
          {!wfs && <span className="sub">Loading the flows…</span>}
        </div>
      )}
      {(dirty || allowDirty) && (
        <label className="chk">
          <input
            type="checkbox"
            checked={allowDirty}
            onChange={(e) => setAllowDirty(e.target.checked)}
          />{" "}
          Start anyway — my uncommitted files stay out of keel's commits
        </label>
      )}
      {err && <ErrorBox error={err} />}
      <div className="row tkn-acts">
        {noPr ? (
          <button
            className="btn"
            type="button"
            disabled={busy}
            onClick={() => void send("later")}
          >
            Not now
          </button>
        ) : (
          <>
            {other ? (
              <button
                className="btn primary"
                type="button"
                disabled={busy || !wid}
                onClick={() => void send("other")}
              >
                {wid
                  ? `Start ${short(
                      wfs?.find((w) => w.id === wid),
                      wid,
                    )} instead`
                  : "Pick a flow above"}
              </button>
            ) : (
              <button
                className="btn primary"
                type="button"
                disabled={busy}
                onClick={() => void send("start")}
              >
                Start
              </button>
            )}
            <button
              className="btn"
              type="button"
              disabled={busy}
              onClick={() => setOther((o) => !o)}
              aria-expanded={other}
            >
              {other ? "Keep keel's flow" : "Use another flow"}
            </button>
            <button
              className="btn ghost"
              type="button"
              disabled={busy}
              onClick={() => void send("later")}
            >
              Not now
            </button>
          </>
        )}
        <DraftKept shown={keptPicked || keptTyped} />
      </div>
    </section>
  );
}

/** keel's suggestion before anything ran: the flow, why, its steps, another flow, how it runs, the start's warnings. */
function SuggestCard({
  t,
  wfs,
  onStarted,
  onNotNow,
  onPick,
  picking,
}: {
  t: Task;
  wfs: Workflow[] | null;
  onStarted: (t: Task) => void;
  onNotNow: () => void;
  onPick: () => void;
  picking: boolean;
}) {
  const pid = t.project_id;
  const s = wfs ? suggestFor(t, wfs) : null;
  const [chosen, setChosen] = useState<string | null>(null);
  const wid = chosen ?? s?.id ?? "";
  const [other, setOther] = useState(false);
  const [mode, setMode] = useState<RunMode>("manual");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err | null>(null);
  const [allowDirty, setAllowDirty] = useState(false);
  const [allowFake, setAllowFake] = useState(false);
  const [allowForce, setAllowForce] = useState(false);
  const [doctor, setDoctor] = useState(false);
  // keel 0.18.55 a project keel does not know yet: ask once before the flow starts (Run init first / Start anyway)
  const { projects } = useApp();
  const proj = projects.find((x) => x.id === pid);
  const unknown =
    needsInit(proj) &&
    !isInitFlow(wfs?.find((w) => w.id === wid) ?? { id: wid });
  const [asking, setAsking] = useState(false);
  const [askedOk, setAskedOk] = useState(false);
  const init = useStartInit(proj);
  useEffect(() => {
    tasksApi.projectSettings(pid).then(
      (x) => setMode(x.effective.run_mode ?? "manual"),
      () => undefined,
    );
  }, [pid]);
  const dirty = !!err && /uncommitted changes/i.test(err.message);
  const fake = !!err && /fake model/i.test(err.message);
  // 0.18.57 one owner per task: another flow or side agent works on this ticket, branch or files
  const owned = !!err && /already works on this/i.test(err.message);
  const wf = wfs?.find((w) => w.id === wid);
  const start = async (anyway = askedOk) => {
    if (unknown && !anyway) {
      setAsking(true);
      return;
    }
    setAsking(false);
    setBusy(true);
    setErr(null);
    try {
      onStarted(
        await tasksApi.start(t.id, {
          workflow_id: wid || undefined,
          run_mode: mode,
          ...(allowDirty ? { allow_dirty: true } : {}),
          ...(allowFake ? { allow_fake: true } : {}),
          ...(allowForce ? { force: true } : {}),
        }),
      );
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const pickOther = (id: string) => {
    setChosen(id);
    setAsking(false);
    setAskedOk(false);
  };
  return (
    <section className="tkn-suggest" aria-label="Start a flow">
      <span className="tkn-kicker">
        <Spark />
        KEEL SUGGESTS
      </span>
      {!wfs ? (
        <span className="sub">Loading the flows…</span>
      ) : !s ? (
        <span className="sub">
          This project has no flow yet: add one on the Workflows page.
        </span>
      ) : (
        <>
          <h3 className="tkn-suggest-h" data-testid="suggested-flow">
            {short(wf, wid)} flow
          </h3>
          {wid === s.id ? (
            <p className="tkn-why">
              <span className="sub">Why: </span>
              {s.why}
            </p>
          ) : (
            <p className="tkn-why sub">
              You picked this flow instead of keel's{" "}
              {short(
                wfs.find((w) => w.id === s.id),
                s.id,
              )}
              .
            </p>
          )}
          {stepsOf(wid, wf) && (
            <p className="tkn-steps mono">{stepsOf(wid, wf)}</p>
          )}
          {other && (
            <div
              className="tkn-chips"
              role="group"
              aria-label="Pick another flow"
            >
              {wfs.map((w) => (
                <button
                  key={w.id}
                  type="button"
                  className="tkn-chip"
                  aria-pressed={wid === w.id}
                  onClick={() => pickOther(w.id)}
                >
                  {short(w, w.id)}
                  {w.id === s.id ? " (keel's pick)" : ""}
                </button>
              ))}
            </div>
          )}
          <label className="tkn-runs">
            <span className="sub">How it runs</span>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as RunMode)}
            >
              {RUN_WORDS.map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                  {k === "manual" ? " (recommended)" : ""}
                </option>
              ))}
            </select>
          </label>
          <span className="hint">
            The flow gets the title{t.description ? ", the description" : ""}
            {t.external_key ? " and the Jira key" : ""} as its request.
          </span>
        </>
      )}
      {/* Same help as Start a flow: the Doctor sorts the files (commit / stash / hide on this computer / .gitignore). */}
      {dirty && !doctor && (
        <button className="btn" type="button" onClick={() => setDoctor(true)}>
          Ask the Doctor what to do with these files
        </button>
      )}
      {doctor && (
        <WorkspaceDoctor
          pid={pid}
          onClean={() => {
            setErr(null);
            setDoctor(false);
          }}
        />
      )}
      {(dirty || allowDirty) && (
        <label className="chk">
          <input
            type="checkbox"
            checked={allowDirty}
            onChange={(e) => setAllowDirty(e.target.checked)}
          />{" "}
          Start anyway — my uncommitted files stay out of keel's commits
        </label>
      )}
      {(fake || allowFake) && (
        <label className="chk">
          <input
            type="checkbox"
            checked={allowFake}
            onChange={(e) => setAllowFake(e.target.checked)}
          />{" "}
          Run with the fake model anyway
        </label>
      )}
      {(owned || allowForce) && (
        <label className="chk">
          <input
            type="checkbox"
            checked={allowForce}
            onChange={(e) => setAllowForce(e.target.checked)}
          />{" "}
          Start anyway — two agents on one task may write over each other
        </label>
      )}
      {err && <ErrorBox error={err} />}
      {asking && proj && (
        <InitAsk
          name={proj.name || proj.id}
          busy={init.busy}
          onInit={() => void init.start()}
          onAnyway={() => {
            setAskedOk(true);
            void start(true);
          }}
          onCancel={() => setAsking(false)}
        />
      )}
      {init.err && <ErrorBox error={init.err} />}
      <div className="row tkn-acts">
        <button
          className="btn primary"
          type="button"
          onClick={() => void start()}
          disabled={busy || init.busy || !wid}
        >
          {busy ? "Starting…" : `Start ${short(wf, wid || "the")} flow`}
        </button>
        <button
          className="btn"
          type="button"
          disabled={!wfs?.length}
          onClick={() => setOther((o) => !o)}
          aria-expanded={other}
        >
          {other ? "Hide other flows" : "Use another flow"}
        </button>
        <button className="btn ghost" type="button" onClick={onNotNow}>
          Not now
        </button>
      </div>
      <p className="sub tkn-pick">
        Not sure?{" "}
        <button
          className="linkbtn tkn-link"
          type="button"
          disabled={picking}
          onClick={onPick}
        >
          {picking ? "Picking…" : "Pick a flow for me"}
        </button>{" "}
        — keel reads the ticket with the model and proposes a flow in your
        Inbox. It starts nothing.
      </p>
    </section>
  );
}

// ---------- the drawer ----------

function History({ events }: { events: TaskEvent[] }) {
  return (
    <ol className="tkn-history" aria-label="History">
      {[...events].reverse().map((e) => (
        <li key={e.id}>
          <span className="tkn-ev-t mono" title={e.at}>
            {clock(e.at, false)}
          </span>
          <b className={`tkn-ev-who w-${e.actor}`}>
            {ACTOR[e.actor] ?? e.actor}
          </b>
          <span className="tkn-ev-n">
            {e.from_status && e.to_status && e.from_status !== e.to_status && (
              <span className="tkn-ev-move">
                {STATUS_WORDS[e.from_status as TaskStatus] ?? e.from_status} →{" "}
                {STATUS_WORDS[e.to_status as TaskStatus] ?? e.to_status}.{" "}
              </span>
            )}
            <Ticks text={e.note} />
          </span>
        </li>
      ))}
    </ol>
  );
}

const LONG = 280;

export function TaskPanel({
  id,
  pid,
  onClose,
  onChanged,
}: {
  id: string;
  pid: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { toast, setProjectId } = useApp();
  const r = useLoad(`task:${id}`, () => tasksApi.get(id));
  const t = r.data;
  const tpid = t?.project_id ?? pid;
  const [wfs, setWfs] = useState<Workflow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err | null>(null);
  const [pr, setPr] = useState("");
  const [note, setNote] = useState("");
  const [ask, setAsk] = useState<null | "cancel" | "delete" | "back">(null);
  const [more, setMore] = useState(false);
  const [descOpen, setDescOpen] = useState(false);
  /** "Pick a flow for me": keel is picking (the model may take a while), or what it proposed (its Inbox item). */
  const [picked, setPicked] = useState<
    | null
    | { busy: true }
    | { busy: false; title: string; item: TaskItem | null }
  >(null);
  const review = useHasPage("repo");
  useEffect(() => {
    if (t && t.project_id !== pid) setProjectId(t.project_id);
  }, [t, pid, setProjectId]);
  useEffect(() => {
    let live = true;
    tasksApi.workflows(tpid).then(
      (l) => live && setWfs(l),
      (e) => live && setErr(errorParts(e)),
    );
    return () => {
      live = false;
    };
  }, [tpid]);
  // the proposal was answered (Start, Not now, here or in the Inbox): its "keel picked a flow" line goes too
  useEffect(() => {
    if (
      picked &&
      !picked.busy &&
      picked.item &&
      t &&
      !t.waiting.some((w) => w.id === picked.item!.id)
    )
      setPicked(null);
  }, [t, picked]);

  const run = async (what: () => Promise<Task | unknown>, msg: string) => {
    setBusy(true);
    setErr(null);
    try {
      const next = await what();
      if (next && typeof next === "object" && "id" in next && "waiting" in next)
        r.setData(next as Task);
      else await r.reload();
      toast(msg);
      setAsk(null);
      setNote("");
      onChanged();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  const pick = async (task: Task) => {
    setBusy(true);
    setErr(null);
    setPicked({ busy: true });
    toast(PICKING);
    try {
      const next = await tasksApi.pick(task.id);
      r.setData(next);
      const item =
        [...next.waiting].reverse().find((it) => it.kind === "task-proposal") ??
        null;
      setPicked({
        busy: false,
        title: item?.title ?? "keel made no proposal: see the history.",
        item,
      });
      toast(
        item
          ? `keel picked a flow: ${item.title}. It waits in your Inbox.`
          : "keel made no proposal: see the history.",
      );
      onChanged();
    } catch (e) {
      setErr(errorParts(e));
      setPicked(null);
      void r.reload();
    } finally {
      setBusy(false);
    }
  };

  const remove = async (task: Task) => {
    setBusy(true);
    try {
      await tasksApi.remove(task.id);
      toast(`${nameOf(task)} deleted.`);
      onChanged();
      onClose();
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };

  const openFlow = () => {
    if (t) setProjectId(t.project_id);
    go("flow");
  };

  const title = t ? `${nameOf(t)} · ${t.title}` : "Task";
  const flowRuns = !!t?.flow && ["running", "waiting"].includes(t.flow.status);
  const canStart =
    !!t && ["todo", "in_progress", "blocked"].includes(t.status) && !flowRuns;
  const backable =
    !!t && ["in_review", "testing_pp", "ready_prod"].includes(t.status);
  const closed = !!t && (t.status === "done" || t.status === "cancelled");
  const props = t ? proposals(t) : [];
  const flowProposal = props.some((p) => (p.data ?? {}).kind !== "review");
  const fwf = t?.flow?.workflow_id
    ? wfs?.find((w) => w.id === t.flow!.workflow_id)
    : undefined;
  const pct = t ? progressOf(t, fwf) : null;
  /** where the flow is, in the workflow's own words (its step name), else its phase */
  const at = t?.flow ? (fwf?.steps.find((x) => x.id === t.flow!.current)?.name ?? t.flow.phase ?? null) : null;
  const n = t?.pr_url ? prNumber(t.pr_url) : null;

  const foot = t && (
    <div className="tkn-foot">
      {ask === "back" && (
        <div className="confirm" role="group" aria-label="Send back">
          <label htmlFor="tkn-back">Why does it go back?</label>
          <textarea
            id="tkn-back"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="row">
            <button
              className="btn sm warn"
              type="button"
              disabled={busy || !note.trim()}
              onClick={() =>
                void run(
                  () => tasksApi.move(t.id, "in_progress", note.trim()),
                  "Sent back to In progress.",
                )
              }
            >
              Send back
            </button>
            <button
              className="btn sm"
              type="button"
              onClick={() => setAsk(null)}
            >
              Keep it
            </button>
          </div>
        </div>
      )}
      {ask === "cancel" && (
        <Confirm
          text={`Cancel ${nameOf(t)}?${flowRuns ? " Its flow stops." : ""}`}
          yes="Cancel task"
          busy={busy}
          onYes={() =>
            void run(() => tasksApi.move(t.id, "cancelled"), "Cancelled.")
          }
          onNo={() => setAsk(null)}
        />
      )}
      {ask === "delete" && (
        <Confirm
          text={`Delete ${nameOf(t)} and its history? The Jira ticket stays.`}
          yes="Delete"
          busy={busy}
          onYes={() => void remove(t)}
          onNo={() => setAsk(null)}
        />
      )}
      <div className="row tkn-more" role="group" aria-label="More actions">
        <button
          className="btn"
          type="button"
          aria-expanded={more}
          onClick={() => setMore((m) => !m)}
        >
          {more ? "Less" : "More actions"}
        </button>
        {more && (
          <>
            {t.status === "in_review" && (
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(
                    () => tasksApi.move(t.id, "testing_pp"),
                    "Approved: Testing (PP).",
                  )
                }
                title="keel reads the reviews with a GitHub token; without one, say it here"
              >
                Mark approved
              </button>
            )}
            {t.status === "testing_pp" && !t.waiting.length && (
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(() => tasksApi.confirm(t.id, "pp"), "Confirmed.")
                }
              >
                Confirm PP
              </button>
            )}
            {t.status === "ready_prod" && !t.waiting.length && (
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(() => tasksApi.confirm(t.id, "prod"), "Shipped.")
                }
              >
                Ship
              </button>
            )}
            {backable && (
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() => setAsk("back")}
              >
                Send back
              </button>
            )}
            {closed && (
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(() => tasksApi.move(t.id, "todo"), "Back to To do.")
                }
              >
                Reopen
              </button>
            )}
            {!closed && (
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() => setAsk("cancel")}
              >
                Cancel task
              </button>
            )}
            <button
              className="btn tkn-danger"
              type="button"
              disabled={busy}
              onClick={() => setAsk("delete")}
            >
              Delete
            </button>
          </>
        )}
      </div>
    </div>
  );

  return (
    <Drawer
      title={title}
      onClose={onClose}
      id="task-drawer"
      className="tkn-drawer"
      footer={foot || undefined}
    >
      {r.error && !t ? (
        <ErrorBox error={r.error} onRetry={() => void r.reload()} />
      ) : !t ? (
        <Loading what="Loading the task" />
      ) : (
        <>
          <div className="tkn-head">
            <span className="tag">{keyOf(t)}</span>
            <span className={`tkn-type t-${t.type}`}>{t.type}</span>
            <StatusPill s={t.status} />
            <span className="tkn-grow" />
            {t.external_url && (
              <a
                className="btn sm ghost"
                href={t.external_url}
                target="_blank"
                rel="noreferrer"
              >
                Open in Jira ↗
              </a>
            )}
          </div>
          <dl className="tkn-facts">
            <dt>Status</dt>
            <dd>
              {STATUS_WORDS[t.status]}
              {t.external_status ? (
                <>
                  {" "}
                  · Jira: <b>{t.external_status}</b>
                </>
              ) : null}
            </dd>
            {t.assignee && (
              <>
                <dt>Assignee</dt>
                <dd>{t.assignee}</dd>
              </>
            )}
            {t.priority && (
              <>
                <dt>Priority</dt>
                <dd>{t.priority}</dd>
              </>
            )}
            <dt>Source</dt>
            <dd>
              {t.source === "jira"
                ? "Jira ticket"
                : "Local task (no Jira ticket)"}
            </dd>
          </dl>

          {t.status === "blocked" && (
            <div className="errbox" role="note">
              <b>Blocked</b>
              <span className="sub">
                {t.blocked_reason ?? "No reason given."} Start a new flow, or
                move it by hand.
              </span>
            </div>
          )}

          {answerItems(t).map((it) => (
            <WaitCard
              key={it.id}
              it={it}
              busy={busy}
              onAct={(action, nt) =>
                run(
                  () => tasksApi.act(it.id, action, nt),
                  action === "done"
                    ? "Recorded: you moved it in Jira."
                    : action === "confirm"
                      ? "Confirmed."
                      : action === "pr"
                        ? "PR link saved: the task is in review."
                        : action === "later"
                          ? "Put aside."
                          : "Sent back to In progress.",
                )
              }
            />
          ))}
          {t.flow?.status === "waiting" && (
            <div
              className="tkn-wait"
              role="group"
              aria-label="The flow waits for you"
            >
              <span className="tkn-wait-tag">waits for you</span>
              <b className="tkn-wait-q">
                The {t.flow.workflow_id ?? ""} flow waits for your answer
                {at ? ` at ${at}` : ""}.
              </b>
              <span className="sub">
                Answer it on the flow, or in the Inbox with the other questions.
              </span>
              <div className="row">
                <button
                  className="btn primary"
                  type="button"
                  onClick={openFlow}
                >
                  Answer on the flow ▸
                </button>
                <a className="btn" href={hashFor("inbox", t.project_id)}>
                  Open the Inbox
                </a>
              </div>
            </div>
          )}
          {reviewComments(t).map((it) => (
            <section key={it.id} className="tkn-wait" aria-label={it.title}>
              <span className="tkn-wait-tag">review comment</span>
              <b className="tkn-wait-q">
                <Ticks text={it.title} />
              </b>
              {it.detail && (
                <span className="sub">
                  <Ticks text={it.detail} />
                </span>
              )}
              <AutomationItem
                pid={t.project_id}
                kind={it.kind}
                data={it.data ?? {}}
                idBase={`tkn-item-${it.id}`}
                itemId={it.id}
                actions={[
                  ...((it.data ?? {}).reply
                    ? [{ id: "reply", label: "Use keel's reply" }]
                    : []),
                  { id: "address", label: "Address it (small flow)" },
                  { id: "handle", label: "I'll handle it" },
                ]}
                onAct={async (action, b) => {
                  r.setData(await tasksApi.actWith(it.id, action, b));
                  toast(
                    action === "handle"
                      ? "Noted: you handle it."
                      : action === "address"
                        ? "A small flow addresses it."
                        : "Reply posted.",
                  );
                  onChanged();
                }}
              />
            </section>
          ))}

          {t.flow && (
            <section className="tkn-flow" aria-label="Flow">
              <div className="tkn-flow-top">
                <b>{short(fwf, t.flow.workflow_id ?? "")} flow</b>
                <Pill tone={flowTone(t.flow.status)}>
                  {flowWords(t.flow.status)}
                </Pill>
                {at && t.flow.status !== "done" && (
                  <span className="sub">at {at}</span>
                )}
                <span className="tkn-grow" />
                <button
                  className="linkbtn tkn-link"
                  type="button"
                  onClick={openFlow}
                >
                  Open flow ▸
                </button>
              </div>
              {pct !== null && (
                <div
                  className={`tkn-bar f-${t.flow.status}`}
                  role="progressbar"
                  aria-label="How far the flow is"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={pct}
                >
                  <span style={{ width: `${pct}%` }} />
                </div>
              )}
            </section>
          )}

          {props.map((it) => (
            <ProposalCard
              key={it.id}
              it={it}
              wfs={wfs}
              onAct={async (action, b) => {
                r.setData(await tasksApi.actWith(it.id, action, b));
                toast(
                  action === "later"
                    ? "Put aside."
                    : "Started: follow it on the flow.",
                );
                onChanged();
              }}
            />
          ))}

          {canStart && !flowProposal && (
            <SuggestCard
              key={t.id}
              t={t}
              wfs={wfs}
              picking={!!picked?.busy}
              onPick={() => void pick(t)}
              onNotNow={onClose}
              onStarted={(nx) => {
                r.setData(nx);
                toast(`${nameOf(nx)}: the ${nx.workflow_id} flow started.`);
                onChanged();
              }}
            />
          )}
          {picked && (
            <p
              className="sub tkn-picked"
              role="status"
              data-testid="pick-status"
            >
              {picked.busy ? (
                PICKING
              ) : (
                <>
                  {picked.title}
                  {picked.item && (
                    <>
                      {" "}
                      <a
                        className="linkbtn tkn-link"
                        href={inboxItemHash(picked.item)}
                      >
                        Open it in the Inbox ▸
                      </a>
                    </>
                  )}
                </>
              )}
            </p>
          )}

          {t.pr_url && (
            <section className="tkn-sec" aria-label="Pull request">
              <h3>Pull request</h3>
              <div className="tkn-pr">
                <a href={t.pr_url} target="_blank" rel="noreferrer">
                  <b>{prLabel(t.pr_url)}</b>{" "}
                  <span className="mono sub">
                    {t.pr_url.replace(/^https?:\/\//, "")}
                  </span>
                </a>
                {review && n && /\/pull\//.test(t.pr_url) && (
                  <a className="tkn-link" href={`#/repo/@review/pr:${n}`}>
                    Open in Review ▸
                  </a>
                )}
              </div>
              {t.reviewers.length > 0 && (
                <div className="chips" aria-label="Reviewers">
                  {t.reviewers.map((v) => (
                    <span
                      key={`${v.on}:${v.login}`}
                      className={`chip ${v.state === "approved" ? "c-ok" : v.state === "changes_requested" ? "c-bad" : ""}`}
                      title={v.on}
                    >
                      {v.on === "jira" ? "Jira " : "@"}
                      {v.login} · {v.state.replace("_", " ")}
                    </span>
                  ))}
                </div>
              )}
              {!closed && (
                <div className="row">
                  <button
                    className="btn sm"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void run(
                        () => tasksApi.review(t.id),
                        "keel proposes a review: see its proposal.",
                      )
                    }
                  >
                    Review it
                  </button>
                  <span className="hint">
                    keel reads the pull request and proposes a review. It posts
                    nothing without you.
                  </span>
                </div>
              )}
            </section>
          )}
          {t.status === "in_progress" && !t.pr_url && (
            <section className="tkn-sec" aria-label="Pull request">
              <h3>Pull request</h3>
              <span className="sub">
                keel never pushes. When the flow built the PR body but opened no
                PR, open it yourself and paste its link: the task goes to In
                review.
              </span>
              <div className="row">
                <input
                  type="text"
                  className="inline-input"
                  aria-label="PR link"
                  placeholder="https://github.com/owner/repo/pull/12"
                  value={pr}
                  onChange={(e) => setPr(e.target.value)}
                  style={{ flex: "1 1 220px" }}
                />
                <button
                  className="btn sm"
                  type="button"
                  disabled={busy || !pr.trim()}
                  onClick={() =>
                    void run(
                      () => tasksApi.setPr(t.id, pr.trim()),
                      "In review.",
                    )
                  }
                >
                  Save link
                </button>
              </div>
            </section>
          )}
          {err && <ErrorBox error={err} />}

          <section className="tkn-sec" aria-label="Description">
            <h3>Description</h3>
            {t.description ? (
              <>
                <p className="tkn-desc">
                  {t.description.length > LONG && !descOpen
                    ? `${t.description.slice(0, LONG - 20).trim()}…`
                    : t.description}
                </p>
                {t.description.length > LONG && (
                  <button
                    className="linkbtn tkn-link"
                    type="button"
                    aria-expanded={descOpen}
                    onClick={() => setDescOpen((o) => !o)}
                  >
                    {descOpen ? "Show less" : "Show more"}
                  </button>
                )}
              </>
            ) : (
              <span className="sub">No description.</span>
            )}
          </section>
          <section className="tkn-sec" aria-label="History section">
            <h3>History</h3>
            {t.events?.length ? (
              <History events={t.events} />
            ) : (
              <span className="sub">Nothing yet.</span>
            )}
          </section>
        </>
      )}
    </Drawer>
  );
}
