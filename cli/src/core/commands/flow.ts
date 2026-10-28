// Flow and gate commands: start, resume, stop, answer, approve and reject, the diagrams, and the question chain.

import { api, ApiError, get, post, type Job, type JobStep, type RunMode, type ThreadState, type Workflow } from "../sdk";
import { inboxApi, type InboxItem } from "../../../../web/src/inboxApi";
import * as fmt from "../format";
import { pressButton, RUN_MODE_ABOUT, RUN_MODES } from "../buttons";
import { choiceProblem, choiceRows } from "../../../../web/src/questions";
import type { ChoiceInfo } from "../../../../web/src/api";
import { resolve } from "node:path";
import { COMMANDS_BY_NAME, P, diagram, errorReply, fail, flowNow, join, needProject, ok, text } from "./base";
import type { Choice, Ctx, Impl, Reply } from "./base";

const DIAGRAM_KINDS = ["flow", "workflow", "map-er", "map-system", "map-modules", "graph"] as const;


let workflowCache: { pid: string; rows: Workflow[] } | null = null;
export async function workflows(ctx: Ctx): Promise<Workflow[]> {
  const pid = needProject(ctx).id;
  if (workflowCache?.pid !== pid) workflowCache = { pid, rows: await api.workflows(pid) };
  return workflowCache.rows;
}
const cachedWorkflowIds = (ctx: Ctx) => (workflowCache?.pid === ctx.core.pid ? workflowCache.rows.map((w) => w.id) : []);

/** keel's ASCII renderer (engine diagrams.py through POST /api/diagrams/ascii). */
export async function draw(ctx: Ctx, kind: string, data: unknown, zoom?: string, maxLines = 60): Promise<string> {
  const r = await post<{ text: string }>("/diagrams/ascii", { kind, data, width: Math.max(60, Math.min(ctx.width - 2, 160)), max_lines: maxLines,
    ...(zoom ? { zoom } : {}) });
  return r.text;
}

async function startFlow(ctx: Ctx, wid: string, idea: string, flags: { fake?: boolean; dirty?: boolean; force?: boolean; mode?: RunMode } = {}): Promise<Reply> {
  const p = needProject(ctx);
  const first = idea.split("\n")[0].trim();
  const title = first.length <= 72 ? first : first.slice(0, 71).trimEnd() + "…";
  try {
    const t = await api.startFlow(p.id, { workflow_id: wid, title, request: idea, options: { started_via: "tui" },
      ...(flags.fake ? { allow_fake: true } : {}), ...(flags.dirty ? { allow_dirty: true } : {}), ...(flags.force ? { force: true } : {}),
      // 0.18.28 /flow --mode important: the flow starts in that run mode (else the project's)
      ...(flags.mode ? { run_mode: flags.mode } : {}) });
    await ctx.core.refresh();
    // the reply can come back before the graph reaches its first step: no step and no phase yet, so say it is starting
    const at = t.current || t.phase ? ` at ${t.current || "-"} (phase ${t.phase || "none"})` : "";
    const now = at || (t.status && t.status !== "running") ? `It is ${t.status}${at}` : "It is starting";
    const lines = [`started ${wid} "${title}" on ${p.id} (thread ${t.thread_id}), recorded as started via keel TUI.`,
      `${now}; it runs until its first gate for you. Run mode: ${t.run_mode ?? flags.mode ?? "the project's"}.`];
    if (t.cap_note) lines.push(t.cap_note);
    lines.push("Follow it: Ctrl+F (flow rail), /live (agents), /next.");
    return ok(lines.join("\n"));
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      const body = e.body ?? {};
      // 0.18.57 untracked paths that are not the person's work: Ignore this path (.git/info/exclude), then start again
      const ignorable = Array.isArray(body.ignorable) ? (body.ignorable as string[]) : [];
      if (ignorable.length && !flags.dirty) {
        const choices: Choice[] = [...ignorable.slice(0, 5).map((path) => ({ label: `Ignore ${path} (adds it to .git/info/exclude)`, value: `ignore:${path}` })),
          { label: "Start anyway (my files stay out of keel's commits)", value: "dirty" }, { label: "No", value: "no" }];
        return { blocks: errorReply(e).blocks, pick: { title: "Uncommitted work in the folder: what now?", choices,
          then: async (v) => {
            if (v.startsWith("ignore:")) {
              await api.excludePath(p.id, v.slice("ignore:".length));
              return startFlow(ctx, wid, idea, flags);
            }
            return v === "dirty" ? startFlow(ctx, wid, idea, { ...flags, dirty: true }) : text("Not started.");
          } } };
      }
      // 0.18.57 one owner per task: say who works on it; start anyway only on a yes
      const owners = Array.isArray(body.owners) ? (body.owners as { who: string; why: string }[]) : [];
      if (owners.length && !flags.force) {
        return { blocks: errorReply(e).blocks, pick: { title: `${owners.map((o) => `${o.who} (${o.why})`).join("; ")} — start anyway?`,
          choices: [{ label: "Yes, start anyway (two agents on one task write over each other)", value: "yes" }, { label: "No", value: "no" }],
          then: (v) => (v === "yes" ? startFlow(ctx, wid, idea, { ...flags, force: true }) : text("Not started.")) } };
      }
      const m = (e.message + " " + (e.hint ?? "")).toLowerCase();
      const why = m.includes("fake model") && !flags.fake ? "fake" : (m.includes("uncommitted") || m.includes("dirty")) && !flags.dirty ? "dirty" : null;
      if (why) {
        return { blocks: errorReply(e).blocks, pick: { title: why === "fake" ? "Run with the fake model anyway?" : "Start with the uncommitted work in the folder?",
          choices: [{ label: "Yes, start it", value: "yes" }, { label: "No", value: "no" }],
          then: (v) => (v === "yes" ? startFlow(ctx, wid, idea, { ...flags, [why]: true }) : text("Not started.")) } };
      }
    }
    return errorReply(e);
  }
}

