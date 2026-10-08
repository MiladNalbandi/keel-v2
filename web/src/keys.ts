// v0.15.4 every key of keel in one place. The handlers read these constants (the shell, the launcher, Code's IDE keys
// in review/keymap.ts), and the key cheat sheet (⌘/ or ?) lists them, so the sheet cannot drift from what the keys do.
// A combo is the keymap.ts form: "meta+k" is ⌘K on a Mac and Ctrl+K elsewhere.

import {
  ACTIONS,
  IDE_KEYS,
  isMac,
  keyLabel,
  matches,
  type Keymap,
} from "./components/review/keymap";
import { FOCUS_KEYS } from "./pages/repo/model";
import type { ScreenId } from "./routes";

/** Keys that work on every page. */
export const KEYS = {
  /** the key cheat sheet; ? opens it too while you are not typing */
  sheet: "meta+/",
  sheetPlain: "?",
  launcher: "meta+k",
  /** show or hide the menu (the shell checks ⌘ or Ctrl, like before) */
  menuToggle: "meta+\\",
  /** jump into the menu. ⌃⌘M off a Mac would be Ctrl+M twice, so there it is Ctrl+Alt+M */
  menuJump: ["f6", isMac ? "ctrl+meta+m" : "ctrl+alt+m"],
  focus: FOCUS_KEYS,
} as const;

/** Page n (1–9) of the menu: ⌃1–⌃9 on a Mac. Off a Mac Ctrl+1–9 is the browser's own tab switch (and Code's ⌘1), so Ctrl+Alt+1–9. */
export const menuPageKey = (n: number) =>
  isMac ? `ctrl+${n}` : `ctrl+alt+${n}`;

/** The page number (1–9) of a menu page key, or 0. */
export function menuPageOf(e: KeyboardEvent): number {
  const m = /^Digit([1-9])$/.exec(e.code ?? "");
  return m && matches(e, menuPageKey(Number(m[1]))) ? Number(m[1]) : 0;
}

/** The launcher's own keys, while it is open. */
export const LAUNCHER = {
  open: KEYS.launcher,
  down: "ctrl+n",
  up: "ctrl+p",
  /** on a result: its actions */
  actions: "meta+k",
  ask: "meta+enter",
  copy: "meta+shift+c",
} as const;

export const matchesAny = (e: KeyboardEvent, combos: readonly string[]) =>
  combos.some((c) => matches(e, c));

/** Is the key going into a text field (an input, a textarea, a select or contenteditable)? */
export function isTyping(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return (
    el.tagName === "INPUT" ||
    el.tagName === "TEXTAREA" ||
    el.tagName === "SELECT" ||
    el.isContentEditable
  );
}

/** A dialog that holds the keys (the launcher, quick open, a review popup) is open: page keys stay quiet. */
export const modalOpen = () => !!document.querySelector('[aria-modal="true"]');

// ---------- the cheat sheet's list ----------

export type KeyRow = {
  label: string;
  /** combos, shown with keyLabel */
  keys?: readonly string[];
  /** keys written out as they are ("⇧⇧", "Esc Esc", "@") */
  raw?: readonly string[];
  note?: string;
};
export type KeyArea = {
  id: string;
  title: string;
  /** the pages it belongs to; none: every page */
  pages?: ScreenId[];
  rows: (k: Keymap) => KeyRow[];
};

const sh = isMac ? "⇧" : "Shift";
const mod = isMac ? "⌘" : "Ctrl";

