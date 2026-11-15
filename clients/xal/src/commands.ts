// keel's commands in xal: /keel:<name> in the TUI, `xal keel <name>` in the shell. The names and arguments are
// the keel TUI's (docs/plans/keel-tui.md: /flow /status /next /inbox /approve /reject /answer /live /timeline
// /workflows /diagram …), so a person types the same thing in both. Most of them call one of keel's MCP tools and
// print its text unchanged: the words are keel's (engine/keel_engine/mcp_server.py), not this plugin's.
// TODO(docs/plans/agent-clients.md Q2): generate this table from content/agent-clients/commands.yaml once it exists.

import type { Keel, Out } from "./keel.ts";
import { KeelHttpError } from "./rest.ts";

export type Spec = {
  name: string;
  aliases?: string[];
  usage: string;
  describe: string;
  /** keel's MCP tool and its arguments from the words after the command */
  tool?: string;
  args?: (a: string[]) => Record<string, unknown>;
  run?: (a: string[], keel: Keel, out: Out) => Promise<void>;
};

const rest = (a: string[], from = 0) => a.slice(from).join(" ").trim() || undefined;
const num = (v: string | undefined) => (v && /^\d+$/.test(v) ? Number(v) : undefined);
const decision = (a: string[]): { decision: "approve" | "reject"; why?: string } =>
  a[0] === "reject" || a[0] === "approve"
    ? { decision: a[0] as "approve" | "reject", why: rest(a, 1) }
    : { decision: "approve", why: rest(a) };

export const DIAGRAM_KINDS = ["flow", "workflow", "map-system", "map-modules", "map-er", "graph"];

export const SPECS: Spec[] = [
  { name: "help", usage: "", describe: "keel's commands in xal", run: async (_a, _k, out) => out.print(helpText()) },
  { name: "status", usage: "", describe: "the flow on this project: phase, criteria, what waits", tool: "keel_status" },
  { name: "next", usage: "", describe: "what waits for you and how to answer it", tool: "keel_next" },
  { name: "inbox", usage: "", describe: "what waits for a person (gates, questions, requests)", tool: "keel_inbox" },
  { name: "live", aliases: ["jobs"], usage: "", describe: "the agent calls running now", tool: "keel_live" },
  {
    name: "flows",
    usage: "[limit]",
    describe: "this project's flows, newest first",
    tool: "keel_flows",
    args: (a) => ({ limit: num(a[0]) }),
  },
  {
    name: "timeline",
    usage: "[all|steps|agents|failed] [limit]",
    describe: "what happened, newest last",
    tool: "keel_timeline",
    args: (a) => ({ filter: ["all", "steps", "agents", "failed"].includes(a[0] ?? "") ? a[0] : undefined, limit: num(a[1] ?? a[0]) }),
  },
  { name: "steps", usage: "[limit]", describe: "the flow's steps (the timeline's steps)", tool: "keel_timeline", args: (a) => ({ filter: "steps", limit: num(a[0]) }) },
  {
    name: "workflows",
    usage: "[workflow]",
    describe: "the workflows, or one workflow's steps",
    tool: "keel_workflows",
    args: (a) => ({ workflow: rest(a) }),
  },
  { name: "rules", usage: "", describe: "what each phase allows and the rule packs", tool: "keel_rules" },
  { name: "budget", usage: "", describe: "tokens and money: this flow, today, the caps", tool: "keel_budget" },
  { name: "doctor", usage: "[days]", describe: "what crashed in keel's flows, what keel repaired, what to improve", tool: "keel_flow_doctor", args: (a) => ({ days: num(a[0]) }) },
  {
    name: "dashboard",
    usage: "[flow|map|er]",
    describe: "the link to keel's page for this project",
    tool: "keel_dashboard",
    args: (a) => ({ view: ["flow", "map", "er"].includes(a[0] ?? "") ? a[0] : undefined }),
  },
  { name: "plugins", usage: "", describe: "keel's installed plugins", tool: "keel_plugins_installed" },
  {
    name: "flow",
    usage: "<workflow> <idea…>",
    describe: "start a flow (feature, change, bug …) on this project",
    tool: "keel_start_flow",
    args: (a) => {
      if (a.length < 2) throw new Error("usage: /keel:flow <workflow> <idea…>   for example: /keel:flow change Prices in euros");
      return { type: a[0], idea: rest(a, 1) };
    },
  },
  {
    name: "approve",
    usage: "[why…]",
    describe: "approve the gate that waits",
    tool: "keel_approve_gate",
    args: (a) => ({ decision: "approve", why: rest(a) }),
  },
  {
    name: "reject",
    usage: "<why…>",
    describe: "send the gate back, with why",
    tool: "keel_approve_gate",
    args: (a) => {
      const why = rest(a);
      if (!why) throw new Error("usage: /keel:reject <why…>   keel's agents read the why");
      return { decision: "reject", why };
    },
  },
  { name: "answer", usage: "[approve|reject] [why…]", describe: "answer the Inbox's first item", tool: "keel_answer", args: decision },
  { name: "resume", usage: "[approve|reject] [why…]", describe: "continue a flow that waits", tool: "keel_resume", args: decision },
  { name: "stop", usage: "", describe: "stop the flow (keel asks you first)", tool: "keel_stop_flow" },
  { name: "diagram", usage: "<kind> [id]", describe: `a picture in plain text; kind: ${DIAGRAM_KINDS.join(", ")}`, run: diagram },
  { name: "ask", usage: "[--new] <question…>", describe: "ask KeelBot about this project (read-only)", run: ask },
  { name: "watch", usage: "[on|off]", describe: "keel's events here as they happen (gates, flows, agents)", run: watch },
  { name: "panel", usage: "", describe: "one view: status, the flow rail, the inbox, live agents", run: panel },
  {
    name: "brief",
    usage: "",
    describe: "the keel brief xal's model gets when a session starts",
    run: async (_a, keel, out) => {
      if (!keel.brief) await keel.loadBrief();
      out.print(keel.brief || "keel: no brief (keel is not reachable, or pluginConfig.keel.brief is false)");
    },
  },
];

