// The keel activity: which phase and rule apply to the open file (with its history and an unlock for this phase),
// and keel's own pages — the files keel wrote and what agents remember (Memory) — which open as editor tabs.

import { Fragment, useState } from "react";
import { api, errorParts, type Commit, type Fact, type FactKind, type Memory, type RepoFile } from "../../api";
import { RefreshStaleButton } from "../../components/RefreshStale";
import { EmptyState } from "../../components/page";
import { Async, Confirm, Drawer, ErrorBox, Panel, Pill, type PillTone } from "../../components/ui";
import { clock, kfmt, plural } from "../../format";
import { useApp, useLoad } from "../../state";
import { Icon } from "./icons";
import { acOf } from "./model";

/** One sentence: what agents may do with this file in the active phase. */
export function ruleText(f: RepoFile): string {
  const phase = f.phase && f.phase !== "none" ? f.phase : null;
  if (!phase) return "No flow is running, so no phase rule applies now.";
  switch (f.verdict) {
    case "deny": return `Frozen in ${phase}: agents cannot edit it.`;
    case "new-only": return `In ${phase}, agents may only add new files like this, not change this one.`;
    case "delete-only": return `In ${phase}, agents may only delete it.`;
    default: return `Editable in ${phase} by the phase's agent.`;
  }
}

export function statusText(mark: string | undefined | null): string {
  return mark === "A" ? "added on this branch (not committed)" : mark === "M" ? "changed (not committed)" : mark === "D" ? "deleted (not committed)" : "unchanged since the last commit";
}

type Props = { pid: string; file: RepoFile | null; fileError: { message: string; hint?: string } | null; onOpenDocs: () => void; onOpenMemory: () => void };

export function KeelView({ pid, file, fileError, onOpenDocs, onOpenMemory }: Props) {
  return (
    <div className="sv">
      <div className="sv-head"><h2 className="sv-title">keel</h2></div>
      <div className="sv-scroll">
        <section className="kv-sec" aria-label="Rules for the open file">
          <h3>The open file</h3>
          {fileError ? <ErrorBox error={fileError} /> : !file ? <p className="sub">Open a file to see its status, which AC changed it, and whether agents may edit it now.</p> : (
            <>
              <p className="mono kv-path">{file.path}</p>
              <div className={`kv-rule${file.frozen ? " frozen" : ""}`}>{file.frozen && <Icon name="lock" size={14} />}<span>{ruleText(file)}</span></div>
              <dl className="kv-dl">
                <dt>Status</dt><dd>{statusText(file.mark)}</dd>
                <dt>Changed by AC</dt><dd>{file.ac ? <span className="scm-ac">{file.ac}</span> : <span className="sub">no AC on this branch</span>}</dd>
                <dt>keel sees it as</dt><dd className="mono">{file.bucket ?? "—"}</dd>
                <dt>Written by</dt><dd>{file.keel ? "keel" : "people and agents"}</dd>
                <dt>Last commit</dt><dd>{file.last_commit ? <span title={file.last_commit.message}><span className="mono">{file.last_commit.sha.slice(0, 7)}</span> {file.last_commit.message}</span> : "—"}</dd>
              </dl>
              <FileActions key={file.path} pid={pid} f={file} />
            </>
          )}
        </section>
        <section className="kv-sec" aria-label="keel's pages">
          <h3>keel</h3>
          <button type="button" className="kv-link" onClick={onOpenDocs}><Icon name="files" size={16} /><span><b>Files keel wrote</b><span className="sub">specs, knowledge, config: every change is a commit</span></span></button>
          <button type="button" className="kv-link" onClick={onOpenMemory}><Icon name="history" size={16} /><span><b>Memory</b><span className="sub">facts agents get with each step</span></span></button>
          <a className="kv-link" href="#/wiki"><Icon name="link" size={16} /><span><b>Wiki</b><span className="sub">the knowledge base, readable</span></span></a>
        </section>
      </div>
    </div>
  );
}

