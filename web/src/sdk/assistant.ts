// "Ask the assistant" without importing it: a part hands text to KeelBot (or whatever answers in the `assistant` slot)
// with one window event. The assistant's part listens; the Code page opens its column with the text in the input.
//
//   Code › Git: "Ask KeelBot to address the comments"   askAssistant("Address the review comments on PR #7 …")
//        └─▶ window event keel:ask-assistant { text, context } ─▶ KeelBot keeps the text, the Code page opens its panel

export const ASK_ASSISTANT_EVENT = "keel:ask-assistant";

/** The event's detail: the text for the assistant's input, and anything else a part wants to say about it. */
export type AskAssistantDetail = {
  text: string;
  context?: Record<string, unknown>;
};

/** Hand text to the assistant's input: the person reads it and sends it. */
export function askAssistant(text: string, context?: Record<string, unknown>) {
  window.dispatchEvent(
    new CustomEvent<AskAssistantDetail>(ASK_ASSISTANT_EVENT, {
      detail: { text, context },
    }),
  );
}
