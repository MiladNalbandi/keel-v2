// 0.36 Ask KeelBot from a review comment: KeelBot answers inside the comment box, about the lines the comment is on,
// without leaving the review. One ask-mode chat per review (kept in this browser), so a follow-up question keeps what
// was asked before; "Open the chat" goes on in KeelBot with that chat.
//
//   comment box ── Ask KeelBot ──▶ POST helper/sessions (once per review, mode ask) ──▶ POST …/turn {text}
//        ▲                                                                                   │
//        └── answer shown under the box ◀── GET …/sessions/{sid} until an answer after turn n┘

import { go, post, get, shareFocus, type HelperMessage, type HelperSession, type HelperTurnStarted } from "@keel/web-sdk";

const chatKey = (pid: string, reviewKey: string) => `keel2.review.ask.${pid}.${reviewKey}`;
const sidKey = (pid: string) => `keel2.helper.${pid}.session`; // KeelBot's panel opens this chat (HelperPanel)
const e = encodeURIComponent;

function read(k: string): string | null {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
}
function write(k: string, v: string) {
  try {
    localStorage.setItem(k, v);
  } catch {
    /* private window: the chat is made again next time */
  }
}

/** The first answer (or the note of a turn that stopped) after the user message numbered `n`. */
export function answerAfter(messages: HelperMessage[] | undefined, n: number): HelperMessage | null {
  return (messages ?? []).find((m) => (m.role === "helper" || m.role === "note") && m.n > n) ?? null;
}

/** What KeelBot gets: where the lines are, the lines, and the question (what the person typed, or a default one). */
export function askText(o: { where: string; code: string; question: string }): string {
  const q = o.question.trim();
  const lines = [
    `I am writing a review comment on ${o.where}.`,
    "",
    "```",
    o.code.trimEnd(),
    "```",
    "",
  ];
  if (q) lines.push(`My question: ${q}`);
  else
    lines.push(
      "Explain what this code does and say if anything here is wrong or risky (bugs, missed cases). Keep it short, so it can go in my comment.",
    );
  return lines.join("\n");
}

export type AskResult = { sid: string; text: string; failed: boolean };

/**
 * Ask in this review's chat and wait for the answer. `alive()` false stops the wait (the box closed); the answer
 * then still lands in the chat.
 */
export async function askInReview(
  pid: string,
  reviewKey: string,
  title: string,
  text: string,
  alive: () => boolean = () => true,
): Promise<AskResult | null> {
  const base = `/projects/${e(pid)}/helper/sessions`;
  const fresh = async () => {
    const s = await post<HelperSession>(base, { mode: "ask", title });
    write(chatKey(pid, reviewKey), s.id);
    return s.id;
  };
  let sid = read(chatKey(pid, reviewKey));
  let turn: HelperTurnStarted | null = null;
  if (sid) {
    try {
      turn = await post<HelperTurnStarted>(`${base}/${e(sid)}/turn`, { text });
    } catch {
      sid = null; // the chat was deleted (or is gone): a new one
    }
  }
  if (!sid || !turn) {
    sid = await fresh();
    turn = await post<HelperTurnStarted>(`${base}/${e(sid)}/turn`, { text });
  }
  for (let i = 0; i < 400 && alive(); i++) {
    await new Promise((r) => setTimeout(r, i < 4 ? 800 : 1500));
    if (!alive()) return null;
    const s = await get<HelperSession>(`${base}/${e(sid)}`);
    const a = answerAfter(s.messages, turn.n);
    if (a) return { sid, text: a.text.trim() || s.error || "KeelBot gave no answer.", failed: a.role === "note" };
    if (s.status === "failed") return { sid, text: s.error || "KeelBot stopped.", failed: true };
  }
  return alive() ? { sid, text: "KeelBot is still working. Open the chat to see its answer.", failed: true } : null;
}

/** Go on in KeelBot with this chat. */
export function openChat(pid: string, sid: string) {
  write(sidKey(pid), sid);
  shareFocus({ chat: sid, chat_project: pid });
  go("helper");
}
