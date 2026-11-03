// The Desk page (#/desk, #/desk/<request>): paste a support request, see its past cases and what in its text may be personal,
// press Sort (only then it goes to the model company), and follow it to its flow. The Inbox's card for a new request. It imports
// only react and @keel/web-sdk.

import { useState } from "react";
import {
  Async, del, EmptyState, errorParts, get, go, hashForScreen, Markdown, Note, PageHead, Panel, Pill, post, Since, useApp, useLoad, useRoute,
  type InboxCardProps, type PillTone,
} from "@keel/web-sdk";

export type DeskRequest = {
  id: string; project_id: string; title: string; body: string; origin: string | null; status: "new" | "sorting" | "sorted" | "handed" | "closed";
  kind: string | null; urgency: string | null; area: string | null; suggest: string | null; sorted_text: string | null; note: string | null;
  thread_id: string | null; choice: string | null; task_id: string | null; created_at: string; updated_at: string;
};
export type PastCase = { type: "request" | "task" | "flow"; id: string; title: string; status: string | null; score: number };
export type DeskView = { request: DeskRequest; past: PastCase[]; warnings: string[] };
export type DeskList = { requests: DeskRequest[]; sends: string };

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/desk/requests`;
export const deskApi = {
  list: (pid: string) => get<DeskList>(base(pid)),
  add: (pid: string, b: { title: string; body: string; origin: string }) => post<DeskView>(base(pid), b),
  one: (pid: string, id: string) => get<DeskView>(`${base(pid)}/${encodeURIComponent(id)}`),
  sort: (pid: string, id: string) => post<DeskRequest>(`${base(pid)}/${encodeURIComponent(id)}/sort`, {}),
  close: (pid: string, id: string, note: string) => post<DeskRequest>(`${base(pid)}/${encodeURIComponent(id)}/close`, { note }),
  remove: (pid: string, id: string) => del<unknown>(`${base(pid)}/${encodeURIComponent(id)}`),
};

const TONE: Record<string, PillTone> = { new: "warn", sorting: "run", sorted: "warn", handed: "ok", closed: "idle" };
const WORD: Record<string, string> = { new: "new", sorting: "being sorted", sorted: "waits for your choice", handed: "handed over", closed: "closed" };
const URGENT: Record<string, PillTone> = { high: "bad", normal: "idle", low: "idle" };

function AddRequest({ pid, onAdded }: { pid: string; onAdded: (id: string) => void }) {
  const [f, setF] = useState({ title: "", body: "", origin: "" });
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      const v = await deskApi.add(pid, f);
      setF({ title: "", body: "", origin: "" });
      onAdded(v.request.id);
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={add} aria-label="Add a request" className="grid" style={{ gap: 8 }}>
      <div className="field"><label htmlFor="dk-f1">Title (optional)</label> <input id="dk-f1" value={f.title} onChange={set("title")} placeholder="The first line, when you leave it empty" /></div>
      <div className="field"><label htmlFor="dk-f2">The request</label> <textarea id="dk-f2" required rows={5} value={f.body} onChange={set("body")} placeholder="What the person wrote, in their words" /></div>
      <div className="field"><label htmlFor="dk-f3">From (optional)</label> <input id="dk-f3" value={f.origin} onChange={set("origin")} placeholder="Who or where: this stays in keel and never goes to a model" /></div>
      {problem && <Note tone="err" role="alert">{problem}</Note>}
      <div><button className="btn primary" type="submit" disabled={busy}>{busy ? "Adding…" : "Add it"}</button></div>
    </form>
  );
}

function List({ pid }: { pid: string }) {
  const list = useLoad(`desk:${pid}`, () => deskApi.list(pid));
  return (
    <>
      <PageHead title="Desk" sub="Paste a support request. An agent sorts it, keel finds past cases, and you choose what happens next." />
      <Async r={list} what="Loading the requests">
        {(v) => (
          <div className="grid" style={{ gap: 12 }}>
            <Note tone="info"><b>What can go to the model company.</b> {v.sends}</Note>
            <Panel title="Add a request"><AddRequest pid={pid} onAdded={(id) => go("desk", id)} /></Panel>
            <Panel title={`Requests (${v.requests.length})`} body={false}>
              {v.requests.length ? (
                <div className="ss-tablewrap">
                  <table className="ss-table" aria-label="Requests">
                    <thead><tr><th>Request</th><th>Status</th><th>Kind</th><th>Urgency</th><th>Area</th></tr></thead>
                    <tbody>
                      {v.requests.map((r) => (
                        <tr key={r.id}>
                          <td><a href={hashForScreen("desk", r.id)}>{r.title}</a></td>
                          <td><Pill tone={TONE[r.status] ?? "idle"}>{WORD[r.status] ?? r.status}</Pill></td>
                          <td>{r.kind ?? ""}</td>
                          <td>{r.urgency ? <Pill tone={URGENT[r.urgency] ?? "idle"}>{r.urgency}</Pill> : ""}</td>
                          <td>{r.area ?? ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <EmptyState title="No request yet">Paste the first one above.</EmptyState>}
            </Panel>
          </div>
        )}
      </Async>
    </>
  );
}

const pastHref = (c: PastCase) => (c.type === "task" ? hashForScreen("tasks", c.id) : c.type === "flow" ? hashForScreen("flow", c.id) : hashForScreen("desk", c.id));

function One({ pid, id }: { pid: string; id: string }) {
  const { toast } = useApp();
  const view = useLoad(`desk:${pid}:${id}`, () => deskApi.one(pid, id));
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const run = async (fn: () => Promise<unknown>, done?: () => void) => {
    setBusy(true);
    setProblem(null);
    try {
      await fn();
      done?.();
      await view.reload();
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHead title={view.data?.request.title ?? "A request"} actions={<a className="btn sm ghost" href={hashForScreen("desk")}>All requests</a>} />
      <Async r={view} what="Loading the request">
        {({ request: r, past, warnings }) => (
          <div className="grid" style={{ gap: 12 }}>
            <Panel title="The request" extra={<Pill tone={TONE[r.status] ?? "idle"}>{WORD[r.status] ?? r.status}</Pill>}>
              <blockquote style={{ whiteSpace: "pre-wrap", margin: 0 }}>{r.body}</blockquote>
              {r.origin && <p className="sub">From: {r.origin} (stays in keel)</p>}
              {r.note && <p className="sub">Closed: {r.note}</p>}
              {r.task_id && <p><a href={hashForScreen("tasks", r.task_id)}>Open its task</a></p>}
              {r.thread_id && <p><a href={hashForScreen("flow", r.thread_id)}>Open its sorting flow</a></p>}
            </Panel>
            <Panel title={`Past cases (${past.length})`}>
              {past.length ? (
                <ul aria-label="Past cases">
                  {past.map((c) => <li key={`${c.type}:${c.id}`}>{c.type}: <a href={pastHref(c)}>{c.title}</a> {c.status ? <span className="sub">({c.status})</span> : null}</li>)}
                </ul>
              ) : <p className="sub">No request, task or flow of this project shares words with this one.</p>}
              <p className="sub">Found on this computer by shared words, with no model. Their titles go to the model company when you press Sort.</p>
            </Panel>
            {r.status === "new" && (
              <Panel title="Sort it">
                {warnings.length > 0 && <Note tone="warn" role="alert">This text seems to hold {warnings.join(", ")}. It goes to your model company if you press Sort. Delete this request and add it again without that, if you may not share it.</Note>}
                <p>Sort sends this text and the titles of the past cases to your model company, and an agent reads this project's repo. Then you choose: a fix flow, a change flow, a task, or close it.</p>
                {problem && <Note tone="err" role="alert">{problem}</Note>}
                <div className="row">
                  <button className="btn primary" type="button" disabled={busy} onClick={() => void run(() => deskApi.sort(pid, id), () => toast("Sorting. It waits for you at its gate."))}>Sort it</button>
                </div>
              </Panel>
            )}
            {r.sorted_text && (
              <Panel title="How it was sorted">
                <p>{r.kind && <Pill tone="run">{r.kind}</Pill>} {r.urgency && <Pill tone={URGENT[r.urgency] ?? "idle"}>{r.urgency}</Pill>} {r.area && <span className="mono">{r.area}</span>} {r.suggest && <span>· keel suggests <b>{r.suggest}</b></span>}</p>
                <Markdown text={r.sorted_text} />
                {r.status === "sorted" && r.thread_id && <p><a className="btn primary" href={hashForScreen("flow", r.thread_id)}>Choose what happens next</a></p>}
              </Panel>
            )}
            {r.status !== "closed" && (
              <Panel title="Close it">
                <div className="field"><label htmlFor="dk-f101">Why (optional)</label> <input id="dk-f101" value={note} onChange={(e) => setNote(e.target.value)} /></div>
                <div className="row">
                  <button className="btn" type="button" disabled={busy} onClick={() => void run(() => deskApi.close(pid, id, note))}>Close the request</button>
                  <button className="btn ghost" type="button" disabled={busy} onClick={() => void run(() => deskApi.remove(pid, id), () => go("desk"))}>Delete it from keel</button>
                </div>
              </Panel>
            )}
          </div>
        )}
      </Async>
    </>
  );
}

export function DeskPage({ pid }: { pid: string }) {
  const { arg } = useRoute();
  return arg ? <One key={arg} pid={pid} id={arg} /> : <List pid={pid} />;
}

/** The Inbox: a new request nobody sorted yet. Sort stays one click further, on the page that says what goes where. */
export function DeskInboxCard({ item: it, cardKey, kindLabel, onDone }: InboxCardProps) {
  const { setProjectId } = useApp();
  const open = () => {
    setProjectId(it.project_id);
    go("desk", it.id ?? undefined);
  };
  const hid = `inbox-desk-${it.id}`;
  const close = async () => {
    await deskApi.close(it.project_id, it.id ?? "", "");
    await onDone("Request closed.");
  };
  return (
    <article className="inbox-item" aria-labelledby={hid} data-testid="inbox-item" data-key={cardKey} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone="run">{kindLabel}</Pill>
        <span className="sub"><b>{it.project_name}</b></span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title">{it.title}</h2>
      <p className="sub" style={{ margin: 0, whiteSpace: "pre-wrap" }}>{it.detail}{it.more ? "…" : ""}</p>
      <div className="row">
        <button className="btn sm primary" type="button" onClick={open}>Open it</button>
        <button className="btn sm ghost" type="button" onClick={() => void close()}>Close it</button>
      </div>
    </article>
  );
}
