// MSW handlers for every /api route the web uses, backed by a small in-memory db (reset per test).

import { http, HttpResponse } from "msw";
import type { Cap, CapLeft, CapsLeft, Features, FlowBoard, FlowView, GraphFocus, GraphOverview, CiRun, DbConnection, DbResult, GitStatus, HelperChange, HelperDone, HelperFolder, HelperQuestion, HelperSession, IndexStatus, OnCap, Plugin, PullRequest, RunRow, Settings, Stack, ThreadState, Workflow, WorkflowCheck } from "../api";
import * as fx from "./fixtures";
import { createTaskDb, taskHandlers } from "./taskHandlers";

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

export function createDb() {
  return {
    projects: clone(fx.projects),
    flows: { "ludus-engine": { thread: clone(fx.thread), workflow: clone(fx.featureWorkflow) } } as Record<string, { thread: ThreadState | null; workflow: Workflow | null }>,
    /** v0.7.x: each project's board (GET /flows) and the flows in worktrees by id (GET /flows/:tid) */
    boards: {} as Record<string, FlowBoard>,
    threadViews: {} as Record<string, FlowView>,
    workflows: [clone(fx.featureWorkflow), clone(fx.fixWorkflow), clone(fx.initWorkflow)],
    overrides: { "ludus-engine": { cap_tokens: 600000 } } as Record<string, Partial<Settings>>,
    general: clone(fx.generalSettings),
    notifications: clone(fx.notifications),
    nset: clone(fx.notificationSettings),
    memory: clone(fx.memory),
    /** The Repo IDE's file texts (GET /repo/raw, /files, /search). */
    repoTexts: clone(fx.texts),
    caps: clone(fx.caps) as Cap[],
    /** What counts against the caps now: this project's use today, and its API-key use this month (GET /caps/left). */
    capUse: { day: { tokens: 0, usd: 0 }, month: { tokens: 0, usd: 3.5 } },
    stacks: clone(fx.stacks) as Stack[],
    mcpServers: clone(fx.mcpServers),
    agents: clone(fx.agents),
    /** What GET /index answers (tests change it). */
    index: { project: "ludus-engine", status: "ready", files: 412, symbols: 3100, indexed_at: new Date(Date.now() - 5 * 60_000).toISOString() } as IndexStatus,
    /** What POST /repo/update-from-base answers (tests change it). */
    update: { ok: true, merged: true, conflicts: [] as string[], output: "Merge made by the 'ort' strategy.\n 1 file changed" },
    /** When set, PUT /workflows/:wid answers 422 with these validation errors. */
    yamlErrors: null as string[] | null,
    usage: fx.usage(),
    /** GET /budget/now without its caps (they come from `caps` and `capUse`, as GET /caps/left). */
    budgetNow: fx.budgetNow(),
    /** GET /graph and GET /graph/node answers (tests change them). */
    /** KeelBot: sessions as the engine keeps them (tests add the answers and send the helper.* events) */
    helper: { sessions: [] as HelperSession[], next: 1,
      /** Fix mode: each chat's changed files, the commands that wait for an OK, and what Done answers (null: it commits) */
      changes: {} as Record<string, HelperChange[]>, questions: [] as HelperQuestion[], done: null as HelperDone | null,
      /** side sessions: the commits Keep made on each one's branch */
      kept: {} as Record<string, { sha: string; subject: string }[]>,
      /** v0.15.2 the chats' folders */
      folders: [] as HelperFolder[] },
    graph: fx.graphOverview() as GraphOverview,
    graphFocus: fx.graphFocus() as GraphFocus,
    calls: [] as { method: string; path: string; body: unknown }[],
    /** v0.9.0: each project's flows, newest first (GET /runs), and what POST /workflows/import saves (null: the fix workflow) */
    runs: {} as Record<string, RunRow[]>,
    importAs: null as Workflow | null,
    /** v0.10.0 plugins: which are on, the database connections and query answers, git's state */
    plugins: { db: false, git: false } as Record<string, boolean>,
    /** v0.13.0 GET /api/features: keel without an add-on unless a test installs one */
    features: { mode: "dev", modes: ["dev"], parts: { dev: true }, addons: [], screens: [], plugins: [] } as Features,
    dbConns: [] as DbConnection[],
    dbAnswer: null as DbResult | null,
    gitStatus: { branch: "feat/euro", base: "main", upstream: "origin/feat/euro", ahead: 2, behind: 0, base_ahead: 2, base_behind: 1, pushed: true,
      changes: [] } as GitStatus,
    pr: null as PullRequest | null,
    github: { set: false, hint: null as string | null, from: null as "keel" | "env" | null },
    /** v0.11.0 CI/CD plugin: the pipeline runs (newest first) */
    ciRuns: [] as CiRun[],
    /** v0.5.0: tasks, Jira connections, the MCP catalog */
    tk: createTaskDb(),
  };
}
export type Db = ReturnType<typeof createDb>;

