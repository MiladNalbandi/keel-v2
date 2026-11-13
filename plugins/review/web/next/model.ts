// The new Review view (0.18.57, the redesign's Work › Review): the small rules it shows its data with. Pure functions
// over the same api types as the classic view (../reviewApi), so they are easy to test and to share.

import type {
  AiRun,
  AiState,
  Finding,
  JudgeItem,
  JudgeResult,
  PrSummary,
  ReviewThread,
  ReviewView,
  Verdict,
} from "../reviewApi";
import { decisionText } from "../reviewApi";
import { waitsForYou } from "../Threads";
import { sidesOf, verdictLabel } from "../verdict";

export type Tone = "ok" | "run" | "warn" | "bad" | "idle" | "accent";

// ---------------------------------------------------------------- the switch

/** Which Review view this browser shows: the classic one (the default for now) or the new one. */
export type ReviewMode = "classic" | "next";
export const MODE_KEY = "keel2.review.view";
export const isMode = (v: unknown): v is ReviewMode =>
  v === "classic" || v === "next";

// ---------------------------------------------------------------- the list

export type ListFilter = "all" | "review" | "mine" | "assigned";
export const LIST_FILTERS: [ListFilter, string][] = [
  ["all", "All"],
  ["review", "To review"],
  ["mine", "Mine"],
  ["assigned", "Assigned"],
];
export const isListFilter = (v: unknown): v is ListFilter =>
  LIST_FILTERS.some(([f]) => f === v);

/** A pull request matches the search: its number, title, author or branch has the words. */
export function matchesSearch(p: PrSummary, q: string): boolean {
  const words = q.toLowerCase().trim().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay =
    `#${p.number} ${p.number} ${p.title} ${p.author} ${p.branch}`.toLowerCase();
  return words.every((w) => hay.includes(w));
}

/** The pill of a pull request in the list. [waiting] is the number of threads that wait for you on your own pull
 *  request, when its review is loaded already (null: not known). [sent] is what you did in this browser tab. */
export function prPill(
  p: PrSummary,
  waiting: number | null,
  sent?: string,
): [string, Tone] {
  if (sent) return [sent, "ok"];
  if (p.draft) return ["draft", "idle"];
  if (p.mine && waiting)
    return [
      `${waiting} comment${waiting === 1 ? "" : "s"} wait${waiting === 1 ? "s" : ""} for you`,
      "warn",
    ];
  // 0.18.60 your own: who asked for changes, else who approved it
  const d = p.mine ? decisionText(p) : null;
  if (d?.changes) return [d.changes, "warn"];
  if (d?.approved) return [d.approved, "ok"];
  if (p.review_requested) return ["your review is asked", "warn"];
  if (p.assigned) return ["assigned to you", "run"];
  if (p.mine) return ["yours", "idle"];
  return ["open", "ok"];
}

/** The pull request that needs you next: the first one that asks for your review, is not a draft and was not reviewed
 *  in this browser tab yet. */
export function nextUp(prs: PrSummary[], done: string[]): PrSummary | null {
  return (
    prs.find(
      (p) => p.review_requested && !p.draft && !done.includes(`pr:${p.number}`),
    ) ?? null
  );
}

// ---------------------------------------------------------------- one pull request

/** The state pill of a review: merged, closed, draft, approved, changes asked, or open. */
export function stateOf(v: ReviewView): [string, Tone] {
  const s = (v.state ?? "open").toLowerCase();
  if (s === "merged") return ["merged", "accent"];
  if (s === "closed") return ["closed", "bad"];
  if (v.draft) return ["draft", "idle"];
  if (v.changes_requested.length) return ["changes asked", "warn"];
  if (v.approved.length) return ["approved", "ok"];
  return ["open", "ok"];
}

const GREEN = ["success", "skipped", "neutral"];
const RED = ["failure", "failed", "error", "cancelled", "timed_out"];

/** The checks in a few words: "checks pass", "1 check failed", "checks running", or null when there are none. */
export function checksOf(v: ReviewView): [string, Tone] | null {
  if (!v.checks.length) return null;
  const failed = v.checks.filter((c) => RED.includes(c.state)).length;
  if (failed)
    return [`${failed} check${failed === 1 ? "" : "s"} failed`, "bad"];
  if (v.checks.every((c) => GREEN.includes(c.state)))
    return ["checks pass", "ok"];
  return ["checks running", "run"];
}

/** The threads of a filter: waiting for your answer, open, or resolved. */
export type ThreadFilter = "wait" | "open" | "resolved";
export function threadsOf(v: ReviewView, f: ThreadFilter): ReviewThread[] {
  if (f === "wait") return v.threads.filter((t) => waitsForYou(t, v.me));
  if (f === "open") return v.threads.filter((t) => !t.resolved);
  return v.threads.filter((t) => t.resolved);
}

/** Where a thread is, short: "refunds.py · line 13" (the file's name only). */
export function whereOf(path: string | null, line: number | null): string {
  if (!path) return "The pull request";
  const name = path.slice(path.lastIndexOf("/") + 1);
  return line != null ? `${name} · line ${line}` : name;
}

// ---------------------------------------------------------------- keel's check of a thread

/** The tone of a verdict here (its words come from ../verdict, the same as the classic view's). */
export const VERDICT_TONE: Record<Verdict, Tone> = {
  reviewer_right: "ok",
  author_right: "bad",
  partly: "warn",
  unclear: "warn",
};

