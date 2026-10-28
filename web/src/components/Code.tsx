// Code and diff views for agent steps and Markdown: syntax highlighting, line gutters, newlines kept,
// and long content folded after FOLD lines behind "Show all (N lines)".

import { useMemo, useState, type ReactNode } from "react";
import { highlight, languageOf, normLang, useLanguage } from "./highlight";

/** Lines shown before a long block folds. */
export const FOLD = 14;

/** Split into lines, keeping every newline (a trailing one does not make an empty last line). */
export function linesOf(text: string | undefined | null): string[] {
  const t = (text ?? "").replace(/\r\n?/g, "\n");
  if (!t) return [];
  return (t.endsWith("\n") ? t.slice(0, -1) : t).split("\n");
}

/** Folded when there are more than `fold` lines; folded, it shows the first `keep` (0.18.64: the AC gate folds a diff of
 *  over 30 lines to its first 12). */
function useFold(total: number, fold: number, keep = fold) {
  const [open, setOpen] = useState(false);
  const folded = fold > 0 && total > fold && !open;
  return { open, folded, shown: folded ? Math.min(keep, fold) : total, toggle: () => setOpen((o) => !o) };
}

/** The fold button's text while folded, from the total line count. */
export type FoldLabel = (total: number) => string;
const DEFAULT_LABEL: FoldLabel = (total) => `Show all (${total} lines)`;

export function FoldButton({ total, open, onToggle, label = DEFAULT_LABEL }: { total: number; open: boolean; onToggle: () => void; label?: FoldLabel }) {
  return (
    <button type="button" className="fold-btn" aria-expanded={open} onClick={onToggle}>
      {open ? "Show less" : label(total)}
    </button>
  );
}

/** A code block: highlighted by `lang` (a fence tag) or by the extension of `path`, with a line gutter. */
export function CodeBlock({ text, lang, path, gutter = true, fold = FOLD, className, start = 1, foldLabel }: {
  text: string;
  lang?: string | null;
  path?: string;
  gutter?: boolean;
  fold?: number;
  className?: string;
  start?: number;
  foldLabel?: FoldLabel;
}) {
  const all = useMemo(() => linesOf(text), [text]);
  const f = useFold(all.length, fold);
  const language = normLang(lang) ?? languageOf(path);
  const shown = f.folded ? all.slice(0, f.shown) : all;
  const code = shown.join("\n");
  const arrived = useLanguage(language);
  const html = useMemo(() => highlight(code, language), [code, language, arrived]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className={`code ${className ?? ""}`} data-lines={all.length}>
      <div className={`code-grid ${gutter ? "" : "no-gutter"}`}>
        {gutter && <div className="gutter" aria-hidden="true">{shown.map((_, i) => <span key={i}>{start + i}</span>)}</div>}
        {html !== null
          ? <pre className="code-pre"><code className={`hljs language-${language}`} dangerouslySetInnerHTML={{ __html: html }} /></pre>
          : <pre className="code-pre"><code>{code}</code></pre>}
      </div>
      {f.folded || f.open ? <FoldButton total={all.length} open={f.open} onToggle={f.toggle} label={foldLabel} /> : null}
    </div>
  );
}

export type DiffRow = { kind: "add" | "del" | "ctx" | "hunk" | "meta"; text: string; old?: number; new?: number };

