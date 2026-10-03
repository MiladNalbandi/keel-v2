// Example api data for tests (MSW). Shapes follow docs/CONTRACT.md; values follow docs/mockup.html.

import type {
  Agent, Budget, Cap, Checkpoint, Connections, Estimate, Health, Job, JobStep, KeelDoc, KeelMap, LibraryItem, Limit, McpServer,
  Memory, Notification, NotificationSettings, Project, ProjectSettings, RepoFile, RepoInfo, Settings, Skill, SkillDetail,
  Stack, ThreadState, TreeNode, WikiPage, WikiTree, Workflow,
} from "../api";

const now = Date.now();
const ago = (s: number) => new Date(now - s * 1000).toISOString();

export const health: Health = { ok: true, engine: true, keel: { version: "0.67.0", home: "/opt/keel" }, fake: true };

export const projects: Project[] = [
  { id: "ludus-engine", name: "ludus-engine", root: "/workspace/ludus-engine", branch: "feat/scores", flow: "feature", phase: "ac-gate", acs: [1, 3], waiting: 1, running: 1 },
  { id: "platform", name: "platform", root: "/workspace/platform", branch: "fix/tally-queue", flow: "fix", phase: "bug-investigate", acs: [0, 1], waiting: 0, running: 1 },
  { id: "yegi", name: "YegiResearcher", root: "/workspace/YegiResearcher", branch: "main", flow: null, phase: "none", acs: [0, 0], waiting: 0, running: 0 },
];

export const featureWorkflow: Workflow = {
  id: "feature", name: "feature (keel)", keel_rules: true, version: 3, yaml: "name: feature (keel)\nkeel_rules: true\nsteps: []\n",
  steps: [
    { id: "s1", kind: "agent", name: "spec", agent: "explorer", model: "default", phase: "spec" },
    { id: "s2", kind: "gate", name: "spec approval", lock: true, back: "s1", phase: "spec-ok" },
    { id: "s3", kind: "agent", name: "red", agent: "test-author", model: "default", per_ac: true, phase: "red" },
    { id: "s4", kind: "code", name: "verify_red", per_ac: true, lock: true, action: "verify_red" },
    { id: "s5", kind: "agent", name: "green", agent: "implementer", model: "default", per_ac: true, phase: "green" },
    { id: "s6", kind: "code", name: "verify_green + commit", per_ac: true, lock: true, action: "verify_green" },
    { id: "s7", kind: "agent", name: "AC review", agent: "ac-reviewer", model: "default", per_ac: true },
    { id: "s8", kind: "gate", name: "AC gate", per_ac: true, lock: true, back: "s3", phase: "ac-gate" },
    { id: "s9", kind: "agent", name: "integration", agent: "code-reviewer", model: "default" },
    { id: "s10", kind: "parallel", name: "ship review", agent: "reviewer", model: "default", parallel: 4 },
    { id: "s11", kind: "gate", name: "final review", lock: true },
    { id: "s12", kind: "agent", name: "memory", agent: "librarian", model: "default" },
  ],
};

export const fixWorkflow: Workflow = {
  id: "fix", name: "fix (keel)", keel_rules: true, version: 1, yaml: "name: fix (keel)\n",
  steps: [
    { id: "f1", kind: "agent", name: "bug-repro", agent: "reproducer", model: "default" },
    { id: "f2", kind: "gate", name: "gate R", lock: true, back: "f1" },
    { id: "f3", kind: "agent", name: "bug-investigate", agent: "investigator", model: "default" },
    { id: "f4", kind: "agent", name: "bug-fix", agent: "implementer", model: "default" },
    { id: "f5", kind: "code", name: "verify + commit", lock: true },
  ],
};

export const initWorkflow: Workflow = {
  id: "init", name: "init (keel)", keel_rules: true, version: 1, yaml: "name: init (keel)\n",
  steps: [
    { id: "i1", kind: "agent", name: "discover", agent: "explorer", model: "default" },
    { id: "i2", kind: "gate", name: "setup plan approval", lock: true },
    { id: "i3", kind: "parallel", name: "ladder ‖ knowledge build", agent: "librarian", parallel: 4,
      lanes: [{ name: "ladder", sub: "12 rungs", kind: "code" }, { name: "knowledge", sub: "librarian × 4", kind: "agent" }] },
    { id: "i4", kind: "code", name: "memory check", lock: true },
    { id: "i5", kind: "gate", name: "hand over", lock: true },
  ],
};