export function specFor(name: string): Spec | undefined {
  return SPECS.find((s) => s.name === name || s.aliases?.includes(name));
}

export function helpText(): string {
  const w = Math.max(...SPECS.map((s) => `${s.name} ${s.usage}`.trim().length));
  return [
    "keel in xal. In the TUI: /keel:<command>; in a shell: xal keel <command>.",
    ...SPECS.map((s) => `  ${`${s.name} ${s.usage}`.trim().padEnd(w)}  ${s.describe}`),
  ].join("\n");
}

/** Runs one command; prints its answer or a plain error. False when it failed (the CLI's exit code). */
export async function runSpec(spec: Spec, args: string[], keel: Keel, out: Out): Promise<boolean> {
  out.busy?.(`keel: ${spec.name}…`);
  try {
    if (spec.run) await spec.run(args, keel, out);
    else out.print(await keel.tool(spec.tool!, spec.args?.(args) ?? {}, out));
    return true;
  } catch (err) {
    out.print(errorText(err));
    return false;
  } finally {
    out.busy?.(undefined);
  }
}

export function errorText(err: unknown): string {
  if (err instanceof KeelHttpError) return `keel: ${err.message}${err.hint ? `\n  ${err.hint}` : ""}`;
  const msg = err instanceof Error ? err.message : String(err);
  return msg.startsWith("keel") ? msg : `keel: ${msg}`;
}

// ---- the commands keel's MCP server has no single tool for ----

async function diagram(a: string[], keel: Keel, out: Out): Promise<void> {
  const kind = a[0] ?? "flow";
  if (!DIAGRAM_KINDS.includes(kind)) throw new Error(`usage: /keel:diagram <${DIAGRAM_KINDS.join("|")}> [id]`);
  const id = rest(a, 1);
  // keel 0.18: one MCP tool over the shared renderer (engine/keel_engine/diagrams.py); the same text everywhere
  if (await keel.has("keel_diagram").catch(() => false)) {
    out.print(await keel.tool("keel_diagram", { kind, id }, out));
    return;
  }
  out.print(await diagramOverRest(kind, id, keel));
}

