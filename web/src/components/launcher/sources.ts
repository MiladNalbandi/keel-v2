// v0.15.0 the launcher's results: what keel already knows (the inbox, flows, pages, actions, projects) and what the
// parts add (slot launcher.source: pull requests, files, code, tasks) as results with their actions. Loaded when the
// launcher opens, kept 30 s per project.

import {
  api,
  errorParts,
  type BoardFlow,
  type GraphHit,
  type Project,
} from "../../api";
import type { NavGroup } from "../../addons";
import { inboxApi, type InboxItem } from "../../inboxApi";
import { hashFor, hashForScreen } from "../../routes";
import { slotItems } from "../../sdk/registry";
import { SLOTS, type LauncherSourceItem } from "../../sdk/slots";
import type { Action, Item, Kind, Query, Recent, Scope } from "./model";

export type Data = {
  inbox: InboxItem[] | null;
  flows: BoardFlow[] | null;
  /** what each part's source read, by its id (null when it read nothing) */
  parts: Record<string, unknown>;
  /** what could not be read, one line each */
  notes: string[];
};

export const emptyData = (): Data => ({
  inbox: null,
  flows: null,
  parts: {},
  notes: [],
});

/** The parts' sources, in their order (pull requests, files and code, tasks). */
const sources = () => slotItems<LauncherSourceItem>(SLOTS.launcherSource);

/** How a source reports what it could not read: `failed("Files")` for an error, `add(line)` for any line. */
export type LauncherNotes = {
  failed: (what: string) => (e: unknown) => null;
  add: (line: string) => void;
};

// the loading itself is kept too, so two opens at once (React's dev mode) ask the api once
let cache: { pid: string | null; at: number; data: Promise<Data> } | null = null;

/** Tests: forget what was loaded. */
export function resetLauncherCache() {
  cache = null;
}

/** Everything the launcher searches, for this project (the inbox spans all projects). */
export function load(pid: string | null, fresh = false): Promise<Data> {
  if (!fresh && cache && cache.pid === pid && Date.now() - cache.at < 30_000)
    return cache.data;
  const data = fetchAll(pid);
  cache = { pid, at: Date.now(), data };
  return data;
}

async function fetchAll(pid: string | null): Promise<Data> {
  const data = emptyData();
  const note = (what: string) => (e: unknown) => {
    data.notes.push(`${what}: ${errorParts(e).message}`);
    return null;
  };
  const notes: LauncherNotes = {
    failed: note,
    add: (line) => void data.notes.push(line),
  };
  const inbox = inboxApi
    .list()
    .then((v) => (data.inbox = v.items), note("Inbox"));
  if (!pid) {
    await inbox;
  } else {
    await Promise.all([
      inbox,
      ...sources().map((s) =>
        s.load
          ? s.load(pid, notes).then(
              (d) => void (data.parts[s.id] = d ?? null),
              note(s.title ?? s.id),
            )
          : null,
      ),
      api.flowBoard(pid).then((v) => (data.flows = v.flows), note("Flows")),
    ]);
  }
  return data;
}

/** The link that opens a file at a line, from the part that shows files (Code); null when none does. */
export function fileHash(path: string, line?: number): string | null {
  const s = sources().find((x) => x.fileHash);
  return s?.fileHash ? s.fileHash(path, line) : null;
}

/** Code (the code graph's search) for this text; [] when the graph is not there. */
export async function symbols(pid: string, text: string): Promise<GraphHit[]> {
  try {
    const r = await api.graphSearch(pid, text);
    return r.available ? r.results : [];
  } catch {
    return [];
  }
}

// ---------- what the actions do

export type Ctx = {
  pid: string | null;
  projects: Project[];
  setProjectId: (id: string) => void;
  toast: (text: string) => void;
  /** close the launcher (after an action that goes somewhere) */
  close: () => void;
  /** switch to Ask with this question in the field */
  ask: (question: string) => void;
  setScope: (s: Scope) => void;
  dark: boolean;
  toggleTheme: () => void;
  toggleNav: () => void;
  openNotes: () => void;
  nav: NavGroup[];
};

/** An action that goes to a link (and closes the launcher). */
export const goHash = (ctx: Ctx, hash: string) => () => {
  ctx.close();
  if (location.hash !== hash) location.hash = hash;
};

export async function copyText(ctx: Ctx, text: string) {
  try {
    await navigator.clipboard.writeText(text);
    ctx.toast(`Copied ${text.length > 60 ? text.slice(0, 57) + "…" : text}`);
  } catch {
    ctx.toast("The browser did not allow copying.");
  }
}

