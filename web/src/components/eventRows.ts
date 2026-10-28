// The Flow page's Events and Decisions, as data: one engine event becomes one row a person can read (its kind and colour,
// a short line, an agent's answer as Markdown, a gate's decision as a badge with its answers as a question → answer
// list, a step that starts or ends as a small divider), and the gate decisions of a flow become one list (who decided,
// when, which round). No React here: the panels draw these rows.

import type { EngineEvent } from "../api";
import { eventLine } from "./events";

const str = (v: unknown) => (v === undefined || v === null ? "" : String(v));

/** What the Events panel shows: All (everything but usage), or one group. */
export type EventFilter = "all" | "agents" | "gates" | "phases" | "usage";
export const EVENT_FILTERS: [EventFilter, string][] = [
  ["all", "All"],
  ["agents", "Agents"],
  ["gates", "Gates"],
  ["phases", "Phases"],
  ["usage", "Usage"],
];

/** A row's kind: its colour and its label. */
export type RowKind =
  | "agent"
  | "gate"
  | "phase"
  | "flow"
  | "guard"
  | "budget"
  | "usage"
  | "tool"
  | "supervisor"
  | "event";
export type RowGroup = Exclude<EventFilter, "all"> | "other";
export type Tone = "ok" | "warn" | "bad" | "idle" | "run";
export type Decision = { tone: Tone; label: string };
/** Who gave an answer: the person, keel's Supervisor, the flow's run mode, or keel itself (a waived or empty gate). */
export type Who = "you" | "supervisor" | "auto" | "keel";
export const WHO_LABEL: Record<Who, string> = {
  you: "you",
  supervisor: "supervisor",
  auto: "auto (run mode)",
  keel: "keel",
};
export type QA = { n: number; q: string; a: string };

export type EventRow = {
  kind: RowKind;
  group: RowGroup;
  /** the short kind label in front of the row */
  label: string;
  /** the row in one line, plain text */
  text: string;
  /** an agent's answer (Markdown), shown folded */
  md?: string;
  /** a gate's decision */
  decision?: Decision;
  by?: Who;
  /** the gate's name (its step's name, readable) */
  gate?: string;
  /** "Answers to your questions": each question with its answer */
  answers?: QA[];
  /** what else the answer said: the person's note, or the reason */
  note?: string;
  /** a step that starts or finishes: a small divider with the step's readable name */
  divider?: "start" | "end";
  step?: string;
};

