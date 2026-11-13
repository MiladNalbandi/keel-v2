// v0.17.2: MSW handlers for keel's rules (Build › Rules, "Rules now" on the Flow page), over a small in-memory copy of
// the engine's rules view (runtime/rules_view.py).

import { http, HttpResponse } from "msw";
import type { Enforced, RulePack, RulesNow, RulesView } from "../rulesApi";

// #101 one engine: keel's harness checks every kind of rule before the action
const ALL: Enforced = { harness: "before" };

export const NO_DEPS_YAML = `schema: 1
kind: rules
name: no-new-dependencies
title: No new dependencies
version: 1.0.0
publisher: keel
summary: Agents never add a package.
rules:
  - id: no-install
    kind: bash
    match: [npm install, pnpm add]
    reason: Adding a package is a person's decision.
`;

export const libraryPacks = (): RulePack[] => [
  { id: "no-new-dependencies", name: "no-new-dependencies", title: "No new dependencies", version: "1.0.0", publisher: "keel",
    summary: "Agents never add a package.", yaml: NO_DEPS_YAML,
    rules: [{ id: "no-install", kind: "bash", match: ["npm install", "pnpm add"], how: "contains", phases: "all", reason: "Adding a package is a person's decision.", enforced: ALL }] },
  { id: "strict-secrets", name: "strict-secrets", title: "Strict secrets", version: "1.0.0", publisher: "keel",
    summary: "Key files are never read.", yaml: "schema: 1\nname: strict-secrets\n",
    rules: [{ id: "no-key-files", kind: "read", match: ["**/*.pem"], how: "glob", phases: "all", reason: "Keys stay out of the conversation.", enforced: ALL }] },
];

export function rulesView(packs: RulePack[] = [], off: string[] = []): RulesView {
  return {
    configured: true, config_file: ".keel/config.yml", rules_folder: ".keel/rules",
    builtins: [
      { id: "env-files", kind: "edit, read, shell", phases: "always", words: ".env files hold secrets: agents never read, print or change them.", can_off: false, off: false, enforced: ALL, saved_in: "keel (built in)" },
      { id: "force-push", kind: "shell", phases: "always", words: "git push --force and git push -f are refused.", can_off: false, off: false, enforced: ALL, saved_in: "keel (built in)" },
      { id: "read-twice", kind: "read", phases: "always", words: "Reading the same unchanged file twice in one step is refused.", can_off: true, off: off.includes("read-twice"), enforced: ALL, saved_in: "keel (built in)" },
      { id: "push-gate", kind: "shell", phases: "always", words: "An agent's git push waits for keel's push blockers.", can_off: true, off: off.includes("push-gate"), enforced: ALL, saved_in: "keel (built in)" },
    ],
    lists: [
      { key: "guards.protected", kind: "edit, read, shell", words: "Files that hold secrets.", form: "globs", value: ["**/.env", "**/.env.*", "!**/.env.example"], default: [], enforced: ALL, saved_in: "keel's default (saved to .keel/config.yml when you change it)" },
      { key: "guards.read_guard_max_lines", kind: "read", words: "A file with more lines than this is read in parts. 0 = off.", form: "number", value: 2000, default: 2000, enforced: ALL, saved_in: ".keel/config.yml" },
      { key: "rules.push_gate", kind: "shell", words: "The push gate.", form: "bool", value: true, default: true, enforced: ALL, saved_in: "keel's default (saved to .keel/config.yml when you change it)" },
    ],
    packs: packs.map((p) => ({ ...p, off: off.includes(`pack/${p.id}`), file: `.keel/rules/${p.id}.yaml`, saved_in: `.keel/rules/${p.id}.yaml`,
      source: p.source ?? "file" })),
    pack_errors: [],
    engines: [{ engine: "harness", protection: "before", words: "every edit, shell command, read and MCP call is checked before it runs" }],
    buckets: [{ bucket: "api-main", what: "backend production code" }, { bucket: "api-test", what: "backend tests" }],
    phases: [{ phase: "red", meaning: "Write the failing test.", matrix: { "api-main": "deny", "api-test": "allow" } },
             { phase: "green", meaning: "Make it pass.", matrix: { "api-main": "allow", "api-test": "deny" } }],
  };
}

