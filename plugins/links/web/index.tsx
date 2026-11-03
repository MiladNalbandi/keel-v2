// The Links plugin's web part (plugins/links): `npm run build:plugin -- links` (in web/) builds this file on its own into
// plugins/links/web/dist/index.js. It puts Linear in Settings › Connections and a card on the Tools page. The OK for a write is keel's
// own Inbox card. It imports only @keel/web-sdk and react.

import { definePlugin, SLOTS, type ConnectionKindItem, type KeelSdk, type ToolsCardItem } from "@keel/web-sdk";
import { LinearCard, LinksTools } from "./Links";

export function setup(sdk: KeelSdk) {
  sdk.registerSlot<ConnectionKindItem>(SLOTS.connectionsKind, { id: "linear", order: 40, component: LinearCard });
  sdk.registerSlot<ToolsCardItem>(SLOTS.toolsCard, { id: "links", order: 40, component: LinksTools });
}

export default definePlugin({ name: "links", setup });