export function handlers(db: Db) {
  const pluginList = (): Plugin[] => [
    { name: "db", title: "Database", description: "Look at the project's database, run safe queries, and check data in workflows.", needs: ["database"],
      tools: { server: "keel-db", read: ["db_connections", "db_schema", "db_query"] },
      actions: [{ name: "db:check", with: { sql: "required", expect: "optional", connection: "optional" }, summary: "a data check" },
        { name: "db:query", with: { sql: "required", connection: "optional" }, summary: "a read" }],
      shows_in: ["connections", "map", "workflows", "keelbot", "inbox"], commands: [{ name: "sql", description: "Ask a data question" }],
      enabled: db.plugins.db, scope: db.plugins.db ? "project" : null },
    { name: "git", title: "Git", description: "Branches, commits, pushes and pull requests.", needs: ["github"],
      tools: { server: "keel-git", read: ["git_status", "pr_status"] }, actions: [{ name: "git:push", with: {}, summary: "push" }],
      shows_in: ["connections", "code", "workflows", "keelbot", "inbox"], commands: [{ name: "commit", description: "Write a commit message" }],
      enabled: db.plugins.git, scope: db.plugins.git ? "project" : null },
    { name: "ci", title: "CI/CD", description: "Watch the project's pipelines, see why one failed, and fix it with a flow.", needs: ["github"],
      tools: { server: "keel-ci", read: ["ci_runs", "ci_failure"] }, actions: [{ name: "ci:wait", with: { minutes: "optional" }, summary: "wait for CI" }],
      shows_in: ["jobs", "workflows", "keelbot", "settings"], commands: [{ name: "ci", description: "Why did CI fail" }],
      enabled: db.plugins.ci ?? false, scope: db.plugins.ci ? "project" : null },
    { name: "review", title: "Code Review", description: "Review the branch you are on or any pull request.", needs: ["github"],
      tools: {}, actions: [], shows_in: ["code", "keelbot"], commands: [{ name: "review-branch", description: "Review this branch" }],
      enabled: db.plugins.review ?? false, scope: db.plugins.review ? "project" : null },
  ];
  const log = async (req: Request) => {
    let body: unknown = null;
    try {
      body = await req.clone().json();
    } catch {
      /* no body */
    }
    db.calls.push({ method: req.method, path: new URL(req.url).pathname, body });
    return body as Record<string, unknown>;
  };
  return [
    http.get("/api/health", () => HttpResponse.json(fx.health)),
    http.get("/api/features", () => HttpResponse.json(db.features)),
    http.get("/api/projects", () => HttpResponse.json(db.projects)),
    http.post("/api/projects", async ({ request }) => {
      const b = await log(request);
      const p = { ...db.projects[2], id: "new", name: String(b.name ?? "new"), root: String(b.root) };
      db.projects.push(p);
      return HttpResponse.json(p);
    }),
    http.get("/api/projects/:pid/flow", ({ params }) =>
      HttpResponse.json(db.flows[params.pid as string] ?? { thread: null, workflow: null })),
    http.get("/api/projects/:pid/flows", ({ params }) =>
      HttpResponse.json(db.boards[params.pid as string] ?? { flows: [], overlaps: [], conflicts: [], order: [] })),
    http.get("/api/projects/:pid/flows/:tid", ({ params }) => {
      const here = db.flows[params.pid as string];
      if (here?.thread?.thread_id === params.tid) return HttpResponse.json(here);
      const v = db.threadViews[params.tid as string];
      return v ? HttpResponse.json(v) : HttpResponse.json({ error: "No flow" }, { status: 404 });
    }),
    http.post("/api/threads/:tid/worktree/remove", async ({ request, params }) => {
      await log(request);
      for (const b of Object.values(db.boards)) b.flows = b.flows.filter((f) => f.thread_id !== params.tid);
      return HttpResponse.json({ ok: true, worktree: `flow-${params.tid}` });
    }),
    http.post("/api/projects/:pid/flows", async ({ request, params }) => {
      const b = await log(request);
      const w = db.workflows.find((x) => x.id === b.workflow_id)!;
      const t: ThreadState = { ...clone(fx.thread), status: "running", waiting: undefined, current: w.steps[0]?.id ?? null, project_id: params.pid as string, workflow_id: w.id, title: String(b.title) };
      db.flows[params.pid as string] = { thread: t, workflow: w };
      return HttpResponse.json(t);
    }),
    http.post("/api/threads/:tid/resume", async ({ request }) => {
      const b = await log(request);
      const f = db.flows["ludus-engine"];
      if (f.thread) {
        f.thread = { ...f.thread, status: "running", waiting: undefined, current: b.decision === "approve" ? "s9" : "s3" };
      }
      return HttpResponse.json(f.thread);
    }),
    http.post("/api/threads/:tid/stop", async ({ request }) => { await log(request); return HttpResponse.json(db.flows["ludus-engine"].thread); }),
    http.get("/api/threads/:tid/history", () => HttpResponse.json(fx.checkpoints)),
    http.post("/api/threads/:tid/rewind", async ({ request }) => { await log(request); return HttpResponse.json(db.flows["ludus-engine"].thread); }),
    http.get("/api/projects/:pid/estimate", () => HttpResponse.json(fx.estimate)),
    http.post("/api/projects/:pid/estimate", async ({ request }) => {
      const b = await log(request);
      const lines = String(b.yaml ?? "").split("\n").filter((l) => l.trim().startsWith("- ")).length;
      return HttpResponse.json({ ...fx.estimate, tokens: 100000 + lines * 1000 });
    }),
    http.get("/api/providers/models", () => HttpResponse.json(fx.providerModels)),

    http.get("/api/jobs", ({ request }) => {
      const u = new URL(request.url);
      const st = u.searchParams.get("status");
      const pid = u.searchParams.get("project");
      return HttpResponse.json(fx.jobs.filter((j) => (!st || j.status === st) && (!pid || j.project_id === pid)));
    }),
    http.get("/api/jobs/:id", ({ params }) => HttpResponse.json({ ...fx.jobs.find((j) => j.id === params.id) ?? fx.jobs[0], steps: fx.steps })),
    http.get("/api/jobs/:id/steps", () => HttpResponse.json({ steps: fx.steps, running: false })),
    http.post("/api/jobs/:id/stop", async ({ request }) => { await log(request); return new HttpResponse(null, { status: 204 }); }),

    http.get("/api/projects/:pid/repo", () => HttpResponse.json(fx.repo)),
    http.get("/api/projects/:pid/index", () => HttpResponse.json(db.index)),
    http.post("/api/projects/:pid/index/rebuild", async ({ request }) => {
      await log(request);
      db.index = { ...db.index, status: "indexing" };
      return HttpResponse.json(db.index);
    }),
    http.get("/api/projects/:pid/repo/tree", () => HttpResponse.json(fx.tree)),
    http.get("/api/projects/:pid/repo/file", ({ request }) => {
      const path = new URL(request.url).searchParams.get("path") ?? "";
      const text = fx.texts[path];
      if (path !== fx.file.path && text === undefined) return HttpResponse.json({ error: `No file at ${path}` }, { status: 404 });
      return HttpResponse.json(path === fx.file.path ? fx.file : { ...fx.file, path, size: text.length, mark: undefined, ac: null, head: text });
    }),
    http.get("/api/projects/:pid/repo/raw", ({ request }) => {
      const path = new URL(request.url).searchParams.get("path") ?? "";
      const text = db.repoTexts[path];
      if (text === undefined) return HttpResponse.json({ error: `No file at ${path}` }, { status: 404 });
      return new HttpResponse(text, { headers: { "content-type": "text/plain;charset=UTF-8" } });
    }),
    http.get("/api/projects/:pid/repo/files", () => HttpResponse.json({ files: Object.keys(db.repoTexts).sort(), truncated: false })),
    http.get("/api/projects/:pid/repo/changes", () => HttpResponse.json(fx.changes)),
    http.get("/api/projects/:pid/repo/diff", async ({ request }) => {
      await log(request);
      const branch = new URL(request.url).searchParams.get("branch");
      return HttpResponse.json({ path: "api/ScoreController.kt", against: branch ? "branch" : "head", ref: branch ? `main…${branch}` : "HEAD",
        diff: fx.diff, binary: false, truncated: false });
    }),
    http.get("/api/projects/:pid/repo/commit", ({ request }) => {
      const sha = new URL(request.url).searchParams.get("sha") ?? "";
      return HttpResponse.json({ sha, message: "feat(AC-002) refuse a negative score", body: "", author: "keelbot", at: new Date().toISOString(), keel: true,
        files: [{ path: "api/ScoreController.kt", status: "M" }] });
    }),
    http.get("/api/projects/:pid/repo/search", async ({ request }) => {
      await log(request);
      const u = new URL(request.url).searchParams;
      const q = u.get("q") ?? "";
      const cs = u.get("case") === "true";
      const results = Object.entries(db.repoTexts).flatMap(([path, text]) => {
        const matches = text.split("\n").flatMap((t, i) => {
          const at = cs ? t.indexOf(q) : t.toLowerCase().indexOf(q.toLowerCase());
          return at < 0 ? [] : [{ line: i + 1, column: at + 1, length: q.length, text: t, ranges: [[at, at + q.length]] }];
        });
        return matches.length ? [{ path, matches }] : [];
      });
      const n = results.reduce((a, r) => a + r.matches.length, 0);
      return HttpResponse.json({ results, matches: n, files: results.length, truncated: false, timed_out: false, took_ms: 3 });
    }),
    http.get("/api/projects/:pid/repo/commits", () => HttpResponse.json([
      { sha: "a81c3f0aa", message: "feat(AC-002) refuse a negative score", author: "keelbot", at: new Date().toISOString(), keel: true },
      { sha: "4be12d9bb", message: "test(AC-002) a negative score is refused", author: "keelbot", at: new Date().toISOString(), keel: true },
      { sha: "1c0ffee00", message: "docs: readme", author: "Mili", at: new Date().toISOString(), keel: false },
    ])),
    http.post("/api/projects/:pid/repo/update-from-base", async ({ request }) => { await log(request); return HttpResponse.json(db.update); }),
    http.get("/api/projects/:pid/repo/history", ({ request }) => {
      const path = new URL(request.url).searchParams.get("path");
      return HttpResponse.json([
        { sha: "a81c3f0aa", message: `feat(AC-002) refuse a negative score (${path})`, author: "implementer", at: new Date().toISOString() },
        { sha: "77b1e02cc", message: "feat(AC-001) save a score", author: "implementer", at: new Date().toISOString() },
      ]);
    }),
    http.post("/api/projects/:pid/unlock", async ({ request }) => {
      const b = await log(request);
      return HttpResponse.json({ unlocks: [{ path: b.path, phase: b.phase ?? "green" }] });
    }),
    http.get("/api/projects/:pid/keel-docs", () => HttpResponse.json(fx.keelDocs)),
    http.get("/api/projects/:pid/memory", () => HttpResponse.json(db.memory)),
    http.post("/api/projects/:pid/memory", async ({ request }) => {
      const b = await log(request);
      const f = { id: `f${db.memory.facts.length + 1}`, source: "you", at: new Date().toISOString(), ...(b as object) } as Db["memory"]["facts"][number];
      db.memory.facts.push(f);
      return HttpResponse.json(f);
    }),
    http.put("/api/projects/:pid/memory/:fid", async ({ request }) => { const b = await log(request); return HttpResponse.json(b); }),
    http.delete("/api/projects/:pid/memory/:fid", async ({ request, params }) => {
      await log(request);
      db.memory.facts = db.memory.facts.filter((f) => f.id !== params.fid);
      return new HttpResponse(null, { status: 204 });
    }),

    http.get("/api/projects/:pid/map", () => HttpResponse.json(fx.map)),
    http.post("/api/projects/:pid/map/rebuild", () => HttpResponse.json(fx.map)),
    http.get("/api/projects/:pid/wiki", () => HttpResponse.json(fx.wiki)),
    http.post("/api/projects/:pid/wiki/refresh", async ({ request, params }) => {
      await log(request);
      return HttpResponse.json({ ...clone(fx.thread), thread_id: "th_kr1", workflow_id: "knowledge-refresh", status: "running", waiting: undefined, project_id: params.pid });
    }),
    http.get("/api/projects/:pid/wiki/page", () => HttpResponse.json(fx.wikiPage)),

    http.get("/api/projects/:pid/workflows", () => HttpResponse.json(db.workflows)),
    http.post("/api/projects/:pid/workflows", async ({ request }) => {
      const b = await log(request);
      const w: Workflow = { ...clone(fx.fixWorkflow), id: "hotfix", name: String(b.name), based_on: "keel/fix", keel_rules: Boolean(b.keel_rules) };
      db.workflows.push(w);
      return HttpResponse.json(w);
    }),
    http.get("/api/workflows/:wid", ({ params }) => {
      const w = db.workflows.find((x) => x.id === params.wid);
      return w ? HttpResponse.json(w) : HttpResponse.json({ error: "No such workflow" }, { status: 404 });
    }),
    http.put("/api/workflows/:wid", async ({ request, params }) => {
      const b = (await log(request)) as unknown as Workflow;
      if (db.yamlErrors) return HttpResponse.json({ error: "The workflow YAML is not valid.", hint: "Fix the lines below and save again.", errors: db.yamlErrors }, { status: 422 });
      const old = db.workflows.find((x) => x.id === params.wid)!;
      if (old.keel_rules && b.keel_rules) {
        const gone = old.steps.filter((s) => s.lock && !b.steps.some((n) => n.id === s.id));
        if (gone.length) return HttpResponse.json({ error: `${gone[0].name} is a keel rule.`, hint: "Turn keel rules off to remove it." }, { status: 422 });
      }
      const saved = { ...b, version: old.version + 1 };
      db.workflows = db.workflows.map((x) => (x.id === saved.id ? saved : x));
      return HttpResponse.json(saved);
    }),
    http.post("/api/projects/:pid/workflows/explain-step", async ({ request }) => {
      const b = await log(request);
      return HttpResponse.json(fx.explanation(String(b.step_id), Boolean(b.thread_id)));
    }),
    http.post("/api/projects/:pid/workflows/import", async ({ request }) => {
      const b = (await log(request)) as { folder?: string };
      const w = { ...(db.importAs ?? fx.fixWorkflow), folder: b.folder ?? null };
      if (db.importAs) db.workflows.push(w);
      return HttpResponse.json({ workflow: w, review: { agents: ["reproducer"], mcp: [], gates: 1, est_tokens: 60000, edits_files: true } });
    }),
    // v0.9.0: keel's check of a workflow KeelBot wrote; the folders of the Workflows page; a project's flows
    http.post("/api/projects/:pid/workflows/check", async ({ request }) => {
      const { yaml } = (await log(request)) as { yaml: string };
      const lines = yaml.split("\n");
      const check: WorkflowCheck = {
        name: yaml.match(/^name:\s*(.+)$/m)?.[1] ?? "", steps: lines.filter((l) => /^\s*- \{?\s*id:/.test(l)).length,
        gates: lines.filter((l) => /kind: gate/.test(l)).length, keel_rules: /keel_rules: true/.test(yaml),
        agents: [...yaml.matchAll(/agent: ([\w-]+)/g)].map((m) => m[1]), mcp: [], tools: [],
        commands: [...yaml.matchAll(/action: "run: ([^"]+)"/g)].map((m) => m[1]), edits_files: false,
        valid: !yaml.includes("INVALID"), errors: yaml.includes("INVALID") ? ["Step 'look': INVALID is not a step kind."] : [], warnings: [],
      };
      return HttpResponse.json(check);
    }),
    http.put("/api/projects/:pid/workflows/:wid/folder", async ({ request, params }) => {
      const { folder } = (await log(request)) as { folder: string };
      const f = folder.trim() || null;
      db.workflows = db.workflows.map((w) => (w.id === params.wid ? { ...w, folder: f } : w));
      return HttpResponse.json({ folder: f });
    }),
    // v0.10.0 plugins
    http.get("/api/projects/:pid/plugins", () => HttpResponse.json(pluginList())),
    http.put("/api/projects/:pid/plugins/:name", async ({ request, params }) => {
      const b = (await log(request)) as { enabled: boolean };
      db.plugins[String(params.name)] = b.enabled;
      return HttpResponse.json(pluginList());
    }),
    http.get("/api/github", () => HttpResponse.json(db.github)),
    // v0.14.0 Connections › GitLab (the Code Review plugin)
    http.get("/api/gitlab", () => HttpResponse.json({ set: false, url: null, host: null, hint: null })),
    http.put("/api/secrets/:name", async ({ request, params }) => {
      if (params.name !== "GITHUB_REPO_TOKEN") return undefined;   // the providers' keys: the handler further down
      await log(request);
      db.github = { set: true, hint: "…789", from: "keel" };
      return HttpResponse.json({ hint: "…789" });
    }),
    http.get("/api/projects/:pid/db/connections", () => HttpResponse.json(db.dbConns)),
    http.get("/api/projects/:pid/db/suggest", () => HttpResponse.json([{ name: "db", kind: "postgres", url: "postgres://app:app@localhost:15432/scores", // keel:allow-secret
      shown: "postgres://app:•••@localhost:15432/scores", env: "local", source: "docker-compose.yml (service db)", password: true }])),
    http.post("/api/projects/:pid/db/connections", async ({ request }) => {
      const b = (await log(request)) as { name: string; url: string; env: DbConnection["env"]; source?: string };
      const c: DbConnection = { name: b.name, kind: "postgres", env: b.env, shown: b.url.replace(/:[^:@/]+@/, ":•••@"), source: b.source ?? null,
        ok: true, server: "PostgreSQL 16.4", tables: 23, error: null, checked_at: new Date().toISOString(), can_change: ["local", "test"].includes(b.env) };
      db.dbConns.push(c);
      return HttpResponse.json({ connection: c, test: { ok: true, server: "PostgreSQL 16.4", tables: 23 } });
    }),
    http.get("/api/projects/:pid/db/schema", () => HttpResponse.json({ connection: "local", kind: "postgres", tables: [
      { name: "players", columns: [{ name: "id", type: "integer", nullable: false, pk: true }, { name: "name", type: "text", nullable: true, pk: false }], fks: [] },
      { name: "scores", columns: [{ name: "id", type: "integer", nullable: false, pk: true }], fks: [{ column: "player_id", table: "players", ref: "id" }] }] })),
    http.post("/api/projects/:pid/db/query", async ({ request }) => {
      const b = (await log(request)) as { sql: string; change?: boolean; confirm?: boolean; connection?: string };
      if (/^\s*(update|insert|delete)/i.test(b.sql)) {
        return HttpResponse.json({ connection: b.connection || "local", env: "local", kind: "change", sql: b.sql, changed: 3, done: !!b.confirm });
      }
      return HttpResponse.json(db.dbAnswer ?? { connection: b.connection || "local", kind: "read", sql: b.sql, columns: ["id", "name"],
        rows: [[1, "Ada"], [2, null]], count: 2, truncated: false, masked: [], ms: 4 });
    }),
    http.get("/api/projects/:pid/ci/runs", () => HttpResponse.json(db.ciRuns)),
    http.get("/api/projects/:pid/ci/runs/:id", ({ params }) => {
      const r = db.ciRuns.find((x) => x.id === Number(params.id));
      return r ? HttpResponse.json({ ...r, jobs: [{ id: 1, name: "test", status: "completed", conclusion: r.conclusion, url: "u",
        failed_steps: r.failed ? ["run the tests"] : [] }], log: r.failed ? "test · run the tests | FAIL test_total - assert 3 == 4" : "" })
        : HttpResponse.json({ error: "No run" }, { status: 404 });
    }),
    http.post("/api/projects/:pid/ci/runs/:id/rerun", async ({ request, params }) => { await log(request); return HttpResponse.json({ id: Number(params.id), rerun: true }); }),
    http.post("/api/projects/:pid/ci/fix", async ({ request }) => {
      await log(request);
      return HttpResponse.json({ ...clone(fx.thread), thread_id: "t-ci-fix", title: "Fix CI: ci on feat/euro", status: "running" });
    }),
    http.post("/api/projects/:pid/ci/check", async ({ request }) => { await log(request); return HttpResponse.json({ told: [] }); }),
    http.get("/api/projects/:pid/git/status", () => HttpResponse.json(db.gitStatus)),
    http.get("/api/projects/:pid/git/branches", () => HttpResponse.json([
      { name: "feat/euro", current: true, ahead: 2, behind: 1, date: "2026-10-07T09:00:00Z", subject: "feat: euro" },
      { name: "main", current: false, ahead: 0, behind: 0, date: "2026-10-06T09:00:00Z", subject: "Version 0.9.0" }])),
    http.get("/api/projects/:pid/git/pr", () => HttpResponse.json({ pr: db.pr })),
    /** v0.12.0 one branch against the base (the repo fixture is on feat/scores) */
    http.get("/api/projects/:pid/git/branch", ({ request }) => {
      const name = new URL(request.url).searchParams.get("name") ?? "";
      const base = name === fx.repo.base;
      return HttpResponse.json({ name, base: fx.repo.base, current: name === fx.repo.branch, ahead: base ? 0 : 1, behind: 0,
        commits: [{ sha: "b1b2c3d4e5f6", message: base ? "Version 0.9.0" : "feat(AC-002): refuse a negative score", author: "Ada",
          at: new Date().toISOString(), keel: !base }],
        files: base ? [] : [{ path: "api/ScoreController.kt", status: "M" }, { path: "api/Euro.kt", status: "A" }] });
    }),
    http.post("/api/projects/:pid/git/:op", async ({ request, params }) => {
      const b = (await log(request)) as Record<string, unknown>;
      const op = String(params.op);
      if (op === "commit") return HttpResponse.json({ sha: "abc1234def", subject: String(b.message).split("\n")[0], files: ["src/a.ts"] });
      if (op === "push") return HttpResponse.json({ branch: "feat/euro", sha: "abc1234def" });
      if (op === "sync") return HttpResponse.json({ merged: true, from: "origin/main", branch: "feat/euro" });
      if (op === "switch") return HttpResponse.json({ branch: b.branch });
      if (op === "pr") return HttpResponse.json({ url: "https://github.com/o/r/pull/7", updated: false });
      return HttpResponse.json({});
    }),
    http.get("/api/projects/:pid/runs", ({ request, params }) => {
      const u = new URL(request.url);
      const wf = u.searchParams.get("workflow");
      return HttpResponse.json((db.runs[params.pid as string] ?? []).filter((r) => !wf || r.workflow_id === wf).slice(0, Number(u.searchParams.get("limit") ?? 20)));
    }),
    http.get("/api/library", () => HttpResponse.json(fx.library)),
    http.post("/api/projects/:pid/library/:id/install", async ({ request }) => { await log(request); return HttpResponse.json(fx.fixWorkflow); }),

    http.get("/api/projects/:pid/agents", () => HttpResponse.json(db.agents)),
    http.put("/api/projects/:pid/agents/:aid", async ({ request, params }) => {
      const b = await log(request);
      const i = db.agents.findIndex((a) => a.id === params.aid);
      const prev = db.agents[i];
      // like the api: a field sent as null goes back to the default; knowledge changes the token count
      const knowledge = b.knowledge === null ? fx.agents.find((a) => a.id === params.aid)?.knowledge : (b.knowledge as typeof prev.knowledge) ?? prev.knowledge;
      const files = prev.knowledge_files ?? {};
      const next = { ...prev, ...b, knowledge, knowledge_tokens: (knowledge?.sections ?? []).reduce((n, s) => n + (files[s] ?? 0), 0),
        overridden: [...new Set([...prev.overridden.filter((k) => b[k] !== null), ...Object.keys(b).filter((k) => b[k] !== null)])] };
      db.agents[i] = next;
      return HttpResponse.json(next);
    }),
    http.post("/api/projects/:pid/agents", async ({ request }) => { const b = await log(request); return HttpResponse.json({ ...b, custom: true, enabled: true, overridden: [] }); }),
    http.post("/api/agents/:aid/test", () => HttpResponse.json({ ok: true, text: "OK", ms: 1400 })),
    http.get("/api/projects/:pid/skills", () => HttpResponse.json(fx.skills)),
    http.get("/api/skills/:sid", () => HttpResponse.json(fx.skillDetail)),
    http.get("/api/projects/:pid/stacks", () => HttpResponse.json(db.stacks)),
    http.post("/api/projects/:pid/stacks", async ({ request }) => {
      const b = await log(request);
      const from = db.stacks.find((x) => x.name === b.from) ?? db.stacks[0];
      const st = { ...clone(from), name: String(b.name), source: "this project", detected: true, installable: false };
      db.stacks.push(st);
      return HttpResponse.json(st);
    }),
    http.post("/api/projects/:pid/stacks/:name/install", async ({ request, params }) => {
      await log(request);
      const st = { ...db.stacks.find((x) => x.name === params.name)!, installable: false, detected: true, source: "keel pack (installed)" };
      db.stacks = db.stacks.map((x) => (x.name === st.name ? st : x));
      return HttpResponse.json(st);
    }),
    http.post("/api/projects/:pid/skills/import", async ({ request }) => {
      await log(request);
      return HttpResponse.json({ id: "imported-skill", kind: "knowledge", source: "yours", stack: "any", version: "v1", tokens: 500, agents: [], when: "", enabled: true });
    }),
    http.get("/api/mcp-servers", () => HttpResponse.json(db.mcpServers)),
    http.put("/api/mcp-servers/:name", async ({ request, params }) => {
      const b = (await log(request)) as { enabled?: boolean };
      const s = db.mcpServers.find((x) => x.name === params.name)!;
      if (b.enabled !== undefined) s.enabled = b.enabled;
      return HttpResponse.json(s);
    }),
    http.post("/api/mcp-servers/:name/test", () => HttpResponse.json({ ok: true, tools: [{ name: "keel_status" }, { name: "keel_next" }] })),
    http.get("/api/projects/:pid/mcp-allow", () => HttpResponse.json({ explorer: ["keel"] })),
    http.put("/api/projects/:pid/mcp-allow", async ({ request }) => HttpResponse.json(await log(request))),

    http.get("/api/projects/:pid/budget", () => HttpResponse.json(fx.budget)),
    http.get("/api/projects/:pid/helper/sessions", () => HttpResponse.json(db.helper.sessions.map(({ messages: _m, ...x }) => x))),
    http.post("/api/projects/:pid/helper/sessions", async ({ request, params }) => {
      const b = (await log(request)) as { mode?: "ask" | "fix" | "side"; title?: string } | null;
      const now = new Date().toISOString();
      const waiting = db.flows[String(params.pid)]?.thread;
      if (b?.mode === "fix" && waiting?.status !== "waiting") {
        return HttpResponse.json({ error: "No flow of this project waits at a gate.", hint: "Fix mode works while a flow waits." }, { status: 409 });
      }
      const n = db.helper.next++;
      const sess: HelperSession = { id: `h_${n}`, project: String(params.pid), root: "/workspace", mode: b?.mode ?? "ask",
        thread_id: b?.mode === "fix" ? waiting!.thread_id : null, phase: b?.mode === "fix" ? "green" : null, title: b?.title || "New chat",
        ...(b?.mode === "side" ? { worktree: `/workspace/.keel/worktrees/helper-${n}`, branch: `keel/helper/${n}`, base_sha: "abc1234" } : {}),
        model: { provider: "claude", mode: "subscription", model: "sonnet" }, status: "idle", tokens_in: 0, tokens_out: 0, tokens_cached: 0, cost_usd: 0,
        turns: 0, created_at: now, updated_at: now, messages: [], busy: false };
      db.helper.sessions.unshift(sess);
      return HttpResponse.json(sess);
    }),
    http.get("/api/projects/:pid/helper/sessions/:sid", ({ params }) => {
      const sess = db.helper.sessions.find((x) => x.id === params.sid);
      return sess ? HttpResponse.json(sess) : HttpResponse.json({ error: "No KeelBot session" }, { status: 404 });
    }),
    http.patch("/api/projects/:pid/helper/sessions/:sid", async ({ request, params }) => {
      const b = (await log(request)) as { title?: string; model?: HelperSession["model"]; folder?: string };
      const sess = db.helper.sessions.find((x) => x.id === params.sid)!;
      if (b.title) sess.title = b.title;
      if (b.model) sess.model = b.model;
      if (b.folder !== undefined) sess.folder = b.folder || null;
      return HttpResponse.json(sess);
    }),
    http.get("/api/projects/:pid/helper/folders", ({ params }) => HttpResponse.json(db.helper.folders.filter((f) => f.project === params.pid)
      .map((f) => ({ ...f, chats: db.helper.sessions.filter((s) => s.folder === f.id).length })))),
    http.post("/api/projects/:pid/helper/folders", async ({ request, params }) => {
      const b = (await log(request)) as { name: string };
      const f: HelperFolder = { id: `hf_${db.helper.folders.length + 1}`, project: String(params.pid), name: b.name, chats: 0, created_at: "", updated_at: "" };
      db.helper.folders.push(f);
      return HttpResponse.json(f);
    }),
    http.patch("/api/projects/:pid/helper/folders/:fid", async ({ request, params }) => {
      const b = (await log(request)) as { name: string };
      const f = db.helper.folders.find((x) => x.id === params.fid)!;
      f.name = b.name;
      return HttpResponse.json(f);
    }),
    http.delete("/api/projects/:pid/helper/folders/:fid", async ({ request, params }) => {
      await log(request);
      const moved = db.helper.sessions.filter((s) => s.folder === params.fid);
      moved.forEach((s) => { s.folder = null; });
      db.helper.folders = db.helper.folders.filter((x) => x.id !== params.fid);
      return HttpResponse.json({ ok: true, moved: moved.length });
    }),
    http.delete("/api/projects/:pid/helper/sessions/:sid", async ({ request, params }) => {
      await log(request);
      db.helper.sessions = db.helper.sessions.filter((x) => x.id !== params.sid);
      return HttpResponse.json({ ok: true });
    }),
    http.post("/api/projects/:pid/helper/sessions/:sid/turn", async ({ request, params }) => {
      const b = (await log(request)) as { text: string };
      const sess = db.helper.sessions.find((x) => x.id === params.sid)!;
      const n = (sess.messages?.length ?? 0) + 1;
      sess.messages = [...(sess.messages ?? []), { n, role: "user", text: b.text, data: {}, at: new Date().toISOString() }];
      sess.status = "running";
      sess.busy = true;
      if (sess.title === "New chat") sess.title = b.text.slice(0, 80);
      return HttpResponse.json({ session: sess.id, call_id: `call-${sess.id}-${n}`, n, command: b.text.startsWith("/") ? b.text.slice(1).split(" ")[0] : null });
    }),
    http.post("/api/projects/:pid/helper/sessions/:sid/stop", async ({ request, params }) => {
      await log(request);
      return HttpResponse.json(db.helper.sessions.find((x) => x.id === params.sid));
    }),
    http.get("/api/projects/:pid/helper/sessions/:sid/changes", ({ params }) => HttpResponse.json(db.helper.changes[String(params.sid)] ?? [])),
    http.post("/api/projects/:pid/helper/sessions/:sid/undo", async ({ request, params }) => {
      const b = (await log(request)) as { path?: string } | null;
      const sid = String(params.sid);
      db.helper.changes[sid] = b?.path ? (db.helper.changes[sid] ?? []).filter((c) => c.path !== b.path) : [];
      return HttpResponse.json(db.helper.changes[sid]);
    }),
    http.post("/api/projects/:pid/helper/sessions/:sid/done", async ({ request, params }) => {
      await log(request);
      const sid = String(params.sid);
      const files = (db.helper.changes[sid] ?? []).map((c) => c.path);
      const res: HelperDone = db.helper.done ?? { ok: true, sha: "c0ffee1234", message: "helper: fix", files, checks: "pytest -q" };
      if (res.ok) {
        db.helper.changes[sid] = [];
        db.helper.kept[sid] = [...(db.helper.kept[sid] ?? []), { sha: res.sha, subject: `fix(helper): ${res.message}` }];
        const sess = db.helper.sessions.find((x) => x.id === sid)!;
        sess.messages = [...(sess.messages ?? []), { n: (sess.messages?.length ?? 0) + 1, role: "note", text: "keel committed KeelBot's change: helper: fix",
          data: { status: "committed", sha: res.sha, files }, at: new Date().toISOString() }];
      }
      return HttpResponse.json(res);
    }),
    http.get("/api/projects/:pid/helper/sessions/:sid/handover", ({ params }) => {
      const sess = db.helper.sessions.find((x) => x.id === params.sid)!;
      return HttpResponse.json({ session: sess.id, title: sess.title, branch: sess.branch, base: sess.base_sha, worktree: sess.worktree,
        commits: db.helper.kept[sess.id] ?? [], uncommitted: (db.helper.changes[sess.id] ?? []).map((c) => c.path), asked: [], answer: "" });
    }),
    http.post("/api/projects/:pid/helper/sessions/:sid/task", async ({ request, params }) => {
      await log(request);
      const sess = db.helper.sessions.find((x) => x.id === params.sid)!;
      return HttpResponse.json({ id: "task_9", title: sess.title });
    }),
    http.post("/api/projects/:pid/helper/sessions/:sid/flow", async ({ request, params }) => {
      await log(request);
      const sess = db.helper.sessions.find((x) => x.id === params.sid)!;
      sess.worktree = null;
      return HttpResponse.json({ ...db.flows[String(params.pid)]?.thread, status: "running" });
    }),
    http.get("/api/projects/:pid/helper/permissions", ({ params }) => HttpResponse.json(db.helper.questions.filter((q) => q.project === params.pid))),
    http.post("/api/projects/:pid/helper/permissions/:qid", async ({ request, params }) => {
      const b = (await log(request)) as { decision: string };
      db.helper.questions = db.helper.questions.filter((q) => q.id !== params.qid);
      return HttpResponse.json({ id: params.qid, decision: b.decision });
    }),
    http.get("/api/projects/:pid/helper/commands", () => HttpResponse.json([
      { name: "explain", description: "Explain a file, a symbol or the selected lines in plain words", plugin: "core", source: "keel" },
      { name: "where", description: "Find where something is in the code", plugin: "core", source: "keel" },
      { name: "deploy-notes", description: "Our release notes", plugin: "team", source: "project" },
    ])),
    http.get("/api/projects/:pid/graph", () => HttpResponse.json(db.graph)),
    http.get("/api/projects/:pid/graph/search", ({ request }) => {
      const q = (new URL(request.url).searchParams.get("q") ?? "").toLowerCase();
      const all = [
        { id: "method:save", name: "ScoreService.save", kind: "method", file: "app/ScoreService.java", line: 8, unit: "class:svc", group: "package:com.x.app" },
        { id: "class:score", name: "Score", kind: "class", file: "domain/Score.java", line: 3, unit: "class:score", group: "package:com.x.domain" },
      ];
      return HttpResponse.json({ available: true, results: all.filter((h) => h.name.toLowerCase().includes(q)) });
    }),
    http.get("/api/projects/:pid/graph/node", async ({ request }) => {
      await log(request);
      return HttpResponse.json(db.graphFocus);
    }),
    http.get("/api/projects/:pid/budget/now", async ({ request, params }) => {
      await log(request);
      return HttpResponse.json({ ...db.budgetNow, caps: capsLeft(db, params.pid as string).caps.filter((c) => c.checked && (c.window === "day" || c.window === "month")) });
    }),
    http.get("/api/projects/:pid/caps", () => HttpResponse.json(db.caps)),
    http.get("/api/projects/:pid/caps/left", ({ params }) => HttpResponse.json(capsLeft(db, params.pid as string))),
    http.post("/api/projects/:pid/caps", async ({ request }) => {
      const b = (await log(request)) as unknown as Cap;
      const c = { ...b, id: `c${db.caps.length + 10}` };
      db.caps.push(c);
      return HttpResponse.json(c);
    }),
    http.put("/api/projects/:pid/caps/:id", async ({ request, params }) => {
      const b = (await log(request)) as unknown as Cap;
      db.caps = db.caps.map((c) => (c.id === params.id ? { ...b, id: c.id } : c));
      return HttpResponse.json({ ...b, id: params.id });
    }),
    http.delete("/api/projects/:pid/caps/:id", async ({ request, params }) => {
      await log(request);
      db.caps = db.caps.filter((c) => c.id !== params.id);
      return new HttpResponse(null, { status: 204 });
    }),
    http.get("/api/limits", () => HttpResponse.json(fx.limits)),
    http.get("/api/usage/providers", () => HttpResponse.json(db.usage)),
    http.post("/api/usage/providers/:id/refresh", async ({ request, params }) => {
      await log(request);
      const card = db.usage.find((u) => u.id === params.id);
      if (!card) return HttpResponse.json({ error: `${params.id} is not set up` }, { status: 400 });
      const fresh = { ...card, fetched_at: new Date().toISOString(), windows: card.windows.map((w) => ({ ...w, used_pct: 0.7 })) };
      db.usage = db.usage.map((u) => (u.id === card.id ? fresh : u));
      return HttpResponse.json(fresh);
    }),
    http.put("/api/limits", async ({ request }) => HttpResponse.json(await log(request))),
    http.get("/api/settings/general", () => HttpResponse.json(db.general)),
    http.put("/api/settings/general", async ({ request }) => {
      const b = await log(request);
      Object.assign(db.general, b);
      return HttpResponse.json(db.general);
    }),
    http.get("/api/projects/:pid/settings", ({ params }) => HttpResponse.json(fx.projectSettings(db.overrides[params.pid as string] ?? {}))),
    http.put("/api/projects/:pid/settings", async ({ request, params }) => {
      const b = await log(request);
      const pid = params.pid as string;
      const ov = { ...(db.overrides[pid] ?? {}) } as Record<string, unknown>;
      Object.entries(b).forEach(([k, v]) => { if (v === null) delete ov[k]; else ov[k] = v; });
      db.overrides[pid] = ov as Partial<Settings>;
      return HttpResponse.json({ general: db.general, overrides: ov, effective: { ...db.general, ...ov } });
    }),
    http.get("/api/connections", () => HttpResponse.json(fx.connections)),
    http.put("/api/connections/:provider", async ({ request }) => { await log(request); return new HttpResponse(null, { status: 204 }); }),
    http.put("/api/secrets/:name", async ({ request }) => { await log(request); return HttpResponse.json({ hint: "…9KQ" }); }),
    http.post("/api/connections/:provider/test", () => HttpResponse.json({ ok: true, text: "OK", ms: 900 })),

    http.get("/api/notifications", () => HttpResponse.json(db.notifications)),
    http.post("/api/notifications/read-all", async ({ request }) => { await log(request); return new HttpResponse(null, { status: 204 }); }),
    http.post("/api/notifications/:id/read", async ({ request }) => { await log(request); return new HttpResponse(null, { status: 204 }); }),
    http.get("/api/notification-settings", () => HttpResponse.json(db.nset)),
    http.put("/api/notification-settings", async ({ request }) => { const b = await log(request); Object.assign(db.nset, b); return HttpResponse.json(db.nset); }),
    ...taskHandlers(db.tk, log),
  ];
}

/** A small copy of the api's CapPlanner: what each cap leaves now and what a flow started now gets. */
function capsLeft(db: Db, pid: string): CapsLeft {
  const caps: CapLeft[] = db.caps.map((c) => {
    if (c.scope === "flow" || c.scope === "step") {
      return { ...c, used: 0, left: c.limit, window: c.scope, checked: !(c.scope === "step" && c.unit === "usd"), note: "" };
    }
    const use = c.scope === "day" ? db.capUse.day : db.capUse.month;
    const used = c.unit === "usd" ? use.usd : use.tokens;
    return { ...c, used, left: Math.max(0, c.limit - used), window: c.scope === "day" ? "day" : "month", resets_at: "2026-11-01T00:00:00Z", checked: true, note: "" };
  });
  const s = { ...db.general, ...(db.overrides[pid] ?? {}) };
  const out = caps.find((c) => c.checked && c.resets_at && c.left <= 0 && c.action !== "cheaper");
  if (out) {
    return { caps, next_flow: { cap_tokens: s.cap_tokens, on_cap: s.on_cap, cheaper: false, notes: [],
      refused: { cap_id: out.id, error: `The cap "${out.scope}" is used up.`, hint: "It resets on the 1st (2026-11-01, 00:00 UTC)." } } };
  }
  const live = caps.filter((c) => c.checked && !(c.resets_at && c.left <= 0));
  const pick = (list: { left: number; action: OnCap; from: string }[]) => list.sort((a, b) => a.left - b.left)[0];
  const tok = pick([{ left: s.cap_tokens, action: s.on_cap, from: "settings" }, ...live.filter((c) => c.unit === "tokens" && c.scope !== "step").map((c) => ({ left: c.left, action: c.action, from: c.id }))]);
  const usd = pick(live.filter((c) => c.unit === "usd" && c.scope !== "step").map((c) => ({ left: c.left, action: c.action, from: c.id })));
  const step = pick(live.filter((c) => c.unit === "tokens" && c.scope === "step").map((c) => ({ left: c.left, action: c.action, from: c.id })));
  return {
    caps,
    next_flow: {
      cap_tokens: tok.left, on_cap: tok.action, tokens_from: tok.from, cap_usd: usd?.left ?? null, on_cap_usd: usd?.action ?? null, usd_from: usd?.from ?? null,
      step_cap_tokens: step?.left ?? null, step_on_cap: step?.action ?? null, step_from: step?.from ?? null,
      cheaper: caps.some((c) => c.checked && c.resets_at && c.left <= 0 && c.action === "cheaper"), notes: [], refused: null,
    },
  };
}