/** A step id as words: `interview_gate` → "interview gate", `review__fix` → "review fix". */
export function humanize(id: string | null | undefined): string {
  return str(id)
    .replace(/__fix$/, " fix")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Usage events (an agent's tokens so far, a provider's limits): many a minute, hidden unless asked for. */
export const isUsage = (type: string) =>
  type === "agent.usage" ||
  type === "provider.usage" ||
  type.endsWith(".usage");

/** A decision word as a badge: approved (green), sent back (amber), refused or stopped (red), anything else plain. */
export function decisionOf(
  decision: unknown,
  data: Record<string, unknown> = {},
): Decision {
  const d = str(decision).toLowerCase();
  if (d === "approve" || d === "approved" || d === "allow")
    return {
      tone: "ok",
      label: data.choice
        ? `approved · ${humanize(str(data.choice))}`
        : "approved",
    };
  if (d === "reject" || d === "rejected")
    return { tone: "warn", label: "sent back" };
  if (
    d === "deny" ||
    d === "denied" ||
    d === "refuse" ||
    d === "refused" ||
    d === "stop"
  )
    return { tone: "bad", label: d === "stop" ? "stopped" : "refused" };
  if (d === "explain") return { tone: "run", label: "asked back" };
  if (d === "skip") return { tone: "idle", label: "skipped" };
  return { tone: "idle", label: humanize(d) || "decided" };
}

/** Who answered a gate (gate.decided's `by`, or what its reason says). */
export function whoOf(data: Record<string, unknown>): Who {
  const by = str(data.by).toLowerCase();
  if (by === "supervisor") return "supervisor";
  if (by === "engine" || by === "keel") return "keel";
  const why = str(data.why);
  if (why.includes("auto-approved (mode ")) return "auto";
  if (/^waived:/i.test(why)) return "keel";
  return "you";
}

const PREFIX = /^\s*Answers to your questions:\s*/i;
/** The lines keel adds for the agent after the answers (not something the person said). */
const FOR_AGENT =
  /^(Now (write|finish)|This was the last round|Round \d+ of at most|Ask again only)/i;
const NOTE = /^Also, in the user's words:\s*/i;

/** "Answers to your questions:\n1. Q\n   → A\n2. …\nAlso, in the user's words: …" as a list, the person's note and the
 *  rest. Also reads the one-line form ("1. Q → A · 2. Q → A"). null when the text is not that. */
export function parseAnswers(
  text: string,
): { answers: QA[]; note: string } | null {
  if (!PREFIX.test(text)) return null;
  const body = text.replace(PREFIX, "");
  const answers: QA[] = [];
  const notes: string[] = [];
  const lines = body.includes("\n")
    ? body.split("\n")
    : body.split(/\s+·\s+(?=\d+\.\s)/);
  let cur: QA | null = null;
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    const item = line.match(/^(\d+)\.\s+(.*)$/s);
    if (item) {
      const at = item[2].indexOf("→");
      cur = { n: Number(item[1]), q: (at < 0 ? item[2] : item[2].slice(0, at)).trim(), a: at < 0 ? "" : item[2].slice(at + 1).trim() };
      answers.push(cur);
    } else if (line.startsWith("→") && cur) {
      cur.a = [cur.a, line.replace(/^→\s*/, "")].filter(Boolean).join(" ");
    } else if (NOTE.test(line)) {
      notes.push(line.replace(NOTE, ""));
      cur = null;
    } else if (FOR_AGENT.test(line)) {
      cur = null;
    } else if (cur && !cur.a) {
      cur.q = `${cur.q} ${line}`;
    } else if (cur) {
      cur.a = `${cur.a} ${line}`;
    } else notes.push(line);
  }
  return answers.length ? { answers, note: notes.join("\n") } : null;
}

/** keel 0.18.66 why keel went past a gate without asking, in plain words. The engine logs the gate's `when` and the
 *  values it saw ("not asked, git_question: not given": runtime/compiler_gates.py); the known ones read as a sentence. */
const NOT_ASKED: Record<string, string | ((v: string) => string)> = {
  git_question: "the folder is already a git repository with a first commit",
  rung_question: "every setup rung passed",
  "clarify.questions": "the agent had no question for you",
  "clarify.ask": "nothing was unclear enough to ask about",
  "memory_plan.ask": "the memory plan needed no answer",
  "next_preview.ask": "the next finding needed no choice",
  coverage_error: "the coverage could be measured",
  coverage_no_reports: "the coverage reports are set",
  "hunt.stack_down": "the stack is up",
  "hunt.mode": (v) => `the hunt runs without stops (mode ${v})`,
  contract: "this change does not touch the API contract",
};

/** A gate's reason as plain words: keel's "not asked, <what>: <values>", "waived: …", "no gate here (…)". Anything else
 *  (a person's send-back note) stays exactly as it was written. */
export function plainWhy(why: string): string {
  const text = why.trim();
  const m = /^not asked, ([^:]+): (.*)$/s.exec(text);
  if (m) {
    const [, label, value] = m;
    const known = NOT_ASKED[label];
    if (known) return `Not asked: ${typeof known === "function" ? known(value) : known}.`;
    // a marker is in capitals (WEB), a data path in small letters (hunt.map)
    const what = label === label.toUpperCase() ? `the ${label} marker` : humanize(label.replace(/\./g, " "));
    return `Not asked: ${what} was ${value === "not given" ? "not set" : value}.`;
  }
  if (/^not asked$/i.test(text)) return "Not asked.";
  const waived = /^waived:?\s*\(?(.*?)\)?$/i.exec(text);
  if (waived) return waived[1] ? `Waived: ${waived[1]}.` : "Waived.";
  const none = /^no gate here \((.*)\)$/.exec(text);
  if (none) return `No gate here: ${none[1]}.`;
  return why;
}

