// The editor area of the Repo IDE: highlighted code with a sticky line-number gutter, the current line, find in
// file (case / whole word / regex), go to line, word wrap, a Markdown / SVG preview, images, binary and big files,
// and diffs (inline or side by side) against HEAD, the base branch, or one commit. Read-only.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, errorParts, type RepoFile } from "../../api";
import { linesOf, parseDiff, type DiffRow } from "../../components/Code";
import { highlight, languageOf } from "../../components/highlight";
import { Markdown } from "../../components/Markdown";
import { ErrorBox } from "../../components/ui";
import { useLoad } from "../../state";
import { Icon, isImage, extOf } from "./icons";
import {
  bytes, escapeHtml, findAll, findRegExp, markHtml, parseGoto, splitHtmlLines, splitRows,
  type FindOpts, type Hit,
} from "./model";

/** Text files above this are not shown (the first 120 lines are, from the file view). */
export const TEXT_MAX = 2 * 1024 * 1024;
/** Fixed row height (px) — what lets long files render only the rows on screen. */
const LH = 20;
/** Files longer than this render only the visible rows (when not wrapping). */
const VIRTUAL_FROM = 1500;
/** Word wrap is off above this many lines (every row would have to render). */
export const WRAP_MAX = 5000;

export type Cursor = { line: number; col: number };
export type Target = { line: number; col?: number; len?: number; n: number };
export type Cmd = { kind: "find" | "goto"; n: number } | null;

// ---------- file text, cached per path and mtime ----------

const texts = new Map<string, { stamp: string; text: string }>();

/** The whole text of a file (cached until its size or mtime changes). */
export function useFileText(pid: string, meta: RepoFile | null, enabled: boolean) {
  const stamp = meta ? `${meta.size}:${meta.modified ?? 0}` : "";
  const key = meta && enabled ? `${pid}:${meta.path}` : null;
  const [state, setState] = useState<{ key: string; text: string } | { key: string; error: { message: string; hint?: string } } | null>(null);
  useEffect(() => {
    if (!key || !meta) return;
    const hit = texts.get(key);
    if (hit && hit.stamp === stamp) {
      setState({ key, text: hit.text });
      return;
    }
    let live = true;
    api.raw(pid, meta.path).then((text) => {
      texts.set(key, { stamp, text });
      if (texts.size > 30) texts.delete(texts.keys().next().value!);
      if (live) setState({ key, text });
    }, (e) => live && setState({ key, error: errorParts(e) }));
    return () => {
      live = false;
    };
  }, [key, stamp, pid, meta]);
  return state && state.key === key ? state : null;
}

// ---------- the code view ----------

type CodeProps = {
  text: string;
  path: string;
  wrap: boolean;
  target: Target | null;
  cmd: Cmd;
  onCursor: (c: Cursor) => void;
  onLink: (line: number) => void;
  scrollKey: string;
};

/** Per tab: where it was scrolled, and the last jump it made (so coming back to a tab keeps its place). */
const scrolls = new Map<string, number>();
const jumped = new Map<string, number>();
/** The last Ctrl/⌘+F or +G handled (a tab that mounts later must not open it again). */
let handledCmd = 0;