export const thread: ThreadState = {
  thread_id: "th_7f3a", project_id: "ludus-engine", workflow_id: "feature", title: "Scores for players", status: "waiting",
  current: "s8", phase: "ac-gate", ac: "AC-002",
  acs: [
    { id: "AC-001", layer: "API", title: "Save a score for a player", status: "done" },
    { id: "AC-002", layer: "API", title: "Refuse a negative score with 400", status: "green" },
    { id: "AC-003", layer: "WEB", title: "Show the top 10 scores", status: "todo" },
  ],
  waiting: { step: "s8", kind: "gate", title: "AC gate", detail: "RED 4be12d9 · GREEN a81c3f0 · tests 2/2 pass · ac-reviewer: PASS", options: ["approve", "reject"] },
  usage: { tokens_in: 160000, tokens_out: 22000, cost_usd: 2.86, premium_requests: 41, cap_tokens: 600000 },
  checkpoints: 31, updated_at: ago(30),
  blockers: [
    { gate: "coverage", why: "No coverage verdict for HEAD yet.", fix: "Run the coverage step: keel verify coverage." },
    { gate: "deps", why: "build.gradle.kts adds io.ktor:ktor-server-core.", fix: "Approve the new dependency at the next commit." },
  ],
};

export const checkpoints: Checkpoint[] = [
  { id: "cp31", n: 31, step: "commit", at: ago(120), note: "feat(AC-002) a81c3f0 — 1 file, +14" },
  { id: "cp30", n: 30, step: "verify_green", at: ago(150), note: "AC-002 tests pass (2/2)" },
  { id: "cp29", n: 29, step: "green", at: ago(400), note: "implementer · 9 steps" },
];

export const estimate: Estimate = {
  tokens: 410000, low: 287000, high: 656000, cost_usd: 6.4, premium_requests: 54,
  by_provider: { claude: 250000, codex: 80000, copilot: 80000 },
  per_step: [{ step: "s1", tokens: 44000 }, { step: "s3", tokens: 81000 }, { step: "s5", tokens: 111000 }],
};

export const jobs: Job[] = [
  { id: "j-482", project_id: "ludus-engine", thread_id: "th_7f3a", agent: "ac-reviewer", provider: "codex", model: "gpt-5.6-sol", step: "s7", phase: "ac-gate", ac: "AC-002",
    status: "running", started_at: ago(74), ended_at: null, tokens_in: 18000, tokens_out: 2000, cost_usd: 0, premium_requests: 0, steps_count: 3, mcp_calls: 1 },
  { id: "j-480", project_id: "ludus-engine", thread_id: "th_7f3a", agent: "implementer", provider: "copilot", model: "gpt-5", step: "s5", phase: "green", ac: "AC-002",
    status: "done", started_at: ago(900), ended_at: ago(650), tokens_in: 31000, tokens_out: 6000, cost_usd: 0, premium_requests: 6, steps_count: 9, mcp_calls: 2 },
];

export const steps: JobStep[] = [
  { n: 1, at: ago(70), kind: "text", text: "Looking at what the gate needs: RED and GREEN commits for AC-002." },
  { n: 2, at: ago(65), kind: "tool", text: "{filter: \"gates\"}", tool: "keel_timeline", server: "keel", ms: 38, ok: true },
  { n: 3, at: ago(60), kind: "edit", text: "", path: "api/src/main/kotlin/scores/ScoreController.kt", diff: "-    fun save(s: ScoreDto)\n+    fun save(@Valid s: ScoreDto)" },
];

export const repo: RepoInfo = {
  branch: "feat/scores", base: "main", ahead: 6, behind: 0, remote: "origin · github.com/mili/ludus-engine",
  worktrees: [{ branch: "feat/scores", path: "/workspace/ludus-engine" }],
  branches: [{ name: "main", note: "base" }, { name: "feat/scores", note: "this flow" }],
};
export const tree: TreeNode[] = [
  { path: "api", name: "api", depth: 0, kind: "dir", keel: false, frozen: false },
  { path: "api/ScoreController.kt", name: "ScoreController.kt", depth: 1, kind: "file", mark: "M", keel: false, frozen: false, ac: "AC-002" },
  { path: ".keel", name: ".keel", depth: 0, kind: "dir", keel: true, frozen: false },
  { path: ".keel/state.json", name: "state.json", depth: 1, kind: "file", mark: "M", keel: true, frozen: false },
];
export const file: RepoFile = { path: "api/ScoreController.kt", size: 812, mark: "M", frozen: false, keel: false, ac: "AC-002", head: "package scores\n\nclass ScoreController", last_commit: "a81c3f0 feat(AC-002)" };
export const keelDocs: KeelDoc[] = [{ path: ".keel/state.json", what: "Flow state", by: "engine", updated: "live", status: "live" }];
export const memory: Memory = {
  facts: [{ id: "f1", title: "Test needs Docker", text: "ScoreRepositoryTest uses Testcontainers; start Docker first.", kind: "fact", source: "setup-doctor", at: ago(86400) }],
  knowledge: [{ id: "architecture", status: "written", words: 1180, cites: 41 }, { id: "domain", status: "stale", words: 640, cites: 23 }],
};

