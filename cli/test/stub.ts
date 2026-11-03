// A stub keel for keel TUI's tests: the api routes the TUI calls, with one project ("demo") whose feature flow waits
// at its spec gate, an inbox, a running agent, KeelBot and the diagrams route. As a fetch (UI tests) or an http server
// (the contract test); every call is recorded with its method, path, body and headers.
import { createServer, type Server } from "node:http";
import { unstable_IdlePriority, unstable_scheduleCallback } from "scheduler";

export type Call = { method: string; path: string; query: string; body: unknown; headers: Record<string, string> };

const AT = "2026-10-16T10:00:00Z";

export function stubKeel() {
  const calls: Call[] = [];
  const workflow = {
    id: "feature", name: "feature (keel)", keel_rules: true, version: 1, yaml: "", source: "keel",
    steps: [
      { id: "preflight", kind: "code", name: "preflight", phase: "preflight", action: "preflight" },
      { id: "spec", kind: "agent", name: "spec", phase: "spec", agent: "explorer" },
      { id: "spec_gate", kind: "gate", name: "spec approval", phase: "spec" },
      { id: "red", kind: "agent", name: "red", phase: "red", agent: "test-author", per_ac: true },
      { id: "green", kind: "agent", name: "green", phase: "green", agent: "implementer", per_ac: true },
      { id: "ac_gate", kind: "gate", name: "criterion review", phase: "gate", per_ac: true },
    ],
  };
  const waiting = { step: "spec_gate", kind: "gate", title: "spec approval", detail: "Read the spec.", options: ["approve", "reject"], id: "q1",
    labels: { approve: "Approve the spec" } };
  const state = {
    thread: {
      thread_id: "t1", project_id: "demo", workflow_id: "feature", title: "Add a health endpoint", status: "waiting", current: "spec_gate",
      phase: "spec", ac: null, acs: [{ id: "AC-1", layer: "API", title: "GET /health", status: "todo" }, { id: "AC-2", layer: "WEB", title: "shows it", status: "todo" }],
      waiting, usage: { tokens_in: 12000, tokens_out: 3400, cost_usd: 0.42, premium_requests: 0, cap_tokens: 500000 }, checkpoints: 2, updated_at: AT,
    } as Record<string, unknown>,
    session: null as null | Record<string, unknown>,
    // P1-3 the project's chats for /chats (with last_message and message_count); null: just the open one, if any
    // 0.18.18 the person's earlier prompts of the project, newest first (GET …/helper/prompts, keel TUI's ↑ / ↓)
    prompts: [] as string[],
    // 0.21.2 Settings › Resources: how keel2 started keel, and what waits for its next start
    resources: { in_container: true, start: { docker: false, wifi: false }, start_wanted: {} as Record<string, boolean> },
    // 0.18.18 what the engine says of the images a turn named: the ones sent, and the others with why not
    turnImages: { images: [] as string[], images_skipped: [] as { path: string; why: string }[] },
    // 0.18.18 what keel's guarded run answers for keel TUI's `!command` (POST …/helper/sessions/s1/run)
    run: { exit: 0, output: "README.md\nsrc\n", refused: null } as { exit: number | null; output: string; refused: string | null },
    sessions: null as null | Record<string, unknown>[],
    changes: [] as { path: string; status: string; added: number; removed: number; diff: string }[],
    // 0.18.20 one answer's own files (GET …/changes?turn=n), by the turn's user message n
    turnChanges: {} as Record<number, { path: string; status: string; added: number; removed: number; diff: string }[]>,
    // 0.18.19 the files the engine says /undo (or /redo) of a whole answer put back
    turnFiles: ["src/app.ts", "src/b.ts", "README.md"] as string[],
    plugins: { plugins: [] as unknown[], problems: [] as unknown[] },
    messages: [] as Record<string, unknown>[],
    // 0.18.6 the shared focus (api ui/UiFocus.kt)
    focus: { project: null as string | null, chats: {} as Record<string, string>, by: null as string | null, client: null as string | null },
    // 0.18.6 the Code Review plugin's routes for its mode in keel TUI: pull request 7, two files, one thread
    review: {
      drafts: [] as { id: string; path: string | null; line: number | null; side: string; body: string }[],
      viewed: [] as string[],
      threads: [{ id: "th1", path: "src/Prefs.kt", line: 3, side: "RIGHT", resolved: false, outdated: false, reply_to: "1",
        comments: [{ id: "1", author: "bo", body: "Should 20 come from config?", at: AT, url: null }] }],
      submitted: null as null | Record<string, unknown>,
    },
    // 0.18.6 skills: the agents each is on, and the reviewer's own skill list (an override wins over the skill's list)
    skillAgents: { debugging: ["reproducer"], "changelog-entries": [] } as Record<string, string[]>,
    reviewerSkills: ["review"],
    installed: null as null | { version: string; agents: string[] },
    // 0.18.9 stacks: keel's go (turned off or not) and the project's own zig-api
    stacks: [
      { name: "go", lane: "api", source: "keel", origin: "keel", detected: true, off: false, removable: false,
        why: "It ships with keel and other projects may use it. Turn it off for this project instead.", commands: [], tools: [], layers: [] },
      { name: "zig-api", lane: "api", source: "project", origin: "project", detected: true, off: false, removable: true, why: null,
        file: ".keel/stacks/zig-api.yml", commands: [], tools: [], layers: [] },
    ] as Record<string, unknown>[],
    // where POST /flows says the new flow is; null for both is a reply sent before the graph reached its first step
    startAt: { current: "preflight", phase: "preflight" } as { current: string | null; phase: string | null },
    // 0.18.57 a start refused for untracked paths (until ignored) or for a task someone else works on (until force)
    dirty: [] as string[],
    owner: null as null | { kind: string; id: string; who: string; why: string },
    // 0.18.62 the local main and origin/main went their own ways: a new branch is refused, "continue" starts
    diverged: false,
    // keel 0.18.55 the demo project's `configured` (false: keel does not know it yet, no .keel/config.yml); unset: not sent
    configured: undefined as boolean | undefined,
  };
  const reviewView = (key: string) => ({ key, kind: key.startsWith("pr:") ? "pr" : "branch", number: key.startsWith("pr:") ? Number(key.slice(3)) : null,
    title: key === "pr:8" ? "Mine" : "Save the page size", author: "ana", base: "main", branch: "feat/paging", base_sha: "b", head_sha: "h", url: null, state: "open",
    draft: false, same_repo: true, checks: [], approved: [], changes_requested: [], added: 9, removed: 1, commits: [], conversation: [], can_post: true,
    files: [{ path: "src/Prefs.kt", status: "M", from: null, added: 6, removed: 1, binary: false },
      { path: "src/PrefsTest.kt", status: "A", from: null, added: 3, removed: 0, binary: false }],
    threads: state.review.threads, drafts: state.review.drafts, viewed: state.review.viewed, host: null, me: "me", notes: [] });
  const PREFS_DIFF = "diff --git a/src/Prefs.kt b/src/Prefs.kt\n--- a/src/Prefs.kt\n+++ b/src/Prefs.kt\n@@ -1,3 +1,8 @@\n class Prefs {\n-    fun pageSize(user: String) = 20\n"
    + "+    private val cache = mutableMapOf<String, Int>()\n+    fun savePageSize(user: String, size: Int) {\n+        cache[user] = size\n+    }\n+    fun pageSize(user: String) = cache[user] ?: 20\n }\n";
  const project = { id: "demo", name: "demo", root: "/work/demo", branch: "keel/health", flow: "feature", phase: "spec", acs: [0, 2], waiting: 1, running: 1 };
  const job = { id: "j1", project_id: "demo", thread_id: "t1", agent: "explorer", provider: "fake", model: "fake", step: "spec", phase: "spec", ac: null,
    status: "running", started_at: AT, ended_at: null, tokens_in: 900, tokens_out: 100, cost_usd: 0, premium_requests: 0, steps_count: 3, mcp_calls: 0 };
  const inboxItem = { project_id: "demo", project_name: "demo", thread_id: "t1", flow: "Add a health endpoint", workflow_id: "feature", step: "spec_gate",
    kind: "gate", title: "spec approval", detail: "Read the spec.", more: false, options: ["approve", "reject"], id: "q1" };

  const route = (method: string, path: string, query: URLSearchParams, body: any): [number, unknown] => {
    const key = `${method} ${path}`;
    switch (key) {
      case "GET /api/health": return [200, { ok: true, engine: true, version: "0.17.3", fake: true, native: false }];
      case "GET /api/projects": return [200, [{ ...project, ...(state.configured === undefined ? {} : { configured: state.configured }) }, ...["alpha", "zeta"].map((id) => ({ ...project, id, name: id, root: `/work/${id}`, branch: "main",
        flow: null, phase: null, waiting: 0, running: 0 }))]];
      case "GET /api/projects/demo/flow": return [200, { thread: state.thread, workflow }];
      case "GET /api/projects/demo/flows/t1": return [200, { thread: state.thread, workflow }];
      case "GET /api/threads/t1/history": return [200, [{ id: "c1", n: 1, step: "preflight", at: AT, note: "" }, { id: "c2", n: 2, step: "spec", at: AT, note: "" }]];
      case "GET /api/inbox": return [200, { items: state.thread.status === "waiting" ? [inboxItem] : [], count: 1, kinds: ["gate"], projects: [] }];
      case "GET /api/jobs": return [200, query.get("status") === "running" ? [job] : [job]];
      case "GET /api/jobs/j1/steps": return [200, { steps: [{ n: 3, at: AT, kind: "tool", tool: "read_file", text: "src/app.ts" }], running: true }];
      case "GET /api/projects/demo/workflows": return [200, [workflow, ...(state.configured === false ? [{ ...workflow, id: "init", name: "init (keel)" }] : [])]];
      case "GET /api/projects/demo/runs": return [200, [{ thread_id: "t1", title: "Add a health endpoint", workflow_id: "feature", status: "waiting", phase: "spec",
        current: "spec_gate", waiting: "spec approval", acs_done: 0, acs_total: 2, tokens: 15400, where: "folder", branch: "keel/health", error: null,
        created_at: AT, updated_at: AT, cost_usd: 0.42, latest: true }]];
      case "GET /api/resources": return [200, state.resources];
      case "PUT /api/resources": {
        const b = body as Record<string, boolean>;
        const now = state.resources.start;
        const want = { ...state.resources.start_wanted, ...b };
        state.resources = { ...state.resources, start_wanted: Object.fromEntries(Object.entries(want).filter(([k, v]) => v !== now[k as "docker" | "wifi"])) };
        return [200, state.resources];
      }
      case "GET /api/projects/demo/budget/now": return [200, { today: { tokens: 15400, cost_usd: 0.42 }, month: { tokens: 99000, cost_usd: 3.1 }, flows: [], caps: [] }];
      case "POST /api/diagrams/ascii": return [200, { kind: body.kind, text: `DIAGRAM ${body.kind} ${body.data?.workflow?.id ?? ""} w=${body.width}\n`, lines: 1 }];
      case "POST /api/projects/demo/doctor/workspace/exclude":
        state.dirty = state.dirty.filter((d) => d !== body.path);
        return [200, { results: [], remaining: [], clean: true }];
      case "POST /api/projects/demo/flows":
        if (state.dirty.length && !body.allow_dirty) return [409, { error: `This project has uncommitted changes (1 file): ${state.dirty[0]}notes.md`,
          hint: "Commit or stash them first.", dirty: [`${state.dirty[0]}notes.md`], ignorable: state.dirty }];
        if (state.owner && !body.force) return [409, { error: "Someone already works on this", owners: [state.owner] }];
        if (state.diverged && body.branch !== "continue") return [409, { error: "The local main and origin/main went their own ways (4 and 31 commits)",
          hint: "Pull (or rebase) main first, so the flow's new branch starts from the latest code; or continue on the branch the folder is on." }];
        state.thread = { ...state.thread, thread_id: "t2", title: body.title, status: "running", ...state.startAt, waiting: undefined };
        return [200, state.thread];
      case "POST /api/inbox/t1/act":
        if (body.decision === "reject" && !body.why) return [400, { error: "why is missing" }];
        state.thread = { ...state.thread, status: "running", current: "red", phase: "red", waiting: undefined };
        return [200, state.thread];
      case "GET /api/projects/demo/helper/prompts": return [200, state.prompts];
      case "POST /api/projects/demo/helper/sessions/s1/run": return [200, { command: body.command, ...state.run }];
      case "GET /api/projects/demo/helper/commands": return [200, [{ name: "gate", description: "explain the gate", plugin: "keelbot", source: "keel" }]];
      case "POST /api/projects/demo/helper/sessions":
        state.session = { id: "s1", project: "demo", root: "/work/demo", mode: body.mode ?? "ask", title: body.title ?? "", model: body.model ?? { provider: "fake", mode: "api", model: "fake" },
          status: "idle", tokens_in: 0, tokens_out: 0, tokens_cached: 0, cost_usd: 0, turns: 0, created_at: AT, updated_at: AT };
        return [200, state.session];
      case "POST /api/projects/demo/helper/sessions/s1/turn":
        state.messages.push({ n: state.messages.length + 1, role: "user", text: body.text, data: {}, at: AT });
        return [200, { session: "s1", call_id: "call-9", n: state.messages.length, ...state.turnImages }];
      // P1-2 /compact: the engine's {before, after} token counts for the session it just summarised
      case "POST /api/projects/demo/helper/sessions/s1/compact": return [200, { before: 48000, after: 6000 }];
      // P1-3 /export: the engine wrote the chat as Markdown into the project's .keel/chats/<sid>.md
      case "POST /api/projects/demo/helper/sessions/s1/export": return [200, { path: "/work/demo/.keel/chats/s1.md", messages: 2 }];
      // P1-3 /rename: the existing PATCH {title} of a KeelBot session; the chats list shows the new name
      case "PATCH /api/projects/demo/helper/sessions/s1": {
        const title = String(body.title ?? "").trim();
        if (body.mode) state.session = { ...state.session, mode: body.mode };     // 0.18.19 the same chat, another mode
        if (title) {
          state.session = { ...state.session, title };
          state.sessions = state.sessions?.map((x) => (x.id === "s1" ? { ...x, title } : x)) ?? null;
        }
        return [200, state.session];
      }
      case "GET /api/projects/demo/helper/sessions/s1": return [200, { ...state.session, messages: state.messages, busy: false }];
      case "GET /api/projects/demo/helper/sessions": return [200, state.sessions ?? (state.session ? [state.session] : [])];
      // 0.18.5 a new workflow, rule pack or agent
      case "POST /api/projects/demo/workflows/check": {
        const bad = String(body.yaml).includes("kind: oops");
        return [200, { name: "docs check", steps: 2, gates: 1, agents: [], commands: ["npm test"], edits_files: false, valid: !bad,
          errors: bad ? ["steps[1].kind: one of agent, code, gate, branch"] : [], warnings: [], lines: bad ? [{ line: 7, message: "steps[1].kind: one of agent, code, gate, branch" }] : [] }];
      }
      case "POST /api/projects/demo/workflows/import": return [200, { workflow: { id: "docs-check", steps: [{}, {}] }, review: {} }];
      case "POST /api/projects/demo/rules/check": return [200, { ok: true, errors: [], dry_run: { checked: 9, blocked: [{ kind: "edit", target: "db/V1__init.sql", agent: "implementer" }] } }];
      case "POST /api/projects/demo/rules/packs": return [200, { pack: { id: "no-sql-edits" } }];
      case "POST /api/projects/demo/agents": return [200, { id: "doc-writer", tools: body.tools }];
      // 0.18.6 skills
      case "GET /api/projects/demo/skills": return [200, [
        { id: "debugging", kind: "debugging", source: "keel", stack: "any", version: "v2", tokens: 2300, agents: state.skillAgents.debugging, when: "bug-repro", enabled: true },
        { id: "mine", kind: "knowledge", source: "yours", stack: "any", version: "v1", tokens: 40, agents: [], when: "on demand", enabled: false }]];
      case "GET /api/projects/demo/agents": return [200, [
        { id: "reproducer", label: "reproducer", overridden: [], skills: [], phases: ["bug-repro"] },
        { id: "investigator", label: "investigator", overridden: [], skills: [], phases: ["bug-investigate"] },
        { id: "lens-reviewer", label: "lens-reviewer", overridden: ["skills"], skills: state.reviewerSkills, phases: ["ship"] }]];
      case "PUT /api/projects/demo/agents/lens-reviewer": state.reviewerSkills = body.skills; return [200, { id: "lens-reviewer" }];
      case "POST /api/projects/demo/skills/import": return [200, { id: "changelog-entries", tokens: 30, agents: [] }];
      case "GET /api/marketplace/skills": return [200, { catalog_error: null,
        skills: [{ name: "flaky-tests", title: "Flaky tests", publisher: "keel", publisher_title: "keel", version: "1.0.0", summary: "Find why a test fails sometimes.",
          review: { tokens: 1200, sections: ["Steps"], description: "Flaky tests." }, installed: state.installed && { ...state.installed, sid: "flaky-tests" }, update: null }],
        builtin: [{ name: "debugging", summary: "Find the cause.", source: "keel", tokens: 2300, agents: state.skillAgents.debugging }] }];
      case "GET /api/marketplace/skills/flaky-tests": return [200, { name: "flaky-tests", title: "Flaky tests", publisher: "keel", publisher_title: "keel",
        version: "1.0.0", summary: "Find why a test fails sometimes.", review: { tokens: 1200, sections: ["Steps"], description: "Flaky tests." },
        installed: null, update: null, builtin: false, text: "---\nname: flaky-tests\n---\nx", fetch_error: null, refused: null }];
      case "GET /api/marketplace/skills/debugging": return [200, { name: "debugging", builtin: true, installed: null, update: null }];
      case "POST /api/projects/demo/skills/market/install":
        state.installed = { version: "1.0.0", agents: body.agents ?? [] };
        return [200, { skill: { id: body.name, agents: body.agents ?? [], tokens: 1200 }, market: { version: "1.0.0", catalog: "keel" } }];
      // 0.18.9 stacks
      case "GET /api/projects/demo/stacks": return [200, state.stacks];
      case "GET /api/projects/demo/stacks/zig-api": return [200, { ...state.stacks[1], yaml: "name: zig-api\nlane: api\n" }];
      case "DELETE /api/projects/demo/stacks/zig-api":
        state.stacks = state.stacks.filter((x) => x.name !== "zig-api");
        return [200, { name: "zig-api", removed: ".keel/stacks/zig-api.yml", back: null }];
      case "PUT /api/projects/demo/stacks/go/off":
        state.stacks = state.stacks.map((x) => (x.name === "go" ? { ...x, off: body.off } : x));
        return [200, { name: "go", off: body.off, all: body.off ? ["go"] : [] }];
      case "GET /api/projects/demo/stacks/suggest": return [200, { lane: query.get("lane") ?? "api", name: "demo-api", closest: null,
        read: ["build.zig"], detect: { files: ["build.zig"], extensions: [".zig"], package_deps: [] }, layers: [{ id: "unit", what: "pure" }], skills: {},
        keys: [{ key: "api_test_ac", what: "test one acceptance criterion" }, { key: "api_test_module", what: "run every test of the module" }],
        commands: { api_test_module: [{ cmd: "zig build test", from: "build.zig" }] }, tools: [] }];
      case "POST /api/projects/demo/stacks/check": {
        const yaml = body.yaml ?? `name: ${body.form?.name}\nlane: ${body.form?.lane}\ncommands: ${JSON.stringify(body.form?.commands ?? {})}\n`;
        const name = /name: (\S+)/.exec(yaml)?.[1] ?? null;
        const good = /lane: (api|web)/.test(yaml);
        return [200, { ok: good, errors: good ? [] : ["its lane is None"], warnings: ["there is no api_test_ac"], name, yaml, replaces: null,
          review: { lane: "api", layers: ["unit"], commands: { api_test_module: "zig build test" }, tools: [], skills: [], detect: {} } }];
      }
      case "POST /api/projects/demo/stacks/save": {
        const name = /name: (\S+)/.exec(String(body.yaml))![1];
        return [200, { stack: { name }, path: `.keel/stacks/${name}.yml`, replaced: false, warnings: [] }];
      }
      case "GET /api/marketplace/stacks": return [200, { catalog_error: null,
        stacks: [{ name: "zig", title: "Zig", publisher: "keel", publisher_title: "keel", version: "1.1.0", summary: "Zig with zig build.",
          review: { lane: "api", layers: ["unit"], commands: { api_test_module: "zig build test" }, tools: [] }, installed: null, update: null }],
        builtin: [{ name: "go", lane: "api", source: "keel", installable: false, used: true }] }];
      case "GET /api/marketplace/stacks/zig": return [200, { name: "zig", title: "Zig", publisher: "keel", publisher_title: "keel", version: "1.1.0",
        review: { lane: "api", layers: ["unit"], commands: { api_test_module: "zig build test" }, tools: [{ name: "zig-fmt", on: "edit" }] },
        installed: null, update: null, conflict: null, builtin: false, fetch_error: null, refused: null, check: { ok: true, errors: [], warnings: ["there is no api_test_ac"] } }];
      case "POST /api/projects/demo/stacks/market/install":
        return [200, { path: ".keel/stacks/zig.yml", market: { version: "1.1.0", catalog: "keel" }, stack: { name: "zig" } }];
      case "GET /api/connections": return [200, { providers: [
        { id: "fake", label: "Fake (demo)", modes: [{ id: "api", label: "Fake", ready: true, detail: "" }], selected: "api", key_set: false },
        { id: "claude", label: "Claude", modes: [{ id: "subscription", label: "Claude subscription", ready: true, detail: "" }], selected: "subscription", key_set: false },
        { id: "codex", label: "Codex", modes: [{ id: "subscription", label: "Codex login", ready: false, detail: "Log in" }], selected: "subscription", key_set: false },
      ], machine: [] }];
      case "GET /api/ui/focus": return [200, state.focus];
      case "PUT /api/ui/focus": {
        const pid = body.chat_project ?? body.project ?? state.focus.project;
        const chats = { ...state.focus.chats };
        if (body.close && pid) delete chats[pid];
        else if (body.chat && pid) chats[pid] = body.chat;
        state.focus = { project: body.project ?? state.focus.project, chats, by: body.by, client: body.client };
        return [200, state.focus];
      }
      case "POST /api/threads/t1/mode": state.thread = { ...state.thread, run_mode: body.mode }; return [200, state.thread];
      case "POST /api/threads/t1/stop": state.thread = { ...state.thread, status: "stopped", waiting: undefined }; return [200, state.thread];
      // round 3: @mentions, Fix mode's changes, Keep and Undo, questions, plugins
      case "GET /api/projects/demo/repo/files": return [200, { files: ["src/app.ts", "src/score.ts", "README.md", "web/src/App.tsx"], truncated: false }];
      case "GET /api/projects/demo/graph/search": return [200, { available: true, results: query.get("q") === "Sco"
        ? [{ id: "class:ScoreService", name: "ScoreService", kind: "class", file: "src/score.ts", line: 3, unit: "score", group: "" }] : [] }];
      case "GET /api/projects/demo/helper/sessions/s1/changes":
        return [200, query.has("turn") ? state.turnChanges[Number(query.get("turn"))] ?? [] : state.changes];
      case "POST /api/projects/demo/helper/sessions/s1/undo":
        state.changes = body?.path ? state.changes.filter((c) => c.path !== body.path) : [];
        return [200, state.changes];
      // 0.18.19 /undo and /redo of a whole answer: the last user message's turn, marked reverted (or not any more)
      case "POST /api/projects/demo/helper/sessions/s1/undo-turn":
      case "POST /api/projects/demo/helper/sessions/s1/redo-turn": {
        const undo = path.endsWith("/undo-turn");
        const users = state.messages.filter((m) => m.role === "user" && !!(m.data as { reverted?: boolean }).reverted !== undo);
        const u = undo ? users[users.length - 1] : users[0];
        if (!u) return [409, { error: undo ? "Nothing to undo: KeelBot has no answer in this chat that is not undone already." : "Nothing to redo: no answer was undone since the last message." }];
        const ns = state.messages.filter((m) => (m.n as number) >= (u.n as number)
          && (m === u || (m.role !== "user" && !state.messages.some((x) => x.role === "user" && (x.n as number) > (u.n as number) && (x.n as number) < (m.n as number)))))
          .map((m) => m.n as number);
        for (const m of state.messages) if (ns.includes(m.n as number)) m.data = { ...(m.data as object), reverted: undo || undefined };
        return [200, { n: u.n, text: u.text, files: state.turnFiles, messages: ns }];
      }
      case "POST /api/projects/demo/helper/sessions/s1/done": {
        const files = (body?.paths as string[] | undefined) ?? state.changes.map((c) => c.path);
        state.changes = state.changes.filter((c) => !files.includes(c.path));
        return [200, { ok: true, sha: "abc1234def", message: "feat(AC-1): the helper", files, checks: "pytest -q" }];
      }
      case "POST /api/approvals/q9/decide": return [200, { id: "q9", decision: body.decision }];
      case "GET /api/tui/plugins": return [200, state.plugins];
      case "GET /api/projects/demo/git/status": return [200, { branch: "keel/health", base: "main", ahead: 2, behind: 0,
        changes: [{ path: "src/app.ts", status: "M" }, { path: "notes.md", status: "??" }] }];
      case "GET /api/projects/demo/git/log": return [200, [{ sha: "abc1234", author: "Ada", date: "2026-10-16", subject: "feat: health" }]];
      case "GET /api/projects/demo/git/diff": return [200, { diff: `--- a/src/app.ts\n+++ b/src/app.ts\n@@ -1,2 +1,2 @@\n-const a = 1;\n+const a = 2;\n keep();\n${query.get("path") ? "" : ""}` }];
      case "POST /api/projects/demo/git/push": return [200, { pushed: true }];
      // 0.18.9 a switch the folder's changes block, unless keel stashes them first
      case "POST /api/projects/demo/git/switch":
        if (body.branch === "main" && !body.stash) return [409, { error: "git could not switch to main: error: Your local changes to the following files would be overwritten by checkout: docs/spec.md",
          hint: "Stash them and switch, or commit them first." }];
        return [200, { branch: body.branch, created: false, ...(body.stash ? { stashed: { from: "keel/health", message: "keel: changes" } } : {}) }];
      case "GET /api/projects/demo/review/open": {
        const ref = query.get("ref") ?? "";
        if (ref === "404") return [404, { error: "No pull request #404" }];
        return [200, reviewView(ref ? (/^\d+$/.test(ref) ? `pr:${ref}` : `branch:${ref}`) : "pr:7")];
      }
      case "GET /api/projects/demo/review/ai": return [200, { overview: { status: "done", result: { summary: "Saves the page size per user.", risk: "low",
        risk_why: "a cache only", files: [{ path: "src/Prefs.kt", what: "keeps the size in a map" }] } }, findings: { status: "done", result: { findings: [
        { id: "f1", severity: "should_fix", title: "the cache is never cleared", path: "src/Prefs.kt", line: 4, side: "RIGHT", check: "confirmed" }] } }, decisions: {} }];
      case "GET /api/projects/demo/review/diff": return [200, { path: query.get("path"), diff: query.get("path") === "src/Prefs.kt" ? PREFS_DIFF
        : "--- /dev/null\n+++ b/src/PrefsTest.kt\n@@ -0,0 +1,3 @@\n+class PrefsTest {\n+    fun saves() {}\n+}\n", binary: false, truncated: false }];
      case "GET /api/projects/demo/review/prs": return [200, { host: { path: "acme/shop" }, me: "me", counts: { review: 1 }, note: null, prs: [
        { number: 7, title: "Save the page size", author: "ana", branch: "feat/paging", base: "main" }, { number: 8, title: "Mine", author: "me", branch: "feat/mine", base: "main" }] }];
      case "POST /api/projects/demo/review/drafts": {
        const d = { id: `dr${state.review.drafts.length + 1}`, path: body.path, line: body.line, side: body.side, body: body.body };
        state.review.drafts.push(d);
        return [200, d];
      }
      case "POST /api/projects/demo/review/viewed": state.review.viewed.push(body.path); return [200, { viewed: state.review.viewed }];
      case "POST /api/projects/demo/review/threads/th1/reply":
        state.review.threads[0].comments.push({ id: "2", author: "me", body: body.body, at: AT, url: null });
        return [200, reviewView(body.key)];
      case "POST /api/projects/demo/review/threads/th1/resolve": state.review.threads[0].resolved = true; return [200, reviewView(body.key)];
      case "POST /api/projects/demo/review/ai/overview": case "POST /api/projects/demo/review/ai/findings": return [200, { overview: null, findings: null, decisions: {} }];
      case "POST /api/projects/demo/review/submit":
        state.review.submitted = body;
        return [200, { posted: state.review.drafts.length, in_body: 0, event: body.event, url: "https://github.com/acme/shop/pull/7#review-1", view: reviewView(body.key) }];
      default:
        // keel 0.18.18 any KeelBot permission question's answer
        if (method === "POST" && /^\/api\/approvals\/[\w-]+\/decide$/.test(path)) return [200, { id: path.split("/")[3], decision: body.decision }];
        if (method === "GET" && path.startsWith("/api/skills/")) {
          const sid = path.slice("/api/skills/".length);
          return [200, { id: sid, kind: "debugging", stack: "any", source: "keel", version: "v2", tokens: 2300, agents: state.skillAgents[sid] ?? [],
            when: "bug-repro", enabled: true, body: "# Debugging\n\nFind the **cause** first.", refs: [{ path: "references/bisect.md", tokens: 900 }] }];
        }
        if (method === "PUT" && path.startsWith("/api/projects/demo/skills/")) {
          const sid = path.slice("/api/projects/demo/skills/".length);
          state.skillAgents[sid] = body.agents;
          return [200, { id: sid, agents: body.agents }];
        }
        return [404, { error: `stub: no ${key}` }];
    }
  };

  /** KeelBot's answer arrives (the stub's engine stored it). */
  const answer = (text: string, data: Record<string, unknown> = {}) => state.messages.push({ n: state.messages.length + 1, role: "helper", text,
    call_id: "call-9", data: { provider: "fake", model: "fake", tokens_in: 1200, tokens_out: 300, ...data }, at: AT });

  const handle = (method: string, url: string, body: unknown, headers: Record<string, string>) => {
    const u = new URL(url, "http://stub");
    calls.push({ method, path: u.pathname, query: u.search, body, headers });
    return route(method, u.pathname, u.searchParams, body);
  };

  const fetch: typeof globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const h = Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>).map(([k, v]) => [k.toLowerCase(), v]));
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const [status, data] = handle(init?.method ?? "GET", url, body, h);
    return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
  };

  return { calls, state, fetch, handle, answer, workflow };
}

