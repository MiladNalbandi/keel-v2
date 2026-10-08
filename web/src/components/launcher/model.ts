// v0.15.0 the launcher's logic, without React: what a query means (scope, prefix, file:line), how a text matches it,
// which results show in which group and order, and the recent results remembered per browser.

export type Kind =
  | "wait"
  | "pr"
  | "file"
  | "symbol"
  | "task"
  | "flow"
  | "page"
  | "action"
  | "project";
export type Scope =
  "all" | "files" | "code" | "prs" | "tasks" | "flows" | "actions";

/** The scope chips, in Tab order, and the kinds each one shows. */
export const SCOPES: { id: Scope; label: string; kinds: Kind[] }[] = [
  {
    id: "all",
    label: "All",
    kinds: [
      "wait",
      "pr",
      "file",
      "symbol",
      "task",
      "flow",
      "page",
      "action",
      "project",
    ],
  },
  { id: "files", label: "Files", kinds: ["file"] },
  { id: "code", label: "Code", kinds: ["symbol"] },
  { id: "prs", label: "Pull requests", kinds: ["pr"] },
  { id: "tasks", label: "Tasks", kinds: ["task"] },
  { id: "flows", label: "Flows", kinds: ["wait", "flow"] },
  { id: "actions", label: "Actions", kinds: ["action", "page", "project"] },
];

/** A prefix typed first narrows the search: > actions, @ code, # pull requests and tasks, ! flows and gates, ? ask. */
export const PREFIXES: Record<string, { label: string; kinds: Kind[] | null }> =
  {
    ">": { label: "Actions", kinds: ["action", "page", "project"] },
    "@": { label: "Code", kinds: ["symbol"] },
    "#": { label: "Pull requests and tasks", kinds: ["pr", "task"] },
    "!": { label: "Flows and gates", kinds: ["wait", "flow"] },
    "?": { label: "Ask keel", kinds: null },
  };

/** Group titles, in the order they show. */
export const GROUPS: { kind: Kind; label: string }[] = [
  { kind: "wait", label: "Waiting for you" },
  { kind: "pr", label: "Pull requests" },
  { kind: "file", label: "Files" },
  { kind: "symbol", label: "Code" },
  { kind: "task", label: "Tasks" },
  { kind: "flow", label: "Flows" },
  { kind: "page", label: "Pages" },
  { kind: "action", label: "Actions" },
  { kind: "project", label: "Projects" },
];

export type Query = {
  /** the text to match, without the prefix and the :line */
  text: string;
  prefix: string | null;
  /** "Prefs.kt:42": a line to open the file at */
  line: number | null;
  /** the kinds to show (scope and prefix together) */
  kinds: Kind[];
  ask: boolean;
};

export function parseQuery(raw: string, scope: Scope): Query {
  let text = raw.trimStart();
  let prefix: string | null = null;
  if (text && PREFIXES[text[0]]) {
    prefix = text[0];
    text = text.slice(1).trimStart();
  }
  const ask = prefix === "?";
  let line: number | null = null;
  const m = text.match(/^(\S+?):(\d+)$/);
  if (m) {
    text = m[1];
    line = Number(m[2]);
  }
  const scoped = SCOPES.find((s) => s.id === scope)!.kinds;
  const fromPrefix = prefix ? PREFIXES[prefix].kinds : null;
  const kinds = fromPrefix
    ? scoped.filter((k) => fromPrefix.includes(k))
    : scoped;
  return {
    text: text.trim(),
    prefix,
    line,
    kinds: line ? kinds.filter((k) => k === "file") : kinds,
    ask,
  };
}

/** How well `text` matches the query: every word must be in it (a single word may also match letter by letter).
 *  null = no match; `hits` = the matched letters, for bold. */
export function match(
  text: string,
  query: string,
): { score: number; hits: number[] } | null {
  const q = query.trim().toLowerCase();
  if (!q) return { score: 0, hits: [] };
  const t = text.toLowerCase();
  const words = q.split(/\s+/);
  const hits: number[] = [];
  let score = 0;
  for (const w of words) {
    let at = -1;
    // prefer a word start (savePageSize: "page" at the P, "#7 Save the page" at "page")
    for (let i = t.indexOf(w); i >= 0; i = t.indexOf(w, i + 1)) {
      if (
        i === 0 ||
        !/[a-z0-9]/.test(t[i - 1]) ||
        (text[i] !== t[i] && text[i - 1] === t[i - 1])
      ) {
        at = i;
        break;
      }
      if (at < 0) at = i;
    }
    if (at < 0) {
      if (words.length > 1) return null;
      const fuzzy = subsequence(t, w);
      if (!fuzzy) return null;
      return {
        score: 10 - Math.min(9, fuzzy[fuzzy.length - 1] - fuzzy[0] - w.length),
        hits: fuzzy,
      };
    }
    for (let i = 0; i < w.length; i++) hits.push(at + i);
    const start =
      at === 0 ||
      !/[a-z0-9]/.test(t[at - 1]) ||
      (text[at] !== t[at] && text[at - 1] === t[at - 1]);
    score += 50 + (at === 0 ? 30 : 0) + (start ? 15 : 0) - Math.min(20, at);
  }
  score += Math.max(0, 20 - (t.length - q.length) / 4);
  return { score, hits };
}

