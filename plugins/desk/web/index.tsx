// The Desk plugin's web part (plugins/desk): `npm run build:plugin -- desk` (in web/) builds this file on its own into
// plugins/desk/web/dist/index.js. It puts the Desk page in the menu (Run, after Tasks) and the card for a new request in the
// Inbox (the slot inbox.card, kind desk-request). It imports only @keel/web-sdk and react.

import { definePlugin, SLOTS, type InboxCardItem, type KeelSdk } from "@keel/web-sdk";
import { DeskInboxCard, DeskPage } from "./Desk";

export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "desk",
    label: "Desk",
    group: "run",
    order: 25,
    icon: <path d="M4 13h4l2 3h4l2-3h4M4 13V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v7M4 13v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5" />,
    component: DeskPage,
  });
  sdk.registerSlot<InboxCardItem>(SLOTS.inboxCard, { id: "desk", kinds: { "desk-request": "support request" }, component: DeskInboxCard });
}

export default definePlugin({ name: "desk", setup });
