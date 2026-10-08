// What a plugin's web part is: its ES module (built on its own, loaded at run time) default-exports one of these.

import type { ComponentType } from "react";
import type * as Sdk from "./index";

/** The props keel gives an add-on's page: the chosen project ("" when none) and the rest of the link (#/teams/web). */
export type AddonPageProps = { pid: string; arg?: string };

/** @keel/web-sdk itself, as keel hands it to a plugin's setup(). */
export type KeelSdk = typeof Sdk;

/** What a plugin's web module exports by default: its name, and
 *  - setup(sdk): called once when keel loads the module at start; it registers the plugin's pages and slots
 *    (sdk.registerPage, sdk.registerSlot), so its pages take their place in the menu (step 3);
 *  - pages: keel Product's shape, a page per screen id that /api/features lists (loaded on first use). */
export type AddonWeb = {
  name: string;
  setup?: (sdk: KeelSdk) => void;
  pages?: Record<string, ComponentType<AddonPageProps>>;
};

/** A plugin's web part: `export default definePlugin({ name: "map", setup(sdk) { sdk.registerPage({ … }) } })`, or
 *  keel Product's `definePlugin({ name: "product", pages: { initiatives: Initiatives } })`. */
export function definePlugin(plugin: AddonWeb): AddonWeb {
  return plugin;
}
