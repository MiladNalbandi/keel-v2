// v0.17.2 keel's rules: the Rules page (Build › Rules) and "Rules now" on the Flow page (docs/CONTRACT.md, "v0.17.2: rules").
// The rules are files in the project: .keel/config.yml (guards, mcp, rules, boundaries) and .keel/rules/<id>.yaml (packs).

import { del, get, getText, post, put } from "./api";

/** When an engine checks a rule: before the action, only for wrapped programs, after the step (put back), or not at all. */
export type When = "before" | "wrapped" | "after" | "none";
export type Engine = "claude" | "opencode" | "codex" | "copilot" | "api";
export type Enforced = Record<Engine, When>;
export type Protection = "before" | "partial" | "after";

export type BuiltinRule = { id: string; kind: string; phases: string; words: string; can_off: boolean; off: boolean; enforced: Enforced; saved_in: string;
  /** 0.18.64 off until switched on (rules.on): other-projects */
  opt_in?: boolean };
export type RuleList = {
  key: string; kind: string; words: string; form: "globs" | "texts" | "number" | "bool" | "boundaries";
  value: unknown; default: unknown; enforced: Enforced; saved_in: string;
};
export type PackRule = {
  id: string; kind: "bash" | "edit" | "read" | "mcp"; match: string[]; how: string; phases: string | string[];
  lanes?: string[]; except?: string[]; reason: string; enforced?: Enforced;
};
/** docs/rules/pack-format.md: one YAML file, shaped like a plugin manifest so a marketplace can ship it unchanged. */
export type PackSource = "library" | "file" | "marketplace";
export type RulePack = {
  id: string; name: string; title: string; version: string; publisher: string; summary: string; stack?: string;
  requires?: { keel: string; plugins: Record<string, string> };
  rules: PackRule[]; file?: string; off?: boolean; saved_in?: string; yaml?: string;
  /** where an installed pack came from (.keel/rules/.installed.json); "file" = written or imported here */
  source?: PackSource; edited?: boolean; update?: string;
};
export type InstalledPack = Pick<RulePack, "name" | "title" | "version" | "publisher" | "summary" | "requires" | "source" | "file" | "off" | "edited" | "update">;
export type EngineProtection = { engine: Engine; protection: Protection; words: string };
export type RulesView = {
  configured: boolean; config_file: string; rules_folder: string;
  builtins: BuiltinRule[]; lists: RuleList[]; packs: RulePack[]; pack_errors: { file: string; errors: string[] }[];
  engines: EngineProtection[]; buckets: { bucket: string; what: string }[];
  phases: { phase: string; meaning: string; matrix: Record<string, string> }[];
};
export type DryRunHit = { at?: string; thread?: string; agent?: string; phase?: string; tool?: string; kind: string; target: string; reason: string };
export type RuleCheck = {
  ok: boolean; errors: string[]; /** each error at its line (the editor marks it) */ lines?: { line: number; message: string }[];
  pack?: RulePack; /** the pack as keel saves it (rules/packs.py normalize) */ formatted?: string;
  dry_run?: { checked: number; blocked: DryRunHit[] };
};

export type NowBucket = { bucket: string; what: string; rule?: string; words: string };
export type Fired = { reason: string; rule?: string | null; count: number; phase?: string; examples: string[]; source: string; last_at?: string; step?: string };
export type RulesNow = {
  thread_id: string; phase: string; meaning: string; lane?: string | null; status?: string; current?: string | null;
  may: NowBucket[]; may_not: NowBucket[]; always: NowBucket[];
  packs: { pack: string; name: string; rule: string; kind: string; reason: string }[];
  unlocks: { path: string; phase: string; reason?: string | null }[];
  shell: string[];
  engine: { provider?: string | null; protection?: Protection | null; words?: string | null };
  last_command_failure?: { command: string; output: string; step?: string; agent?: string } | null;
  fired?: Fired[];
};

const e = encodeURIComponent;

