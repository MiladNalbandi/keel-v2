// Quick open (Ctrl/⌘+P): a fuzzy file finder over every file of the project. ":12" goes to a line of the open file.

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api, errorParts } from "../../api";
import { FileIcon } from "./icons";
import { nameOf, rankFiles } from "./model";

let cache: { pid: string; at: number; files: string[]; truncated: boolean } | null = null;

/** The text with the matched letters in bold. */
function Hl({ text, hits, offset }: { text: string; hits: number[]; offset: number }) {
  const set = new Set(hits.map((h) => h - offset));
  const out: ReactNode[] = [];
  for (let i = 0; i < text.length; i++) out.push(set.has(i) ? <b key={i}>{text[i]}</b> : text[i]);
  return <>{out}</>;
}

type Props = {
  pid: string;
  hasFile: boolean;
  onOpen: (path: string, pin: boolean) => void;
  onGoto: (line: number) => void;
  onClose: () => void;
};

export function QuickOpen({ pid, hasFile, onOpen, onGoto, onClose }: Props) {
  const [q, setQ] = useState("");
  const [files, setFiles] = useState<string[] | null>(cache?.pid === pid ? cache.files : null);
  const [truncated, setTruncated] = useState(cache?.pid === pid && cache.truncated);
  const [err, setErr] = useState<string | null>(null);
  const [at, setAt] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (cache && cache.pid === pid && Date.now() - cache.at < 30_000) return;
    api.repoFiles(pid).then((r) => {
      cache = { pid, at: Date.now(), files: r.files, truncated: r.truncated };
      setFiles(r.files);
      setTruncated(r.truncated);
    }, (e) => setErr(errorParts(e).message));
  }, [pid]);

  const goto = /^:\d*$/.test(q.trim());
  const line = goto ? Number(q.trim().slice(1)) : 0;
  const ranked = useMemo(() => (goto || !files ? [] : rankFiles(files, q, 60)), [files, q, goto]);
  useEffect(() => setAt(0), [q]);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-i="${at}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [at]);

  const pick = (i: number, pin: boolean) => {
    const r = ranked[i];
    if (!r) return;
    onOpen(r.path, pin);
    onClose();
  };

  return createPortal(
    <div className="qo-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="qo" role="dialog" aria-modal="true" aria-label="Quick open">
        <div className="qo-top">
        <input autoFocus type="text" className="qo-in" role="combobox" aria-expanded="true" aria-controls="qo-list" aria-label="Search files by name"
          aria-activedescendant={ranked[at] ? `qo-${at}` : undefined}
          placeholder="Search files by name (type : and a number to go to a line)" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") { e.preventDefault(); onClose(); }
            else if (e.key === "ArrowDown") { e.preventDefault(); setAt((a) => Math.min(ranked.length - 1, a + 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setAt((a) => Math.max(0, a - 1)); }
            else if (e.key === "Enter") {
              e.preventDefault();
              if (goto) {
                if (line > 0 && hasFile) { onGoto(line); onClose(); }
              } else pick(at, false);
            }
          }} />
          <button type="button" className="qo-x btn sm ghost" onClick={onClose}>Close</button>
        </div>
        {goto ? (
          <p className="qo-note">{hasFile ? (line > 0 ? `Press Enter to go to line ${line}.` : "Type a line number.") : "Open a file first, then go to a line."}</p>
        ) : err ? <p className="qo-note bad">{err}</p> : !files ? <p className="qo-note"><span className="pg-spin" role="status">Listing files…</span></p> : (
          <>
            <ul id="qo-list" ref={listRef} className="qo-list" role="listbox" aria-label="Files">
              {ranked.map((r, i) => {
                const nameAt = r.path.lastIndexOf("/") + 1;
                return (
                  <li key={r.path} id={`qo-${i}`} data-i={i} role="option" aria-selected={i === at} className={i === at ? "on" : ""}
                    onMouseMove={() => setAt(i)} onClick={() => pick(i, false)} onDoubleClick={() => pick(i, true)}>
                    <FileIcon name={nameOf(r.path)} />
                    <span className="qo-name"><Hl text={nameOf(r.path)} hits={r.hits} offset={nameAt} /></span>
                    <span className="qo-dir"><Hl text={r.path.slice(0, Math.max(0, nameAt - 1))} hits={r.hits} offset={0} /></span>
                  </li>
                );
              })}
            </ul>
            {!ranked.length && <p className="qo-note">No file matches “{q}”.</p>}
            {truncated && <p className="qo-note">The project has more than {files.length.toLocaleString()} files; only those are listed.</p>}
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
