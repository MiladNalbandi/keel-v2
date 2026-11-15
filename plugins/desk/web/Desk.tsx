// The Desk page (#/desk, #/desk/<request>): paste a support request, see its past cases and what in its text may be personal,
// press Sort (only then it goes to the model company), and follow it to its flow. The Inbox's card for a new request. It imports
// only react and @keel/web-sdk.

import { useState } from "react";
import {
  agoText, Async, del, EmptyState, errorParts, get, go, hashForScreen, Markdown, Note, PageHead, Panel, Pill, post, Since, useApp, useLoad, useRoute,
  type InboxCardProps, type Loaded, type PillTone,
} from "@keel/web-sdk";
import { usePageKey } from "./keys";

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

function AddRequest({ pid, onAdded, onHide }: { pid: string; onAdded: (id: string) => void; onHide?: () => void }) {
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
  // ⌘↩ adds it from the text box, as the button says
  const send = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      (e.currentTarget as HTMLElement).closest("form")?.requestSubmit();
    }
  };
  return (
    <section className="panel rise" aria-labelledby="dk-add-h">
      <div className="panel-head"><h2 id="dk-add-h">Add a request</h2>{onHide && <button type="button" className="btn sm ghost" onClick={onHide}>Hide</button>}</div>
      <form onSubmit={add} aria-label="Add a request" className="panel-body grid" style={{ gap: 10 }} onKeyDown={send}>
        <div className="field"><label htmlFor="dk-f1">Title (optional)</label> <input id="dk-f1" value={f.title} onChange={set("title")} placeholder="The first line, when you leave it empty" /></div>
        <div className="field"><label htmlFor="dk-f2">The request</label> <textarea id="dk-f2" required rows={5} value={f.body} onChange={set("body")} placeholder="What the person wrote, in their words" /></div>
        <div className="field"><label htmlFor="dk-f3">From (optional)</label> <input id="dk-f3" value={f.origin} onChange={set("origin")} placeholder="Who or where: this stays in keel and never goes to a model" /></div>
        {problem && <Note tone="err" role="alert">{problem}</Note>}
        <div className="rdw-bar">
          <button className="btn primary" type="submit" disabled={busy} aria-keyshortcuts="Meta+Enter Control+Enter">{busy ? "Adding…" : "Add it"} <kbd aria-hidden="true">⌘↩</kbd></button>
          <span className="sub">Nothing goes to a model yet.</span>
        </div>
      </form>
    </section>
  );
}

type Show = "open" | "done" | "all";
const SHOWN: Record<Show, (r: DeskRequest) => boolean> = { open: (r) => r.status !== "closed", done: (r) => r.status === "closed", all: () => true };

