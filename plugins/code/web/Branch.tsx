// v0.12.0 Code › Source control › a branch (the Git plugin): what the branch has that the base does not. Its changed
// files since it left the base (a click shows the file's diff, base...branch) and its commits (each opens its files),
// with Switch to it (keel's switch: git refuses when uncommitted changes would be lost) and Ask KeelBot to review it.

import { useState } from "react";
import { askAssistant, ErrorBox, errorParts, Pill, useApp, useLoad } from "@keel/web-sdk";
import { codeApi as api } from "./codeApi";
import { DiffPane, type DiffMode } from "./Editor";
import { FileIcon, Icon } from "./icons";
import { nameOf, parentOf } from "./model";
import { CommitRow } from "./Scm";

const tone = (s: string) => (s === "A" ? "add" : s === "D" ? "del" : "mod");

export function BranchTab({ pid, name, mode, onOpenCommitFile }: {
  pid: string; name: string; mode: DiffMode;
  onOpenCommitFile: (sha: string, path: string, pin: boolean) => void;
}) {
  const b = useLoad(`branch:${pid}:${name}`, () => api.gitBranch(pid, name));
  const { toast } = useApp();
  const [file, setFile] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  if (b.error) return <div className="ed-note"><ErrorBox error={b.error} onRetry={() => void b.reload()} /></div>;
  const v = b.data;
  if (!v) return <div className="ed-note"><span className="pg-spin" role="status">Reading {name}…</span></div>;
  const isBase = !v.base || v.base === v.name;
  const shown = file && v.files.some((f) => f.path === file) ? file : v.files[0]?.path ?? null;
  const switchTo = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.gitSwitch(pid, v.name);
      toast(`On ${r.branch} now.`);
      void b.reload();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="br" role="region" aria-label={`Branch ${v.name}`}>
      <div className="br-head">
        <Icon name="branch" size={16} />
        <b className="mono">{v.name}</b>
        {v.current && <Pill tone="ok">current</Pill>}
        {isBase
          ? <span className="sub">the base branch</span>
          : <span className="sub">{v.ahead} commit{v.ahead === 1 ? "" : "s"} not in <span className="mono">{v.base}</span>{v.behind > 0 ? ` · ${v.behind} behind it` : ""}</span>}
        <span className="br-acts">
          {!v.current && (
            <button type="button" className="btn sm primary" disabled={busy} onClick={() => void switchTo()}>
              {busy ? "Switching…" : `Switch to ${v.name}`}
            </button>
          )}
          {!isBase && v.files.length > 0 && (
            <button type="button" className="btn sm ghost"
              onClick={() => askAssistant(`Review the branch ${v.name} against ${v.base} (git diff ${v.base}...${v.name}): what it changes, and anything that looks wrong, with file:line.`)}>
              Ask KeelBot to review it
            </button>
          )}
        </span>
      </div>
      {err && <div className="br-err"><ErrorBox error={err} /></div>}
      <div className="br-body">
        <div className="br-side">
          {!isBase && (
            <section aria-label="Changed files">
              <h3 className="br-h">Changed files<span className="sr-count">{v.files.length}</span></h3>
              {v.files.map((f) => (
                <button key={f.path} type="button" className={`scm-row t-${tone(f.status)}`} aria-pressed={shown === f.path}
                  onClick={() => setFile(f.path)} title={f.from ? `${f.path} (from ${f.from})` : f.path} aria-label={`Show the changes of ${f.path} on ${v.name}`}>
                  <FileIcon name={nameOf(f.path)} />
                  <span className={`ex-name${f.status === "D" ? " gone" : ""}`}>{nameOf(f.path)}</span>
                  <span className="sr-dir">{parentOf(f.path)}</span>
                  <span className={`ex-deco t-${tone(f.status)}`}>{f.status}</span>
                </button>
              ))}
              {!v.files.length && <span className="sub">No file differs from {v.base}.</span>}
              {v.truncated && <span className="sub">The first 1000 files.</span>}
            </section>
          )}
          <section aria-label="Commits">
            <h3 className="br-h">{isBase ? "Recent commits" : `Commits not in ${v.base}`}<span className="sr-count">{v.commits.length}</span></h3>
            {v.commits.map((c) => <CommitRow key={c.sha} pid={pid} c={c} onOpen={onOpenCommitFile} />)}
            {!v.commits.length && <span className="sub">None: {v.base} has all of {v.name}.</span>}
          </section>
        </div>
        <div className="br-diff">
          {shown
            ? <DiffPane key={shown} pid={pid} path={shown} against="base" branch={v.name} mode={mode} />
            : <div className="ed-note">{isBase ? "Open another branch to see what it changes against this one." : `${v.name} changes no file against ${v.base}.`}</div>}
        </div>
      </div>
    </div>
  );
}
