// Small values a page keeps in the browser so it looks the same when you come back: the panel that was open, a
// filter, a draft. "session" (sessionStorage) is for view state: it lives as long as the browser tab. "local"
// (localStorage) is for unsent text: it also survives a reload. Every read and write is guarded — a private window,
// blocked storage, a full quota or a value that no longer makes sense gives the fallback, never an error.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

export type StoreKind = "session" | "local";

function storeOf(kind: StoreKind): Storage | undefined {
  try {
    return kind === "local"
      ? globalThis.localStorage
      : globalThis.sessionStorage;
  } catch {
    return undefined;
  }
}

/** The value stored under [key], or [fallback] when there is none, it is not JSON, or [valid] says no. */
export function readStored<T>(
  key: string,
  fallback: T,
  kind: StoreKind = "session",
  valid?: (v: unknown) => boolean,
): T {
  try {
    const raw = storeOf(kind)?.getItem(key);
    if (raw == null) return fallback;
    const v = JSON.parse(raw) as unknown;
    if (valid && !valid(v)) return fallback;
    return v as T;
  } catch {
    return fallback;
  }
}

/** Store [value] under [key] as JSON (quietly does nothing when storage is blocked or full). */
export function writeStored(
  key: string,
  value: unknown,
  kind: StoreKind = "session",
): void {
  try {
    storeOf(kind)?.setItem(key, JSON.stringify(value));
  } catch {
    /* private window or full */
  }
}

/** Forget [key]. */
export function dropStored(key: string, kind: StoreKind = "session"): void {
  try {
    storeOf(kind)?.removeItem(key);
  } catch {
    /* private window */
  }
}

/** 0.18.64 Forget [key], and every mounted useStored of it shows its fallback at once (a sent answer's draft goes from
 *  the form that still shows it). */
export function forgetStored(key: string, kind: StoreKind = "session"): void {
  dropStored(key, kind);
  tell(kind, key, undefined);
}

/** The keys in a store that start with [prefix]. */
export function storedKeys(
  prefix: string,
  kind: StoreKind = "session",
): string[] {
  const out: string[] = [];
  try {
    const s = storeOf(kind);
    if (!s) return out;
    for (let i = 0; i < s.length; i++) {
      const k = s.key(i);
      if (k && k.startsWith(prefix)) out.push(k);
    }
  } catch {
    /* private window */
  }
  return out;
}

export type StoredOptions<T> = {
  /** "session" (default) for view state, "local" for drafts */
  store?: StoreKind;
  /** false: a stored value that is not good any more (a filter that does not exist) gives the fallback */
  valid?: (v: unknown) => boolean;
  /** this value is not stored but removed (an empty draft keeps no key) */
  drop?: (v: T) => boolean;
};

// every mounted useStored, by store and key: a write in one shows at once in the others (the Review panel and the
// code view show the same draft); another browser tab's localStorage write comes in through the "storage" event
type Listener = (value: unknown) => void;
const listeners = new Map<string, Set<Listener>>();
const slot = (kind: StoreKind, key: string) => `${kind}:${key}`;
function listen(kind: StoreKind, key: string, fn: Listener): () => void {
  const k = slot(kind, key);
  let set = listeners.get(k);
  if (!set) listeners.set(k, (set = new Set()));
  set.add(fn);
  return () => {
    set!.delete(fn);
    if (!set!.size) listeners.delete(k);
  };
}
function tell(kind: StoreKind, key: string, value: unknown, except?: Listener) {
  listeners.get(slot(kind, key))?.forEach((fn) => fn !== except && fn(value));
}

/** useState that is kept in the browser under [key]. A new key reads its own value (the state follows the key), and
 *  every component that uses the same key shows the same value (a write in one updates the others). */
