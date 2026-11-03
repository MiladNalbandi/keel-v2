// The Library plugin's web part (plugins/library): `npm run build:plugin -- library` (in web/) builds this file on its own into
// plugins/library/web/dist/index.js. It puts the Library page in the menu (Project, after Wiki) and its documents in the launcher
// (⌘K). It imports only @keel/web-sdk and react.

import { definePlugin, SLOTS, type KeelSdk, type LauncherSourceItem } from "@keel/web-sdk";
import { libraryLauncher, LibraryPage } from "./Library";

export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "library",
    label: "Library",
    group: "know",
    order: 55,
    icon: <path d="M5 4v16M9 4v16M13 5l4 15M4 20h16" />,
    component: LibraryPage,
  });
  sdk.registerSlot<LauncherSourceItem>(SLOTS.launcherSource, libraryLauncher);
}

export default definePlugin({ name: "library", setup });