export const AREAS: KeyArea[] = [
  {
    id: "everywhere",
    title: "Everywhere",
    rows: () => [
      {
        label: "This list of keys",
        keys: [KEYS.sheet],
        raw: [KEYS.sheetPlain],
        note: "? only when you are not typing",
      },
      { label: "Search and actions (the launcher)", keys: [KEYS.launcher] },
      { label: "Show or hide the menu", keys: [KEYS.menuToggle] },
      { label: "Jump into the menu", keys: KEYS.menuJump },
      {
        label: "Open page 1 to 9 of the menu",
        raw: [`${keyLabel(menuPageKey(1))} … ${keyLabel(menuPageKey(9))}`],
        note: isMac
          ? "if macOS uses ⌃1… to switch Desktops, macOS wins"
          : `instead of ${keyLabel("ctrl+1")}…, which the browser keeps for its tabs`,
      },
      { label: "Close a popup or a dialog", keys: ["escape"] },
    ],
  },
  {
    id: "menu",
    title: "The menu (after you jump into it)",
    rows: () => [
      { label: "Next or previous page", raw: ["↓ ↑"] },
      { label: "First or last page", keys: ["home", "end"] },
      { label: "Open the page", keys: ["enter"] },
      { label: "Back to the page", keys: ["escape"] },
    ],
  },
  {
    id: "launcher",
    title: "The launcher",
    rows: () => [
      { label: "Open or close it", keys: [LAUNCHER.open] },
      {
        label: "Next or previous result",
        raw: ["↓ ↑"],
        keys: [LAUNCHER.down, LAUNCHER.up],
      },
      {
        label: "Next or previous group",
        keys: ["alt+arrowdown", "alt+arrowup"],
      },
      { label: "Next or previous scope", keys: ["tab", "shift+tab"] },
      { label: "Run the result", keys: ["enter"] },
      {
        label: "Run result 1 to 9",
        raw: [`${keyLabel("meta+1")} … ${keyLabel("meta+9")}`],
      },
      { label: "All actions of the result", keys: [LAUNCHER.actions] },
      { label: "Ask KeelBot about it", keys: [LAUNCHER.ask] },
      { label: "Copy it", keys: [LAUNCHER.copy] },
      { label: "Ask KeelBot a question", raw: ["?"], note: "type ? first" },
      { label: "Clear, step back, then close", keys: ["escape"] },
    ],
  },
  {
    id: "code",
    title: "Code",
    pages: ["repo"],
    rows: (k) => [
      { label: "Find a file by name (quick open)", keys: ["meta+p"] },
      ...IDE_KEYS.filter((a) => a.keys[k].length).map((a) => ({
        label: a.label,
        keys: a.keys[k],
        note: a.note,
      })),
      {
        label: "Search everywhere (the launcher)",
        raw: [isMac ? "⇧⇧" : "Shift Shift"],
      },
      { label: "Search every file", keys: ["shift+meta+f"] },
      { label: "Explorer, with its filter", keys: ["shift+meta+e"] },
      { label: "Source control (Git)", keys: ["shift+meta+g"] },
      { label: "Find in the open file", keys: ["meta+f"] },
      { label: "Go to a line", keys: ["meta+g"] },
      { label: "Word wrap", keys: ["alt+z"] },
      {
        label: "KeelBot: ask about the selected lines (again: close it)",
        keys: ["meta+i"],
      },
      { label: "Focus mode: only the code", keys: [KEYS.focus] },
      { label: "Leave Focus mode", raw: ["Esc Esc"] },
      { label: "In the row of tabs: next or previous tab", raw: ["→ ←"] },
      {
        label: "In the row of tabs: close or keep the tab",
        keys: ["delete", "enter"],
      },
    ],
  },
  {
    id: "review",
    title: "Code Review (in a review's file tab)",
    pages: ["repo"],
    rows: (k) => ACTIONS.map((a) => ({ label: a.label, keys: a.keys[k] })),
  },
  {
    id: "keelbot",
    title: "KeelBot",
    pages: ["repo", "helper"],
    rows: () => [
      { label: "Send", keys: ["enter"] },
      { label: "A new line", keys: ["shift+enter"] },
      { label: "A command", raw: ["/"] },
      { label: "Name a file, a symbol or an AC", raw: ["@"] },
      { label: "Pick a suggestion", raw: ["↓ ↑"] },
      { label: "Take the suggestion", keys: ["enter", "tab"] },
      { label: "Hide the suggestions", keys: ["escape"] },
    ],
  },
  {
    id: "flow",
    title: "Flow",
    pages: ["flow"],
    rows: () => [
      { label: "Next or previous step", raw: ["↓ ↑"] },
      { label: "First or last step", keys: ["home", "end"] },
      { label: "Open the step", raw: [keyLabel("enter"), "Space"] },
      { label: "Close the big view or the Stop dialog", keys: ["escape"] },
    ],
  },
  {
    id: "workflows",
    title: "Workflows",
    pages: ["workflows"],
    rows: () => [
      { label: "Next or previous step", raw: ["↓ ↑"] },
      {
        label: "Move the step up or down",
        keys: ["alt+arrowup", "alt+arrowdown"],
      },
      { label: "Remove the step", keys: ["delete", "backspace"] },
    ],
  },
  {
    id: "diagram",
    title: "Map and Graph diagrams",
    pages: ["map", "graph"],
    rows: () => [
      { label: "Pan: hold Space and drag", raw: ["Space"] },
      { label: "Zoom", raw: [`${mod} + wheel`] },
      { label: "Pan sideways", raw: [`${sh} + wheel`] },
    ],
  },
];

/** The words a row is found by: its label, its note and its keys as shown. */
export const rowText = (r: KeyRow) =>
  [r.label, r.note ?? "", ...(r.keys ?? []).map(keyLabel), ...(r.raw ?? [])]
    .join(" ")
    .toLowerCase();