export function useStored<T>(
  key: string,
  fallback: T,
  opts: StoredOptions<T> = {},
): [T, Dispatch<SetStateAction<T>>] {
  const kind = opts.store ?? "session";
  const validRef = useRef(opts.valid);
  validRef.current = opts.valid;
  const dropRef = useRef(opts.drop);
  dropRef.current = opts.drop;
  const fallbackRef = useRef(fallback);
  fallbackRef.current = fallback;
  const [state, setState] = useState<{ key: string; value: T }>(() => ({
    key,
    value: readStored(key, fallback, kind, opts.valid),
  }));
  // the key changed (another project, another review): show that key's value
  const current =
    state.key === key
      ? state.value
      : readStored(key, fallback, kind, opts.valid);
  // the newest value, also between a write and the next render (two writes in one click build on each other)
  const latest = useRef({ key, value: current });
  if (latest.current.key !== key) latest.current = { key, value: current };
  useEffect(() => {
    if (state.key !== key)
      setState({
        key,
        value: readStored(key, fallbackRef.current, kind, validRef.current),
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const self = useRef<Listener | null>(null);
  const onOther = useRef<Listener>(() => undefined);
  onOther.current = (value: unknown) => {
    const v =
      value === undefined
        ? readStored(key, fallbackRef.current, kind, validRef.current)
        : (value as T);
    latest.current = { key, value: v };
    setState({ key, value: v });
  };
  useEffect(() => {
    const fn: Listener = (v) => onOther.current(v);
    self.current = fn;
    const stop = listen(kind, key, fn);
    // another browser tab wrote it (localStorage only fires there)
    const onStorage = (e: StorageEvent) => {
      if (e.key === key && kind === "local") fn(undefined);
    };
    window.addEventListener("storage", onStorage);
    return () => {
      stop();
      window.removeEventListener("storage", onStorage);
    };
  }, [kind, key]);
  const set = useCallback<Dispatch<SetStateAction<T>>>(
    (next) => {
      const base =
        latest.current.key === key
          ? latest.current.value
          : readStored(key, fallbackRef.current, kind, validRef.current);
      const value =
        typeof next === "function" ? (next as (p: T) => T)(base) : next;
      latest.current = { key, value };
      if (dropRef.current?.(value)) dropStored(key, kind);
      else writeStored(key, value, kind);
      setState({ key, value });
      tell(kind, key, value, self.current ?? undefined);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, kind],
  );
  return [current, set];
}

/** Keep at most [max] keys that start with [prefix] in localStorage (the oldest by their saved time go first). Drafts
 *  store { text, at }; a key whose value has no time counts as the oldest. */
export function capStored(
  prefix: string,
  max: number,
  kind: StoreKind = "local",
): void {
  const keys = storedKeys(prefix, kind);
  if (keys.length <= max) return;
  const at = (k: string) => {
    const v = readStored<{ at?: unknown } | null>(k, null, kind);
    return v && typeof v === "object" && typeof v.at === "number" ? v.at : 0;
  };
  keys
    .map((k) => [k, at(k)] as const)
    .sort((a, b) => a[1] - b[1])
    .slice(0, keys.length - max)
    .forEach(([k]) => dropStored(k, kind));
}

/** Text you typed but did not send, kept in localStorage under [key] (an empty text keeps no key). At most [cap]
 *  drafts share [capPrefix]: writing a new one drops the oldest. */
export function useDraft(
  key: string,
  opts: { capPrefix?: string; cap?: number } = {},
): [string, (text: string) => void, () => void] {
  const [d, setD] = useStored<{ text: string; at: number } | null>(key, null, {
    store: "local",
    valid: (v) =>
      !!v &&
      typeof v === "object" &&
      typeof (v as { text?: unknown }).text === "string",
    drop: (v) => !v || !v.text,
  });
  const set = useCallback(
    (text: string) => {
      setD(text ? { text, at: Date.now() } : null);
      if (text && opts.capPrefix) capStored(opts.capPrefix, opts.cap ?? 50);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [setD, opts.capPrefix, opts.cap],
  );
  const clear = useCallback(() => setD(null), [setD]);
  return [d?.text ?? "", set, clear];
}

/** Keep an element's scroll position under [key] (sessionStorage): restore it once [ready] is true and the element is
 *  there, save it while the person scrolls. Returns a callback ref to put on the scrolling element. */
export function useStoredScroll<E extends HTMLElement>(key: string, ready = true): (el: E | null) => void {
  const [el, setEl] = useState<E | null>(null);
  useEffect(() => {
    if (!el || !ready) return;
    const top = readStored<number>(key, 0, "session", (v) => typeof v === "number" && Number.isFinite(v) && v >= 0);
    if (top > 0) el.scrollTop = top;
    let t = 0;
    let pending = false;
    let last = el.scrollTop;
    const on = () => {
      last = el.scrollTop;
      pending = true;
      window.clearTimeout(t);
      t = window.setTimeout(() => {
        pending = false;
        writeStored(key, Math.round(last));
      }, 120);
    };
    el.addEventListener("scroll", on, { passive: true });
    return () => {
      window.clearTimeout(t);
      el.removeEventListener("scroll", on);
      // the element may already be out of the page here (its scrollTop reads 0): store the last one seen
      if (pending) writeStored(key, Math.round(last));
    };
  }, [el, key, ready]);
  return setEl;
}
