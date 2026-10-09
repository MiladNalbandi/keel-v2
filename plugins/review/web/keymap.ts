// v0.14.0 the review's keys: IntelliJ's macOS keymap (⌘B, ⌥F7, F7, ⌘[ …) or VS Code's, one list for the key handler
// and for Find action (⇧⌘A), so a key and its name never disagree. On Windows and Linux ⌘ is Ctrl. The keymap itself,
// chord names and matching are keel's own (src/keys.ts, through @keel/web-sdk).

import { keyLabel, matches, readKeymap, type Keymap } from "@keel/web-sdk";

export { keyLabel, matches, readKeymap, saveKeymap, type Keymap } from "@keel/web-sdk";

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

export function keysFor(id: ReviewAction, k: Keymap = readKeymap()): string {
  return (
    ACTIONS.find((a) => a.id === id)
      ?.keys[k].map(keyLabel)
      .join(" or ") ?? ""
  );
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
