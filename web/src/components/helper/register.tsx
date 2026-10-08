// The KeelBot part (web/src/builtins.ts loads this file): its own page (#/helper, also #/keelbot), kept in a
// Product-only keel too, and the assistant column of the Code page (slot assistant, ⌘I). It answers askAssistant:
//
//   askAssistant("/ci run #12") ─▶ keel:ask-assistant ─▶ here: keep the text for KeelBot's own page
//                                                     └▶ the Code page: open the column with the text in the input

import { HelperPage } from "../../pages/Helper";
import {
  ASK_ASSISTANT_EVENT,
  type AskAssistantDetail,
} from "../../sdk/assistant";
import { registerPage, registerSlot } from "../../sdk/registry";
import { SLOTS, type AssistantItem } from "../../sdk/slots";
import { HelperPanel } from "./HelperPanel";
import { PREFILL_KEY } from "./model";

registerPage({
  id: "helper",
  label: "KeelBot",
  group: "know",
  order: 20,
  aliases: ["keelbot"],
  product: true,
  icon: (
    <>
      <path d="M4 5h16v11H10l-5 4v-4H4z" />
      <path d="M12 8l.8 1.7 1.7.8-1.7.8L12 13l-.8-1.7-1.7-.8 1.7-.8z" />
    </>
  ),
  component: HelperPage,
});

registerSlot<AssistantItem>(SLOTS.assistant, {
  id: "keelbot",
  title: "KeelBot",
  component: HelperPanel,
});

// KeelBot's own page reads the handed-over text once when it opens (a part may go there right after asking)
window.addEventListener(ASK_ASSISTANT_EVENT, (e) => {
  const text = (e as CustomEvent<AskAssistantDetail>).detail?.text;
  if (!text) return;
  try {
    sessionStorage.setItem(PREFILL_KEY, text);
  } catch {
    /* the Code page's column still gets the event */
  }
});
