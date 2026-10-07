// The Repo IDE's logic, kept free of React so it is easy to test: deep links, editor tabs (preview / pinned),
// fuzzy file matching, the explorer's rows, highlighted lines, find in file, and side-by-side diff rows.

import type { Change, TreeNode } from "../../api";
import type { DiffRow } from "../../components/Code";

// ---------- deep links: #/repo/<path>:<line> ----------

export type DeepLink = { path: string; line?: number };

/** "src/a.kt:12" → { path: "src/a.kt", line: 12 }; "src/a.kt" → { path }. */
export function parseDeepLink(arg: string | undefined | null): DeepLink | null {
  if (!arg) return null;
  const m = arg.match(/^(.*?)(?::(\d+))?$/);
  const path = (m?.[1] ?? arg).replace(/^\/+/, "");
  if (!path) return null;
  const line = m?.[2] ? Number(m[2]) : undefined;
  return line && line > 0 ? { path, line } : { path };
}

/** The hash for a file (and line): readable slashes, each name encoded. */
export function repoHash(path: string, line?: number): string {
  return `#/repo/${path.split("/").map(encodeURIComponent).join("/")}${line ? `:${line}` : ""}`;
}

// ---------- editor tabs ----------

export type TabKind = "file" | "commit" | "docs" | "memory" | "doctor" | "db";
export type View = "code" | "diff" | "preview";
export type EditorTab = { id: string; kind: TabKind; path: string; sha?: string; preview: boolean; view: View };
export type Tabs = { tabs: EditorTab[]; active: string | null };
export type OpenSpec = { kind?: TabKind; path: string; sha?: string; view?: View };

export const tabId = (s: OpenSpec) => (s.kind && s.kind !== "file"
  ? (s.kind === "commit" ? `commit:${s.sha}:${s.path}` : s.kind === "db" ? `db:${s.path}` : `keel:${s.kind}`) : s.path);

/**
 * Open a tab like VS Code: a single click opens a preview tab (italic) that the next single click replaces;
 * `pin` (a double click) keeps it. An open tab is only activated (and pinned when asked).
 */
export function openTab(t: Tabs, spec: OpenSpec, pin = false): Tabs {
  const id = tabId(spec);
  const have = t.tabs.find((x) => x.id === id);
  if (have) {
    return {
      tabs: t.tabs.map((x) => (x.id === id ? { ...x, preview: x.preview && !pin, view: spec.view ?? x.view } : x)),
      active: id,
    };
  }
  const tab: EditorTab = { id, kind: spec.kind ?? "file", path: spec.path, sha: spec.sha, preview: !pin, view: spec.view ?? "code" };
  const tabs = [...t.tabs];
  const prev = tabs.findIndex((x) => x.preview);
  if (!pin && prev >= 0) {
    tabs[prev] = tab;
  } else {
    const at = t.active ? tabs.findIndex((x) => x.id === t.active) : -1;
    tabs.splice(at >= 0 ? at + 1 : tabs.length, 0, tab);
  }
  return { tabs, active: id };
}

export const pinTab = (t: Tabs, id: string): Tabs => ({ ...t, tabs: t.tabs.map((x) => (x.id === id ? { ...x, preview: false } : x)) });

/** Close a tab; the one to its right (else left) becomes active. */
export function closeTab(t: Tabs, id: string): Tabs {
  const i = t.tabs.findIndex((x) => x.id === id);
  if (i < 0) return t;
  const tabs = t.tabs.filter((x) => x.id !== id);
  const active = t.active !== id ? t.active : (tabs[i] ?? tabs[i - 1])?.id ?? null;
  return { tabs, active };
}

export const setView = (t: Tabs, id: string, view: View): Tabs => ({ ...t, tabs: t.tabs.map((x) => (x.id === id ? { ...x, view } : x)) });

