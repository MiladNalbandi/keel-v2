// The Record plugin's web part (plugins/record): `npm run build:plugin -- record` (in web/) builds this file on its own
// into plugins/record/web/dist/index.js. It puts the Record page in the menu (Run, after Jobs) and a small panel under a
// flow's steps (the slot flow.panel). It imports only @keel/web-sdk and react.

import { definePlugin, SLOTS, type FlowPanelItem, type KeelSdk } from "@keel/web-sdk";
import { RecordPage, RecordFlowPanel } from "./Record";

export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "record",
    label: "Record",
    group: "watch",
    order: 30,
    icon: <path d="M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6" />,
    component: RecordPage,
  });
  sdk.registerSlot<FlowPanelItem>(SLOTS.flowPanel, { id: "record", component: RecordFlowPanel });
}

export default definePlugin({ name: "record", setup });
