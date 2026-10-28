// KeelBot's Questions action block(s), split out of Actions.tsx (Actions.tsx re-exports them).

import { useContext, useEffect, useMemo, useState } from "react";
import { answersMessage, answersOf, AskKeelBot, ClarifyForm, clearDraft, DraftKept, noKeys, parseQuestions, pickedMode, readAnswersMessage, useAnswerDraft,
  type ClarifyAnswers } from "@keel/web-sdk";

/** A short, steady key for a block's text (the same questions give the same key after a reload). */
export function textKey(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return `${(h >>> 0).toString(36)}-${s.length}`;
}

/** keel 0.18.9 KeelBot asks the person to choose (```keel-questions```): keel's question form (pick one, tick several,
 *  your own words, how many are answered); Send gives the answers to KeelBot as the next message.
 *  keel 0.18.13 `answered`: the chat's later message with the answers (it starts with "Answers to your questions"): the
 *  card shows what was sent, read only, also after a reload (derived from the chat, nothing stored).
 *  0.18.38 one question with the id "next" is KeelBot's next steps: buttons, not a form.
 *  0.18.61 one question that takes one answer: a click on an option sends it, no "Send my answers" step (your own
 *  words still wait for Send). Several questions keep the Send step.
 *  0.18.63 an option with `"mode": "edit"` (KeelBot's "Switch to Edit") switches the chat to Edit before its answer goes.
 *  0.18.64 the picked options and typed words are kept until they are sent (another page or project and back: still
 *  there), by the project and the questions' text. */
export function QuestionsCard({ body, answered, pid }: { body: string; answered?: string | null;
  /** the chat's project: the draft is kept under it (none: not kept) */
  pid?: string }) {
  const qs = parseQuestions(body);
  const ask = useContext(AskKeelBot);
  const place = useMemo(() => (pid ? { project: pid, thread: "keelbot", question: textKey(body) } : null), [pid, body]);
  const [picked, setPicked, keptPicked] = useAnswerDraft<ClarifyAnswers>(place, "picked", {}, noKeys);
  const [typed, setTyped, keptTyped] = useAnswerDraft<ClarifyAnswers>(place, "typed", {});
  const [sent, setSent] = useState<string | null>(null);
  // answered (here, or the chat already has the answers): nothing is kept any more
  const done = !!(answered ?? sent);
  useEffect(() => {
    if (done) clearDraft(place);
  }, [done, place]);
  if (!qs.length) return <p className="kb-card bad" role="note">KeelBot's questions could not be read. Ask it to ask again.</p>;
  const send = (p: ClarifyAnswers = picked, t: ClarifyAnswers = typed) => {
    const answers = answersOf(qs, p, t);
    const text = answersMessage(qs, answers);
    const mode = pickedMode(qs, answers);
    if (mode) ask?.(text, { mode });
    else ask?.(text);
    setSent(text);
    clearDraft(place);
  };
  const one = qs.length === 1 && !qs[0].multiple;
  const onPick = (id: string, value: string | string[]) => {
    const next = { ...picked, [id]: value };
    setPicked(next);
    // the option, not words typed before it (the form clears them, but this render still has them)
    if (one && ask && typeof value === "string") send(next, { ...typed, [id]: "" });
  };
  const said = readAnswersMessage(answered ?? sent ?? "");
  const q = qs[0];
  if (qs.length === 1 && q.id === "next" && !q.multiple) {
    // 0.18.38 KeelBot's next steps (one question with the id "next"): a row of buttons, one press sends the pick
    const pick = (label: string) => {
      const text = answersMessage(qs, { next: label });
      const mode = pickedMode(qs, { next: label });
      if (mode) ask?.(text, { mode });
      else ask?.(text);
      setSent(text);
    };
    return (
      <section className="kb-next" aria-label="Next steps">
        <span className="kb-next-h">Next — KeelBot can</span>
        {said ? <p className="kb-done" role="status">You picked: <b>{said[0]?.answer}</b></p> : (
          <div className="kb-next-btns">
            {q.options.map((o) => (
              <button key={o.label} type="button" className={`btn sm${o.recommended ? " primary" : ""}`} title={o.description} disabled={!ask}
                onClick={() => pick(o.label)}>{o.label}</button>
            ))}
          </div>
        )}
      </section>
    );
  }
  return (
    <section className="kb-card" aria-label="KeelBot's questions">
      <div className="kb-card-h"><span className="kb-tag">Questions</span><b>KeelBot asks</b></div>
      {said ? (
        <div className="kb-done" role="status">
          <p>Your answers went to KeelBot:</p>
          <ol className="kb-answers" aria-label="Your answers">
            {said.map((x, i) => <li key={i}><span className="sub">{x.question}</span> → <b>{x.answer}</b></li>)}
          </ol>
        </div>
      ) : (
        <>
          <ClarifyForm questions={qs} picked={picked} typed={typed}
            onPick={onPick} onType={(id, text) => setTyped((p) => ({ ...p, [id]: text }))} />
          <div className="row"><button type="button" className="btn sm primary" disabled={!ask} onClick={() => send()}>{one ? "Send" : "Send my answers"}</button>
            <DraftKept shown={keptPicked || keptTyped} /></div>
        </>
      )}
    </section>
  );
}
