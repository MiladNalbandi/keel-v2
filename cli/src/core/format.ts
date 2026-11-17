// keel's answers as text: the same words as keel2 mcp's tools (engine keel_engine/mcp_server.py, mcp_more.py), from
// the web's typed data. Pure functions, no UI: keel TUI prints them, another front end (an xal plugin, OpenTUI) can too.

import type { HelperChange } from "./sdk";
import type { BudgetNow, Checkpoint, CrashEnd, DoctorReport, FlowView, Health, Job, JobStep, Project, RunRow, ThreadState, Workflow } from "./sdk";
import type { InboxItem } from "../../../web/src/inboxApi";
import { PLAIN, type Paint } from "./theme";
import { renderDiff } from "./diff";
import { gateDetailLines, gateFirstLines } from "./gatemd";
import { modelText } from "./effort";

export const n = (x: unknown) => {
  const v = Number(x ?? 0);
  return Number.isFinite(v) ? Math.trunc(v).toLocaleString("en-US") : String(x);
};
export const usd = (x: unknown) => `$${Number(x ?? 0).toFixed(2)}`;
/** 1.2k, 3.4M: short token counts for the status line. */
export const short = (x: number) => (x >= 1e6 ? `${(x / 1e6).toFixed(1)}M` : x >= 1000 ? `${(x / 1000).toFixed(1)}k` : String(x));
export const clock = (at?: string | null) => {
  const d = at ? new Date(at) : null;
  if (!d || Number.isNaN(d.getTime())) return "--";
  const p = (v: number) => String(v).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};

/** P1-3 how long ago: "just now", "5m ago", "3h ago", "2d ago" ("--" with no time). */
export const age = (at?: string | null, now = Date.now()) => {
  const t = at ? new Date(at).getTime() : NaN;
  if (Number.isNaN(t)) return "--";
  const m = Math.floor(Math.max(0, now - t) / 60_000);
  return m < 1 ? "just now" : m < 60 ? `${m}m ago` : m < 24 * 60 ? `${Math.floor(m / 60)}h ago` : `${Math.floor(m / (24 * 60))}d ago`;
};

export const tokensOf = (u?: { tokens_in?: number; tokens_out?: number } | null) => Number(u?.tokens_in ?? 0) + Number(u?.tokens_out ?? 0);

export function flowName(t: ThreadState, w: Workflow | null): string {
  const wid = t.workflow_id || "?";
  // "feature (keel)" for the workflow feature: its name says enough
  return w?.name && w.name !== wid && !w.name.startsWith(wid) ? `${w.name} (${wid})` : w?.name || wid;
}

export function acCount(t: ThreadState | null | undefined): [number, number] {
  const acs = t?.acs ?? [];
  return [acs.filter((a) => a.status === "done" || a.status === "already-met").length, acs.length];
}

type Waiting = NonNullable<ThreadState["waiting"]>;

/** A flow's or an agent call's status as a small badge: running accent, waiting amber, done green, failed red. */
export function badge(status: string, c: Paint = PLAIN): string {
  switch (status) {
    case "running": return c.bold(c.accent("▶ running"));
    case "waiting": return c.bold(c.gate("◆ waiting"));
    case "done": return c.ok("✔ done");
    case "failed": return c.bad("✗ failed");
    case "guard": return c.bad("✗ refused");
    case "stopped": return c.faint("■ stopped");
    default: return c.dim(status);
  }
}

/** n/m criteria: green when all are done. */
const criteria = (done: number, all: number, c: Paint) => (all && done === all ? c.ok(`${done}/${all}`) : `${done}/${all}`);
const label = (s: string, c: Paint) => c.accent(s);