/** The same stub as an http server (the contract test): SSE on /api/events sends what `sse` is given. */
export async function stubServer(): Promise<{ url: string; stub: ReturnType<typeof stubKeel>; sse: (event: string, data: unknown) => void; close: () => Promise<void> }> {
  const stub = stubKeel();
  const streams: import("node:http").ServerResponse[] = [];
  const server: Server = createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => {
      const headers = Object.fromEntries(Object.entries(req.headers).map(([k, v]) => [k, String(v)]));
      if (req.url?.startsWith("/api/events")) {
        stub.calls.push({ method: "GET", path: "/api/events", query: req.url.slice("/api/events".length), body: undefined, headers });
        res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
        res.write(":connected\n\n");
        streams.push(res);
        return;
      }
      const [status, data] = stub.handle(req.method ?? "GET", req.url ?? "/", raw ? JSON.parse(raw) : undefined, headers);
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(data));
    });
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const port = (server.address() as { port: number }).port;
  return {
    url: `http://127.0.0.1:${port}`, stub,
    sse: (event, data) => streams.forEach((s) => s.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    close: () => new Promise((r) => { streams.forEach((s) => s.end()); server.closeAllConnections?.(); server.close(() => r()); }),
  };
}

/** Until React has no work queued: with React 19 Ink's updates render in Scheduler tasks (and their effects in more),
 *  not at once as with React 18, so a fixed wait can end between a key's render and its effects. An idle-priority
 *  task runs only once every task before it ran; twice, for the work those tasks queued. */
export async function settle(): Promise<void> {
  for (let i = 0; i < 2; i++) {
    await new Promise<void>((r) => unstable_scheduleCallback(unstable_IdlePriority, () => r()));
    await new Promise<void>((r) => setImmediate(r));
  }
}

/** `ms` milliseconds, then until the screen caught up (settle). */
export const tick = async (ms = 20) => {
  await new Promise((r) => setTimeout(r, ms));
  await settle();
};

export async function waitFor(cond: () => boolean, ms = 4000): Promise<void> {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error("waitFor timed out");
    await tick(20);
  }
}