export const map: KeelMap = {
  sha: "9a00e8f36b", at: ago(120), counts: { modules: 2, tables: 1 },
  limits: ["tables come from the migrations"],
  levels: {
    system: {
      width: 600, height: 200,
      nodes: [
        { id: "app:web", kind: "app", title: "web", sub: "ts-react", rows: [{ t: "6 pages" }], x: 30, y: 40, w: 180, h: 76 },
        { id: "db:main", kind: "data", title: "postgres", rows: [{ t: "4 tables" }], x: 380, y: 40, w: 180, h: 58 },
      ],
      edges: [{ from: "app:web", to: "db:main", kind: "sql", label: "JDBC", d: "M 210 70 H 380", lx: 295, ly: 70 }],
    },
    er: {
      width: 400, height: 200,
      nodes: [{ id: "tbl:scores", kind: "data", title: "scores", sub: "3 columns", rows: [{ t: "id uuid", flag: "pk" }, { t: "points int" }], x: 30, y: 30, w: 200, h: 100 }],
      edges: [],
    },
  },
};

export const wiki: WikiTree = {
  sections: [
    { id: "knowledge", title: "Knowledge base", items: [{ id: "kb:architecture", title: "architecture", status: "written" }] },
    { id: "workflows", title: "Workflows", items: [{ id: "wf:feature", title: "feature (keel)" }] },
    { id: "runbook", title: "Setup", items: [{ id: "runbook", title: "Runbook" }] },
    { id: "decisions", title: "Decisions", items: [] },
  ],
};
export const wikiPage: WikiPage = {
  id: "kb:architecture", title: "architecture",
  markdown: "The app is **layered**.\n\n- Routes live in `api/routes.py:28`.\n- Services never import the API.",
  meta: { status: "written", words: 1180, cites: 41 },
};

export const library: LibraryItem[] = [
  { id: "dependency-upgrade", name: "Dependency upgrade", source: "keel", version: "1.3.0", about: "Bump one dependency, run the suite, triage CVEs, then ask you.",
    steps: 6, gates: 2, est_tokens: 90000, agents: ["dependency-triager", "implementer"], mcp: [], edits_files: true, installed: false },
];

export const agents: Agent[] = [
  { id: "explorer", label: "explorer", about: "Maps the code an AC touches, read-only.", custom: false, phases: ["spec"],
    model: { provider: "claude", mode: "subscription", model: "sonnet" }, tools: ["mcp:keel"], skills: ["spec-authoring"], prompt: "You map code.", enabled: true, overridden: [] },
  { id: "implementer", label: "implementer", about: "Writes the minimum code to make the failing test pass.", custom: false, phases: ["green"],
    model: { provider: "copilot", mode: "subscription", model: "gpt-5" }, tools: ["mcp:keel"], skills: [], prompt: "You implement.", enabled: true, overridden: ["model"] },
  { id: "lit-check", label: "Lit-Check", about: "Checks the spec against docs/.", custom: true, phases: ["spec"],
    model: { provider: "copilot", mode: "api", model: "gpt-5" }, tools: ["mcp:keel", "mcp:github"], skills: [], prompt: "Check the spec.", enabled: true, overridden: [] },
];

export const skills: Skill[] = [
  { id: "web-testing", kind: "testing", source: "keel", stack: "ts-react", version: "0.67.0", tokens: 940, agents: ["test-author"], when: "red · layer WEB", enabled: true },
  { id: "ludus-domain", kind: "knowledge", source: "yours", stack: "any", version: "v3", tokens: 1800, agents: ["explorer"], when: "every step", enabled: true },
];
export const skillDetail: SkillDetail = { ...skills[0], body: "---\nname: web-testing\n---\n# web-testing", refs: [{ path: "examples/msw.ts", tokens: 900 }] };

