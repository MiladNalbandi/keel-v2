// KeelBot panel's pure helpers: file:line links in answers, what the person is typing (@ or /), and the words for
// a session. Kept apart from the component so tests can check them directly.

import type { HelperMessage, HelperSession } from "@keel/web-sdk";

// file:line links are keel's own (the launcher reads them in answers too)
export { fileLink } from "@keel/web-sdk";

/** KeelBot's own page (#/keelbot) reads this once when it opens: text another page handed over (askAssistant). */
export const PREFILL_KEY = "keel2.keelbot.prefill";

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

/** An answer's model for its footer: "sonnet · high" (the effort only when the call had one). */
export const answerModel = (d: HelperMessage["data"]) => `${d.model ?? ""}${d.effort ? ` · ${d.effort}` : ""}`;

/** "1.2k tokens · $0.04" for a session or one answer. */
export function usageText(tokens: number, cost: number): string {
  const t = tokens >= 1_000_000 ? `${(tokens / 1_000_000).toFixed(1)}M` : tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens);
  return cost > 0 ? `${t} tokens · $${cost.toFixed(2)}` : `${t} tokens`;
}

const k = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

/** 0.31 the parts of a usage, for the line under an answer and its tooltip: "0.1k new · 1.7k cached · 5 out". */
export function usageParts(u: { tokens_in?: number; tokens_out?: number; tokens_cached?: number }): { line: string; title: string } {
  const i = u.tokens_in ?? 0, o = u.tokens_out ?? 0, c = u.tokens_cached ?? 0;
  return {
    line: c > 0 ? `${k(i)} new · ${k(c)} cached · ${k(o)} out` : `${k(i)} in · ${k(o)} out`,
    title: `Sent to the model: ${i.toLocaleString()} new tokens${c > 0 ? ` and ${c.toLocaleString()} it had in its cache (they cost about a tenth)` : ""}. ` +
      `Its answer: ${o.toLocaleString()} tokens. The total keel counts is new + answer + a tenth of the cached.`,
  };
}

export const sessionTokens = (s: Pick<HelperSession, "tokens_in" | "tokens_out" | "tokens_cached">) =>
  s.tokens_in + s.tokens_out + Math.floor(s.tokens_cached / 10);

export const messageTokens = (m: HelperMessage) =>
  (m.data.tokens_in ?? 0) + (m.data.tokens_out ?? 0) + Math.floor((m.data.tokens_cached ?? 0) / 10);

/** Questions that show what KeelBot is for, from the project's own state. */
export function starters(opts: { flowWaits: boolean; openFile?: string | null }): string[] {
  const out = ["Where does this project start, and how is it organised?", "/where the main entry point"];
  if (opts.openFile) out.unshift(`/explain ${opts.openFile}`);
  if (opts.flowWaits) out.unshift("/gate");
  return out.slice(0, 4);
}


/** 0.18.57 the chat's banner about the project folder's branch: the folder is on another branch now than the last
 *  answer read (live: the session's folder_branch), or the last answer came after a switch (or during one). */
export type FolderBanner = { kind: "live" | "switched" | "during"; to: string; from: string; at?: string | null; by?: string };

export function folderBanner(s: Pick<HelperSession, "folder_branch"> | null | undefined, messages: HelperMessage[]): FolderBanner | null {
  const answers = messages.filter((m) => m.role !== "user" && m.data?.folder_branch);
  const last = answers[answers.length - 1];
  if (!last) return null;
  const read = last.data.folder_branch!;
  if (s?.folder_branch && s.folder_branch !== read) return { kind: "live", to: s.folder_branch, from: read };
  const sw = last.data.branch_switch;
  if (!sw) return null;
  return { kind: sw.during ? "during" : "switched", to: sw.to, from: sw.from, at: sw.at, by: sw.by };
}
