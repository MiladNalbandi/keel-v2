"""A small, safe Markdown to HTML for keel Product's presentation: headings, lists, tables, paragraphs, **bold**,
*italic* and `code`. Everything else is text; all text is escaped first."""

from __future__ import annotations

import html
import re


def inline(text: str) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![*\w])\*([^*\s][^*]*)\*(?!\w)", r"<i>\1</i>", t)
    return t


def to_html(text: str, base_level: int = 3) -> str:
    out: list[str] = []
    lines = (text or "").replace("\r\n", "\n").split("\n")
    i = 0
    para: list[str] = []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(p.strip() for p in para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush()
            level = min(6, base_level + len(m.group(1)) - 1)
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip()):
            flush()
            head = [c.strip() for c in s.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><tr>" + "".join(f"<th>{inline(h)}</th>" for h in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows) + "</table>")
            continue
        if re.match(r"^([-*]|\d+\.)\s+", s):
            flush()
            ordered = bool(re.match(r"^\d+\.", s))
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        para.append(s)
        i += 1
    flush()
    return "\n".join(out)


def sections(text: str) -> list[tuple[str, str]]:
    """(title, body) per '## ' section of a Markdown document; the text before the first one has the title ''."""
    parts: list[tuple[str, list[str]]] = [("", [])]
    for line in (text or "").splitlines():
        m = re.match(r"^#{1,3}\s+(.*)$", line.strip())
        if m:
            parts.append((m.group(1).strip(), []))
        else:
            parts[-1][1].append(line)
    return [(t, "\n".join(b).strip()) for t, b in parts if t or "\n".join(b).strip()]
