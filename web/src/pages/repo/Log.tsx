// v0.15.2 Code › Source control › Log, like JetBrains' Git › Log: the branches on the left (local, remote, tags; the
// current one marked), the commits of the chosen branch (or all) with a graph, refs, author, date and id, filters for
// author, text (or a commit id) and path, "Uncommitted changes" on top, and which commits are only on this branch.
// A commit shows its files and the diff of one; a double click opens the file's change as a commit tab. It only reads.
// Why an editor tab and not a part of the Git side bar: the log needs width (the graph and four columns, the files and
// a diff), and a tab keeps it open next to the files, like the branch tab.

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { api, type Change } from "../../api";
import { useNarrow } from "../../components/page";
import { ErrorBox } from "../../components/ui";
import { gitLogApi, type GitLog, type LogCommit, type RefItem } from "../../gitLogApi";
import { useLoad } from "../../state";
import { DiffPane, type DiffMode } from "./Editor";
import { ALL, authorsOf, byRemote, graphRows, sortRefs, when, type GraphRow } from "./gitLog";
import { Chevron, FileIcon, Icon } from "./icons";
import { acOf, decoOf, nameOf, parentOf } from "./model";

const PAGE = 100;
const MAX = 1000;
const LANE = 12;
const ROW = 24;
const UNCOMMITTED = "uncommitted";
/** ref badges on a row; more are "+N" (the names in its tooltip) */
const BADGES = 3;
const NO_FILTER = { author: "", q: "", path: "" };

type Kept = { filter: typeof NO_FILTER; sel: string | null };
/** The filters and the chosen commit, per project for this browser tab: they stay when another tab was in front. */
const keptKey = (pid: string) => `keel2.repo.log.${pid}`;
function readKept(pid: string): Kept | null {
  try {
    const v = sessionStorage.getItem(keptKey(pid));
    return v ? (JSON.parse(v) as Kept) : null;
  } catch {
    return null;
  }
}
function writeKept(pid: string, k: Kept) {
  try {
    sessionStorage.setItem(keptKey(pid), JSON.stringify(k));
  } catch {
    /* private mode or full */
  }
}

type Props = {
  pid: string;
  /** "" = the current branch, "*" = all branches, else a branch, remote branch or tag */
  branch: string;
  /** the work tree's uncommitted files (the Code page reads them) */
  changes: Change[] | null;
  mode: DiffMode;
  onBranch: (branch: string) => void;
  onOpenCommitFile: (sha: string, path: string, pin: boolean) => void;
  onOpenChange: (path: string, pin: boolean) => void;
};

const tone = (s: string) => (s === "A" ? "add" : s === "D" ? "del" : s === "R" || s === "C" ? "ren" : "mod");

function Graph({ row, width, head }: { row: GraphRow | null; width: number; head?: "dashed" }) {
  const x = (lane: number) => 7 + lane * LANE;
  const y = [0, ROW / 2, ROW];
  return (
    <svg className="lg-graph" width={width} height={ROW} viewBox={`0 0 ${width} ${ROW}`} aria-hidden="true">
      {row?.segs.map((s, i) => {
        const [x1, x2, y1, y2] = [x(s.x1), x(s.x2), y[s.y1], y[s.y2]];
        const mid = (y1 + y2) / 2;
        return (
          <path key={i} className={`lg-c${s.color}`} d={x1 === x2 ? `M${x1} ${y1}V${y2}` : `M${x1} ${y1}C${x1} ${mid} ${x2} ${mid} ${x2} ${y2}`} />
        );
      })}
      {head === "dashed" && <path className="lg-c0 lg-dash" d={`M${x(0)} ${y[1]}V${y[2]}`} />}
      {row ? <circle className={`lg-dot lg-c${row.color}`} cx={x(row.lane)} cy={y[1]} r={3.6} /> : <circle className="lg-dot lg-open" cx={x(0)} cy={y[1]} r={3.6} />}
    </svg>
  );
}

