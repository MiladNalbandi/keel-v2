// A small, safe Markdown renderer for wiki pages and agent output (no HTML passthrough): headings,
// paragraphs, lists, fenced code (highlighted, folded when long), block quotes, tables, rules, and inline
// code / bold / italic / links, and GitHub's <details><summary> fold-outs (as in keel's PR bodies). A fence closes
// only on a plain fence of the same kind at least as long (a ``` block inside a ```` block stays text).
// `file:line` in backticks shows as a citation chip. With `breaks`, a single newline inside a paragraph stays a
// line break (agents write like that).

import { Fragment, type ReactNode } from "react";
import { CodeBlock, FOLD } from "./Code";

const CITE = /^[\w@./-]+\.[\w]+:\d+(-\d+)?$/;

function inline(text: string, key = 0): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\s][^*]*\*)|(_[^_\s][^_]*_)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = key * 1000;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (m[1]) {
      const c = t.slice(1, -1);
      out.push(CITE.test(c) ? <span key={k++} className="cite">{c}</span> : <code key={k++}>{c}</code>);
    } else if (m[2] || m[3]) out.push(<b key={k++}>{inline(t.slice(2, -2), k)}</b>);
    else if (m[4] || m[5]) out.push(<em key={k++}>{inline(t.slice(1, -1), k)}</em>);
    else if (m[6]) {
      const mm = t.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/)!;
      const href = /^(https?:|#|\/)/.test(mm[2]) ? mm[2] : undefined;
      out.push(href ? <a key={k++} href={href} target={href.startsWith("http") ? "_blank" : undefined} rel="noreferrer">{mm[1]}</a> : <span key={k++} className="mono">{mm[1]}</span>);
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (line: string) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

/** Lines joined by `<br>` (breaks) or by a space (classic Markdown). */
function joined(lines: string[], breaks: boolean, key: number): ReactNode[] {
  if (!breaks) return inline(lines.join(" "), key);
  return lines.flatMap((l, j) => (j ? [<br key={`br${j}`} />, ...inline(l, key + j)] : inline(l, key)));
}

const DETAILS = /^\s*<details>\s*(?:<summary>(.*?)<\/summary>)?\s*$/i;
const SUMMARY = /^\s*<summary>(.*?)<\/summary>\s*$/i;
const END_DETAILS = /^\s*<\/details>\s*$/i;

/** Does this line close a fence opened with `open` (``` or ~~~, n long)? Same char, at least as long, nothing after. */
const closes = (line: string, open: string) => {
  const t = line.trim();
  return t.length >= open.length && t === open[0].repeat(t.length);
};

export function Markdown({ text, breaks = false, fold = FOLD }: { text: string; breaks?: boolean; fold?: number }) {
  const lines = (text ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const det = line.match(DETAILS);
    if (det) {
      // <details><summary>…</summary> … </details>: a fold-out with Markdown inside (nested ones count)
      let summary = det[1] ?? "";
      const body: string[] = [];
      let depth = 1;
      i++;
      if (!summary && i < lines.length && SUMMARY.test(lines[i])) summary = lines[i++].match(SUMMARY)![1];
      let fenceOpen: string | null = null;
      while (i < lines.length) {
        const l = lines[i];
        const f = l.match(/^\s*(```+|~~~+)/);
        if (fenceOpen) { if (closes(l, fenceOpen)) fenceOpen = null; }
        else if (f) fenceOpen = f[1];
        else if (DETAILS.test(l)) depth++;
        else if (END_DETAILS.test(l) && --depth === 0) break;
        body.push(l);
        i++;
      }
      i++;
      blocks.push(
        <details key={key++} className="md-details">
          <summary>{inline(summary.replace(/<[^>]+>/g, "") || "Details", key)}</summary>
          <Markdown text={body.join("\n")} breaks={breaks} fold={fold} />
        </details>,
      );
      continue;
    }
    if (END_DETAILS.test(line) || SUMMARY.test(line)) { i++; continue; }
    const fence = line.match(/^\s*(```+|~~~+)\s*([\w+#.-]*)/);
    if (fence) {
      const body: string[] = [];
      const close = fence[1];
      i++;
      while (i < lines.length && !closes(lines[i], close)) body.push(lines[i++]);
      i++;
      blocks.push(<CodeBlock key={key++} className="md-code" text={body.join("\n")} lang={fence[2]} gutter={body.length > 3} fold={fold} />);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const level = Math.min(4, h[1].length);
      const Tag = `h${level}` as "h1" | "h2" | "h3" | "h4";
      blocks.push(<Tag key={key++}>{inline(h[2], key)}</Tag>);
      i++;
      continue;
    }
    if (/^(-{3,}|\*{3,})\s*$/.test(line)) { blocks.push(<hr key={key++} />); i++; continue; }
    if (line.trim().startsWith("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
      blocks.push(
        <div key={key++} className="table-wrap"><table>
          <thead><tr>{head.map((c, j) => <th key={j}>{inline(c, j)}</th>)}</tr></thead>
          <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c, ri * 50 + j)}</td>)}</tr>)}</tbody>
        </table></div>,
      );
      continue;
    }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      blocks.push(<blockquote key={key++}>{joined(body, breaks, key)}</blockquote>);
      continue;
    }
    const li = line.match(/^\s*([-*+]|\d+[.)])\s+/);
    if (li) {
      const ordered = /\d/.test(li[1]);
      const items: string[] = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
        if (m) items.push(m[2]);
        else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) items[items.length - 1] += " " + lines[i].trim();
        else break;
        i++;
      }
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={key++}>{items.map((t, j) => <li key={j}>{inline(t, j)}</li>)}</L>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*```|\s*~~~|\s*>|\s*([-*+]|\d+[.)])\s|\s*<\/?details>)/i.test(lines[i]) && !lines[i].trim().startsWith("|")) {
      para.push(lines[i++].trim());
    }
    if (!para.length) { para.push(lines[i++]); }
    blocks.push(<p key={key++}>{joined(para, breaks, key)}</p>);
  }
  return <Fragment>{blocks}</Fragment>;
}
