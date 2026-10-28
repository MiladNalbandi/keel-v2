// 0.18.64 the AC gate's code: the criterion's RED commit (its test) and GREEN commit (its production code) as diffs, one
// card per file, in the gate card. The engine reads git (GET .../flows/{tid}/ac-code); lock, generated and binary files
// come folded without text, and Show asks for that one file.

import { useState } from "react";
import { api, errorParts, type AcCode, type AcCodeCommit, type AcCodeFile } from "../../api";
import { DiffView } from "../../components/Code";
import { codeHref } from "../../format";
import { hashFor } from "../../routes";
import { useHasPage } from "../../sdk/registry";

/** A file's diff folds when it has more than FILE_FOLD lines, to its first FILE_KEEP. */
export const FILE_FOLD = 30;
export const FILE_KEEP = 12;
type Tab = "red" | "green" | "both";

/** The Markdown detail without one `## <title>` section (the card shows the commits itself; the TUI and MCP keep it). */
export function dropSection(text: string, title: string): string {
  const lines = text.split("\n");
  const at = lines.findIndex((l) => l.trim() === `## ${title}`);
  if (at < 0) return text;
  let end = lines.findIndex((l, i) => i > at && /^## /.test(l));
  if (end < 0) end = lines.length;
  return [...lines.slice(0, at), ...lines.slice(end)].join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

const NOTE: Record<AcCodeFile["status"], string> = { added: "new file", deleted: "deleted", modified: "", renamed: "renamed" };

function noteOf(f: AcCodeFile): string {
  const parts = [f.status === "renamed" && f.old_path ? `renamed from ${f.old_path}` : NOTE[f.status]];
  if (f.binary) parts.push("binary, no diff");
  else if (f.bucket === "lock") parts.push("lock file · folded");
  else if (f.bucket === "generated") parts.push("generated · folded");
  else if (f.diff === null) parts.push("left out: the diffs are long · folded");
  else if (f.truncated) parts.push(`cut at ${f.diff.split("\n").length} of ${f.lines ?? "?"} lines`);
  return parts.filter(Boolean).join(" · ");
}

function FileDiff({ pid, tid, ac, sha, f }: { pid: string; tid: string; ac: string; sha: string; f: AcCodeFile }) {
  const [shown, setShown] = useState<string | null>(f.folded || f.diff === null ? null : f.diff);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const show = async () => {
    if (f.diff !== null) { setShown(f.diff); return; }
    setBusy(true);
    setErr(null);
    try {
      const one = await api.acCodeFile(pid, tid, ac, sha, f.path);
      if (one.file?.diff != null) setShown(one.file.diff);
      else setErr("keel has no diff for this file.");
    } catch (e) {
      setErr(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  const note = noteOf(f);
  return (
    <div className="acc-file" data-path={f.path} data-bucket={f.bucket}>
      <div className="acc-fhead">
        <span className="acc-path">{f.path}</span>
        <span className="acc-add" aria-label={`${f.added} added`}>+{f.added}</span>
        <span className="acc-del" aria-label={`${f.removed} removed`}>−{f.removed}</span>
        {note && <span className="acc-note">{note}</span>}
        {shown === null && !f.binary && (
          <button type="button" className="btn sm acc-show" disabled={busy} onClick={() => void show()}
            aria-label={`Show the diff of ${f.path}`}>{busy ? "Loading…" : "Show"}</button>
        )}
      </div>
      {err && <p className="sub acc-err">{err}</p>}
      {shown ? (
        <DiffView diff={shown} path={f.path} fold={FILE_FOLD} keep={FILE_KEEP} className="acc-diff"
          foldLabel={(total) => `… ${total - FILE_KEEP} more lines · show all`} />
      ) : null}
    </div>
  );
}

function Files({ pid, tid, ac, c }: { pid: string; tid: string; ac: string; c: AcCodeCommit }) {
  return (
    <>
      {c.files.map((f) => <FileDiff key={`${c.sha}:${f.path}`} pid={pid} tid={tid} ac={ac} sha={c.sha} f={f} />)}
      {c.more_files ? <p className="sub acc-more">… and {c.more_files} more file(s): see them in Code.</p> : null}
      {!c.files.length && <p className="sub">No files in this commit.</p>}
    </>
  );
}

function TabLabel({ name, c, missing }: { name: string; c: AcCodeCommit | null; missing: string }) {
  if (!c) return <>{name} · no <code>{missing}</code> commit found</>;
  return <>{name} · <code>{c.short}</code> {c.subject}</>;
}

/** The criterion's code: a summary line with links to Code and Review, RED / GREEN / Both tabs, and a card per file. */
export function AcCodeSection({ pid, tid, code }: { pid: string; tid: string; code: AcCode }) {
  const { red, green, ac } = code;
  const [tab, setTab] = useState<Tab>(red ? "red" : green ? "green" : "both");
  const hasCode = useHasPage("repo");
  const hasReview = useHasPage("review");
  const commit = tab === "red" ? red : tab === "green" ? green : green ?? red;
  const first = commit?.files.find((f) => !f.folded && !f.binary) ?? commit?.files[0];
  const tabs: { id: Tab; c: AcCodeCommit | null; name: string; missing: string }[] = [
    { id: "red", c: red, name: "RED", missing: `test(${ac})` },
    { id: "green", c: green, name: "GREEN", missing: `feat(${ac})` },
  ];
  const pick = (t: Tab) => setTab(t);
  return (
    <section className="acc" aria-label="The code of this criterion">
      <div className="acc-head">
        <h4>The code of this criterion</h4>
        <span className="acc-sum" data-testid="acc-sum">
          {code.commits} {code.commits === 1 ? "commit" : "commits"} · {code.files} {code.files === 1 ? "file" : "files"} · <span className="acc-add">+{code.added}</span> <span className="acc-del">−{code.removed}</span>
        </span>
        <span className="acc-links">
          {hasCode && first && commit ? <a href={codeHref(first.path, null, commit.sha)}>Open in Code ›</a> : null}
          {hasReview ? <a href={hashFor("review", code.branch ? `branch:${code.branch}` : undefined)}>Open in Review ›</a> : null}
        </span>
      </div>
      {code.commits > 0 && (
        <>
          <div className="acc-tabs" role="tablist" aria-label="Commits">
            {tabs.map((t) => (
              <button key={t.id} type="button" role="tab" id={`acc-tab-${t.id}`} className="acc-tab" aria-selected={tab === t.id}
                aria-controls="acc-panel" disabled={!t.c} onClick={() => pick(t.id)}>
                <TabLabel name={t.name} c={t.c} missing={t.missing} />
              </button>
            ))}
            <button type="button" role="tab" id="acc-tab-both" className="acc-tab" aria-selected={tab === "both"} aria-controls="acc-panel"
              onClick={() => pick("both")}>Both</button>
          </div>
          <div className="acc-panel" role="tabpanel" id="acc-panel" aria-labelledby={`acc-tab-${tab}`}>
            {tab === "both" ? (
              tabs.map((t) => t.c ? (
                <div key={t.id} className="acc-group">
                  <p className="acc-group-h"><TabLabel name={t.name} c={t.c} missing={t.missing} /></p>
                  <Files pid={pid} tid={tid} ac={ac} c={t.c} />
                </div>
              ) : <p key={t.id} className="sub acc-group-h"><TabLabel name={t.name} c={null} missing={t.missing} /></p>)
            ) : commit ? <Files pid={pid} tid={tid} ac={ac} c={commit} /> : null}
          </div>
        </>
      )}
      {code.commits === 0 && (
        <p className="sub">No <code>{`test(${ac})`}</code> commit found, and no <code>{`feat(${ac})`}</code> commit found.</p>
      )}
      <p className="sub acc-hint">RED shows the test, GREEN the production code, Both the criterion's whole diff. Files over {FILE_FOLD} lines fold;
        generated files and lock files stay folded.{code.truncated ? " Long diffs are cut here; Code shows them whole." : ""}</p>
    </section>
  );
}
