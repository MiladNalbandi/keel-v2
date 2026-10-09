// Search across files (Ctrl/⌘+Shift+F): text or regex, match case, whole word, files to include / exclude.
// The api runs git grep; results come grouped by file with the matching lines, and a click opens the file at the line.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ErrorBox, errorParts } from "@keel/web-sdk";
import { codeApi as api, type SearchResult } from "./codeApi";
import { Chevron, FileIcon, Icon } from "./icons";
import { nameOf, parentOf } from "./model";

type Props = {
  pid: string;
  focus: number;
  onOpen: (path: string, line: number, col: number, len: number, pin: boolean) => void;
};

/** The line with its matches marked; leading spaces trimmed (ranges shift with them). */
function Preview({ text, ranges }: { text: string; ranges: [number, number][] }) {
  // like VS Code: no leading spaces, and a long start is cut so the first match is in view
  let lead = text.length - text.trimStart().length;
  const first = ranges[0]?.[0] ?? 0;
  if (first - lead > 24) lead = first - 12;
  const cut = lead > 0 && text.slice(0, lead).trim() !== "";
  const t = (cut ? "…" : "") + text.slice(lead);
  if (cut) lead -= 1;
  const out: ReactNode[] = [];
  let at = 0;
  ranges.forEach(([s0, e0], i) => {
    const s = Math.max(0, s0 - lead);
    const e = Math.max(s, e0 - lead);
    if (s > at) out.push(t.slice(at, s));
    out.push(<mark key={i} className="sr-m">{t.slice(s, e)}</mark>);
    at = e;
  });
  out.push(t.slice(at));
  return <>{out}</>;
}

export function SearchView({ pid, focus, onOpen }: Props) {
  const [q, setQ] = useState("");
  const [matchCase, setCase] = useState(false);
  const [word, setWord] = useState(false);
  const [regex, setRegex] = useState(false);
  const [globs, setGlobs] = useState(false);
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [res, setRes] = useState<SearchResult | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const input = useRef<HTMLInputElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    if (!focus) return;
    input.current?.focus();
    input.current?.select();
  }, [focus]);

  // search as you type, after a short pause; an older answer never replaces a newer one
  useEffect(() => {
    const n = ++seq.current;
    if (!q) {
      setRes(null);
      setErr(null);
      setBusy(false);
      return;
    }
    setBusy(true);
    const t = window.setTimeout(() => {
      api.search(pid, { q, regex, case: matchCase, word, include: globs ? include : undefined, exclude: globs ? exclude : undefined, max: 1000 }).then(
        (r) => {
          if (n !== seq.current) return;
          setRes(r);
          setErr(null);
          setClosed(new Set());
        },
        (e) => {
          if (n !== seq.current) return;
          setErr(errorParts(e));
          setRes(null);
        },
      ).finally(() => n === seq.current && setBusy(false));
    }, 300);
    return () => window.clearTimeout(t);
  }, [pid, q, regex, matchCase, word, globs, include, exclude]);

  const toggleFile = (p: string) => setClosed((s) => {
    const n = new Set(s);
    if (n.has(p)) n.delete(p);
    else n.add(p);
    return n;
  });

  return (
    <div className="sv">
      <div className="sv-head">
        <h2 className="sv-title">Search</h2>
        <div className="sv-actions">
          <button type="button" className="ib" aria-label="Collapse all results" title="Collapse all results" disabled={!res?.results.length}
            onClick={() => setClosed(new Set(res?.results.map((r) => r.path)))}><Icon name="collapse" size={16} /></button>
        </div>
      </div>
      <div className="sr-form">
        <div className="sr-box">
          <input ref={input} type="search" aria-label="Search in files" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="sr-tg">
            <button type="button" className={`ib tg${matchCase ? " on" : ""}`} aria-pressed={matchCase} aria-label="Match case" title="Match case" onClick={() => setCase(!matchCase)}>Aa</button>
            <button type="button" className={`ib tg${word ? " on" : ""}`} aria-pressed={word} aria-label="Match whole word" title="Match whole word" onClick={() => setWord(!word)}><u>ab</u></button>
            <button type="button" className={`ib tg${regex ? " on" : ""}`} aria-pressed={regex} aria-label="Use regular expression" title="Use regular expression" onClick={() => setRegex(!regex)}>.*</button>
          </span>
        </div>
        <button type="button" className="sr-more linkbtn" aria-expanded={globs} onClick={() => setGlobs(!globs)}>{globs ? "Hide file filters" : "Files to include or exclude"}</button>
        {globs && (
          <>
            <label className="sr-lab">files to include
              <input type="text" value={include} onChange={(e) => setInclude(e.target.value)} placeholder="e.g. *.kt, src/" spellCheck={false} />
            </label>
            <label className="sr-lab">files to exclude
              <input type="text" value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="e.g. *Test.kt, docs/" spellCheck={false} />
            </label>
          </>
        )}
      </div>
      <div className="sr-sum" aria-live="polite">
        {busy ? <span className="pg-spin" role="status">Searching…</span>
          : res ? (
            res.matches
              ? <>{res.matches.toLocaleString()} result{res.matches === 1 ? "" : "s"} in {res.files.toLocaleString()} file{res.files === 1 ? "" : "s"}
                {res.truncated && <span className="sr-warn"> — the first {res.matches.toLocaleString()} only; narrow the search</span>}
                {res.timed_out && <span className="sr-warn"> — stopped after 10 s</span>}</>
              : <>No results. {globs && (include || exclude) ? "Check the file filters, or " : ""}try another word.</>
          ) : !q ? <span className="sub">Searches tracked and new files; .gitignore'd and binary files are skipped.</span> : null}
      </div>
      {err && <div className="sv-pad"><ErrorBox error={err} /></div>}
      <div className="sr-list" role="tree" aria-label="Search results">
        {res?.results.map((f) => {
          const open = !closed.has(f.path);
          return (
            <div key={f.path} role="none">
              <button type="button" role="treeitem" aria-expanded={open} className="sr-file" onClick={() => toggleFile(f.path)} title={f.path}>
                <span className="ex-tw"><Chevron open={open} /></span>
                <FileIcon name={nameOf(f.path)} />
                <span className="sr-fname">{nameOf(f.path)}</span>
                <span className="sr-dir">{parentOf(f.path)}</span>
                <span className="sr-count">{f.matches.length}</span>
              </button>
              {open && f.matches.map((m) => (
                <button key={`${m.line}:${m.column}`} type="button" role="treeitem" className="sr-hit"
                  aria-label={`${f.path} line ${m.line}: ${m.text.trim()}`}
                  onClick={() => onOpen(f.path, m.line, m.column, m.length, false)}
                  onDoubleClick={() => onOpen(f.path, m.line, m.column, m.length, true)}>
                  <span className="sr-ln">{m.line}</span>
                  <span className="sr-txt"><Preview text={m.text} ranges={m.ranges} /></span>
                </button>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
