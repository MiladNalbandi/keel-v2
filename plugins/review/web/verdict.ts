// keel's check of a review thread says who is right in words that name the role and the person: "Reviewer (ana) is
// right", "Author (you) is right", "Unclear: keel could not decide". One label set for the classic Threads panel, the
// new Comments tab and the text a follow-up question to KeelBot carries, so the views never say it differently.

import type { JudgeItem, ReviewThread, ReviewView, Verdict } from "./reviewApi";

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