/** Point an open tab at something else in its place (a console moved to another database); an open tab for that wins. */
export function retargetTab(t: Tabs, id: string, spec: OpenSpec): Tabs {
  const next = tabId(spec);
  if (next === id || !t.tabs.some((x) => x.id === id)) return t;
  if (t.tabs.some((x) => x.id === next)) return { tabs: t.tabs.filter((x) => x.id !== id), active: next };
  return {
    tabs: t.tabs.map((x) => (x.id === id ? { ...x, id: next, kind: spec.kind ?? x.kind, path: spec.path, preview: false } : x)),
    active: t.active === id ? next : t.active,
  };
}

// ---------- fuzzy file matching (quick open) ----------

export type Fuzzy = { score: number; hits: number[] };

const isBoundary = (s: string, i: number) =>
  i === 0 || "/._- ".includes(s[i - 1]) || (s[i] >= "A" && s[i] <= "Z" && s[i - 1] >= "a" && s[i - 1] <= "z");

function subseq(path: string, q: string, from: number): Fuzzy | null {
  const low = path.toLowerCase();
  const hits: number[] = [];
  let score = 0;
  let at = from;
  for (const ch of q) {
    const i = low.indexOf(ch, at);
    if (i < 0) return null;
    const prev = hits[hits.length - 1];
    score += 1;
    if (prev !== undefined && i === prev + 1) score += 5;
    if (isBoundary(path, i)) score += 4;
    hits.push(i);
    at = i + 1;
  }
  return { score, hits };
}

/**
 * Match `query` against a path: the letters in order, anywhere. Matches in the file name, in a row and at word
 * starts score higher; shorter paths win ties. Null = no match.
 */
export function fuzzy(query: string, path: string): Fuzzy | null {
  const q = query.replace(/\s+/g, "").toLowerCase();
  if (!q) return { score: 0, hits: [] };
  const nameAt = path.lastIndexOf("/") + 1;
  const inName = q.includes("/") ? null : subseq(path, q, nameAt);
  const whole = subseq(path, q, 0);
  let best = inName ? { score: inName.score + 10 + (path.toLowerCase().startsWith(q, nameAt) ? 20 : 0), hits: inName.hits } : whole;
  if (inName && whole && whole.score > best!.score) best = whole;
  if (!best) return null;
  return { score: best.score - path.length / 100, hits: best.hits };
}

export function rankFiles(files: string[], query: string, limit = 50): { path: string; hits: number[] }[] {
  if (!query.trim()) return files.slice(0, limit).map((path) => ({ path, hits: [] }));
  const out: { path: string; hits: number[]; score: number }[] = [];
  for (const path of files) {
    const f = fuzzy(query, path);
    if (f) out.push({ path, hits: f.hits, score: f.score });
  }
  out.sort((a, b) => b.score - a.score || a.path.length - b.path.length);
  return out.slice(0, limit);
}

// ---------- explorer rows ----------

export const parentOf = (p: string) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
export const nameOf = (p: string) => p.slice(p.lastIndexOf("/") + 1);
export const depthOf = (p: string) => p.split("/").length - 1;

/** Children by parent path ("" = the root), folders first, then by name. */
export function indexTree(nodes: TreeNode[]): Map<string, TreeNode[]> {
  const byPath = new Map<string, TreeNode>();
  for (const n of nodes) byPath.set(n.path, n);
  const idx = new Map<string, TreeNode[]>();
  for (const n of byPath.values()) {
    const p = parentOf(n.path);
    const list = idx.get(p);
    if (list) list.push(n);
    else idx.set(p, [n]);
  }
  for (const list of idx.values()) {
    list.sort((a, b) => (a.kind === b.kind ? a.name.toLowerCase().localeCompare(b.name.toLowerCase()) : a.kind === "dir" ? -1 : 1));
  }
  return idx;
}

export type Row = { node: TreeNode; depth: number; open: boolean };

/**
 * The rows the explorer shows: open folders' children, depth first. With a filter, only the files whose name
 * (or path, when the filter has a "/") contains it, and the folders on their way, all open.
 */
