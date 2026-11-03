// The Goals plugin's web part (plugins/goals): `npm run build:plugin -- goals` (in web/) builds this file on its own into
// plugins/goals/web/dist/index.js. It puts the Goals page in the menu (Run, after Tasks) and the card for a goal's next step in the Inbox
// (the slot inbox.card, kind goal-step). It imports only @keel/web-sdk and react.

import { definePlugin, SLOTS, type InboxCardItem, type KeelSdk } from "@keel/web-sdk";
import { GoalInboxCard, GoalsPage } from "./Goals";

export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "goals",
    label: "Goals",
    group: "run",
    order: 22,
    icon: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" /></>,
    component: GoalsPage,
  });
  sdk.registerSlot<InboxCardItem>(SLOTS.inboxCard, { id: "goals", kinds: { "goal-step": "goal's next step" }, component: GoalInboxCard });
}

export default definePlugin({ name: "goals", setup });
