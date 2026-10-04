// Code and diff views for agent steps and Markdown: syntax highlighting, line gutters, newlines kept,
// and long content folded after FOLD lines behind "Show all (N lines)".

import { useMemo, useState, type ReactNode } from "react";
import { highlight, languageOf, normLang } from "./highlight";

/** Lines shown before a long block folds. */
export const FOLD = 14;

/** Split into lines, keeping every newline (a trailing one does not make an empty last line). */
export function linesOf(text: string | undefined | null): string[] {
  const t = (text ?? "").replace(/\r\n?/g, "\n");
  if (!t) return [];
  return (t.endsWith("\n") ? t.slice(0, -1) : t).split("\n");
}

function useFold(total: number, fold: number) {
  const [open, setOpen] = useState(false);
  const folded = fold > 0 && total > fold && !open;
  return { open, folded, shown: folded ? fold : total, toggle: () => setOpen((o) => !o) };
}

export function FoldButton({ total, open, onToggle }: { total: number; open: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="fold-btn" aria-expanded={open} onClick={onToggle}>
      {open ? "Show less" : `Show all (${total} lines)`}
    </button>
  );
}

/** A code block: highlighted by `lang` (a fence tag) or by the extension of `path`, with a line gutter. */
export function CodeBlock({ text, lang, path, gutter = true, fold = FOLD, className, start = 1 }: {
  text: string;
  lang?: string | null;
  path?: string;
  gutter?: boolean;
  fold?: number;
  className?: string;
  start?: number;
}) {
  const all = useMemo(() => linesOf(text), [text]);
  const f = useFold(all.length, fold);
  const language = normLang(lang) ?? languageOf(path);
  const shown = f.folded ? all.slice(0, f.shown) : all;
  const code = shown.join("\n");
  const html = useMemo(() => highlight(code, language), [code, language]);
  return (
    <div className={`code ${className ?? ""}`} data-lines={all.length}>
      <div className={`code-grid ${gutter ? "" : "no-gutter"}`}>
        {gutter && <div className="gutter" aria-hidden="true">{shown.map((_, i) => <span key={i}>{start + i}</span>)}</div>}
        {html !== null
          ? <pre className="code-pre"><code className={`hljs language-${language}`} dangerouslySetInnerHTML={{ __html: html }} /></pre>
          : <pre className="code-pre"><code>{code}</code></pre>}
      </div>
      {f.folded || f.open ? <FoldButton total={all.length} open={f.open} onToggle={f.toggle} /> : null}
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
  for (const line of linesOf(diff)) {
    if (line.startsWith("+++ ") || line.startsWith("--- ")) {
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

const SIGN: Record<DiffRow["kind"], string> = { add: "+", del: "-", ctx: " ", hunk: "", meta: "" };

/** A unified diff: green added and red removed lines, old and new line numbers, highlighted by `path`. */
export function DiffView({ diff, path, fold = FOLD }: { diff: string; path?: string; fold?: number }) {
  const parsed = useMemo(() => parseDiff(diff), [diff]);
  const f = useFold(parsed.rows.length, fold);
  const language = languageOf(path ?? parsed.files[0]);
  const rows = f.folded ? parsed.rows.slice(0, f.shown) : parsed.rows;
  return (
    <div className="code diffview" data-lines={parsed.rows.length}>
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
      {f.folded || f.open ? <FoldButton total={parsed.rows.length} open={f.open} onToggle={f.toggle} /> : null}
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