export function visibleRows(idx: Map<string, TreeNode[]>, expanded: Set<string>, filter = ""): Row[] {
  const out: Row[] = [];
  const f = filter.trim().toLowerCase();
  if (f) {
    const keep = new Set<string>();
    for (const list of idx.values()) {
      for (const n of list) {
        const hay = (f.includes("/") ? n.path : n.name).toLowerCase();
        if (n.kind === "file" && hay.includes(f)) {
          keep.add(n.path);
          for (let p = parentOf(n.path); p; p = parentOf(p)) keep.add(p);
        }
      }
    }
    const walk = (parent: string) => {
      for (const n of idx.get(parent) ?? []) {
        if (!keep.has(n.path)) continue;
        out.push({ node: n, depth: depthOf(n.path), open: n.kind === "dir" });
        if (n.kind === "dir") walk(n.path);
      }
    };
    walk("");
    return out;
  }
  const walk = (parent: string) => {
    for (const n of idx.get(parent) ?? []) {
      const open = n.kind === "dir" && expanded.has(n.path);
      out.push({ node: n, depth: depthOf(n.path), open });
      if (open) walk(n.path);
    }
  };
  walk("");
  return out;
}

/** Every folder above `path` (to open them when the explorer reveals a file). */
export function ancestors(path: string): string[] {
  const out: string[] = [];
  for (let p = parentOf(path); p; p = parentOf(p)) out.unshift(p);
  return out;
}

// ---------- git decorations ----------

export type Deco = { letter: string; tone: "add" | "mod" | "del" | "untracked" | "conflict" | "ren"; title: string };

/** The VS Code letter for a change: U untracked, A added, M modified, D deleted, R renamed, ! conflict. */
export function decoOf(c: Change | undefined): Deco | undefined {
  if (!c) return undefined;
  if (c.conflict) return { letter: "!", tone: "conflict", title: "Conflict" };
  if (c.untracked) return { letter: "U", tone: "untracked", title: "Untracked (new, not in git yet)" };
  const x = c.unstaged ?? c.staged;
  if (x === "D") return { letter: "D", tone: "del", title: "Deleted" };
  if (c.staged === "A") return { letter: "A", tone: "add", title: "Added" };
  if (c.staged === "R" || c.staged === "C") return { letter: "R", tone: "ren", title: `Renamed from ${c.from ?? "another file"}` };
  return { letter: "M", tone: "mod", title: c.staged && c.unstaged ? "Modified (staged and changed again)" : c.staged ? "Modified (staged)" : "Modified" };
}

/** Folders that hold a change (their own colour in the explorer, like VS Code). */
export function changedFolders(changes: Change[]): Map<string, Deco["tone"]> {
  const out = new Map<string, Deco["tone"]>();
  const rank: Record<Deco["tone"], number> = { conflict: 5, mod: 4, del: 3, ren: 2, add: 1, untracked: 0 };
  for (const c of changes) {
    const d = decoOf(c);
    if (!d) continue;
    for (let p = parentOf(c.path); p; p = parentOf(p)) {
      const have = out.get(p);
      if (!have || rank[d.tone] > rank[have]) out.set(p, d.tone);
    }
  }
  return out;
}

// ---------- highlighted lines ----------

const TOKEN = /(<[^>]*>|\n)/;

/** hljs HTML → one HTML string per line: spans that cross a newline are closed and opened again. */
export function splitHtmlLines(html: string): string[] {
  const out: string[] = [];
  const open: string[] = [];
  let cur = "";
  for (const tok of html.split(TOKEN)) {
    if (!tok) continue;
    if (tok === "\n") {
      out.push(cur + "</span>".repeat(open.length));
      cur = open.join("");
    } else if (tok[0] === "<") {
      if (tok[1] === "/") open.pop();
      else if (!tok.endsWith("/>")) open.push(tok);
      cur += tok;
    } else {
      cur += tok;
    }
  }
  out.push(cur);
  return out;
}

export const escapeHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/**
 * Wrap text ranges of a line's HTML in `<mark class=…>` (find matches, the search hit). Offsets count text
 * characters (an entity is one); a mark is closed and opened again around tags so the HTML stays nested.
 */