/** One verdict for the whole thread, from keel's points: all the reviewer's, all the author's, a mix (partly), or
 *  unclear. */
export function verdictOf(items: JudgeItem[]): Verdict | null {
  if (!items.length) return null;
  const right = items.filter((i) => i.verdict === "reviewer_right").length;
  const wrong = items.filter((i) => i.verdict === "author_right").length;
  if (right === items.length) return "reviewer_right";
  if (wrong === items.length) return "author_right";
  if (
    items.some((i) => i.verdict === "partly") ||
    (right > 0 && wrong > 0) ||
    right > 0
  )
    return "partly";
  return "unclear";
}

/** keel's finished check of a thread, or null (none, running, failed, or a flow it started). */
export function doneCheck(
  run: AiRun<JudgeResult> | null | undefined,
): JudgeResult | null {
  return run?.status === "done" && run.result?.stage === "done"
    ? run.result
    : null;
}

/** keel says the code should change: a point where the reviewer is right, or partly. */
export const needsChange = (r: JudgeResult | null): boolean =>
  !!r?.items?.some(
    (i) => i.verdict === "reviewer_right" || i.verdict === "partly",
  );

/** The pill of a thread: resolved, a fix running, keel checking, keel's verdict, waiting for you, or not checked. */
export function threadPill(
  t: ReviewThread,
  v: ReviewView,
  ai: AiState | null,
  checking: boolean,
): [string, Tone] {
  if (t.resolved) return ["resolved", "idle"];
  if (t.pending) return ["pending", "run"];
  if (ai?.addressed?.[t.id]) return ["fix running", "run"];
  if (checking) return ["keel is checking", "run"];
  const sum = verdictOf(doneCheck(ai?.judges?.[t.id])?.items ?? []);
  if (sum) return [verdictLabel(sum, sidesOf(v, t)), VERDICT_TONE[sum]];
  if (waitsForYou(t, v.me)) return ["waits for you", "warn"];
  if (t.comments.length && same(t.comments[t.comments.length - 1].author, v.me))
    return ["you replied", "idle"];
  return ["not checked", "idle"];
}

export const same = (
  a: string | null | undefined,
  b: string | null | undefined,
) => !!a && !!b && a.toLowerCase() === b.toLowerCase();

// ---------------------------------------------------------------- keel's problems

export type ProblemGroup = {
  id: string;
  label: string;
  tone: Tone;
  items: Finding[];
  actions: boolean;
};

/** keel's checked findings in the groups the person decides on: Blocking, Should fix, Small things, and the ones that
 *  were there before this change (never blocking, no actions). */
export function problemGroups(ai: AiState | null): ProblemGroup[] {
  const r = ai?.findings?.status === "done" ? ai.findings.result : null;
  if (!r) return [];
  const found = r.findings ?? [];
  const before = [
    ...found.filter((f) => f.pre_existing),
    ...(r.pre_existing ?? []),
  ];
  const now = found.filter((f) => !f.pre_existing);
  const groups: ProblemGroup[] = [
    {
      id: "blocking",
      label: "Blocking",
      tone: "bad",
      items: now.filter((f) => f.severity === "blocking"),
      actions: true,
    },
    {
      id: "should",
      label: "Should fix",
      tone: "warn",
      items: now.filter((f) => f.severity === "should_fix"),
      actions: true,
    },
    {
      id: "nits",
      label: "Small things",
      tone: "idle",
      items: [...now.filter((f) => f.severity === "nit"), ...(r.nits ?? [])],
      actions: true,
    },
    {
      id: "before",
      label: "Already there before this change (never blocking)",
      tone: "idle",
      items: before,
      actions: false,
    },
  ];
  return groups.filter((g) => g.items.length);
}

/** Findings still open (not dismissed, not added as a comment) that block. */
export function openProblems(ai: AiState | null): {
  open: number;
  blocking: number;
} {
  const groups = problemGroups(ai).filter((g) => g.actions);
  const open = groups
    .flatMap((g) => g.items)
    .filter(
      (f) => !ai?.decisions[f.id] || ai.decisions[f.id].decision === "open",
    );
  return {
    open: open.length,
    blocking: open.filter((f) => f.severity === "blocking").length,
  };
}

// ---------------------------------------------------------------- the checklist

export const CHECKLIST: [string, string][] = [
  [
    "Broad view (5 min)",
    "Read the description, the ticket and keel's overview. Should this change exist at all? Are the checks green?",
  ],
  [
    "The ticket (5–10 min)",
    "Check what the ticket asks. Read the test of one thing marked done.",
  ],
  [
    "Tests first, then the files to open first",
    "Send design comments early, before you read the rest.",
  ],
  [
    "keel's problems",
    "Add each as a comment, or dismiss it with a reason. You stay responsible.",
  ],
  [
    "The rest, plus one security pass",
    "Auth, input, data exposure, secrets. Look outside the diff where needed.",
  ],
  ["Write comments", "Say if it blocks or not, and why. Praise good parts."],
  [
    "Decide",
    "Approve when it makes the code better, even if not perfect. Request changes only for blocking items.",
  ],
];

/** A review key's short label: "#42" (GitHub), "!42" (GitLab), or the branch. */
export function keyLabel(key: string, gitlab = false): string {
  if (key.startsWith("pr:")) return `${gitlab ? "!" : "#"}${key.slice(3)}`;
  return key.startsWith("branch:") ? key.slice(7) : key;
}
