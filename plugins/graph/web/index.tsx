// The Graph plugin's web part (plugins/graph): `npm run build:plugin -- graph` (in web/) builds this file on its own into
// plugins/graph/web/dist/index.js and style.css (graph.css, the look of keel 0.15.1). keel loads both at start from the
// urls /api/features gives and calls setup() once: it puts the Graph page in the menu where it always was (Project,
// after Map). It imports only @keel/web-sdk and react: keel's page shares its own copies (the import map).

import { definePlugin, type KeelSdk } from "@keel/web-sdk";
import { GraphPage } from "./Graph";
import "./graph.css";

/** The Graph page in the menu: the same id, label, group, place and icon as keel 0.15.1. */
export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "graph",
    label: "Graph",
    group: "know",
    order: 40,
    icon: (
      <>
        <circle cx="6" cy="6" r="2.5" />
        <circle cx="18" cy="7" r="2.5" />
        <circle cx="12" cy="18" r="2.5" />
        <path d="M8.3 7l7.3-.2M7.2 8.3l3.7 7.5M16.9 9.3l-3.7 6.5" />
      </>
    ),
    component: GraphPage,
  });
}

export default definePlugin({ name: "graph", setup });