export function markHtml(html: string, ranges: [number, number, string][]): string {
  if (!ranges.length) return html;
  const rs = [...ranges].sort((a, b) => a[0] - b[0]);
  let out = "";
  let pos = 0;
  let ri = 0;
  let openEnd = -1;
  let cls = "";
  const step = (unit: string) => {
    if (openEnd >= 0 && pos >= openEnd) { out += "</mark>"; openEnd = -1; }
    while (ri < rs.length && rs[ri][1] <= pos) ri++;
    if (openEnd < 0 && ri < rs.length && pos >= rs[ri][0]) {
      cls = rs[ri][2];
      out += `<mark class="${cls}">`;
      openEnd = rs[ri][1];
    }
    out += unit;
    pos++;
  };
  for (const tok of html.split(/(<[^>]*>|&[#\w]+;)/)) {
    if (!tok) continue;
    if (tok[0] === "<") {
      if (openEnd >= 0 && pos >= openEnd) { out += "</mark>"; openEnd = -1; }
      out += openEnd >= 0 ? `</mark>${tok}<mark class="${cls}">` : tok;
    } else if (tok[0] === "&" && tok.endsWith(";") && tok.length > 2) {
      step(tok);
    } else {
      for (let i = 0; i < tok.length; i++) step(tok[i]);
    }
  }
  if (openEnd >= 0) out += "</mark>";
  return out;
}

// ---------- find in file ----------

export type FindOpts = { matchCase: boolean; word: boolean; regex: boolean };
export type Hit = { line: number; start: number; end: number };

/** The search as a global RegExp, or an error to show (a bad regex). */
export function findRegExp(q: string, o: FindOpts): RegExp | { error: string } | null {
  if (!q) return null;
  let body = o.regex ? q : q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (o.word) body = `\\b(?:${body})\\b`;
  try {
    return new RegExp(body, o.matchCase ? "g" : "gi");
  } catch (e) {
    return { error: e instanceof Error ? e.message.replace(/^Invalid regular expression: /, "") : "not a valid regex" };
  }
}

/** Every match in the lines (1-based line numbers), at most `cap`. */
export function findAll(lines: string[], re: RegExp, cap = 10_000): Hit[] {
  const hits: Hit[] = [];
  for (let i = 0; i < lines.length && hits.length < cap; i++) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(lines[i])) && hits.length < cap) {
      if (m[0].length === 0) { re.lastIndex++; continue; }
      hits.push({ line: i + 1, start: m.index, end: m.index + m[0].length });
    }
  }
  return hits;
}

/** "12" or "12:5" → line and column (clamped to the file), else null. */
export function parseGoto(s: string, lines: number): { line: number; col: number } | null {
  const m = s.trim().match(/^:?(\d+)(?:[:,](\d+))?$/);
  if (!m) return null;
  return { line: Math.min(Math.max(1, Number(m[1])), Math.max(1, lines)), col: Math.max(1, Number(m[2] ?? 1)) };
}

// ---------- side-by-side diff ----------

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

// ---------- small helpers ----------

/** "1.4 MB", "812 bytes". */
export function bytes(n: number): string {
  if (n < 1024) return `${n} bytes`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** The AC id a commit message names (feat(AC-002): …), if any. */
export const acOf = (msg: string) => msg.match(/\bAC-\d+\b/)?.[0];

/** A GitHub (or GitLab) web link for a file, from the remote URL; null for other hosts. */
export function webUrl(remote: string | null | undefined, branch: string | null | undefined, path: string, line?: number): string | null {
  if (!remote || !branch) return null;
  const m = remote.trim().match(/^(?:https?:\/\/|git@)(github\.com|gitlab\.com)[:/](.+?)(?:\.git)?\/?$/);
  if (!m) return null;
  const sep = m[1] === "gitlab.com" ? "/-/blob/" : "/blob/";
  return `https://${m[1]}/${m[2]}${sep}${branch.split("/").map(encodeURIComponent).join("/")}/${path.split("/").map(encodeURIComponent).join("/")}${line ? `#L${line}` : ""}`;
}
