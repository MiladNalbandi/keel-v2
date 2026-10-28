// keel 0.18.9 "Ask KeelBot to fix it": when a button KeelBot gave fails (a git switch, a flow's stop, a query …), the
// card offers to hand the error to KeelBot, which finds the cause in the project and proposes a fix as buttons the person
// presses. KeelBot's chat provides the way to ask (AskKeelBot); a card outside a chat shows no such button.
import { createContext, useContext } from "react";
import { fixRequest } from "../askFix";

export { fixRequest };

/** keel 0.18.63 `mode`: switch the chat to it first (a picked "Switch to Edit" option), then send the text */
export type AskOptions = { mode?: "edit" | "ask" };
export const AskKeelBot = createContext<((text: string, opts?: AskOptions) => void) | null>(null);

export function AskFixButton({ what, error }: { what: string; error: { message: string; hint?: string } | null }) {
  const ask = useContext(AskKeelBot);
  if (!ask || !error) return null;
  return (
    <button type="button" className="btn sm" onClick={() => ask(fixRequest(what, error))}
      title="KeelBot reads the error and the project, says why it failed and proposes a fix you press">
      Ask KeelBot to fix it
    </button>
  );
}
