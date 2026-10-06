// v0.5.0: MSW handlers for tasks, the Jira connection and the MCP catalog, over a small in-memory copy of the api's
// lifecycle (enough for the Tasks page, the Inbox's task items, Connections › Jira and Tools › Catalog).

import { http, HttpResponse } from "msw";
import type { CatalogEntry, JiraView, Task, TaskItem, TaskList, TaskStatus } from "../tasksApi";

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();

function task(over: Partial<Task> & { id: string; title: string }): Task {
  return {
    project_id: "ludus-engine", description: "", type: "story", status: "todo", source: "jira", external_key: null, external_url: null,
    external_status: null, assignee: "Dev One", priority: "High", thread_id: null, workflow_id: null, pr_url: null, reviewers: [],
    blocked_reason: null, created_at: ago(90), updated_at: ago(5), flow: null, waiting: [],
    events: [{ id: 1, task_id: over.id, at: ago(90), kind: "created", from_status: null, to_status: "todo", note: "Imported from Jira (status To Do).", actor: "jira" }],
    ...over,
  };
}

export function createTaskDb() {
  return {
    tasks: [
      task({ id: "tk_1", title: "Rank players weekly", external_key: "ABC-1", external_url: "https://acme.atlassian.net/browse/ABC-1", external_status: "To Do" }),
      task({ id: "tk_2", title: "Fix the tally queue", type: "bug", status: "in_review", external_key: "ABC-2", external_status: "In Review",
        thread_id: "th_2", workflow_id: "fix", pr_url: "https://github.com/acme/app/pull/7",
        flow: { thread_id: "th_2", workflow_id: "fix", status: "done", phase: "ship", current: null, title: "ABC-2: Fix the tally queue" },
        reviewers: [{ login: "ana", on: "github", state: "approved" }, { login: "bo", on: "github", state: "requested" }] }),
      task({ id: "tk_3", title: "Local clean-up", source: "local", status: "blocked", assignee: "Someone Else", blocked_reason: "verify_green failed 3 times",
        thread_id: "th_3", workflow_id: "change", flow: { thread_id: "th_3", workflow_id: "change", status: "failed", phase: "green", current: null, title: "Local clean-up" } }),
      task({ id: "tk_4", title: "Shipped thing", status: "done", external_key: "ABC-4" }),
    ] as Task[],
    sync: { connected: true, kind: "cloud" as const, last_sync_at: ago(3), last_sync_error: null as string | null, me: "Dev One", poll_minutes: 5 },
    items: [] as TaskItem[],
    nextItem: 100,
    jira: {
      "ludus-engine": {
        connected: true, token_set: true, token_hint: "…XYZ", default_jql: "assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC",
        jql: "project = ABC AND assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC", last_sync_at: ago(3), last_sync_error: null,
        me: { name: "Dev One", account_id: "acc-1" }, mcp_server: null,
        settings: { kind: "cloud", base_url: "https://acme.atlassian.net", email: "dev@acme.com", project_key: "ABC", board_id: null, jql: null,
          status_map: {}, reviewer_field: null, jira_reviewers: [], github_reviewers: ["ana"], poll_minutes: 5 },
      },
    } as Record<string, JiraView>,
    catalogAdded: false,
  };
}
export type TaskDb = ReturnType<typeof createTaskDb>;

const NEXT_ITEM: Partial<Record<TaskStatus, { stage: string; title: (k: string) => string }>> = {
  testing_pp: { stage: "pp", title: (k) => `Confirm PP testing for ${k}` },
  ready_prod: { stage: "prod", title: (k) => `Ship ${k} to production` },
};