function subsequence(t: string, w: string): number[] | null {
  const out: number[] = [];
  let j = 0;
  for (let i = 0; i < t.length && j < w.length; i++)
    if (t[i] === w[j]) {
      out.push(i);
      j++;
    }
  return j === w.length ? out : null;
}

// ---------- results

export type Action = {
  id: string;
  label: string;
  /** its key in the launcher, as a chord ("enter", "meta+shift+c"); shown with keyLabel */
  keys?: string;
  /** why it cannot run now (shown instead of running) */
  disabled?: string;
  /** asks once more in the panel before it runs ("Check out feat/paging? Your branch changes.") */
  confirm?: string;
  run: () => void | Promise<unknown>;
};

export type Preview =
  | { kind: "code"; path: string; line?: number | null }
  | {
      kind: "text";
      title: string;
      lines: [string, string][];
      body?: string | null;
    };

export type Item = {
  id: string;
  kind: Kind;
  title: string;
  /** the right side: where, who, how many */
  sub?: string;
  /** a second line under the title (a file's folder) */
  detail?: string;
  mono?: boolean;
  /** a key hint on the right (an action's own shortcut, as a chord) */
  keys?: string;
  /** a small tag before the title (a symbol's kind: method, class) */
  badge?: string;
  /** the first one runs on ↩; ⌘K shows them all */
  actions: Action[];
  /** ⌘↩: the question KeelBot gets about this result */
  ask?: string;
  /** ⌘⇧C copies this */
  copy?: string;
  preview?: Preview;
  /** a base score (higher first); results already matched elsewhere (files, code) come with theirs */
  score?: number;
  hits?: number[];
  /** already matched against this query (files by path, code by the code graph): kept as they are */
  ranked?: boolean;
};

export type Group = { label: string; items: Item[] };

/** Rank every result for the query into groups: the best one first ("Best match"), then each kind in GROUPS order,
 *  `per` results each (all of them in one scope). */
export function rank(items: Item[], q: Query, per: number): Group[] {
  const wanted = items.filter((i) => q.kinds.includes(i.kind));
  const scored: Item[] = [];
  for (const it of wanted) {
    if (it.ranked) {
      scored.push({
        ...it,
        score: it.score ?? 0,
        hits: it.hits ?? match(it.title, q.text)?.hits,
      });
      continue;
    }
    const m = match(it.title, q.text);
    if (m)
      scored.push({ ...it, score: (it.score ?? 0) + m.score, hits: m.hits });
  }
  const groups: Group[] = [];
  const byKind = new Map<Kind, Item[]>();
  for (const it of scored)
    byKind.set(it.kind, [...(byKind.get(it.kind) ?? []), it]);
  for (const list of byKind.values())
    list.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  let best: Item | null = null;
  if (q.text && q.kinds.length > 1) {
    for (const it of scored)
      if (!best || (it.score ?? 0) > (best.score ?? 0)) best = it;
    if (best) groups.push({ label: "Best match", items: [best] });
  }
  for (const g of GROUPS) {
    const list = (byKind.get(g.kind) ?? [])
      .filter((it) => it !== best)
      .slice(0, per);
    if (list.length) groups.push({ label: g.label, items: list });
  }
  return groups;
}

/** The results in the order the list shows them (↑↓ and ⌘1–9 walk this). */
export const flat = (groups: Group[]): Item[] => groups.flatMap((g) => g.items);

/** The first row of the next (dir 1) or previous (-1) group, from row `at`. */
export function groupJump(groups: Group[], at: number, dir: 1 | -1): number {
  const starts: number[] = [];
  let n = 0;
  for (const g of groups) {
    starts.push(n);
    n += g.items.length;
  }
  if (!starts.length) return 0;
  if (dir === 1) return starts.find((s) => s > at) ?? starts[starts.length - 1];
  const before = starts.filter((s) => s < at);
  return before.length ? before[before.length - 1] : 0;
}

// ---------- recent results (per browser)

export type Recent = {
  id: string;
  kind: Kind;
  title: string;
  sub?: string;
  pid: string | null;
  at: number;
};
const RECENT_KEY = "keel2.launcher.recent";

export function readRecent(): Recent[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(v)
      ? v.filter((r) => r && typeof r.id === "string")
      : [];
  } catch {
    return [];
  }
}

export function remember(it: Item, pid: string | null) {
  if (it.kind === "wait") return;
  const own =
    it.kind === "page" || it.kind === "action" || it.kind === "project"
      ? null
      : pid;
  const list = [
    {
      id: it.id,
      kind: it.kind,
      title: it.title,
      sub: it.sub,
      pid: own,
      at: Date.now(),
    },
    ...readRecent().filter((r) => !(r.id === it.id && r.pid === own)),
  ].slice(0, 30);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  } catch {
    /* private window */
  }
}

/** The text with the matched letters marked: [text, hit] runs. */
export function runs(
  text: string,
  hits: number[] | undefined,
  offset = 0,
): [string, boolean][] {
  if (!hits?.length) return [[text, false]];
  const set = new Set(hits.map((h) => h - offset));
  const out: [string, boolean][] = [];
  for (let i = 0; i < text.length; i++) {
    const on = set.has(i);
    const last = out[out.length - 1];
    if (last && last[1] === on) last[0] += text[i];
    else out.push([text[i], on]);
  }
  return out;
}
