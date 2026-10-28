// keel 0.18.65 the project's base branch and its canonical remote (the stale-base field report, R2 and R5). The start
// dialog asks when base_branch names a branch this repo has no local copy of (use the remote's, a local branch that
// tracks it, or another branch), and Settings › General › Base branch shows what keel compares with and lets the person
// change it. keel writes .keel/config.yml (PUT /projects/{pid}/base); an agent never does.

import { useEffect, useState } from "react";
import { api, errorParts, type BaseMissing, type BaseView } from "../api";
import { ErrorBox } from "./ui";

/** base_branch names no local branch: the person's choice, one click each (the start dialog, Settings). */
export function BaseQuestion({ pid, missing, onDone }: { pid: string; missing: BaseMissing; onDone: (v: BaseView) => void }) {
  const pickable = missing.choices.find((c) => c.id === "pick")?.branches ?? missing.branches;
  const [pick, setPick] = useState(pickable[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const choose = (choice: "remote" | "track" | "pick", branch?: string) => {
    setBusy(true);
    setErr(null);
    api.chooseBase(pid, { choice, ...(branch ? { branch } : {}) })
      .then(onDone)
      .catch(setErr)
      .finally(() => setBusy(false));
  };
  return (
    <div className="hint amber base-q" role="group" aria-label="Which base branch?" data-testid="base-missing">
      <span>{missing.text} Which base should keel use?</span>
      <span className="base-q-row">
        {missing.choices.filter((c) => c.id !== "pick").map((c) => (
          <button key={c.id} type="button" className="btn sm" disabled={busy} title={c.about} onClick={() => choose(c.id, c.ref)}>
            {c.label}
          </button>
        ))}
        {pickable.length > 0 && (
          <>
            <select aria-label="Another base branch" value={pick} onChange={(e) => setPick(e.target.value)} disabled={busy}>
              {pickable.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
            <button type="button" className="btn sm" disabled={busy || !pick} onClick={() => choose("pick", pick)}>
              Use {pick || "this branch"}
            </button>
          </>
        )}
      </span>
      {err != null && <ErrorBox error={errorParts(err)} />}
    </div>
  );
}

/** Settings › General › Base branch (a project's own): what keel compares with, the drift, the canonical remote. */
export function BaseBranchPanel({ pid }: { pid: string }) {
  const [view, setView] = useState<BaseView | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let gone = false;
    setView(null);
    if (!pid) return;
    api.base(pid).then((v) => { if (!gone) setView(v); }).catch((e) => { if (!gone) setErr(e); });
    return () => { gone = true; };
  }, [pid]);
  if (!pid) return null;
  if (err && !view) return <ErrorBox error={errorParts(err)} />;
  if (!view) return <span className="ss-meta">Reading the base…</span>;
  const change = (body: Parameters<typeof api.chooseBase>[1]) => {
    setBusy(true);
    setErr(null);
    api.chooseBase(pid, body).then(setView).catch(setErr).finally(() => setBusy(false));
  };
  const remoteBranches = view.remote ? [view.remote] : [];
  const choices = [...new Set([...(view.configured ? [view.configured] : []), ...view.branches, ...remoteBranches])];
  return (
    <div className="base-panel" data-testid="base-panel">
      <dl className="ss-dl">
        <dt>keel compares with</dt>
        <dd className="ss-mono" data-testid="base-ref">{view.ref ?? "no base branch"}</dd>
        <dt>base_branch</dt>
        <dd>
          <select aria-label="Base branch" value={view.configured ?? ""} disabled={busy || !view.configured_file}
            onChange={(e) => change({ choice: "pick", branch: e.target.value })}>
            {!view.configured && <option value="">(not set: main, else master)</option>}
            {choices.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </dd>
        <dt>Canonical remote</dt>
        <dd>
          <select aria-label="Canonical remote" value={view.setting ?? ""} disabled={busy || !view.configured_file || !view.remotes.length}
            onChange={(e) => change({ choice: "canonical", remote: e.target.value })}>
            <option value="">{view.canonical ? `default (${view.canonical})` : "default"}</option>
            {view.remotes.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </dd>
      </dl>
      {view.warning && <p className="hint amber" data-testid="base-warning">{view.warning}.</p>}
      {view.missing && <BaseQuestion pid={pid} missing={view.missing} onDone={setView} />}
      <p className="ss-meta ss-small">
        Saved in <span className="ss-mono">.keel/config.yml</span> (base_branch, git.remote). The canonical remote is the one keel
        trusts for the base: by default the remote the base branch follows, else one that has it, else origin. Agents never change these.
      </p>
      {!view.configured_file && <p className="ss-meta ss-small">This project has no .keel/config.yml yet: run keel init first.</p>}
      {err != null && view && <ErrorBox error={errorParts(err)} />}
    </div>
  );
}

