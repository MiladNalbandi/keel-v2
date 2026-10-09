// v0.15.2 KeelBot's chat state that outlives the panel: each chat's draft (the text not sent yet, with its @ mentions),
// where each chat was scrolled to, the mode of a chat not started yet, which folders are folded, and whether the chat
// list is open. Going to another page, or closing the panel, and coming back finds them as they were. Drafts and folded
// folders are kept in this browser (localStorage); the rest in this tab (sessionStorage).
// Also the chat list's pure helpers: search, chats grouped by folder, and "how long ago".

import type { HelperMention, HelperMode, HelperSession } from "@keel/web-sdk";
import type { HelperFolder } from "./keelbotApi";

export type Draft = { text: string; mentions: HelperMention[] };

/** The key of a chat not started yet (it has no id until its first message). */
const NEW = "new";
const MAX_DRAFTS = 40;
const draftsKey = (pid: string) => `keel2.helper.${pid}.drafts`;
const foldedKey = (pid: string) => `keel2.helper.${pid}.folded`;
const tabKey = (pid: string) => `keel2.helper.${pid}.tab`;

function readJson<T>(key: string, fallback: T, where: "local" | "session" = "local"): T {
  try {
    const raw = (where === "local" ? localStorage : sessionStorage).getItem(key);
    const v = raw ? (JSON.parse(raw) as T) : null;
    return v && typeof v === "object" ? v : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown, where: "local" | "session" = "local") {
  try {
    (where === "local" ? localStorage : sessionStorage).setItem(key, JSON.stringify(value));
  } catch {
    /* private window: it lives as long as the page */
  }
}

/** What this tab remembers per project: the new chat's mode, the list open or not, where each chat was scrolled to. */
type TabState = { mode?: HelperMode; list?: boolean; scroll?: Record<string, Scroll> };
const readTab = (pid: string) => readJson<TabState>(tabKey(pid), {}, "session");
const writeTab = (pid: string, patch: Partial<TabState>) => writeJson(tabKey(pid), { ...readTab(pid), ...patch }, "session");

/** The draft of a chat (null: the new chat). */
export function getDraft(pid: string, sid: string | null): Draft {
  const d = readJson<Record<string, Draft>>(draftsKey(pid), {})[sid ?? NEW];
  return d && typeof d.text === "string" ? { text: d.text, mentions: Array.isArray(d.mentions) ? d.mentions : [] } : { text: "", mentions: [] };
}

/** Keep a chat's draft; an empty one is forgotten. Only the newest drafts are kept. */
export function setDraft(pid: string, sid: string | null, d: Draft) {
  const all = readJson<Record<string, Draft>>(draftsKey(pid), {});
  const key = sid ?? NEW;
  delete all[key];
  if (d.text.trim()) all[key] = { text: d.text.slice(0, 20_000), mentions: d.mentions };
  const keys = Object.keys(all);
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_DRAFTS))) delete all[k];
  writeJson(draftsKey(pid), all);
}

export const dropDraft = (pid: string, sid: string | null) => setDraft(pid, sid, { text: "", mentions: [] });

/** Where a chat was scrolled to: the top, and whether it was at the newest message (then it follows new ones). */
export type Scroll = { top: number; bottom: boolean };
export const getScroll = (pid: string, sid: string | null): Scroll | null => readTab(pid).scroll?.[sid ?? NEW] ?? null;
export function saveScroll(pid: string, sid: string | null, s: Scroll) {
  const all = { ...(readTab(pid).scroll ?? {}) };
  delete all[sid ?? NEW];
  all[sid ?? NEW] = { top: Math.round(s.top), bottom: s.bottom };
  const keys = Object.keys(all);
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_DRAFTS))) delete all[k];
  writeTab(pid, { scroll: all });
}

/** The mode picked for a chat not started yet (Ask, Fix or Side). */
export const getNewMode = (pid: string): HelperMode => {
  const m = readTab(pid).mode;
  return m === "fix" || m === "side" ? m : "ask";
};
export const saveNewMode = (pid: string, mode: HelperMode) => writeTab(pid, { mode });

/** Whether the panel's chat list is open (the KeelBot page always shows it on a wide screen). */
export const getListOpen = (pid: string) => readTab(pid).list === true;
export const saveListOpen = (pid: string, list: boolean) => writeTab(pid, { list });

/** Folded folders in the chat list ("none" is the group of chats with no folder). */
export const getFolded = (pid: string): string[] => {
  const v = readJson<unknown>(foldedKey(pid), []);
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
};
export const saveFolded = (pid: string, ids: string[]) => writeJson(foldedKey(pid), ids);

export type ChatGroup = { folder: HelperFolder | null; chats: HelperSession[] };

/**
 * The chats by folder: the folders by name, then the chats with no folder (or a folder that is gone), each group in the
 * list's order (newest first). A search keeps the chats whose title, or whose folder's name, has every word, and drops
 * the groups it leaves empty; without a search an empty folder stays, so a chat can be moved into it.
 */
export function groupChats(chats: HelperSession[], folders: HelperFolder[], query = ""): ChatGroup[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const hit = (s: HelperSession, f: HelperFolder | null) =>
    words.every((w) => s.title.toLowerCase().includes(w) || (f ? f.name.toLowerCase().includes(w) : false));
  const known = new Set(folders.map((f) => f.id));
  const sorted = [...folders].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
  const groups: ChatGroup[] = sorted.map((f) => ({ folder: f, chats: chats.filter((s) => s.folder === f.id && hit(s, f)) }));
  groups.push({ folder: null, chats: chats.filter((s) => !(s.folder && known.has(s.folder)) && hit(s, null)) });
  return words.length ? groups.filter((g) => g.chats.length) : groups;
}

/** "now", "5 min", "3 h", "2 d", else the date: how long ago a chat was last used. */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return "";
  const min = Math.floor((now - t) / 60_000);
  if (min < 1) return "now";
  if (min < 60) return `${min} min`;
  if (min < 24 * 60) return `${Math.floor(min / 60)} h`;
  if (min < 7 * 24 * 60) return `${Math.floor(min / (24 * 60))} d`;
  return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
