// The Library page (#/library, #/library/<doc>): the company's documents, what can go to the model company, a search that stays on
// this computer, adding a document (pasted or from a text file), and one document with its switches. It imports only react and
// @keel/web-sdk.

import { useState } from "react";
import {
  Async, del, EmptyState, errorParts, get, go, goHash, hashForScreen, Markdown, Note, PageHead, Panel, Pill, post, Switch, useApp, useLoad, useRoute,
  type LauncherSourceItem,
} from "@keel/web-sdk";

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

function AddDoc({ pid, onAdded }: { pid: string; onAdded: (id: string) => void }) {
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
    <form onSubmit={add} aria-label="Add a document" className="grid" style={{ gap: 8 }}>
      <div className="field"><label htmlFor="lb-f101">Title</label> <input id="lb-f101" required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
      <div className="field"><label htmlFor="lb-f102">The text</label> <textarea id="lb-f102" required rows={6} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} placeholder="Paste it, or choose a text file. Headings (# ...) help a search name the section." /></div>
      <div className="field"><label htmlFor="lb-f103">Or a text file</label> <input id="lb-f103" type="file" accept=".txt,.md,.markdown,text/plain" onChange={(e) => void fromFile(e)} /></div>
      <div className="field"><label htmlFor="lb-f104">Where it comes from (optional)</label> <input id="lb-f104" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} placeholder="Security policy, March edition" /></div>
      <div className="field"><label htmlFor="lb-f1">For</label> <select id="lb-f1" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })}>
          <option value="all">every project</option>
          <option value="project">only this project</option>
        </select></div>
      <label><input type="checkbox" checked={f.for_agents} onChange={(e) => setF({ ...f, for_agents: e.target.checked })} /> Agents may read it (what they read goes to your model company)</label>
      {problem && <Note tone="err" role="alert">{problem}</Note>}
      <div><button className="btn primary" type="submit" disabled={busy}>{busy ? "Adding…" : "Add it"}</button></div>
    </form>
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
    <form onSubmit={(e) => void run(e)} role="search" aria-label="Search the library">
      <div className="row">
        <input aria-label="Search words" value={q} onChange={(e) => setQ(e.target.value)} placeholder="What does it say about…" style={{ flex: 1 }} />
        <button className="btn" type="submit">Search</button>
      </div>
      {hits && (hits.length ? (
        <ul aria-label="Found" style={{ listStyle: "none", padding: 0 }}>
          {hits.map((h, i) => (
            <li key={i} style={{ padding: "8px 0", borderTop: "1px solid var(--border)" }}>
              <a href={hashForScreen("library", h.doc_id)}>{h.title}</a>{h.section ? <span className="sub"> › {h.section}</span> : null} <span className="sub">version {h.version}</span>
              {!h.for_agents && <> <Pill tone="idle">people only</Pill></>}
              <blockquote style={{ whiteSpace: "pre-wrap", margin: "4px 0 0" }}>{h.quote}</blockquote>
            </li>
          ))}
        </ul>
      ) : <p className="sub">The library says nothing about that.</p>)}
    </form>
  );
}

function List({ pid }: { pid: string }) {
  const list = useLoad(`library:${pid}`, () => libraryApi.list(pid));
  return (
    <>
      <PageHead title="Library" sub="Your company's policies and rules in one place, which agents can search and quote with the source." />
      <Async r={list} what="Loading the library">
        {(v) => (
          <div className="grid" style={{ gap: 12 }}>
            <Note tone="info"><b>What can go to the model company.</b> {v.sends}</Note>
            <AgentsLine pid={pid} />
            <Panel title="Search"><Search pid={pid} /></Panel>
            <Panel title={`Documents (${v.docs.length})`} body={false}>
              {v.docs.length ? (
                <div className="ss-tablewrap">
                  <table className="ss-table" aria-label="Documents">
                    <thead><tr><th>Title</th><th>From</th><th>For</th><th>Agents</th><th>Version</th></tr></thead>
                    <tbody>
                      {v.docs.map((d) => (
                        <tr key={d.id}>
                          <td><a href={hashForScreen("library", d.id)}>{d.title}</a></td>
                          <td>{d.source ?? ""}</td>
                          <td>{d.project_id ? "this project" : "every project"}</td>
                          <td>{d.for_agents ? <Pill tone="ok">may read</Pill> : <Pill tone="idle">people only</Pill>}</td>
                          <td>{d.version}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <EmptyState title="The library is empty">Add your first document below.</EmptyState>}
            </Panel>
            <Panel title="Add a document"><AddDoc pid={pid} onAdded={(id) => go("library", id)} /></Panel>
          </div>
        )}
      </Async>
    </>
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
    <>
      <PageHead title={doc.data?.title ?? "A document"} actions={<a className="btn sm ghost" href={hashForScreen("library")}>All documents</a>} />
      <Async r={doc} what="Loading the document">
        {(d) => (
          <div className="grid" style={{ gap: 12 }}>
            <Panel title={`Version ${d.version}`} extra={<span className="sub">{d.source ?? ""}</span>}>
              <div className="row" style={{ alignItems: "center" }}>
                <Switch on={d.for_agents} disabled={busy} onChange={(on: boolean) => void run(() => libraryApi.change(pid, id, { for_agents: on }))}>Agents may read it</Switch>
                {edit === null && <button className="btn sm" type="button" onClick={() => setEdit(d.body)}>Change the text</button>}
                <button className="btn sm ghost" type="button" disabled={busy} onClick={() => void libraryApi.remove(pid, id).then(() => go("library"))}>Delete it</button>
              </div>
              {d.for_agents ? <p className="sub">When an agent reads this, it goes to your model company.</p> : <p className="sub">For people only: no agent tool returns it.</p>}
              {edit === null ? <Markdown text={d.body} /> : (
                <div className="grid" style={{ gap: 8 }}>
                  <div className="field"><label htmlFor="lb-f105">The text</label> <textarea id="lb-f105" rows={14} value={edit} onChange={(e) => setEdit(e.target.value)} /></div>
                  <div className="row">
                    <button className="btn primary" type="button" disabled={busy || !edit.trim()} onClick={() => void run(() => libraryApi.change(pid, id, { body: edit })).then(() => setEdit(null))}>Save a new version</button>
                    <button className="btn ghost" type="button" onClick={() => setEdit(null)}>Cancel</button>
                  </div>
                </div>
              )}
            </Panel>
          </div>
        )}
      </Async>
    </>
  );
}

export function LibraryPage({ pid }: { pid: string }) {
  const { arg } = useRoute();
  return arg ? <One key={arg} pid={pid} id={arg} /> : <List pid={pid} />;
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