/** A gate.decided event's parts: its decision, who, its gate's name, its answers (or its reason). */
export function gateParts(
  ev: EngineEvent,
  nameOf?: (step: string) => string | undefined,
) {
  const d = ev.data ?? {};
  const step = str(ev.step);
  const gate =
    str(d.gate) ||
    (step && nameOf?.(step.replace(/__fix$/, ""))) ||
    humanize(step) ||
    "gate";
  const why = str(d.why).trim();
  const parsed = parseAnswers(why);
  const by = whoOf(d);
  const decision =
    d.clarify && str(d.decision) === "approve"
      ? { tone: "ok" as Tone, label: "answered" }
      : decisionOf(d.decision, d);
  const note = parsed ? parsed.note : by === "auto" ? "" : plainWhy(why);
  return { gate, decision, by, answers: parsed?.answers, note, why };
}

/** One event as a row. `nameOf` gives a step's name in the workflow (the block's name), when the page knows it. */
export function eventRow(
  ev: EngineEvent,
  nameOf?: (step: string) => string | undefined,
): EventRow {
  const d = ev.data ?? {};
  const step = str(ev.step).replace(/__fix$/, "");
  const stepWords = humanize(ev.step) || "step";
  if (isUsage(ev.type)) {
    const tok = Number(d.tokens_in ?? 0) + Number(d.tokens_out ?? 0);
    return {
      kind: "usage",
      group: "usage",
      label: "usage",
      text:
        ev.type === "provider.usage"
          ? `${str(d.provider) || "provider"} usage limits updated`
          : `${str(d.agent) || "agent"} used ${tok.toLocaleString("en")} tokens so far`,
    };
  }
  switch (ev.type) {
    case "step.started":
    case "step.finished": {
      const name = step ? nameOf?.(step) : undefined;
      const status =
        ev.type === "step.finished" && d.status && str(d.status) !== "done"
          ? ` · ${humanize(str(d.status))}`
          : "";
      return {
        kind: "phase",
        group: "phases",
        label: "step",
        divider: ev.type === "step.started" ? "start" : "end",
        step: stepWords,
        text: `${stepWords}${name && name.toLowerCase() !== stepWords.toLowerCase() ? ` (${name})` : ""} ${ev.type === "step.started" ? "started" : "finished"}${status}`,
      };
    }
    case "agent.finished":
    case "helper.finished": {
      const result = typeof d.result === "string" ? d.result.trim() : "";
      return {
        kind: "agent",
        group: "agents",
        label: "agent",
        text: `${str(d.agent) || "agent"} finished · ${str(d.status) || "done"}`,
        md: result || undefined,
      };
    }
    case "agent.started":
    case "code.started":
    case "helper.started":
    case "code.finished":
      return {
        kind: "agent",
        group: "agents",
        label: ev.type.startsWith("code") ? "code" : "agent",
        text: eventLine(ev).text,
      };
    case "gate.waiting":
      return {
        kind: "gate",
        group: "gates",
        label: "gate",
        text: `${str(d.title) || humanize(ev.step) || "a gate"} waits for you`,
      };
    case "gate.decided": {
      const g = gateParts(ev, nameOf);
      return {
        kind: "gate",
        group: "gates",
        label: "gate",
        gate: g.gate,
        decision: g.decision,
        by: g.by,
        answers: g.answers,
        note: g.note || undefined,
        text: `${g.gate} ${g.decision.label}${g.by !== "you" ? ` by ${WHO_LABEL[g.by]}` : ""}`,
      };
    }
    case "helper.permission":
      return {
        kind: "gate",
        group: "gates",
        label: "gate",
        text: eventLine(ev).text,
      };
    case "helper.permission.answered": {
      const decision = decisionOf(
        str(d.decision) === "deny" ? "deny" : "allow",
      );
      return {
        kind: "gate",
        group: "gates",
        label: "gate",
        gate: "KeelBot's command",
        decision,
        note: str(d.why) || undefined,
        text: `KeelBot's command ${decision.label}`,
      };
    }
    case "supervisor.started":
      return {
        kind: "supervisor",
        group: "agents",
        label: "supervisor",
        text: `supervisor looks${d.model ? ` · ${str(d.provider)} ${str(d.model)}` : ""}`,
      };
    case "supervisor.finished":
      return {
        kind: "supervisor",
        group: "agents",
        label: "supervisor",
        text: `supervisor finished · ${str(d.status) || "done"}`,
      };
    case "supervisor.looking":
      return {
        kind: "supervisor",
        group: "gates",
        label: "supervisor",
        text: `supervisor looks at the ${humanize(str(d.kind)) || "question"}`,
      };
    case "supervisor.decided": {
      const decision = decisionOf(d.decision, d);
      return {
        kind: "supervisor",
        group: "gates",
        label: "supervisor",
        gate: humanize(ev.step) || undefined,
        decision,
        by: "supervisor",
        note: str(d.why) || undefined,
        text: `supervisor ${decision.label}${d.confidence ? ` (${str(d.confidence)} confidence)` : ""}`,
      };
    }
    case "supervisor.escalated":
      return {
        kind: "supervisor",
        group: "gates",
        label: "supervisor",
        decision: { tone: "warn", label: "asks you" },
        note: str(d.summary) || undefined,
        text: `supervisor leaves it to you: ${str(d.reason) || str(d.code) || "it is not sure"}`,
      };
    case "supervisor.paused":
      return {
        kind: "supervisor",
        group: "gates",
        label: "supervisor",
        text: "supervisor paused: it answered too often in this flow",
      };
    case "guard.refused":
      return {
        kind: "guard",
        group: "other",
        label: "guard",
        text: eventLine(ev).text,
      };
    case "budget.warn":
    case "budget.stop":
      return {
        kind: "budget",
        group: "other",
        label: "budget",
        text: eventLine(ev).text,
      };
    case "mcp.call":
    case "tui.call":
    case "xal.call":
      return {
        kind: "tool",
        group: "other",
        label: ev.type === "mcp.call" ? "mcp" : "tui",
        text: eventLine(ev).text,
      };
    case "thread.started":
    case "thread.done":
    case "thread.failed":
      return {
        kind: "flow",
        group: "phases",
        label: "flow",
        text: eventLine(ev).text,
      };
    default: {
      const l = eventLine(ev);
      return {
        kind: "event",
        group: "other",
        label: l.k === "event" ? "event" : l.k,
        text: l.text,
      };
    }
  }
}

