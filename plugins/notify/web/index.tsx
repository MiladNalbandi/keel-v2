// The Notify plugin's web part (plugins/notify): `npm run build:plugin -- notify` (in web/) builds this file on its own into
// plugins/notify/web/dist/index.js. It puts its tab in Settings (Settings › Notify: the channels, what is sent, the last
// messages) and one line in Settings › Connections that points there. It imports only @keel/web-sdk and react.

import { definePlugin, SLOTS, type ConnectionKindItem, type KeelSdk, type SettingsTabItem } from "@keel/web-sdk";
import { NotifyConnections, NotifyTab } from "./Notify";

export function setup(sdk: KeelSdk) {
  sdk.registerSlot<SettingsTabItem>(SLOTS.settingsTab, {
    id: "notify",
    title: "Notify",
    order: 65,
    line: "A chat or mail message when a flow waits for you. Nothing is sent until you add a channel here.",
    component: NotifyTab,
  });
  sdk.registerSlot<ConnectionKindItem>(SLOTS.connectionsKind, { id: "notify", order: 90, component: NotifyConnections });
}

export default definePlugin({ name: "notify", setup });