export async function decide(ctx: Ctx, decision: "approve" | "reject", rest: string): Promise<Reply> {
  const m = /^#?(\d+)\b\s*(.*)$/s.exec(rest.trim());
  if (m) {
    // /approve 2 — the inbox's second item, in any project
    const items = ctx.core.snapshot.inbox.length ? ctx.core.snapshot.inbox : (await inboxApi.list()).items;
    const item = items[Number(m[1]) - 1];
    if (!item) return fail(`The inbox has no item ${m[1]} (it has ${items.length}). /inbox lists them.`);
    return decideItem(ctx, item, decision, m[2].trim());
  }
  const t = flowNow(ctx);
  if (!t || t.status !== "waiting" || !t.waiting) {
    const n = ctx.core.snapshot.inbox.length;
    return fail(`Nothing waits on ${ctx.core.snapshot.project?.id ?? "this project"}.` + (n ? ` The inbox has ${n}: /inbox, then /${decision} <n>.` : ""));
  }
  const w = t.waiting;
  if (!(w.options ?? ["approve", "reject"]).includes(decision)) return fail(`${w.title} takes only: ${(w.options ?? []).join(", ")}`);
  if (w.kind === "clarify" && decision === "approve") return fail(`${w.title} asks questions: /answer goes through them.`);
  if (decision === "reject" && !rest.trim()) return fail("Say why: /reject <why>. The reason goes back to the agent.");
  return actOnFlow(ctx, t, decision, rest.trim());
}

async function actOnFlow(ctx: Ctx, t: ThreadState, decision: "approve" | "reject", why: string, payload?: Record<string, unknown>): Promise<Reply> {
  const before = t.waiting?.title ?? "the gate";
  try {
    const answered = await inboxApi.act(t.thread_id, { decision, ...(why ? { why } : {}), ...(payload ? { payload } : {}), id: t.waiting?.id ?? null });
    await ctx.core.refresh();
    // the flow moves on at once: say where it is now (the answer above is from the moment of the decision)
    const now = ctx.core.snapshot.flow?.thread;
    const s = now && now.thread_id === t.thread_id ? now : answered;
    const after = `${s.status} at ${s.current || "-"} (phase ${s.phase || "none"})` + (s.waiting ? `; now waiting: ${s.waiting.title}` : "");
    return ok(`${decision === "approve" ? "approved" : "sent back"} ${before}. The flow is ${after}.`);
  } catch (e) {
    return errorReply(e);
  }
}

async function decideItem(ctx: Ctx, item: InboxItem, decision: "approve" | "reject", why: string): Promise<Reply> {
  try {
    if (item.permission) {
      const d = decision === "approve" ? "once" : "deny";
      await inboxApi.decide(item.permission.id, d, why);
      await ctx.core.refresh();
      return ok(`${d === "once" ? "allowed once" : "refused"}: ${item.permission.command}`);
    }
    if (item.kind === "clarify" && decision === "approve") return fail("That one asks questions: /project " + item.project_id + ", then /answer.");
    if (decision === "reject" && !why) return fail(`Say why: /reject ${ctx.core.snapshot.inbox.indexOf(item) + 1} <why>.`);
    const s = await inboxApi.act(item.thread_id, { decision, ...(why ? { why } : {}), id: item.id ?? null });
    await ctx.core.refresh();
    return ok(`${decision === "approve" ? "approved" : "sent back"} ${item.title} (${item.project_name}). The flow is ${s.status} at ${s.current || "-"}.`);
  } catch (e) {
    return errorReply(e);
  }
}

