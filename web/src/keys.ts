// keel's keys (core): the keymap a person picked (IntelliJ's macOS keys or VS Code's), a chord's name ("⌥⌘→"), and
// whether a key event is a chord. The shell, the launcher and the parts (Code, Code Review) all use these, so a key and
// its name never disagree. On Windows and Linux ⌘ is Ctrl.
// v0.15.4 every key of keel in one place. The handlers read these constants (the shell, the launcher), and the key cheat
// sheet (⌘/ or ?) lists them, so the sheet cannot drift from what the keys do. The parts list their own keys in slot
// keys.area (Code, Code Review, KeelBot), from the same tables their handlers read. A combo is "meta+k": ⌘K on a Mac
// and Ctrl+K elsewhere.

export type Keymap = "intellij" | "vscode";
const KEY = "keel2.keymap";

export const isMac =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

export function readKeymap(): Keymap {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "intellij" || v === "vscode") return v;
  } catch {
    /* private window */
  }
  return "intellij";
}

export function saveKeymap(k: Keymap) {
  try {
    localStorage.setItem(KEY, k);
  } catch {
    /* private window */
  }
  window.dispatchEvent(new CustomEvent("keel:keymap", { detail: k }));
}

const NAMES: Record<string, string> = {
  meta: isMac ? "⌘" : "Ctrl+",
  ctrl: isMac ? "⌃" : "Ctrl+",
  alt: isMac ? "⌥" : "Alt+",
  shift: isMac ? "⇧" : "Shift+",
  arrowright: "→",
  arrowleft: "←",
  arrowdown: "↓",
  arrowup: "↑",
  enter: "↩",
  escape: "Esc",
  tab: "Tab",
  home: "Home",
  end: "End",
  delete: "Delete",
  backspace: "⌫",
  "[": "[",
  "]": "]",
};

/** "meta+alt+arrowright" → "⌥⌘→" (Mac order: ⌃ ⌥ ⇧ ⌘). */
export function keyLabel(combo: string): string {
  return combo
    .split(" ")
    .map((chord) => {
      const parts = chord.split("+");
      const key = parts.pop()!;
      const order = isMac ? ["ctrl", "alt", "shift", "meta"] : ["meta", "ctrl", "alt", "shift"];
      const mods = order
        .filter((m) => parts.includes(m))
        .map((m) => NAMES[m])
        .join("");
      return mods + (NAMES[key] ?? key.toUpperCase());
    })
    .join(" ");
}

/** Does this key event match one chord ("meta+b")? On a Mac "meta" is ⌘, elsewhere Ctrl. */
export function matches(e: KeyboardEvent, chord: string): boolean {
  const parts = chord.toLowerCase().split("+");
  const key = parts.pop()!;
  const want = { meta: parts.includes("meta"), ctrl: parts.includes("ctrl"), alt: parts.includes("alt"), shift: parts.includes("shift") };
  // off a Mac, ⌘ and ⌃ are both Ctrl
  const needCmd = isMac ? want.meta : want.meta || want.ctrl;
  if ((isMac ? e.metaKey : e.ctrlKey) !== needCmd) return false;
  if (isMac && e.ctrlKey !== want.ctrl) return false;
  if (e.altKey !== want.alt || e.shiftKey !== want.shift) return false;
  // ⌥ changes the character on a Mac (⌥V = √): compare the physical key for letters and digits
  const code = e.code.toLowerCase();
  if (/^[a-z]$/.test(key)) return code === `key${key}`;
  if (/^[0-9]$/.test(key)) return code === `digit${key}`;
  if (key === "[") return code === "bracketleft";
  if (key === "]") return code === "bracketright";
  if (key === "-") return code === "minus";
  if (key === ".") return code === "period";
  return e.key.toLowerCase() === key;
}

/** Double Shift (Search everywhere): two Shift presses within 350 ms with no other key between. */
export function doubleShift(): (e: KeyboardEvent) => boolean {
  let last = 0;
  return (e: KeyboardEvent) => {
    if (e.key !== "Shift" || e.repeat) {
      if (e.key !== "Shift") last = 0;
      return false;
    }
    const now = Date.now();
    const hit = now - last < 350;
    last = hit ? 0 : now;
    return hit;
  };
}

// ---------- v0.15.4 keel's own keys ----------

/** Focus mode (the Code page: only the code): ⇧⌘\ (⌘\ hides the menu, ⇧⌘\ everything but the IDE), and the event that
 *  toggles it (detail false: leave it). The menu key leaves Focus mode first, so it is keel's, not only the Code page's. */
export const FOCUS_KEYS = "shift+meta+\\";
export const FOCUS_EVENT = "keel:focus";

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
  pages?: string[];
  rows: (k: Keymap) => KeyRow[];
  /** its place in the sheet: keel's own areas are 10–30 and 70–90, the parts' (slot keys.area) go between */
  order?: number;
};

const sh = isMac ? "⇧" : "Shift";
const mod = isMac ? "⌘" : "Ctrl";

/** keel's own areas. The parts add theirs through slot keys.area: Code (40), Code Review (50), KeelBot (60). */
export const AREAS: KeyArea[] = [
  {
    id: "everywhere",
    title: "Everywhere",
    order: 10,
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
    order: 20,
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
    order: 30,
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
    id: "flow",
    title: "Flow",
    pages: ["flow"],
    order: 70,
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
    order: 80,
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
    order: 90,
    rows: () => [
      { label: "Pan: hold Space and drag", raw: ["Space"] },
      { label: "Zoom", raw: [`${mod} + wheel`] },
      { label: "Pan sideways", raw: [`${sh} + wheel`] },
    ],
  },
];

/** keel's areas and the parts' (slot keys.area), by their order. */
export const allAreas = (parts: readonly KeyArea[]): KeyArea[] =>
  [...AREAS, ...parts].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));

/** The words a row is found by: its label, its note and its keys as shown. */
export const rowText = (r: KeyRow) =>
  [r.label, r.note ?? "", ...(r.keys ?? []).map(keyLabel), ...(r.raw ?? [])]
    .join(" ")
    .toLowerCase();
