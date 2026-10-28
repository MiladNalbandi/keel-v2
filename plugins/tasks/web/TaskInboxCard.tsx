// The Inbox's card for a task's items (the slot inbox.card, kinds task and jira-manual): confirm PP testing, confirm
// the release, or say the Jira ticket was moved by hand. Moved from keel's pages/Inbox.tsx with the Tasks page.

import { useState } from "react";
import { ErrorBox, errorParts, go, Pill, Since, Ticks, useApp, type InboxCardProps, type InboxTask } from "@keel/web-sdk";
import { tasksApi } from "./tasksApi";
import { AUTOMATION_KINDS, AutomationItem } from "./Automations";

/** The kinds of a task's items, with their label in the Inbox (its pill and its Kind filter). */
export const TASK_ITEM_KINDS: Record<string, string> = { task: "task", "jira-manual": "move in Jira", ...AUTOMATION_KINDS };

/** v0.5.0: a task's item — confirm PP testing, confirm the release, or say the Jira ticket was moved by hand; 0.18.52
 *  keel's proposal or a KeelBot-owned ticket's review comment. */
export function TaskInboxCard(props: InboxCardProps) {
  return props.item.kind in AUTOMATION_KINDS ? <AutomationCard {...props} /> : <TaskItemCard {...props} />;
}

/** 0.18.52 "I want to run the fix flow on SHOPS-1234": why, questions, Start / Use another flow / Not now (or a review comment). */
function AutomationCard({ item: it, cardKey, kindLabel, onDone }: InboxCardProps) {
  const { setProjectId } = useApp();
  const t = it.task!;
  const hid = `inbox-task-${t.item_id}`;
  const data = { ...(it.payload ?? {}), ...(it.questions ? { questions: it.questions } : {}) } as Record<string, unknown>;
  const openTask = () => {
    setProjectId(it.project_id);
    go("tasks", t.id);
  };
  const done: Record<string, string> = { start: "the flow started", other: "the flow started", later: "put aside", reply: "reply posted",
    address: "a small flow addresses it", handle: "you handle it" };
  return (
    <article className={`inbox-item k-${it.kind}`} aria-labelledby={hid} data-testid="inbox-item" data-key={cardKey} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone="run">{kindLabel}</Pill>
        <span className="sub"><b>{it.project_name}</b> · {t.key ? <span className="mono">{t.key}</span> : "task"} · {t.title}</span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title"><Ticks text={it.title} /></h2>
      {it.detail && <p className="sub" style={{ margin: 0 }}><Ticks text={it.detail} /></p>}
      <AutomationItem pid={it.project_id} kind={it.kind} data={data} actions={t.actions} idBase={hid} itemId={t.item_id}
        onAct={async (action, b) => {
          await tasksApi.actWith(t.item_id, action, b);
          await onDone(`${t.key ?? t.title}: ${done[action] ?? "done"}.`);
        }} />
      <div className="row">
        {t.url && <a className="btn sm ghost" href={t.url} target="_blank" rel="noreferrer">Open in Jira ↗</a>}
        <button className="btn sm ghost inbox-open" type="button" onClick={openTask} aria-label={`Open task ${t.key ?? t.title} in ${it.project_name}`}>Open task ▸</button>
      </div>
    </article>
  );
}

function TaskItemCard({ item: it, cardKey, kindLabel, onDone }: InboxCardProps) {
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
      const said: Record<string, string> = { done: "recorded", send_back: "sent back", pr: "PR link saved, the task is in review", later: "put aside" };
      await onDone(`${t.key ?? t.title}: ${said[a.id] ?? "confirmed"}.`);
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
    <article className={`inbox-item k-${it.kind}`} aria-labelledby={hid} data-testid="inbox-item" data-key={cardKey} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone={it.kind === "jira-manual" ? "run" : "warn"}>{kindLabel}</Pill>
        <span className="sub"><b>{it.project_name}</b> · {t.key ? <span className="mono">{t.key}</span> : "task"} · {t.title}</span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title"><Ticks text={it.title} /></h2>
      {it.detail && <p className="sub" style={{ margin: 0 }}><Ticks text={it.detail} /></p>}
      {(askNote || t.actions.some((a) => a.needs_note)) && (
        <div className="field">
          {/* B11 the flow finished without a PR: the note box is its link */}
          {t.actions.some((a) => a.id === "pr") ? <>
            <label htmlFor={noteId}>Pull request link</label>
            <input id={noteId} type="url" value={note} onChange={(e) => setNote(e.target.value)} placeholder="https://github.com/acme/app/pull/12" />
          </> : <>
            <label htmlFor={noteId}>{t.actions.some((a) => a.needs_note) ? "Note (needed to send back)" : "Note"}</label>
            <textarea id={noteId} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
          </>}
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