/** The shared renderer through keel's api (POST /api/diagrams/ascii {kind, data}), with the data read over REST. */
export async function diagramOverRest(kind: string, id: string | undefined, keel: Keel): Promise<string> {
  let data: Record<string, unknown>;
  if (kind === "flow") {
    const pid = (await keel.projectInfo())?.id;
    if (!pid) throw new Error("keel: which project? set pluginConfig.keel.project");
    const view = await keel.rest.get<{ thread: unknown; workflow: unknown }>(
      id ? `/projects/${encodeURIComponent(pid)}/flows/${encodeURIComponent(id)}` : `/projects/${encodeURIComponent(pid)}/flow`,
    );
    if (!view?.thread || !view.workflow) return "keel: no flow on this project yet. Start one with /keel:flow <workflow> <idea…>";
    data = { workflow: view.workflow, thread: view.thread };
  } else if (kind === "workflow") {
    if (!id) throw new Error("usage: /keel:diagram workflow <workflow id>   (/keel:workflows lists them)");
    data = { workflow: await keel.rest.get(`/workflows/${encodeURIComponent(id)}`) };
  } else {
    throw new Error(`keel: the ${kind} picture needs keel's keel_diagram tool (keel 0.18). /keel:dashboard map links the Map.`);
  }
  try {
    const res = await keel.rest.post<{ text: string }>("/diagrams/ascii", { kind, data, width: 100, max_lines: 60 }, "diagram");
    return res.text;
  } catch (err) {
    if (err instanceof KeelHttpError && (err.status === 404 || err.status === 405)) {
      return "keel: this keel has no shared diagram renderer yet (POST /api/diagrams/ascii comes with keel 0.18). /keel:dashboard flow links the flow page.";
    }
    throw err;
  }
}

type Session = { id: string; status: string; busy?: boolean; error?: string | null; messages?: { n: number; role: string; text: string }[] };

async function ask(a: string[], keel: Keel, out: Out): Promise<void> {
  const fresh = a[0] === "--new";
  const text = rest(a, fresh ? 1 : 0);
  if (fresh) keel.chat = undefined;
  if (!text) {
    out.print(fresh ? "keel: a new KeelBot chat starts with your next /keel:ask." : "usage: /keel:ask [--new] <question…>");
    return;
  }
  const pid = (await keel.projectInfo())?.id;
  if (!pid) throw new Error("keel: which project? set pluginConfig.keel.project");
  const base = `/projects/${encodeURIComponent(pid)}/helper/sessions`;
  if (!keel.chat) {
    const s = await keel.rest.post<Session>(base, { mode: "ask", title: `xal: ${text.slice(0, 60)}` }, "keelbot");
    keel.chat = s.id;
  }
  const started = await keel.rest.post<{ n: number }>(`${base}/${encodeURIComponent(keel.chat)}/turn`, { text }, "keelbot");
  const deadline = Date.now() + Math.max(keel.cfg.timeoutMs, 300_000);
  for (;;) {
    await new Promise((r) => setTimeout(r, 1000));
    const s = await keel.rest.get<Session>(`${base}/${encodeURIComponent(keel.chat)}`);
    const answers = (s.messages ?? []).filter((m) => m.n > started.n && m.role !== "user");
    if (!s.busy && s.status !== "running") {
      const said = answers.map((m) => m.text.trim()).filter(Boolean).join("\n\n");
      out.print(said ? `KeelBot:\n${said}` : s.status === "failed" ? `keel: KeelBot failed${s.error ? ": " + s.error : ""}` : "KeelBot gave no answer.");
      return;
    }
    if (Date.now() > deadline) {
      out.print("keel: KeelBot is still working; its answer shows in keel's KeelBot panel.");
      return;
    }
  }
}

async function watch(a: string[], keel: Keel, out: Out): Promise<void> {
  const what = a[0] ?? (keel.live ? "status" : "on");
  if (what === "off") {
    out.print(keel.unwatch() ? "keel · notices off" : "keel · notices were off");
    return;
  }
  if (what === "status") {
    out.print("keel · notices on (/keel:watch off stops them)");
    return;
  }
  if (what !== "on") throw new Error("usage: /keel:watch [on|off]");
  if (!(await keel.watch(out))) out.print("keel · notices are on already");
}

async function panel(_a: string[], keel: Keel, out: Out): Promise<void> {
  const part = async (title: string, f: () => Promise<string>) => {
    let text: string;
    try {
      text = await f();
    } catch (err) {
      text = errorText(err);
    }
    return `-- ${title} ${"-".repeat(Math.max(2, 60 - title.length))}\n${text}`;
  };
  const parts = [
    await part("keel · status", () => keel.tool("keel_status", {}, out)),
    await part("flow", async () => ((await keel.has("keel_diagram")) ? keel.tool("keel_diagram", { kind: "flow" }, out) : diagramOverRest("flow", undefined, keel))),
    await part("inbox", () => keel.tool("keel_inbox", {}, out)),
    await part("live agents", () => keel.tool("keel_live", {}, out)),
  ];
  out.print(parts.join("\n"));
}
