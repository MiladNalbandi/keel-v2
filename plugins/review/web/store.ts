// v0.14.0 one shared copy of every open review (the Review tool window, the file tabs and the popups read the same
// view, AI state and pending comments), the review's cursor and navigation history, and the actions that change it.

import { useSyncExternalStore } from "react";
import { errorParts } from "@keel/web-sdk";
import {
  reviewApi,
  type AiState,
  type Draft,
  type Places,
  type ReviewView,
  type SubmitEvent,
} from "./reviewApi";

export type Side = "RIGHT" | "LEFT";
export type Err = { message: string; hint?: string; details?: string[] };
export type FileView = "diff" | "code";
export type Place = {
  key: string;
  path: string;
  view: FileView;
  line: number | null;
  side: Side;
};
export type Popup =
  | { kind: "places"; title: string; places: Places; key: string }
  | { kind: "actions" }
  | { kind: "submit"; key: string; event?: SubmitEvent }
  | { kind: "merge"; key: string }
  | { kind: "line"; at: Place }
  | { kind: "recent" }
  | null;

type One = {
  view: ReviewView | null;
  error: Err | null;
  ai: AiState | null;
  aiError: Err | null;
  busy: string | null;
  err: Err | null;
  loading: boolean;
};

const reviews = new Map<string, One>();
const ui = {
  /** where the cursor is in the active review tab */
  current: null as Place | null,
  /** the line a new comment is being written on */
  composing: null as Place | null,
  /** a line to scroll to in a tab (n changes every time) */
  focus: null as (Place & { n: number }) | null,
  /** F7 / ⇧F7 in a tab: which change block (n changes every time) */
  change: null as {
    key: string;
    path: string;
    index: number;
    n: number;
  } | null,
  popup: null as Popup,
  /** how many change blocks the active diff tab has (F7 at the last one goes to the next file) */
  changes: 0,
  history: { list: [] as Place[], at: -1 },
  findingIdx: -1,
};
let version = 0;
const subs = new Set<() => void>();
const emit = () => {
  version++;
  subs.forEach((f) => f());
};

/** Re-render when anything in the store changes. */
export function useReviewStore(): number {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => {
        subs.delete(f);
      };
    },
    () => version,
  );
}

const id = (pid: string, key: string) => `${pid}|${key}`;
const blank = (): One => ({
  view: null,
  error: null,
  ai: null,
  aiError: null,
  busy: null,
  err: null,
  loading: false,
});

export function review(pid: string, key: string): One {
  return reviews.get(id(pid, key)) ?? blank();
}

function set(pid: string, key: string, patch: Partial<One>) {
  reviews.set(id(pid, key), { ...review(pid, key), ...patch });
  emit();
}

export const uiState = () => ui;
export function setUi(patch: Partial<typeof ui>) {
  Object.assign(ui, patch);
  emit();
}

/** Tests: forget everything. */
export function resetReviews() {
  reviews.clear();
  Object.assign(ui, {
    current: null,
    composing: null,
    focus: null,
    change: null,
    popup: null,
    history: { list: [], at: -1 },
    findingIdx: -1,
  });
  opener = null;
  emit();
}

// ---- opening files in editor tabs (the Code page registers how)

type Opener = (key: string, path: string, view: FileView, pin: boolean) => void;
let opener: Opener | null = null;
export function setOpener(f: Opener | null) {
  opener = f;
}

/** Opens a file of a review in an editor tab (diff or the whole file) and scrolls to a line; remembered for ⌘[ ⌘]. */
export function openPlace(
  p: Place,
  opts: { pin?: boolean; remember?: boolean } = {},
) {
  opener?.(p.key, p.path, p.view, !!opts.pin);
  ui.focus = { ...p, n: Date.now() };
  if (p.line) ui.current = p;
  if (opts.remember !== false) {
    const list = [...ui.history.list.slice(0, ui.history.at + 1), p].slice(-50);
    ui.history = { list, at: list.length - 1 };
  }
  emit();
}

// ---- loading

const inflight = new Map<string, Promise<void>>();

export function load(pid: string, key: string, refresh = false): Promise<void> {
  const k = id(pid, key) + (refresh ? "|r" : "");
  const running = inflight.get(k);
  if (running) return running;
  set(pid, key, { loading: true });
  const p = reviewApi
    .view(pid, key, refresh)
    .then(
      (view) => set(pid, key, { view, error: null, loading: false }),
      (e) => set(pid, key, { error: errorParts(e), loading: false }),
    )
    .finally(() => inflight.delete(k));
  inflight.set(k, p);
  return p;
}

export function loadAi(pid: string, key: string): Promise<void> {
  return reviewApi.ai(pid, key).then(
    (ai) => set(pid, key, { ai, aiError: null }),
    (e) => set(pid, key, { aiError: errorParts(e) }),
  );
}

