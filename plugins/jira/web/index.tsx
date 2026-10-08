// The Jira plugin's web part (plugins/jira): `npm run build:plugin -- jira` (in web/) builds this file on its own into
// plugins/jira/web/dist/index.js. keel loads it at start from the url /api/features gives and calls setup() once: it
// puts the Jira section in Connections and the MCP catalog in Tools, where they always were. It imports only
// @keel/web-sdk and react: keel's page shares its own copies (the import map).

import {
  definePlugin,
  SLOTS,
  type ConnectionKindItem,
  type KeelSdk,
  type ToolsCardItem,
} from "@keel/web-sdk";
import { JiraCatalog, JiraSection } from "./JiraCard";

/** Connections › Jira and Tools › Catalog: the same ids, titles and order as keel 0.15.1. */
export function setup(sdk: KeelSdk) {
  sdk.registerSlot<ConnectionKindItem>(SLOTS.connectionsKind, {
    id: "jira",
    title: "Jira",
    order: 10,
    component: JiraSection,
  });
  sdk.registerSlot<ToolsCardItem>(SLOTS.toolsCard, {
    id: "jira-catalog",
    title: "Catalog",
    order: 10,
    component: JiraCatalog,
  });
}

export default definePlugin({ name: "jira", setup });
