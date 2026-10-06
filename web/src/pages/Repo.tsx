// Repo (Project): a small, read-only VS Code for the project — files with git and keel marks, editor tabs,
// search, source control, and keel's own pages (the IDE is in ./repo/). The head keeps the code graph index and
// "Update from <base>", the one action here that changes the repo (a git merge that stops on a conflict).

import { useEffect, useState } from "react";
import { api, errorParts, type IndexStatus, type RepoInfo, type UpdateFromBase } from "../api";
import { ErrorBox, PageHead } from "../components/ui";
import { plural } from "../format";
import { go, useApp, useLoad } from "../state";
import { RepoIde } from "./repo/Ide";

export function RepoPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const repo = useLoad(`repo:${pid}`, () => api.repo(pid));
  const [result, setResult] = useState<UpdateFromBase | { error: { message: string; hint?: string } } | null>(null);
  /** Bumped after a merge brought new files: the explorer reads the tree again. */
  const [version, setVersion] = useState(0);
  // Focus: the page head (title, the index row, Update from base) goes, so the code and the Helper get the room
  const [focus, setFocus] = useState<boolean>(() => {
    try { return localStorage.getItem("keel2.repo.focus") === "1"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("keel2.repo.focus", focus ? "1" : "0"); } catch { /* private window */ }
  }, [focus]);
  return (
    <div className={`repo-page${focus ? " focus" : ""}`}>
      {!focus && <PageHead title="Repo" sub={<>Read, search and check the code of {project?.name ?? pid}. Read-only: keel never edits files here.</>}
        actions={<>
          <IndexBadge pid={pid} />
          {repo.data && repo.data.base && repo.data.branch && repo.data.base !== repo.data.branch && (
            <UpdateFromBaseButton pid={pid} r={repo.data} onResult={(x) => {
              setResult(x);
              void repo.reload();
              if ("merged" in x && x.merged) setVersion((v) => v + 1);
            }} />
          )}
          <button className="btn sm" type="button" onClick={() => go("helper")} title="Only the Helper, on a page of its own">Helper only</button>
          <button className="btn sm ghost" type="button" onClick={() => setFocus(true)} title="Hide this header: the code and the Helper get the room">Focus</button>
        </>} />}
      {repo.error && <div style={{ marginBottom: 12 }}><ErrorBox error={repo.error} onRetry={() => void repo.reload()} /></div>}
      {result && <UpdateResult result={result} base={repo.data?.base ?? "base"} onClose={() => setResult(null)} />}
      <RepoIde pid={pid} repo={repo.data} version={version} focus={focus} onFocus={setFocus} />
    </div>
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
    <span className="row rp-index-row" style={{ gap: 8 }} aria-label="Code graph index">
      <span className={`tag rp-index ${i.status === "failed" ? "star" : ""}`} title={i.error ?? "The code graph agents use before grep"}>{text}</span>
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
    <button className={`btn sm ${r.behind ? "primary" : ""}`} type="button" onClick={run} disabled={busy}
      title={`git merge ${r.base} into ${r.branch}; stops and changes nothing if files conflict`}>
      {busy ? "Updating…" : `Update from ${r.base}`}
    </button>
  );
}

function UpdateResult({ result, base, onClose }: { result: UpdateFromBase | { error: { message: string; hint?: string } }; base: string; onClose: () => void }) {
  const close = <button className="btn sm ghost" type="button" onClick={onClose}>Close</button>;
  if ("error" in result) {
    return <div style={{ marginBottom: 12 }}><ErrorBox error={result.error} /></div>;
  }
  const out = result.output?.trim();
  const details = out ? <details><summary className="sub">git output</summary><pre className="outbox mono">{out}</pre></details> : null;
  if (result.conflicts?.length) {
    return (
      <div className="errbox" role="alert" style={{ marginBottom: 12 }}>
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
      <div className="errbox" role="alert" style={{ marginBottom: 12 }}>
        <b>The update did not work.</b>
        {details ?? <span className="sub">git gave no output.</span>}
        <div>{close}</div>
      </div>
    );
  }
  return (
    <div className="okbox" role="status" style={{ marginBottom: 12 }}>
      <b>{result.merged ? `Updated: ${base} is merged into this branch.` : `Already up to date with ${base}.`}</b>
      {details}
      <div>{close}</div>
    </div>
  );
}
