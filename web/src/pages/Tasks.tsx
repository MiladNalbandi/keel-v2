// Tasks (Run): the work of this project as a board — from Jira (synced) or your own list. A task starts a flow only
// when you press Start; keel then moves it (and its Jira ticket) to In review when the PR opens, to Testing (PP) when the
// PR is approved, and waits for you to confirm PP and the release. #/tasks/<id> opens one task's drawer.

import { useEffect, useMemo, useState } from "react";
import { api, errorParts, type RunMode, type Workflow } from "../api";
import { EmptyState } from "../components/EmptyState";
import { RunModePicker } from "../components/RunMode";
import { Confirm, Drawer, ErrorBox, Loading, PageHead, Pill, type PillTone } from "../components/ui";
import { agoText } from "../components/UsageStrip";
import { clock } from "../format";
import { hashFor } from "../routes";
import {
  DEFAULT_FLOW, STATUS_LABEL, tasksApi, type NewTask, type Task, type TaskEvent, type TaskItem, type TaskList, type TaskStatus, type TaskType,
} from "../tasksApi";
import { go, useApp, useLoad, useRoute } from "../state";
import { WorkspaceDoctor } from "../components/WorkspaceDoctor";

/** The board's columns, in order; Blocked shows only when a task is blocked, cancelled tasks sit under Done. */
const COLUMNS: TaskStatus[] = ["todo", "in_progress", "in_review", "testing_pp", "ready_prod", "done"];
const column = (s: TaskStatus): TaskStatus => (s === "cancelled" ? "done" : s);

const STATUS_TONE: Record<TaskStatus, PillTone> = {
  todo: "idle", in_progress: "run", in_review: "warn", testing_pp: "warn", ready_prod: "warn", done: "ok", cancelled: "idle", blocked: "bad",
};
const TYPE_LABEL: Record<TaskType, string> = { bug: "bug", story: "story", task: "task" };
const ACTOR: Record<TaskEvent["actor"], string> = { user: "you", keel: "keel", jira: "Jira" };

const nameOf = (t: Task) => t.external_key ?? `task ${t.id.slice(0, 8)}`;
const prLabel = (url: string) => url.match(/\/pull\/(\d+)/)?.[1] ? `PR #${url.match(/\/pull\/(\d+)/)![1]}` : "PR";
const reviewerMark = (s: string) => (s === "approved" ? "✓" : s === "changes_requested" ? "✕" : s === "commented" ? "…" : "");

function FlowPill({ t }: { t: Task }) {
  if (!t.flow) return null;
  const s = t.flow.status;
  const tone: PillTone = s === "running" ? "run" : s === "waiting" ? "warn" : s === "failed" ? "bad" : s === "done" ? "ok" : "idle";
  return <Pill tone={tone} title={`flow ${t.flow.workflow_id ?? ""} · ${s}`}>{t.flow.workflow_id ?? "flow"} · {s}{s === "running" && t.flow.phase ? ` · ${t.flow.phase}` : ""}</Pill>;
}

function TaskCard({ t, onOpen }: { t: Task; onOpen: () => void }) {
  return (
    <li className={`tk-card s-${t.status}`} data-testid="task-card">
      <button type="button" className="tk-card-btn" onClick={onOpen} aria-label={`${nameOf(t)}: ${t.title}`}>
        <span className="tk-card-top">
          <span className="tag" title={t.external_key ? "Jira ticket" : "a local task (no Jira ticket)"}>{t.external_key ?? "local"}</span>
          <span className={`tk-type t-${t.type}`}>{TYPE_LABEL[t.type]}</span>
          {t.status === "cancelled" && <Pill tone="idle">cancelled</Pill>}
          {t.waiting.length > 0 && <b className="amber tk-needs" title="waiting for you in the Inbox">◆ needs you</b>}
        </span>
        <span className="tk-title">{t.title}</span>
        <span className="tk-meta">
          {t.assignee && <span className="sub">{t.assignee}</span>}
          <FlowPill t={t} />
          {t.pr_url && <span className="mono sub">{prLabel(t.pr_url)}</span>}
        </span>
        {t.reviewers.length > 0 && (
          <span className="chips tk-revs">
            {t.reviewers.map((r) => <span key={`${r.on}:${r.login}`} className={`chip ${r.state === "approved" ? "c-ok" : r.state === "changes_requested" ? "c-bad" : ""}`}
              title={`${r.on}: ${r.state}`}>{r.on === "jira" ? "J " : "@"}{r.login}{reviewerMark(r.state) && ` ${reviewerMark(r.state)}`}</span>)}
          </span>
        )}
        {t.status === "blocked" && t.blocked_reason && <span className="tk-why">{t.blocked_reason}</span>}
      </button>
    </li>
  );
}

