// v0.16.0 MSW handlers for the marketplace: /api/marketplace (search, one plugin, refresh) and /api/plugins (installed,
// install, update, rollback, on/off, remove, restart, sources, rules, sets, install from a file), backed by a small
// in-memory catalog (reset per test).

import { http, HttpResponse } from "msw";
import type {
  InstalledPlugin,
  MarketPlugin,
  PluginProblem,
  PluginSet,
  RestartState,
  Rules,
  Source,
} from "../marketplaceApi";

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));

const CATALOG: MarketPlugin[] = [
  {
    name: "code",
    title: "Code",
    publisher: "keel",
    verified: true,
    category: "code",
    trust: "code",
    summary: "The Code page: files, search and an editor.",
    versions: [
      {
        version: "1.0.0",
        released: "2026-10-09",
        requires: { sdk: 1, keel: ">=0.15.4" },
        permissions: { workspace: "write", pages: true },
      },
    ],
    checks: [
      "sha256 matches the catalog",
      "signed by keel, a key this keel trusts",
    ],
  },
  {
    name: "db",
    title: "Database",
    publisher: "keel",
    verified: true,
    category: "code",
    trust: "code",
    tags: ["sql"],
    summary: "Connect a database. Agents get read-only SQL tools.",
    versions: [
      {
        version: "1.4.0",
        released: "2026-10-02",
        requires: { sdk: 1, keel: ">=0.15.4", plugins: { code: ">=1.0.0" } },
        permissions: {
          secrets: ["database"],
          network: ["from-connections"],
          workspace: "read",
        },
      },
      {
        version: "1.3.0",
        released: "2026-09-18",
        requires: { sdk: 1, keel: ">=0.15.4", plugins: { code: ">=1.0.0" } },
        permissions: { secrets: ["database"] },
      },
    ],
    checks: [
      "sha256 matches the catalog",
      "signed by keel, a key this keel trusts",
      "its ids start with db",
    ],
  },
  {
    name: "wiki",
    title: "Wiki",
    publisher: "keel",
    verified: true,
    category: "knowledge",
    trust: "web",
    summary: "The project's knowledge base as pages.",
    versions: [
      { version: "1.1.0", requires: { sdk: 1 }, permissions: { pages: true } },
    ],
  },
  {
    name: "relnotes",
    title: "Release notes",
    publisher: "ana-k",
    verified: false,
    category: "other",
    trust: "content",
    summary: "A workflow that writes release notes.",
    versions: [{ version: "0.4.0", permissions: {} }],
  },
];

export function createMarket() {
  return {
    catalog: clone(CATALOG),
    installed: [] as InstalledPlugin[],
    restart: {
      pending: false,
      scheduled: false,
      supervised: true,
      running: 0,
    } as RestartState,
    problems: [] as PluginProblem[],
    sources: [
      {
        id: "keel",
        title: "keel marketplace",
        url: "https://keel-studio.github.io/keel-marketplace/v1/index.json",
        key: "RWQBAgMEBQYHCHm1Vi6P5lT5QHixEuipi6eQH4U65pW+1+DjkQutBJZk",
        on: true,
        official: true,
      },
    ] as Source[],
    status: [
      { id: "keel", ok: true, problem: null as string | null, old: false },
    ],
    rules: {
      agents_may_ask: true,
      allow_unverified: false,
      check_daily: true,
      restart_when_idle: false,
    } as Rules,
    sets: [
      {
        id: "developer",
        title: "Developer",
        summary: "Read and change code with agents.",
        plugins: ["code", "db", "wiki"],
      },
      {
        id: "tickets",
        title: "Tickets",
        summary: "Start flows from your issues.",
        plugins: ["tasks", "jira"],
      },
    ] as PluginSet[],
    removed: [] as { name: string; data: string | null }[],
    searches: [] as { q: string; category: string }[],
  };
}
export type Market = ReturnType<typeof createMarket>;

