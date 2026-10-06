// Repo (Project): branch bar, files tree with A/M/keel/frozen marks and a file detail, commits and worktrees,
// the files keel wrote, and what agents remember (memory facts you can add, edit and forget).

import { Fragment, useState } from "react";
import { api, errorParts, type Commit, type Fact, type FactKind, type IndexStatus, type Memory, type RepoFile, type RepoInfo, type TreeNode, type UpdateFromBase } from "../api";
import { RefreshStaleButton } from "../components/RefreshStale";
import { WorkspaceDoctor } from "../components/WorkspaceDoctor";
import { Async, Confirm, Drawer, ErrorBox, PageHead, Panel, Pill, Tabs, type PillTone } from "../components/ui";
import { clock, plural } from "../format";
import { useApp, useLoad } from "../state";

type Tab = "files" | "branch" | "docs" | "memory";

export function RepoPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const [tab, setTab] = useState<Tab>("files");
  const repo = useLoad(`repo:${pid}`, () => api.repo(pid));
  const [result, setResult] = useState<UpdateFromBase | { error: { message: string; hint?: string } } | null>(null);
  const update = result && <UpdateResult result={result} base={repo.data?.base ?? "base"} onClose={() => setResult(null)} />;
  return (
    <>
      <PageHead title="Repo" sub={<>{project?.name} · <span className="mono">{project?.root}</span></>} actions={<IndexBadge pid={pid} />} />
      <Async r={repo} what="Reading the repo">
        {(r) => (
          <div className="branchbar">
            <span className="br-ic" aria-hidden="true">⎇</span><b className="mono">{r.branch}</b>
            <span className="sub">from <span className="mono">{r.base}</span></span>
            <span className="tag">↑ {r.ahead} ahead</span><span className={`tag ${r.behind ? "star" : ""}`}>↓ {r.behind} behind</span>
            <span className="sub mono">{r.remote || "no remote"}</span>
            <UpdateFromBaseButton pid={pid} r={r} onResult={(x) => { setResult(x); void repo.reload(); }} />
          </div>
        )}
      </Async>
      {update}
      <div className="row" style={{ margin: "12px 0" }}>
        <Tabs value={tab} onChange={setTab} label="Repo" options={[["files", "Files"], ["branch", "Branch & commits"], ["docs", "keel docs"], ["memory", "Memory"]]} />
      </div>
      {tab === "files" && <FilesTab pid={pid} />}
      {tab === "branch" && repo.data && <BranchTab pid={pid} r={repo.data} />}
      {tab === "docs" && <DocsTab pid={pid} />}
      {tab === "memory" && <MemoryTab pid={pid} />}
    </>
  );
}

