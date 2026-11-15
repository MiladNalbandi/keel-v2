// Repo (Project): a small, read-only VS Code for the project — files with git and keel marks, editor tabs,
// search, source control, and keel's own pages (the IDE is in ./Ide.tsx). The head keeps "Update from <base>", the one
// action here that changes the repo (a git merge that stops on a conflict). v0.17.1: the code graph index, its Rebuild
// and Focus are in the status bar (./IndexStatus.tsx, ./Ide.tsx); KeelBot's own page opens from its panel.

import { useEffect, useState } from "react";
import { ErrorBox, errorParts, isMac, keyLabel, PageHead, SLOTS, useApp, useLoad, useSlot, type AssistantItem } from "@keel/web-sdk";
import { codeApi as api, type RepoInfo, type UpdateFromBase } from "./codeApi";
import { RepoIde } from "./Ide";
import { FOCUS_EVENT } from "./model";

export function RepoPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const repo = useLoad(`repo:${pid}`, () => api.repo(pid));
  const [result, setResult] = useState<UpdateFromBase | { error: { message: string; hint?: string } } | null>(null);
  /** Bumped after a merge brought new files: the explorer reads the tree again. */
  const [version, setVersion] = useState(0);
  // 0.28 the head's Go to file (⌘P) and Ask (⌘I): each click is a new number the IDE answers
  const [qoKey, setQoKey] = useState(0);
  const [askKey, setAskKey] = useState(0);
  const assistant = useSlot<AssistantItem>(SLOTS.assistant)[0] ?? null;
  // Focus: the page head (title, Update from base) goes, so the code and KeelBot get the room
  const [focus, setFocus] = useState<boolean>(() => {
    try { return localStorage.getItem("keel2.repo.focus") === "1"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("keel2.repo.focus", focus ? "1" : "0"); } catch { /* private window */ }
  }, [focus]);
  // v0.15.x Focus mode is the whole window, like an IDE's Zen mode: keel's menu and the usage bar go too (the shell
  // hides them while <html data-focus> is set); ⇧⌘\ turns it on and off, Esc twice leaves it (as in VS Code)
  useEffect(() => {
    const root = document.documentElement;
    if (focus) root.dataset.focus = "code";
    else delete root.dataset.focus;
    return () => { delete root.dataset.focus; };
  }, [focus]);
  useEffect(() => {
    let lastEsc = 0;
    const onKey = (e: KeyboardEvent) => {
      if ((isMac ? e.metaKey : e.ctrlKey) && e.shiftKey && !e.altKey && (e.code === "Backslash" || e.key === "|" || e.key === "\\")) {
        e.preventDefault();
        setFocus((f) => !f);
        return;
      }
      // only an Esc nothing else used (a popup or the find bar closing keeps Focus); v0.15.4 ⇧Esc hides a tool window instead
      if (e.key !== "Escape" || e.shiftKey || e.defaultPrevented) { lastEsc = 0; return; }
      const now = Date.now();
      if (now - lastEsc < 450) { lastEsc = 0; setFocus(false); } else lastEsc = now;
    };
    const onToggle = (e: Event) => {
      const want = (e as CustomEvent<boolean | undefined>).detail;
      setFocus((f) => (typeof want === "boolean" ? want : !f));
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener(FOCUS_EVENT, onToggle);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener(FOCUS_EVENT, onToggle); };
  }, []);
  return (
    <div className={`repo-page${focus ? " focus" : ""}`}>
      {!focus && <PageHead title="Code" sub={<>Read, search and check the code of {project?.name ?? pid}. Read-only: keel never edits files here.</>}
        actions={<>
          {repo.data && repo.data.base && repo.data.branch && repo.data.base !== repo.data.branch && (
            <UpdateFromBaseButton pid={pid} r={repo.data} onResult={(x) => {
              setResult(x);
              void repo.reload();
              if ("merged" in x && x.merged) setVersion((v) => v + 1);
            }} />
          )}
          <button className="btn sm" type="button" onClick={() => setQoKey((n) => n + 1)} title="Find a file by name; type : and a number to go to a line">
            Go to file <kbd>{keyLabel("meta+p")}</kbd>
          </button>
          {assistant && (
            // a press keeps the selected lines: they go along with the question
            <button className="btn sm primary" type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => setAskKey((n) => n + 1)}
              title={`Ask ${assistant.title} about the selected lines, or this file`}>
              Ask {assistant.title} <kbd>{keyLabel("meta+i")}</kbd>
            </button>
          )}
        </>} />}
      {repo.error && <div style={{ marginBottom: 12 }}><ErrorBox error={repo.error} onRetry={() => void repo.reload()} /></div>}
      {result && <UpdateResult result={result} base={repo.data?.base ?? "base"} onClose={() => setResult(null)} />}
      <RepoIde pid={pid} repo={repo.data} version={version} focus={focus} onFocus={setFocus} onRepoChanged={() => void repo.reload()}
        quickOpenKey={qoKey} askKey={askKey} />
    </div>
  );
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