function FileActions({ pid, f }: { pid: string; f: RepoFile }) {
  const { project, toast } = useApp();
  const [hist, setHist] = useState<Commit[] | null>(null);
  const [histErr, setHistErr] = useState<{ message: string; hint?: string } | null>(null);
  const [histOpen, setHistOpen] = useState(false);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [unlockErr, setUnlockErr] = useState<{ message: string; hint?: string } | null>(null);
  const [unlocked, setUnlocked] = useState(false);
  const phase = project?.phase && project.phase !== "none" ? project.phase : undefined;

  const showHistory = async () => {
    if (histOpen) {
      setHistOpen(false);
      return;
    }
    setHistOpen(true);
    if (hist) return;
    setHistErr(null);
    try {
      setHist(await api.fileHistory(pid, f.path));
    } catch (e) {
      setHistErr(errorParts(e));
    }
  };
  const unlock = async () => {
    setBusy(true);
    setUnlockErr(null);
    try {
      await api.unlock(pid, f.path, phase);
      setUnlocked(true);
      setAsking(false);
      toast(`${f.path} is unlocked${phase ? ` in ${phase}` : ""}. The unlock is logged.`);
    } catch (e) {
      setUnlockErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="kv-actions">
      <div className="row">
        <button className="btn sm" type="button" onClick={showHistory} aria-expanded={histOpen}>History</button>
        {unlocked ? <Pill tone="ok">unlocked{phase ? ` in ${phase}` : ""}</Pill> : (
          <button className="btn sm ghost" type="button" onClick={() => setAsking(true)} disabled={asking}>Unlock for this phase</button>
        )}
      </div>
      {asking && (
        <Confirm
          text={<>Let agents edit <b className="mono">{f.path}</b>{phase ? <> in the <b>{phase}</b> phase</> : " in the current phase"}, for this flow only?
            keel rules normally stop this. The unlock is logged in the flow's events and shows in Memory.</>}
          yes="Yes, unlock it" busy={busy} onYes={unlock} onNo={() => setAsking(false)} />
      )}
      {unlockErr && <ErrorBox error={unlockErr} />}
      {histOpen && (histErr ? <ErrorBox error={histErr} /> : !hist ? <span className="pg-spin" role="status">Reading history…</span> : (
        <ol className="kv-hist" aria-label="File history">
          {hist.map((c) => (
            <li key={c.sha}>
              <span className="scm-msg">{c.keel && <span className="scm-keel">keel</span>}{acOf(c.message) && <span className="scm-ac">{acOf(c.message)}</span>}{c.message}</span>
              <span className="scm-meta mono">{c.sha.slice(0, 7)} · {c.author} · {clock(c.at, false)}</span>
            </li>
          ))}
          {!hist.length && <li className="sub">No commit touched this file yet.</li>}
        </ol>
      ))}
    </div>
  );
}

// ---------- keel's pages, shown as editor tabs ----------

const DOC_PILL: Record<string, [PillTone, string]> = { ok: ["ok", "ok"], live: ["run", "live"], check: ["warn", "check"] };

export function DocsView({ pid, onOpen }: { pid: string; onOpen: (path: string) => void }) {
  const { project } = useApp();
  const docs = useLoad(`keeldocs:${pid}`, () => api.keelDocs(pid));
  return (
    <div className="ed-doc">
      <Panel title={`Files keel wrote in ${project?.name ?? pid}`} extra={<span className="hint">every change is a commit you can review</span>} body={false}>
        <Async r={docs} what="Reading keel files">
          {(list) => (
            list.length ? <div className="table-wrap rt-wrap"><table className="rt" aria-label="Files keel wrote">
              <thead><tr><th>File</th><th>What</th><th>Written by</th><th>Updated</th><th><span className="sr-only">State</span></th></tr></thead>
              <tbody>
                {list.map((d) => (
                  <tr key={d.path}>
                    <td className="mono rt-main pg-wrap">{d.path.endsWith("/") ? d.path : <button type="button" className="linkbtn mono" onClick={() => onOpen(d.path)}>{d.path}</button>}</td>
                    <td className="rt-full">{d.what}</td><td className="sub" data-label="by">{d.by}</td><td className="sub">{d.updated}</td>
                    <td className="rt-end"><Pill tone={DOC_PILL[d.status]?.[0] ?? "idle"}>{DOC_PILL[d.status]?.[1] ?? d.status}</Pill></td>
                  </tr>
                ))}
              </tbody>
            </table></div> : <EmptyState compact title="keel has not written a file here yet">The init flow writes the knowledge base and keel's config; each change is a commit you can review.</EmptyState>
          )}
        </Async>
      </Panel>
      <p className="hint">Knowledge sections are readable in the <a href="#/wiki">Wiki</a>. A flow's state and events live in keel's data, not in <span className="mono">.keel/</span>.</p>
    </div>
  );
}

const KIND_TAG: Record<FactKind, string> = { rule: "keel", flaky: "star", fact: "", unlock: "" };
const KB_PILL: Record<string, [PillTone, string]> = { written: ["ok", "written"], stale: ["warn", "stale"], missing: ["idle", "not written"] };

function FactDrawer({ pid, fact, onClose, onSaved }: { pid: string; fact: Fact | null; onClose: () => void; onSaved: () => void }) {
  const { toast } = useApp();
  const [title, setTitle] = useState(fact?.title ?? "");
  const [text, setText] = useState(fact?.text ?? "");
  const [kind, setKind] = useState<FactKind>(fact?.kind ?? "fact");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (fact) await api.editFact(pid, fact.id, { title, text, kind });
      else await api.addFact(pid, { title, text, kind });
      toast(fact ? "Saved." : "Added. Agents see it from their next step.");
      onSaved();
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title={fact ? "Edit memory" : "Add to memory"} onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={save} disabled={busy || !title.trim() || !text.trim()}>{busy ? "Saving…" : "Save"}</button></>}>
      <div className="field"><label htmlFor="mf-title">Title</label><input type="text" id="mf-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Test needs Docker" /></div>
      <div className="field"><label htmlFor="mf-text">What agents should know</label><textarea id="mf-text" value={text} onChange={(e) => setText(e.target.value)} style={{ fontFamily: "var(--f-body)", fontSize: 14 }} /></div>
      <div className="field"><label htmlFor="mf-kind">Kind</label>
        <select id="mf-kind" value={kind} onChange={(e) => setKind(e.target.value as FactKind)}>
          <option value="fact">fact</option><option value="rule">rule — your preference</option><option value="flaky">flaky — a test to rerun once</option><option value="unlock">unlock — a file a phase may change</option>
        </select>
      </div>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

export function MemoryView({ pid }: { pid: string }) {
  const { project, toast } = useApp();
  const mem = useLoad<Memory>(`mem:${pid}`, () => api.memory(pid));
  const [edit, setEdit] = useState<Fact | null | "new">(null);
  const forget = async (f: Fact) => {
    try {
      await api.forgetFact(pid, f.id);
      mem.setData((m) => (m ? { ...m, facts: m.facts.filter((x) => x.id !== f.id) } : m));
      toast("Forgotten. Agents will not see it again.");
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  return (
    <div className="ed-doc">
      <Async r={mem} what="Reading memory">
        {(m) => {
          const chars = m.facts.reduce((a, f) => a + f.text.length + f.title.length, 0);
          const kbTokens = (m.knowledge.reduce((a, k) => a + k.words, 0) / Math.max(1, m.knowledge.length)) * 1.3;
          return (
            <div className="grid g2">
              <Panel title={`What agents remember about ${project?.name ?? pid}`} extra={m.facts.length ? <button className="btn sm" type="button" onClick={() => setEdit("new")}>Add</button> : undefined} body="grid">
                <div className="grid" style={{ gap: 8 }}>
                  {!m.facts.length && <EmptyState compact title="Nothing remembered yet" action={<button className="btn sm" type="button" onClick={() => setEdit("new")}>Add a fact</button>}>Agents and you add facts here as the project goes: a rule you prefer, a flaky test, a file a phase may change.</EmptyState>}
                  {m.facts.map((f) => (
                    <div key={f.id} className="mem">
                      <div className="row" style={{ justifyContent: "space-between" }}><b>{f.title}</b><span className={`tag ${KIND_TAG[f.kind] ?? ""}`}>{f.kind}</span></div>
                      <span>{f.text}</span>
                      <div className="row" style={{ justifyContent: "space-between" }}>
                        <span className="hint">from {f.source} · {clock(f.at, false)}</span>
                        <span className="row">
                          <button className="btn sm ghost" type="button" onClick={() => setEdit(f)} aria-label={`Edit ${f.title}`}>Edit</button>
                          <button className="btn sm ghost" type="button" onClick={() => forget(f)} aria-label={`Forget ${f.title}`}>Forget</button>
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </Panel>
              <div className="grid" style={{ alignContent: "start" }}>
                <Panel title="Knowledge base" extra={<span className="hint">checked against HEAD</span>} body="grid">
                  {m.knowledge.some((k) => k.status === "stale") && (
                    <div style={{ marginBottom: 8 }}><RefreshStaleButton pid={pid} sections={m.knowledge.filter((k) => k.status === "stale").map((k) => k.id)} /></div>
                  )}
                  <div className="grid" style={{ gap: 6 }}>
                    {!m.knowledge.length && <EmptyState compact title="No knowledge base yet">The init flow writes it: architecture, domain, conventions, data, integrations and journeys.</EmptyState>}
                    {m.knowledge.map((k) => (
                      <Fragment key={k.id}>
                        <div className="row" style={{ justifyContent: "space-between" }}>
                          <span><b>{k.id}</b> <span className="sub">{plural(k.words, "word")} · {plural(k.cites, "citation")}</span></span>
                          <Pill tone={KB_PILL[k.status]?.[0] ?? "idle"}>{KB_PILL[k.status]?.[1] ?? k.status}</Pill>
                        </div>
                      </Fragment>
                    ))}
                  </div>
                </Panel>
                <Panel title="Cost of memory" body="kv">
                  <span>Facts sent with each agent step</span><b className="num">{chars ? `≈ ${kfmt(chars / 4)} tokens` : "none yet"}</b>
                  <span>Knowledge section (one, when asked)</span><b className="num">{kbTokens ? `≈ ${kfmt(kbTokens)} tokens` : "none written yet"}</b>
                  <span>How facts are chosen</span><b>by phase and agent</b>
                </Panel>
              </div>
              {edit && <FactDrawer pid={pid} fact={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => void mem.reload()} />}
            </div>
          );
        }}
      </Async>
    </div>
  );
}
