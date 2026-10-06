// MSW handlers for every /api route the web uses, backed by a small in-memory db (reset per test).

import { http, HttpResponse } from "msw";
import type { Cap, IndexStatus, Settings, Stack, ThreadState, Workflow } from "../api";
import * as fx from "./fixtures";

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

export function createDb() {
  return {
    projects: clone(fx.projects),
    flows: { "ludus-engine": { thread: clone(fx.thread), workflow: clone(fx.featureWorkflow) } } as Record<string, { thread: ThreadState | null; workflow: Workflow | null }>,
    workflows: [clone(fx.featureWorkflow), clone(fx.fixWorkflow), clone(fx.initWorkflow)],
    overrides: { "ludus-engine": { cap_tokens: 600000 } } as Record<string, Partial<Settings>>,
    general: clone(fx.generalSettings),
    notifications: clone(fx.notifications),
    nset: clone(fx.notificationSettings),
    memory: clone(fx.memory),
    caps: clone(fx.caps) as Cap[],
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
    calls: [] as { method: string; path: string; body: unknown }[],
  };
}
export type Db = ReturnType<typeof createDb>;

export function handlers(db: Db) {
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
    http.get("/api/projects", () => HttpResponse.json(db.projects)),
    http.post("/api/projects", async ({ request }) => {
      const b = await log(request);
      const p = { ...db.projects[2], id: "new", name: String(b.name ?? "new"), root: String(b.root) };
      db.projects.push(p);
      return HttpResponse.json(p);
    }),
    http.get("/api/projects/:pid/flow", ({ params }) =>
      HttpResponse.json(db.flows[params.pid as string] ?? { thread: null, workflow: null })),
    http.post("/api/projects/:pid/flows", async ({ request, params }) => {
      const b = await log(request);
      const w = db.workflows.find((x) => x.id === b.workflow_id)!;
      const t: ThreadState = { ...clone(fx.thread), status: "running", waiting: undefined, current: w.steps[0].id, project_id: params.pid as string, workflow_id: w.id, title: String(b.title) };
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
    http.get("/api/projects/:pid/repo/file", () => HttpResponse.json(fx.file)),
    http.get("/api/projects/:pid/repo/commits", () => HttpResponse.json([{ sha: "a81c3f0aa", message: "feat(AC-002) refuse a negative score", author: "implementer", at: new Date().toISOString() }])),
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
      await log(request);
      return HttpResponse.json({ workflow: fx.fixWorkflow, review: { agents: ["reproducer"], mcp: [], gates: 1, est_tokens: 60000, edits_files: true } });
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
    http.get("/api/projects/:pid/caps", () => HttpResponse.json(db.caps)),
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
  ];
}
