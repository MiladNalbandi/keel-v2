// The Map plugin's web part (plugins/map): `npm run build:plugin -- map` (in web/) builds this file on its own into
// plugins/map/web/dist/index.js. keel loads it at start from the url /api/features gives and calls setup() once: it
// puts the Map page in the menu where it always was (Project, between KeelBot and Graph). It imports only
// @keel/web-sdk and react: keel's page shares its own copies (the import map).

import { definePlugin, type KeelSdk } from "@keel/web-sdk";
import { MapPage } from "./Map";

/** The Map page in the menu: the same id, label, group, place and icon as keel 0.15.1. */
export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "map",
    label: "Map",
    group: "know",
    order: 30,
    icon: (
      <>
        <path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z" />
        <path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
      </>
    ),
    component: MapPage,
  });
}

export default definePlugin({ name: "map", setup });