/** A question as keel shows it (a clarify gate's, or one KeelBot asks): options (the recommended first), and
 *  `multiple` when several may be picked (keel 0.18.9). */
export type Question = { id: string; question: string; why?: string; multiple?: boolean;
  /** keel 0.18.22 `why`: the option takes a reason (a findings gate's "accept"); its answer is "<label>: <reason>" */
  options: { label: string; description?: string; recommended?: boolean; why?: "required" | "optional" }[] };
export type Answers = Record<string, string | string[]>;

const OTHER = "\u0000other";
const BACK = "\u0000back";

/** keel 0.18.9 questions one after the other: pick one (or tick several), Other… for your own words, ← Back; then a
 *  check of every answer ("Send these answers?", change one) before `send` gets them.
 *  keel 0.18.64 `keep` hears the answers so far at every step (/answer keeps them as a draft). */
export function questionChain(qs: Question[], send: (answers: Answers) => Promise<Reply> | Reply, k = 0, answers: Answers = {},
  onlyOne = false, keep?: (answers: Answers) => void): Promise<Reply> | Reply {
  keep?.(answers);
  // 0.18.61 one question: its answer goes at once; the "Send these answers?" check is for several
  if (k >= qs.length) return qs.length === 1 ? send(answers) : reviewAnswers(qs, send, answers, keep);
  const q = qs[k];
  const opts = [...q.options].sort((a, b) => Number(!!b.recommended) - Number(!!a.recommended));
  // one answer changed from the check: straight back to it; else the next question
  const next = (v: string | string[]) => onlyOne ? reviewAnswers(qs, send, { ...answers, [q.id]: v }, keep)
    : questionChain(qs, send, k + 1, { ...answers, [q.id]: v }, false, keep);
  const own = (picked: string[]) => ({ blocks: [], ask: { prompt: q.question, then: (words: string) =>
    next(q.multiple ? [...picked, words.trim()].filter(Boolean) : words.trim()) } }) as Reply;
  // keel 0.18.22 an option that takes a reason: ask for it (again while empty when it is needed), send "<label>: <reason>"
  const reason = (o: Question["options"][number], again = false): Reply => ({
    blocks: again ? [{ kind: "error", text: `${o.label} needs a reason.` }] : [],
    ask: { prompt: o.why === "required" ? `Why ${o.label}? (needed)` : `Why ${o.label}? (optional: Enter sends without)`,
      then: (words: string) => (o.why === "required" && !words.trim() ? reason(o, true)
        : next(words.trim() ? `${o.label}: ${words.trim()}` : o.label)) } });
  const choices: Choice[] = [
    ...opts.map((o) => ({ label: o.label + (o.recommended ? " (recommended)" : "") + (o.why === "required" ? " · needs a reason" : ""),
      value: o.label, hint: o.description })),
    { label: "Other… (my own words)", value: OTHER },
    ...(k > 0 && !onlyOne ? [{ label: "← Back", value: BACK, hint: "the question before" }] : []),
  ];
  return {
    blocks: [{ kind: "text", text: `${k + 1}/${qs.length} ${q.question}${q.multiple ? " (pick any)" : ""}` + (q.why ? `\n   ${q.why}` : "") }],
    pick: {
      title: q.question, multi: !!q.multiple, choices,
      then: (v) => {
        const picked: string[] = q.multiple ? (JSON.parse(v) as string[]) : [v];
        if (picked.includes(BACK)) return questionChain(qs, send, k - 1, answers, false, keep);
        const real = picked.filter((x) => x !== OTHER);
        if (picked.includes(OTHER)) return own(real);
        if (!real.length) return questionChain(qs, send, k, answers, onlyOne, keep);  // nothing ticked: ask again
        const takes = !q.multiple ? opts.find((o) => o.label === real[0] && (o.why === "required" || o.why === "optional")) : undefined;
        if (takes) return reason(takes);
        return next(q.multiple ? real : real[0]);
      },
    },
  };
}

/** Every answer at once, then send them or change one. */
function reviewAnswers(qs: Question[], send: (answers: Answers) => Promise<Reply> | Reply, answers: Answers,
  keep?: (answers: Answers) => void): Reply {
  keep?.(answers);
  const shown = (v: string | string[] | undefined) => (Array.isArray(v) ? v.join("; ") : v) || "(not answered: keel uses the recommended one)";
  return {
    blocks: [{ kind: "text", text: ["Your answers:", ...qs.map((q, i) => `  ${i + 1}. ${q.question}\n     → ${shown(answers[q.id])}`)].join("\n") }],
    pick: {
      title: "Send these answers?",
      choices: [{ label: "Send them", value: "send" }, ...qs.map((q, i) => ({ label: `Change ${i + 1}: ${q.question.slice(0, 60)}`, value: `c${i}` })),
        { label: "Cancel", value: "cancel", hint: "nothing is sent" }],
      then: (v) => v === "send" ? send(answers) : v === "cancel" ? text("Not sent.")
        : questionChain(qs, send, Number(v.slice(1)), answers, true, keep),
    },
  };
}