export function taskHandlers(db: TaskDb, log: (req: Request) => Promise<Record<string, unknown>>) {
  const find = (id: string) => db.tasks.find((t) => t.id === id);
  const withItems = (t: Task): Task => ({ ...t, waiting: db.items.filter((i) => i.task_id === t.id && !i.done_at) });
  const move = (t: Task, to: TaskStatus, note: string, actor: "user" | "keel" = "user") => {
    db.items.filter((i) => i.task_id === t.id && i.kind === "task" && !i.done_at).forEach((i) => { i.done_at = new Date().toISOString(); });
    t.events = [...(t.events ?? []), { id: (t.events?.length ?? 0) + 1, task_id: t.id, at: new Date().toISOString(), kind: "status", from_status: t.status, to_status: to, note, actor }];
    t.status = to;
    const next = NEXT_ITEM[to];
    if (next) {
      db.items.push({ id: db.nextItem++, task_id: t.id, project_id: t.project_id, kind: "task", stage: next.stage, title: next.title(t.external_key ?? t.title),
        detail: "Confirm when it works.", created_at: new Date().toISOString(), done_at: null });
    }
  };
  const err = (status: number, error: string, hint?: string) => HttpResponse.json({ error, hint }, { status });

  return [
    http.get("/api/projects/:pid/tasks", ({ params }) => HttpResponse.json({
      tasks: db.tasks.filter((t) => t.project_id === params.pid).map(withItems), sync: params.pid === "ludus-engine" ? db.sync : { ...db.sync, connected: false, kind: null, me: null },
    } satisfies TaskList)),
    http.post("/api/projects/:pid/tasks", async ({ request, params }) => {
      const b = await log(request);
      if (!String(b.title ?? "").trim()) return err(400, "Give the task a title");
      const t = task({ id: `tk_new${db.tasks.length}`, project_id: params.pid as string, title: String(b.title), type: (b.type as Task["type"]) ?? "task",
        description: String(b.description ?? ""), external_key: (b.external_key as string) ?? null, source: b.external_key ? "jira" : "local",
        assignee: null, events: [] });
      db.tasks.unshift(t);
      return HttpResponse.json(t);
    }),
    http.post("/api/projects/:pid/tasks/sync", async ({ request }) => {
      await log(request);
      db.sync.last_sync_at = new Date().toISOString();
      return HttpResponse.json({ ok: true, jira: true, total: 3, created: 1, updated: 0, moved: 1, at: db.sync.last_sync_at, reviews_checked: 1, reviews_moved: 0 });
    }),
    http.get("/api/tasks/:id", ({ params }) => {
      const t = find(params.id as string);
      return t ? HttpResponse.json(withItems(t)) : err(404, `No task ${params.id}`);
    }),
    http.put("/api/tasks/:id", async ({ request, params }) => {
      const b = await log(request);
      const t = find(params.id as string)!;
      Object.assign(t, b);
      return HttpResponse.json(withItems(t));
    }),
    http.delete("/api/tasks/:id", async ({ request, params }) => {
      await log(request);
      db.tasks = db.tasks.filter((t) => t.id !== params.id);
      return HttpResponse.json({ ok: true });
    }),
    http.post("/api/tasks/:id/start", async ({ request, params }) => {
      const b = await log(request);
      const t = find(params.id as string)!;
      t.thread_id = "th_new";
      t.workflow_id = String(b.workflow_id ?? "change");
      t.flow = { thread_id: "th_new", workflow_id: t.workflow_id, status: "running", phase: "spec", current: "spec", title: t.title };
      move(t, "in_progress", `Started the ${t.workflow_id} flow.`);
      return HttpResponse.json(withItems(t));
    }),
    http.post("/api/tasks/:id/status", async ({ request, params }) => {
      const b = await log(request);
      const t = find(params.id as string)!;
      if (b.to === "in_progress" && ["in_review", "testing_pp", "ready_prod"].includes(t.status) && !b.note) return err(400, "Say why it goes back");
      move(t, b.to as TaskStatus, String(b.note ?? "moved"));
      return HttpResponse.json(withItems(t));
    }),
    http.post("/api/tasks/:id/confirm", async ({ request, params }) => {
      const b = await log(request);
      const t = find(params.id as string)!;
      move(t, b.stage === "pp" ? "ready_prod" : "done", "confirmed");
      return HttpResponse.json(withItems(t));
    }),
    http.post("/api/tasks/:id/pr", async ({ request, params }) => {
      const b = await log(request);
      const t = find(params.id as string)!;
      t.pr_url = String(b.url);
      move(t, "in_review", `PR link added: ${b.url}`);
      return HttpResponse.json(withItems(t));
    }),
    http.post("/api/inbox/tasks/:itemId/act", async ({ request, params }) => {
      const b = await log(request);
      const it = db.items.find((i) => i.id === Number(params.itemId));
      if (!it || it.done_at) return err(409, "This was answered already");
      const t = find(it.task_id)!;
      if (b.action === "send_back") move(t, "in_progress", String(b.note));
      else if (b.action === "confirm") move(t, it.stage === "pp" ? "ready_prod" : "done", "confirmed");
      it.done_at = new Date().toISOString();
      return HttpResponse.json(withItems(t));
    }),

    http.get("/api/projects/:pid/jira", ({ params }) => HttpResponse.json(db.jira[params.pid as string] ?? {
      connected: false, token_set: false, token_hint: null, default_jql: "assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC",
      jql: "assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC", last_sync_at: null, last_sync_error: null, me: null, mcp_server: null,
      settings: { kind: "cloud", base_url: "", email: null, project_key: null, board_id: null, jql: null, status_map: {}, reviewer_field: null, jira_reviewers: [], github_reviewers: [], poll_minutes: 5 },
    } satisfies JiraView)),
    http.put("/api/projects/:pid/jira", async ({ request, params }) => {
      const b = await log(request);
      const pid = params.pid as string;
      const cur = db.jira[pid];
      if (!cur && !b.token) return err(400, "The token is missing", "Paste an API token from id.atlassian.com › Security › API tokens.");
      const { token, ...rest } = b;
      const settings = { ...(cur?.settings ?? { status_map: {}, jira_reviewers: [], github_reviewers: [], poll_minutes: 5 }), ...rest } as JiraView["settings"];
      db.jira[pid] = { ...(cur ?? { default_jql: "assignee = currentUser()", jql: "assignee = currentUser()", last_sync_at: null, last_sync_error: null, me: null, mcp_server: null }),
        connected: true, token_set: true, token_hint: token ? `…${String(token).slice(-3)}` : cur!.token_hint, settings } as JiraView;
      return HttpResponse.json(db.jira[pid]);
    }),
    http.delete("/api/projects/:pid/jira", async ({ request, params }) => {
      await log(request);
      delete db.jira[params.pid as string];
      return HttpResponse.json({ ok: true });
    }),
    http.post("/api/projects/:pid/jira/test", async ({ request }) => {
      const b = await log(request);
      return HttpResponse.json(b.token === "bad" ? { ok: false, error: "Jira refused the login (401).", hint: "Check the email and the API token.", kind: "auth" }
        : { ok: true, user: { name: "Dev One", account_id: "acc-1" } });
    }),
    http.get("/api/projects/:pid/jira/discover", () => HttpResponse.json({
      statuses: [{ name: "To Do", category: "new" }, { name: "In Progress", category: "indeterminate" }, { name: "Code Review", category: "indeterminate" },
        { name: "QA on PP", category: "indeterminate" }, { name: "Ready for Release", category: "indeterminate" }, { name: "Done", category: "done" }],
      transitions: [], fields: [{ id: "customfield_10010", name: "Reviewers", type: "array", items: "user", custom: true }],
      suggested: { todo: "To Do", in_progress: "In Progress", in_review: "Code Review", testing_pp: "QA on PP", ready_prod: "Ready for Release", done: "Done" },
      keel_statuses: ["todo", "in_progress", "in_review", "testing_pp", "ready_prod", "done", "cancelled", "blocked"],
    })),
    http.get("/api/projects/:pid/mcp-catalog", ({ params }) => HttpResponse.json([{
      id: "jira", name: "Jira (mcp-atlassian)", about: "Lets agents read the ticket and its comments themselves.", url: "https://github.com/sooperset/mcp-atlassian",
      license: "MIT", command: "uvx mcp-atlassian", ready: !!db.jira[params.pid as string], why: db.jira[params.pid as string] ? null : "Connect Jira for this project first.",
      server: db.catalogAdded ? `jira-${params.pid}` : null, added: db.catalogAdded,
    } satisfies CatalogEntry])),
    http.post("/api/projects/:pid/mcp-catalog/:id", async ({ request, params }) => {
      await log(request);
      db.catalogAdded = true;
      return HttpResponse.json({ name: `jira-${params.pid}`, command: "uvx", args: ["mcp-atlassian"], env: { JIRA_API_TOKEN: `secret:jira.${params.pid}` },
        enabled: false, builtin: false, status: "off", tools: [], label: "Jira (mcp-atlassian, optional)" });
    }),
  ];
}
