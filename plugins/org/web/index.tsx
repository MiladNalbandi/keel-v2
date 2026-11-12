// The Org plugin's web part (plugins/org): `npm run build:plugin -- org` (in web/) builds this file on its own into
// plugins/org/web/dist/index.js. It puts the Org page in the menu (Work, after Goals) and the ready-made orgs in
// Settings › Plugins › Marketplace › Orgs. It imports only @keel/web-sdk and react.

import { definePlugin, SLOTS, type KeelSdk, type MarketKindItem } from "@keel/web-sdk";
import { OrgPage } from "./Org";
import { OrgMarket } from "./Templates";
import "./org.css";

export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "org",
    label: "Org",
    group: "run",
    order: 24,
    needsProject: false,
    icon: <><circle cx="12" cy="5" r="2.5" /><circle cx="5" cy="18" r="2.5" /><circle cx="19" cy="18" r="2.5" /><path d="M12 7.5v4M12 11.5H5v4M12 11.5h7v4" /></>,
    component: OrgPage,
  });
  // Settings › Plugins › Marketplace › Orgs: the ready-made orgs (#/settings/plugins?tab=market&kind=orgs)
  sdk.registerSlot<MarketKindItem>(SLOTS.marketKind, {
    id: "orgs",
    title: "Orgs",
    order: 10,
    line: "Ready-made orgs of agents: who is in each one and who gives work to whom. Use one as it is, or change any line before you save it as your own.",
    component: OrgMarket,
  });
}

export default definePlugin({ name: "org", setup });
