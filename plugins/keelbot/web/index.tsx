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
//   new answers (0.15.2) an answer you did not see: a number on KeelBot's menu entry (slot nav.badge), on its icon in
//                       the folded menu and on its button in the Code page (the assistant's count); its own sound
//                       (watched on every page: slot shell.watch; switched in the bell's settings and Settings › This
//                       browser: slots notes.setting, settings.browser)
//   ⌘/ (0.15.4)         its keys in the key cheat sheet                                                    slot keys.area

import {
  ASK_ASSISTANT_EVENT,
  definePlugin,
  type AskAssistantDetail,
  type AssistantItem,
  type KeelSdk,
  type KeysAreaItem,
  type NavBadgeItem,
  type NotesSettingItem,
  type SettingsBrowserItem,
  type ShellWatchItem,
} from "@keel/web-sdk";
import { HelperPage } from "./HelperPage";
import { HelperPanel } from "./HelperPanel";
import { PREFILL_KEY } from "./model";
import { KeelBotCount, KeelBotSoundField, KeelBotSoundRow, KeelBotWatch } from "./unread";
import "./helper.css";

/** The count on the Code page's KeelBot button. */
const ActCount = () => <KeelBotCount kind="act" />;

/** KeelBot's keys in the key cheat sheet (⌘/), on the Code page and its own page. */
export const KEELBOT_KEYS: KeysAreaItem = {
  id: "keelbot",
  title: "KeelBot",
  pages: ["repo", "helper"],
  order: 60,
  rows: () => [
    { label: "Send", keys: ["enter"] },
    { label: "A new line", keys: ["shift+enter"] },
    { label: "A command", raw: ["/"] },
    { label: "Name a file, a symbol or an AC", raw: ["@"] },
    { label: "Pick a suggestion", raw: ["↓ ↑"] },
    { label: "Take the suggestion", keys: ["enter", "tab"] },
    { label: "Hide the suggestions", keys: ["escape"] },
  ],
};

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
    count: ActCount,
  });

  // 0.15.2 new answers: the count on its menu entry and folded-menu icon, the watcher, its sound's switch
  sdk.registerSlot<NavBadgeItem>(sdk.SLOTS.navBadge, {
    id: "keelbot",
    page: "helper",
    component: KeelBotCount,
  });
  sdk.registerSlot<ShellWatchItem>(sdk.SLOTS.shellWatch, {
    id: "keelbot",
    component: KeelBotWatch,
  });
  sdk.registerSlot<NotesSettingItem>(sdk.SLOTS.notesSetting, {
    id: "keelbot",
    component: KeelBotSoundField,
  });
  sdk.registerSlot<SettingsBrowserItem>(sdk.SLOTS.settingsBrowser, {
    id: "keelbot",
    component: KeelBotSoundRow,
  });
  // 0.15.4 its keys in the key cheat sheet
  sdk.registerSlot<KeysAreaItem>(sdk.SLOTS.keysArea, KEELBOT_KEYS);

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
