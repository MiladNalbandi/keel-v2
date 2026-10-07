// Inbox (Run): everything that waits for a person, in every project — gates, the explorer's questions, failing checks,
// budget pauses, new dependencies. Each item can be answered here (the same resume as the Flow page) or opened on its
// Flow page. The list follows the app's event stream (gate events send project.changed to every tab).
// #/inbox/<project> opens it filtered to one project (the Answer link on All projects). Long details fold with
// "Show more"; after you answer, the focus moves to the next item so the keyboard can go on.

import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorParts } from "../api";
import { answersOf, ClarifyForm, type ClarifyAnswers } from "../components/ClarifyForm";
import { RunModeNote } from "../components/RunMode";
import { EmptyState } from "../components/EmptyState";
import { GateDetail } from "./Flow";
import { ErrorBox, Loading, PageHead, Pill, Since } from "../components/ui";
import { inboxApi, type InboxAnswer, type InboxItem, type InboxTask } from "../inboxApi";
import { go, useApp, useLoad, useRoute } from "../state";
import { tasksApi } from "../tasksApi";
import "../components/inbox.css";

const KIND_LABEL: Record<string, string> = {
  gate: "gate", clarify: "questions", fix: "needs a fix", budget: "budget", usage: "plan window", dependency: "new dependency",
  task: "task", "jira-manual": "move in Jira", permission: "may it run?",
};
const kindLabel = (k: string) => KIND_LABEL[k] ?? k;
const keyOf = (it: InboxItem) => `${it.thread_id}:${it.id ?? it.step}`;

/** Details longer than this fold behind "Show more". */
const LONG_LINES = 6;
const LONG_CHARS = 420;