export function marketHandlers(
  m: Market,
  log: (req: Request) => Promise<Record<string, unknown>>,
) {
  const entry = (name: string) => m.catalog.find((p) => p.name === name);
  const hit = (p: MarketPlugin) => {
    const have = m.installed.find((x) => x.name === p.name);
    const { versions, checks: _c, ...rest } = p;
    const newest = versions?.[0]?.version;
    // the engine's Hit (docs/plugins/13-step4-contract.md §12)
    return {
      ...rest,
      publisher_title: p.publisher,
      latest: newest,
      version: newest,
      permissions: versions?.[0]?.permissions ?? {},
      installed: have?.version ?? null,
      installed_from: have?.from ?? null,
      update: have && have.version !== newest ? newest : null,
      fits: true,
      why_not: null,
      revoked: null,
      old: false,
    };
  };
  const notFound = (name: string) =>
    HttpResponse.json(
      {
        error: `No plugin ${name} in the catalogs.`,
        hint: "Search for another word.",
      },
      { status: 404 },
    );
  const pending = () => ({
    pending: m.restart.pending,
    changes: m.installed.map((x) => ({
      name: x.name,
      now: null,
      next: x.version ?? null,
    })),
  });
  const install = (name: string, version?: string) => {
    const p = entry(name)!;
    m.installed.push({
      name,
      title: p.title,
      version: version ?? p.versions?.[0]?.version,
      from: "marketplace",
      on: true,
      parts: ["engine", "web"],
      status: "restart",
      can_remove: true,
    });
    m.restart = { ...m.restart, pending: true };
  };
  return [
    http.get("/api/marketplace", ({ request }) => {
      const u = new URL(request.url);
      const q = (u.searchParams.get("q") ?? "").toLowerCase();
      const category = u.searchParams.get("category") ?? "";
      m.searches.push({ q, category });
      const hits = m.catalog
        .filter(
          (p) =>
            (!category || p.category === category) &&
            (!q ||
              [p.name, p.title, p.summary, ...(p.tags ?? [])].some((s) =>
                (s ?? "").toLowerCase().includes(q),
              )),
        )
        .map(hit);
      return HttpResponse.json({
        plugins: hits,
        sources: m.status,
        categories: [
          "code",
          "review",
          "knowledge",
          "tickets",
          "product",
          "other",
        ],
      });
    }),
    http.post("/api/marketplace/refresh", async ({ request }) => {
      await log(request);
      return HttpResponse.json({ sources: m.status });
    }),
    http.get("/api/marketplace/:name", ({ params }) => {
      const p = entry(String(params.name));
      if (!p) return notFound(String(params.name));
      const needs = p.versions?.[0]?.requires?.plugins ?? {};
      const step = (e: MarketPlugin, neededBy: string | null) => ({
        name: e.name,
        version: e.versions?.[0]?.version,
        title: e.title,
        trust: e.trust,
        publisher: e.publisher,
        publisher_title: e.publisher,
        verified: e.verified,
        permissions: e.versions?.[0]?.permissions ?? {},
        size: 1000,
        needed_by: neededBy,
        source: "keel",
      });
      const install = [
        ...Object.keys(needs)
          .filter((n) => !m.installed.some((x) => x.name === n) && entry(n))
          .map((n) => step(entry(n)!, p.name)),
        step(p, null),
      ];
      return HttpResponse.json({
        ...hit(p),
        versions: p.versions,
        needs,
        checks: p.checks,
        plan: {
          name: p.name,
          version: p.versions?.[0]?.version,
          title: p.title,
          source: "keel",
          install,
          turn_on: [],
          checks: p.checks,
        },
        refused: null,
      });
    }),
    http.get("/api/plugins/installed", () =>
      HttpResponse.json({
        plugins: m.installed,
        restart: m.restart,
        pending_restart: pending(),
        problems: m.problems,
        mode: "on",
      }),
    ),
    http.post("/api/plugins/install", async ({ request }) => {
      const b = await log(request);
      const name = String(b.name);
      if (!entry(name)) return notFound(name);
      for (const n of Object.keys(
        entry(name)!.versions?.[0]?.requires?.plugins ?? {},
      ))
        if (!m.installed.some((x) => x.name === n) && entry(n)) install(n);
      install(name, b.version as string | undefined);
      const version = m.installed.find((x) => x.name === name)!.version;
      return HttpResponse.json({
        name,
        title: entry(name)!.title,
        version,
        installed: [{ name, version, from: null }],
        turned_on: [],
        pending_restart: pending(),
      });
    }),
    http.post("/api/plugins/install-file", async ({ request }) => {
      const b = await log(request);
      m.installed.push({
        name: "hello",
        title: "Hello",
        version: "0.1.0",
        from: "file",
        on: true,
        parts: ["content"],
      });
      m.restart = { ...m.restart, pending: true };
      return HttpResponse.json({
        name: "hello",
        version: "0.1.0",
        source: "file",
        path: b.path,
      });
    }),
    http.post("/api/plugins/restart", async ({ request }) => {
      const b = await log(request);
      if (b.now)
        return HttpResponse.json(
          { restarting: true, scheduled: false, running: m.restart.running },
          { status: 202 },
        );
      m.restart = { ...m.restart, scheduled: true };
      return HttpResponse.json(
        { restarting: false, scheduled: true, running: m.restart.running },
        { status: 202 },
      );
    }),
    http.get("/api/plugins/sources", () =>
      HttpResponse.json({ sources: m.sources }),
    ),
    http.put("/api/plugins/sources", async ({ request }) => {
      const b = await log(request);
      // the official one may only be turned on or off
      const sent = b.sources as Source[];
      const official = m.sources.find((s) => s.id === "keel")!;
      m.sources = [
        {
          ...official,
          on: sent.find((s) => s.id === "keel")?.on ?? official.on,
        },
        ...sent.filter((s) => s.id !== "keel"),
      ];
      return HttpResponse.json({ sources: m.sources });
    }),
    http.get("/api/plugins/rules", () => HttpResponse.json(m.rules)),
    http.put("/api/plugins/rules", async ({ request }) => {
      m.rules = (await log(request)) as unknown as Rules;
      return HttpResponse.json(m.rules);
    }),
    http.get("/api/plugins/sets", () =>
      HttpResponse.json({
        sets: m.sets.map((s) => ({
          ...s,
          missing: s.plugins.filter(
            (n) => !m.installed.some((x) => x.name === n),
          ),
          off: s.plugins.filter((n) =>
            m.installed.some((x) => x.name === n && x.on === false),
          ),
        })),
      }),
    ),
    http.post("/api/plugins/:name/update", async ({ request, params }) => {
      await log(request);
      const p = m.installed.find((x) => x.name === params.name)!;
      const from = p.version ?? null;
      p.previous = p.version;
      p.version =
        typeof p.update === "string"
          ? p.update
          : entry(p.name)?.versions?.[0]?.version;
      p.update = null;
      m.restart = { ...m.restart, pending: true };
      return HttpResponse.json({
        name: p.name,
        version: p.version,
        title: p.title,
        installed: [{ name: p.name, version: p.version, from }],
        turned_on: [],
        pending_restart: pending(),
      });
    }),
    http.post("/api/plugins/:name/rollback", async ({ request, params }) => {
      await log(request);
      const p = m.installed.find((x) => x.name === params.name)!;
      const from = p.version ?? null;
      [p.version, p.previous] = [p.previous ?? p.version, null];
      m.restart = { ...m.restart, pending: true };
      return HttpResponse.json({
        name: p.name,
        version: p.version,
        from,
        pending_restart: pending(),
      });
    }),
    http.put("/api/plugins/:name", async ({ request, params }) => {
      const b = await log(request);
      const p = m.installed.find((x) => x.name === params.name)!;
      p.on = Boolean(b.on);
      m.restart = { ...m.restart, pending: true };
      return HttpResponse.json({
        name: p.name,
        on: p.on,
        also: [],
        pending_restart: pending(),
      });
    }),
    http.delete("/api/plugins/:name", async ({ request, params }) => {
      await log(request);
      m.removed.push({
        name: String(params.name),
        data: new URL(request.url).searchParams.get("data"),
      });
      const gone = m.installed.find((x) => x.name === params.name);
      m.installed = m.installed.filter((x) => x.name !== params.name);
      m.restart = { ...m.restart, pending: true };
      return HttpResponse.json({
        name: params.name,
        removed: gone?.version,
        data: new URL(request.url).searchParams.get("data"),
        back_to_image: null,
        pending_restart: pending(),
      });
    }),
  ];
}
