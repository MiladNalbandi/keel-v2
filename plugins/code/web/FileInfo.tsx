// keel 0.28 (the redesign) the column on the right of the open file: its outline (classes, functions, headings: click one
// to go to its line) and "This file in keel": the phase rule for it now, the AC that changed it on this branch, who wrote
// it, its last commit, and the way to keel's view of it and to Memory. All of it is the file's own data (GET /repo/file
// and its text); the keel view in the side bar keeps the history and the unlock.

import { useEffect, useRef } from "react";
import type { RepoFile } from "./codeApi";
import { Icon } from "./icons";
import { ruleText } from "./KeelView";
import type { Sym } from "./outline";

const KIND: Record<Sym["kind"], [string, string]> = {
  class: ["C", "class"],
  fn: ["ƒ", "function"],
  type: ["T", "type"],
  head: ["#", "heading"],
};

export function FileInfo({ file, syms, at, onGo, onKeel, onMemory }: {
  file: RepoFile;
  syms: Sym[];
  /** the symbol the cursor is in */
  at: Sym | null;
  onGo: (line: number) => void;
  onKeel: () => void;
  onMemory: () => void;
}) {
  const list = useRef<HTMLOListElement>(null);
  // the symbol the cursor is in stays in view: the list scrolls, never the window (.ei-syms is position: relative)
  useEffect(() => {
    const box = list.current;
    const el = at ? box?.querySelector<HTMLElement>(`[data-line="${at.line}"]`) : null;
    if (!box || !el) return;
    const top = el.offsetTop, h = el.offsetHeight;
    if (top < box.scrollTop) box.scrollTop = top;
    else if (top + h > box.scrollTop + box.clientHeight) box.scrollTop = top + h - box.clientHeight;
  }, [at?.line]);
  const phase = file.phase && file.phase !== "none" ? file.phase : null;
  const tone = file.frozen || file.verdict === "deny" ? "frozen" : phase ? "open" : "none";
  return (
    <aside className="ed-info" aria-label="About this file">
      <section className="ei-sec ei-outline" aria-label="Outline">
        <h3 className="ei-h">Outline</h3>
        {syms.length ? (
          <ol ref={list} className="ei-syms">
            {syms.map((s) => (
              <li key={`${s.line}:${s.name}`}>
                <button type="button" className={`ei-sym d${s.depth}${at === s ? " on" : ""}`} data-line={s.line}
                  aria-current={at === s ? "true" : undefined} title={`${KIND[s.kind][1]} ${s.name}, line ${s.line}`} onClick={() => onGo(s.line)}>
                  <span className={`ei-k k-${s.kind}`} aria-hidden="true">{KIND[s.kind][0]}</span>
                  <span className="ei-n">{s.name}</span>
                  <span className="ei-l" aria-hidden="true">{s.line}</span>
                </button>
              </li>
            ))}
          </ol>
        ) : <p className="ei-none">No classes, functions or headings found in this file.</p>}
      </section>
      <section className="ei-sec ei-keel" aria-label="This file in keel">
        <h3 className="ei-h">This file in keel</h3>
        <p className={`ei-rule t-${tone}`}>{file.frozen && <Icon name="lock" size={13} />}<span>{ruleText(file)}</span></p>
        <dl className="ei-dl">
          {file.ac && <><dt>Changed for</dt><dd><span className="scm-ac">{file.ac}</span></dd></>}
          <dt>Written by</dt><dd>{file.keel ? "keel" : "people and agents"}</dd>
          {file.bucket && <><dt>keel sees it as</dt><dd className="mono">{file.bucket}</dd></>}
          {file.last_commit && (
            <><dt>Last commit</dt><dd title={file.last_commit.message}><span className="mono">{file.last_commit.sha.slice(0, 7)}</span> {file.last_commit.message}</dd></>
          )}
        </dl>
        <div className="ei-acts">
          <button type="button" className="btn sm" onClick={onKeel} title="The file's history and the unlock for this phase">History and rules</button>
          <button type="button" className="btn sm ghost" onClick={onMemory} title="Facts agents get with each step">Memory</button>
        </div>
      </section>
    </aside>
  );
}
