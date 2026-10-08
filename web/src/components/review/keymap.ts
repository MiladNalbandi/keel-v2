// v0.14.0 the review's keys: IntelliJ's macOS keymap (⌘B, ⌥F7, F7, ⌘[ …) or VS Code's, one list for the key handler
// and for Find action (⇧⌘A), so a key and its name never disagree. On Windows and Linux ⌘ is Ctrl.

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

export type ReviewAction =
  | "nextChange"
  | "prevChange"
  | "nextFile"
  | "prevFile"
  | "nextFinding"
  | "prevFinding"
  | "declaration"
  | "usages"
  | "jumpToSource"
  | "back"
  | "forward"
  | "explain"
  | "lineActions"
  | "toggleViewed"
  | "submit"
  | "findAction"
  | "recent"
  | "closePopup";

/** combo: modifiers + key, e.g. "meta+alt+arrowright", "alt+f7", "f7", "meta+[" ("meta" is ⌘, or Ctrl off a Mac). */
export const ACTIONS: {
  id: ReviewAction;
  label: string;
  keys: Record<Keymap, string[]>;
}[] = [
  {
    id: "nextChange",
    label: "Next change",
    keys: { intellij: ["f7"], vscode: ["alt+f5"] },
  },
  {
    id: "prevChange",
    label: "Previous change",
    keys: { intellij: ["shift+f7"], vscode: ["shift+alt+f5"] },
  },
  {
    id: "nextFile",
    label: "Next file",
    keys: {
      intellij: ["meta+alt+arrowright"],
      vscode: ["meta+alt+arrowright"],
    },
  },
  {
    id: "prevFile",
    label: "Previous file",
    keys: { intellij: ["meta+alt+arrowleft"], vscode: ["meta+alt+arrowleft"] },
  },
  {
    id: "nextFinding",
    label: "Next keel finding",
    keys: { intellij: ["f2"], vscode: ["f8"] },
  },
  {
    id: "prevFinding",
    label: "Previous keel finding",
    keys: { intellij: ["shift+f2"], vscode: ["shift+f8"] },
  },
  {
    id: "declaration",
    label: "Go to declaration (or ⌘-click a name)",
    keys: { intellij: ["meta+b"], vscode: ["f12"] },
  },
  {
    id: "usages",
    label: "Find usages",
    keys: { intellij: ["alt+f7"], vscode: ["shift+f12"] },
  },
  {
    id: "jumpToSource",
    label: "Jump to source (the whole file)",
    keys: { intellij: ["meta+arrowdown"], vscode: ["alt+enter"] },
  },
  {
    id: "back",
    label: "Back",
    keys: { intellij: ["meta+["], vscode: ["ctrl+-"] },
  },
  {
    id: "forward",
    label: "Forward",
    keys: { intellij: ["meta+]"], vscode: ["ctrl+shift+-"] },
  },
  {
    id: "explain",
    label: "Quick documentation: keel explains it",
    keys: { intellij: ["f1", "ctrl+j"], vscode: ["meta+k meta+i"] },
  },
  {
    id: "lineActions",
    label: "Actions on this line (comment, ask keel, copy link)",
    keys: { intellij: ["alt+enter"], vscode: ["meta+."] },
  },
  {
    id: "toggleViewed",
    label: "Mark the file viewed",
    keys: { intellij: ["ctrl+alt+v"], vscode: ["ctrl+alt+v"] },
  },
  {
    id: "submit",
    label: "Submit the review",
    keys: { intellij: ["meta+enter"], vscode: ["meta+enter"] },
  },
  {
    id: "findAction",
    label: "Find action",
    keys: { intellij: ["shift+meta+a"], vscode: ["shift+meta+p"] },
  },
  {
    id: "recent",
    label: "Recent places",
    keys: { intellij: ["meta+e"], vscode: ["meta+e"] },
  },
  {
    id: "closePopup",
    label: "Close the popup",
    keys: { intellij: ["escape"], vscode: ["escape"] },
  },
];

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

export function keysFor(id: ReviewAction, k: Keymap = readKeymap()): string {
  return (
    ACTIONS.find((a) => a.id === id)
      ?.keys[k].map(keyLabel)
      .join(" or ") ?? ""
  );
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

/** The review action for a key event, in this keymap (two-chord VS Code keys are left out here). */
export function actionFor(
  e: KeyboardEvent,
  k: Keymap = readKeymap(),
): ReviewAction | null {
  for (const a of ACTIONS)
    for (const combo of a.keys[k])
      if (!combo.includes(" ") && matches(e, combo)) return a.id;
  return null;
}

/** IntelliJ's keys for the Code page itself: ⌘1 files, ⌘9 Git, ⇧⌘9 Review, ⇧⌘O go to file, ⌘L go to line (⇧⇧ is the launcher). */
export type IdeAction =
  "explorer" | "scm" | "review" | "quickOpen" | "gotoLine";
export function ideActionFor(
  e: KeyboardEvent,
  k: Keymap = readKeymap(),
): IdeAction | null {
  if (k !== "intellij") return null;
  if (matches(e, "meta+1")) return "explorer";
  if (matches(e, "shift+meta+9")) return "review";
  if (matches(e, "meta+9")) return "scm";
  if (matches(e, "shift+meta+o")) return "quickOpen";
  if (matches(e, "meta+l")) return "gotoLine";
  return null;
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

/** The name under the cursor or the selection (for ⌘B and ⌥F7): letters, digits, _ and $. */
export function wordAt(text: string, offset: number): string | null {
  const isWord = (c: string) => /[A-Za-z0-9_$]/.test(c);
  let a = offset;
  let b = offset;
  while (a > 0 && isWord(text[a - 1])) a--;
  while (b < text.length && isWord(text[b])) b++;
  const w = text.slice(a, b);
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(w) ? w : null;
}
