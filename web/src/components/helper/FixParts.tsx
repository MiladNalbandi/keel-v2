// The Helper's Fix mode (a flow waits at a gate): the command that waits for the person's OK, the files the Helper
// changed in this chat (Diff, Undo, Undo all), and Done, where keel runs the checks and commits only those files.

import { useState } from "react";
import { api, errorParts, type HelperChange, type HelperDone, type HelperQuestion } from "../../api";
import { DiffView, FoldedText } from "../Code";

/** "May the Helper run this?" Allow once, Always (this command, for the rest of the chat), or Deny with a reason. */
export function PermissionCard({ pid, q, onAnswered }: { pid: string; q: HelperQuestion; onAnswered: () => void }) {
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const answer = async (decision: "once" | "always" | "deny") => {
    setBusy(true);
    setErr(null);
    try {
      await api.helperAnswer(pid, q.id, decision, why.trim());
      onAnswered();
    } catch (e) {
      setErr(errorParts(e).message);
      setBusy(false);
    }
  };
  return (
    <div className="hp-perm" role="group" aria-label="The Helper asks to run a command">
      <p className="hp-perm-q"><b>May the Helper run this?</b> It changes something, so keel asks you first.</p>
      <pre className="hp-perm-cmd">{q.command}</pre>
      <input className="hp-perm-why" value={why} onChange={(e) => setWhy(e.target.value)} aria-label="Why not (optional)"
        placeholder="Why not (optional, the Helper reads it)" />
      <div className="hp-perm-btns">
        <button type="button" className="btn sm warn" disabled={busy} onClick={() => void answer("once")}>Allow once</button>
        <button type="button" className="btn sm" disabled={busy} onClick={() => void answer("always")}
          title="Allow this exact command for the rest of this chat">Always</button>
        <button type="button" className="btn sm" disabled={busy} onClick={() => void answer("deny")}>Deny</button>
      </div>
      {err && <p className="hp-err" role="alert">{err}</p>}
    </div>
  );
}

/** The files the Helper changed in this chat, against what they were before its first change. */
export function ChangesBox({ changes, busy, onOpen, onUndo, onDone }: {
  changes: HelperChange[];
  busy: boolean;
  onOpen: (path: string) => void;
  onUndo: (path?: string) => void;
  onDone: () => void;
}) {
  const [shown, setShown] = useState<string | null>(null);
  return (
    <section className="hp-changes" aria-label="What the Helper changed">
      <header className="hp-changes-head">
        <b>Changed files</b><span className="hp-sub">{changes.length}</span>
        <button type="button" className="hp-tb" disabled={busy} onClick={() => onUndo()}>Undo all</button>
      </header>
      <ul className="hp-files">
        {changes.map((c) => (
          <li key={c.path}>
            <div className="hp-file">
              <button type="button" className="hp-file-name" title={`Open ${c.path} in the editor (its changes)`} onClick={() => onOpen(c.path)}>
                {c.path}
              </button>
              {c.status !== "modified" && <span className="hp-sub">{c.status}</span>}
              <span className="hp-plus" aria-label={`${c.added} lines added`}>+{c.added}</span>
              <span className="hp-minus" aria-label={`${c.removed} lines removed`}>−{c.removed}</span>
              <button type="button" className="hp-tb" aria-expanded={shown === c.path} aria-label={`Diff of ${c.path}`}
                onClick={() => setShown(shown === c.path ? null : c.path)}>Diff</button>
              <button type="button" className="hp-tb" disabled={busy} aria-label={`Undo ${c.path}`} onClick={() => onUndo(c.path)}>Undo</button>
            </div>
            {shown === c.path && <DiffView diff={c.diff} path={c.path} fold={40} />}
          </li>
        ))}
      </ul>
      <button type="button" className="btn sm primary hp-done" disabled={busy} onClick={onDone}>
        {busy ? "Checking…" : "Done: run the checks and commit"}
      </button>
    </section>
  );
}

/** Done did not commit: why, the checks' output, and a way to hand the failure back to the Helper. */
export function DoneFailed({ res, onAskFix, onClose }: { res: Extract<HelperDone, { ok: false }>; onAskFix: () => void; onClose: () => void }) {
  return (
    <div className="hp-failed" role="alert">
      <p><b>Not committed.</b> {res.error}</p>
      {res.command && <p className="hp-sub">Checks: <code>{res.command}</code></p>}
      {res.output && <FoldedText text={res.output} fold={20} className="hp-out" />}
      <div className="hp-perm-btns">
        {res.step !== "changes" && <button type="button" className="btn sm primary" onClick={onAskFix}>Ask the Helper to fix it</button>}
        <button type="button" className="btn sm ghost" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

/** What the Helper reads when the person hands a failed Done back to it. */
export function fixRequest(res: Extract<HelperDone, { ok: false }>): string {
  const tail = (res.output ?? "").split("\n").slice(-60).join("\n").trim();
  const head = res.step === "checks"
    ? `The checks failed after your change${res.command ? ` (\`${res.command}\`)` : ""}. Find why and fix it.`
    : `keel did not commit your change: ${res.error} Fix it.`;
  return tail ? `${head}\n\n\`\`\`\n${tail}\n\`\`\`` : head;
}