export function CodeView({ text, path, wrap, target, cmd, onCursor, onLink, scrollKey }: CodeProps) {
  const lines = useMemo(() => {
    const l = linesOf(text);
    return l.length ? l : [""];
  }, [text]);
  const html = useMemo(() => {
    const lang = languageOf(path);
    const h = text.length <= 200_000 ? highlight(lines.join("\n"), lang) : null;
    return h !== null ? splitHtmlLines(h) : lines.map(escapeHtml);
  }, [lines, path, text.length]);
  const wrapOn = wrap && lines.length <= WRAP_MAX;
  const virtual = !wrapOn && lines.length > VIRTUAL_FROM;
  const maxCols = useMemo(() => (virtual ? lines.reduce((m, l) => Math.max(m, l.replace(/\t/g, "    ").length), 0) : 0), [lines, virtual]);
  const box = useRef<HTMLDivElement>(null);
  const [win, setWin] = useState({ from: 0, to: 120 });
  const [cursor, setCursor] = useState<Cursor | null>(null);
  const [flash, setFlash] = useState<Target | null>(null);

  // find in file
  const [findOpen, setFindOpen] = useState(false);
  const [q, setQ] = useState("");
  const [opts, setOpts] = useState<FindOpts>({ matchCase: false, word: false, regex: false });
  const [hitAt, setHitAt] = useState(0);
  const findInput = useRef<HTMLInputElement>(null);
  const re = useMemo(() => findRegExp(q, opts), [q, opts]);
  const hits = useMemo<Hit[]>(() => (findOpen && re instanceof RegExp ? findAll(lines, re) : []), [findOpen, re, lines]);
  const byLine = useMemo(() => {
    const m = new Map<number, number[]>();
    hits.forEach((h, i) => {
      const l = m.get(h.line);
      if (l) l.push(i);
      else m.set(h.line, [i]);
    });
    return m;
  }, [hits]);

  // go to line
  const [gotoOpen, setGotoOpen] = useState(false);
  const [gotoText, setGotoText] = useState("");

  const measure = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const h = el.clientHeight || 800;
    const from = Math.max(0, Math.floor(el.scrollTop / LH) - 40);
    const to = Math.min(lines.length, Math.ceil((el.scrollTop + h) / LH) + 40);
    setWin((w) => (w.from === from && w.to === to ? w : { from, to }));
  }, [lines.length]);

  const scrollTo = useCallback((line: number, always = false) => {
    const el = box.current;
    if (!el) return;
    if (wrapOn) {
      const row = el.querySelector<HTMLElement>(`[data-line="${line}"]`);
      row?.scrollIntoView?.({ block: "center" });
      return;
    }
    const top = (line - 1) * LH;
    const h = el.clientHeight;
    if (!always && h && top >= el.scrollTop && top <= el.scrollTop + h - LH * 2) return;
    el.scrollTop = Math.max(0, top - (h ? h / 3 : 0));
    measure();
  }, [wrapOn, measure]);

  // keep each tab's scroll position
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const saved = scrolls.get(scrollKey);
    if (saved !== undefined && (!target || jumped.get(scrollKey) === target.n)) el.scrollTop = saved;
    measure();
    return () => {
      scrolls.set(scrollKey, el.scrollTop);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollKey]);

  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure]);

  // a jump from a deep link, the search, go to line or quick open
  useEffect(() => {
    if (!target) return;
    const line = Math.min(Math.max(1, target.line), lines.length);
    setFlash({ ...target, line });
    if (jumped.get(scrollKey) === target.n) return;
    jumped.set(scrollKey, target.n);
    setCursor({ line, col: target.col ?? 1 });
    onCursor({ line, col: target.col ?? 1 });
    window.setTimeout(() => scrollTo(line, true), 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.n, target?.line, lines.length]);

  // Ctrl/⌘+F and Ctrl/⌘+G from the IDE
  useEffect(() => {
    if (!cmd || cmd.n <= handledCmd) return;
    handledCmd = cmd.n;
    if (cmd.kind === "find") {
      const sel = window.getSelection?.()?.toString() ?? "";
      if (sel && !sel.includes("\n") && sel.length < 200) setQ(sel);
      setGotoOpen(false);
      setFindOpen(true);
      window.setTimeout(() => {
        findInput.current?.focus();
        findInput.current?.select();
      }, 0);
    } else {
      setFindOpen(false);
      setGotoText("");
      setGotoOpen(true);
    }
  }, [cmd?.n]); // eslint-disable-line react-hooks/exhaustive-deps

  // the first match at or after the cursor when the search changes
  useEffect(() => {
    if (!hits.length) return;
    const from = cursor?.line ?? 1;
    const i = hits.findIndex((h) => h.line >= from);
    const at = i < 0 ? 0 : i;
    setHitAt(at);
    scrollTo(hits[at].line);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hits]);

  const step = (d: 1 | -1) => {
    if (!hits.length) return;
    const at = (hitAt + d + hits.length) % hits.length;
    setHitAt(at);
    const h = hits[at];
    setCursor({ line: h.line, col: h.start + 1 });
    onCursor({ line: h.line, col: h.start + 1 });
    scrollTo(h.line);
  };

  const pickLine = (line: number, col = 1) => {
    setCursor({ line, col });
    onCursor({ line, col });
  };

  const onMouseUp = () => {
    const sel = window.getSelection?.();
    const node = sel?.focusNode;
    if (!node) return;
    const el = node.nodeType === 1 ? (node as Element) : node.parentElement;
    const row = el?.closest<HTMLElement>(".cv-row");
    const code = row?.querySelector(".cv-code");
    if (!row || !code) return;
    const line = Number(row.dataset.line);
    let col = 1;
    if (code.contains(node)) {
      const walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
      let off = 0;
      for (let t = walker.nextNode(); t; t = walker.nextNode()) {
        if (t === node) {
          off += sel!.focusOffset;
          break;
        }
        off += t.textContent?.length ?? 0;
      }
      col = off + 1;
    }
    pickLine(line, col);
  };

  const go = () => {
    const g = parseGoto(gotoText, lines.length);
    if (!g) return;
    setGotoOpen(false);
    const t = { line: g.line, col: g.col, n: Date.now() };
    setFlash(t);
    pickLine(g.line, g.col);
    window.setTimeout(() => scrollTo(g.line, true), 0);
    box.current?.focus();
  };

  const from = virtual ? win.from : 0;
  const to = virtual ? win.to : lines.length;
  const rows: ReactNode[] = [];
  const err = re && !(re instanceof RegExp) ? re.error : null;
  for (let i = from; i < to; i++) {
    const n = i + 1;
    const marks: [number, number, string][] = [];
    for (const k of byLine.get(n) ?? []) marks.push([hits[k].start, hits[k].end, k === hitAt ? "fd cur" : "fd"]);
    if (flash && flash.line === n && flash.len && !marks.length) {
      const c = (flash.col ?? 1) - 1;
      marks.push([c, c + flash.len, "fd hit"]);
    }
    const h = marks.length ? markHtml(html[i], marks) : html[i];
    rows.push(
      <div key={n} className={`cv-row${cursor?.line === n ? " cur" : ""}${flash?.line === n ? " tgt" : ""}`} data-line={n}>
        <span className="cv-ln" aria-hidden="true" onClick={() => { pickLine(n); onLink(n); }}>{n}</span>
        <code className="cv-code" dangerouslySetInnerHTML={{ __html: h }} />
      </div>,
    );
  }
  const digits = String(lines.length).length;

  return (
    <div className="cv-wrap">
      {findOpen && (
        <div className="find" role="search" aria-label="Find in file">
          <input ref={findInput} type="text" className="find-in" aria-label="Find in file" placeholder="Find" value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
              if (e.key === "Escape") { e.preventDefault(); setFindOpen(false); box.current?.focus(); }
            }} />
          <Toggle on={opts.matchCase} label="Match case" onClick={() => setOpts((o) => ({ ...o, matchCase: !o.matchCase }))}>Aa</Toggle>
          <Toggle on={opts.word} label="Match whole word" onClick={() => setOpts((o) => ({ ...o, word: !o.word }))}><u>ab</u></Toggle>
          <Toggle on={opts.regex} label="Use regular expression" onClick={() => setOpts((o) => ({ ...o, regex: !o.regex }))}>.*</Toggle>
          <span className={`find-n${err || (q && !hits.length) ? " none" : ""}`} aria-live="polite" title={err ?? undefined}>
            {err ? "Bad regex" : !q ? "" : hits.length ? `${hitAt + 1} of ${hits.length.toLocaleString()}${hits.length >= 10_000 ? "+" : ""}` : "No results"}
          </span>
          <button type="button" className="ib" aria-label="Previous match" title="Previous match (Shift+Enter)" onClick={() => step(-1)} disabled={!hits.length}><Icon name="up" size={16} /></button>
          <button type="button" className="ib" aria-label="Next match" title="Next match (Enter)" onClick={() => step(1)} disabled={!hits.length}><Icon name="down" size={16} /></button>
          <button type="button" className="ib" aria-label="Close find" title="Close (Escape)" onClick={() => { setFindOpen(false); box.current?.focus(); }}><Icon name="close" size={16} /></button>
        </div>
      )}
      {gotoOpen && (
        <div className="goto" role="dialog" aria-label="Go to line">
          <input type="text" autoFocus aria-label="Go to line" inputMode="numeric" placeholder={`Go to line 1–${lines.length.toLocaleString()} (line:column)`}
            value={gotoText} onChange={(e) => setGotoText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); go(); }
              if (e.key === "Escape") { e.preventDefault(); setGotoOpen(false); box.current?.focus(); }
            }}
            onBlur={() => setGotoOpen(false)} />
          <span className="hint">{parseGoto(gotoText, lines.length) ? `Press Enter to go to line ${parseGoto(gotoText, lines.length)!.line}` : "Type a line number"}</span>
        </div>
      )}
      <div ref={box} className={`cv${wrapOn ? " wrap" : ""}`} tabIndex={0} role="region" aria-label={`Code of ${path}`}
        onScroll={virtual ? measure : undefined} onMouseUp={onMouseUp} style={{ ["--gd" as string]: `${Math.max(3, digits)}ch` }}
        data-lines={lines.length}>
        <div className="cv-lines" style={virtual ? { paddingTop: from * LH, paddingBottom: (lines.length - to) * LH, minWidth: `calc(${Math.max(3, digits)}ch + ${maxCols}ch + 64px)` } : undefined}>
          {rows}
        </div>
      </div>
    </div>
  );
}