export const rulesApi = {
  view: (pid: string) => get<RulesView>(`/projects/${e(pid)}/rules`),
  setConfig: (pid: string, patch: Record<string, unknown>) => put<RulesView>(`/projects/${e(pid)}/rules/config`, patch),
  library: () => get<RulePack[]>("/rules/library"),
  install: (pid: string, id: string) => post<{ pack: RulePack; view: RulesView }>(`/projects/${e(pid)}/rules/library/${e(id)}/install`, {}),
  /** v0.17.2 the installable-item routes a marketplace install uses */
  installed: (pid: string) => get<InstalledPack[]>(`/projects/${e(pid)}/rule-packs`),
  installPack: (pid: string, body: { source: PackSource; name?: string; version?: string; yaml?: string; replace?: boolean }) =>
    post<{ pack: RulePack; installed: InstalledPack[] }>(`/projects/${e(pid)}/rule-packs/install`, body),
  uninstall: (pid: string, name: string) => post<{ installed: InstalledPack[] }>(`/projects/${e(pid)}/rule-packs/uninstall`, { name }),
  add: (pid: string, yaml: string) => post<{ pack: RulePack; view: RulesView }>(`/projects/${e(pid)}/rules/packs`, { yaml }),
  edit: (pid: string, id: string, yaml: string) => put<{ pack: RulePack; view: RulesView }>(`/projects/${e(pid)}/rules/packs/${e(id)}`, { yaml }),
  remove: (pid: string, id: string) => del<RulesView>(`/projects/${e(pid)}/rules/packs/${e(id)}`),
  check: (pid: string, yaml: string) => post<RuleCheck>(`/projects/${e(pid)}/rules/check`, { yaml }),
  exportUrl: (pid: string, id: string) => `/api/projects/${e(pid)}/rules/packs/${e(id)}/export`,
  text: (pid: string, id: string) => getText(`/projects/${e(pid)}/rules/packs/${e(id)}/export`),
  now: (tid: string) => get<RulesNow>(`/threads/${e(tid)}/rules-now`),
};

/** A pack as YAML, from the New rule form (the engine checks it again before it is saved). */
export function packYaml(p: { name: string; title: string; version?: string; summary?: string; keel?: string; rules: PackRule[] }): string {
  const s = (v: string) => (/^[\w./*!-][\w ./*!@:-]*$/.test(v) && !/^(yes|no|true|false|null|on|off)$/i.test(v) && !/:\s/.test(v) ? v : JSON.stringify(v));
  const list = (xs: string[]) => `[${xs.map(s).join(", ")}]`;
  // like keel's own packs: a short list on one line, a long one an item per line (rules/packs.py normalize)
  const field = (key: string, xs: string[]) => (list(xs).length <= 62 ? [`    ${key}: ${list(xs)}`] : [`    ${key}:`, ...xs.map((x) => `      - ${s(x)}`)]);
  const lines = [`# keel rule pack: ${p.title} (format: docs/rules/pack-format.md)`, "schema: 1", "kind: rules", `name: ${p.name}`,
    `title: ${s(p.title)}`, `version: ${p.version || "1.0.0"}`, "publisher: local"];
  if (p.summary) lines.push(`summary: ${s(p.summary)}`);
  lines.push("requires:", `  keel: '${p.keel || ">=0.17.2"}'`);
  lines.push("rules:");
  for (const r of p.rules) {
    lines.push(`  - id: ${r.id}`, `    kind: ${r.kind}`, ...field("match", r.match));
    if (r.how && r.how !== { bash: "contains", edit: "glob", read: "glob", mcp: "name" }[r.kind]) lines.push(`    how: ${r.how}`);
    lines.push(`    phases: ${Array.isArray(r.phases) ? list(r.phases) : r.phases || "all"}`);
    if (r.lanes?.length) lines.push(`    lanes: ${list(r.lanes)}`);
    if (r.except?.length) lines.push(...field("except", r.except));
    lines.push(`    reason: ${s(r.reason)}`);
  }
  return lines.join("\n") + "\n";
}

export const KIND_WORDS: Record<PackRule["kind"], string> = {
  bash: "shell command", edit: "file change", read: "file read", mcp: "MCP tool",
};

export const WHEN_WORDS: Record<When, string> = {
  before: "checked before the action",
  wrapped: "checked before, for git, gh, package managers and file printers only",
  after: "checked after the step: keel puts the file back",
  none: "not checked by this engine",
};
