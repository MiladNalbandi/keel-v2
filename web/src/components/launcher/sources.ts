// v0.15.0 the launcher's results: what keel already knows (the inbox, pull requests, files, code, tasks, flows, pages,
// actions, projects) as results with their actions. Loaded when the launcher opens, kept 30 s per project.

import {
  api,
  errorParts,
  type BoardFlow,
  type GraphHit,
  type Project,
} from "../../api";
import type { NavGroup } from "../../addons";
import { inboxApi, type InboxItem } from "../../inboxApi";
import { repoHash, reviewHash, rankFiles } from "../../pages/repo/model";
import { reviewApi, type PrSummary } from "../../reviewApi";
import { hashFor, hashForScreen, type ScreenId } from "../../routes";
import { STATUS_LABEL, tasksApi, type Task } from "../../tasksApi";
import type { Action, Item, Kind, Query, Recent, Scope } from "./model";

export type Data = {
  inbox: InboxItem[] | null;
  prs: PrSummary[] | null;
  /** the pull requests' host, for "Open on GitHub" */
  host: { kind: string; web: string } | null;
  files: string[] | null;
  tasks: Task[] | null;
  flows: BoardFlow[] | null;
  /** what could not be read, one line each */
  notes: string[];
};

const empty = (): Data => ({
  inbox: null,
  prs: null,
  host: null,
  files: null,
  tasks: null,
  flows: null,
  notes: [],
});
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
  const data = empty();
  const note = (what: string) => (e: unknown) => {
    data.notes.push(`${what}: ${errorParts(e).message}`);
    return null;
  };
  const inbox = inboxApi
    .list()
    .then((v) => (data.inbox = v.items), note("Inbox"));
  if (!pid) {
    await inbox;
  } else {
    const prs = api
      .plugins(pid)
      .then(async (plugins) => {
        if (!plugins.find((p) => p.name === "review")?.enabled) return;
        const list = await reviewApi.prs(pid, "all");
        data.prs = list.prs;
        data.host = list.host
          ? { kind: list.host.kind, web: list.host.web }
          : null;
        if (list.note) data.notes.push(`Pull requests: ${list.note}`);
      }, note("Plugins"))
      .catch(note("Pull requests"));
    await Promise.all([
      inbox,
      prs,
      api.repoFiles(pid).then((v) => (data.files = v.files), note("Files")),
      tasksApi.list(pid).then((v) => (data.tasks = v.tasks), note("Tasks")),
      api.flowBoard(pid).then((v) => (data.flows = v.flows), note("Flows")),
    ]);
  }
  return data;
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