export function rulesNow(tid: string): RulesNow {
  return {
    thread_id: tid, phase: "green", meaning: "Make the failing test pass. Tests are frozen.", lane: "api", status: "running", current: "green",
    may: [{ bucket: "api-main", what: "backend production code", rule: "allow", words: "edit" }],
    may_not: [{ bucket: "api-test", what: "backend tests", rule: "deny", words: "read only" }],
    always: [{ bucket: "protected-env", what: ".env files (secrets)", words: "never read or changed" },
             { bucket: "keel-rules", what: ".keel/config.yml and .keel/rules/", words: "never changed by an agent" }],
    packs: [{ pack: "no-new-dependencies", name: "No new dependencies", rule: "no-install", kind: "bash", reason: "Adding a package is a person's decision." }],
    unlocks: [{ path: "src/test/kotlin/FlakyTest.kt", phase: "green", reason: "fix the broken fixture" }],
    shell: ["git push --force and git push -f are refused."],
    engine: { provider: "copilot", protection: "before", words: "every edit, shell command, read and MCP call is checked before it runs" },
    last_command_failure: { command: "./gradlew test", output: "3 tests failed", step: "green", agent: "implementer" },
    fired: [{ reason: "Adding a package is a person's decision. (rule no-new-dependencies/no-install)", rule: "no-new-dependencies/no-install",
      count: 2, phase: "green", examples: ["npm install left-pad"], source: "before the action" }],
  };
}

export function createRulesDb() {
  return { installed: [] as RulePack[], off: [] as string[], config: [] as Record<string, unknown>[] };
}
export type RulesDb = ReturnType<typeof createRulesDb>;

export function rulesHandlers(db: RulesDb, log: (req: Request) => Promise<unknown>) {
  const view = () => rulesView(db.installed, db.off);
  return [
    http.get("/api/projects/:pid/rules", () => HttpResponse.json(view())),
    http.get("/api/rules/library", () => HttpResponse.json(libraryPacks())),
    http.put("/api/projects/:pid/rules/config", async ({ request }) => {
      await log(request);
      const patch = (await request.clone().json()) as { rules?: { off?: string[] } };
      db.config.push(patch);
      if (patch.rules?.off) {
        if (patch.rules.off.includes("env-files")) return HttpResponse.json({ error: "These rules always stay on: env-files." }, { status: 400 });
        db.off = patch.rules.off;
      }
      return HttpResponse.json(view());
    }),
    http.post("/api/projects/:pid/rules/library/:id/install", async ({ request, params }) => {
      await log(request);
      const p = { ...libraryPacks().find((x) => x.id === params.id)!, source: "library" as const };
      db.installed.push(p);
      return HttpResponse.json({ pack: p, view: view() });
    }),
    http.post("/api/projects/:pid/rules/packs", async ({ request }) => {
      await log(request);
      const { yaml } = (await request.clone().json()) as { yaml: string };
      const id = /^name:\s*(\S+)/m.exec(yaml)?.[1] ?? "x";
      const title = /^title:\s*(.+)/m.exec(yaml)?.[1] ?? id;
      if (!/rules:/.test(yaml)) return HttpResponse.json({ error: "The rule pack is not valid.", hint: "rules: a list with at least one rule" }, { status: 422 });
      const p: RulePack = { id, name: id, title, version: "1.0.0", publisher: "local", summary: "", source: "file",
        rules: [{ id: "r", kind: "bash", match: ["x"], how: "contains", phases: "all", reason: "because" }] };
      db.installed.push(p);
      return HttpResponse.json({ pack: p, view: view() });
    }),
    http.put("/api/projects/:pid/rules/packs/:id", async ({ request, params }) => {
      await log(request);
      const p = db.installed.find((x) => x.id === params.id)!;
      return HttpResponse.json({ pack: p, view: view() });
    }),
    http.delete("/api/projects/:pid/rules/packs/:id", async ({ request, params }) => {
      await log(request);
      db.installed = db.installed.filter((x) => x.id !== params.id);
      return HttpResponse.json(view());
    }),
    http.get("/api/projects/:pid/rules/packs/:id/export", ({ params }) => new HttpResponse(params.id === "no-new-dependencies" ? NO_DEPS_YAML : `id: ${params.id}\n`, { headers: { "Content-Type": "text/yaml" } })),
    http.post("/api/projects/:pid/rules/check", async ({ request }) => {
      await log(request);
      const { yaml } = (await request.clone().json()) as { yaml: string };
      if (!/reason:\s*(?!""\s*$)\S/m.test(yaml)) {
        const at = yaml.split("\n").findIndex((l) => l.trim().startsWith("- id:")) + 1;
        const message = "rules[1].reason: say in plain words why it is refused (the agent reads it)";
        return HttpResponse.json({ ok: false, errors: [message], lines: [{ line: at || 1, message }] });
      }
      return HttpResponse.json({ ok: true, errors: [], dry_run: { checked: 37, blocked: [{ phase: "green", agent: "implementer", kind: "bash", target: "npm install left-pad", reason: "x" }] } });
    }),
    http.get("/api/threads/:tid/rules-now", ({ params }) => HttpResponse.json(rulesNow(params.tid as string))),
  ];
}