// ---------- New task ----------

function NewTaskDrawer({ pid, onClose, onMade }: { pid: string; onClose: () => void; onMade: (t: Task) => void }) {
  const [title, setTitle] = useState("");
  const [type, setType] = useState<TaskType>("task");
  const [description, setDescription] = useState("");
  const [key, setKey] = useState("");
  const [reviewers, setReviewers] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const make = async () => {
    setBusy(true);
    setErr(null);
    const body: NewTask = { title: title.trim(), type, description: description.trim() || undefined, external_key: key.trim() || undefined,
      reviewers: reviewers.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean) };
    try {
      onMade(await tasksApi.create(pid, body));
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };
  return (
    <Drawer title="New task" onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={make} disabled={busy || !title.trim()}>{busy ? "Creating…" : "Create task"}</button></>}>
      <div className="field">
        <label htmlFor="nt-title">Title</label>
        <input type="text" id="nt-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What should change?" />
      </div>
      <fieldset className="field tk-types">
        <legend className="lab">Type</legend>
        <div className="row">
          {(["bug", "story", "task"] as TaskType[]).map((k) => (
            <label key={k} className="radio"><input type="radio" name="nt-type" value={k} checked={type === k} onChange={() => setType(k)} />
              <span><b>{k}</b> <span className="sub">starts the {DEFAULT_FLOW[k]} flow</span></span></label>
          ))}
        </div>
      </fieldset>
      <div className="field">
        <label htmlFor="nt-desc">Description</label>
        <textarea id="nt-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={5} placeholder="What you know: the goal, the place, how to check it." />
      </div>
      <div className="field">
        <label htmlFor="nt-key">Jira key (optional)</label>
        <input type="text" id="nt-key" value={key} onChange={(e) => setKey(e.target.value)} placeholder="ABC-123" />
        <span className="hint">The real ticket this task belongs to. Without a Jira connection keel asks you to move it by hand.</span>
      </div>
      <div className="field">
        <label htmlFor="nt-rev">GitHub reviewers (optional)</label>
        <input type="text" id="nt-rev" value={reviewers} onChange={(e) => setReviewers(e.target.value)} placeholder="octocat, hubot" />
        <span className="hint">Asked on the PR when it opens (needs a GitHub token in Connections).</span>
      </div>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

// ---------- one task ----------

function History({ events }: { events: TaskEvent[] }) {
  return (
    <ol className="tk-history" aria-label="History">
      {[...events].reverse().map((e) => (
        <li key={e.id} className={`tk-ev a-${e.actor}`}>
          <span className="tk-ev-t mono sub" title={e.at}>{clock(e.at, false)}</span>
          <span className={`tk-ev-who w-${e.actor}`}>{ACTOR[e.actor] ?? e.actor}</span>
          <span className="tk-ev-n">
            {e.from_status && e.to_status && e.from_status !== e.to_status && (
              <b className="tk-ev-move">{STATUS_LABEL[e.from_status as TaskStatus] ?? e.from_status} → {STATUS_LABEL[e.to_status as TaskStatus] ?? e.to_status}. </b>
            )}
            {e.note}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** An Inbox item of this task, with the same buttons as the Inbox. */
function ItemBox({ it, onAct, busy }: { it: TaskItem; onAct: (action: string, note?: string) => Promise<void>; busy: boolean }) {
  const [note, setNote] = useState("");
  const [askNote, setAskNote] = useState(false);
  const id = `tk-item-${it.id}`;
  return (
    <div className={`tk-item k-${it.kind}`} role="group" aria-label={it.title}>
      <b>{it.title}</b>
      <span className="sub">{it.detail}</span>
      {(askNote || it.kind === "task") && (
        <div className="field">
          <label htmlFor={id}>{it.kind === "task" ? "Note (needed to send back)" : "Note"}</label>
          <textarea id={id} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
      )}
      <div className="row">
        {it.kind === "jira-manual" ? (
          <button className="btn sm warn" type="button" disabled={busy} onClick={() => void onAct("done")}>Done, I moved it</button>
        ) : (
          <>
            <button className="btn sm warn" type="button" disabled={busy} onClick={() => void onAct("confirm", note.trim() || undefined)}>
              {it.stage === "prod" ? "Shipped, confirm" : "PP works, confirm"}
            </button>
            <button className="btn sm" type="button" disabled={busy} onClick={() => {
              if (!note.trim()) { setAskNote(true); document.getElementById(id)?.focus(); return; }
              void onAct("send_back", note.trim());
            }}>Send back</button>
          </>
        )}
      </div>
    </div>
  );
}

function StartBox({ t, pid, onStarted }: { t: Task; pid: string; onStarted: (t: Task) => void }) {
  const [wfs, setWfs] = useState<Workflow[] | null>(null);
  const [wid, setWid] = useState("");
  const [mode, setMode] = useState<RunMode>("manual");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [allowDirty, setAllowDirty] = useState(false);
  const [allowFake, setAllowFake] = useState(false);
  const [doctor, setDoctor] = useState(false);
  useEffect(() => {
    api.workflows(pid).then((l) => {
      setWfs(l);
      const want = DEFAULT_FLOW[t.type];
      setWid(l.some((w) => w.id === want) ? want : l.find((w) => w.id === "feature")?.id ?? l[0]?.id ?? "");
    }, (e) => setErr(errorParts(e)));
    api.projectSettings(pid).then((s) => setMode(s.effective.run_mode ?? "manual"), () => undefined);
  }, [pid, t.type]);
  const dirty = !!err && /uncommitted changes/i.test(err.message);
  const fake = !!err && /fake model/i.test(err.message);
  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      onStarted(await tasksApi.start(t.id, { workflow_id: wid || undefined, run_mode: mode, ...(allowDirty ? { allow_dirty: true } : {}), ...(allowFake ? { allow_fake: true } : {}) }));
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="tk-sec" aria-label="Start a flow">
      <h3>Start a flow</h3>
      <div className="field">
        <label htmlFor="tk-wf">Workflow</label>
        <select id="tk-wf" value={wid} onChange={(e) => setWid(e.target.value)} disabled={!wfs}>
          {!wfs && <option value="">Loading…</option>}
          {wfs?.map((w) => <option key={w.id} value={w.id}>{w.name}{w.id === DEFAULT_FLOW[t.type] ? " (for a " + t.type + ")" : ""}</option>)}
        </select>
        <span className="hint">The flow gets the title{t.description ? ", the description" : ""}{t.external_key ? " and the Jira key" : ""} as its request.</span>
      </div>
      <RunModePicker value={mode} onChange={setMode} />
      {/* Same help as Start a flow: the Doctor sorts the files (commit / stash / hide on this computer / .gitignore). */}
      {dirty && !doctor && <button className="btn" type="button" onClick={() => setDoctor(true)}>Ask the Doctor what to do with these files</button>}
      {doctor && <WorkspaceDoctor pid={pid} onClean={() => { setErr(null); setDoctor(false); }} />}
      {(dirty || allowDirty) && <label className="chk"><input type="checkbox" checked={allowDirty} onChange={(e) => setAllowDirty(e.target.checked)} /> Start anyway — my uncommitted files stay out of keel's commits</label>}
      {(fake || allowFake) && <label className="chk"><input type="checkbox" checked={allowFake} onChange={(e) => setAllowFake(e.target.checked)} /> Run with the fake model anyway</label>}
      {err && <ErrorBox error={err} />}
      <div className="row"><button className="btn primary" type="button" onClick={start} disabled={busy || !wid}>{busy ? "Starting…" : "Start flow"}</button></div>
    </section>
  );
}

function TaskDrawer({ id, pid, onClose, onChanged }: { id: string; pid: string; onClose: () => void; onChanged: () => void }) {
  const { toast, setProjectId } = useApp();
  const r = useLoad(`task:${id}`, () => tasksApi.get(id));
  const t = r.data;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [pr, setPr] = useState("");
  const [note, setNote] = useState("");
  const [ask, setAsk] = useState<null | "cancel" | "delete" | "back">(null);
  useEffect(() => {
    if (t && t.project_id !== pid) setProjectId(t.project_id);
  }, [t, pid, setProjectId]);

  const run = async (what: () => Promise<Task | unknown>, msg: string) => {
    setBusy(true);
    setErr(null);
    try {
      const next = await what();
      if (next && typeof next === "object" && "id" in next) r.setData(next as Task);
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

  const title = t ? `${nameOf(t)} · ${t.title}` : "Task";
  const flowRuns = t?.flow && ["running", "waiting"].includes(t.flow.status);
  const canStart = t && ["todo", "in_progress", "blocked"].includes(t.status) && !flowRuns;
  const backable = t && ["in_review", "testing_pp", "ready_prod"].includes(t.status);
  return (
    <Drawer title={title} onClose={onClose} id="task-drawer">
      {r.error && !t ? <ErrorBox error={r.error} onRetry={() => void r.reload()} /> : !t ? <Loading what="Loading the task" /> : (
        <>
          <div className="tk-head">
            <Pill tone={STATUS_TONE[t.status]}>{STATUS_LABEL[t.status]}</Pill>
            <span className={`tk-type t-${t.type}`}>{t.type}</span>
            <span className="sub">{t.source === "jira" ? "Jira" : "local"}</span>
            {t.external_status && <span className="sub">Jira: <b>{t.external_status}</b></span>}
            {t.external_url && <a className="btn sm ghost" href={t.external_url} target="_blank" rel="noreferrer">Open in Jira ↗</a>}
          </div>
          <dl className="kv tk-kv">
            {t.assignee && <><dt>Assignee</dt><dd>{t.assignee}</dd></>}
            {t.priority && <><dt>Priority</dt><dd>{t.priority}</dd></>}
            {t.flow && <><dt>Flow</dt><dd><FlowPill t={t} /> <button className="linkbtn" type="button" onClick={() => { setProjectId(t.project_id); go("flow"); }}>Open flow ▸</button></dd></>}
            {t.pr_url && <><dt>Pull request</dt><dd><a href={t.pr_url} target="_blank" rel="noreferrer" className="mono">{t.pr_url}</a></dd></>}
            {t.reviewers.length > 0 && <><dt>Reviewers</dt><dd className="chips">{t.reviewers.map((v) => (
              <span key={`${v.on}:${v.login}`} className="chip" title={v.on}>{v.on === "jira" ? "Jira " : "@"}{v.login} · {v.state.replace("_", " ")}</span>
            ))}</dd></>}
          </dl>
          {t.status === "blocked" && <div className="errbox" role="note"><b>Blocked</b><span className="sub">{t.blocked_reason ?? "No reason given."} Start a new flow, or move it by hand.</span></div>}

          {t.waiting.map((it) => (
            <ItemBox key={it.id} it={it} busy={busy} onAct={(action, n) => run(() => tasksApi.act(it.id, action, n),
              action === "done" ? "Recorded: you moved it in Jira." : action === "confirm" ? "Confirmed." : "Sent back to In progress.")} />
          ))}

          {canStart && <StartBox t={t} pid={t.project_id} onStarted={(n) => { r.setData(n); toast(`${nameOf(n)}: the ${n.workflow_id} flow started.`); onChanged(); }} />}

          {t.status === "in_progress" && !t.pr_url && (
            <section className="tk-sec" aria-label="Pull request">
              <h3>Pull request</h3>
              <span className="sub">keel never pushes. When the flow built the PR body but opened no PR, open it yourself and paste its link: the task goes to In review.</span>
              <div className="row">
                <input type="text" className="inline-input" aria-label="PR link" placeholder="https://github.com/owner/repo/pull/12" value={pr}
                  onChange={(e) => setPr(e.target.value)} style={{ flex: "1 1 220px" }} />
                <button className="btn sm" type="button" disabled={busy || !pr.trim()} onClick={() => void run(() => tasksApi.setPr(t.id, pr.trim()), "In review.")}>Save link</button>
              </div>
            </section>
          )}

          <section className="tk-sec" aria-label="Move">
            <div className="row">
              {t.status === "in_review" && (
                <button className="btn sm" type="button" disabled={busy} onClick={() => void run(() => tasksApi.move(t.id, "testing_pp"), "Approved: Testing (PP).")}
                  title="keel reads the reviews with a GitHub token; without one, say it here">Mark approved</button>
              )}
              {t.status === "testing_pp" && !t.waiting.length && (
                <button className="btn sm warn" type="button" disabled={busy} onClick={() => void run(() => tasksApi.confirm(t.id, "pp"), "Confirmed.")}>Confirm PP</button>
              )}
              {t.status === "ready_prod" && !t.waiting.length && (
                <button className="btn sm warn" type="button" disabled={busy} onClick={() => void run(() => tasksApi.confirm(t.id, "prod"), "Shipped.")}>Ship</button>
              )}
              {backable && <button className="btn sm" type="button" disabled={busy} onClick={() => setAsk("back")}>Send back</button>}
              {(t.status === "done" || t.status === "cancelled") && (
                <button className="btn sm" type="button" disabled={busy} onClick={() => void run(() => tasksApi.move(t.id, "todo"), "Back to To do.")}>Reopen</button>
              )}
              {t.status !== "done" && t.status !== "cancelled" && <button className="btn sm ghost" type="button" disabled={busy} onClick={() => setAsk("cancel")}>Cancel task</button>}
              <button className="btn sm ghost" type="button" disabled={busy} onClick={() => setAsk("delete")}>Delete</button>
            </div>
            {ask === "back" && (
              <div className="confirm" role="group" aria-label="Send back">
                <label htmlFor="tk-back">Why does it go back?</label>
                <textarea id="tk-back" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
                <div className="row">
                  <button className="btn sm warn" type="button" disabled={busy || !note.trim()} onClick={() => void run(() => tasksApi.move(t.id, "in_progress", note.trim()), "Sent back to In progress.")}>Send back</button>
                  <button className="btn sm" type="button" onClick={() => setAsk(null)}>Keep it</button>
                </div>
              </div>
            )}
            {ask === "cancel" && <Confirm text={`Cancel ${nameOf(t)}?${flowRuns ? " Its flow stops." : ""}`} yes="Cancel task" busy={busy}
              onYes={() => void run(() => tasksApi.move(t.id, "cancelled"), "Cancelled.")} onNo={() => setAsk(null)} />}
            {ask === "delete" && <Confirm text={`Delete ${nameOf(t)} and its history? The Jira ticket stays.`} yes="Delete" busy={busy}
              onYes={() => void remove(t)} onNo={() => setAsk(null)} />}
          </section>
          {err && <ErrorBox error={err} />}

          {t.description && (
            <section className="tk-sec" aria-label="Description">
              <h3>Description</h3>
              <p className="tk-desc">{t.description}</p>
            </section>
          )}
          <section className="tk-sec" aria-label="History section">
            <h3>History</h3>
            {t.events?.length ? <History events={t.events} /> : <span className="sub">Nothing yet.</span>}
          </section>
        </>
      )}
    </Drawer>
  );
}

// ---------- the page ----------

function SyncLine({ data, busy }: { data: TaskList; busy: boolean }) {
  const s = data.sync;
  if (!s.connected) {
    return (
      <p className="tk-sync" data-testid="sync-line">
        <span className="sub">Not connected to Jira: tasks stay in keel, with their history.</span>{" "}
        <a className="btn sm ghost" href={hashFor("connections")}>Connect Jira</a>
      </p>
    );
  }
  return (
    <p className="tk-sync" data-testid="sync-line">
      <span className="sub">Jira {s.kind === "server" ? "Server" : "Cloud"}{s.me ? ` as ${s.me}` : ""} · </span>
      <span className="sub">{s.last_sync_at ? `synced ${agoText(s.last_sync_at)}` : "not synced yet"}{s.poll_minutes ? `, every ${s.poll_minutes} min` : ", only by hand"}</span>
      {s.last_sync_error && <span className="badc tk-sync-err" role="alert">Last sync failed: {s.last_sync_error}</span>}
      {busy && <span className="sub"> · syncing…</span>}
    </p>
  );
}

export function TasksPage({ pid }: { pid: string }) {
  const { toast, reloadProjects } = useApp();
  const { arg } = useRoute();
  const r = useLoad(`tasks:${pid}`, () => tasksApi.list(pid));
  const [who, setWho] = useState<"all" | "mine">("all");
  const [source, setSource] = useState<"" | "jira" | "local">("");
  const [q, setQ] = useState("");
  const [making, setMaking] = useState(false);
  const [open, setOpen] = useState<string | null>(arg ?? null);
  const [syncing, setSyncing] = useState(false);
  useEffect(() => setOpen(arg ?? null), [arg]);

  const me = r.data?.sync.me ?? null;
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (r.data?.tasks ?? []).filter((t) =>
      (who === "all" || !t.assignee || t.assignee === me) && (!source || t.source === source) &&
      (!needle || `${t.external_key ?? ""} ${t.title}`.toLowerCase().includes(needle)));
  }, [r.data, who, source, q, me]);
  const blocked = shown.filter((t) => t.status === "blocked");
  const columns: TaskStatus[] = blocked.length ? ["blocked", ...COLUMNS] : COLUMNS;
  const total = r.data?.tasks.length ?? 0;

  const sync = async () => {
    setSyncing(true);
    try {
      const s = await tasksApi.sync(pid);
      if (!s.ok) toast(`Sync failed: ${s.error ?? "unknown"}`);
      else toast(s.jira ? `Synced: ${s.created} new, ${s.updated} changed, ${s.moved} moved${s.reviews_checked ? `; ${s.reviews_checked} PR(s) read` : ""}.`
        : s.reviews_checked ? `${s.reviews_checked} PR(s) read, ${s.reviews_moved} approved.` : "Nothing to sync: Jira is not connected.");
      await Promise.all([r.reload(), reloadProjects()]);
    } catch (e) {
      toast(errorParts(e).message);
    } finally {
      setSyncing(false);
    }
  };
  const close = () => {
    setOpen(null);
    if (arg) go("tasks");
  };

  return (
    <>
      <PageHead title="Tasks" sub="Your tickets as a board. Start a flow for one; keel moves it through review, PP testing and production, and asks you at PP and release."
        actions={<>
          <button className="btn" type="button" onClick={() => void sync()} disabled={syncing}>{syncing ? "Syncing…" : "Sync now"}</button>
          <button className="btn primary" type="button" onClick={() => setMaking(true)}>New task</button>
        </>} />
      {r.data && <SyncLine data={r.data} busy={syncing} />}
      <div className="row tk-filters" role="group" aria-label="Filters">
        <div className="tabs" role="tablist" aria-label="Whose tasks">
          <button role="tab" type="button" aria-selected={who === "all"} onClick={() => setWho("all")}>All</button>
          <button role="tab" type="button" aria-selected={who === "mine"} onClick={() => setWho("mine")}>Mine</button>
        </div>
        <label className="row" htmlFor="tk-source"><span className="sub">Source</span>
          <select id="tk-source" value={source} onChange={(e) => setSource(e.target.value as typeof source)}>
            <option value="">All</option><option value="jira">Jira</option><option value="local">Local</option>
          </select>
        </label>
        <input type="search" className="inline-input pg-search" placeholder="Find a task" aria-label="Find a task" value={q} onChange={(e) => setQ(e.target.value)} />
        {r.data && <span className="hint" aria-live="polite">{shown.length === total ? `${total} tasks` : `${shown.length} of ${total} tasks`}</span>}
      </div>
      {r.error && !r.data ? <ErrorBox error={r.error} onRetry={() => void r.reload()} />
        : !r.data ? <Loading what="Loading tasks" />
          : !total ? (
            <div className="panel">
              <EmptyState title="No task yet" action={<>
                <button className="btn primary" type="button" onClick={() => setMaking(true)}>New task</button>
                {r.data.sync.connected ? <button className="btn" type="button" onClick={() => void sync()}>Sync now</button>
                  : <a className="btn" href={hashFor("connections")}>Connect Jira</a>}
              </>}>
                Create one, or connect Jira to bring in your tickets. You can still start a flow directly on the Flow page.
              </EmptyState>
            </div>
          ) : (
            <div className="tk-board" aria-label="Task board">
              {columns.map((c) => {
                const list = shown.filter((t) => column(t.status) === c);
                return (
                  <section key={c} className={`tk-col c-${c}${list.length ? "" : " is-empty"}`} aria-labelledby={`tk-col-${c}`} data-testid={`col-${c}`}>
                    <h2 id={`tk-col-${c}`} className="tk-col-h"><span>{STATUS_LABEL[c]}</span><span className="tk-count">{list.length}</span></h2>
                    {list.length ? <ul className="tk-cards">{list.map((t) => <TaskCard key={t.id} t={t} onOpen={() => setOpen(t.id)} />)}</ul>
                      : <span className="sub tk-none">Nothing here</span>}
                  </section>
                );
              })}
            </div>
          )}
      {making && <NewTaskDrawer pid={pid} onClose={() => setMaking(false)} onMade={(t) => {
        setMaking(false);
        toast(`${nameOf(t)} created.`);
        void r.reload();
        setOpen(t.id);
      }} />}
      {open && <TaskDrawer key={open} id={open} pid={pid} onClose={close} onChanged={() => { void r.reload(); void reloadProjects(); }} />}
    </>
  );
}