/** What waits, and how to answer it in keel TUI. */
export function waitingLines(w: Waiting, c: Paint = PLAIN): string[] {
  const labels = w.labels ?? {};
  const cmd = (s: string) => c.bold(c.gate(s));
  const out = [`${c.bold(c.gate("waiting"))}   ${c.gate(`${w.kind || "gate"}: ${w.title}`)}`];
  if (w.kind === "clarify") {
    out.push(`          answer: ${cmd("/answer")} (one question after the other)`);
    for (const q of w.questions ?? []) {
      const choices = q.options.map((o) => o.label + (o.recommended ? c.recommended(" (recommended)") : "")).join(" / ");
      out.push(`          ${c.strong("hint", q.id + ":")} ${c.strong("question", q.question)}  [${choices}]`);
    }
  } else if (w.choices?.length) {
    out.push(`          answer: ${cmd(answerWords(w))}`);
    out.push(...choiceLines(w, c).map((l) => "          " + l));
  } else {
    const opts = w.options ?? ["approve", "reject"];
    const parts = [];
    if (opts.includes("approve")) parts.push(cmd("/approve") + (labels.approve ? c.dim(` ("${labels.approve}")`) : ""));
    if (opts.includes("reject")) parts.push(cmd("/reject <why>") + (labels.reject ? c.dim(` ("${labels.reject}")`) : ""));
    out.push(`          answer: ${parts.join(" or ") || "-"}`);
    if (w.questions?.length) {
      // keel 0.18.22 a gate that asks beside approve / reject (a findings gate: one question per finding)
      out.push(`          or decide each: ${cmd("/answer")} (one after the other; ${cmd("/answer f1=fix f2=\"accept: <why>\"")})`);
      for (const q of w.questions) {
        const choices = q.options.map((o) => o.label + (o.recommended ? c.recommended(" (recommended)") : "") + (o.why === "required" ? c.meta(" +why") : "")).join(" / ");
        out.push(`          ${c.strong("hint", q.id + ":")} ${c.strong("question", q.question)}  [${choices}]`);
      }
    }
  }
  const detail = folded(w.detail ?? "").trim();
  // the engine's Markdown as plain text: headings bold, code indented, a diff coloured, the summary first
  if (detail) out.push(...gateDetailLines(detail, c, { budget: 600, prose: (l) => labelLine(l, c) }).map((l) => "          " + l));
  return out;
}

/** 0.18.23 a gate's `<details><summary>title</summary> … </details>` fold-outs (a spike's angles) as their title line
 *  only, "▸ title"; the dashboard's Flow page opens them. */
export function folded(text: string): string {
  return text.replace(/^[ \t]*<details>[ \t]*(?:<summary>(.*?)<\/summary>)?[\s\S]*?^[ \t]*<\/details>[ \t]*$/gim,
    (_m, title: string | undefined) => `▸ ${title || "more"}`);
}

/** keel 0.18.13 a waiting question's named choices with the engine's words (choice_info): one line each, its number,
 *  label and name, the default marked, whether it needs a reason, what it does. [] for an older engine (no words). */
export function choiceLines(w: Waiting, c: Paint = PLAIN): string[] {
  return (w.choice_info ?? []).map((x, n) => `${c.meta(`${n + 1}.`)} ${c.bold(x.label || x.name)}${x.label && x.label !== x.name ? c.meta(` (${x.name})`) : ""}`
    + (x.default ? c.recommended(" · default") : "") + (x.why === "required" ? c.gate(" · needs a reason") : x.why === "optional" ? c.meta(" · reason optional") : "")
    + (x.description ? c.detail(` — ${x.description}`) : ""));
}

/** A "label: text" line under a gate or a question ("why it matters: …"): the label as a hint, the text readable (never
 *  dim). A line without a short label is readable text. */
