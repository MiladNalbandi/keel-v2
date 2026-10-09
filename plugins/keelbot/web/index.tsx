// The KeelBot plugin's web part (plugins/keelbot): `npm run build:plugin -- keelbot` (in web/) builds this file on its
// own into plugins/keelbot/web/dist/index.js and style.css (helper.css, the look of keel 0.15.1). keel loads both at
// start from the urls /api/features gives and calls setup() once: it registers what KeelBot registered as a built-in,
// with the same ids, titles and places. It imports only @keel/web-sdk and react: keel's page shares its own copies (the
// import map).
//
//   its own page        #/helper, also #/keelbot: Project, after Code; kept in a Product-only keel too     registerPage
//   the Code page       its column (⌘I), with what the person points at                                  slot assistant
//   askAssistant        another part hands KeelBot a question ("/ci run #12"):
//     askAssistant(text) ─▶ keel:ask-assistant ─▶ here: kept for KeelBot's own page (session storage, once)
//                                              └▶ the Code page: opens the column with the text in the input
//   its answers' cards  a plugin's block (keel-query, keel-git) shows the card that plugin put in slot keelbot.card

import {
  ASK_ASSISTANT_EVENT,
  definePlugin,
  type AskAssistantDetail,
  type AssistantItem,
  type KeelSdk,
} from "@keel/web-sdk";
import { HelperPage } from "./HelperPage";
import { HelperPanel } from "./HelperPanel";
import { PREFILL_KEY } from "./model";
import "./helper.css";

export function setup(sdk: KeelSdk) {
  sdk.registerPage({
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

  sdk.registerSlot<AssistantItem>(sdk.SLOTS.assistant, {
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
}

export default definePlugin({ name: "keelbot", setup });
