// The Library page (#/library, #/library/<doc>): the company's documents, what can go to the model company, a search that stays on
// this computer, adding a document (pasted or from a text file), and one document with its switches. It imports only react and
// @keel/web-sdk.

import { useState } from "react";
import {
  Async, del, EmptyState, errorParts, get, go, goHash, hashForScreen, Markdown, Note, PageHead, Pill, post, Switch, useApp, useLoad, useRoute,
  type LauncherSourceItem, type Loaded,
} from "@keel/web-sdk";
import { usePageKey } from "./keys";

export type DocRow = { id: string; project_id: string | null; title: string; source: string | null; for_agents: boolean; version: number; updated_at: string; size: number };
export type Doc = DocRow & { body: string };
export type Hit = { doc_id: string; title: string; section: string | null; version: number; quote: string; score: number; for_agents: boolean };

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/library`;
export const libraryApi = {
  list: (pid: string) => get<{ docs: DocRow[]; sends: string }>(`${base(pid)}/docs`),
  one: (pid: string, id: string) => get<Doc>(`${base(pid)}/docs/${encodeURIComponent(id)}`),
  add: (pid: string, b: Record<string, unknown>) => post<Doc>(`${base(pid)}/docs`, b),
  change: (pid: string, id: string, b: Record<string, unknown>) => post<Doc>(`${base(pid)}/docs/${encodeURIComponent(id)}`, b),
  remove: (pid: string, id: string) => del<unknown>(`${base(pid)}/docs/${encodeURIComponent(id)}`),
  search: (pid: string, q: string) => get<Hit[]>(`${base(pid)}/search?q=${encodeURIComponent(q)}`),
  plugins: (pid: string) => get<{ name: string; enabled: boolean }[]>(`/projects/${encodeURIComponent(pid)}/plugins`),
};

/** Says plainly whether agents of this project reach the library at all. */
function AgentsLine({ pid }: { pid: string }) {
  const plugins = useLoad(`plugins:${pid}`, () => libraryApi.plugins(pid), { live: false });
  if (!plugins.data) return null;
  const on = !!plugins.data.find((p) => p.name === "library")?.enabled;
  return on ? <Note tone="ok">The Library is on for this project: KeelBot and the agents you gave its tools (Tools page, and the Answers flow's agents) can read the documents marked for agents.</Note>
    : <Note tone="info">The Library is off for this project, so no agent reads it here. Turn it on in <a href="#/settings/plugins">Settings › Plugins</a>.</Note>;
}

function AddDoc({ pid, onAdded, onCancel }: { pid: string; onAdded: (id: string) => void; onCancel?: () => void }) {
  const [f, setF] = useState({ title: "", body: "", source: "", scope: "all", for_agents: true });
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fromFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setF({ ...f, body: await file.text(), title: f.title || file.name.replace(/\.[^.]+$/, ""), source: f.source || file.name });
  };
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      onAdded((await libraryApi.add(pid, f)).id);
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel rise" aria-labelledby="lb-add-h">
      <div className="panel-head"><h2 id="lb-add-h">Add a document</h2></div>
      <form onSubmit={add} aria-label="Add a document" className="panel-body grid" style={{ gap: 10 }}>
        <div className="field"><label htmlFor="lb-f101">Title</label> <input id="lb-f101" required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
        <div className="field"><label htmlFor="lb-f102">The text</label> <textarea id="lb-f102" required rows={6} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} placeholder="Paste it, or choose a text file. Headings (# ...) help a search name the section." /></div>
        <div className="field"><label htmlFor="lb-f103">Or a text file</label> <input id="lb-f103" type="file" accept=".txt,.md,.markdown,text/plain" onChange={(e) => void fromFile(e)} /></div>
        <div className="field"><label htmlFor="lb-f104">Where it comes from (optional)</label> <input id="lb-f104" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} placeholder="Security policy, March edition" /></div>
        <div className="field"><label htmlFor="lb-f1">For</label> <select id="lb-f1" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })}>
            <option value="all">every project</option>
            <option value="project">only this project</option>
          </select></div>
        <label className="lb-check"><input type="checkbox" checked={f.for_agents} onChange={(e) => setF({ ...f, for_agents: e.target.checked })} /> Agents may read it (what they read goes to your model company)</label>
        {problem && <Note tone="err" role="alert">{problem}</Note>}
        <div className="rdw-bar">
          <button className="btn primary" type="submit" disabled={busy}>{busy ? "Adding…" : "Add it"}</button>
          {onCancel && <button className="btn ghost" type="button" onClick={onCancel}>Cancel</button>}
        </div>
      </form>
    </section>
  );
}

function Search({ pid }: { pid: string }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    if (q.trim()) setHits(await libraryApi.search(pid, q));
  };
  return (
    <form onSubmit={(e) => void run(e)} role="search" aria-label="Search the library" className="panel lb-search rise r1">
      <div className="rdw-bar" style={{ gap: 8, flexWrap: "nowrap" }}>
        <input className="rdw-inp" aria-label="Search words" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") { setQ(""); setHits(null); } }} placeholder="What does it say about…" style={{ flex: 1, minWidth: 0 }} />
        <button className="btn" type="submit">Search</button>
      </div>
      {hits && (hits.length ? (
        <>
          <p className="sub" style={{ margin: "10px 0 0" }}>{hits.length} {hits.length === 1 ? "passage" : "passages"} found on this computer, best first</p>
          <ul aria-label="Found" className="lb-hits">
            {hits.map((h, i) => (
              <li key={i}>
                <span className="rdw-bar" style={{ gap: 6 }}>
                  <a href={hashForScreen("library", h.doc_id)}>{h.title}</a>{h.section ? <span className="sub"> › {h.section}</span> : null} <span className="sub">· version {h.version}</span>
                  {!h.for_agents && <> <Pill tone="idle">people only</Pill></>}
                </span>
                <blockquote className="rdw-quote">{h.quote}</blockquote>
              </li>
            ))}
          </ul>
        </>
      ) : <p className="sub" style={{ margin: "10px 0 0" }}>The library says nothing about that.</p>)}
    </form>
  );
}

const scopeWord = (d: DocRow) => (d.project_id ? "this project" : "every project");

/** The left column: the search and the documents. */
function Docs({ pid, list, current }: { pid: string; list: Loaded<{ docs: DocRow[]; sends: string }>; current: string | null }) {
  return (
    <div className="rdw-list">
      <Search pid={pid} />
      <Async r={list} what="Loading the library">
        {(v) => (
          <>
            <div className="rdw-bar rise r2"><h2 className="lbl">Documents · {v.docs.length}</h2><span className="grow" /><span className="sub">Agents may read</span></div>
            {v.docs.length ? (
              <ul className="rdw-items rise r2" aria-label="Documents">
                {v.docs.map((d) => (
                  <li key={d.id}>
                    <a className="rdw-item" href={hashForScreen("library", d.id)} aria-current={d.id === current ? "page" : undefined}>
                      <span className="rdw-item-top"><b>{d.title}</b>{d.for_agents ? <Pill tone="ok">may read</Pill> : <Pill tone="idle">people only</Pill>}</span>
                      <span className="sub">{[d.source, scopeWord(d), `version ${d.version}`].filter(Boolean).join(" · ")}</span>
                    </a>
                  </li>
                ))}
              </ul>
            ) : <EmptyState title="The library is empty" compact>Add your first document in Add a document.</EmptyState>}
          </>
        )}
      </Async>
    </div>
  );
}

function One({ pid, id }: { pid: string; id: string }) {
  const { toast } = useApp();
  const doc = useLoad(`library:${pid}:${id}`, () => libraryApi.one(pid, id));
  const [edit, setEdit] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      await doc.reload();
    } catch (err) {
      const p = errorParts(err);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Async r={doc} what="Loading the document">
      {(d) => (
        <section className="panel rdw-detail lb-doc rise" aria-labelledby="lb-title">
          <div className="rdw-meta">
            <span className="lb-ver">Version {d.version}</span>
            <span>{[d.source, scopeWord(d)].filter(Boolean).join(" · ")}</span>
            <span className="grow" />
            {edit === null && <button className="btn sm" type="button" onClick={() => setEdit(d.body)}>Change the text</button>}
            <button className="btn sm ghost" type="button" disabled={busy} onClick={() => void libraryApi.remove(pid, id).then(() => go("library"))}>Delete it</button>
            <a className="btn sm ghost" href={hashForScreen("library")}>All documents</a>
          </div>
          <h2 id="lb-title" className="lb-title">{d.title}</h2>
          <div className="rdw-bar lb-who">
            <Switch on={d.for_agents} disabled={busy} onChange={(on: boolean) => void run(() => libraryApi.change(pid, id, { for_agents: on }))}>Agents may read it</Switch>
            {d.for_agents ? <span className="sub">When an agent reads this, it goes to your model company.</span> : <span className="sub">For people only: no agent tool returns it.</span>}
          </div>
          {edit === null ? <div className="lb-body"><Markdown text={d.body} /></div> : (
            <div className="grid" style={{ gap: 10 }}>
              <div className="field"><label htmlFor="lb-f105">The text</label> <textarea id="lb-f105" rows={14} value={edit} onChange={(e) => setEdit(e.target.value)} /></div>
              <div className="rdw-bar">
                <button className="btn primary" type="button" disabled={busy || !edit.trim()} onClick={() => void run(() => libraryApi.change(pid, id, { body: edit })).then(() => setEdit(null))}>Save a new version</button>
                <button className="btn ghost" type="button" onClick={() => setEdit(null)}>Cancel</button>
              </div>
            </div>
          )}
        </section>
      )}
    </Async>
  );
}

/** What can go to the model company, and whether agents read the library here: two notes side by side. */
function Notes({ pid, list }: { pid: string; list: Loaded<{ docs: DocRow[]; sends: string }> }) {
  return (
    <div className="lb-notes rise r1">
      {list.data && <Note tone="info"><b>What can go to the model company.</b> {list.data.sends}</Note>}
      <AgentsLine pid={pid} />
    </div>
  );
}

/** #/library and #/library/<doc>: the documents on the left, the open one on the right (one column on a phone). */
export function LibraryPage({ pid }: { pid: string }) {
  const { arg } = useRoute();
  const list = useLoad(`library:${pid}`, () => libraryApi.list(pid));
  // the add form fills the right side while no document is open; on one it opens above it with its button (or N)
  const [adding, setAdding] = useState(false);
  const add = () => {
    setAdding(true);
    window.setTimeout(() => document.getElementById("lb-f101")?.focus(), 0);
  };
  usePageKey("n", add);
  return (
    <div className="rdw rdw-library">
      <PageHead
        title="Library"
        sub="Your company's policies and rules in one place, which agents can search and quote with the source."
        actions={<button className="btn primary" type="button" onClick={add} aria-keyshortcuts="N">Add a document <kbd aria-hidden="true">N</kbd></button>}
      />
      {!arg && <Notes pid={pid} list={list} />}
      <div className="rdw-cols">
        <Docs pid={pid} list={list} current={arg ?? null} />
        <div className="rdw-detail">
          {(adding || !arg) && <AddDoc pid={pid} onCancel={arg ? () => setAdding(false) : undefined} onAdded={(id) => { setAdding(false); void list.reload(); go("library", id); }} />}
          {arg && <One key={arg} pid={pid} id={arg} />}
        </div>
      </div>
    </div>
  );
}

/** ⌘K: the library's documents, by title. */
export const libraryLauncher: LauncherSourceItem = {
  id: "library",
  title: "Library",
  order: 60,
  kinds: ["page"],
  load: (pid, notes) => libraryApi.list(pid).then((v) => v.docs, notes.failed("Library")),
  items: (ctx, data) => ((data as DocRow[] | null) ?? []).map((d) => ({
    id: `library:${d.id}`,
    kind: "page" as const,
    title: d.title,
    sub: "Library",
    actions: [{ id: "open", label: "Open the document", keys: "enter", run: goHash(ctx, hashForScreen("library", d.id)) }],
    score: -5,
  })),
};