/** `/answer id=a,b other="own words"`: a question that takes several (`multiple`) gets the comma-separated labels as a
 *  list; any other keeps its value as it is (own words may have commas). */
export function pairAnswers(qs: Question[], pairs: [string, string][]): Answers {
  return Object.fromEntries(pairs.map(([id, raw]) => {
    const v = raw.replace(/^"|"$/g, "");
    return [id, qs.find((q) => q.id === id)?.multiple ? v.split(",").map((x) => x.trim()).filter(Boolean) : v];
  }));
}

/** A picked choice: sent at once, or first its reason when it needs one (asked again while empty) or may have one. */
function answerChoice(ctx: Ctx, t: ThreadState, c: ChoiceInfo): Promise<Reply> | Reply {
  if (c.why === "none") return actOnFlow(ctx, t, "approve", "", { choice: c.name });
  const ask = (again: boolean): Reply => ({ blocks: again ? [{ kind: "error", text: `${c.label || c.name} needs a reason.` }] : [], ask: {
    prompt: c.why === "required" ? `Why ${c.label || c.name}? (needed)` : `Why ${c.label || c.name}? (optional: Enter sends without)`,
    then: (why: string) => (c.why === "required" && !why.trim() ? ask(true) : actOnFlow(ctx, t, "approve", why.trim(), { choice: c.name })) } });
  return ask(false);
}

/** keel 0.18.64 the answers picked so far in /answer's questions, per flow, for the question they answer: Esc (to switch
 *  project, to read something first) and /answer again goes on from them. Sent, or the flow asks something new: gone.
 *  keel2's own memory (nothing on disk): a new keel2 starts empty. */
const kept = new Map<string, { q: string; answers: Answers }>();

/** The waiting question's own id, else its step and criterion (as the web's drafts name it). */
const questionOf = (t: ThreadState) => t.waiting?.id || `${t.waiting?.step ?? ""}|${t.ac ?? ""}`;

/** The kept answers to the flow's waiting question ({} when none, or when they were for another question). */
export function keptAnswers(t: ThreadState): Answers {
  const d = kept.get(t.thread_id);
  if (d && d.q === questionOf(t)) return d.answers;
  kept.delete(t.thread_id);
  return {};
}

/** /answer for questions (a clarify gate's, or a gate's beside approve / reject): one after the other, checked before
 *  keel gets them, from the kept answers on (the first one not answered yet; all answered: the check). */
function askQuestions(ctx: Ctx, t: ThreadState): Promise<Reply> | Reply {
  const qs = (t.waiting?.questions ?? []) as Question[];
  const q = questionOf(t);
  const answers = keptAnswers(t);
  const keep = (a: Answers) => {
    if (Object.keys(a).length) kept.set(t.thread_id, { q, answers: a });
    else kept.delete(t.thread_id);
  };
  const send = async (a: Answers) => {
    const r = await actOnFlow(ctx, t, "approve", "", { answers: a });
    if (!r.blocks.some((b) => b.kind === "error")) kept.delete(t.thread_id);
    return r;
  };
  const done = qs.filter((x) => x.id in answers).map((x) => qs.indexOf(x) + 1);
  if (!done.length) return questionChain(qs, send, 0, {}, false, keep);
  const first = qs.findIndex((x) => !(x.id in answers));
  const r = first < 0 ? reviewAnswers(qs, send, answers, keep) : questionChain(qs, send, first, answers, false, keep);
  const said = { kind: "dim" as const, text: `  draft kept: your answers to ${done.join(", ")} from before (← Back or "Change" alters them)` };
  return r instanceof Promise ? r.then((x) => ({ ...x, blocks: [said, ...x.blocks] })) : { ...r, blocks: [said, ...r.blocks] };
}

function pickWorkflowThen(ctx: Ctx, rows: Workflow[], then: (w: Workflow) => Promise<Reply> | Reply, title: string): Reply {
  // keel 0.18.55 a project keel does not know yet: init first, marked recommended
  const unknown = ctx.core.snapshot.project?.configured === false;
  const list = unknown ? [...rows.filter(isInit), ...rows.filter((w) => !isInit(w))] : rows;
  return { blocks: [], pick: { title: unknown ? `${title} (keel does not know this project yet: init first)` : title,
    choices: list.map((w) => ({ label: w.id + (unknown && isInit(w) ? " (recommended)" : ""), value: w.id,
      hint: unknown && isInit(w) ? "sets keel up: the stack, the test commands, .keel/config.yml, the project knowledge" : `${w.name} · ${w.steps.length} steps` })),
    then: (v) => then(rows.find((w) => w.id === v)!) } };
}