export const copyAction = (
  ctx: Ctx,
  text: string,
  label = "Copy",
): Action => ({
  id: "copy",
  label,
  keys: "meta+shift+c",
  run: () => copyText(ctx, text),
});
export const askAction = (
  ctx: Ctx,
  q: string,
  label = "Ask KeelBot about it",
): Action => ({
  id: "ask",
  label,
  keys: "meta+enter",
  disabled: ctx.pid ? undefined : "Choose a project first",
  run: () => ctx.ask(q),
});
export const linkAction = (label: string, url: string): Action => ({
  id: "web",
  label,
  keys: "meta+o",
  run: () => void window.open(url, "_blank", "noopener"),
});

// ---------- one result of each kind (keel's own; the parts make theirs)

function waitItem(ctx: Ctx, w: InboxItem): Item {
  const here = w.project_id === ctx.pid;
  const toFlow = () => {
    if (!here) ctx.setProjectId(w.project_id);
    goHash(ctx, hashFor("flow", w.thread_id))();
  };
  const plain =
    w.kind === "gate" &&
    w.options.includes("approve") &&
    !w.questions?.length &&
    !w.choices?.length &&
    !w.task &&
    !w.permission;
  return {
    id: `wait:${w.thread_id}:${w.id ?? w.kind}`,
    kind: "wait",
    title: w.title,
    sub: `${w.flow}${here ? "" : ` · ${w.project_name}`}`,
    actions: [
      { id: "open", label: "Open the flow", keys: "enter", run: toFlow },
      {
        id: "inbox",
        label: "Open in the Inbox",
        run: goHash(ctx, hashFor("inbox")),
      },
      ...(plain
        ? [
            {
              id: "approve",
              label: w.labels?.approve ?? "Approve",
              keys: "meta+shift+a",
              confirm: `${w.labels?.approve ?? "Approve"} “${w.title}” in ${w.flow}? The flow goes on.`,
              run: async () => {
                try {
                  await inboxApi.act(w.thread_id, {
                    decision: "approve",
                    id: w.id ?? undefined,
                  });
                  ctx.toast(`Approved: ${w.title}`);
                  ctx.close();
                } catch (e) {
                  ctx.toast(`Not approved: ${errorParts(e).message}`);
                }
              },
            } satisfies Action,
          ]
        : []),
      ...(here
        ? [
            askAction(
              ctx,
              `${w.flow}: ${w.title}. What should I check before I answer?`,
            ),
          ]
        : []),
    ],
    ask: here
      ? `${w.flow}: ${w.title}. What should I check before I answer?`
      : undefined,
    preview: {
      kind: "text",
      title: w.title,
      lines: [
        ["Flow", w.flow],
        ["Project", w.project_name],
        ...(w.phase ? [["Phase", w.phase] as [string, string]] : []),
        ...(w.since
          ? [
              ["Since", w.since.slice(0, 16).replace("T", " ")] as [
                string,
                string,
              ],
            ]
          : []),
      ],
      body: w.detail,
    },
    score: here ? 30 : 10,
  };
}


function flowItem(ctx: Ctx, f: BoardFlow): Item {
  const now = f.waiting
    ? `waits: ${f.waiting.title ?? f.waiting.kind ?? "you"}`
    : `${f.status}${f.phase ? ` · ${f.phase}` : ""}`;
  return {
    id: `flow:${f.thread_id}`,
    kind: "flow",
    title: f.title,
    sub: now,
    actions: [
      {
        id: "open",
        label: "Open the flow",
        keys: "enter",
        run: goHash(ctx, hashFor("flow", f.thread_id)),
      },
      askAction(
        ctx,
        `Where is the flow "${f.title}" now, and what does it wait for?`,
      ),
    ],
    ask: `Where is the flow "${f.title}" now, and what does it wait for?`,
    preview: {
      kind: "text",
      title: f.title,
      lines: [
        ["Status", f.status],
        ...(f.phase ? [["Phase", f.phase] as [string, string]] : []),
        [
          "Where",
          f.where === "worktree"
            ? `its own worktree${f.branch ? ` (${f.branch})` : ""}`
            : "the project folder",
        ],
        ["Files", String(f.files.length)],
      ],
    },
    score:
      f.status === "done" || f.status === "failed" || f.status === "stopped"
        ? -10
        : 5,
  };
}

/** keel's own actions (always there). */

export function actionItems(ctx: Ctx): Item[] {
  const a = (
    id: string,
    title: string,
    run: () => void,
    keys?: string,
    sub?: string,
  ): Item => ({
    id: `act:${id}`,
    kind: "action",
    title,
    keys,
    sub,
    actions: [{ id: "run", label: title, keys: "enter", run }],
  });
  const own = [
    a("ask", "Ask KeelBot…", () => ctx.ask(""), "?", "read only, answers here"),
    a("file", "Go to file…", () => ctx.setScope("files")),
    a(
      "new-flow",
      "Start a new flow",
      goHash(ctx, hashFor("flow")),
      undefined,
      "Flow",
    ),
    a(
      "new-task",
      "Add a task",
      goHash(ctx, hashFor("tasks")),
      undefined,
      "Tasks",
    ),
    a(
      "theme",
      ctx.dark ? "Switch to the light theme" : "Switch to the dark theme",
      () => {
        ctx.toggleTheme();
        ctx.close();
      },
    ),
    a(
      "menu",
      "Show or hide the menu",
      () => {
        ctx.toggleNav();
        ctx.close();
      },
      "meta+\\",
    ),
    a("notes", "Open the notifications", () => {
      ctx.close();
      ctx.openNotes();
    }),
  ];
  // the parts' actions after keel's own (Code: Focus mode)
  return [...own, ...sources().flatMap((s) => s.actions?.(ctx) ?? [])];
}