function RefGroup({ title, children, start = true }: { title: string; children: ReactNode; start?: boolean }) {
  const [open, setOpen] = useState(start);
  return (
    <section className="lg-rg">
      <button type="button" className="lg-rgh" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="ex-tw"><Chevron open={open} /></span>
        {title}
      </button>
      {open && children}
    </section>
  );
}

function RefButton({ value, label, on, onPick, title, children }: {
  value: string; label: string; on: boolean; onPick: (v: string) => void; title?: string; children?: ReactNode;
}) {
  return (
    <button type="button" className="lg-rb" aria-pressed={on} onClick={() => onPick(value)} title={title ?? value} aria-label={`Show the log of ${label}`}>
      <span className="lg-rn">{label}</span>
      {children}
    </button>
  );
}

function BranchList({ refs, branch, onPick }: { refs: { head: string | null; base: string | null; local: RefItem[]; remote: RefItem[]; tags: RefItem[] }; branch: string; onPick: (v: string) => void }) {
  return (
    <nav className="lg-refs" aria-label="Branches">
      <RefButton value={ALL} label="all branches" on={branch === ALL} onPick={onPick} title="Every branch, remote branch and tag" />
      <RefButton value="" label="HEAD" on={branch === ""} onPick={onPick} title="The branch the project folder is on">
        <span className="lg-rm">{refs.head ? `current: ${refs.head}` : "detached"}</span>
      </RefButton>
      <RefGroup title={`Local (${refs.local.length})`}>
        {refs.local.map((b) => (
          <RefButton key={b.name} value={b.name} label={b.name} on={branch === b.name} onPick={onPick}
            title={`${b.name}${b.current ? " (current)" : ""}: ${b.subject}`}>
            {b.current && <span className="lg-cur" title="The project folder is on this branch">current</span>}
            {b.name === refs.base && <span className="lg-rm">base</span>}
            {(b.ahead ?? 0) > 0 && <span className="lg-rm" title={`${b.ahead} commits not in ${refs.base}`}>↑{b.ahead}</span>}
            {(b.behind ?? 0) > 0 && <span className="lg-rm" title={`${b.behind} commits of ${refs.base} it does not have`}>↓{b.behind}</span>}
          </RefButton>
        ))}
      </RefGroup>
      {refs.remote.length > 0 && (
        <RefGroup title={`Remote (${refs.remote.length})`}>
          {byRemote(refs.remote).map(([remote, list]) => (
            <div key={remote} className="lg-remote" role="group" aria-label={remote}>
              <span className="lg-rname">{remote}</span>
              {list.map((b) => <RefButton key={b.name} value={b.name} label={b.name} on={branch === b.name} onPick={onPick} title={`${b.name}: ${b.subject}`}><span className="lg-rshort" aria-hidden="true">{b.short}</span></RefButton>)}
            </div>
          ))}
        </RefGroup>
      )}
      {refs.tags.length > 0 && (
        <RefGroup title={`Tags (${refs.tags.length})`} start={false}>
          {refs.tags.map((t) => <RefButton key={t.name} value={t.name} label={t.name} on={branch === t.name} onPick={onPick} title={`${t.name}: ${t.subject}`} />)}
        </RefGroup>
      )}
    </nav>
  );
}