export const stacks: Stack[] = [
  { name: "ts-react", lane: "web", source: "built-in", detected: true, detect: "tsconfig.json, *.tsx", layers: ["unit", "component", "page"],
    commands: [{ name: "typecheck", cmd: "npx tsc --noEmit" }], tools: [{ name: "eslint", on: "pre-commit", fail: "block" }], skills: ["web-testing"] },
  { name: "kotlin-spring", lane: "api", source: "keel pack", detected: false, detect: "build.gradle.kts", layers: ["unit", "slice"],
    commands: [{ name: "test", cmd: "./gradlew test" }], tools: [], skills: [], installable: true },
];

export const mcpServers: McpServer[] = [
  { name: "keel", command: "node", args: ["/opt/keel/mcp/server.js"], enabled: true, builtin: true, status: "ok", tools: ["keel_status", "keel_next"] },
  { name: "serena", command: "uvx", args: ["serena"], enabled: false, builtin: false, status: "off", tools: [] },
];

export const budget: Budget = {
  month: { tokens: 1900000, cost_usd: 24.1, premium_requests: 212, flows: 6 },
  days: [{ day: "2026-10-01", claude: 180000, codex: 60000, copilot: 40000, fake: 0 }, { day: "2026-10-02", claude: 220000, codex: 90000, copilot: 30000, fake: 0 }],
  caps: [{ id: "c1", scope: "flow", limit: 600000, unit: "tokens", action: "pause" }],
  top: [{ agent: "implementer", provider: "copilot", tokens: 1420000, cost_usd: 0 }],
  recent: [{ title: "ludus-engine · feature", estimate: 410000, real: 386000, status: "running" }],
};
export const caps: Cap[] = [
  { id: "c1", scope: "flow", limit: 600000, unit: "tokens", action: "pause" },
  { id: "c2", scope: "api_month", limit: 100, unit: "usd", action: "stop" },
];
export const providerModels = {
  claude: [{ id: "opus", label: "Claude Opus" }, { id: "sonnet", label: "Claude Sonnet" }],
  codex: [{ id: "gpt-5.6-sol", label: "GPT-5.6 Sol" }],
  copilot: [{ id: "gpt-5", label: "GPT-5 (Copilot)" }],
  fake: [{ id: "fake", label: "Fake model" }],
};
export const limits: Limit[] = [{ id: "claude", name: "Claude Max", unit: "% of 5-hour window", used: 62, cap: 100, note: "resets in 1h 48m" }];

export const generalSettings: Settings = {
  gates_mode: "every-ac", keel_rules: true, fix_attempts: 3, coverage_min: 80,
  default_model: { provider: "fake", mode: "api", model: "fake" },
  implementer_model: { provider: "claude", mode: "subscription", model: "opus" },
  reviewer_model: { provider: "codex", mode: "subscription", model: "gpt-5.6-sol" },
  cheaper_model: { provider: "claude", mode: "subscription", model: "haiku" },
  cap_tokens: 500000, on_cap: "pause", branch_pattern: "feat/{slug}", web_lane_worktree: true, push_pr: "ask", notify: "all", env_names: [], mcp: ["keel"],
};
export function projectSettings(overrides: Partial<Settings>): ProjectSettings {
  return { general: generalSettings, overrides, effective: { ...generalSettings, ...overrides } };
}

export const connections: Connections = {
  providers: [
    { id: "claude", label: "Claude", selected: "subscription", key_set: false, login_secret: "CLAUDE_CODE_OAUTH_TOKEN", login_set: false,
      modes: [{ id: "subscription", label: "Subscription", ready: true, detail: "claude CLI, logged in" }, { id: "api", label: "API key", ready: false, detail: "ANTHROPIC_API_KEY" }] },
  ],
  machine: [{ name: "git", ok: true, version: "2.47" }, { name: "serena", ok: false }],
};

export const notifications: Notification[] = [
  { id: "n1", type: "review", project_id: "ludus-engine", title: "AC gate waits for you", body: "AC-002 — ac-reviewer says PASS", link: "#/flow", at: ago(60), read: false },
  { id: "n2", type: "finished", project_id: "platform", title: "Lit-Check finished", body: "0 issues", link: "#/live", at: ago(3600), read: true },
];
export const notificationSettings: NotificationSettings = {
  sound: true, volume: 0.5, tone: "chime", popup: true, desktop: false, scope: "all",
  kinds: { review: true, failed: true, budget: true, finished: true, started: false }, quiet: false,
};