function pageItems(ctx: Ctx): Item[] {
  const out: Item[] = [
    {
      id: "page:projects",
      kind: "page",
      title: "All projects",
      sub: "Projects",
      actions: [
        {
          id: "open",
          label: "Go to All projects",
          keys: "enter",
          run: goHash(ctx, hashFor("projects")),
        },
      ],
    },
  ];
  for (const g of ctx.nav)
    for (const p of g.pages) {
      const hash = p.addon ? hashForScreen(p.id) : hashFor(p.id);
      out.push({
        id: `page:${p.id}`,
        kind: "page",
        title: p.label,
        sub: g.label,
        actions: [
          {
            id: "open",
            label: `Go to ${p.label}`,
            keys: "enter",
            run: goHash(ctx, hash),
          },
        ],
      });
    }
  return out;
}

function projectItems(ctx: Ctx): Item[] {
  return ctx.projects
    .filter((p) => p.id !== ctx.pid)
    .map((p) => ({
      id: `proj:${p.id}`,
      kind: "project" as Kind,
      title: `Switch to ${p.name}`,
      sub: p.waiting ? `◆ ${p.waiting} waiting` : p.branch,
      actions: [
        {
          id: "open",
          label: `Switch to ${p.name}`,
          keys: "enter",
          run: () => {
            ctx.setProjectId(p.id);
            ctx.toast(`Now showing ${p.name}`);
            ctx.close();
          },
        },
      ],
    }));
}

/** Every result for this query (the ranking picks and orders them). The parts' results come between what waits and
 *  the flows (pull requests, files, code, tasks), in their sources' order. */
export function itemsFor(
  ctx: Ctx,
  data: Data,
  q: Query,
  code: GraphHit[],
  filesPer: number,
): Item[] {
  const out: Item[] = [];
  const want = (k: Kind) => q.kinds.includes(k);
  if (want("wait"))
    for (const w of data.inbox ?? []) out.push(waitItem(ctx, w));
  for (const s of sources())
    if (s.items)
      out.push(...s.items(ctx, data.parts[s.id] ?? null, q, { code, filesPer }));
  if (want("flow"))
    for (const f of data.flows ?? []) out.push(flowItem(ctx, f));
  if (want("page")) out.push(...pageItems(ctx));
  if (want("action")) out.push(...actionItems(ctx));
  if (want("project")) out.push(...projectItems(ctx));
  return out;
}

/** The empty launcher: what waits for you (this project's first, then what the parts add: pull requests to review),
 *  what you used last, and a few actions. */
export function startGroups(
  ctx: Ctx,
  data: Data,
  recent: Recent[],
): { label: string; items: Item[] }[] {
  const waits = (data.inbox ?? [])
    .map((w) => waitItem(ctx, w))
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    .slice(0, 4);
  const theirs = sources().flatMap(
    (s) => s.waiting?.(ctx, data.parts[s.id] ?? null) ?? [],
  );
  const all = [
    ...itemsFor(
      ctx,
      data,
      {
        text: "",
        prefix: null,
        line: null,
        ask: false,
        kinds: ["pr", "task", "flow", "page", "action", "project"],
      },
      [],
      0,
    ),
  ];
  const byId = new Map(all.map((i) => [i.id, i]));
  const mine = recent
    .filter((r) => r.pid === null || r.pid === ctx.pid)
    .flatMap((r) => {
      // a file or a piece of code is made again by its part (they are not in the list above)
      for (const s of sources()) {
        const again = s.recent?.(ctx, r);
        if (again) return [again];
      }
      if (r.kind === "file" || r.kind === "symbol") return [];
      const it = byId.get(r.id);
      return it ? [it] : [];
    })
    .slice(0, 5);
  // a quick action already under Recent is not shown twice
  const quick = actionItems(ctx).filter(
    (i) => ["act:ask", "act:file", "act:new-flow"].includes(i.id) && !mine.some((m) => m.id === i.id),
  );
  return [
    { label: "Waiting for you", items: [...waits, ...theirs] },
    { label: "Recent", items: mine },
    { label: "Quick actions", items: quick },
  ].filter((g) => g.items.length);
}
