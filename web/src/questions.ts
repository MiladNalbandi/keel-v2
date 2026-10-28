// keel 0.18.13 "one way to ask": the questions a flow (the explorer's clarify loop, init's three questions) or KeelBot
// (```keel-questions```) asks, and the named choices of a gate, read the same way in the dashboard, KeelBot's panel and
// keel2. No React here: keel2 imports this file too (Dockerfile, stage cli).
import type { ChoiceInfo, ClarifyQuestion } from "./api";

export type ClarifyAnswers = Record<string, string | string[]>;

/** The first line of the message that carries the answers to KeelBot's questions (the chat reads it back: C2). */
export const ANSWERS_PREFIX = "Answers to your questions:";

/** The options with the recommended ones first (the order among each group stays). */
export function byRecommended<T extends { recommended?: boolean }>(
  options: T[],
): T[] {
  return [
    ...options.filter((o) => o.recommended),
    ...options.filter((o) => !o.recommended),
  ];
}

/** What a question's options start as before the person touches it: a `multiple` question ticks every recommended
 *  option; a single question picks nothing (unanswered = the recommended one, written down as an assumption). */
export function defaultPick(q: ClarifyQuestion): string[] {
  return q.multiple
    ? q.options.filter((o) => o.recommended).map((o) => o.label)
    : [];
}

/** keel 0.18.22 the picked option of a single question when it takes a reason (its `why`), else undefined: the typed
 *  words are then that option's reason, not an answer of their own. */
export function reasonOption(
  q: ClarifyQuestion,
  picked: string | string[] | undefined,
): string | undefined {
  if (q.multiple || picked === undefined) return undefined;
  const label = Array.isArray(picked) ? picked[0] : picked;
  const o = q.options.find((x) => x.label === label);
  return o && (o.why === "required" || o.why === "optional") ? o.label : undefined;
}

/** The answers to send: a typed answer wins over a clicked option (but a clicked option that takes a reason sends
 *  "<label>: <typed words>") (a `multiple` question: the typed words are added to
 *  the ticked ones, and an untouched one sends its recommended options, the ones it shows ticked); an unanswered
 *  question is left out (the engine then uses the recommended option and says so). */
export function answersOf(
  questions: ClarifyQuestion[],
  picked: ClarifyAnswers,
  typed: ClarifyAnswers,
): ClarifyAnswers {
  const out: ClarifyAnswers = {};
  for (const q of questions) {
    const t = String(typed[q.id] ?? "").trim();
    const p = q.id in picked ? picked[q.id] : undefined;
    if (q.multiple) {
      const ticked =
        p === undefined ? defaultPick(q) : Array.isArray(p) ? p : p ? [p] : [];
      const list = [...ticked, ...(t && q.own_words !== false ? [t] : [])];
      if (list.length) out[q.id] = list;
    } else if (t) {
      const r = reasonOption(q, p);
      out[q.id] = r ? `${r}: ${t}` : t;
    } else if (p) out[q.id] = Array.isArray(p) ? p[0] : p;
  }
  return out;
}

/** B9 where the form keeps the reason of one ticked option of a `multiple` question (in `typed`, next to its own words). */
export const reasonKey = (questionId: string, label: string) => `${questionId}::${label}`;

/** The ticked options of a `multiple` question that take a reason (a skip menu's steps). */
function reasonPicks(q: ClarifyQuestion, picked: ClarifyAnswers): string[] {
  if (!q.multiple) return [];
  const p = q.id in picked ? picked[q.id] : undefined;
  const ticked = p === undefined ? defaultPick(q) : Array.isArray(p) ? p : p ? [p] : [];
  return ticked.filter((label) => {
    const why = q.options.find((o) => o.label === label)?.why;
    return why === "required" || why === "optional";
  });
}

/** B9 the reasons typed for ticked options, by option label (the resume's payload.reasons). */
export function reasonsOf(questions: ClarifyQuestion[], picked: ClarifyAnswers, typed: ClarifyAnswers): Record<string, string> {
  const out: Record<string, string> = {};
  for (const q of questions) {
    for (const label of reasonPicks(q, picked)) {
      const t = String(typed[reasonKey(q.id, label)] ?? "").trim();
      if (t) out[label] = t;
    }
  }
  return out;
}

/** B9 why the answers cannot be sent yet: a ticked option that needs a reason has none ("" = they can). */
export function reasonsMissing(questions: ClarifyQuestion[], picked: ClarifyAnswers, typed: ClarifyAnswers): string {
  for (const q of questions) {
    for (const label of reasonPicks(q, picked)) {
      const o = q.options.find((x) => x.label === label);
      if (o?.why === "required" && !String(typed[reasonKey(q.id, label)] ?? "").trim()) return `Write why for “${label}” first: it needs a reason.`;
    }
  }
  return "";
}