export function labelLine(l: string, c: Paint = PLAIN): string {
  const m = /^(\s*)([\w ()'-]{2,28}:)( .*)$/.exec(l);
  return m ? m[1] + c.bold(c.hint(m[2])) + c.detail(m[3]) : c.detail(l);
}

/** A text cut at a word near `max`, with "…" when it was longer. */
export function cut(text: string, max: number): string {
  if (text.length <= max) return text;
  const at = text.lastIndexOf(" ", max);
  return text.slice(0, at > max * 0.6 ? at : max).trimEnd() + " …";
}

/** How to answer a gate with named exits: /approve when it has one, /reject when it may, /answer for the others. */
export function answerWords(w: Waiting): string {
  const choices = w.choices ?? [];
  const opts = w.options ?? [];
  const parts = [];
  if (choices.includes("approve") || (!choices.length && opts.includes("approve"))) parts.push("/approve");
  if (opts.includes("reject") || choices.includes("reject")) parts.push("/reject <why>");
  const other = choices.filter((c) => c !== "approve" && c !== "reject");
  if (other.length) parts.push(`/answer <${other.join(" | ")}>`);
  return (parts.length > 2 ? parts.slice(0, -1).join(", ") + " or " + parts[parts.length - 1] : parts.join(" or ")) || "/approve or /reject <why>";
}

/** /status: the flow in a few short lines; what waits for you first, one line per criterion (cut to the screen). */
export function status(p: Project, view: FlowView | null, c: Paint = PLAIN, width = 100): string {
  const fit = (s: string, w = width) => (s.length > w ? s.slice(0, Math.max(10, w - 1)).trimEnd() + "…" : s);
  const t = view?.thread;
  const where = `${c.bold(p.id)} · branch ${c.run(p.branch || "-")}`;
  if (!t) return `${where}
No flow yet. Start one: ${c.accent("/flow")}`;
  const mode = t.run_mode ?? "manual";
  const out = [
    `${c.bold(`"${t.title}"`)} · ${flowName(t, view?.workflow ?? null)} · ${badge(t.status, c)} · run mode ${c.bold(mode)}`,
    `${where} · step ${t.current || "-"} · phase ${t.phase || "none"}`,
  ];
  const w = t.waiting;
  if (w) {
    out.push("", c.bold(c.gate(`◆ Waiting for you: ${w.title}`)) + (t.ac ? c.gate(` (for ${t.ac})`) : ""));
    // each way to answer: the command as a hint (bold), what it does readable beside it
    const row = (a: string, b: string) => `  ${c.bold(c.hint(a))}${" ".repeat(Math.max(2, 34 - a.length))}${c.detail(b)}`;
    const l = w.labels ?? {};
    if (w.kind === "clarify") out.push(row("/answer", `${(w.questions ?? []).length} question(s), one after the other`));
    else {
      const opts = w.options ?? ["approve", "reject"];
      const choices = (w.choices ?? []).filter((x) => x !== "approve" && x !== "reject");
      if (w.choice_info?.length) {
        // keel 0.18.13 each choice with its own words; the reason only where the choice needs one
        for (const x of w.choice_info) out.push(row(`/answer ${x.name}${x.why === "required" ? " <why>" : x.why === "optional" ? " [why]" : ""}`,
          `${x.label || x.name}${x.default ? " (default)" : ""}${x.description ? ` — ${x.description}` : ""}`));
      } else {
        if (opts.includes("approve") || (w.choices ?? []).includes("approve")) out.push(row("/approve", l.approve || "go on"));
        if (choices.length) out.push(row(`/answer <${choices.join("|")}>`, "choose what happens"));
        if (opts.includes("reject")) out.push(row("/reject <why>", l.reject || "send it back with your reason"));
      }
    }
    out.push(row("or ask KeelBot", "it explains the gate and suggests an answer"));
    out.push(...gateFirstLines(folded(w.detail ?? ""), c, 2, width - 2, (x) => labelLine(x, c)).map((x) => "  " + x));
  }
  if (t.acs?.length) {
    const [done, all] = acCount(t);
    const mark = (s: string, now: boolean) => (s === "done" || s === "already-met" ? c.ok("■") : now ? c.accent("▶") : s === "red" ? c.bad("✗")
      : s === "green" ? c.ok("●") : c.faint("□"));
    out.push("", `${c.bold("Criteria")}: ${criteria(done, all, c)} done  ${t.acs.map((a) => mark(a.status, a.id === t.ac)).join("")}`);
    t.acs.forEach((a) => out.push(`  ${mark(a.status, a.id === t.ac)} ${c.bold(a.id)} ${c.dim(a.layer)}  ${fit(a.title.replace(/\s+/g, " "), width - a.id.length - a.layer.length - 8)}`));
  }
  const u = t.usage ?? ({} as ThreadState["usage"]);
  const used = tokensOf(u);
  // meta: quieter than the content above, still readable
  out.push("", c.meta(`Tokens: ${short(used)}` + (u.cap_tokens ? ` of ${short(u.cap_tokens)} (${Math.round((100 * used) / u.cap_tokens)} %)` : " (no cap)")
    + ` · ${usd(u.cost_usd)}`));
  if (t.error) out.push(`${c.bad("Error")}: ${c.bad(fit(t.error))}`);
  const b = t.blockers ?? [];
  if (b.length) out.push(c.dim(`Before a push: ${b.length} check${b.length === 1 ? "" : "s"} still to run (${b.map((x) => x.gate).join(", ")}); the flow runs them at the end. /next says how.`));
  out.push(c.meta(`flow id ${t.thread_id} · /steps, /timeline, /flows for more`));
  return out.join("\n");
}

/** keel 0.18.55 keel does not know the project yet (no .keel/config.yml; an older keel does not say): what to do, in
 *  one line; null when it knows it, or while its init flow runs or waits. */
export function initHint(p: Project | null, t: ThreadState | null | undefined, c: Paint = PLAIN): string | null {
  if (!p || p.configured !== false) return null;
  if (t && t.workflow_id === "init" && (t.status === "running" || t.status === "waiting")) return null;
  return `keel does not know this project yet: ${c.bold(c.gate("/flow init"))} sets it up (finds the stack and the test commands, writes .keel/config.yml and the project knowledge).`;
}

export function next(p: Project | null, view: FlowView | null, c: Paint = PLAIN): string {
  const t = view?.thread;
  const cmd = (x: string) => c.bold(c.gate(x));
  if (!p) return "no project: add one in the dashboard (All projects › Add projects), then /project.";
  if (!t && p.configured === false) return `no flow yet, and keel does not know this project: start with ${cmd("/flow init")} (recommended), then ${c.accent("/flow")} for the rest.`;
  if (!t) return `no flow: start one with ${c.accent("/flow")} (feature, change, fix, diagnose, review, cover, ship, hunt, lint, init …).`;
  const w = t.waiting;
  const ac = t.ac ? ` for ${t.ac}` : "";
  let out: string[];
  const at = (x: string) => c.gate(`waiting at ${x}`);
  if (t.status === "waiting" && w) {
    if (w.kind === "clarify") out = [`${at(w.title)}: answer the ${(w.questions ?? []).length} question(s) with ${cmd("/answer")}.`];
    // a question with named choices first: a stuck criterion (kind fix) is answered with its choices, not approve
    else if (w.choices?.length) out = [`${at(`${w.step} (${w.title})`)}${ac}: ${cmd(answerWords(w))}.`, ...choiceLines(w, c).map((l) => "  " + l)];
    else if (w.kind === "budget") out = [`${at(w.title)}: ${cmd("/approve")} to go past the token cap, or ${cmd("/reject <why>")} to stop.`];
    else if (w.kind === "fix") out = [`${at(w.title)}: ${cmd("/approve")} to try again, or ${cmd("/reject <why>")} to stop the flow.`];
    else {
      const l = w.labels ?? {};
      out = [`${at(`${w.step} (${w.title})`)}${ac}: ${cmd("/approve")}` + (l.approve ? c.dim(` ("${l.approve}")`) : "")
        + ((w.options ?? []).includes("reject") ? ` or ${cmd("/reject <why>")}` + (l.reject ? c.dim(` ("${l.reject}")`) : "") : "") + "."];
    }
  } else if (t.status === "running") out = [`${c.accent("running")} ${t.current || "the next step"} (phase ${t.phase || "none"})${ac}: nothing to do, keel is working. ${c.accent("/live")} follows the agents.`];
  else if (t.status === "done") out = [`flow ${flowName(t, view?.workflow ?? null)} is ${c.ok("done")}. Review the branch ${p.branch || ""} and open a PR, or start a new flow (${c.accent("/flow")}).`];
  else if (t.status === "failed") out = [`flow ${c.bad("failed")} at ${t.current}: ${t.error || "see /timeline"}. ${c.accent("/resume")} goes on from the last saved step.`];
  else if (t.status === "stopped") out = [`flow ${c.faint("stopped")} at ${t.current}. ${c.accent("/resume")} goes on from the last saved step, or start a new flow (${c.accent("/flow")}).`];
  else out = [`flow is ${t.status} at ${t.current}.`];
  const blockers = t.blockers ?? [];
  if (blockers.length) out.push("", c.warn("blocking a push:"), ...blockers.map((b) => `  ${c.bad(b.gate + ":")} ${b.why} ${c.faint("->")} ${c.warn(b.fix)}`));
  return out.join("\n");
}

/** One line for the start screen: what waits for you. */
export function waitingBrief(inbox: InboxItem[], p: Project | null): string {
  if (!inbox.length) return "nothing waits for you";
  const here = p ? inbox.filter((i) => i.project_id === p.id) : [];
  const first = here[0] ?? inbox[0];
  const elsewhere = inbox.length - here.length;
  return `${inbox.length} waiting for you · ${first.kind}: ${first.title}` + (here.length ? "" : ` (${first.project_name})`)
    + (here.length && elsewhere ? ` · ${elsewhere} in other projects` : "");
}

export function timeline(history: Checkpoint[], jobs: Job[], limit = 20, thread?: ThreadState | null, c: Paint = PLAIN): string {
  const ev: [number, string][] = [];
  const at = (s?: string | null) => (s ? Date.parse(s) || 0 : 0);
  history.forEach((h) => ev.push([at(h.at), `${c.faint(clock(h.at))}  ${c.run("step ")}  ${c.bold(h.step)}` + (h.note ? c.dim(` · ${h.note}`) : "")]));
  jobs.forEach((j) => {
    const where = [j.step, j.ac].filter(Boolean).join(" ");
    ev.push([at(j.started_at), `${c.faint(clock(j.started_at))}  ${c.accent("agent")}  ${c.bold(j.agent)} ${badge(j.status, c)} · ${where} · `
      + c.dim(`${n(tokensOf(j))} tokens · ${modelText(j)}`) + " " + c.faint(`job ${j.id}`)]);
  });
  if (thread?.error) ev.push([at(thread.updated_at), `${c.faint(clock(thread.updated_at))}  ${c.bad("flow ")}  ${badge(thread.status, c)} · ${c.bad(thread.error)}`]);
  if (!ev.length) return thread ? "no events yet." : "nothing yet: no flow has run on this project.";
  return ev.sort((a, b) => b[0] - a[0]).slice(0, limit).map(([, l]) => l).join("\n");
}

/** /flows: one short line per flow, numbered (/stop <n> takes the number); its id shortened at the end. */
export function flows(rows: RunRow[], c: Paint = PLAIN): string {
  if (!rows.length) return `no flow has run on this project yet. ${c.accent("/flow")} starts one.`;
  const title = (s: string) => (s.length > 48 ? s.slice(0, 47).trimEnd() + "…" : s);
  return rows.map((r, k) => `${c.bold(`${String(k + 1).padStart(2)}.`)} ${badge(r.status, c)}${r.current ? c.dim(` at ${r.current}`) : ""}  ${c.bold(`"${title(r.title)}"`)}`
    + `  ${c.accent(r.workflow_id || "?")} · criteria ${criteria(r.acs_done ?? 0, r.acs_total ?? 0, c)} · ${usd(r.cost_usd)}`
    + (r.where === "worktree" ? c.dim(" · own worktree") : "") + (r.error ? ` · ${c.bad("error")}` : "") + "  " + c.faint(r.thread_id.slice(0, 8))).join("\n")
    + "\n" + c.dim(`/stop <n> stops one, /stop all stops every running or waiting one.`);
}

export function inbox(items: InboxItem[], c: Paint = PLAIN): string {
  if (!items.length) return "nothing waits for a person.";
  const out: string[] = [];
  items.forEach((i, k) => {
    out.push(`${c.bold(c.gate(`${k + 1}.`))} ${c.dim(i.project_name || i.project_id)} · ${c.bold(i.flow || i.thread_id)} · ${c.gate(`${i.kind}: ${i.title}`)}`
      + (i.ac ? ` (${c.accent(i.ac)})` : "") + ` · ${c.dim((i.options ?? []).join(" / ") || "-")}`);
    (i.questions ?? []).forEach((q) => out.push(`     ${c.strong("hint", q.id + ":")} ${c.strong("question", q.question)}`));
    if (i.permission) out.push(`     command: ${c.kw(i.permission.command)}`);
    if (i.detail) out.push(...String(i.detail).slice(0, 300).split("\n").map((l) => "     " + labelLine(l, c)));
  });
  return out.join("\n");
}

export function jobs(rows: Job[], c: Paint = PLAIN): string {
  if (!rows.length) return "no agent calls yet.";
  return rows.map((j) => `${badge(j.status, c)} ${c.bold(j.agent)} · ${[j.step, j.ac].filter(Boolean).join(" ") || "-"} · `
    + c.dim(`${modelText(j)} · ${n(tokensOf(j))} tokens · ${usd(j.cost_usd)} · ${clock(j.started_at)}`) + " " + c.faint(`job ${j.id}`)).join("\n");
}

export function stepLine(s: JobStep, width = 160, c: Paint = PLAIN): string {
  const head = s.kind === "tool" ? `${s.tool ?? "tool"}${s.server ? ` (${s.server})` : ""}: ` : s.path ? `${s.kind} ${s.path}: ` : `${s.kind}: `;
  const text = String(s.text ?? "").replace(/\s+/g, " ").trim();
  const line = head + text;
  const cutLine = line.length > width ? line.slice(0, width - 1) + "…" : line;
  if (!c.colored) return cutLine;
  const h = cutLine.slice(0, Math.min(head.length, cutLine.length));
  const col = s.ok === false || s.kind === "guard" || s.kind === "error" ? c.bad : s.kind === "write" || s.kind === "edit" ? c.ok : s.kind === "read" ? c.run : c.accent;
  return col(h) + c.dim(cutLine.slice(h.length));
}

export function live(running: Job[], last: Record<string, JobStep | undefined>, c: Paint = PLAIN): string {
  if (!running.length) return "no agent is running.";
  const out: string[] = [];
  running.forEach((j) => {
    out.push(`${badge("running", c)} ${c.bold(j.agent)} on ${j.project_id} · ${[j.step, j.ac].filter(Boolean).join(" ")} · `
      + c.dim(`${modelText(j)} · ${n(tokensOf(j))} tokens · since ${clock(j.started_at)}`) + " " + c.faint(`job ${j.id}`));
    const s = last[j.id];
    if (s) out.push(`    ${c.faint("last:")} ${stepLine(s, 160, c)}`);
  });
  return out.join("\n");
}

export function budget(now: BudgetNow, c: Paint = PLAIN): string {
  const out = [`${label("today", c)}  ${c.bold(n(now.today?.tokens))} tokens · ${usd(now.today?.cost_usd)}`,
    `${label("month", c)}  ${c.bold(n(now.month?.tokens))} tokens · ${usd(now.month?.cost_usd)}`];
  (now.flows ?? []).forEach((f) => out.push(`${label("flow ", c)}  "${c.bold(f.title)}" ${badge(f.status, c)} · ${n(f.tokens)} tokens`
    + c.dim(f.cap_tokens ? ` of cap ${n(f.cap_tokens)}` : "") + ` · ${usd(f.cost_usd)}`));
  (now.caps ?? []).forEach((cp) => {
    const x = cp as unknown as Record<string, unknown>;
    out.push(`${c.warn("cap  ")}  ${x.scope} ${x.window ?? ""}: ${x.left} of ${x.limit} ${x.unit} left` + c.dim(x.resets_at ? ` (resets ${x.resets_at})` : "") + ` · then ${x.action}`);
  });
  return out.join("\n");
}

/** #115 how a crash ended, in words: keel repaired it, you retried or stopped, it waits for you, or the flow failed. */
const END_WORD: Record<CrashEnd, string> = {
  repaired: "keel repaired it", repairing: "keel repairs it", retried: "retried", waiting: "waits for you", stopped: "stopped", failed: "the flow failed",
};

/** #115 the flow doctor's report (the dashboard's Watch › Flow doctor): how many crashes and how they ended, each kind with
 *  what to improve and, for a bug in keel, where in its code, and the latest crashes. */
export function doctor(r: DoctorReport, c: Paint = PLAIN, now = Date.now()): string {
  const days = `last ${r.days} day${r.days === 1 ? "" : "s"}`;
  if (!r.total) return `${label("flow doctor", c)}  ${c.dim(days)}\nNo step crashed in keel's flows: nothing to repair.`;
  const tone = (e: CrashEnd) => (e === "repaired" || e === "retried" ? c.ok : e === "waiting" ? c.warn : e === "failed" ? c.bad : c.dim);
  const ends = (x: Partial<Record<CrashEnd, number>>) => (["repaired", "retried", "waiting", "stopped", "failed"] as const)
    .filter((e) => (x[e] ?? 0) > 0).map((e) => tone(e)(`${e} ${x[e]}`)).join(c.dim(" · "));
  const out = [`${label("flow doctor", c)}  ${c.dim(days)}`,
    `${c.bold(String(r.total))} crash${r.total === 1 ? "" : "es"} in ${r.flows} flow${r.flows === 1 ? "" : "s"}: ${ends(r)}`];
  out.push("", label("what crashed", c));
  for (const k of r.kinds) {
    out.push(`  ${c.bold(k.title)} ${c.dim(`(${k.kind}) · ${k.count}×`)}${ends(k) ? ` ${c.dim("·")} ${ends(k)}` : ""}`);
    out.push(`    ${k.advice}`);
    const e = (k.example?.error ?? "").split("\n").find((l) => l.trim())?.trim();
    if (e) out.push(c.dim(`    e.g. ${e.length > 110 ? e.slice(0, 109) + "…" : e}`));
    if (k.where.length) out.push(c.dim(`    in keel's code: ${k.where.slice(0, 3).join(", ")}`));
  }
  if (r.recent.length) {
    out.push("", label("latest", c));
    for (const x of r.recent.slice(0, 6)) {
      const where = [x.project, x.workflow, x.step].filter(Boolean).join(" · ");
      out.push(`  ${c.dim(age(x.at, now).padEnd(9))} ${where} ${c.dim("·")} ${x.title ?? x.kind ?? "a crash"}${x.end ? ` ${c.dim("→")} ${tone(x.end)(END_WORD[x.end] ?? x.end)}` : ""}`);
    }
  }
  return out.join("\n");
}

export function workflowsList(rows: Workflow[], c: Paint = PLAIN): string {
  if (!rows.length) return "no workflows.";
  return rows.map((w) => `${c.accent(w.id.padEnd(18))} ${c.bold(w.name)} · ${c.dim(`${w.steps.length} steps`)} · `
    + c.gate(`${w.steps.filter((s) => s.kind === "gate").length} gates`)
    + c.dim((w.source ? ` · ${w.source}` : "") + (w.runs ? ` · ran ${w.runs}x` : ""))).join("\n");
}

type RulesLike = {
  config_file?: string; rules_folder?: string;
  engines?: { engine: string; words: string }[];
  builtins?: { id: string; words: string; phases: string; can_off?: boolean; off?: boolean }[];
  lists?: { key: string; value: string; saved_in: string }[];
  packs?: { id: string; version?: string; off?: boolean; title: string; summary?: string; source?: string; file?: string; rules?: { kind: string; match: string; phases: string; reason: string }[] }[];
  pack_errors?: { file: string; errors: string[] }[];
};
type RulesNowLike = { phase?: string; meaning?: string; may?: { what: string }[]; may_not?: { what: string }[]; fired?: { count: number; reason: string }[] };

export function rules(p: Project, v: RulesLike, now?: RulesNowLike | null, c: Paint = PLAIN): string {
  const h = (x: string) => c.bold(c.accent(x));
  const out = [`keel's rules for ${c.bold(p.name || p.id)} ${c.dim(`(saved in ${v.config_file} and ${v.rules_folder}/)`)}:`, "", h("Engines:")];
  (v.engines ?? []).forEach((e) => out.push(`  ${c.bold(e.engine)}: ${e.words}`));
  out.push("", h("Always on:"));
  (v.builtins ?? []).filter((b) => !b.can_off).forEach((b) => out.push(`  ${c.kw(b.id)}: ${b.words} ${c.dim(`(${b.phases})`)}`));
  out.push("", h("keel's rules you can switch off:"));
  (v.builtins ?? []).filter((b) => b.can_off).forEach((b) => out.push(`  ${c.kw(b.id)}${b.off ? c.bad(" [OFF]") : ""}: ${b.words} ${c.dim(`(${b.phases})`)}`));
  if (v.lists?.length) {
    out.push("", h("The project's lists:"));
    v.lists.forEach((x) => out.push(`  ${c.attr(x.key)} = ${x.value}  ${c.dim(`(${x.saved_in})`)}`));
  }
  const packs = v.packs ?? [];
  out.push("", packs.length ? h("Rule packs:") : `${h("Rule packs:")} none (the dashboard's Rules page adds one from the library).`);
  packs.forEach((pk) => {
    out.push(`  ${c.kw(pk.id)} ${pk.version ?? ""}${pk.off ? c.bad(" [OFF]") : ""} — ${c.bold(pk.title)}: ${pk.summary ?? ""}`);
    (pk.rules ?? []).forEach((r) => out.push(`    ${c.warn(r.kind)} ${r.match} ${c.dim(`(${r.phases})`)}: ${r.reason}`));
  });
  (v.pack_errors ?? []).forEach((e) => out.push(`  ${c.bad("NOT USED")} ${e.file}: ${e.errors.join("; ")}`));
  if (now) {
    out.push("", `${h("Now")} (phase ${c.bold(now.phase ?? "")}): ${now.meaning ?? ""}`,
      `  ${c.ok("may change:")} ` + ((now.may ?? []).map((b) => b.what).join(", ") || "nothing"),
      `  ${c.bad("may not change:")} ` + ((now.may_not ?? []).map((b) => b.what).join(", ") || "—"));
    (now.fired ?? []).forEach((f) => out.push(`  ${c.warn(`stopped ${f.count}x:`)} ${f.reason}`));
  }
  return out.join("\n");
}

type InstalledLike = { name: string; version?: string; title?: string; from?: string; on?: boolean; status?: string; update?: string; problems?: string[] };

export function plugins(installed: InstalledLike[], here?: { name: string; enabled: boolean }[], c: Paint = PLAIN): string {
  if (!installed.length) return "keel has no plugins (a core-only keel). Settings › Plugins adds them.";
  const on = new Map((here ?? []).map((p) => [p.name, p.enabled]));
  const onOff = (b: boolean | undefined) => (b ? c.ok("on") : c.faint("off"));
  return ["keel has:", ...installed.map((p) => `  ${c.accent(p.name.padEnd(10))} ${c.dim(p.version ?? "")}  ${c.bold(p.title ?? "")} · ${onOff(p.on)}`
    + (on.has(p.name) ? ` · this project: ${onOff(on.get(p.name))}` : "") + c.dim(p.from ? ` · from ${p.from}` : "")
    + (p.update ? c.warn(` · update ${p.update}`) : "") + (p.problems?.length ? c.bad(` · problem: ${p.problems.join("; ")}`) : ""))].join("\n");
}

export function which(url: string, h: Health | null): string {
  if (!h) return `${url} · not reachable`;
  return `${url} · ${h.native ? "native" : "docker"}` + (h.fake ? " · fake model" : "") + (h.engine ? "" : " · engine down");
}

/** An agent's tool steps as xal shows them: ">" reads, "*" changes, the title faint, the outcome at the right (✓ / x). */
export function toolLines(steps: JobStep[], width: number, c: Paint = PLAIN, max = 6): string[] {
  const tools = steps.filter((s) => !["text", "answer", "thinking"].includes(s.kind));
  const shown = tools.slice(-max);
  const out: string[] = [];
  if (tools.length > shown.length) out.push(c.faint(`  … ${tools.length - shown.length} earlier steps`));
  for (const s of shown) {
    const changes = s.kind === "write" || s.kind === "edit";
    const failed = s.ok === false || s.kind === "guard" || s.kind === "error";
    const glyph = changes ? "*" : ">";
    const title = (s.kind === "tool" ? `${s.tool ?? "tool"}${s.server ? ` (${s.server})` : ""} ` : `${s.kind} `)
      + (s.path ?? String(s.text ?? "").replace(/\s+/g, " ").trim());
    const status = (s.ms ? `${(s.ms / 1000).toFixed(1)}s ` : "") + (failed ? "x" : "✓");
    const room = Math.max(10, width - 4 - status.length - 2);
    const t = title.length > room ? title.slice(0, room - 1) + "…" : title;
    const pad = " ".repeat(Math.max(1, width - 4 - t.length - status.length));
    out.push(`  ${c.faint(glyph)} ${changes ? c.fg(t) : c.faint(t)}${pad}${c.faint(status.slice(0, -1))}${failed ? c.bad("x") : c.ok("✓")}`);
  }
  return out;
}

/** The line after a turn: ✓ Finished in 4.2s, x Failed after …, ! Interrupted after … (xal's status words). */
export function turnFooter(outcome: "done" | "failed" | "stopped", ms: number, extra: string, c: Paint = PLAIN): string {
  const s = `${(ms / 1000).toFixed(1)}s`;
  const [glyph, words] = outcome === "done" ? [c.ok("✓"), `Finished in ${s}`] : outcome === "failed" ? [c.bad("x"), `Failed after ${s}`]
    : [c.warn("!"), `Interrupted after ${s}`];
  return `  ${glyph} ${c.dim(words + (extra ? ` · ${extra}` : ""))}`;
}

/** KeelBot's changed files: each with its status and +/- lines, then its diff with line numbers (at most `lines`). */
export function changesText(changes: HelperChange[], c: Paint = PLAIN, lines = 24): string {
  if (!changes.length) return "KeelBot changed no file in this chat (or every change is kept or undone).";
  const out: string[] = [];
  for (const ch of changes) {
    const mark = ch.status === "added" ? c.ok("A") : ch.status === "deleted" ? c.bad("D") : c.warn("M");
    out.push(`${mark} ${c.bold(ch.path)}  ${c.ok(`+${ch.added}`)} ${c.bad(`-${ch.removed}`)}`);
    const body = renderDiff(ch.diff.replace(/\n$/, ""), c).filter((_, i, all) => !/^(\s*\x1b\[[0-9;]*m)?\s*(---|\+\+\+) /.test(stripStyle(all[i])));
    out.push(...body.slice(0, lines).map((l) => "  " + l));
    if (body.length > lines) out.push(c.faint(`  … ${body.length - lines} more lines`));
  }
  return out.join("\n");
}

const stripStyle = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, "");

/** keel 0.18.7 an agent's flow, short, for its line: "implement next step" (cut at 32 characters), or nothing. */
export function flowOf(titles: Record<string, string>, threadId?: string | null): string {
  const t = threadId ? titles[threadId] : "";
  return t ? `"${t.length > 32 ? t.slice(0, 31).trimEnd() + "…" : t}"` : "";
}