/** An action on a review: busy while it runs, its error kept for the person. */
async function act<T>(
  pid: string,
  key: string,
  name: string,
  fn: () => Promise<T>,
): Promise<T | undefined> {
  set(pid, key, { busy: name, err: null });
  try {
    const r = await fn();
    set(pid, key, { busy: null });
    return r;
  } catch (e) {
    set(pid, key, { busy: null, err: errorParts(e) });
    return undefined;
  }
}

const patchView = (
  pid: string,
  key: string,
  f: (v: ReviewView) => ReviewView,
) => {
  const v = review(pid, key).view;
  if (v) set(pid, key, { view: f(v) });
};

// ---- the person's actions

export async function addDraft(
  pid: string,
  key: string,
  body: string,
  at: { path: string; line: number; side: Side } | null,
  findingId?: string,
) {
  const d = await act(pid, key, "draft", () =>
    reviewApi.addDraft(pid, {
      key,
      path: at?.path ?? null,
      line: at?.line ?? null,
      side: at?.side ?? "RIGHT",
      body,
      finding_id: findingId,
    }),
  );
  if (d) {
    patchView(pid, key, (v) => ({ ...v, drafts: [...v.drafts, d] }));
    setUi({ composing: null });
  }
  return d;
}

export async function editDraft(
  pid: string,
  key: string,
  d: Draft,
  body: string,
) {
  const n = await act(pid, key, "draft", () =>
    reviewApi.editDraft(pid, d.id, body),
  );
  if (n)
    patchView(pid, key, (v) => ({
      ...v,
      drafts: v.drafts.map((x) => (x.id === d.id ? n : x)),
    }));
}

export async function deleteDraft(pid: string, key: string, d: Draft) {
  if (await act(pid, key, "draft", () => reviewApi.deleteDraft(pid, d.id)))
    patchView(pid, key, (v) => ({
      ...v,
      drafts: v.drafts.filter((x) => x.id !== d.id),
    }));
}

export async function toggleViewed(pid: string, key: string, path: string) {
  const on = !review(pid, key).view?.viewed.includes(path);
  const r = await act(pid, key, "viewed", () =>
    reviewApi.viewed(pid, key, path, on),
  );
  if (r) patchView(pid, key, (v) => ({ ...v, viewed: r.viewed }));
}

export async function reply(
  pid: string,
  key: string,
  threadId: string,
  body: string,
) {
  const v = await act(pid, key, "reply", () =>
    reviewApi.reply(pid, key, threadId, body),
  );
  if (v) set(pid, key, { view: v });
}

export async function resolve(
  pid: string,
  key: string,
  threadId: string,
  resolved: boolean,
) {
  const v = await act(pid, key, "resolve", () =>
    reviewApi.resolve(pid, key, threadId, resolved),
  );
  if (v) set(pid, key, { view: v });
}

export async function submit(
  pid: string,
  key: string,
  event: SubmitEvent,
  body: string,
) {
  const r = await act(pid, key, "submit", () =>
    reviewApi.submit(pid, key, event, body),
  );
  if (r) set(pid, key, { view: r.view });
  return r;
}

export async function merge(
  pid: string,
  key: string,
  method: string,
  deleteBranch: boolean,
) {
  const r = await act(pid, key, "merge", () =>
    reviewApi.merge(pid, key, method, deleteBranch),
  );
  if (r) set(pid, key, { view: r.view });
  return r;
}

export async function checkout(pid: string, key: string) {
  return act(pid, key, "checkout", () => reviewApi.checkout(pid, key));
}

export async function startAi(
  pid: string,
  key: string,
  kind: "overview" | "findings",
) {
  const s = await act(pid, key, kind, () => reviewApi.aiStart(pid, key, kind));
  if (s) set(pid, key, { ai: s });
}

export async function decide(
  pid: string,
  key: string,
  findingId: string,
  decision: "dismissed" | "commented" | "open",
  why?: string,
) {
  const s = await act(pid, key, "decide", () =>
    reviewApi.decide(pid, key, findingId, decision, why),
  );
  if (s) set(pid, key, { ai: s });
}

export async function lookUp(
  pid: string,
  key: string,
  word: string,
  kind: "declaration" | "usages",
): Promise<string | null> {
  const r = await act(pid, key, kind, () =>
    kind === "declaration"
      ? reviewApi.definition(pid, key, word)
      : reviewApi.usages(pid, key, word),
  );
  if (!r) return null;
  if (!r.places.length)
    return kind === "declaration"
      ? `keel found no declaration of ${word} in ${r.ref}.`
      : `No usage of ${word} in ${r.ref}.`;
  if (kind === "declaration" && r.places.length === 1) {
    const p = r.places[0];
    openPlace(
      {
        key,
        path: p.path,
        view: "code",
        line: p.line,
        side: "RIGHT",
      },
      { pin: false },
    );
    return null;
  }
  setUi({
    popup: {
      kind: "places",
      title:
        kind === "declaration"
          ? `Declarations of ${word}`
          : `Usages of ${word}`,
      places: r,
      key,
    },
  });
  return null;
}