/** The questions' part of a resume's payload: the answers, and the reasons of ticked options when there are some. */
export function questionsPayload(questions: ClarifyQuestion[], picked: ClarifyAnswers, typed: ClarifyAnswers): Record<string, unknown> {
  const reasons = reasonsOf(questions, picked, typed);
  return { answers: answersOf(questions, picked, typed), ...(Object.keys(reasons).length ? { reasons } : {}) };
}

const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");

/** Questions as keel's form reads them (the explorer's format, engine runtime/clarify.py): at most 4, 2 to 4 options
 *  each (a plain string is a label), the recommended one marked (else the first), `multiple` when several may be
 *  picked. `raw` is a ```keel-questions``` block's text, or what it holds (a list, or {questions: [...]}). */
export function parseQuestions(raw: unknown): ClarifyQuestion[] {
  let v = raw;
  if (typeof raw === "string") {
    try {
      v = JSON.parse(raw);
    } catch {
      return [];
    }
  }
  const list = Array.isArray(v)
    ? v
    : v &&
        typeof v === "object" &&
        Array.isArray((v as { questions?: unknown }).questions)
      ? (v as { questions: unknown[] }).questions
      : [];
  const out: ClarifyQuestion[] = [];
  list.forEach((q, i) => {
    if (out.length >= 4 || !q || typeof q !== "object") return;
    const x = q as Record<string, unknown>;
    const question = str(x.question);
    const options = (Array.isArray(x.options) ? x.options : [])
      .slice(0, 4)
      .map((o) =>
        typeof o === "string" ? { label: o } : (o as Record<string, unknown>),
      )
      .filter((o) => str(o?.label))
      .map((o) => ({
        label: str(o.label),
        description: str(o.description) || undefined,
        recommended: o.recommended === true,
        ...(o.mode === "edit" || o.mode === "ask" ? { mode: o.mode as "edit" | "ask" } : {}),
      }));
    if (!question || !options.length) return;
    if (!options.some((o) => o.recommended)) options[0].recommended = true;
    out.push({
      id: str(x.id) || `q${i + 1}`,
      question,
      why: str(x.why) || undefined,
      multiple: x.multiple === true,
      options,
    });
  });
  return out;
}

/** keel 0.18.63 the KeelBot chat mode the picked options ask for (an option with `"mode": "edit"`, KeelBot's "Switch to
 *  Edit"), or null. Picking it switches the chat before the answer goes, so the answer already runs in that mode. */
export function pickedMode(questions: ClarifyQuestion[], answers: ClarifyAnswers): "edit" | "ask" | null {
  for (const q of questions) {
    const a = answers[q.id];
    for (const label of Array.isArray(a) ? a : a ? [a] : []) {
      const m = q.options.find((o) => o.label === label)?.mode;
      if (m) return m;
    }
  }
  return null;
}

/** The answers as the next chat message to KeelBot (it starts with ANSWERS_PREFIX). */
export function answersMessage(
  questions: ClarifyQuestion[],
  answers: ClarifyAnswers,
): string {
  return [
    ANSWERS_PREFIX,
    ...questions.map((q, i) => {
      const a = answers[q.id];
      const said = Array.isArray(a) ? a.join("; ") : a;
      return `${i + 1}. ${q.question}\n   → ${said || "(not answered: use the recommended one)"}`;
    }),
  ].join("\n");
}

/** A message answersMessage wrote, read back: each question and what was sent; null for any other message. */
export function readAnswersMessage(
  text: string,
): { question: string; answer: string }[] | null {
  const t = (text ?? "").replace(/\r\n?/g, "\n").trim();
  if (!t.startsWith(ANSWERS_PREFIX)) return null;
  const out: { question: string; answer: string }[] = [];
  for (const line of t.slice(ANSWERS_PREFIX.length).split("\n")) {
    const q = /^\s*\d+\.\s+(.*)$/.exec(line);
    const a = /^\s*→\s?(.*)$/.exec(line);
    if (q) out.push({ question: q[1].trim(), answer: "" });
    else if (a && out.length) out[out.length - 1].answer = a[1].trim();
  }
  return out;
}

// ------------------------------------------------------------------ a gate's named choices

/** A waiting question's choices as rows to show: the engine's choice_info when it sent one (same order as `choices`),
 *  else (an older engine) the raw names, the first one the default, a `reject` that needs a reason. */
export function choiceRows(
  choices: string[] | null | undefined,
  info?: ChoiceInfo[] | null,
): ChoiceInfo[] {
  if (info?.length) return info;
  return (choices ?? []).map((name, n) => ({
    name,
    label: name,
    description: "",
    why: name === "reject" ? "required" : "none",
    default: n === 0,
  }));
}

/** Why an answer with this choice cannot be sent yet ("" = it can). */
export function choiceProblem(c: ChoiceInfo | undefined, why: string): string {
  if (!c) return "Choose one first.";
  return c.why === "required" && !why.trim()
    ? `Write why first: “${c.label}” needs a reason.`
    : "";
}