function Toggle({ on, label, onClick, children }: { on: boolean; label: string; onClick: () => void; children: ReactNode }) {
  return <button type="button" className={`ib tg${on ? " on" : ""}`} aria-pressed={on} aria-label={label} title={label} onClick={onClick}>{children}</button>;
}

// ---------- diffs ----------

export type DiffMode = "inline" | "split";
const DIFF_ROWS = 6000;

function cell(r: DiffRow | undefined, lang: string | null) {
  if (!r) return "";
  return highlight(r.text, lang) ?? escapeHtml(r.text);
}

export function DiffPane({ pid, path, against, sha, mode }: { pid: string; path: string; against: "head" | "base"; sha?: string; mode: DiffMode }) {
  const d = useLoad(`diff:${pid}:${path}:${sha ?? against}`, () => api.diff(pid, path, against, sha));
  const parsed = useMemo(() => (d.data ? parseDiff(d.data.diff) : null), [d.data]);
  const lang = languageOf(path);
  if (d.error) return <div className="ed-note"><ErrorBox error={d.error} /></div>;
  if (!d.data || !parsed) return <div className="ed-note"><span className="pg-spin" role="status">Reading the changes…</span></div>;
  const head = (
    <div className="dv-head">
      <span>Changes to <b className="mono">{path.split("/").pop()}</b> against <b className="mono">{d.data.ref}</b></span>
      <span className="dv-stat"><span className="add">+{parsed.added}</span> <span className="del">−{parsed.removed}</span></span>
    </div>
  );
  if (d.data.binary) return <div className="dv">{head}<div className="ed-note">A binary file changed. keel does not show binary diffs.</div></div>;
  if (!parsed.rows.length) return <div className="dv">{head}<div className="ed-note">No changes against {d.data.ref}.</div></div>;
  const rows = parsed.rows.slice(0, DIFF_ROWS);
  const more = parsed.rows.length - rows.length;
  return (
    <div className={`dv ${mode}`}>
      {head}
      <div className="dv-body" tabIndex={0} role="region" aria-label={`Changes in ${path}`} data-mode={mode}>
        {mode === "inline" ? rows.map((r, i) => (
          r.kind === "hunk" || r.kind === "meta"
            ? <div key={i} className={`dv-row ${r.kind}`}><span className="dv-hunk">{r.text}</span></div>
            : <div key={i} className={`dv-row ${r.kind}`} data-kind={r.kind}>
              <span className="dv-ln">{r.old ?? ""}</span><span className="dv-ln">{r.new ?? ""}</span>
              <span className="dv-sg" aria-hidden="true">{r.kind === "add" ? "+" : r.kind === "del" ? "−" : ""}</span>
              <code className="dv-code" dangerouslySetInnerHTML={{ __html: cell(r, lang) }} />
            </div>
        )) : splitRows(rows).map((s, i) => (
          s.kind === "hunk"
            ? <div key={i} className="dv-srow hunk"><span className="dv-hunk">{s.text}</span></div>
            : <div key={i} className="dv-srow">
              <span className={`dv-ln ${s.left?.kind ?? "none"}`}>{s.left?.old ?? ""}</span>
              <code className={`dv-code ${s.left?.kind ?? "none"}`} data-side="old" dangerouslySetInnerHTML={{ __html: cell(s.left, lang) }} />
              <span className={`dv-ln ${s.right?.kind ?? "none"}`}>{s.right?.new ?? ""}</span>
              <code className={`dv-code ${s.right?.kind ?? "none"}`} data-side="new" dangerouslySetInnerHTML={{ __html: cell(s.right, lang) }} />
            </div>
        ))}
        {(more > 0 || d.data.truncated) && <div className="ed-note">The diff is long: keel shows the first {rows.length.toLocaleString()} lines.</div>}
      </div>
    </div>
  );
}

// ---------- images, Markdown, binary and big files ----------

export function ImagePane({ pid, path, size, onDims }: { pid: string; path: string; size: number; onDims: (d: string) => void }) {
  return (
    <div className="img-pane">
      <img src={api.rawUrl(pid, path)} alt={path} onLoad={(e) => onDims(`${e.currentTarget.naturalWidth} × ${e.currentTarget.naturalHeight}`)} />
      <span className="hint">{bytes(size)}</span>
    </div>
  );
}

export function MarkdownPane({ text }: { text: string }) {
  return <div className="md-pane"><div className="md wdoc"><Markdown text={text} fold={0} /></div></div>;
}

export function Notice({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="ed-note ed-notice"><b>{title}</b>{children && <span className="sub">{children}</span>}</div>;
}

/** What the editor can show for a file: text, an image, or a notice (binary, too big). */
export function kindOf(meta: RepoFile): "text" | "image" | "binary" | "big" {
  if (isImage(meta.path) && meta.size <= 10 * 1024 * 1024) return "image";
  if (meta.binary) return "binary";
  if (meta.size > TEXT_MAX) return "big";
  return "text";
}

export const canPreview = (path: string) => ["md", "markdown", "mdx"].includes(extOf(path));
