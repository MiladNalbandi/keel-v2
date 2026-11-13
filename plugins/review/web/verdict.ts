// keel's check of a review thread says who is right in words that name the role and the person: "Reviewer (ana) is
// right", "Author (you) is right", "Unclear: keel could not decide". One label set for the classic Threads panel, the
// new Comments tab and the text a follow-up question to KeelBot carries, so the views never say it differently. The
// words for the same point in two threads live here too.

import type {
  AiRun,
  FixResult,
  JudgeItem,
  ReviewThread,
  ReviewView,
  SameAs,
  Verdict,
} from "./reviewApi";

/** The two sides of a thread by name ("you" when it is you; null when keel does not know the name). */
export type Sides = { reviewer: string | null; author: string | null };

const same = (a: string | null | undefined, b: string | null | undefined) =>
  !!a && !!b && a.toLowerCase() === b.toLowerCase();

/** A thread's reviewer (the first one in it who did not write the change) and the change's author. */
export function sidesOf(
  view: Pick<ReviewView, "me" | "author">,
  t: Pick<ReviewThread, "comments">,
): Sides {
  const name = (n: string | null | undefined) =>
    !n ? null : same(n, view.me) ? "you" : n;
  const reviewer =
    t.comments.find((c) => !same(c.author, view.author))?.author ??
    t.comments[0]?.author;
  return { reviewer: name(reviewer), author: name(view.author) };
}

const who = (role: string, name: string | null) =>
  name ? `${role} (${name})` : role;

/** keel's verdict on one point, or on a whole thread, in words. */
export function verdictLabel(v: Verdict, s: Sides): string {
  if (v === "reviewer_right") return `${who("Reviewer", s.reviewer)} is right`;
  if (v === "author_right") return `${who("Author", s.author)} is right`;
  if (v === "partly") return `${who("Reviewer", s.reviewer)} is partly right`;
  return "Unclear: keel could not decide";
}

/** keel's points as text (a follow-up question to KeelBot): one line each, in the same words. */
export const checkLines = (items: JudgeItem[], s: Sides): string =>
  items.map((i) => `- ${verdictLabel(i.verdict, s)}: ${i.claim} — ${i.why}`).join("\n");

/** The link to another thread that makes the same point: "Same point as the thread on line 22 (bo)". */
export function samePointLabel(o: SameAs, view: Pick<ReviewView, "me">): string {
  const name = !o.author ? null : same(o.author, view.me) ? "you" : o.author;
  return `Same point as the thread on ${o.line != null ? `line ${o.line}` : o.path ?? "the pull request"}${name ? ` (${name})` : ""}`;
}

/** The button that puts keel's reply into every thread of the point: "Use as my reply on both" (or "on all 3"). */
export const useOnAllLabel = (others: number) =>
  `Use as my reply on ${others === 1 ? "both" : `all ${others + 1}`}`;

/** keel's check of the author's "fixed", in words: "Fixed in abc1234" (then Resolve) or "Still open: …". Null when
 *  there is none to show: no check, not done, the head moved on since (a new one comes), or the thread is resolved. */
export function fixNote(
  run: AiRun<FixResult> | null | undefined,
  t: Pick<ReviewThread, "resolved">,
): { fixed: boolean; text: string } | null {
  const r = run?.result;
  if (!run || run.stale || t.resolved || run.status !== "done" || r?.stage !== "done")
    return null;
  return r.fixed
    ? { fixed: true, text: `Fixed in ${r.sha ?? run.head_sha.slice(0, 7)}` }
    : {
        fixed: false,
        text: `Still open: ${r.still_open || r.why || "keel does not see the fix in the new code"}`,
      };
}