/** Does the row show under this filter? All shows everything but usage. */
export function inFilter(row: EventRow, f: EventFilter): boolean {
  if (f === "all") return row.group !== "usage";
  return row.group === f;
}

// ------------------------------------------------------------------ decisions

/** The event types a flow's decisions come from (gate answers and the Supervisor's own words). */
export const DECISION_TYPES = [
  "gate.decided",
  "supervisor.decided",
  "supervisor.escalated",
  "supervisor.paused",
];

export type DecisionItem = {
  key: string;
  at: string;
  /** the step id (no __fix) and its readable name */
  step: string;
  stepName: string;
  /** "gate": a gate's answer; "supervisor": what the Supervisor said */
  source: "gate" | "supervisor";
  gate: string;
  decision: Decision;
  by: Who;
  answers?: QA[];
  note?: string;
  ac?: string;
  /** the n-th decision at this step (and criterion) in this flow: 2 when it ran twice */
  round: number;
  /** how many times that step (and criterion) was decided in all */
  rounds: number;
};

const keyOf = (e: EngineEvent) =>
  `${e.type}|${e.step ?? ""}|${e.at}|${str(e.data?.decision)}|${str(e.data?.why).slice(0, 40)}`;

/** Events from two places (the stored ones and this visit's live ones) once each, oldest first. */
export function mergeEvents(
  ...lists: (EngineEvent[] | null | undefined)[]
): EngineEvent[] {
  const seen = new Map<string, EngineEvent>();
  for (const l of lists)
    for (const e of l ?? []) if (!seen.has(keyOf(e))) seen.set(keyOf(e), e);
  return [...seen.values()].sort(
    (a, b) => (Date.parse(a.at) || 0) - (Date.parse(b.at) || 0),
  );
}

