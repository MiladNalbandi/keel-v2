// keel 0.18.13 a waiting question's named choices (a gate's exits, a stuck criterion's retry / skip / edit / stop, a
// plan window's continue / wait / cheaper / stop) as one card per choice: its label and what it does, the engine's
// default marked. A click selects; a choice that needs a reason shows the reason box and Send is refused (with a line
// that says so) until it has text; an optional reason shows the box as optional. The number keys 1–9 pick a choice
// while the focus is in the form (not while typing). Flow's gate card and the Inbox send the same answer: approve with
// payload {choice: name} and the reason.
// keel 0.18.64 `draft`: the picked choice and the reason are kept until sent (drafts.tsx), with a quiet "draft kept".
import { useState } from "react";
import type { ChoiceInfo } from "../api";
import { DraftKept, useAnswerDraft, type DraftPlace } from "../drafts";
import { choiceProblem } from "../questions";

export function ChoiceForm({
  choices,
  onSend,
  busy = false,
  id,
  sendLabel = "Send",
  draft = null,
}: {
  choices: ChoiceInfo[];
  onSend: (choice: string, why: string) => void | Promise<void>;
  busy?: boolean;
  /** makes the reason box's id unique on a page with several forms (the Inbox) */
  id: string;
  sendLabel?: string;
  /** where the unsent choice and reason are kept (null: not kept) */
  draft?: DraftPlace | null;
}) {
  const first = (choices.find((c) => c.default) ?? choices[0])?.name ?? "";
  // the default choice is no draft: only a choice the person clicked is kept
  const [stored, setPicked, keptChoice] = useAnswerDraft<string>(draft, "choice", first, (v) => !v || v === first);
  const [why, setWhy, keptWhy] = useAnswerDraft<string>(draft, "choice-why", "");
  const [problem, setProblem] = useState("");
  // a kept choice the question does not have (any more) is the default again
  const picked = choices.some((c) => c.name === stored) ? stored : first;
  const chosen = choices.find((c) => c.name === picked);
  const whyId = `${id}-choice-why`;

  const pick = (name: string) => {
    setPicked(name);
    setProblem("");
  };
  const send = () => {
    const p = choiceProblem(chosen, why);
    if (p) {
      setProblem(p);
      document.getElementById(whyId)?.focus();
      return;
    }
    void onSend(chosen!.name, why.trim());
  };
  const onKey = (e: React.KeyboardEvent) => {
    const tag = (e.target as HTMLElement).tagName;
    if (
      busy ||
      e.metaKey ||
      e.ctrlKey ||
      e.altKey ||
      tag === "TEXTAREA" ||
      tag === "INPUT" ||
      tag === "SELECT"
    )
      return;
    const k = Number(e.key);
    if (!Number.isInteger(k) || k < 1 || k > Math.min(9, choices.length))
      return;
    e.preventDefault();
    e.stopPropagation();
    pick(choices[k - 1].name);
  };

  return (
    <div
      className="choice-form"
      data-testid="choice-form"
      onKeyDown={onKey}
      tabIndex={-1}
    >
      <div className="choice-opts" role="radiogroup" aria-label="Choose one">
        {choices.map((c, n) => {
          const on = c.name === picked;
          return (
            <button
              key={c.name}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={busy}
              className={`clarify-opt choice-opt${on ? " on" : ""}${c.default ? " default" : ""}`}
              aria-keyshortcuts={n < 9 ? String(n + 1) : undefined}
              onClick={() => pick(c.name)}
            >
              <span className="choice-head">
                {n < 9 && (
                  <span className="choice-n" aria-hidden="true">
                    {n + 1}
                  </span>
                )}
                <b>{c.label || c.name}</b>
                {c.default && <span className="choice-default">default</span>}
                {c.why === "required" && (
                  <span className="choice-need">needs a reason</span>
                )}
              </span>
              {c.description && <span className="sub">{c.description}</span>}
            </button>
          );
        })}
      </div>
      {chosen && chosen.why !== "none" && (
        <div className="field">
          <label htmlFor={whyId}>
            {chosen.why === "required"
              ? `Why (needed for “${chosen.label || chosen.name}”)`
              : "Why (optional)"}
          </label>
          <textarea
            id={whyId}
            rows={2}
            value={why}
            aria-invalid={!!problem || undefined}
            onChange={(e) => {
              setWhy(e.target.value);
              if (e.target.value.trim()) setProblem("");
            }}
          />
        </div>
      )}
      {problem && (
        <p className="choice-problem" role="alert">
          {problem}
        </p>
      )}
      <div className="row">
        <button
          type="button"
          className="btn warn"
          disabled={busy || !chosen}
          onClick={send}
        >
          {busy
            ? "Sending…"
            : chosen
              ? `${sendLabel}: ${chosen.label || chosen.name}`
              : sendLabel}
        </button>
        <DraftKept shown={keptChoice || keptWhy} />
      </div>
    </div>
  );
}
