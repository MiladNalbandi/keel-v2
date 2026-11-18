// 0.36 The comment box under a line (or under the last of the selected lines) in Review › Files: your comment, and
// Ask KeelBot about those lines without leaving the review. The answer shows in the box; "Use in my comment" puts it
// in the comment, "Open the chat" goes on in KeelBot.
//
//   ┌ Comment on lines 12–18                                   ┐
//   │ │ + if amount > refundable_amount(order):                 │
//   │ [Your comment… (pending until you submit)              ]  │
//   │ [Add to my review] [Cancel]               [Ask KeelBot]   │
//   │ ┌ KeelBot ───────────────────────────────────────────┐    │
//   │ │ [Ask about these lines… (empty: explain + problems)] [Ask]│
//   │ │ The check misses the coupon: …                      │    │
//   │ │ [Use in my comment] [Open the chat]                 │    │
//   │ └─────────────────────────────────────────────────────┘    │

import { useEffect, useRef, useState } from "react";
import { ErrorBox, Markdown } from "@keel/web-sdk";
import { askInReview, askText, openChat, type AskResult } from "./askKeelBot";

export function CommentBox({
  pid,
  reviewKey,
  label,
  code,
  askWhere,
  chatTitle,
  helper,
  text,
  setText,
  busy,
  err,
  onSubmit,
  onCancel,
}: {
  pid: string;
  reviewKey: string;
  /** "line 20" or "lines 12–18" */
  label: string;
  /** the code the comment is on (shown as a quote; KeelBot gets it) */
  code?: string;
  /** where the lines are, as KeelBot reads it (path, PR, commit) */
  askWhere: string;
  chatTitle: string;
  /** the assistant's name (KeelBot), null when it is off */
  helper: string | null;
  text: string;
  setText: (t: string) => void;
  busy: boolean;
  err: { message: string; hint?: string } | null;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const [asking, setAsking] = useState(false); // the ask part is open
  const [question, setQuestion] = useState("");
  const [wait, setWait] = useState<number | null>(null); // seconds waited, null when not waiting
  const [answer, setAnswer] = useState<AskResult | null>(null);
  const [askErr, setAskErr] = useState<{ message: string; hint?: string } | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (wait == null) return;
    const t = setTimeout(() => setWait((w) => (w == null ? w : w + 1)), 1000);
    return () => clearTimeout(t);
  }, [wait]);

  const quote = (code ?? "").split("\n").slice(0, 8);
  const more = (code ?? "").split("\n").length - quote.length;

  const ask = async () => {
    if (wait != null) return;
    setAskErr(null);
    setAnswer(null);
    setWait(0);
    try {
      const r = await askInReview(
        pid,
        reviewKey,
        chatTitle,
        askText({ where: askWhere, code: code ?? "", question }),
        () => alive.current,
      );
      if (!alive.current) return;
      if (r) setAnswer(r);
      setQuestion("");
    } catch (e) {
      if (alive.current) setAskErr(e instanceof Error ? e : { message: String(e) });
    } finally {
      if (alive.current) setWait(null);
    }
  };

  return (
    <form
      className="rvn-compose"
      aria-label={`Comment on ${label}`}
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div className="rvn-cb-h">Comment on {label}</div>
      {quote.some((l) => l.trim()) && (
        <pre className="rvn-cb-q" aria-label="The code the comment is on">
          {quote.join("\n")}
          {more > 0 && `\n… ${more} more line${more === 1 ? "" : "s"}`}
        </pre>
      )}
      <textarea
        className="rvn-ta"
        rows={2}
        autoFocus
        aria-label={`Your comment on ${label}`}
        placeholder="Your comment (pending until you submit)"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            onSubmit();
          }
        }}
      />
      <div className="rvn-row">
        <button type="submit" className="rvn-btn primary" disabled={!text.trim() || busy}>
          Add to my review
        </button>
        <button type="button" className="rvn-btn" onClick={onCancel}>
          Cancel
        </button>
        {helper && !asking && (
          <button
            type="button"
            className="rvn-btn rvn-cb-ask"
            title={`Ask ${helper} about ${label} before you write the comment`}
            onClick={() => setAsking(true)}
          >
            Ask {helper}
          </button>
        )}
      </div>
      {err && <ErrorBox error={err} />}
      {helper && asking && (
        <section className="rvn-cb-bot" aria-label={`Ask ${helper} about ${label}`}>
          <div className="rvn-cb-askrow">
            <input
              className="rvn-ta rvn-cb-in"
              autoFocus
              aria-label={`Your question to ${helper}`}
              placeholder="Your question (optional)"
              title={`Ask ${helper} about ${label}. Left empty, it explains the code and looks for problems.`}
              value={question}
              disabled={wait != null}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void ask();
                }
                if (e.key === "Escape") {
                  e.preventDefault();
                  setAsking(false);
                }
              }}
            />
            <button type="button" className="rvn-btn" disabled={wait != null} onClick={() => void ask()}>
              Ask
            </button>
            <button
              type="button"
              className="rvn-x"
              aria-label={`Close ${helper}`}
              onClick={() => setAsking(false)}
            >
              ×
            </button>
          </div>
          {wait != null && (
            <p className="rvn-dim" role="status">
              {helper} reads the code… {wait > 0 ? `${wait} s` : ""}
            </p>
          )}
          {askErr && <ErrorBox error={askErr} />}
          {answer && (
            <>
              <div className={`rvn-cb-a${answer.failed ? " failed" : ""}`} aria-label={`${helper}'s answer`}>
                <Markdown text={answer.text} breaks answer />
              </div>
              <div className="rvn-row">
                {!answer.failed && (
                  <button
                    type="button"
                    className="rvn-btn"
                    onClick={() => setText(text.trim() ? `${text.trimEnd()}\n\n${answer.text}` : answer.text)}
                  >
                    Use in my comment
                  </button>
                )}
                <button type="button" className="rvn-btn" onClick={() => openChat(pid, answer.sid)}>
                  Open the chat
                </button>
              </div>
            </>
          )}
        </section>
      )}
    </form>
  );
}