/** Every decision of a flow, oldest first, from its gate.decided and supervisor.* events. */
export function decisionsOf(
  events: EngineEvent[],
  nameOf?: (step: string) => string | undefined,
): DecisionItem[] {
  const out: DecisionItem[] = [];
  const seen: Record<string, number> = {};
  for (const ev of mergeEvents(events)) {
    if (!DECISION_TYPES.includes(ev.type)) continue;
    const d = ev.data ?? {};
    const step = str(ev.step).replace(/__fix$/, "");
    const stepName =
      (step && nameOf?.(step)) || humanize(ev.step) || "the flow";
    if (ev.type === "gate.decided") {
      // an unlock is an answer too, but not a round of the gate
      const g = gateParts(ev, nameOf);
      const round =
        str(d.gate) === "unlock"
          ? 0
          : (seen[`${step}|${str(d.ac)}`] =
              (seen[`${step}|${str(d.ac)}`] ?? 0) + 1);
      out.push({
        key: keyOf(ev),
        at: ev.at,
        step,
        stepName,
        source: "gate",
        gate: str(d.gate) === "unlock" ? "allow a file" : g.gate,
        decision: g.decision,
        by: g.by,
        answers: g.answers,
        note: g.note || undefined,
        ac: str(d.ac) || undefined,
        round,
        rounds: 0,
      });
    } else {
      const r = eventRow(ev, nameOf);
      out.push({
        key: keyOf(ev),
        at: ev.at,
        step,
        stepName,
        source: "supervisor",
        gate: "supervisor",
        decision: r.decision ?? { tone: "idle", label: "paused" },
        by: "supervisor",
        note: r.note ?? r.text,
        round: 0,
        rounds: 0,
      });
    }
  }
  for (const it of out)
    if (it.source === "gate" && it.round)
      it.rounds = seen[`${it.step}|${it.ac ?? ""}`] ?? 1;
  return out;
}

/** A gate log line ("gate s2 approve: why", "ac AC-1 reject", "gate x answered the explorer's 3 question(s)") as a
 *  decision without a time or a who: what a flow shows when its events cannot be read. */
export function logDecision(
  line: string,
  nameOf?: (step: string) => string | undefined,
): DecisionItem | null {
  const m = line.match(/^(gate|ac)\s+(\S+)\s+(\w[\w-]*)(.*)$/s);
  if (!m) return null;
  const [, what, id, word, tail] = m;
  const colon = tail.indexOf(": ");
  const why = colon >= 0 ? tail.slice(colon + 2).trim() : "";
  const answered = word === "answered";
  const step = what === "gate" ? id : "";
  return {
    key: line,
    at: "",
    step,
    stepName: what === "ac" ? id : (nameOf?.(id) ?? humanize(id)),
    source: "gate",
    gate: what === "ac" ? `criterion ${id}` : (nameOf?.(id) ?? humanize(id)),
    decision: answered ? { tone: "ok", label: "answered" } : decisionOf(word),
    by: why.includes("auto-approved (mode ") ? "auto" : "you",
    note: answered
      ? line.replace(/^.*?answered\s+/, "answered ")
      : (why && plainWhy(why)) || undefined,
    ac: what === "ac" ? id : undefined,
    round: 0,
    rounds: 0,
  };
}