const goHash = (ctx: Ctx, hash: string) => () => {
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

const copyAction = (ctx: Ctx, text: string, label = "Copy"): Action => ({
  id: "copy",
  label,
  keys: "meta+shift+c",
  run: () => copyText(ctx, text),
});
const askAction = (
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
const linkAction = (label: string, url: string): Action => ({
  id: "web",
  label,
  keys: "meta+o",
  run: () => void window.open(url, "_blank", "noopener"),
});

// ---------- one result of each kind

export function fileItem(
  ctx: Ctx,
  path: string,
  line: number | null,
  score?: number,
  hits?: number[],
): Item {
  const at = line ? `${path}:${line}` : path;
  const slash = path.lastIndexOf("/");
  return {
    id: `file:${path}`,
    kind: "file",
    title: path.slice(slash + 1),
    detail: slash > 0 ? path.slice(0, slash) : undefined,
    mono: true,
    sub: line ? `line ${line}` : undefined,
    actions: [
      {
        id: "open",
        label: line ? `Open at line ${line}` : "Open in Code",
        keys: "enter",
        run: goHash(ctx, repoHash(path, line ?? undefined)),
      },
      askAction(ctx, `Explain ${at}: what it does and how it is used.`),
      copyAction(ctx, at, "Copy the path"),
    ],
    ask: `Explain ${at}: what it does and how it is used.`,
    copy: at,
    preview: { kind: "code", path, line },
    score,
    // the hits are on the whole path; the title shows the name only
    hits: hits?.filter((h) => h > slash).map((h) => h - slash - 1),
    ranked: score !== undefined,
  };
}

export function symbolItem(ctx: Ctx, h: GraphHit, score: number): Item {
  const at = `${h.file}:${h.line}`;
  return {
    id: `sym:${h.file}:${h.line}:${h.name}`,
    kind: "symbol",
    title: h.name,
    sub: at,
    mono: true,
    badge: h.kind,
    actions: [
      {
        id: "open",
        label: "Go to the declaration",
        keys: "enter",
        run: goHash(ctx, repoHash(h.file, h.line)),
      },
      askAction(
        ctx,
        `Explain ${h.name} (${at}): what it does and who calls it.`,
      ),
      copyAction(ctx, at, "Copy file:line"),
    ],
    ask: `Explain ${h.name} (${at}): what it does and who calls it.`,
    copy: at,
    preview: { kind: "code", path: h.file, line: h.line },
    score,
    ranked: true,
  };
}

function prItem(ctx: Ctx, p: PrSummary, host: string | null): Item {
  const key = `pr:${p.number}`;
  const role = p.mine
    ? "yours"
    : p.review_requested
      ? "you review"
      : p.assigned
        ? "assigned to you"
        : p.author;
  const where = host === "gitlab" ? "GitLab" : "GitHub";
  return {
    id: key,
    kind: "pr",
    title: `#${p.number} ${p.title}`,
    sub: `${p.mine ? "" : p.author + " · "}${role === p.author ? p.branch : role}${p.draft ? " · draft" : ""}`,
    actions: [
      {
        id: "open",
        label: "Open the review",
        keys: "enter",
        run: goHash(ctx, reviewHash(key)),
      },
      askAction(
        ctx,
        `What does pull request #${p.number} "${p.title}" (branch ${p.branch}) change, and what should I check first?`,
        "Ask KeelBot what it changes",
      ),
      {
        id: "checkout",
        label: "Check out the branch",
        keys: "meta+shift+o",
        confirm: `Check out the branch of #${p.number} (${p.branch})? The files in your project folder change to it.`,
        run: async () => {
          try {
            const r = await reviewApi.checkout(ctx.pid!, key);
            ctx.toast(r.note || `On ${r.branch} now.`);
            ctx.close();
          } catch (e) {
            ctx.toast(`Not checked out: ${errorParts(e).message}`);
          }
        },
      },
      ...(p.url
        ? [
            copyAction(ctx, p.url, "Copy the link"),
            linkAction(`Open on ${where}`, p.url),
          ]
        : []),
    ],
    ask: `What does pull request #${p.number} "${p.title}" (branch ${p.branch}) change, and what should I check first?`,
    copy: p.url || `#${p.number}`,
    preview: {
      kind: "text",
      title: `#${p.number} ${p.title}`,
      lines: [
        ["Author", p.mine ? `${p.author} (you)` : p.author],
        ["Branch", `${p.branch} → ${p.base}`],
        [
          "You",
          p.mine
            ? "wrote it: you can merge it"
            : p.review_requested
              ? "are asked to review"
              : p.assigned
                ? "are assigned"
                : "are not asked",
        ],
        ...(p.draft ? [["State", "draft"] as [string, string]] : []),
      ],
    },
  };
}

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

function taskItem(ctx: Ctx, t: Task): Item {
  return {
    id: `task:${t.id}`,
    kind: "task",
    // the Jira key first: typing ABC-12 finds it
    title: t.external_key ? `${t.external_key} ${t.title}` : t.title,
    sub: STATUS_LABEL[t.status] ?? t.status,
    actions: [
      {
        id: "open",
        label: "Open the task",
        keys: "enter",
        run: goHash(ctx, hashFor("tasks", t.id)),
      },
      askAction(
        ctx,
        `Task${t.external_key ? " " + t.external_key : ""}: ${t.title}. Where in the code would this change go?`,
      ),
      ...(t.external_url ? [linkAction("Open in Jira", t.external_url)] : []),
      copyAction(
        ctx,
        t.external_key ?? t.title,
        t.external_key ? "Copy the key" : "Copy the title",
      ),
    ],
    ask: `Task${t.external_key ? " " + t.external_key : ""}: ${t.title}. Where in the code would this change go?`,
    copy: t.external_key ?? t.title,
    preview: {
      kind: "text",
      title: t.title,
      lines: [
        ["Status", STATUS_LABEL[t.status] ?? t.status],
        ["Type", t.type],
        ...(t.external_key
          ? [["Jira", t.external_key] as [string, string]]
          : []),
        ...(t.flow
          ? [
              [
                "Flow",
                `${t.flow.status}${t.flow.phase ? ` · ${t.flow.phase}` : ""}`,
              ] as [string, string],
            ]
          : []),
      ],
      body: t.description,
    },
    score: t.status === "done" || t.status === "cancelled" ? -20 : 0,
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
  return [
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
      const hash = p.addon ? hashForScreen(p.id) : hashFor(p.id as ScreenId);
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

/** Every result for this query (the ranking picks and orders them). Files are matched here, by their path. */
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
  if (want("pr"))
    for (const p of data.prs ?? [])
      out.push(prItem(ctx, p, data.host?.kind ?? null));
  if (want("file") && data.files)
    rankFiles(data.files, q.text, filesPer).forEach((r, i) =>
      out.push(fileItem(ctx, r.path, q.line, 60 - i * 3, r.hits)),
    );
  if (want("symbol"))
    code.forEach((h, i) => out.push(symbolItem(ctx, h, 55 - i * 3)));
  if (want("task"))
    for (const t of data.tasks ?? []) out.push(taskItem(ctx, t));
  if (want("flow"))
    for (const f of data.flows ?? []) out.push(flowItem(ctx, f));
  if (want("page")) out.push(...pageItems(ctx));
  if (want("action")) out.push(...actionItems(ctx));
  if (want("project")) out.push(...projectItems(ctx));
  return out;
}

/** The empty launcher: what waits for you (this project's first, pull requests to review), what you used last, and a
 *  few actions. */
export function startGroups(
  ctx: Ctx,
  data: Data,
  recent: Recent[],
): { label: string; items: Item[] }[] {
  const waits = (data.inbox ?? [])
    .map((w) => waitItem(ctx, w))
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    .slice(0, 4);
  const review = (data.prs ?? [])
    .filter((p) => p.review_requested && !p.mine)
    .slice(0, 3)
    .map((p) => prItem(ctx, p, data.host?.kind ?? null));
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
      if (r.kind === "file") return [fileItem(ctx, r.id.slice(5), null)];
      if (r.kind === "symbol") {
        const m = r.id.match(/^sym:(.+):(\d+):([^:]+)$/);
        return m
          ? [
              symbolItem(
                ctx,
                {
                  id: r.id,
                  name: m[3],
                  kind: "",
                  file: m[1],
                  line: Number(m[2]),
                  unit: "",
                  group: "",
                },
                0,
              ),
            ]
          : [];
      }
      const it = byId.get(r.id);
      return it ? [it] : [];
    })
    .slice(0, 5);
  const quick = actionItems(ctx).filter((i) =>
    ["act:ask", "act:file", "act:new-flow"].includes(i.id),
  );
  return [
    { label: "Waiting for you", items: [...waits, ...review] },
    { label: "Recent", items: mine },
    { label: "Quick actions", items: quick },
  ].filter((g) => g.items.length);
}