function Detail({ text, id }: { text: string; id: string }) {
  const long = text.split("\n").length > LONG_LINES || text.length > LONG_CHARS;
  const [open, setOpen] = useState(false);
  return (
    <div className="inbox-detail-wrap">
      {/* a gate's text is Markdown (a PR body, a final review), with `$ command` output folded, as on the Flow page */}
      <div id={id} className={`inbox-detail md${long ? (open ? " open" : " folded") : ""}`}><GateDetail text={text} /></div>
      {long && (
        <button className="btn sm ghost inbox-more" type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
          {open ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

type Buttons = { approve: string; reject: string; needWhy: boolean; whyLabel: string };

/** The button words for one item (the engine's labels win). */
function buttonsFor(it: InboxItem): Buttons {
  const l = it.labels ?? {};
  if (it.kind === "dependency") return { approve: l.approve ?? "Allow", reject: l.reject ?? "Refuse", needWhy: false, whyLabel: "Why (optional)" };
  if (it.kind === "budget") return { approve: "Continue over the cap", reject: "Stop here", needWhy: false, whyLabel: "Note (optional)" };
  if (it.kind === "fix") return { approve: l.approve ?? "Try again", reject: l.reject ?? "Stop the flow", needWhy: false, whyLabel: "Note (optional)" };
  if (it.kind === "clarify") return { approve: l.approve ?? "Send my answers", reject: "Send back", needWhy: false, whyLabel: "Anything else the explorer should know (optional)" };
  return { approve: l.approve ?? `Approve${it.ac ? " " + it.ac : ""}`, reject: l.reject ?? "Send back", needWhy: true, whyLabel: "Why (needed to send back)" };
}

/** v0.5.0: a task's item — confirm PP testing, confirm the release, or say the Jira ticket was moved by hand. */
function TaskInboxCard({ it, onDone }: { it: InboxItem; onDone: (msg: string) => Promise<void> }) {
  const { setProjectId } = useApp();
  const t = it.task!;
  const [note, setNote] = useState("");
  const [askNote, setAskNote] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const hid = `inbox-task-${t.item_id}`;
  const noteId = `${hid}-note`;
  const act = async (a: InboxTask["actions"][number]) => {
    if (a.needs_note && !note.trim()) {
      setAskNote(true);
      window.setTimeout(() => document.getElementById(noteId)?.focus(), 0);
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await tasksApi.act(t.item_id, a.id, note.trim() || undefined);
      await onDone(a.id === "done" ? `${t.key ?? t.title}: recorded.` : a.id === "send_back" ? `${t.key ?? t.title}: sent back.` : `${t.key ?? t.title}: confirmed.`);
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };
  const openTask = () => {
    setProjectId(it.project_id);
    go("tasks", t.id);
  };
  return (
    <article className={`inbox-item k-${it.kind}`} aria-labelledby={hid} data-testid="inbox-item" data-key={keyOf(it)} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone={it.kind === "jira-manual" ? "run" : "warn"}>{kindLabel(it.kind)}</Pill>
        <span className="sub"><b>{it.project_name}</b> · {t.key ? <span className="mono">{t.key}</span> : "task"} · {t.title}</span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title">{it.title}</h2>
      {it.detail && <p className="sub" style={{ margin: 0 }}>{it.detail}</p>}
      {(askNote || t.actions.some((a) => a.needs_note)) && (
        <div className="field">
          <label htmlFor={noteId}>{t.actions.some((a) => a.needs_note) ? "Note (needed to send back)" : "Note"}</label>
          <textarea id={noteId} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
      )}
      <div className="row inbox-actions">
        {t.actions.map((a, n) => (
          <button key={a.id} className={`btn sm${n === 0 ? " warn" : ""}`} type="button" disabled={busy} onClick={() => void act(a)}>{a.label}</button>
        ))}
        {t.url && <a className="btn sm ghost" href={t.url} target="_blank" rel="noreferrer">Open in Jira ↗</a>}
        <button className="btn sm ghost inbox-open" type="button" onClick={openTask} aria-label={`Open task ${t.key ?? t.title} in ${it.project_name}`}>Open task ▸</button>
      </div>
      {err && <ErrorBox error={err} />}
    </article>
  );
}

/** A Helper's command that waits for the person's OK: Allow once, Always (this command, for the rest of that chat), Deny. */
function PermissionInboxCard({ it, onDone }: { it: InboxItem; onDone: (msg: string) => Promise<void> }) {
  const { setProjectId } = useApp();
  const q = it.permission!;
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const hid = `inbox-perm-${q.id}`;
  const answer = async (decision: "once" | "always" | "deny") => {
    setBusy(true);
    setErr(null);
    try {
      await api.helperAnswer(it.project_id, q.id, decision, why.trim());
      await onDone(decision === "deny" ? "Refused: KeelBot will not run it." : "Allowed: KeelBot runs it now.");
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };
  return (
    <article className="inbox-item k-permission" aria-labelledby={hid} data-testid="inbox-item" data-key={keyOf(it)} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone="warn">{kindLabel(it.kind)}</Pill>
        <span className="sub"><b>{it.project_name}</b> · KeelBot · {it.flow}</span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title">{it.title}</h2>
      <pre className="code inbox-cmd">{q.command}</pre>
      <div className="field">
        <label htmlFor={`${hid}-why`}>Why not (optional, KeelBot reads it)</label>
        <input id={`${hid}-why`} value={why} onChange={(e) => setWhy(e.target.value)} />
      </div>
      <div className="row inbox-actions">
        <button className="btn sm warn" type="button" disabled={busy} onClick={() => void answer("once")}>Allow once</button>
        <button className="btn sm" type="button" disabled={busy} onClick={() => void answer("always")}>Always for this command</button>
        <button className="btn sm" type="button" disabled={busy} onClick={() => void answer("deny")}>Deny</button>
        <button className="btn sm ghost inbox-open" type="button" onClick={() => { setProjectId(it.project_id); go("repo"); }}
          aria-label={`Open KeelBot in ${it.project_name}`}>Open KeelBot ▸</button>
      </div>
      {err && <ErrorBox error={err} />}
    </article>
  );
}

function InboxCard({ it, onDone }: { it: InboxItem; onDone: (msg: string) => Promise<void> }) {
  const { setProjectId } = useApp();
  const b = buttonsFor(it);
  const [why, setWhy] = useState("");
  const [askWhy, setAskWhy] = useState(false);
  const [picked, setPicked] = useState<ClarifyAnswers>({});
  const [typed, setTyped] = useState<ClarifyAnswers>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const hid = `inbox-${it.thread_id}`;
  const whyId = `${hid}-why`;

  const act = async (decision: "approve" | "reject", payload?: Record<string, unknown>, msg?: string) => {
    if (decision === "reject" && b.needWhy && !why.trim()) {
      setAskWhy(true);
      window.setTimeout(() => document.getElementById(whyId)?.focus(), 0);
      return;
    }
    setBusy(true);
    setErr(null);
    const body: InboxAnswer = { decision, id: it.id ?? undefined, ...(why.trim() ? { why: why.trim() } : {}) };
    if (it.kind === "clarify" && it.questions?.length) body.payload = { answers: answersOf(it.questions, picked, typed) };
    else if (payload) body.payload = payload;
    try {
      await inboxApi.act(it.thread_id, body);
      await onDone(msg ?? (decision === "approve" ? `${it.project_name}: ${b.approve}. The flow goes on.` : `${it.project_name}: ${b.reject}.`));
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };

  const openFlow = () => {
    setProjectId(it.project_id);
    go("flow");
  };

  const choices = it.choices ?? [];
  return (
    <article className={`inbox-item k-${it.kind}`} aria-labelledby={hid} data-testid="inbox-item" data-key={keyOf(it)} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone={it.kind === "fix" || it.kind === "budget" || it.kind === "usage" ? "bad" : "warn"}>{kindLabel(it.kind)}</Pill>
        <span className="sub"><b>{it.project_name}</b> · {it.flow}{it.workflow_id ? <> · <span className="mono">{it.workflow_id}</span></> : null}</span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title">{it.title}</h2>
      <RunModeNote mode={it.run_mode} kind={it.kind} title={it.title} auto={{ count: it.auto_approved ?? 0, last: it.last_auto }} />
      {it.kind === "clarify" && it.questions?.length ? (
        <ClarifyForm questions={it.questions} picked={picked} typed={typed}
          onPick={(id, label) => setPicked((p) => ({ ...p, [id]: label }))} onType={(id, text) => setTyped((p) => ({ ...p, [id]: text }))} />
      ) : it.detail ? (
        <Detail text={it.detail} id={`${hid}-detail`} />
      ) : null}
      {it.more && <span className="hint">The rest is on the Flow page.</span>}
      {(askWhy || !b.needWhy) && (
        <div className="field">
          <label htmlFor={whyId}>{b.whyLabel}</label>
          <textarea id={whyId} rows={2} value={why} onChange={(e) => setWhy(e.target.value)} />
        </div>
      )}
      <div className="row inbox-actions">
        {it.kind === "usage" && choices.length ? (
          <>
            {choices.includes("continue") && <button className="btn warn sm" type="button" disabled={busy} onClick={() => act("approve", { choice: "continue" })}>Continue anyway</button>}
            {choices.includes("wait") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("approve", { choice: "wait" })}>Wait for the reset</button>}
            {choices.includes("cheaper") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("approve", { choice: "cheaper" })}>Use the cheaper model</button>}
            {choices.includes("stop") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("reject")}>Stop here</button>}
          </>
        ) : it.kind === "gate" && choices.length ? (
          <>
            {choices.map((c, n) => (
              <button key={c} className={`btn sm${n === 0 ? " warn" : ""}`} type="button" disabled={busy}
                onClick={() => act("approve", { choice: c }, `${it.project_name}: ${c}.`)}>{c}</button>
            ))}
            {it.options.includes("reject") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("reject")}>{b.reject}</button>}
          </>
        ) : (
          <>
            {it.options.includes("approve") && <button className="btn warn sm" type="button" disabled={busy} onClick={() => act("approve")}>{b.approve}</button>}
            {it.options.includes("reject") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("reject")}>{b.reject}</button>}
          </>
        )}
        <button className="btn sm ghost inbox-open" type="button" onClick={openFlow} aria-label={`Open flow ${it.flow} in ${it.project_name}`}>Open flow ▸</button>
      </div>
      {err && <ErrorBox error={err} />}
    </article>
  );
}

export function InboxPage() {
  const { toast, reloadProjects, projects: known } = useApp();
  const { arg } = useRoute();
  const r = useLoad("inbox", () => inboxApi.list());
  const [project, setProject] = useState(arg ?? "");
  useEffect(() => setProject(arg ?? ""), [arg]);
  const [kind, setKind] = useState("");
  const items = useMemo(
    () => (r.data?.items ?? []).filter((it) => (!project || it.project_id === project) && (!kind || it.kind === kind)),
    [r.data, project, kind],
  );
  // v0.7.x: when several flows of one project wait at once, each flow's items sit under its own heading
  const groups = useMemo(() => {
    const flowsOf = new Map<string, Set<string>>();
    for (const it of items) {
      if (it.task || it.permission) continue;
      flowsOf.set(it.project_id, (flowsOf.get(it.project_id) ?? new Set<string>()).add(it.thread_id));
    }
    if (![...flowsOf.values()].some((x) => x.size > 1)) return null;
    const order: string[] = [];
    const by = new Map<string, InboxItem[]>();
    for (const it of items) {
      const g = it.task || it.permission ? `other:${it.project_id}` : `${it.project_id}:${it.thread_id}`;
      if (!by.has(g)) { by.set(g, []); order.push(g); }
      by.get(g)!.push(it);
    }
    return order.map((g) => {
      const first = by.get(g)![0];
      return { key: g, items: by.get(g)!, title: g.startsWith("other:") ? `${first.project_name} · tasks and KeelBot` : `${first.project_name} · ${first.flow}` };
    });
  }, [items]);
  const total = r.data?.count ?? 0;
  // a project from the link that has nothing waiting still shows in the filter
  const projectOpts = useMemo(() => {
    const l = r.data?.projects ?? [];
    return project && !l.some((p) => p.id === project) ? [...l, { id: project, name: known.find((p) => p.id === project)?.name ?? project, count: 0 }] : l;
  }, [r.data, project, known]);

  // after an answer: focus the item that came after it (or before it, or the empty state)
  const listRef = useRef<HTMLDivElement>(null);
  const emptyRef = useRef<HTMLDivElement>(null);
  const focusNext = useRef<{ key: string | null; index: number } | null>(null);
  useEffect(() => {
    const f = focusNext.current;
    if (!f) return;
    focusNext.current = null;
    const els = [...(listRef.current?.querySelectorAll<HTMLElement>("[data-testid=inbox-item]") ?? [])];
    const target = els.find((el) => el.dataset.key === f.key) ?? els[Math.min(f.index, els.length - 1)] ?? emptyRef.current;
    target?.focus();
  }, [items]);

  const done = async (msg: string, key: string) => {
    const i = items.findIndex((x) => keyOf(x) === key);
    const next = items[i + 1] ?? items[i - 1] ?? null;
    focusNext.current = { key: next ? keyOf(next) : null, index: Math.max(0, i) };
    toast(msg);
    await Promise.all([r.reload(), reloadProjects()]);
  };

  const card = (it: InboxItem) => it.task
    ? <TaskInboxCard key={keyOf(it)} it={it} onDone={(msg) => done(msg, keyOf(it))} />
    : it.permission
      ? <PermissionInboxCard key={keyOf(it)} it={it} onDone={(msg) => done(msg, keyOf(it))} />
      : <InboxCard key={keyOf(it)} it={it} onDone={(msg) => done(msg, keyOf(it))} />;

  return (
    <>
      <PageHead title="Inbox" sub="Everything that waits for you, in every project: gates, questions, and tasks to confirm. Answer it here or open its flow or task." />
      <div className="row inbox-filters" role="group" aria-label="Filters">
        <label className="row" htmlFor="inbox-project"><span className="sub">In project</span>
          <select id="inbox-project" value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">All projects</option>
            {projectOpts.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.count})</option>)}
          </select>
        </label>
        <label className="row" htmlFor="inbox-kind"><span className="sub">Kind</span>
          <select id="inbox-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">All kinds</option>
            {(r.data?.kinds ?? []).map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
          </select>
        </label>
        {r.data && <span className="hint" aria-live="polite">{items.length === total ? `${total} waiting` : `${items.length} of ${total} waiting`}</span>}
      </div>
      {r.error && !r.data ? <ErrorBox error={r.error} onRetry={() => void r.reload()} />
        : !r.data ? <Loading what="Loading the inbox" />
          : !items.length ? (
            <div className="panel">
              <EmptyState ref={emptyRef} className="inbox-empty" title="Nothing is waiting for you"
                action={total ? <button className="btn sm" type="button" onClick={() => { setProject(""); setKind(""); }}>Show everything</button>
                  : <a className="btn sm" href="#/projects">See your projects</a>}>
                {total ? "Nothing matches these filters." : "When a flow stops at a gate or needs a decision, it shows up here."}
              </EmptyState>
            </div>
          ) : (
            <div className="inbox-list" ref={listRef}>
              {groups
                ? groups.map((g) => (
                  <section key={g.key} className="inbox-group" aria-label={g.title}>
                    <h2 className="inbox-group-h">{g.title} <span className="sub">{g.items.length}</span></h2>
                    {g.items.map(card)}
                  </section>
                ))
                : items.map(card)}
            </div>
          )}
    </>
  );
}