/** The left column: what can go to the model company, and the requests. */
function Requests({ list, current }: { list: Loaded<DeskList>; current: string | null }) {
  const [show, setShow] = useState<Show>("open");
  return (
    <div className="rdw-list">
      <Async r={list} what="Loading the requests">
        {(v) => {
          const shown = v.requests.filter(SHOWN[show]);
          return (
            <>
              <Note tone="info"><b>What can go to the model company.</b> {v.sends}</Note>
              <div className="rdw-bar rise r1">
                <h2 className="lbl">Requests · {v.requests.length}</h2>
                <span className="grow" />
                <div className="seg2" role="group" aria-label="Show">
                  {(["open", "done", "all"] as const).map((s) => (
                    <button key={s} type="button" aria-pressed={show === s} onClick={() => setShow(s)}>{s === "open" ? "Open" : s === "done" ? "Done" : "All"}</button>
                  ))}
                </div>
              </div>
              {shown.length ? (
                <ul className="rdw-items rise r2" aria-label="Requests">
                  {shown.map((r) => (
                    <li key={r.id}>
                      <a className="rdw-item" href={hashForScreen("desk", r.id)} aria-current={r.id === current ? "page" : undefined}>
                        <span className="rdw-item-top"><b>{r.title}</b><Pill tone={TONE[r.status] ?? "idle"}>{WORD[r.status] ?? r.status}</Pill></span>
                        <span className="sub">
                          {r.kind && <span className="mono">{r.kind}</span>}
                          {r.urgency && <Pill tone={URGENT[r.urgency] ?? "idle"}>{r.urgency}</Pill>}
                          {r.area && <span>{r.area}</span>}
                          <span className="grow" />
                          <span>{agoText(r.created_at)}</span>
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
              ) : v.requests.length ? (
                <p className="sub">No {show === "done" ? "closed" : "open"} request.</p>
              ) : (
                <EmptyState title="No request yet" compact>Paste the first one in Add a request.</EmptyState>
              )}
            </>
          );
        }}
      </Async>
    </div>
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
  const sort = () => void run(() => deskApi.sort(pid, id), () => toast("Sorting. It waits for you at its gate."));
  usePageKey("s", () => { if (!busy) sort(); }, view.data?.request.status === "new");
  return (
    <Async r={view} what="Loading the request">
      {({ request: r, past, warnings }) => (
        <div className="rdw-detail">
          <section className="panel rise" aria-labelledby="dk-title">
            <div className="panel-body grid" style={{ gap: 10 }}>
              <div className="rdw-meta">
                <Pill tone={TONE[r.status] ?? "idle"}>{WORD[r.status] ?? r.status}</Pill>
                <span>{agoText(r.created_at)}</span>
                <span className="grow" />
                <a className="btn sm ghost" href={hashForScreen("desk")}>All requests</a>
              </div>
              <h2 id="dk-title" className="dk-title">{r.title}</h2>
              <blockquote className="rdw-quote">{r.body}</blockquote>
              {r.origin && <p className="sub" style={{ margin: 0 }}>From: {r.origin} (stays in keel)</p>}
              {r.note && <p className="sub" style={{ margin: 0 }}>Closed: {r.note}</p>}
              {(r.task_id || r.thread_id) && (
                <p className="rdw-bar" style={{ margin: 0 }}>
                  {r.task_id && <a href={hashForScreen("tasks", r.task_id)}>Open its task</a>}
                  {r.thread_id && <a href={hashForScreen("flow", r.thread_id)}>Open its sorting flow</a>}
                </p>
              )}
            </div>
          </section>
          <Panel title={`Past cases · ${past.length}`} className="rise r1">
            {past.length ? (
              <ul aria-label="Past cases" className="rdw-items">
                {past.map((c) => (
                  <li key={`${c.type}:${c.id}`}>
                    <a className="rdw-item dk-past" href={pastHref(c)}>
                      <span className="rdw-item-top"><span className="mono dk-type">{c.type}</span><b>{c.title}</b>{c.status ? <span className="sub">{c.status}</span> : null}<span aria-hidden="true" className="sub">›</span></span>
                    </a>
                  </li>
                ))}
              </ul>
            ) : <p className="sub" style={{ marginTop: 0 }}>No request, task or flow of this project shares words with this one.</p>}
            <p className="sub" style={{ marginBottom: 0 }}>Found on this computer by shared words, with no model. Their titles go to the model company when you press Sort.</p>
          </Panel>
          {r.status === "new" && (
            <Panel title="Sort it" className="rise r2">
              <div className="grid" style={{ gap: 10 }}>
                {warnings.length > 0 && <Note tone="warn" role="alert">This text seems to hold {warnings.join(", ")}. It goes to your model company if you press Sort. Delete this request and add it again without that, if you may not share it.</Note>}
                <p style={{ margin: 0 }}>Sort sends this text and the titles of the past cases to your model company, and an agent reads this project's repo. Then you choose: a fix flow, a change flow, a task, or close it.</p>
                {problem && <Note tone="err" role="alert">{problem}</Note>}
                <div className="rdw-bar">
                  <button className="btn primary" type="button" disabled={busy} onClick={sort} aria-keyshortcuts="S">Sort it <kbd aria-hidden="true">S</kbd></button>
                </div>
              </div>
            </Panel>
          )}
          {r.sorted_text && (
            <Panel title="How it was sorted" className="rise r2">
              <p className="rdw-bar" style={{ marginTop: 0 }}>{r.kind && <Pill tone="run">{r.kind}</Pill>} {r.urgency && <Pill tone={URGENT[r.urgency] ?? "idle"}>{r.urgency}</Pill>} {r.area && <span className="mono">{r.area}</span>} {r.suggest && <span>· keel suggests <b>{r.suggest}</b></span>}</p>
              <Markdown text={r.sorted_text} />
              {r.status === "sorted" && r.thread_id && <p style={{ marginBottom: 0 }}><a className="btn primary" href={hashForScreen("flow", r.thread_id)}>Choose what happens next</a></p>}
            </Panel>
          )}
          {r.status !== "closed" && (
            <Panel title="Close it" className="rise r3">
              <div className="grid" style={{ gap: 10 }}>
                <div className="field"><label htmlFor="dk-f101">Why (optional)</label> <input id="dk-f101" value={note} onChange={(e) => setNote(e.target.value)} /></div>
                <div className="rdw-bar">
                  <button className="btn" type="button" disabled={busy} onClick={() => void run(() => deskApi.close(pid, id, note))}>Close the request</button>
                  <button className="btn ghost" type="button" disabled={busy} onClick={() => void run(() => deskApi.remove(pid, id), () => go("desk"))}>Delete it from keel</button>
                </div>
              </div>
            </Panel>
          )}
        </div>
      )}
    </Async>
  );
}

/** #/desk and #/desk/<request>: the requests on the left, the open one on the right (one column on a phone). */
export function DeskPage({ pid }: { pid: string }) {
  const { arg } = useRoute();
  const list = useLoad(`desk:${pid}`, () => deskApi.list(pid));
  // the add form fills the right side while no request is open; on a request it opens above it with its button (or N)
  const [adding, setAdding] = useState(false);
  const add = () => {
    setAdding(true);
    window.setTimeout(() => document.getElementById("dk-f2")?.focus(), 0);
  };
  usePageKey("n", add);
  return (
    <div className="rdw rdw-desk">
      <PageHead
        title="Desk"
        sub="Paste a support request. An agent sorts it, keel finds past cases, and you choose what happens next."
        actions={<button className="btn primary" type="button" onClick={add} aria-keyshortcuts="N">Add a request <kbd aria-hidden="true">N</kbd></button>}
      />
      <div className="rdw-cols">
        <Requests list={list} current={arg ?? null} />
        <div className="rdw-detail">
          {(adding || !arg) && <AddRequest pid={pid} onHide={arg ? () => setAdding(false) : undefined} onAdded={(id) => { setAdding(false); void list.reload(); go("desk", id); }} />}
          {arg && <One key={arg} pid={pid} id={arg} />}
        </div>
      </div>
    </div>
  );
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
