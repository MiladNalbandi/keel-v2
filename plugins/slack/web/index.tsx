// The Slack plugin's web part (plugins/slack): `npm run build:plugin -- slack` (in web/) builds this file on its own into
// plugins/slack/web/dist/index.js. It puts its tab in Settings (Settings › Slack: the tokens, who may act, what goes where, what happened) and one
// line in Settings › Connections that points there. It imports only @keel/web-sdk and react.

import { definePlugin, SLOTS, type ConnectionKindItem, type KeelSdk, type SettingsTabItem } from "@keel/web-sdk";
import { SlackConnections, SlackTab } from "./Slack";

export function setup(sdk: KeelSdk) {
  sdk.registerSlot<SettingsTabItem>(SLOTS.settingsTab, {
    id: "slack",
    title: "Slack",
    order: 66,
    line: "Ask how work goes, answer a gate and make a task from Slack. keel connects out, so it needs no public address.",
    component: SlackTab,
  });
  sdk.registerSlot<ConnectionKindItem>(SLOTS.connectionsKind, { id: "slack", order: 91, component: SlackConnections });
}

export default definePlugin({ name: "slack", setup });