/** The chosen commit: its message, who and when, its files, and the diff of one file. */
function CommitDetail({ pid, c, base, shown, mode, onOpenCommitFile }: {
  pid: string; c: LogCommit; base: string | null; mode: DiffMode;
  /** the shown branch; null = all branches */
  shown: string | null;
  onOpenCommitFile: (sha: string, path: string, pin: boolean) => void;
}) {
  const view = useLoad(`commit:${pid}:${c.sha}`, () => api.commit(pid, c.sha), { live: false });
  const [file, setFile] = useState<string | null>(null);
  const files = view.data?.files ?? [];
  const pick = file && files.some((f) => f.path === file) ? file : files[0]?.path ?? null;
  return (
    <section className="lg-detail" aria-label={`Commit ${c.sha.slice(0, 7)}`}>
      <div className="lg-dside">
        <div className="lg-msg">
          <b>{c.subject}</b>
          {view.data?.body && <p className="lg-body">{view.data.body}</p>}
          <span className="lg-meta mono">
            {c.sha.slice(0, 10)} · {c.author} &lt;{c.email}&gt; · {new Date(c.at).toLocaleString()}
          </span>
          {base && (
            <span className={`lg-where ${c.in_base ? "in" : "out"}`}>
              {c.in_base ? `Already on ${base}` : shown ? `Only on ${shown}: ${base} does not have it yet` : `Not on ${base} yet`}
            </span>
          )}
        </div>
        <h3 className="br-h">Changed files{view.data && <span className="sr-count">{files.length}</span>}</h3>
        {view.error ? <ErrorBox error={view.error} onRetry={() => void view.reload()} />
          : !view.data ? <span className="pg-spin" role="status">Reading the commit…</span>
            : files.map((f) => (
              <button key={f.path} type="button" className={`scm-row t-${tone(f.status)}`} aria-pressed={pick === f.path}
                onClick={() => setFile(f.path)} onDoubleClick={() => onOpenCommitFile(c.sha, f.path, true)}
                title={`${f.from ? `${f.path} (from ${f.from})` : f.path} · double-click: open it in a tab`}
                aria-label={`Show the change of ${f.path} in ${c.sha.slice(0, 7)}`}>
                <FileIcon name={nameOf(f.path)} />
                <span className={`ex-name${f.status === "D" ? " gone" : ""}`}>{nameOf(f.path)}</span>
                <span className="sr-dir">{parentOf(f.path)}</span>
                <span className={`ex-deco t-${tone(f.status)}`}>{f.status}</span>
              </button>
            ))}
        {view.data && !files.length && <span className="sub lg-pad">This commit changed no file.</span>}
      </div>
      <div className="lg-diff">
        {pick ? <DiffPane key={`${c.sha}:${pick}`} pid={pid} path={pick} against="head" sha={c.sha} mode={mode} />
          : <div className="ed-note">{view.data ? "No file to show." : ""}</div>}
      </div>
    </section>
  );
}

/** The uncommitted files: each one's diff against HEAD; a double click opens the file's diff tab. */
function UncommittedDetail({ pid, changes, mode, onOpenChange }: { pid: string; changes: Change[]; mode: DiffMode; onOpenChange: (path: string, pin: boolean) => void }) {
  const [file, setFile] = useState<string | null>(null);
  const pick = file && changes.some((c) => c.path === file) ? file : changes[0]?.path ?? null;
  return (
    <section className="lg-detail" aria-label="Uncommitted changes">
      <div className="lg-dside">
        <div className="lg-msg">
          <b>Uncommitted changes</b>
          <span className="lg-meta">In the project folder, not in any commit yet. This view does not commit; Source control does.</span>
        </div>
        <h3 className="br-h">Changed files<span className="sr-count">{changes.length}</span></h3>
        {changes.map((c) => {
          const d = decoOf(c);
          return (
            <button key={c.path} type="button" className={`scm-row t-${d?.tone ?? "mod"}`} aria-pressed={pick === c.path}
              onClick={() => setFile(c.path)} onDoubleClick={() => onOpenChange(c.path, true)}
              title={`${c.path} · ${d?.title ?? ""} · double-click: open it in a tab`} aria-label={`Show the uncommitted change of ${c.path}`}>
              <FileIcon name={nameOf(c.path)} />
              <span className={`ex-name${d?.tone === "del" ? " gone" : ""}`}>{nameOf(c.path)}</span>
              <span className="sr-dir">{parentOf(c.path)}</span>
              <span className={`ex-deco t-${d?.tone ?? "mod"}`}>{d?.letter ?? "M"}</span>
            </button>
          );
        })}
      </div>
      <div className="lg-diff">
        {pick && <DiffPane key={`wt:${pick}`} pid={pid} path={pick} against="head" mode={mode} />}
      </div>
    </section>
  );
}