/** Parse a unified diff (`---`/`+++` headers, `@@ -a,b +c,d @@` hunks) into rows with line numbers. */
export function parseDiff(diff: string): { rows: DiffRow[]; added: number; removed: number; files: string[] } {
  const rows: DiffRow[] = [];
  const files: string[] = [];
  let o = 1;
  let n = 1;
  let added = 0;
  let removed = 0;
  const all = linesOf(diff);
  let header = false;
  for (let i = 0; i < all.length; i++) {
    const line = all[i];
    // a file header is a `--- ` line followed by `+++ ` (a removed `-- comment` line inside a hunk is not one)
    const isHeader: boolean = (line.startsWith("--- ") && (all[i + 1] ?? "").startsWith("+++ ")) || (line.startsWith("+++ ") && header);
    header = line.startsWith("--- ") && isHeader;
    if (isHeader) {
      const f = line.slice(4).replace(/^[ab]\//, "").replace(/\t.*$/, "");
      if (f !== "/dev/null" && !files.includes(f)) files.push(f);
      continue;
    }
    if (/^(diff --git|index |new file mode|deleted file mode|similarity index|rename (from|to) )/.test(line)) continue;
    const h = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
    if (h) {
      o = Number(h[1]);
      n = Number(h[2]);
      rows.push({ kind: "hunk", text: line });
      continue;
    }
    if (line.startsWith("\\")) { rows.push({ kind: "meta", text: line }); continue; }
    if (line.startsWith("+")) { rows.push({ kind: "add", text: line.slice(1), new: n++ }); added++; continue; }
    if (line.startsWith("-")) { rows.push({ kind: "del", text: line.slice(1), old: o++ }); removed++; continue; }
    rows.push({ kind: "ctx", text: line.startsWith(" ") ? line.slice(1) : line, old: o++, new: n++ });
  }
  return { rows, added, removed, files };
}

export type SplitRow = { kind: "hunk"; text: string } | { kind: "pair"; left?: DiffRow; right?: DiffRow };

/** Unified diff rows → side-by-side rows: removed lines on the left next to the added lines that replace them. */
export function splitRows(rows: DiffRow[]): SplitRow[] {
  const out: SplitRow[] = [];
  let dels: DiffRow[] = [];
  let adds: DiffRow[] = [];
  const flush = () => {
    for (let i = 0; i < Math.max(dels.length, adds.length); i++) out.push({ kind: "pair", left: dels[i], right: adds[i] });
    dels = [];
    adds = [];
  };
  for (const r of rows) {
    if (r.kind === "del") {
      if (adds.length) flush();
      dels.push(r);
    } else if (r.kind === "add") {
      adds.push(r);
    } else if (r.kind === "ctx") {
      flush();
      out.push({ kind: "pair", left: r, right: r });
    } else {
      flush();
      out.push({ kind: "hunk", text: r.text });
    }
  }
  flush();
  return out;
}

const SIGN: Record<DiffRow["kind"], string> = { add: "+", del: "-", ctx: " ", hunk: "", meta: "" };

/** A unified diff: green added and red removed lines, old and new line numbers, highlighted by `path`. */
export function DiffView({ diff, path, fold = FOLD, keep, foldLabel, className }: {
  diff: string; path?: string; fold?: number; foldLabel?: FoldLabel; className?: string;
  /** the lines shown while folded (default: `fold`) */
  keep?: number;
}) {
  const parsed = useMemo(() => parseDiff(diff), [diff]);
  const f = useFold(parsed.rows.length, fold, keep);
  const language = languageOf(path ?? parsed.files[0]);
  useLanguage(language);
  const rows = f.folded ? parsed.rows.slice(0, f.shown) : parsed.rows;
  return (
    <div className={`code diffview ${className ?? ""}`} data-lines={parsed.rows.length}>
      <div className="diff-rows" role="table" aria-label={`Changes${path ? ` in ${path}` : ""}`}>
        {rows.map((r, i) => {
          if (r.kind === "hunk" || r.kind === "meta") return <div key={i} className={`dl ${r.kind}`} role="row"><span className="dtext">{r.text}</span></div>;
          const html = highlight(r.text, language);
          return (
            <div key={i} className={`dl ${r.kind}`} role="row" data-kind={r.kind}>
              <span className="ln" aria-hidden="true">{r.old ?? ""}</span>
              <span className="ln" aria-hidden="true">{r.new ?? ""}</span>
              <span className="sg" aria-hidden="true">{SIGN[r.kind]}</span>
              {html !== null ? <code className="dtext hljs" dangerouslySetInnerHTML={{ __html: html || " " }} /> : <code className="dtext">{r.text || " "}</code>}
            </div>
          );
        })}
      </div>
      {f.folded || f.open ? <FoldButton total={parsed.rows.length} open={f.open} onToggle={f.toggle} label={foldLabel} /> : null}
    </div>
  );
}

/** Plain text that keeps its newlines, folded after FOLD lines (tool output, errors). */
export function FoldedText({ text, fold = FOLD, className }: { text: string; fold?: number; className?: string }): ReactNode {
  const all = useMemo(() => linesOf(text), [text]);
  const f = useFold(all.length, fold);
  return (
    <div className={`code ${className ?? ""}`} data-lines={all.length}>
      <pre className="code-pre plain">{(f.folded ? all.slice(0, f.shown) : all).join("\n")}</pre>
      {f.folded || f.open ? <FoldButton total={all.length} open={f.open} onToggle={f.toggle} /> : null}
    </div>
  );
}
