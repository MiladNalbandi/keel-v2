// The explorer's questions (clarify loop) as buttons: one click per answer, the recommended option first,
// and a text field for "something else". Controlled by the gate card, which sends the answers with the resume.
import type { ClarifyQuestion } from "../api";

export type ClarifyAnswers = Record<string, string>;

/** The answers to send: a typed answer wins over a clicked option; an unanswered question is left out (the engine
 *  then uses the recommended option and says so to the explorer). */
export function answersOf(questions: ClarifyQuestion[], picked: ClarifyAnswers, typed: ClarifyAnswers): ClarifyAnswers {
  const out: ClarifyAnswers = {};
  for (const q of questions) {
    const t = (typed[q.id] ?? "").trim();
    const p = picked[q.id];
    if (t) out[q.id] = t;
    else if (p) out[q.id] = p;
  }
  return out;
}

export function ClarifyForm({ questions, picked, typed, onPick, onType }: {
  questions: ClarifyQuestion[];
  picked: ClarifyAnswers;
  typed: ClarifyAnswers;
  onPick: (id: string, label: string) => void;
  onType: (id: string, text: string) => void;
}) {
  return (
    <div className="clarify" data-testid="clarify-form">
      {questions.map((q, n) => {
        const usesText = !!(typed[q.id] ?? "").trim();
        return (
          <fieldset key={q.id} className="clarify-q">
            <legend><span className="clarify-n">{n + 1}</span> {q.question}</legend>
            {q.why && <p className="sub clarify-why">{q.why}</p>}
            <div className="clarify-opts" role="radiogroup" aria-label={q.question}>
              {q.options.map((o) => {
                const on = !usesText && picked[q.id] === o.label;
                return (
                  <button key={o.label} type="button" role="radio" aria-checked={on} className={`clarify-opt${on ? " on" : ""}`}
                    onClick={() => { onType(q.id, ""); onPick(q.id, o.label); }}>
                    <b>{o.label}</b>
                    {o.recommended && <span className="clarify-rec">recommended</span>}
                    {o.description && <span className="sub">{o.description}</span>}
                  </button>
                );
              })}
            </div>
            <input className="clarify-other" type="text" aria-label={`Your own answer to: ${q.question}`}
              placeholder="or answer in your own words" value={typed[q.id] ?? ""} onChange={(e) => onType(q.id, e.target.value)} />
          </fieldset>
        );
      })}
      <p className="sub">Not answered = the recommended option, and the explorer writes it down as an assumption.</p>
    </div>
  );
}