/** "Index: ready · 412 files · 3,100 symbols · 5 min ago" and Rebuild: the code graph agents query instead of grep. */
export function IndexBadge({ pid }: { pid: string }) {
  const { toast } = useApp();
  const idx = useLoad(`index:${pid}`, () => api.index(pid));
  const [busy, setBusy] = useState(false);
  const i = idx.data;
  const rebuild = async () => {
    setBusy(true);
    try {
      idx.setData(await api.rebuildIndex(pid));
    } catch (e) {
      toast(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  if (!i) return null;
  const text = indexText(i);
  return (
    <span className="row" style={{ gap: 8 }} aria-label="Code graph index">
      <span className={`tag ${i.status === "failed" ? "star" : ""}`} title={i.error ?? "The code graph agents use before grep"}>{text}</span>
      <button className="btn sm" type="button" onClick={rebuild} disabled={busy || i.status === "indexing"}>
        {busy ? "Starting…" : "Rebuild"}
      </button>
    </span>
  );
}

function ago(iso: string): string {
  const min = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000));
  if (Number.isNaN(min)) return "";
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  if (min < 48 * 60) return `${Math.round(min / 60)} h ago`;
  return `${Math.round(min / 1440)} days ago`;
}

function indexText(i: IndexStatus): string {
  if (i.status === "indexing") return "Index: indexing…";
  if (i.status === "failed") return `Index: failed: ${i.error ?? "unknown reason"}`;
  if (i.status === "idle") return "Index: not built yet";
  const when = i.indexed_at ? ` · ${ago(i.indexed_at)}` : "";
  return `Index: ready · ${plural(i.files, "file")} · ${plural(i.symbols, "symbol")}${when}`;
}

function UpdateFromBaseButton({ pid, r, onResult }: {
  pid: string; r: RepoInfo; onResult: (x: UpdateFromBase | { error: { message: string; hint?: string } }) => void;
}) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const out = await api.updateFromBase(pid);
      onResult(out);
      if (out.merged) toast(`Merged ${r.base} into ${r.branch}.`);
    } catch (e) {
      onResult({ error: errorParts(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <span style={{ marginLeft: "auto" }}>
      <button className={`btn sm ${r.behind ? "primary" : ""}`} type="button" onClick={run} disabled={busy}
        title={`git merge ${r.base} into ${r.branch}; stops and changes nothing if files conflict`}>
        {busy ? "Updating…" : `Update from ${r.base}`}
      </button>
    </span>
  );
}

function UpdateResult({ result, base, onClose }: { result: UpdateFromBase | { error: { message: string; hint?: string } }; base: string; onClose: () => void }) {
  const close = <button className="btn sm ghost" type="button" onClick={onClose}>Close</button>;
  if ("error" in result) {
    return <div style={{ marginTop: 12 }}><ErrorBox error={result.error} /></div>;
  }
  const out = result.output?.trim();
  const details = out ? <details><summary className="sub">git output</summary><pre className="outbox mono">{out}</pre></details> : null;
  if (result.conflicts?.length) {
    return (
      <div className="errbox" role="alert" style={{ marginTop: 12 }}>
        <b>Not updated: {result.conflicts.length === 1 ? "1 file conflicts" : `${result.conflicts.length} files conflict`} with {base}.</b>
        <span className="sub">keel stopped the merge, so nothing changed. Fix these files by hand (or ask an agent), then try again.</span>
        <ul className="errlist mono">{result.conflicts.map((c) => <li key={c}>{c}</li>)}</ul>
        {details}
        <div>{close}</div>
      </div>
    );
  }
  if (!result.ok) {
    return (
      <div className="errbox" role="alert" style={{ marginTop: 12 }}>
        <b>The update did not work.</b>
        {details ?? <span className="sub">git gave no output.</span>}
        <div>{close}</div>
      </div>
    );
  }
  return (
    <div className="okbox" role="status" style={{ marginTop: 12 }}>
      <b>{result.merged ? `Updated: ${base} is merged into this branch.` : `Already up to date with ${base}.`}</b>
      {details}
      <div>{close}</div>
    </div>
  );
}

function TreeRow({ n, sel, onPick }: { n: TreeNode; sel: boolean; onPick: (p: string) => void }) {
  const file = n.kind === "file";
  return (
    <button type="button" className={`trow ${n.kind} ${sel ? "sel" : ""}`} style={{ paddingLeft: 10 + n.depth * 18 }}
      onClick={file ? () => onPick(n.path) : undefined} tabIndex={file ? 0 : -1} aria-label={file ? `Open ${n.path}` : undefined}>
      <span className="ti" aria-hidden="true">{file ? "·" : "▾"}</span>
      <span className="tn">{n.name}{file ? "" : "/"}</span>
      {n.mark && <b className={`fm ${n.mark.toLowerCase()}`}>{n.mark}</b>}
      {n.ac && <span className="tag">{n.ac}</span>}
      {n.frozen && <span className="tag star">frozen</span>}
      {n.keel && <span className="tag keel">keel</span>}
    </button>
  );
}

function FilesTab({ pid }: { pid: string }) {
  const tree = useLoad(`tree:${pid}`, () => api.tree(pid, 4));
  const [path, setPath] = useState<string | null>(null);
  const file = useLoad(path ? `file:${pid}:${path}` : null, () => api.file(pid, path!));
  const [doctor, setDoctor] = useState(false);
  const dirty = (tree.data ?? []).filter((n) => n.mark).length;
  return (
    <>
    {dirty > 0 && (
      <div className="wbar" style={{ marginBottom: 12 }}>
        <span>{dirty} uncommitted file{dirty === 1 ? "" : "s"}. A flow starts only on a clean tree.</span>
        {!doctor && <button className="btn sm" type="button" onClick={() => setDoctor(true)}>Clean up with the Doctor</button>}
      </div>
    )}
    {doctor && <div style={{ marginBottom: 12 }}><WorkspaceDoctor pid={pid} onClean={() => void tree.reload()} /></div>}
    <div className="grid g2">
      <Panel title="Project structure" extra={<div className="legend">
        <span><b className="fm a">A</b> added</span><span><b className="fm m">M</b> changed</span>
        <span><span className="tag keel">keel</span> written by keel</span><span><span className="tag">frozen</span> locked in this phase</span>
      </div>}>
        <Async r={tree} what="Reading files">
          {(nodes) => nodes.length ? (
            <div className="tree">{nodes.map((n) => <TreeRow key={n.path} n={n} sel={n.path === path} onPick={setPath} />)}</div>
          ) : <div className="empty">The repo is empty.</div>}
        </Async>
      </Panel>
      <Panel title={path ?? "File"} body="grid">
        {!path ? <p className="sub" style={{ margin: 0 }}>Pick a file to see its status, which AC changed it, and whether agents may edit it now.</p> : (
          <Async r={file} what="Opening">
            {(f) => (
              <div className="grid" style={{ gap: 8 }}>
                <div className="kv">
                  <span>Status</span><b>{f.mark === "A" ? "added in this branch" : f.mark === "M" ? "changed in this branch" : f.mark === "D" ? "deleted in this branch" : "unchanged"}</b>
                  <span>Acceptance criterion</span><b>{f.ac || "—"}</b>
                  <span>In this phase</span><b>{f.frozen ? "frozen — agents cannot edit it" : "editable by the phase's agent"}</b>
                  <span>Written by</span><b>{f.keel ? "keel" : "people and agents"}</b>
                  <span>Size</span><b className="num">{f.size.toLocaleString()} bytes</b>
                  <span>Last commit</span><b className="mono">{f.last_commit || "—"}</b>
                </div>
                <pre className="head" aria-label="First lines of the file">{f.head || "(empty)"}</pre>
                <FileActions key={f.path} pid={pid} f={f} />
              </div>
            )}
          </Async>
        )}
      </Panel>
    </div>
    </>
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
    <div className="grid" style={{ gap: 8 }}>
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
      {histOpen && (
        <Panel title={`History of ${f.path.split("/").pop()}`} body={false} className="inner">
          {histErr ? <div className="panel-body"><ErrorBox error={histErr} /></div> : !hist ? <div className="empty loading">Reading history…</div> : (
            <div className="table-wrap"><table aria-label="File history">
              <thead><tr><th>Commit</th><th>Message</th><th>By</th><th>When</th></tr></thead>
              <tbody>
                {hist.map((c) => <tr key={c.sha}><td className="mono sub">{c.sha.slice(0, 7)}</td><td>{commitTag(c.message)}</td><td className="sub">{c.author}</td><td className="mono sub">{clock(c.at, false)}</td></tr>)}
                {!hist.length && <tr><td colSpan={4} className="empty">No commit touched this file yet.</td></tr>}
              </tbody>
            </table></div>
          )}
        </Panel>
      )}
    </div>
  );
}

function commitTag(msg: string) {
  const m = msg.match(/^(\w+)(\([^)]*\))?:?/);
  if (!m) return <>{msg}</>;
  const kind = m[1];
  return <><span className={`tag ${kind === "feat" ? "keel" : ""}`}>{m[1]}{m[2] ?? ""}</span>{msg.slice(m[0].length)}</>;
}

function BranchTab({ pid, r }: { pid: string; r: RepoInfo }) {
  const commits = useLoad(`commits:${pid}`, () => api.commits(pid, 30));
  return (
    <div className="grid g2">
      <Panel title={`Commits on ${r.branch}`} extra={<span className="hint">keel commits: test(AC) holds only tests, feat(AC) only code</span>} body={false}>
        <Async r={commits} what="Reading commits">
          {(list) => (
            <div className="table-wrap"><table>
              <thead><tr><th>Commit</th><th>Message</th><th>By</th><th>When</th></tr></thead>
              <tbody>
                {list.map((c) => (
                  <tr key={c.sha}><td className="mono sub">{c.sha.slice(0, 7)}</td><td>{commitTag(c.message)}</td><td className="sub">{c.author}</td><td className="mono sub">{clock(c.at, false)}</td></tr>
                ))}
                {!list.length && <tr><td colSpan={4} className="empty">No commits on this branch yet.</td></tr>}
              </tbody>
            </table></div>
          )}
        </Async>
      </Panel>
      <div className="grid" style={{ alignContent: "start" }}>
        <Panel title="Worktrees (lanes)" body="grid">
          <div className="grid" style={{ gap: 8 }}>
            {r.worktrees.length ? r.worktrees.map((w) => (
              <div key={w.path} className="grid" style={{ gap: 1 }}><b className="mono">{w.branch}</b><span className="sub mono">{w.path}</span></div>
            )) : <span className="sub">Only the main worktree.</span>}
          </div>
        </Panel>
        <Panel title="Branches" body="grid">
          <div className="grid" style={{ gap: 6 }}>
            {r.branches.map((b) => <div key={b.name} className="row" style={{ justifyContent: "space-between" }}><span className="mono">{b.name}</span><span className="sub">{b.note}</span></div>)}
          </div>
        </Panel>
      </div>
    </div>
  );
}

const DOC_PILL: Record<string, [PillTone, string]> = { ok: ["ok", "ok"], live: ["run", "live"], check: ["warn", "check"] };

function DocsTab({ pid }: { pid: string }) {
  const { project } = useApp();
  const docs = useLoad(`keeldocs:${pid}`, () => api.keelDocs(pid));
  return (
    <>
      <Panel title={`Files keel wrote in ${project?.name ?? pid}`} extra={<span className="hint">every change is a commit you can review</span>} body={false}>
        <Async r={docs} what="Reading keel files">
          {(list) => (
            <div className="table-wrap"><table>
              <thead><tr><th>File</th><th>What</th><th>Written by</th><th>Updated</th><th></th></tr></thead>
              <tbody>
                {list.map((d) => (
                  <tr key={d.path}><td className="mono">{d.path}</td><td>{d.what}</td><td className="sub">{d.by}</td><td className="sub">{d.updated}</td>
                    <td><Pill tone={DOC_PILL[d.status]?.[0] ?? "idle"}>{DOC_PILL[d.status]?.[1] ?? d.status}</Pill></td></tr>
                ))}
                {!list.length && <tr><td colSpan={5} className="empty">keel has not written any file here yet.</td></tr>}
              </tbody>
            </table></div>
          )}
        </Async>
      </Panel>
      <p className="hint">Knowledge sections are readable in the <a href="#/wiki">Wiki</a>. A flow's state and events live in keel's data, not in <span className="mono">.keel/</span>.</p>
    </>
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

function MemoryTab({ pid }: { pid: string }) {
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
    <Async r={mem} what="Reading memory">
      {(m) => {
        const chars = m.facts.reduce((a, f) => a + f.text.length + f.title.length, 0);
        return (
          <div className="grid g2">
            <Panel title={`What agents remember about ${project?.name ?? pid}`} extra={<button className="btn sm" type="button" onClick={() => setEdit("new")}>Add</button>} body="grid">
              <div className="grid" style={{ gap: 8 }}>
                {!m.facts.length && <span className="sub">Nothing yet. Agents and you add facts here as the project goes.</span>}
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
                  {!m.knowledge.length && <span className="sub">No knowledge base yet. The init flow writes it.</span>}
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
                <span>Facts sent with each agent step</span><b className="num">≈ {Math.round(chars / 4)} tokens</b>
                <span>Knowledge section (one, when asked)</span><b className="num">≈ {Math.round((m.knowledge.reduce((a, k) => a + k.words, 0) / Math.max(1, m.knowledge.length)) * 1.3 / 100) / 10}k tokens</b>
                <span>How facts are chosen</span><b>by phase and agent</b>
              </Panel>
            </div>
            {edit && <FactDrawer pid={pid} fact={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => void mem.reload()} />}
          </div>
        );
      }}
    </Async>
  );
}