export function LogTab({ pid, branch, changes, mode, onBranch, onOpenCommitFile, onOpenChange }: Props) {
  const all = branch === ALL;
  const [kept] = useState(() => readKept(pid));
  const [draft, setDraft] = useState(kept?.filter ?? NO_FILTER);
  const [applied, setApplied] = useState(draft);
  useEffect(() => {
    const t = window.setTimeout(() => setApplied(draft), 350);
    return () => window.clearTimeout(t);
  }, [draft]);
  const sig = JSON.stringify([branch, applied.author.trim(), applied.q.trim(), applied.path.trim()]);
  const [more, setMore] = useState({ sig, n: PAGE });
  const limit = more.sig === sig ? more.n : PAGE;

  const refs = useLoad(`log-refs:${pid}`, () => gitLogApi.refs(pid));
  const log = useLoad(`log:${pid}:${sig}:${limit}`, () =>
    gitLogApi.log(pid, { branch: all ? undefined : branch, all, author: applied.author, q: applied.q, path: applied.path, limit }));
  // more commits (a bigger limit) keep the ones on screen until they come
  const last = useRef<{ sig: string; data: GitLog } | null>(null);
  if (log.data) last.current = { sig, data: log.data };
  const data = log.data ?? (last.current?.sig === sig ? last.current.data : null);

  const [sel, setSel] = useState<string | null>(kept?.sel ?? null);
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => writeKept(pid, { filter: applied, sel }), [pid, applied, sel]);
  // back on this tab: the chosen commit in view again
  const seen = !!data;
  useEffect(() => {
    if (seen && sel) listRef.current?.querySelector<HTMLElement>(`[data-id="${sel}"]`)?.scrollIntoView?.({ block: "nearest" });
    // only when the list first shows
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seen]);
  const filtered = !!(applied.author.trim() || applied.q.trim());
  const commits = data?.commits ?? [];
  const rows = useMemo(() => graphRows(filtered ? commits.map((c) => ({ sha: c.sha, parents: [] })) : commits), [commits, filtered]);
  // a phone shows fewer lanes (the rest is cut at the column's edge)
  const narrow = useNarrow(720);
  const lanes = Math.min(narrow ? 4 : 10, Math.max(1, ...rows.map((r) => r.width)));
  const gw = 14 + (lanes - 1) * LANE;
  const authors = useMemo(() => authorsOf(commits), [commits]);
  const dirty = changes?.length ?? 0;
  const shown = all ? "all branches" : data?.branch ?? (branch || refs.data?.head || "HEAD");
  const onHead = !all && (branch === "" || (!!data?.head && branch === data.head));
  const showDirty = dirty > 0 && (onHead || all) && !filtered && !applied.path.trim();
  const ids = [...(showDirty ? [UNCOMMITTED] : []), ...commits.map((c) => c.sha)];
  const selected = sel === UNCOMMITTED ? (showDirty ? UNCOMMITTED : null) : commits.find((c) => c.sha === sel) ?? null;
  const base = data?.base ?? null;
  const onBase = !all && !!base && (data?.branch === base || (branch === "" && data?.head === base));

  const move = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const at = sel ? ids.indexOf(sel) : -1;
    const next = ids[Math.min(ids.length - 1, Math.max(0, at + (e.key === "ArrowDown" ? 1 : -1)))];
    if (!next) return;
    setSel(next);
    listRef.current?.querySelector<HTMLElement>(`[data-id="${next}"]`)?.scrollIntoView?.({ block: "nearest" });
  };

  let summary: ReactNode = null;
  if (data && base) {
    summary = all ? <span>Marked: commits that <span className="mono">{base}</span> does not have</span>
      : onBase ? <span><span className="mono">{base}</span> is the base branch</span>
        : (
          <span>
            <b>{data.ahead}</b> commit{data.ahead === 1 ? "" : "s"} only on <span className="mono">{shown}</span>
            {data.behind > 0 && <> · {data.behind} behind <span className="mono">{base}</span></>}
          </span>
        );
  }

  return (
    <div className="lg" role="region" aria-label="Git log">
      <form className="lg-bar" role="search" aria-label="Log filters" onSubmit={(e) => { e.preventDefault(); setApplied(draft); }}>
        <select aria-label="Branch" value={branch} onChange={(e) => onBranch(e.target.value)}>
          <option value={ALL}>All branches</option>
          <option value="">HEAD{refs.data?.head ? ` (${refs.data.head})` : ""}</option>
          {refs.data && refs.data.local.length > 0 && <optgroup label="Local">{refs.data.local.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}</optgroup>}
          {refs.data && refs.data.remote.length > 0 && <optgroup label="Remote">{refs.data.remote.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}</optgroup>}
          {refs.data && refs.data.tags.length > 0 && <optgroup label="Tags">{refs.data.tags.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}</optgroup>}
          {branch && branch !== ALL && refs.data && ![...refs.data.local, ...refs.data.remote, ...refs.data.tags].some((b) => b.name === branch) && <option value={branch}>{branch}</option>}
        </select>
        <input className="lg-in" aria-label="Author" placeholder="Author" list={`lg-authors-${pid}`} value={draft.author} onChange={(e) => setDraft({ ...draft, author: e.target.value })} />
        <datalist id={`lg-authors-${pid}`}>{authors.map((a) => <option key={a} value={a} />)}</datalist>
        <input className="lg-in lg-q" type="search" aria-label="Text or commit id" placeholder="Text or commit id" value={draft.q} onChange={(e) => setDraft({ ...draft, q: e.target.value })} />
        <input className="lg-in mono" aria-label="Path" placeholder="Path: a file or folder" value={draft.path} onChange={(e) => setDraft({ ...draft, path: e.target.value })} />
        {(draft.author || draft.q || draft.path) && (
          <button type="button" className="btn sm ghost" onClick={() => { setDraft(NO_FILTER); setApplied(NO_FILTER); }}>Clear</button>
        )}
        <button type="button" className="ib" aria-label="Read the log again" title="Read the log again" onClick={() => { void log.reload(); void refs.reload(); }}>
          <Icon name="refresh" size={15} />
        </button>
        <span className="lg-sum" aria-live="polite">{summary}</span>
      </form>
      <div className="lg-main">
        {refs.error ? <div className="lg-refs"><ErrorBox error={refs.error} /></div>
          : refs.data ? <BranchList refs={refs.data} branch={branch} onPick={onBranch} />
            : <div className="lg-refs"><span className="pg-spin" role="status">Reading branches…</span></div>}
        <div className={`lg-center${selected ? " with-detail" : ""}`}>
          <div className="lg-head" aria-hidden="true" style={{ gridTemplateColumns: `${gw}px minmax(0, 1fr) var(--lg-who) var(--lg-when) var(--lg-sha)` }}>
            <span /><span>Subject</span><span>Author</span><span>Date</span><span>Commit</span>
          </div>
          <div ref={listRef} className="lg-list" role="listbox" aria-label="Commits" tabIndex={0} onKeyDown={move}
            aria-activedescendant={sel && ids.includes(sel) ? `lg-${pid}-${sel}` : undefined}>
            {log.error ? <div className="lg-pad"><ErrorBox error={log.error} onRetry={() => void log.reload()} /></div>
              : !data ? <span className="pg-spin lg-pad" role="status">Reading the log…</span>
                : (
                  <>
                    {showDirty && (
                      <div id={`lg-${pid}-${UNCOMMITTED}`} data-id={UNCOMMITTED} role="option" aria-selected={sel === UNCOMMITTED} className="lg-row lg-wt"
                        style={{ gridTemplateColumns: `${gw}px minmax(0, 1fr)` }} onClick={() => setSel(UNCOMMITTED)}>
                        <Graph row={null} width={gw} head={rows[0]?.lane === 0 && commits[0]?.refs.some((r) => r.current || r.kind === "head") ? "dashed" : undefined} />
                        <span className="lg-subj">Uncommitted changes ({dirty} file{dirty === 1 ? "" : "s"})</span>
                      </div>
                    )}
                    {commits.map((c, i) => {
                      const ac = acOf(c.subject);
                      const badges = sortRefs(c.refs);
                      const cut = !!base && !all && c.in_base && i > 0 && !commits[i - 1].in_base;
                      const where = !base ? "" : c.in_base ? `already on ${base}` : all ? `not on ${base} yet` : `only on ${shown}`;
                      return (
                        <div key={c.sha} id={`lg-${pid}-${c.sha}`} data-id={c.sha} role="option" aria-selected={sel === c.sha}
                          aria-label={`${c.subject}, ${c.author}, ${c.sha.slice(0, 7)}${where ? `, ${where}` : ""}`}
                          className={`lg-row${base ? (c.in_base ? " in" : " out") : ""}${cut ? " cut" : ""}`}
                          style={{ gridTemplateColumns: `${gw}px minmax(0, 1fr) var(--lg-who) var(--lg-when) var(--lg-sha)` }}
                          title={where ? `${c.subject}\n${where}` : c.subject} onClick={() => setSel(c.sha)}>
                          <Graph row={rows[i]} width={gw} />
                          <span className="lg-subj">
                            {badges.slice(0, BADGES).map((r) => (
                              <span key={`${r.kind}:${r.name}`} className={`lg-ref k-${r.kind}${r.current ? " cur" : ""}`} title={r.kind === "remote" ? `remote branch ${r.name}` : r.kind === "tag" ? `tag ${r.name}` : r.current ? `${r.name} (current branch)` : r.kind === "head" ? "HEAD (detached)" : `branch ${r.name}`}>
                                {r.name}
                              </span>
                            ))}
                            {badges.length > BADGES && <span className="lg-ref k-more" title={badges.slice(BADGES).map((r) => r.name).join("\n")}>+{badges.length - BADGES}</span>}
                            {c.keel && <span className="scm-keel" title="A keel commit">keel</span>}
                            {ac && <span className="scm-ac">{ac}</span>}
                            <span className="lg-s">{c.subject}</span>
                          </span>
                          <span className="lg-who" title={`${c.author} <${c.email}>`}>{c.author}</span>
                          <span className="lg-when" title={new Date(c.at).toLocaleString()}>{when(c.at)}</span>
                          <span className="lg-sha mono">{c.sha.slice(0, 7)}</span>
                        </div>
                      );
                    })}
                    {!commits.length && (
                      <p className="sub lg-pad">{filtered || applied.path.trim() ? "No commit matches these filters." : "No commits yet."}</p>
                    )}
                    {data.has_more && (limit < MAX ? (
                      <button type="button" className="btn sm lg-more" disabled={log.loading} onClick={() => setMore({ sig, n: limit + PAGE })}>
                        {log.loading ? "Reading…" : `Show ${PAGE} more`}
                      </button>
                    ) : <p className="sub lg-pad">The first {MAX} commits. The filters find older ones.</p>)}
                  </>
                )}
          </div>
          {selected === UNCOMMITTED && changes ? <UncommittedDetail pid={pid} changes={changes} mode={mode} onOpenChange={onOpenChange} />
            : selected && typeof selected === "object" ? <CommitDetail key={selected.sha} pid={pid} c={selected} base={base} shown={all ? null : shown} mode={mode} onOpenCommitFile={onOpenCommitFile} />
              : null}
        </div>
      </div>
    </div>
  );
}