/** The init flow, or a workflow made from it. */
const isInit = (w: Pick<Workflow, "id" | "based_on">) => w.id === "init" || w.based_on === "keel/init" || w.based_on === "init";

/** keel 0.18.55 another flow on a project keel does not know yet: ask once (Run init first / Start anyway); else go on. */
function initFirst(ctx: Ctx, w: Workflow, rows: Workflow[], flags: Parameters<typeof startFlow>[3], go: () => Promise<Reply> | Reply): Promise<Reply> | Reply {
  const p = ctx.core.snapshot.project;
  if (p?.configured !== false || isInit(w)) return go();
  const init = rows.find(isInit);
  return { blocks: [{ kind: "notice", text: `keel does not know ${p.name || p.id} yet (no .keel/config.yml). init finds the stack and the test commands, `
      + "and writes .keel/config.yml and the project knowledge: flows work better after it." }],
    pick: { title: "keel does not know this project yet", choices: [
      ...(init ? [{ label: "Run init first (recommended)", value: "init", hint: `then /flow ${w.id}` }] : []),
      { label: "Start anyway", value: "anyway", hint: `the ${w.id} flow, as asked` }],
    then: (v) => (v === "init" && init ? startInitFlow(ctx, flags) : go()) } };
}

/** /flow init needs no idea: it sets keel up for the project. */
const startInitFlow = (ctx: Ctx, flags: Parameters<typeof startFlow>[3]) =>
  startFlow(ctx, "init", `Set up keel for ${needProject(ctx).name || needProject(ctx).id}`, flags);

async function flowDiagram(ctx: Ctx, zoom?: string): Promise<Reply> {
  const p = needProject(ctx);
  const view = await api.flow(p.id);
  if (!view.thread || !view.workflow) return text("No flow on this project: /flow starts one, /workflows shows them.");
  const hist = await api.history(view.thread.thread_id).catch(() => []);
  return diagram(await draw(ctx, "flow", { workflow: view.workflow, thread: view.thread, visited: hist.map((c) => c.step) }, zoom, 400));
}

type MapView = { missing?: string; schema?: unknown; levels?: Record<string, unknown> };


