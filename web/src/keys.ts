// keel's keys (core): the keymap a person picked (IntelliJ's macOS keys or VS Code's), a chord's name ("⌥⌘→"), and
// whether a key event is a chord. The shell, the launcher and the parts (Code, Code Review) all use these, so a key and
// its name never disagree. On Windows and Linux ⌘ is Ctrl.

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
