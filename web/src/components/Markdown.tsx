// A small, safe Markdown renderer for wiki pages (no HTML passthrough): headings, paragraphs, lists,
// fenced code, block quotes, tables, rules, and inline code / bold / italic / links. `file:line` in
// backticks shows as a citation chip.

import { Fragment, type ReactNode } from "react";

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

export function Markdown({ text }: { text: string }) {
  const lines = (text ?? "").replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = line.match(/^```(\w*)/);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push(<pre key={key++}><code>{body.join("\n")}</code></pre>);
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
      blocks.push(<blockquote key={key++}>{inline(body.join(" "), key)}</blockquote>);
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
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*>|\s*([-*+]|\d+[.)])\s)/.test(lines[i]) && !lines[i].trim().startsWith("|")) {
      para.push(lines[i++].trim());
    }
    if (!para.length) { para.push(lines[i++]); }
    blocks.push(<p key={key++}>{inline(para.join(" "), key)}</p>);
  }
  return <Fragment>{blocks}</Fragment>;
}
