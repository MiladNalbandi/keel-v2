// The Helper panel's pure helpers: file:line links in answers, what the person is typing (@ or /), and the words for
// a session. Kept apart from the component so tests can check them directly.

import type { HelperMessage, HelperSession } from "../../api";

/** `src/app/checkout.js:12` or `src/app/checkout.js:12-14` → {path, line}; anything else → null. */
export function fileLink(text: string): { path: string; line: number } | null {
  const m = /^\s*([\w@.~-][\w@./~ -]*\.[\w]+):(\d+)(?:[-–](\d+))?\s*$/.exec(text);
  if (!m || m[1].includes("://") || m[1].startsWith("/")) return null;
  return { path: m[1].replace(/^\.\//, ""), line: Number(m[2]) };
}

export type Typing = { kind: "mention" | "command"; query: string; start: number } | null;

/** What the person is typing at the caret: `/com` at the start (a command) or `@que` after a space (a mention). */
export function typingAt(text: string, caret: number): Typing {
  const before = text.slice(0, caret);
  const cmd = /^\/([a-z0-9-]*)$/.exec(before);
  if (cmd) return { kind: "command", query: cmd[1], start: 0 };
  const at = /(^|\s)@([^\s@]*)$/.exec(before);
  if (at) return { kind: "mention", query: at[2], start: before.length - at[2].length - 1 };
  return null;
}

/** The text with the typed `@query` (or `/query`) replaced by the chosen word and a space. */
export function replaceTyping(text: string, typing: NonNullable<Typing>, caret: number, word: string): { text: string; caret: number } {
  const lead = typing.kind === "command" ? "/" : "@";
  const next = text.slice(0, typing.start) + lead + word + " " + text.slice(caret);
  return { text: next, caret: typing.start + lead.length + word.length + 1 };
}

/** "1.2k tokens · $0.04" for a session or one answer. */
export function usageText(tokens: number, cost: number): string {
  const t = tokens >= 1_000_000 ? `${(tokens / 1_000_000).toFixed(1)}M` : tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens);
  return cost > 0 ? `${t} tokens · $${cost.toFixed(2)}` : `${t} tokens`;
}

export const sessionTokens = (s: Pick<HelperSession, "tokens_in" | "tokens_out" | "tokens_cached">) =>
  s.tokens_in + s.tokens_out + Math.floor(s.tokens_cached / 10);

export const messageTokens = (m: HelperMessage) =>
  (m.data.tokens_in ?? 0) + (m.data.tokens_out ?? 0) + Math.floor((m.data.tokens_cached ?? 0) / 10);

/** Questions that show what the Helper is for, from the project's own state. */
export function starters(opts: { flowWaits: boolean; openFile?: string | null }): string[] {
  const out = ["Where does this project start, and how is it organised?", "/where the main entry point"];
  if (opts.openFile) out.unshift(`/explain ${opts.openFile}`);
  if (opts.flowWaits) out.unshift("/gate");
  return out.slice(0, 4);
}