export const FLOW_IMPLS: Impl[] = [
  {
    name: "flow",
    complete: (ctx, a) => cachedWorkflowIds(ctx).filter((x) => x.startsWith(a)),
    run: async (ctx, a) => {
      const rows = await workflows(ctx);
      const mode = /(?:^|\s)--mode[= ](manual|important|auto|readonly)(?=\s|$)/.exec(a)?.[1] as RunMode | undefined;
      if (/(?:^|\s)--mode(?:[= ]\S*)?(?=\s|$)/.test(a) && !mode) return fail(`--mode is one of ${RUN_MODES.join(", ")}.`);
      const flags = { fake: /(^|\s)--fake(\s|$)/.test(a), dirty: /(^|\s)--dirty(\s|$)/.test(a), ...(mode ? { mode } : {}) };
      const rest = a.replace(/(^|\s)--(fake|dirty)(?=\s|$)/g, " ").replace(/(^|\s)--mode[= ]\S+(?=\s|$)/g, " ").trim();
      const [first, ...more] = rest.split(/\s+/);
      const wf = rows.find((w) => w.id === first);
      const idea = wf ? more.join(" ").trim() : "";
      const ask = (w: Workflow): Promise<Reply> | Reply => (w.id === "init" ? startInitFlow(ctx, flags)
        : { blocks: [], ask: { prompt: `What should the ${w.id} flow do? (one or two sentences)`,
          then: (t) => (t.trim() ? startFlow(ctx, w.id, t.trim(), flags) : text("Not started: the idea was empty.")) } });
      if (wf && idea) return initFirst(ctx, wf, rows, flags, () => startFlow(ctx, wf.id, idea, flags));
      if (wf) return initFirst(ctx, wf, rows, flags, () => ask(wf));
      if (first) return fail(`No workflow "${first}". This project has: ${rows.map((w) => w.id).join(", ")}`);
      return pickWorkflowThen(ctx, rows, (w) => initFirst(ctx, w, rows, flags, () => ask(w)), "Which flow?");
    },
  },
  { name: "flows", run: async (ctx) => text(fmt.flows(await api.runs(needProject(ctx).id, undefined, 20), P(ctx))) },
  {
    name: "resume",
    run: async (ctx, a) => {
      // keel 0.18.7 /resume <n>: a flow from /flows by its number (or the start of its id), else the project's newest
      const arg = a.trim();
      let t: ThreadState | null = flowNow(ctx);
      if (arg) {
        const p = needProject(ctx);
        const rows = await api.runs(p.id, undefined, 20);
        const n = /^#?(\d+)$/.exec(arg);
        const row = n ? rows[Number(n[1]) - 1] : rows.find((r) => arg.length >= 6 && r.thread_id.startsWith(arg));
        if (!row) return fail(`No flow ${arg}: /flows lists them, numbered.`);
        t = (await api.flowOf(p.id, row.thread_id)).thread;
        if (!t) return fail(`No flow ${arg}.`);
      }
      if (!t) {
        const others = ctx.core.snapshot.projects.filter((p) => p.flow && p.id !== ctx.core.snapshot.project?.id).map((p) => p.id);
        return fail("No flow on this project." + (others.length ? ` Flows are on ${others.join(", ")}: /project ${others[0]}, then /resume.` : ""));
      }
      if (t.status === "waiting" || t.status === "running") return text(`The flow is ${t.status}: nothing to resume. ${t.status === "waiting" ? "/approve or /reject <why>." : ""}`);
      if (t.status === "done") return text("The flow is done: start a new one with /flow.");
      const h = await api.history(t.thread_id);
      const last = [...h].sort((a, b) => b.n - a.n)[0];
      if (!last) return fail("This flow has no saved step yet: start it again with /flow.");
      await api.rewind(t.thread_id, last.id);
      await ctx.core.refresh();
      return ok(`Resumed after "${last.step}". The next step runs now.`);
    },
  },
  {
    name: "stop",
    run: async (ctx, a) => {
      // /stop all, /stop <n> (/flows numbers them), /stop <flow id>: the project's flows, after one yes
      const arg = a.trim();
      if (arg) {
        const rows = await api.runs(needProject(ctx).id, undefined, 20);
        const open = rows.filter((r) => r.status === "running" || r.status === "waiting");
        const n = /^#?(\d+)$/.exec(arg);
        const chosen = arg === "all" ? open : n ? [rows[Number(n[1]) - 1]].filter(Boolean) : rows.filter((r) => arg.length >= 6 && r.thread_id.startsWith(arg));
        if (!chosen.length) return arg === "all" ? text("No running or waiting flow to stop.") : fail(`No flow ${arg}: /flows lists them, numbered.`);
        const shut = chosen.filter((r) => r.status !== "running" && r.status !== "waiting");
        if (shut.length === chosen.length) return text(`"${chosen[0].title}" is ${chosen[0].status} already.`);
        const b = { kind: "stop" as const, flows: chosen.filter((r) => !shut.includes(r)).map((r) => r.thread_id) };
        const name = (tid: string) => `"${rows.find((r) => r.thread_id === tid)?.title ?? tid}"`;
        return { blocks: [], pick: { title: b.flows.length === 1 ? `Stop ${name(b.flows[0])}?` : `Stop ${b.flows.length} flows: ${b.flows.map(name).join(", ")}?`,
          choices: [{ label: "Yes, stop", value: "yes", hint: "agents at work stop; every finished step stays saved" }, { label: "No", value: "no" }],
          then: async (v) => {
            if (v !== "yes") return text("Not stopped.");
            const said = await pressButton(b, needProject(ctx).id, name);
            await ctx.core.refresh();
            return ok(said);
          } } };
      }
      const t = flowNow(ctx);
      if (!t || !["running", "waiting"].includes(t.status)) return text("No running flow to stop. /stop all stops every running or waiting one.");
      return { blocks: [], pick: { title: `Stop "${t.title}"? Agents at work stop; /resume goes on later.`,
        choices: [{ label: "Yes, stop it", value: "yes" }, { label: "No", value: "no" }],
        then: async (v) => {
          if (v !== "yes") return text("Not stopped.");
          await api.stopThread(t.thread_id);
          await ctx.core.refresh();
          return ok("Stopped. Every finished step is saved: /resume goes on from there.");
        } } };
    },
  },
  {
    // the flow's run mode, as the dashboard's switch: how much keel decides by itself, from the next gate on
    name: "run",
    run: async (ctx, a) => {
      const t = flowNow(ctx);
      if (!t || !["running", "waiting"].includes(t.status)) return text("No running or waiting flow: a run mode is set when a flow starts (/flow).");
      const want = a.trim().toLowerCase() as RunMode;
      const now = t.run_mode ?? "manual";
      const set = async (m: RunMode) => {
        if (m === now) return text(`"${t.title}" runs in ${m} mode already.`);
        await inboxApi.setMode(t.thread_id, m);
        await ctx.core.refresh();
        return ok(`"${t.title}" runs in ${m} mode from its next gate on: it ${RUN_MODE_ABOUT[m]}.`);
      };
      if (want) return RUN_MODES.includes(want) ? set(want) : fail(`A run mode is one of ${RUN_MODES.join(", ")}.`);
      return { blocks: [], pick: { title: `Run mode of "${t.title}" (now ${now})`,
        choices: RUN_MODES.map((m) => ({ label: m + (m === now ? " (now)" : ""), value: m, hint: RUN_MODE_ABOUT[m] })),
        then: (v) => set(v as RunMode) } };
    },
  },
  { name: "status", run: async (ctx) => {
    await ctx.core.refresh();
    return text(fmt.status(needProject(ctx), ctx.core.snapshot.flow, P(ctx), ctx.width));
  } },
  { name: "next", run: async (ctx) => {
    await ctx.core.refresh();
    return text(fmt.next(ctx.core.snapshot.project, ctx.core.snapshot.flow, P(ctx)));
  } },
  { name: "inbox", run: async (ctx) => {
    await ctx.core.refresh();
    const items = ctx.core.snapshot.inbox;
    const c = P(ctx);
    return text(fmt.inbox(items, c) + (items.length ? `\n\n${c.bold(c.gate("/approve <n>"))} or ${c.bold(c.gate("/reject <n> <why>"))} answers one; ${c.accent("Ctrl+B")} keeps the inbox on screen.` : ""));
  } },
  { name: "approve", run: (ctx, a) => decide(ctx, "approve", a) },
  { name: "reject", run: (ctx, a) => decide(ctx, "reject", a) },
  {
    name: "answer",
    run: async (ctx, a) => {
      await ctx.core.refresh();
      const t = flowNow(ctx);
      const w = t?.waiting;
      if (!t || !w) return fail("Nothing waits for an answer here.");
      const arg = a.trim();
      if (w.kind === "clarify") {
        const pairs = [...arg.matchAll(/(\w[\w-]*)=("[^"]*"|\S+)/g)];
        if (pairs.length) return actOnFlow(ctx, t, "approve", "", { answers: pairAnswers((w.questions ?? []) as Question[], pairs.map((m) => [m[1], m[2]])) });
        return askQuestions(ctx, t);
      }
      if (w.choices?.length) {
        const rows = choiceRows(w.choices, w.choice_info);
        if (arg) {
          // /answer <choice> <why>: the first word is the choice, the rest its reason
          const [, name, why] = /^(\S+)\s*([\s\S]*)$/.exec(arg)!;
          const c = rows.find((x) => x.name === name);
          if (!c) return fail(`Choose one of: ${rows.map((x) => x.name).join(", ")}`);
          if (choiceProblem(c, why)) return fail(`${c.label || c.name} needs a reason: /answer ${c.name} <why>.`);
          return actOnFlow(ctx, t, "approve", why.trim(), { choice: c.name });
        }
        return { blocks: [], pick: { title: w.title, index: Math.max(0, rows.findIndex((x) => x.default)),
          choices: rows.map((x) => ({ label: (x.label || x.name) + (x.default ? " (default)" : "") + (x.why === "required" ? " · needs a reason" : ""),
            value: x.name, hint: x.description || (x.label && x.label !== x.name ? x.name : undefined) })),
          then: (v) => answerChoice(ctx, t, rows.find((x) => x.name === v)!) } };
      }
      if (w.questions?.length) {
        // keel 0.18.22 a gate that asks questions beside approve / reject (a findings gate: one per finding): the
        // answers go with an approve; an unanswered one follows the approve (a finding: fix)
        const qs = w.questions as Question[];
        const pairs = [...arg.matchAll(/(\w[\w-]*)=("[^"]*"|\S+)/g)];
        if (pairs.length) return actOnFlow(ctx, t, "approve", "", { answers: pairAnswers(qs, pairs.map((m) => [m[1], m[2]])) });
        return askQuestions(ctx, t);
      }
      return text(`${w.title}: /approve or /reject <why>.`);
    },
  },
  { name: "jobs", run: async (ctx) => text(fmt.jobs(await api.jobs({ project: needProject(ctx).id, limit: 20 }), P(ctx))) },
  {
    name: "live", run: async (ctx) => {
      const running = await api.jobs({ status: "running", limit: 50 });
      const last: Record<string, JobStep | undefined> = {};
      await Promise.all(running.map(async (j: Job) => {
        const s = await api.jobSteps(j.id).catch(() => ({ steps: [] as JobStep[] }));
        last[j.id] = s.steps[s.steps.length - 1];
      }));
      return { ...text(fmt.live(running, last, P(ctx)) + (running.length ? `\n\n${P(ctx).accent("Ctrl+A")} keeps the agents on screen.` : "")) };
    },
  },
  {
    name: "steps",
    run: async (ctx, a) => {
      const id = a.trim();
      if (!id) return flowDiagram(ctx);
      const r = await api.jobSteps(id);
      return text(r.steps.length ? r.steps.map((s) => `${P(ctx).faint(String(s.n).padStart(3))} ${fmt.stepLine(s, Math.max(60, ctx.width - 6), P(ctx))}`).join("\n") + (r.running ? "\n… still running" : "")
        : "no steps yet.");
    },
  },
  {
    name: "timeline",
    run: async (ctx) => {
      const p = needProject(ctx);
      const t = flowNow(ctx);
      const [hist, jobs] = await Promise.all([t ? api.history(t.thread_id) : Promise.resolve([]), api.jobs({ project: p.id, limit: 20 })]);
      return text(fmt.timeline(hist, jobs, 20, t, P(ctx)));
    },
  },
  {
    name: "workflows",
    complete: (ctx, a) => cachedWorkflowIds(ctx).filter((x) => x.startsWith(a)),
    run: async (ctx, a) => {
      const rows = await workflows(ctx);
      const [id, zoom] = a.trim().split(/\s+/);
      if (id) {
        const w = rows.find((x) => x.id === id);
        if (!w) return fail(`No workflow "${id}". This project has: ${rows.map((x) => x.id).join(", ")}`);
        return diagram(await draw(ctx, "workflow", { workflow: w }, zoom));
      }
      return join(text(fmt.workflowsList(rows, P(ctx))), pickWorkflowThen(ctx, rows, async (w) => diagram(await draw(ctx, "workflow", { workflow: w })), "Draw which workflow?"));
    },
  },
  {
    name: "diagram",
    complete: (ctx, a) => {
      const [k, rest] = a.split(/\s+/, 2);
      if (rest === undefined) return DIAGRAM_KINDS.filter((x) => x.startsWith(k));
      return k === "workflow" ? cachedWorkflowIds(ctx).filter((x) => x.startsWith(rest)).map((x) => `${k} ${x}`) : [];
    },
    run: async (ctx, a) => {
      const [kind, arg, zoom] = a.trim().split(/\s+/);
      const p = needProject(ctx);
      if (!kind) return { blocks: [], pick: { title: "Which diagram?", choices: DIAGRAM_KINDS.map((k) => ({ label: k, value: k })),
        then: (v) => (v === "workflow" || v === "graph" ? text(`/diagram ${v} <${v === "graph" ? "symbol" : "id"}>`) : COMMANDS_BY_NAME.diagram.run(ctx, v)) } };
      if (kind === "flow") return flowDiagram(ctx, arg);
      if (kind === "workflow") {
        if (!arg) return COMMANDS_BY_NAME.workflows.run(ctx, "");
        return COMMANDS_BY_NAME.workflows.run(ctx, [arg, zoom].filter(Boolean).join(" "));
      }
      if (kind === "map-er" || kind === "map-system" || kind === "map-modules") {
        const m = await get<MapView>(`/projects/${encodeURIComponent(p.id)}/map`);
        if (m.missing) return fail(`No map yet: ${m.missing}. The Map page in the dashboard builds it.`);
        if (kind === "map-er") return diagram(await draw(ctx, "map-er", { schema: m.schema ?? { tables: [], relations: [] }, ...(arg ? { focus: arg } : {}) }));
        const level = m.levels?.[kind === "map-system" ? "system" : "modules"];
        if (!level) return fail(`The map has no ${kind.slice(4)} level.`);
        return diagram(await draw(ctx, kind, { level }, arg));
      }
      if (kind === "graph") {
        if (!arg) return fail("Which symbol? /diagram graph <name>");
        const hits = await api.graphSearch(p.id, arg);
        if (!hits.available) return fail(`The code graph is not ready: ${hits.reason ?? "build it in the dashboard (Code › Graph)"}`);
        const hit = hits.results.find((h) => h.name === arg) ?? hits.results[0];
        if (!hit) return fail(`No symbol "${arg}" in the code graph.`);
        const focus = await get<Record<string, unknown>>(`/projects/${encodeURIComponent(p.id)}/graph/node?id=${encodeURIComponent(hit.id)}&depth=2`);
        return diagram(await draw(ctx, "graph", focus));
      }
      return fail(`No diagram "${kind}". keel draws: ${DIAGRAM_KINDS.join(", ")}`);
    },
  },
];
