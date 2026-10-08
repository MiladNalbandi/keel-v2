// v0.15.2 KeelBot's new answers. When an answer finishes while you are not looking at its chat (you are on another
// page, the panel is closed, another chat is open, or the browser tab is hidden), keel counts it as new and plays
// KeelBot's own sound (when it is on). The menu's KeelBot entry, its icon in the folded menu and the Code page's KeelBot
// button show the number; opening the chat clears it. The numbers and the sound switch are kept in this browser
// (localStorage), so a reload or a second tab shows the same.

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { playKeelBot } from "../../notify";
import { useApp } from "../../state";

const UNREAD_KEY = "keel2.keelbot.unread";
const SOUND_KEY = "keel2.keelbot.sound";
const PLAYED_KEY = "keel2.keelbot.played";

/** project → chat → the call ids of its answers not seen yet */
type Unread = Record<string, Record<string, string[]>>;

const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
function subscribe(l: () => void) {
  listeners.add(l);
  return () => void listeners.delete(l);
}
if (typeof window !== "undefined") {
  // another tab changed them
  window.addEventListener("storage", (e) => {
    if (e.key === null || e.key === UNREAD_KEY || e.key === SOUND_KEY) emit();
  });
}

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return memory.get(key) ?? null;
  }
}
/** a private window that refuses localStorage keeps the values for this page only */
const memory = new Map<string, string>();
function store(key: string, value: string) {
  memory.set(key, value);
  try {
    localStorage.setItem(key, value);
  } catch {
    /* kept in memory */
  }
  emit();
}

let raw: string | null = null;
let cached: Unread = {};
/** The stored numbers, the same object until they change (useSyncExternalStore needs that). */
function unread(): Unread {
  const now = stored(UNREAD_KEY);
  if (now !== raw) {
    raw = now;
    try {
      const v = now ? JSON.parse(now) : {};
      cached = v && typeof v === "object" && !Array.isArray(v) ? v : {};
    } catch {
      cached = {};
    }
  }
  return cached;
}

export function addUnread(pid: string, sid: string, call: string) {
  const all = unread();
  const calls = all[pid]?.[sid] ?? [];
  if (calls.includes(call)) return;
  store(UNREAD_KEY, JSON.stringify({ ...all, [pid]: { ...(all[pid] ?? {}), [sid]: [...calls, call].slice(-50) } }));
}

/** The chat is open (or gone): nothing new in it. */
export function markSeen(pid: string, sid: string) {
  const all = unread();
  if (!all[pid]?.[sid]) return;
  const { [sid]: _gone, ...rest } = all[pid];
  store(UNREAD_KEY, JSON.stringify({ ...all, [pid]: rest }));
}

/** This project's new answers: in all, and per chat. */
export function useKeelBotUnread(pid: string | null): { total: number; chats: Record<string, number> } {
  const all = useSyncExternalStore(subscribe, unread, unread);
  return useMemo(() => {
    const chats: Record<string, number> = {};
    let total = 0;
    for (const [sid, calls] of Object.entries((pid && all[pid]) || {})) {
      if (Array.isArray(calls) && calls.length) {
        chats[sid] = calls.length;
        total += calls.length;
      }
    }
    return { total, chats };
  }, [all, pid]);
}

// ---------- the sound switch (on at first) ----------

export const keelbotSoundOn = () => stored(SOUND_KEY) !== "0";
export const setKeelBotSound = (on: boolean) => store(SOUND_KEY, on ? "1" : "0");
export function useKeelBotSound(): [boolean, (on: boolean) => void] {
  return [useSyncExternalStore(subscribe, keelbotSoundOn, keelbotSoundOn), setKeelBotSound];
}

/** Two tabs get the same event: only the first one plays the sound. */
function firstToPlay(call: string): boolean {
  if (stored(PLAYED_KEY) === call) return false;
  store(PLAYED_KEY, call);
  return true;
}

// ---------- who looks at which chat ----------

const viewing = new Map<number, { pid: string; sid: string }>();
let nextView = 0;

/** Someone looks at this chat now: a panel shows it and the browser tab is visible. */
export const looking = (pid: string, sid: string) =>
  !(typeof document !== "undefined" && document.hidden) && [...viewing.values()].some((v) => v.pid === pid && v.sid === sid);

/**
 * The panel shows this chat (`shown`: its messages are on screen, not covered by the chat list). While it does and the
 * tab is visible, its new answers are seen at once; when the tab comes back, the ones that came meanwhile are cleared.
 */
export function useSeen(pid: string, sid: string | null, shown: boolean) {
  const all = useSyncExternalStore(subscribe, unread, unread);
  const [visible, setVisible] = useState(() => typeof document === "undefined" || !document.hidden);
  useEffect(() => {
    const on = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  useEffect(() => {
    if (!shown || !sid) return;
    const id = ++nextView;
    viewing.set(id, { pid, sid });
    return () => void viewing.delete(id);
  }, [pid, sid, shown]);
  const has = !!(sid && all[pid]?.[sid]?.length);
  useEffect(() => {
    if (shown && sid && visible && has) markSeen(pid, sid);
  }, [pid, sid, shown, visible, has]);
}

/**
 * Watches the live events (in the app shell, so on every page): a KeelBot answer that finished while nobody looked at
 * its chat becomes a new answer, with KeelBot's sound. An answer the person stopped is not news.
 */
export function useKeelBotWatch() {
  const { recent, nset } = useApp();
  const nsetRef = useRef(nset);
  nsetRef.current = nset;
  // the answers already in the list when the shell starts are not news
  const handled = useRef<Set<string> | null>(null);
  if (!handled.current) handled.current = new Set(recent.filter((e) => e.type === "helper.finished" && e.call_id).map((e) => e.call_id!));
  useEffect(() => {
    for (const ev of recent) {
      if (ev.type !== "helper.finished" || !ev.call_id || !ev.thread_id || !ev.project_id || handled.current!.has(ev.call_id)) continue;
      handled.current!.add(ev.call_id);
      if ((ev.data as { status?: string } | undefined)?.status === "stopped") continue;
      if (looking(ev.project_id, ev.thread_id)) continue;
      addUnread(ev.project_id, ev.thread_id, ev.call_id);
      const s = nsetRef.current;
      if (keelbotSoundOn() && !s.quiet && firstToPlay(ev.call_id)) playKeelBot(s.volume);
    }
  }, [recent]);
}

/** The number of new KeelBot answers in this project, as a menu count (nav), a folded-menu badge (rail) or on the Code
 *  page's KeelBot button (act); nothing when there is none. */
export function KeelBotCount({ kind }: { kind: "nav" | "rail" | "act" }) {
  const { pid } = useApp();
  const { total } = useKeelBotUnread(pid);
  if (!total) return null;
  const label = `${total} new KeelBot ${total === 1 ? "answer" : "answers"}`;
  if (kind === "rail") return <span className="rail-count kb-new" title={label} aria-hidden="true">{total > 9 ? "9+" : total}</span>;
  if (kind === "act") return <span className="act-n kb-new" title={label} data-testid="keelbot-unread-act">{total > 99 ? "99+" : total}</span>;
  return <span className="count kb-new" title={label} aria-label={label} data-testid="keelbot-unread">{total}</span>;
}
