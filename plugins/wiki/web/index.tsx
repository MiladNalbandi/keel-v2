// The Wiki plugin's web part (plugins/wiki): `npm run build:plugin -- wiki` (in web/) builds this file on its own into
// plugins/wiki/web/dist/index.js. keel loads it at start from the url /api/features gives and calls setup() once: it
// puts the Wiki page in the menu where it always was (Project, the last one, after Graph). It imports only
// @keel/web-sdk and react: keel's page shares its own copies (the import map).

import { definePlugin, type KeelSdk } from "@keel/web-sdk";
import { WikiPage } from "./Wiki";

/** The Wiki page in the menu: the same id, label, group, place and icon as keel 0.15.1. */
export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "wiki",
    label: "Wiki",
    group: "know",
    order: 50,
    icon: (
      <path d="M12 6c-2-1.5-5-2-8-1.5V19c3-.5 6 0 8 1.5 2-1.5 5-2 8-1.5V4.5c-3-.5-6 0-8 1.5zM12 6v14.5" />
    ),
    component: WikiPage,
  });
}

export default definePlugin({ name: "wiki", setup });
