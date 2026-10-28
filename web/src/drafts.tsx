// keel 0.18.64 an answer to a waiting question that is not sent yet: the picked options, the typed words, a gate's
// reason, a picked choice. Each field is kept in localStorage under the project, the flow (or the KeelBot chat, the
// task) and the question, as the person types or clicks; the form shows it again when it comes back (another project,
// another page, a reload) with a quiet "draft kept". Sending the answer forgets it; a new question of the same flow
// forgets the old one's; a draft older than 14 days goes. Storage that throws (a private window) only means nothing
// is kept: the form works as before.

import { useCallback, useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import { capStored, dropStored, forgetStored, readStored, storedKeys, useStored } from "./storage";

export const DRAFT_PREFIX = "keel2.answer.";
/** a draft this old is not shown and goes at the next clean-up */
export const DRAFT_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
/** at most this many fields are kept in all (the oldest go first) */
export const DRAFT_MAX = 200;
/** a field larger than this (as JSON) is not kept (it stays in the form until the page goes) */
export const DRAFT_MAX_CHARS = 20_000;

/** Where a draft belongs: the project, the flow (or another thing that asks: a KeelBot chat, a task), the question. */
export type DraftPlace = { project: string; thread: string; question: string };

/** The waiting question's own id, else its step and criterion (an older engine sends no id). */
export function questionKey(w: { id?: string | null; step?: string | null; ac?: string | null }): string {
  return w.id || `${w.step ?? ""}|${w.ac ?? ""}`;
}

const part = (s: string) => encodeURIComponent(s);
const threadPrefix = (project: string, thread: string) => `${DRAFT_PREFIX}${part(project)}/${part(thread)}/`;
const placePrefix = (p: DraftPlace) => `${threadPrefix(p.project, p.thread)}${part(p.question)}/`;
/** The localStorage key of one field of a draft. */
export const draftKey = (p: DraftPlace, field: string) => `${placePrefix(p)}${part(field)}`;

type Kept<T> = { v: T; at: number };

function fresh(v: unknown, now = Date.now()): boolean {
  if (!v || typeof v !== "object" || !("v" in v)) return false;
  const at = (v as { at?: unknown }).at;
  return typeof at === "number" && now - at < DRAFT_MAX_AGE_MS;
}

/** An empty answer keeps no key: "", {}, [], null, or an object whose values are all empty. */
export function blank(v: unknown): boolean {
  if (v == null || v === "") return true;
  if (typeof v === "string") return !v.trim();
  if (Array.isArray(v)) return !v.length;
  if (typeof v === "object") return Object.values(v).every(blank);
  return false;
}

/** A picked answer is a draft once it has a key: a pick-any question with nothing ticked is an answer too. */
export const noKeys = (v: object) => !Object.keys(v).length;

/** Drop drafts older than 14 days and, beyond DRAFT_MAX, the oldest. */
export function pruneDrafts(now = Date.now()): void {
  for (const k of storedKeys(DRAFT_PREFIX, "local")) {
    if (!fresh(readStored<unknown>(k, null, "local"), now)) dropStored(k, "local");
  }
  capStored(DRAFT_PREFIX, DRAFT_MAX, "local");
}

/** Forget every field of this question's draft (the answer was sent); the forms that show it are emptied too. */
export function clearDraft(p: DraftPlace | null): void {
  if (!p) return;
  for (const k of storedKeys(placePrefix(p), "local")) forgetStored(k, "local");
}

/** Forget the drafts of this flow's other questions (it asks something new, or [keep] is null: it waits for nothing). */
export function dropOtherDrafts(project: string, thread: string, keep: string | null): void {
  const mine = keep === null ? null : placePrefix({ project, thread, question: keep });
  for (const k of storedKeys(threadPrefix(project, thread), "local")) {
    if (!mine || !k.startsWith(mine)) forgetStored(k, "local");
  }
}

let pruned = 0;

/** useState for one field of an unsent answer, kept under [place] (null: not kept, plain state). The third value is
 *  true while the field shows a draft that was there when the form came (the "draft kept" hint). */
export function useAnswerDraft<T>(
  place: DraftPlace | null,
  field: string,
  empty: T,
  isEmpty: (v: T) => boolean = blank,
): [T, Dispatch<SetStateAction<T>>, boolean] {
  // not kept: a key of this form's own (never written), so two such forms do not share their state
  const own = useRef(`${DRAFT_PREFIX}-/${Math.random().toString(36).slice(2)}/`);
  const key = place ? draftKey(place, field) : `${own.current}${part(field)}`;
  const emptyRef = useRef(empty);
  emptyRef.current = empty;
  const isEmptyRef = useRef(isEmpty);
  isEmptyRef.current = isEmpty;
  const [kept, setKept] = useStored<Kept<T> | null>(key, null, {
    store: "local",
    valid: (v) => !!place && fresh(v),
    drop: (k) => !place || !k || isEmptyRef.current(k.v) || JSON.stringify(k).length > DRAFT_MAX_CHARS,
  });
  // what the key held when this form first showed it: the hint is for a draft that came back, not for one being typed
  const first = useRef<{ key: string; had: boolean } | null>(null);
  if (!first.current || first.current.key !== key) first.current = { key, had: !!kept && !isEmpty(kept.v) };
  useEffect(() => {
    // the clean-up runs now and then, not on every form
    if (Date.now() - pruned < 60_000) return;
    pruned = Date.now();
    pruneDrafts();
  }, []);
  const set = useCallback<Dispatch<SetStateAction<T>>>(
    (next) =>
      setKept((prev) => {
        const base = prev ? prev.v : emptyRef.current;
        const v = typeof next === "function" ? (next as (p: T) => T)(base) : next;
        return { v, at: Date.now() };
      }),
    [setKept],
  );
  const value = kept ? kept.v : empty;
  return [value, set, first.current.had && !!kept && !isEmpty(kept.v)];
}

/** The quiet line a form shows while a kept draft is in it. */
export function DraftKept({ shown }: { shown: boolean }) {
  if (!shown) return null;
  return (
    <span className="hint draft-kept" role="status" data-testid="draft-kept">
      Draft kept: not sent yet.
    </span>
  );
}
