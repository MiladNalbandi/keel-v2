// The Supervisor plugin's web part (plugins/supervisor): `npm run build:plugin -- supervisor` (in web/) builds this file
// on its own into plugins/supervisor/web/dist/index.js. It puts the Supervisor in Jobs as the tab Supervisor
// (#/jobs/supervisor): the flow that waits now with its on/off switch and mode, and the audit log of every look,
// answer and escalation with its reason. It also puts a chip in a flow's header, a note on a flow's Inbox item, and a
// row on Settings › General. It imports only @keel/web-sdk and react.

import {
  definePlugin,
  SLOTS,
  type FlowChipItem,
  type InboxNoteItem,
  type JobsTabItem,
  type KeelSdk,
  type SettingsSectionItem,
} from "@keel/web-sdk";
import { SupervisorView } from "./Supervisor";
import { SupervisorChip, SupervisorNote, SupervisorSettingsRow } from "./SupervisorParts";

export function setup(sdk: KeelSdk) {
  sdk.registerSlot<JobsTabItem>(SLOTS.jobsTab, {
    id: "supervisor",
    title: "Supervisor",
    sub: "What the Supervisor looked at, answered and asked you about, and why.",
    plugin: "supervisor",
    order: 30,
    component: SupervisorView,
  });
  sdk.registerSlot<FlowChipItem>(SLOTS.flowChip, { id: "supervisor", component: SupervisorChip });
  sdk.registerSlot<InboxNoteItem>(SLOTS.inboxNote, { id: "supervisor", component: SupervisorNote });
  sdk.registerSlot<SettingsSectionItem>(SLOTS.settingsSection, {
    id: "supervisor",
    title: "Supervisor",
    sub: "A strong model that watches your flows. Off until you turn it on, and then it only suggests until you pick another mode.",
    order: 95,
    rows: [],
    component: SupervisorSettingsRow,
  });
}

export default definePlugin({ name: "supervisor", setup });
